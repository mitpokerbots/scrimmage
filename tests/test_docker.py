"""
End-to-end matches in the real game image.

    docker build -t scrimmage-game:latest game/
    pytest -m docker
"""

from __future__ import annotations

import gzip
import itertools
import json
import shutil
import sqlite3
import threading
import time
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest

from scrimmage import settings as settings_module
from scrimmage.config import Config
from scrimmage.services import bots, builds, matches
from scrimmage.services.storage import Storage
from scrimmage.settings import Settings
from scrimmage.worker.main import Machine, Worker
from tests.conftest import FIXTURE_BOTS, add_team_with_bot, zip_dir

pytestmark = pytest.mark.docker

HOSTILE_PROBES = """
import os, signal, socket

def probe(name, action):
    try:
        action()
        print("PROBE", name, "ALLOWED", flush=True)
    except Exception as exc:
        print("PROBE", name, "BLOCKED", type(exc).__name__, flush=True)

def engine_pid():
    for pid in os.listdir("/proc"):
        if pid.isdigit():
            try:
                if b"run_match" in open(f"/proc/{pid}/cmdline", "rb").read():
                    return int(pid)
            except OSError:
                pass
    raise LookupError("engine not visible")

def read_opponent_home():
    # Homes are /game/bot-<id>; /game itself cannot be listed, so guess ids.
    for i in range(1, 200):
        path = f"/game/bot-{i}"
        if path != os.environ["HOME"] and os.path.exists(path):
            return os.listdir(path)
    raise LookupError("no other home found")

probe("read_opponent_home", read_opponent_home)
probe("list_homes", lambda: os.listdir("/game"))
probe("read_opponent_input", lambda: os.listdir("/match/b") + os.listdir("/match/a"))
probe("read_output", lambda: os.listdir("/match/output"))
probe("network", lambda: socket.create_connection(("1.1.1.1", 53), timeout=2))
probe("write_rootfs", lambda: open("/usr/evil", "w"))
probe("kill_engine", lambda: os.kill(engine_pid(), signal.SIGKILL))
probe("read_engine_memory", lambda: open(f"/proc/{engine_pid()}/maps").read())

def read_opponent_memory():
    for pid in os.listdir("/proc"):
        if pid.isdigit() and os.stat(f"/proc/{pid}").st_uid not in (0, os.getuid()):
            return open(f"/proc/{pid}/maps").read()
    raise LookupError("no opponent process found")

probe("read_opponent_memory", read_opponent_memory)
probe("setuid_root", lambda: os.setuid(0))
"""

FORK_BOMB = """
import os, time
forked = 0
for _ in range(2000):
    try:
        pid = os.fork()
    except OSError:
        break
    if pid == 0:
        time.sleep(600)
        os._exit(0)
    forked += 1
print("FORKED", forked, flush=True)
"""

LINGERING_CHILD = """
import os, time
if os.fork() == 0:
    time.sleep(600)  # keeps the bot's stdout pipe open after the game
    os._exit(0)
"""

TRICKLE_BOT = """
import socket, sys, time
sock = socket.create_connection(("localhost", int(sys.argv[-1])))
sock.makefile("rb").readline()
while True:  # never finish a line: stall the engine as long as possible
    sock.sendall(b" ")
    time.sleep(1)
"""

# Gigabytes of output, while the engine is listening: it buffers bot output in memory.
OUTPUT_FLOOD = """
import sys
block = b"x" * (1 << 20)
for _ in range(4000):
    sys.stdout.buffer.write(block)
sys.stdout.flush()
"""

ENDLESS_LINE_BOT = """
import socket, sys
sock = socket.create_connection(("localhost", int(sys.argv[-1])))
sock.makefile("rb").readline()
chunk = b"x" * (1 << 20)
while True:  # one line that never ends: make the engine buffer it all
    sock.sendall(chunk)
"""

MEMORY_HOG = """
import time
hog = b"x" * (2000 << 20)  # over the default 1536 MB per bot
time.sleep(2)
"""


