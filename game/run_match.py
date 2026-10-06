"""
In-container match runner. This is the game image's entrypoint.

The worker starts one container per match with no network, a read-only root
filesystem, and this layout:

    /match               tmpfs, mode 0700: bots cannot traverse into it
    /match/a, /match/b   the two built bots (read-only bind mounts)
    /match/match.json    parameters (read-only)
    /match/output        bind mount for results, written only by this script
    /game                tmpfs, root-owned, mode 0711: the engine's working dir
    /game/bot-<id>       one size-capped tmpfs per bot: its home and TMPDIR

A bot always lives at /game/bot-<id>, when it is built and in every game, as A
or as B, so anything its build step bakes in (absolute paths, rpaths, venvs)
keeps working.

With MODE=build in match.json, this script instead builds one bot (see
``build_main``): it runs the bot's "build" command alone in the container and
packs the result, which every later game uses as-is.

This script runs the stock engine (``engine.py``, unmodified) as root and
patches what it needs around it, so that neither bot can make the other lose:

1. Privilege separation. Each bot's build and run commands execute as its own
   uid (unique per worker slot, so limits are per bot) with a private 0700
   home, so a bot cannot read, modify, signal, or ptrace its opponent.
2. Connection identity. The engine accepts the first TCP connection on each
   player's port. Without a check, bot A could race to connect to bot B's port
   and play both seats. Accepted connections are verified against the expected
   uid via /proc/net/tcp and impostors are dropped.
3. Turn-based CPU. While one bot is being queried, every process of the other
   bot is SIGSTOPped, so a bot cannot burn CPU on its opponent's clock. Both
   bots share the game's pinned cores (CORES), so each may use that many
   threads while it thinks; thread-count variables are set to match.
4. Per-bot resource caps. Each bot may run at most BOT_NPROC processes/threads
   (RLIMIT_NPROC), so it cannot exhaust the container's pid limit, and a
   memory guard kills a bot whose resident memory plus home-directory usage
   exceeds its half of the container's memory, before the kernel's OOM killer
   could pick the opponent instead.
5. Bounded waits. The engine trusts bots to answer and to exit. A bot that
   trickles an answer one byte at a time is killed once it overruns its game
   clock, and when the game ends every process a bot left behind is killed,
   so nothing it forked can hold the engine's pipes open. Either way the
   offender loses the game; it cannot turn a loss into a timed-out match.
6. Bounded engine memory. The engine keeps everything a bot prints and reads
   socket lines of any length. Bot output is passed through a reader that
   keeps only PLAYER_LOG_SIZE_LIMIT bytes, and socket lines are cut at
   MAX_LINE characters, so no bot can make the engine itself run out of
   memory (which would end the match unrated instead of as a loss).
"""

from __future__ import annotations

import contextlib
import json
import os
import resource
import shutil
import signal
import socket
import string
import subprocess
import sys
import tarfile
import threading
import time
import traceback
from pathlib import Path
from typing import Any

INPUT = Path("/match")
GAME = Path("/game")
OUTPUT = Path("/match/output")
RUNNER_DIR = Path(__file__).resolve().parent
BOT_TMP = ".tmp"

# Must match the container's pids limit in scrimmage/worker/sandbox.py.
BOT_NPROC = 256
MEMORY_POLL_SECONDS = 0.05
# How long an exact (PSS) memory measurement is reused for a bot that forks.
EXACT_REFRESH_SECONDS = 0.25
# How far past its game clock a single answer may run before the bot is killed.
QUERY_GRACE_SECONDS = 2.0
# How long a bot gets to exit after the game before its processes are killed.
QUIT_GRACE_SECONDS = 5.0
# Longest line read from a bot's socket; real responses are a few characters.
MAX_LINE = 4096
OUTPUT_CHUNK = 1 << 16
PAGE_SIZE = os.sysconf("SC_PAGE_SIZE")

# Copied out of the sandbox; anything larger is truncated.
MAX_ENGINE_LOG_BYTES = 1 << 20
MAX_GAME_LOG_BYTES = 64 << 20

