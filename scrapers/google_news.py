"""Google News RSS scraper — listing for sites that block direct access.

Reuters (DataDome) answers 401 to both its section pages and its news sitemap,
and weg.net answers 403 to anything that isn't an interactive browser. Google
News indexes both, and its public RSS search endpoint accepts a ``site:`` filter
down to a URL path, so a single query lists one Reuters section:

    https://news.google.com/rss/search?q=site:reuters.com/business/energy+when:1d

Each item has the headline (suffixed with " - <Source>"), the publication time
and a ``news.google.com/rss/articles/<id>`` redirect link. The redirect is
decoded to the publisher URL in two steps (both public Google News endpoints):

    1. GET  news.google.com/rss/articles/<id>   → data-n-a-sg / data-n-a-ts
    2. POST news.google.com/_/DotsSplashUi/data/batchexecute (one batch for all)

Decoding is best-effort: on any failure the Google News link is kept — it
still redirects to the article in a browser, it just can't be enriched.

Article bodies are left empty; the screener scores on the headline. The pipeline
skips trafilatura for these domains (see ``fetcher.rss_fetcher``).
"""

from __future__ import annotations

import json
import logging
import math
import re
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import quote, quote_plus, urlparse

import feedparser
import requests

from fetcher.rss_fetcher import Article, _parse_published_date

logger = logging.getLogger(__name__)

_RSS_SEARCH_URL = "https://news.google.com/rss/search?q={query}&hl={hl}&gl={gl}&ceid={ceid}"
_ARTICLE_URL = "https://news.google.com/rss/articles/{gid}"
_BATCH_URL = "https://news.google.com/_/DotsSplashUi/data/batchexecute"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0.0.0 Safari/537.36"
    )
}

_SIG_RE = re.compile(r'data-n-a-sg="([^"]+)"')
_TS_RE = re.compile(r'data-n-a-ts="([^"]+)"')

# Locale presets for the RSS endpoint (hl, gl, ceid)
LOCALE_EN_US = ("en-US", "US", "US:en")
LOCALE_PT_BR = ("pt-BR", "BR", "BR:pt-419")


def build_query(site: str, since: datetime, now: Optional[datetime] = None) -> str:
    """``site:<site> when:<N>d`` covering the window from ``since`` to now."""
    now = now or datetime.now(timezone.utc)
    days = max(1, math.ceil((now - since).total_seconds() / 86_400))
    return f"site:{site} when:{days}d"


def strip_source_suffix(title: str, source_title: str = "") -> str:
    """Remove the trailing " - Reuters" that Google News appends to headlines."""
    title = (title or "").strip()
    if source_title:
        suffix = f" - {source_title.strip()}"
        if title.endswith(suffix):
            return title[: -len(suffix)].strip()
    return title


def article_id(link: str) -> str:
    """Extract the Google News article id from a news.google.com link."""
    return urlparse(link).path.rstrip("/").split("/")[-1]


def parse_decoding_params(html: str) -> Optional[tuple[str, str]]:
    """Return (timestamp, signature) from a news.google.com/rss/articles page."""
    sg = _SIG_RE.search(html)
    ts = _TS_RE.search(html)
    if not sg or not ts:
        return None
    return ts.group(1), sg.group(1)


def build_batch_payload(params: list[tuple[str, str, str]]) -> str:
    """Form body for batchexecute. ``params`` = [(gid, timestamp, signature)].

    Each request carries its list index as the rpc id, because responses do
    not come back in request order.
    """
    reqs = [
        [
            "Fbv4je",
            '["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",null,1,null,null,'
            'null,null,null,0,1],"X","X",1,[1,1,1],1,1,null,0,0,null,0],'
            f'"{gid}",{ts},"{sig}"]',
            None,
            str(i),
        ]
        for i, (gid, ts, sig) in enumerate(params)
    ]
    return "f.req=" + quote(json.dumps([reqs]))


