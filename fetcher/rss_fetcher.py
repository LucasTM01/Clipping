"""
Camada 1 — Coleta de artigos via RSS feeds.

Usa feedparser para ler os feeds e trafilatura para extrair o texto
completo dos artigos quando necessário.
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field, asdict

import feedparser
import trafilatura
import requests

from config.settings import RSS_FEEDS, MAX_ARTICLE_AGE_HOURS, ENRICH_MAX_WORKERS

logger = logging.getLogger(__name__)


@dataclass
class Article:
    source: str
    title: str
    url: str
    published_at: str
    full_text: str = ""
    language: str = "pt-br"

    def to_dict(self) -> dict:
        return asdict(self)


def _parse_published_date(entry: dict) -> datetime | None:
    """Tenta extrair a data de publicação de um entry do feedparser.

    ``calendar.timegm`` e não ``time.mktime``: o feedparser já devolve
    ``published_parsed`` normalizado em **UTC**, e ``mktime`` interpreta a struct
    como horário **local**, somando o offset da máquina. Numa máquina em UTC−3
    isso gravava toda publicação 3h adiantada — 18% dos artigos ficavam com
    ``published_at`` posterior ao momento em que foram coletados, e o feed do
    dashboard mostrava "publicado daqui a 2 horas". ``timegm`` é a inversa
    correta de ``gmtime``, que é o que o feedparser usa.
    """
    from calendar import timegm

    for date_field in ("published_parsed", "updated_parsed"):
        parsed = entry.get(date_field)
        if parsed:
            try:
                return datetime.fromtimestamp(timegm(parsed), tz=timezone.utc)
            except (OverflowError, ValueError, OSError):
                continue
    return None


def _is_recent(published: datetime | None, max_hours: int) -> bool:
    """Verifica se o artigo está dentro da janela temporal."""
    if published is None:
        return True  # na dúvida, inclui
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_hours)
    return published >= cutoff


def _extract_full_text(url: str) -> str:
    """Usa trafilatura para extrair o texto completo de uma URL."""
    try:
        downloaded = trafilatura.fetch_url(url)
        if downloaded:
            text = trafilatura.extract(
                downloaded,
                include_comments=False,
                include_tables=False,
                no_fallback=False,
            )
            return text or ""
    except Exception as e:
        logger.warning(f"Falha ao extrair texto de {url}: {e}")
    return ""


_FEED_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0.0.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
}


def fetch_feed(url: str, timeout: int = 30) -> feedparser.FeedParserDict:
    """Baixa e parseia um feed com timeout.

    ``feedparser.parse(url)`` não tem timeout — um servidor que trava a conexão
    trava o pipeline inteiro. Também remove espaço/BOM antes do ``<?xml``, que
    alguns WordPress emitem e que faz o feedparser descartar o feed inteiro.
    """
    resp = requests.get(url, headers=_FEED_HEADERS, timeout=timeout)
    resp.raise_for_status()
    return feedparser.parse(resp.content.lstrip(b"\xef\xbb\xbf \t\r\n"))


def fetch_all_feeds() -> list[Article]:
    """
    Coleta artigos de todos os feeds configurados.
    Retorna apenas artigos das últimas MAX_ARTICLE_AGE_HOURS horas.
    """
    articles: list[Article] = []

    for feed_config in RSS_FEEDS:
        feed_name = feed_config["name"]
        feed_url = feed_config["url"]
        feed_lang = feed_config.get("language", "pt-br")

        logger.info(f"Coletando feed: {feed_name} ({feed_url})")

        try:
            feed = fetch_feed(feed_url)
        except Exception as e:
            logger.error(f"Erro ao buscar feed {feed_name}: {e}")
            continue

        if feed.bozo and not feed.entries:
            logger.warning(f"Feed {feed_name} retornou erro: {feed.bozo_exception}")
            continue

        lookback = feed_config.get("lookback_hours", MAX_ARTICLE_AGE_HOURS)
        count = 0
        for entry in feed.entries:
            title = entry.get("title", "").strip()
            url = entry.get("link", "").strip()

            if not title or not url:
                continue

            published = _parse_published_date(entry)
            if not _is_recent(published, lookback):
                continue

            published_str = (
                published.isoformat() if published
                else datetime.now(timezone.utc).isoformat()
            )

            # Tenta usar o summary do feed antes de fazer fetch completo
            summary = entry.get("summary", "") or entry.get("description", "")

            article = Article(
                source=feed_name,
                title=title,
                url=url,
                published_at=published_str,
                full_text=summary,
                language=feed_lang,
            )
            articles.append(article)
            count += 1

        logger.info(f"  {feed_name}: {count} artigos recentes coletados")

    logger.info(f"Total coletado: {len(articles)} artigos")
    return articles


# Domínios que bloqueiam trafilatura (paywall / Cloudflare / 401).
# Artigos dessas fontes são pulados pelo enriquecimento via requests/trafilatura,
# mas podem ser enriquecidos via enrich_with_playwright() usando o Chrome real.
_NO_ENRICH_DOMAINS: frozenset[str] = frozenset({
    "www.reuters.com",
    "reuters.com",
    "www.bloomberg.com",
    "bloomberg.com",
})


# Domínios que sempre recusam o fetch (403 / paywall) e não têm rota via
# Playwright — nenhum enriquecimento é tentado; fica o summary do feed.
#   ft.com           → 403 em 100% das tentativas (paywall)
#   weg.net          → 403 para tudo que não é browser interativo
#   news.google.com  → links do Google News que não puderam ser decodificados
_NEVER_ENRICH_DOMAINS: frozenset[str] = frozenset({
    "www.ft.com",
    "ft.com",
    "www.weg.net",
    "weg.net",
    "news.google.com",
})


def _host(url: str) -> str:
    try:
        from urllib.parse import urlparse
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


def _skip_enrich(url: str) -> bool:
    """Retorna True se a URL pertence a um domínio que bloqueia enriquecimento."""
    return _host(url) in _NO_ENRICH_DOMAINS


def enrich_full_text(articles: list[Article]) -> list[Article]:
    """
    Para artigos que só têm summary/description curto, busca o texto
    completo via trafilatura em paralelo (ENRICH_MAX_WORKERS threads).
    Só faz fetch se o texto atual for < 300 chars e o domínio não estiver
    na lista de bloqueio (_NO_ENRICH_DOMAINS / _NEVER_ENRICH_DOMAINS).
    """
    to_enrich = [
        a for a in articles
        if len(a.full_text) < 300
        and not _skip_enrich(a.url)
        and _host(a.url) not in _NEVER_ENRICH_DOMAINS
    ]

    if not to_enrich:
        logger.info("Nenhum artigo precisou de enriquecimento.")
        return articles

    logger.info(
        f"Enriquecendo {len(to_enrich)} artigos em paralelo "
        f"({ENRICH_MAX_WORKERS} workers)..."
    )

    enriched = 0

    def _fetch_and_update(article: Article) -> bool:
        full = _extract_full_text(article.url)
        if full and len(full) > len(article.full_text):
            article.full_text = full
            return True
        return False

    with ThreadPoolExecutor(max_workers=ENRICH_MAX_WORKERS) as pool:
        futures = {pool.submit(_fetch_and_update, a): a for a in to_enrich}
        for future in as_completed(futures):
            try:
                if future.result():
                    enriched += 1
            except Exception as e:
                article = futures[future]
                logger.warning(f"Falha ao enriquecer {article.url[:60]}: {e}")

    logger.info(f"Artigos enriquecidos com texto completo: {enriched}/{len(to_enrich)}")
    return articles


_BLOCKED_STATUSES = frozenset({401, 403, 429})
_MAX_BLOCKED_IN_A_ROW = 3


def enrich_with_playwright(articles: list[Article]) -> list[Article]:
    """
    Enriquece artigos de domínios bloqueados (Reuters, Bloomberg) via Playwright
    usando o perfil real do Chrome — que traz cookies de sessão do usuário,
    tornando o acesso indistinguível de uma visita humana.

    Roda sequencialmente (uma página por vez dentro do mesmo contexto de browser)
    para não disparar detecção de bot por múltiplos requests paralelos.

    Se um domínio responder 401/403/429 em _MAX_BLOCKED_IN_A_ROW páginas
    seguidas, os demais artigos dele são pulados — sem sessão válida no perfil
    do pipeline, insistir só gasta minutos do run sem trazer texto.
    """
    from scrapers.playwright_utils import persistent_browser_context

    to_enrich = [
        a for a in articles
        if _skip_enrich(a.url) and len(a.full_text) < 300
    ]
    if not to_enrich:
        return articles

    logger.info(
        f"Playwright enrich: {len(to_enrich)} artigos de domínios bloqueados "
        f"(Reuters/Bloomberg)..."
    )

    enriched = 0
    blocked_in_a_row: dict[str, int] = {}
    try:
        with persistent_browser_context(headless=True) as ctx:
            page = ctx.new_page()
            for article in to_enrich:
                host = _host(article.url)
                if blocked_in_a_row.get(host, 0) >= _MAX_BLOCKED_IN_A_ROW:
                    continue
                try:
                    resp = page.goto(article.url, wait_until="domcontentloaded", timeout=20_000)
                    if resp is not None and resp.status in _BLOCKED_STATUSES:
                        blocked_in_a_row[host] = blocked_in_a_row.get(host, 0) + 1
                        if blocked_in_a_row[host] == _MAX_BLOCKED_IN_A_ROW:
                            logger.warning(
                                f"Playwright enrich: {host} bloqueou {_MAX_BLOCKED_IN_A_ROW} "
                                f"páginas seguidas (HTTP {resp.status}) — pulando o restante. "
                                "Rode `python -m tools.setup_pipeline_browser` para renovar a sessão."
                            )
                        continue
                    blocked_in_a_row[host] = 0
                    # Pequena pausa para simular leitura humana e deixar JS carregar
                    page.wait_for_timeout(1_500)
                    html = page.content()
                    text = trafilatura.extract(
                        html,
                        include_comments=False,
                        include_tables=False,
                        no_fallback=False,
                    ) or ""
                    if len(text) > len(article.full_text):
                        article.full_text = text
                        enriched += 1
                        logger.debug(f"Playwright enrich OK: {article.url[:70]}")
                    else:
                        logger.debug(f"Playwright enrich: sem texto extraível em {article.url[:70]}")
                except Exception as e:
                    logger.debug(f"Playwright enrich falhou: {article.url[:70]}: {e}")
            page.close()
    except Exception as e:
        logger.warning(f"Playwright enrich: browser não iniciou: {e}")

    logger.info(f"Playwright enrich: {enriched}/{len(to_enrich)} artigos enriquecidos")
    return articles
