"""Offline tests for CanalSolarScraper."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scrapers.canalsolar import (
    CanalSolarScraper,
    parse_listing,
    parse_article,
    _parse_pt_date,
)

FIXTURES = Path(__file__).parent / "fixtures"

_NOW = datetime(2026, 5, 9, 15, 0, 0, tzinfo=timezone.utc)
_SINCE_24H = _NOW - timedelta(hours=24)  # 2026-05-08T15:00Z


@pytest.fixture
def listing_html() -> str:
    return (FIXTURES / "canalsolar_listing.html").read_text(encoding="utf-8")


@pytest.fixture
def article_html() -> str:
    return (FIXTURES / "canalsolar_article.html").read_text(encoding="utf-8")


def _mock_response(html: str, status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.text = html
    resp.raise_for_status.return_value = None
    return resp


# ---------------------------------------------------------------------------
# _parse_pt_date
# ---------------------------------------------------------------------------

def test_parse_pt_date_normal() -> None:
    dt = _parse_pt_date("9 de maio de 2026")
    assert dt is not None
    assert dt.year == 2026 and dt.month == 5 and dt.day == 9
    assert dt.tzinfo == timezone.utc


def test_parse_pt_date_midnight_br_tz() -> None:
    dt = _parse_pt_date("8 de maio de 2026")
    assert dt is not None
    # midnight BR_TZ (-03:00) = 03:00 UTC
    assert dt.hour == 3 and dt.minute == 0


def test_parse_pt_date_all_months() -> None:
    months = [
        ("janeiro", 1), ("fevereiro", 2), ("março", 3), ("abril", 4),
        ("maio", 5), ("junho", 6), ("julho", 7), ("agosto", 8),
        ("setembro", 9), ("outubro", 10), ("novembro", 11), ("dezembro", 12),
    ]
    for name, num in months:
        dt = _parse_pt_date(f"1 de {name} de 2026")
        assert dt is not None and dt.month == num, f"failed for {name}"


def test_parse_pt_date_invalid_returns_none() -> None:
    assert _parse_pt_date("") is None
    assert _parse_pt_date("today") is None
    assert _parse_pt_date("8 de unknownmonth de 2026") is None


# ---------------------------------------------------------------------------
# parse_listing
# ---------------------------------------------------------------------------

def test_parse_listing_count(listing_html: str) -> None:
    items = parse_listing(listing_html)
    assert len(items) == 4


def test_parse_listing_titles(listing_html: str) -> None:
    items = parse_listing(listing_html)
    titles = [i.title for i in items]
    assert any("WEG" in t for t in titles)
    assert any("Intelbras" in t for t in titles)
    assert any("ABSOLAR" in t for t in titles)


def test_parse_listing_absolute_urls(listing_html: str) -> None:
    items = parse_listing(listing_html)
    for item in items:
        assert item.url.startswith("https://canalsolar.com.br/")


def test_parse_listing_dates_parsed(listing_html: str) -> None:
    items = parse_listing(listing_html)
    assert all(i.approx_date is not None for i in items)


def test_parse_listing_old_article_date(listing_html: str) -> None:
    items = parse_listing(listing_html)
    old = next(i for i in items if "antigo" in i.url)
    assert old.approx_date is not None
    assert old.approx_date.month == 3  # março


def test_parse_listing_dedup() -> None:
    html = """
    <article class="elementor-post elementor-grid-item">
      <div class="elementor-post__text">
        <h3 class="elementor-post__title">
          <a href="https://canalsolar.com.br/artigo-1/">Artigo 1</a>
        </h3>
        <div class="elementor-post__meta-data">
          <span class="elementor-post-date">9 de maio de 2026</span>
        </div>
      </div>
    </article>
    <article class="elementor-post elementor-grid-item">
      <div class="elementor-post__text">
        <h3 class="elementor-post__title">
          <a href="https://canalsolar.com.br/artigo-1/">Artigo 1 dup</a>
        </h3>
        <div class="elementor-post__meta-data">
          <span class="elementor-post-date">9 de maio de 2026</span>
        </div>
      </div>
    </article>
    """
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

def test_fetch_articles_returns_recent_only(listing_html: str, article_html: str) -> None:
    scraper = CanalSolarScraper(concurrency=1)
    with patch("scrapers.canalsolar.requests.get") as mock_get:
        # listing + 3 recent article fetches (old article filtered before fetch)
        mock_get.side_effect = (
            [_mock_response(listing_html)]
            + [_mock_response(article_html)] * 3
        )
        articles = scraper.fetch_articles(_SINCE_24H)

    assert len(articles) == 3


def test_fetch_articles_excludes_old(listing_html: str, article_html: str) -> None:
    scraper = CanalSolarScraper(concurrency=1)
    with patch("scrapers.canalsolar.requests.get") as mock_get:
        mock_get.side_effect = (
            [_mock_response(listing_html)]
            + [_mock_response(article_html)] * 3
        )
        articles = scraper.fetch_articles(_SINCE_24H)

    assert not any("antigo" in a.url for a in articles)


def test_fetch_articles_correct_source(listing_html: str, article_html: str) -> None:
    scraper = CanalSolarScraper(concurrency=1)
    with patch("scrapers.canalsolar.requests.get") as mock_get:
        mock_get.side_effect = (
            [_mock_response(listing_html)]
            + [_mock_response(article_html)] * 3
        )
        articles = scraper.fetch_articles(_SINCE_24H)

    for a in articles:
        assert a.source == "Canal Solar"
        assert a.language == "pt-br"


def test_fetch_articles_empty_listing() -> None:
    scraper = CanalSolarScraper(concurrency=1)
    with patch("scrapers.canalsolar.requests.get") as mock_get:
        mock_get.return_value = _mock_response("<html></html>")
        articles = scraper.fetch_articles(_SINCE_24H)

    assert articles == []


def test_fetch_articles_listing_http_error() -> None:
    from requests.exceptions import HTTPError
    scraper = CanalSolarScraper(concurrency=1)
    bad = MagicMock()
    bad.raise_for_status.side_effect = HTTPError()
    with patch("scrapers.canalsolar.requests.get") as mock_get:
        mock_get.return_value = bad
        articles = scraper.fetch_articles(_SINCE_24H)

    assert articles == []


def test_fetch_articles_article_error_skipped(listing_html: str, article_html: str) -> None:
    from requests.exceptions import HTTPError
    scraper = CanalSolarScraper(concurrency=1)
    bad = MagicMock()
    bad.raise_for_status.side_effect = HTTPError()
    with patch("scrapers.canalsolar.requests.get") as mock_get:
        mock_get.side_effect = [
            _mock_response(listing_html),
            _mock_response(article_html),
            bad,
            bad,
        ]
        articles = scraper.fetch_articles(_SINCE_24H)

    assert len(articles) >= 1


def test_scraper_satisfies_base_protocol() -> None:
    from scrapers.base import BaseScraper
    assert isinstance(CanalSolarScraper(), BaseScraper)
