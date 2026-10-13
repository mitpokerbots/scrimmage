"""Pkrtask session: the real logged-in user, admins only, including off-season."""

from __future__ import annotations

import sqlite3
from typing import Any

from scrimmage.services import accounts
from tests.conftest import Client
from tests.test_offseason import close_until


def test_session_requires_login(client: Client) -> None:
    response = client.get("/api/pkrtask/session")
    assert response.status_code == 401
    assert response.get_json() == {"error": "login required"}
    assert response.headers["Cache-Control"] == "no-store"


def test_session_rejects_non_admins(make_client: Any) -> None:
    alice: Client = make_client("alice")
    response = alice.get("/api/pkrtask/session")
    assert response.status_code == 403
    assert response.get_json() == {"error": "admins only"}
    assert response.headers["Cache-Control"] == "no-store"
    assert 'href="/pkrtask"' not in alice.get("/").get_data(as_text=True)


def test_admin_session(make_client: Any, conn: sqlite3.Connection) -> None:
    accounts.record_login(conn, "zoe", "Zoe Z", frozenset({"zoe"}))
    make_client("alice")
    boss: Client = make_client("boss")
    response = boss.get("/api/pkrtask/session")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.get_json() == {
        "viewer": {"kerberos": "boss", "display_name": None},
        "admins": [
            {"kerberos": "boss", "display_name": None},
            {"kerberos": "zoe", "display_name": "Zoe Z"},
        ],
    }
    assert 'href="/pkrtask"' in boss.get("/").get_data(as_text=True)


def test_session_uses_the_real_user_while_impersonating(make_client: Any) -> None:
    make_client("alice")
    boss: Client = make_client("boss")
    boss.post("/admin/impersonate", {"kerberos": "alice"})
    page = boss.get("/").get_data(as_text=True)
    assert "viewing the site as" in page and "alice" in page
    response = boss.get("/api/pkrtask/session")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    body = response.get_json()
    assert body["viewer"] == {"kerberos": "boss", "display_name": None}


def test_session_when_the_site_is_closed(
    client: Client, make_client: Any, conn: sqlite3.Connection
) -> None:
    close_until(conn, "2999-01-04")
    response = client.get("/api/pkrtask/session")
    assert response.status_code == 401
    assert response.get_json() == {"error": "login required"}
    assert response.headers["Cache-Control"] == "no-store"
    assert "data-countdown" not in response.get_data(as_text=True)

    alice: Client = make_client("alice")
    closed = alice.get("/api/pkrtask/session")
    assert closed.status_code == 403
    assert closed.get_json() == {"error": "admins only"}
    assert "data-countdown" not in closed.get_data(as_text=True)
