from __future__ import annotations

import gzip
import hashlib
import io
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from flask import Flask
from flask.testing import FlaskClient

from scrimmage.services import bots, builds, matches
from scrimmage.services.storage import Storage
from scrimmage.settings import Settings
from tests.conftest import add_team_with_bot


def headers(
    worker: str = "w1", token: str = "test-token", commit: str = "test-commit"
) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "X-Worker": worker, "X-Scrimmage-Commit": commit}


def status(cores: int) -> dict[str, Any]:
    resources = {"cores": cores, "memory_mb": 64_000}
    return {"total": resources, "free": resources}


@pytest.fixture
def api(app: Flask) -> FlaskClient:
    return app.test_client()


@pytest.fixture
def queued(
    conn: sqlite3.Connection, storage: Storage, tmp_path: Path, python_bot_zip: bytes
) -> tuple[int, int]:
    a, _ = add_team_with_bot(conn, storage, "Aces", python_bot_zip, tmp_path)
    b, _ = add_team_with_bot(conn, storage, "Kings", python_bot_zip, tmp_path)
    matches.challenge(conn, Settings(conn), a, b)
    return a, b


def claim(api: FlaskClient, worker: str = "w1") -> list[dict[str, Any]]:
    response = api.post("/api/worker/claim", json=status(4), headers=headers(worker))
    assert response.status_code == 200
    games: list[dict[str, Any]] = response.get_json()["games"]
    return games


def submit(api: FlaskClient, game_id: int, outcome: dict[str, Any], worker: str = "w1") -> Any:
    return api.post(
        f"/api/worker/games/{game_id}/result",
        data={
            "outcome": json.dumps(outcome),
            "game": (io.BytesIO(gzip.compress(b"Final, A (3), B (-3)")), "game.log.gz"),
        },
        headers=headers(worker),
        content_type="multipart/form-data",
    )


def test_authentication(api: FlaskClient) -> None:
    assert api.get("/api/worker/version").get_json() == {"commit": "test-commit"}
    assert api.post("/api/worker/claim", json={}).status_code == 401
    assert api.post("/api/worker/claim", json={}, headers=headers(token="nope")).status_code == 401
    outdated = api.post("/api/worker/claim", json={}, headers=headers(commit="old"))
    assert outdated.status_code == 409 and outdated.get_json()["error"] == "outdated"
    bad_name = api.post("/api/worker/claim", json={}, headers=headers(worker="../x"))
    assert bad_name.status_code == 400


def test_claim_and_result(api: FlaskClient, conn: sqlite3.Connection, queued: Any) -> None:
    games = claim(api)
    assert len(games) == 1
    game = games[0]
    assert set(game["bots"]) == {"a", "b"} and game["params"]["NUM_ROUNDS"] == 1000
    assert claim(api, "w2") == []  # nothing left

    bot = game["bots"]["a"]
    built = f"/api/worker/bots/{bot['id']}/build"
    response = api.get(built, headers=headers())
    assert response.status_code == 200
    assert hashlib.sha256(response.get_data()).hexdigest() == bot["sha256"]
    # Workers can only fetch bots of games they hold, and never their source.
    assert api.get(built, headers=headers("w2")).status_code == 404
    assert api.get(f"/api/worker/bots/{bot['id']}/source", headers=headers()).status_code == 404

    assert submit(api, game["id"], {"scores": [3, -3]}, worker="w2").status_code == 409
    assert submit(api, game["id"], {"scores": [3, -3]}).status_code == 200
    row = matches.get_game(conn, game["id"])
    assert row is not None and row["status"] == "done" and row["score_a"] == 3


def test_forged_scores_are_rejected(
    api: FlaskClient, conn: sqlite3.Connection, queued: Any
) -> None:
    game = claim(api)[0]
    assert submit(api, game["id"], {"scores": [400, 0]}).status_code == 200
    row = matches.get_game(conn, game["id"])
    assert row is not None and row["status"] == "error" and "Invalid result" in row["error"]


def test_logs_must_be_gzip(api: FlaskClient, queued: Any) -> None:
    game = claim(api)[0]
    response = api.post(
        f"/api/worker/games/{game['id']}/result",
        data={"outcome": "{}", "game": (io.BytesIO(b"plain"), "game.log.gz")},
        headers=headers(),
        content_type="multipart/form-data",
    )
    assert response.status_code == 400


