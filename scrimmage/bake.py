"""
Pre-built machine images for fleet workers: ``scrimmage bake-images``.

A fresh fleet machine would spend minutes installing packages, this app, and
the multi-gigabyte game image before playing. After each deploy, the main
server bakes a machine image (AMI) from the worker launch template instead:

  1. launch a throwaway machine from the template with SCRIMMAGE_BAKE set:
     deploy/install-worker.sh installs everything for this commit, then powers
     the machine off (on failure, it stays up so its console can be read);
  2. image it, and add a template version that uses the image. The fleet
     launches the template's newest version, so new machines boot from it.

Machines booted from an image of an older commit still work, as they catch up
at boot, so a failed bake only costs boot time.
"""

from __future__ import annotations

import base64
import logging
import os
import time
from typing import Any

log = logging.getLogger(__name__)

# The throwaway machine that installs the worker: small, same architecture.
BAKE_INSTANCE_TYPE = "m8g.large"
BAKED = "scrimmage-baked"  # description prefix of baked template versions
INSTALL_TIMEOUT_SECONDS = 60 * 60
POLL_SECONDS = 15


class BakeFailed(Exception):
    pass


def with_bake_flag(user_data_b64: str) -> str:
    """The template's user data, with SCRIMMAGE_BAKE exported right after the shebang."""
    script = base64.b64decode(user_data_b64).decode()
    shebang, _, rest = script.partition("\n")
    return base64.b64encode(f"{shebang}\nexport SCRIMMAGE_BAKE=1\n{rest}".encode()).decode()


class Baker:
    def __init__(self, ec2: Any, *, subnet: str, commit: str) -> None:
        self.ec2 = ec2
        self.subnet = subnet
        self.commit = commit

    def _versions(self, template: str) -> list[dict[str, Any]]:
        pages = self.ec2.get_paginator("describe_launch_template_versions").paginate(
            LaunchTemplateName=template
        )
        return [v for page in pages for v in page["LaunchTemplateVersions"]]

    def bake(self, template: str) -> str | None:
        """Bake ``template``'s image for this commit. Returns the AMI id (None: already baked)."""
        versions = self._versions(template)
        description = f"{BAKED} {self.commit}"
        if any(v.get("VersionDescription") == description for v in versions):
            log.info("%s: already baked for %s", template, self.commit[:12])
            return None
        # The template as CloudFormation defined it: the newest version not baked by us.
        source = max(
            (v for v in versions if not v.get("VersionDescription", "").startswith(BAKED)),
            key=lambda v: v["VersionNumber"],
        )
        stack = self._stack_of(template)
        log.info("%s: baking from version %d", template, source["VersionNumber"])
        instance = self.ec2.run_instances(
            LaunchTemplate={
                "LaunchTemplateName": template,
                "Version": str(source["VersionNumber"]),
            },
            InstanceType=BAKE_INSTANCE_TYPE,
            SubnetId=self.subnet,
            MinCount=1,
            MaxCount=1,
            UserData=with_bake_flag(source["LaunchTemplateData"]["UserData"]),
            InstanceInitiatedShutdownBehavior="stop",
            TagSpecifications=[
                {
                    "ResourceType": "instance",
                    "Tags": [
                        {"Key": "Name", "Value": f"{stack}-bake"},
                        {"Key": "scrimmage-bake", "Value": stack},
                    ],
                }
            ],
        )["Instances"][0]["InstanceId"]
        try:
            self._wait_stopped(instance)
            image = self.ec2.create_image(
                InstanceId=instance,
                Name=f"{template}-{self.commit[:12]}-{int(time.time())}",
                Description=description,
                TagSpecifications=[
                    {"ResourceType": kind, "Tags": [{"Key": "scrimmage-baked", "Value": stack}]}
                    for kind in ("image", "snapshot")
                ],
            )["ImageId"]
            self.ec2.get_waiter("image_available").wait(
                ImageIds=[image], WaiterConfig={"Delay": 15, "MaxAttempts": 240}
            )
        finally:
            self.ec2.terminate_instances(InstanceIds=[instance])
        self.ec2.create_launch_template_version(
            LaunchTemplateName=template,
            SourceVersion=str(source["VersionNumber"]),
            VersionDescription=description,
            LaunchTemplateData={"ImageId": image},
        )
        log.info("%s: new machines boot from %s", template, image)
        self._remove_old(template, keep=image)
        return str(image)

    def _stack_of(self, template: str) -> str:
        found = self.ec2.describe_launch_templates(LaunchTemplateNames=[template])
        tags = {t["Key"]: t["Value"] for t in found["LaunchTemplates"][0].get("Tags", [])}
        return str(tags["scrimmage-stack"])

    def _wait_stopped(self, instance: str) -> None:
        deadline = time.monotonic() + INSTALL_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            time.sleep(POLL_SECONDS)
            found = self.ec2.describe_instances(InstanceIds=[instance])
            state = found["Reservations"][0]["Instances"][0]["State"]["Name"]
            if state == "stopped":
                return
            if state in ("terminated", "shutting-down"):
                raise BakeFailed(f"bake machine {instance} was terminated")
        console = self.ec2.get_console_output(InstanceId=instance, Latest=True).get("Output", "")
        raise BakeFailed(
            f"bake machine {instance} did not finish installing within "
            f"{INSTALL_TIMEOUT_SECONDS // 60} minutes. End of its console:\n{console[-3000:]}"
        )

    def _remove_old(self, template: str, keep: str) -> None:
        """Delete older baked versions, their images, and the images' snapshots."""
        old = [
            v
            for v in self._versions(template)
            if v.get("VersionDescription", "").startswith(BAKED)
            and v["LaunchTemplateData"].get("ImageId") != keep
        ]
        if not old:
            return
        self.ec2.delete_launch_template_versions(
            LaunchTemplateName=template, Versions=[str(v["VersionNumber"]) for v in old]
        )
        images = {v["LaunchTemplateData"]["ImageId"] for v in old}
        for image in self.ec2.describe_images(ImageIds=sorted(images))["Images"]:
            self.ec2.deregister_image(ImageId=image["ImageId"])
            for mapping in image.get("BlockDeviceMappings", []):
                snapshot = mapping.get("Ebs", {}).get("SnapshotId")
                if snapshot:
                    self.ec2.delete_snapshot(SnapshotId=snapshot)


def main(commit: str, region: str) -> int:
    template = os.environ.get("WORKER_TEMPLATE", "")
    subnet = os.environ.get("BAKE_SUBNET", "")
    if not template or not subnet:
        log.info("No worker template configured; nothing to bake")
        return 0
    import boto3  # noqa: PLC0415 -- only needed on AWS

    baker = Baker(boto3.client("ec2", region_name=region or None), subnet=subnet, commit=commit)
    try:
        baker.bake(template)
    except Exception:
        log.exception("%s: bake failed; machines keep installing at boot", template)
        return 1
    return 0
