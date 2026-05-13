"""Offline tests for AutomotiveBusinessScraper."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scrapers.automotivebusiness import (
    AutomotiveBusinessScraper,
    parse_listing,
    parse_article,
)

FIXTURES = Path(__file__).parent / "fixtures"

_NOW = datetime(2026, 5, 9, 15, 0, 0, tzinfo=timezone.utc)
_SINCE_24H = _NOW - timedelta(hours=24)


@pytest.fixture
def listing_html() -> str:
    return (FIXTURES / "automotivebusiness_listing.html").read_text(encoding="utf-8")


@pytest.fixture
def article_html() -> str:
    return (FIXTURES / "automotivebusiness_article.html").read_text(encoding="utf-8")


def _mock_response(html: str, status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.text = html
    resp.raise_for_status.return_value = None
    return resp


# ---------------------------------------------------------------------------
# parse_listing
# ---------------------------------------------------------------------------

def test_parse_listing_count(listing_html: str) -> None:
    items = parse_listing(listing_html)
    assert len(items) == 5


def test_parse_listing_titles(listing_html: str) -> None:
    items = parse_listing(listing_html)
    titles = [i.title for i in items]
    assert any("Randoncorp" in t for t in titles)
    assert any("Marcopolo" in t for t in titles)
    assert any("Frasle" in t for t in titles)


def test_parse_listing_absolute_urls(listing_html: str) -> None:
    items = parse_listing(listing_html)
    for item in items:
        assert item.url.startswith("https://www.automotivebusiness.com.br/noticias/")


def test_parse_listing_no_section_url() -> None:
    # /noticias without a slug must not be captured
    html = '<a href="/noticias">Ver todas</a><a href="/noticias/">Listagem</a>'
    items = parse_listing(html)
    assert items == []


def test_parse_listing_dedup() -> None:
    html = (
        '<li><a href="/noticias/artigo-1">Artigo 1</a></li>'
        '<li><a href="/noticias/artigo-1">Artigo 1 dup</a></li>'
    )
    items = parse_listing(html)
    assert len(items) == 1


def test_parse_listing_empty() -> None:
    assert parse_listing("<html></html>") == []


# ---------------------------------------------------------------------------
# parse_article
# ---------------------------------------------------------------------------

def test_parse_article_extracts_date(article_html: str) -> None:
    pub, _ = parse_article(article_html)
    assert pub is not None
    assert pub.year == 2026 and pub.month == 5 and pub.day == 9


def test_parse_article_extracts_text(article_html: str) -> None:
    _, full_text = parse_article(article_html)
    assert len(full_text) > 50
    assert "<" not in full_text


# ---------------------------------------------------------------------------
# fetch_articles (mocked requests)
# ---------------------------------------------------------------------------

def test_fetch_articles_filters_by_since(listing_html: str, article_html: str) -> None:
    old_html = article_html.replace(
        '"datePublished": "2026-05-09T10:32:00"',
        '"datePublished": "2026-04-01T10:32:00"',
    ).replace(
        'content="2026-05-09T13:32:00+00:00"',
        'content="2026-04-01T13:32:00+00:00"',
    )
    scraper = AutomotiveBusinessScraper(max_pages=1, concurrency=1)
    with patch("scrapers.automotivebusiness.requests.get") as mock_get:
        mock_get.side_effect = (
            [_mock_response(listing_html)]
            + [_mock_response(article_html)] * 4
            + [_mock_response(old_html)]
        )
        articles = scraper.fetch_articles(_SINCE_24H)

    assert len(articles) == 4
    assert all(a.source == "Automotive Business" for a in articles)


def test_fetch_articles_correct_fields(listing_html: str, article_html: str) -> None:
    scraper = AutomotiveBusinessScraper(max_pages=1, concurrency=1)
    with patch("scrapers.automotivebusiness.requests.get") as mock_get:
        mock_get.side_effect = (
            [_mock_response(listing_html)]
            + [_mock_response(article_html)] * 5
        )
        articles = scraper.fetch_articles(_SINCE_24H)

    assert articles
    a = articles[0]
    assert a.source == "Automotive Business"
    assert a.language == "pt-br"
    assert a.url.startswith("https://www.automotivebusiness.com.br/noticias/")
    assert a.title
    assert "+00:00" in a.published_at


def test_fetch_articles_stops_on_empty_page(listing_html: str, article_html: str) -> None:
    scraper = AutomotiveBusinessScraper(max_pages=5, concurrency=1)
    with patch("scrapers.automotivebusiness.requests.get") as mock_get:
        mock_get.side_effect = (
            [_mock_response(listing_html)]
            + [_mock_response(article_html)] * 5
            + [_mock_response("<html></html>")]  # empty page 2 → stop
        )
        scraper.fetch_articles(_SINCE_24H)

    # listing page 1 + 5 article fetches + listing page 2 (empty → stop)
    assert mock_get.call_count == 7


def test_fetch_articles_empty_listing() -> None:
    scraper = AutomotiveBusinessScraper(max_pages=3, concurrency=1)
    with patch("scrapers.automotivebusiness.requests.get") as mock_get:
        mock_get.return_value = _mock_response("<html></html>")
        articles = scraper.fetch_articles(_SINCE_24H)

    assert articles == []


def test_fetch_articles_http_error_returns_partial(listing_html: str, article_html: str) -> None:
    from requests.exceptions import HTTPError
    scraper = AutomotiveBusinessScraper(max_pages=1, concurrency=1)
    bad = MagicMock()
    bad.raise_for_status.side_effect = HTTPError()
    with patch("scrapers.automotivebusiness.requests.get") as mock_get:
        mock_get.side_effect = [
            _mock_response(listing_html),
            _mock_response(article_html),
            bad, bad, bad, bad,
        ]
        articles = scraper.fetch_articles(_SINCE_24H)

    assert len(articles) >= 1


def test_scraper_satisfies_base_protocol() -> None:
    from scrapers.base import BaseScraper
    assert isinstance(AutomotiveBusinessScraper(), BaseScraper)
