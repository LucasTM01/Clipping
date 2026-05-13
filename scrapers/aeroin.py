"""Aeroin scraper (https://aeroin.net/).

Aeroin is a WordPress site without RSS, behind Sucuri CloudProxy (requires JS execution).
Listing uses an infinite-scroll "carregar mais" button.
Article pages expose `datePublished` via JSON-LD.

All navigation is done inside a single Playwright browser session (sequential) to avoid
greenlet/thread conflicts with Playwright's sync API.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from html import unescape
from typing import Optional

import trafilatura

from fetcher.rss_fetcher import Article
from scrapers.playwright_utils import browser_context, extract_published_at

logger = logging.getLogger(__name__)


@dataclass
class _ListingItem:
    url: str
    title: str


_LISTING_LINK_RE = re.compile(
    r'<h[1-6][^>]*class="[^"]*entry-title[^"]*"[^>]*>\s*'
    r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
    flags=re.DOTALL | re.IGNORECASE,
)
_FALLBACK_LINK_RE = re.compile(
    r'<a[^>]+rel="bookmark"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
    flags=re.DOTALL | re.IGNORECASE,
)
_TAG_STRIP_RE = re.compile(r"<[^>]+>")


def _clean_title(raw: str) -> str:
    return unescape(_TAG_STRIP_RE.sub("", raw)).strip()


def parse_listing(html: str) -> list[_ListingItem]:
    """Extract (url, title) pairs from an Aeroin listing page."""
    items: list[_ListingItem] = []
    seen: set[str] = set()

    for regex in (_LISTING_LINK_RE, _FALLBACK_LINK_RE):
        for url, raw_title in regex.findall(html):
            if url in seen:
                continue
            title = _clean_title(raw_title)
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


class AeroinScraper:
    name: str = "Aeroin"
    base_url: str = "https://aeroin.net/"
    language: str = "pt-br"

    def __init__(self, max_pages: Optional[int] = None) -> None:
        from config.settings import SCRAPER_MAX_PAGES
        self.max_pages = max_pages if max_pages is not None else SCRAPER_MAX_PAGES

    def fetch_articles(self, since: datetime) -> list[Article]:
        """Collect and parse articles published since `since`.

        Uses a single Playwright browser session for everything — listing
        pagination needs JS (Sucuri WAF), and article pages are fetched in
        the same session to reuse the established WAF cookie.
        """
        from playwright.sync_api import TimeoutError as PWTimeout

        results: list[Article] = []

        with browser_context() as ctx:
            # --- Step 1: collect listing ---
            listing_page = ctx.new_page()
            listing_page.goto(self.base_url, wait_until="domcontentloaded", timeout=30_000)

            for _ in range(self.max_pages):
                button = listing_page.locator(
                    "a:has-text('Carregar mais'), button:has-text('Carregar mais'), "
                    ".load-more, a.load-more, button.load-more"
                ).first
                try:
                    if not button.is_visible(timeout=2_000):
                        break
                except PWTimeout:
                    break
                try:
                    button.click(timeout=5_000)
                    listing_page.wait_for_load_state("networkidle", timeout=10_000)
                except PWTimeout:
                    break

                listing_html = listing_page.content()
                earliest = _earliest_visible_date(listing_html)
                if earliest is not None and earliest < since:
                    break

            listing_html = listing_page.content()
            listing_page.close()

            items = parse_listing(listing_html)
            logger.info(f"Aeroin: {len(items)} URLs coletadas da listagem")

            # --- Step 2: fetch each article page sequentially ---
            article_page = ctx.new_page()
            for item in items:
                try:
                    article_page.goto(item.url, wait_until="domcontentloaded", timeout=30_000)
                    html = article_page.content()
                except Exception as e:
                    logger.warning(f"Aeroin: falha ao carregar {item.url[:80]}: {e}")
                    continue

                published, full_text = parse_article(html)
                if published is not None and published < since:
                    continue

                published_str = (
                    published.isoformat() if published
                    else datetime.now(timezone.utc).isoformat()
                )
                results.append(Article(
                    source=self.name,
                    title=item.title,
                    url=item.url,
                    published_at=published_str,
                    full_text=full_text,
                    language=self.language,
                ))
            article_page.close()

        return results


def _earliest_visible_date(html: str) -> Optional[datetime]:
    """Approximate earliest publication date visible on a listing page."""
    candidates: list[datetime] = []
    for m in re.finditer(
        r'<time[^>]+datetime=["\']([^"\']+)["\']', html, flags=re.IGNORECASE
    ):
        try:
            dt = datetime.fromisoformat(m.group(1).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            candidates.append(dt)
        except ValueError:
            continue
    return min(candidates) if candidates else None
