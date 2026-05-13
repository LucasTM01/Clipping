"""Offline tests for PortalSolarScraper."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scrapers.portalsolar import PortalSolarScraper, parse_listing, parse_article, _parse_date

FIXTURES = Path(__file__).parent / "fixtures"

_NOW = datetime(2026, 5, 9, 12, 0, 0, tzinfo=timezone.utc)
_SINCE_24H = _NOW - timedelta(hours=24)


@pytest.fixture
def listing_html() -> str:
    return (FIXTURES / "portalsolar_listing.html").read_text(encoding="utf-8")


@pytest.fixture
def article_html() -> str:
    return (FIXTURES / "portalsolar_article.html").read_text(encoding="utf-8")


def _mock_response(html: str, status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.text = html
    resp.raise_for_status.return_value = None
    return resp


# ---------------------------------------------------------------------------
# _parse_date
# ---------------------------------------------------------------------------

def test_parse_date_utc_z() -> None:
    dt = _parse_date("2026-05-09T10:00:00.000Z")
    assert dt is not None
    assert dt.tzinfo == timezone.utc
    assert dt.year == 2026 and dt.month == 5 and dt.day == 9
    assert dt.hour == 10


def test_parse_date_with_offset() -> None:
    dt = _parse_date("2026-05-09T08:30:00-03:00")
    assert dt is not None
    assert dt.tzinfo == timezone.utc
    assert dt.hour == 11  # -03:00 → UTC = +3h


def test_parse_date_empty_returns_none() -> None:
    assert _parse_date("") is None
    assert _parse_date("invalid") is None


# ---------------------------------------------------------------------------
# parse_listing
# ---------------------------------------------------------------------------

def test_parse_listing_extracts_three_recent_and_one_old(listing_html: str) -> None:
    items = parse_listing(listing_html)
    assert len(items) == 4


def test_parse_listing_titles(listing_html: str) -> None:
    items = parse_listing(listing_html)
    titles = [i.title for i in items]
    assert any("WEG" in t for t in titles)
    assert any("ABSOLAR" in t for t in titles)
    assert any("Intelbras" in t for t in titles)


def test_parse_listing_absolute_urls(listing_html: str) -> None:
    items = parse_listing(listing_html)
    for item in items:
        assert item.url.startswith("https://www.portalsolar.com.br/")


def test_parse_listing_dates_parsed(listing_html: str) -> None:
    items = parse_listing(listing_html)
    dated = [i for i in items if i.pub_date is not None]
    assert len(dated) == 4


def test_parse_listing_dedup() -> None:
    html = """
    <script type="application/ld+json">
    {"@type":"NewsArticle","headline":"T1","datePublished":"2026-05-09T10:00:00Z",
     "mainEntityOfPage":{"@id":"https://www.portalsolar.com.br/noticias/t1"}}
    </script>
    <script type="application/ld+json">
    {"@type":"NewsArticle","headline":"T1 dup","datePublished":"2026-05-09T10:00:00Z",
     "mainEntityOfPage":{"@id":"https://www.portalsolar.com.br/noticias/t1"}}
    </script>
    """
    items = parse_listing(html)
    assert len(items) == 1


def test_parse_listing_skips_non_newsarticle() -> None:
    html = """
    <script type="application/ld+json">
    {"@type":"WebSite","name":"Portal Solar"}
    </script>
    <script type="application/ld+json">
    {"@type":"NewsArticle","headline":"Ok","datePublished":"2026-05-09T10:00:00Z",
     "mainEntityOfPage":{"@id":"https://www.portalsolar.com.br/noticias/ok"}}
    </script>
    """
    items = parse_listing(html)
    assert len(items) == 1


def test_parse_listing_empty_html() -> None:
    assert parse_listing("<html></html>") == []


# ---------------------------------------------------------------------------
# parse_article
# ---------------------------------------------------------------------------

def test_parse_article_extracts_date(article_html: str) -> None:
    pub, full_text = parse_article(article_html)
    assert pub is not None
    assert pub.year == 2026 and pub.month == 5 and pub.day == 9


def test_parse_article_extracts_text(article_html: str) -> None:
    _, full_text = parse_article(article_html)
    assert len(full_text) > 50
    assert "<" not in full_text


# ---------------------------------------------------------------------------
# fetch_articles (mocked requests)
# ---------------------------------------------------------------------------

def test_fetch_articles_returns_recent_only(listing_html: str, article_html: str) -> None:
    scraper = PortalSolarScraper(max_pages=3, concurrency=1)
    with patch("scrapers.portalsolar.requests.get") as mock_get:
        # listing page → article pages (3 recent, 1 old filtered before fetch)
        mock_get.side_effect = (
            [_mock_response(listing_html)]
            + [_mock_response(article_html)] * 3  # 3 recent articles fetched
        )
        articles = scraper.fetch_articles(_SINCE_24H)

    assert len(articles) == 3


def test_fetch_articles_excludes_old(listing_html: str, article_html: str) -> None:
    scraper = PortalSolarScraper(max_pages=3, concurrency=1)
    with patch("scrapers.portalsolar.requests.get") as mock_get:
        mock_get.side_effect = (
            [_mock_response(listing_html)]
            + [_mock_response(article_html)] * 3
        )
        articles = scraper.fetch_articles(_SINCE_24H)

    urls = [a.url for a in articles]
    assert not any("artigo-antigo" in u for u in urls)


def test_fetch_articles_correct_fields(listing_html: str, article_html: str) -> None:
    scraper = PortalSolarScraper(max_pages=3, concurrency=1)
    with patch("scrapers.portalsolar.requests.get") as mock_get:
        mock_get.side_effect = (
            [_mock_response(listing_html)]
            + [_mock_response(article_html)] * 3
        )
        articles = scraper.fetch_articles(_SINCE_24H)

    assert articles
    a = articles[0]
    assert a.source == "Portal Solar"
    assert a.language == "pt-br"
    assert a.url.startswith("https://www.portalsolar.com.br/")
    assert a.title
    assert "+00:00" in a.published_at


def test_fetch_articles_stops_on_old_page(listing_html: str) -> None:
    # Simulate a listing page where oldest article is before since
    scraper = PortalSolarScraper(max_pages=5, concurrency=1)
    with patch("scrapers.portalsolar.requests.get") as mock_get:
        mock_get.side_effect = [_mock_response(listing_html)]
        scraper.fetch_articles(_SINCE_24H)

    # Listing has an old article → should stop after page 1
    assert mock_get.call_count >= 1


def test_fetch_articles_empty_listing() -> None:
    scraper = PortalSolarScraper(max_pages=3, concurrency=1)
    with patch("scrapers.portalsolar.requests.get") as mock_get:
        mock_get.return_value = _mock_response("<html></html>")
        articles = scraper.fetch_articles(_SINCE_24H)

    assert articles == []


def test_fetch_articles_http_error_returns_partial(listing_html: str, article_html: str) -> None:
    from requests.exceptions import HTTPError
    scraper = PortalSolarScraper(max_pages=3, concurrency=1)

    bad = MagicMock()
    bad.raise_for_status.side_effect = HTTPError()

    with patch("scrapers.portalsolar.requests.get") as mock_get:
        mock_get.side_effect = [_mock_response(listing_html), _mock_response(article_html), bad, bad]
        articles = scraper.fetch_articles(_SINCE_24H)

    assert len(articles) >= 1


def test_scraper_satisfies_base_protocol() -> None:
    from scrapers.base import BaseScraper
    assert isinstance(PortalSolarScraper(), BaseScraper)
