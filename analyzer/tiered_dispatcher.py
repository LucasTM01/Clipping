"""
Dispatcher por tier: recebe ScoredArticle list e roteia para análise LLM.

Tiers:
  high     → análise via backend configurado (futuramente Qwen local)
  medium   → análise via backend configurado (OpenAI GPT ou Ollama)
  low_review → sem análise LLM, não vai ao briefing
  discard  → nunca chega aqui (filtrado pelo screener_v2)

Ambos high e medium usam analyze_batch() do api_analyzer, que já respeita
ANALYSIS_BACKEND. A distinção high/medium é preservada no campo `tier` do
AnalysisResult para quando o tier local (Qwen) for implementado.
"""

import logging
from datetime import datetime

from analyzer.api_analyzer import AnalysisResult, analyze_batch
from screener.local_llm import ScreeningResult
from screener.screener_v2 import ScoredArticle

log = logging.getLogger(__name__)


def dispatch_batch(scored: list[ScoredArticle]) -> list[AnalysisResult]:
    """
    Analisa artigos high e medium. Artigos low_review são ignorados (não
    chegam ao briefing mas já foram logados no audit pelo screener_v2).
    """
    if not scored:
        return []

    to_analyze = [s for s in scored if s.tier in ("high", "medium", "low_review")]
    n_review = sum(1 for s in scored if s.tier == "low_review")

    if not to_analyze:
        log.info("Dispatcher: nenhum artigo para análise LLM")
        return []

    # Converter ScoredArticle → ScreeningResult para compatibilidade com analyze_batch
    screenings = [_to_screening_result(s) for s in to_analyze]

    log.info(
        f"Dispatcher: {len(to_analyze)} artigos para análise "
        f"(high={sum(1 for s in to_analyze if s.tier == 'high')}, "
        f"medium={sum(1 for s in to_analyze if s.tier == 'medium')})"
    )

    analyses = analyze_batch(screenings)

    # Enriquecer com relevance_score e tier do ScoredArticle original
    score_map = {s.article.url: s for s in to_analyze}
    for result in analyses:
        original = score_map.get(result.url)
        if original:
            result.relevance_score = original.relevance_score
            result.tier = original.tier

    return analyses


def _to_screening_result(scored: ScoredArticle) -> ScreeningResult:
    return ScreeningResult(
        article=scored.article,
        relevant=True,
        tickers=scored.predicted_tickers,
        sector_tags=[],
        confidence=scored.relevance_score,
    )
