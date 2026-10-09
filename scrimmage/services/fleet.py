"""
Match capacity: the main server's own worker plus an elastic worker fleet.

The fleet is an EC2 Auto Scaling group of identical worker machines whose
capacity is counted in cores (vCPUs): a game needs as many as it has cores.

Admins set, on the dashboard, the hardware for new games and how many may run
at once. ``reconcile`` (every minute) keeps the fleet at that size, minus what
the main server runs itself, and drops back to the idle capacity once nothing
has run for a while. Nothing else ever grows the fleet.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass
from typing import Any, Protocol

from scrimmage import settings as settings_module
from scrimmage.db import now
from scrimmage.services import hardware, queue
from scrimmage.services.errors import UserError
from scrimmage.services.hardware import GameHardware
from scrimmage.settings import Settings

log = logging.getLogger(__name__)

# The worker on the main server reports under this name.
LOCAL_WORKER = "main"
HEARTBEAT_FRESH_SECONDS = 120


@dataclass(frozen=True)
class FleetStatus:
    desired: int = 0  # cores requested from AWS
    in_service: int = 0  # cores on running machines
    starting: int = 0  # cores on machines still launching
    maximum: int = 0
    instances: int = 0
    problem: str | None = None  # why AWS last failed to add capacity


class Fleet(Protocol):
    def status(self) -> FleetStatus: ...

    def set_desired(self, cores: int) -> None: ...


class AutoScalingFleet:
    def __init__(self, group: str, region: str, client: Any = None) -> None:
        if client is None:
            import boto3  # noqa: PLC0415 -- only needed on AWS

            client = boto3.client("autoscaling", region_name=region or None)
        self.client = client
        self.group = group
        self._cache: tuple[float, FleetStatus] | None = None

    def status(self) -> FleetStatus:
        if self._cache is not None and time.monotonic() - self._cache[0] < 5:
            return self._cache[1]
        result = self._fetch_status()
        self._cache = (time.monotonic(), result)
        return result

    def _fetch_status(self) -> FleetStatus:
        groups = self.client.describe_auto_scaling_groups(AutoScalingGroupNames=[self.group])
        group = groups["AutoScalingGroups"][0]
        in_service = starting = 0
        for instance in group["Instances"]:
            weight = int(instance.get("WeightedCapacity") or 1)
            state = instance["LifecycleState"]
            if state == "InService":
                in_service += weight
            elif state.startswith("Pending"):
                starting += weight
        activities = self.client.describe_scaling_activities(
            AutoScalingGroupName=self.group, MaxRecords=1
        )["Activities"]
        problem = None
        if activities and activities[0]["StatusCode"] in ("Failed", "Cancelled"):
            problem = activities[0].get("StatusMessage") or activities[0]["Description"]
        return FleetStatus(
            desired=group["DesiredCapacity"],
            in_service=in_service,
            starting=starting,
            maximum=group["MaxSize"],
            instances=len(group["Instances"]),
            problem=problem,
        )

    def set_desired(self, cores: int) -> None:
        self._cache = None
        self.client.set_desired_capacity(
            AutoScalingGroupName=self.group, DesiredCapacity=cores, HonorCooldown=False
        )


def from_config(group: str, region: str) -> Fleet | None:
    """The fleet, or None without one (local development, or no AWS)."""
    return AutoScalingFleet(group, region) if group else None


def local_cores(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT cores FROM worker_status WHERE name = ? AND updated_at > ?",
        (LOCAL_WORKER, now() - HEARTBEAT_FRESH_SECONDS),
    ).fetchone()
    return int(row[0]) if row else 0


def cores_for(games: int, needs: GameHardware, local: int) -> int:
    """Fleet cores to run ``games`` such games at once, beyond the main server's cores.

    Games bigger than the main server run only on the fleet.
    """
    if needs.cores > local:
        return games * needs.cores
    return max(0, games * needs.cores - local)


def largest_unfinished(conn: sqlite3.Connection) -> int:
    """Cores of the biggest queued or running game (0 if none)."""
    row = conn.execute(
        "SELECT max(cores) FROM games WHERE status IN ('queued', 'running')"
    ).fetchone()
    return int(row[0] or 0)


def max_games(conn: sqlite3.Connection, fleet: Fleet | None, needs: GameHardware) -> int:
    """Most games of this hardware that can run at once (main server + fleet limit)."""
    local = local_cores(conn)
    on_main = local // needs.cores
    if fleet is None:
        return on_main
    return on_main + fleet.status().maximum // needs.cores


def set_capacity(
    conn: sqlite3.Connection,
    fleet: Fleet | None,
    *,
    capacity: int,
    idle_capacity: int,
    idle_minutes: int,
    cores: int,
) -> None:
    """Admin action: choose the cores per new game and how many games run at once."""
    if cores not in hardware.CORE_CHOICES:
        choices = ", ".join(str(c) for c in hardware.CORE_CHOICES)
        raise UserError(f"Games can have {choices} cores.")
    needs = hardware.for_cores(cores)
    if capacity < 0 or idle_capacity < 0:
        raise UserError("Capacity cannot be negative.")
    if fleet is not None:
        limit = max_games(conn, fleet, needs)
        if max(capacity, idle_capacity) > limit:
            raise UserError(
                f"At most {limit} games of that size at once (the fleet's limit is set by "
                "FleetMaxVcpus in CloudFormation)."
            )
    for key, value in (
        ("cores_per_game", cores),
        ("match_capacity", capacity),
        ("idle_match_capacity", idle_capacity),
        ("idle_minutes", idle_minutes),
        ("capacity_changed_at", now()),
    ):
        try:
            settings_module.update(conn, key, str(value))
        except ValueError as exc:
            raise UserError(str(exc)) from None
    reconcile(conn, fleet)


def reconcile(conn: sqlite3.Connection, fleet: Fleet | None) -> None:
    """Size the fleet; fall back to the idle capacity when nothing has run for a while."""
    s = Settings(conn)
    capacity = s.number("match_capacity")
    idle_capacity = s.number("idle_match_capacity")
    idle_minutes = s.number("idle_minutes")
    if (
        idle_minutes
        and capacity > idle_capacity
        and queue.is_idle(conn, idle_minutes, since=s.number("capacity_changed_at"))
    ):
        log.info("Idle for %d minutes: capacity %d -> %d", idle_minutes, capacity, idle_capacity)
        settings_module.update(conn, "match_capacity", str(idle_capacity))
        capacity = idle_capacity
    if fleet is None:
        return
    local = local_cores(conn)
    target = cores_for(capacity, hardware.for_new_games(s), local)
    # Games left from earlier, bigger settings still need a machine that fits them.
    largest = largest_unfinished(conn)
    if largest > local:
        target = max(target, largest)
    status = fleet.status()
    target = min(target, status.maximum)
    if target != status.desired:
        log.info("Fleet: %d -> %d cores", status.desired, target)
        fleet.set_desired(target)
