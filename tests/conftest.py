from __future__ import annotations

import os
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from price_monitor.models import Product, Run

FIXTURES = Path(__file__).parent / "fixtures"


def read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def make_product(
    sku: str,
    price: str = "10.00",
    *,
    in_stock: bool = True,
    stock_count: int | None = 5,
    name: str | None = None,
    category: str = "Travel",
    rating: int | None = 3,
) -> Product:
    return Product(
        sku=sku,
        name=name or f"Book {sku}",
        category=category,
        price=Decimal(price),
        currency="GBP",
        in_stock=in_stock,
        stock_count=stock_count if in_stock else 0,
        rating=rating,
        url=f"https://books.toscrape.com/catalogue/{sku.lower()}/index.html",
    )


def make_run(run_id: int, source: str = "scrape", count: int = 0) -> Run:
    stamp = datetime(2026, 10, 25, 12, run_id, tzinfo=UTC)
    return Run(run_id, stamp, stamp, source, None, count)


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Run every test in a temp dir without the developer's PM_* variables or ``.env``.

    This keeps a real Telegram token (or database path) from leaking into tests.
    """
    for key in list(os.environ):
        if key.startswith("PM_"):
            monkeypatch.delenv(key)
    monkeypatch.chdir(tmp_path)
