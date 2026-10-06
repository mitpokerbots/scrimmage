"""
Building bots once.

A bot's ``commands.json`` has two command lists: ``build`` and ``run``. The
build runs once, right after the upload, alone in a sandbox (only the bot's own
files, no network, its own user, time and memory limits), and whatever it
leaves in the bot's directory becomes the bot. Games start ``run`` directly.

    upload ──► build ──ready──► becomes the team's current bot
                    └─failed──► team sees the build log; nothing changes

Only built bots can be current, and games are only made between current bots,
so every game's bots are already built.
"""

from __future__ import annotations

import json
import sqlite3

from scrimmage.db import Row, now, one, transaction
from scrimmage.services.errors import UserError
from scrimmage.services.hardware import ENGINE_RESERVE_MB, GUARD_SLACK_MB
from scrimmage.settings import Settings

# Builds are leased like games (see queue.py).
LEASE_SECONDS = 90
BUILD_BOT_MEMORY_MB = 4096
BUILD_MEMORY_MB = BUILD_BOT_MEMORY_MB + ENGINE_RESERVE_MB + GUARD_SLACK_MB
_REQUEUE = "status = 'queued', worker = NULL, lease_until = NULL"


def request(conn: sqlite3.Connection, bot_id: int) -> None:
    conn.execute(
        "INSERT INTO builds (bot_id, status, created_at) VALUES (?, 'queued', ?)",
        (bot_id, now()),
    )


def get(conn: sqlite3.Connection, bot_id: int) -> Row | None:
    return one(conn, "SELECT * FROM builds WHERE bot_id = ?", (bot_id,))


def require_ready(conn: sqlite3.Connection, bot_id: int) -> None:
    build = get(conn, bot_id)
    if build is None or build["status"] != "ready":
        raise UserError("Only bots that built successfully can be used.")


def build_cores(settings: Settings, worker_cores: int) -> int:
    """Builds use up to ``build_cores``, but never more than the worker has."""
    return max(1, min(settings.number("build_cores"), worker_cores))


def build_parameters(settings: Settings, bot: Row, cores: int) -> dict[str, int | float | str]:
    return {
        "MODE": "build",
        "BOT_DIR_A": f"bot-{bot['id']}",
        "BUILD_TIMEOUT": float(settings.number("bot_build_timeout_seconds")),
        "CORES": cores,
        "BOT_MEMORY_BYTES": BUILD_BOT_MEMORY_MB << 20,
        "PLAYER_LOG_SIZE_LIMIT": 1 << 20,
    }


def claim(
    conn: sqlite3.Connection,
    worker: str,
    cores: int,
    memory_mb: int,
    per_build_cores: int,
) -> list[Row]:
    """Lease queued builds that fit in the worker's free resources."""
    if per_build_cores > cores or memory_mb < BUILD_MEMORY_MB:
        return []
    fits = min(cores // per_build_cores, memory_mb // BUILD_MEMORY_MB)
    with transaction(conn):
        rows = conn.execute(
            "UPDATE builds SET status = 'running', worker = ?, lease_until = ? "
            "WHERE id IN (SELECT id FROM builds WHERE status = 'queued' ORDER BY id LIMIT ?) "
            "RETURNING *",
            (worker, now() + LEASE_SECONDS, fits),
        ).fetchall()
    return rows


def renew_leases(conn: sqlite3.Connection, worker: str, build_ids: list[int]) -> list[int]:
    """Extend the worker's build leases. Returns the builds it no longer holds."""
    with transaction(conn):
        held = {
            row[0]
            for row in conn.execute(
                "UPDATE builds SET lease_until = ? WHERE worker = ? AND status = 'running' "
                "AND id IN (SELECT value FROM json_each(?)) RETURNING id",
                (now() + LEASE_SECONDS, worker, json.dumps(build_ids)),
            )
        }
    return [build_id for build_id in build_ids if build_id not in held]


def requeue_expired(conn: sqlite3.Connection) -> int:
    cur = conn.execute(
        f"UPDATE builds SET {_REQUEUE} WHERE status = 'running' AND lease_until < ?", (now(),)
    )
    return cur.rowcount


def release(conn: sqlite3.Connection, worker: str) -> int:
    cur = conn.execute(
        f"UPDATE builds SET {_REQUEUE} WHERE status = 'running' AND worker = ?", (worker,)
    )
    return cur.rowcount


def holds(conn: sqlite3.Connection, worker: str, build_id: int) -> Row | None:
    return one(
        conn,
        "SELECT * FROM builds WHERE id = ? AND worker = ? AND status = 'running'",
        (build_id, worker),
    )


def record(
    conn: sqlite3.Connection,
    build_id: int,
    worker: str,
    *,
    ok: bool,
    error: str | None,
    seconds: float,
    size_bytes: int | None,
    sha256: str | None,
) -> bool:
    """Store a build's outcome. Returns False if the worker no longer held it."""
    with transaction(conn):
        build = holds(conn, worker, build_id)
        if build is None:
            return False
        conn.execute(
            "UPDATE builds SET status = ?, error = ?, seconds = ?, size_bytes = ?, sha256 = ?, "
            "lease_until = NULL, finished_at = ? WHERE id = ?",
            (
                "ready" if ok else "failed",
                None if ok else (error or "build failed")[:2000],
                seconds,
                size_bytes,
                sha256,
                now(),
                build_id,
            ),
        )
        bot = one(conn, "SELECT * FROM bots WHERE id = ?", (build["bot_id"],))
        assert bot is not None
        if ok and not bot["is_deleted"]:
            # An upload becomes current once it builds, unless a newer upload
            # of the team already has.
            conn.execute(
                "UPDATE teams SET current_bot_id = ? WHERE id = ? AND NOT EXISTS ("
                "  SELECT 1 FROM bots JOIN builds ON builds.bot_id = bots.id "
                "  WHERE bots.team_id = ? AND bots.id > ? AND NOT bots.is_deleted "
                "  AND builds.status = 'ready')",
                (bot["id"], bot["team_id"], bot["team_id"], bot["id"]),
            )
    return True


def queue_counts(conn: sqlite3.Connection) -> dict[str, int]:
    counts = {"queued": 0, "running": 0}
    for row in conn.execute(
        "SELECT status, count(*) AS n FROM builds WHERE status IN ('queued', 'running') "
        "GROUP BY status"
    ):
        counts[row["status"]] = row["n"]
    return counts
