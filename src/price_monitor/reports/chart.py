"""Dependency-free inline SVG chart for the HTML report.

A diverging horizontal bar chart of per-product price changes in percent:
increases extend right, decreases extend left from a shared zero baseline.
Colours come from CSS classes defined by the report template, so the chart
follows the page's light/dark theme.
"""

from __future__ import annotations

import math
from decimal import Decimal
from html import escape

from price_monitor.models import Change
from price_monitor.reports.formatting import money, signed_pct

WIDTH = 1040
LABEL_W = 300
VALUE_PAD = 64
ROW_H = 28
BAR_H = 14
TOP = 34
BOTTOM = 12
RADIUS = 4


def _axis(value: float) -> tuple[int, int]:
    """Symmetric axis limit and tick step for the largest absolute value.

    >>> _axis(23.4), _axis(7.1), _axis(3), _axis(64)
    ((25, 5), (10, 5), (5, 5), (70, 10))
    """
    step = 5 if value <= 30 else 10 if value <= 100 else 50
    return max(step, math.ceil(value / step) * step), step


def _bar_path(x0: float, x1: float, y: float, h: float) -> str:
    """Bar with a rounded data end and a square end at the baseline ``x0``."""
    width = abs(x1 - x0)
    r = min(RADIUS, width, h / 2)
    if x1 >= x0:
        return (
            f"M{x0:.1f},{y:.1f} H{x1 - r:.1f} A{r},{r} 0 0 1 {x1:.1f},{y + r:.1f} "
            f"V{y + h - r:.1f} A{r},{r} 0 0 1 {x1 - r:.1f},{y + h:.1f} H{x0:.1f} Z"
        )
    return (
        f"M{x0:.1f},{y:.1f} H{x1 + r:.1f} A{r},{r} 0 0 0 {x1:.1f},{y + r:.1f} "
        f"V{y + h - r:.1f} A{r},{r} 0 0 0 {x1 + r:.1f},{y + h:.1f} H{x0:.1f} Z"
    )


def _truncate(text: str, limit: int = 40) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def price_change_chart(
    changes: list[Change], threshold_pct: Decimal, max_items: int = 20
) -> str | None:
    """Return the SVG markup, or ``None`` when there are no price changes to plot."""
    moves = [c for c in changes if c.price_delta_pct is not None]
    if not moves:
        return None
    moves.sort(key=lambda c: abs(c.price_delta_pct or 0), reverse=True)
    moves = sorted(moves[:max_items], key=lambda c: c.price_delta_pct or 0, reverse=True)

    threshold = float(threshold_pct)
    limit, step = _axis(max(max(abs(float(c.price_delta_pct or 0)) for c in moves), threshold))
    plot_left, plot_right = LABEL_W + VALUE_PAD, WIDTH - VALUE_PAD
    zero = (plot_left + plot_right) / 2
    half = (plot_right - plot_left) / 2
    height = TOP + ROW_H * len(moves) + BOTTOM

    def x(pct: float) -> float:
        return zero + pct / limit * half

    parts: list[str] = [
        f'<svg class="chart" viewBox="0 0 {WIDTH} {height}" width="100%" role="img" '
        f'aria-label="Price change in percent for {len(moves)} products" '
        f'xmlns="http://www.w3.org/2000/svg">'
    ]

    for tick in range(-limit, limit + 1, step):
        tx = x(tick)
        cls = "axis-zero" if tick == 0 else "grid"
        parts.append(
            f'<line class="{cls}" x1="{tx:.1f}" x2="{tx:.1f}" y1="{TOP - 6}" '
            f'y2="{height - BOTTOM}"/>'
        )
        label = f"{tick:+d}%" if tick else "0%"
        parts.append(f'<text class="tick" x="{tx:.1f}" y="{TOP - 12}">{label}</text>')

    for sign in (-1, 1):
        tx = x(sign * threshold)
        parts.append(
            f'<line class="threshold" x1="{tx:.1f}" x2="{tx:.1f}" y1="{TOP - 2}" '
            f'y2="{height - BOTTOM}"/>'
        )

    for i, change in enumerate(moves):
        pct = float(change.price_delta_pct or 0)
        row_y = TOP + i * ROW_H
        bar_y = row_y + (ROW_H - BAR_H) / 2
        direction = "up" if pct > 0 else "down"
        faded = "" if change.is_alert else " below"
        tip = (
            f"{change.name}: {money(change.old_price, change.currency)} → "
            f"{money(change.new_price, change.currency)} ({signed_pct(change.price_delta_pct)})"
            f"{'' if change.is_alert else ' - below alert threshold'}"
        )
        x1 = x(pct)
        value_x, anchor = (x1 + 6, "start") if pct > 0 else (x1 - 6, "end")
        parts.append(
            f'<g class="row"><title>{escape(tip)}</title>'
            f'<rect class="hit" x="0" y="{row_y}" width="{WIDTH}" height="{ROW_H}"/>'
            f'<text class="label" x="{LABEL_W}" y="{row_y + ROW_H / 2}">'
            f"{escape(_truncate(change.name))}</text>"
            f'<path class="bar bar-{direction}{faded}" d="{_bar_path(zero, x1, bar_y, BAR_H)}"/>'
            f'<text class="value" x="{value_x:.1f}" y="{row_y + ROW_H / 2}" '
            f'text-anchor="{anchor}">{signed_pct(change.price_delta_pct)}</text></g>'
        )

    parts.append("</svg>")
    return "".join(parts)
