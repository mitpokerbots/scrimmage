from __future__ import annotations

import gzip
import io
import sqlite3
import tarfile

import pytest
from flask import Flask

from scrimmage import settings as settings_module
from scrimmage.services import queue
from scrimmage.services.storage import Storage
from scrimmage.settings import Settings
from tests.conftest import Client, claim_one


def upload(client: Client, data: bytes, name: str = "") -> object:
    return client.post(
        "/team/bots",
        {"file": (io.BytesIO(data), "bot.zip"), "name": name},
        content_type="multipart/form-data",
    )


def setup_team(make_client, kerberos: str, team: str, bot: bytes) -> Client:  # type: ignore[no-untyped-def]
    c: Client = make_client(kerberos)
    c.post("/team/create", {"name": team})
    upload(c, bot)
    c.finish_builds()
    return c


def test_logged_out_pages(client: Client) -> None:
    assert client.get("/").status_code == 200
    assert client.get("/healthz").get_data(as_text=True) == "ok\n"
    assert client.get("/announcements").status_code == 200
    # Pages that need a login redirect to it.
    response = client.get("/team")
    assert response.status_code == 302 and "/login" in response.headers["Location"]
    assert client.get("/admin/").status_code == 404


def test_security_headers(client: Client) -> None:
    headers = client.get("/").headers
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
    assert headers["X-Content-Type-Options"] == "nosniff"


def test_csrf_required(client: Client) -> None:
    client.login("alice")
    response = client.http.post("/team/create", data={"name": "NoToken"})
    assert response.status_code == 400
    assert "session expired" in response.get_data(as_text=True)


def test_team_lifecycle(make_client, python_bot_zip: bytes) -> None:  # type: ignore[no-untyped-def]
    alice: Client = make_client("alice")
    page = alice.get("/").get_data(as_text=True)
    assert "Create a team" in page
    alice.post("/team/create", {"name": "Aces"})
    response = upload(alice, python_bot_zip, "first")
    assert response.status_code == 302  # type: ignore[attr-defined]
    page = alice.get("/team").get_data(as_text=True)
    assert "first" in page and "Building…" in page and 'class="positive"' not in page
    assert alice.get("/bots/1/build-log").status_code == 404  # not built yet
    alice.finish_builds()
    page = alice.get("/team").get_data(as_text=True)
    assert 'class="positive"' in page and "Built" in page

    bob: Client = make_client("bob")
    teams_page = bob.get("/").get_data(as_text=True)
    assert "Aces" in teams_page
    team_id = 1
    bob.post("/team/join", {"team_id": team_id})
    alice.post("/team/join-requests/2", {"action": "accept"})
    assert "Aces" in bob.get("/team").get_data(as_text=True)

    # Bots download only for the owning team.
    assert alice.get("/bots/1/download").status_code == 200
    carol: Client = make_client("carol")
    assert carol.get("/bots/1/download").status_code == 404
    # So do build logs.
    assert carol.get("/bots/1/build-log").status_code == 404
    assert alice.get("/bots/1/build-log").status_code == 200


def test_bad_upload_shows_reason(make_client) -> None:  # type: ignore[no-untyped-def]
    alice: Client = make_client("alice")
    alice.post("/team/create", {"name": "Aces"})
    response = upload(alice, b"not a zip")
    page = alice.get("/team").get_data(as_text=True)
    assert response.status_code == 302  # type: ignore[attr-defined]
    assert "not a zip archive" in page


def test_upload_size_limit(make_client, conn: sqlite3.Connection) -> None:  # type: ignore[no-untyped-def]
    settings_module.update(conn, "max_bot_upload_mb", "1")
    alice: Client = make_client("alice")
    alice.post("/team/create", {"name": "Aces"})
    upload(alice, b"x" * (2 << 20))
    assert "at most 1 MB" in alice.get("/team").get_data(as_text=True)


