"""Bloomberg industry scraper.

Covers:
  - Transportation: https://www.bloomberg.com/industries/transportation
  - Energy:         https://www.bloomberg.com/industries/energy

Bloomberg blocks plain requests and headless Chromium with fingerprinting.
Access requires real Chrome (channel="chrome") + stealth init script.

Listing data comes from Bloomberg's internal JSON pagination API
(lineup-next/api/paginate), which returns exact ISO-8601 publishedAt
timestamps and clean headlines — no date parsing needed.

Article body is extracted pre-paywall: Bloomberg serves 1-2 paragraphs
server-rendered before the JS paywall wall fires. trafilatura grabs them.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

import trafilatura

from fetcher.rss_fetcher import Article
from scrapers.playwright_utils import browser_context, extract_published_at

logger = logging.getLogger(__name__)

_BASE = "https://www.bloomberg.com"
_PAGINATE_URL = (
    _BASE
    + "/lineup-next/api/paginate"
    "?id=archive_story_list"
    "&page={page_id}"
    "&offset={offset}"
    "&variation=archive"
    "&type=lineup_content"
    "&locale=en"
)
_PAGE_SIZE = 12

# JS executed inside the browser to call Bloomberg's internal paginate API
# using the session credentials already established by the page visit.
_FETCH_JS = """
async (url) => {
    const r = await fetch(url, {credentials: "include"});
    if (!r.ok) return null;
    return r.json();
}
"""


def _parse_items(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract item list from a paginate API response dict."""
    return data.get("archive_story_list", {}).get("items", [])


def _extract_items_from_html(html: str) -> list[dict[str, Any]]:
    """Extract the offset=0 items embedded in Bloomberg's __NEXT_DATA__ script.

    Bloomberg embeds the first page of articles server-side; the paginate API
    only returns subsequent pages (offset>=12).  We parse __NEXT_DATA__ to
    recover these first items without a second network call.
    """
    m = re.search(
        r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
        html, flags=re.DOTALL | re.IGNORECASE,
    )
    if not m:
        return []
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return []

    # Walk the nested props tree looking for archive_story_list.items
    raw = json.dumps(data)
    # Fast path: search for publishedAt patterns and reconstruct minimal dicts
    # using the full data walk
    results: list[dict[str, Any]] = []
    _walk_for_items(data, results)
    return results


def _walk_for_items(node: Any, out: list[dict[str, Any]]) -> None:
    """Recursively find dicts that look like Bloomberg article items."""
    if isinstance(node, list):
        for item in node:
            _walk_for_items(item, out)
    elif isinstance(node, dict):
        if "headline" in node and "publishedAt" in node and "url" in node:
            out.append(node)
        else:
            for v in node.values():
                _walk_for_items(v, out)


