"""The worker against the real web app over HTTP, with Docker replaced by a fake."""

from __future__ import annotations

import gzip
import json
import sqlite3
import tarfile
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from scrimmage.cli import maintain_once
from scrimmage.config import Config
from scrimmage.services import accounts, builds, matches, queue, tournaments
from scrimmage.services.storage import Storage
from scrimmage.settings import Settings
from scrimmage.worker import main as worker_main
from scrimmage.worker.main import EXIT_OUTDATED, Machine, Worker
from scrimmage.worker.sandbox import MatchOutput
from tests.conftest import add_team_with_bot, claim

FINAL = b"Round #1, A (0), B (0)\nA awarded 7\n\nFinal, A (%d), B (%d)\n"


class FakeSandbox:
    """Stands in for Docker: 'plays' a match by returning canned output."""

    def __init__(
        self, score_a: int = 7, ok: bool = True, delay: float = 0.0, build_ok: bool = True
    ) -> None:
        self.score_a = score_a
        self.ok = ok
        self.build_ok = build_ok
        self.delay = delay
        self.calls: list[dict[str, Any]] = []
        self.builds: list[dict[str, Any]] = []
        self.killed: set[str] = set()
        self._kill = threading.Event()

    def build(self, **kwargs: Any) -> MatchOutput:
        self.builds.append(kwargs)
        assert kwargs["bot_b"] is None and (kwargs["bot_a"] / "commands.json").exists()
        if not self.build_ok:
            files = {"build.log": b"main.cpp:1: error"}
            return MatchOutput(
                {"ok": False, "error": "build exited with code 1"}, 0, False, False, files
            )
        output = kwargs["run_dir"] / "output"
        output.mkdir(parents=True)
        archive = output / "bot.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(kwargs["bot_a"], arcname=".")
        files = {"build.log": b"compiled"}
        return MatchOutput({"ok": True, "seconds": 2.0}, 0, False, False, files, archive)

    def run(self, **kwargs: Any) -> MatchOutput:
        if kwargs["params"].get("MODE") == "build":
            return self.build(**kwargs)
        self.calls.append(kwargs)
        assert (kwargs["bot_a"] / "commands.json").exists()
        assert len(kwargs["cpus"]) == kwargs["params"]["CORES"]
        assert (kwargs["bot_b"] / "commands.json").exists()
        deadline = time.monotonic() + self.delay
        while time.monotonic() < deadline:
            if self._kill.is_set() or kwargs["job"] in self.killed:
                return MatchOutput({}, 137, False, False, {})
            time.sleep(0.02)
        files = {
            "game.log": FINAL % (self.score_a, -self.score_a),
            "a.log": b"bot a says hi",
            "engine.log": b"A connected successfully",
        }
        status = {"ok": True} if self.ok else {"ok": False, "error": "Traceback: kaboom"}
        return MatchOutput(status, 0 if self.ok else 1, False, False, files)

    def kill_all(self) -> None:
        self._kill.set()

    def kill_job(self, job: str) -> None:
        self.killed.add(job)


def two_cores() -> Machine:
    return Machine(cpus=[0, 1], memory_mb=16_000)


@pytest.fixture
def worker_config(config: Config, live_server: str, tmp_path: Path) -> Config:
    return replace(config, server_url=live_server, data_dir=tmp_path / "worker-data")


@pytest.fixture
def teams(
    conn: sqlite3.Connection, storage: Storage, tmp_path: Path, python_bot_zip: bytes
) -> tuple[int, int]:
    a, _ = add_team_with_bot(conn, storage, "Aces", python_bot_zip, tmp_path)
    b, _ = add_team_with_bot(conn, storage, "Kings", python_bot_zip, tmp_path)
    return a, b