@pytest.fixture
def worker(
    config: Config, conn: sqlite3.Connection, live_server: str, tmp_path: Path
) -> Iterator[Worker]:
    """A real worker (real Docker) talking to the app over HTTP."""
    settings_module.update(conn, "game_num_hands", "200")
    w = Worker(
        replace(config, server_url=live_server, data_dir=tmp_path / "worker-data"),
        machine=Machine(cpus=[0, 1, 2, 3], memory_mb=16_000),
    )
    thread = threading.Thread(target=w.run, kwargs={"install_signal_handlers": False})
    thread.start()
    yield w
    w.stop()
    thread.join(timeout=60)


def python_variant(tmp_path: Path, name: str, prelude: str) -> bytes:
    """The fixture Python bot with extra code run before it connects."""
    source = tmp_path / name
    shutil.copytree(FIXTURE_BOTS / "python", source)
    player = source / "player.py"
    player.write_text(prelude + "\n" + player.read_text())
    return zip_dir(source)


TEAM_NUMBERS = itertools.count()


def wait_built(conn: sqlite3.Connection, bot_id: int) -> sqlite3.Row:
    """Wait for the worker to build an upload."""
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        build = builds.get(conn, bot_id)
        if build is not None and build["status"] in ("ready", "failed"):
            return build
        time.sleep(0.2)
    raise AssertionError("build did not finish")


def upload(
    conn: sqlite3.Connection, storage: Storage, tmp_path: Path, name: str, bot: bytes
) -> tuple[int, sqlite3.Row]:
    """Upload a bot through a new team, and wait for the worker to build it."""
    team, bot_id = add_team_with_bot(conn, storage, name, bot, tmp_path, build=False)
    return team, wait_built(conn, bot_id)


def play(
    worker: Worker,
    conn: sqlite3.Connection,
    storage: Storage,
    tmp_path: Path,
    bot_a: bytes,
    bot_b: bytes,
) -> tuple[sqlite3.Row, dict[str, str]]:
    n = next(TEAM_NUMBERS)
    a, build_a = upload(conn, storage, tmp_path, f"A{n}", bot_a)
    b, build_b = upload(conn, storage, tmp_path, f"B{n}", bot_b)
    assert build_a["status"] == "ready", build_a["error"]
    assert build_b["status"] == "ready", build_b["error"]
    matches.challenge(conn, Settings(conn), a, b)
    game_id = conn.execute("SELECT max(id) FROM games").fetchone()[0]
    deadline = time.monotonic() + 300
    while True:
        row = matches.get_game(conn, game_id)
        assert row is not None
        if row["status"] in ("done", "error") or time.monotonic() > deadline:
            break
        time.sleep(0.2)
    game = row
    logs = {}
    for kind in ("game", "a", "b", "engine"):
        path = storage.log_path(game["id"], kind)
        if path.exists():
            logs[kind] = gzip.decompress(path.read_bytes()).decode(errors="replace")
    return row, logs


def test_python_match(
    worker: Worker,
    conn: sqlite3.Connection,
    storage: Storage,
    tmp_path: Path,
    python_bot_zip: bytes,
) -> None:
    game, logs = play(worker, conn, storage, tmp_path, python_bot_zip, python_bot_zip)
    assert game["status"] == "done", game["error"]
    assert game["score_a"] + game["score_b"] == 0
    assert "Round #200" in logs["game"]
    assert "A connected successfully" in logs["engine"]
    assert "B connected successfully" in logs["engine"]


def test_java_vs_cpp(
    worker: Worker, conn: sqlite3.Connection, storage: Storage, tmp_path: Path
) -> None:
    java = zip_dir(FIXTURE_BOTS / "java", prefix="javabot-project")
    cpp = zip_dir(FIXTURE_BOTS / "cpp")
    game, logs = play(worker, conn, storage, tmp_path, java, cpp)
    assert game["status"] == "done", game["error"]
    assert "A connected successfully" in logs["engine"], logs["engine"]
    assert "B connected successfully" in logs["engine"], logs["engine"]


