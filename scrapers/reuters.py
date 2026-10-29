"""Reuters section scrapers via Google News RSS.

Reuters is protected by DataDome: the section pages, the article pages AND the
public news sitemap (``/arc/outboundfeeds/news-sitemap/``) answer HTTP 401 to
anything that isn't an interactive browser. The sitemap used to slip through
intermittently, which is why these sources delivered 0–7 articles/day while the
sections publish 10–40.

The listing now comes from Google News, restricted to the section path
(``site:reuters.com/business/energy``) — see ``scrapers.google_news``. Links are
decoded to the real reuters.com URL. Article bodies are left empty
(``full_text=""``) and the screener scores on the headline.

Sections covered (one ReutersScraper instance per section, see registry.py):
    /business/aerospace-defense/    → "Reuters Aerospace & Defense"
    /business/autos-transportation/ → "Reuters Autos & Transportation"
    /business/energy/               → "Reuters Energy"
"""

from __future__ import annotations

import re

from scrapers.google_news import LOCALE_EN_US, GoogleNewsScraper

_REUTERS_BASE = "https://www.reuters.com"
_SECTION = "/business/aerospace-defense/"


class ReutersScraper(GoogleNewsScraper):
    """Scraper for a single Reuters section via Google News RSS."""

    def __init__(
        self,
        name: str = "Reuters Aerospace & Defense",
        section: str = _SECTION,
    ) -> None:
        super().__init__(
            name=name,
            site=f"reuters.com{section.rstrip('/')}",
            base_url=f"{_REUTERS_BASE}{section}",
            language="en",
            locale=LOCALE_EN_US,
            url_pattern=re.escape(f"{_REUTERS_BASE}{section}"),
        )
        self.section = section
