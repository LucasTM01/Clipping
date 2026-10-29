"""
Atribui tickers a um artigo via votação ponderada dos top-k vizinhos positivos
com keyword-gating para evitar que vizinhos kNN errados superem menções explícitas.

Lógica de três fases:
  1. Votos kNN: soma dos scores dos vizinhos por ticker.
  2. Keyword matching: sempre executado (não só como fallback).
  3. Gating: se keywords identificam tickers, filtra votos kNN para apenas os
     confirmados por keyword. Se kNN e keywords divergem completamente, keywords
     ganham (artigo menciona empresa explicitamente; vizinhos erraram o setor).
     Se não há sinal de keyword, usa kNN puro (artigos macro/setoriais).
"""

import re
from functools import lru_cache

from config.settings import COVERAGE_UNIVERSE


def classify_tickers(
    nearest_positives: list[tuple[dict, float]],
    article_text: str = "",
    min_confidence: float = 0.10,
) -> tuple[list[str], dict[str, float]]:
    """
    Retorna (predicted_tickers, ticker_confidence_dict).

    predicted_tickers: tickers com confidence >= min_confidence, ordenados desc.
    ticker_confidence_dict: score normalizado [0,1] para cada ticker ativo.
    """
    # ── Fase 1: votos kNN ─────────────────────────────────────────────────────
    vote_totals: dict[str, float] = {}
    score_sum = sum(score for _, score in nearest_positives)

    for payload, score in nearest_positives:
        for ticker in payload.get("tickers", []):
            if ticker:
                vote_totals[ticker] = vote_totals.get(ticker, 0.0) + score

    # ── Fase 2: keyword matching — SEMPRE, não só como fallback ──────────────
    keyword_votes = _keyword_fallback(article_text) if article_text else {}

    # ── Fase 3: combinar com keyword-gating ──────────────────────────────────
    if vote_totals and keyword_votes:
        # Keywords identificaram tickers → filtrar kNN para só os confirmados
        filtered = {t: v for t, v in vote_totals.items() if t in keyword_votes}
        if filtered:
            # kNN e keywords convergem → usar kNN filtrado (mais robusto que keyword puro)
            active_votes = filtered
            normalizer = score_sum if score_sum > 0 else 1.0
        else:
            # kNN e keywords divergem completamente → keywords ganham
            # (artigo menciona empresa explicitamente; vizinhos erraram o setor)
            active_votes = keyword_votes
            normalizer = max(keyword_votes.values())
    elif vote_totals:
        # Sem sinal de keyword → kNN puro (artigo macro/setorial sem nome de empresa)
        active_votes = vote_totals
        normalizer = score_sum if score_sum > 0 else 1.0
    elif keyword_votes:
        # Sem vizinhos com ticker → keyword puro
        active_votes = keyword_votes
        normalizer = max(keyword_votes.values())
    else:
        return [], {}

    confidence = {t: v / normalizer for t, v in active_votes.items()}

    predicted = sorted(
        [t for t, c in confidence.items() if c >= min_confidence],
        key=lambda t: confidence[t],
        reverse=True,
    )
    return predicted, confidence


@lru_cache(maxsize=1)
def _compiled_keywords() -> list[tuple[str, list[re.Pattern]]]:
    """Compila as keywords de cada ticker como regex com limite de palavra (\\b).

    Limite de palavra evita falsos positivos por substring: "weg" não casa
    "norwegian", "mills" não casa "windmills", "6x1" casa só o token isolado.
    """
    compiled: list[tuple[str, list[re.Pattern]]] = []
    for ticker, info in COVERAGE_UNIVERSE.items():
        patterns = [
            re.compile(r"\b" + re.escape(kw.lower()) + r"\b")
            for kw in info["keywords"]
        ]
        compiled.append((ticker, patterns))
    return compiled


def _keyword_fallback(text: str) -> dict[str, float]:
    """Keyword matching contra COVERAGE_UNIVERSE com limite de palavra."""
    text_lower = text.lower()
    votes: dict[str, float] = {}
    for ticker, patterns in _compiled_keywords():
        hits = sum(1 for p in patterns if p.search(text_lower))
        if hits:
            votes[ticker] = float(hits)
    return votes
