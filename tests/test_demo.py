from __future__ import annotations

from price_monitor.demo import simulate_next_snapshot

from .conftest import make_product

BASE = [make_product(f"SKU{i:03d}", f"{10 + i}.50") for i in range(100)]


def test_same_seed_same_result_different_seed_different_result() -> None:
    first, report_a = simulate_next_snapshot(BASE, seed=7)
    second, report_b = simulate_next_snapshot(BASE, seed=7)
    third, _ = simulate_next_snapshot(BASE, seed=8)

    assert first == second
    assert report_a == report_b
    assert first != third


def test_applies_requested_number_of_changes() -> None:
    result, report = simulate_next_snapshot(
        BASE, seed=1, price_change_share=0.1, sold_out=3, removed=2
    )
    by_sku = {p.sku: p for p in result}
    original = {p.sku: p for p in BASE}

    assert len(result) == 98
    assert all(sku not in by_sku for sku in report.removed)
    assert len(report.sold_out) == 3
    assert all(not by_sku[s].in_stock and by_sku[s].stock_count == 0 for s in report.sold_out)
    assert len(report.price_up) + len(report.price_down) == 10
    assert report.price_up and report.price_down
    for sku in report.price_up:
        assert by_sku[sku].price > original[sku].price
    for sku in report.price_down:
        assert by_sku[sku].price < original[sku].price
    # every touched SKU is touched by exactly one kind of change
    touched = report.price_up + report.price_down + report.sold_out + report.removed
    assert len(touched) == len(set(touched))


def test_some_price_moves_stay_below_the_default_threshold() -> None:
    result, report = simulate_next_snapshot(BASE, seed=42, price_change_share=0.3)
    original = {p.sku: p.price for p in BASE}
    moves = [
        abs(p.price - original[p.sku]) / original[p.sku] * 100
        for p in result
        if p.sku in report.price_up + report.price_down
    ]
    assert any(m < 5 for m in moves)
    assert any(m >= 5 for m in moves)


def test_input_is_not_mutated_and_restock_works_on_simulated_base() -> None:
    base = [*BASE[:10], make_product("OOS1", in_stock=False), make_product("OOS2", in_stock=False)]
    snapshot_before = list(base)

    result, report = simulate_next_snapshot(base, seed=3, sold_out=0, removed=0)

    assert base == snapshot_before
    assert report.restocked == ["OOS1"]
    restocked = next(p for p in result if p.sku == "OOS1")
    assert restocked.in_stock and restocked.stock_count >= 1
