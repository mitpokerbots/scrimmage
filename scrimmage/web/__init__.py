"""
The web application.

Production runs it under gunicorn behind Apache, which terminates TLS and runs
the Shibboleth SP for Touchstone (see deploy/). Gunicorn listens on a unix
socket that only Apache can reach, so the X-Remote-User header Apache sets on
/auth/touchstone cannot be forged by clients.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from flask import Flask, flash, g, render_template
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.wrappers.response import Response

from scrimmage import db
from scrimmage.config import Config, load_config
from scrimmage.services import fleet
from scrimmage.services.errors import UserError
from scrimmage.services.storage import Storage
from scrimmage.web import common

# Hard cap on any request body; the per-upload limit is an admin setting.
MAX_REQUEST_BYTES = 640 << 20  # a 512 MiB built bot plus its log


def create_app(config: Config | None = None) -> Flask:
    config = config or load_config()
    db.migrate(config.db_path)

    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=config.secret_key,
        SESSION_COOKIE_NAME="scrimmage_session",
        SESSION_COOKIE_SECURE=config.secure_cookies,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=timedelta(days=14),
        MAX_CONTENT_LENGTH=MAX_REQUEST_BYTES,
        MAX_FORM_MEMORY_SIZE=1 << 20,
        TEMPLATES_AUTO_RELOAD=False,
    )
    app.extensions["scrimmage"] = common.AppState(
        config=config,
        storage=Storage(config.data_dir),
        fleet=fleet.from_config(config.fleet_group, config.aws_region),
    )
    # Apache is the only client and sets X-Forwarded-Proto/-For.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)  # type: ignore[method-assign]

    from scrimmage.web import admin, auth, main, sponsor, worker_api  # noqa: PLC0415

    app.register_blueprint(auth.bp)
    app.register_blueprint(main.bp)
    app.register_blueprint(admin.bp)
    app.register_blueprint(sponsor.bp)
    app.register_blueprint(worker_api.bp)

    app.before_request(common.load_user)
    app.before_request(common.offseason_gate)
    app.before_request(common.check_csrf)
    app.teardown_appcontext(common.close_conn)
    # Globals (not a context processor) so imported macros can use them too.
    app.jinja_env.globals.update(common.template_globals(config))
    app.add_template_filter(common.format_time, "time")
    app.add_template_filter(common.time_ago, "ago")
    app.add_template_filter(common.duration, "duration")
    app.add_template_filter(common.human_bytes, "bytes")

    @app.after_request
    def security_headers(response: Response) -> Response:
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault(
            "Content-Security-Policy",
            # Semantic UI loads the Lato font from Google Fonts.
            "default-src 'self'; img-src 'self' data:; "
            "style-src 'self' https://fonts.googleapis.com; "
            "font-src 'self' data: https://fonts.gstatic.com; "
            "script-src 'self'; frame-ancestors 'none'; form-action 'self'",
        )
        if g.get("user") is not None:
            response.headers.setdefault("Cache-Control", "private, no-store")
        return response

    @app.errorhandler(UserError)
    def user_error(exc: UserError) -> Response:
        flash(str(exc), "error")
        return common.back()

    @app.errorhandler(HTTPException)
    def http_error(exc: HTTPException) -> tuple[str, int]:
        return render_template("error.html", error=exc), exc.code or 500

    @app.get("/healthz")
    def healthz() -> tuple[str, int, dict[str, str]]:
        common.conn().execute("SELECT 1").fetchone()
        return "ok\n", 200, {"Content-Type": "text/plain"}

    if not app.debug:
        logging.getLogger("werkzeug").setLevel(logging.WARNING)
    app.logger.info("scrimmage web starting (auth=%s, data=%s)", config.auth_mode, config.data_dir)
    return app
