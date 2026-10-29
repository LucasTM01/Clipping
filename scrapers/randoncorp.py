"""Randoncorp blog scraper (https://www.randoncorp.com/pt/blog/).

Umbraco site (not WordPress — no /wp-json/, no /feed/). The blog page is
server-rendered and lists EVERY post (~800, ~1.4 MB) as cards:

    <a href="https://www.randoncorp.com/pt/blog/<slug>/" class="... js-news"
       data-cat="Noticias" data-title="<title>">
        ...<div class="item__details__date">Publicado em 28/10/2026</div>...
    </a>

Only the day is published (no time, no JSON-LD/meta date on the post page
either), so a post is kept when its day (BR time) is on/after the day of
``since``. A post from yesterday can therefore show up in both the 19h run and
the next morning's run — accepted, since RAPT4 is a covered name and missing a
company post is worse than repeating one.

Post bodies are fetched in parallel and extracted with trafilatura; the
search-widget text the extractor picks up ("Buscando por") is stripped.
"""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, time, timezone
from html import unescape

import requests
import trafilatura

from fetcher.rss_fetcher import Article
from scrapers.playwright_utils import BR_TZ

logger = logging.getLogger(__name__)

_BASE = "https://www.randoncorp.com"
_LISTING_URL = f"{_BASE}/pt/blog/"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0.0.0 Safari/537.36"
    )
}

_CARD_RE = re.compile(
    r'<a\s+href="(https://www\.randoncorp\.com/pt/blog/[^"]+)"([^>]*)>(.*?)</a>',
    flags=re.DOTALL | re.IGNORECASE,
)
_DATA_TITLE_RE = re.compile(r'data-title="([^"]*)"')
_ITEM_TITLE_RE = re.compile(r'<div class="item__title">(.*?)</div>', re.DOTALL)
_DATE_RE = re.compile(r"Publicado em (\d{2})/(\d{2})/(\d{4})")
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_NOISE_PREFIX_RE = re.compile(r"^\s*Buscando por\s*", re.IGNORECASE)


@dataclass
class _ListingItem:
    url: str
    title: str
    pub_date: datetime  # midnight BR_TZ, as UTC


def _clean(text: str) -> str:
    return _WS_RE.sub(" ", unescape(_TAG_RE.sub("", text))).strip()


def parse_listing(html: str) -> list[_ListingItem]:
    """Extract every dated post card from the blog page (newest first)."""
    by_url: dict[str, _ListingItem] = {}
    for url, attrs, inner in _CARD_RE.findall(html):
        url = unescape(url)
        date_m = _DATE_RE.search(inner)
        if not date_m:
            continue
        title_m = _DATA_TITLE_RE.search(attrs) or _ITEM_TITLE_RE.search(inner)
        title = _clean(title_m.group(1)) if title_m else ""
        if not title:
            continue
        day, month, year = (int(x) for x in date_m.groups())
        try:
            pub = datetime.combine(
                datetime(year, month, day).date(), time(0, 0), tzinfo=BR_TZ
            ).astimezone(timezone.utc)
        except ValueError:
            continue
        by_url.setdefault(url, _ListingItem(url=url, title=title, pub_date=pub))
    return sorted(by_url.values(), key=lambda i: i.pub_date, reverse=True)


def is_in_window(item: _ListingItem, since: datetime) -> bool:
    """Day-precision filter: keep posts whose BR day is on/after since's BR day."""
    return item.pub_date.astimezone(BR_TZ).date() >= since.astimezone(BR_TZ).date()


def extract_body(html: str) -> str:
    text = trafilatura.extract(
        html, include_comments=False, include_tables=False, no_fallback=False
    ) or ""
    return _NOISE_PREFIX_RE.sub("", text).strip()


class RandoncorpScraper:
    name: str = "Randoncorp Blog"
    base_url: str = _LISTING_URL
    language: str = "pt-br"

    def __init__(self) -> None:
        from config.settings import SCRAPER_CONCURRENCY
        self.concurrency = SCRAPER_CONCURRENCY

    def fetch_articles(self, since: datetime) -> list[Article]:
        try:
            resp = requests.get(self.base_url, headers=_HEADERS, timeout=30)
            resp.raise_for_status()
            resp.encoding = resp.encoding or "utf-8"
        except Exception as e:
            logger.warning(f"Randoncorp: erro ao buscar listagem: {e}")
            return []

        items = [i for i in parse_listing(resp.text) if is_in_window(i, since)]
        logger.info(f"Randoncorp: {len(items)} posts no período")
        if not items:
            return []

        articles: list[Article] = []
        with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
            futures = {pool.submit(self._fetch_body, i.url): i for i in items}
            for fut in as_completed(futures):
                item = futures[fut]
                try:
                    body = fut.result()
                except Exception as e:
                    logger.warning(f"Randoncorp: falha em {item.url[:80]}: {e}")
                    body = ""
                articles.append(Article(
                    source=self.name,
                    title=item.title,
                    url=item.url,
                    published_at=item.pub_date.isoformat(),
                    full_text=body,
                    language=self.language,
                ))
        articles.sort(key=lambda a: a.published_at, reverse=True)
        return articles

    def _fetch_body(self, url: str) -> str:
        resp = requests.get(url, headers=_HEADERS, timeout=20)
        resp.raise_for_status()
        resp.encoding = resp.encoding or "utf-8"
        return extract_body(resp.text)
