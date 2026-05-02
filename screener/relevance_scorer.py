"""
Novelty detection one-class para triagem de artigos.

Fase 1 (só positivos): score = média de similaridade cosseno aos top-k
vizinhos positivos. Threshold calibrado por LOO-CV em calibrate_threshold.py.

Fase 2 (com negativos): trocar _compute_score() para classificação binária
supervisionada sem mudar as interfaces externas.
"""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import numpy as np

from fetcher.rss_fetcher import Article
from screener.embedding_store import EmbeddingStore
from screener.ticker_classifier import classify_tickers
from config.settings import settings

log = logging.getLogger(__name__)

CALIBRATION_PATH = Path(__file__).parent.parent / "config" / "calibration.json"

# Tier boundaries fixas; o threshold calibrado substitui o limite inferior de discard
TIER_HIGH = 0.85
TIER_MEDIUM = 0.55
TIER_LOW_REVIEW = 0.40  # substituído por calibration["threshold"] quando disponível


@dataclass
class Candidate:
    article: Article
    embedding: np.ndarray
    nearest_positives: list[tuple[dict, float]] = field(default_factory=list)
    nearest_negatives: list[tuple[dict, float]] = field(default_factory=list)  # fase 2


@dataclass
class ScoredArticle:
    article: Article
    relevance_score: float
    predicted_tickers: list[str]
    ticker_confidence: dict[str, float]
    tier: Literal["high", "medium", "low_review", "discard"]
    rationale: str


class RelevanceScorer:
    def __init__(self, store: EmbeddingStore | None = None) -> None:
        self._store = store or EmbeddingStore()
        self._calibration = _load_calibration()
        self._discard_threshold = (
            self._calibration.get("threshold", TIER_LOW_REVIEW)
            if self._calibration
            else TIER_LOW_REVIEW
        )
        log.info(f"RelevanceScorer: discard_threshold={self._discard_threshold:.3f} "
                 f"(k={self._calibration.get('k', settings.embedding_top_k) if self._calibration else settings.embedding_top_k})")

    def score(self, candidate: Candidate) -> ScoredArticle:
        relevance_score = _compute_score(
            candidate.nearest_positives,
            candidate.nearest_negatives,
        )
        predicted_tickers, ticker_confidence = classify_tickers(
            candidate.nearest_positives,
            article_text=candidate.article.title + " " + (candidate.article.full_text or ""),
        )
        tier = _assign_tier(relevance_score, self._discard_threshold)
        rationale = _build_rationale(
            relevance_score, tier, candidate.nearest_positives[:3]
        )
        return ScoredArticle(
            article=candidate.article,
            relevance_score=relevance_score,
            predicted_tickers=predicted_tickers,
            ticker_confidence=ticker_confidence,
            tier=tier,
            rationale=rationale,
        )

    def build_candidate(self, article: Article, embedding: np.ndarray) -> Candidate:
        k = self._calibration.get("k", settings.embedding_top_k) if self._calibration else settings.embedding_top_k
        nearest_positives = self._store.search_knn(embedding, k=k, label_filter="positive")
        return Candidate(
            article=article,
            embedding=embedding,
            nearest_positives=nearest_positives,
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _compute_score(
    nearest_positives: list[tuple[dict, float]],
    nearest_negatives: list[tuple[dict, float]],
) -> float:
    """
    Fase 1: média da similaridade cosseno aos top-k positivos.
    Fase 2: substituir por margem (mean_pos - mean_neg) mapeada para [0,1].
    """
    if not nearest_positives:
        return 0.0
    pos_scores = [s for _, s in nearest_positives]
    base = float(np.mean(pos_scores))

    if nearest_negatives:
        neg_scores = [s for _, s in nearest_negatives]
        neg_mean = float(np.mean(neg_scores))
        # margem normalizada: (pos - neg + 1) / 2
        base = (base - neg_mean + 1.0) / 2.0

    return min(max(base, 0.0), 1.0)


def _assign_tier(
    score: float,
    discard_threshold: float,
) -> Literal["high", "medium", "low_review", "discard"]:
    if score < discard_threshold:
        return "discard"
    if score >= TIER_HIGH:
        return "high"
    if score >= TIER_MEDIUM:
        return "medium"
    return "low_review"


def _build_rationale(
    score: float,
    tier: str,
    top3: list[tuple[dict, float]],
) -> str:
    neighbors = "; ".join(
        f"[{s:.3f}] {p.get('title', '')[:60]}" for p, s in top3
    )
    return f"score={score:.3f} tier={tier} | top3: {neighbors}"


def _load_calibration() -> dict | None:
    if CALIBRATION_PATH.exists():
        with open(CALIBRATION_PATH, encoding="utf-8") as f:
            return json.load(f)
    log.warning(
        "config/calibration.json not found — using default discard threshold "
        f"({TIER_LOW_REVIEW}). Run data/calibrate_threshold.py to calibrate."
    )
    return None
