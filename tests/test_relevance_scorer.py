"""Unit tests for screener/relevance_scorer.py — pure functions only, no Qdrant."""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from screener.relevance_scorer import (
    TIER_HIGH,
    TIER_LOW_REVIEW,
    TIER_MEDIUM,
    Candidate,
    RelevanceScorer,
    _assign_tier,
    _build_rationale,
    _compute_score,
)
from fetcher.rss_fetcher import Article


def _article(title="Test Article"):
    return Article(
        title=title,
        url="https://example.com/test",
        source="Test Source",
        published_at="2026-05-01T10:00:00",
        full_text="full text here",
    )


def _neighbors(*scores):
    return [({}, s) for s in scores]


def _neighbors_with_tickers(tickers, score):
    return [({"tickers": tickers, "title": "test"}, score)]


# ---------------------------------------------------------------------------
# _compute_score
# ---------------------------------------------------------------------------

class TestComputeScore:
    def test_empty_positives_returns_zero(self):
        assert _compute_score([], []) == 0.0

    def test_single_positive(self):
        assert _compute_score(_neighbors(0.8), []) == pytest.approx(0.8)

    def test_mean_of_positives(self):
        result = _compute_score(_neighbors(0.6, 0.8, 1.0), [])
        assert result == pytest.approx(0.8)

    def test_clamped_to_one(self):
        result = _compute_score(_neighbors(1.0, 1.0), [])
        assert result <= 1.0

    def test_clamped_to_zero(self):
        result = _compute_score(_neighbors(0.0, 0.0), [])
        assert result >= 0.0

    def test_with_negatives_margin_formula(self):
        # margin = (pos_mean - neg_mean + 1) / 2
        pos = _neighbors(0.8)
        neg = _neighbors(0.4)
        expected = (0.8 - 0.4 + 1.0) / 2.0
        assert _compute_score(pos, neg) == pytest.approx(expected)

    def test_with_negatives_higher_neg_lowers_score(self):
        pos = _neighbors(0.7)
        neg_low = _neighbors(0.2)
        neg_high = _neighbors(0.6)
        score_low_neg = _compute_score(pos, neg_low)
        score_high_neg = _compute_score(pos, neg_high)
        assert score_high_neg < score_low_neg

    def test_with_negatives_result_clamped(self):
        pos = _neighbors(0.0)
        neg = _neighbors(1.0)
        result = _compute_score(pos, neg)
        assert 0.0 <= result <= 1.0


# ---------------------------------------------------------------------------
# _assign_tier
# ---------------------------------------------------------------------------

class TestAssignTier:
    THRESHOLD = 0.5446  # calibration threshold

    def test_high_at_boundary(self):
        assert _assign_tier(TIER_HIGH, self.THRESHOLD) == "high"

    def test_high_above_boundary(self):
        assert _assign_tier(0.95, self.THRESHOLD) == "high"

    def test_medium_just_below_high(self):
        assert _assign_tier(TIER_HIGH - 0.001, self.THRESHOLD) == "medium"

    def test_medium_at_boundary(self):
        assert _assign_tier(TIER_MEDIUM, self.THRESHOLD) == "medium"

    def test_low_review_just_below_medium(self):
        result = _assign_tier(TIER_MEDIUM - 0.001, self.THRESHOLD)
        assert result == "low_review"

    def test_low_review_at_threshold(self):
        assert _assign_tier(self.THRESHOLD, self.THRESHOLD) == "low_review"

    def test_discard_just_below_threshold(self):
        assert _assign_tier(self.THRESHOLD - 0.001, self.THRESHOLD) == "discard"

    def test_discard_zero(self):
        assert _assign_tier(0.0, self.THRESHOLD) == "discard"

    def test_discard_threshold_equals_tier_low_review(self):
        # When no calibration, discard_threshold = TIER_LOW_REVIEW
        assert _assign_tier(TIER_LOW_REVIEW, TIER_LOW_REVIEW) == "low_review"
        assert _assign_tier(TIER_LOW_REVIEW - 0.001, TIER_LOW_REVIEW) == "discard"


# ---------------------------------------------------------------------------
# _build_rationale
# ---------------------------------------------------------------------------

class TestBuildRationale:
    def test_contains_score_and_tier(self):
        rationale = _build_rationale(0.75, "medium", [])
        assert "0.750" in rationale
        assert "medium" in rationale

    def test_empty_top3(self):
        rationale = _build_rationale(0.5, "low_review", [])
        assert "top3:" in rationale

    def test_top3_titles_included(self):
        top3 = [({"title": "Embraer reporta lucro recorde"}, 0.9)]
        rationale = _build_rationale(0.9, "high", top3)
        assert "Embraer" in rationale
        assert "0.900" in rationale


# ---------------------------------------------------------------------------
# RelevanceScorer.score — mocked store
# ---------------------------------------------------------------------------

class TestRelevanceScorerScore:
    def _make_scorer(self, discard_threshold=0.5446):
        mock_store = MagicMock()
        scorer = RelevanceScorer.__new__(RelevanceScorer)
        scorer._store = mock_store
        scorer._calibration = {"threshold": discard_threshold, "k": 5}
        scorer._discard_threshold = discard_threshold
        return scorer, mock_store

    def test_score_returns_scored_article(self):
        scorer, _ = self._make_scorer()
        candidate = Candidate(
            article=_article(),
            embedding=np.zeros(1024),
            nearest_positives=_neighbors(0.8, 0.75, 0.7),
        )
        result = scorer.score(candidate)
        assert result.relevance_score == pytest.approx(0.75)
        assert result.tier == "medium"
        assert result.article.title == "Test Article"

    def test_score_high_tier(self):
        scorer, _ = self._make_scorer()
        candidate = Candidate(
            article=_article(),
            embedding=np.zeros(1024),
            nearest_positives=_neighbors(0.9, 0.9, 0.9),
        )
        result = scorer.score(candidate)
        assert result.tier == "high"

    def test_score_discard_tier(self):
        scorer, _ = self._make_scorer(discard_threshold=0.5446)
        candidate = Candidate(
            article=_article(),
            embedding=np.zeros(1024),
            nearest_positives=_neighbors(0.3, 0.3),
        )
        result = scorer.score(candidate)
        assert result.tier == "discard"

    def test_score_no_positives_is_discard(self):
        scorer, _ = self._make_scorer()
        candidate = Candidate(
            article=_article(),
            embedding=np.zeros(1024),
            nearest_positives=[],
        )
        result = scorer.score(candidate)
        assert result.relevance_score == 0.0
        assert result.tier == "discard"

    def test_build_candidate_uses_calibration_k(self):
        scorer, mock_store = self._make_scorer()
        mock_store.search_knn.return_value = _neighbors(0.8)
        emb = np.ones(1024)
        candidate = scorer.build_candidate(_article(), emb)
        mock_store.search_knn.assert_called_once_with(emb, k=5, label_filter="positive")
        assert len(candidate.nearest_positives) == 1
