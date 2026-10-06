"""
The match worker: builds bots and plays games from the server's queue, one
sandboxed container per job.

The same program runs on the main server and on every fleet machine. It talks
to the server only through the worker API, so a worker never touches the
database and fleet machines need nothing but the API URL and token.

Each job says what it needs (cores and memory; see services/hardware.py). The
worker keeps a ledger of its free CPUs and memory (``Machine``), claims jobs
that fit, and gives each its own CPUs for its whole run.

    dispatcher thread   claims builds and games that fit the free resources
    one thread per job  builds a bot, or plays a game, in a container
    heartbeat thread    renews leases; kills jobs the server took back

On SIGTERM (systemd stop, instance termination) it kills its containers and
tells the server to requeue everything it held. If the server runs newer code
it exits with EXIT_OUTDATED, and a fleet machine then shuts down so the fleet
replaces it with an up-to-date one.
"""

from __future__ import annotations

import gzip
import logging
import os
import platform
import random
import secrets
import shutil
import signal
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import requests

from scrimmage.config import Config
from scrimmage.services.bots import extract
from scrimmage.services.hardware import container_memory_mb
from scrimmage.services.queue import parse_scores
from scrimmage.worker import sandbox as sandbox_module
from scrimmage.worker.cache import BotCache
from scrimmage.worker.client import LeaseLost, Outdated, ServerClient
from scrimmage.worker.sandbox import MatchOutput, Sandbox

log = logging.getLogger(__name__)

EXIT_OUTDATED = 3
IDLE_POLL_SECONDS = 3.0
HEARTBEAT_SECONDS = 15.0
SUBMIT_RETRY_SECONDS = 600.0
RESERVED_MEMORY_MB = 1024  # OS and Docker (and the website, on the main server)
LOG_FILES = {"game": "game.log", "a": "a.log", "b": "b.log", "engine": "engine.log"}


class MatchRunner(Protocol):
    def run(
        self,
        *,
        job: str,
        run_dir: Path,
        bot_a: Path,
        bot_b: Path | None,
        params: dict[str, Any],
        cpus: list[int],
        timeout: int,
    ) -> MatchOutput: ...

    def kill_all(self) -> None: ...

    def kill_job(self, job: str) -> None: ...


def available_cpus() -> list[int]:
    if hasattr(os, "sched_getaffinity"):
        return sorted(os.sched_getaffinity(0))
    return list(range(os.cpu_count() or 1))


def physical_memory_mb() -> int:
    return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") >> 20


@dataclass(frozen=True)
class Allocation:
    cpus: list[int]
    memory_mb: int
    sandbox_slot: int  # picks the job's pair of bot uids


class Machine:
    """The worker's ledger of free CPUs, memory, and sandbox uid pairs."""

    def __init__(self, cpus: list[int], memory_mb: int) -> None:
        self.cpus, self.memory_mb = list(cpus), memory_mb
        self._free_cpus = list(cpus)
        self._free_memory_mb = memory_mb
        # At most one job per CPU, so one uid pair per CPU is enough.
        self._free_slots = list(range(len(cpus)))
        self._lock = threading.Lock()

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "total": {"cores": len(self.cpus), "memory_mb": self.memory_mb},
                "free": {"cores": len(self._free_cpus), "memory_mb": self._free_memory_mb},
            }

    def allocate(self, cores: int, memory_mb: int) -> Allocation:
        with self._lock:
            if cores > len(self._free_cpus) or memory_mb > self._free_memory_mb:
                raise RuntimeError("the server sent a job that does not fit")
            allocation = Allocation(
                cpus=self._free_cpus[:cores],
                memory_mb=memory_mb,
                sandbox_slot=self._free_slots.pop(0),
            )
            del self._free_cpus[:cores]
            self._free_memory_mb -= memory_mb
            return allocation

    def release(self, allocation: Allocation) -> None:
        with self._lock:
            self._free_cpus = sorted(self._free_cpus + allocation.cpus)
            self._free_memory_mb += allocation.memory_mb
            self._free_slots.append(allocation.sandbox_slot)

    def idle(self) -> bool:
        with self._lock:
            return not self._free_cpus


