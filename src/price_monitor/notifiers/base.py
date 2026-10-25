"""Notifier interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from price_monitor.diff import DiffResult


@dataclass(frozen=True, slots=True)
class NotifyResult:
    notifier: str
    delivered: bool
    detail: str


class Notifier(ABC):
    """Sends the alerts of a :class:`DiffResult` somewhere (terminal, chat, e-mail...)."""

    name: str = "notifier"

    @abstractmethod
    def send(self, diff: DiffResult) -> NotifyResult:
        """Deliver ``diff.alerts``. Must not raise for "nothing to send"."""
