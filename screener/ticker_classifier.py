"""
Atribui tickers a um artigo via votação ponderada dos top-k vizinhos positivos.

Fase 1: só positivos no índice. Tickers são votados pelos vizinhos que têm
ticker no payload. Vizinhos macro/setoriais (tickers=[]) contribuem 0 votos
mas confirmam relevância setorial.
"""

from config.settings import COVERAGE_UNIVERSE


def classify_tickers(
    nearest_positives: list[tuple[dict, float]],
    article_text: str = "",
    min_confidence: float = 0.15,
) -> tuple[list[str], dict[str, float]]:
    """
    Retorna (predicted_tickers, ticker_confidence_dict).

    predicted_tickers: tickers com confidence >= min_confidence, ordenados desc.
    ticker_confidence_dict: score normalizado [0,1] para cada ticker que apareceu.

    Fallback: se nenhum vizinho tem ticker, tenta keyword matching contra
    COVERAGE_UNIVERSE usando article_text.
    """
    vote_totals: dict[str, float] = {}
    score_sum = sum(score for _, score in nearest_positives)

    for payload, score in nearest_positives:
        for ticker in payload.get("tickers", []):
            if ticker:
                vote_totals[ticker] = vote_totals.get(ticker, 0.0) + score

    if not vote_totals and article_text:
        vote_totals = _keyword_fallback(article_text)

    if not vote_totals:
        return [], {}

    max_vote = max(vote_totals.values())
    confidence = {t: v / max_vote for t, v in vote_totals.items()}

    predicted = sorted(
        [t for t, c in confidence.items() if c >= min_confidence],
        key=lambda t: confidence[t],
        reverse=True,
    )
    return predicted, confidence


def _keyword_fallback(text: str) -> dict[str, float]:
    """Keyword matching contra COVERAGE_UNIVERSE como fallback."""
    text_lower = text.lower()
    votes: dict[str, float] = {}
    for ticker, info in COVERAGE_UNIVERSE.items():
        hits = sum(1 for kw in info["keywords"] if kw.lower() in text_lower)
        if hits:
            votes[ticker] = float(hits)
    return votes
