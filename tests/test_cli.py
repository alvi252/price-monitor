"""End-to-end CLI tests. The crawler is replaced by a stub, so no network is used."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from price_monitor import cli
from price_monitor.scraper.crawler import CrawlResult
from price_monitor.scraper.http import FetchStats
from price_monitor.storage import Storage

from .conftest import make_product

runner = CliRunner()
PRODUCTS = [make_product(f"SKU{i:02d}", f"{10 + i}.00") for i in range(30)]


@pytest.fixture
def fake_crawl(monkeypatch: pytest.MonkeyPatch):
    calls: list[list[str]] = []

    async def crawl_site(settings, on_progress=None):
        calls.append(list(settings.categories))
        if on_progress:
            on_progress(len(PRODUCTS), len(PRODUCTS))
        return CrawlResult(
            products=list(PRODUCTS),
            categories={"Travel": len(PRODUCTS)},
            stats=FetchStats(requests=31),
        )

    monkeypatch.setattr(cli, "crawl_site", crawl_site)
    return calls


def invoke(*args: str):
    return runner.invoke(cli.app, ["--db", "pm.db", *args])


def test_scrape_saves_a_run(fake_crawl) -> None:
    result = invoke("scrape", "--categories", "Travel, Poetry")

    assert result.exit_code == 0, result.output
    assert fake_crawl == [["Travel", "Poetry"]]
    assert "Saved run #1" in result.output
    with Storage("pm.db") as storage:
        assert storage.list_runs()[0].product_count == 30


def test_scrape_with_errors_does_not_save(monkeypatch: pytest.MonkeyPatch) -> None:
    async def crawl_site(settings, on_progress=None):
        return CrawlResult(products=PRODUCTS[:5], categories={"Travel": 6}, errors=["boom"])

    monkeypatch.setattr(cli, "crawl_site", crawl_site)
    result = invoke("scrape")

    assert result.exit_code == 1
    with Storage("pm.db") as storage:
        assert storage.list_runs() == []


def test_commands_need_a_run_first() -> None:
    assert invoke("diff").exit_code == 1
    assert invoke("demo-snapshot").exit_code == 1
    result = invoke("runs")
    assert result.exit_code == 0 and "No runs stored yet" in result.output


def test_demo_snapshot_diff_and_report(fake_crawl, tmp_path: Path) -> None:
    assert invoke("scrape").exit_code == 0

    demo = invoke("demo-snapshot", "--seed", "1")
    assert demo.exit_code == 0, demo.output
    assert "Simulated run #2" in demo.output

    diff = invoke("diff", "--threshold", "5")
    assert diff.exit_code == 0, diff.output
    assert "run #1" in diff.output and "run #2" in diff.output
    assert "out of stock" in diff.output

    out = tmp_path / "reports"
    report = invoke("report", "--out", str(out), "--format", "html,csv")
    assert report.exit_code == 0, report.output
    assert sorted(p.name for p in out.iterdir()) == [
        "price-report-run1-vs-run2-changes.csv",
        "price-report-run1-vs-run2-snapshot.csv",
        "price-report-run1-vs-run2.html",
    ]

    runs = invoke("runs")
    assert "demo (simulated)" in runs.output


def test_run_pipeline_with_simulated_changes(fake_crawl, tmp_path: Path) -> None:
    result = invoke("run", "--simulate-changes", "--out", str(tmp_path / "r"))

    assert result.exit_code == 0, result.output
    assert "Alerts:" in result.output
    assert "telegram: skipped" in result.output
    assert len(list((tmp_path / "r").iterdir())) == 4


def test_report_rejects_unknown_format(fake_crawl) -> None:
    invoke("scrape")
    result = invoke("report", "--format", "pdf")
    assert result.exit_code == 2


def test_version() -> None:
    result = runner.invoke(cli.app, ["--version"])
    assert result.exit_code == 0 and "price-monitor 0.1.0" in result.output
