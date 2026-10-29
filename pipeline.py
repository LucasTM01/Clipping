"""
Camada 5 — Orquestração do pipeline completo.

Executa todas as camadas em sequência:
1. Coleta (RSS Fetcher)
2. Enriquecimento de texto (trafilatura)
3. Triagem de relevância (Ollama)
4. Análise e sumarização (Claude API)
5. Geração de documento (Word)
"""

import logging
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

from config.settings import MAX_ARTICLE_AGE_HOURS
from fetcher.rss_fetcher import fetch_all_feeds, enrich_full_text, enrich_with_playwright
from scrapers.registry import fetch_all_scrapers
from screener.local_llm import screen_articles
from screener.screener_v2 import screen_articles_v2
from analyzer.api_analyzer import analyze_batch
from analyzer.tiered_dispatcher import dispatch_batch
from generator.doc_generator import generate_briefing
from data.export_feedback_candidates import export_candidates, export_approved
from notifier.email_sender import PipelineStats

logger = logging.getLogger(__name__)


def setup_logging(verbose: bool = False) -> None:
    """Configura logging para console e arquivo."""
    level = logging.DEBUG if verbose else logging.INFO
    log_format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    date_format = "%H:%M:%S"

    # Console handler (force UTF-8 on Windows)
    import io
    console = logging.StreamHandler(
        io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    )
    console.setLevel(level)
    console.setFormatter(logging.Formatter(log_format, datefmt=date_format))

    # File handler
    log_dir = Path(__file__).parent / "output"
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / f"pipeline_{datetime.now().strftime('%Y%m%d')}.log"

    file_handler = logging.FileHandler(str(log_file), encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(log_format, datefmt=date_format))

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(console)
    root.addHandler(file_handler)


