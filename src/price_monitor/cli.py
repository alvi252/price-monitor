"""Command line interface: ``price-monitor scrape | demo-snapshot | diff | report | run | runs``."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Annotated

import typer
from rich import box
from rich.console import Console
from rich.logging import RichHandler
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn
from rich.table import Table

from price_monitor import __version__
from price_monitor.config import Settings
from price_monitor.demo import simulate_next_snapshot
from price_monitor.diff import CHANGE_ORDER, DiffResult, build_diff
from price_monitor.models import Run
from price_monitor.notifiers import build_notifiers
from price_monitor.notifiers.console import changes_table
from price_monitor.reports import FORMATS, write_reports
from price_monitor.scraper import CategoryNotFoundError, crawl_site
from price_monitor.scraper.http import FetchError
from price_monitor.storage import Storage

app = typer.Typer(
    help="Competitor price & stock monitor: scrape, store snapshots, detect changes, report.",
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode="rich",
)
console = Console(highlight=False)
err_console = Console(stderr=True, highlight=False)

ThresholdOpt = Annotated[
    float | None,
    typer.Option(
        "--threshold", "-t", min=0, help="Alert threshold, % (default: PM_ALERT_THRESHOLD_PCT)."
    ),
]
OutOpt = Annotated[
    Path | None, typer.Option("--out", "-o", help="Report directory (default: PM_REPORTS_DIR).")
]
FormatsOpt = Annotated[str, typer.Option("--format", "-f", help="Comma-separated: html,xlsx,csv.")]
ALL_FORMATS = ",".join(FORMATS)


def _settings(ctx: typer.Context) -> Settings:
    settings = ctx.obj
    if not isinstance(settings, Settings):  # pragma: no cover - set by the callback
        raise RuntimeError("Settings were not initialised")
    return settings


def _version(value: bool) -> None:
    if value:
        console.print(f"price-monitor {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    ctx: typer.Context,
    db: Annotated[
        Path | None, typer.Option("--db", help="SQLite file (default: PM_DB_PATH).")
    ] = None,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Show debug logs.")] = False,
    version: Annotated[
        bool, typer.Option("--version", callback=_version, is_eager=True, help="Show version.")
    ] = False,
) -> None:
    logging.basicConfig(
        level=logging.INFO if verbose else logging.WARNING,
        format="%(message)s",
        handlers=[RichHandler(console=err_console, show_path=False, rich_tracebacks=True)],
        force=True,
    )
    # httpx logs full request URLs at INFO level; the Telegram URL contains the bot token.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    settings = Settings()
    if db is not None:
        settings = settings.model_copy(update={"db_path": db})
    ctx.obj = settings


# --------------------------------------------------------------------------- helpers


def _run_label(run: Run) -> str:
    kind = "[yellow]simulated[/]" if run.is_simulated else "real crawl"
    return f"run #{run.id} ({run.finished_at:%Y-%m-%d %H:%M} UTC, {kind})"


def _do_scrape(settings: Settings) -> Run:
    console.print(
        f"[bold]Scraping[/] {len(settings.categories)} categories from {settings.base_url} "
        f"[dim](concurrency {settings.concurrency}, delay {settings.request_delay}s, "
        f"timeout {settings.timeout}s, {settings.max_retries} retries)[/]"
    )
    started_at = datetime.now(UTC).replace(microsecond=0)
    started = time.monotonic()
    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(bar_width=36),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Product pages", total=None)

        def on_progress(done: int, total: int) -> None:
            progress.update(task, completed=done, total=total)

        try:
            result = asyncio.run(crawl_site(settings, on_progress))
        except CategoryNotFoundError as exc:
            err_console.print(f"[red]Error:[/] {exc}")
            raise typer.Exit(2) from exc
        except FetchError as exc:
            err_console.print(f"[red]Could not reach the site:[/] {exc}")
            raise typer.Exit(1) from exc

    elapsed = time.monotonic() - started
    table = Table(
        title="Crawled categories", title_justify="left", header_style="bold", box=box.SIMPLE_HEAD
    )
    table.add_column("Category")
    table.add_column("Products", justify="right")
    for name, count in result.categories.items():
        table.add_row(name, str(count))
    table.add_row("[bold]Total (unique)[/]", f"[bold]{len(result.products)}[/]")
    console.print(table)

    stats = result.stats
    if not result.ok:
        for error in result.errors[:10]:
            err_console.print(f"[red]x[/] {error}")
        err_console.print(
            f"[red]{len(result.errors)} page(s) failed - snapshot NOT saved[/] "
            "(a partial snapshot would report false 'removed' products)."
        )
        raise typer.Exit(1)

    with Storage(settings.db_path) as storage:
        run = storage.save_run(result.products, source="scrape", started_at=started_at)
    console.print(
        f"[green]Saved[/] {_run_label(run)}: {run.product_count} products in {elapsed:.1f}s "
        f"[dim]({stats.requests} requests, {stats.retries} retries)[/]"
    )
    return run


def _do_demo_snapshot(settings: Settings, seed: int) -> Run:
    with Storage(settings.db_path) as storage:
        _, base = storage.latest_pair()
        if base is None:
            err_console.print("[red]No runs yet.[/] Run `price-monitor scrape` first.")
            raise typer.Exit(1)
        products, report = simulate_next_snapshot(storage.load_snapshot(base.id), seed=seed)
        run = storage.save_run(
            products,
            source="demo",
            note=f"Simulated from run #{base.id} (seed {seed}): {report.describe()}",
        )
    console.print(f"[yellow]Simulated[/] {_run_label(run)} from run #{base.id}, seed {seed}:")
    console.print(f"  {report.describe()}")
    return run


def _load_diff(settings: Settings, threshold: float | None) -> DiffResult:
    with Storage(settings.db_path) as storage:
        previous, current = storage.latest_pair()
        if current is None:
            err_console.print("[red]No runs yet.[/] Run `price-monitor scrape` first.")
            raise typer.Exit(1)
        old = storage.load_snapshot(previous.id) if previous else []
        new = storage.load_snapshot(current.id)
    return build_diff(
        previous,
        current,
        old,
        new,
        threshold_pct=(
            Decimal(str(threshold)) if threshold is not None else settings.alert_threshold_pct
        ),
        alert_on_new_removed=settings.alert_on_new_removed,
    )


def _print_diff(diff: DiffResult, *, show_table: bool = True) -> None:
    if diff.previous is None:
        console.print(
            f"Only one run stored ({_run_label(diff.current)}). "
            "Run the monitor again to detect changes."
        )
        return
    summary = diff.summary
    if summary is None:  # pragma: no cover - build_diff always sets it
        return
    console.print(f"\n[bold]Changes[/] {_run_label(diff.previous)} -> {_run_label(diff.current)}")
    parts = [f"{summary.products_after} products"]
    parts += [f"{summary.count(t)} {t.label.lower()}" for t in CHANGE_ORDER if summary.count(t)]
    parts.append(f"[bold]{summary.alerts} alert(s)[/] at >= {diff.threshold_pct}%")
    console.print(" | ".join(parts))
    if show_table and diff.changes:
        console.print(changes_table(diff.changes, title=f"All changes ({len(diff.changes)})"))


def _notify(settings: Settings, diff: DiffResult) -> None:
    selection = build_notifiers(settings, console)
    for name, reason in selection.skipped.items():
        console.print(f"[dim]{name}: skipped ({reason})[/]")
    for notifier in selection.active:
        result = notifier.send(diff)
        if notifier.name != "console":
            colour = "green" if result.delivered else "yellow"
            console.print(f"[{colour}]{notifier.name}:[/] {result.detail}")


def _write_reports(
    settings: Settings, diff: DiffResult, out: Path | None, formats: str
) -> list[Path]:
    wanted = [f.strip().lower() for f in formats.split(",") if f.strip()]
    try:
        paths = write_reports(
            diff, out or settings.reports_dir, site_url=settings.base_url, formats=wanted
        )
    except ValueError as exc:
        err_console.print(f"[red]Error:[/] {exc}")
        raise typer.Exit(2) from exc
    console.print("[bold]Reports[/]")
    for path in paths:
        console.print(f"  [green]+[/] {path}")
    return paths


# --------------------------------------------------------------------------- commands


@app.command()
def scrape(
    ctx: typer.Context,
    categories: Annotated[
        str | None,
        typer.Option("--categories", "-c", help="Comma-separated category names to crawl."),
    ] = None,
) -> None:
    """Crawl the configured categories and store a new snapshot."""
    settings = _settings(ctx)
    if categories:
        settings = settings.model_copy(
            update={"categories": [c.strip() for c in categories.split(",") if c.strip()]}
        )
    _do_scrape(settings)


@app.command("demo-snapshot")
def demo_snapshot(
    ctx: typer.Context,
    seed: Annotated[int, typer.Option(help="Random seed - same seed, same changes.")] = 42,
) -> None:
    """[yellow]Demo only:[/] store a SIMULATED follow-up snapshot derived from the latest run.

    The sandbox site never changes, so this is how change detection can be shown.
    Simulated runs are labelled as such in every report.
    """
    _do_demo_snapshot(_settings(ctx), seed)


@app.command()
def diff(
    ctx: typer.Context,
    threshold: ThresholdOpt = None,
    notify: Annotated[
        bool, typer.Option("--notify", help="Send alerts to all configured notifiers.")
    ] = False,
) -> None:
    """Show changes between the two most recent runs."""
    settings = _settings(ctx)
    result = _load_diff(settings, threshold)
    _print_diff(result)
    if notify and result.previous is not None:
        _notify(settings, result)


@app.command()
def report(
    ctx: typer.Context,
    out: OutOpt = None,
    formats: FormatsOpt = ALL_FORMATS,
    threshold: ThresholdOpt = None,
) -> None:
    """Write HTML, Excel and CSV reports for the latest run (and changes vs the previous one)."""
    settings = _settings(ctx)
    _write_reports(settings, _load_diff(settings, threshold), out, formats)


@app.command()
def run(
    ctx: typer.Context,
    simulate_changes: Annotated[
        bool,
        typer.Option(
            "--simulate-changes",
            help="Demo: after the real crawl, add a simulated snapshot (see demo-snapshot).",
        ),
    ] = False,
    seed: Annotated[int, typer.Option(help="Seed for --simulate-changes.")] = 42,
    out: OutOpt = None,
    formats: FormatsOpt = ALL_FORMATS,
    threshold: ThresholdOpt = None,
) -> None:
    """Full pipeline: scrape -> diff -> reports -> alerts."""
    settings = _settings(ctx)
    _do_scrape(settings)
    if simulate_changes:
        _do_demo_snapshot(settings, seed)
    result = _load_diff(settings, threshold)
    _print_diff(result, show_table=False)
    if result.previous is not None:
        _notify(settings, result)
    _write_reports(settings, result, out, formats)


@app.command()
def runs(
    ctx: typer.Context,
    limit: Annotated[int, typer.Option(help="How many runs to show.")] = 10,
) -> None:
    """List stored runs, newest first."""
    with Storage(_settings(ctx).db_path) as storage:
        items = storage.list_runs(limit=limit)
    table = Table(header_style="bold", box=box.SIMPLE_HEAD)
    for column in ("Run", "Finished (UTC)", "Source", "Products", "Note"):
        table.add_column(column)
    for item in items:
        source = "[yellow]demo (simulated)[/]" if item.is_simulated else "scrape"
        table.add_row(
            f"#{item.id}",
            f"{item.finished_at:%Y-%m-%d %H:%M:%S}",
            source,
            str(item.product_count),
            item.note or "",
        )
    console.print(table if items else "No runs stored yet.")
