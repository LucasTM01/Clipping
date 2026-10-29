"""Offline tests for the Bloomberg sitemap + Industries RSS scraper."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import pytest

import scrapers.bloomberg as bbg
from scrapers.bloomberg import (
    SECTION_ENERGY,
    SECTION_INDUSTRIES,
    SECTION_TRANSPORTATION,
    BloombergScraper,
    _Story,
    classify,
    merge_stories,
    parse_industries_rss,
    parse_sitemap,
)

_NOW = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)
_SINCE_24H = _NOW - timedelta(hours=24)

_SITEMAP = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">
  <url><loc>https://www.bloomberg.com/news/articles/2026-10-29/shell-lifts-buyback-as-refining-windfall-grows</loc>
    <news:news><news:publication_date>2026-10-29T07:06:00.000Z</news:publication_date>
    <news:title>Shell Lifts Buyback to $5 Billion as Refining Windfall Grows</news:title></news:news></url>
  <url><loc>https://www.bloomberg.com/news/articles/2026-10-29/indigo-posts-a-loss</loc>
    <news:news><news:publication_date>2026-10-29T10:32:01.000Z</news:publication_date>
    <news:title>India&#8217;s Largest Airline Posts A Loss</news:title></news:news></url>
  <url><loc>https://www.bloomberg.com/news/articles/2026-10-28/trump-calls-for-peltola-ban</loc>
    <news:news><news:publication_date>2026-10-28T19:53:51.000Z</news:publication_date>
    <news:title>Trump Calls for Peltola to Be Banned From Alaska Senate Race</news:title></news:news></url>
  <url><loc>https://www.bloomberg.com/opinion/articles/2026-10-29/oil-is-cheap</loc>
    <news:news><news:publication_date>2026-10-29T08:00:00.000Z</news:publication_date>
    <news:title>Oil Is Cheap, Opinion</news:title></news:news></url>
  <url><loc>https://www.bloomberg.com/news/articles/2026-10-20/old-oil-story</loc>
    <news:news><news:publication_date>2026-10-20T08:00:00.000Z</news:publication_date>
    <news:title>Old Oil Story</news:title></news:news></url>
</urlset>"""

_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Bloomberg Industries</title>
<item>
  <title><![CDATA[Caterpillar Slumps as Modest Beat Fails to Soothe AI Jitters]]></title>
  <description><![CDATA[Caterpillar Inc. shares slumped as much as 10%.]]></description>
  <link>https://www.bloomberg.com/news/articles/2026-10-29/caterpillar-tops-estimates</link>
  <pubDate>Thu, 29 Oct 2026 10:33:57 GMT</pubDate>
