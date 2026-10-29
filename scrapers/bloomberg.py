"""Bloomberg scrapers via Bloomberg's own public feeds.

Covers (one BloombergScraper instance per section, see registry.py):
  - "Bloomberg Energy"          — energy/power/oil & gas stories
  - "Bloomberg Transportation"  — aviation, autos, trucking, rail, shipping
  - "Bloomberg Industries"      — remaining Industries-feed stories (e.g.
                                  Caterpillar, industrial earnings)

The /industries/<x> pages are behind PerimeterX ("Are you a robot?") for any
automated browser, which is why these sources delivered 0 articles every day.
Two public, crawler-facing feeds give the same stories without a browser:

  1. Google News sitemap ``/sitemaps/news/latest.xml`` — every Bloomberg story
     of the last ~2 days with exact ``publication_date`` and headline, but no
     section information.
  2. Industries RSS ``/feeds/industries/news.rss`` — the latest ~20 Industries
     stories, each with a one-paragraph description (used as ``full_text``).

Both are fetched once per run (module-level cache shared by the instances)
and every story is assigned to exactly ONE section by headline/slug keywords
(transportation first, then energy, then Industries-feed leftovers), so the
same URL never appears under two sources. Sitemap stories that match neither
keyword set (politics, markets, ...) are dropped.

Article bodies are paywalled; the pipeline does not try trafilatura on
bloomberg.com (see ``fetcher.rss_fetcher._NO_ENRICH_DOMAINS``).
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from html import unescape
from typing import Optional

import feedparser
import requests

from fetcher.rss_fetcher import Article, _parse_published_date

logger = logging.getLogger(__name__)

_BASE = "https://www.bloomberg.com"
_SITEMAP_URL = f"{_BASE}/sitemaps/news/latest.xml"
_INDUSTRIES_RSS_URL = f"{_BASE}/feeds/industries/news.rss"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0.0.0 Safari/537.36"
    )
}

# Only news stories — opinion columns, newsletters and live blogs are skipped.
_STORY_PATH_RE = re.compile(r"^https://www\.bloomberg\.com/news/(?:articles|features)/")

_URL_BLOCK_RE = re.compile(r"<url>(.*?)</url>", re.DOTALL)
_LOC_RE = re.compile(r"<loc>([^<]+)</loc>")
_PUB_DATE_RE = re.compile(r"<news:publication_date>([^<]+)</news:publication_date>")
_TITLE_RE = re.compile(
    r"<news:title>(?:<!\[CDATA\[(.*?)\]\]>|([^<]*))</news:title>", re.DOTALL
)

SECTION_ENERGY = "energy"
SECTION_TRANSPORTATION = "transportation"
SECTION_INDUSTRIES = "industries"

# Matched against headline + URL slug (lowercase, hyphens → spaces).
_TRANSPORTATION_RE = re.compile(
    r"\b(?:airlines?|aircraft|airplanes?|airbus|boeing|embraer|bombardier|jets?|"
    r"jetliners?|aviation|aerospace|airports?|flights?|fighter|lockheed|rheinmetall|"
    r"northrop|saab|"
    r"trucks?|trucking|truckers?|freight|shipping|shipbuild\w*|containers?|ports?|"
    r"rail|railroads?|railways?|locomotives?|cars?|automakers?|carmakers?|autos?|"
    r"auto ?parts|evs?|electric vehicles?|tesla|toyota|volkswagen|vw|general motors|"
    r"ford|stellantis|byd|geely|hyundai|kia|nissan|honda|renault|mercedes|bmw|volvo|"
    r"daimler|scania|traton|paccar|cummins|logistics|fedex|maersk|buses|tires?)\b",
    re.IGNORECASE,
)
_ENERGY_RE = re.compile(
    r"\b(?:energy|oil|crude|brent|wti|opec\+?|gas|lng|gasoline|diesel|fuel|refiner(?:y|ies|s)?|"
    r"pipelines?|power|electricity|grid|utilit(?:y|ies)|solar|"
    r"offshore wind|onshore wind|wind (?:power|farms?|turbines?|energy|projects?)|renewables?|"
    r"nuclear|uranium|coal|hydro\w*|batter(?:y|ies)|hydrogen|transformers?|turbines?|"
    r"biofuels?|ethanol|emissions|carbon|petrobras|exxon|chevron|shell|bp|"
    r"totalenergies|aramco|equinor|sinopec|petrochina|cnooc|conocophillips|"
    r"data centers? power)\b",
    re.IGNORECASE,
)

_CACHE_TTL_S = 600
_cache: dict[str, tuple[float, list["_Story"]]] = {}


@dataclass
class _Story:
    url: str
    title: str
    pub_date: datetime
    summary: str = ""
    in_industries_feed: bool = False


def _to_utc(iso: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(iso.strip().replace("Z", "+00:00")).astimezone(timezone.utc)
    except (ValueError, AttributeError):
        return None


def parse_sitemap(xml_text: str) -> list[_Story]:
    """Extract news stories (url, title, publication date) from the news sitemap."""
    stories: list[_Story] = []
    for block_m in _URL_BLOCK_RE.finditer(xml_text):
        block = block_m.group(1)
        loc_m = _LOC_RE.search(block)
        if not loc_m:
            continue
        url = loc_m.group(1).strip()
        if not _STORY_PATH_RE.match(url):
            continue
        date_m = _PUB_DATE_RE.search(block)
        pub = _to_utc(date_m.group(1)) if date_m else None
        title_m = _TITLE_RE.search(block)
        title = unescape((title_m.group(1) or title_m.group(2) or "").strip()) if title_m else ""
        if pub is None or not title:
            continue
        stories.append(_Story(url=url, title=title, pub_date=pub))
    return stories


def parse_industries_rss(content: bytes | str) -> list[_Story]:
    """Extract stories (with description) from the Industries RSS feed."""
    feed = feedparser.parse(content)
    stories: list[_Story] = []
    for entry in feed.entries:
        url = entry.get("link", "").strip()
        title = entry.get("title", "").strip()
        pub = _parse_published_date(entry)
        if not url or not title or pub is None:
            continue
        stories.append(_Story(
            url=url,
            title=title,
            pub_date=pub,
            summary=(entry.get("summary") or "").strip(),
            in_industries_feed=True,
        ))
    return stories


def merge_stories(sitemap: list[_Story], industries: list[_Story]) -> list[_Story]:
    """Union by URL. Industries-feed entries win (they carry the description)."""
    by_url: dict[str, _Story] = {s.url: s for s in sitemap}
    for s in industries:
        by_url[s.url] = s
    return sorted(by_url.values(), key=lambda s: s.pub_date, reverse=True)


def classify(story: _Story) -> Optional[str]:
    """Assign a story to exactly one section, or None to drop it."""
    slug = story.url.rstrip("/").rsplit("/", 1)[-1].replace("-", " ")
    text = f"{story.title} {slug}"
    if _TRANSPORTATION_RE.search(text):
        return SECTION_TRANSPORTATION
    if _ENERGY_RE.search(text):
        return SECTION_ENERGY
    if story.in_industries_feed:
        return SECTION_INDUSTRIES
    return None


def _fetch_text(url: str) -> Optional[str]:
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=20)
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        logger.warning(f"Bloomberg: erro ao buscar {url}: {e}")
        return None


def _load_stories() -> list[_Story]:
    """Fetch + merge both feeds once per run (cached for _CACHE_TTL_S)."""
    cached = _cache.get("stories")
    if cached and time.monotonic() - cached[0] < _CACHE_TTL_S:
        return cached[1]

    sitemap_xml = _fetch_text(_SITEMAP_URL)
    rss_text = _fetch_text(_INDUSTRIES_RSS_URL)
    sitemap = parse_sitemap(sitemap_xml) if sitemap_xml else []
    industries = parse_industries_rss(rss_text) if rss_text else []
    logger.info(
        f"Bloomberg: {len(sitemap)} matérias no sitemap, {len(industries)} no feed Industries"
    )
    stories = merge_stories(sitemap, industries)
    if sitemap_xml is not None or rss_text is not None:
        _cache["stories"] = (time.monotonic(), stories)
    return stories


class BloombergScraper:
    """Scraper for one Bloomberg section (see module docstring).

        BloombergScraper("Bloomberg Energy",         "energy")
        BloombergScraper("Bloomberg Transportation", "transportation")
        BloombergScraper("Bloomberg Industries",     "industries")
    """

    language: str = "en"

    def __init__(self, name: str, section: str) -> None:
        self.name = name
        self.section = section
        self.base_url = f"{_BASE}/industries/{section}" if section != SECTION_INDUSTRIES else f"{_BASE}/industries"

    def fetch_articles(self, since: datetime) -> list[Article]:
        stories = [
            s for s in _load_stories()
            if s.pub_date >= since and classify(s) == self.section
        ]
        logger.info(f"{self.name}: {len(stories)} artigos")
        return [
            Article(
                source=self.name,
                title=s.title,
                url=s.url,
                published_at=s.pub_date.isoformat(),
                full_text=s.summary,
                language=self.language,
            )
            for s in stories
        ]
