"""The README's Excel screenshot is rendered by scripts/xlsx_preview.py.

These tests check that the preview really reflects the workbook (values, number
formats and conditional formatting), so the screenshot cannot drift from the file.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from price_monitor.reports.excel import write_excel

from .test_reports import sample_diff

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "xlsx_preview.py"
spec = importlib.util.spec_from_file_location("xlsx_preview", SCRIPT)
assert spec is not None and spec.loader is not None
xlsx_preview = importlib.util.module_from_spec(spec)
spec.loader.exec_module(xlsx_preview)


@pytest.mark.parametrize(
    ("value", "fmt", "expected"),
    [
        (1234.5, '"£"#,##0.00', "£1,234.50"),
        (2.4, '+"£"#,##0.00;-"£"#,##0.00;"£"#,##0.00', "+£2.40"),
        (-12.05, '+"£"#,##0.00;-"£"#,##0.00;"£"#,##0.00', "-£12.05"),
        (-0.212, "+0.0%;-0.0%;0.0%", "-21.2%"),
        (0, "+0.0%;-0.0%;0.0%", "0.0%"),
        (5, "General", "5"),
    ],
)
def test_number_formats(value: float, fmt: str, expected: str) -> None:
    assert xlsx_preview.format_number(value, fmt) == expected


def test_preview_reflects_workbook_content(tmp_path: Path) -> None:
    path = write_excel(sample_diff(), tmp_path)
    html = xlsx_preview.render_sheet(path, "Changes")

    assert "Rising &lt;Book&gt;" in html
    assert "-25.0%" in html and "£20.00" in html
    # conditional formatting from the file: "Price down" cell gets the blue fill + font
    assert 'style="background:#DCEAFB;color:#1C4F95;font-weight:700;">Price down<' in html
    assert '<span class="tab active">Changes</span>' in html
