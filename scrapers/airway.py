"""Airway scraper (https://www.airway.com.br/ultimas-noticias/).

WordPress site without RSS and without WAF — plain requests work.
Pagination via numbered pages (/ultimas-noticias/page/N/).
Article cards on the listing carry no date, so date is extracted
from each article page (JSON-LD datePublished / meta / <time>).
"""

from __future__ import annotations

import logging
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from html import unescape
from typing import Optional

import requests
import trafilatura

from fetcher.rss_fetcher import Article
from scrapers.playwright_utils import extract_published_at

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

_ARTICLE_LINK_RE = re.compile(
    r'<h2\s+class="entry-title">\s*<a\s+href="([^"]+)"[^>]*>([^<]+)</a>',
    flags=re.IGNORECASE,
)
_NEXT_PAGE_RE = re.compile(
    r'<a\s+class="next\s+page-numbers"\s+href="([^"]+)"',
    flags=re.IGNORECASE,
)
_TAG_STRIP_RE = re.compile(r"<[^>]+>")
_JSONLD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    flags=re.IGNORECASE | re.DOTALL,
)


@dataclass
class _ListingItem:
    url: str
    title: str


def parse_listing(html: str) -> tuple[list[_ListingItem], Optional[str]]:
    """Return (items, next_page_url) from a listing page HTML."""
    items: list[_ListingItem] = []
    seen: set[str] = set()

    # Since July/2026 Airway publishes the article list in the CollectionPage
    # JSON-LD, rather than in the former WordPress ``entry-title`` cards.
    # Prefer that structured data when present and retain the HTML parser below
    # as a fallback for older pages and temporary template rollbacks.
    for raw_json in _JSONLD_RE.findall(html):
        try:
            data = json.loads(raw_json.strip())
        except json.JSONDecodeError:
            continue
        nodes = data if isinstance(data, list) else [data]
        for node in nodes:
            if not isinstance(node, dict):
                continue
            entity = node.get("mainEntity")
            if not isinstance(entity, dict):
                continue
            entries = entity.get("itemListElement")
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                raw_url = entry.get("item")
                if isinstance(raw_url, dict):
                    raw_url = raw_url.get("@id") or raw_url.get("url")
                title = str(entry.get("name") or "").strip()
                if not isinstance(raw_url, str) or not raw_url or not title:
                    continue
                if raw_url.startswith("/"):
                    raw_url = "https://www.airway.com.br" + raw_url
                if raw_url in seen:
                    continue
                seen.add(raw_url)
                items.append(_ListingItem(url=raw_url, title=title))

    for url, raw_title in _ARTICLE_LINK_RE.findall(html):
        if url in seen:
            continue
        title = unescape(_TAG_STRIP_RE.sub("", raw_title)).strip()
        if not title:
            continue
        seen.add(url)
        items.append(_ListingItem(url=url, title=title))

    next_m = _NEXT_PAGE_RE.search(html)
    next_url = unescape(next_m.group(1)) if next_m else None
    return items, next_url


class AirwayScraper:
    name: str = "Airway"
    # The canonical tag currently advertises /latest-news/, but that endpoint
    # returns HTTP 404. The legacy route remains the live listing endpoint.
    base_url: str = "https://www.airway.com.br/ultimas-noticias/"
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
        """Paginate listing pages up to max_pages and collect all article URLs."""
        items: list[_ListingItem] = []
        url: Optional[str] = self.base_url

        for page_num in range(1, self.max_pages + 1):
            if url is None:
                break
            try:
                resp = requests.get(url, headers=_HEADERS, timeout=20)
                resp.raise_for_status()
            except Exception as e:
                logger.warning(f"Airway: erro ao buscar página {page_num}: {e}")
                break

            page_items, next_url = parse_listing(resp.text)
            items.extend(page_items)
            logger.debug(f"Airway: página {page_num} — {len(page_items)} artigos, next={next_url}")
            url = next_url

        logger.info(f"Airway: {len(items)} URLs coletadas ({page_num} páginas)")
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
                    logger.warning(f"Airway: falha em {item.url[:80]}: {e}")
                    continue
                if article is None:
                    continue
                try:
                    pub = datetime.fromisoformat(article.published_at)
                except ValueError:
                    pub = None
                if pub is not None and pub < since:
                    continue
                results.append(article)
        return results

    def _fetch_one(self, item: _ListingItem) -> Optional[Article]:
        resp = requests.get(item.url, headers=_HEADERS, timeout=20)
        resp.raise_for_status()
        html = resp.text

        published = extract_published_at(html)
        full_text = trafilatura.extract(
            html,
            include_comments=False,
            include_tables=False,
            no_fallback=False,
        ) or ""
        published_str = (
            published.isoformat() if published
            else datetime.now(timezone.utc).isoformat()
        )

        return Article(
            source=self.name,
            title=item.title,
            url=item.url,
            published_at=published_str,
            full_text=full_text,
            language=self.language,
        )
