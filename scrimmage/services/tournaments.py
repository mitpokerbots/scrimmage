"""
Round-robin tournaments.

Every pair of entrants plays ``games_per_pair`` games with alternating seats,
using the bots the teams had when the tournament started. Tournament games
share the queue at a lower priority than scrimmages. Ratings (with error bars)
are refitted every minute while games finish; once none are left the
tournament is done.
"""

from __future__ import annotations

import itertools
import random
import sqlite3

from scrimmage.db import Row, now, one, transaction
from scrimmage.services import hardware, ratings
from scrimmage.services.errors import UserError
from scrimmage.services.matches import TOURNAMENT_PRIORITY, insert_game
from scrimmage.services.storage import Storage
from scrimmage.settings import Settings


def eligible_teams(conn: sqlite3.Connection) -> list[Row]:
    return conn.execute(
        "SELECT t.* FROM teams t JOIN bots b ON b.id = t.current_bot_id "
        "WHERE NOT t.is_disabled ORDER BY t.name"
    ).fetchall()


def create(
    conn: sqlite3.Connection,
    settings: Settings,
    title: str,
    games_per_pair: int,
    is_private: bool,
    team_ids: list[int],
    created_by: int,
) -> int:
    title = " ".join(title.split())[:100]
    if games_per_pair < 1:
        raise UserError("Games per pair must be at least 1.")
    with transaction(conn):
        eligible = {t["id"]: t for t in eligible_teams(conn)}
        teams = [eligible[i] for i in sorted(set(team_ids)) if i in eligible]
        if len(teams) < 2:
            raise UserError("A tournament needs at least two teams with a current bot.")
        if not title:
            count = conn.execute("SELECT count(*) FROM tournaments").fetchone()[0]
            title = f"Tournament #{count + 1}"
        tournament_id = conn.execute(
            "INSERT INTO tournaments (title, games_per_pair, is_private, status, created_by, "
            "created_at) VALUES (?, ?, ?, 'running', ?, ?) RETURNING id",
            (title, games_per_pair, int(is_private), created_by, now()),
        ).fetchone()[0]
        conn.executemany(
            "INSERT INTO tournament_entries (tournament_id, team_id, bot_id) VALUES (?, ?, ?)",
            [(tournament_id, t["id"], t["current_bot_id"]) for t in teams],
        )
        pairings = []
        for first, second in itertools.combinations(teams, 2):
            for index in range(games_per_pair):
                pairings.append((first, second) if index % 2 == 0 else (second, first))
        # Shuffled so partial standings are meaningful while the tournament runs.
        random.shuffle(pairings)
        game_hardware = hardware.for_new_games(settings)
        for team_a, team_b in pairings:
            insert_game(
                conn,
                kind="tournament",
                team_a=team_a,
                team_b=team_b,
                priority=TOURNAMENT_PRIORITY,
                hardware=game_hardware,
                tournament_id=tournament_id,
            )
    tid: int = tournament_id
    return tid


def get(conn: sqlite3.Connection, tournament_id: int) -> Row | None:
    return one(conn, "SELECT * FROM tournaments WHERE id = ?", (tournament_id,))


def listing(conn: sqlite3.Connection, include_private: bool) -> list[Row]:
    return conn.execute(
        "SELECT t.*, "
        "(SELECT count(*) FROM tournament_entries e WHERE e.tournament_id = t.id) AS entrants, "
        "count(g.id) AS total_games, "
        "count(g.id) FILTER (WHERE g.status = 'done') AS done_games, "
        "count(g.id) FILTER (WHERE g.status = 'queued') AS queued_games, "
        "count(g.id) FILTER (WHERE g.status = 'running') AS running_games, "
        "count(g.id) FILTER (WHERE g.status = 'error') AS error_games "
        "FROM tournaments t LEFT JOIN games g ON g.tournament_id = t.id "
        "WHERE ? OR NOT t.is_private GROUP BY t.id ORDER BY t.id DESC",
        (int(include_private),),
    ).fetchall()


def set_private(conn: sqlite3.Connection, tournament_id: int, is_private: bool) -> None:
    conn.execute(
        "UPDATE tournaments SET is_private = ? WHERE id = ?", (int(is_private), tournament_id)
    )


def progress(conn: sqlite3.Connection, tournament_id: int) -> dict[str, int]:
    counts = {"queued": 0, "running": 0, "done": 0, "error": 0}
    for row in conn.execute(
        "SELECT status, count(*) AS n FROM games WHERE tournament_id = ? GROUP BY status",
        (tournament_id,),
    ):
        counts[row["status"]] = row["n"]
    counts["total"] = sum(counts.values())
    return counts


