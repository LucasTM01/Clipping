"""Common interface for site-specific scrapers."""

from datetime import datetime
from typing import Protocol, runtime_checkable

from fetcher.rss_fetcher import Article


@runtime_checkable
class BaseScraper(Protocol):
    name: str
    base_url: str
    language: str

    def fetch_articles(self, since: datetime) -> list[Article]: ...
