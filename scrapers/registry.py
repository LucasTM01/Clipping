"""Scraper registry and orchestration."""

from __future__ import annotations

import logging
from datetime import datetime

from fetcher.rss_fetcher import Article
from scrapers.aeroin import AeroinScraper
from scrapers.airway import AirwayScraper
from scrapers.automotivebusiness import AutomotiveBusinessScraper
from scrapers.bloomberg import BloombergScraper
from scrapers.google_news import LOCALE_PT_BR, GoogleNewsScraper
from scrapers.infomoney import InfoMoneyScraper
from scrapers.oglobo import OGloboScraper
from scrapers.portalsolar import PortalSolarScraper
from scrapers.randoncorp import RandoncorpScraper
from scrapers.reuters import ReutersScraper
from scrapers.wordpress import WordPressScraper
from scrapers.cvm import CVMScraper
from scrapers.base import BaseScraper

logger = logging.getLogger(__name__)


SCRAPERS: list[BaseScraper] = [
    AeroinScraper(),
    AirwayScraper(),
    WordPressScraper("Autodata", "https://www.autodata.com.br"),
    AutomotiveBusinessScraper(),
    WordPressScraper("Canal Solar", "https://canalsolar.com.br"),
    WordPressScraper("Diário do Transporte", "https://diariodotransporte.com.br"),
    WordPressScraper("Aerospace Global News", "https://aerospaceglobalnews.com", language="en"),
    RandoncorpScraper(),
    # weg.net answers 403 to anything but an interactive browser — listing via Google News.
    GoogleNewsScraper(
        "WEG News",
        site="weg.net/institutional",
        base_url="https://www.weg.net/institutional/BR/pt/all-news/list",
        language="pt-br",
        locale=LOCALE_PT_BR,
        url_pattern=r"https://www\.weg\.net/institutional/[^/]+/[^/]+/news/",
    ),
    InfoMoneyScraper(),
    OGloboScraper(),
    PortalSolarScraper(),
    ReutersScraper(),
    ReutersScraper("Reuters Autos & Transportation", "/business/autos-transportation/"),
    ReutersScraper("Reuters Energy", "/business/energy/"),
    BloombergScraper("Bloomberg Transportation", "transportation"),
    BloombergScraper("Bloomberg Energy", "energy"),
    BloombergScraper("Bloomberg Industries", "industries"),
    CVMScraper(),
]


def fetch_all_scrapers(since: datetime) -> list[Article]:
    """Run every enabled scraper and merge results into a single Article list."""
    try:
        from config.settings import SCRAPERS_ENABLED
    except ImportError:
        SCRAPERS_ENABLED = [s.name.lower() for s in SCRAPERS]

    enabled = {n.lower() for n in SCRAPERS_ENABLED}
    articles: list[Article] = []

    for scraper in SCRAPERS:
        if scraper.name.lower() not in enabled:
            logger.info(f"Scraper {scraper.name} desabilitado, pulando.")
            continue
        logger.info(f"Coletando scraper: {scraper.name} ({scraper.base_url})")
        try:
            got = scraper.fetch_articles(since)
        except Exception as e:
            logger.error(f"Scraper {scraper.name} falhou: {e}", exc_info=True)
            continue
        logger.info(f"  {scraper.name}: {len(got)} artigos coletados")
        articles.extend(got)

    logger.info(f"Total scrapers: {len(articles)} artigos")
    return articles
