"""
SQLite access.

One database file in WAL mode serves the web processes and the worker on the
same host. Connections run in autocommit mode; every multi-statement write
goes through ``transaction()``, which takes the write lock up front
(BEGIN IMMEDIATE) so concurrent writers queue on busy_timeout instead of
failing with SQLITE_BUSY when upgrading a read lock.
"""

from __future__ import annotations

import fcntl
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from importlib import resources
from pathlib import Path
from typing import Any

Row = sqlite3.Row


def now() -> int:
    return int(time.time())


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, isolation_level=None, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA temp_store = MEMORY")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def one(conn: sqlite3.Connection, sql: str, params: Any = ()) -> Row | None:
    row: Row | None = conn.execute(sql, params).fetchone()
    return row


def all_rows(conn: sqlite3.Connection, sql: str, params: Any = ()) -> list[Row]:
    return conn.execute(sql, params).fetchall()


def scalar(conn: sqlite3.Connection, sql: str, params: Any = ()) -> Any:
    row = conn.execute(sql, params).fetchone()
    return None if row is None else row[0]


def _migrations() -> list[tuple[int, str]]:
    files = sorted(
        (f for f in resources.files("scrimmage.schema").iterdir() if f.name.endswith(".sql")),
        key=lambda f: f.name,
    )
    return [(int(f.name.split("_", 1)[0]), f.read_text()) for f in files]


def migrate(path: Path) -> int:
    """Apply pending schema migrations. Returns the resulting schema version.

    Safe to call from several processes at once (the web and worker services
    both migrate on start): a file lock serializes them.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_name(path.name + ".migrate-lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        conn = connect(path)
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            version: int = conn.execute("PRAGMA user_version").fetchone()[0]
            for number, sql in _migrations():
                if number <= version:
                    continue
                # executescript commits implicitly, so the version bump rides in
                # the same script to keep each migration atomic.
                conn.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version = {number};\nCOMMIT;")
                version = number
            return version
        finally:
            conn.close()
