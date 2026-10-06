"""
Built-bot cache on the worker's disk.

Built bots are immutable, so each is downloaded and unpacked once and then
bind-mounted read-only into every game it plays. Least-recently-used entries
are evicted past a size cap.

The archives come from builds, i.e. from student code, so they are unpacked
with tarfile's "data" filter: no absolute paths, no links pointing outside the
bot, no device files, no setuid bits.
"""

from __future__ import annotations

import logging
import os
import shutil
import tarfile
import threading
from collections.abc import Callable
from pathlib import Path

log = logging.getLogger(__name__)


def _tree_size(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except FileNotFoundError:
                continue
    return total


MAX_CACHE_BYTES = 20 << 30


class BotCache:
    def __init__(self, root: Path, max_bytes: int = MAX_CACHE_BYTES) -> None:
        self.root = root
        self.max_bytes = max_bytes
        self._lock = threading.Lock()
        self._in_use: dict[int, int] = {}
        self._bot_locks: dict[int, threading.Lock] = {}
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True, mode=0o700)

    def acquire(self, bot_id: int, fetch: Callable[[Path], None]) -> Path:
        """Return the unpacked bot, calling ``fetch(archive_path)`` to download it if needed."""
        dest = self.root / str(bot_id)
        with self._lock:
            self._in_use[bot_id] = self._in_use.get(bot_id, 0) + 1
            bot_lock = self._bot_locks.setdefault(bot_id, threading.Lock())
        try:
            with bot_lock:  # other bots download in parallel
                if not dest.exists():
                    tmp = self.root / f".{bot_id}.tmp"
                    archive = self.root / f".{bot_id}.tar.gz"
                    shutil.rmtree(tmp, ignore_errors=True)
                    try:
                        fetch(archive)
                        tmp.mkdir()
                        with tarfile.open(archive) as tar:
                            tar.extractall(tmp, filter="data")
                    finally:
                        archive.unlink(missing_ok=True)
                    os.chmod(tmp, 0o700)
                    tmp.rename(dest)
                    with self._lock:
                        self._evict()
        except BaseException:
            self.release(bot_id)
            raise
        os.utime(dest)
        return dest

    def release(self, bot_id: int) -> None:
        with self._lock:
            self._release_locked(bot_id)

    def _release_locked(self, bot_id: int) -> None:
        self._in_use[bot_id] -= 1
        if self._in_use[bot_id] == 0:
            del self._in_use[bot_id]

    def _evict(self) -> None:
        entries = []
        for entry in self.root.iterdir():
            if entry.name.isdigit():
                entries.append((entry.stat().st_mtime, entry, _tree_size(entry)))
        total = sum(size for _, _, size in entries)
        for _mtime, entry, size in sorted(entries):
            if total <= self.max_bytes:
                break
            if int(entry.name) in self._in_use:
                continue
            log.info("Evicting cached bot %s (%d bytes)", entry.name, size)
            shutil.rmtree(entry, ignore_errors=True)
            total -= size
        if total > self.max_bytes:
            log.warning("Bot cache is %d bytes, above the %d cap", total, self.max_bytes)