def test_challenge_and_logs(
    make_client, conn: sqlite3.Connection, storage: Storage, python_bot_zip: bytes
) -> None:  # type: ignore[no-untyped-def]
    alice = setup_team(make_client, "alice", "Aces", python_bot_zip)
    bob = setup_team(make_client, "bob", "Kings", python_bot_zip)
    carol = setup_team(make_client, "carol", "Queens", python_bot_zip)
    home = alice.get("/").get_data(as_text=True)
    assert "Team Standings" in home and 'title="Challenge"' in home

    alice.post("/challenge", {"team_id": 2})
    game = claim_one(conn)
    assert game is not None
    for kind in ("game", "a", "b", "engine"):
        storage.write_log(game["id"], kind, gzip.compress(f"{kind} log contents".encode()))
    queue.record_result(conn, Settings(conn), game["id"], queue.Result(50, -50))

    games_page = alice.get("/games").get_data(as_text=True)
    assert "Completed" in games_page and 'class="positive"' in games_page

    # Logs are served gzipped when the client accepts it.
    response = alice.get("/games/1/logs/game", headers={"Accept-Encoding": "gzip"})
    assert response.status_code == 200
    assert response.headers["Content-Encoding"] == "gzip"
    assert gzip.decompress(response.get_data()) == b"game log contents"
    plain = alice.get("/games/1/logs/a")
    assert plain.get_data() == b"a log contents"

    # Each team sees only its own bot's output; outsiders see nothing.
    assert alice.get("/games/1/logs/b").status_code == 404
    assert bob.get("/games/1/logs/b").status_code == 200
    assert bob.get("/games/1/logs/a").status_code == 404
    assert carol.get("/games/1/logs/game").status_code == 404
    assert alice.get("/games/1/logs/passwd").status_code == 404


def test_downward_challenge_accept_flow(
    make_client, conn: sqlite3.Connection, python_bot_zip: bytes
) -> None:  # type: ignore[no-untyped-def]
    alice = setup_team(make_client, "alice", "Aces", python_bot_zip)
    bob = setup_team(make_client, "bob", "Kings", python_bot_zip)
    conn.execute("UPDATE teams SET elo = 1700 WHERE name = 'Aces'")
    alice.post("/challenge", {"team_id": 2})
    assert "Aces" in bob.get("/").get_data(as_text=True).split("<h2>Challenges</h2>")[1]
    bob.post("/challenges/1/answer", {"action": "accept"})
    assert claim_one(conn) is not None


def test_touchstone_login(touchstone_app: Flask) -> None:
    http = touchstone_app.test_client()
    response = http.get("/login?next=/team")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/auth/touchstone")

    # Without Apache in front, the header is missing: refuse rather than guess.
    assert http.get("/auth/touchstone").status_code == 500

    denied = http.get("/auth/touchstone", headers={"X-Remote-User": "someone@harvard.edu"})
    assert denied.status_code == 403

    response = http.get(
        "/auth/touchstone",
        headers={"X-Remote-User": "Alice@MIT.EDU", "X-Display-Name": "Alice Ng;Alice"},
    )
    assert response.status_code == 302 and response.headers["Location"] == "/team"
    page = http.get("/").get_data(as_text=True)
    assert "Log out alice" in page

    # Dev login is not available in touchstone mode.
    assert http.post("/auth/dev", data={"kerberos": "x"}).status_code in (400, 404)


def test_login_link(app: Flask, conn: sqlite3.Connection) -> None:
    from scrimmage.services import accounts

    token = accounts.mint_login_token(conn, "boss")
    client = Client(app.test_client())
    assert client.get(f"/auth/link/{token}").status_code == 200  # confirmation page only
    response = client.post(f"/auth/link/{token}")
    assert response.status_code == 302
    assert "Admin" in client.get("/").get_data(as_text=True)  # boss is a bootstrap admin
    other = Client(app.test_client())
    other.post(f"/auth/link/{token}")
    assert "already used" in other.get("/").get_data(as_text=True)


def test_open_redirect_blocked(client: Client) -> None:
    client.get("/login?next=https://evil.example/")
    response = client.post("/auth/dev", {"kerberos": "alice"})
    assert response.headers["Location"] == "/"


def test_admin_pages(make_client, python_bot_zip: bytes) -> None:  # type: ignore[no-untyped-def]
    setup_team(make_client, "alice", "Aces", python_bot_zip)
    setup_team(make_client, "bob", "Kings", python_bot_zip)
    boss: Client = make_client("boss")
    for path in (
        "/admin/",
        "/admin/settings",
        "/admin/teams",
        "/admin/users",
        "/admin/games",
        "/admin/games?status=error",
        "/admin/tournaments",
        "/admin/announcements",
        "/sponsor/",
        "/sponsor/teams/1",
    ):
        response = boss.get(path)
        assert response.status_code == 200, path

    dashboard = boss.get("/admin/").get_data(as_text=True)
    assert "8 cores, 12 GB per bot" in dashboard and "Graviton4" in dashboard
    estimate = boss.get("/admin/tournaments?teams=100&games_per_pair=2&minutes=2&at_once=200")
    page = estimate.get_data(as_text=True)
    assert "9,900 games" in page and "<td>8 cores</td>" in page

    boss.post("/admin/settings", {"key": "game_num_hands", "value": "100"})
    assert "<td>100</td>" in boss.get("/admin/settings").get_data(as_text=True)
    boss.post("/admin/settings", {"key": "game_num_hands", "value": "-1"})
    assert "at least" in boss.get("/admin/settings").get_data(as_text=True)

    boss.post(
        "/admin/tournaments", {"title": "Week 1", "games_per_pair": "2", "team_ids": ["1", "2"]}
    )
    page = boss.get("/tournaments/1").get_data(as_text=True)
    assert (
        "Week 1" in page and '<div class="value">2</div>\n  <div class="label">Queued</div>' in page
    )
    csv_rows = boss.get("/admin/tournaments/1/games.csv").get_data(as_text=True).splitlines()
    assert csv_rows[0].startswith("id,status,team_a_name") and len(csv_rows) == 3
    assert boss.get("/admin/tournaments/1/logs.tar").status_code == 200
    assert "Aces" in boss.get("/admin/games?tournament=1&team=ace").get_data(as_text=True)
    assert "No games" in boss.get("/admin/games?team=nobody").get_data(as_text=True)
    assert boss.get("/admin/games/1").status_code == 200
    boss.post("/admin/tournaments/1/cancel")
    assert "Removed 2 queued games" in boss.get("/tournaments/1").get_data(as_text=True)

    boss.post("/admin/announcements", {"title": "Hello", "body": "World", "is_public": "1"})
    assert "Hello" in make_client().get("/").get_data(as_text=True)

    boss.post("/admin/teams", {"name": "Reference 1", "is_reference": "1"})
    assert "Reference 1" in boss.get("/admin/teams").get_data(as_text=True)


