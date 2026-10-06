from __future__ import annotations

import sqlite3
from dataclasses import replace
from typing import Any

import pytest
from flask import Flask

from scrimmage import settings as settings_module
from scrimmage.db import now
from scrimmage.services import fleet, matches
from scrimmage.services.errors import UserError
from scrimmage.settings import Settings
from tests.conftest import Client


class FakeAutoScaling:
    """Just enough of boto3's autoscaling client."""

    def __init__(self, maximum: int = 2048) -> None:
        self.desired = 0
        self.maximum = maximum
        self.instances: list[dict[str, Any]] = []
        self.failure: str | None = None

    def describe_auto_scaling_groups(self, **_: Any) -> dict[str, Any]:
        return {
            "AutoScalingGroups": [
                {
                    "DesiredCapacity": self.desired,
                    "MaxSize": self.maximum,
                    "Instances": self.instances,
                }
            ]
        }

    def describe_scaling_activities(self, **_: Any) -> dict[str, Any]:
        if self.failure:
            return {
                "Activities": [
                    {"StatusCode": "Failed", "StatusMessage": self.failure, "Description": "x"}
                ]
            }
        return {"Activities": []}

    def set_desired_capacity(self, DesiredCapacity: int, **_: Any) -> None:
        self.desired = DesiredCapacity


def report_local_worker(conn: sqlite3.Connection, cores: int) -> None:
    conn.execute(
        "INSERT INTO worker_status (name, cores, busy_cores, started_at, updated_at) "
        "VALUES ('main', ?, 0, ?, ?)",
        (cores, now(), now()),
    )


@pytest.fixture
def aws() -> FakeAutoScaling:
    return FakeAutoScaling()


@pytest.fixture
def workers(aws: FakeAutoScaling) -> fleet.Fleet:
    return fleet.AutoScalingFleet("workers", "us-east-1", client=aws)


def apply(
    conn: sqlite3.Connection,
    workers: fleet.Fleet | None,
    capacity: int,
    cores: int = 1,
    idle_minutes: int = 30,
) -> None:
    fleet.set_capacity(
        conn, workers, capacity=capacity, idle_capacity=2, idle_minutes=idle_minutes, cores=cores
    )


def test_games_beyond_the_main_server_go_to_the_fleet(
    conn: sqlite3.Connection, aws: FakeAutoScaling, workers: fleet.Fleet
) -> None:
    report_local_worker(conn, 2)
    apply(conn, workers, 1000)
    assert aws.desired == 998
    apply(conn, workers, 2)
    assert aws.desired == 0
    with pytest.raises(UserError, match="At most 2050"):
        apply(conn, workers, 5000)


def test_threads_per_bot_multiply_cores(
    conn: sqlite3.Connection, aws: FakeAutoScaling, workers: fleet.Fleet
) -> None:
    report_local_worker(conn, 2)
    apply(conn, workers, 10, cores=2)
    assert aws.desired == 10 * 2 - 2  # the main server runs one 2-core game
    # Games bigger than the main server's 2 cores all run on the fleet.
    apply(conn, workers, 100, cores=4)
    assert aws.desired == 400
    assert Settings(conn).number("cores_per_game") == 4
    with pytest.raises(UserError, match="1, 2, 4, 8 cores"):
        apply(conn, workers, 1, cores=3)
    with pytest.raises(UserError, match="At most 256"):
        apply(conn, workers, 257, cores=8)


def test_leftover_big_games_keep_a_machine(
    conn: sqlite3.Connection,
    aws: FakeAutoScaling,
    workers: fleet.Fleet,
    two_teams: tuple[int, int],
) -> None:
    report_local_worker(conn, 2)
    apply(conn, workers, 3, cores=8)
    assert aws.desired == 3 * 8
    # Back to 1-core games while an 8-core game is still queued: the fleet keeps
    # room for it rather than stranding it.
    matches.challenge(conn, Settings(conn), *two_teams)
    apply(conn, workers, 2)
    assert aws.desired == 8
    conn.execute("UPDATE games SET status = 'done'")
    fleet.reconcile(conn, workers)
    assert aws.desired == 0


def test_status_and_aws_errors(aws: FakeAutoScaling, workers: fleet.Fleet) -> None:
    aws.desired = 48
    aws.instances = [
        {"LifecycleState": "InService", "WeightedCapacity": "16"},
        {"LifecycleState": "InService", "WeightedCapacity": "16"},
        {"LifecycleState": "Pending", "WeightedCapacity": "16"},
    ]
    aws.failure = "You have requested more vCPU capacity than your current vCPU limit of 32"
    status = workers.status()
    assert (status.in_service, status.starting, status.instances) == (32, 16, 3)
    assert status.problem is not None and "vCPU limit" in status.problem


def test_reconcile_scales_down_when_idle(
    conn: sqlite3.Connection, aws: FakeAutoScaling, workers: fleet.Fleet
) -> None:
    report_local_worker(conn, 2)
    apply(conn, workers, 100)
    assert aws.desired == 98
    fleet.reconcile(conn, workers)  # just raised: not idle yet
    assert aws.desired == 98
    settings_module.update(conn, "capacity_changed_at", str(now() - 3600))
    fleet.reconcile(conn, workers)
    assert aws.desired == 0
    assert Settings(conn).number("match_capacity") == 2


def test_reconcile_restores_drifted_capacity(
    conn: sqlite3.Connection, aws: FakeAutoScaling, workers: fleet.Fleet
) -> None:
    report_local_worker(conn, 2)
    apply(conn, workers, 10, idle_minutes=0)
    aws.desired = 0  # e.g. someone changed it in the AWS console
    fleet.reconcile(conn, workers)
    assert aws.desired == 8


def test_without_a_fleet(conn: sqlite3.Connection) -> None:
    apply(conn, None, 4)
    assert Settings(conn).number("match_capacity") == 4
    fleet.reconcile(conn, None)


def test_admin_dashboard_sets_hardware(
    app: Flask,
    make_client: Any,
    conn: sqlite3.Connection,
    aws: FakeAutoScaling,
    workers: fleet.Fleet,
) -> None:
    state = app.extensions["scrimmage"]
    app.extensions["scrimmage"] = replace(state, fleet=workers)
    report_local_worker(conn, 2)
    boss: Client = make_client("boss")
    page = boss.get("/admin/").get_data(as_text=True)
    assert "Games at once" in page and "8 cores" in page
    form = {
        "cores": "2",
        "capacity": "33",
        "idle_capacity": "2",
        "idle_minutes": "30",
    }
    boss.post("/admin/capacity", form)
    assert aws.desired == 33 * 2 - 2
    assert 'value="2" selected' in boss.get("/admin/").get_data(as_text=True)
    alice: Client = make_client("alice")
    assert alice.post("/admin/capacity", {**form, "capacity": "9999"}).status_code == 404
    assert aws.desired == 64
