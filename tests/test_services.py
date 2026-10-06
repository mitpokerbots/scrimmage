from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from scrimmage import settings as settings_module
from scrimmage.db import now
from scrimmage.services import accounts, bots, matches, queue, ratings, tournaments
from scrimmage.services.errors import UserError
from scrimmage.services.storage import Storage
from scrimmage.settings import Settings
from tests.conftest import add_team_with_bot, claim, claim_one, make_zip

COMMANDS = '{"build": [], "run": ["python3", "player.py"]}'


# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------


def test_record_login_creates_and_updates_user(conn: sqlite3.Connection) -> None:
    first = accounts.record_login(conn, "Alice", "Alice A", frozenset({"alice"}))
    second = accounts.record_login(conn, "alice", None, frozenset())
    assert first == second
    user = accounts.get_user(conn, first)
    assert user is not None
    assert user["kerberos"] == "alice"
    assert user["display_name"] == "Alice A"  # not erased by a login without a name
    assert user["is_admin"] == 1  # bootstrap admin status sticks


@pytest.mark.parametrize("bad", ["", "a b", "../x", "x" * 80, "-lead"])
def test_invalid_kerberos_rejected(bad: str) -> None:
    with pytest.raises(UserError):
        accounts.normalize_kerberos(bad)


def test_join_flow(conn: sqlite3.Connection) -> None:
    s = Settings(conn)
    alice = accounts.record_login(conn, "alice", None, frozenset())
    bob = accounts.record_login(conn, "bob", None, frozenset())
    team = accounts.create_team(conn, alice, "Aces")
    with pytest.raises(UserError):
        accounts.create_team(conn, alice, "Another")
    with pytest.raises(UserError):
        accounts.create_team(conn, bob, "aces")  # names are case-insensitive

    accounts.request_join(conn, s, bob, team)
    assert [u["kerberos"] for u in accounts.join_requests_for(conn, team)] == ["bob"]
    accounts.answer_join(conn, s, team, bob, accept=True)
    assert {u["kerberos"] for u in accounts.members(conn, team)} == {"alice", "bob"}
    with pytest.raises(UserError):
        accounts.answer_join(conn, s, team, bob, accept=True)

    accounts.leave_team(conn, alice)
    accounts.leave_team(conn, bob)
    disabled = accounts.get_team(conn, team)
    assert disabled is not None and disabled["is_disabled"] == 1


def test_team_size_limit(conn: sqlite3.Connection) -> None:
    settings_module.update(conn, "maximum_team_size", "1")
    s = Settings(conn)
    alice = accounts.record_login(conn, "alice", None, frozenset())
    bob = accounts.record_login(conn, "bob", None, frozenset())
    team = accounts.create_team(conn, alice, "Solo")
    with pytest.raises(UserError, match="full"):
        accounts.request_join(conn, s, bob, team)
    assert accounts.joinable_teams(conn, s) == []


def test_login_tokens_are_single_use(conn: sqlite3.Connection) -> None:
    token = accounts.mint_login_token(conn, "alice")
    assert accounts.redeem_login_token(conn, token) == "alice"
    with pytest.raises(UserError):
        accounts.redeem_login_token(conn, token)
    with pytest.raises(UserError):
        accounts.redeem_login_token(conn, "made-up")


# ---------------------------------------------------------------------------
# Bot archives
# ---------------------------------------------------------------------------


def _write(tmp_path: Path, data: bytes) -> Path:
    path = tmp_path / "upload.zip"
    path.write_bytes(data)
    return path


def test_inspect_finds_nested_root(tmp_path: Path) -> None:
    data = make_zip(
        {
            "__MACOSX/bot/._commands.json": "junk",
            "bot/commands.json": COMMANDS,
            "bot/player.py": "print(1)",
            "bot/lib/commands.json": COMMANDS,  # deeper copies are ignored
        }
    )
    archive = bots.inspect_zip(_write(tmp_path, data))
    assert archive.root == "bot"