# Filled in by _configure() from match.json.
PLAYERS: dict[str, tuple[str, int]] = {}  # engine player name -> (home dir name, uid)
PATH_TO_UID: dict[str, int] = {}
ALL_BOT_UIDS: frozenset[int] = frozenset()
OUTPUT_LIMIT = 1 << 20  # bytes of each bot's output kept; set from match.json
CORES = 1  # the game's CPUs; the active bot may use all of them

_expected_connect_uid: int | None = None
# The bot currently allowed to run; every other bot is SIGSTOPped.
_active_uid: int | None = None


def _configure(params: dict[str, Any]) -> None:
    global ALL_BOT_UIDS, OUTPUT_LIMIT, CORES
    OUTPUT_LIMIT = int(params.get("PLAYER_LOG_SIZE_LIMIT", OUTPUT_LIMIT))
    CORES = int(params.get("CORES", 1))
    PLAYERS["A"] = (str(params.get("BOT_DIR_A", "a")), int(params.get("BOT_UID_A", 2001)))
    if params.get("MODE") != "build":
        PLAYERS["B"] = (str(params.get("BOT_DIR_B", "b")), int(params.get("BOT_UID_B", 2002)))
    PATH_TO_UID.update({str(GAME / sub): uid for sub, uid in PLAYERS.values()})
    ALL_BOT_UIDS = frozenset(uid for _, uid in PLAYERS.values())


# ---------------------------------------------------------------------------
# Process control by uid
# ---------------------------------------------------------------------------


def _real_uid(pid: str) -> int | None:
    """Real uid of a process. Unlike the owner of /proc/<pid>, a process cannot
    hide it (non-dumpable processes show up as owned by root)."""
    try:
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                if line.startswith("Uid:"):
                    return int(line.split()[1])
    except (FileNotFoundError, ProcessLookupError):
        pass
    return None


def _bot_pids() -> dict[int, list[int]]:
    """Map each bot uid to its live pids."""
    found: dict[int, list[int]] = {uid: [] for uid in ALL_BOT_UIDS}
    for entry in os.scandir("/proc"):
        if not entry.name.isdigit():
            continue
        try:
            uid: int | None = entry.stat().st_uid
        except FileNotFoundError:
            continue
        if uid == 0:
            uid = _real_uid(entry.name)
        if uid in found:
            found[uid].append(int(entry.name))
    return found


def _signal_pids(pids: list[int], sig: signal.Signals) -> None:
    for pid in pids:
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, sig)


def _signal_uid(uid: int, sig: signal.Signals) -> None:
    _signal_pids(_bot_pids()[uid], sig)


def _activate(uid: int, force: bool = False) -> None:
    """Let ``uid`` run and freeze every other bot."""
    global _active_uid
    if uid == _active_uid and not force:
        return
    pids = _bot_pids()
    for other in ALL_BOT_UIDS - {uid}:
        _signal_pids(pids[other], signal.SIGSTOP)
    _signal_pids(pids[uid], signal.SIGCONT)
    _active_uid = uid


def _thaw_all() -> None:
    global _active_uid
    for pids in _bot_pids().values():
        _signal_pids(pids, signal.SIGCONT)
    _active_uid = None


def _kill_all_bots() -> None:
    for pids in _bot_pids().values():
        _signal_pids(pids, signal.SIGCONT)
        _signal_pids(pids, signal.SIGKILL)


# Reported to the server with the result (stored with the game).
STATS: dict[str, Any] = {"bots": {}, "events": []}


def _event(message: str) -> None:
    STATS["events"].append(message)
    print(message, flush=True)


def _kill_bot(name: str, reason: str) -> None:
    _signal_uid(PLAYERS[name][1], signal.SIGKILL)
    _event(f"{name} {reason}; its processes were killed")


