"""Domain models shared by the scraper, storage, diff engine and reports."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

CENT = Decimal("0.01")


def to_money(value: Decimal | str | int | float) -> Decimal:
    """Normalise a price to two decimal places.

    >>> to_money("51.774")
    Decimal('51.77')
    >>> to_money(3)
    Decimal('3.00')
    """
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True, slots=True)
class Product:
    """One product as seen on a competitor site at a single point in time."""

    sku: str
    name: str
    category: str
    price: Decimal
    currency: str
    in_stock: bool
    stock_count: int | None
    rating: int | None
    url: str


@dataclass(frozen=True, slots=True)
class Run:
    """Metadata of one stored snapshot."""

    id: int
    started_at: datetime
    finished_at: datetime
    source: str  # "scrape" for real crawls, "demo" for simulated snapshots
    note: str | None
    product_count: int

    @property
    def is_simulated(self) -> bool:
        return self.source == "demo"


class ChangeType(StrEnum):
    PRICE_UP = "price_up"
    PRICE_DOWN = "price_down"
    OUT_OF_STOCK = "out_of_stock"
    BACK_IN_STOCK = "back_in_stock"
    NEW = "new"
    REMOVED = "removed"

    @property
    def label(self) -> str:
        """Human readable label used in reports.

        >>> ChangeType.BACK_IN_STOCK.label
        'Back in stock'
        """
        return _CHANGE_LABELS[self]


_CHANGE_LABELS = {
    ChangeType.PRICE_UP: "Price up",
    ChangeType.PRICE_DOWN: "Price down",
    ChangeType.OUT_OF_STOCK: "Out of stock",
    ChangeType.BACK_IN_STOCK: "Back in stock",
    ChangeType.NEW: "New product",
    ChangeType.REMOVED: "Removed",
}


@dataclass(frozen=True, slots=True)
class Change:
    """A single detected difference for one SKU between two snapshots."""

    type: ChangeType
    sku: str
    name: str
    category: str
    url: str
    currency: str
    old_price: Decimal | None = None
    new_price: Decimal | None = None
    old_in_stock: bool | None = None
    new_in_stock: bool | None = None
    old_stock_count: int | None = None
    new_stock_count: int | None = None
    is_alert: bool = False

    @property
    def is_price_change(self) -> bool:
        return self.type in (ChangeType.PRICE_UP, ChangeType.PRICE_DOWN)

    @property
    def price_delta(self) -> Decimal | None:
        """Absolute price difference (new - old) for price changes, otherwise ``None``.

        >>> Change(ChangeType.PRICE_UP, "A", "Book", "Cat", "u", "GBP",
        ...        old_price=Decimal("10.00"), new_price=Decimal("12.50")).price_delta
        Decimal('2.50')
        """
        if not self.is_price_change or self.old_price is None or self.new_price is None:
            return None
        return self.new_price - self.old_price

    @property
    def price_delta_pct(self) -> Decimal | None:
        """Relative price difference in percent, rounded to 0.1.

        >>> Change(ChangeType.PRICE_DOWN, "A", "Book", "Cat", "u", "GBP",
        ...        old_price=Decimal("40.00"), new_price=Decimal("37.00")).price_delta_pct
        Decimal('-7.5')
        """
        delta = self.price_delta
        if delta is None or not self.old_price:
            return None
        return (delta / self.old_price * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
