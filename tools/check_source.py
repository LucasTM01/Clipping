"""
Live health check for individual news sources (RSS feeds + Playwright scrapers).

Lets you test each source in isolation to detect breakages — site changed layout,
RSS feed went down, scraper selectors stale, etc. No LLM required.

Usage:
    python -m tools.check_source --list                    # list all sources
    python -m tools.check_source aeroin                    # test one source
    python -m tools.check_source "Pipeline Valor"          # name with spaces
    python -m tools.check_source --all                     # test everything
    python -m tools.check_source --rss                     # all RSS feeds
    python -m tools.check_source --scrapers                # all Playwright scrapers
    python -m tools.check_source aeroin --lookback 12      # last 12h instead of default
    python -m tools.check_source aeroin -v                 # show URLs + first paragraphs
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
import traceback

# Force UTF-8 stdout on Windows (cp1252 default chokes on emoji)
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import feedparser

from config.settings import MAX_ARTICLE_AGE_HOURS, RSS_FEEDS
from fetcher.rss_fetcher import Article, _is_recent, _parse_published_date


# ---------------------------------------------------------------------------
# Probe abstractions
# ---------------------------------------------------------------------------

@dataclass
class ProbeResult:
    name: str
    kind: str
    ok: bool
    n_articles: int
    elapsed_s: float
    error: str = ""
    sample_titles: list[str] = field(default_factory=list)
    sample_urls: list[str] = field(default_factory=list)
    oldest: datetime | None = None
    newest: datetime | None = None
    text_lengths: list[int] = field(default_factory=list)
    extra: dict = field(default_factory=dict)


def _probe_rss(name: str, url: str, language: str, lookback_hours: int) -> ProbeResult:
    """Probe a single RSS feed: parse, filter by lookback, sample stats."""
    start = time.perf_counter()
    res = ProbeResult(name=name, kind="rss", ok=False, n_articles=0, elapsed_s=0)
    res.extra["url"] = url

    try:
        feed = feedparser.parse(url)
    except Exception as exc:
        res.error = f"feedparser raised: {exc!r}"
        res.elapsed_s = time.perf_counter() - start
        return res

    if feed.bozo and not feed.entries:
        res.error = f"bozo + no entries: {getattr(feed, 'bozo_exception', 'unknown')}"
        res.elapsed_s = time.perf_counter() - start
        return res

    articles: list[Article] = []
    skipped_no_meta = 0
    skipped_old = 0
    for entry in feed.entries:
        title = (entry.get("title") or "").strip()
        link = (entry.get("link") or "").strip()
        if not title or not link:
            skipped_no_meta += 1
            continue
        published = _parse_published_date(entry)
        if not _is_recent(published, lookback_hours):
            skipped_old += 1
            continue
        published_str = (
            published.isoformat() if published
            else datetime.now(timezone.utc).isoformat()
        )
        summary = entry.get("summary", "") or entry.get("description", "")
        articles.append(Article(
            source=name, title=title, url=link,
            published_at=published_str, full_text=summary, language=language,
        ))

    res.elapsed_s = time.perf_counter() - start
    res.n_articles = len(articles)
    res.ok = len(articles) > 0 or len(feed.entries) > 0
    res.extra["entries_total"] = len(feed.entries)
    res.extra["skipped_no_meta"] = skipped_no_meta
    res.extra["skipped_old"] = skipped_old

    if not res.ok:
        res.error = "feed retornou 0 entries"

    res.sample_titles = [a.title for a in articles[:5]]
    res.sample_urls = [a.url for a in articles[:5]]
    res.text_lengths = [len(a.full_text) for a in articles[:5]]

    dates = []
    for a in articles:
        try:
            dates.append(datetime.fromisoformat(a.published_at))
        except (ValueError, TypeError):
            pass
    if dates:
        res.oldest = min(dates)
        res.newest = max(dates)

    return res


def _probe_scraper(scraper, lookback_hours: int) -> ProbeResult:
    """Probe a Playwright scraper by calling fetch_articles(since)."""
    start = time.perf_counter()
    res = ProbeResult(name=scraper.name, kind="scraper", ok=False, n_articles=0, elapsed_s=0)
    res.extra["url"] = getattr(scraper, "base_url", "")

    since = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)

    try:
        articles = scraper.fetch_articles(since)
    except Exception as exc:
        res.error = f"{type(exc).__name__}: {exc}"
        res.extra["traceback"] = traceback.format_exc()
        res.elapsed_s = time.perf_counter() - start
        return res

    res.elapsed_s = time.perf_counter() - start
    res.n_articles = len(articles)
    res.ok = True

    res.sample_titles = [a.title for a in articles[:5]]
    res.sample_urls = [a.url for a in articles[:5]]
    res.text_lengths = [len(a.full_text) for a in articles[:5]]

    dates = []
    for a in articles:
        try:
            dates.append(datetime.fromisoformat(a.published_at))
        except (ValueError, TypeError):
            pass
    if dates:
        res.oldest = min(dates)
        res.newest = max(dates)

    if res.n_articles == 0:
        res.extra["warning"] = (
            "0 artigos. Pode ser legítimo (nada novo no período) ou "
            "indicar quebra de selector — confira o log do scraper."
        )

    return res


# ---------------------------------------------------------------------------
# Source registry (RSS + Scrapers unified)
# ---------------------------------------------------------------------------

@dataclass
class Source:
    name: str
    kind: str  # "rss" | "scraper"
    runner: callable  # () -> ProbeResult


def _build_sources(lookback_hours: int) -> list[Source]:
    sources: list[Source] = []

    for f in RSS_FEEDS:
        name = f["name"]
        url = f["url"]
        lang = f.get("language", "pt-br")
        sources.append(Source(
            name=name, kind="rss",
            runner=lambda n=name, u=url, l=lang: _probe_rss(n, u, l, lookback_hours),
        ))

    # Import scrapers lazily — avoids loading Playwright if not needed
    try:
        from scrapers.registry import SCRAPERS
    except Exception as exc:
        print(f"⚠️  Scrapers indisponíveis: {exc}", file=sys.stderr)
        SCRAPERS = []

    for s in SCRAPERS:
        sources.append(Source(
            name=s.name, kind="scraper",
            runner=lambda sc=s: _probe_scraper(sc, lookback_hours),
        ))

    return sources


def _normalize(s: str) -> str:
    """Lowercase + strip diacritics for forgiving name matching."""
    import unicodedata
    s = unicodedata.normalize("NFD", s)
    return "".join(c for c in s if not unicodedata.combining(c)).lower().strip()


def _find_source(query: str, sources: list[Source]) -> Source | None:
    q = _normalize(query)
    exact = [s for s in sources if _normalize(s.name) == q]
    if exact:
        return exact[0]
    partial = [s for s in sources if q in _normalize(s.name)]
    if len(partial) == 1:
        return partial[0]
    if len(partial) > 1:
        print(f"❌ Ambíguo — '{query}' casa com:", file=sys.stderr)
        for s in partial:
            print(f"   - {s.name} ({s.kind})", file=sys.stderr)
        return None
    return None


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

def _status_emoji(r: ProbeResult) -> str:
    if not r.ok:
        return "❌"
    if r.n_articles == 0:
        return "⚠️"
    return "✅"


def _print_detailed(r: ProbeResult, verbose: bool = False) -> None:
    emoji = _status_emoji(r)
    print(f"\n{emoji} {r.name} ({r.kind})")
    if r.extra.get("url"):
        print(f"   URL:        {r.extra['url']}")
    print(f"   Status:     {'OK' if r.ok else 'FAIL'}")
    print(f"   Artigos:    {r.n_articles}")
    print(f"   Tempo:      {r.elapsed_s:.2f}s")

    if r.kind == "rss":
        total = r.extra.get("entries_total", 0)
        print(f"   Entries no feed: {total} "
              f"(filtrados por lookback: {r.extra.get('skipped_old', 0)})")

    if r.oldest and r.newest:
        print(f"   Datas:      {r.oldest.strftime('%Y-%m-%d %H:%M')} → "
              f"{r.newest.strftime('%Y-%m-%d %H:%M')}")

    if r.text_lengths:
        avg = sum(r.text_lengths) / len(r.text_lengths)
        print(f"   Texto:      média {avg:.0f} chars (amostra de {len(r.text_lengths)})")

    if r.error:
        print(f"   ❌ Erro:    {r.error}")
        if verbose and r.extra.get("traceback"):
            print("   --- Traceback ---")
            for line in r.extra["traceback"].splitlines():
                print(f"   {line}")

    if r.extra.get("warning"):
        print(f"   ⚠️  {r.extra['warning']}")

    if r.sample_titles:
        print(f"   Amostra de títulos:")
        for i, t in enumerate(r.sample_titles, 1):
            print(f"     {i}. {t[:90]}")
            if verbose and i <= len(r.sample_urls):
                print(f"        {r.sample_urls[i-1]}")


def _print_table_row(r: ProbeResult) -> None:
    emoji = _status_emoji(r)
    name = r.name[:30].ljust(30)
    kind = r.kind.ljust(8)
    n = f"{r.n_articles:>4}"
    t = f"{r.elapsed_s:>6.2f}s"
    err = f" — {r.error[:60]}" if r.error else ""
    print(f"{emoji}  {name}  {kind}  {n}  {t}{err}")


def _print_summary(results: list[ProbeResult]) -> None:
    n_ok = sum(1 for r in results if r.ok and r.n_articles > 0)
    n_warn = sum(1 for r in results if r.ok and r.n_articles == 0)
    n_fail = sum(1 for r in results if not r.ok)
    total_time = sum(r.elapsed_s for r in results)
    total_articles = sum(r.n_articles for r in results)
    print()
    print("=" * 70)
    print(f"Resumo: {n_ok} ✅ OK  ·  {n_warn} ⚠️ vazio  ·  {n_fail} ❌ falha")
    print(f"Total: {total_articles} artigos em {total_time:.1f}s")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Live health check para fontes RSS e scrapers.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("source", nargs="?", help="Nome (ou parte do nome) da fonte")
    parser.add_argument("--list", action="store_true", help="Lista fontes e sai")
    parser.add_argument("--all", action="store_true", help="Testa todas as fontes")
    parser.add_argument("--rss", action="store_true", help="Testa apenas RSS feeds")
    parser.add_argument("--scrapers", action="store_true", help="Testa apenas scrapers")
    parser.add_argument(
        "--lookback", type=int, default=MAX_ARTICLE_AGE_HOURS,
        help=f"Janela em horas (default: {MAX_ARTICLE_AGE_HOURS})",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Saída detalhada")
    parser.add_argument("--quiet", action="store_true", help="Silencia logs do pipeline")
    args = parser.parse_args()

    log_level = logging.WARNING if args.quiet else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        stream=sys.stderr,
    )

    sources = _build_sources(args.lookback)

    # --- list mode ---
    if args.list:
        print(f"{len(sources)} fontes configuradas (lookback default: {args.lookback}h):\n")
        rss = [s for s in sources if s.kind == "rss"]
        scr = [s for s in sources if s.kind == "scraper"]
        print(f"📡 RSS feeds ({len(rss)}):")
        for s in rss:
            print(f"   - {s.name}")
        print(f"\n🕷️  Scrapers ({len(scr)}):")
        for s in scr:
            print(f"   - {s.name}")
        return 0

    # --- batch mode ---
    if args.all or args.rss or args.scrapers:
        if args.rss:
            targets = [s for s in sources if s.kind == "rss"]
        elif args.scrapers:
            targets = [s for s in sources if s.kind == "scraper"]
        else:
            targets = sources

        print(f"Testando {len(targets)} fontes (lookback: {args.lookback}h)...\n")
        header = f"   {'NOME':<30}  {'TIPO':<8}  {'N':>4}  {'TEMPO':>7}"
        print(header)
        print("-" * len(header))

        results: list[ProbeResult] = []
        for s in targets:
            try:
                r = s.runner()
            except Exception as exc:
                r = ProbeResult(name=s.name, kind=s.kind, ok=False,
                                n_articles=0, elapsed_s=0, error=f"runner exception: {exc}")
            results.append(r)
            _print_table_row(r)

        _print_summary(results)

        if args.verbose:
            print("\n--- Detalhes ---")
            for r in results:
                _print_detailed(r, verbose=False)

        return 0 if all(r.ok for r in results) else 1

    # --- single source mode ---
    if not args.source:
        parser.print_help()
        return 2

    src = _find_source(args.source, sources)
    if src is None:
        print(f"❌ Fonte '{args.source}' não encontrada. Use --list para ver disponíveis.",
              file=sys.stderr)
        return 2

    print(f"Testando: {src.name} ({src.kind}) — lookback {args.lookback}h")
    try:
        result = src.runner()
    except Exception as exc:
        result = ProbeResult(name=src.name, kind=src.kind, ok=False,
                             n_articles=0, elapsed_s=0,
                             error=f"runner exception: {exc}")
        result.extra["traceback"] = traceback.format_exc()

    _print_detailed(result, verbose=args.verbose)
    return 0 if result.ok and result.n_articles > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
