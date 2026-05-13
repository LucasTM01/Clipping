"""InfoMoney scraper via WordPress REST API.

InfoMoney runs WordPress and exposes a public REST API:
    https://www.infomoney.com.br/wp-json/wp/v2/posts

Pagination: ?page=N (1-based) with per_page=100, orderby=date desc.
Stops when the oldest post on a page is older than `since`, or when
`SCRAPER_MAX_PAGES` pages have been fetched.

No Playwright required — JSON responses include title, URL, date_gmt
and the full article HTML (content.rendered). HTML is stripped in-process
so the enrich_full_text step won't re-fetch these articles.
"""

from __future__ import annotations

import html as _html
import logging
import re
from datetime import datetime, timezone
from typing import Optional

import requests

from fetcher.rss_fetcher import Article

logger = logging.getLogger(__name__)

_API_URL = "https://www.infomoney.com.br/wp-json/wp/v2/posts"
_PER_PAGE = 100
_FIELDS = "id,link,title,date_gmt,content"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(raw: str) -> str:
    """Remove HTML tags and decode entities."""
    text = _TAG_RE.sub(" ", raw)
    text = _html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _parse_date_gmt(s: str) -> Optional[datetime]:
    """Parse WP date_gmt (UTC, no suffix) → timezone-aware UTC datetime."""
    try:
        return datetime.fromisoformat(s.strip()).replace(tzinfo=timezone.utc)
    except (ValueError, AttributeError):
        return None


class InfoMoneyScraper:
    """Scraper for https://www.infomoney.com.br/ultimas-noticias/ via WP API."""

    name: str = "InfoMoney"
    base_url: str = "https://www.infomoney.com.br/ultimas-noticias/"
    language: str = "pt-br"

    def __init__(self, max_pages: Optional[int] = None) -> None:
        from config.settings import SCRAPER_MAX_PAGES
        self.max_pages = max_pages if max_pages is not None else SCRAPER_MAX_PAGES

    def fetch_articles(self, since: datetime) -> list[Article]:
        articles: list[Article] = []
        seen: set[str] = set()

        for page_num in range(1, self.max_pages + 1):
            try:
                resp = requests.get(
                    _API_URL,
                    params={
                        "per_page": _PER_PAGE,
                        "page": page_num,
                        "orderby": "date",
                        "order": "desc",
                        "_fields": _FIELDS,
                    },
                    headers=_HEADERS,
                    timeout=20,
                )
                resp.raise_for_status()
            except requests.HTTPError as e:
                if e.response is not None and e.response.status_code == 400:
                    break  # page beyond total pages
                logger.warning(f"InfoMoney: erro na página {page_num}: {e}")
                break
            except Exception as e:
                logger.warning(f"InfoMoney: erro na página {page_num}: {e}")
                break

            posts = resp.json()
            if not posts:
                break

            stop = False
            for post in posts:
                url = post.get("link", "").strip()
                if not url or url in seen:
                    continue

                pub_dt = _parse_date_gmt(post.get("date_gmt", ""))
                if pub_dt is None:
                    continue
                if pub_dt < since:
                    stop = True
                    break

                seen.add(url)
                title = _strip_html(post.get("title", {}).get("rendered", ""))
                full_text = _strip_html(post.get("content", {}).get("rendered", ""))

                articles.append(
                    Article(
                        source=self.name,
                        title=title,
                        url=url,
                        published_at=pub_dt.isoformat(),
                        full_text=full_text,
                        language=self.language,
                    )
                )

            logger.debug(
                f"InfoMoney: página {page_num} — {len(posts)} posts, "
                f"{len(articles)} acumulados"
            )

            if stop:
                break

        logger.info(f"InfoMoney: {len(articles)} artigos coletados")
        return articles
