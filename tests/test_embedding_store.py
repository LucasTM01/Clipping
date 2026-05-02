"""Unit tests for screener/embedding_store.py — Qdrant client is mocked."""

from datetime import datetime
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from screener.embedding_store import EmbeddingStore, HistoricalExample, _str_to_uint64


# ---------------------------------------------------------------------------
# _str_to_uint64 — pure function
# ---------------------------------------------------------------------------

class TestStrToUint64:
    def test_deterministic(self):
        assert _str_to_uint64("abc") == _str_to_uint64("abc")

    def test_different_inputs_differ(self):
        assert _str_to_uint64("url1_title1") != _str_to_uint64("url2_title2")

    def test_result_is_positive_integer(self):
        result = _str_to_uint64("test")
        assert isinstance(result, int)
        assert result >= 0

    def test_fits_uint64(self):
        result = _str_to_uint64("test")
        assert result < 2**64

    def test_empty_string_stable(self):
        assert _str_to_uint64("") == _str_to_uint64("")


# ---------------------------------------------------------------------------
# EmbeddingStore — Qdrant client mocked
# ---------------------------------------------------------------------------

def _make_store():
    """Returns an EmbeddingStore with a fully mocked QdrantClient."""
    with patch("screener.embedding_store.QdrantClient") as MockClient:
        mock_client = MagicMock()
        MockClient.return_value = mock_client
        mock_client.get_collections.return_value.collections = []
        store = EmbeddingStore()
        store._client = mock_client
        return store, mock_client


def _example(title="Test", tickers=None):
    return HistoricalExample(
        id="abc123",
        title=title,
        body="body text",
        embedding=np.ones(1024, dtype=np.float32),
        tickers=tickers or ["EMBR3"],
        sectors=["aerospace"],
        label="positive",
        published_at=datetime(2026, 1, 1),
        source="Valor",
    )


class TestEmbeddingStoreUpsert:
    def test_upsert_empty_returns_zero(self):
        store, mock_client = _make_store()
        result = store.upsert([])
        assert result == 0
        mock_client.upsert.assert_not_called()

    def test_upsert_returns_count(self):
        store, mock_client = _make_store()
        examples = [_example("A"), _example("B")]
        result = store.upsert(examples)
        assert result == 2

    def test_upsert_calls_qdrant_once(self):
        store, mock_client = _make_store()
        store.upsert([_example()])
        mock_client.upsert.assert_called_once()

    def test_upsert_payload_contains_title(self):
        store, mock_client = _make_store()
        store.upsert([_example("My Title")])
        call_args = mock_client.upsert.call_args
        points = call_args[1]["points"]
        assert points[0].payload["title"] == "My Title"

    def test_upsert_payload_contains_tickers(self):
        store, mock_client = _make_store()
        store.upsert([_example(tickers=["WEGE3"])])
        points = mock_client.upsert.call_args[1]["points"]
        assert points[0].payload["tickers"] == ["WEGE3"]

    def test_upsert_id_is_uint64(self):
        store, mock_client = _make_store()
        store.upsert([_example()])
        points = mock_client.upsert.call_args[1]["points"]
        assert isinstance(points[0].id, int)
        assert points[0].id >= 0

    def test_upsert_idempotent_same_id(self):
        store, mock_client = _make_store()
        ex1 = _example("Same")
        ex2 = _example("Same")
        ex1.id = "same_id"
        ex2.id = "same_id"
        store.upsert([ex1])
        store.upsert([ex2])
        all_ids = [
            call[1]["points"][0].id
            for call in mock_client.upsert.call_args_list
        ]
        assert all_ids[0] == all_ids[1]


class TestEmbeddingStoreSearchKnn:
    def _mock_result(self, payload, score):
        r = MagicMock()
        r.payload = payload
        r.score = score
        return r

    def test_search_returns_list_of_tuples(self):
        store, mock_client = _make_store()
        mock_client.query_points.return_value.points = [
            self._mock_result({"title": "A", "tickers": ["EMBR3"]}, 0.9)
        ]
        results = store.search_knn(np.ones(1024), k=5)
        assert len(results) == 1
        payload, score = results[0]
        assert payload["title"] == "A"
        assert score == 0.9

    def test_search_empty_results(self):
        store, mock_client = _make_store()
        mock_client.query_points.return_value.points = []
        results = store.search_knn(np.ones(1024), k=5)
        assert results == []

    def test_search_with_label_filter_passes_filter(self):
        store, mock_client = _make_store()
        mock_client.query_points.return_value.points = []
        store.search_knn(np.ones(1024), k=5, label_filter="positive")
        call_kwargs = mock_client.query_points.call_args[1]
        assert call_kwargs["query_filter"] is not None

    def test_search_without_label_filter_passes_none(self):
        store, mock_client = _make_store()
        mock_client.query_points.return_value.points = []
        store.search_knn(np.ones(1024), k=5, label_filter=None)
        call_kwargs = mock_client.query_points.call_args[1]
        assert call_kwargs["query_filter"] is None


class TestEmbeddingStoreCount:
    def test_count_delegates_to_client(self):
        store, mock_client = _make_store()
        mock_client.count.return_value.count = 42
        assert store.count() == 42
