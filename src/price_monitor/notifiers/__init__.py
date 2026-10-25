"""Pluggable alert notifiers.

To add a channel (e-mail, Slack, a webhook...), subclass :class:`Notifier` and
register it in :func:`build_notifiers`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from rich.console import Console

from price_monitor.config import Settings
from price_monitor.notifiers.base import Notifier, NotifyResult
from price_monitor.notifiers.console import ConsoleNotifier
from price_monitor.notifiers.telegram import TelegramNotifier


@dataclass(slots=True)
class NotifierSelection:
    active: list[Notifier] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)  # name -> reason


def build_notifiers(settings: Settings, console: Console) -> NotifierSelection:
    selection = NotifierSelection(active=[ConsoleNotifier(console)])
    token = settings.telegram_bot_token.get_secret_value() if settings.telegram_bot_token else ""
    if token and settings.telegram_chat_id:
        selection.active.append(TelegramNotifier(token, settings.telegram_chat_id))
    else:
        selection.skipped["telegram"] = "PM_TELEGRAM_BOT_TOKEN / PM_TELEGRAM_CHAT_ID not set"
    return selection


__all__ = [
    "ConsoleNotifier",
    "Notifier",
    "NotifierSelection",
    "NotifyResult",
    "TelegramNotifier",
    "build_notifiers",
]
