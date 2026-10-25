"""Report writers: Excel, CSV and a self-contained HTML page."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from price_monitor.diff import DiffResult
from price_monitor.reports.csv_export import write_csv
from price_monitor.reports.excel import write_excel
from price_monitor.reports.html import write_html

FORMATS = ("html", "xlsx", "csv")


def write_reports(
    diff: DiffResult, out_dir: Path, *, site_url: str, formats: Iterable[str] = FORMATS
) -> list[Path]:
    wanted = set(formats)
    unknown = wanted - set(FORMATS)
    if unknown:
        raise ValueError(f"Unknown report format(s): {', '.join(sorted(unknown))}")
    paths: list[Path] = []
    if "html" in wanted:
        paths.append(write_html(diff, out_dir, site_url=site_url))
    if "xlsx" in wanted:
        paths.append(write_excel(diff, out_dir))
    if "csv" in wanted:
        paths.extend(write_csv(diff, out_dir))
    return paths


__all__ = ["FORMATS", "write_csv", "write_excel", "write_html", "write_reports"]
