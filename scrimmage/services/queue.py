"""
The game queue and results: what the worker API calls.

Games wait in the ``games`` table with status 'queued'. A worker claims a
batch, which marks them 'running' under a lease in that worker's name, and
renews the lease every few seconds while it plays. A game whose lease runs out
(the worker crashed, lost its network, or its spot instance was reclaimed) goes
back to 'queued'. Results are accepted only from the worker holding the lease.
Scrimmage results move the ladder (Elo); tournament results are rated later
(tournaments.update_ratings).

If ``down_challenges_affect_elo`` is off, a game the higher-rated team
initiated as challenger does not change Elo.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass

from scrimmage.db import Row, now, one, transaction
from scrimmage.services import ratings
from scrimmage.services.errors import UserError
from scrimmage.services.hardware import GameHardware
from scrimmage.settings import Settings

FINAL_LINE = re.compile(r"^Final, (\w+) \((-?\d+)\), (\w+) \((-?\d+)\)\s*$", re.MULTILINE)


def parse_scores(game_log: str) -> tuple[int, int] | None:
    """Return (score of A, score of B) from the engine's ``Final`` line.

    Seats swap every hand, so the order on the Final line depends on the
    parity of the hand count; match by name rather than position.
    """
    match = FINAL_LINE.search(game_log)
    if match is None:
        return None
    scores = {match.group(1): int(match.group(2)), match.group(3): int(match.group(4))}
    if set(scores) != {"A", "B"}:
        return None
    return scores["A"], scores["B"]


def winner_of(score_a: int, score_b: int) -> str:
    if score_a > score_b:
        return "a"
    if score_b > score_a:
        return "b"
    return "tie"


LEASE_SECONDS = 90
_REQUEUE = "status = 'queued', started_at = NULL, worker = NULL, lease_until = NULL"


CLAIM_WINDOW = 64


@dataclass(frozen=True)
class Resources:
    cores: int
    memory_mb: int

    def fits(self, game: GameHardware) -> bool:
        return game.cores <= self.cores and game.memory_mb <= self.memory_mb


def hardware_of(game: Row) -> GameHardware:
    return GameHardware(cores=game["cores"], bot_memory_mb=game["bot_memory_mb"])


def claim(conn: sqlite3.Connection, worker: str, free: Resources, total: Resources) -> list[Row]:
    """Lease the next queued games that fit in the worker's free resources.

    Games are taken in queue order. One that could never fit on this worker is
    skipped (another machine will take it); one that merely doesn't fit right
    now stops the claim, so big games are not starved by small ones behind them.
    """
    with transaction(conn):
        # Only games whose bots are both built.
        candidates = conn.execute(
            "SELECT g.* FROM games g "
            "JOIN builds ba ON ba.bot_id = g.bot_a_id JOIN builds bb ON bb.bot_id = g.bot_b_id "
            "WHERE g.status = 'queued' AND ba.status = 'ready' AND bb.status = 'ready' "
            "ORDER BY g.priority, g.id LIMIT ?",
            (CLAIM_WINDOW,),
        ).fetchall()
        chosen = []
        cores, memory_mb = free.cores, free.memory_mb
        for game in candidates:
            needs = hardware_of(game)
            if not total.fits(needs):
                continue
            if not Resources(cores, memory_mb).fits(needs):
                break
            chosen.append(game["id"])
            cores -= needs.cores
            memory_mb -= needs.memory_mb
        if not chosen:
            return []
        rows = conn.execute(
            "UPDATE games SET status = 'running', started_at = ?, worker = ?, lease_until = ? "
            "WHERE id IN (SELECT value FROM json_each(?)) RETURNING *",
            (now(), worker, now() + LEASE_SECONDS, json.dumps(chosen)),
        ).fetchall()
    return sorted(rows, key=lambda r: (r["priority"], r["id"]))


def renew_leases(conn: sqlite3.Connection, worker: str, game_ids: list[int]) -> list[int]:
    """Extend the worker's leases. Returns the games it no longer holds."""
    with transaction(conn):
        held = {
            row[0]
            for row in conn.execute(
                "UPDATE games SET lease_until = ? WHERE worker = ? AND status = 'running' "
                "AND id IN (SELECT value FROM json_each(?)) RETURNING id",
                (now() + LEASE_SECONDS, worker, json.dumps(game_ids)),
            )
        }
    return [game_id for game_id in game_ids if game_id not in held]


