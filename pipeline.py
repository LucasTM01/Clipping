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
from datetime import datetime
from pathlib import Path

from fetcher.rss_fetcher import fetch_all_feeds, enrich_full_text
from screener.local_llm import screen_articles
from screener.screener_v2 import screen_articles_v2
from analyzer.api_analyzer import analyze_batch
from analyzer.tiered_dispatcher import dispatch_batch
from generator.doc_generator import generate_briefing
from data.export_feedback_candidates import export_candidates

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
) -> Path | None:
    """
    Executa o pipeline completo e retorna o path do documento gerado.
    """
    setup_logging(verbose)
    start_time = datetime.now()

    logger.info("=" * 60)
    logger.info("PIPELINE DE CLIPPING — EQUITY RESEARCH")
    logger.info(f"Execução: {start_time.strftime('%d/%m/%Y %H:%M:%S')}")
    logger.info("=" * 60)

    # --- Camada 1: Coleta ---
    logger.info("--- CAMADA 1: Coleta de feeds RSS ---")
    articles = fetch_all_feeds()

    if not articles:
        logger.warning("Nenhum artigo coletado. Verifique conectividade e feeds.")
        return None

    # --- Camada 1b: Enriquecimento ---
    if enrich_text:
        logger.info("--- CAMADA 1b: Enriquecimento de texto ---")
        articles = enrich_full_text(articles)

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
    else:
        logger.info("--- CAMADA 2: Triagem de relevância (v2: BGE-M3 + Qdrant) ---")
        scored = screen_articles_v2(articles)
        if not scored:
            logger.info("Nenhum artigo relevante identificado. Gerando documento vazio.")
            output_path = generate_briefing([])
            _log_summary(start_time, len(articles), 0, 0, output_path)
            return output_path
        logger.info("--- CAMADA 3: Análise e sumarização (tiered dispatcher) ---")
        analyses = dispatch_batch(scored)

    # --- Camada 4: Documento ---
    logger.info("--- CAMADA 4: Geração do documento ---")
    output_path = generate_briefing(analyses)

    n_relevant = len(scored) if not use_legacy_screener else len(screenings)
    _log_summary(start_time, len(articles), n_relevant, len(analyses), output_path)

    # Feedback loop: acumula near-miss candidates do audit log para revisão
    if not use_legacy_screener:
        try:
            n_candidates = export_candidates()
            if n_candidates:
                logger.info(f"Feedback: {n_candidates} novos near-miss candidates -> feedback/candidates.jsonl")
        except Exception as e:
            logger.warning(f"Feedback export falhou (não crítico): {e}")

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