def _reap_after_quit(
    name: str, proc: subprocess.Popen[bytes] | None, done: threading.Event
) -> None:
    """Kill everything a bot left running once its main process has exited.

    Runs while the engine waits for the bot's output to end; leftover children
    holding the output pipe would otherwise make that wait last forever.
    """
    uid = PLAYERS[name][1]
    deadline = time.monotonic() + QUIT_GRACE_SECONDS
    while not done.is_set():
        if proc is None or proc.poll() is not None or time.monotonic() > deadline:
            _signal_uid(uid, signal.SIGKILL)
            done.wait(0.5)
        else:
            done.wait(0.05)


# ---------------------------------------------------------------------------
# Memory guard
# ---------------------------------------------------------------------------


# Shared scratch directories; a bot's files in them count toward its memory.
SHARED_DIRS = (Path("/tmp"), Path("/dev/shm"))


def _anon_bytes(pid: int) -> int:
    """Cheap upper bound of a process's private memory (resident minus file/shmem pages).

    Pages a forked child still shares copy-on-write with its parent count in
    both, so summing this over a bot's processes can overstate its usage.
    """
    try:
        with open(f"/proc/{pid}/statm") as f:
            fields = f.read().split()
        return (int(fields[1]) - int(fields[2])) * PAGE_SIZE
    except (FileNotFoundError, ProcessLookupError, IndexError, ValueError):
        return 0


def _pss_anon_bytes(pid: int) -> int:
    """A process's proportional share of anonymous memory (exact across forks, but slower).

    Reading another user's smaps needs CAP_SYS_PTRACE, which only this runner has.
    """
    try:
        with open(f"/proc/{pid}/smaps_rollup") as f:
            for line in f:
                if line.startswith("Pss_Anon:"):
                    return int(line.split()[1]) << 10
    except (FileNotFoundError, ProcessLookupError):
        return 0  # the process is gone
    raise ValueError(f"no Pss_Anon for process {pid}")


def _disk_bytes(path: Path) -> int:
    stats = os.statvfs(path)
    return (stats.f_blocks - stats.f_bfree) * stats.f_frsize


def _shared_dir_bytes() -> dict[int, int]:
    """Bytes of files in the shared scratch directories, by owner uid."""
    usage: dict[int, int] = {}
    stack = [str(d) for d in SHARED_DIRS if d.is_dir()]
    while stack:
        with contextlib.suppress(OSError), os.scandir(stack.pop()) as entries:
            for entry in entries:
                with contextlib.suppress(OSError):
                    info = entry.stat(follow_symlinks=False)
                    usage[info.st_uid] = usage.get(info.st_uid, 0) + info.st_blocks * 512
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(entry.path)
    return usage


class MemoryGuard(threading.Thread):
    """Kill a bot whose memory exceeds its cap.

    A bot's memory is its processes' anonymous memory plus the files it wrote:
    its home, and what it owns in /tmp and /dev/shm. The cheap estimate is
    checked first; only when it is over the cap is the exact (PSS) figure
    computed, so bots that fork many workers sharing memory are not overcounted.
    """

    def __init__(self, cap_bytes: int) -> None:
        super().__init__(name="memory-guard", daemon=True)
        self.cap_bytes = cap_bytes
        self.stopped = threading.Event()
        self.reported: set[str] = set()
        self.peak: dict[str, int] = {}
        # name -> (cheap estimate, exact figure, when): the last exact measurement.
        self.exact: dict[str, tuple[int, int, float]] = {}

    def run(self) -> None:
        while not self.stopped.wait(MEMORY_POLL_SECONDS):
            try:
                self.check()
            except Exception:
                # Never stop guarding: report and keep going.
                traceback.print_exc()

    def measure(self, name: str, pids: list[int], files: int) -> int:
        cheap = sum(_anon_bytes(pid) for pid in pids) + files
        if cheap <= self.cap_bytes:
            return cheap
        # Over by the cheap estimate: maybe only from pages forked processes share.
        now = time.monotonic()
        last = self.exact.get(name)
        if last is not None and now - last[2] < EXACT_REFRESH_SECONDS:
            estimate = last[1] + max(0, cheap - last[0])  # plus any growth since
            if estimate <= self.cap_bytes:
                return estimate
        try:
            exact = sum(_pss_anon_bytes(pid) for pid in pids) + files
        except (OSError, ValueError):
            return cheap  # can't tell: hold the bot to the cheap estimate
        self.exact[name] = (cheap, exact, now)
        return exact

    def check(self) -> None:
        pids = _bot_pids()
        shared = _shared_dir_bytes()
        for name, (sub, uid) in PLAYERS.items():
            files = _disk_bytes(GAME / sub) + shared.get(uid, 0)
            used = self.measure(name, pids[uid], files)
            self.peak[name] = max(self.peak.get(name, 0), used)
            if used <= self.cap_bytes:
                continue
            _signal_pids(pids[uid], signal.SIGKILL)
            if name not in self.reported:
                self.reported.add(name)
                _event(
                    f"{name} used {used >> 20} MB, over its {self.cap_bytes >> 20} MB "
                    "memory limit; its processes were killed"
                )


