"""Demo helper: derive a *simulated* follow-up snapshot from a real one.

books.toscrape.com is a static sandbox - its prices and stock never change, so two
real crawls are always identical and change detection would have nothing to show.
This module copies a real snapshot and applies seeded, reproducible edits
(price moves, sell-outs, delistings, restocks). Runs created this way are stored
with ``source = 'demo'`` and every report labels them as simulated.

It is a demonstration tool only and is never used by the regular ``scrape``
command.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field, replace
from decimal import Decimal

from price_monitor.models import Product, to_money


@dataclass(frozen=True, slots=True)
class SimulationReport:
    price_up: list[str] = field(default_factory=list)  # SKUs
    price_down: list[str] = field(default_factory=list)
    sold_out: list[str] = field(default_factory=list)
    restocked: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)

    def describe(self) -> str:
        """One-line summary, e.g. stored as the run note.

        >>> SimulationReport(price_up=["a"], removed=["b", "c"]).describe()
        '1 price increases, 0 price decreases, 0 sold out, 0 restocked, 2 removed'
        """
        return (
            f"{len(self.price_up)} price increases, {len(self.price_down)} price decreases, "
            f"{len(self.sold_out)} sold out, {len(self.restocked)} restocked, "
            f"{len(self.removed)} removed"
        )


def simulate_next_snapshot(
    products: list[Product],
    *,
    seed: int = 42,
    price_change_share: float = 0.12,
    sold_out: int = 4,
    removed: int = 2,
    restock_share: float = 0.5,
) -> tuple[list[Product], SimulationReport]:
    """Return a modified copy of ``products`` and a report of what was changed.

    * ``price_change_share`` of the products get a new price: roughly a third move by
      1.5-4.5 % (below the default 5 % alert threshold), the rest by 5-25 %,
      alternating between increases and decreases;
    * ``sold_out`` in-stock products become out of stock;
    * ``removed`` products disappear from the snapshot;
    * ``restock_share`` of already out-of-stock products come back in stock
      (only relevant when the base snapshot itself was simulated).

    The same input and seed always produce the same output.
    """
    rng = random.Random(seed)
    base = sorted(products, key=lambda p: p.sku)
    by_sku = {p.sku: p for p in base}
    report = SimulationReport()

    pool = [p.sku for p in base]
    rng.shuffle(pool)

    def take(n: int, eligible: set[str] | None = None) -> list[str]:
        picked = [s for s in pool if eligible is None or s in eligible][: max(0, n)]
        for sku in picked:
            pool.remove(sku)
        return picked

    removed_skus = take(removed)
    report.removed.extend(removed_skus)

    in_stock = {p.sku for p in base if p.in_stock}
    for sku in take(sold_out, in_stock):
        by_sku[sku] = replace(by_sku[sku], in_stock=False, stock_count=0)
        report.sold_out.append(sku)

    out_of_stock = sorted(p.sku for p in base if not p.in_stock)
    for sku in out_of_stock[: math.ceil(len(out_of_stock) * restock_share)]:
        if sku in pool:
            pool.remove(sku)
            by_sku[sku] = replace(by_sku[sku], in_stock=True, stock_count=rng.randint(1, 20))
            report.restocked.append(sku)

    price_targets = take(round(len(base) * price_change_share))
    for i, sku in enumerate(price_targets):
        small = i % 3 == 0
        pct = rng.uniform(1.5, 4.5) if small else rng.uniform(5.0, 25.0)
        direction = 1 if i % 2 == 0 else -1
        old = by_sku[sku].price
        new = to_money(old * (1 + Decimal(str(direction * pct)) / 100))
        if new == old or new <= 0:
            continue
        by_sku[sku] = replace(by_sku[sku], price=new)
        (report.price_up if new > old else report.price_down).append(sku)

    gone = set(removed_skus)
    result = [p for sku, p in by_sku.items() if sku not in gone]
    result.sort(key=lambda p: (p.category, p.name))
    return result, report
