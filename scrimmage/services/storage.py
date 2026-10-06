"""
Files on the main server's data volume.

    <data>/bots/<bot_id>.zip              uploaded bots (immutable)
    <data>/builds/<bot_id>.*              built bots (.tar.gz) and build logs (.log.gz)
    <data>/logs/<id // 1000>/<id>/*.gz    per-game logs, gzipped by the worker
    <data>/tmp/                           in-flight uploads
"""

from __future__ import annotations

import shutil
from pathlib import Path

from scrimmage.services.errors import UserError

LOG_KINDS = ("game", "a", "b", "engine")
# Below this much free space, new uploads and builds are refused: the database
# must never run out of room (that would take the whole site down), and game
# logs keep coming.
MIN_FREE_BYTES = 5 << 30
# Below this, admins are warned (the Admin page, and an alert email).
WARN_FREE_BYTES = 15 << 30


class Storage:
    def __init__(self, data_dir: Path) -> None:
        self.bots_dir = data_dir / "bots"
        self.builds_dir = data_dir / "builds"
        self.logs_dir = data_dir / "logs"
        self.tmp_dir = data_dir / "tmp"
        for d in (self.bots_dir, self.builds_dir, self.logs_dir, self.tmp_dir):
            d.mkdir(parents=True, exist_ok=True)

    def bot_zip(self, bot_id: int) -> Path:
        return self.bots_dir / f"{bot_id}.zip"

    def build_archive(self, bot_id: int) -> Path:
        return self.builds_dir / f"{bot_id}.tar.gz"

    def build_log(self, bot_id: int) -> Path:
        return self.builds_dir / f"{bot_id}.log.gz"

    def log_dir(self, game_id: int) -> Path:
        return self.logs_dir / str(game_id // 1000) / str(game_id)

    def log_path(self, game_id: int, kind: str) -> Path:
        if kind not in LOG_KINDS:
            raise ValueError(f"unknown log kind {kind!r}")
        return self.log_dir(game_id) / f"{kind}.log.gz"

    def write_log(self, game_id: int, kind: str, gzipped: bytes) -> None:
        path = self.log_path(game_id, kind)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(gzipped)
        tmp.replace(path)

    def disk(self) -> tuple[int, int]:
        """(free bytes, total bytes) of the data volume."""
        usage = shutil.disk_usage(self.logs_dir)
        return usage.free, usage.total

    def require_space(self) -> None:
        if self.disk()[0] < MIN_FREE_BYTES:
            raise UserError(
                "The server's storage is nearly full, so new bots can't be accepted right "
                "now. The staff have been alerted; please try again later."
            )

    def delete_logs(self, game_id: int) -> None:
        shutil.rmtree(self.log_dir(game_id), ignore_errors=True)
