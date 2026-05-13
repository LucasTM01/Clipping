"""Portal Solar scraper (https://www.portalsolar.com.br/noticias/ultimas-noticias).

Next.js SSR site — server-rendered, plain requests sufficient.
The listing page embeds one JSON-LD NewsArticle block per article card,
exposing title, URL and datePublished without needing per-article fetches.
Pagination via ?page=N query string.
Articles fetched in parallel for full text only.
"""

from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import requests
import trafilatura

from fetcher.rss_fetcher import Article
from scrapers.playwright_utils import extract_published_at

logger = logging.getLogger(__name__)

_BASE = "https://www.portalsolar.com.br"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

_JSON_LD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    flags=re.DOTALL | re.IGNORECASE,
)


@dataclass
class _ListingItem:
    url: str
    title: str
    pub_date: Optional[datetime]


def _parse_date(s: str) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
    except (ValueError, AttributeError):
        return None


def parse_listing(html: str) -> list[_ListingItem]:
    """Extract articles from JSON-LD NewsArticle blocks on a listing page."""
    items: list[_ListingItem] = []
    seen: set[str] = set()

    for m in _JSON_LD_RE.finditer(html):
        try:
            data = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        if data.get("@type") != "NewsArticle":
            continue

        raw_url = (
            data.get("mainEntityOfPage", {}).get("@id", "")
            or data.get("url", "")
        )
        if not raw_url:
            continue
        url = raw_url if raw_url.startswith("http") else _BASE + raw_url
        if url in seen:
            continue
        seen.add(url)

        title = data.get("headline", "").strip()
        pub_date = _parse_date(data.get("datePublished", ""))
        items.append(_ListingItem(url=url, title=title, pub_date=pub_date))

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


class PortalSolarScraper:
    name: str = "Portal Solar"
    base_url: str = f"{_BASE}/noticias/ultimas-noticias"
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
        candidates = self._collect_listing(since)
        if not candidates:
            return []
        return self._fetch_articles(candidates, since)

    def _collect_listing(self, since: datetime) -> list[_ListingItem]:
        """Paginate ?page=N, stop when oldest article on page predates since."""
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
                logger.warning(f"Portal Solar: erro na página {page_num}: {e}")
                break

            page_items = parse_listing(resp.text)
            new = [i for i in page_items if i.url not in seen]
            if not new:
                break
            for i in new:
                seen.add(i.url)
            items.extend(new)
            logger.debug(f"Portal Solar: página {page_num} — {len(new)} artigos")

            # Early-exit: if the oldest dated article on this page is before since
            dated = [i.pub_date for i in new if i.pub_date is not None]
            if dated and min(dated) < since:
                break

        logger.info(f"Portal Solar: {len(items)} URLs coletadas")
        return items

    def _fetch_articles(self, items: list[_ListingItem], since: datetime) -> list[Article]:
        # Drop items already known to be older than since
        candidates = [i for i in items if i.pub_date is None or i.pub_date >= since]

        results: list[Article] = []
        with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
            futures = {pool.submit(self._fetch_one, item): item for item in candidates}
            for fut in as_completed(futures):
                item = futures[fut]
                try:
                    article = fut.result()
                except Exception as e:
                    logger.warning(f"Portal Solar: falha em {item.url[:80]}: {e}")
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

        # Prefer date from listing JSON-LD (already parsed); fall back to article HTML
        pub = item.pub_date or extract_published_at(resp.text)
        full_text = trafilatura.extract(
            resp.text,
            include_comments=False,
            include_tables=False,
            no_fallback=False,
        ) or ""

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
