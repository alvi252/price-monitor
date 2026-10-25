"""Crawler and fetcher tests against an in-memory HTTP transport - no network."""

from __future__ import annotations

import asyncio
import hashlib
from collections import Counter
from collections.abc import Callable
from urllib.robotparser import RobotFileParser

import httpx
import pytest

from price_monitor.config import Settings
from price_monitor.scraper.crawler import CategoryNotFoundError, Crawler
from price_monitor.scraper.http import FetchError, PoliteFetcher

from .conftest import read_fixture

BASE = "https://books.toscrape.com/"
CATEGORY = BASE + "catalogue/category/books/mystery_3/"
PRODUCT_TEMPLATE = read_fixture("product_sharp_objects.html")


def product_html(url: str) -> str:
    """Product page with a URL-derived UPC, so every product has its own SKU."""
    upc = hashlib.sha1(url.encode()).hexdigest()[:16]
    return PRODUCT_TEMPLATE.replace("e00eb4fd7b871a48", upc)


def site_handler(overrides: dict[str, Callable[[], httpx.Response]] | None = None):
    pages = {
        BASE: read_fixture("home.html"),
        CATEGORY + "index.html": read_fixture("category_mystery_page1.html"),
        CATEGORY + "page-2.html": read_fixture("category_mystery_page2.html"),
    }
    seen: Counter[str] = Counter()

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        seen[url] += 1
        if overrides and url in overrides:
            return overrides[url]()
        if url in pages:
            return httpx.Response(200, text=pages[url])
        if "/catalogue/" in url and url.endswith("/index.html"):
            return httpx.Response(200, text=product_html(url))
        return httpx.Response(404)

    return handler, seen


class FakeSleep:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def run_crawl(handler, settings: Settings | None = None, sleep: FakeSleep | None = None):
    settings = settings or Settings(categories=["mystery"], request_delay=0)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            fetcher = PoliteFetcher(client, delay=0, sleep=sleep or FakeSleep())
            return await Crawler(settings, fetcher).crawl()

    return asyncio.run(go())


def fetch(handler, url: str, **kwargs) -> tuple[str | Exception, PoliteFetcher]:
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            fetcher = PoliteFetcher(client, delay=0, **kwargs)
            try:
                return await fetcher.get_text(url), fetcher
            except FetchError as exc:
                return exc, fetcher

    return asyncio.run(go())


def test_crawl_follows_pagination_and_parses_every_product() -> None:
    handler, seen = site_handler()
    progress: list[tuple[int, int]] = []
    settings = Settings(categories=["Mystery"], request_delay=0)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            fetcher = PoliteFetcher(client, delay=0, sleep=FakeSleep())
            return await Crawler(settings, fetcher).crawl(lambda d, t: progress.append((d, t)))

    result = asyncio.run(go())

    assert result.ok
    assert result.categories == {"Mystery": 32}
    assert len(result.products) == 32
    assert len({p.sku for p in result.products}) == 32
    assert all(p.category == "Mystery" for p in result.products)
    assert result.stats.requests == 1 + 2 + 32  # home + 2 listing pages + products
    assert max(seen.values()) == 1  # nothing fetched twice
    assert progress[-1] == (32, 32)


def test_unknown_category_lists_available_ones() -> None:
    handler, _ = site_handler()
    with pytest.raises(CategoryNotFoundError) as exc_info:
        run_crawl(handler, Settings(categories=["Cookbooks"], request_delay=0))
    assert "Cookbooks" in str(exc_info.value)
    assert "Mystery" in exc_info.value.available


def test_failed_product_is_reported_not_silently_dropped() -> None:
    broken = BASE + "catalogue/sharp-objects_997/index.html"
    handler, _ = site_handler({broken: lambda: httpx.Response(404)})

    result = run_crawl(handler)

    assert not result.ok
    assert len(result.products) == 31
    assert len(result.errors) == 1
    assert "sharp-objects_997" in result.errors[0]


def test_retries_5xx_with_backoff_then_succeeds() -> None:
    responses = iter([httpx.Response(503), httpx.Response(502), httpx.Response(200, text="ok")])
    sleep = FakeSleep()

    text, fetcher = fetch(lambda _: next(responses), BASE, sleep=sleep, backoff_base=0.5)

    assert text == "ok"
    assert fetcher.stats.retries == 2
    assert fetcher.stats.requests == 3
    assert 0.5 <= sleep.calls[0] <= 1.0  # base * 2**0 + jitter
    assert 1.0 <= sleep.calls[1] <= 1.5  # base * 2**1 + jitter


def test_honours_retry_after_header() -> None:
    responses = iter(
        [httpx.Response(429, headers={"Retry-After": "2"}), httpx.Response(200, text="ok")]
    )
    sleep = FakeSleep()

    text, _ = fetch(lambda _: next(responses), BASE, sleep=sleep)

    assert text == "ok"
    assert sleep.calls == [2.0]


def test_client_errors_are_not_retried() -> None:
    error, fetcher = fetch(lambda _: httpx.Response(404), BASE, sleep=FakeSleep())

    assert isinstance(error, FetchError)
    assert error.reason == "HTTP 404"
    assert fetcher.stats.requests == 1


def test_gives_up_after_max_retries_on_timeouts() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    error, fetcher = fetch(handler, BASE, sleep=FakeSleep(), max_retries=2)

    assert isinstance(error, FetchError)
    assert "ReadTimeout" in error.reason
    assert fetcher.stats.requests == 3
    assert fetcher.stats.failures == 1


def test_robots_txt_disallow_blocks_request() -> None:
    robots = RobotFileParser()
    robots.parse(["User-agent: *", "Disallow: /private/"])
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, text="secret")

    error, _ = fetch(handler, BASE + "private/page.html", robots=robots, sleep=FakeSleep())

    assert isinstance(error, FetchError)
    assert "robots.txt" in error.reason
    assert calls == []


def test_concurrency_limit_is_respected() -> None:
    in_flight = 0
    peak = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return httpx.Response(200, text="ok")

    async def go() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            fetcher = PoliteFetcher(client, concurrency=3, delay=0)
            await asyncio.gather(*(fetcher.get_text(f"{BASE}p{i}") for i in range(12)))

    asyncio.run(go())
    assert peak == 3
