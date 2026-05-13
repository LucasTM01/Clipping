"""Offline tests for AeroinScraper parsing helpers and datetime cascade."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from scrapers.aeroin import parse_listing, parse_article
from scrapers.playwright_utils import extract_published_at, BR_TZ


FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def listing_html() -> str:
    return (FIXTURES / "aeroin_listing.html").read_text(encoding="utf-8")


@pytest.fixture
def article_html() -> str:
    return (FIXTURES / "aeroin_article.html").read_text(encoding="utf-8")


def test_parse_listing_extracts_unique_articles(listing_html: str) -> None:
    items = parse_listing(listing_html)
    urls = [it.url for it in items]
    assert len(urls) == len(set(urls)), "duplicates leaked into listing"
    assert "https://aeroin.net/embraer-anuncia-novo-pedido/" in urls
    assert "https://aeroin.net/azul-amplia-frota/" in urls
    assert "https://aeroin.net/gol-resultado/" in urls


def test_parse_listing_extracts_titles(listing_html: str) -> None:
    items = parse_listing(listing_html)
    titles = {it.title for it in items}
    assert "Embraer anuncia novo pedido de E2" in titles
    assert "Azul amplia frota com novas aeronaves" in titles


def test_extract_published_at_prefers_jsonld(article_html: str) -> None:
    dt = extract_published_at(article_html)
    assert dt is not None
    assert dt.tzinfo is not None
    expected = datetime(2026, 5, 4, 15, 30, tzinfo=BR_TZ).astimezone(timezone.utc)
    assert dt == expected


def test_extract_published_at_meta_fallback() -> None:
    html = (
        '<html><head>'
        '<meta property="article:published_time" content="2026-05-01T10:00:00-03:00">'
        '</head><body></body></html>'
    )
    dt = extract_published_at(html)
    assert dt is not None
    expected = datetime(2026, 5, 1, 10, 0, tzinfo=BR_TZ).astimezone(timezone.utc)
    assert dt == expected


def test_extract_published_at_time_tag_fallback() -> None:
    html = '<html><body><time datetime="2026-04-30T08:00:00-03:00">30/04</time></body></html>'
    dt = extract_published_at(html)
    assert dt is not None
    expected = datetime(2026, 4, 30, 8, 0, tzinfo=BR_TZ).astimezone(timezone.utc)
    assert dt == expected


def test_extract_published_at_visible_text_rounds_to_start_of_day() -> None:
    html = "<html><body><p>Publicado em 25/04/2026</p></body></html>"
    dt = extract_published_at(html)
    assert dt is not None
    expected = datetime(2026, 4, 25, 0, 0, tzinfo=BR_TZ).astimezone(timezone.utc)
    assert dt == expected


def test_extract_published_at_returns_none_for_no_signal() -> None:
    html = "<html><body><p>Sem data por aqui</p></body></html>"
    assert extract_published_at(html) is None


def test_parse_article_returns_published_and_text(article_html: str) -> None:
    published, full_text = parse_article(article_html)
    assert published is not None
    assert published.tzinfo is not None
    assert "Embraer" in full_text
    assert len(full_text) > 200
