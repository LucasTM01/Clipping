"""Offline tests for AutodataScraper parsing helpers."""

from __future__ import annotations

from datetime import date, datetime, timezone, timedelta
from pathlib import Path

import pytest

from scrapers.autodata import parse_listing, _date_from_url

FIXTURES = Path(__file__).parent / "fixtures"

_NOW = datetime(2026, 5, 8, 21, 0, 0, tzinfo=timezone.utc)
_SINCE_24H = _NOW - timedelta(hours=24)


@pytest.fixture
def listing_html() -> str:
    return (FIXTURES / "autodata_listing.html").read_text(encoding="utf-8")


# --- parse_listing ---

def test_parse_listing_extracts_unique_articles(listing_html: str) -> None:
    items, _ = parse_listing(listing_html)
    urls = [it.url for it in items]
    assert len(urls) == len(set(urls)), "duplicates leaked"
    assert len(items) == 3


def test_parse_listing_extracts_titles(listing_html: str) -> None:
    items, _ = parse_listing(listing_html)
    titles = {it.title for it in items}
    assert "Stellantis e Leapmotor avaliam ampliar parceria na Europa" in titles
    assert "Embraer entrega mais três aeronaves KC-390 para a FAB" in titles
    assert "Marcopolo registra crescimento no primeiro trimestre" in titles


def test_parse_listing_category_span_not_captured(listing_html: str) -> None:
    # Category labels live in <span> tags — regex only matches <a href=noticias/...>
    items, _ = parse_listing(listing_html)
    titles = {it.title for it in items}
    assert "Montadora" not in titles
    assert "Automotivo" not in titles


def test_parse_listing_has_more_pages(listing_html: str) -> None:
    _, has_more = parse_listing(listing_html)
    assert has_more is True


def test_parse_listing_no_more_pages() -> None:
    html = (
        '<li><a href="https://www.autodata.com.br/noticias/2026/05/08/foo/1/">'
        "Foo Title</a></li>"
    )
    _, has_more = parse_listing(html)
    assert has_more is False


def test_parse_listing_pub_dates_correct(listing_html: str) -> None:
    items, _ = parse_listing(listing_html)
    dates = {it.pub_date.date() for it in items}
    assert date(2026, 5, 8) in dates
    assert date(2026, 5, 7) in dates


def test_parse_listing_pub_dates_are_utc(listing_html: str) -> None:
    items, _ = parse_listing(listing_html)
    for item in items:
        assert item.pub_date.tzinfo == timezone.utc


def test_parse_listing_urls_normalised(listing_html: str) -> None:
    items, _ = parse_listing(listing_html)
    for item in items:
        assert item.url.endswith("/"), "URLs should have trailing slash"
        assert item.url.startswith("https://www.autodata.com.br/noticias/")


# --- _date_from_url ---

def test_date_from_url_midnight_br_to_utc() -> None:
    dt = _date_from_url("2026", "05", "08")
    # Midnight BR_TZ (UTC-3) = 03:00 UTC
    assert dt == datetime(2026, 5, 8, 3, 0, 0, tzinfo=timezone.utc)


def test_date_from_url_is_utc() -> None:
    dt = _date_from_url("2026", "01", "15")
    assert dt.tzinfo == timezone.utc


# --- since filtering ---

def test_since_filter_keeps_today(listing_html: str) -> None:
    items, _ = parse_listing(listing_html)
    recent = [i for i in items if i.pub_date >= _SINCE_24H]
    # Both 2026-05-08 articles (03:00 UTC) are within 24h of _NOW (21:00 UTC)
    assert len(recent) == 2


def test_since_filter_drops_old(listing_html: str) -> None:
    items, _ = parse_listing(listing_html)
    old = [i for i in items if i.pub_date < _SINCE_24H]
    # 2026-05-07T03:00 UTC is 42h before _NOW
    assert len(old) == 1
    assert "Marcopolo" in old[0].title
