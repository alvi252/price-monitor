"""SQLite storage: one ``runs`` row per crawl, one ``snapshots`` row per product per run.

Prices are stored as integer minor units (pence/cents) to avoid float rounding.
A run and all of its rows are written in a single transaction, so a crash
mid-way never leaves a half-written snapshot behind.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import TracebackType

from price_monitor.models import Product, Run

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at    TEXT    NOT NULL,
    finished_at   TEXT    NOT NULL,
    source        TEXT    NOT NULL CHECK (source IN ('scrape', 'demo')),
    note          TEXT,
    product_count INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS snapshots (
    run_id      INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    sku         TEXT    NOT NULL,
    name        TEXT    NOT NULL,
    category    TEXT    NOT NULL,
    price_minor INTEGER NOT NULL,
    currency    TEXT    NOT NULL,
    in_stock    INTEGER NOT NULL CHECK (in_stock IN (0, 1)),
    stock_count INTEGER,
    rating      INTEGER,
    url         TEXT    NOT NULL,
    PRIMARY KEY (run_id, sku)
);

CREATE INDEX IF NOT EXISTS idx_snapshots_sku ON snapshots (sku);
"""


def _to_minor(price: Decimal) -> int:
    return int((price * 100).to_integral_value())


def _from_minor(value: int) -> Decimal:
    return (Decimal(value) / 100).quantize(Decimal("0.01"))


def _now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


class Storage:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self.init_schema()

    # -- lifecycle -----------------------------------------------------------
    def __enter__(self) -> Storage:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._conn.close()

    def init_schema(self) -> None:
        """Create tables if needed. Safe to call any number of times."""
        with self._conn:
            self._conn.executescript(_SCHEMA)
            # PRAGMA does not accept bound parameters; SCHEMA_VERSION is a code constant.
            self._conn.execute(f"PRAGMA user_version = {int(SCHEMA_VERSION)}")

    # -- writes --------------------------------------------------------------
    def save_run(
        self,
        products: Iterable[Product],
        *,
        source: str = "scrape",
        started_at: datetime | None = None,
        note: str | None = None,
    ) -> Run:
        rows = list(products)
        started = started_at or _now()
        finished = _now()
        with self._conn:  # one transaction: run + all rows, or nothing
            cursor = self._conn.execute(
                "INSERT INTO runs (started_at, finished_at, source, note, product_count)"
                " VALUES (?, ?, ?, ?, ?)",
                (started.isoformat(), finished.isoformat(), source, note, len(rows)),
            )
            run_id = cursor.lastrowid
            if run_id is None:  # pragma: no cover - sqlite always returns it after INSERT
                raise RuntimeError("SQLite did not return the new run id")
            self._conn.executemany(
                "INSERT INTO snapshots (run_id, sku, name, category, price_minor, currency,"
                " in_stock, stock_count, rating, url) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        run_id,
                        p.sku,
                        p.name,
                        p.category,
                        _to_minor(p.price),
                        p.currency,
                        int(p.in_stock),
                        p.stock_count,
                        p.rating,
                        p.url,
                    )
                    for p in rows
                ],
            )
        return Run(run_id, started, finished, source, note, len(rows))

    # -- reads ---------------------------------------------------------------
    def list_runs(self, limit: int | None = None) -> list[Run]:
        """Runs, newest first."""
        sql = "SELECT * FROM runs ORDER BY id DESC"
        params: tuple[int, ...] = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (limit,)
        return [self._run_from_row(r) for r in self._conn.execute(sql, params)]

    def get_run(self, run_id: int) -> Run | None:
        row = self._conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return self._run_from_row(row) if row else None

    def latest_pair(self) -> tuple[Run | None, Run | None]:
        """``(previous, current)`` - the two most recent runs (either may be ``None``)."""
        runs = self.list_runs(limit=2)
        current = runs[0] if runs else None
        previous = runs[1] if len(runs) > 1 else None
        return previous, current

    def load_snapshot(self, run_id: int) -> list[Product]:
        rows = self._conn.execute(
            "SELECT * FROM snapshots WHERE run_id = ? ORDER BY category, name", (run_id,)
        )
        return [
            Product(
                sku=r["sku"],
                name=r["name"],
                category=r["category"],
                price=_from_minor(r["price_minor"]),
                currency=r["currency"],
                in_stock=bool(r["in_stock"]),
                stock_count=r["stock_count"],
                rating=r["rating"],
                url=r["url"],
            )
            for r in rows
        ]

    @staticmethod
    def _run_from_row(row: sqlite3.Row) -> Run:
        return Run(
            id=row["id"],
            started_at=datetime.fromisoformat(row["started_at"]),
            finished_at=datetime.fromisoformat(row["finished_at"]),
            source=row["source"],
            note=row["note"],
            product_count=row["product_count"],
        )
