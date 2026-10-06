"""Users, teams, and team membership."""

from __future__ import annotations

import hashlib
import re
import secrets
import sqlite3

from scrimmage.db import Row, now, one, transaction
from scrimmage.services.errors import UserError
from scrimmage.settings import Settings

KERBEROS_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
TEAM_NAME_MAX = 40
LOGIN_LINK_SECONDS = 15 * 60


def normalize_kerberos(raw: str) -> str:
    kerberos = raw.strip().lower()
    if not KERBEROS_RE.match(kerberos):
        raise UserError(f"{raw!r} is not a valid kerberos.")
    return kerberos


def record_login(
    conn: sqlite3.Connection,
    kerberos: str,
    display_name: str | None,
    bootstrap_admins: frozenset[str],
) -> int:
    """Create or update the user for a successful login; returns the user id."""
    kerberos = normalize_kerberos(kerberos)
    is_bootstrap_admin = int(kerberos in bootstrap_admins)
    with transaction(conn):
        row = conn.execute(
            "INSERT INTO users (kerberos, display_name, is_admin, created_at, last_login_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT (kerberos) DO UPDATE SET "
            "  display_name = coalesce(excluded.display_name, users.display_name), "
            "  is_admin = max(users.is_admin, excluded.is_admin), "
            "  last_login_at = excluded.last_login_at "
            "RETURNING id",
            (kerberos, display_name or None, is_bootstrap_admin, now(), now()),
        ).fetchone()
    user_id: int = row["id"]
    return user_id


def get_user(conn: sqlite3.Connection, user_id: int) -> Row | None:
    return one(conn, "SELECT * FROM users WHERE id = ?", (user_id,))


def get_team(conn: sqlite3.Connection, team_id: int) -> Row | None:
    return one(conn, "SELECT * FROM teams WHERE id = ?", (team_id,))


def members(conn: sqlite3.Connection, team_id: int) -> list[Row]:
    return conn.execute(
        "SELECT * FROM users WHERE team_id = ? ORDER BY kerberos", (team_id,)
    ).fetchall()


def _member_count(conn: sqlite3.Connection, team_id: int) -> int:
    count: int = conn.execute(
        "SELECT count(*) FROM users WHERE team_id = ?", (team_id,)
    ).fetchone()[0]
    return count


def clean_team_name(raw: str) -> str:
    name = " ".join(raw.split())
    if not name:
        raise UserError("Team name cannot be empty.")
    if len(name) > TEAM_NAME_MAX:
        raise UserError(f"Team name must be at most {TEAM_NAME_MAX} characters.")
    return name


def insert_team(conn: sqlite3.Connection, name: str, is_reference: bool = False) -> int:
    name = clean_team_name(name)
    try:
        cur = conn.execute(
            "INSERT INTO teams (name, is_reference, created_at) VALUES (?, ?, ?)",
            (name, int(is_reference), now()),
        )
    except sqlite3.IntegrityError:
        raise UserError(f"A team named {name!r} already exists.") from None
    assert cur.lastrowid is not None
    return cur.lastrowid


def create_team(conn: sqlite3.Connection, user_id: int, name: str) -> int:
    with transaction(conn):
        user = get_user(conn, user_id)
        if user is None or user["team_id"] is not None:
            raise UserError("You are already on a team.")
        team_id = insert_team(conn, name)
        conn.execute("UPDATE users SET team_id = ? WHERE id = ?", (team_id, user_id))
        conn.execute("DELETE FROM join_requests WHERE user_id = ?", (user_id,))
    return team_id


def leaderboard(conn: sqlite3.Connection) -> list[Row]:
    """Active teams by rating, with their current bot's name."""
    return conn.execute(
        "SELECT t.*, b.name AS bot_name, "
        "(SELECT count(*) FROM users u WHERE u.team_id = t.id) AS size "
        "FROM teams t LEFT JOIN bots b ON b.id = t.current_bot_id "
        "WHERE NOT t.is_disabled ORDER BY t.elo DESC, t.name"
    ).fetchall()


def all_teams(conn: sqlite3.Connection) -> list[Row]:
    return conn.execute(
        "SELECT t.*, b.name AS bot_name, "
        "(SELECT group_concat(u.kerberos, ', ') FROM users u WHERE u.team_id = t.id) AS members, "
        "(SELECT count(*) FROM games g WHERE g.team_a_id = t.id OR g.team_b_id = t.id) AS games "
        "FROM teams t LEFT JOIN bots b ON b.id = t.current_bot_id "
        "ORDER BY t.is_disabled, t.name"
    ).fetchall()


