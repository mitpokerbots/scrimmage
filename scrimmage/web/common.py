"""Request-scoped state, access decorators, CSRF, and template helpers."""

from __future__ import annotations

import hmac
import secrets
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from functools import wraps
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from flask import abort, current_app, g, redirect, render_template, request, session, url_for
from werkzeug.wrappers.response import Response

from scrimmage import db
from scrimmage.config import Config
from scrimmage.services import accounts
from scrimmage.services.fleet import Fleet
from scrimmage.services.storage import Storage
from scrimmage.settings import Settings

TIMEZONE = ZoneInfo("America/New_York")
PAGE_SIZE = 50


@dataclass(frozen=True)
class AppState:
    config: Config
    storage: Storage
    fleet: Fleet | None


def state() -> AppState:
    app_state: AppState = current_app.extensions["scrimmage"]
    return app_state


def conn() -> sqlite3.Connection:
    if "db" not in g:
        g.db = db.connect(state().config.db_path)
    connection: sqlite3.Connection = g.db
    return connection


def close_conn(_exc: BaseException | None = None) -> None:
    connection = g.pop("db", None)
    if connection is not None:
        connection.close()


def settings() -> Settings:
    if "settings" not in g:
        g.settings = Settings(conn())
    loaded: Settings = g.settings
    return loaded


# ---------------------------------------------------------------------------
# Current user
# ---------------------------------------------------------------------------


def load_user() -> None:
    """Populate g.user, g.team, g.real_user and g.is_admin from the session.

    ``real_user`` is whoever actually logged in; ``user`` differs from it only
    while an admin is impersonating someone.
    """
    g.user = g.team = g.real_user = None
    g.is_admin = False
    real_id = session.get("real_user_id")
    if real_id is None:
        return
    real = accounts.get_user(conn(), real_id)
    if real is None:
        session.clear()
        return
    g.real_user = real
    g.is_admin = bool(real["is_admin"])
    acting_id = session.get("user_id", real_id)
    if acting_id != real_id and not g.is_admin:
        acting_id = real_id
    g.user = real if acting_id == real_id else accounts.get_user(conn(), acting_id)
    if g.user is None:
        session["user_id"] = real_id
        g.user = real
    if g.user["team_id"] is not None:
        g.team = accounts.get_team(conn(), g.user["team_id"])


def log_in(user_id: int) -> None:
    session.clear()
    session.permanent = True
    session["real_user_id"] = user_id
    session["user_id"] = user_id


def login_required[**P, R](view: Callable[P, R]) -> Callable[P, R | Response]:
    @wraps(view)
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> R | Response:
        if g.user is None:
            return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))
        return view(*args, **kwargs)

    return wrapped


def team_required[**P, R](view: Callable[P, R]) -> Callable[P, R | Response]:
    @wraps(view)
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> R | Response:
        if g.user is None:
            return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))
        if g.team is None:
            return redirect(url_for("main.index"))
        return view(*args, **kwargs)

    return wrapped


def admin_required[**P, R](view: Callable[P, R]) -> Callable[P, R]:
    @wraps(view)
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
        if not g.is_admin:
            abort(404)
        return view(*args, **kwargs)

    return wrapped


# ---------------------------------------------------------------------------
# CSRF: every state-changing request carries the session's token.
# ---------------------------------------------------------------------------


def csrf_token() -> str:
    token = session.get("csrf")
    if token is None:
        token = session["csrf"] = secrets.token_urlsafe(32)
    return str(token)


def check_csrf() -> None:
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    if request.blueprint == "worker_api":
        return  # bearer-token API, no cookies involved
    expected = session.get("csrf")
    sent = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token", "")
    if not expected or not hmac.compare_digest(str(expected), sent):
        abort(400, "Your session expired or the form was stale. Reload the page and try again.")


# ---------------------------------------------------------------------------
# Redirect helpers
# ---------------------------------------------------------------------------


