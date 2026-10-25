"""Console notifier: prints alerts as a rich table."""

from __future__ import annotations

from rich import box
from rich.console import Console
from rich.table import Table

from price_monitor.diff import DiffResult
from price_monitor.models import Change, ChangeType
from price_monitor.notifiers.base import Notifier, NotifyResult
from price_monitor.reports.formatting import money, signed_pct

STYLES = {
    ChangeType.PRICE_UP: "bold red",
    ChangeType.PRICE_DOWN: "bold blue",
    ChangeType.OUT_OF_STOCK: "bold yellow",
    ChangeType.BACK_IN_STOCK: "bold green",
    ChangeType.NEW: "bold magenta",
    ChangeType.REMOVED: "dim",
}


def _short_stock(in_stock: bool | None, count: int | None) -> str:
    """
    >>> _short_stock(True, 3), _short_stock(False, 0), _short_stock(True, None)
    ('3 pcs', 'sold out', 'in stock')
    """
    if not in_stock:
        return "sold out"
    return f"{count} pcs" if count is not None else "in stock"


def changes_table(changes: list[Change], *, title: str) -> Table:
    table = Table(
        title=title, title_justify="left", header_style="bold", box=box.SIMPLE_HEAD, expand=False
    )
    table.add_column("Change", no_wrap=True)
    table.add_column("Product", max_width=44, overflow="ellipsis", no_wrap=True)
    table.add_column("Category", no_wrap=True)
    table.add_column("Was", justify="right", no_wrap=True, min_width=7)
    table.add_column("Now", justify="right", no_wrap=True, min_width=7)
    table.add_column("%", justify="right", no_wrap=True, min_width=6)
    table.add_column("Stock", no_wrap=True, min_width=18)
    for c in changes:
        style = STYLES[c.type]
        pct = signed_pct(c.price_delta_pct) if c.is_price_change else ""
        stock = (
            f"{_short_stock(c.old_in_stock, c.old_stock_count)} -> "
            f"{_short_stock(c.new_in_stock, c.new_stock_count)}"
            if c.type in (ChangeType.OUT_OF_STOCK, ChangeType.BACK_IN_STOCK)
            else ""
        )
        table.add_row(
            f"[{style}]{c.type.label}[/]",
            c.name,
            c.category,
            money(c.old_price, c.currency),
            money(c.new_price, c.currency),
            f"[{style}]{pct}[/]" if pct else "",
            stock,
        )
    return table


class ConsoleNotifier(Notifier):
    name = "console"

    def __init__(self, console: Console) -> None:
        self.console = console

    def send(self, diff: DiffResult) -> NotifyResult:
        alerts = diff.alerts
        if not alerts:
            self.console.print("[green]No alerts[/] - nothing crossed the alert threshold.")
            return NotifyResult(self.name, False, "no alerts")
        title = f"Alerts: {len(alerts)} (price moves >= {diff.threshold_pct}% and stock changes)"
        self.console.print(changes_table(alerts, title=title))
        return NotifyResult(self.name, True, f"{len(alerts)} alert(s) printed")
