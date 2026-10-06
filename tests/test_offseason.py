"""Off-season: until site_opens_on, only admins see the site; everyone else a countdown."""

from __future__ import annotations

import sqlite3
from typing import Any

from scrimmage import settings as settings_module
from tests.conftest import Client


def close_until(conn: sqlite3.Connection, day: str) -> None:
    settings_module.update(conn, "site_opens_on", day)


def test_everyone_but_admins_sees_the_countdown(
    client: Client, make_client: Any, conn: sqlite3.Connection, python_bot_zip: bytes
) -> None:
    close_until(conn, "2999-01-04")

    page = client.get("/").get_data(as_text=True)
    assert "data-countdown" in page and "January 4, 2999" in page
    assert "Announcements" not in page and "Log in" in page  # admins still get in
    assert client.get("/login").status_code in (200, 302)
    assert client.get("/healthz").status_code == 200

    alice: Client = make_client("alice")
    for path in ("/", "/team", "/games", "/tournaments", "/announcements", "/sponsor/"):
        response = alice.get(path)
        assert response.status_code == 200 and "data-countdown" in response.get_data(as_text=True)
    # Nothing can be done either: posts go back to the countdown.
    assert alice.post("/team/create", {"name": "Aces"}).status_code == 302
    assert conn.execute("SELECT count(*) FROM teams").fetchone()[0] == 0
    assert "Log out alice" in alice.get("/").get_data(as_text=True)

    boss: Client = make_client("boss")
    page = boss.get("/").get_data(as_text=True)
    assert "data-countdown" not in page and "the site is closed" in page.lower()
    assert boss.get("/admin/").status_code == 200


def test_site_opens_on_the_day_or_when_cleared(make_client: Any, conn: sqlite3.Connection) -> None:
    alice: Client = make_client("alice")
    close_until(conn, "2000-01-01")  # already past
    assert "data-countdown" not in alice.get("/").get_data(as_text=True)
    close_until(conn, "2999-01-04")
    assert "data-countdown" in alice.get("/").get_data(as_text=True)
    close_until(conn, "")
    assert "data-countdown" not in alice.get("/").get_data(as_text=True)


def test_site_opens_on_must_be_a_date(make_client: Any, conn: sqlite3.Connection) -> None:
    boss: Client = make_client("boss")
    boss.post("/admin/settings", {"key": "site_opens_on", "value": "next january"})
    assert "must be a date" in boss.get("/admin/settings").get_data(as_text=True)
    boss.post("/admin/settings", {"key": "site_opens_on", "value": "2027-01-04"})
    assert settings_module.Settings(conn).text("site_opens_on") == "2027-01-04"
