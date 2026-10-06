"""
The API match workers use: claim builds and games, fetch bots, renew leases,
report results.

Only reachable on the internal port (Apache denies /api/worker on the public
site; see deploy/apache-scrimmage.conf), and every call but /version needs the
shared worker token. Workers send their commit; a worker running other code
gets 409 and retires itself, and the fleet replaces it with an up-to-date one.

A worker can only download what it is working on: the source of a bot whose
build it holds, and the built bots of games it holds.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from typing import Any

from flask import Blueprint, abort, g, jsonify, request, send_file
from werkzeug.wrappers.response import Response

from scrimmage.db import now, one
from scrimmage.services import builds, queue
from scrimmage.services.storage import LOG_KINDS, MIN_FREE_BYTES
from scrimmage.settings import Settings
from scrimmage.web import common

bp = Blueprint("worker_api", __name__, url_prefix="/api/worker")

WORKER_NAME = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
GZIP_MAGIC = b"\x1f\x8b"


def _error(status: int, message: str, **extra: Any) -> Response:
    response = jsonify(error=message, **extra)
    response.status_code = status
    return response


@bp.before_request
def authenticate() -> Response | None:
    if request.endpoint == "worker_api.version":
        return None
    config = common.state().config
    sent = request.headers.get("Authorization", "").removeprefix("Bearer ")
    if not hmac.compare_digest(sent.encode(), config.worker_token.encode()):
        return _error(401, "bad worker token")
    if request.headers.get("X-Scrimmage-Commit") != config.commit:
        return _error(409, "outdated", commit=config.commit)
    worker = request.headers.get("X-Worker", "")
    if not WORKER_NAME.match(worker):
        return _error(400, "bad worker name")
    g.worker = worker
    return None


def _body() -> dict[str, Any]:
    body = request.get_json(silent=True)
    return body if isinstance(body, dict) else {}


def _int_list(value: object) -> list[int]:
    if not isinstance(value, list) or not all(isinstance(v, int) for v in value):
        abort(400)
    return value


def _resources(value: object) -> queue.Resources:
    if not isinstance(value, dict):
        abort(400)
    try:
        return queue.Resources(cores=int(value["cores"]), memory_mb=int(value["memory_mb"]))
    except (KeyError, TypeError, ValueError):
        abort(400)


def _report(body: dict[str, Any], jobs: int) -> None:
    """Record what the worker has, for the dashboard and fleet sizing."""
    total = _resources(body.get("total"))
    free = _resources(body.get("free", body.get("total")))
    common.conn().execute(
        "INSERT INTO worker_status (name, cores, busy_cores, games, commit_id, "
        "started_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (name) DO UPDATE SET cores = excluded.cores, "
        "busy_cores = excluded.busy_cores, games = excluded.games, "
        "commit_id = excluded.commit_id, updated_at = excluded.updated_at",
        (
            g.worker,
            total.cores,
            total.cores - free.cores,
            jobs,
            common.state().config.commit,
            now(),
            now(),
        ),
    )


@bp.get("/version")
def version() -> Response:
    return jsonify(commit=common.state().config.commit)


@bp.post("/start")
def start() -> Response:
    """A worker (re)started: anything it held before is requeued."""
    conn = common.conn()
    requeued = queue.release(conn, g.worker) + builds.release(conn, g.worker)
    conn.execute("DELETE FROM worker_status WHERE name = ?", (g.worker,))
    _report(_body(), 0)
    return jsonify(requeued=requeued)


@bp.post("/stop")
def stop() -> Response:
    """A worker is shutting down cleanly: requeue what it held."""
    conn = common.conn()
    requeued = queue.release(conn, g.worker) + builds.release(conn, g.worker)
    conn.execute(
        "UPDATE worker_status SET cores = 0, busy_cores = 0, games = 0 WHERE name = ?",
        (g.worker,),
    )
    return jsonify(requeued=requeued)


@bp.post("/heartbeat")
def heartbeat() -> Response:
    body = _body()
    games = _int_list(body.get("games", []))
    build_ids = _int_list(body.get("builds", []))
    _report(body, len(games) + len(build_ids))
    conn = common.conn()
    return jsonify(
        cancel=queue.renew_leases(conn, g.worker, games),
        cancel_builds=builds.renew_leases(conn, g.worker, build_ids),
    )


@bp.post("/claim")
def claim() -> Response:
    """Builds first (games wait for them), then games, within the worker's free resources."""
    body = _body()
    conn = common.conn()
    settings = Settings(conn)
    free, total = _resources(body.get("free")), _resources(body.get("total"))
    cores = builds.build_cores(settings, total.cores)
    build_rows = builds.claim(conn, g.worker, free.cores, free.memory_mb, cores)
    build_jobs = []
    for row in build_rows:
        bot = one(conn, "SELECT * FROM bots WHERE id = ?", (row["bot_id"],))
        assert bot is not None
        params = builds.build_parameters(settings, bot, cores)
        build_jobs.append(
            {
                "id": row["id"],
                "bot": {"id": bot["id"], "root": bot["root"], "sha256": bot["sha256"]},
                "params": params,
                "timeout": int(float(params["BUILD_TIMEOUT"])) + 120,
            }
        )
        free = queue.Resources(
            free.cores - int(params["CORES"]), free.memory_mb - builds.BUILD_MEMORY_MB
        )

    rows = queue.claim(conn, g.worker, free, total)
    games = []
    for row in rows:
        bots = {}
        for side in ("a", "b"):
            build = builds.get(conn, row[f"bot_{side}_id"])
            assert build is not None
            bots[side] = {"id": row[f"bot_{side}_id"], "sha256": build["sha256"]}
        params = queue.match_parameters(settings, row)
        games.append(
            {
                "id": row["id"],
                "bots": bots,
                "params": params,
                "timeout": queue.hard_timeout_seconds(params),
            }
        )
    return jsonify(builds=build_jobs, games=games)


