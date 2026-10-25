"""Pure HTML parsers for the books.toscrape.com sandbox shop.

Parsers never touch the network: they take HTML plus the URL it came from and
return plain data. That keeps them fast to test against saved fixtures and makes
it obvious what has to change when the scraper is pointed at a different shop.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser, LexborNode

from price_monitor.models import Product, to_money

CURRENCY_SYMBOLS = {"£": "GBP", "€": "EUR", "$": "USD"}
RATING_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
_AVAILABLE_RE = re.compile(r"\((\d+)\s+available\)", re.IGNORECASE)
_PRICE_RE = re.compile(r"\d[\d.,\s]*")


class ParseError(ValueError):
    """Raised when a page does not have the structure the parser expects."""


@dataclass(frozen=True, slots=True)
class ListingPage:
    product_urls: list[str]
    next_url: str | None
    total_results: int | None


def parse_price(text: str) -> tuple[Decimal, str]:
    """Split a displayed price into amount and ISO currency code.

    >>> parse_price("£51.77")
    (Decimal('51.77'), 'GBP')
    >>> parse_price("Â£9.50")  # mis-decoded pound sign is still recognised
    (Decimal('9.50'), 'GBP')
    >>> parse_price("$1,234.56")
    (Decimal('1234.56'), 'USD')
    >>> parse_price("12,50 €")
    (Decimal('12.50'), 'EUR')
    """
    match = _PRICE_RE.search(text)
    if match is None:
        raise ParseError(f"No price found in {text!r}")
    raw = re.sub(r"\s", "", match.group())
    if "," in raw and "." in raw:
        # Whichever separator comes last is the decimal one: "1,234.56" / "1.234,56".
        decimal_sep, thousands_sep = (",", ".") if raw.rfind(",") > raw.rfind(".") else (".", ",")
        raw = raw.replace(thousands_sep, "").replace(decimal_sep, ".")
    elif "," in raw:
        # "12,50" is a decimal comma, "1,234" is a thousands separator.
        raw = raw.replace(",", "") if len(raw.rpartition(",")[2]) == 3 else raw.replace(",", ".")
    try:
        amount = to_money(raw)
    except InvalidOperation as exc:
        raise ParseError(f"Bad price {text!r}") from exc
    currency = next((code for sym, code in CURRENCY_SYMBOLS.items() if sym in text), "UNKNOWN")
    return amount, currency


def parse_availability(text: str) -> tuple[bool, int | None]:
    """Return ``(in_stock, stock_count)`` from an availability label.

    >>> parse_availability("In stock (22 available)")
    (True, 22)
    >>> parse_availability("Out of stock")
    (False, 0)
    >>> parse_availability("In stock")
    (True, None)
    """
    normalized = " ".join(text.split()).lower()
    if not normalized or "out of stock" in normalized or "unavailable" in normalized:
        return False, 0
    match = _AVAILABLE_RE.search(normalized)
    count = int(match.group(1)) if match else None
    if count == 0:
        return False, 0
    return True, count


def parse_rating(node: LexborNode | None) -> int | None:
    if node is None:
        return None
    classes = (node.attributes.get("class") or "").lower().split()
    return next((RATING_WORDS[c] for c in classes if c in RATING_WORDS), None)


def parse_category_links(html: str, page_url: str) -> dict[str, str]:
    """Map category display name -> absolute category URL from the sidebar."""
    tree = LexborHTMLParser(html)
    links: dict[str, str] = {}
    for anchor in tree.css("div.side_categories ul.nav-list ul li > a"):
        name = anchor.text(strip=True)
        href = anchor.attributes.get("href")
        if name and href:
            links[name] = urljoin(page_url, href)
    if not links:
        raise ParseError(f"No categories found on {page_url}")
    return links


def parse_listing_page(html: str, page_url: str) -> ListingPage:
    """Extract product links, the next-page link and the result count."""
    tree = LexborHTMLParser(html)
    urls: list[str] = []
    for anchor in tree.css("article.product_pod h3 a"):
        href = anchor.attributes.get("href")
        if href:
            urls.append(urljoin(page_url, href))

    next_anchor = tree.css_first("ul.pager li.next a")
    next_href = next_anchor.attributes.get("href") if next_anchor else None
    next_url = urljoin(page_url, next_href) if next_href else None

    total: int | None = None
    counter = tree.css_first("form.form-horizontal strong")
    if counter is not None and counter.text(strip=True).isdigit():
        total = int(counter.text(strip=True))

    return ListingPage(product_urls=urls, next_url=next_url, total_results=total)


def parse_product_page(html: str, url: str, *, fallback_category: str = "") -> Product:
    """Parse a product detail page into a :class:`Product`."""
    tree = LexborHTMLParser(html)
    main = tree.css_first("div.product_main")
    if main is None:
        raise ParseError(f"Not a product page: {url}")

    title = main.css_first("h1")
    price_node = main.css_first("p.price_color")
    if title is None or price_node is None:
        raise ParseError(f"Product title or price missing on {url}")

    info = _product_info_table(tree)
    sku = info.get("UPC")
    if not sku:
        raise ParseError(f"UPC missing on {url}")

    price, currency = parse_price(price_node.text(strip=True))

    availability_node = main.css_first("p.availability")
    availability_text = info.get("Availability") or (
        availability_node.text(strip=True) if availability_node else ""
    )
    in_stock, stock_count = parse_availability(availability_text)

    breadcrumb = [a.text(strip=True) for a in tree.css("ul.breadcrumb li a")]
    # Home > Books > <Category> > (active product name)
    category = breadcrumb[-1] if len(breadcrumb) >= 3 else fallback_category

    return Product(
        sku=sku,
        name=title.text(strip=True),
        category=category or fallback_category or "Uncategorised",
        price=price,
        currency=currency,
        in_stock=in_stock,
        stock_count=stock_count,
        rating=parse_rating(main.css_first("p.star-rating")),
        url=url,
    )


def _product_info_table(tree: LexborHTMLParser) -> dict[str, str]:
    info: dict[str, str] = {}
    for row in tree.css("table.table-striped tr"):
        header, cell = row.css_first("th"), row.css_first("td")
        if header is not None and cell is not None:
            info[header.text(strip=True)] = cell.text(strip=True)
    return info
