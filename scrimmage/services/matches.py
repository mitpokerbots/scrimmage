"""
Challenges between teams, and the game listings the website shows.

Challenge rules (unchanged from the previous server):
  * Both teams need a current bot and must not be disabled.
  * If ``down_challenges_require_accept`` is on, a higher-rated challenger needs
    the lower-rated opponent to accept, unless the opponent is a reference team.
  * The team that starts a game (the challenger on auto-accept, the opponent
    when accepting) may have at most ``spawn_limit_per_team`` queued or
    running games.

Playing queued games and recording results is in queue.py.
"""

from __future__ import annotations

import sqlite3

from scrimmage.db import Row, now, one, transaction
from scrimmage.services import hardware
from scrimmage.services.errors import UserError
from scrimmage.services.hardware import GameHardware
from scrimmage.settings import Settings

SCRIMMAGE_PRIORITY = 0
TOURNAMENT_PRIORITY = 10


def _playable(team: Row | None) -> bool:
    return team is not None and not team["is_disabled"] and team["current_bot_id"] is not None


def _outstanding(conn: sqlite3.Connection, team_id: int) -> int:
    count: int = conn.execute(
        "SELECT count(*) FROM games WHERE initiator_id = ? AND status IN ('queued', 'running')",
        (team_id,),
    ).fetchone()[0]
    return count


def _check_spawn_limit(conn: sqlite3.Connection, settings: Settings, team_id: int) -> None:
    if _outstanding(conn, team_id) >= settings.number("spawn_limit_per_team"):
        raise UserError(
            "You already have the maximum number of games queued or running. "
            "Wait for some to finish."
        )


def insert_game(
    conn: sqlite3.Connection,
    *,
    kind: str,
    team_a: Row,
    team_b: Row,
    priority: int,
    hardware: GameHardware,
    request_id: int | None = None,
    initiator_id: int | None = None,
    tournament_id: int | None = None,
) -> int:
    cur = conn.execute(
        "INSERT INTO games (kind, tournament_id, request_id, initiator_id, team_a_id, "
        "team_b_id, bot_a_id, bot_b_id, status, priority, cores, bot_memory_mb, "
        "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?)",
        (
            kind,
            tournament_id,
            request_id,
            initiator_id,
            team_a["id"],
            team_b["id"],
            team_a["current_bot_id"],
            team_b["current_bot_id"],
            priority,
            hardware.cores,
            hardware.bot_memory_mb,
            now(),
        ),
    )
    assert cur.lastrowid is not None
    return cur.lastrowid


def challenge(
    conn: sqlite3.Connection, settings: Settings, challenger_id: int, opponent_id: int
) -> str:
    """Challenge another team. Returns a message describing what happened."""
    if not settings.flag("challenges_enabled"):
        raise UserError("Challenges are currently disabled.")
    if challenger_id == opponent_id:
        raise UserError("You cannot challenge your own team.")
    with transaction(conn):
        challenger = one(conn, "SELECT * FROM teams WHERE id = ?", (challenger_id,))
        opponent = one(conn, "SELECT * FROM teams WHERE id = ?", (opponent_id,))
        if not _playable(challenger):
            raise UserError("Upload a bot before challenging other teams.")
        assert challenger is not None
        if not _playable(opponent):
            raise UserError("That team cannot be challenged right now.")
        assert opponent is not None
        if settings.flag("challenges_only_reference") and not opponent["is_reference"]:
            raise UserError("Right now you may only challenge reference teams.")
        duplicate = one(
            conn,
            "SELECT 1 FROM game_requests WHERE challenger_id = ? AND opponent_id = ? "
            "AND status = 'pending'",
            (challenger_id, opponent_id),
        )
        if duplicate is not None:
            raise UserError(f"You already have a pending challenge to {opponent['name']}.")

        needs_accept = (
            settings.flag("down_challenges_require_accept")
            and not opponent["is_reference"]
            and challenger["elo"] > opponent["elo"]
        )
        if needs_accept:
            conn.execute(
                "INSERT INTO game_requests (challenger_id, opponent_id, status, created_at) "
                "VALUES (?, ?, 'pending', ?)",
                (challenger_id, opponent_id, now()),
            )
            return (
                f"Challenged {opponent['name']}. They are rated lower, "
                "so they have to accept first."
            )

        _check_spawn_limit(conn, settings, challenger_id)
        request_id = conn.execute(
            "INSERT INTO game_requests (challenger_id, opponent_id, status, created_at, "
            "decided_at) VALUES (?, ?, 'accepted', ?, ?) RETURNING id",
            (challenger_id, opponent_id, now(), now()),
        ).fetchone()[0]
        insert_game(
            conn,
            kind="scrimmage",
            team_a=challenger,
            team_b=opponent,
            priority=SCRIMMAGE_PRIORITY,
            hardware=hardware.for_new_games(settings),
            request_id=request_id,
            initiator_id=challenger_id,
        )
    return f"Challenged {opponent['name']}. The game is in the queue."