def standings(conn: sqlite3.Connection, tournament_id: int) -> list[Row]:
    return conn.execute(
        """
        WITH sides AS (
          SELECT team_a_id AS team_id, winner = 'a' AS win, winner = 'b' AS loss,
                 winner = 'tie' AS tie, score_a AS chips
          FROM games WHERE tournament_id = :t AND status = 'done'
          UNION ALL
          SELECT team_b_id, winner = 'b', winner = 'a', winner = 'tie', score_b
          FROM games WHERE tournament_id = :t AND status = 'done'
        ), totals AS (
          SELECT team_id, sum(win) AS wins, sum(loss) AS losses, sum(tie) AS ties,
                 sum(chips) AS chips
          FROM sides GROUP BY team_id
        )
        SELECT e.*, t.name AS team_name, b.name AS bot_name,
          coalesce(x.wins, 0) AS wins, coalesce(x.losses, 0) AS losses,
          coalesce(x.ties, 0) AS ties, coalesce(x.chips, 0) AS chips
        FROM tournament_entries e
        JOIN teams t ON t.id = e.team_id
        JOIN bots b ON b.id = e.bot_id
        LEFT JOIN totals x ON x.team_id = e.team_id
        WHERE e.tournament_id = :t
        ORDER BY e.rating IS NULL, e.rating DESC, wins DESC, t.name COLLATE NOCASE
        """,
        {"t": tournament_id},
    ).fetchall()


def retry_errors(conn: sqlite3.Connection, tournament_id: int) -> int:
    with transaction(conn):
        cur = conn.execute(
            "UPDATE games SET status = 'queued', error = NULL, started_at = NULL, "
            "finished_at = NULL, worker = NULL, lease_until = NULL "
            "WHERE tournament_id = ? AND status = 'error'",
            (tournament_id,),
        )
        if cur.rowcount:
            conn.execute(
                "UPDATE tournaments SET status = 'running', finished_at = NULL WHERE id = ?",
                (tournament_id,),
            )
    return cur.rowcount


def delete(conn: sqlite3.Connection, storage: Storage, tournament_id: int) -> None:
    with transaction(conn):
        running = conn.execute(
            "SELECT count(*) FROM games WHERE tournament_id = ? AND status = 'running'",
            (tournament_id,),
        ).fetchone()[0]
        if running:
            raise UserError("Wait for running games to finish (or cancel the queue) first.")
        game_ids = [
            r[0]
            for r in conn.execute("SELECT id FROM games WHERE tournament_id = ?", (tournament_id,))
        ]
        conn.execute("DELETE FROM tournaments WHERE id = ?", (tournament_id,))
    for game_id in game_ids:
        storage.delete_logs(game_id)


def cancel_queued(conn: sqlite3.Connection, tournament_id: int) -> int:
    """Drop queued games; running games finish normally."""
    cur = conn.execute(
        "DELETE FROM games WHERE tournament_id = ? AND status = 'queued'", (tournament_id,)
    )
    return cur.rowcount


# ---------------------------------------------------------------------------
# Ratings (refitted every minute by `scrimmage maintain`)
# ---------------------------------------------------------------------------


def update_ratings(conn: sqlite3.Connection) -> list[int]:
    """Refit every running tournament; mark finished ones done. Returns their ids."""
    running = [r[0] for r in conn.execute("SELECT id FROM tournaments WHERE status = 'running'")]
    for tournament_id in running:
        games = conn.execute(
            "SELECT ea.id, eb.id, g.winner FROM games g "
            "JOIN tournament_entries ea ON ea.tournament_id = g.tournament_id "
            "  AND ea.team_id = g.team_a_id "
            "JOIN tournament_entries eb ON eb.tournament_id = g.tournament_id "
            "  AND eb.team_id = g.team_b_id "
            "WHERE g.tournament_id = ? AND g.status = 'done'",
            (tournament_id,),
        ).fetchall()
        fitted = ratings.bradley_terry((a, b, winner) for a, b, winner in games)
        with transaction(conn):
            conn.executemany(
                "UPDATE tournament_entries SET rating = ?, rating_error = ? WHERE id = ?",
                [(rating, error, entry) for entry, (rating, error) in fitted.items()],
            )
            conn.execute(
                "UPDATE tournaments SET status = 'done', finished_at = ? "
                "WHERE id = ? AND NOT EXISTS (SELECT 1 FROM games "
                "  WHERE tournament_id = ? AND status IN ('queued', 'running'))",
                (now(), tournament_id, tournament_id),
            )
    return running