@pytest.fixture
def start_worker(worker_config: Config) -> Iterator[Callable[..., Worker]]:
    started: list[tuple[Worker, threading.Thread]] = []

    def start(sandbox: FakeSandbox, **overrides: Any) -> Worker:
        worker = Worker(replace(worker_config, **overrides), sandbox=sandbox, machine=two_cores())
        thread = threading.Thread(target=worker.run, kwargs={"install_signal_handlers": False})
        thread.start()
        started.append((worker, thread))
        return worker

    yield start
    for worker, thread in started:
        worker.stop()
        thread.join(timeout=30)


def wait_for(condition: Callable[[], bool], seconds: float = 15) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.05)
    raise AssertionError("condition not met in time")


def status_of(conn: sqlite3.Connection, game_id: int) -> str:
    return str(conn.execute("SELECT status FROM games WHERE id = ?", (game_id,)).fetchone()[0])


def test_worker_plays_and_reports(
    conn: sqlite3.Connection, storage: Storage, teams: tuple[int, int], start_worker: Any
) -> None:
    a, b = teams
    matches.challenge(conn, Settings(conn), a, b)
    matches.challenge(conn, Settings(conn), a, b)
    sandbox = FakeSandbox(score_a=42)
    start_worker(sandbox)
    wait_for(lambda: status_of(conn, 1) == "done" and status_of(conn, 2) == "done")

    game = matches.get_game(conn, 1)
    assert game is not None
    assert (game["score_a"], game["score_b"], game["winner"]) == (42, -42, "a")
    assert game["worker"] == "test-worker"
    assert gzip.decompress(storage.log_path(1, "a").read_bytes()) == b"bot a says hi"
    assert not storage.log_path(1, "b").exists()  # bot B printed nothing
    team = accounts.get_team(conn, a)
    assert team is not None and team["wins"] == 2
    # Per-slot sandbox users and the per-bot memory share are added by the worker.
    params = sandbox.calls[0]["params"]
    assert params["NUM_ROUNDS"] == 1000 and params["BOT_UID_A"] >= 20000
    assert params["BOT_MEMORY_BYTES"] > 0
    status = conn.execute("SELECT * FROM worker_status WHERE name = 'test-worker'").fetchone()
    assert status["cores"] == 2 and status["commit_id"] == "test-commit"
    # Each bot lives at a seat-independent path, so a build's absolute paths keep working.
    assert (params["BOT_DIR_A"], params["BOT_DIR_B"]) == ("bot-1", "bot-2")
    stats = json.loads(game["stats"])
    assert stats["worker"] == "test-worker" and stats["machine"] == "test-machine"


def test_worker_builds_uploads(
    conn: sqlite3.Connection,
    storage: Storage,
    tmp_path: Path,
    python_bot_zip: bytes,
    start_worker: Any,
) -> None:
    team, good = add_team_with_bot(conn, storage, "Aces", python_bot_zip, tmp_path, build=False)
    _, bad = add_team_with_bot(conn, storage, "Kings", python_bot_zip, tmp_path, build=False)
    sandbox = FakeSandbox()
    original = sandbox.build
    sandbox.build = lambda **kw: (  # type: ignore[method-assign]
        FakeSandbox(build_ok=False).build(**kw)
        if kw["params"]["BOT_DIR_A"] == f"bot-{bad}"
        else original(**kw)
    )
    start_worker(sandbox)

    def finished() -> bool:
        return all(
            (b := builds.get(conn, bot)) is not None and b["status"] in ("ready", "failed")
            for bot in (good, bad)
        )

    wait_for(finished)
    ready, failed = builds.get(conn, good), builds.get(conn, bad)
    assert ready is not None and ready["status"] == "ready"
    assert failed is not None and failed["status"] == "failed" and "code 1" in failed["error"]
    with tarfile.open(storage.build_archive(good)) as tar:
        assert "./commands.json" in tar.getnames()
    log = gzip.decompress(storage.build_log(bad).read_bytes())
    assert log == b"main.cpp:1: error"
    current = conn.execute("SELECT current_bot_id FROM teams WHERE id = ?", (team,)).fetchone()
    assert current[0] == good


