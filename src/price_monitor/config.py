"""Runtime configuration loaded from environment variables and an optional ``.env`` file.

Every setting can be overridden with a ``PM_``-prefixed environment variable,
for example ``PM_ALERT_THRESHOLD_PCT=10`` or ``PM_CATEGORIES="Travel,Poetry"``.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Annotated

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from price_monitor import __version__

DEFAULT_CATEGORIES = [
    "Mystery",
    "Historical Fiction",
    "Science Fiction",
    "Poetry",
    "Food and Drink",
    "Travel",
]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PM_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- What to crawl -------------------------------------------------------
    base_url: str = "https://books.toscrape.com/"
    categories: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: list(DEFAULT_CATEGORIES)
    )
    max_pages_per_category: int = Field(20, ge=1, le=200)

    # --- Politeness ----------------------------------------------------------
    concurrency: int = Field(4, ge=1, le=8)
    request_delay: float = Field(0.25, ge=0, description="Pause after each request, seconds.")
    timeout: float = Field(15.0, gt=0)
    max_retries: int = Field(3, ge=0, le=10)
    backoff_base: float = Field(0.5, ge=0)
    user_agent: str = f"price-monitor-demo/{__version__} (portfolio demo; polite crawler)"
    respect_robots_txt: bool = True

    # --- Storage & output ----------------------------------------------------
    db_path: Path = Path("data/price_monitor.db")
    reports_dir: Path = Path("reports")

    # --- Alerts --------------------------------------------------------------
    alert_threshold_pct: Decimal = Field(Decimal("5"), ge=0)
    alert_on_new_removed: bool = False
    telegram_bot_token: SecretStr | None = None
    telegram_chat_id: str | None = None

    @field_validator("categories", mode="before")
    @classmethod
    def _split_categories(cls, value: object) -> object:
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value

    @field_validator("base_url")
    @classmethod
    def _trailing_slash(cls, value: str) -> str:
        return value if value.endswith("/") else value + "/"
