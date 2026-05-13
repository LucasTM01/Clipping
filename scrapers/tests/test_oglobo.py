"""Offline tests for OGloboScraper parsing helpers."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from scrapers.oglobo import parse_listing
from scrapers.playwright_utils import extract_published_at, parse_relative_time_pt, BR_TZ


FIXTURES = Path(__file__).parent / "fixtures"

_NOW = datetime(2026, 5, 8, 21, 0, 0, tzinfo=timezone.utc)

URL_BETANO = "https://oglobo.globo.com/blogs/lauro-jardim/post/2026/05/diretor-global-da-betano-e-acusado-de-assedio-por-ex-executiva.ghtml"
URL_CBF = "https://oglobo.globo.com/esportes/selecao-brasileira/noticia/2026/05/08/cbf-divulga-valores-e-inicio-da-venda-de-ingressos-para-brasil-x-panama-no-maracana.ghtml"
URL_CASA = "https://oglobo.globo.com/play/realities/noticia/2026/05/08/em-noite-de-eliminacao-casa-do-patrao-registra-sua-pior-audiencia-em-sao-paulo.ghtml"


@pytest.fixture
def listing_html() -> str:
    return (FIXTURES / "oglobo_listing.html").read_text(encoding="utf-8")


@pytest.fixture
def article_html() -> str:
    return (FIXTURES / "oglobo_article.html").read_text(encoding="utf-8")


# --- listing parse ---

def test_parse_listing_extracts_unique_articles(listing_html: str) -> None:
    items, _ = parse_listing(listing_html, _NOW)
    urls = [i.url for i in items]
    assert len(urls) == len(set(urls)), "duplicates leaked"
    assert URL_BETANO in urls
    assert URL_CBF in urls
    assert URL_CASA in urls


def test_parse_listing_extracts_titles(listing_html: str) -> None:
    items, _ = parse_listing(listing_html, _NOW)
    titles = {i.title for i in items}
    assert "Diretor global da Betano é acusado de assédio por ex-executiva" in titles
    assert "CBF divulga valores e início da venda de ingressos para Brasil x Panamá, no Maracanã" in titles


def test_parse_listing_has_approx_times(listing_html: str) -> None:
    items, _ = parse_listing(listing_html, _NOW)
    by_url = {i.url: i for i in items}
    # "Há 30 minutos" → approx_time should be ~30 min before _NOW
    cbf = by_url[URL_CBF]
    assert cbf.approx_time is not None
    diff = (_NOW - cbf.approx_time).total_seconds()
    assert 25 * 60 <= diff <= 35 * 60, f"expected ~30min diff, got {diff}s"


def test_parse_listing_has_load_more(listing_html: str) -> None:
    _, has_more = parse_listing(listing_html, _NOW)
    assert has_more is True


def test_parse_listing_no_load_more() -> None:
    html = '<div class="feed-post-body"><h2><a href="https://oglobo.globo.com/foo/" class="feed-post-link">Foo</a></h2></div>'
    _, has_more = parse_listing(html, _NOW)
    assert has_more is False


# --- parse_relative_time_pt ---

def test_parse_relative_time_minutos() -> None:
    dt = parse_relative_time_pt("Há 30 minutos", _NOW)
    assert dt is not None
    diff = (_NOW - dt).total_seconds()
    assert abs(diff - 30 * 60) < 1


def test_parse_relative_time_horas() -> None:
    dt = parse_relative_time_pt("Há 2 horas", _NOW)
    assert dt is not None
    diff = (_NOW - dt).total_seconds()
    assert abs(diff - 2 * 3600) < 1


def test_parse_relative_time_agora() -> None:
    dt = parse_relative_time_pt("Agora", _NOW)
    assert dt == _NOW


def test_parse_relative_time_unknown() -> None:
    dt = parse_relative_time_pt("publicado ontem às 18h", _NOW)
    # "ontem" pattern should still be recognised
    assert dt is not None
    # verify it's a day before now (in BR_TZ)
    br_now = _NOW.astimezone(BR_TZ)
    expected_date = (br_now - timedelta(days=1)).date()
    assert dt.astimezone(BR_TZ).date() == expected_date


def test_parse_relative_time_unrecognised() -> None:
    dt = parse_relative_time_pt("publicado em 2026", _NOW)
    assert dt is None


# --- article datetime extraction ---

def test_extract_published_at_from_time_tag(article_html: str) -> None:
    dt = extract_published_at(article_html)
    assert dt is not None
    assert dt.tzinfo is not None
    expected = datetime(2026, 5, 8, 21, 4, 23, tzinfo=timezone.utc)
    assert dt == expected
