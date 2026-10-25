"""Self-contained HTML report (inline CSS + inline SVG, no external assets)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from jinja2 import Environment, PackageLoader, select_autoescape

from price_monitor import __version__
from price_monitor.diff import DiffResult
from price_monitor.models import Change, ChangeType
from price_monitor.reports.chart import price_change_chart
from price_monitor.reports.formatting import (
    category_stats,
    money,
    report_basename,
    signed_money,
    signed_pct,
    stock_label,
)

_env = Environment(
    loader=PackageLoader("price_monitor", "reports/templates"),
    autoescape=select_autoescape(["html", "j2"]),
    trim_blocks=True,
    lstrip_blocks=True,
)
_env.filters["money"] = money


@dataclass(frozen=True, slots=True)
class Tile:
    label: str
    value: str
    hint: str
    tone: str  # CSS modifier: up, down, out, back, new, removed, neutral, alert


@dataclass(frozen=True, slots=True)
class ChangeRow:
    tone: str
    label: str
    name: str
    url: str
    category: str
    old_price: str
    new_price: str
    delta: str
    delta_pct: str
    direction: str  # "up", "down" or ""
    stock_before: str
    stock_now: str
    is_alert: bool
    is_price_change: bool


TONES = {
    ChangeType.PRICE_UP: "up",
    ChangeType.PRICE_DOWN: "down",
    ChangeType.OUT_OF_STOCK: "out",
    ChangeType.BACK_IN_STOCK: "back",
    ChangeType.NEW: "new",
    ChangeType.REMOVED: "removed",
}


def _row(c: Change) -> ChangeRow:
    delta = c.price_delta
    direction = "" if delta is None or delta == 0 else ("up" if delta > 0 else "down")
    return ChangeRow(
        tone=TONES[c.type],
        label=c.type.label,
        name=c.name,
        url=c.url,
        category=c.category,
        old_price=money(c.old_price, c.currency),
        new_price=money(c.new_price, c.currency),
        delta=signed_money(delta, c.currency),
        delta_pct=signed_pct(c.price_delta_pct),
        direction=direction,
        stock_before=stock_label(c.old_in_stock, c.old_stock_count),
        stock_now=stock_label(c.new_in_stock, c.new_stock_count),
        is_alert=c.is_alert,
        is_price_change=c.is_price_change,
    )


def _tiles(diff: DiffResult) -> list[Tile]:
    s = diff.summary
    in_stock = sum(1 for p in diff.snapshot if p.in_stock)
    tiles = [
        Tile(
            "Products tracked",
            str(len(diff.snapshot)),
            f"{in_stock} in stock",
            "neutral",
        )
    ]
    if s is None or not diff.has_baseline:
        return tiles
    avg = s.avg_price_change_pct
    tiles += [
        Tile("Price increases", str(s.count(ChangeType.PRICE_UP)), "vs previous run", "up"),
        Tile("Price decreases", str(s.count(ChangeType.PRICE_DOWN)), "vs previous run", "down"),
        Tile(
            "Alerts",
            str(s.alerts),
            f"price moves ≥ {diff.threshold_pct}% and stock changes",
            "alert",
        ),
        Tile("Went out of stock", str(s.count(ChangeType.OUT_OF_STOCK)), "were available", "out"),
        Tile("Back in stock", str(s.count(ChangeType.BACK_IN_STOCK)), "were sold out", "back"),
        Tile(
            "New / removed",
            f"{s.count(ChangeType.NEW)} / {s.count(ChangeType.REMOVED)}",
            "products listed / delisted",
            "new",
        ),
        Tile(
            "Average price move",
            signed_pct(avg) if avg is not None else "-",
            "across changed prices",
            "neutral",
        ),
    ]
    return tiles


def render_html(diff: DiffResult, *, site_url: str) -> str:
    template = _env.get_template("report.html.j2")
    return template.render(
        diff=diff,
        site_url=site_url,
        version=__version__,
        generated_at=datetime.now(UTC).strftime("%d %b %Y, %H:%M UTC"),
        tiles=_tiles(diff),
        rows=[_row(c) for c in diff.changes],
        chart_svg=price_change_chart(diff.changes, diff.threshold_pct),
        price_change_count=sum(1 for c in diff.changes if c.is_price_change),
        categories=category_stats(diff.snapshot),
        currency=diff.snapshot[0].currency if diff.snapshot else "GBP",
    )


def write_html(diff: DiffResult, out_dir: Path, *, site_url: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{report_basename(diff)}.html"
    path.write_text(render_html(diff, site_url=site_url), encoding="utf-8")
    return path
