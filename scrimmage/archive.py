"""
The off-site archive: ``scrimmage archive``, nightly (deploy/systemd).

Everything stays on the data volume for good: it is cheap (a game's logs are
about 100-300 KB gzipped). This copies it all to S3 as well, so the history
survives losing the server, its volume, and their snapshots:

    database/latest.sqlite3.gz         a consistent copy of the database
    database/daily/<date>.sqlite3.gz   one a day (the bucket moves them to
                                       Glacier Instant Retrieval after 30 days
                                       and deletes them after 400)
    files/bots/, files/builds/, files/logs/
                                       uploads, built bots, build and game
                                       logs, laid out as on the data volume

Files are uploaded once and again only if they change, tracked in
<data>/archive.sqlite3. To get everything back:

    aws s3 sync s3://BUCKET/files/ /srv/scrimmage/
    aws s3 cp s3://BUCKET/database/latest.sqlite3.gz - | gunzip > scrimmage.sqlite3
"""

from __future__ import annotations

import datetime
import gzip
import logging
import os
import shutil
import sqlite3
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from scrimmage import db
from scrimmage.config import Config

log = logging.getLogger(__name__)

ARCHIVED_DIRS = ("bots", "builds", "logs")
UPLOAD_THREADS = 16


def _files(data_dir: Path) -> Iterator[tuple[str, os.stat_result]]:
    for top in ARCHIVED_DIRS:
        for root, _dirs, names in os.walk(data_dir / top):
            for name in names:
                if name.endswith(".tmp"):
                    continue  # being written
                path = Path(root) / name
                yield str(path.relative_to(data_dir)), path.stat()


def _manifest(data_dir: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(data_dir / "archive.sqlite3", isolation_level=None)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS uploaded "
        "(path TEXT PRIMARY KEY, size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL)"
    )
    return conn


def copy_database(config: Config, dest: Path) -> None:
    """A gzipped, consistent copy of the live database."""
    plain = dest.with_suffix("")
    source = db.connect(config.db_path)
    target = sqlite3.connect(plain)
    try:
        with target:
            source.backup(target)
    finally:
        target.close()
        source.close()
    with open(plain, "rb") as fin, gzip.open(dest, "wb", compresslevel=6) as fout:
        shutil.copyfileobj(fin, fout, 1 << 20)
    plain.unlink()


def run(config: Config, s3: Any, bucket: str) -> dict[str, int]:
    data_dir = config.data_dir
    tmp = data_dir / "tmp" / "archive-db.sqlite3.gz"
    copy_database(config, tmp)
    try:
        s3.upload_file(str(tmp), bucket, "database/latest.sqlite3.gz")
        today = datetime.datetime.now(datetime.UTC).date().isoformat()
        s3.upload_file(str(tmp), bucket, f"database/daily/{today}.sqlite3.gz")
    finally:
        tmp.unlink(missing_ok=True)

    manifest = _manifest(data_dir)
    known = {
        path: (size, mtime) for path, size, mtime in manifest.execute("SELECT * FROM uploaded")
    }
    pending = [
        (path, stat.st_size, stat.st_mtime_ns)
        for path, stat in _files(data_dir)
        if known.get(path) != (stat.st_size, stat.st_mtime_ns)
    ]

    def upload(item: tuple[str, int, int]) -> tuple[str, int, int]:
        s3.upload_file(str(data_dir / item[0]), bucket, f"files/{item[0]}")
        return item

    uploaded = 0
    with ThreadPoolExecutor(UPLOAD_THREADS) as pool:
        for path, size, mtime in pool.map(upload, pending):
            manifest.execute(
                "INSERT OR REPLACE INTO uploaded VALUES (?, ?, ?)", (path, size, mtime)
            )
            uploaded += 1
    manifest.close()
    log.info("Archived the database and %d new or changed files to s3://%s", uploaded, bucket)
    return {"files": uploaded}


def main(config: Config) -> int:
    bucket = os.environ.get("ARCHIVE_BUCKET", "")
    if not bucket:
        log.info("ARCHIVE_BUCKET is not set; nothing to archive")
        return 0
    import boto3  # noqa: PLC0415 -- only needed on AWS

    run(config, boto3.client("s3", region_name=config.aws_region or None), bucket)
    return 0