</item>
<item>
  <title><![CDATA[India's Largest Airline Posts A Loss]]></title>
  <description><![CDATA[IndiGo posted a loss on fuel costs.]]></description>
  <link>https://www.bloomberg.com/news/articles/2026-10-29/indigo-posts-a-loss</link>
  <pubDate>Thu, 29 Oct 2026 10:32:01 GMT</pubDate>
</item>
</channel></rss>"""


def _story(title: str, url: str = "https://www.bloomberg.com/news/articles/x/slug", feed: bool = False) -> _Story:
    return _Story(url=url, title=title, pub_date=_NOW, in_industries_feed=feed)


@pytest.fixture(autouse=True)
def _clear_cache():
    bbg._cache.clear()
    yield
    bbg._cache.clear()


# --- parsing ---

def test_parse_sitemap_keeps_only_news_stories() -> None:
    urls = [s.url for s in parse_sitemap(_SITEMAP)]
    assert len(urls) == 4
    assert not any("/opinion/" in u for u in urls)


def test_parse_sitemap_fields() -> None:
    by_url = {s.url: s for s in parse_sitemap(_SITEMAP)}
    s = by_url["https://www.bloomberg.com/news/articles/2026-10-29/indigo-posts-a-loss"]
    assert s.title == "India’s Largest Airline Posts A Loss"
    assert s.pub_date == datetime(2026, 10, 29, 10, 32, 1, tzinfo=timezone.utc)
    assert not s.in_industries_feed


def test_parse_industries_rss() -> None:
    stories = parse_industries_rss(_RSS)
    assert len(stories) == 2
    assert all(s.in_industries_feed and s.summary for s in stories)


def test_merge_prefers_industries_entry() -> None:
    merged = merge_stories(parse_sitemap(_SITEMAP), parse_industries_rss(_RSS))
    urls = [s.url for s in merged]
    assert len(urls) == len(set(urls)) == 5
    indigo = next(s for s in merged if s.url.endswith("indigo-posts-a-loss"))
    assert indigo.summary == "IndiGo posted a loss on fuel costs."


# --- classification ---

@pytest.mark.parametrize("title,expected", [
    ("Embraer Wins Jet Order From United", SECTION_TRANSPORTATION),
    ("VW Makes a Loss in China, Sparking Urgent Call for Cost Cuts", SECTION_TRANSPORTATION),
    ("India's Largest Airline Posts a Loss on Surging Fuel Costs", SECTION_TRANSPORTATION),
    ("Shell Lifts Buyback as Refining Windfall Grows", SECTION_ENERGY),
    ("Petrobras Pumps at Record as Iran War Upends Oil Markets", SECTION_ENERGY),
    ("Orsted Scraps Offshore Wind Project", SECTION_ENERGY),
    ("BlackRock to Wind Down Private Credit Fund", None),
    ("Trump Calls for Peltola to Be Banned From Alaska Senate Race", None),
])
def test_classify_sitemap_story(title: str, expected) -> None:
    assert classify(_story(title)) == expected


def test_classify_unmatched_industries_story_goes_to_industries() -> None:
    assert classify(_story("Merck Boosts Profit Forecast", feed=True)) == SECTION_INDUSTRIES


def test_classify_uses_url_slug() -> None:
    s = _story("Caterpillar Slumps", url="https://www.bloomberg.com/news/articles/x/ai-power-demand-fuels-growth")
    assert classify(s) == SECTION_ENERGY


# --- fetch_articles (mocked network) ---

def _fake_fetch(url: str):
    return _SITEMAP if url.endswith(".xml") else _RSS


def test_sections_are_disjoint_and_filtered_by_since() -> None:
    with patch("scrapers.bloomberg._fetch_text", side_effect=_fake_fetch):
        energy = BloombergScraper("Bloomberg Energy", SECTION_ENERGY).fetch_articles(_SINCE_24H)
        transport = BloombergScraper("Bloomberg Transportation", SECTION_TRANSPORTATION).fetch_articles(_SINCE_24H)
        industries = BloombergScraper("Bloomberg Industries", SECTION_INDUSTRIES).fetch_articles(_SINCE_24H)

    e, t, i = ({a.url for a in x} for x in (energy, transport, industries))
    assert not (e & t) and not (e & i) and not (t & i)
    assert any("shell-lifts" in u for u in e)
    assert any("indigo" in u for u in t)
    assert not any("old-oil-story" in u for u in e)          # older than since
    assert not any("peltola" in u for u in e | t | i)         # politics dropped
    assert all(a.source == "Bloomberg Energy" and a.language == "en" for a in energy)


def test_feeds_fetched_once_per_run() -> None:
    with patch("scrapers.bloomberg._fetch_text", side_effect=_fake_fetch) as mock_fetch:
        for section in (SECTION_ENERGY, SECTION_TRANSPORTATION, SECTION_INDUSTRIES):
            BloombergScraper("x", section).fetch_articles(_SINCE_24H)
    assert mock_fetch.call_count == 2


def test_both_feeds_down_returns_empty() -> None:
    with patch("scrapers.bloomberg._fetch_text", return_value=None):
        assert BloombergScraper("Bloomberg Energy", SECTION_ENERGY).fetch_articles(_SINCE_24H) == []


def test_scraper_satisfies_base_protocol() -> None:
    from scrapers.base import BaseScraper
    assert isinstance(BloombergScraper("Bloomberg Energy", SECTION_ENERGY), BaseScraper)
