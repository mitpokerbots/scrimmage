"""Admin pages. Every route is hidden (404) from non-admins."""

from __future__ import annotations

import csv
import io
import json
import sqlite3
import tarfile
from collections.abc import Iterator

from flask import Blueprint, abort, flash, g, redirect, render_template, request, session, url_for
from werkzeug.wrappers.response import Response

from scrimmage import settings as settings_module
from scrimmage.db import all_rows, now, one, transaction
from scrimmage.services import accounts, builds, fleet, hardware, matches, queue, tournaments
from scrimmage.services import storage as storage_module
from scrimmage.services.errors import UserError
from scrimmage.services.storage import LOG_KINDS
from scrimmage.settings import Settings
from scrimmage.web import common
from scrimmage.web.common import PAGE_SIZE, admin_required
from scrimmage.web.main import save_upload

bp = Blueprint("admin", __name__, url_prefix="/admin")

GAME_STATUSES = ("queued", "running", "done", "error")
WORKER_STALE_SECONDS = 60


@bp.before_request
@admin_required
def require_admin() -> None:
    return None


@bp.get("/")
def dashboard() -> str:
    conn = common.conn()
    s = common.settings()
    workers = all_rows(conn, "SELECT * FROM worker_status ORDER BY name = 'main' DESC, name")
    fresh = [w for w in workers if now() - w["updated_at"] <= WORKER_STALE_SECONDS]
    fleet_status, fleet_error = None, None
    the_fleet = common.state().fleet
    if the_fleet is not None:
        try:
            fleet_status = the_fleet.status()
        except Exception as exc:
            fleet_error = f"Could not reach AWS: {exc}"
    needs = hardware.for_new_games(s)
    local = fleet.local_cores(conn)
    cores = fleet.cores_for(s.number("match_capacity"), needs, local)
    idle_cores = fleet.cores_for(s.number("idle_match_capacity"), needs, local)
    return render_template(
        "admin/dashboard.html",
        workers=workers,
        stale_after=WORKER_STALE_SECONDS,
        now=now(),
        has_fleet=the_fleet is not None,
        fleet_status=fleet_status,
        fleet_error=fleet_error,
        needs=needs,
        core_choices=hardware.CORE_CHOICES,
        local_cores=local,
        cores_online=sum(w["cores"] for w in fresh),
        cores_busy=sum(w["busy_cores"] for w in fresh),
        games_running=sum(w["games"] for w in fresh),
        hourly_spot=cores * hardware.usd_per_core_hour(spot=True),
        hourly_on_demand=cores * hardware.usd_per_core_hour(spot=False),
        idle_hourly_on_demand=idle_cores * hardware.usd_per_core_hour(spot=False),
        commit=common.state().config.commit,
        hardware=hardware,
        main_server=hardware.MAIN_SERVER,
        main_server_monthly=hardware.MAIN_SERVER_USD_PER_HOUR * 730,
        build_counts=builds.queue_counts(conn),
        disk=common.state().storage.disk(),
        disk_warn=storage_module.WARN_FREE_BYTES,
        disk_min=storage_module.MIN_FREE_BYTES,
        counts=matches.queue_counts(conn),
        errors=matches.all_games(conn, "error", 10, 0),
        totals=one(
            conn,
            "SELECT (SELECT count(*) FROM users) AS users, "
            "(SELECT count(*) FROM teams WHERE NOT is_disabled) AS teams, "
            "(SELECT count(*) FROM bots) AS bots, "
            "(SELECT count(*) FROM games WHERE status = 'done') AS games",
        ),
    )


@bp.post("/capacity")
def set_capacity() -> Response:
    form = request.form
    try:
        numbers = {
            key: int(form.get(key, ""))
            for key in ("capacity", "idle_capacity", "idle_minutes", "cores")
        }
    except ValueError:
        raise UserError("Every hardware field must be a whole number.") from None
    try:
        fleet.set_capacity(
            common.conn(),
            common.state().fleet,
            capacity=numbers["capacity"],
            idle_capacity=numbers["idle_capacity"],
            idle_minutes=numbers["idle_minutes"],
            cores=numbers["cores"],
        )
    except UserError:
        raise
    except Exception as exc:
        raise UserError(f"AWS rejected the change: {exc}") from None
    flash(
        f"New games get {hardware.label(numbers['cores'])}, {numbers['capacity']} at once. "
        "New worker machines take a minute or two to come online.",
        "success",
    )
    return redirect(url_for("admin.dashboard"))


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


@bp.get("/settings")
def settings_page() -> str:
    visible = [s for s in settings_module.SETTINGS if not s.hidden]
    return render_template("admin/settings.html", all_settings=visible)


