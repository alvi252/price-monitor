"""Render one worksheet of an .xlsx file as an Excel-looking HTML page.

Used only to produce the README screenshot of the Excel report without needing
Excel or LibreOffice. Everything shown is read back from the real file with
openpyxl: cell values, number formats, column widths, header styles, hyperlink
styling, the autofilter range and the ``cellIs`` conditional-formatting rules
(evaluated here the way Excel would evaluate them).

Usage:
    python scripts/xlsx_preview.py reports/price-report-run1-vs-run2.xlsx --sheet Changes \
        -o preview.html
"""

from __future__ import annotations

import argparse
import re
from decimal import Decimal
from html import escape
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.cell.cell import Cell
from openpyxl.styles.colors import Color
from openpyxl.utils import get_column_letter, range_boundaries
from openpyxl.worksheet.worksheet import Worksheet

_NUMBER_RE = re.compile(r"[#0,]+(?:\.0+)?%?")


def format_number(value: float | int | Decimal, number_format: str) -> str:
    """Apply the subset of Excel number formats that the report uses.

    >>> format_number(1234.5, '"£"#,##0.00')
    '£1,234.50'
    >>> format_number(-2.5, '+"£"#,##0.00;-"£"#,##0.00;"£"#,##0.00')
    '-£2.50'
    >>> format_number(0.123, '+0.0%;-0.0%;0.0%')
    '+12.3%'
    >>> format_number(7, 'General')
    '7'
    """
    if number_format in ("General", "") or isinstance(value, bool):
        return str(value)
    sections = number_format.split(";")
    number = float(value)
    if len(sections) >= 2 and number < 0:
        section, number = sections[1], abs(number)
    elif len(sections) >= 3 and number == 0:
        section = sections[2]
    else:
        section = sections[0]
    literal = section.replace('"', "")
    match = _NUMBER_RE.search(literal)
    if match is None:
        return str(value)
    pattern = match.group()
    if pattern.endswith("%"):
        number *= 100
    decimals = len(pattern.split(".")[1].rstrip("%")) if "." in pattern else 0
    body = f"{number:,.{decimals}f}" if "," in pattern else f"{number:.{decimals}f}"
    if pattern.endswith("%"):
        body += "%"
    return literal[: match.start()] + body + literal[match.end() :]


def _hex(color: Color | None) -> str | None:
    if color is None or color.type != "rgb" or not isinstance(color.rgb, str):
        return None
    rgb = color.rgb[-6:]
    return None if rgb == "000000" and color.rgb.startswith("00") else f"#{rgb}"


def _rule_matches(value: object, operator: str, formulas: list[str]) -> bool:
    def literal(text: str) -> object:
        text = text.strip()
        if text.startswith('"') and text.endswith('"'):
            return text[1:-1]
        return float(text)

    if value is None or value == "":
        return False
    targets = [literal(f) for f in formulas]
    numeric = isinstance(value, int | float | Decimal) and not isinstance(value, bool)
    if numeric and all(isinstance(t, float) for t in targets):
        v, t = float(value), [float(x) for x in targets]  # type: ignore[arg-type]
        return {
            "greaterThan": lambda: v > t[0],
            "lessThan": lambda: v < t[0],
            "greaterThanOrEqual": lambda: v >= t[0],
            "lessThanOrEqual": lambda: v <= t[0],
            "equal": lambda: v == t[0],
            "notEqual": lambda: v != t[0],
            "between": lambda: t[0] <= v <= t[1],
        }.get(operator, lambda: False)()
    if operator == "equal":
        return str(value).lower() == str(targets[0]).lower()
    if operator == "notEqual":
        return str(value).lower() != str(targets[0]).lower()
    return False


def _conditional_styles(ws: Worksheet) -> dict[str, dict[str, str]]:
    """Cell coordinate -> CSS overrides from matching ``cellIs`` rules."""
    styles: dict[str, dict[str, str]] = {}
    for cf_range in ws.conditional_formatting:
        rules = sorted(cf_range.rules, key=lambda r: r.priority)
        for bounds in str(cf_range.sqref).split():
            min_col, min_row, max_col, max_row = range_boundaries(bounds)
            for row in ws.iter_rows(min_row, max_row, min_col, max_col):
                for cell in row:
                    for rule in rules:
                        if rule.type != "cellIs" or rule.dxf is None:
                            continue
                        if not _rule_matches(cell.value, rule.operator, list(rule.formula)):
                            continue
                        css = styles.setdefault(cell.coordinate, {})
                        dxf = rule.dxf
                        if dxf.fill is not None:
                            bg = _hex(dxf.fill.bgColor) or _hex(dxf.fill.fgColor)
                            if bg:
                                css.setdefault("background", bg)
                        if dxf.font is not None:
                            if dxf.font.color is not None and _hex(dxf.font.color):
                                css.setdefault("color", _hex(dxf.font.color) or "")
                            if dxf.font.b:
                                css.setdefault("font-weight", "700")
                        if rule.stopIfTrue:
                            break
    return styles


def _cell_css(cell: Cell) -> dict[str, str]:
    css: dict[str, str] = {}
    fill = cell.fill
    if fill is not None and fill.fill_type == "solid":
        bg = _hex(fill.fgColor)
        if bg:
            css["background"] = bg
    font = cell.font
    if font is not None:
        color = _hex(font.color) if font.color is not None else None
        if color:
            css["color"] = color
        if font.b:
            css["font-weight"] = "700"
        if font.u:
            css["text-decoration"] = "underline"
        if font.sz and float(font.sz) != 11:
            css["font-size"] = f"{float(font.sz) * 4 / 3:.0f}px"
    return css


