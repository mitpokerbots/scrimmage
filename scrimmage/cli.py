"""
Command-line entry point: ``scrimmage <command>``.

Reads the same environment as the services (DATA_DIR, AUTH_MODE, ...). On the
server, /usr/local/bin/scrimmage wraps this with /etc/scrimmage/env loaded and
runs it as the scrimmage user.
"""

from __future__ import annotations

import argparse
import logging
import re
import shutil
import sqlite3
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from scrimmage import db
from scrimmage import settings as settings_module
from scrimmage.config import Config, load_config
from scrimmage.services import accounts, builds, fleet, queue, tournaments
from scrimmage.services import storage as storage_module
from scrimmage.services.errors import UserError
from scrimmage.services.storage import Storage
from scrimmage.settings import Settings

log = logging.getLogger("scrimmage")


def _migrate(_args: argparse.Namespace) -> None:
    config = load_config()
    version = db.migrate(config.db_path)
    print(f"Database {config.db_path} is at schema version {version}.")


def _worker(_args: argparse.Namespace) -> None:
    from scrimmage.worker.main import main  # noqa: PLC0415 -- needs the docker package

    sys.exit(main(load_config()))


def maintain_once(config: Config) -> None:
    """The main server's periodic jobs (systemd runs this every minute)."""
    db.migrate(config.db_path)
    conn = db.connect(config.db_path)
    try:
        requeued = queue.requeue_expired(conn)
        if requeued:
            log.warning("Requeued %d games whose worker stopped responding", requeued)
        requeued = builds.requeue_expired(conn)
        if requeued:
            log.warning("Requeued %d builds whose worker stopped responding", requeued)
        tournaments.update_ratings(conn)
        conn.execute("DELETE FROM worker_status WHERE updated_at < ?", (db.now() - 86400,))
        try:
            fleet.reconcile(conn, fleet.from_config(config.fleet_group, config.aws_region))
        except Exception:
            log.exception("Could not update the worker fleet")
        check_disk(conn, config)
    finally:
        conn.close()


DISK_ALERT_EVERY_SECONDS = 86400


def check_disk(conn: sqlite3.Connection, config: Config, sns: Any = None) -> None:
    """Warn the admins (once a day) when the data volume is filling up."""
    free, total = Storage(config.data_dir).disk()
    if free >= storage_module.WARN_FREE_BYTES:
        return
    log.warning("The data volume has only %.1f GB free", free / 1e9)
    last = Settings(conn).number("disk_alert_at")
    if not config.alert_topic or db.now() - last < DISK_ALERT_EVERY_SECONDS:
        return
    if sns is None:
        import boto3  # noqa: PLC0415 -- only needed on AWS

        sns = boto3.client("sns", region_name=config.aws_region or None)
    try:
        sns.publish(
            TopicArn=config.alert_topic,
            Subject="Scrimmage server: disk filling up",
            Message=(
                f"The scrimmage server's data volume has {free / 1e9:.1f} GB free of "
                f"{total / 1e9:.0f} GB. Below {storage_module.MIN_FREE_BYTES / 1e9:.0f} GB it "
                "stops accepting new bots. Increase DataVolumeSize in the CloudFormation stack "
                "(no downtime), or delete old data."
            ),
        )
    except Exception:
        log.exception("Could not send the disk space alert")
        return
    settings_module.update(conn, "disk_alert_at", str(db.now()))


def _maintain(_args: argparse.Namespace) -> None:
    maintain_once(load_config())


def _dev(args: argparse.Namespace) -> None:
    """Web server, worker, and maintenance in one process, for local development."""
    from scrimmage.web import create_app  # noqa: PLC0415
    from scrimmage.worker.main import Worker  # noqa: PLC0415

    config = replace(load_config(), server_url=f"http://{args.host}:{args.port}")
    app = create_app(config)
    worker = Worker(config)

    def background() -> None:
        while True:
            try:
                maintain_once(config)
            except Exception:
                log.exception("Maintenance failed")
            time.sleep(15)

    threading.Thread(target=background, daemon=True).start()
    threading.Thread(
        target=worker.run, kwargs={"install_signal_handlers": False}, daemon=True
    ).start()
    try:
        app.run(host=args.host, port=args.port, use_reloader=False)
    finally:
        worker.stop()