def run_pipeline(
    use_llm_screener: bool = True,
    enrich_text: bool = True,
    verbose: bool = False,
    use_legacy_screener: bool = False,
    deep_dive: bool | None = None,
    clipping_version: str = "v2",
) -> Path | None:
    """
    Executa o pipeline completo e retorna o path do documento gerado.

    deep_dive: força ligar/desligar o 3º email (análise detalhada em 2 estágios).
               None → usa settings.deep_dive_enabled.
    clipping_version: "v2" (default) → email "Safra's Morning Notes - AI View"
               (inglês, factual, por setor). "v1" → briefing HTML legado (PT, com tese).
    """
    setup_logging(verbose)
    start_time = datetime.now()
    stats = PipelineStats(started_at=start_time)
    scored: list = []

    logger.info("=" * 60)
    logger.info("PIPELINE DE CLIPPING — EQUITY RESEARCH")
    logger.info(f"Execução: {start_time.strftime('%d/%m/%Y %H:%M:%S')}")
    logger.info("=" * 60)

    try:
        # --- Camada 1: Coleta ---
        logger.info("--- CAMADA 1: Coleta de feeds RSS ---")
        articles = fetch_all_feeds()

        # --- Camada 1c: Scrapers (sites sem RSS) ---
        logger.info("--- CAMADA 1c: Scrapers (sites sem RSS) ---")
        since = datetime.now(timezone.utc) - timedelta(hours=MAX_ARTICLE_AGE_HOURS)
        try:
            scraped = fetch_all_scrapers(since)
        except Exception as e:
            logger.error(f"Scrapers falharam (não crítico): {e}", exc_info=True)
            scraped = []
        articles.extend(scraped)

        # Collect per-source fetch counts
        for a in articles:
            stats.ensure_source(a.source).n_fetched += 1
        stats.n_fetched = len(articles)

        if not articles:
            logger.warning("Nenhum artigo coletado. Verifique conectividade e feeds.")
            stats.error = "Nenhum artigo coletado"
            return None

        # --- Camada 1b: Enriquecimento ---
        if enrich_text:
            logger.info("--- CAMADA 1b: Enriquecimento de texto ---")
            articles = enrich_full_text(articles)
            articles = enrich_with_playwright(articles)  # Reuters/Bloomberg via Chrome real

        # --- Camada 2: Triagem ---
        if use_legacy_screener:
            logger.info("--- CAMADA 2: Triagem de relevância (legacy: keyword + Ollama) ---")
            screenings = screen_articles(articles, use_llm=use_llm_screener)
            if not screenings:
                logger.info("Nenhum artigo relevante identificado. Gerando documento vazio.")
                output_path = generate_briefing([])
                _log_summary(start_time, len(articles), 0, 0, output_path)
                return output_path
            logger.info("--- CAMADA 3: Análise e sumarização (legacy) ---")
            analyses = analyze_batch(screenings)
            scored = []
        else:
            logger.info("--- CAMADA 2: Triagem de relevância (v2: BGE-M3 + Qdrant) ---")
            scored = screen_articles_v2(articles)

            # Collect screening stats
            for s in scored:
                src = stats.ensure_source(s.article.source)
                src.tier_counts[s.tier] += 1
                stats.tier_counts[s.tier] = stats.tier_counts.get(s.tier, 0) + 1
                if s.tier != "discard":
                    src.n_approved += 1
                    src.scores.append(s.relevance_score)

            if not scored:
                logger.info("Nenhum artigo relevante identificado. Gerando documento vazio.")
                output_path = generate_briefing([])
                _log_summary(start_time, len(articles), 0, 0, output_path)
                return output_path

            # --- Limite de artigos por briefing ---
            from config.settings import settings as _s
            max_arts = _s.briefing_max_articles
            if max_arts > 0 and len(scored) > max_arts:
                scored.sort(key=lambda s: s.relevance_score, reverse=True)
                dropped = len(scored) - max_arts
                scored = scored[:max_arts]
                logger.info(
                    f"Limite de {max_arts} artigos atingido — "
                    f"{dropped} artigos com menor score removidos do briefing."
                )

            logger.info("--- CAMADA 3: Análise e sumarização (tiered dispatcher) ---")
            analyses = dispatch_batch(scored)

        # Collect analysis stats
        for a in analyses:
            stats.sentiment_counts[a.sentiment] = (
                stats.sentiment_counts.get(a.sentiment, 0) + 1
            )
            if a.action_required:
                stats.n_action_required += 1
            stats.ensure_source(a.source).n_analyzed += 1
        stats.n_analyzed = len(analyses)

        # --- Camada 4: Documento ---
        logger.info("--- CAMADA 4: Geração do documento ---")
        output_path = generate_briefing(analyses)
        stats.docx_path = str(output_path)

        n_relevant = len(scored) if not use_legacy_screener else len(screenings)
        _log_summary(start_time, len(articles), n_relevant, len(analyses), output_path)

        # Feedback loop
        if not use_legacy_screener:
            try:
                n_cand = export_candidates()
                if n_cand:
                    logger.info(f"Feedback: {n_cand} novos near-miss candidates -> feedback/candidates.jsonl")
            except Exception as e:
                logger.warning(f"Feedback near-miss export falhou (não crítico): {e}")

            try:
                n_appr = export_approved()
                if n_appr:
                    logger.info(f"Feedback: {n_appr} artigos aprovados -> feedback/candidates.jsonl")
            except Exception as e:
                logger.warning(f"Feedback approved export falhou (não crítico): {e}")

    except Exception as exc:
        stats.error = str(exc)
        logger.error(f"Pipeline falhou: {exc}", exc_info=True)
        output_path = None
        analyses = []

    # --- Notificações por email ---
    stats.finished_at = datetime.now()
    from config.settings import settings as _settings
    if _settings.email_enabled:
        logger.info(f"--- Enviando emails (briefing {clipping_version}) ---")
        from notifier.email_sender import (
            send_briefing_email,
            send_briefing_v2_email,
            send_ops_report,
        )
        if clipping_version == "v2":
            if scored:
                from analyzer.briefing_v2 import build_briefing_v2
                items = build_briefing_v2(scored)
                send_briefing_v2_email(items, stats, _settings.email_briefing_to)
            elif analyses:
                logger.warning(
                    "Briefing v2 requer o screener v2 (sem ScoredArticle no modo legacy) "
                    "— enviando briefing v1."
                )
                send_briefing_email(analyses, _settings.email_briefing_to)
        elif analyses:
            send_briefing_email(analyses, _settings.email_briefing_to)
        send_ops_report(stats, _settings.email_ops_to)

    # --- Deep dive: análise detalhada em 2 estágios (3º email) ---
    run_deep_dive = _settings.deep_dive_enabled if deep_dive is None else deep_dive
    if run_deep_dive and scored:
        logger.info("--- Deep dive: análise detalhada (Estágio A local + Estágio B API) ---")
        try:
            from analyzer.deep_dive import run_deep_dive as _run_deep_dive
            from notifier.email_sender import send_deep_dive_email
            briefs, synthesis = _run_deep_dive(scored)
            if briefs:
                send_deep_dive_email(briefs, synthesis, _settings.deep_dive_to)
        except Exception as e:
            logger.warning(f"Deep dive falhou (não crítico): {e}", exc_info=True)

    return output_path


def _log_summary(
    start_time: datetime,
    total_articles: int,
    relevant: int,
    analyzed: int,
    output_path: Path | None,
) -> None:
    """Loga resumo da execução."""
    elapsed = datetime.now() - start_time
    logger.info("=" * 60)
    logger.info("RESUMO DA EXECUÇÃO")
    logger.info(f"  Artigos coletados:   {total_articles}")
    logger.info(f"  Artigos relevantes:  {relevant}")
    logger.info(f"  Artigos analisados:  {analyzed}")
    logger.info(f"  Documento:           {output_path}")
    logger.info(f"  Tempo total:         {elapsed}")
    logger.info("=" * 60)
