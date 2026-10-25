"""Crawl selected categories: listing pages (with pagination) -> product pages."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx

from price_monitor.config import Settings
from price_monitor.models import Product
from price_monitor.scraper.http import FetchError, FetchStats, PoliteFetcher, load_robots
from price_monitor.scraper.parsers import (
    ParseError,
    parse_category_links,
    parse_listing_page,
    parse_product_page,
)

log = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int], None]  # (done, total)


class CategoryNotFoundError(ValueError):
    def __init__(self, missing: list[str], available: list[str]) -> None:
        super().__init__(
            f"Unknown categories: {', '.join(missing)}. Available: {', '.join(sorted(available))}"
        )
        self.missing = missing
        self.available = available


@dataclass(slots=True)
class CrawlResult:
    products: list[Product]
    categories: dict[str, int]  # category name -> number of product URLs found
    errors: list[str] = field(default_factory=list)
    stats: FetchStats = field(default_factory=FetchStats)

    @property
    def ok(self) -> bool:
        return not self.errors


def resolve_categories(wanted: list[str], available: dict[str, str]) -> dict[str, str]:
    """Match configured names case-insensitively against the site's categories.

    >>> resolve_categories(["travel"], {"Travel": "https://x/travel"})
    {'Travel': 'https://x/travel'}
    """
    by_lower = {name.lower(): name for name in available}
    missing = [w for w in wanted if w.lower() not in by_lower]
    if missing:
        raise CategoryNotFoundError(missing, list(available))
    return {by_lower[w.lower()]: available[by_lower[w.lower()]] for w in wanted}


class Crawler:
    def __init__(self, settings: Settings, fetcher: PoliteFetcher) -> None:
        self.settings = settings
        self.fetcher = fetcher

    async def crawl(self, on_progress: ProgressCallback | None = None) -> CrawlResult:
        home_html = await self.fetcher.get_text(self.settings.base_url)
        categories = resolve_categories(
            self.settings.categories,
            parse_category_links(home_html, self.settings.base_url),
        )

        errors: list[str] = []
        listing_results = await asyncio.gather(
            *(self._collect_product_urls(name, url, errors) for name, url in categories.items())
        )

        # A product can be listed in several categories; fetch each page once.
        product_urls: dict[str, str] = {}
        counts: dict[str, int] = {}
        for name, urls in zip(categories, listing_results, strict=True):
            counts[name] = len(urls)
            for url in urls:
                product_urls.setdefault(url, name)

        total = len(product_urls)
        done = 0

        async def fetch_product(url: str, category: str) -> Product | None:
            nonlocal done
            try:
                html = await self.fetcher.get_text(url)
                return parse_product_page(html, url, fallback_category=category)
            except (FetchError, ParseError) as exc:
                errors.append(str(exc))
                log.error("Product failed: %s", exc)
                return None
            finally:
                done += 1
                if on_progress is not None:
                    on_progress(done, total)

        results = await asyncio.gather(*(fetch_product(u, c) for u, c in product_urls.items()))
        products = sorted((p for p in results if p is not None), key=lambda p: (p.category, p.name))
        return CrawlResult(
            products=products, categories=counts, errors=errors, stats=self.fetcher.stats
        )

    async def _collect_product_urls(self, name: str, url: str, errors: list[str]) -> list[str]:
        urls: list[str] = []
        next_url: str | None = url
        pages = 0
        while next_url and pages < self.settings.max_pages_per_category:
            try:
                page = parse_listing_page(await self.fetcher.get_text(next_url), next_url)
            except (FetchError, ParseError) as exc:
                errors.append(str(exc))
                log.error("Listing page failed for %s: %s", name, exc)
                break
            urls.extend(page.product_urls)
            next_url = page.next_url
            pages += 1
        log.info("Category %s: %d products on %d page(s)", name, len(urls), pages)
        return urls


async def crawl_site(
    settings: Settings, on_progress: ProgressCallback | None = None
) -> CrawlResult:
    """Convenience entry point: builds the HTTP client, robots.txt check and crawler."""
    timeout = httpx.Timeout(settings.timeout)
    limits = httpx.Limits(max_connections=settings.concurrency)
    headers = {"User-Agent": settings.user_agent, "Accept": "text/html"}
    async with httpx.AsyncClient(
        timeout=timeout, limits=limits, headers=headers, follow_redirects=True
    ) as client:
        robots = (
            await load_robots(client, settings.base_url) if settings.respect_robots_txt else None
        )
        fetcher = PoliteFetcher(
            client,
            concurrency=settings.concurrency,
            delay=settings.request_delay,
            max_retries=settings.max_retries,
            backoff_base=settings.backoff_base,
            robots=robots,
            user_agent=settings.user_agent,
        )
        return await Crawler(settings, fetcher).crawl(on_progress)