def answer_request(
    conn: sqlite3.Connection, settings: Settings, team_id: int, request_id: int, accept: bool
) -> str:
    with transaction(conn):
        request = one(
            conn,
            "SELECT * FROM game_requests WHERE id = ? AND opponent_id = ? AND status = 'pending'",
            (request_id, team_id),
        )
        if request is None:
            raise UserError("That challenge is no longer pending.")
        challenger = one(conn, "SELECT * FROM teams WHERE id = ?", (request["challenger_id"],))
        assert challenger is not None
        if not accept:
            conn.execute(
                "UPDATE game_requests SET status = 'rejected', decided_at = ? WHERE id = ?",
                (now(), request_id),
            )
            return f"Rejected the challenge from {challenger['name']}."
        opponent = one(conn, "SELECT * FROM teams WHERE id = ?", (team_id,))
        if not _playable(challenger) or not _playable(opponent):
            raise UserError("Both teams need a current bot to play.")
        assert opponent is not None
        _check_spawn_limit(conn, settings, team_id)
        conn.execute(
            "UPDATE game_requests SET status = 'accepted', decided_at = ? WHERE id = ?",
            (now(), request_id),
        )
        insert_game(
            conn,
            kind="scrimmage",
            team_a=challenger,
            team_b=opponent,
            priority=SCRIMMAGE_PRIORITY,
            hardware=hardware.for_new_games(settings),
            request_id=request_id,
            initiator_id=team_id,
        )
    return f"Accepted the challenge from {challenger['name']}. The game is in the queue."


def cancel_request(conn: sqlite3.Connection, team_id: int, request_id: int) -> None:
    cur = conn.execute(
        "UPDATE game_requests SET status = 'cancelled', decided_at = ? "
        "WHERE id = ? AND challenger_id = ? AND status = 'pending'",
        (now(), request_id, team_id),
    )
    if cur.rowcount != 1:
        raise UserError("That challenge is no longer pending.")


def incoming_requests(conn: sqlite3.Connection, team_id: int) -> list[Row]:
    return conn.execute(
        "SELECT r.*, t.name AS team_name, t.elo AS team_elo FROM game_requests r "
        "JOIN teams t ON t.id = r.challenger_id "
        "WHERE r.opponent_id = ? AND r.status = 'pending' ORDER BY r.id DESC",
        (team_id,),
    ).fetchall()


def outgoing_requests(conn: sqlite3.Connection, team_id: int) -> list[Row]:
    return conn.execute(
        "SELECT r.*, t.name AS team_name, t.elo AS team_elo FROM game_requests r "
        "JOIN teams t ON t.id = r.opponent_id "
        "WHERE r.challenger_id = ? AND r.status = 'pending' ORDER BY r.id DESC",
        (team_id,),
    ).fetchall()


# ---------------------------------------------------------------------------
# Listings
# ---------------------------------------------------------------------------