def test_heartbeat_and_restart(api: FlaskClient, conn: sqlite3.Connection, queued: Any) -> None:
    game = claim(api)[0]
    beat = api.post(
        "/api/worker/heartbeat", json={**status(4), "games": [game["id"]]}, headers=headers()
    )
    assert beat.get_json() == {"cancel": [], "cancel_builds": []}
    other = api.post(
        "/api/worker/heartbeat", json={**status(4), "games": [game["id"]]}, headers=headers("w2")
    )
    assert other.get_json() == {"cancel": [game["id"]], "cancel_builds": []}
    # A worker that restarts gives back whatever it held.
    restarted = api.post("/api/worker/start", json=status(4), headers=headers())
    assert restarted.get_json() == {"requeued": 1}
    assert conn.execute("SELECT status FROM games").fetchone()[0] == "queued"


def test_api_is_not_a_user_area(api: FlaskClient) -> None:
    # No CSRF token or session needed, but also nothing reachable without the token.
    assert api.post("/api/worker/stop", headers=headers()).status_code == 200
    assert api.post("/api/worker/stop").status_code == 401


def submit_build(
    api: FlaskClient, build_id: int, outcome: dict[str, Any], archive: bytes | None = None
) -> Any:
    data: dict[str, Any] = {
        "outcome": json.dumps(outcome),
        "log": (io.BytesIO(gzip.compress(b"compiling...")), "build.log.gz"),
    }
    if archive is not None:
        data["bot"] = (io.BytesIO(archive), "bot.tar.gz")
    return api.post(
        f"/api/worker/builds/{build_id}/result",
        data=data,
        headers=headers(),
        content_type="multipart/form-data",
    )


def test_builds(
    api: FlaskClient,
    conn: sqlite3.Connection,
    storage: Storage,
    tmp_path: Path,
    python_bot_zip: bytes,
) -> None:
    team, bot_id = add_team_with_bot(conn, storage, "Aces", python_bot_zip, tmp_path, build=False)
    # Not playable until built.
    assert conn.execute("SELECT current_bot_id FROM teams").fetchone()[0] is None
    response = api.post("/api/worker/claim", json=status(16), headers=headers())
    (job,) = response.get_json()["builds"]
    assert job["params"]["MODE"] == "build" and job["bot"]["id"] == bot_id
    source = f"/api/worker/bots/{bot_id}/source"
    fetched = api.get(source, headers=headers())
    assert hashlib.sha256(fetched.get_data()).hexdigest() == job["bot"]["sha256"]
    assert api.get(source, headers=headers("w2")).status_code == 404
    # Leases work like games'.
    beat = api.post(
        "/api/worker/heartbeat", json={**status(16), "builds": [job["id"]]}, headers=headers("w2")
    )
    assert beat.get_json()["cancel_builds"] == [job["id"]]
    # A successful build needs its archive (gzip), and makes the bot current.
    assert submit_build(api, job["id"], {"ok": True}).status_code == 400
    assert submit_build(api, job["id"], {"ok": True}, b"not gzip").status_code == 400
    archive = gzip.compress(b"tar")
    ok = submit_build(api, job["id"], {"ok": True, "seconds": 3.5}, archive)
    assert ok.status_code == 200
    build = builds.get(conn, bot_id)
    assert build is not None and build["status"] == "ready"
    assert build["sha256"] == hashlib.sha256(archive).hexdigest()
    assert storage.build_archive(bot_id).read_bytes() == archive
    assert gzip.decompress(storage.build_log(bot_id).read_bytes()) == b"compiling..."
    current = conn.execute("SELECT current_bot_id FROM teams WHERE id = ?", (team,))
    assert current.fetchone()[0] == bot_id
    # Reporting again (lease gone) is refused.
    assert submit_build(api, job["id"], {"ok": True}, archive).status_code == 409


def test_failed_build_keeps_the_previous_bot(
    api: FlaskClient,
    conn: sqlite3.Connection,
    storage: Storage,
    tmp_path: Path,
    python_bot_zip: bytes,
) -> None:
    team, first = add_team_with_bot(conn, storage, "Aces", python_bot_zip, tmp_path)
    upload = tmp_path / "second.zip"
    upload.write_bytes(python_bot_zip)
    user = conn.execute("SELECT id FROM users").fetchone()[0]
    bots.create(conn, storage, Settings(conn), team, user, "", upload)
    (job,) = api.post("/api/worker/claim", json=status(16), headers=headers()).get_json()["builds"]
    assert submit_build(api, job["id"], {"ok": False, "error": "g++: error"}).status_code == 200
    second = builds.get(conn, job["bot"]["id"])
    assert second is not None and second["status"] == "failed" and "g++" in second["error"]
    current = conn.execute("SELECT current_bot_id FROM teams WHERE id = ?", (team,))
    assert current.fetchone()[0] == first