def team_bots(conn: sqlite3.Connection, team_id: int) -> list[Row]:
    return conn.execute(
        "SELECT b.*, u.kerberos AS uploader, bu.status AS build_status, "
        "bu.error AS build_error, bu.seconds AS build_seconds FROM bots b "
        "LEFT JOIN users u ON u.id = b.uploaded_by "
        "LEFT JOIN builds bu ON bu.bot_id = b.id "
        "WHERE b.team_id = ? AND NOT b.is_deleted ORDER BY b.id DESC",
        (team_id,),
    ).fetchall()


def update_team(
    conn: sqlite3.Connection,
    team_id: int,
    *,
    name: str,
    is_disabled: bool,
    is_reference: bool,
) -> None:
    with transaction(conn):
        team = get_team(conn, team_id)
        if team is None:
            raise UserError("No such team.")
        name = clean_team_name(name)
        try:
            conn.execute(
                "UPDATE teams SET name = ?, is_reference = ? WHERE id = ?",
                (name, int(is_reference), team_id),
            )
        except sqlite3.IntegrityError:
            raise UserError(f"A team named {name!r} already exists.") from None
        if is_disabled and not team["is_disabled"]:
            disable_team(conn, team_id)
        elif not is_disabled:
            conn.execute("UPDATE teams SET is_disabled = 0 WHERE id = ?", (team_id,))


def delete_team(conn: sqlite3.Connection, team_id: int) -> list[int]:
    """Delete a team that never played; teams with history can only be disabled.

    Returns the ids of the team's bots so the caller can remove their files.
    """
    with transaction(conn):
        played = conn.execute(
            "SELECT 1 FROM games WHERE team_a_id = ? OR team_b_id = ? LIMIT 1",
            (team_id, team_id),
        ).fetchone()
        entered = conn.execute(
            "SELECT 1 FROM tournament_entries WHERE team_id = ? LIMIT 1", (team_id,)
        ).fetchone()
        if played or entered:
            raise UserError("That team has played games; disable it instead.")
        conn.execute("UPDATE users SET team_id = NULL WHERE team_id = ?", (team_id,))
        conn.execute(
            "DELETE FROM game_requests WHERE challenger_id = ? OR opponent_id = ?",
            (team_id, team_id),
        )
        conn.execute("UPDATE teams SET current_bot_id = NULL WHERE id = ?", (team_id,))
        bot_ids = [r[0] for r in conn.execute("SELECT id FROM bots WHERE team_id = ?", (team_id,))]
        conn.execute("DELETE FROM bots WHERE team_id = ?", (team_id,))
        conn.execute("DELETE FROM teams WHERE id = ?", (team_id,))
    return bot_ids


def reset_ratings(conn: sqlite3.Connection) -> None:
    """Start a new season: every team back to 1500 with a clean record."""
    conn.execute("UPDATE teams SET elo = 1500, wins = 0, losses = 0, ties = 0")


def all_users(conn: sqlite3.Connection) -> list[Row]:
    return conn.execute(
        "SELECT u.*, t.name AS team_name FROM users u LEFT JOIN teams t ON t.id = u.team_id "
        "ORDER BY u.kerberos"
    ).fetchall()


def admin_set_user(
    conn: sqlite3.Connection, user_id: int, team_id: int | None, is_admin: bool
) -> None:
    with transaction(conn):
        user = get_user(conn, user_id)
        if user is None:
            raise UserError("No such user.")
        if team_id is not None and get_team(conn, team_id) is None:
            raise UserError("No such team.")
        conn.execute(
            "UPDATE users SET team_id = ?, is_admin = ? WHERE id = ?",
            (team_id, int(is_admin), user_id),
        )
        if team_id is not None:
            conn.execute("DELETE FROM join_requests WHERE user_id = ?", (user_id,))


def admin_create_user(conn: sqlite3.Connection, kerberos: str, team_id: int | None) -> int:
    kerberos = normalize_kerberos(kerberos)
    with transaction(conn):
        if team_id is not None and get_team(conn, team_id) is None:
            raise UserError("No such team.")
        try:
            cur = conn.execute(
                "INSERT INTO users (kerberos, team_id, created_at) VALUES (?, ?, ?)",
                (kerberos, team_id, now()),
            )
        except sqlite3.IntegrityError:
            raise UserError(f"{kerberos} already has an account.") from None
    assert cur.lastrowid is not None
    return cur.lastrowid


def delete_user(conn: sqlite3.Connection, user_id: int) -> None:
    with transaction(conn):
        user = get_user(conn, user_id)
        if user is None:
            raise UserError("No such user.")
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        if user["team_id"] is not None and _member_count(conn, user["team_id"]) == 0:
            disable_team(conn, user["team_id"])


def joinable_teams(conn: sqlite3.Connection, settings: Settings) -> list[Row]:
    return conn.execute(
        "SELECT t.*, (SELECT count(*) FROM users u WHERE u.team_id = t.id) AS size "
        "FROM teams t WHERE NOT t.is_disabled AND NOT t.is_reference "
        "AND (SELECT count(*) FROM users u WHERE u.team_id = t.id) < ? "
        "ORDER BY t.name",
        (settings.number("maximum_team_size"),),
    ).fetchall()


