"""The nightly S3 archive and the worker image baker, against fake AWS clients."""

from __future__ import annotations

import base64
import gzip
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from scrimmage import archive, bake
from scrimmage.config import Config
from scrimmage.services.storage import Storage


class FakeS3:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def upload_file(self, filename: str, bucket: str, key: str) -> None:
        assert bucket == "archive"
        self.objects[key] = Path(filename).read_bytes()


def test_archive_uploads_everything_once(
    config: Config, conn: sqlite3.Connection, storage: Storage
) -> None:
    storage.bot_zip(1).write_bytes(b"zip")
    storage.write_log(7, "game", gzip.compress(b"Final, A (1), B (-1)"))
    s3 = FakeS3()
    assert archive.run(config, s3, "archive") == {"files": 2}
    assert s3.objects["files/bots/1.zip"] == b"zip"
    assert "files/logs/0/7/game.log.gz" in s3.objects
    copy = gzip.decompress(s3.objects["database/latest.sqlite3.gz"])
    assert copy.startswith(b"SQLite format 3")
    assert any(key.startswith("database/daily/") for key in s3.objects)

    # Only new or changed files go up again.
    s3.objects.clear()
    storage.write_log(8, "game", gzip.compress(b"another game"))
    assert archive.run(config, s3, "archive") == {"files": 1}
    assert [k for k in s3.objects if k.startswith("files/")] == ["files/logs/0/8/game.log.gz"]


USER_DATA = base64.b64encode(b"#!/bin/bash\nset -uo pipefail\necho install\n").decode()


class FakeEc2:
    """Just enough of EC2 for the baker, with instances that stop on their own."""

    def __init__(self) -> None:
        self.versions = [
            {
                "VersionNumber": 1,
                "LaunchTemplateData": {"UserData": USER_DATA, "ImageId": "ami-ubuntu"},
            }
        ]
        self.launched: list[dict[str, Any]] = []
        self.terminated: list[str] = []
        self.deregistered: list[str] = []
        self.deleted_snapshots: list[str] = []
        self.state = "stopped"
        self.images = 0

    def get_paginator(self, _name: str) -> Any:
        versions = self.versions

        class Paginator:
            def paginate(self, **_: Any) -> list[dict[str, Any]]:
                return [{"LaunchTemplateVersions": list(versions)}]

        return Paginator()

    def describe_launch_templates(self, **_: Any) -> dict[str, Any]:
        return {"LaunchTemplates": [{"Tags": [{"Key": "scrimmage-stack", "Value": "pb"}]}]}

    def run_instances(self, **kwargs: Any) -> dict[str, Any]:
        self.launched.append(kwargs)
        return {"Instances": [{"InstanceId": f"i-{len(self.launched)}"}]}

    def describe_instances(self, **_: Any) -> dict[str, Any]:
        return {"Reservations": [{"Instances": [{"State": {"Name": self.state}}]}]}

    def get_console_output(self, **_: Any) -> dict[str, Any]:
        return {"Output": "E: apt failed"}

    def create_image(self, **_: Any) -> dict[str, Any]:
        self.images += 1
        return {"ImageId": f"ami-baked-{self.images}"}

    def get_waiter(self, _name: str) -> Any:
        class Waiter:
            def wait(self, **_: Any) -> None:
                pass

        return Waiter()

    def terminate_instances(self, InstanceIds: list[str]) -> None:
        self.terminated += InstanceIds

    def create_launch_template_version(self, **kwargs: Any) -> dict[str, Any]:
        number = max(v["VersionNumber"] for v in self.versions) + 1
        self.versions.append(
            {
                "VersionNumber": number,
                "VersionDescription": kwargs["VersionDescription"],
                "LaunchTemplateData": kwargs["LaunchTemplateData"],
            }
        )
        return {"LaunchTemplateVersion": {"VersionNumber": number}}

    def delete_launch_template_versions(self, Versions: list[str], **_: Any) -> None:
        self.versions = [v for v in self.versions if str(v["VersionNumber"]) not in Versions]

    def describe_images(self, ImageIds: list[str]) -> dict[str, Any]:
        return {
            "Images": [
                {"ImageId": i, "BlockDeviceMappings": [{"Ebs": {"SnapshotId": f"snap-{i}"}}]}
                for i in ImageIds
            ]
        }

    def deregister_image(self, ImageId: str) -> None:
        self.deregistered.append(ImageId)

    def delete_snapshot(self, SnapshotId: str) -> None:
        self.deleted_snapshots.append(SnapshotId)


