"""O Globo scraper (https://oglobo.globo.com/ultimas-noticias/).

JS-rendered React SPA — Playwright required.
Listing cards expose `span.feed-post-datetime` with plain relative text
("Há 5 minutos", "Agora") and no datetime attribute. Exact publication
time is extracted from each article via `<time datetime="...">`.
Pagination via `.load-more a` ("Veja mais" button).

All navigation runs inside a single Playwright browser session.
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
from scrapers.playwright_utils import (
    browser_context,
    extract_published_at,
    parse_relative_time_pt,
)

logger = logging.getLogger(__name__)


@dataclass
class _ListingItem:
    url: str
    title: str
    approx_time: Optional[datetime]


# Matches any <a> tag regardless of attribute order, captures full tag + inner text
_A_TAG_RE = re.compile(r"(<a\s[^>]+>)(.*?)</a>", flags=re.DOTALL | re.IGNORECASE)
_CLASS_ATTR_RE = re.compile(r'\bclass="([^"]*)"', flags=re.IGNORECASE)
_HREF_ATTR_RE = re.compile(r'\bhref="([^"]+)"', flags=re.IGNORECASE)
_DATETIME_RE = re.compile(
    r'<span[^>]+class="[^"]*feed-post-datetime[^"]*"[^>]*>(.*?)</span>',
    flags=re.DOTALL | re.IGNORECASE,
)
_LOAD_MORE_RE = re.compile(
    r'<div[^>]+class="[^"]*load-more[^"]*"[^>]*>.*?<a[^>]+href="([^"]+)"',
    flags=re.DOTALL | re.IGNORECASE,
)
_TAG_STRIP_RE = re.compile(r"<[^>]+>")


def _clean(raw: str) -> str:
    return unescape(_TAG_STRIP_RE.sub("", raw)).strip()


def parse_listing(html: str, now: datetime) -> tuple[list[_ListingItem], bool]:
    """Extract listing items and whether a 'Veja mais' button is present."""
    items: list[_ListingItem] = []
    seen: set[str] = set()

    link_positions: list[tuple[int, str, str]] = []
    for m in _A_TAG_RE.finditer(html):
        tag_open = m.group(1)
        class_m = _CLASS_ATTR_RE.search(tag_open)
        if not class_m or "feed-post-link" not in class_m.group(1):
            continue
        href_m = _HREF_ATTR_RE.search(tag_open)
        if not href_m:
            continue
        url = href_m.group(1)
        title = _clean(m.group(2))
        link_positions.append((m.start(), url, title))

    datetime_positions = [
        (m.start(), _clean(m.group(1)))
        for m in _DATETIME_RE.finditer(html)
    ]

    for pos, url, title in link_positions:
        if not url or not title or url in seen:
            continue
        if not url.startswith("http"):
            continue
        seen.add(url)

        # Find the nearest datetime span that appears after this link in the HTML
        approx: Optional[datetime] = None
        for dt_pos, dt_text in datetime_positions:
            if dt_pos > pos:
                approx = parse_relative_time_pt(dt_text, now)
                break

        items.append(_ListingItem(url=url, title=title, approx_time=approx))

    has_more = bool(_LOAD_MORE_RE.search(html))
    return items, has_more


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


class OGloboScraper:
    name: str = "OGlobo"
    base_url: str = "https://oglobo.globo.com/ultimas-noticias/"
    language: str = "pt-br"

    def __init__(self, max_pages: Optional[int] = None) -> None:
        from config.settings import SCRAPER_MAX_PAGES
        self.max_pages = max_pages if max_pages is not None else SCRAPER_MAX_PAGES

    def fetch_articles(self, since: datetime) -> list[Article]:
        """Collect and parse articles published since `since`.

        Uses a single Playwright session — listing and articles fetched
        sequentially to avoid greenlet conflicts and share the browser session.
        """
        from playwright.sync_api import TimeoutError as PWTimeout

        results: list[Article] = []

        with browser_context() as ctx:
            listing_page = ctx.new_page()
            listing_page.goto(
                self.base_url, wait_until="domcontentloaded", timeout=30_000
            )

            # --- Step 1: paginate until we have enough older articles ---
            for _ in range(self.max_pages):
                now = datetime.now(timezone.utc)
                listing_html = listing_page.content()
                items_so_far, _ = parse_listing(listing_html, now)

                times = [i.approx_time for i in items_so_far if i.approx_time]
                oldest = min(times, default=None)
                if oldest is not None and oldest < since:
                    break

                button = listing_page.locator(".load-more a").first
                try:
                    if not button.is_visible(timeout=2_000):
                        break
                    button.click(timeout=5_000)
                    listing_page.wait_for_load_state("networkidle", timeout=10_000)
                except PWTimeout:
                    break

            # --- Step 2: collect final listing, keep only candidates ---
            now = datetime.now(timezone.utc)
            items, _ = parse_listing(listing_page.content(), now)
            items = [
                i for i in items
                if i.approx_time is None or i.approx_time >= since
            ]
            listing_page.close()
            logger.info(f"OGlobo: {len(items)} URLs coletadas da listagem")

            # --- Step 3: fetch each article page sequentially ---
            article_page = ctx.new_page()
            for item in items:
                try:
                    article_page.goto(
                        item.url, wait_until="domcontentloaded", timeout=30_000
                    )
                    html = article_page.content()
                except Exception as e:
                    logger.warning(f"OGlobo: falha ao carregar {item.url[:80]}: {e}")
                    continue

                published, full_text = parse_article(html)
                if published is not None and published < since:
                    continue

                published_str = (
                    published.isoformat()
                    if published
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