def test_hostile_bot_is_contained(
    worker: Worker,
    conn: sqlite3.Connection,
    storage: Storage,
    tmp_path: Path,
    python_bot_zip: bytes,
) -> None:
    hostile = python_variant(tmp_path, "hostile", HOSTILE_PROBES)
    # As player B, so the opponent's processes are already running.
    game, logs = play(worker, conn, storage, tmp_path, python_bot_zip, hostile)
    assert game["status"] == "done", game["error"]
    probes = [line for line in logs["b"].splitlines() if line.startswith("PROBE")]
    assert len(probes) == 10, logs["b"]
    assert all(" BLOCKED " in line for line in probes), probes
    for name in ("read_opponent_home", "read_engine_memory", "read_opponent_memory"):
        assert f"{name} BLOCKED PermissionError" in logs["b"], probes
    assert "A connected successfully" in logs["engine"]


def test_fork_bomb_cannot_block_opponent(
    worker: Worker,
    conn: sqlite3.Connection,
    storage: Storage,
    tmp_path: Path,
    python_bot_zip: bytes,
) -> None:
    bomb = python_variant(tmp_path, "bomb", FORK_BOMB)
    game, logs = play(worker, conn, storage, tmp_path, bomb, python_bot_zip)
    assert game["status"] == "done", game["error"]
    forked = int(logs["a"].split("FORKED")[1].split()[0])
    assert forked < 256
    assert "B connected successfully" in logs["engine"]


def test_lingering_child_cannot_stall_the_match(
    worker: Worker,
    conn: sqlite3.Connection,
    storage: Storage,
    tmp_path: Path,
    python_bot_zip: bytes,
) -> None:
    lingerer = python_variant(tmp_path, "lingerer", LINGERING_CHILD)
    started = time.monotonic()
    game, _ = play(worker, conn, storage, tmp_path, lingerer, python_bot_zip)
    assert game["status"] == "done", game["error"]
    assert time.monotonic() - started < 60


def test_trickling_bot_is_cut_off(
    worker: Worker,
    conn: sqlite3.Connection,
    storage: Storage,
    tmp_path: Path,
    python_bot_zip: bytes,
) -> None:
    settings_module.update(conn, "game_time_bank_seconds", "3")
    source = tmp_path / "trickle"
    source.mkdir()
    (source / "commands.json").write_text('{"build": [], "run": ["python3", "trickle.py"]}')
    (source / "trickle.py").write_text(TRICKLE_BOT)
    started = time.monotonic()
    game, logs = play(worker, conn, storage, tmp_path, zip_dir(source), python_bot_zip)
    assert game["status"] == "done", game["error"]
    assert "A kept the engine waiting past its game clock" in logs["engine"]
    assert game["winner"] == "b"
    assert time.monotonic() - started < 60


def test_output_flood_is_capped(
    worker: Worker,
    conn: sqlite3.Connection,
    storage: Storage,
    tmp_path: Path,
    python_bot_zip: bytes,
) -> None:
    flood = python_variant(tmp_path, "flood", OUTPUT_FLOOD)
    game, logs = play(worker, conn, storage, tmp_path, flood, python_bot_zip)
    assert game["status"] == "done", game["error"]
    assert 0 < len(logs["a"]) <= Settings(conn).number("player_log_size_limit")


def test_build_output_flood_is_capped(
    worker: Worker,
    conn: sqlite3.Connection,
    storage: Storage,
    tmp_path: Path,
    python_bot_zip: bytes,
) -> None:
    source = tmp_path / "noisy-build"
    shutil.copytree(FIXTURE_BOTS / "python", source)
    build = ["python3", "-c", OUTPUT_FLOOD]
    (source / "commands.json").write_text(
        json.dumps({"build": build, "run": ["python3", "player.py"]})
    )
    game, logs = play(worker, conn, storage, tmp_path, zip_dir(source), python_bot_zip)
    assert game["status"] == "done", game["error"]
    assert "A connected successfully" in logs["engine"]
    build_log = gzip.decompress(storage.build_log(game["bot_a_id"]).read_bytes())
    assert 0 < len(build_log) <= 1 << 20