def describe_failure(output: MatchOutput, timeout: int) -> str:
    if output.timed_out:
        return f"The match did not finish within {timeout} seconds and was stopped."
    error = str(output.status.get("error") or "").strip()
    if error:
        return "The game engine crashed:\n" + error[-1500:]
    if output.oom_killed:
        return "The match ran out of memory before the engine could finish."
    return f"The engine exited (code {output.exit_code}) without reporting a result."


def outcome_of(output: MatchOutput, timeout: int) -> dict[str, Any]:
    if output.status.get("ok") and not output.timed_out:
        scores = parse_scores(output.files.get("game.log", b"").decode(errors="replace"))
        if scores is not None:
            return {"scores": list(scores)}
    return {"error": describe_failure(output, timeout)}


class Worker:
    def __init__(
        self,
        config: Config,
        sandbox: MatchRunner | None = None,
        machine: Machine | None = None,
        client: ServerClient | None = None,
    ) -> None:
        self.config = config
        if machine is None:
            cpus = available_cpus()
            if config.worker_cores:
                cpus = cpus[: config.worker_cores]
            machine = Machine(cpus, max(0, physical_memory_mb() - RESERVED_MEMORY_MB))
        self.machine = machine
        self.sandbox: MatchRunner = sandbox or Sandbox(
            config.game_image, max_parallel=len(machine.cpus)
        )
        self.client = client or ServerClient(
            config.server_url, config.worker_token, config.worker_name, config.commit
        )
        self.runs_dir = config.data_dir / "runs"
        self.cache = BotCache(config.data_dir / "cache" / "bots")
        self.stopping = threading.Event()
        self.exit_code = 0
        self._running: dict[str, set[int]] = {"games": set(), "builds": set()}
        self._cancelled: set[str] = set()  # job names the server took back
        self._jobs: list[threading.Thread] = []
        self._lock = threading.Lock()

    def status(self) -> dict[str, Any]:
        return self.machine.status()

    # -- lifecycle ----------------------------------------------------------

    def prepare(self) -> None:
        if isinstance(self.sandbox, Sandbox):
            self.sandbox.check_image()
            removed = self.sandbox.remove_stale_containers()
            if removed:
                log.info("Removed %d leftover containers", removed)
        shutil.rmtree(self.runs_dir, ignore_errors=True)
        self.runs_dir.mkdir(parents=True, mode=0o700)
        self.client.start(self.status())

    def run(self, install_signal_handlers: bool = True) -> int:
        if install_signal_handlers:
            for sig in (signal.SIGTERM, signal.SIGINT):
                signal.signal(sig, lambda *_: self.stop())
        try:
            self.prepare()
        except Outdated:
            log.warning("The server runs newer code; this worker retires")
            return EXIT_OUTDATED
        m = self.machine
        log.info(
            "Worker %s: CPUs %s, %d MB; server %s",
            self.config.worker_name,
            m.cpus,
            m.memory_mb,
            self.config.server_url,
        )
        threads = [
            threading.Thread(target=self._dispatch_loop, name="dispatch", daemon=True),
            threading.Thread(target=self._heartbeat_loop, name="heartbeat", daemon=True),
        ]
        for thread in threads:
            thread.start()
        while not self.stopping.is_set():
            self.stopping.wait(1.0)
        for thread in threads + self._jobs:
            thread.join(timeout=60)
        try:
            self.client.stop()
        except (requests.RequestException, Outdated, LeaseLost):
            log.warning("Could not tell the server we stopped; its leases will expire instead")
        log.info("Worker stopped")
        return self.exit_code

    def stop(self, exit_code: int = 0) -> None:
        if self.stopping.is_set():
            return
        log.info("Stopping: killing running jobs; the server will requeue them")
        self.exit_code = exit_code
        self.stopping.set()
        try:
            self.sandbox.kill_all()
        except Exception:
            log.exception("Could not kill containers")

    # -- threads ------------------------------------------------------------

    def _dispatch_loop(self) -> None:
        while not self.stopping.is_set():
            if self.machine.idle():
                self.stopping.wait(1.0)
                continue
            try:
                jobs = self.client.claim(self.status())
            except Outdated:
                self.stop(EXIT_OUTDATED)
                return
            except (requests.RequestException, LeaseLost) as exc:
                log.warning("Claim failed: %s", exc)
                jobs = {}
            started = 0
            for kind, run in (("builds", self.build), ("games", self.play)):
                for job in jobs.get(kind, []):
                    self._start(kind, job, run)
                    started += 1
            if not started:
                self.stopping.wait(IDLE_POLL_SECONDS * random.uniform(0.7, 1.3))  # noqa: S311

    def _start(
        self, kind: str, job: dict[str, Any], run: Callable[[dict[str, Any], Allocation], None]
    ) -> None:
        params = job["params"]
        allocation = self.machine.allocate(int(params["CORES"]), container_memory_mb(params))
        with self._lock:
            self._running[kind].add(job["id"])
        thread = threading.Thread(
            target=self._run_job,
            args=(kind, job, allocation, run),
            name=f"{kind[:-1]}-{job['id']}",
        )
        self._jobs = [t for t in self._jobs if t.is_alive()] + [thread]
        thread.start()

    def _run_job(
        self,
        kind: str,
        job: dict[str, Any],
        allocation: Allocation,
        run: Callable[[dict[str, Any], Allocation], None],
    ) -> None:
        try:
            run(job, allocation)
        except Exception as exc:
            log.exception("%s %d failed with an internal error", kind[:-1], job["id"])
            error = f"Internal error on the worker: {exc!r}"
            if kind == "games":
                self._report(lambda: self.client.submit(job["id"], {"error": error}, {}))
            else:
                self._report(
                    lambda: self.client.submit_build(
                        job["id"], {"ok": False, "error": error}, gzip.compress(b""), None
                    )
                )
        finally:
            with self._lock:
                self._running[kind].discard(job["id"])
                self._cancelled.discard(f"{kind[:-1]}-{job['id']}")
            self.machine.release(allocation)

    def _heartbeat_loop(self) -> None:
        while not self.stopping.wait(HEARTBEAT_SECONDS):
            with self._lock:
                games, builds = list(self._running["games"]), list(self._running["builds"])
            try:
                reply = self.client.heartbeat(self.status(), games, builds)
            except Outdated:
                self.stop(EXIT_OUTDATED)
                return
            except (requests.RequestException, LeaseLost) as exc:
                log.warning("Heartbeat failed: %s", exc)
                continue
            for prefix, ids in (
                ("game", reply.get("cancel", [])),
                ("build", reply.get("cancel_builds", [])),
            ):
                for job_id in ids:
                    log.warning("Server took back %s %d; stopping it", prefix, job_id)
                    with self._lock:
                        self._cancelled.add(f"{prefix}-{job_id}")
                    self.sandbox.kill_job(f"{prefix}-{job_id}")

    def _gone(self, job: str) -> bool:
        with self._lock:
            return self.stopping.is_set() or job in self._cancelled

    # -- builds ---------------------------------------------------------------

    def build(self, job: dict[str, Any], allocation: Allocation) -> None:
        """Build one bot from source and upload the result."""
        build_id: int = job["id"]
        bot = job["bot"]
        uid, _ = sandbox_module.bot_uids(allocation.sandbox_slot)
        params = {**job["params"], "BOT_UID_A": uid}
        run_dir = self.runs_dir / f"build-{build_id}-{secrets.token_hex(4)}"
        source = run_dir / "source"
        try:
            run_dir.mkdir(parents=True, mode=0o700)
            archive = run_dir / "source.zip"
            self.client.download_source(bot["id"], bot["sha256"], archive)
            extract(archive, bot["root"], source)
            archive.unlink()
            log.info("Build %d (bot %d) on CPUs %s", build_id, bot["id"], allocation.cpus)
            output = self.sandbox.run(
                job=f"build-{build_id}",
                run_dir=run_dir / "container",
                bot_a=source,
                bot_b=None,
                params=params,
                cpus=allocation.cpus,
                timeout=job["timeout"],
            )
            if self._gone(f"build-{build_id}"):
                return
            ok = bool(output.status.get("ok")) and output.archive is not None
            error = output.status.get("error")
            if output.timed_out:
                ok, error = False, "build timed out"
            outcome = {"ok": ok, "error": error, "seconds": output.status.get("seconds", 0)}
            log.info("Build %d: %s", build_id, "ready" if ok else f"failed ({error})")
            log_bytes = gzip.compress(output.files.get("build.log", b""), compresslevel=6)
            self._report(
                lambda: self.client.submit_build(
                    build_id, outcome, log_bytes, output.archive if ok else None
                )
            )
        finally:
            shutil.rmtree(run_dir, ignore_errors=True)

    # -- games ------------------------------------------------------------------

    def play(self, game: dict[str, Any], allocation: Allocation) -> None:
        game_id: int = game["id"]
        uid_a, uid_b = sandbox_module.bot_uids(allocation.sandbox_slot)
        params = {**game["params"], "BOT_UID_A": uid_a, "BOT_UID_B": uid_b}
        run_dir = self.runs_dir / f"game-{game_id}-{secrets.token_hex(4)}"
        acquired: list[int] = []
        started = time.monotonic()
        try:
            paths = []
            for side in ("a", "b"):
                bot = game["bots"][side]

                def fetch(dest: Path, bot: dict[str, Any] = bot) -> None:
                    self.client.download_build(bot["id"], bot["sha256"], dest)

                paths.append(self.cache.acquire(bot["id"], fetch))
                acquired.append(bot["id"])
            log.info("Game %d on CPUs %s", game_id, allocation.cpus)
            output = self.sandbox.run(
                job=f"game-{game_id}",
                run_dir=run_dir,
                bot_a=paths[0],
                bot_b=paths[1],
                params=params,
                cpus=allocation.cpus,
                timeout=game["timeout"],
            )
        finally:
            for bot_id in acquired:
                self.cache.release(bot_id)
            shutil.rmtree(run_dir, ignore_errors=True)

        if self._gone(f"game-{game_id}"):
            return  # the server requeues it
        logs = {
            kind: gzip.compress(output.files[name], compresslevel=6)
            for kind, name in LOG_FILES.items()
            if name in output.files
        }
        outcome = outcome_of(output, game["timeout"])
        outcome["stats"] = {
            **(output.status.get("stats") or {}),
            "seconds": round(time.monotonic() - started, 1),
            "worker": self.config.worker_name,
            "machine": self.config.machine_type or platform.machine(),
            "cpus": allocation.cpus,
            "oom_killed": output.oom_killed,
        }
        log.info(
            "Game %d finished in %.0fs: %s",
            game_id,
            time.monotonic() - started,
            outcome.get("scores") or "error",
        )
        self._report(lambda: self.client.submit(game_id, outcome, logs))

    def _report(self, send: Callable[[], None]) -> None:
        """Send a result, retrying through server restarts."""
        if self.stopping.is_set():
            return
        deadline = time.monotonic() + SUBMIT_RETRY_SECONDS
        delay = 2.0
        while True:
            try:
                send()
                return
            except LeaseLost:
                log.warning("A job was reassigned before its result arrived")
                return
            except Outdated:
                self.stop(EXIT_OUTDATED)
                return
            except requests.RequestException as exc:
                if time.monotonic() > deadline or self.stopping.is_set():
                    log.error("Giving up on reporting a result: %s", exc)
                    return
                log.warning("Reporting a result failed (%s); retrying", exc)
                time.sleep(delay)
                delay = min(delay * 2, 30)


def main(config: Config) -> int:
    return Worker(config).run()
