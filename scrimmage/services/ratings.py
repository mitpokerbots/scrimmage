"""
Ratings, on the familiar Elo scale (a 400-point gap means 10:1 odds).

Ladder (scrimmages): plain Elo, updated after every game.

    E_a = 1 / (1 + 10^((R_b - R_a) / 400))
    R_a' = R_a + K (S_a - E_a),   S_a in {1, 0.5, 0}

Tournaments: a Bradley-Terry fit of all games at once (the model behind
BayesElo), so the order games happened in doesn't matter. Each team also gets
PRIOR_GAMES virtual draws against an average opponent, which keeps ratings
finite for teams that won or lost everything. Error bars are 95% intervals
from the fit's curvature.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable

INITIAL_RATING = 1500.0
K_FACTOR = 40.0
PRIOR_GAMES = 2.0
ELO_PER_NATURAL_LOG = 400 / math.log(10)

SCORE = {"a": (1.0, 0.0), "b": (0.0, 1.0), "tie": (0.5, 0.5)}


def expected_score(rating: float, opponent: float) -> float:
    return 1.0 / (1.0 + math.pow(10.0, (opponent - rating) / 400.0))


def elo_update(rating_a: float, rating_b: float, winner: str) -> tuple[float, float]:
    score_a, score_b = SCORE[winner]
    expected_a = expected_score(rating_a, rating_b)
    return (
        rating_a + K_FACTOR * (score_a - expected_a),
        rating_b + K_FACTOR * (score_b - (1.0 - expected_a)),
    )


def bradley_terry(
    games: Iterable[tuple[int, int, str]], iterations: int = 10_000
) -> dict[int, tuple[float, float]]:
    """Rate players from (player_a, player_b, winner) games.

    Returns {player: (rating, error)}, where rating ± error is a 95% interval.
    Fitted with the minorize-maximize algorithm (Hunter 2004).
    """
    points: dict[int, float] = defaultdict(float)
    played: dict[int, dict[int, float]] = defaultdict(lambda: defaultdict(float))
    for a, b, winner in games:
        score_a, score_b = SCORE[winner]
        points[a] += score_a
        points[b] += score_b
        played[a][b] += 1
        played[b][a] += 1
    if not played:
        return {}

    # Strength gamma = e^theta; the virtual average opponent has gamma 1.
    gamma = {p: 1.0 for p in played}
    for _ in range(iterations):
        updated = {}
        for p, opponents in played.items():
            denominator = PRIOR_GAMES / (gamma[p] + 1.0)
            denominator += sum(n / (gamma[p] + gamma[q]) for q, n in opponents.items())
            updated[p] = (points[p] + PRIOR_GAMES / 2) / denominator
        converged = all(abs(updated[p] / gamma[p] - 1) < 1e-8 for p in played)
        gamma = updated
        if converged:
            break

    mean = sum(math.log(g) for g in gamma.values()) / len(gamma)
    ratings = {}
    for p, opponents in played.items():
        information = PRIOR_GAMES * gamma[p] / (gamma[p] + 1.0) ** 2
        information += sum(
            n * gamma[p] * gamma[q] / (gamma[p] + gamma[q]) ** 2 for q, n in opponents.items()
        )
        rating = INITIAL_RATING + ELO_PER_NATURAL_LOG * (math.log(gamma[p]) - mean)
        error = 1.96 * ELO_PER_NATURAL_LOG / math.sqrt(information)
        ratings[p] = (rating, error)
    return ratings