@bp.post("/settings")
def save_setting() -> Response:
    key = request.form.get("key", "")
    if key in settings_module.BY_KEY and settings_module.BY_KEY[key].hidden:
        abort(400)
    try:
        settings_module.update(common.conn(), key, request.form.get("value", ""))
    except ValueError as exc:
        raise UserError(str(exc)) from None
    flash(f"Saved {key}.", "success")
    return redirect(url_for("admin.settings_page"))


# ---------------------------------------------------------------------------
# Teams
# ---------------------------------------------------------------------------


@bp.get("/teams")
def teams() -> str:
    return render_template("admin/teams.html", teams=accounts.all_teams(common.conn()))


@bp.post("/teams")
def create_team() -> Response:
    conn = common.conn()
    with transaction(conn):
        accounts.insert_team(
            conn, request.form.get("name", ""), is_reference=bool(request.form.get("is_reference"))
        )
    flash("Team created.", "success")
    return redirect(url_for("admin.teams"))


@bp.post("/teams/<int:team_id>")
def update_team(team_id: int) -> Response:
    accounts.update_team(
        common.conn(),
        team_id,
        name=request.form.get("name", ""),
        is_disabled=bool(request.form.get("is_disabled")),
        is_reference=bool(request.form.get("is_reference")),
    )
    flash("Team updated.", "success")
    return redirect(url_for("admin.teams"))


@bp.post("/teams/<int:team_id>/delete")
def delete_team(team_id: int) -> Response:
    storage = common.state().storage
    for bot_id in accounts.delete_team(common.conn(), team_id):
        storage.bot_zip(bot_id).unlink(missing_ok=True)
    flash("Team deleted.", "success")
    return redirect(url_for("admin.teams"))


@bp.post("/teams/<int:team_id>/bots")
def upload_bot(team_id: int) -> Response:
    if accounts.get_team(common.conn(), team_id) is None:
        abort(404)
    save_upload(team_id, g.real_user["id"])
    flash("Bot uploaded and set as that team's current bot.", "success")
    return redirect(url_for("admin.teams"))


@bp.post("/teams/reset-ratings")
def reset_ratings() -> Response:
    if request.form.get("confirm") != "RESET":
        raise UserError("Type RESET to confirm.")
    accounts.reset_ratings(common.conn())
    flash("Every team is back to 1500 with a clean record.", "success")
    return redirect(url_for("admin.teams"))


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


def _optional_team_id() -> int | None:
    raw = request.form.get("team_id", "")
    return int(raw) if raw.isdigit() else None


@bp.get("/users")
def users() -> str:
    conn = common.conn()
    return render_template(
        "admin/users.html", users=accounts.all_users(conn), teams=accounts.all_teams(conn)
    )


@bp.post("/users")
def create_user() -> Response:
    accounts.admin_create_user(common.conn(), request.form.get("kerberos", ""), _optional_team_id())
    flash("User added.", "success")
    return redirect(url_for("admin.users"))


@bp.post("/users/<int:user_id>")
def update_user(user_id: int) -> Response:
    is_admin = bool(request.form.get("is_admin"))
    if user_id == g.real_user["id"] and not is_admin:
        raise UserError("You cannot remove your own admin access.")
    accounts.admin_set_user(common.conn(), user_id, _optional_team_id(), is_admin)
    flash("User updated.", "success")
    return redirect(url_for("admin.users"))


@bp.post("/users/<int:user_id>/delete")
def delete_user(user_id: int) -> Response:
    if user_id == g.real_user["id"]:
        raise UserError("You cannot delete yourself.")
    accounts.delete_user(common.conn(), user_id)
    flash("User deleted.", "success")
    return redirect(url_for("admin.users"))


@bp.get("/impersonate")
def impersonate_page() -> str:
    return render_template("admin/impersonate.html")


@bp.post("/impersonate")
def impersonate() -> Response:
    conn = common.conn()
    user = one(
        conn,
        "SELECT * FROM users WHERE kerberos = ?",
        (request.form.get("kerberos", "").strip().lower(),),
    )
    if user is None:
        raise UserError("No user with that kerberos has logged in yet.")
    session["user_id"] = user["id"]
    flash(f"You are now viewing the site as {user['kerberos']}.", "success")
    return redirect(url_for("main.index"))


@bp.post("/impersonate/stop")
def stop_impersonating() -> Response:
    session["user_id"] = session.get("real_user_id")
    return redirect(url_for("admin.impersonate_page"))


# ---------------------------------------------------------------------------
# Games
# ---------------------------------------------------------------------------