def _login_link(args: argparse.Namespace) -> None:
    config = load_config()
    db.migrate(config.db_path)
    conn = db.connect(config.db_path)
    token = accounts.mint_login_token(conn, args.kerberos)
    print(f"{config.public_url}/auth/link/{token}")
    print("Valid for 15 minutes, once.", file=sys.stderr)


def _admin(args: argparse.Namespace) -> None:
    config = load_config()
    db.migrate(config.db_path)
    conn = db.connect(config.db_path)
    if args.action == "list":
        for row in conn.execute("SELECT kerberos FROM users WHERE is_admin ORDER BY kerberos"):
            print(row[0])
        return
    if not args.kerberos:
        raise UserError(f"usage: scrimmage admin {args.action} KERBEROS")
    kerberos = accounts.normalize_kerberos(args.kerberos)
    if args.action == "add":
        conn.execute(
            "INSERT INTO users (kerberos, is_admin, created_at) VALUES (?, 1, ?) "
            "ON CONFLICT (kerberos) DO UPDATE SET is_admin = 1",
            (kerberos, db.now()),
        )
    else:
        conn.execute("UPDATE users SET is_admin = 0 WHERE kerberos = ?", (kerberos,))
    print(f"{kerberos}: admin {'granted' if args.action == 'add' else 'revoked'}.")


def _backup(args: argparse.Namespace) -> None:
    """Consistent copy of the live database (safe while services run)."""
    config = load_config()
    dest = Path(args.dest)
    source = db.connect(config.db_path)
    target = sqlite3.connect(dest)
    with target:
        source.backup(target)
    target.close()
    print(f"Wrote {dest} ({dest.stat().st_size} bytes).")


def _archive(_args: argparse.Namespace) -> None:
    from scrimmage import archive  # noqa: PLC0415

    sys.exit(archive.main(load_config()))


def _bake_images(_args: argparse.Namespace) -> None:
    from scrimmage import bake  # noqa: PLC0415

    config = load_config()
    sys.exit(bake.main(config.commit, config.aws_region))


def _export_bots(args: argparse.Namespace) -> None:
    """Copy every active team's current bot to DEST/<team name>.zip."""
    config = load_config()
    storage = Storage(config.data_dir)
    conn = db.connect(config.db_path)
    dest = Path(args.dest)
    dest.mkdir(parents=True, exist_ok=True)
    rows = conn.execute(
        "SELECT name, current_bot_id FROM teams WHERE NOT is_disabled "
        "AND current_bot_id IS NOT NULL ORDER BY name"
    ).fetchall()
    for row in rows:
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", row["name"]).strip("_") or "team"
        shutil.copyfile(storage.bot_zip(row["current_bot_id"]), dest / f"{safe}.zip")
    print(f"Exported {len(rows)} bots to {dest}.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scrimmage", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("migrate", help="create or upgrade the database").set_defaults(func=_migrate)
    sub.add_parser("worker", help="run the match worker").set_defaults(func=_worker)

    sub.add_parser(
        "maintain", help="requeue lost games, update tournament ratings, size the fleet"
    ).set_defaults(func=_maintain)

    dev = sub.add_parser("dev", help="web server + worker + maintenance in one process")
    dev.add_argument("--host", default="127.0.0.1")
    dev.add_argument("--port", type=int, default=8000)
    dev.set_defaults(func=_dev)

    link = sub.add_parser("login-link", help="print a one-time login URL for a kerberos")
    link.add_argument("kerberos")
    link.set_defaults(func=_login_link)

    admin = sub.add_parser("admin", help="list admins, or grant or revoke admin access")
    admin.add_argument("action", choices=["list", "add", "remove"])
    admin.add_argument("kerberos", nargs="?")
    admin.set_defaults(func=_admin)

    backup = sub.add_parser("backup", help="write a consistent copy of the database")
    backup.add_argument("dest")
    backup.set_defaults(func=_backup)

    sub.add_parser(
        "archive", help="copy the database, bots, and logs to the S3 archive (nightly)"
    ).set_defaults(func=_archive)
    sub.add_parser(
        "bake-images", help="pre-build fleet worker machine images for this commit"
    ).set_defaults(func=_bake_images)

    export = sub.add_parser("export-bots", help="copy every team's current bot into a directory")
    export.add_argument("dest")
    export.set_defaults(func=_export_bots)
    return parser


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except UserError as exc:
        sys.exit(f"error: {exc}")


if __name__ == "__main__":
    main()