# Run by a build: it must see nothing but its own bot, and change nothing else.
BUILD_PROBES = """
import os, socket
def probe(name, action):
    try:
        action()
        print("PROBE", name, "ALLOWED", flush=True)
    except Exception as exc:
        print("PROBE", name, "BLOCKED", type(exc).__name__, flush=True)
probe("network", lambda: socket.create_connection(("1.1.1.1", 53), timeout=2))
probe("write_rootfs", lambda: open("/usr/evil", "w"))
probe("read_inputs", lambda: os.listdir("/match"))
probe("list_homes", lambda: os.listdir("/game"))
probe("setuid_root", lambda: os.setuid(0))
open("home.txt", "w").write(os.environ["HOME"])
"""

# Fails unless the bot runs at the path it was built at.
CHECK_BUILT_HOME = """
import os
assert open("home.txt").read() == os.environ["HOME"], "built at a different path"
"""


def test_build_is_sandboxed_and_paths_survive(
    worker: Worker,
    conn: sqlite3.Connection,
    storage: Storage,
    tmp_path: Path,
    python_bot_zip: bytes,
) -> None:
    source = tmp_path / "prober"
    shutil.copytree(FIXTURE_BOTS / "python", source)
    player = source / "player.py"
    player.write_text(CHECK_BUILT_HOME + "\n" + player.read_text())
    (source / "commands.json").write_text(
        json.dumps({"build": ["python3", "-c", BUILD_PROBES], "run": ["python3", "player.py"]})
    )
    bot = zip_dir(source)
    # As player B too: the bot's home does not depend on its seat.
    for first, second in ((bot, python_bot_zip), (python_bot_zip, bot)):
        game, logs = play(worker, conn, storage, tmp_path, first, second)
        assert game["status"] == "done", game["error"]
        assert "A connected successfully" in logs["engine"], logs["engine"]
        assert "B connected successfully" in logs["engine"], logs["engine"]
    build_log = gzip.decompress(storage.build_log(game["bot_b_id"]).read_bytes())
    probes = [line for line in build_log.decode().splitlines() if line.startswith("PROBE")]
    assert len(probes) == 5 and all(" BLOCKED " in line for line in probes), probes


def test_failed_build_keeps_the_previous_bot(
    worker: Worker,
    conn: sqlite3.Connection,
    storage: Storage,
    tmp_path: Path,
    python_bot_zip: bytes,
) -> None:
    team, first = upload(conn, storage, tmp_path, "Aces", python_bot_zip)
    broken = tmp_path / "broken"
    shutil.copytree(FIXTURE_BOTS / "cpp", broken)
    (broken / "src" / "main.cpp").write_text("int main() { return syntax error; }")
    upload_path = tmp_path / "broken.zip"
    upload_path.write_bytes(zip_dir(broken))
    user = conn.execute("SELECT id FROM users").fetchone()[0]
    second = wait_built(
        conn, bots.create(conn, storage, Settings(conn), team, user, "", upload_path)
    )
    assert second["status"] == "failed" and "exited with code" in second["error"]
    log = gzip.decompress(storage.build_log(second["bot_id"]).read_bytes()).decode()
    assert "error" in log
    current = conn.execute("SELECT current_bot_id FROM teams WHERE id = ?", (team,))
    assert current.fetchone()[0] == first["bot_id"]


def test_endless_line_cannot_exhaust_engine_memory(
    worker: Worker,
    conn: sqlite3.Connection,
    storage: Storage,
    tmp_path: Path,
    python_bot_zip: bytes,
) -> None:
    source = tmp_path / "endless"
    source.mkdir()
    (source / "commands.json").write_text('{"build": [], "run": ["python3", "endless.py"]}')
    (source / "endless.py").write_text(ENDLESS_LINE_BOT)
    game, _ = play(worker, conn, storage, tmp_path, zip_dir(source), python_bot_zip)
    assert game["status"] == "done", game["error"]
    assert game["winner"] == "b"


REPORT_HARDWARE = """
import os
print("HARDWARE", len(os.sched_getaffinity(0)), os.environ["OMP_NUM_THREADS"], flush=True)
hog = b"x" * (2500 << 20)  # over 1-core games' 1536 MB per bot, under 2-core games' 3072
"""


