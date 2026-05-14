"""Autodata scraper (https://www.autodata.com.br/ultimas-noticias/).

The listing page is JS-rendered (Bootstrap + jQuery) — Playwright required
for the listing phase only.  Article pages are server-rendered so body
extraction uses plain requests + trafilatura (faster, parallel).

Pagination: ?p=N query parameter; page 1 = base URL.

The article URL embeds the publication date (/noticias/YYYY/MM/DD/slug/id/),
enabling early-exit pagination without fetching individual article pages.
"""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from html import unescape
from typing import Optional

import requests
import trafilatura

from fetcher.rss_fetcher import Article
from scrapers.playwright_utils import browser_context

logger = logging.getLogger(__name__)

_BASE = "https://www.autodata.com.br"
_BR_TZ = timezone(timedelta(hours=-3))

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

# Matches article hrefs that embed the date, e.g.:
#   https://www.autodata.com.br/noticias/2026/05/08/slug/103096/  (absolute)
#   /noticias/2026/05/08/slug/103096/                              (relative)
_ARTICLE_LINK_RE = re.compile(
    r'href="((?:https?://www\.autodata\.com\.br)?/noticias/(\d{4})/(\d{2})/(\d{2})/[^"]+)"'
    r"[^>]*>(.*?)</a>",
    flags=re.IGNORECASE | re.DOTALL,
)

# Presence of any ?p=N link means there are more pages.
_HAS_NEXT_RE = re.compile(
    r'href="https?://www\.autodata\.com\.br/ultimas-noticias/\?p=\d+"',
    flags=re.IGNORECASE,
)

_TAG_STRIP_RE = re.compile(r"<[^>]+>")


@dataclass
class _ListingItem:
    url: str
    title: str
    pub_date: datetime  # midnight BR_TZ → UTC, from URL path


def _date_from_url(year: str, month: str, day: str) -> datetime:
    """Return midnight of the given date in BR_TZ, converted to UTC."""
    return datetime(
        int(year), int(month), int(day), 0, 0, 0, tzinfo=_BR_TZ
    ).astimezone(timezone.utc)


def parse_listing(html: str) -> tuple[list[_ListingItem], bool]:
    """Parse a fully-rendered listing page.

    Returns (items, has_more_pages).  Each URL appears exactly once — the
    dict-keyed-by-URL pattern ensures idempotency if the same URL appears
    in multiple anchor tags.
    """
    url_to_item: dict[str, _ListingItem] = {}

    for m in _ARTICLE_LINK_RE.finditer(html):
        raw_url = m.group(1)
        if raw_url.startswith("/"):
            raw_url = _BASE + raw_url
        url = raw_url.rstrip("/") + "/"
        year, month, day = m.group(2), m.group(3), m.group(4)
        raw_title = m.group(5)
        title = unescape(_TAG_STRIP_RE.sub("", raw_title)).strip()
        if not title:
            continue
        pub_date = _date_from_url(year, month, day)
        url_to_item[url] = _ListingItem(url=url, title=title, pub_date=pub_date)

    items = list(url_to_item.values())
    has_more = bool(_HAS_NEXT_RE.search(html))
    return items, has_more


class AutodataScraper:
    name: str = "Autodata"
    base_url: str = f"{_BASE}/ultimas-noticias/"
    language: str = "pt-br"

    def __init__(
        self,
        max_pages: Optional[int] = None,
        concurrency: Optional[int] = None,
    ) -> None:
        from config.settings import SCRAPER_MAX_PAGES, SCRAPER_CONCURRENCY
        self.max_pages = max_pages if max_pages is not None else SCRAPER_MAX_PAGES
        self.concurrency = concurrency if concurrency is not None else SCRAPER_CONCURRENCY

    def fetch_articles(self, since: datetime) -> list[Article]:
        listing_items = self._collect_listing(since)
        if not listing_items:
            return []
        return self._fetch_articles(listing_items)

    def _collect_listing(self, since: datetime) -> list[_ListingItem]:
        items: list[_ListingItem] = []
        seen: set[str] = set()

        with browser_context(headless=True) as ctx:
            page = ctx.new_page()

            for page_num in range(1, self.max_pages + 1):
                url = (
                    self.base_url
                    if page_num == 1
                    else f"{self.base_url}?p={page_num}"
                )
                try:
                    page.goto(url, wait_until="networkidle", timeout=30_000)
                except Exception as e:
                    logger.warning(f"Autodata: timeout na página {page_num}: {e}")
                    break

                page_items, has_more = parse_listing(page.content())
                if not page_items:
                    logger.debug(f"Autodata: página {page_num} vazia, parando")
                    break

                stop = False
                for item in page_items:
                    if item.url in seen:
                        continue
                    seen.add(item.url)
                    if item.pub_date < since:
                        stop = True
                        break
                    items.append(item)

                logger.debug(
                    f"Autodata: página {page_num} — {len(page_items)} artigos, "
                    f"has_more={has_more}"
                )
                if stop or not has_more:
                    break

            page.close()

        logger.info(f"Autodata: {len(items)} URLs coletadas")
        return items

    def _fetch_articles(self, items: list[_ListingItem]) -> list[Article]:
        results: list[Article] = []
        with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
            futures = {pool.submit(self._fetch_one, item): item for item in items}
            for fut in as_completed(futures):
                item = futures[fut]
                try:
                    article = fut.result()
                    if article is not None:
                        results.append(article)
                except Exception as e:
                    logger.warning(f"Autodata: falha em {item.url[:80]}: {e}")
        logger.info(f"Autodata: {len(results)} artigos retornados")
        return results

    def _fetch_one(self, item: _ListingItem) -> Optional[Article]:
        resp = requests.get(item.url, headers=_HEADERS, timeout=20)
        resp.raise_for_status()
        full_text = trafilatura.extract(
            resp.text,
            include_comments=False,
            include_tables=False,
            no_fallback=False,
        ) or ""
        return Article(
            source=self.name,
            title=item.title,
            url=item.url,
            published_at=item.pub_date.isoformat(),
            full_text=full_text,
            language=self.language,
        )
