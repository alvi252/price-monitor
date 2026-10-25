from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from price_monitor.config import DEFAULT_CATEGORIES, Settings


def test_defaults_are_polite() -> None:
    settings = Settings()
    assert settings.concurrency == 4
    assert settings.request_delay > 0
    assert settings.categories == DEFAULT_CATEGORIES
    assert "price-monitor-demo" in settings.user_agent


def test_environment_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PM_CATEGORIES", "Travel, Poetry ,")
    monkeypatch.setenv("PM_ALERT_THRESHOLD_PCT", "7.5")
    monkeypatch.setenv("PM_BASE_URL", "https://books.toscrape.com")
    settings = Settings()
    assert settings.categories == ["Travel", "Poetry"]
    assert settings.alert_threshold_pct == Decimal("7.5")
    assert settings.base_url == "https://books.toscrape.com/"


def test_dotenv_file_is_read(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("PM_DB_PATH=custom.db\nPM_CONCURRENCY=2\n", encoding="utf-8")
    settings = Settings()  # tests run inside tmp_path (see conftest)
    assert settings.db_path == Path("custom.db")
    assert settings.concurrency == 2


def test_concurrency_has_an_upper_bound() -> None:
    with pytest.raises(ValidationError):
        Settings(concurrency=50)