def _to_utc(iso: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(timezone.utc)
    except (ValueError, AttributeError):
        return None


class BloombergScraper:
    """Scraper for a single Bloomberg industry page.

    Instantiate once per industry:
        BloombergScraper("Bloomberg Transportation", "phx-industries-transportation")
        BloombergScraper("Bloomberg Energy",         "phx-industries-energy")
    """

    language: str = "en"

    def __init__(self, name: str, page_id: str, max_pages: Optional[int] = None) -> None:
        self.name = name
        self.page_id = page_id
        self.base_url = f"{_BASE}/industries/{page_id.removeprefix('phx-industries-')}"
        from config.settings import SCRAPER_MAX_PAGES, SCRAPER_CONCURRENCY, BLOOMBERG_LOOKBACK_HOURS
        self.max_pages = max_pages if max_pages is not None else SCRAPER_MAX_PAGES
        self.concurrency = SCRAPER_CONCURRENCY
        self.lookback_hours = BLOOMBERG_LOOKBACK_HOURS

    def fetch_articles(self, since: datetime) -> list[Article]:
        from playwright.sync_api import TimeoutError as PWTimeout

        # Bloomberg industry pages are curated digests that update infrequently.
        # We collect everything available up to max_pages and rely on the
        # screener's Qdrant novelty detection for deduplication rather than a
        # fixed time window that would silently skip content on quiet weeks.
        # The lookback_hours cap prevents unbounded historical crawls.
        effective_since = datetime.now(timezone.utc) - timedelta(hours=self.lookback_hours)
        # Honor the caller's since if it asks for an even wider window.
        effective_since = min(effective_since, since)
        logger.debug(f"{self.name}: effective_since={effective_since.isoformat()}")

        results: list[Article] = []

        with browser_context(channel="chrome", stealth=True, locale="en-US") as ctx:
            listing_page = ctx.new_page()
            try:
                listing_page.goto(
                    self.base_url, wait_until="networkidle", timeout=45_000
                )
            except PWTimeout:
                logger.warning(f"{self.name}: timeout loading listing page")
                return []
            except Exception:
                # networkidle can time out on slow pages; continue with current state
                pass

            title = listing_page.title().lower()
            if "robot" in title or "access denied" in title or "are you a" in title:
                logger.error(
                    f"{self.name}: Bloomberg bot-detection triggered — "
                    "ensure Chrome is installed (channel='chrome')"
                )
                return []

            # --- Page 0: extract items embedded in __NEXT_DATA__ HTML ---
            html_items = _extract_items_from_html(listing_page.content())
            first_page_urls: set[str] = {i.get("url", "") for i in html_items}
            logger.debug(f"{self.name}: {len(html_items)} items from __NEXT_DATA__")

            all_items: list[dict[str, Any]] = []
            seen_urls: set[str] = set()
            stop = False

            def _add_items(items: list[dict[str, Any]]) -> bool:
                """Append items, return True if we should stop pagination."""
                for item in items:
                    url = item.get("url", "")
                    if url in seen_urls:
                        continue
                    seen_urls.add(url)
                    pub = _to_utc(item.get("publishedAt", ""))
                    if pub is not None and pub < effective_since:
                        return True
                    all_items.append(item)
                return False

            if _add_items(html_items):
                stop = True

            # --- Pages 1+: fetch via paginate API (offset=12, 24, …) ---
            if not stop:
                for page_num in range(1, self.max_pages):
                    offset = page_num * _PAGE_SIZE
                    api_url = _PAGINATE_URL.format(page_id=self.page_id, offset=offset)
                    try:
                        data = listing_page.evaluate(_FETCH_JS, api_url)
                    except Exception as e:
                        logger.warning(f"{self.name}: API call failed (offset={offset}): {e}")
                        break

                    if not data:
                        logger.debug(f"{self.name}: empty API response at offset={offset}, stopping")
                        break

                    items = _parse_items(data)
                    if not items:
                        break

                    if _add_items(items):
                        break

                    logger.debug(
                        f"{self.name}: API page {page_num} — {len(items)} items, "
                        f"total so far: {len(all_items)}"
                    )

            listing_page.close()
            logger.info(f"{self.name}: {len(all_items)} artigos candidatos da listagem")

            if not all_items:
                return []

            # --- Fetch article pages sequentially ---
            article_page = ctx.new_page()
            for item in all_items:
                raw_url = item.get("url", "")
                # __NEXT_DATA__ may have absolute URLs; API items have relative ones.
                url = raw_url if raw_url.startswith("http") else _BASE + raw_url
                # Only fetch bloomberg.com article pages; skip external links.
                if not url.startswith(_BASE + "/"):
                    continue

                try:
                    article_page.goto(
                        url, wait_until="domcontentloaded", timeout=30_000
                    )
                    html = article_page.content()
                except Exception as e:
                    logger.warning(f"{self.name}: falha ao carregar {url[:80]}: {e}")
                    continue

                # Use precise timestamp from API; fall back to HTML extraction.
                # item.get("publishedAt") may return None for JSON null values,
                # so use `or ""` rather than a default arg to _to_utc.
                pub = _to_utc(item.get("publishedAt") or "")
                if pub is None:
                    pub = extract_published_at(html)
                if pub is None:
                    if raw_url in first_page_urls:
                        pub = datetime.now(timezone.utc)
                    else:
                        logger.debug(f"{self.name}: data desconhecida, pulando {url[:80]}")
                        continue
                # effective_since already applied at listing stage; honour it here too.
                if pub < effective_since:
                    continue

                full_text = trafilatura.extract(
                    html,
                    include_comments=False,
                    include_tables=False,
                    no_fallback=False,
                ) or ""

                # If trafilatura got nothing, use the API summary as fallback
                if not full_text and item.get("summary"):
                    full_text = item["summary"]

                published_str = pub.isoformat()
                results.append(Article(
                    source=self.name,
                    title=item.get("headline", ""),
                    url=url,
                    published_at=published_str,
                    full_text=full_text,
                    language=self.language,
                ))
            article_page.close()

        logger.info(f"{self.name}: {len(results)} artigos retornados")
        return results
