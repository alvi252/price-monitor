"""A small, polite async HTTP fetcher.

* bounded concurrency (``asyncio.Semaphore``) and a pause after every request;
* per-request timeouts (configured on the ``httpx.AsyncClient``);
* retries with exponential backoff and jitter for timeouts, connection errors,
  HTTP 429 and 5xx - honouring ``Retry-After`` when the server sends it;
* optional ``robots.txt`` check before any URL is requested.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.robotparser import RobotFileParser

import httpx

log = logging.getLogger(__name__)

RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})

Sleep = Callable[[float], Awaitable[None]]


class FetchError(Exception):
    def __init__(self, url: str, reason: str) -> None:
        super().__init__(f"{url}: {reason}")
        self.url = url
        self.reason = reason


@dataclass(slots=True)
class FetchStats:
    requests: int = 0
    retries: int = 0
    failures: int = 0


def parse_retry_after(value: str | None) -> float | None:
    """Parse a numeric ``Retry-After`` header (HTTP-date values are ignored).

    >>> parse_retry_after("3")
    3.0
    >>> parse_retry_after("Wed, 21 Oct 2015 07:28:00 GMT") is None
    True
    """
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


class PoliteFetcher:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        concurrency: int = 4,
        delay: float = 0.25,
        max_retries: int = 3,
        backoff_base: float = 0.5,
        max_wait: float = 30.0,
        robots: RobotFileParser | None = None,
        user_agent: str = "*",
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._client = client
        self._semaphore = asyncio.Semaphore(concurrency)
        self._delay = delay
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._max_wait = max_wait
        self._robots = robots
        self._user_agent = user_agent
        self._sleep = sleep
        self.stats = FetchStats()

    async def get_text(self, url: str) -> str:
        if self._robots is not None and not self._robots.can_fetch(self._user_agent, url):
            self.stats.failures += 1
            raise FetchError(url, "disallowed by robots.txt")

        last_error = "unknown error"
        for attempt in range(self._max_retries + 1):
            retry_after: float | None = None
            try:
                response = await self._request(url)
            except httpx.TransportError as exc:  # timeouts, DNS, refused connections...
                last_error = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
            else:
                if response.status_code < 400:
                    return response.text
                last_error = f"HTTP {response.status_code}"
                if response.status_code not in RETRYABLE_STATUS:
                    break
                retry_after = parse_retry_after(response.headers.get("Retry-After"))

            if attempt == self._max_retries:
                break
            wait = retry_after
            if wait is None:
                wait = self._backoff_base * 2**attempt + random.uniform(0, self._backoff_base)
            wait = min(wait, self._max_wait)
            self.stats.retries += 1
            log.warning("Retrying %s in %.1fs (%s)", url, wait, last_error)
            await self._sleep(wait)

        self.stats.failures += 1
        raise FetchError(url, last_error)

    async def _request(self, url: str) -> httpx.Response:
        async with self._semaphore:
            self.stats.requests += 1
            try:
                return await self._client.get(url)
            finally:
                # Keep the slot busy a little longer: caps the request rate per slot.
                if self._delay:
                    await self._sleep(self._delay)


async def load_robots(client: httpx.AsyncClient, base_url: str) -> RobotFileParser | None:
    """Fetch and parse ``robots.txt``. ``None`` means "no restrictions published"."""
    robots_url = base_url.rstrip("/") + "/robots.txt"
    try:
        response = await client.get(robots_url)
    except httpx.TransportError as exc:
        log.warning("Could not load %s (%s); continuing without it", robots_url, exc)
        return None
    if response.status_code >= 400:
        return None
    parser = RobotFileParser(robots_url)
    parser.parse(response.text.splitlines())
    return parser
