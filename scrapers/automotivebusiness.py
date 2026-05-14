"""Automotive Business scraper (https://www.automotivebusiness.com.br/noticias).

Next.js SSR site — server-rendered, plain requests sufficient.
Listing cards are minimal <li><a href="/noticias/slug"> with no dates.
Pagination via ?page=N. Articles fetched in parallel for date + full text.
"""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from html import unescape
from typing import Optional

import requests
import trafilatura

from fetcher.rss_fetcher import Article
from scrapers.playwright_utils import BR_TZ, extract_published_at

logger = logging.getLogger(__name__)

_BASE = "https://www.automotivebusiness.com.br"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

# Matches <a href="/noticias/slug">Title</a> — slug must be non-empty
_ARTICLE_LINK_RE = re.compile(
    r'<a[^>]+href="(/noticias/([^/"?#][^"?#]*))"[^>]*>(.*?)</a>',
    flags=re.DOTALL | re.IGNORECASE,
)
_TAG_STRIP_RE = re.compile(r"<[^>]+>")


@dataclass
class _ListingItem:
    url: str
    title: str


def parse_listing(html: str) -> list[_ListingItem]:
    """Extract (url, title) pairs from an Automotive Business listing page."""
    items: list[_ListingItem] = []
    seen: set[str] = set()
    for path, _slug, raw_title in _ARTICLE_LINK_RE.findall(html):
        url = _BASE + path
        if url in seen:
            continue
        title = unescape(_TAG_STRIP_RE.sub("", raw_title)).strip()
        if not title:
            continue
        seen.add(url)
        items.append(_ListingItem(url=url, title=title))
    return items


def parse_article(html: str) -> tuple[Optional[datetime], str]:
    """Extract (published_at, full_text) from an article page HTML."""
    published_at = extract_published_at(html)
    full_text = trafilatura.extract(
        html,
        include_comments=False,
        include_tables=False,
        no_fallback=False,
    ) or ""
    return published_at, full_text


class AutomotiveBusinessScraper:
    name: str = "Automotive Business"
    base_url: str = f"{_BASE}/noticias"
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
        listing_items = self._collect_listing()
        if not listing_items:
            return []
        return self._fetch_articles(listing_items, since)

    def _collect_listing(self) -> list[_ListingItem]:
        """Paginate via ?page=N up to max_pages and collect all article URLs."""
        items: list[_ListingItem] = []
        seen: set[str] = set()

        for page_num in range(1, self.max_pages + 1):
            url = (
                self.base_url
                if page_num == 1
                else f"{self.base_url}?page={page_num}"
            )
            try:
                resp = requests.get(url, headers=_HEADERS, timeout=20)
                resp.raise_for_status()
            except Exception as e:
                logger.warning(f"Automotive Business: erro na página {page_num}: {e}")
                break

            page_items = parse_listing(resp.text)
            new = [i for i in page_items if i.url not in seen]
            if not new:
                break
            for i in new:
                seen.add(i.url)
            items.extend(new)
            logger.debug(f"Automotive Business: página {page_num} — {len(new)} artigos")

        logger.info(f"Automotive Business: {len(items)} URLs coletadas")
        return items

    def _fetch_articles(self, items: list[_ListingItem], since: datetime) -> list[Article]:
        results: list[Article] = []
        with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
            futures = {pool.submit(self._fetch_one, item): item for item in items}
            for fut in as_completed(futures):
                item = futures[fut]
                try:
                    article = fut.result()
                except Exception as e:
                    logger.warning(f"Automotive Business: falha em {item.url[:80]}: {e}")
                    continue
                if article is None:
                    continue
                try:
                    pub = datetime.fromisoformat(article.published_at)
                except ValueError:
                    pub = None
                if pub is not None:
                    # Se o timestamp é meia-noite no horário de Brasília, a fonte
                    # forneceu apenas a data (sem hora). O artigo pode ter sido
                    # publicado em qualquer momento do dia — tratar como 23:59:59
                    # para não rejeitar incorretamente artigos de ontem.
                    pub_br = pub.astimezone(BR_TZ)
                    if pub_br.hour == 0 and pub_br.minute == 0 and pub_br.second == 0:
                        pub = pub_br.replace(
                            hour=23, minute=59, second=59
                        ).astimezone(timezone.utc)
                    if pub < since:
                        continue
                results.append(article)
        return results

    def _fetch_one(self, item: _ListingItem) -> Optional[Article]:
        resp = requests.get(item.url, headers=_HEADERS, timeout=20)
        resp.raise_for_status()
        pub, full_text = parse_article(resp.text)
        published_str = (
            pub.isoformat() if pub else datetime.now(timezone.utc).isoformat()
        )
        return Article(
            source=self.name,
            title=item.title,
            url=item.url,
            published_at=published_str,
            full_text=full_text,
            language=self.language,
        )
