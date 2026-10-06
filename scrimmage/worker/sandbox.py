"""
Run one match or build in a locked-down container (see game/run_match.py for
what happens inside).

Container limits:
  * no network, no shared IPC namespace; read-only root filesystem; tmpfs for
    /game, each bot's home (/game/bot-<id>), and a shared /tmp and /dev/shm
  * all capabilities dropped except the few the in-container runner needs to
    switch to the per-bot uids; no-new-privileges
  * memory hard limit with no swap, pid limit, the game's own pinned CPUs
  * a wall-clock limit after which the container is killed

Inside, the runner holds each bot to its memory share (BOT_MEMORY_BYTES from
the game's hardware) and BOT_NPROC processes, so the container limits are only
reached by the engine misbehaving, never by one bot starving the other.
"""

from __future__ import annotations

import contextlib
import json
import logging
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import docker
import docker.errors
import requests.exceptions
from docker.types import Ulimit

from scrimmage.services.hardware import container_memory_mb

log = logging.getLogger(__name__)

LABEL = "scrimmage.game"


def tmpfs_mounts(params: dict[str, Any]) -> dict[str, str]:
    # A bot's home counts against its memory share, so it can be that big.
    bot_memory = int(params["BOT_MEMORY_BYTES"])
    home = f"size={bot_memory},mode=0700,exec"
    # Shared scratch space (Python's multiprocessing needs /dev/shm). The runner
    # counts each bot's files here toward its memory, so these only need room
    # for both bots' shares.
    shared = f"size={2 * bot_memory},mode=1777,nosuid,nodev"
    mounts = {
        # Root-only parent of every bind mount, so bots cannot reach the inputs
        # or outputs whatever the host-side permissions are.
        "/match": "size=1m,mode=0700",
        "/game": "size=64m,mode=0711,exec",
        "/tmp": shared + ",exec",
        "/dev/shm": shared,
    }
    for key in ("BOT_DIR_A", "BOT_DIR_B"):
        if key in params:
            mounts[f"/game/{params[key]}"] = home
    return mounts


# For the runner (root) only: bots run under their own uids, which drops every
# capability. SYS_PTRACE lets the runner read bots' memory maps (exact memory
# accounting for bots that fork).
CAP_ADD = ["CHOWN", "DAC_OVERRIDE", "FOWNER", "SETUID", "SETGID", "KILL", "SYS_PTRACE"]
BOT_NPROC = 256  # must match game/run_match.py
PIDS_LIMIT = 2 * BOT_NPROC + 128
OUTPUT_FILES = ("status.json", "game.log", "a.log", "b.log", "engine.log", "build.log")
BUILD_ARCHIVE = "bot.tar.gz"  # written by a build; left on disk for the worker to upload
MAX_OUTPUT_BYTES = 64 << 20
# Bots run as uid pairs starting here, one pair per worker slot.
BOT_UID_BASE = 20000


def bot_uids(slot: int) -> tuple[int, int]:
    return BOT_UID_BASE + 2 * slot, BOT_UID_BASE + 2 * slot + 1


@dataclass(frozen=True)
class MatchOutput:
    status: dict[str, Any]
    exit_code: int | None
    timed_out: bool
    oom_killed: bool
    files: dict[str, bytes]
    archive: Path | None = None  # a build's packed bot, inside the run directory


class Sandbox:
    def __init__(self, image: str, max_parallel: int = 8) -> None:
        # One pooled HTTP connection per concurrent container.wait(), plus spare.
        self.client = docker.from_env(timeout=120, max_pool_size=max_parallel + 4)
        self.image = image

    def kill_all(self) -> None:
        """Kill every running match container (used on shutdown)."""
        self._kill(LABEL)

    def kill_job(self, job: str) -> None:
        self._kill(f"{LABEL}={job}")

    def _kill(self, label: str) -> None:
        for container in self.client.containers.list(filters={"label": label}):
            try:
                container.kill()
            except docker.errors.APIError:
                log.warning("Could not kill container %s", container.name)

    def check_image(self) -> None:
        try:
            self.client.images.get(self.image)
        except docker.errors.ImageNotFound:
            raise RuntimeError(
                f"Game image {self.image!r} not found. Build it with: "
                "docker build -t scrimmage-game:latest game/"
            ) from None

    def remove_stale_containers(self) -> int:
        stale = self.client.containers.list(all=True, filters={"label": LABEL})
        for container in stale:
            container.remove(force=True)
        return len(stale)

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
    ) -> MatchOutput:
        """Run a game (two bots) or a build (``bot_b`` None, MODE=build)."""
        run_dir.mkdir(parents=True, mode=0o700)
        output_dir = run_dir / "output"
        output_dir.mkdir(mode=0o700)
        (run_dir / "match.json").write_text(json.dumps(params))
        memory = container_memory_mb(params) << 20
        inputs = {str(bot_a): {"bind": "/match/a", "mode": "ro"}}
        if bot_b is not None:
            inputs[str(bot_b)] = {"bind": "/match/b", "mode": "ro"}

        container = self.client.containers.run(
            self.image,
            detach=True,
            name=f"scrimmage-{job}-{secrets.token_hex(3)}",
            labels={LABEL: job},
            network_mode="none",
            # A private IPC namespace; /dev/shm is the tmpfs above.
            ipc_mode="none",
            read_only=True,
            tmpfs=tmpfs_mounts(params),
            volumes={
                **inputs,
                str(run_dir / "match.json"): {"bind": "/match/match.json", "mode": "ro"},
                str(output_dir): {"bind": "/match/output", "mode": "rw"},
            },
            cap_drop=["ALL"],
            cap_add=CAP_ADD,
            security_opt=["no-new-privileges"],
            mem_limit=memory,
            memswap_limit=memory,
            cpuset_cpus=",".join(str(c) for c in cpus),
            pids_limit=PIDS_LIMIT,
            ulimits=[
                Ulimit(name="nofile", soft=4096, hard=4096),
                Ulimit(name="core", soft=0, hard=0),
            ],
            init=True,
        )
        timed_out = False
        exit_code: int | None = None
        try:
            try:
                exit_code = container.wait(timeout=timeout)["StatusCode"]
            except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectionError):
                timed_out = True
                with contextlib.suppress(docker.errors.APIError):
                    container.kill()
            container.reload()
            oom_killed = bool(container.attrs["State"].get("OOMKilled"))
        finally:
            container.remove(force=True)

        files = {}
        for name in OUTPUT_FILES:
            path = output_dir / name
            if path.is_file():
                with open(path, "rb") as f:
                    files[name] = f.read(MAX_OUTPUT_BYTES)
        try:
            status = json.loads(files.get("status.json", b"{}"))
        except json.JSONDecodeError:
            status = {}
        archive = output_dir / BUILD_ARCHIVE
        return MatchOutput(
            status=status,
            exit_code=exit_code,
            timed_out=timed_out,
            oom_killed=oom_killed,
            files=files,
            archive=archive if archive.is_file() else None,
        )
