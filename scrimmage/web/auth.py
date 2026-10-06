"""
Login and logout.

Touchstone: /login sends the browser to /auth/touchstone, which Apache
protects with Shibboleth (deploy/apache-scrimmage.conf). Shibboleth redirects to
MIT's Okta IdP and, once the user is authenticated, Apache forwards the request
with X-Remote-User set to their eduPersonPrincipalName (kerberos@mit.edu).

Login links: ``scrimmage login-link KERBEROS`` on the server prints a one-time
URL, for bootstrapping admins before Touchstone registration is finished.
"""

from __future__ import annotations

from flask import Blueprint, abort, flash, g, redirect, render_template, request, session, url_for
from werkzeug.wrappers.response import Response

from scrimmage.services import accounts
from scrimmage.services.errors import UserError
from scrimmage.web import common

bp = Blueprint("auth", __name__)

MIT_SCOPE = "@mit.edu"


def _header(name: str) -> str:
    """Apache passes attribute bytes through; WSGI decodes them as latin-1."""
    raw = request.headers.get(name, "")
    return raw.encode("latin-1", "replace").decode("utf-8", "replace").strip()


def _finish_login(kerberos: str, display_name: str | None) -> Response:
    config = common.state().config
    user_id = accounts.record_login(common.conn(), kerberos, display_name, config.bootstrap_admins)
    next_url = common.safe_next(session.get("login_next"))
    common.log_in(user_id)
    return redirect(next_url)


@bp.get("/login")
def login() -> Response | str:
    if g.user is not None:
        return redirect(common.safe_next(request.args.get("next")))
    session["login_next"] = common.safe_next(request.args.get("next"))
    if common.state().config.auth_mode == "touchstone":
        return redirect(url_for("auth.touchstone"))
    return render_template("dev_login.html")


@bp.get("/auth/touchstone")
def touchstone() -> Response | tuple[str, int]:
    if common.state().config.auth_mode != "touchstone":
        abort(404)
    eppn = _header("X-Remote-User").lower()
    if not eppn:
        # Only reachable if Apache is not protecting this path.
        return render_template(
            "error.html",
            error="Touchstone did not identify you. The server's Shibboleth setup is incomplete; "
            "please tell the organizers.",
        ), 500
    if not eppn.endswith(MIT_SCOPE):
        return render_template(
            "error.html", error="Only MIT accounts (kerberos@mit.edu) can use this site."
        ), 403
    try:
        kerberos = accounts.normalize_kerberos(eppn[: -len(MIT_SCOPE)])
    except UserError as exc:
        return render_template("error.html", error=str(exc)), 403
    display_name = _header("X-Display-Name").split(";")[0].strip() or None
    return _finish_login(kerberos, display_name)


@bp.post("/auth/dev")
def dev_login() -> Response:
    if common.state().config.auth_mode != "dev":
        abort(404)
    kerberos = accounts.normalize_kerberos(request.form.get("kerberos", ""))
    return _finish_login(kerberos, None)


@bp.get("/auth/link/<token>")
def login_link(token: str) -> str:
    # Rendered as a confirmation form so link previewers cannot spend the token.
    return render_template("login_link.html", token=token)


@bp.post("/auth/link/<token>")
def redeem_link(token: str) -> Response:
    try:
        kerberos = accounts.redeem_login_token(common.conn(), token)
    except UserError as exc:
        flash(str(exc), "error")
        return redirect(url_for("main.index"))
    return _finish_login(kerberos, None)


@bp.post("/logout")
def logout() -> Response:
    session.clear()
    return redirect(url_for("main.index"))
