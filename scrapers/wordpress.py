"""Generic scraper for WordPress sites via the public REST API.

    GET {base}/wp-json/wp/v2/posts?per_page=50&page=N&_fields=...

The REST API is the same data the site's own front end and RSS feed are built
from, but unlike the RSS feed (usually capped at 10–12 items, sometimes sorted
by modification date) it pages through every post in exact ``date_gmt`` order
and returns the full rendered body. That makes it far sturdier than parsing the
listing HTML, which is what broke the old Autodata (listing URL now 404) and
Canal Solar (listing markup changed) scrapers.

Used for (see registry.py): Autodata, Canal Solar, Diário do Transporte,
Aerospace Global News.

Canal Solar's English edition (/en/) is a machine translation of the same posts
(GTranslate) and has no REST route of its own — the Portuguese source covers it.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from html import unescape
from typing import Any, Optional

import requests

from fetcher.rss_fetcher import Article

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

_FIELDS = "date_gmt,link,title,excerpt,content"
_PER_PAGE = 50

_BLOCK_TAG_RE = re.compile(r"</?(?:p|div|br|h[1-6]|li|ul|ol|blockquote|figure|tr)[^>]*>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)
_WS_RE = re.compile(r"[ \t\r\f\v]+")
_NL_RE = re.compile(r"\n\s*\n+")


def html_to_text(html: str) -> str:
    """Rendered WP HTML → plain text with paragraph breaks."""
    if not html:
        return ""
    text = _SCRIPT_RE.sub(" ", html)
    text = _BLOCK_TAG_RE.sub("\n", text)
    text = _TAG_RE.sub("", text)
    text = unescape(text).replace("\xa0", " ")
    text = _WS_RE.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    return _NL_RE.sub("\n\n", text).strip()


def _parse_date_gmt(value: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(value.strip()).replace(tzinfo=timezone.utc)
    except (ValueError, AttributeError):
        return None


def parse_posts(posts: list[dict[str, Any]]) -> list[tuple[str, str, datetime, str]]:
    """REST API payload → [(url, title, published_utc, full_text)]."""
    out: list[tuple[str, str, datetime, str]] = []
    for post in posts:
        if not isinstance(post, dict):
            continue
        url = (post.get("link") or "").strip()
        title = html_to_text((post.get("title") or {}).get("rendered", ""))
        pub = _parse_date_gmt(post.get("date_gmt") or "")
        if not url or not title or pub is None:
            continue
        body = html_to_text((post.get("content") or {}).get("rendered", ""))
        if not body:
            body = html_to_text((post.get("excerpt") or {}).get("rendered", ""))
        out.append((url, title, pub, body))
    return out


class WordPressScraper:
    """Scraper for one WordPress site (or one category of it)."""

    def __init__(
        self,
        name: str,
        base_url: str,
        language: str = "pt-br",
        api_base: str = "",
        categories: Optional[list[int]] = None,
        max_pages: Optional[int] = None,
    ) -> None:
        self.name = name
        self.base_url = base_url
        self.language = language
        self.api_url = f"{(api_base or base_url).rstrip('/')}/wp-json/wp/v2/posts"
        self.categories = categories or []
        from config.settings import SCRAPER_MAX_PAGES
        self.max_pages = max_pages if max_pages is not None else SCRAPER_MAX_PAGES

    def fetch_articles(self, since: datetime) -> list[Article]:
        articles: list[Article] = []
        seen: set[str] = set()

        for page in range(1, self.max_pages + 1):
            params: dict[str, Any] = {
                "per_page": _PER_PAGE,
                "page": page,
                "orderby": "date",
                "order": "desc",
                "_fields": _FIELDS,
            }
            if self.categories:
                params["categories"] = ",".join(str(c) for c in self.categories)
            try:
                resp = requests.get(self.api_url, params=params, headers=_HEADERS, timeout=30)
                if resp.status_code == 400 and page > 1:
                    break  # rest_post_invalid_page_number — past the last page
                resp.raise_for_status()
                posts = resp.json()
            except Exception as e:
                logger.warning(f"{self.name}: erro na página {page} da API WordPress: {e}")
                break

            parsed = parse_posts(posts if isinstance(posts, list) else [])
            if not parsed:
                break

            reached_old = False
            for url, title, pub, body in parsed:
                if pub < since:
                    reached_old = True
                    continue
                if url in seen:
                    continue
                seen.add(url)
                articles.append(Article(
                    source=self.name,
                    title=title,
                    url=url,
                    published_at=pub.isoformat(),
                    full_text=body,
                    language=self.language,
                ))
            if reached_old or len(posts) < _PER_PAGE:
                break

        logger.info(f"{self.name}: {len(articles)} artigos via WordPress REST API")
        return articles