_GAME_SELECT = (
    "SELECT g.*, ta.name AS team_a_name, tb.name AS team_b_name, "
    "ba.name AS bot_a_name, bb.name AS bot_b_name, tr.title AS tournament_title "
    "FROM games g "
    "JOIN teams ta ON ta.id = g.team_a_id JOIN teams tb ON tb.id = g.team_b_id "
    "JOIN bots ba ON ba.id = g.bot_a_id JOIN bots bb ON bb.id = g.bot_b_id "
    "LEFT JOIN tournaments tr ON tr.id = g.tournament_id "
)


def get_game(conn: sqlite3.Connection, game_id: int) -> Row | None:
    return one(conn, _GAME_SELECT + "WHERE g.id = ?", (game_id,))


def recent_scrimmages(conn: sqlite3.Connection, limit: int) -> list[Row]:
    return conn.execute(
        _GAME_SELECT + "WHERE g.kind = 'scrimmage' ORDER BY g.id DESC LIMIT ?", (limit,)
    ).fetchall()


def team_games(conn: sqlite3.Connection, team_id: int, limit: int, offset: int) -> list[Row]:
    return conn.execute(
        _GAME_SELECT + "WHERE g.kind = 'scrimmage' AND g.id IN ("
        "  SELECT id FROM games WHERE team_a_id = ? UNION ALL "
        "  SELECT id FROM games WHERE team_b_id = ?"
        ") ORDER BY g.id DESC LIMIT ? OFFSET ?",
        (team_id, team_id, limit, offset),
    ).fetchall()


def all_games(conn: sqlite3.Connection, status: str | None, limit: int, offset: int) -> list[Row]:
    return search_games(conn, limit, offset, status=status)


def search_games(
    conn: sqlite3.Connection,
    limit: int,
    offset: int,
    *,
    status: str | None = None,
    team: str = "",
    tournament_id: int | None = None,
    text: str = "",
) -> list[Row]:
    """Newest first. ``team`` matches either team's name; ``text`` the error or worker."""
    where: list[str] = []
    args: list[str | int] = []
    if status:
        where.append("g.status = ?")
        args.append(status)
    if team:
        where.append("(ta.name LIKE ? OR tb.name LIKE ?)")
        args += [f"%{team}%"] * 2
    if tournament_id is not None:
        where.append("g.tournament_id = ?")
        args.append(tournament_id)
    if text:
        where.append("(g.error LIKE ? OR g.worker LIKE ?)")
        args += [f"%{text}%"] * 2
    clause = f"WHERE {' AND '.join(where)} " if where else ""
    return conn.execute(
        _GAME_SELECT + clause + "ORDER BY g.id DESC LIMIT ? OFFSET ?", (*args, limit, offset)
    ).fetchall()


def tournament_games(conn: sqlite3.Connection, tournament_id: int) -> list[Row]:
    return conn.execute(
        _GAME_SELECT + "WHERE g.tournament_id = ? ORDER BY g.id", (tournament_id,)
    ).fetchall()


def queue_counts(conn: sqlite3.Connection) -> dict[str, int]:
    counts = {"queued": 0, "running": 0, "error": 0}
    for row in conn.execute(
        "SELECT status, count(*) AS n FROM games WHERE status IN ('queued', 'running', 'error') "
        "GROUP BY status"
    ):
        counts[row["status"]] = row["n"]
    return counts


def elo_history(conn: sqlite3.Connection, team_id: int) -> list[tuple[int, float]]:
    """(finished_at, rating after the game) for every rated scrimmage of a team."""
    return [
        (row[0], row[1])
        for row in conn.execute(
            "SELECT finished_at, CASE WHEN team_a_id = ? THEN elo_a_after ELSE elo_b_after END "
            "FROM games WHERE kind = 'scrimmage' AND status = 'done' "
            "AND elo_a_after IS NOT NULL AND (team_a_id = ? OR team_b_id = ?) "
            "ORDER BY finished_at, id",
            (team_id, team_id, team_id),
        )
    ]