def parse_batch_response(text: str) -> dict[int, str]:
    """Map request index → decoded publisher URL from a batchexecute response."""
    out: dict[int, str] = {}
    for chunk in text.split("\n\n")[1:2]:
        try:
            rows = json.loads(chunk)
        except json.JSONDecodeError:
            continue
        for row in rows:
            if not (isinstance(row, list) and len(row) >= 3 and row[0] == "wrb.fr"):
                continue
            try:
                payload = json.loads(row[2])
                idx = int(row[-1])
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if isinstance(payload, list) and len(payload) > 1 and str(payload[1]).startswith("http"):
                out[idx] = payload[1]
    return out


class GoogleNewsScraper:
    """One Google News ``site:`` query = one source.

    ``site`` may include a path (``reuters.com/business/energy``) to restrict
    the listing to a section. ``url_pattern`` (regex), when given, drops decoded
    URLs that don't match it — Google's ``site:`` match is loose on paths and
    also returns catalog pages and PDFs.
    """

    def __init__(
        self,
        name: str,
        site: str,
        base_url: str,
        language: str = "en",
        locale: tuple[str, str, str] = LOCALE_EN_US,
        url_pattern: str = "",
        decode_urls: bool = True,
    ) -> None:
        self.name = name
        self.site = site
        self.base_url = base_url
        self.language = language
        self.locale = locale
        self.url_pattern = re.compile(url_pattern) if url_pattern else None
        self.decode_urls = decode_urls

    def fetch_articles(self, since: datetime) -> list[Article]:
        hl, gl, ceid = self.locale
        url = _RSS_SEARCH_URL.format(
            query=quote_plus(build_query(self.site, since)), hl=hl, gl=gl, ceid=ceid
        )
        session = requests.Session()
        session.headers.update(_HEADERS)
        try:
            resp = session.get(url, timeout=20)
            resp.raise_for_status()
        except Exception as e:
            logger.warning(f"{self.name}: erro ao buscar Google News RSS: {e}")
            return []

        feed = feedparser.parse(resp.content)
        entries = []
        for entry in feed.entries:
            pub = _parse_published_date(entry)
            if pub is None or pub < since:
                continue
            source_title = (entry.get("source") or {}).get("title", "")
            title = strip_source_suffix(entry.get("title", ""), source_title)
            link = entry.get("link", "").strip()
            if title and link:
                entries.append((title, link, pub))

        logger.info(f"{self.name}: {len(entries)} artigos no Google News (de {len(feed.entries)})")
        if not entries:
            return []

        links = [link for _, link, _ in entries]
        decoded = self._decode(session, links) if self.decode_urls else {}

        articles: list[Article] = []
        seen: set[str] = set()
        for title, link, pub in entries:
            final_url = decoded.get(link, link)
            if self.url_pattern and final_url != link and not self.url_pattern.match(final_url):
                continue
            if final_url in seen:
                continue
            seen.add(final_url)
            articles.append(Article(
                source=self.name,
                title=title,
                url=final_url,
                published_at=pub.isoformat(),
                full_text="",
                language=self.language,
            ))
        return articles

    def _decode(self, session: requests.Session, links: list[str]) -> dict[str, str]:
        """Best-effort decode of news.google.com links → publisher URLs."""
        params: list[tuple[str, str, str]] = []
        param_links: list[str] = []
        for link in links:
            gid = article_id(link)
            try:
                r = session.get(_ARTICLE_URL.format(gid=gid), timeout=15)
                r.raise_for_status()
            except Exception as e:
                logger.debug(f"{self.name}: falha ao obter parâmetros de {gid[:30]}: {e}")
                continue
            p = parse_decoding_params(r.text)
            if p is None:
                continue
            params.append((gid, p[0], p[1]))
            param_links.append(link)

        if not params:
            logger.warning(f"{self.name}: não foi possível decodificar links do Google News")
            return {}

        try:
            r = session.post(
                _BATCH_URL,
                data=build_batch_payload(params),
                headers={"content-type": "application/x-www-form-urlencoded;charset=UTF-8"},
                timeout=30,
            )
            r.raise_for_status()
        except Exception as e:
            logger.warning(f"{self.name}: batchexecute falhou ({e}) — mantendo links do Google News")
            return {}

        by_idx = parse_batch_response(r.text)
        decoded = {param_links[i]: u for i, u in by_idx.items() if i < len(param_links)}
        logger.info(f"{self.name}: {len(decoded)}/{len(links)} links decodificados")
        return decoded
