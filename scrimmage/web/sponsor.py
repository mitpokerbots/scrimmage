"""Read-only sponsor portal: teams, members, and ratings.

Sponsors use HTTP basic auth (username ``sponsor``, password from the
``sponsor_portal_password`` setting); admins get in with their session.
"""

from __future__ import annotations

import hmac

from flask import Blueprint, abort, g, render_template, request
from werkzeug.wrappers.response import Response

from scrimmage.services import accounts, matches, tournaments
from scrimmage.web import charts, common

bp = Blueprint("sponsor", __name__, url_prefix="/sponsor")


def _authorized() -> bool:
    if g.is_admin:
        return True
    password = common.settings().text("sponsor_portal_password")
    auth = request.authorization
    if not password or auth is None or auth.type != "basic":
        return False
    return hmac.compare_digest((auth.username or "").lower(), "sponsor") & hmac.compare_digest(
        (auth.password or "").encode(), password.encode()
    )


@bp.before_request
def require_sponsor() -> Response | None:
    if _authorized():
        return None
    return Response(
        "Sponsor login required.\n",
        401,
        {"WWW-Authenticate": 'Basic realm="Pokerbots sponsors", charset="UTF-8"'},
    )


@bp.get("/")
def index() -> str:
    conn = common.conn()
    return render_template(
        "sponsor/index.html",
        teams=accounts.leaderboard(conn),
        tournaments=tournaments.listing(conn, include_private=False),
    )


@bp.get("/tournaments/<int:tournament_id>")
def tournament(tournament_id: int) -> str:
    conn = common.conn()
    row = tournaments.get(conn, tournament_id)
    if row is None or (row["is_private"] and not g.is_admin):
        abort(404)
    return render_template(
        "tournament.html",
        tournament=row,
        progress=tournaments.progress(conn, tournament_id),
        standings=tournaments.standings(conn, tournament_id),
    )


@bp.get("/teams/<int:team_id>")
def team(team_id: int) -> str:
    conn = common.conn()
    row = accounts.get_team(conn, team_id)
    if row is None:
        abort(404)
    return render_template(
        "sponsor/team.html",
        team=row,
        members=accounts.members(conn, team_id),
        bots=accounts.team_bots(conn, team_id),
        chart=charts.rating_chart(matches.elo_history(conn, team_id)),
    )
