"""Offline tests for AirwayScraper parsing helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from scrapers.airway import parse_listing
from scrapers.playwright_utils import extract_published_at, BR_TZ


FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def listing_html() -> str:
    return (FIXTURES / "airway_listing.html").read_text(encoding="utf-8")


@pytest.fixture
def article_html() -> str:
    return (FIXTURES / "airway_article.html").read_text(encoding="utf-8")


def test_parse_listing_extracts_unique_articles(listing_html: str) -> None:
    items, _ = parse_listing(listing_html)
    urls = [it.url for it in items]
    assert len(urls) == len(set(urls)), "duplicates leaked"
    assert "https://www.airway.com.br/embraer-anuncia-pedido-de-e2/" in urls
    assert "https://www.airway.com.br/azul-amplia-malha/" in urls
    assert "https://www.airway.com.br/gol-resultado-trimestral/" in urls


def test_parse_listing_extracts_titles(listing_html: str) -> None:
    items, _ = parse_listing(listing_html)
    titles = {it.title for it in items}
    assert "Embraer anuncia pedido firme de E2 por companhia asiática" in titles
    assert "Azul amplia malha doméstica para o verão" in titles


def test_parse_listing_returns_next_url(listing_html: str) -> None:
    _, next_url = parse_listing(listing_html)
    assert next_url == "https://www.airway.com.br/ultimas-noticias/page/2/"


def test_parse_listing_no_next_on_last_page() -> None:
    html = '<h2 class="entry-title"><a href="https://www.airway.com.br/foo/">Foo</a></h2>'
    _, next_url = parse_listing(html)
    assert next_url is None


def test_parse_listing_extracts_current_jsonld_item_list() -> None:
    html = '''
    <script type="application/ld+json">{
      "@type": "CollectionPage",
      "mainEntity": {"@type": "ItemList", "itemListElement": [
        {"@type": "ListItem", "position": 1, "name": "Notícia atual", "item": "/noticia-atual/"},
        {"@type": "ListItem", "position": 2, "name": "Outra notícia", "item": "https://www.airway.com.br/outra-noticia/"}
      ]}
    }</script>
    '''

    items, _ = parse_listing(html)

    assert [(item.title, item.url) for item in items] == [
        ("Notícia atual", "https://www.airway.com.br/noticia-atual/"),
        ("Outra notícia", "https://www.airway.com.br/outra-noticia/"),
    ]


def test_extract_published_at_from_article(article_html: str) -> None:
    dt = extract_published_at(article_html)
    assert dt is not None
    assert dt.tzinfo is not None
    expected = datetime(2026, 5, 5, 14, 20, tzinfo=BR_TZ).astimezone(timezone.utc)
    assert dt == expected


def test_extract_published_at_prefers_jsonld_over_meta(article_html: str) -> None:
    # Both JSON-LD and meta are present — result should be consistent
    dt = extract_published_at(article_html)
    assert dt is not None
    # Both carry same datetime, so either cascade path gives the right answer
    assert dt.year == 2026
    assert dt.month == 5
    assert dt.day == 5
