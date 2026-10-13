"""Session for Pkrtask, the admin task planner served at /pkrtask.

The Next.js app calls this on the internal port with the browser's session
cookie. The identity is the real logged-in user, never an impersonated one.
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, g, jsonify
from werkzeug.wrappers.response import Response

from scrimmage.db import Row
from scrimmage.services import accounts
from scrimmage.web import common

bp = Blueprint("pkrtask", __name__, url_prefix="/api/pkrtask")


def _person(user: Row) -> dict[str, str | None]:
    return {"kerberos": user["kerberos"], "display_name": user["display_name"]}


def _json(status: int, body: Any) -> Response:
    response = jsonify(body)
    response.status_code = status
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.get("/session")
def session() -> Response:
    # Logged-out before not-admin: a missing user is 401, not 403.
    if g.real_user is None:
        return _json(401, {"error": "login required"})
    if not g.is_admin:
        return _json(403, {"error": "admins only"})
    return _json(
        200,
        {
            "viewer": _person(g.real_user),
            "admins": [_person(user) for user in accounts.list_admins(common.conn())],
        },
    )
