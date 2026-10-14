"""Pkrtask session: who is logged in (the real user), including off-season."""

from __future__ import annotations

import sqlite3
from typing import Any

from tests.conftest import Client
from tests.test_offseason import close_until


def test_session_requires_login(client: Client) -> None:
    response = client.get("/api/pkrtask/session")
    assert response.status_code == 401
    assert response.get_json() == {"error": "login required"}
    assert response.headers["Cache-Control"] == "no-store"


def test_session_reports_any_logged_in_user(make_client: Any) -> None:
    alice: Client = make_client("alice")
    response = alice.get("/api/pkrtask/session")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.get_json() == {"viewer": {"kerberos": "alice", "display_name": None}}


def test_nav_link_is_admin_only(make_client: Any) -> None:
    alice: Client = make_client("alice")
    assert 'href="/pkrtask"' not in alice.get("/").get_data(as_text=True)
    boss: Client = make_client("boss")
    assert 'href="/pkrtask"' in boss.get("/").get_data(as_text=True)


def test_session_uses_the_real_user_while_impersonating(make_client: Any) -> None:
    make_client("alice")
    boss: Client = make_client("boss")
    boss.post("/admin/impersonate", {"kerberos": "alice"})
    page = boss.get("/").get_data(as_text=True)
    assert "viewing the site as" in page and "alice" in page
    response = boss.get("/api/pkrtask/session")
    assert response.status_code == 200
    assert response.get_json() == {"viewer": {"kerberos": "boss", "display_name": None}}


def test_session_when_the_site_is_closed(
    client: Client, make_client: Any, conn: sqlite3.Connection
) -> None:
    close_until(conn, "2999-01-04")
    response = client.get("/api/pkrtask/session")
    assert response.status_code == 401
    assert "data-countdown" not in response.get_data(as_text=True)

    alice: Client = make_client("alice")
    closed = alice.get("/api/pkrtask/session")
    assert closed.status_code == 200
    assert closed.get_json() == {"viewer": {"kerberos": "alice", "display_name": None}}