def requeue_expired(conn: sqlite3.Connection) -> int:
    cur = conn.execute(
        f"UPDATE games SET {_REQUEUE} WHERE status = 'running' AND lease_until < ?", (now(),)
    )
    return cur.rowcount


def release(conn: sqlite3.Connection, worker: str, game_ids: list[int] | None = None) -> int:
    """Requeue games a worker holds (all of them, or ``game_ids``)."""
    if game_ids is None:
        cur = conn.execute(
            f"UPDATE games SET {_REQUEUE} WHERE status = 'running' AND worker = ?", (worker,)
        )
        return cur.rowcount
    cur = conn.execute(
        f"UPDATE games SET {_REQUEUE} WHERE status = 'running' AND worker = ? "
        "AND id IN (SELECT value FROM json_each(?))",
        (worker, json.dumps(game_ids)),
    )
    return cur.rowcount


def retry_failed(conn: sqlite3.Connection, game_id: int) -> None:
    cur = conn.execute(
        f"UPDATE games SET {_REQUEUE}, error = NULL, finished_at = NULL "
        "WHERE id = ? AND status = 'error'",
        (game_id,),
    )
    if cur.rowcount != 1:
        raise UserError("Only failed games can be retried.")


def retry_failed_scrimmages(conn: sqlite3.Connection) -> int:
    cur = conn.execute(
        f"UPDATE games SET {_REQUEUE}, error = NULL, finished_at = NULL "
        "WHERE status = 'error' AND kind = 'scrimmage'"
    )
    return cur.rowcount


@dataclass(frozen=True)
class Result:
    score_a: int
    score_b: int


class LeaseLost(Exception):
    """The game is no longer running under this worker's lease."""


def _held(conn: sqlite3.Connection, game_id: int, worker: str | None) -> Row:
    game = one(conn, "SELECT * FROM games WHERE id = ? AND status = 'running'", (game_id,))
    if game is None or (worker is not None and game["worker"] != worker):
        raise LeaseLost(f"game {game_id} is not running under worker {worker!r}")
    return game


def record_result(
    conn: sqlite3.Connection,
    settings: Settings,
    game_id: int,
    result: Result,
    worker: str | None = None,
    stats: dict[str, object] | None = None,
) -> None:
    if result.score_a + result.score_b != 0:
        # Heads-up poker is zero-sum; anything else is a broken or forged report.
        raise ValueError(f"scores {result.score_a} and {result.score_b} do not sum to zero")
    winner = winner_of(result.score_a, result.score_b)
    with transaction(conn):
        game = _held(conn, game_id, worker)
        updates: dict[str, object] = {
            "status": "done",
            "score_a": result.score_a,
            "score_b": result.score_b,
            "winner": winner,
            "finished_at": now(),
            "lease_until": None,
            "stats": json.dumps(stats) if stats else None,
        }
        if game["kind"] == "scrimmage":
            updates.update(_apply_scrimmage(conn, settings, game, winner))
        columns = ", ".join(f"{key} = ?" for key in updates)
        conn.execute(f"UPDATE games SET {columns} WHERE id = ?", (*updates.values(), game_id))