def request_join(conn: sqlite3.Connection, settings: Settings, user_id: int, team_id: int) -> None:
    with transaction(conn):
        user = get_user(conn, user_id)
        team = get_team(conn, team_id)
        if user is None or user["team_id"] is not None:
            raise UserError("You are already on a team.")
        if team is None or team["is_disabled"] or team["is_reference"]:
            raise UserError("That team cannot be joined.")
        if _member_count(conn, team_id) >= settings.number("maximum_team_size"):
            raise UserError("That team is full.")
        conn.execute(
            "INSERT INTO join_requests (user_id, team_id, created_at) VALUES (?, ?, ?) "
            "ON CONFLICT (user_id) DO UPDATE SET team_id = excluded.team_id, "
            "created_at = excluded.created_at",
            (user_id, team_id, now()),
        )


def cancel_join(conn: sqlite3.Connection, user_id: int) -> None:
    conn.execute("DELETE FROM join_requests WHERE user_id = ?", (user_id,))


def pending_join_request(conn: sqlite3.Connection, user_id: int) -> Row | None:
    return one(
        conn,
        "SELECT jr.*, t.name AS team_name FROM join_requests jr "
        "JOIN teams t ON t.id = jr.team_id WHERE jr.user_id = ?",
        (user_id,),
    )


def join_requests_for(conn: sqlite3.Connection, team_id: int) -> list[Row]:
    return conn.execute(
        "SELECT u.* FROM join_requests jr JOIN users u ON u.id = jr.user_id "
        "WHERE jr.team_id = ? ORDER BY jr.created_at",
        (team_id,),
    ).fetchall()


def answer_join(
    conn: sqlite3.Connection, settings: Settings, team_id: int, user_id: int, accept: bool
) -> None:
    with transaction(conn):
        cur = conn.execute(
            "DELETE FROM join_requests WHERE user_id = ? AND team_id = ?", (user_id, team_id)
        )
        if cur.rowcount != 1:
            raise UserError("That join request no longer exists.")
        if not accept:
            return
        if _member_count(conn, team_id) >= settings.number("maximum_team_size"):
            raise UserError("Your team is full.")
        cur = conn.execute(
            "UPDATE users SET team_id = ? WHERE id = ? AND team_id IS NULL", (team_id, user_id)
        )
        if cur.rowcount != 1:
            raise UserError("That user already joined another team.")


def disable_team(conn: sqlite3.Connection, team_id: int) -> None:
    """Disable a team and withdraw its pending challenges (caller holds a transaction)."""
    conn.execute("UPDATE teams SET is_disabled = 1 WHERE id = ?", (team_id,))
    conn.execute(
        "UPDATE game_requests SET status = 'cancelled', decided_at = ? "
        "WHERE status = 'pending' AND (challenger_id = ? OR opponent_id = ?)",
        (now(), team_id, team_id),
    )


def leave_team(conn: sqlite3.Connection, user_id: int) -> None:
    """Leave the current team. A team whose last member leaves is disabled."""
    with transaction(conn):
        user = get_user(conn, user_id)
        if user is None or user["team_id"] is None:
            raise UserError("You are not on a team.")
        team_id = user["team_id"]
        conn.execute("UPDATE users SET team_id = NULL WHERE id = ?", (user_id,))
        if _member_count(conn, team_id) == 0:
            disable_team(conn, team_id)


# ---------------------------------------------------------------------------
# One-time login links (break-glass access from the server shell)
# ---------------------------------------------------------------------------


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def mint_login_token(conn: sqlite3.Connection, kerberos: str) -> str:
    kerberos = normalize_kerberos(kerberos)
    token = secrets.token_urlsafe(32)
    conn.execute(
        "INSERT INTO login_tokens (token_hash, kerberos, expires_at) VALUES (?, ?, ?)",
        (_hash_token(token), kerberos, now() + LOGIN_LINK_SECONDS),
    )
    return token


def redeem_login_token(conn: sqlite3.Connection, token: str) -> str:
    """Return the kerberos for a valid unused token and mark it used."""
    with transaction(conn):
        row = conn.execute(
            "UPDATE login_tokens SET used_at = ? "
            "WHERE token_hash = ? AND used_at IS NULL AND expires_at > ? RETURNING kerberos",
            (now(), _hash_token(token), now()),
        ).fetchone()
        conn.execute("DELETE FROM login_tokens WHERE expires_at < ?", (now() - 86400,))
    if row is None:
        raise UserError("This login link is invalid, expired, or already used.")
    kerberos: str = row["kerberos"]
    return kerberos
