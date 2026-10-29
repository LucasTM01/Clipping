"""Offline tests for the Randoncorp blog scraper."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from scrapers.playwright_utils import BR_TZ
from scrapers.randoncorp import RandoncorpScraper, extract_body, is_in_window, parse_listing

_LISTING = """
<div class="latest__news">
  <a href="https://www.randoncorp.com/pt/blog/randon-venice-cascavel/" class="latest__news__item js-news"
     data-cat="Noticias" data-title="Randon Venice expande atua&#231;&#227;o em Cascavel">
    <div class="item__category">Noticias</div>
    <div class="item__title">Randon Venice expande atuação em Cascavel</div>
    <div class="item__details"><div class="item__details__date">Publicado em 28/10/2026</div></div>
  </a>
  <a href="https://www.randoncorp.com/pt/blog/resultado-3t26/" class="latest__news__item js-news"
     data-cat="Noticias" data-title="Randoncorp divulga resultado do 3T26">
    <div class="item__details"><div class="item__details__date">Publicado em 29/10/2026</div></div>
  </a>
  <a href="https://www.randoncorp.com/pt/blog/alcool-gel/" class="card js-news" data-cat="ESG" data-title="Álcool gel">
    <div class="item__details"><div class="item__details__date">Publicado em 15/03/2020</div></div>
  </a>
  <a href="https://www.randoncorp.com/pt/blog/resultado-3t26/" class="card js-news" data-title="Duplicado">
    <div>Publicado em 29/10/2026</div>
  </a>
  <a href="https://www.randoncorp.com/pt/blog/sem-data/" data-title="Sem data"><div>Leia mais</div></a>
</div>
"""

_ARTICLE = """<html><body><div class="search">Buscando por</div>
<article><h1>Randon Venice</h1><p>A Randon Venice está ampliando sua presença nacional e responde agora pela
distribuição dos produtos Randon na região de Cascavel, no Paraná, com implementos rodoviários e peças.</p>
<p>A concessionária própria atua na comercialização de implementos, peças de reposição e pneus.</p></article>
</body></html>"""


def _resp(text: str) -> MagicMock:
    r = MagicMock()
    r.text = text
    r.encoding = "utf-8"
    r.raise_for_status = MagicMock()
    return r


def test_parse_listing_dedups_and_sorts() -> None:
    items = parse_listing(_LISTING)
    assert [i.url.rsplit("/", 2)[-2] for i in items] == ["resultado-3t26", "randon-venice-cascavel", "alcool-gel"]


def test_parse_listing_title_and_date() -> None:
    item = next(i for i in parse_listing(_LISTING) if "venice" in i.url)
    assert item.title == "Randon Venice expande atuação em Cascavel"
    assert item.pub_date == datetime(2026, 10, 28, tzinfo=BR_TZ).astimezone(timezone.utc)


def test_is_in_window_is_day_precision_in_br_time() -> None:
    item = next(i for i in parse_listing(_LISTING) if "venice" in i.url)  # 28/10 (BR)
    # 19h run on 28/10 (since = 28/10 01:00 BRT) and 06:30 run on 29/10 (since = 28/10 06:30 BRT)
    assert is_in_window(item, datetime(2026, 10, 28, 4, 0, tzinfo=timezone.utc))
    assert is_in_window(item, datetime(2026, 10, 28, 9, 30, tzinfo=timezone.utc))
    assert not is_in_window(item, datetime(2026, 10, 29, 9, 30, tzinfo=timezone.utc))


def test_extract_body_strips_search_widget() -> None:
    body = extract_body(_ARTICLE)
    assert not body.startswith("Buscando por")
    assert "Cascavel" in body


def test_fetch_articles() -> None:
    since = datetime(2026, 10, 28, 9, 30, tzinfo=timezone.utc)
    with patch("scrapers.randoncorp.requests.get", side_effect=lambda url, **kw: _resp(_LISTING if url.endswith("/pt/blog/") else _ARTICLE)):
        articles = RandoncorpScraper().fetch_articles(since)

    assert {a.url.rsplit("/", 2)[-2] for a in articles} == {"resultado-3t26", "randon-venice-cascavel"}
    assert all(a.source == "Randoncorp Blog" and a.full_text for a in articles)


def test_fetch_listing_error_returns_empty() -> None:
    with patch("scrapers.randoncorp.requests.get", side_effect=ConnectionError("down")):
        assert RandoncorpScraper().fetch_articles(datetime(2026, 10, 28, tzinfo=timezone.utc)) == []


def test_scraper_satisfies_base_protocol() -> None:
    from scrapers.base import BaseScraper
    assert isinstance(RandoncorpScraper(), BaseScraper)
