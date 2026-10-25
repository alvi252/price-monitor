"""Telegram notifier (Bot API ``sendMessage``).

Only active when both ``PM_TELEGRAM_BOT_TOKEN`` and ``PM_TELEGRAM_CHAT_ID`` are set.
The token is never logged or included in error messages.
"""

from __future__ import annotations

import logging
from html import escape

import httpx

from price_monitor.diff import DiffResult
from price_monitor.models import Change, ChangeType
from price_monitor.notifiers.base import Notifier, NotifyResult
from price_monitor.reports.formatting import money, signed_pct

log = logging.getLogger(__name__)

TELEGRAM_LIMIT = 4096  # max characters per message


def _line(c: Change) -> str:
    name = f'<a href="{escape(c.url, quote=True)}">{escape(c.name)}</a>'
    if c.is_price_change:
        arrow = "▲" if c.type is ChangeType.PRICE_UP else "▼"
        return (
            f"{arrow} {name}: {money(c.old_price, c.currency)} → "
            f"{money(c.new_price, c.currency)} ({signed_pct(c.price_delta_pct)})"
        )
    return f"• {escape(c.type.label)}: {name}"


def _is_ok(response: httpx.Response) -> bool:
    try:
        return bool(response.json().get("ok", False))
    except ValueError:
        return False


def format_messages(diff: DiffResult, limit: int = TELEGRAM_LIMIT) -> list[str]:
    """Render alerts as Telegram HTML messages, split so none exceeds ``limit``."""
    alerts = diff.alerts
    if not alerts:
        return []
    runs = f"run #{diff.previous.id} → #{diff.current.id}" if diff.previous else ""
    header = f"<b>Price Monitor</b>: {len(alerts)} alert(s) {runs}".rstrip()
    if diff.current.is_simulated:
        header += "\n<i>Demo: changes are simulated</i>"

    messages: list[str] = []
    current = header
    for line in map(_line, alerts):
        candidate = f"{current}\n{line}"
        if len(candidate) > limit:
            messages.append(current)
            current = line[:limit]
        else:
            current = candidate
    messages.append(current)
    return messages


class TelegramNotifier(Notifier):
    name = "telegram"

    def __init__(
        self,
        token: str,
        chat_id: str,
        *,
        client: httpx.Client | None = None,
        api_base: str = "https://api.telegram.org",
        timeout: float = 10.0,
    ) -> None:
        self._token = token
        self.chat_id = chat_id
        self._client = client
        self._api_base = api_base.rstrip("/")
        self._timeout = timeout

    def send(self, diff: DiffResult) -> NotifyResult:
        messages = format_messages(diff)
        if not messages:
            return NotifyResult(self.name, False, "no alerts")
        client = self._client or httpx.Client(timeout=self._timeout)
        try:
            for text in messages:
                response = client.post(
                    f"{self._api_base}/bot{self._token}/sendMessage",
                    json={
                        "chat_id": self.chat_id,
                        "text": text,
                        "parse_mode": "HTML",
                        "disable_web_page_preview": True,
                    },
                )
                if response.status_code != 200 or not _is_ok(response):
                    # Do not include the request URL: it contains the bot token.
                    detail = f"Telegram API returned HTTP {response.status_code}"
                    log.error(detail)
                    return NotifyResult(self.name, False, detail)
        except httpx.HTTPError as exc:
            detail = f"Telegram request failed: {type(exc).__name__}"
            log.error(detail)
            return NotifyResult(self.name, False, detail)
        finally:
            if self._client is None:
                client.close()
        return NotifyResult(self.name, True, f"{len(messages)} message(s) sent")
