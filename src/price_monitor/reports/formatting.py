"""Formatting helpers shared by all report writers."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from price_monitor.diff import DiffResult
from price_monitor.models import Product

CURRENCY_SIGNS = {"GBP": "£", "EUR": "€", "USD": "$"}


def money(value: Decimal | None, currency: str = "GBP") -> str:
    """
    >>> money(Decimal("1234.5"), "GBP")
    '£1,234.50'
    >>> money(None)
    '-'
    """
    if value is None:
        return "-"
    sign = CURRENCY_SIGNS.get(currency, "")
    text = f"{abs(value):,.2f}"
    prefix = "-" if value < 0 else ""
    return f"{prefix}{sign}{text}" if sign else f"{prefix}{text} {currency}"


def signed_money(value: Decimal | None, currency: str = "GBP") -> str:
    """
    >>> signed_money(Decimal("2.5"))
    '+£2.50'
    >>> signed_money(Decimal("-0.40"))
    '-£0.40'
    """
    if value is None:
        return "-"
    return ("+" if value > 0 else "") + money(value, currency)


def signed_pct(value: Decimal | None) -> str:
    """
    >>> signed_pct(Decimal("12.3"))
    '+12.3%'
    >>> signed_pct(Decimal("-4.0"))
    '-4.0%'
    """
    if value is None:
        return "-"
    return f"{'+' if value > 0 else ''}{value:.1f}%"


def stock_label(in_stock: bool | None, count: int | None) -> str:
    """
    >>> stock_label(True, 12)
    'In stock (12)'
    >>> stock_label(False, 0)
    'Out of stock'
    """
    if in_stock is None:
        return "-"
    if not in_stock:
        return "Out of stock"
    return f"In stock ({count})" if count is not None else "In stock"


def report_basename(diff: DiffResult) -> str:
    if diff.previous is None:
        return f"price-report-run{diff.current.id}"
    return f"price-report-run{diff.previous.id}-vs-run{diff.current.id}"


@dataclass(frozen=True, slots=True)
class CategoryStats:
    name: str
    products: int
    in_stock: int
    avg_price: Decimal
    min_price: Decimal
    max_price: Decimal
    avg_rating: Decimal | None


def category_stats(products: list[Product]) -> list[CategoryStats]:
    groups: dict[str, list[Product]] = defaultdict(list)
    for p in products:
        groups[p.category].append(p)
    stats = []
    for name in sorted(groups):
        items = groups[name]
        prices = [p.price for p in items]
        ratings = [p.rating for p in items if p.rating is not None]
        stats.append(
            CategoryStats(
                name=name,
                products=len(items),
                in_stock=sum(1 for p in items if p.in_stock),
                avg_price=(sum(prices, Decimal(0)) / len(prices)).quantize(Decimal("0.01")),
                min_price=min(prices),
                max_price=max(prices),
                avg_rating=(
                    (Decimal(sum(ratings)) / len(ratings)).quantize(Decimal("0.1"))
                    if ratings
                    else None
                ),
            )
        )
    return stats
