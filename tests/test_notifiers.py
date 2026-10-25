from __future__ import annotations

import io
import json

import httpx
from pydantic import SecretStr
from rich.console import Console

from price_monitor.config import Settings
from price_monitor.notifiers import ConsoleNotifier, TelegramNotifier, build_notifiers
from price_monitor.notifiers.telegram import format_messages

from .test_reports import sample_diff

TOKEN = "123456:SECRET-TOKEN"


def quiet_console() -> Console:
    return Console(file=io.StringIO(), width=140, record=True)


def test_only_console_notifier_without_telegram_settings() -> None:
    selection = build_notifiers(Settings(), quiet_console())
    assert [n.name for n in selection.active] == ["console"]
    assert "telegram" in selection.skipped


def test_telegram_needs_both_token_and_chat_id() -> None:
    only_token = build_notifiers(Settings(telegram_bot_token=SecretStr(TOKEN)), quiet_console())
    assert [n.name for n in only_token.active] == ["console"]

    both = build_notifiers(
        Settings(telegram_bot_token=SecretStr(TOKEN), telegram_chat_id="42"), quiet_console()
    )
    assert [n.name for n in both.active] == ["console", "telegram"]
    assert both.skipped == {}


def test_telegram_settings_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("PM_TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("PM_TELEGRAM_CHAT_ID", "-100500")
    selection = build_notifiers(Settings(), quiet_console())
    assert [n.name for n in selection.active] == ["console", "telegram"]


def test_console_notifier_prints_only_alerts() -> None:
    console = quiet_console()
    result = ConsoleNotifier(console).send(sample_diff())
    output = console.export_text()

    assert result.delivered
    assert "Alerts: 3" in output
    assert "Rising <Book>" in output and "+20.0%" in output
    assert "Book SMALL" not in output  # +2 % change is below the threshold


def test_telegram_message_format_and_escaping() -> None:
    (message,) = format_messages(sample_diff())

    assert message.startswith("<b>Price Monitor</b>: 3 alert(s) run #1 → #2")
    assert "Demo: changes are simulated" in message
    assert "Rising &lt;Book&gt;" in message
    assert "▼" in message and "(-25.0%)" in message
    assert "• Out of stock:" in message


def test_telegram_messages_are_split_under_the_limit() -> None:
    messages = format_messages(sample_diff(), limit=200)
    assert len(messages) > 1
    assert all(len(m) <= 200 for m in messages)


def test_telegram_send_uses_bot_api() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ok": True, "result": {}})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = TelegramNotifier(TOKEN, "42", client=client).send(sample_diff())

    assert result.delivered
    assert len(requests) == 1
    assert requests[0].url.path == f"/bot{TOKEN}/sendMessage"
    payload = json.loads(requests[0].content)
    assert payload["chat_id"] == "42" and payload["parse_mode"] == "HTML"


def test_telegram_error_does_not_leak_token() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(401, json={"ok": False}))
    )
    result = TelegramNotifier(TOKEN, "42", client=client).send(sample_diff())

    assert not result.delivered
    assert "401" in result.detail
    assert "SECRET" not in result.detail


def test_telegram_skips_when_nothing_to_send() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - must not run
        raise AssertionError("no request expected")

    diff = sample_diff()
    diff.changes = [c for c in diff.changes if not c.is_alert]
    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = TelegramNotifier(TOKEN, "42", client=client).send(diff)
    assert not result.delivered and result.detail == "no alerts"
