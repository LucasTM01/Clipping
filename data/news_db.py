"""Proprietary news database — SQLite-backed, append-only.

Each pipeline run inserts all screened articles (including discards).
Duplicate URLs are silently ignored (INSERT OR IGNORE).
The `capital_goods` column is updated separately via the Streamlit feedback loop.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from screener.relevance_scorer import ScoredArticle

log = logging.getLogger(__name__)

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS articles (
    key             TEXT PRIMARY KEY,
    headline        TEXT NOT NULL,
    url             TEXT NOT NULL UNIQUE,
    source          TEXT,
    date            TEXT,
    published_at    TEXT,
    relevance_score REAL,
    tier            TEXT,
    capital_goods   INTEGER,
    created_at      TEXT
);
"""


def _default_db_path() -> Path:
    from config.settings import settings
    return Path(settings.news_db_path)


def _article_key(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()


def _extract_date(published_at: str) -> str:
    """Return YYYY-MM-DD from any ISO-ish datetime string, or '' on failure."""
    try:
        return published_at[:10]
    except Exception:
        return ""


def init_db(db_path: str | Path | None = None) -> None:
    """Create the articles table if it does not exist."""
    path = Path(db_path) if db_path else _default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute(_CREATE_SQL)
        conn.commit()


def upsert_article(scored: ScoredArticle, db_path: str | Path | None = None) -> None:
    """Insert one article. Silently skips if the URL already exists."""
    path = Path(db_path) if db_path else _default_db_path()
    art = scored.article
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO articles
                (key, headline, url, source, date, published_at,
                 relevance_score, tier, capital_goods, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
            """,
            (
                _article_key(art.url),
                art.title,
                art.url,
                art.source,
                _extract_date(art.published_at),
                art.published_at,
                round(scored.relevance_score, 6),
                scored.tier,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()


def update_capital_goods(
    url: str,
    value: int,
    db_path: str | Path | None = None,
) -> None:
    """Set capital_goods = 1 or 0 for the given article URL."""
    path = Path(db_path) if db_path else _default_db_path()
    with sqlite3.connect(path) as conn:
        conn.execute(
            "UPDATE articles SET capital_goods = ? WHERE url = ?",
            (value, url),
        )
        conn.commit()
    log.debug(f"news_db: capital_goods={value} for {url[:80]}")