@pytest.mark.parametrize(
    ("files", "message"),
    [
        ({"player.py": "x"}, "no commands.json"),
        ({"commands.json": "{"}, "not valid JSON"),
        ({"commands.json": '{"build": [], "run": []}'}, "must not be empty"),
        ({"commands.json": '{"build": "make", "run": ["x"]}'}, "list of strings"),
        ({"a/commands.json": COMMANDS, "b/commands.json": COMMANDS}, "more than one"),
        ({"commands.json": COMMANDS, "../evil": "x"}, "unsafe path"),
        ({"commands.json": COMMANDS, "/etc/passwd": "x"}, "unsafe path"),
    ],
)
def test_inspect_rejects_bad_zips(tmp_path: Path, files: dict[str, str], message: str) -> None:
    with pytest.raises(UserError, match=message):
        bots.inspect_zip(_write(tmp_path, make_zip(files)))


def test_inspect_rejects_non_zip(tmp_path: Path) -> None:
    with pytest.raises(UserError, match="not a zip"):
        bots.inspect_zip(_write(tmp_path, b"hello"))


def test_extract_strips_root_and_keeps_exec_bit(tmp_path: Path) -> None:
    import zipfile

    path = tmp_path / "bot.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("outer/commands.json", COMMANDS)
        info = zipfile.ZipInfo("outer/run.sh")
        info.external_attr = 0o755 << 16
        zf.writestr(info, "#!/bin/sh\n")
        zf.writestr("elsewhere.txt", "not extracted")
    dest = tmp_path / "out"
    bots.extract(path, "outer", dest)
    assert sorted(p.name for p in dest.iterdir()) == ["commands.json", "run.sh"]
    assert (dest / "run.sh").stat().st_mode & 0o111


def test_upload_limit_per_day(
    conn: sqlite3.Connection, storage: Storage, tmp_path: Path, python_bot_zip: bytes
) -> None:
    settings_module.update(conn, "bot_uploads_per_day", "1")
    team_id, _ = add_team_with_bot(conn, storage, "Aces", python_bot_zip, tmp_path)
    upload = tmp_path / "again.zip"
    upload.write_bytes(python_bot_zip)
    with pytest.raises(UserError, match="upload limit"):
        bots.create(conn, storage, Settings(conn), team_id, None, "", upload)


def test_current_bot_cannot_be_deleted(
    conn: sqlite3.Connection, storage: Storage, tmp_path: Path, python_bot_zip: bytes
) -> None:
    team_id, bot_id = add_team_with_bot(conn, storage, "Aces", python_bot_zip, tmp_path)
    assert storage.bot_zip(bot_id).exists()
    with pytest.raises(UserError, match="current bot"):
        bots.delete(conn, team_id, bot_id)


# ---------------------------------------------------------------------------
# Challenges, Elo, queue
# ---------------------------------------------------------------------------


def test_parse_scores_matches_by_name() -> None:
    log = "Round #1000, A (5), B (-5)\n\nFinal, B (-120), A (120)\nA preflop bets EV: 3\n"
    assert queue.parse_scores(log) == (120, -120)
    assert queue.parse_scores("no final line") is None


def test_elo_is_zero_sum_and_favors_upsets() -> None:
    a, b = ratings.elo_update(1500, 1500, "a")
    assert a + b == pytest.approx(3000)
    assert a == pytest.approx(1520)
    upset_a, _ = ratings.elo_update(1400, 1600, "a")
    assert upset_a - 1400 > 20


def test_upward_challenge_queues_game(conn: sqlite3.Connection, two_teams: tuple[int, int]) -> None:
    a, b = two_teams
    message = matches.challenge(conn, Settings(conn), a, b)
    assert "queue" in message
    game = claim_one(conn)
    assert game is not None
    assert (game["team_a_id"], game["team_b_id"], game["initiator_id"]) == (a, b, a)


def test_downward_challenge_needs_accept(
    conn: sqlite3.Connection, two_teams: tuple[int, int]
) -> None:
    a, b = two_teams
    conn.execute("UPDATE teams SET elo = 1600 WHERE id = ?", (a,))
    s = Settings(conn)
    assert "accept" in matches.challenge(conn, s, a, b)
    with pytest.raises(UserError, match="already have a pending"):
        matches.challenge(conn, s, a, b)
    assert claim_one(conn) is None
    request_id = matches.incoming_requests(conn, b)[0]["id"]
    matches.answer_request(conn, s, b, request_id, accept=True)
    game = claim_one(conn)
    assert game is not None and game["initiator_id"] == b


