"""Offline tests for Bloomberg scraper helpers."""

from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from scrapers.bloomberg import _parse_items, _to_utc
from scrapers.playwright_utils import extract_published_at

FIXTURES = Path(__file__).parent / "fixtures"

_NOW = datetime(2026, 5, 8, 21, 0, 0, tzinfo=timezone.utc)
_SINCE_24H = _NOW - timedelta(hours=24)


@pytest.fixture
def paginate_data() -> dict:
    return json.loads((FIXTURES / "bloomberg_paginate_response.json").read_text())


@pytest.fixture
def article_html() -> str:
    return (FIXTURES / "bloomberg_article.html").read_text(encoding="utf-8")


# --- _parse_items ---

def test_parse_items_returns_list(paginate_data: dict) -> None:
    items = _parse_items(paginate_data)
    assert isinstance(items, list)
    assert len(items) == 3


def test_parse_items_has_headline(paginate_data: dict) -> None:
    items = _parse_items(paginate_data)
    assert items[0]["headline"] == "JetBlue Rises on Strong Travel Demand Forecast, Spending Cuts"


def test_parse_items_has_url(paginate_data: dict) -> None:
    items = _parse_items(paginate_data)
    assert items[0]["url"].startswith("/news/articles/")


def test_parse_items_has_published_at(paginate_data: dict) -> None:
    items = _parse_items(paginate_data)
    assert items[0]["publishedAt"] == "2026-05-08T11:10:53.050Z"


def test_parse_items_empty_on_bad_data() -> None:
    assert _parse_items({}) == []
    assert _parse_items({"archive_story_list": {}}) == []


# --- _to_utc ---

def test_to_utc_parses_z_suffix() -> None:
    dt = _to_utc("2026-05-08T11:10:53.050Z")
    assert dt is not None
    assert dt == datetime(2026, 5, 8, 11, 10, 53, 50000, tzinfo=timezone.utc)


def test_to_utc_parses_offset() -> None:
    dt = _to_utc("2026-05-08T08:10:53-03:00")
    assert dt is not None
    assert dt == datetime(2026, 5, 8, 11, 10, 53, tzinfo=timezone.utc)


def test_to_utc_returns_none_on_empty() -> None:
    assert _to_utc("") is None


def test_to_utc_returns_none_on_garbage() -> None:
    assert _to_utc("not-a-date") is None


# --- article datetime extraction ---

def test_article_has_time_tag_datetime(article_html: str) -> None:
    dt = extract_published_at(article_html)
    assert dt is not None
    assert dt == datetime(2026, 5, 8, 11, 10, 53, 50000, tzinfo=timezone.utc)


# --- since filtering logic (unit, no network) ---

def test_since_filter_keeps_recent_item(paginate_data: dict) -> None:
    items = _parse_items(paginate_data)
    # item[0] publishedAt = 2026-05-08T11:10:53Z — within 24h of _NOW
    dt = _to_utc(items[0]["publishedAt"])
    assert dt is not None
    assert dt >= _SINCE_24H


def test_since_filter_drops_old_item(paginate_data: dict) -> None:
    items = _parse_items(paginate_data)
    # item[2] publishedAt = 2026-05-07T14:00:00Z — exactly 31h before _NOW
    dt = _to_utc(items[2]["publishedAt"])
    assert dt is not None
    assert dt < _SINCE_24H
