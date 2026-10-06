"""
Admin-editable settings, stored in the ``settings`` table.

Each setting is typed and range-checked; unknown keys and invalid values are
rejected rather than stored.
"""

from __future__ import annotations

import datetime
import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class Setting:
    key: str
    default: bool | int | str
    description: str
    minimum: int | None = None
    maximum: int | None = None
    secret: bool = False
    # Edited on the admin dashboard rather than the settings page.
    hidden: bool = False
    # A YYYY-MM-DD date (or empty).
    date: bool = False

    def parse(self, raw: str) -> bool | int | str:
        if isinstance(self.default, bool):
            lowered = raw.strip().lower()
            if lowered not in ("true", "false"):
                raise ValueError(f"{self.key} must be true or false")
            return lowered == "true"
        if isinstance(self.default, int):
            try:
                value = int(raw.strip())
            except ValueError:
                raise ValueError(f"{self.key} must be an integer") from None
            if self.minimum is not None and value < self.minimum:
                raise ValueError(f"{self.key} must be at least {self.minimum}")
            if self.maximum is not None and value > self.maximum:
                raise ValueError(f"{self.key} must be at most {self.maximum}")
            return value
        text = raw.strip()
        if self.date and text:
            try:
                datetime.date.fromisoformat(text)
            except ValueError:
                raise ValueError(f"{self.key} must be a date like 2027-01-04, or empty") from None
        return text

    @staticmethod
    def serialize(value: bool | int | str) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)


SETTINGS: tuple[Setting, ...] = (
    Setting(
        "site_opens_on",
        "2027-01-04",
        "Until this date (YYYY-MM-DD, midnight Eastern), only admins can use the site; "
        "everyone else sees a countdown. Empty = open.",
        date=True,
    ),
    Setting("challenges_enabled", True, "Teams can challenge each other."),
    Setting("challenges_only_reference", False, "Teams may only challenge reference teams."),
    Setting(
        "down_challenges_require_accept",
        True,
        "A higher-rated team challenging a lower-rated team needs the lower team to accept.",
    ),
    Setting(
        "down_challenges_affect_elo",
        True,
        "Games where a higher-rated team challenged a lower-rated team change Elo.",
    ),
    Setting(
        "spawn_limit_per_team", 5, "Queued or running games a team may have initiated.", 1, 100
    ),
    Setting("maximum_team_size", 4, "Maximum members per team.", 1, 20),
    Setting("recent_games_to_show", 20, "Games listed on the home page.", 0, 200),
    Setting("max_bot_upload_mb", 100, "Largest accepted bot zip, in MB.", 1, 1000),
    Setting("bot_uploads_per_day", 50, "Bots a team may upload per 24 hours.", 1, 10_000),
    Setting("game_num_hands", 1000, "Hands per game.", 1, 100_000),
    Setting("game_starting_stack", 400, "Starting stack per hand, in chips.", 1, 1_000_000),
    Setting("game_big_blind", 2, "Big blind, in chips.", 1, 1_000_000),
    Setting("game_small_blind", 1, "Small blind, in chips.", 1, 1_000_000),
    Setting("game_time_bank_seconds", 60, "Total thinking time per bot per game.", 1, 3600),
    Setting(
        "bot_build_timeout_seconds",
        600,
        "Time allowed for a bot's build step (it runs once per upload, not per game).",
        10,
        3600,
    ),
    Setting("build_cores", 4, "CPU cores a bot's build step may use.", 1, 16),
    Setting("bot_connect_timeout_seconds", 10, "Time allowed for a bot to connect.", 1, 300),
    Setting("player_log_size_limit", 524_288, "Bytes of bot output kept per game.", 0, 64 << 20),
    Setting(
        "sponsor_portal_password",
        "",
        "Password for /sponsor (username 'sponsor'). Leave empty to disable the portal.",
        secret=True,
    ),
    Setting(
        "match_capacity",
        2,
        "Matches to run at once; above the main server's own slots, worker machines are added.",
        0,
        100_000,
        hidden=True,
    ),
    Setting(
        "idle_match_capacity",
        2,
        "Capacity to return to once no game has been queued or running for idle_minutes.",
        0,
        100_000,
        hidden=True,
    ),
    Setting(
        "idle_minutes",
        30,
        "Minutes without any games before capacity returns to idle_match_capacity (0 = never).",
        0,
        10_000,
        hidden=True,
    ),
    Setting("capacity_changed_at", 0, "When an admin last changed capacity.", hidden=True),
    Setting("disk_alert_at", 0, "When admins were last emailed about disk space.", hidden=True),
    # Hardware for new games (see services/hardware.py).
    Setting(
        "cores_per_game",
        1,
        "CPU cores per game (1, 2, 4 or 8). Bots take turns, so this is each bot's thread count.",
        1,
        8,
        hidden=True,
    ),
)
BY_KEY = {s.key: s for s in SETTINGS}


class Settings:
    """Snapshot of all settings, loaded with one query."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        stored = {row["key"]: row["value"] for row in conn.execute("SELECT * FROM settings")}
        self._values: dict[str, bool | int | str] = {}
        for setting in SETTINGS:
            raw = stored.get(setting.key)
            self._values[setting.key] = setting.default if raw is None else setting.parse(raw)

    def flag(self, key: str) -> bool:
        value = self._values[key]
        if not isinstance(value, bool):
            raise TypeError(f"setting {key} is not a boolean")
        return value

    def number(self, key: str) -> int:
        value = self._values[key]
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"setting {key} is not an integer")
        return value

    def text(self, key: str) -> str:
        value = self._values[key]
        if not isinstance(value, str):
            raise TypeError(f"setting {key} is not a string")
        return value

    def display(self, key: str) -> str:
        return Setting.serialize(self._values[key])


def update(conn: sqlite3.Connection, key: str, raw: str) -> None:
    if key not in BY_KEY:
        raise ValueError(f"unknown setting {key!r}")
    value = BY_KEY[key].parse(raw)
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        (key, Setting.serialize(value)),
    )