def test_engine_crash_is_reported(
    conn: sqlite3.Connection, teams: tuple[int, int], start_worker: Any
) -> None:
    matches.challenge(conn, Settings(conn), *teams)
    start_worker(FakeSandbox(ok=False))
    wait_for(lambda: status_of(conn, 1) == "error")
    game = matches.get_game(conn, 1)
    assert game is not None and "kaboom" in game["error"]
    team = accounts.get_team(conn, teams[0])
    assert team is not None and team["elo"] == 1500  # errors never move ratings


def test_missing_bot_is_an_error_not_a_crash(
    conn: sqlite3.Connection, storage: Storage, teams: tuple[int, int], start_worker: Any
) -> None:
    for path in storage.builds_dir.iterdir():
        path.unlink()
    matches.challenge(conn, Settings(conn), *teams)
    start_worker(FakeSandbox())
    wait_for(lambda: status_of(conn, 1) == "error")
    game = matches.get_game(conn, 1)
    assert game is not None and "Internal error" in game["error"]


def test_stop_requeues_running_games(
    conn: sqlite3.Connection, teams: tuple[int, int], start_worker: Any
) -> None:
    matches.challenge(conn, Settings(conn), *teams)
    sandbox = FakeSandbox(delay=30)
    worker = start_worker(sandbox)
    wait_for(lambda: bool(sandbox.calls))
    worker.stop()
    wait_for(lambda: status_of(conn, 1) == "queued")


def test_server_reclaiming_a_game_cancels_it(
    conn: sqlite3.Connection,
    teams: tuple[int, int],
    start_worker: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(worker_main, "HEARTBEAT_SECONDS", 0.2)
    matches.challenge(conn, Settings(conn), *teams)
    sandbox = FakeSandbox(delay=30)
    start_worker(sandbox)
    wait_for(lambda: bool(sandbox.calls))
    # As if this worker's lease expired and the game went to someone else.
    queue.release(conn, "test-worker")
    claimed = claim(conn, "other-worker")
    assert claimed
    wait_for(lambda: "game-1" in sandbox.killed)
    game = matches.get_game(conn, 1)
    assert game is not None and game["worker"] == "other-worker"


def test_outdated_worker_retires(worker_config: Config, conn: sqlite3.Connection) -> None:
    worker = Worker(
        replace(worker_config, commit="old"), sandbox=FakeSandbox(), machine=two_cores()
    )
    assert worker.run(install_signal_handlers=False) == EXIT_OUTDATED


def test_maintain_rates_finished_tournaments(
    config: Config,
    conn: sqlite3.Connection,
    storage: Storage,
    tmp_path: Path,
    python_bot_zip: bytes,
) -> None:
    ids = [
        add_team_with_bot(conn, storage, name, python_bot_zip, tmp_path)[0]
        for name in ("Aces", "Kings", "Queens")
    ]
    tid = tournaments.create(conn, Settings(conn), "Final", 2, False, ids, 1)
    s = Settings(conn)
    order = {team: rank for rank, team in enumerate(ids)}  # Aces beat Kings beat Queens
    while game := claim(conn, "w"):
        a_wins = order[game[0]["team_a_id"]] < order[game[0]["team_b_id"]]
        result = queue.Result(5, -5) if a_wins else queue.Result(-5, 5)
        queue.record_result(conn, s, game[0]["id"], result, worker="w")
    maintain_once(config)
    tournament = tournaments.get(conn, tid)
    assert tournament is not None and tournament["status"] == "done"
    standings = tournaments.standings(conn, tid)
    assert [r["team_name"] for r in standings] == ["Aces", "Kings", "Queens"]
    assert standings[0]["rating"] > 1500 > standings[2]["rating"]
    assert all(r["rating_error"] > 0 for r in standings)


def test_maintain_requeues_expired_leases(
    config: Config, conn: sqlite3.Connection, teams: tuple[int, int]
) -> None:
    matches.challenge(conn, Settings(conn), *teams)
    claim(conn, "vanished-worker")
    conn.execute("UPDATE games SET lease_until = 0")
    maintain_once(config)
    assert status_of(conn, 1) == "queued"
