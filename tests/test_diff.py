from __future__ import annotations

from decimal import Decimal

import pytest

from price_monitor.diff import build_diff, detect_changes, summarize
from price_monitor.models import ChangeType

from .conftest import make_product, make_run


def kinds(changes) -> list[tuple[ChangeType, str]]:
    return [(c.type, c.sku) for c in changes]


def test_identical_snapshots_have_no_changes() -> None:
    snapshot = [make_product("A"), make_product("B", in_stock=False)]
    assert detect_changes(snapshot, snapshot) == []


def test_detects_every_change_type() -> None:
    old = [
        make_product("UP", "10.00"),
        make_product("DOWN", "20.00"),
        make_product("SOLD", "5.00"),
        make_product("BACK", "7.00", in_stock=False),
        make_product("GONE", "9.00"),
    ]
    new = [
        make_product("UP", "12.00"),
        make_product("DOWN", "15.00"),
        make_product("SOLD", "5.00", in_stock=False),
        make_product("BACK", "7.00", stock_count=3),
        make_product("NEW", "4.00"),
    ]

    changes = detect_changes(old, new)

    assert kinds(changes) == [
        (ChangeType.PRICE_DOWN, "DOWN"),
        (ChangeType.PRICE_UP, "UP"),
        (ChangeType.OUT_OF_STOCK, "SOLD"),
        (ChangeType.BACK_IN_STOCK, "BACK"),
        (ChangeType.NEW, "NEW"),
        (ChangeType.REMOVED, "GONE"),
    ]
    up = changes[1]
    assert up.price_delta == Decimal("2.00")
    assert up.price_delta_pct == Decimal("20.0")
    down = changes[0]
    assert down.price_delta == Decimal("-5.00")
    assert down.price_delta_pct == Decimal("-25.0")
    back = changes[3]
    assert (back.old_stock_count, back.new_stock_count) == (0, 3)


@pytest.mark.parametrize(
    ("new_price", "is_alert"),
    [("105.00", True), ("95.00", True), ("104.99", False), ("100.01", False), ("80.00", True)],
)
def test_price_alert_threshold_is_inclusive(new_price: str, is_alert: bool) -> None:
    changes = detect_changes(
        [make_product("A", "100.00")],
        [make_product("A", new_price)],
        threshold_pct=Decimal("5"),
    )
    assert len(changes) == 1
    assert changes[0].is_alert is is_alert


def test_stock_changes_always_alert_new_and_removed_only_when_enabled() -> None:
    old = [make_product("A"), make_product("GONE")]
    new = [make_product("A", in_stock=False), make_product("NEW")]

    default = {c.type: c.is_alert for c in detect_changes(old, new)}
    assert default == {
        ChangeType.OUT_OF_STOCK: True,
        ChangeType.NEW: False,
        ChangeType.REMOVED: False,
    }

    enabled = {c.type: c.is_alert for c in detect_changes(old, new, alert_on_new_removed=True)}
    assert enabled[ChangeType.NEW] and enabled[ChangeType.REMOVED]


def test_price_and_stock_change_on_same_product_yield_two_entries() -> None:
    changes = detect_changes(
        [make_product("A", "10.00")], [make_product("A", "8.00", in_stock=False)]
    )
    assert kinds(changes) == [(ChangeType.PRICE_DOWN, "A"), (ChangeType.OUT_OF_STOCK, "A")]
    # The stock entry must not repeat the price delta (it would be double counted).
    assert changes[1].price_delta is None


def test_price_changes_sorted_by_magnitude_within_type() -> None:
    old = [make_product(s, "100.00") for s in ("S", "M", "L")]
    new = [make_product("S", "101.00"), make_product("M", "110.00"), make_product("L", "150.00")]
    assert [c.sku for c in detect_changes(old, new)] == ["L", "M", "S"]


def test_summary_counts_alerts_and_average() -> None:
    old = [make_product("A", "10.00"), make_product("B", "10.00"), make_product("C")]
    new = [make_product("A", "11.00"), make_product("B", "9.70"), make_product("C", in_stock=False)]
    changes = detect_changes(old, new, threshold_pct=Decimal("5"))

    summary = summarize(len(old), len(new), changes)

    assert summary.count(ChangeType.PRICE_UP) == 1
    assert summary.count(ChangeType.PRICE_DOWN) == 1
    assert summary.count(ChangeType.OUT_OF_STOCK) == 1
    assert summary.count(ChangeType.NEW) == 0
    assert summary.alerts == 2  # +10 % and the sell-out; -3 % is below the threshold
    assert summary.avg_price_change_pct == Decimal("3.5")  # mean of +10.0 and -3.0
    assert summary.total_changes == 3


def test_build_diff_without_previous_run_has_no_changes() -> None:
    result = build_diff(None, make_run(1), [], [make_product("A")], threshold_pct=Decimal("5"))
    assert result.changes == []
    assert not result.has_baseline
    assert result.summary is not None and result.summary.products_after == 1
