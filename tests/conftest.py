from __future__ import annotations

import gzip
import hashlib
import io
import os
import sqlite3
import tarfile
import tempfile
import threading
import zipfile
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest
from flask import Flask
from flask.testing import FlaskClient
from werkzeug.serving import make_server

from scrimmage import db
from scrimmage import settings as settings_module
from scrimmage.config import Config
from scrimmage.services import accounts, bots, builds, queue
from scrimmage.services.storage import Storage
from scrimmage.settings import Settings
from scrimmage.web import create_app

FIXTURE_BOTS = Path(__file__).parent / "fixtures" / "bots"


@pytest.fixture(autouse=True)
def site_open(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests see the site as open; test_offseason closes it explicitly."""
    opened = [
        replace(s, default="") if s.key == "site_opens_on" else s for s in settings_module.SETTINGS
    ]
    monkeypatch.setattr(settings_module, "SETTINGS", tuple(opened))
    monkeypatch.setattr(settings_module, "BY_KEY", {s.key: s for s in opened})


@pytest.fixture(autouse=True)
def roomy_disk(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests don't depend on how full the machine running them is."""
    monkeypatch.setattr(Storage, "disk", lambda self: (500 << 30, 1000 << 30))


@pytest.fixture
def config(tmp_path: Path) -> Config:
    return Config(
        data_dir=tmp_path / "data",
        public_url="http://localhost:8000",
        auth_mode="dev",
        bootstrap_admins=frozenset({"boss"}),
        contact_email="pokerbots@mit.edu",
        secret_key="test-secret",
        worker_token="test-token",
        commit="test-commit",
        fleet_group="",
        alert_topic="",
        aws_region="",
        server_url="http://127.0.0.1:1",
        game_image="scrimmage-game:latest",
        worker_name="test-worker",
        worker_cores=2,
        machine_type="test-machine",
    )


# A worker with plenty of everything, for claiming games directly in tests.
ROOMY = queue.Resources(cores=64, memory_mb=1 << 20)


def claim(
    conn: sqlite3.Connection, worker: str = "test-worker", games: int = 1
) -> list[sqlite3.Row]:
    """Claim up to ``games`` one-core CPU games, as a worker with that many free cores."""
    free = queue.Resources(cores=games, memory_mb=ROOMY.memory_mb)
    return queue.claim(conn, worker, free, ROOMY)


def claim_one(conn: sqlite3.Connection, worker: str = "test-worker") -> sqlite3.Row | None:
    rows = claim(conn, worker)
    return rows[0] if rows else None


@pytest.fixture
def live_server(app: Flask) -> Iterator[str]:
    """The app on a real port, for workers that speak HTTP to it."""
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


@pytest.fixture
def conn(config: Config) -> Iterator[sqlite3.Connection]:
    db.migrate(config.db_path)
    connection = db.connect(config.db_path)
    yield connection
    connection.close()


@pytest.fixture
def storage(config: Config) -> Storage:
    return Storage(config.data_dir)


def zip_dir(source: Path, prefix: str = "") -> bytes:
    """Zip a directory, optionally nesting it under ``prefix``/ as students often do."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _dirs, files in os.walk(source):
            for name in files:
                path = Path(root) / name
                arcname = Path(prefix) / path.relative_to(source)
                info = zipfile.ZipInfo(str(arcname))
                info.external_attr = (0o755 if os.access(path, os.X_OK) else 0o644) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                zf.writestr(info, path.read_bytes())
    return buffer.getvalue()


def make_zip(files: dict[str, str | bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buffer.getvalue()


@pytest.fixture
def python_bot_zip() -> bytes:
    return zip_dir(FIXTURE_BOTS / "python", prefix="my-bot")


def finish_builds(
    conn: sqlite3.Connection, storage: Storage, *, fail: frozenset[int] = frozenset()
) -> None:
    """Complete every queued build the way a worker would (bots in ``fail`` fail)."""
    for build in builds.claim(conn, "test-builder", 1 << 10, 1 << 30, 1):
        _finish_build(conn, storage, build, fail)


def _finish_build(
    conn: sqlite3.Connection, storage: Storage, build: sqlite3.Row, fail: frozenset[int]
) -> None:
    if build["bot_id"] in fail:
        builds.record(
            conn,
            build["id"],
            "test-builder",
            ok=False,
            error="exit 1",
            seconds=1.0,
            size_bytes=None,
            sha256=None,
        )
        return
    bot = conn.execute("SELECT * FROM bots WHERE id = ?", (build["bot_id"],)).fetchone()
    with tempfile.TemporaryDirectory() as tmp:
        bots.extract(storage.bot_zip(bot["id"]), bot["root"], Path(tmp) / "bot")
        archive = storage.build_archive(bot["id"])
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(Path(tmp) / "bot", arcname=".")
    data = archive.read_bytes()
    storage.build_log(bot["id"]).write_bytes(gzip.compress(b"built"))
    builds.record(
        conn,
        build["id"],
        "test-builder",
        ok=True,
        error=None,
        seconds=1.0,
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
    )


def add_team_with_bot(
    conn: sqlite3.Connection,
    storage: Storage,
    name: str,
    zip_bytes: bytes,
    tmp_path: Path,
    kerberos: str | None = None,
    *,
    build: bool = True,
) -> tuple[int, int]:
    """Create a team (with one member) and upload a bot, built unless ``build`` is False.

    Returns (team_id, bot_id)."""
    user_id = accounts.record_login(conn, kerberos or name.lower(), None, frozenset())
    team_id = accounts.create_team(conn, user_id, name)
    upload = tmp_path / f"{name}-{os.urandom(4).hex()}.zip"
    upload.write_bytes(zip_bytes)
    bot_id = bots.create(conn, storage, Settings(conn), team_id, user_id, "", upload)
    if build:
        finish_builds(conn, storage)
    return team_id, bot_id


@pytest.fixture
def app(config: Config) -> Flask:
    flask_app = create_app(config)
    flask_app.testing = True
    return flask_app


@pytest.fixture
def touchstone_app(config: Config) -> Flask:
    flask_app = create_app(replace(config, auth_mode="touchstone"))
    flask_app.testing = True
    return flask_app


class Client:
    """A test client that logs in through the dev login and sends CSRF tokens."""

    def __init__(self, flask_client: FlaskClient) -> None:
        self.http = flask_client

    def token(self) -> str:
        with self.http.session_transaction() as session:
            session.setdefault("csrf", "test-token")
            return str(session["csrf"])

    def finish_builds(self) -> None:
        state = self.http.application.extensions["scrimmage"]
        connection = db.connect(state.config.db_path)
        try:
            finish_builds(connection, state.storage)
        finally:
            connection.close()

    def login(self, kerberos: str) -> None:
        self.post("/auth/dev", {"kerberos": kerberos})

    def get(self, path: str, **kwargs: object):  # type: ignore[no-untyped-def]
        return self.http.get(path, **kwargs)  # type: ignore[arg-type]

    def post(self, path: str, data: dict[str, object] | None = None, **kwargs: object):  # type: ignore[no-untyped-def]
        payload = {"csrf_token": self.token(), **(data or {})}
        return self.http.post(path, data=payload, **kwargs)  # type: ignore[arg-type]


@pytest.fixture
def client(app: Flask) -> Client:
    return Client(app.test_client())


@pytest.fixture
def make_client(app: Flask):  # type: ignore[no-untyped-def]
    def factory(kerberos: str | None = None) -> Client:
        new = Client(app.test_client())
        if kerberos:
            new.login(kerberos)
        return new

    return factory


@pytest.fixture
def two_teams(
    conn: sqlite3.Connection, storage: Storage, tmp_path: Path, python_bot_zip: bytes
) -> tuple[int, int]:
    a, _ = add_team_with_bot(conn, storage, "Aces", python_bot_zip, tmp_path)
    b, _ = add_team_with_bot(conn, storage, "Kings", python_bot_zip, tmp_path)
    return a, b
