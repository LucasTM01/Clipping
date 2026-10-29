"""Offline tests for the Google News RSS scraper (Reuters sections, WEG)."""

from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch
from urllib.parse import unquote

from scrapers.google_news import (
    GoogleNewsScraper,
    article_id,
    build_batch_payload,
    build_query,
    parse_batch_response,
    parse_decoding_params,
    strip_source_suffix,
)
from scrapers.reuters import ReutersScraper

_NOW = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)
_SINCE_24H = _NOW - timedelta(hours=24)

_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Google News</title>
<item>
  <title>Airbus holds annual targets, bets on year-end delivery surge - Reuters</title>
  <link>https://news.google.com/rss/articles/CBMiAAA?oc=5</link>
  <pubDate>Wed, 28 Oct 2026 22:09:00 GMT</pubDate>
  <source url="https://www.reuters.com">Reuters</source>
</item>
<item>
  <title>Embraer wins KC-390 order from NATO ally - Reuters</title>
  <link>https://news.google.com/rss/articles/CBMiBBB?oc=5</link>
  <pubDate>Thu, 29 Oct 2026 09:00:00 GMT</pubDate>
  <source url="https://www.reuters.com">Reuters</source>
</item>
<item>
  <title>Old story - Reuters</title>
  <link>https://news.google.com/rss/articles/CBMiCCC?oc=5</link>
  <pubDate>Mon, 26 Oct 2026 09:00:00 GMT</pubDate>
  <source url="https://www.reuters.com">Reuters</source>
</item>
</channel></rss>"""

_ARTICLE_PAGE = '<c-wiz><div jscontroller="x" data-n-a-sg="SIG_{gid}" data-n-a-ts="1730000000"></div></c-wiz>'

_DECODED = {
    "CBMiAAA": "https://www.reuters.com/business/aerospace-defense/airbus-holds-annual-targets-2026-10-28/",
    "CBMiBBB": "https://www.reuters.com/business/aerospace-defense/embraer-kc-390-nato-2026-10-29/",
}


def _batch_response(gids_in_order: list[str]) -> str:
    """batchexecute answer — deliberately in reverse order, keyed by rpc id."""
    rows = [
        ["wrb.fr", "Fbv4je", json.dumps(["garturlres", _DECODED[g], 1]), None, None, None, str(i)]
        for i, g in enumerate(gids_in_order)
    ][::-1]
    rows += [["di", 42], ["af.httprm", 42, "123", 1]]
    return ")]}'\n\n" + json.dumps(rows) + "\n\n"


def _resp(text: str = "", content: bytes | None = None) -> MagicMock:
    r = MagicMock()
    r.text = text
    r.content = content if content is not None else text.encode()
    r.status_code = 200
    r.raise_for_status = MagicMock()
    return r


# --- pure helpers ---

def test_build_query_rounds_window_up_to_days() -> None:
    assert build_query("weg.net", _NOW - timedelta(hours=15), _NOW) == "site:weg.net when:1d"
    assert build_query("weg.net", _NOW - timedelta(hours=25), _NOW) == "site:weg.net when:2d"


def test_strip_source_suffix() -> None:
    assert strip_source_suffix("Airbus holds targets - Reuters", "Reuters") == "Airbus holds targets"
    assert strip_source_suffix("No suffix here", "Reuters") == "No suffix here"
    assert strip_source_suffix("Keeps - dashes - inside", "") == "Keeps - dashes - inside"


def test_article_id() -> None:
    assert article_id("https://news.google.com/rss/articles/CBMiXYZ?oc=5") == "CBMiXYZ"


def test_parse_decoding_params() -> None:
    assert parse_decoding_params(_ARTICLE_PAGE.format(gid="A")) == ("1730000000", "SIG_A")
    assert parse_decoding_params("<html></html>") is None


def test_build_batch_payload_tags_each_request_with_its_index() -> None:
    payload = unquote(build_batch_payload([("G0", "1", "S0"), ("G1", "2", "S1")]))
    reqs = json.loads(payload.removeprefix("f.req="))[0]
    assert [r[3] for r in reqs] == ["0", "1"]
    assert '"G1",2,"S1"' in reqs[1][1]


def test_parse_batch_response_maps_by_index_not_order() -> None:
    out = parse_batch_response(_batch_response(["CBMiAAA", "CBMiBBB"]))
    assert out == {0: _DECODED["CBMiAAA"], 1: _DECODED["CBMiBBB"]}


def test_parse_batch_response_garbage() -> None:
    assert parse_batch_response("not json") == {}


# --- fetch_articles (mocked network) ---

def _fake_session(decode_ok: bool = True) -> MagicMock:
    session = MagicMock()
    session.headers = {}

    def get(url, timeout=None):
        if "/rss/search" in url:
            return _resp(content=_RSS.encode())
        gid = url.rstrip("/").split("/")[-1]
        return _resp(_ARTICLE_PAGE.format(gid=gid))

    def post(url, data=None, headers=None, timeout=None):
        if not decode_ok:
            raise ConnectionError("boom")
        return _resp(_batch_response(["CBMiAAA", "CBMiBBB"]))

    session.get.side_effect = get
    session.post.side_effect = post
    return session


def test_reuters_fetch_decodes_and_filters_by_since() -> None:
    scraper = ReutersScraper()
    with patch("scrapers.google_news.requests.Session", return_value=_fake_session()):
        articles = scraper.fetch_articles(_SINCE_24H)

    assert {a.url for a in articles} == set(_DECODED.values())
    assert all(a.source == "Reuters Aerospace & Defense" for a in articles)
    assert "Embraer wins KC-390 order from NATO ally" in {a.title for a in articles}
    assert all(not a.title.endswith("- Reuters") for a in articles)
    assert all("+00:00" in a.published_at for a in articles)


def test_fetch_keeps_google_link_when_decoding_fails() -> None:
    scraper = ReutersScraper()
    with patch("scrapers.google_news.requests.Session", return_value=_fake_session(decode_ok=False)):
        articles = scraper.fetch_articles(_SINCE_24H)

    assert len(articles) == 2
    assert all(a.url.startswith("https://news.google.com/") for a in articles)


def test_fetch_listing_error_returns_empty() -> None:
    session = MagicMock()
    session.headers = {}
    session.get.side_effect = ConnectionError("down")
    with patch("scrapers.google_news.requests.Session", return_value=session):
        assert ReutersScraper().fetch_articles(_SINCE_24H) == []


def test_url_prefix_drops_decoded_urls_outside_section() -> None:
    scraper = GoogleNewsScraper(
        "Reuters Energy", site="reuters.com/business/energy",
        base_url="https://www.reuters.com/business/energy/",
        url_pattern=r"https://www\.reuters\.com/business/energy/",
    )
    with patch("scrapers.google_news.requests.Session", return_value=_fake_session()):
        assert scraper.fetch_articles(_SINCE_24H) == []


def test_reuters_scraper_section_config() -> None:
    s = ReutersScraper("Reuters Energy", "/business/energy/")
    assert s.site == "reuters.com/business/energy"
    assert s.base_url == "https://www.reuters.com/business/energy/"
    assert s.language == "en"


def test_scraper_satisfies_base_protocol() -> None:
    from scrapers.base import BaseScraper
    assert isinstance(ReutersScraper(), BaseScraper)
