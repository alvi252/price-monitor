from __future__ import annotations

from decimal import Decimal

import pytest

from price_monitor.scraper.parsers import (
    ParseError,
    parse_availability,
    parse_category_links,
    parse_listing_page,
    parse_price,
    parse_product_page,
)

from .conftest import read_fixture

BASE = "https://books.toscrape.com/"
MYSTERY = BASE + "catalogue/category/books/mystery_3/index.html"
SHARP_OBJECTS = BASE + "catalogue/sharp-objects_997/index.html"


def test_category_links_from_sidebar() -> None:
    links = parse_category_links(read_fixture("home.html"), BASE + "index.html")

    assert len(links) == 50
    assert links["Mystery"] == MYSTERY
    assert links["Food and Drink"].endswith("/food-and-drink_33/index.html")
    assert "Books" not in links  # the parent "Books" node is not a category


def test_listing_first_page_has_next_link_and_absolute_urls() -> None:
    page = parse_listing_page(read_fixture("category_mystery_page1.html"), MYSTERY)

    assert len(page.product_urls) == 20
    assert page.product_urls[0] == SHARP_OBJECTS
    assert page.next_url == BASE + "catalogue/category/books/mystery_3/page-2.html"
    assert page.total_results == 32


def test_listing_last_page_has_no_next_link() -> None:
    url = BASE + "catalogue/category/books/mystery_3/page-2.html"
    page = parse_listing_page(read_fixture("category_mystery_page2.html"), url)

    assert len(page.product_urls) == 12
    assert page.next_url is None


def test_product_page() -> None:
    product = parse_product_page(read_fixture("product_sharp_objects.html"), SHARP_OBJECTS)

    assert product.sku == "e00eb4fd7b871a48"
    assert product.name == "Sharp Objects"
    assert product.category == "Mystery"
    assert product.price == Decimal("47.82")
    assert product.currency == "GBP"
    assert product.in_stock is True
    assert product.stock_count == 20
    assert product.rating == 4  # the main product, not the "related products" block
    assert product.url == SHARP_OBJECTS


def test_product_page_out_of_stock() -> None:
    html = read_fixture("product_out_of_stock_variant.html")
    product = parse_product_page(html, SHARP_OBJECTS)

    assert product.in_stock is False
    assert product.stock_count == 0


def test_non_product_page_raises() -> None:
    with pytest.raises(ParseError):
        parse_product_page(read_fixture("home.html"), BASE)


@pytest.mark.parametrize(
    ("text", "amount", "currency"),
    [
        ("£51.77", "51.77", "GBP"),
        ("Â£51.77", "51.77", "GBP"),
        ("€ 1.299,00", "1299.00", "EUR"),
        ("$12", "12.00", "USD"),
        ("1,234", "1234.00", "UNKNOWN"),
    ],
)
def test_parse_price(text: str, amount: str, currency: str) -> None:
    assert parse_price(text) == (Decimal(amount), currency)


def test_parse_price_without_digits() -> None:
    with pytest.raises(ParseError):
        parse_price("Call for price")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("In stock (22 available)", (True, 22)),
        ("  In stock\n (1 available) ", (True, 1)),
        ("In stock (0 available)", (False, 0)),
        ("In stock", (True, None)),
        ("Out of stock", (False, 0)),
        ("", (False, 0)),
    ],
)
def test_parse_availability(text: str, expected: tuple[bool, int | None]) -> None:
    assert parse_availability(text) == expected