class FakeAutoScaling:
    """The fleet's group, as CloudFormation created it (version 1 of the template)."""

    def __init__(self) -> None:
        self.spec = {"LaunchTemplateId": "lt-1", "LaunchTemplateName": "pb-worker", "Version": "1"}
        self.updates: list[dict[str, Any]] = []

    def describe_auto_scaling_groups(self, **_: Any) -> dict[str, Any]:
        policy = {
            "LaunchTemplate": {"LaunchTemplateSpecification": dict(self.spec), "Overrides": []},
            "InstancesDistribution": {"SpotAllocationStrategy": "price-capacity-optimized"},
        }
        return {"AutoScalingGroups": [{"MixedInstancesPolicy": policy}]}

    def update_auto_scaling_group(self, **kwargs: Any) -> None:
        self.updates.append(kwargs)
        self.spec = kwargs["MixedInstancesPolicy"]["LaunchTemplate"]["LaunchTemplateSpecification"]


def baker(ec2: FakeEc2, autoscaling: FakeAutoScaling, commit: str) -> bake.Baker:
    return bake.Baker(ec2, autoscaling, group="pb-workers", subnet="subnet-1", commit=commit)


@pytest.fixture(autouse=True)
def fast_polling(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bake, "POLL_SECONDS", 0)


def test_bake_adds_a_template_version_and_cleans_up() -> None:
    ec2, autoscaling = FakeEc2(), FakeAutoScaling()
    template = "pb-worker"
    assert baker(ec2, autoscaling, "a" * 40).bake(template) == "ami-baked-1"
    launch = ec2.launched[0]
    # A small throwaway machine from the CloudFormation version, told to bake and stop.
    assert launch["InstanceType"] == "m8g.large" and launch["SubnetId"] == "subnet-1"
    assert launch["LaunchTemplate"]["Version"] == "1"
    assert launch["InstanceInitiatedShutdownBehavior"] == "stop"
    script = base64.b64decode(launch["UserData"]).decode()
    assert script.startswith("#!/bin/bash\nexport SCRIMMAGE_BAKE=1\nset -uo pipefail")
    assert ec2.terminated == ["i-1"]
    assert ec2.versions[-1]["LaunchTemplateData"] == {"ImageId": "ami-baked-1"}

    # The fleet now launches the baked version, with the rest of its policy kept.
    assert autoscaling.spec == {"LaunchTemplateId": "lt-1", "Version": "2"}
    policy = autoscaling.updates[0]["MixedInstancesPolicy"]
    assert policy["InstancesDistribution"]["SpotAllocationStrategy"] == "price-capacity-optimized"

    # The same commit again: nothing to bake, but a stack update may have reset
    # the group to CloudFormation's version, so it is pointed back.
    autoscaling.spec = {"LaunchTemplateId": "lt-1", "Version": "1"}
    assert baker(ec2, autoscaling, "a" * 40).bake(template) is None
    assert autoscaling.spec["Version"] == "2"
    # A new commit replaces the old image, still baking from version 1.
    assert baker(ec2, autoscaling, "b" * 40).bake(template) == "ami-baked-2"
    assert ec2.launched[1]["LaunchTemplate"]["Version"] == "1"
    assert [v["VersionNumber"] for v in ec2.versions] == [1, 3]
    assert autoscaling.spec["Version"] == "3"
    assert ec2.deregistered == ["ami-baked-1"]
    assert ec2.deleted_snapshots == ["snap-ami-baked-1"]


def test_failed_bake_reports_the_console_and_cleans_up(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bake, "INSTALL_TIMEOUT_SECONDS", 0)
    ec2 = FakeEc2()
    ec2.state = "running"
    with pytest.raises(bake.BakeFailed, match="apt failed"):
        baker(ec2, FakeAutoScaling(), "a" * 40).bake("pb-worker")
    assert ec2.terminated == ["i-1"] and len(ec2.versions) == 1


class FakeSns:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    def publish(self, **kwargs: Any) -> None:
        self.messages.append(kwargs)


def test_full_disk_refuses_uploads_and_alerts_daily(
    config: Config,
    conn: sqlite3.Connection,
    storage: Storage,
    tmp_path: Path,
    python_bot_zip: bytes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dataclasses import replace

    from scrimmage import cli
    from scrimmage.services import errors
    from tests.conftest import add_team_with_bot

    gb = 1 << 30
    usage = {"free": 50 * gb}
    monkeypatch.setattr(Storage, "disk", lambda self: (usage["free"], 100 * gb))
    sns = FakeSns()
    alerting = replace(config, alert_topic="arn:aws:sns:us-east-1:1:alerts")

    cli.check_disk(conn, alerting, sns)
    assert sns.messages == []
    usage["free"] = 10 * gb
    cli.check_disk(conn, alerting, sns)
    cli.check_disk(conn, alerting, sns)  # once a day, not every minute
    assert len(sns.messages) == 1 and "10.7 GB free" in sns.messages[0]["Message"]

    usage["free"] = 4 * gb
    with pytest.raises(errors.UserError, match="storage is nearly full"):
        add_team_with_bot(conn, storage, "Aces", python_bot_zip, tmp_path)