def safe_next(target: str | None, default: str = "/") -> str:
    """Only allow same-site relative paths as redirect targets."""
    if not target:
        return default
    parts = urlsplit(target)
    if parts.scheme or parts.netloc or not target.startswith("/") or target.startswith("//"):
        return default
    return target


def back(default_endpoint: str = "main.index") -> Response:
    """Redirect to the page the form was submitted from."""
    referrer = request.referrer
    if referrer:
        parts = urlsplit(referrer)
        if parts.netloc == request.host:
            path = parts.path + (f"?{parts.query}" if parts.query else "")
            return redirect(safe_next(path, url_for(default_endpoint)))
    return redirect(url_for(default_endpoint))


def form_int(name: str) -> int:
    try:
        return int(request.form[name])
    except (KeyError, ValueError):
        abort(400, f"Missing or invalid field {name!r}.")


def page_number() -> int:
    try:
        return max(1, int(request.args.get("page", "1")))
    except ValueError:
        return 1


# ---------------------------------------------------------------------------
# Template filters
# ---------------------------------------------------------------------------


def format_time(ts: int | None) -> str:
    if ts is None:
        return ""
    return datetime.fromtimestamp(ts, TIMEZONE).strftime("%b %-d, %Y %-I:%M %p")


def time_ago(ts: int | None) -> str:
    if ts is None:
        return ""
    seconds = int(datetime.now(UTC).timestamp()) - ts
    if seconds < 0:
        return "just now"
    for limit, unit, size in (
        (60, "second", 1),
        (3600, "minute", 60),
        (86400, "hour", 3600),
        (86400 * 30, "day", 86400),
    ):
        if seconds < limit:
            value = seconds // size
            if unit == "second" and value < 10:
                return "just now"
            return f"{value} {unit}{'' if value == 1 else 's'} ago"
    return format_time(ts)


def duration(seconds: int | None) -> str:
    if seconds is None:
        return ""
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes}m {secs:02d}s" if minutes else f"{secs}s"


def human_bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def visible_sides(game: sqlite3.Row) -> list[str]:
    """Which bot logs ('a', 'b') of a game the current viewer may open."""
    if g.is_admin:
        return ["a", "b"]
    if g.team is None:
        return []
    return [side for side in ("a", "b") if game[f"team_{side}_id"] == g.team["id"]]


def opens_at(s: Settings) -> datetime | None:
    """When the site opens to non-admins (None: it is open)."""
    text = s.text("site_opens_on")
    if not text:
        return None
    opening = datetime.combine(date.fromisoformat(text), time(), tzinfo=TIMEZONE)
    return opening if opening > datetime.now(TIMEZONE) else None


# Reachable while the site is closed: logging in (so admins can), assets, and
# the machinery that keeps running regardless.
OPEN_ENDPOINTS = ("static", "healthz")
OPEN_BLUEPRINTS = ("auth.", "worker_api.")


def offseason_gate() -> str | Response | None:
    """Before the site opens, non-admins see only a countdown."""
    endpoint = request.endpoint or ""
    if g.is_admin or endpoint in OPEN_ENDPOINTS or endpoint.startswith(OPEN_BLUEPRINTS):
        return None
    opening = opens_at(settings())
    if opening is None:
        return None
    if request.method != "GET":
        return redirect(url_for("main.index"))
    return render_template("countdown.html", opening=opening, countdown=True)


def asset_url(config: Config) -> Callable[[str], str]:
    """Static file URLs that change with each deploy, so browsers never use stale ones."""

    def url(filename: str) -> str:
        return url_for("static", filename=filename, v=config.commit[:12])

    return url


def template_globals(config: Config) -> dict[str, Any]:
    return {
        "asset": asset_url(config),
        "csrf_token": csrf_token,
        "visible_sides": visible_sides,
        "site": config,
        "settings": settings,
        "now_eastern": lambda: datetime.now(TIMEZONE),
        "site_opens_at": lambda: opens_at(settings()),
    }