def test_spawn_limit(conn: sqlite3.Connection, two_teams: tuple[int, int]) -> None:
    a, b = two_teams
    settings_module.update(conn, "spawn_limit_per_team", "2")
    s = Settings(conn)
    matches.challenge(conn, s, a, b)
    matches.challenge(conn, s, a, b)
    with pytest.raises(UserError, match="maximum number"):
        matches.challenge(conn, s, a, b)


def test_challenge_rules(conn: sqlite3.Connection, two_teams: tuple[int, int]) -> None:
    a, b = two_teams
    with pytest.raises(UserError, match="own team"):
        matches.challenge(conn, Settings(conn), a, a)
    settings_module.update(conn, "challenges_only_reference", "true")
    with pytest.raises(UserError, match="reference"):
        matches.challenge(conn, Settings(conn), a, b)
    settings_module.update(conn, "challenges_enabled", "false")
    with pytest.raises(UserError, match="disabled"):
        matches.challenge(conn, Settings(conn), a, b)


def test_record_result_updates_ratings(
    conn: sqlite3.Connection, two_teams: tuple[int, int]
) -> None:
    a, b = two_teams
    s = Settings(conn)
    matches.challenge(conn, s, a, b)
    game = claim_one(conn)
    assert game is not None
    queue.record_result(conn, s, game["id"], queue.Result(300, -300))
    team_a, team_b = accounts.get_team(conn, a), accounts.get_team(conn, b)
    assert team_a is not None and team_b is not None
    assert team_a["elo"] == pytest.approx(1520) and team_a["wins"] == 1
    assert team_b["elo"] == pytest.approx(1480) and team_b["losses"] == 1
    assert matches.elo_history(conn, a) == [(pytest.approx(now(), abs=5), pytest.approx(1520))]
    with pytest.raises(queue.LeaseLost):
        queue.record_result(conn, s, game["id"], queue.Result(1, -1))


def test_down_challenges_can_be_unrated(
    conn: sqlite3.Connection, two_teams: tuple[int, int]
) -> None:
    a, b = two_teams
    conn.execute("UPDATE teams SET elo = 1600 WHERE id = ?", (a,))
    settings_module.update(conn, "down_challenges_require_accept", "false")
    settings_module.update(conn, "down_challenges_affect_elo", "false")
    s = Settings(conn)
    matches.challenge(conn, s, a, b)
    game = claim_one(conn)
    assert game is not None
    queue.record_result(conn, s, game["id"], queue.Result(10, -10))
    team_a = accounts.get_team(conn, a)
    assert team_a is not None and team_a["elo"] == 1600 and team_a["wins"] == 1


def test_scrimmages_jump_ahead_of_tournaments(
    conn: sqlite3.Connection, two_teams: tuple[int, int]
) -> None:
    a, b = two_teams
    tournaments.create(conn, Settings(conn), "T", 3, False, [a, b], 1)
    matches.challenge(conn, Settings(conn), a, b)
    first = claim_one(conn)
    assert first is not None and first["kind"] == "scrimmage"


def test_requeue_and_retry(conn: sqlite3.Connection, two_teams: tuple[int, int]) -> None:
    a, b = two_teams
    matches.challenge(conn, Settings(conn), a, b)
    game = claim_one(conn, "w1")
    assert game is not None
    assert queue.release(conn, "w1") == 1
    game = claim_one(conn, "w2")
    assert game is not None
    queue.record_error(conn, game["id"], "boom", worker="w2")
    queue.retry_failed(conn, game["id"])
    again = claim_one(conn)
    assert again is not None and again["id"] == game["id"]


def test_leases(conn: sqlite3.Connection, two_teams: tuple[int, int]) -> None:
    a, b = two_teams
    s = Settings(conn)
    matches.challenge(conn, s, a, b)
    matches.challenge(conn, s, a, b)
    first, second = claim(conn, "w1", 5)
    assert queue.renew_leases(conn, "w1", [first["id"], second["id"]]) == []
    # w2 holds nothing, so both games are reported as not its own.
    assert queue.renew_leases(conn, "w2", [first["id"]]) == [first["id"]]

    # w1 goes silent: its leases run out and the games return to the queue.
    conn.execute("UPDATE games SET lease_until = 0")
    assert queue.requeue_expired(conn) == 2
    assert queue.renew_leases(conn, "w1", [first["id"]]) == [first["id"]]
    retaken = claim_one(conn, "w2")
    assert retaken is not None and retaken["id"] == first["id"]

    # A late result from w1 is refused; w2's is accepted.
    with pytest.raises(queue.LeaseLost):
        queue.record_result(conn, s, first["id"], queue.Result(5, -5), worker="w1")
    queue.record_result(conn, s, first["id"], queue.Result(5, -5), worker="w2")