@bp.get("/games")
def games() -> str:
    args = request.args
    status = args.get("status") or None
    if status not in (None, *GAME_STATUSES):
        abort(404)
    tournament = args.get("tournament", "")
    page = common.page_number()
    rows = matches.search_games(
        common.conn(),
        PAGE_SIZE + 1,
        (page - 1) * PAGE_SIZE,
        status=status,
        team=args.get("team", "").strip(),
        tournament_id=int(tournament) if tournament.isdigit() else None,
        text=args.get("text", "").strip(),
    )
    return render_template(
        "admin/games.html",
        games=rows[:PAGE_SIZE],
        page=page,
        has_next=len(rows) > PAGE_SIZE,
        status=status,
        statuses=GAME_STATUSES,
        filters={k: v for k, v in args.items() if k != "page" and v},
    )


@bp.get("/games/<int:game_id>")
def game_detail(game_id: int) -> str:
    conn = common.conn()
    game = matches.get_game(conn, game_id)
    if game is None:
        abort(404)
    stats = json.loads(game["stats"]) if game["stats"] else {}
    storage = common.state().storage
    return render_template(
        "admin/game.html",
        game=game,
        stats=stats,
        bot_stats=[(side, stats.get("bots", {}).get(side.upper(), {})) for side in ("a", "b")],
        logs=[kind for kind in LOG_KINDS if storage.log_path(game_id, kind).exists()],
        builds={side: builds.get(conn, game[f"bot_{side}_id"]) for side in ("a", "b")},
        cpu=hardware.CPU,
    )


@bp.post("/games/<int:game_id>/retry")
def retry_game(game_id: int) -> Response:
    queue.retry_failed(common.conn(), game_id)
    flash(f"Game {game_id} is back in the queue.", "success")
    return common.back("admin.games")


@bp.post("/games/<int:game_id>/cancel")
def cancel_game(game_id: int) -> Response:
    queue.cancel_queued(common.conn(), game_id)
    flash(f"Game {game_id} cancelled.", "success")
    return common.back("admin.games")


@bp.post("/games/retry-errors")
def retry_all_errors() -> Response:
    count = queue.retry_failed_scrimmages(common.conn())
    flash(f"Requeued {count} failed scrimmages.", "success")
    return redirect(url_for("admin.games"))


# ---------------------------------------------------------------------------
# Tournaments
# ---------------------------------------------------------------------------


def typical_game_minutes(conn: sqlite3.Connection, settings: Settings) -> float:
    """Average length of recent games, or a pessimistic guess before there are any."""
    row = one(
        conn,
        "SELECT avg(json_extract(stats, '$.seconds')) AS seconds, count(*) AS n FROM ("
        "  SELECT stats FROM games WHERE status = 'done' AND stats IS NOT NULL "
        "  ORDER BY id DESC LIMIT 500)",
    )
    if row is not None and row["n"] >= 20 and row["seconds"]:
        return round(float(row["seconds"]) / 60, 1)
    # Both bots spending their whole time bank, plus start-up.
    return round((2 * settings.number("game_time_bank_seconds") + 30) / 60, 1)


@bp.get("/tournaments")
def tournament_list() -> str:
    conn = common.conn()
    s = common.settings()
    eligible = tournaments.eligible_teams(conn)
    args = request.args
    try:
        teams = max(2, int(args.get("teams") or max(len(eligible), 2)))
        games_per_pair = max(1, int(args.get("games_per_pair") or 2))
        minutes = max(0.1, float(args.get("minutes") or typical_game_minutes(conn, s)))
        at_once = max(1, int(args.get("at_once") or max(s.number("match_capacity"), 1)))
    except ValueError:
        raise UserError("Estimates need numbers.") from None
    estimates = [
        (
            label,
            choice,
            hardware.estimate_tournament(teams, games_per_pair, minutes, choice, at_once),
        )
        for label, choice in hardware.choices()
    ]
    hours_per_month = 730
    return render_template(
        "admin/tournaments.html",
        tournaments=tournaments.listing(conn, include_private=True),
        eligible=eligible,
        estimate={
            "teams": teams,
            "games_per_pair": games_per_pair,
            "minutes": minutes,
            "at_once": at_once,
        },
        estimates=estimates,
        main_server=hardware.MAIN_SERVER,
        main_server_monthly=hardware.MAIN_SERVER_USD_PER_HOUR * hours_per_month,
    )


@bp.post("/tournaments")
def create_tournament() -> Response:
    try:
        games_per_pair = int(request.form.get("games_per_pair", ""))
    except ValueError:
        raise UserError("Games per pair must be a number.") from None
    team_ids = [int(v) for v in request.form.getlist("team_ids") if v.isdigit()]
    tournament_id = tournaments.create(
        common.conn(),
        common.settings(),
        request.form.get("title", ""),
        games_per_pair,
        bool(request.form.get("is_private")),
        team_ids,
        g.real_user["id"],
    )
    flash("Tournament created; its games are in the queue.", "success")
    return redirect(url_for("main.tournament_detail", tournament_id=tournament_id))


