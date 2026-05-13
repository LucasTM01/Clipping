"""Reuters scrapers via the public Google News Sitemap XML.

Reuters is protected by Cloudflare — direct page requests return a CAPTCHA
challenge and article bodies cannot be fetched programmatically.

Instead, we use Reuters' publicly accessible Google News Sitemap XML:
    https://www.reuters.com/arc/outboundfeeds/news-sitemap/?outputType=xml

The sitemap contains ISO-8601 publication dates and article titles for all
Reuters sections, paginated in reverse-chronological order (50 entries/page
via ?from=N).  We filter entries whose URL path contains the target section
and stop as soon as we reach articles older than `since`.

Article bodies are left empty (`full_text=""`).  The pipeline's
`enrich_full_text` step will attempt an HTTP re-fetch, which Cloudflare will
block; `trafilatura` returns "" for the challenge page, so the article flows
through the screener based on its title alone.

Sections covered (one ReutersScraper instance per section):
    /business/aerospace-defense/    → "Reuters Aerospace & Defense"
    /business/autos-transportation/ → "Reuters Autos & Transportation"
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import requests

from fetcher.rss_fetcher import Article

logger = logging.getLogger(__name__)

_SITEMAP_BASE = (
    "https://www.reuters.com/arc/outboundfeeds/news-sitemap/?outputType=xml"
)
_PAGE_SIZE = 50  # Reuters returns 50 entries per sitemap page

_SECTION = "/business/aerospace-defense/"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/xml,application/xml,application/xhtml+xml,*/*",
}

# Each <url> block in the sitemap
_URL_BLOCK_RE = re.compile(r"<url>(.*?)</url>", re.DOTALL)
_LOC_RE = re.compile(r"<loc>([^<]+)</loc>")
_PUB_DATE_RE = re.compile(r"<news:publication_date>([^<]+)</news:publication_date>")
_TITLE_CDATA_RE = re.compile(r"<news:title><!\[CDATA\[(.*?)\]\]></news:title>", re.DOTALL)
_TITLE_PLAIN_RE = re.compile(r"<news:title>([^<]+)</news:title>")


@dataclass
class _SitemapItem:
    url: str
    title: str
    pub_date: datetime


def _parse_iso(s: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(s.strip().replace("Z", "+00:00")).astimezone(
            timezone.utc
        )
    except (ValueError, AttributeError):
        return None


def parse_sitemap(xml_text: str, section: str = _SECTION) -> list[_SitemapItem]:
    """Extract section-filtered items from a news-sitemap XML page."""
    items: list[_SitemapItem] = []

    for block_m in _URL_BLOCK_RE.finditer(xml_text):
        block = block_m.group(1)

        loc_m = _LOC_RE.search(block)
        if not loc_m or section not in loc_m.group(1):
            continue
        url = loc_m.group(1).strip()

        date_m = _PUB_DATE_RE.search(block)
        pub_date = _parse_iso(date_m.group(1)) if date_m else None
        if pub_date is None:
            continue

        title_m = _TITLE_CDATA_RE.search(block) or _TITLE_PLAIN_RE.search(block)
        title = title_m.group(1).strip() if title_m else ""
        if not title:
            continue

        items.append(_SitemapItem(url=url, title=title, pub_date=pub_date))

    return items


_REUTERS_BASE = "https://www.reuters.com"


class ReutersScraper:
    """Scraper for a single Reuters section via the public news sitemap.

    Instantiate once per section — see registry.py for the live instances.
    """

    language: str = "en"

    def __init__(
        self,
        name: str = "Reuters Aerospace & Defense",
        section: str = _SECTION,
        max_pages: Optional[int] = None,
    ) -> None:
        self.name = name
        self.section = section
        self.base_url = f"{_REUTERS_BASE}{section}"
        from config.settings import SCRAPER_MAX_PAGES
        self.max_pages = max_pages if max_pages is not None else SCRAPER_MAX_PAGES

    def fetch_articles(self, since: datetime) -> list[Article]:
        items = self._fetch_sitemap_items(since)
        logger.info(f"{self.name}: {len(items)} artigos da listagem")
        return [
            Article(
                source=self.name,
                title=item.title,
                url=item.url,
                published_at=item.pub_date.isoformat(),
                full_text="",   # Cloudflare blocks article pages
                language=self.language,
            )
            for item in items
        ]

    def _fetch_sitemap_items(self, since: datetime) -> list[_SitemapItem]:
        items: list[_SitemapItem] = []
        seen: set[str] = set()

        for page_num in range(self.max_pages):
            offset = page_num * _PAGE_SIZE
            url = _SITEMAP_BASE + (f"&from={offset}" if offset else "")

            try:
                resp = requests.get(url, headers=_HEADERS, timeout=20)
                resp.raise_for_status()
            except Exception as e:
                logger.warning(f"{self.name}: erro ao buscar sitemap offset={offset}: {e}")
                break

            # Count total <url> blocks to detect end of sitemap (section may be sparse)
            total_in_page = len(_URL_BLOCK_RE.findall(resp.text))
            if total_in_page == 0:
                break

            page_items = parse_sitemap(resp.text, self.section)

            # Check oldest date across the whole page to decide early-exit.
            # We need dates from ALL sections to know when we've passed `since`,
            # because the target section may appear sparsely across pages.
            all_dates = [
                _parse_iso(m)
                for m in _PUB_DATE_RE.findall(resp.text)
                if _parse_iso(m) is not None
            ]
            oldest_on_page = min(all_dates) if all_dates else None

            for item in page_items:
                if item.url in seen:
                    continue
                seen.add(item.url)
                if item.pub_date >= since:
                    items.append(item)

            logger.debug(
                f"{self.name}: sitemap page {page_num + 1} — "
                f"{total_in_page} total entries, {len(page_items)} section matches"
            )

            if oldest_on_page is not None and oldest_on_page < since:
                break

        return items