def _apply_scrimmage(
    conn: sqlite3.Connection, settings: Settings, game: Row, winner: str
) -> dict[str, object]:
    team_a = one(conn, "SELECT * FROM teams WHERE id = ?", (game["team_a_id"],))
    team_b = one(conn, "SELECT * FROM teams WHERE id = ?", (game["team_b_id"],))
    assert team_a is not None and team_b is not None
    # Team A is always the challenger.
    rated = settings.flag("down_challenges_affect_elo") or team_a["elo"] <= team_b["elo"]
    elo_a, elo_b = team_a["elo"], team_b["elo"]
    if rated:
        elo_a, elo_b = ratings.elo_update(team_a["elo"], team_b["elo"], winner)
    for side, team_id, bot_id, new_elo in (
        ("a", game["team_a_id"], game["bot_a_id"], elo_a),
        ("b", game["team_b_id"], game["bot_b_id"], elo_b),
    ):
        column = "ties" if winner == "tie" else ("wins" if winner == side else "losses")
        conn.execute(
            f"UPDATE teams SET elo = ?, {column} = {column} + 1 WHERE id = ?", (new_elo, team_id)
        )
        conn.execute(f"UPDATE bots SET {column} = {column} + 1 WHERE id = ?", (bot_id,))
    return {
        "elo_a_before": team_a["elo"],
        "elo_b_before": team_b["elo"],
        "elo_a_after": elo_a,
        "elo_b_after": elo_b,
    }


def record_error(
    conn: sqlite3.Connection, game_id: int, message: str, worker: str | None = None
) -> None:
    with transaction(conn):
        _held(conn, game_id, worker)
        conn.execute(
            "UPDATE games SET status = 'error', error = ?, finished_at = ?, lease_until = NULL "
            "WHERE id = ?",
            (message[:2000], now(), game_id),
        )


def is_idle(conn: sqlite3.Connection, minutes: int, since: int = 0) -> bool:
    """No game queued or running, and none finished in the last ``minutes``
    (counting from ``since`` if that is later)."""
    busy = conn.execute(
        "SELECT 1 FROM games WHERE status IN ('queued', 'running') LIMIT 1"
    ).fetchone()
    if busy is not None:
        return False
    last = conn.execute("SELECT max(finished_at) FROM games").fetchone()[0] or 0
    return now() - max(last, since) >= minutes * 60


def match_parameters(settings: Settings, game: Row) -> dict[str, int | float | str]:
    """Everything the match container needs to know about one game.

    Engine values match the names in game/config_template.py; the hardware
    values are read by game/run_match.py.
    """
    needs = hardware_of(game)
    return {
        "NUM_ROUNDS": settings.number("game_num_hands"),
        "STARTING_STACK": settings.number("game_starting_stack"),
        "BIG_BLIND": settings.number("game_big_blind"),
        "SMALL_BLIND": settings.number("game_small_blind"),
        "STARTING_GAME_CLOCK": float(settings.number("game_time_bank_seconds")),
        "BUILD_TIMEOUT": float(settings.number("bot_build_timeout_seconds")),
        "CONNECT_TIMEOUT": float(settings.number("bot_connect_timeout_seconds")),
        "PLAYER_LOG_SIZE_LIMIT": settings.number("player_log_size_limit"),
        "BOT_DIR_A": f"bot-{game['bot_a_id']}",
        "BOT_DIR_B": f"bot-{game['bot_b_id']}"
        + ("-b" if game["bot_a_id"] == game["bot_b_id"] else ""),
        "CORES": needs.cores,
        "BOT_MEMORY_BYTES": needs.bot_memory_mb << 20,
    }


def hard_timeout_seconds(params: dict[str, int | float | str]) -> int:
    """Wall-clock budget for a whole match container before it is killed."""
    budget = (
        4 * float(params["CONNECT_TIMEOUT"])
        + 2 * float(params["STARTING_GAME_CLOCK"])
        + 0.02 * float(params["NUM_ROUNDS"])
        + 120
    )
    return int(budget)


def cancel_queued(conn: sqlite3.Connection, game_id: int) -> None:
    cur = conn.execute(
        "UPDATE games SET status = 'error', error = 'Cancelled by an admin.', finished_at = ? "
        "WHERE id = ? AND status = 'queued'",
        (now(), game_id),
    )
    if cur.rowcount != 1:
        raise UserError("Only queued games can be cancelled.")
