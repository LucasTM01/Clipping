"""
Camada 2 — Triagem de relevância usando LLM local via Ollama.

Recebe artigos e determina quais são relevantes para o universo
de cobertura, retornando tickers e tags setoriais.
"""

import json
import logging
import re
from dataclasses import dataclass

import requests

from config.settings import (
    OLLAMA_BASE_URL,
    OLLAMA_MODEL,
    SCREENER_SNIPPET_LENGTH,
    SCREENER_CONFIDENCE_THRESHOLD,
    COVERAGE_UNIVERSE,
    RELEVANT_SECTORS,
)
from config.prompts import SCREENING_SYSTEM_PROMPT, SCREENING_USER_PROMPT
from fetcher.rss_fetcher import Article

logger = logging.getLogger(__name__)


@dataclass
class ScreeningResult:
    article: Article
    relevant: bool
    tickers: list[str]
    sector_tags: list[str]
    confidence: float


def _keyword_prefilter(article: Article) -> tuple[bool, list[str], list[str]]:
    """
    Pré-filtro baseado em keywords antes de chamar o LLM.
    Retorna (possivelmente_relevante, tickers_encontrados, setores_encontrados).
    """
    text_lower = (article.title + " " + article.full_text[:1000]).lower()

    matched_tickers = []
    for ticker, info in COVERAGE_UNIVERSE.items():
        for kw in info["keywords"]:
            if kw.lower() in text_lower:
                matched_tickers.append(ticker)
                break

    matched_sectors = []
    for sector_kw in RELEVANT_SECTORS:
        if sector_kw.lower() in text_lower:
            matched_sectors.append(sector_kw)

    is_relevant = bool(matched_tickers) or bool(matched_sectors)
    return is_relevant, matched_tickers, matched_sectors


def _call_ollama(system_prompt: str, user_prompt: str) -> dict | None:
    """Chama a API do Ollama e retorna o JSON parseado."""
    url = f"{OLLAMA_BASE_URL}/api/chat"
    payload = {
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
        "format": "json",
        "options": {
            "temperature": 0.1,
            "num_predict": 256,
        },
    }

    try:
        response = requests.post(url, json=payload, timeout=30)
        response.raise_for_status()
        data = response.json()
        content = data.get("message", {}).get("content", "")
        return json.loads(content)
    except requests.exceptions.ConnectionError:
        logger.error("Ollama não está rodando. Verifique se o serviço está ativo.")
        return None
    except (json.JSONDecodeError, KeyError) as e:
        logger.warning(f"Resposta inválida do Ollama: {e}")
        return None
    except requests.exceptions.RequestException as e:
        logger.error(f"Erro na chamada ao Ollama: {e}")
        return None


def _validate_tickers(tickers: list) -> list[str]:
    """Garante que só tickers válidos do universo de cobertura passem."""
    valid = set(COVERAGE_UNIVERSE.keys())
    return [t.upper() for t in tickers if t.upper() in valid]


def screen_articles(
    articles: list[Article],
    use_llm: bool = True,
) -> list[ScreeningResult]:
    """
    Filtra artigos por relevância. Usa keyword prefilter + LLM local.
    Se use_llm=False ou Ollama indisponível, só usa keywords.
    """
    results: list[ScreeningResult] = []
    llm_available = use_llm
    llm_calls = 0
    llm_failures = 0

    for article in articles:
        # Pré-filtro por keywords
        kw_relevant, kw_tickers, kw_sectors = _keyword_prefilter(article)

        # Se keyword match é forte (ticker direto), aceita sem LLM
        if kw_tickers:
            results.append(ScreeningResult(
                article=article,
                relevant=True,
                tickers=kw_tickers,
                sector_tags=kw_sectors,
                confidence=0.95,
            ))
            continue

        # Se keywords setoriais encontradas ou não tem match, usa LLM
        if llm_available:
            snippet = article.full_text[:SCREENER_SNIPPET_LENGTH]
            if not snippet:
                snippet = article.title

            prompt = SCREENING_USER_PROMPT.format(
                title=article.title,
                source=article.source,
                snippet=snippet,
            )

            llm_result = _call_ollama(SCREENING_SYSTEM_PROMPT, prompt)
            llm_calls += 1

            if llm_result is None:
                llm_failures += 1
                # Se muitas falhas, desabilita LLM
                if llm_failures > 5:
                    logger.warning("Muitas falhas do Ollama, desabilitando LLM.")
                    llm_available = False
                # Fallback: usa resultado do keyword
                if kw_relevant:
                    results.append(ScreeningResult(
                        article=article,
                        relevant=True,
                        tickers=kw_tickers,
                        sector_tags=kw_sectors,
                        confidence=0.5,
                    ))
                continue

            is_relevant = llm_result.get("relevant", False)
            confidence = float(llm_result.get("confidence", 0.0))
            tickers = _validate_tickers(llm_result.get("tickers", []))
            sector_tags = llm_result.get("sector_tags", [])

            # Merge com resultados do keyword
            all_tickers = list(set(kw_tickers + tickers))
            all_sectors = list(set(kw_sectors + sector_tags))

            if is_relevant and confidence >= SCREENER_CONFIDENCE_THRESHOLD:
                results.append(ScreeningResult(
                    article=article,
                    relevant=True,
                    tickers=all_tickers,
                    sector_tags=all_sectors,
                    confidence=confidence,
                ))
        else:
            # Sem LLM: aceita se teve match de setor
            if kw_relevant:
                results.append(ScreeningResult(
                    article=article,
                    relevant=True,
                    tickers=kw_tickers,
                    sector_tags=kw_sectors,
                    confidence=0.5,
                ))

    logger.info(
        f"Triagem: {len(articles)} artigos -> {len(results)} relevantes "
        f"(LLM calls: {llm_calls}, falhas: {llm_failures})"
    )
    return results
