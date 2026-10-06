"""
What games run on, and what it costs.

Every game runs on AWS Graviton4 (Arm Neoverse V2, 2.8 GHz), where one vCPU is
one physical core: the main server (m8g.large, 2 cores) and the worker fleet
(m8g.4xlarge machines, 16 cores and 64 GiB each). Admins choose, on the
dashboard, the cores per game: 1, 2, 4 or 8. The two bots take turns, so this
is how many threads each bot can use while it thinks. Each bot gets 1.5 GB of
memory per core, which is what a machine has per core, so games pack onto
machines by cores alone.

Each game records its hardware when it is created, so changing it never
affects games already queued.

Prices are AWS us-east-1 on-demand list prices (Linux), used for estimates
only. Spot prices move daily; SPOT_FRACTION is a typical discount, not a quote.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from scrimmage.settings import Settings

CPU = "AWS Graviton4 (Arm Neoverse V2, 2.8 GHz), one physical core per vCPU"
# Per container, besides the bots' shares: the engine, and headroom for what a
# bot can allocate between two checks of the runner's memory guard.
ENGINE_RESERVE_MB = 384
GUARD_SLACK_MB = 64
# What each worker machine keeps for the OS and Docker.
MACHINE_RESERVE_MB = 1024

FLEET_MACHINE = "m8g.4xlarge"
FLEET_MACHINE_CORES = 16
FLEET_MACHINE_MEMORY_GIB = 64
FLEET_MACHINE_USD_PER_HOUR = 0.71808
SPOT_FRACTION = 0.40
MAIN_SERVER = "m8g.large"
MAIN_SERVER_USD_PER_HOUR = 0.08976

CORE_CHOICES = (1, 2, 4, 8)
BOT_MEMORY_MB_PER_CORE = 1536
MAX_GAME_MEMORY_MB = FLEET_MACHINE_MEMORY_GIB * 1024 - MACHINE_RESERVE_MB


def usd_per_core_hour(spot: bool) -> float:
    price = FLEET_MACHINE_USD_PER_HOUR * (SPOT_FRACTION if spot else 1.0)
    return price / FLEET_MACHINE_CORES


def game_memory_mb(bot_memory_mb: int) -> int:
    return 2 * bot_memory_mb + ENGINE_RESERVE_MB + GUARD_SLACK_MB


def container_memory_mb(params: dict[str, int | float | str]) -> int:
    """Memory limit of a game container (two bots) or a build container (one)."""
    bot = int(params["BOT_MEMORY_BYTES"]) >> 20
    bots = 1 if params.get("MODE") == "build" else 2
    return bots * bot + ENGINE_RESERVE_MB + GUARD_SLACK_MB


@dataclass(frozen=True)
class GameHardware:
    cores: int
    bot_memory_mb: int

    @property
    def memory_mb(self) -> int:
        return game_memory_mb(self.bot_memory_mb)

    def usd_per_game_hour(self, spot: bool) -> float:
        """What one game running for an hour costs on fleet machines."""
        return usd_per_core_hour(spot) * self.cores


def for_cores(cores: int) -> GameHardware:
    return GameHardware(cores=cores, bot_memory_mb=BOT_MEMORY_MB_PER_CORE * cores)


def label(cores: int) -> str:
    return "1 core" if cores == 1 else f"{cores} cores"


def choices() -> list[tuple[str, GameHardware]]:
    return [(label(cores), for_cores(cores)) for cores in CORE_CHOICES]


def for_new_games(settings: Settings) -> GameHardware:
    return for_cores(settings.number("cores_per_game"))


@dataclass(frozen=True)
class TournamentEstimate:
    games: int
    game_hours: float
    wall_hours: float  # at the given number of games at once
    spot_usd: float
    on_demand_usd: float


def estimate_tournament(
    teams: int,
    games_per_pair: int,
    minutes_per_game: float,
    hardware: GameHardware,
    games_at_once: int,
) -> TournamentEstimate:
    """Fleet cost of a round robin (the always-on main server is not included)."""
    games = games_per_pair * teams * (teams - 1) // 2
    game_hours = games * minutes_per_game / 60
    at_once = max(1, games_at_once)
    return TournamentEstimate(
        games=games,
        game_hours=game_hours,
        wall_hours=math.ceil(games / at_once) * minutes_per_game / 60,
        spot_usd=game_hours * hardware.usd_per_game_hour(spot=True),
        on_demand_usd=game_hours * hardware.usd_per_game_hour(spot=False),
    )
