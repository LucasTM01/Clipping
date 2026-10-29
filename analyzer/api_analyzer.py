"""
Camada 3 — Análise e sumarização via LLM (Ollama/Qwen por padrão, OpenAI GPT opcional).

Selecione o backend via ANALYSIS_BACKEND no .env:
  ANALYSIS_BACKEND=ollama   → usa Ollama (padrão, modelo definido em ANALYSIS_MODEL)
  ANALYSIS_BACKEND=openai   → usa OpenAI GPT (modelo definido em GPT_MODEL)

Chamadas são feitas em paralelo (máx. API_MAX_WORKERS simultâneas).
"""

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

import requests

from config.settings import (
    ANALYSIS_BACKEND,
    ANALYSIS_MODEL,
    OLLAMA_BASE_URL,
    OPENAI_API_KEY,
    GPT_MODEL,
    GPT_MAX_TOKENS,
    API_MAX_WORKERS,
)
from config.prompts import ANALYSIS_SYSTEM_PROMPT, ANALYSIS_USER_PROMPT
from screener.local_llm import ScreeningResult

logger = logging.getLogger(__name__)


@dataclass
class AnalysisResult:
    source: str
    title: str
    url: str
    published_at: str
    tickers: list[str]
    summary: str
    sentiment: str
    action_required: bool
    action_detail: str | None
    category: str  # "company" ou "macro_sectoral"
    sector_tags: list[str]
    relevance_score: float | None = None   # preenchido pelo screener v2
    tier: str | None = None                # "high" | "medium" | "low_review" | None


def _build_analysis_prompt(screening: ScreeningResult) -> str:
    # Tickers do screener NÃO são passados ao prompt de propósito — o LLM
    # classifica de forma independente lendo o texto (evita ancoragem em EMBR3).
    # Eles permanecem em screening.tickers apenas como fallback se o LLM falhar.
    return ANALYSIS_USER_PROMPT.format(
        title=screening.article.title,
        source=screening.article.source,
        published_at=screening.article.published_at,
        full_text=screening.article.full_text[:4000],
    )


# ---------------------------------------------------------------------------
# Ollama backend
# ---------------------------------------------------------------------------

