from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path

import pytest
from openpyxl import load_workbook

from price_monitor.diff import DiffResult, build_diff
from price_monitor.reports import write_reports
from price_monitor.reports.chart import price_change_chart
from price_monitor.reports.csv_export import CHANGE_COLUMNS, SNAPSHOT_COLUMNS, write_csv
from price_monitor.reports.excel import write_excel
from price_monitor.reports.html import render_html

from .conftest import make_product, make_run

SITE = "https://books.toscrape.com/"


def sample_diff(current_source: str = "demo") -> DiffResult:
    old = [
        make_product("UP", "10.00", name="Rising <Book>"),
        make_product("DOWN", "20.00", category="Poetry"),
        make_product("SMALL", "50.00"),
        make_product("SOLD", "5.00"),
        make_product("GONE", "9.00"),
    ]
    new = [
        make_product("UP", "12.00", name="Rising <Book>"),
        make_product("DOWN", "15.00", category="Poetry"),
        make_product("SMALL", "51.00"),
        make_product("SOLD", "5.00", in_stock=False),
    ]
    return build_diff(
        make_run(1, "scrape", len(old)),
        make_run(2, current_source, len(new)),
        old,
        new,
        threshold_pct=Decimal("5"),
    )


# --------------------------------------------------------------------------- Excel


def test_excel_structure_and_formatting(tmp_path: Path) -> None:
    path = write_excel(sample_diff(), tmp_path)
    wb = load_workbook(path)

    assert path.name == "price-report-run1-vs-run2.xlsx"
    assert wb.sheetnames == ["Summary", "Changes", "Snapshot"]

    changes = wb["Changes"]
    header = [c.value for c in changes[1]]
    assert header[:4] == ["Change", "Product", "Category", "SKU"]
    assert changes["A1"].font.b and changes["A1"].fill.fgColor.rgb.endswith("1F2937")
    assert changes.freeze_panes == "A2"
    assert changes.auto_filter.ref == "A1:L6"  # 5 changes + header
    assert [changes.cell(row=r, column=1).value for r in range(2, 7)] == [
        "Price down",
        "Price up",
        "Price up",
        "Out of stock",
        "Removed",
    ]
    assert changes["E2"].value == Decimal("20.00")
    assert changes["E2"].number_format == '"£"#,##0.00'
    assert changes["H2"].value == Decimal("-0.25")  # stored as a fraction, shown as %
    assert changes["H2"].number_format == "+0.0%;-0.0%;0.0%"
    assert changes["K2"].value == "Yes"  # -25 % is an alert
    assert changes["K4"].value is None  # +2 % is below the threshold

    rules = {str(cf.sqref): [r.operator for r in cf.rules] for cf in changes.conditional_formatting}
    assert len(rules["A2:A6"]) == 6  # one rule per change type
    assert rules["H2:H6"] == ["greaterThan", "lessThan"]

    snapshot = wb["Snapshot"]
    assert snapshot.max_row == 5  # header + 4 products
    assert snapshot.auto_filter.ref == "A1:H5"

    summary_text = " ".join(
        str(c.value) for row in wb["Summary"].iter_rows() for c in row if c.value
    )
    assert "SIMULATED" in summary_text


def test_excel_summary_has_no_demo_note_for_real_runs(tmp_path: Path) -> None:
    wb = load_workbook(write_excel(sample_diff("scrape"), tmp_path))
    summary_text = " ".join(
        str(c.value) for row in wb["Summary"].iter_rows() for c in row if c.value
    )
    assert "SIMULATED" not in summary_text


# --------------------------------------------------------------------------- CSV


def test_csv_exports(tmp_path: Path) -> None:
    snapshot_path, changes_path = write_csv(sample_diff(), tmp_path)

    assert snapshot_path.read_bytes().startswith(b"\xef\xbb\xbf")  # BOM for Excel
    with snapshot_path.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert list(rows[0]) == SNAPSHOT_COLUMNS
    assert len(rows) == 4

    with changes_path.open(encoding="utf-8-sig", newline="") as fh:
        changes = list(csv.DictReader(fh))
    assert list(changes[0]) == CHANGE_COLUMNS
    first = changes[0]
    assert first["change"] == "price_down"
    assert (first["old_price"], first["new_price"], first["price_delta_pct"]) == (
        "20.00",
        "15.00",
        "-25.0",
    )
    assert first["alert"] == "yes"
    removed = changes[-1]
    assert removed["change"] == "removed" and removed["new_price"] == ""


# --------------------------------------------------------------------------- HTML


def test_html_report_content() -> None:
    html = render_html(sample_diff(), site_url=SITE)

    assert html.startswith("<!doctype html>")
    assert "<svg" in html and "Price changes vs previous run" in html
    assert "Demo data." in html  # simulated runs are always labelled
    assert "Rising &lt;Book&gt;" in html and "Rising <Book>" not in html  # escaped
    assert "-25.0%" in html and "+20.0%" in html
    assert "<script" not in html  # self-contained, no JS needed
    assert 'href="http' not in html.split("<style>")[0]  # no external stylesheet


def test_html_report_for_real_run_has_no_demo_banner() -> None:
    assert "Demo data." not in render_html(sample_diff("scrape"), site_url=SITE)


def test_html_report_first_run_without_baseline() -> None:
    diff = build_diff(None, make_run(1), [], [make_product("A")], threshold_pct=Decimal("5"))
    html = render_html(diff, site_url=SITE)
    assert "No baseline yet" in html
    assert '<svg class="chart"' not in html


def test_chart_only_plots_price_changes() -> None:
    diff = sample_diff()
    svg = price_change_chart(diff.changes, diff.threshold_pct)
    assert svg is not None
    assert svg.count('class="bar ') == 3  # UP, DOWN, SMALL - not the stock/removed rows
    assert svg.count('class="threshold"') == 2  # +5 % and -5 %
    assert svg.count(' below"') == 1  # SMALL (+2 %) is drawn faded
    assert price_change_chart([], Decimal("5")) is None


# --------------------------------------------------------------------------- dispatcher


def test_write_reports_all_formats(tmp_path: Path) -> None:
    paths = write_reports(sample_diff(), tmp_path / "out", site_url=SITE)
    assert sorted(p.name for p in paths) == [
        "price-report-run1-vs-run2-changes.csv",
        "price-report-run1-vs-run2-snapshot.csv",
        "price-report-run1-vs-run2.html",
        "price-report-run1-vs-run2.xlsx",
    ]
    assert all(p.stat().st_size > 0 for p in paths)


def test_write_reports_rejects_unknown_format(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="pdf"):
        write_reports(sample_diff(), tmp_path, site_url=SITE, formats=["html", "pdf"])
