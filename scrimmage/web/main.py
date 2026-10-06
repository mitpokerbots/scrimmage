"""Pages for competitors: home, team, bots, challenges, games, tournaments."""

from __future__ import annotations

import gzip
import secrets
from collections.abc import Iterator
from pathlib import Path

from flask import (
    Blueprint,
    abort,
    flash,
    g,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from werkzeug.wrappers.response import Response

from scrimmage.db import Row, all_rows, one
from scrimmage.services import accounts, bots, matches, tournaments
from scrimmage.services.errors import UserError
from scrimmage.services.storage import LOG_KINDS
from scrimmage.web import charts, common
from scrimmage.web.common import PAGE_SIZE, login_required, team_required

bp = Blueprint("main", __name__)

ANNOUNCEMENTS = (
    "SELECT a.*, u.kerberos AS author FROM announcements a "
    "LEFT JOIN users u ON u.id = a.author_id {} ORDER BY a.id DESC"
)


@bp.get("/")
def index() -> str:
    conn = common.conn()
    s = common.settings()
    recent = matches.recent_scrimmages(conn, s.number("recent_games_to_show"))
    if g.user is None:
        announcements = all_rows(conn, ANNOUNCEMENTS.format("WHERE a.is_public") + " LIMIT 5")
        return render_template("logged_out.html", recent=recent, announcements=announcements)
    latest = all_rows(conn, ANNOUNCEMENTS.format("") + " LIMIT 1")
    if g.team is None:
        return render_template(
            "no_team.html",
            latest=latest,
            teams=accounts.joinable_teams(conn, s),
            join_request=accounts.pending_join_request(conn, g.user["id"]),
        )
    return render_template(
        "home.html",
        latest=latest,
        recent=recent,
        leaderboard=accounts.leaderboard(conn),
        incoming=matches.incoming_requests(conn, g.team["id"]),
        outgoing=matches.outgoing_requests(conn, g.team["id"]),
    )


# ---------------------------------------------------------------------------
# Joining a team
# ---------------------------------------------------------------------------


@bp.post("/team/create")
@login_required
def create_team() -> Response:
    accounts.create_team(common.conn(), g.user["id"], request.form.get("name", ""))
    flash("Team created. Upload a bot to start playing.", "success")
    return redirect(url_for("main.team"))


@bp.post("/team/join")
@login_required
def request_join() -> Response:
    accounts.request_join(
        common.conn(), common.settings(), g.user["id"], common.form_int("team_id")
    )
    flash("Request sent. A member of the team has to accept it.", "success")
    return redirect(url_for("main.index"))


@bp.post("/team/join/cancel")
@login_required
def cancel_join() -> Response:
    accounts.cancel_join(common.conn(), g.user["id"])
    return redirect(url_for("main.index"))


# ---------------------------------------------------------------------------
# Team page
# ---------------------------------------------------------------------------


@bp.get("/team")
@team_required
def team() -> str:
    conn = common.conn()
    team_id = g.team["id"]
    return render_template(
        "team.html",
        members=accounts.members(conn, team_id),
        join_requests=accounts.join_requests_for(conn, team_id),
        bots=accounts.team_bots(conn, team_id),
    )


@bp.get("/charts")
@team_required
def charts_page() -> str:
    history = matches.elo_history(common.conn(), g.team["id"])
    return render_template("charts.html", chart=charts.rating_chart(history))


@bp.post("/team/leave")
@team_required
def leave_team() -> Response:
    accounts.leave_team(common.conn(), g.user["id"])
    flash("You left the team.", "success")
    return redirect(url_for("main.index"))


@bp.post("/team/join-requests/<int:user_id>")
@team_required
def answer_join(user_id: int) -> Response:
    accept = request.form.get("action") == "accept"
    accounts.answer_join(common.conn(), common.settings(), g.team["id"], user_id, accept)
    flash("Added to your team." if accept else "Request declined.", "success")
    return redirect(url_for("main.team"))


# ---------------------------------------------------------------------------
# Bots
# ---------------------------------------------------------------------------


def save_upload(team_id: int, user_id: int | None) -> int:
    """Validate and store the bot zip in the current request."""
    state = common.state()
    s = common.settings()
    limit_mb = s.number("max_bot_upload_mb")
    limit = limit_mb << 20
    if request.content_length is not None and request.content_length > limit + (1 << 16):
        raise UserError(f"Bots must be at most {limit_mb} MB.")
    upload = request.files.get("file")
    if upload is None or not upload.filename:
        raise UserError("Choose a .zip file to upload.")
    tmp = state.storage.tmp_dir / f"upload-{secrets.token_hex(8)}.zip"
    try:
        upload.save(tmp)
        if tmp.stat().st_size > limit:
            raise UserError(f"Bots must be at most {limit_mb} MB.")
        return bots.create(
            common.conn(), state.storage, s, team_id, user_id, request.form.get("name", ""), tmp
        )
    finally:
        tmp.unlink(missing_ok=True)


@bp.post("/team/bots")
@team_required
def upload_bot() -> Response:
    save_upload(g.team["id"], g.user["id"])
    flash("Bot uploaded. It becomes your current bot once it builds.", "success")
    return redirect(url_for("main.team"))


@bp.post("/team/bots/<int:bot_id>/current")
@team_required
def set_current_bot(bot_id: int) -> Response:
    bots.set_current(common.conn(), g.team["id"], bot_id)
    flash("Current bot changed.", "success")
    return redirect(url_for("main.team"))


@bp.post("/team/bots/<int:bot_id>/delete")
@team_required
def delete_bot(bot_id: int) -> Response:
    bots.delete(common.conn(), g.team["id"], bot_id)
    flash("Bot deleted.", "success")
    return redirect(url_for("main.team"))


def own_bot(bot_id: int) -> Row:
    """A bot of the user's team (any bot, for admins), or 404."""
    bot = one(
        common.conn(),
        "SELECT b.*, t.name AS team_name FROM bots b JOIN teams t ON t.id = b.team_id "
        "WHERE b.id = ?",
        (bot_id,),
    )
    if bot is None or not (g.is_admin or (g.team is not None and bot["team_id"] == g.team["id"])):
        abort(404)
    return bot


@bp.get("/bots/<int:bot_id>/build-log")
@login_required
def build_log(bot_id: int) -> Response:
    own_bot(bot_id)
    path = common.state().storage.build_log(bot_id)
    if not path.exists():
        abort(404, "No build log yet: the bot has not finished building.")
    return send_gzipped_text(path, f"bot-{bot_id}-build.txt")


@bp.get("/bots/<int:bot_id>/download")
@login_required
def download_bot(bot_id: int) -> Response:
    bot = own_bot(bot_id)
    path = common.state().storage.bot_zip(bot_id)
    if not path.exists():
        abort(404)
    name = f"{bot['team_name']}-{bot['name']}".replace("/", "-").replace(" ", "_")
    return send_file(
        path, mimetype="application/zip", as_attachment=True, download_name=f"{name}.zip"
    )


# ---------------------------------------------------------------------------
# Challenges
# ---------------------------------------------------------------------------


@bp.post("/challenge")
@team_required
def challenge() -> Response:
    message = matches.challenge(
        common.conn(), common.settings(), g.team["id"], common.form_int("team_id")
    )
    flash(message, "success")
    return redirect(url_for("main.index"))


@bp.post("/challenges/<int:request_id>/answer")
@team_required
def answer_challenge(request_id: int) -> Response:
    accept = request.form.get("action") == "accept"
    message = matches.answer_request(
        common.conn(), common.settings(), g.team["id"], request_id, accept
    )
    flash(message, "success")
    return redirect(url_for("main.index"))


@bp.post("/challenges/<int:request_id>/cancel")
@team_required
def cancel_challenge(request_id: int) -> Response:
    matches.cancel_request(common.conn(), g.team["id"], request_id)
    flash("Challenge withdrawn.", "success")
    return redirect(url_for("main.index"))


# ---------------------------------------------------------------------------
# Games and logs
# ---------------------------------------------------------------------------


@bp.get("/games")
@team_required
def games() -> str:
    page = common.page_number()
    rows = matches.team_games(common.conn(), g.team["id"], PAGE_SIZE + 1, (page - 1) * PAGE_SIZE)
    return render_template(
        "games.html", games=rows[:PAGE_SIZE], page=page, has_next=len(rows) > PAGE_SIZE
    )


def can_read_log(game: Row, kind: str) -> bool:
    """Both teams see the game and engine logs; each team sees only its own bot's log."""
    if g.is_admin:
        return True
    if g.team is None:
        return False
    if game["tournament_id"] is not None:
        tournament = tournaments.get(common.conn(), game["tournament_id"])
        if tournament is None or tournament["is_private"]:
            return False
    side = {game["team_a_id"]: "a", game["team_b_id"]: "b"}.get(g.team["id"])
    if side is None:
        return False
    return kind in ("game", "engine") or kind == side


@bp.get("/games/<int:game_id>/logs/<kind>")
@login_required
def game_log(game_id: int, kind: str) -> Response:
    if kind not in LOG_KINDS:
        abort(404)
    game = matches.get_game(common.conn(), game_id)
    if game is None or not can_read_log(game, kind):
        abort(404)
    path = common.state().storage.log_path(game_id, kind)
    if not path.exists():
        abort(404, "That log is not available: the game has not finished, or its logs expired.")
    return send_gzipped_text(path, f"game-{game_id}-{kind}.txt")


def send_gzipped_text(path: Path, filename: str) -> Response:
    if "gzip" in request.headers.get("Accept-Encoding", ""):
        # Logs are stored gzipped; let the browser inflate them.
        response = send_file(path, mimetype="text/plain", download_name=filename, conditional=True)
        response.headers["Content-Encoding"] = "gzip"
        response.headers["Content-Type"] = "text/plain; charset=utf-8"
        response.headers["Vary"] = "Accept-Encoding"
        return response

    def inflate() -> Iterator[bytes]:
        with gzip.open(path, "rb") as f:
            yield from iter(lambda: f.read(1 << 16), b"")

    return Response(
        inflate(),
        mimetype="text/plain",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# Tournaments and announcements
# ---------------------------------------------------------------------------


@bp.get("/tournaments")
@login_required
def tournament_list() -> str:
    rows = tournaments.listing(common.conn(), include_private=g.is_admin)
    return render_template("tournaments.html", tournaments=rows)


@bp.get("/tournaments/<int:tournament_id>")
@login_required
def tournament_detail(tournament_id: int) -> str:
    conn = common.conn()
    tournament = tournaments.get(conn, tournament_id)
    if tournament is None or (tournament["is_private"] and not g.is_admin):
        abort(404)
    return render_template(
        "tournament.html",
        tournament=tournament,
        progress=tournaments.progress(conn, tournament_id),
        standings=tournaments.standings(conn, tournament_id),
    )


@bp.get("/announcements")
def announcements() -> str:
    rows = all_rows(common.conn(), ANNOUNCEMENTS.format("" if g.user else "WHERE a.is_public"))
    return render_template("announcements.html", announcements=rows)