EXPORT_COLUMNS = (
    "id",
    "status",
    "team_a_name",
    "bot_a_id",
    "team_b_name",
    "bot_b_id",
    "score_a",
    "score_b",
    "winner",
    "cores",
    "bot_memory_mb",
    "worker",
    "started_at",
    "finished_at",
    "error",
)
BOT_STAT_COLUMNS = ("connected", "clock_used", "clock_out", "bankroll", "peak_memory_mb")


@bp.get("/tournaments/<int:tournament_id>/games.csv")
def tournament_csv(tournament_id: int) -> Response:
    """Every game of a tournament, with each bot's time, memory and kill events."""
    rows = matches.tournament_games(common.conn(), tournament_id)
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(
        [*EXPORT_COLUMNS, "machine", "seconds"]
        + [f"{side}_{column}" for side in "ab" for column in BOT_STAT_COLUMNS]
        + ["events"]
    )
    for row in rows:
        stats = json.loads(row["stats"]) if row["stats"] else {}
        bots = stats.get("bots", {})
        writer.writerow(
            [row[column] for column in EXPORT_COLUMNS]
            + [stats.get("machine", ""), stats.get("seconds", "")]
            + [bots.get(side, {}).get(column, "") for side in "AB" for column in BOT_STAT_COLUMNS]
            + [" | ".join(stats.get("events", []))]
        )
    return Response(
        out.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="tournament-{tournament_id}-games.csv"'
        },
    )


@bp.get("/tournaments/<int:tournament_id>/logs.tar")
def tournament_logs(tournament_id: int) -> Response:
    """Every log of a tournament's games, streamed as an (uncompressed) tar of .gz files."""
    storage = common.state().storage
    game_ids = [row["id"] for row in matches.tournament_games(common.conn(), tournament_id)]

    def stream() -> Iterator[bytes]:
        for game_id in game_ids:
            for kind in LOG_KINDS:
                path = storage.log_path(game_id, kind)
                if not path.exists():
                    continue
                info = tarfile.TarInfo(f"game-{game_id}/{kind}.log.gz")
                info.size = path.stat().st_size
                info.mtime = int(path.stat().st_mtime)
                yield info.tobuf(format=tarfile.PAX_FORMAT)
                with open(path, "rb") as f:
                    yield from iter(lambda: f.read(1 << 16), b"")
                yield b"\0" * (-info.size % tarfile.BLOCKSIZE)
        yield b"\0" * (2 * tarfile.BLOCKSIZE)

    return Response(
        stream(),
        mimetype="application/x-tar",
        headers={
            "Content-Disposition": f'attachment; filename="tournament-{tournament_id}-logs.tar"'
        },
    )


@bp.post("/tournaments/<int:tournament_id>/<action>")
def tournament_action(tournament_id: int, action: str) -> Response:
    conn = common.conn()
    if tournaments.get(conn, tournament_id) is None:
        abort(404)
    if action == "retry":
        flash(f"Requeued {tournaments.retry_errors(conn, tournament_id)} failed games.", "success")
    elif action == "cancel":
        flash(f"Removed {tournaments.cancel_queued(conn, tournament_id)} queued games.", "success")
    elif action in ("private", "public"):
        tournaments.set_private(conn, tournament_id, action == "private")
        flash(f"Tournament is now {action}.", "success")
    elif action == "delete":
        tournaments.delete(conn, common.state().storage, tournament_id)
        flash("Tournament deleted.", "success")
        return redirect(url_for("admin.tournament_list"))
    else:
        abort(404)
    return redirect(url_for("main.tournament_detail", tournament_id=tournament_id))


# ---------------------------------------------------------------------------
# Announcements
# ---------------------------------------------------------------------------


@bp.get("/announcements")
def announcements() -> str:
    rows = all_rows(
        common.conn(),
        "SELECT a.*, u.kerberos AS author FROM announcements a "
        "LEFT JOIN users u ON u.id = a.author_id ORDER BY a.id DESC",
    )
    return render_template("admin/announcements.html", announcements=rows)


@bp.post("/announcements")
def create_announcement() -> Response:
    title = request.form.get("title", "").strip()
    body = request.form.get("body", "").strip()
    if not title or not body:
        raise UserError("An announcement needs a title and a body.")
    common.conn().execute(
        "INSERT INTO announcements (author_id, title, body, is_public, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            g.real_user["id"],
            title[:200],
            body[:20000],
            int(bool(request.form.get("is_public"))),
            now(),
        ),
    )
    flash("Announcement posted.", "success")
    return redirect(url_for("admin.announcements"))


@bp.post("/announcements/<int:announcement_id>/delete")
def delete_announcement(announcement_id: int) -> Response:
    common.conn().execute("DELETE FROM announcements WHERE id = ?", (announcement_id,))
    flash("Announcement deleted.", "success")
    return redirect(url_for("admin.announcements"))