def test_results_must_be_zero_sum(conn: sqlite3.Connection, two_teams: tuple[int, int]) -> None:
    a, b = two_teams
    matches.challenge(conn, Settings(conn), a, b)
    game = claim_one(conn)
    assert game is not None
    with pytest.raises(ValueError, match="sum to zero"):
        queue.record_result(conn, Settings(conn), game["id"], queue.Result(400, 1))


# ---------------------------------------------------------------------------
# Tournaments
# ---------------------------------------------------------------------------


def test_tournament_round_robin(
    conn: sqlite3.Connection, storage: Storage, tmp_path: Path, python_bot_zip: bytes
) -> None:
    ids = [
        add_team_with_bot(conn, storage, name, python_bot_zip, tmp_path)[0]
        for name in ("A1", "B2", "C3", "D4")
    ]
    tid = tournaments.create(conn, Settings(conn), "", 2, False, ids, 1)
    assert tournaments.progress(conn, tid)["total"] == 6 * 2
    pairs = conn.execute(
        "SELECT team_a_id, team_b_id FROM games WHERE tournament_id = ?", (tid,)
    ).fetchall()
    # Each pair plays once in each seat assignment.
    assert len({(r[0], r[1]) for r in pairs}) == 12
    s = Settings(conn)
    while (game := claim_one(conn)) is not None:
        winner_a = game["team_a_id"] < game["team_b_id"]
        queue.record_result(conn, s, game["id"], queue.Result(*((5, -5) if winner_a else (-5, 5))))
    assert tournaments.update_ratings(conn) == [tid]
    finished = tournaments.get(conn, tid)
    assert finished is not None and finished["status"] == "done"
    rows = tournaments.standings(conn, tid)
    assert [r["wins"] for r in rows] == [6, 4, 2, 0]
    assert [r["rating"] for r in rows] == sorted((r["rating"] for r in rows), reverse=True)
    assert rows[0]["team_id"] == ids[0]
    # Tournament games never touch the ladder.
    team = accounts.get_team(conn, ids[0])
    assert team is not None and team["elo"] == 1500 and team["wins"] == 0


def test_bradley_terry_orders_and_bounds_ratings() -> None:
    # 1 beats 2 and 3 every time; 2 and 3 split their games.
    games = [(1, 2, "a"), (3, 1, "b"), (2, 3, "a"), (3, 2, "a"), (2, 3, "tie")] * 4
    fitted = ratings.bradley_terry(games)
    assert fitted[1][0] > fitted[2][0] == pytest.approx(fitted[3][0])
    # The ratings average 1500 and stay finite despite an unbeaten team.
    assert sum(r for r, _ in fitted.values()) / 3 == pytest.approx(1500)
    assert fitted[1][0] < 2500 and all(error > 0 for _, error in fitted.values())
    # More games, tighter error bars.
    assert ratings.bradley_terry(games * 10)[1][1] < fitted[1][1]
    assert ratings.bradley_terry([]) == {}


# ---------------------------------------------------------------------------
# Settings and migrations
# ---------------------------------------------------------------------------


def test_settings_validation(conn: sqlite3.Connection) -> None:
    with pytest.raises(ValueError, match="at least"):
        settings_module.update(conn, "game_num_hands", "0")
    with pytest.raises(ValueError, match="true or false"):
        settings_module.update(conn, "challenges_enabled", "yes")
    with pytest.raises(ValueError, match="unknown"):
        settings_module.update(conn, "nope", "1")
    settings_module.update(conn, "game_num_hands", " 250 ")
    assert Settings(conn).number("game_num_hands") == 250


def test_migrate_is_idempotent(config) -> None:  # type: ignore[no-untyped-def]
    from scrimmage import db

    assert db.migrate(config.db_path) == db.migrate(config.db_path) == 1
