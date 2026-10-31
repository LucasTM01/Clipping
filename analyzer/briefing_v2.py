"""
Briefing v2 — geração dos itens do email v2 (em inglês, factual, por setor).

Diferente da v1 (analyzer/api_analyzer.py → AnalysisResult com opinião/tese), a v2
produz, por notícia, apenas:
  - um resumo factual curto EM INGLÊS (sem opinião, sem dizer a qual empresa pertence)
  - o setor (um dos 4 do clipping "Safra's Morning Notes")

O código da v1 permanece intacto; este módulo é independente e roda sobre os mesmos
ScoredArticle aprovados que alimentam o briefing.
"""

from __future__ import annotations

import logging
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
from config.prompts import (
    BRIEFING_V2_SECTORS,
    BRIEFING_V2_SYSTEM_PROMPT,
    BRIEFING_V2_USER_PROMPT,
)
from analyzer.api_analyzer import _parse_json_response
from screener.screener_v2 import ScoredArticle

logger = logging.getLogger(__name__)

_MAX_FULL_TEXT = 4000
_DEFAULT_SECTOR = "Others"

# Mapeamento de nome de fonte (produção) → nome curto de exibição (como no Word).
# Ordem importa: "Pipeline Valor" deve casar "pipeline" antes de "valor".
_SOURCE_DISPLAY: list[tuple[str, str]] = [
    ("pipeline", "Pipeline"),
    ("valor", "Valor"),
    ("estadão", "Estadão"),
    ("estadao", "Estadão"),
    ("folha", "Folha"),
    ("cnn", "CNN"),
    ("aeroin", "Aeroin"),
    ("airway", "Airway"),
    ("automotive business", "Automotive Business"),
    ("autodata", "AutoData"),
    ("brazil journal", "Brazil Journal"),
    ("canal solar", "Canal Solar"),
    ("portal solar", "Portal Solar"),
    ("diário do transporte", "Diário do Transporte"),
    ("diario do transporte", "Diário do Transporte"),
    ("aerospace global", "Aerospace Global News"),
    ("randoncorp", "Randoncorp"),
    ("weg news", "WEG"),
    ("infomoney", "InfoMoney"),
    ("oglobo", "O Globo"),
    ("o globo", "O Globo"),
    ("reuters", "Reuters"),
    ("bloomberg", "Bloomberg"),
    ("cvm", "CVM"),
]


@dataclass
class BriefItemV2:
    sector: str
    text: str          # resumo factual em inglês
    source: str        # nome curto da fonte (Valor, Folha, Reuters...)
    url: str
    published_at: str


def short_source(name: str) -> str:
    """Converte o nome da fonte de produção no nome curto de exibição."""
    n = (name or "").strip().lower()
    for needle, display in _SOURCE_DISPLAY:
        if needle in n:
            return display
    return (name or "Source").strip().title()


def _normalize_sector(raw: str | None) -> str:
    if not raw:
        return _DEFAULT_SECTOR
    raw = raw.strip()
    for s in BRIEFING_V2_SECTORS:
        if raw.lower() == s.lower():
            return s
    return _DEFAULT_SECTOR


def _build_prompt(scored: ScoredArticle) -> str:
    art = scored.article
    return BRIEFING_V2_USER_PROMPT.format(
        title=art.title,
        source=art.source,
        published_at=art.published_at,
        full_text=(art.full_text or "")[:_MAX_FULL_TEXT],
    )


def _fallback_item(scored: ScoredArticle) -> BriefItemV2:
    """Sem LLM: usa o título como texto e Others como setor."""
    art = scored.article
    return BriefItemV2(
        sector=_DEFAULT_SECTOR,
        text=art.title,
        source=short_source(art.source),
        url=art.url,
        published_at=art.published_at,
    )


def _item_from_parsed(scored: ScoredArticle, parsed: dict) -> BriefItemV2:
    art = scored.article
    text = (parsed.get("text") or "").strip() or art.title
    return BriefItemV2(
        sector=_normalize_sector(parsed.get("sector")),
        text=text,
        source=short_source(art.source),
        url=art.url,
        published_at=art.published_at,
    )


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------

def _classify_one_ollama(scored: ScoredArticle) -> BriefItemV2:
    url = f"{OLLAMA_BASE_URL}/api/chat"
    payload = {
        "model": ANALYSIS_MODEL,
        "think": False,
        "messages": [
            {"role": "system", "content": BRIEFING_V2_SYSTEM_PROMPT},
            {"role": "user", "content": _build_prompt(scored)},
        ],
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.1, "num_predict": 400},
    }
    try:
        resp = requests.post(url, json=payload, timeout=240)
        resp.raise_for_status()
        content = resp.json().get("message", {}).get("content", "")
        parsed = _parse_json_response(content)
        if parsed:
            return _item_from_parsed(scored, parsed)
        logger.warning(f"v2 JSON inválido (Ollama): {scored.article.title[:60]}")
    except requests.exceptions.ConnectionError:
        logger.error("Ollama não está rodando — fallback v2.")
    except requests.exceptions.RequestException as e:
        logger.error(f"Erro Ollama v2: {e} — {scored.article.title[:50]}")
    return _fallback_item(scored)


def _classify_one_openai(client, scored: ScoredArticle) -> BriefItemV2:
    from openai import RateLimitError, APIError
    try:
        resp = client.chat.completions.create(
            model=GPT_MODEL,
            max_tokens=GPT_MAX_TOKENS,
            temperature=0.2,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": BRIEFING_V2_SYSTEM_PROMPT},
                {"role": "user", "content": _build_prompt(scored)},
            ],
        )
        content = resp.choices[0].message.content or ""
        parsed = _parse_json_response(content)
        if parsed:
            return _item_from_parsed(scored, parsed)
        logger.warning(f"v2 JSON inválido (OpenAI): {scored.article.title[:60]}")
    except RateLimitError:
        logger.warning(f"v2 rate limit — fallback: {scored.article.title[:50]}")
    except APIError as e:
        logger.error(f"v2 erro OpenAI: {e} — {scored.article.title[:50]}")
    return _fallback_item(scored)


# ---------------------------------------------------------------------------
# Public
# ---------------------------------------------------------------------------

def build_briefing_v2(scored: list[ScoredArticle]) -> list[BriefItemV2]:
    """Gera os itens do briefing v2 (em inglês, por setor) em paralelo."""
    if not scored:
        return []

    backend = ANALYSIS_BACKEND.lower()
    logger.info(
        f"Briefing v2: gerando {len(scored)} itens "
        f"({API_MAX_WORKERS} workers, backend: {backend})"
    )

    if backend == "openai":
        if not OPENAI_API_KEY:
            logger.warning("OPENAI_API_KEY ausente — briefing v2 usa fallback (títulos).")
            return [_fallback_item(s) for s in scored]
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)
        submit = lambda s: _classify_one_openai(client, s)
    else:
        submit = _classify_one_ollama

    results: list[BriefItemV2] = []
    with ThreadPoolExecutor(max_workers=API_MAX_WORKERS) as pool:
        futures = {pool.submit(submit, s): s for s in scored}
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except Exception as e:
                s = futures[future]
                logger.error(f"Falha inesperada v2: {e} — {s.article.title[:50]}")
                results.append(_fallback_item(s))

    logger.info(f"Briefing v2: {len(results)} itens gerados via {backend}")
    return results
