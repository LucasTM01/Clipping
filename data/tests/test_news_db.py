"""Offline tests for data/news_db.py."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from data.news_db import (
    _article_key,
    _extract_date,
    init_db,
    update_capital_goods,
    upsert_article,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_scored(url: str, title: str = "Test title", source: str = "TestFeed",
                 published_at: str = "2026-05-08T14:00:00+00:00",
                 score: float = 0.75, tier: str = "medium") -> object:
    article = SimpleNamespace(
        title=title,
        url=url,
        source=source,
        published_at=published_at,
        full_text="",
    )
    return SimpleNamespace(
        article=article,
        relevance_score=score,
        tier=tier,
        predicted_tickers=[],
        ticker_confidence={},
        rationale="",
    )


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "test_news.db"
    init_db(path)
    return path


# ---------------------------------------------------------------------------
# init_db
# ---------------------------------------------------------------------------

def test_init_db_creates_file(tmp_path: Path) -> None:
    path = tmp_path / "sub" / "news.db"
    init_db(path)
    assert path.exists()


def test_init_db_creates_table(db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    assert "articles" in tables


def test_init_db_idempotent(db_path: Path) -> None:
    init_db(db_path)
    init_db(db_path)  # should not raise


# ---------------------------------------------------------------------------
# _article_key / _extract_date
# ---------------------------------------------------------------------------

def test_article_key_is_deterministic() -> None:
    url = "https://example.com/article/123"
    assert _article_key(url) == _article_key(url)


def test_article_key_differs_for_different_urls() -> None:
    assert _article_key("https://a.com/1") != _article_key("https://a.com/2")


def test_article_key_length() -> None:
    assert len(_article_key("https://any.com")) == 32


def test_extract_date_iso() -> None:
    assert _extract_date("2026-05-08T14:00:00+00:00") == "2026-05-08"


def test_extract_date_short() -> None:
    assert _extract_date("2026-05-08") == "2026-05-08"


def test_extract_date_empty() -> None:
    assert _extract_date("") == ""


# ---------------------------------------------------------------------------
# upsert_article
# ---------------------------------------------------------------------------

def test_upsert_inserts_row(db_path: Path) -> None:
    scored = _make_scored("https://example.com/news/1")
    upsert_article(scored, db_path)
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT headline, url, tier FROM articles").fetchone()
    assert row is not None
    assert row[1] == "https://example.com/news/1"
    assert row[2] == "medium"


def test_upsert_stores_correct_fields(db_path: Path) -> None:
    scored = _make_scored(
        url="https://example.com/news/2",
        title="Embraer wins deal",
        source="Reuters",
        published_at="2026-05-08T10:30:00+00:00",
        score=0.88,
        tier="high",
    )
    upsert_article(scored, db_path)
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT headline, source, date, relevance_score, tier, capital_goods FROM articles"
        ).fetchone()
    assert row[0] == "Embraer wins deal"
    assert row[1] == "Reuters"
    assert row[2] == "2026-05-08"
    assert abs(row[3] - 0.88) < 1e-5
    assert row[4] == "high"
    assert row[5] is None  # not yet classified


def test_upsert_duplicate_url_ignored(db_path: Path) -> None:
    url = "https://example.com/news/3"
    upsert_article(_make_scored(url, title="First"), db_path)
    upsert_article(_make_scored(url, title="Second"), db_path)
    with sqlite3.connect(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
        title = conn.execute("SELECT headline FROM articles").fetchone()[0]
    assert count == 1
    assert title == "First"  # second insert was ignored


def test_upsert_discard_tier_stored(db_path: Path) -> None:
    scored = _make_scored("https://example.com/news/4", tier="discard", score=0.2)
    upsert_article(scored, db_path)
    with sqlite3.connect(db_path) as conn:
        tier = conn.execute("SELECT tier FROM articles").fetchone()[0]
    assert tier == "discard"


def test_upsert_multiple_articles(db_path: Path) -> None:
    for i in range(5):
        upsert_article(_make_scored(f"https://example.com/news/{i}"), db_path)
    with sqlite3.connect(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
    assert count == 5


# ---------------------------------------------------------------------------
# update_capital_goods
# ---------------------------------------------------------------------------

def test_update_capital_goods_sets_one(db_path: Path) -> None:
    url = "https://example.com/news/cg1"
    upsert_article(_make_scored(url), db_path)
    update_capital_goods(url, value=1, db_path=db_path)
    with sqlite3.connect(db_path) as conn:
        val = conn.execute("SELECT capital_goods FROM articles WHERE url=?", (url,)).fetchone()[0]
    assert val == 1


def test_update_capital_goods_sets_zero(db_path: Path) -> None:
    url = "https://example.com/news/cg2"
    upsert_article(_make_scored(url), db_path)
    update_capital_goods(url, value=0, db_path=db_path)
    with sqlite3.connect(db_path) as conn:
        val = conn.execute("SELECT capital_goods FROM articles WHERE url=?", (url,)).fetchone()[0]
    assert val == 0


def test_update_capital_goods_can_change(db_path: Path) -> None:
    url = "https://example.com/news/cg3"
    upsert_article(_make_scored(url), db_path)
    update_capital_goods(url, value=1, db_path=db_path)
    update_capital_goods(url, value=0, db_path=db_path)
    with sqlite3.connect(db_path) as conn:
        val = conn.execute("SELECT capital_goods FROM articles WHERE url=?", (url,)).fetchone()[0]
    assert val == 0


def test_update_capital_goods_unknown_url_no_error(db_path: Path) -> None:
    update_capital_goods("https://nonexistent.com/", value=1, db_path=db_path)
