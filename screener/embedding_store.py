"""
Wrapper sobre Qdrant para o histórico de notícias labeladas.
"""

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import numpy as np
from qdrant_client import QdrantClient, models as qmodels
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
    VectorParams,
)

from config.settings import settings

VECTOR_SIZE = 1024  # BGE-M3 output dimensionality


@dataclass
class HistoricalExample:
    id: str                             # hash estável de url + título
    title: str
    body: str | None                    # None quando artigo só tinha título
    embedding: np.ndarray               # shape (1024,)
    tickers: list[str]                  # [] para macro/setoriais sem ticker
    sectors: list[str]
    label: Literal["positive", "negative"]
    published_at: datetime | None
    source: str


class EmbeddingStore:
    def __init__(self) -> None:
        self._client = QdrantClient(
            host=settings.qdrant_host,
            port=settings.qdrant_port,
        )
        self._ensure_collection()

    def _ensure_collection(self) -> None:
        existing = {c.name for c in self._client.get_collections().collections}
        if settings.qdrant_collection not in existing:
            self._client.create_collection(
                collection_name=settings.qdrant_collection,
                vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
            )

    _UPSERT_BATCH_SIZE = 256

    def upsert(self, examples: list[HistoricalExample]) -> int:
        if not examples:
            return 0
        points = [
            PointStruct(
                id=_str_to_uint64(ex.id),
                vector=ex.embedding.tolist(),
                payload={
                    "title": ex.title,
                    "body": ex.body,
                    "tickers": ex.tickers,
                    "sectors": ex.sectors,
                    "label": ex.label,
                    "published_at": ex.published_at.isoformat() if ex.published_at else None,
                    "source": ex.source,
                    "original_id": ex.id,
                },
            )
            for ex in examples
        ]
        for i in range(0, len(points), self._UPSERT_BATCH_SIZE):
            self._client.upsert(
                collection_name=settings.qdrant_collection,
                points=points[i : i + self._UPSERT_BATCH_SIZE],
            )
        return len(points)

    def search_knn(
        self,
        embedding: np.ndarray,
        k: int,
        label_filter: Literal["positive", "negative"] | None = None,
    ) -> list[tuple[dict, float]]:
        """Retorna lista de (payload, cosine_score) dos k vizinhos mais próximos."""
        query_filter = None
        if label_filter:
            query_filter = Filter(
                must=[FieldCondition(key="label", match=MatchValue(value=label_filter))]
            )
        results = self._client.query_points(
            collection_name=settings.qdrant_collection,
            query=embedding.tolist(),
            limit=k,
            query_filter=query_filter,
            with_payload=True,
        )
        return [(r.payload, r.score) for r in results.points]

    def count(self) -> int:
        return self._client.count(collection_name=settings.qdrant_collection).count


def _str_to_uint64(s: str) -> int:
    """Converte string ID em inteiro uint64 para o Qdrant."""
    return int(hashlib.sha256(s.encode()).hexdigest()[:16], 16)