def render_sheet(path: Path, sheet: str, max_rows: int = 60, file_label: str | None = None) -> str:
    wb = load_workbook(path)
    ws = wb[sheet]
    conditional = _conditional_styles(ws)
    max_col = ws.max_column
    last_row = min(ws.max_row, max_rows)
    filter_cols: set[int] = set()
    if ws.auto_filter.ref:
        min_c, min_r, max_c, _ = range_boundaries(ws.auto_filter.ref)
        filter_cols = set(range(min_c, max_c + 1)) if min_r == 1 else set()

    widths = []
    for col in range(1, max_col + 1):
        dim = ws.column_dimensions[get_column_letter(col)]
        widths.append(round((dim.width or 8.43) * 7 + 5))

    # An explicit table width makes the browser honour the Excel column widths
    # (table-layout: fixed), so long text is clipped at the column edge like in Excel.
    total = 42 + sum(widths)
    out: list[str] = [f'<table class="grid" style="width:{total}px"><colgroup>']
    out.append('<col style="width:42px">')
    out += [f'<col style="width:{w}px">' for w in widths]
    out.append('</colgroup><thead><tr><th class="corner"></th>')
    out += [f"<th>{get_column_letter(c)}</th>" for c in range(1, max_col + 1)]
    out.append("</tr></thead><tbody>")
    for r in range(1, last_row + 1):
        height = ws.row_dimensions[r].height
        style = f' style="height:{round(height * 4 / 3)}px"' if height else ""
        out.append(f'<tr{style}><th class="rownum">{r}</th>')
        for c in range(1, max_col + 1):
            cell = ws.cell(row=r, column=c)
            css = _cell_css(cell) | conditional.get(cell.coordinate, {})
            value = cell.value
            numeric = isinstance(value, int | float | Decimal) and not isinstance(value, bool)
            text = format_number(value, cell.number_format) if numeric else (value or "")
            classes = ["num"] if numeric else []
            if r == 1 and c in filter_cols:
                classes.append("filter")
            attr = f' class="{" ".join(classes)}"' if classes else ""
            style_attr = "".join(f"{k}:{v};" for k, v in css.items())
            style_attr = f' style="{style_attr}"' if style_attr else ""
            out.append(f"<td{attr}{style_attr}>{escape(str(text))}</td>")
        out.append("</tr>")
    out.append("</tbody></table>")

    tabs = "".join(
        f'<span class="tab{" active" if name == sheet else ""}">{escape(name)}</span>'
        for name in wb.sheetnames
    )
    label = escape(file_label or path.name)
    return _PAGE.format(label=label, sheet=escape(sheet), table="".join(out), tabs=tabs)


_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{label}</title>
<style>
  body {{ margin: 0; background: #e9ebee; font: 14.5px Calibri, Carlito, "Segoe UI", Lato,
         Arial, sans-serif; color: #111; }}
  .window {{ margin: 0; background: #fff; }}
  .titlebar {{ background: #1e6b45; color: #fff; padding: 9px 16px; font: 600 13.5px "Segoe UI",
              Lato, Arial, sans-serif; display: flex; justify-content: space-between; }}
  .titlebar span {{ opacity: .85; font-weight: 400; }}
  .viewport {{ overflow: hidden; border-top: 1px solid #c8c8c8; }}
  table.grid {{ border-collapse: collapse; table-layout: fixed; }}
  .grid th {{ background: #f3f3f3; color: #555; font-weight: 400; font-size: 12.5px;
             border: 1px solid #d4d4d4; height: 22px; }}
  .grid th.rownum {{ text-align: center; }}
  .grid td {{ border: 1px solid #e1e1e1; padding: 2px 6px; white-space: nowrap; overflow: hidden;
             text-overflow: clip; height: 21px; }}
  .grid td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .grid td.filter {{ position: relative; padding-right: 22px; }}
  .grid td.filter::after {{ content: "▾"; position: absolute; right: 3px; top: 50%;
      transform: translateY(-50%); width: 15px; height: 15px; line-height: 14px; font-size: 11px;
      text-align: center; color: #444; background: #fff; border: 1px solid #b9b9b9;
      border-radius: 2px; font-weight: 400; }}
  .tabs {{ display: flex; gap: 2px; padding: 0 12px; background: #f3f3f3;
          border-top: 1px solid #c8c8c8; font: 13px "Segoe UI", Lato, Arial, sans-serif; }}
  .tab {{ padding: 6px 16px; color: #444; }}
  .tab.active {{ background: #fff; color: #1e6b45; font-weight: 700;
                border-bottom: 2px solid #1e6b45; }}
</style></head>
<body><div class="window">
  <div class="titlebar">{label} <span>sheet: {sheet} - rendered from the file by xlsx_preview.py
  </span></div>
  <div class="viewport">{table}</div>
  <div class="tabs">{tabs}</div>
</div></body></html>
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("xlsx", type=Path)
    parser.add_argument("--sheet", default="Changes")
    parser.add_argument("--max-rows", type=int, default=60)
    parser.add_argument("-o", "--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(render_sheet(args.xlsx, args.sheet, args.max_rows), encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