def test_admin_hardware_settings_reach_the_bots(
    worker: Worker,
    conn: sqlite3.Connection,
    storage: Storage,
    tmp_path: Path,
    python_bot_zip: bytes,
) -> None:
    settings_module.update(conn, "cores_per_game", "2")
    reporter = python_variant(tmp_path, "reporter", REPORT_HARDWARE)
    game, logs = play(worker, conn, storage, tmp_path, reporter, python_bot_zip)
    assert game["status"] == "done", game["error"]
    assert (game["cores"], game["bot_memory_mb"]) == (2, 3072)
    assert "HARDWARE 2 2" in logs["a"]
    assert "memory limit" not in logs["engine"]


def test_memory_hog_loses_instead_of_opponent(
    worker: Worker,
    conn: sqlite3.Connection,
    storage: Storage,
    tmp_path: Path,
    python_bot_zip: bytes,
) -> None:
    hog = python_variant(tmp_path, "hog", MEMORY_HOG)
    game, logs = play(worker, conn, storage, tmp_path, python_bot_zip, hog)
    assert game["status"] == "done", game["error"]
    assert "B used" in logs["engine"] and "memory limit" in logs["engine"], logs["engine"]
    assert game["winner"] == "a"


PARALLEL_WORK = """
import os, time
def spin(n):
    total = 0
    for i in range(n):
        total += i
    return total
cores, work = int(os.environ["SCRIMMAGE_CORES"]), 4_000_000
started = time.perf_counter()
spin(work * cores)
serial = time.perf_counter() - started
started = time.perf_counter()
children = []
for _ in range(cores):
    pid = os.fork()
    if pid == 0:
        spin(work)
        os._exit(0)
    children.append(pid)
for pid in children:
    os.waitpid(pid, 0)
print("SPEEDUP", cores, round(serial / (time.perf_counter() - started), 2), flush=True)
"""


def test_multi_core_bots_run_in_parallel(
    worker: Worker,
    conn: sqlite3.Connection,
    storage: Storage,
    tmp_path: Path,
    python_bot_zip: bytes,
) -> None:
    settings_module.update(conn, "cores_per_game", "4")
    parallel = python_variant(tmp_path, "parallel", PARALLEL_WORK)
    game, logs = play(worker, conn, storage, tmp_path, parallel, python_bot_zip)
    assert game["status"] == "done", game["error"]
    cores, speedup = logs["a"].split("SPEEDUP")[1].split()[:2]
    assert int(cores) == 4 and float(speedup) > 2.5, logs["a"]


# 700 MB shared by a pool of 4 forked workers: about 700 MB in all, not 3.5 GB.
FORKED_POOL = """
import multiprocessing as mp, os
big = bytearray(700 << 20)
def touch(i):
    return big[i * 4096] + len(big)
with mp.get_context("fork").Pool(int(os.environ["SCRIMMAGE_CORES"])) as pool:
    print("POOL", sum(pool.map(touch, range(64))) // (64 << 20), flush=True)
"""

# 2 GB in /dev/shm: over the default 1.5 GB per bot, so it counts against this bot.
SHM_HOG = """
import time
with open("/dev/shm/hog", "wb") as f:
    for _ in range(2048):
        f.write(b"x" * (1 << 20))
time.sleep(2)
"""


def test_forked_workers_share_memory_and_shm_counts(
    worker: Worker,
    conn: sqlite3.Connection,
    storage: Storage,
    tmp_path: Path,
    python_bot_zip: bytes,
) -> None:
    settings_module.update(conn, "cores_per_game", "4")
    pool = python_variant(tmp_path, "pool", FORKED_POOL)
    game, logs = play(worker, conn, storage, tmp_path, pool, python_bot_zip)
    assert game["status"] == "done", game["error"]
    assert "POOL 700" in logs["a"], logs["a"] + logs["engine"]
    assert "memory limit" not in logs["engine"]

    settings_module.update(conn, "cores_per_game", "1")  # 1.5 GB per bot
    hog = python_variant(tmp_path, "shm-hog", SHM_HOG)
    game, logs = play(worker, conn, storage, tmp_path, python_bot_zip, hog)
    assert game["status"] == "done", game["error"]
    assert "B used" in logs["engine"] and "memory limit" in logs["engine"], logs["engine"]
    assert "A connected successfully" in logs["engine"] and game["winner"] == "a"
