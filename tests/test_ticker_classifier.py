"""Unit tests for screener/ticker_classifier.py — no external deps needed."""

import pytest
from screener.ticker_classifier import classify_tickers, _keyword_fallback


def _neighbors(*pairs):
    """Helper: list of (payload, score) from (tickers, score) pairs."""
    return [
        ({"tickers": list(tickers), "title": "test"}, score)
        for tickers, score in pairs
    ]


# ---------------------------------------------------------------------------
# classify_tickers — voting logic
# ---------------------------------------------------------------------------

class TestClassifyTickers:
    def test_empty_neighbors_no_text_returns_empty(self):
        tickers, conf = classify_tickers([])
        assert tickers == []
        assert conf == {}

    def test_single_neighbor_single_ticker(self):
        neighbors = _neighbors((["EMBR3"], 0.9))
        tickers, conf = classify_tickers(neighbors)
        assert tickers == ["EMBR3"]
        assert conf["EMBR3"] == pytest.approx(1.0)

    def test_dominant_ticker_wins(self):
        neighbors = _neighbors(
            (["EMBR3"], 0.9),
            (["EMBR3"], 0.8),
            (["WEGE3"], 0.5),
        )
        tickers, conf = classify_tickers(neighbors)
        assert tickers[0] == "EMBR3"
        assert conf["EMBR3"] > conf["WEGE3"]

    def test_macro_neighbors_contribute_no_votes(self):
        """Neighbors with tickers=[] should not produce any ticker."""
        neighbors = _neighbors(
            ([], 0.9),
            ([], 0.8),
        )
        tickers, conf = classify_tickers(neighbors, article_text="")
        assert tickers == []

    def test_min_confidence_filters_weak_tickers(self):
        neighbors = _neighbors(
            (["EMBR3"], 0.9),
            (["EMBR3"], 0.9),
            (["WEGE3"], 0.01),   # very weak
        )
        tickers, conf = classify_tickers(neighbors, min_confidence=0.15)
        assert "WEGE3" not in tickers
        assert "EMBR3" in tickers

    def test_confidence_normalized_by_score_sum(self):
        # score_sum = 1.0 + 0.5 = 1.5
        # EMBR3 confidence = 1.0 / 1.5 ≈ 0.667
        # WEGE3 confidence = 0.5 / 1.5 ≈ 0.333
        neighbors = _neighbors(
            (["EMBR3"], 1.0),
            (["WEGE3"], 0.5),
        )
        _, conf = classify_tickers(neighbors)
        assert conf["EMBR3"] == pytest.approx(1.0 / 1.5)
        assert conf["WEGE3"] == pytest.approx(0.5 / 1.5)

    def test_multiple_tickers_per_neighbor(self):
        neighbors = [
            ({"tickers": ["RAPT4", "FRAS3"], "title": "test"}, 0.8)
        ]
        tickers, conf = classify_tickers(neighbors)
        assert "RAPT4" in tickers
        assert "FRAS3" in tickers

    def test_returns_sorted_descending_by_confidence(self):
        neighbors = _neighbors(
            (["WEGE3"], 0.6),
            (["EMBR3"], 0.9),
            (["EMBR3"], 0.9),
        )
        tickers, _ = classify_tickers(neighbors)
        assert tickers[0] == "EMBR3"

    def test_fallback_to_keywords_when_no_ticker_votes(self):
        neighbors = _neighbors(([], 0.9))
        tickers, conf = classify_tickers(neighbors, article_text="Embraer aviação comercial")
        assert "EMBR3" in tickers

    # ── Keyword-gating tests ─────────────────────────────────────────────────

    def test_keyword_gates_wrong_knn_ticker(self):
        """Keywords should override wrong kNN votes when text explicitly names a company."""
        # kNN votes EMBR3 (wrong — neighbors are ambiguous industrial articles)
        # but text explicitly mentions Marcopolo/ônibus → POMO4 should win
        neighbors = _neighbors(
            (["EMBR3"], 0.9),
            (["EMBR3"], 0.8),
            (["EMBR3"], 0.7),
        )
        tickers, conf = classify_tickers(
            neighbors,
            article_text="Marcopolo anuncia novos ônibus articulados para BRT",
        )
        assert "POMO4" in tickers    # keyword gate brings POMO4
        assert "EMBR3" not in tickers  # wrong kNN suppressed by gating

    def test_knn_confirmed_by_keyword(self):
        """When kNN and keyword agree on a ticker, filtered kNN is returned."""
        # EMBR3 appears in both kNN votes and keyword match; WEGE3 only in kNN
        neighbors = _neighbors(
            (["EMBR3"], 0.9),
            (["WEGE3"], 0.5),
        )
        tickers, conf = classify_tickers(
            neighbors,
            article_text="Embraer anuncia novo C-390 para defesa aérea",
        )
        assert tickers[0] == "EMBR3"   # correct — kNN+keyword both confirm
        assert "WEGE3" not in tickers  # kNN-only, not confirmed by keyword

    def test_macro_article_uses_knn_pure_when_no_keyword_match(self):
        """Macro/sectoral article without company names should use kNN pure."""
        neighbors = _neighbors(
            (["WEGE3"], 0.8),
            (["EMBR3"], 0.5),
        )
        tickers, conf = classify_tickers(
            neighbors,
            article_text="Setor industrial apresenta crescimento no primeiro trimestre",
        )
        # kNN pure — no keyword signal to filter with
        assert "WEGE3" in tickers  # top kNN vote preserved


# ---------------------------------------------------------------------------
# _keyword_fallback
# ---------------------------------------------------------------------------

class TestKeywordFallback:
    def test_known_ticker_keyword_match(self):
        votes = _keyword_fallback("Embraer anuncia novos contratos")
        assert "EMBR3" in votes

    def test_case_insensitive(self):
        votes = _keyword_fallback("EMBRAER aviação")
        assert "EMBR3" in votes

    def test_no_match_returns_empty(self):
        votes = _keyword_fallback("noticia completamente irrelevante sobre futebol")
        assert votes == {}

    def test_multiple_tickers_detected(self):
        votes = _keyword_fallback("WEG e Marcopolo anunciam parceria")
        assert "WEGE3" in votes
        assert "POMO4" in votes

    def test_more_keyword_hits_means_higher_vote(self):
        votes = _keyword_fallback("Embraer Embraer aviação Embraer")
        assert votes.get("EMBR3", 0) >= 1
