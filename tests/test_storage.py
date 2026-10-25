from __future__ import annotations

import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest

from price_monitor.storage import Storage

from .conftest import make_product


def test_schema_creation_is_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "nested" / "pm.db"
    with Storage(db) as storage:
        storage.init_schema()
        storage.init_schema()
        storage.save_run([make_product("A")])
    with Storage(db) as storage:  # re-opening runs CREATE ... IF NOT EXISTS again
        assert len(storage.list_runs()) == 1

    conn = sqlite3.connect(db)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
    conn.close()


def test_roundtrip_keeps_exact_prices_and_fields(tmp_path: Path) -> None:
    products = [
        make_product("A", "51.77", rating=5),
        make_product("B", "0.10", in_stock=False, category="Poetry", rating=None),
    ]
    with Storage(tmp_path / "pm.db") as storage:
        run = storage.save_run(products, source="scrape", note="first")
        loaded = storage.load_snapshot(run.id)

    by_sku = {p.sku: p for p in loaded}
    assert by_sku == {p.sku: p for p in products}
    assert by_sku["A"].price == Decimal("51.77")
    assert run.product_count == 2
    assert run.note == "first"


def test_prices_are_stored_as_integer_minor_units(tmp_path: Path) -> None:
    with Storage(tmp_path / "pm.db") as storage:
        storage.save_run([make_product("A", "19.99")])
    conn = sqlite3.connect(tmp_path / "pm.db")
    assert conn.execute("SELECT price_minor FROM snapshots").fetchone()[0] == 1999
    conn.close()


def test_latest_pair_returns_previous_and_current(tmp_path: Path) -> None:
    with Storage(tmp_path / "pm.db") as storage:
        assert storage.latest_pair() == (None, None)
        first = storage.save_run([make_product("A")])
        assert storage.latest_pair() == (None, first)
        second = storage.save_run([make_product("A")], source="demo")
        third = storage.save_run([make_product("A")])

        previous, current = storage.latest_pair()
        assert (previous.id, current.id) == (second.id, third.id)
        assert previous.is_simulated and not current.is_simulated
        assert [r.id for r in storage.list_runs()] == [third.id, second.id, first.id]
        assert storage.get_run(first.id) == first
        assert storage.get_run(999) is None


def test_failed_insert_leaves_no_partial_run(tmp_path: Path) -> None:
    duplicate_sku = [make_product("A"), make_product("A")]
    with Storage(tmp_path / "pm.db") as storage:
        with pytest.raises(sqlite3.IntegrityError):
            storage.save_run(duplicate_sku)
        assert storage.list_runs() == []
