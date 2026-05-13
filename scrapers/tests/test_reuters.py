"""Offline tests for ReutersScraper parsing helpers."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from scrapers.reuters import parse_sitemap, _parse_iso, _SECTION

FIXTURES = Path(__file__).parent / "fixtures"

_NOW = datetime(2026, 5, 8, 21, 0, 0, tzinfo=timezone.utc)
_SINCE_24H = _NOW - timedelta(hours=24)

_AUTOS_SECTION = "/business/autos-transportation/"
_ENERGY_SECTION = "/business/energy/"


@pytest.fixture
def sitemap_xml() -> str:
    return (FIXTURES / "reuters_sitemap.xml").read_text(encoding="utf-8")


# --- parse_sitemap ---

def test_parse_sitemap_filters_to_section(sitemap_xml: str) -> None:
    items = parse_sitemap(sitemap_xml, _SECTION)
    for item in items:
        assert _SECTION in item.url


def test_parse_sitemap_excludes_other_sections(sitemap_xml: str) -> None:
    items = parse_sitemap(sitemap_xml, _SECTION)
    urls = [it.url for it in items]
    assert not any("rates-bonds" in u for u in urls)
    assert not any("/technology/" in u for u in urls)


def test_parse_sitemap_extracts_titles(sitemap_xml: str) -> None:
    items = parse_sitemap(sitemap_xml, _SECTION)
    titles = {it.title for it in items}
    assert "Embraer wins $1.5 billion KC-390 deal with NATO ally" in titles
    assert "Boeing resumes 737 MAX deliveries after FAA clears production fixes" in titles


def test_parse_sitemap_item_count(sitemap_xml: str) -> None:
    items = parse_sitemap(sitemap_xml, _SECTION)
    # Fixture has 3 aerospace-defense entries (2 recent + 1 old)
    assert len(items) == 3


def test_parse_sitemap_pub_dates_utc(sitemap_xml: str) -> None:
    items = parse_sitemap(sitemap_xml, _SECTION)
    for item in items:
        assert item.pub_date.tzinfo == timezone.utc


def test_parse_sitemap_pub_dates_correct(sitemap_xml: str) -> None:
    items = parse_sitemap(sitemap_xml, _SECTION)
    dates = {it.pub_date for it in items}
    assert datetime(2026, 5, 8, 20, 0, 0, tzinfo=timezone.utc) in dates
    assert datetime(2026, 5, 8, 14, 15, 0, tzinfo=timezone.utc) in dates


def test_parse_sitemap_empty_xml() -> None:
    assert parse_sitemap("", _SECTION) == []


def test_parse_sitemap_no_matching_section() -> None:
    xml = (
        "<url><loc>https://www.reuters.com/markets/rates/foo-2026-05-08/</loc>"
        "<news:news><news:publication_date>2026-05-08T10:00:00.000Z</news:publication_date>"
        "<news:title><![CDATA[Unrelated]]></news:title></news:news></url>"
    )
    assert parse_sitemap(xml, _SECTION) == []


# --- _parse_iso ---

def test_parse_iso_z_suffix() -> None:
    dt = _parse_iso("2026-05-08T20:00:00.000Z")
    assert dt == datetime(2026, 5, 8, 20, 0, 0, tzinfo=timezone.utc)


def test_parse_iso_offset() -> None:
    dt = _parse_iso("2026-05-08T17:00:00-03:00")
    assert dt == datetime(2026, 5, 8, 20, 0, 0, tzinfo=timezone.utc)


def test_parse_iso_invalid_returns_none() -> None:
    assert _parse_iso("not-a-date") is None
    assert _parse_iso("") is None


# --- since filtering ---

def test_since_filter_keeps_recent(sitemap_xml: str) -> None:
    items = parse_sitemap(sitemap_xml, _SECTION)
    recent = [i for i in items if i.pub_date >= _SINCE_24H]
    assert len(recent) == 2


def test_since_filter_drops_old(sitemap_xml: str) -> None:
    items = parse_sitemap(sitemap_xml, _SECTION)
    old = [i for i in items if i.pub_date < _SINCE_24H]
    assert len(old) == 1
    assert "Older aerospace" in old[0].title


# --- autos-transportation section ---

def test_parse_sitemap_autos_filters_to_section(sitemap_xml: str) -> None:
    items = parse_sitemap(sitemap_xml, _AUTOS_SECTION)
    for item in items:
        assert _AUTOS_SECTION in item.url


def test_parse_sitemap_autos_excludes_aerospace(sitemap_xml: str) -> None:
    items = parse_sitemap(sitemap_xml, _AUTOS_SECTION)
    urls = [it.url for it in items]
    assert not any("aerospace-defense" in u for u in urls)


def test_parse_sitemap_autos_item_count(sitemap_xml: str) -> None:
    items = parse_sitemap(sitemap_xml, _AUTOS_SECTION)
    assert len(items) == 2


def test_parse_sitemap_autos_titles(sitemap_xml: str) -> None:
    items = parse_sitemap(sitemap_xml, _AUTOS_SECTION)
    titles = {it.title for it in items}
    assert "GM electric truck sales surge in Q1 2026" in titles
    assert "Toyota expands hybrid lineup to meet European demand" in titles


def test_parse_sitemap_autos_pub_dates_utc(sitemap_xml: str) -> None:
    items = parse_sitemap(sitemap_xml, _AUTOS_SECTION)
    for item in items:
        assert item.pub_date.tzinfo == timezone.utc


def test_parse_sitemap_sections_are_independent(sitemap_xml: str) -> None:
    """Each section instance sees only its own articles."""
    aero = parse_sitemap(sitemap_xml, _SECTION)
    autos = parse_sitemap(sitemap_xml, _AUTOS_SECTION)
    aero_urls = {it.url for it in aero}
    autos_urls = {it.url for it in autos}
    assert aero_urls.isdisjoint(autos_urls)


# --- energy section ---

def test_parse_sitemap_energy_filters_to_section(sitemap_xml: str) -> None:
    items = parse_sitemap(sitemap_xml, _ENERGY_SECTION)
    for item in items:
        assert _ENERGY_SECTION in item.url


def test_parse_sitemap_energy_item_count(sitemap_xml: str) -> None:
    items = parse_sitemap(sitemap_xml, _ENERGY_SECTION)
    assert len(items) == 2


def test_parse_sitemap_energy_titles(sitemap_xml: str) -> None:
    items = parse_sitemap(sitemap_xml, _ENERGY_SECTION)
    titles = {it.title for it in items}
    assert "Oil prices rise as OPEC signals deeper output cuts" in titles
    assert "Global solar capacity hits record 2 terawatts" in titles


def test_parse_sitemap_energy_excludes_other_sections(sitemap_xml: str) -> None:
    items = parse_sitemap(sitemap_xml, _ENERGY_SECTION)
    urls = [it.url for it in items]
    assert not any("aerospace-defense" in u for u in urls)
    assert not any("autos-transportation" in u for u in urls)


def test_parse_sitemap_all_sections_disjoint(sitemap_xml: str) -> None:
    aero = {it.url for it in parse_sitemap(sitemap_xml, _SECTION)}
    autos = {it.url for it in parse_sitemap(sitemap_xml, _AUTOS_SECTION)}
    energy = {it.url for it in parse_sitemap(sitemap_xml, _ENERGY_SECTION)}
    assert aero.isdisjoint(autos)
    assert aero.isdisjoint(energy)
    assert autos.isdisjoint(energy)
