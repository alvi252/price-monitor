"""Change detection between two snapshots."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal

from price_monitor.models import Change, ChangeType, Product, Run

# Order used everywhere a list of changes is shown: the most actionable first.
CHANGE_ORDER = [
    ChangeType.PRICE_DOWN,
    ChangeType.PRICE_UP,
    ChangeType.OUT_OF_STOCK,
    ChangeType.BACK_IN_STOCK,
    ChangeType.NEW,
    ChangeType.REMOVED,
]


@dataclass(frozen=True, slots=True)
class DiffSummary:
    products_before: int
    products_after: int
    counts: dict[ChangeType, int]
    alerts: int
    avg_price_change_pct: Decimal | None  # mean of per-product % changes (changed items only)

    def count(self, change_type: ChangeType) -> int:
        return self.counts.get(change_type, 0)

    @property
    def total_changes(self) -> int:
        return sum(self.counts.values())


@dataclass(slots=True)
class DiffResult:
    previous: Run | None
    current: Run
    snapshot: list[Product]
    changes: list[Change] = field(default_factory=list)
    summary: DiffSummary | None = None
    threshold_pct: Decimal = Decimal("5")

    @property
    def alerts(self) -> list[Change]:
        return [c for c in self.changes if c.is_alert]

    @property
    def has_baseline(self) -> bool:
        return self.previous is not None


def detect_changes(
    old: Iterable[Product],
    new: Iterable[Product],
    *,
    threshold_pct: Decimal = Decimal("5"),
    alert_on_new_removed: bool = False,
) -> list[Change]:
    """Compare two snapshots keyed by SKU.

    * every price change is reported; it is an *alert* when ``|change %| >= threshold_pct``;
    * stock transitions (in stock <-> out of stock) are always alerts;
    * new / removed products are alerts only when ``alert_on_new_removed`` is set.

    A product whose price *and* availability changed yields two entries.
    """
    before = {p.sku: p for p in old}
    after = {p.sku: p for p in new}
    changes: list[Change] = []

    for sku, cur in after.items():
        prev = before.get(sku)
        if prev is None:
            changes.append(_change(ChangeType.NEW, cur, new=cur, alert=alert_on_new_removed))
            continue

        if cur.price != prev.price:
            kind = ChangeType.PRICE_UP if cur.price > prev.price else ChangeType.PRICE_DOWN
            # Decide on the exact ratio, not the rounded display value (4.99 % is not 5 %).
            exact_pct = abs(cur.price - prev.price) / prev.price * 100 if prev.price else None
            is_alert = exact_pct is None or exact_pct >= threshold_pct
            changes.append(_change(kind, cur, old=prev, new=cur, alert=is_alert))

        if prev.in_stock and not cur.in_stock:
            changes.append(_change(ChangeType.OUT_OF_STOCK, cur, old=prev, new=cur, alert=True))
        elif not prev.in_stock and cur.in_stock:
            changes.append(_change(ChangeType.BACK_IN_STOCK, cur, old=prev, new=cur, alert=True))

    for sku, prev in before.items():
        if sku not in after:
            changes.append(_change(ChangeType.REMOVED, prev, old=prev, alert=alert_on_new_removed))

    return sort_changes(changes)


def sort_changes(changes: Iterable[Change]) -> list[Change]:
    """Group by change type (see ``CHANGE_ORDER``), biggest relative move first."""
    rank = {t: i for i, t in enumerate(CHANGE_ORDER)}

    def key(c: Change) -> tuple[int, Decimal, str]:
        pct = c.price_delta_pct
        return (rank[c.type], -abs(pct) if pct is not None else Decimal(0), c.name)

    return sorted(changes, key=key)


def summarize(old_count: int, new_count: int, changes: list[Change]) -> DiffSummary:
    counts = Counter(c.type for c in changes)
    pcts = [c.price_delta_pct for c in changes if c.price_delta_pct is not None]
    avg = (sum(pcts, Decimal(0)) / len(pcts)).quantize(Decimal("0.1")) if pcts else None
    return DiffSummary(
        products_before=old_count,
        products_after=new_count,
        counts={t: counts[t] for t in CHANGE_ORDER if counts[t]},
        alerts=sum(1 for c in changes if c.is_alert),
        avg_price_change_pct=avg,
    )


def build_diff(
    previous: Run | None,
    current: Run,
    old_snapshot: list[Product],
    new_snapshot: list[Product],
    *,
    threshold_pct: Decimal,
    alert_on_new_removed: bool = False,
) -> DiffResult:
    changes = (
        detect_changes(
            old_snapshot,
            new_snapshot,
            threshold_pct=threshold_pct,
            alert_on_new_removed=alert_on_new_removed,
        )
        if previous is not None
        else []
    )
    return DiffResult(
        previous=previous,
        current=current,
        snapshot=new_snapshot,
        changes=changes,
        summary=summarize(len(old_snapshot), len(new_snapshot), changes),
        threshold_pct=threshold_pct,
    )


def _change(
    kind: ChangeType,
    ref: Product,
    *,
    old: Product | None = None,
    new: Product | None = None,
    alert: bool = False,
) -> Change:
    return Change(
        type=kind,
        sku=ref.sku,
        name=ref.name,
        category=ref.category,
        url=ref.url,
        currency=ref.currency,
        old_price=old.price if old else None,
        new_price=new.price if new else None,
        old_in_stock=old.in_stock if old else None,
        new_in_stock=new.in_stock if new else None,
        old_stock_count=old.stock_count if old else None,
        new_stock_count=new.stock_count if new else None,
        is_alert=alert,
    )
