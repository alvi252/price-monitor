"""Regenerate the README screenshots from real output files.

Requires the optional dependency group: ``pip install -e ".[screenshots]"`` and a
Chromium build (``playwright install chromium`` or pass ``--chromium PATH``).

    python scripts/capture_screenshots.py \
        --html reports/price-report-run1-vs-run2.html \
        --xlsx reports/price-report-run1-vs-run2.xlsx \
        --console-log assets/run-output.ansi \
        --out assets/screenshots

The console screenshot is made from the captured ANSI output of a real
``price-monitor run`` (see README), converted to SVG with rich.
"""

from __future__ import annotations

import argparse
import io
import os
import re
import sys
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright
from rich.console import Console
from rich.text import Text

sys.path.insert(0, str(Path(__file__).resolve().parent))
from xlsx_preview import render_sheet

VIEWPORT = {"width": 1440, "height": 900}


# Cursor show/hide and other non-colour control sequences emitted around progress bars.
_NON_SGR_CSI = re.compile(r"\x1b\[[0-9;?]*[A-La-ln-z]")


def console_svg(ansi_log: Path, title: str, width: int) -> str:
    raw = _NON_SGR_CSI.sub("", ansi_log.read_text(encoding="utf-8"))
    text = Text.from_ansi(raw.rstrip("\n"))
    console = Console(record=True, width=width, file=io.StringIO(), force_terminal=True)
    console.print(text, soft_wrap=True)
    return console.export_svg(title=title)


def main() -> None:
    parser = argparse.ArgumentParser(description="Capture README screenshots.")
    parser.add_argument("--html", type=Path, required=True, help="HTML report")
    parser.add_argument("--xlsx", type=Path, required=True, help="Excel report")
    parser.add_argument("--sheet", default="Changes")
    parser.add_argument("--console-log", type=Path, required=True, help="ANSI output of a run")
    parser.add_argument("--console-title", default="price-monitor run --simulate-changes")
    parser.add_argument("--console-width", type=int, default=132)
    parser.add_argument("--out", type=Path, default=Path("assets/screenshots"))
    parser.add_argument(
        "--chromium",
        default=os.environ.get("PM_CHROMIUM_EXECUTABLE"),
        help="Path to a Chromium executable (default: Playwright's bundled browser).",
    )
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp, sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=args.chromium)
        page = browser.new_page(viewport=VIEWPORT, device_scale_factor=1)

        # 1. HTML report, full page.
        page.goto(args.html.resolve().as_uri())
        page.wait_for_load_state("networkidle")
        report_png = args.out / "pm-1-report.png"
        page.screenshot(path=str(report_png), full_page=True)

        # 2. Excel report rendered from the real .xlsx.
        preview = Path(tmp) / "xlsx-preview.html"
        preview.write_text(render_sheet(args.xlsx, args.sheet), encoding="utf-8")
        page.goto(preview.as_uri())
        excel_png = args.out / "pm-2-excel.png"
        page.locator(".window").screenshot(path=str(excel_png))

        # 3. Terminal output of `price-monitor run`, via rich's SVG export.
        svg = console_svg(args.console_log, args.console_title, args.console_width)
        holder = Path(tmp) / "console.html"
        holder.write_text(
            '<!doctype html><html><head><meta charset="utf-8"><style>'
            "body{margin:0;padding:28px;background:#e9ebee;display:inline-block}"
            "svg{display:block;width:1240px;height:auto}</style></head>"
            f"<body>{svg}</body></html>",
            encoding="utf-8",
        )
        page.goto(holder.as_uri())
        page.wait_for_load_state("networkidle")
        console_png = args.out / "pm-3-console.png"
        page.locator("body").screenshot(path=str(console_png))

        browser.close()

    for path in (report_png, excel_png, console_png):
        print(path)


if __name__ == "__main__":
    main()