def test_impersonation(make_client, python_bot_zip: bytes) -> None:  # type: ignore[no-untyped-def]
    setup_team(make_client, "alice", "Aces", python_bot_zip)
    boss: Client = make_client("boss")
    boss.post("/admin/impersonate", {"kerberos": "alice"})
    page = boss.get("/team").get_data(as_text=True)
    assert "viewing the site as" in page and "Aces" in page
    boss.post("/admin/impersonate/stop")
    assert "viewing the site as" not in boss.get("/").get_data(as_text=True)

    # Non-admins cannot impersonate.
    alice: Client = make_client("alice")
    assert alice.post("/admin/impersonate", {"kerberos": "boss"}).status_code == 404


def test_sponsor_basic_auth(client: Client, conn: sqlite3.Connection) -> None:
    import base64

    def auth(password: str) -> dict[str, str]:
        token = base64.b64encode(f"sponsor:{password}".encode()).decode()
        return {"Authorization": f"Basic {token}"}

    # Portal is off until a password is set.
    assert client.get("/sponsor/", headers=auth("")).status_code == 401
    settings_module.update(conn, "sponsor_portal_password", "hunter2")
    assert client.get("/sponsor/", headers=auth("wrong")).status_code == 401
    assert client.get("/sponsor/", headers=auth("hunter2")).status_code == 200


@pytest.mark.parametrize("path", ["/admin/teams", "/admin/users", "/admin/settings"])
def test_admin_hidden_from_players(make_client, path: str) -> None:  # type: ignore[no-untyped-def]
    alice: Client = make_client("alice")
    assert alice.get(path).status_code == 404


def test_admin_game_history(
    make_client, conn: sqlite3.Connection, storage: Storage, python_bot_zip: bytes
) -> None:  # type: ignore[no-untyped-def]
    setup_team(make_client, "alice", "Aces", python_bot_zip)
    setup_team(make_client, "bob", "Kings", python_bot_zip)
    boss: Client = make_client("boss")
    boss.post(
        "/admin/tournaments", {"title": "Final", "games_per_pair": "1", "team_ids": ["1", "2"]}
    )
    game = claim_one(conn)
    assert game is not None
    storage.write_log(game["id"], "game", gzip.compress(b"Final, A (5), B (-5)"))
    stats = {
        "bots": {
            "A": {"connected": True, "clock_used": 12.5, "bankroll": 5, "peak_memory_mb": 300},
            "B": {"connected": True, "clock_used": 30.0, "clock_out": True, "bankroll": -5},
        },
        "events": ["B kept the engine waiting past its game clock; its processes were killed"],
        "machine": "m8g.8xlarge",
        "seconds": 95.0,
    }
    queue.record_result(conn, Settings(conn), game["id"], queue.Result(5, -5), "test-worker", stats)

    page = boss.get(f"/admin/games/{game['id']}").get_data(as_text=True)
    assert "m8g.8xlarge" in page and "12.50 s" in page and "past its game clock" in page
    rows = boss.get("/admin/tournaments/1/games.csv").get_data(as_text=True).splitlines()
    assert "m8g.8xlarge" in rows[1] and ",12.5," in rows[1]
    archive = boss.get("/admin/tournaments/1/logs.tar").get_data()
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        member = tar.extractfile(f"game-{game['id']}/game.log.gz")
        assert member is not None and gzip.decompress(member.read()) == b"Final, A (5), B (-5)"
