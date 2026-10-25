"""CSV export: one file for the latest snapshot, one for the detected changes."""

from __future__ import annotations

import csv
from pathlib import Path

from price_monitor.diff import DiffResult
from price_monitor.reports.formatting import report_basename

SNAPSHOT_COLUMNS = [
    "sku",
    "name",
    "category",
    "price",
    "currency",
    "in_stock",
    "stock_count",
    "rating",
    "url",
]
CHANGE_COLUMNS = [
    "change",
    "sku",
    "name",
    "category",
    "currency",
    "old_price",
    "new_price",
    "price_delta",
    "price_delta_pct",
    "old_in_stock",
    "new_in_stock",
    "old_stock_count",
    "new_stock_count",
    "alert",
    "url",
]


def _cell(value: object) -> object:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    return value


def write_csv(diff: DiffResult, out_dir: Path) -> list[Path]:
    """Write ``<base>-snapshot.csv`` and ``<base>-changes.csv``; return their paths.

    Files are UTF-8 with a BOM so that Excel opens them with the right encoding.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    base = report_basename(diff)

    snapshot_path = out_dir / f"{base}-snapshot.csv"
    with snapshot_path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow(SNAPSHOT_COLUMNS)
        for p in diff.snapshot:
            writer.writerow(
                _cell(v)
                for v in (
                    p.sku,
                    p.name,
                    p.category,
                    p.price,
                    p.currency,
                    p.in_stock,
                    p.stock_count,
                    p.rating,
                    p.url,
                )
            )

    changes_path = out_dir / f"{base}-changes.csv"
    with changes_path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow(CHANGE_COLUMNS)
        for c in diff.changes:
            writer.writerow(
                _cell(v)
                for v in (
                    c.type.value,
                    c.sku,
                    c.name,
                    c.category,
                    c.currency,
                    c.old_price,
                    c.new_price,
                    c.price_delta,
                    c.price_delta_pct,
                    c.old_in_stock,
                    c.new_in_stock,
                    c.old_stock_count,
                    c.new_stock_count,
                    c.is_alert,
                    c.url,
                )
            )

    return [snapshot_path, changes_path]
