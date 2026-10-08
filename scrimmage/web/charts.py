"""Tiny server-rendered SVG charts (no JavaScript, no external libraries)."""

from __future__ import annotations

from datetime import datetime

from markupsafe import Markup, escape

from scrimmage.web.common import TIMEZONE

WIDTH, HEIGHT = 720, 220
PAD_LEFT, PAD_RIGHT, PAD_TOP, PAD_BOTTOM = 44, 12, 12, 24


def rating_chart(points: list[tuple[int, float]], start: float = 1500.0) -> Markup:
    """Rating after each game, plotted against game number."""
    if not points:
        return Markup(
            '<p class="muted">No rated games yet. '
            "Challenge someone, and the line shows up after the match.</p>"
        )
    values = [start] + [value for _, value in points]
    low, high = min(values), max(values)
    if high - low < 50:
        middle = (high + low) / 2
        low, high = middle - 25, middle + 25
    span_x = max(1, len(values) - 1)
    inner_w = WIDTH - PAD_LEFT - PAD_RIGHT
    inner_h = HEIGHT - PAD_TOP - PAD_BOTTOM

    def x(i: int) -> float:
        return PAD_LEFT + inner_w * i / span_x

    def y(v: float) -> float:
        return PAD_TOP + inner_h * (1 - (v - low) / (high - low))

    path = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(values))
    first = datetime.fromtimestamp(points[0][0], TIMEZONE).strftime("%b %-d")
    last = datetime.fromtimestamp(points[-1][0], TIMEZONE).strftime("%b %-d")
    grid = []
    for fraction in (0.0, 0.5, 1.0):
        value = low + (high - low) * fraction
        gy = y(value)
        grid.append(
            f'<line x1="{PAD_LEFT}" x2="{WIDTH - PAD_RIGHT}" y1="{gy:.1f}" y2="{gy:.1f}" '
            f'class="grid"/><text x="{PAD_LEFT - 6}" y="{gy + 4:.1f}" text-anchor="end">'
            f"{value:.0f}</text>"
        )
    label = escape(f"Rating over {len(points)} games, now {values[-1]:.0f}")
    return Markup(
        f'<svg class="chart" viewBox="0 0 {WIDTH} {HEIGHT}" role="img" aria-label="{label}">'
        + "".join(grid)
        + f'<polyline points="{path}" class="line"/>'
        f'<text x="{PAD_LEFT}" y="{HEIGHT - 6}">{escape(first)}</text>'
        f'<text x="{WIDTH - PAD_RIGHT}" y="{HEIGHT - 6}" text-anchor="end">{escape(last)}</text>'
        "</svg>"
    )
