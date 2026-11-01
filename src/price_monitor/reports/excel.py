"""Excel export with formatted headers, filters and conditional formatting."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.formatting.rule import CellIsRule
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from price_monitor import __version__
from price_monitor.diff import CHANGE_ORDER, DiffResult
from price_monitor.models import ChangeType, Run
from price_monitor.reports.formatting import CURRENCY_SIGNS, report_basename

HEADER_FILL = PatternFill("solid", start_color="1F2937", end_color="1F2937")
HEADER_FONT = Font(bold=True, color="FFFFFF")
TITLE_FONT = Font(bold=True, size=16, color="111827")
MUTED_FONT = Font(color="6B7280")

# (fill, font colour) per change type - the same palette as the HTML report.
CHANGE_STYLES: dict[ChangeType, tuple[str, str]] = {
    ChangeType.PRICE_UP: ("FDE2E1", "9B1C1C"),
    ChangeType.PRICE_DOWN: ("DCEAFB", "1C4F95"),
    ChangeType.OUT_OF_STOCK: ("FFF1D6", "8A5A00"),
    ChangeType.BACK_IN_STOCK: ("DDF3E4", "1E6B35"),
    ChangeType.NEW: ("ECE9FB", "4A3AA7"),
    ChangeType.REMOVED: ("EEEEEC", "52514E"),
}
UP_FONT = Font(color="B42318", bold=True)
DOWN_FONT = Font(color="1C5CAB", bold=True)

# Column widths leave room for the autofilter button next to each header.
CHANGES_HEADER = [
    ("Change", 15),
    ("Product", 46),
    ("Category", 18),
    ("SKU", 20),
    ("Old price", 13),
    ("New price", 13),
    ("Change, abs", 14),
    ("Change, %", 13),
    ("Stock before", 15),
    ("Stock now", 15),
    ("Alert", 9),
    ("URL", 60),
]
SNAPSHOT_HEADER = [
    ("Product", 48),
    ("Category", 18),
    ("SKU", 20),
    ("Price", 11),
    ("In stock", 11),
    ("Units available", 17),
    ("Rating (1-5)", 15),
    ("URL", 60),
]


SIMULATED_NOTE = (
    "Note: the current run was produced by `price-monitor demo-snapshot` - "
    "its changes are SIMULATED from a real crawl for demonstration."
)


def money_format(currency: str, signed: bool = False) -> str:
    sign = CURRENCY_SIGNS.get(currency, "")
    body = f'"{sign}"#,##0.00' if sign else "#,##0.00"
    return f"+{body};-{body};{body}" if signed else body


def _stock(in_stock: bool | None, count: int | None) -> str:
    if in_stock is None:
        return ""
    if not in_stock:
        return "Out of stock"
    return f"In stock ({count})" if count is not None else "In stock"


def _write_header(ws: Worksheet, columns: list[tuple[str, int]]) -> None:
    for idx, (title, width) in enumerate(columns, start=1):
        cell = ws.cell(row=1, column=idx, value=title)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(vertical="center")
        ws.column_dimensions[get_column_letter(idx)].width = width
    ws.row_dimensions[1].height = 22
    ws.freeze_panes = "A2"


def _finish_table(ws: Worksheet, n_columns: int, n_rows: int) -> None:
    last = get_column_letter(n_columns)
    ws.auto_filter.ref = f"A1:{last}{max(n_rows + 1, 2)}"


def write_excel(diff: DiffResult, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{report_basename(diff)}.xlsx"
    currency = diff.snapshot[0].currency if diff.snapshot else "GBP"

    wb = Workbook()
    summary_ws = wb.active
    assert summary_ws is not None
    summary_ws.title = "Summary"
    _write_summary(summary_ws, diff)
    _write_changes(wb.create_sheet("Changes"), diff, currency)
    _write_snapshot(wb.create_sheet("Snapshot"), diff, currency)

    wb.properties.creator = f"price-monitor {__version__}"
    wb.properties.title = "Competitor price & stock report"
    wb.save(path)
    return path


def _write_summary(ws: Worksheet, diff: DiffResult) -> None:
    ws["A1"] = "Competitor price & stock report"
    ws["A1"].font = TITLE_FONT
    ws.row_dimensions[1].height = 26
    generated = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    ws["A2"] = f"Generated {generated} by price-monitor {__version__}"
    ws["A2"].font = MUTED_FONT

    rows = summary_rows(diff)
    start = 4
    for offset, (label, value) in enumerate(rows):
        label_cell = ws.cell(row=start + offset, column=1, value=label)
        label_cell.font = Font(bold=True)
        value_cell = ws.cell(row=start + offset, column=2, value=value)
        value_cell.alignment = Alignment(horizontal="left")

    if diff.current.is_simulated:
        note_row = start + len(rows) + 1
        note = ws.cell(row=note_row, column=1, value=SIMULATED_NOTE)
        note.font = Font(bold=True, color="8A5A00")
    ws.column_dimensions["A"].width = 24
    ws.column_dimensions["B"].width = 58


def summary_rows(diff: DiffResult) -> list[tuple[str, object]]:
    """Label/value pairs of the Summary sheet (the Google Sheets writer reuses them)."""
    summary = diff.summary
    rows: list[tuple[str, object]] = [
        ("Previous run", _run_text(diff.previous)),
        ("Current run", _run_text(diff.current)),
        ("Alert threshold", f"price change of {diff.threshold_pct}% or more"),
        ("", ""),
        ("Products tracked", len(diff.snapshot)),
    ]
    if summary is not None:
        rows += [(ct.label, summary.count(ct)) for ct in CHANGE_ORDER]
        rows.append(("Alerts", summary.alerts))
        if summary.avg_price_change_pct is not None:
            rows.append(("Average price change", f"{summary.avg_price_change_pct:+.1f}%"))
    return rows


def _run_text(run: Run | None) -> str:
    if run is None:
        return "-"
    kind = "simulated (demo-snapshot)" if run.is_simulated else "real crawl"
    return f"#{run.id} - {run.finished_at:%Y-%m-%d %H:%M} UTC - {kind}"


def changes_rows(diff: DiffResult) -> list[list[object]]:
    """One row per change, in the order of ``CHANGES_HEADER`` (also used by Google Sheets)."""
    rows: list[list[object]] = []
    for c in diff.changes:
        pct = c.price_delta_pct
        rows.append(
            [
                c.type.label,
                c.name,
                c.category,
                c.sku,
                c.old_price,
                c.new_price,
                c.price_delta,
                (pct / 100) if pct is not None else None,
                _stock(c.old_in_stock, c.old_stock_count),
                _stock(c.new_in_stock, c.new_stock_count),
                "Yes" if c.is_alert else None,
                c.url,
            ]
        )
    return rows


def snapshot_rows(diff: DiffResult) -> list[list[object]]:
    """One row per product, in the order of ``SNAPSHOT_HEADER`` (also used by Google Sheets)."""
    return [
        [
            p.name,
            p.category,
            p.sku,
            p.price,
            "Yes" if p.in_stock else "No",
            p.stock_count,
            p.rating,
            p.url,
        ]
        for p in diff.snapshot
    ]


def _write_changes(ws: Worksheet, diff: DiffResult, currency: str) -> None:
    _write_header(ws, CHANGES_HEADER)
    money_fmt, delta_fmt = money_format(currency), money_format(currency, signed=True)
    for row, values in enumerate(changes_rows(diff), start=2):
        for col, value in enumerate(values, start=1):
            cell = ws.cell(row=row, column=col, value=value)
            if col in (5, 6):
                cell.number_format = money_fmt
            elif col == 7:
                cell.number_format = delta_fmt
            elif col == 8:
                cell.number_format = "+0.0%;-0.0%;0.0%"
        ws.cell(row=row, column=12).hyperlink = values[-1]
        ws.cell(row=row, column=12).font = Font(color="1C5CAB", underline="single")

    n = len(diff.changes)
    _finish_table(ws, len(CHANGES_HEADER), n)
    if n == 0:
        return
    last = n + 1
    # Real conditional formatting (not static fills), so it survives sorting/filtering.
    for change_type, (fill, font) in CHANGE_STYLES.items():
        ws.conditional_formatting.add(
            f"A2:A{last}",
            CellIsRule(
                operator="equal",
                formula=[f'"{change_type.label}"'],
                fill=PatternFill("solid", start_color=fill, end_color=fill),
                font=Font(color=font, bold=True),
            ),
        )
    for rng in (f"G2:G{last}", f"H2:H{last}"):
        ws.conditional_formatting.add(
            rng, CellIsRule(operator="greaterThan", formula=["0"], font=UP_FONT)
        )
        ws.conditional_formatting.add(
            rng, CellIsRule(operator="lessThan", formula=["0"], font=DOWN_FONT)
        )
    ws.conditional_formatting.add(
        f"K2:K{last}",
        CellIsRule(operator="equal", formula=['"Yes"'], font=Font(bold=True, color="B42318")),
    )


def _write_snapshot(ws: Worksheet, diff: DiffResult, currency: str) -> None:
    _write_header(ws, SNAPSHOT_HEADER)
    money_fmt = money_format(currency)
    for row, values in enumerate(snapshot_rows(diff), start=2):
        for col, value in enumerate(values, start=1):
            cell = ws.cell(row=row, column=col, value=value)
            if col == 4:
                cell.number_format = money_fmt
        ws.cell(row=row, column=8).hyperlink = values[-1]
        ws.cell(row=row, column=8).font = Font(color="1C5CAB", underline="single")
    n = len(diff.snapshot)
    _finish_table(ws, len(SNAPSHOT_HEADER), n)
    if n:
        ws.conditional_formatting.add(
            f"E2:E{n + 1}",
            CellIsRule(
                operator="equal",
                formula=['"No"'],
                fill=PatternFill("solid", start_color="FFF1D6", end_color="FFF1D6"),
                font=Font(color="8A5A00", bold=True),
            ),
        )