@bp.get("/bots/<int:bot_id>/source")
def bot_source(bot_id: int) -> Response:
    held = one(
        common.conn(),
        "SELECT 1 FROM builds WHERE bot_id = ? AND worker = ? AND status = 'running'",
        (bot_id, g.worker),
    )
    path = common.state().storage.bot_zip(bot_id)
    if held is None or not path.exists():
        abort(404)
    return send_file(path, mimetype="application/zip")


@bp.get("/bots/<int:bot_id>/build")
def bot_build(bot_id: int) -> Response:
    held = one(
        common.conn(),
        "SELECT 1 FROM games WHERE status = 'running' AND worker = ? "
        "AND (bot_a_id = ? OR bot_b_id = ?) LIMIT 1",
        (g.worker, bot_id, bot_id),
    )
    if held is None:
        abort(404)
    path = common.state().storage.build_archive(bot_id)
    if not path.exists():
        abort(404)
    return send_file(path, mimetype="application/gzip")


@bp.post("/builds/<int:build_id>/result")
def build_result(build_id: int) -> Response:
    conn = common.conn()
    try:
        outcome = json.loads(request.form.get("outcome", ""))
    except json.JSONDecodeError:
        abort(400)
    build = builds.holds(conn, g.worker, build_id)
    if build is None:
        return _error(409, "lease lost")
    storage = common.state().storage
    log = request.files.get("log")
    if log is not None:
        data = log.read()
        if not data.startswith(GZIP_MAGIC):
            abort(400)
        storage.build_log(build["bot_id"]).write_bytes(data)
    ok = bool(outcome.get("ok"))
    error = str(outcome.get("error") or "") or None
    size = digest = None
    archive = request.files.get("bot")
    if ok and storage.disk()[0] < MIN_FREE_BYTES:
        ok, error = False, "the server's storage is nearly full; upload again later"
    if ok:
        if archive is None:
            abort(400)
        path = storage.build_archive(build["bot_id"])
        tmp = path.with_suffix(".tmp")
        archive.save(tmp)
        with open(tmp, "rb") as f:
            magic = f.read(2)
        if magic != GZIP_MAGIC:
            tmp.unlink()
            abort(400)
        sha = hashlib.sha256()
        with open(tmp, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                sha.update(chunk)
        size, digest = tmp.stat().st_size, sha.hexdigest()
        tmp.replace(path)
    recorded = builds.record(
        conn,
        build_id,
        g.worker,
        ok=ok,
        error=error,
        seconds=float(outcome.get("seconds") or 0),
        size_bytes=size,
        sha256=digest,
    )
    if not recorded:
        return _error(409, "lease lost")
    return jsonify(ok=True)


@bp.post("/games/<int:game_id>/result")
def result(game_id: int) -> Response:
    conn = common.conn()
    try:
        outcome = json.loads(request.form.get("outcome", ""))
    except json.JSONDecodeError:
        abort(400)
    held = one(
        conn,
        "SELECT 1 FROM games WHERE id = ? AND status = 'running' AND worker = ?",
        (game_id, g.worker),
    )
    if held is None:
        return _error(409, "lease lost")
    storage = common.state().storage
    for kind in LOG_KINDS:
        upload = request.files.get(kind)
        if upload is not None:
            data = upload.read()
            if not data.startswith(GZIP_MAGIC):
                abort(400)
            storage.write_log(game_id, kind, data)
    stats = outcome.get("stats")
    try:
        scores = outcome.get("scores")
        if (
            isinstance(scores, list)
            and len(scores) == 2
            and all(isinstance(x, int) for x in scores)
        ):
            try:
                queue.record_result(
                    conn,
                    Settings(conn),
                    game_id,
                    queue.Result(*scores),
                    worker=g.worker,
                    stats=stats if isinstance(stats, dict) else None,
                )
            except ValueError as exc:
                queue.record_error(conn, game_id, f"Invalid result: {exc}", worker=g.worker)
        else:
            message = str(outcome.get("error") or "The worker reported no result.")
            queue.record_error(conn, game_id, message, worker=g.worker)
    except queue.LeaseLost:
        return _error(409, "lease lost")
    return jsonify(ok=True)