def _analyze_one_ollama(screening: ScreeningResult) -> AnalysisResult:
    """Analisa um artigo via Ollama (Qwen ou qualquer modelo local)."""
    user_prompt = _build_analysis_prompt(screening)
    url = f"{OLLAMA_BASE_URL}/api/chat"
    payload = {
        "model": ANALYSIS_MODEL,
        "think": False,
        "messages": [
            {"role": "system", "content": ANALYSIS_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
        "format": "json",
        "options": {
            "temperature": 0.1,
            "num_predict": 800,
        },
    }

    try:
        response = requests.post(url, json=payload, timeout=240)
        response.raise_for_status()
        content = response.json().get("message", {}).get("content", "")
        parsed = _parse_json_response(content)
        if parsed:
            logger.debug(f"Análise OK (Ollama): {screening.article.title[:60]}")
            return AnalysisResult(
                source=screening.article.source,
                title=screening.article.title,
                url=screening.article.url,
                published_at=screening.article.published_at,
                tickers=parsed.get("tickers", screening.tickers),
                summary=parsed.get("summary", "Erro na geração de resumo."),
                sentiment=parsed.get("sentiment", "Neutro"),
                action_required=parsed.get("action_required", False),
                action_detail=parsed.get("action_detail"),
                category=parsed.get("category", "macro_sectoral"),
                sector_tags=screening.sector_tags,
            )
        logger.warning(f"JSON inválido (Ollama): {screening.article.title[:60]}")
        return _fallback_analysis(screening)

    except requests.exceptions.ConnectionError:
        logger.error("Ollama não está rodando. Usando fallback.")
        return _fallback_analysis(screening)
    except requests.exceptions.RequestException as e:
        logger.error(f"Erro Ollama: {e} — {screening.article.title[:50]}")
        return _fallback_analysis(screening)


# ---------------------------------------------------------------------------
# OpenAI backend
# ---------------------------------------------------------------------------

def _analyze_one_openai(client, screening: ScreeningResult) -> AnalysisResult:
    """Analisa um artigo via OpenAI GPT. O client é thread-safe."""
    from openai import RateLimitError, APIError

    try:
        user_prompt = _build_analysis_prompt(screening)
        response = client.chat.completions.create(
            model=GPT_MODEL,
            max_tokens=GPT_MAX_TOKENS,
            temperature=0.2,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": ANALYSIS_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
        )
        content = response.choices[0].message.content or ""
        parsed = _parse_json_response(content)
        if parsed:
            logger.debug(f"Análise OK (OpenAI): {screening.article.title[:60]}")
            return AnalysisResult(
                source=screening.article.source,
                title=screening.article.title,
                url=screening.article.url,
                published_at=screening.article.published_at,
                tickers=parsed.get("tickers", screening.tickers),
                summary=parsed.get("summary", "Erro na geração de resumo."),
                sentiment=parsed.get("sentiment", "Neutro"),
                action_required=parsed.get("action_required", False),
                action_detail=parsed.get("action_detail"),
                category=parsed.get("category", "macro_sectoral"),
                sector_tags=screening.sector_tags,
            )
        logger.warning(f"JSON inválido (OpenAI): {screening.article.title[:60]}")
        return _fallback_analysis(screening)

    except RateLimitError:
        logger.warning(f"Rate limit — fallback: {screening.article.title[:50]}")
        return _fallback_analysis(screening)
    except APIError as e:
        logger.error(f"Erro OpenAI API: {e} — {screening.article.title[:50]}")
        return _fallback_analysis(screening)


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def analyze_batch(
    screenings: list[ScreeningResult],
    fallback_local: bool = False,
) -> list[AnalysisResult]:
    """
    Analisa um batch de artigos em paralelo.
    O backend é controlado por ANALYSIS_BACKEND ('ollama' ou 'openai').
    """
    if not screenings:
        return []

    backend = ANALYSIS_BACKEND.lower()
    logger.info(
        f"Iniciando análise de {len(screenings)} artigos "
        f"({API_MAX_WORKERS} workers, backend: {backend}, "
        f"modelo: {ANALYSIS_MODEL if backend == 'ollama' else GPT_MODEL})"
    )

    if backend == "openai":
        if not OPENAI_API_KEY:
            logger.warning("OPENAI_API_KEY não configurada. Usando fallback.")
            return [_fallback_analysis(s) for s in screenings]
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)
        submit = lambda s: _analyze_one_openai(client, s)
    else:
        submit = _analyze_one_ollama

    results: list[AnalysisResult] = []
    with ThreadPoolExecutor(max_workers=API_MAX_WORKERS) as pool:
        futures = {pool.submit(submit, s): s for s in screenings}
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except Exception as e:
                s = futures[future]
                logger.error(f"Falha inesperada em thread: {e} — {s.article.title[:50]}")
                results.append(_fallback_analysis(s))

    logger.info(f"Análise concluída: {len(results)} artigos processados via {backend}")
    return results


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _strip_thinking(text: str) -> str:
    """Remove <think>...</think> blocks emitted by reasoning models (e.g. Qwen3)."""
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def _parse_json_response(text: str) -> dict | None:
    text = _strip_thinking(text)

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # Bracket-balanced extractor: finds the first complete {...} object,
    # stopping at its true closing brace rather than the last } in the string.
    start = text.find("{")
    if start != -1:
        depth = 0
        for i, ch in enumerate(text[start:], start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        break

    logger.debug(f"_parse_json_response falhou. Resposta (300 chars): {text[:300]!r}")
    return None


def _fallback_analysis(screening: ScreeningResult) -> AnalysisResult:
    tickers_str = ", ".join(screening.tickers) if screening.tickers else "Macro/Setorial"
    summary = (
        f"[Análise automática — sem API] {screening.article.title}. "
        f"Artigo da fonte {screening.article.source} potencialmente relevante "
        f"para {tickers_str}. Requer revisão manual do analista."
    )
    return AnalysisResult(
        source=screening.article.source,
        title=screening.article.title,
        url=screening.article.url,
        published_at=screening.article.published_at,
        tickers=screening.tickers,
        summary=summary,
        sentiment="Neutro",
        action_required=False,
        action_detail=None,
        category="company" if screening.tickers else "macro_sectoral",
        sector_tags=screening.sector_tags,
    )
