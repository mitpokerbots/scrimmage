"""Session for Pkrtask, the task planner served at /pkrtask.

The Next.js app calls this on the internal port with the browser's session
cookie. It only reports who is logged in (the real user, never an impersonated
one); Pkrtask's own roster decides who may use it.
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, g, jsonify
from werkzeug.wrappers.response import Response

bp = Blueprint("pkrtask", __name__, url_prefix="/api/pkrtask")


def _json(status: int, body: Any) -> Response:
    response = jsonify(body)
    response.status_code = status
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.get("/session")
def session() -> Response:
    if g.real_user is None:
        return _json(401, {"error": "login required"})
    user = g.real_user
    return _json(
        200, {"viewer": {"kerberos": user["kerberos"], "display_name": user["display_name"]}}
    )