# ---------------------------------------------------------------------------
# subprocess shim: run bot commands as the bot's uid
# ---------------------------------------------------------------------------


def _limit_processes() -> None:
    # Runs in the child after it switched to the bot's uid, just before exec.
    resource.setrlimit(resource.RLIMIT_NPROC, (BOT_NPROC, BOT_NPROC))


def _oom_first(pid: int) -> None:
    """If the container ever runs out of memory, the kernel kills bots, not the engine.

    Set from here (as root) because the bot's uid can't; its children inherit it.
    """
    with contextlib.suppress(OSError), open(f"/proc/{pid}/oom_score_adj", "w") as f:
        f.write("1000")


def _bot_kwargs(kwargs: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    cwd = str(kwargs.get("cwd", ""))
    if cwd not in PATH_TO_UID:
        raise RuntimeError(f"engine launched a process with unexpected cwd={cwd!r}")
    uid = PATH_TO_UID[cwd]
    tmp = f"{cwd}/{BOT_TMP}"
    kwargs.update(
        user=uid,
        group=uid,
        extra_groups=[],
        umask=0o077,  # its files in the shared /tmp and /dev/shm are private
        start_new_session=True,
        preexec_fn=_limit_processes,
        env=_bot_env(cwd, tmp),
    )
    return uid, kwargs


def _bot_env(home: str, tmp: str) -> dict[str, str]:
    threads = str(CORES)  # the game's cores; more threads would only contend
    return {
        "PATH": os.environ["PATH"],
        "HOME": home,
        "TMPDIR": tmp,
        "LANG": "C.UTF-8",
        "PYTHONUNBUFFERED": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "OMP_NUM_THREADS": threads,
        "OPENBLAS_NUM_THREADS": threads,
        "MKL_NUM_THREADS": threads,
        "TF_NUM_INTRAOP_THREADS": threads,
        "TF_NUM_INTEROP_THREADS": "1" if CORES == 1 else "2",
        "TF_CPP_MIN_LOG_LEVEL": "2",
        "JAVA_TOOL_OPTIONS": (
            f"-XX:ActiveProcessorCount={threads} "
            f"{'-XX:+UseSerialGC ' if CORES == 1 else ''}-XX:-UsePerfData "
            f"-Djava.io.tmpdir={tmp}"
        ),
        "SCRIMMAGE_CORES": threads,
    }


def _keep_capped(source: Any, keep: bytearray) -> None:
    """Read ``source`` to EOF, keeping only the first OUTPUT_LIMIT bytes."""
    try:
        while chunk := source.read1(OUTPUT_CHUNK):
            room = OUTPUT_LIMIT - len(keep)
            if room > 0:
                keep += chunk[:room]
    finally:
        source.close()


def _forward_capped(source: Any, write_fd: int) -> None:
    """Copy at most OUTPUT_LIMIT bytes from ``source`` to ``write_fd``; drop the rest."""
    sent = 0
    with os.fdopen(write_fd, "wb") as sink:
        try:
            while chunk := source.read1(OUTPUT_CHUNK):
                room = OUTPUT_LIMIT - sent
                if room > 0:
                    part = chunk[:room]
                    with contextlib.suppress(BrokenPipeError):
                        sink.write(part)
                        sink.flush()
                    sent += len(part)
        finally:
            source.close()


class _SubprocessShim:
    """Stands in for the ``subprocess`` module inside engine.py."""

    def __getattr__(self, name: str) -> Any:
        return getattr(subprocess, name)

    @staticmethod
    def run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        """The build step: like subprocess.run, but keeps only capped output."""
        uid, kwargs = _bot_kwargs(kwargs)
        timeout = kwargs.pop("timeout", None)
        kwargs.pop("check", None)
        _activate(uid, force=True)
        output = bytearray()
        try:
            with subprocess.Popen(args, **kwargs) as proc:
                _oom_first(proc.pid)
                reader = threading.Thread(target=_keep_capped, args=(proc.stdout, output))
                reader.start()
                try:
                    proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    _signal_uid(uid, signal.SIGKILL)
                    reader.join(5)
                    raise subprocess.TimeoutExpired(
                        args, timeout or 0, output=bytes(output)
                    ) from None
                # Background processes the build left would hold the pipe open.
                _signal_uid(uid, signal.SIGKILL)
                reader.join(5)
                return subprocess.CompletedProcess(args, proc.returncode, stdout=bytes(output))
        finally:
            _signal_uid(uid, signal.SIGKILL)

    @staticmethod
    def Popen(args: list[str], **kwargs: Any) -> subprocess.Popen[bytes]:
        global _expected_connect_uid
        uid, kwargs = _bot_kwargs(kwargs)
        _signal_uid(uid, signal.SIGKILL)
        _activate(uid, force=True)
        _expected_connect_uid = uid
        proc = subprocess.Popen(args, **kwargs)
        _oom_first(proc.pid)
        if proc.stdout is not None:
            # The engine buffers everything it reads from here; cap it.
            read_fd, write_fd = os.pipe()
            threading.Thread(
                target=_forward_capped, args=(proc.stdout, write_fd), daemon=True
            ).start()
            proc.stdout = os.fdopen(read_fd, "rb")
        return proc


# ---------------------------------------------------------------------------
# socket shim: only accept the connection from the expected bot
# ---------------------------------------------------------------------------


def _tcp_owner_uid(local_port: int, remote_port: int) -> int | None:
    for table in ("/proc/net/tcp", "/proc/net/tcp6"):
        with contextlib.suppress(FileNotFoundError), open(table) as f:
            next(f)
            for line in f:
                fields = line.split()
                lport = int(fields[1].rsplit(":", 1)[1], 16)
                rport = int(fields[2].rsplit(":", 1)[1], 16)
                if lport == local_port and rport == remote_port:
                    return int(fields[7])
    return None


class _LineLimitedFile:
    """A socket file whose readline() never buffers more than MAX_LINE characters."""

    def __init__(self, file: Any) -> None:
        self._file = file

    def readline(self, size: int = -1) -> Any:
        return self._file.readline(MAX_LINE if size < 0 else min(size, MAX_LINE))

    def __getattr__(self, name: str) -> Any:
        return getattr(self._file, name)


class _BotSocket(socket.socket):
    def makefile(self, *args: Any, **kwargs: Any) -> Any:
        return _LineLimitedFile(super().makefile(*args, **kwargs))


class _VerifiedSocket(socket.socket):
    def accept(self) -> tuple[socket.socket, Any]:
        timeout = self.gettimeout()
        deadline = None if timeout is None else time.monotonic() + timeout
        server_port = self.getsockname()[1]
        while True:
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("timed out waiting for the bot to connect")
                self.settimeout(remaining)
            conn, addr = super().accept()
            owner = _tcp_owner_uid(local_port=addr[1], remote_port=server_port)
            if owner is not None and owner == _expected_connect_uid:
                bot = _BotSocket(conn.family, conn.type, conn.proto, fileno=conn.detach())
                return bot, addr
            print(
                f"Rejected connection from uid {owner} (expected {_expected_connect_uid})",
                flush=True,
            )
            conn.close()


class _SocketShim:
    def __getattr__(self, name: str) -> Any:
        return getattr(socket, name)

    socket = _VerifiedSocket


# ---------------------------------------------------------------------------
# Setup and teardown
# ---------------------------------------------------------------------------


def _write_config(params: dict[str, Any]) -> None:
    template = string.Template((RUNNER_DIR / "config_template.py").read_text())
    values = {
        "player_1_path": repr(str(GAME / PLAYERS["A"][0])),
        "player_2_path": repr(str(GAME / PLAYERS["B"][0])),
        **{key: repr(value) for key, value in params.items()},
    }
    (GAME / "config.py").write_text(template.substitute(values))


def _install_bot(source: Path, home: str, uid: int) -> None:
    """Copy a bot into its tmpfs home (already mounted at /game/<home>)."""
    dest = GAME / home
    shutil.copytree(source, dest, symlinks=True, dirs_exist_ok=True)
    (dest / BOT_TMP).mkdir(exist_ok=True)
    for root, dirs, files in os.walk(dest):
        for name in dirs + files:
            os.lchown(os.path.join(root, name), uid, uid)
    os.chown(dest, uid, uid)
    os.chmod(dest, 0o700)


def _copy_out(src: Path, dest: Path, limit: int) -> None:
    if not src.is_file():
        return
    with open(src, "rb") as fin, open(dest, "wb") as fout:
        fout.write(fin.read(limit))


BUILD_ARCHIVE = "bot.tar.gz"
MAX_BUILD_LOG_BYTES = 1 << 20
MAX_BUILD_ARCHIVE_BYTES = 512 << 20  # fits the worker API's upload limit


def _pack(home: Path, archive: Path) -> None:
    """Archive a built bot: regular files, directories and symlinks only.

    Symlinks are kept as links (extraction on the worker refuses any that point
    outside the bot); anything else a build could create (FIFOs, sockets) is
    dropped.
    """

    def keep(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
        if info.name == f"./{BOT_TMP}" or info.name.startswith(f"./{BOT_TMP}/"):
            return None
        if not (info.isfile() or info.isdir() or info.issym()):
            return None
        info.uid = info.gid = 0
        info.uname = info.gname = ""
        return info

    with tarfile.open(archive, "w:gz", compresslevel=1) as tar:
        tar.add(home, arcname=".", filter=keep)


def build_main(params: dict[str, Any]) -> int:
    """Build one bot once. The engine is not involved.

    Runs the bot's "build" command as the bot's uid, with the same protections
    as a game (its own memory share and process limit, bounded output, a time
    limit). Afterwards the bot's commands.json gets an empty "build", so games
    run the built bot directly.
    """
    _configure(params)
    home, uid = PLAYERS["A"]
    _install_bot(INPUT / "a", home, uid)
    commands_path = GAME / home / "commands.json"
    status: dict[str, Any] = {"ok": False}
    log = b""
    guard = MemoryGuard(int(params.get("BOT_MEMORY_BYTES", 1 << 30)))
    started = time.monotonic()
    try:
        commands = json.loads(commands_path.read_text())
        build = commands.get("build") or []
        guard.start()
        if build:
            try:
                result = _SubprocessShim.run(
                    build,
                    cwd=str(GAME / home),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    timeout=float(params.get("BUILD_TIMEOUT", 600)),
                )
                log = result.stdout
                if result.returncode != 0:
                    status = {"ok": False, "error": f"build exited with code {result.returncode}"}
                else:
                    status = {"ok": True}
            except subprocess.TimeoutExpired as exc:
                log = exc.output or b""
                status = {"ok": False, "error": "build timed out"}
            except OSError as exc:
                status = {"ok": False, "error": f"could not start the build: {exc}"}
        else:
            status = {"ok": True}
        guard.stopped.set()
        _kill_all_bots()
        if guard.reported:
            status = {"ok": False, "error": "build exceeded the memory limit"}
        if status["ok"]:
            commands["build"] = []
            os.chown(commands_path, 0, 0)
            commands_path.write_text(json.dumps(commands, indent=2) + "\n")
            _pack(GAME / home, OUTPUT / BUILD_ARCHIVE)
            size = (OUTPUT / BUILD_ARCHIVE).stat().st_size
            if size > MAX_BUILD_ARCHIVE_BYTES:
                (OUTPUT / BUILD_ARCHIVE).unlink()
                status = {
                    "ok": False,
                    "error": f"the built bot is {size >> 20} MB compressed, over the "
                    f"{MAX_BUILD_ARCHIVE_BYTES >> 20} MB limit",
                }
    except Exception:
        status = {"ok": False, "error": "internal error\n" + traceback.format_exc()}
    finally:
        guard.stopped.set()
        _kill_all_bots()
        status["seconds"] = round(time.monotonic() - started, 1)
        (OUTPUT / "build.log").write_bytes(log[:MAX_BUILD_LOG_BYTES])
        (OUTPUT / "status.json").write_text(json.dumps(status))
    return 0


def main() -> int:
    params = json.loads((INPUT / "match.json").read_text())
    if params.get("MODE") == "build":
        return build_main(params)
    _configure(params)
    for name, (home, uid) in PLAYERS.items():
        _install_bot(INPUT / name.lower(), home, uid)
    _write_config(params)

    os.chdir(GAME)
    sys.path.insert(0, str(RUNNER_DIR))

    engine_log = open(OUTPUT / "engine.log", "w", buffering=1)
    status: dict[str, Any] = {"ok": False}
    guard = MemoryGuard(int(params.get("BOT_MEMORY_BYTES", 1 << 30)))
    try:
        with contextlib.redirect_stdout(engine_log), contextlib.redirect_stderr(engine_log):
            guard.start()
            import engine  # noqa: PLC0415 -- must import after chdir; reads ./config.py

            engine.subprocess = _SubprocessShim()
            engine.socket = _SocketShim()

            original_query = engine.Player.query
            original_stop = engine.Player.stop

            def query(self: Any, *args: Any, **kwargs: Any) -> Any:
                _activate(PLAYERS[self.name][1])
                watchdog = threading.Timer(
                    max(self.game_clock, 0.0) + QUERY_GRACE_SECONDS,
                    _kill_bot,
                    args=(self.name, "kept the engine waiting past its game clock"),
                )
                watchdog.start()
                try:
                    return original_query(self, *args, **kwargs)
                finally:
                    watchdog.cancel()

            def stop(self: Any) -> None:
                STATS["bots"][self.name] = {
                    "connected": self.socketfile is not None,
                    "clock_used": round(float(params["STARTING_GAME_CLOCK"]) - self.game_clock, 3),
                    "clock_out": self.game_clock <= 0,
                    "bankroll": self.bankroll,
                }
                _thaw_all()
                done = threading.Event()
                reaper = threading.Thread(
                    target=_reap_after_quit,
                    args=(self.name, self.bot_subprocess, done),
                    daemon=True,
                )
                reaper.start()
                try:
                    original_stop(self)
                finally:
                    done.set()

            engine.Player.query = query
            engine.Player.stop = stop

            engine.Game().run()
        status = {"ok": True}
    except BaseException:
        status = {"ok": False, "error": traceback.format_exc()}
    finally:
        guard.stopped.set()
        _kill_all_bots()
        for name, peak in guard.peak.items():
            STATS["bots"].setdefault(name, {})["peak_memory_mb"] = peak >> 20
        status["stats"] = STATS
        engine_log.close()
        _copy_out(OUTPUT / "engine.log", OUTPUT / "engine.log.tmp", MAX_ENGINE_LOG_BYTES)
        os.replace(OUTPUT / "engine.log.tmp", OUTPUT / "engine.log")
        limit = int(params.get("PLAYER_LOG_SIZE_LIMIT", 1 << 20))
        _copy_out(GAME / "gamelog.txt", OUTPUT / "game.log", MAX_GAME_LOG_BYTES)
        _copy_out(GAME / "A.txt", OUTPUT / "a.log", limit)
        _copy_out(GAME / "B.txt", OUTPUT / "b.log", limit)
        (OUTPUT / "status.json").write_text(json.dumps(status))
    return 0 if status["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
