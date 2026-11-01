"""Google Sheets writer, tested against an in-memory fake of the gspread client (no network)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from price_monitor import cli
from price_monitor.reports import sheets
from price_monitor.reports.csv_export import CHANGE_COLUMNS
from price_monitor.reports.excel import CHANGES_HEADER, SNAPSHOT_HEADER
from price_monitor.reports.sheets import (
    SheetsAccessError,
    SheetsConfigError,
    service_account_email,
    write_sheets,
)

from .test_cli import fake_crawl, invoke  # noqa: F401 - fixture import
from .test_reports import sample_diff

SHEET_ID = "1AbCdEfGhIjKlMnOpQrStUvWxYz"
EMAIL = "monitor@demo-project.iam.gserviceaccount.com"
SECRET = "-----BEGIN PRIVATE KEY-----do-not-print-----END PRIVATE KEY-----"


class FakeWorksheet:
    def __init__(self, title: str, rows: int = 1000, cols: int = 26) -> None:
        self.title, self.row_count, self.col_count = title, rows, cols
        self.values: list[list[Any]] = [["stale"]] * 3
        self.calls: list[str] = []
        self.formats: list[dict[str, Any]] = []

    def clear(self) -> None:
        self.calls.append("clear")
        self.values = []

    def resize(self, rows: int, cols: int) -> None:
        self.calls.append("resize")
        self.row_count, self.col_count = rows, cols

    def update(self, values: list[list[Any]], range_name: str, raw: bool = True) -> None:
        assert range_name == "A1" and raw is True
        assert len(values) <= self.row_count, "rows must fit the grid or Google rejects it"
        assert max(len(r) for r in values) <= self.col_count
        self.calls.append("update")
        self.values = values

    def batch_format(self, formats: list[dict[str, Any]]) -> None:
        self.calls.append("batch_format")
        self.formats = formats


class FakeSpreadsheet:
    def __init__(self, titles: tuple[str, ...] = ()) -> None:
        self.sheets = {t: FakeWorksheet(t) for t in titles}

    def worksheets(self) -> list[FakeWorksheet]:
        return list(self.sheets.values())

    def add_worksheet(self, title: str, rows: int, cols: int) -> FakeWorksheet:
        ws = self.sheets[title] = FakeWorksheet(title, rows, cols)
        ws.values = []
        return ws


class FakeClient:
    def __init__(self, spreadsheet: FakeSpreadsheet, error: Exception | None = None) -> None:
        self.spreadsheet, self.error, self.opened = spreadsheet, error, []

    def open_by_key(self, key: str) -> FakeSpreadsheet:
        self.opened.append(key)
        if self.error:
            raise self.error
        return self.spreadsheet


@pytest.fixture
def key_file(tmp_path: Path) -> Path:
    path = tmp_path / "key.json"
    path.write_text(json.dumps({"client_email": EMAIL, "private_key": SECRET}), encoding="utf-8")
    return path


def run(diff, key_file, client, sheet: str = SHEET_ID) -> str:
    return write_sheets(diff, key_file=key_file, sheet=sheet, client=client)


def test_writes_three_worksheets_with_headers_and_rows(key_file: Path) -> None:
    diff = sample_diff()
    book = FakeSpreadsheet()
    client = FakeClient(book)

    run(diff, key_file, client, sheet=f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit")

    assert client.opened == [SHEET_ID]
    assert list(book.sheets) == ["Summary", "Changes", "Snapshot"]
    changes, snapshot = book.sheets["Changes"].values, book.sheets["Snapshot"].values
    assert changes[0] == [title for title, _ in CHANGES_HEADER]
    assert snapshot[0] == [title for title, _ in SNAPSHOT_HEADER]
    assert len(changes) == 1 + len(diff.changes) and len(snapshot) == 1 + len(diff.snapshot)
    summary = dict(row[:2] for row in book.sheets["Summary"].values if len(row) == 2)
    assert summary["Products tracked"] == 4
    assert summary["Current run"].startswith("#2")
    assert len(CHANGE_COLUMNS) != len(CHANGES_HEADER)  # Excel/Sheets columns, not the raw CSV ones


def test_prices_are_numbers_and_empty_cells_are_empty_strings(key_file: Path) -> None:
    book = FakeSpreadsheet()
    run(sample_diff(), key_file, FakeClient(book))

    header, *rows = book.sheets["Changes"].values
    up = next(r for r in rows if r[3] == "UP")
    old, new, delta, pct = (
        up[header.index(h)] for h in ("Old price", "New price", "Change, abs", "Change, %")
    )
    assert (old, new, delta) == (10.0, 12.0, 2.0)
    assert all(isinstance(v, float) for v in (old, new, delta, pct))
    assert pct == pytest.approx(0.2)
    removed = next(r for r in rows if r[3] == "GONE")
    assert removed[header.index("New price")] == ""  # None -> empty, not the text "None"

    prices = [r[3] for r in book.sheets["Snapshot"].values[1:]]
    assert prices and all(isinstance(p, float) for p in prices)
    formats = {f["range"]: f["format"] for f in book.sheets["Changes"].formats}
    assert formats["H2:H6"]["numberFormat"]["type"] == "PERCENT"
    assert formats["A1:L1"]["textFormat"]["bold"] is True


def test_second_run_replaces_contents_and_reuses_worksheets(key_file: Path) -> None:
    book = FakeSpreadsheet()
    client = FakeClient(book)
    run(sample_diff(), key_file, client)
    first = {t: ws for t, ws in book.sheets.items()}

    run(sample_diff(), key_file, client)

    assert book.sheets == first  # same worksheet objects, nothing duplicated
    for ws in book.sheets.values():
        assert ws.calls.count("clear") == 1  # created on run 1, cleared on run 2
        assert ws.values[0][0] != "stale"
    assert book.sheets["Changes"].calls.count("update") == 2  # exactly one write per run
    assert len(book.sheets["Changes"].values) == 1 + len(sample_diff().changes)


def test_existing_worksheets_are_cleared_and_grown_when_too_small(key_file: Path) -> None:
    book = FakeSpreadsheet(("Summary", "Changes", "Snapshot", "Notes"))
    book.sheets["Snapshot"].row_count = 2
    run(sample_diff(), key_file, FakeClient(book))

    snapshot = book.sheets["Snapshot"]
    assert snapshot.calls == ["clear", "resize", "update", "batch_format"]
    assert book.sheets["Changes"].calls == ["clear", "update", "batch_format"]
    assert book.sheets["Notes"].calls == []  # foreign worksheets are left alone
    assert len(snapshot.values) == 1 + len(sample_diff().snapshot)
    assert snapshot.row_count >= len(snapshot.values)


def test_missing_settings_name_the_variables(key_file: Path) -> None:
    with pytest.raises(SheetsConfigError, match="PM_GOOGLE_SERVICE_ACCOUNT_FILE and PM_GOOGLE"):
        write_sheets(sample_diff(), key_file=None, sheet=None)
    with pytest.raises(SheetsConfigError, match="PM_GOOGLE_SHEET_ID"):
        write_sheets(sample_diff(), key_file=key_file, sheet="")


def test_bad_key_file_never_echoes_its_contents(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(SECRET, encoding="utf-8")
    with pytest.raises(SheetsConfigError) as info:
        service_account_email(bad)
    assert "do-not-print" not in str(info.value)
    with pytest.raises(SheetsConfigError, match="Cannot read"):
        service_account_email(tmp_path / "missing.json")


def test_no_access_tells_to_share_with_the_service_account(key_file: Path) -> None:
    # gspread's open_by_key raises the builtin PermissionError on HTTP 403.
    client = FakeClient(FakeSpreadsheet(), error=PermissionError())
    with pytest.raises(SheetsAccessError) as info:
        run(sample_diff(), key_file, client)
    message = str(info.value)
    assert EMAIL in message and "Share the sheet" in message
    assert "do-not-print" not in message


def test_no_access_with_real_gspread_error_types(key_file: Path) -> None:
    gspread_exceptions = pytest.importorskip("gspread.exceptions")

    class Response:
        status_code = 403
        text = ""

        def json(self) -> dict:
            return {"error": {"code": 403, "message": "denied", "status": "PERMISSION_DENIED"}}

    client = FakeClient(FakeSpreadsheet(), error=gspread_exceptions.APIError(Response()))
    with pytest.raises(SheetsAccessError, match=EMAIL):
        run(sample_diff(), key_file, client)


# --------------------------------------------------------------------------- CLI


@pytest.fixture
def stored_runs(fake_crawl) -> None:  # noqa: F811
    assert invoke("scrape").exit_code == 0
    assert invoke("demo-snapshot").exit_code == 0


def test_cli_sheets_without_settings_exits_2(stored_runs, tmp_path: Path) -> None:
    result = invoke("report", "-f", "sheets", "-o", str(tmp_path))
    assert result.exit_code == 2
    assert "PM_GOOGLE_SERVICE_ACCOUNT_FILE" in result.output


def test_cli_sheets_writes_through_the_fake_client(
    stored_runs, key_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    book = FakeSpreadsheet()
    monkeypatch.setattr(sheets, "_connect", lambda path, timeout: FakeClient(book))
    monkeypatch.setenv("PM_GOOGLE_SERVICE_ACCOUNT_FILE", str(key_file))
    monkeypatch.setenv("PM_GOOGLE_SHEET_ID", SHEET_ID)

    result = invoke("report", "-f", "csv,sheets", "-o", str(tmp_path))

    assert result.exit_code == 0, result.output
    assert list(book.sheets) == ["Summary", "Changes", "Snapshot"]
    assert len(list(tmp_path.glob("*.csv"))) == 2
    assert SECRET not in result.output


def test_cli_sheets_no_access_prints_the_email_and_exits_1(
    stored_runs, key_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = FakeClient(FakeSpreadsheet(), error=PermissionError())
    monkeypatch.setattr(sheets, "_connect", lambda path, timeout: client)
    monkeypatch.setenv("PM_GOOGLE_SERVICE_ACCOUNT_FILE", str(key_file))
    monkeypatch.setenv("PM_GOOGLE_SHEET_ID", SHEET_ID)

    result = invoke("report", "-f", "sheets", "-o", str(tmp_path))

    assert result.exit_code == 1
    assert EMAIL in result.output and "do-not-print" not in result.output


def test_cli_unknown_format_next_to_sheets_still_exits_2(stored_runs) -> None:
    assert invoke("report", "-f", "sheets,pdf").exit_code == 2
    assert cli.SHEETS_FORMAT == "sheets"
