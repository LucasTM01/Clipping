"""Canal Solar scraper (https://canalsolar.com.br/noticias/).

WordPress + Elementor site — listing is server-rendered, pagination is
JavaScript-driven AJAX (Content Views plugin). Only the first page is
collected; date filtering discards articles older than `since`.

Date on the listing is plain PT-BR text ("8 de maio de 2026") with no
datetime attribute. It is parsed to midnight BR_TZ for pre-filtering.
Precise timestamps come from article page JSON-LD.
"""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, date, time, timezone
from html import unescape
from typing import Optional

import requests
import trafilatura

from fetcher.rss_fetcher import Article
from scrapers.playwright_utils import BR_TZ, extract_published_at

logger = logging.getLogger(__name__)

_BASE = "https://canalsolar.com.br"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

_PT_MONTHS: dict[str, int] = {
    "janeiro": 1, "fevereiro": 2, "março": 3, "abril": 4,
    "maio": 5, "junho": 6, "julho": 7, "agosto": 8,
    "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12,
}

# Matches "8 de maio de 2026" or "15 de março de 2026"
_DATE_TEXT_RE = re.compile(
    r'(\d{1,2})\s+de\s+(\w+)\s+de\s+(\d{4})', re.IGNORECASE
)

_TITLE_LINK_RE = re.compile(
    r'<h3[^>]+class="[^"]*elementor-post__title[^"]*"[^>]*>\s*'
    r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
    flags=re.DOTALL | re.IGNORECASE,
)
_DATE_SPAN_RE = re.compile(
    r'<span[^>]+class="[^"]*elementor-post-date[^"]*"[^>]*>(.*?)</span>',
    flags=re.DOTALL | re.IGNORECASE,
)
_TAG_STRIP_RE = re.compile(r"<[^>]+>")


@dataclass
class _ListingItem:
    url: str
    title: str
    approx_date: Optional[datetime]  # midnight BR_TZ → UTC; None if unparseable


def _parse_pt_date(text: str) -> Optional[datetime]:
    """Parse "D de MÊS de YYYY" → datetime at midnight BR_TZ converted to UTC."""
    m = _DATE_TEXT_RE.search(text.strip().lower())
    if not m:
        return None
    day, month_name, year = int(m.group(1)), m.group(2), int(m.group(3))
    month = _PT_MONTHS.get(month_name)
    if month is None:
        return None
    try:
        d = date(year, month, day)
    except ValueError:
        return None
    return datetime.combine(d, time.min, tzinfo=BR_TZ).astimezone(timezone.utc)


def parse_listing(html: str) -> list[_ListingItem]:
    """Extract (url, title, approx_date) from a Canal Solar listing page."""
    items: list[_ListingItem] = []
    seen: set[str] = set()

    # Pair each title/link with the nearest date span that follows it in the HTML
    title_matches = list(_TITLE_LINK_RE.finditer(html))
    date_matches = list(_DATE_SPAN_RE.finditer(html))

    for tm in title_matches:
        raw_url = tm.group(1)
        raw_title = tm.group(2)
        url = raw_url if raw_url.startswith("http") else _BASE + raw_url
        if url in seen:
            continue
        title = unescape(_TAG_STRIP_RE.sub("", raw_title)).strip()
        if not title:
            continue
        seen.add(url)

        # Find the closest date span appearing after this title in the HTML
        approx: Optional[datetime] = None
        for dm in date_matches:
            if dm.start() > tm.start():
                date_text = unescape(_TAG_STRIP_RE.sub("", dm.group(1))).strip()
                approx = _parse_pt_date(date_text)
                break

        items.append(_ListingItem(url=url, title=title, approx_date=approx))

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


class CanalSolarScraper:
    name: str = "Canal Solar"
    base_url: str = f"{_BASE}/noticias/"
    language: str = "pt-br"

    def __init__(self, concurrency: Optional[int] = None) -> None:
        from config.settings import SCRAPER_CONCURRENCY
        self.concurrency = concurrency if concurrency is not None else SCRAPER_CONCURRENCY

    def fetch_articles(self, since: datetime) -> list[Article]:
        try:
            resp = requests.get(self.base_url, headers=_HEADERS, timeout=20)
            resp.raise_for_status()
        except Exception as e:
            logger.warning(f"Canal Solar: erro ao buscar listagem: {e}")
            return []

        items = parse_listing(resp.text)
        # Pre-filter: drop items whose listing date-in-BR_TZ is before since's date-in-BR_TZ.
        # Listing dates are midnight BR_TZ (no time), so comparing timestamps directly would
        # incorrectly discard same-day articles published after midnight. Date comparison avoids this.
        since_date_br = since.astimezone(BR_TZ).date()
        candidates = [
            i for i in items
            if i.approx_date is None
            or i.approx_date.astimezone(BR_TZ).date() >= since_date_br
        ]
        logger.info(
            f"Canal Solar: {len(items)} artigos na listagem, "
            f"{len(candidates)} candidatos após filtro de data"
        )
        if not candidates:
            return []
        return self._fetch_articles(candidates, since)

    def _fetch_articles(self, items: list[_ListingItem], since: datetime) -> list[Article]:
        results: list[Article] = []
        with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
            futures = {pool.submit(self._fetch_one, item): item for item in items}
            for fut in as_completed(futures):
                item = futures[fut]
                try:
                    article = fut.result()
                except Exception as e:
                    logger.warning(f"Canal Solar: falha em {item.url[:80]}: {e}")
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
        pub, full_text = parse_article(resp.text)
        # Fall back to listing approx_date if article page has no parseable date
        if pub is None:
            pub = item.approx_date
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
