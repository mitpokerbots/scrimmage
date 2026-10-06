"""Multi-core games (1, 2, 4 or 8 cores): scheduling and the bots' thread settings."""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path
from types import ModuleType

import pytest

from scrimmage import settings as settings_module
from scrimmage.services import hardware, matches, queue
from scrimmage.settings import Settings
from scrimmage.worker.main import Machine

RUN_MATCH = Path(__file__).parents[1] / "game" / "run_match.py"


@pytest.fixture
def run_match() -> ModuleType:
    spec = importlib.util.spec_from_file_location("run_match_under_test", RUN_MATCH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_bots_are_told_their_core_count(run_match: ModuleType) -> None:
    run_match._configure({"BOT_UID_A": 20000, "BOT_UID_B": 20001, "CORES": 8})
    env = run_match._bot_env("/game/bot-1", "/game/bot-1/.tmp")
    assert env["SCRIMMAGE_CORES"] == env["OMP_NUM_THREADS"] == env["MKL_NUM_THREADS"] == "8"
    assert "ActiveProcessorCount=8" in env["JAVA_TOOL_OPTIONS"]
    assert "UseSerialGC" not in env["JAVA_TOOL_OPTIONS"]
    run_match._configure({"BOT_UID_A": 20000, "BOT_UID_B": 20001, "CORES": 1})
    env = run_match._bot_env("/game/bot-1", "/game/bot-1/.tmp")
    assert env["OMP_NUM_THREADS"] == "1" and "UseSerialGC" in env["JAVA_TOOL_OPTIONS"]


def test_8_core_games_run_only_on_the_fleet(
    conn: sqlite3.Connection, two_teams: tuple[int, int]
) -> None:
    settings_module.update(conn, "cores_per_game", "8")
    matches.challenge(conn, Settings(conn), *two_teams)
    main_server = queue.Resources(cores=2, memory_mb=7_000)
    assert queue.claim(conn, "main", main_server, main_server) == []
    machine = queue.Resources(cores=16, memory_mb=hardware.MAX_GAME_MEMORY_MB)
    busy = queue.Resources(cores=7, memory_mb=hardware.MAX_GAME_MEMORY_MB)
    assert queue.claim(conn, "fleet-1", busy, machine) == []
    (game,) = queue.claim(conn, "fleet-1", machine, machine)
    params = queue.match_parameters(Settings(conn), game)
    assert (params["CORES"], params["BOT_MEMORY_BYTES"]) == (8, 8 * 1536 << 20)


def test_machine_gives_each_game_its_own_cores() -> None:
    machine = Machine(cpus=list(range(16)), memory_mb=hardware.MAX_GAME_MEMORY_MB)
    first = machine.allocate(cores=4, memory_mb=10_000)
    second = machine.allocate(cores=4, memory_mb=10_000)
    assert not set(first.cpus) & set(second.cpus)
    assert machine.status()["free"]["cores"] == 8
    with pytest.raises(RuntimeError):
        machine.allocate(cores=16, memory_mb=1)
    machine.release(first)
    machine.release(second)
    whole = machine.allocate(cores=16, memory_mb=60_000)
    assert whole.cpus == list(range(16)) and machine.idle()


def test_every_choice_packs_by_cores() -> None:
    for cores in hardware.CORE_CHOICES:
        needs = hardware.for_cores(cores)
        # A machine has room for the memory of a game on each of its cores.
        per_core = hardware.MAX_GAME_MEMORY_MB / hardware.FLEET_MACHINE_CORES
        assert needs.memory_mb <= cores * per_core
        assert hardware.FLEET_MACHINE_CORES % cores == 0
    eight = hardware.for_cores(8)
    assert eight.usd_per_game_hour(spot=False) == pytest.approx(8 * 0.71808 / 16)
