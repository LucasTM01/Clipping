"""Offline tests for InfoMoneyScraper."""

from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scrapers.infomoney import InfoMoneyScraper, _strip_html, _parse_date_gmt

FIXTURES = Path(__file__).parent / "fixtures"

_NOW = datetime(2026, 5, 9, 3, 0, 0, tzinfo=timezone.utc)
_SINCE_24H = _NOW - timedelta(hours=24)


@pytest.fixture
def api_posts() -> list[dict]:
    return json.loads((FIXTURES / "infomoney_api.json").read_text(encoding="utf-8"))


def _mock_response(posts: list[dict], status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = posts
    resp.raise_for_status.return_value = None
    return resp


# ---------------------------------------------------------------------------
# _strip_html
# ---------------------------------------------------------------------------

def test_strip_html_removes_tags() -> None:
    assert _strip_html("<p>Hello <b>world</b></p>") == "Hello world"


def test_strip_html_decodes_entities() -> None:
    assert _strip_html("R&amp;D &amp; M&amp;A") == "R&D & M&A"


def test_strip_html_collapses_whitespace() -> None:
    assert _strip_html("  foo   bar  ") == "foo bar"


def test_strip_html_empty() -> None:
    assert _strip_html("") == ""


# ---------------------------------------------------------------------------
# _parse_date_gmt
# ---------------------------------------------------------------------------

def test_parse_date_gmt_returns_utc() -> None:
    dt = _parse_date_gmt("2026-05-09T02:03:44")
    assert dt is not None
    assert dt.tzinfo == timezone.utc
    assert dt.year == 2026 and dt.month == 5 and dt.day == 9
    assert dt.hour == 2 and dt.minute == 3


def test_parse_date_gmt_invalid_returns_none() -> None:
    assert _parse_date_gmt("not-a-date") is None
    assert _parse_date_gmt("") is None


# ---------------------------------------------------------------------------
# fetch_articles (mocked requests)
# ---------------------------------------------------------------------------

def test_fetch_articles_returns_recent_only(api_posts: list[dict]) -> None:
    scraper = InfoMoneyScraper(max_pages=5)
    with patch("scrapers.infomoney.requests.get") as mock_get:
        mock_get.side_effect = [
            _mock_response(api_posts),
            _mock_response([]),  # second page empty → stop
        ]
        articles = scraper.fetch_articles(_SINCE_24H)

    # fixture has 3 recent + 1 old → only 3 returned
    assert len(articles) == 3


def test_fetch_articles_excludes_old(api_posts: list[dict]) -> None:
    scraper = InfoMoneyScraper(max_pages=5)
    with patch("scrapers.infomoney.requests.get") as mock_get:
        mock_get.side_effect = [_mock_response(api_posts), _mock_response([])]
        articles = scraper.fetch_articles(_SINCE_24H)

    urls = [a.url for a in articles]
    assert not any("old-artigo-antigo" in u for u in urls)


def test_fetch_articles_correct_fields(api_posts: list[dict]) -> None:
    scraper = InfoMoneyScraper(max_pages=5)
    with patch("scrapers.infomoney.requests.get") as mock_get:
        mock_get.side_effect = [_mock_response(api_posts), _mock_response([])]
        articles = scraper.fetch_articles(_SINCE_24H)

    a = articles[0]
    assert a.source == "InfoMoney"
    assert a.language == "pt-br"
    assert a.url.startswith("https://www.infomoney.com.br/")
    assert a.title
    assert a.published_at
    assert "+00:00" in a.published_at  # timezone-aware


def test_fetch_articles_full_text_stripped(api_posts: list[dict]) -> None:
    scraper = InfoMoneyScraper(max_pages=5)
    with patch("scrapers.infomoney.requests.get") as mock_get:
        mock_get.side_effect = [_mock_response(api_posts), _mock_response([])]
        articles = scraper.fetch_articles(_SINCE_24H)

    for a in articles:
        assert "<" not in a.full_text  # no HTML tags in full_text


def test_fetch_articles_dedup(api_posts: list[dict]) -> None:
    # Return same posts twice (simulating overlap between pages)
    scraper = InfoMoneyScraper(max_pages=5)
    with patch("scrapers.infomoney.requests.get") as mock_get:
        mock_get.side_effect = [
            _mock_response(api_posts[:3]),
            _mock_response(api_posts[:3]),  # exact duplicate page
            _mock_response([]),
        ]
        articles = scraper.fetch_articles(_SINCE_24H)

    urls = [a.url for a in articles]
    assert len(urls) == len(set(urls))  # no duplicates


def test_fetch_articles_stops_on_old_article(api_posts: list[dict]) -> None:
    # Only the 3 recent + old fixture: scraper must stop at old one, not request page 2
    scraper = InfoMoneyScraper(max_pages=5)
    with patch("scrapers.infomoney.requests.get") as mock_get:
        mock_get.return_value = _mock_response(api_posts)
        scraper.fetch_articles(_SINCE_24H)

    # Should stop after 1 page (old article triggers early-exit)
    assert mock_get.call_count == 1


def test_fetch_articles_empty_response() -> None:
    scraper = InfoMoneyScraper(max_pages=5)
    with patch("scrapers.infomoney.requests.get") as mock_get:
        mock_get.return_value = _mock_response([])
        articles = scraper.fetch_articles(_SINCE_24H)

    assert articles == []


def test_fetch_articles_http_error_returns_partial() -> None:
    from requests.exceptions import HTTPError
    scraper = InfoMoneyScraper(max_pages=5)

    good_resp = _mock_response([{
        "id": 1,
        "link": "https://www.infomoney.com.br/economia/slug/",
        "title": {"rendered": "Título"},
        "date_gmt": "2026-05-09T01:00:00",
        "content": {"rendered": "<p>Texto.</p>"},
    }])
    bad_resp = MagicMock()
    bad_resp.status_code = 500
    http_err = HTTPError(response=bad_resp)
    bad_resp.raise_for_status.side_effect = http_err

    with patch("scrapers.infomoney.requests.get") as mock_get:
        mock_get.side_effect = [good_resp, bad_resp]
        articles = scraper.fetch_articles(_SINCE_24H)

    assert len(articles) == 1


def test_scraper_satisfies_base_protocol() -> None:
    from scrapers.base import BaseScraper
    assert isinstance(InfoMoneyScraper(), BaseScraper)
