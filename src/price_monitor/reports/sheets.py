"""Google Sheets export: the Summary, Changes and Snapshot sheets of the Excel report.

Needs the optional ``sheets`` extra (``pip install "price-monitor[sheets]"``); ``gspread`` is
imported only when a sheet is really written. Authentication is a service account key file:
the spreadsheet must be shared (as Editor) with the ``client_email`` stored in that file.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from price_monitor import __version__
from price_monitor.diff import DiffResult
from price_monitor.reports.excel import (
    CHANGES_HEADER,
    SIMULATED_NOTE,
    SNAPSHOT_HEADER,
    changes_rows,
    money_format,
    snapshot_rows,
    summary_rows,
)

SUMMARY_SHEET, CHANGES_SHEET, SNAPSHOT_SHEET = "Summary", "Changes", "Snapshot"
_URL_ID = re.compile(r"/spreadsheets/(?:u/\d+/)?d/([A-Za-z0-9_-]+)")
_BARE_ID = re.compile(r"[A-Za-z0-9_-]{10,}")


class SheetsConfigError(ValueError):
    """Settings are missing or unusable (the CLI reports it as invalid input, exit code 2)."""


class SheetsAccessError(RuntimeError):
    """Google refused access to the spreadsheet or the API call failed."""


def sheet_id_from(value: str) -> str:
    """Spreadsheet ID from a bare ID or from a browser URL.

    >>> sheet_id_from("1AbC_dEf-ghIJkLmNoPqRsTuVwXyZ0123456789")
    '1AbC_dEf-ghIJkLmNoPqRsTuVwXyZ0123456789'
    >>> sheet_id_from("https://docs.google.com/spreadsheets/d/1AbCdEfGhIjK/edit#gid=0")
    '1AbCdEfGhIjK'
    >>> sheet_id_from("not a sheet")
    Traceback (most recent call last):
      ...
    price_monitor.reports.sheets.SheetsConfigError: PM_GOOGLE_SHEET_ID is neither a spreadsheet ID nor a Google Sheets URL.
    """  # noqa: E501
    text = value.strip()
    match = _URL_ID.search(text)
    if match:
        return match.group(1)
    if _BARE_ID.fullmatch(text):
        return text
    raise SheetsConfigError(
        "PM_GOOGLE_SHEET_ID is neither a spreadsheet ID nor a Google Sheets URL."
    )


def _cell(value: object) -> object:
    """JSON-friendly cell: prices stay numbers (Decimal -> float), None -> empty.

    >>> _cell(Decimal("12.50")), _cell(None), _cell(7), _cell("x")
    (12.5, '', 7, 'x')
    """
    if value is None:
        return ""
    if isinstance(value, Decimal):
        # JSON has no decimal type; Sheets stores every number as a double anyway.
        return float(value)
    return value


def _matrix(header: list[tuple[str, int]], rows: list[list[object]]) -> list[list[object]]:
    return [[title for title, _ in header], *([_cell(v) for v in row] for row in rows)]


def _summary_matrix(diff: DiffResult) -> list[list[object]]:
    generated = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    matrix: list[list[object]] = [
        ["Competitor price & stock report"],
        [f"Generated {generated} by price-monitor {__version__}"],
        [],
        *([label, _cell(value)] for label, value in summary_rows(diff)),
    ]
    if diff.current.is_simulated:
        matrix += [[], [SIMULATED_NOTE]]
    return matrix


def service_account_email(key_file: Path) -> str:
    """``client_email`` from the key file. Nothing else from the file is ever read out."""
    try:
        data = json.loads(key_file.read_text(encoding="utf-8"))
        email = data["client_email"]
    except OSError as exc:
        raise SheetsConfigError(
            f"Cannot read the service account key file {key_file}: {exc.strerror}."
        ) from exc
    except (ValueError, KeyError, TypeError) as exc:
        raise SheetsConfigError(
            f"{key_file} is not a Google service account key (JSON with a client_email)."
        ) from exc
    if not isinstance(email, str) or not email:
        raise SheetsConfigError(f"{key_file} has no usable client_email.")
    return email


def _connect(key_file: Path, timeout: float) -> Any:
    try:
        import gspread
    except ImportError as exc:
        raise SheetsConfigError(
            'Google Sheets output needs the optional package: pip install "price-monitor[sheets]"'
        ) from exc
    try:
        client = gspread.service_account(filename=key_file)
    except (ValueError, KeyError) as exc:  # malformed key; the message could quote the key
        raise SheetsConfigError(f"{key_file} is not a valid service account key.") from exc
    client.set_timeout(timeout)
    return client


def _api_error_types() -> tuple[type[Exception], ...]:
    try:
        from gspread.exceptions import APIError, SpreadsheetNotFound
    except ImportError:
        return (PermissionError,)
    return (PermissionError, SpreadsheetNotFound, APIError)


def _access_error(exc: Exception, email: str) -> SheetsAccessError:
    code = getattr(exc, "code", None)
    if code == 429:
        return SheetsAccessError(
            "Google Sheets API quota exceeded (too many requests). Wait a minute and run again."
        )
    if code is None or code in (403, 404):
        return SheetsAccessError(
            "The service account has no access to this spreadsheet (or it does not exist). "
            f"Share the sheet as Editor with {email} and check PM_GOOGLE_SHEET_ID."
        )
    return SheetsAccessError(f"Google Sheets API error {code}: {getattr(exc, 'error', {})}")


def _replace(spreadsheet: Any, existing: dict[str, Any], title: str, matrix: list[list[object]]):
    """Replace the contents of one worksheet (created when missing) with ``matrix``."""
    n_rows = max(len(matrix), 1)
    n_cols = max((len(row) for row in matrix), default=1)
    ws = existing.get(title)
    if ws is None:
        ws = spreadsheet.add_worksheet(title, rows=n_rows, cols=n_cols)
    else:
        ws.clear()
        if ws.row_count < n_rows or ws.col_count < n_cols:
            ws.resize(rows=max(ws.row_count, n_rows), cols=max(ws.col_count, n_cols))
    # raw=True (the default): numbers stay numbers, text is never re-parsed as a formula.
    ws.update(matrix, "A1", raw=True)
    return ws


def write_sheets(
    diff: DiffResult,
    *,
    key_file: Path | None,
    sheet: str | None,
    timeout: float = 15.0,
    client: Any = None,
) -> str:
    """Write Summary, Changes and Snapshot into the spreadsheet; return a short description.

    ``client`` is a ``gspread.Client``; it is created from ``key_file`` when not given.
    """
    missing = [
        name
        for name, value in (
            ("PM_GOOGLE_SERVICE_ACCOUNT_FILE", key_file),
            ("PM_GOOGLE_SHEET_ID", sheet),
        )
        if not value
    ]
    if missing or key_file is None or sheet is None:
        raise SheetsConfigError(
            f"Google Sheets output needs {' and '.join(missing)} (see the README, Google Sheets)."
        )
    sheet_id = sheet_id_from(sheet)
    email = service_account_email(key_file)
    if client is None:
        client = _connect(key_file, timeout)

    currency = diff.snapshot[0].currency if diff.snapshot else "GBP"
    api_errors = _api_error_types()
    try:
        spreadsheet = client.open_by_key(sheet_id)
        existing = {ws.title: ws for ws in spreadsheet.worksheets()}
        _replace(spreadsheet, existing, SUMMARY_SHEET, _summary_matrix(diff))
        changes_ws = _replace(
            spreadsheet, existing, CHANGES_SHEET, _matrix(CHANGES_HEADER, changes_rows(diff))
        )
        snapshot_ws = _replace(
            spreadsheet, existing, SNAPSHOT_SHEET, _matrix(SNAPSHOT_HEADER, snapshot_rows(diff))
        )
        _format(changes_ws, len(CHANGES_HEADER), len(diff.changes), currency, changes=True)
        _format(snapshot_ws, len(SNAPSHOT_HEADER), len(diff.snapshot), currency, changes=False)
    except api_errors as exc:
        raise _access_error(exc, email) from exc
    return f"Google Sheet {sheet_id} ({SUMMARY_SHEET}, {CHANGES_SHEET}, {SNAPSHOT_SHEET})"


def _format(ws: Any, n_columns: int, n_rows: int, currency: str, *, changes: bool) -> None:
    """One ``batch_format`` call: bold header plus money / percent number formats."""
    last_col = chr(ord("A") + n_columns - 1)
    formats: list[dict[str, Any]] = [
        {"range": f"A1:{last_col}1", "format": {"textFormat": {"bold": True}}}
    ]
    if n_rows:
        end = n_rows + 1

        def number(pattern: str, kind: str = "NUMBER") -> dict[str, Any]:
            return {"numberFormat": {"type": kind, "pattern": pattern}}

        if changes:
            formats += [
                {"range": f"E2:F{end}", "format": number(money_format(currency))},
                {"range": f"G2:G{end}", "format": number(money_format(currency, signed=True))},
                {"range": f"H2:H{end}", "format": number("+0.0%;-0.0%;0.0%", "PERCENT")},
            ]
        else:
            formats.append({"range": f"D2:D{end}", "format": number(money_format(currency))})
    ws.batch_format(formats)
