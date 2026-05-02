"""
LOO-CV no dataset positivo para calibrar o threshold de relevância.

Para cada ponto no Qdrant, consulta k+1 vizinhos usando o próprio vetor
armazenado (sem recarregar o modelo), descarta o self-match, e computa
o score LOO. O threshold é o percentil P5 dos scores — garantindo que
>=95% dos positivos conhecidos passem.

Salva em config/calibration.json:
{
  "threshold": float,
  "recall_at_threshold": float,
  "k": int,
  "n_train": int,
  "p5_score": float,
  "p50_score": float
}
"""

import json
import logging
import sys
from pathlib import Path

import numpy as np

from screener.embedding_store import EmbeddingStore
from config.settings import settings

CALIBRATION_PATH = Path(__file__).parent.parent / "config" / "calibration.json"
log = logging.getLogger(__name__)


def _loo_score(
    store: EmbeddingStore,
    point_id: int,
    original_id: str,
    k: int,
) -> float | None:
    """
    Score LOO de um ponto: busca k+1 vizinhos usando o vetor armazenado,
    remove o self-match pelo original_id, retorna média dos k restantes.
    """
    results = store._client.query_points(
        collection_name=settings.qdrant_collection,
        query=point_id,           # Qdrant usa o vetor armazenado do ponto
        limit=k + 2,              # +2 para garantir k após remover self e possíveis ties
        with_payload=True,
    )
    neighbors = [
        (p.payload, p.score)
        for p in results.points
        if p.payload.get("original_id") != original_id
    ][:k]

    if not neighbors:
        return None
    return float(np.mean([s for _, s in neighbors]))


def calibrate(k: int | None = None, target_recall: float = 0.95) -> dict:
    k = k or settings.embedding_top_k
    store = EmbeddingStore()
    n_total = store.count()

    if n_total == 0:
        raise RuntimeError("Qdrant está vazio — rode data/ingest_history.py primeiro.")

    log.info(f"Calibrando threshold com LOO-CV em {n_total} pontos (k={k})...")

    # Paginar todos os pontos para obter IDs e original_ids
    all_points: list[tuple[int, str]] = []  # (qdrant_id, original_id)
    offset = None
    while True:
        batch, next_offset = store._client.scroll(
            collection_name=settings.qdrant_collection,
            limit=100,
            offset=offset,
            with_payload=["original_id"],
            with_vectors=False,
        )
        all_points.extend((p.id, p.payload.get("original_id", "")) for p in batch)
        if next_offset is None:
            break
        offset = next_offset

    log.info(f"Coletados {len(all_points)} pontos. Iniciando LOO-CV...")

    loo_scores: list[float] = []
    failed = 0
    for i, (point_id, original_id) in enumerate(all_points):
        score = _loo_score(store, point_id, original_id, k)
        if score is None:
            failed += 1
            continue
        loo_scores.append(score)
        if (i + 1) % 50 == 0:
            log.info(f"  {i+1}/{len(all_points)} processados...")

    if not loo_scores:
        raise RuntimeError("Nenhum score LOO calculado.")

    scores_arr = np.array(loo_scores)
    threshold = float(np.percentile(scores_arr, (1 - target_recall) * 100))
    recall = float(np.mean(scores_arr >= threshold))

    result = {
        "threshold": round(threshold, 4),
        "recall_at_threshold": round(recall, 4),
        "k": k,
        "n_train": len(loo_scores),
        "p5_score": round(float(np.percentile(scores_arr, 5)), 4),
        "p50_score": round(float(np.percentile(scores_arr, 50)), 4),
        "p95_score": round(float(np.percentile(scores_arr, 95)), 4),
        "failed_points": failed,
    }

    CALIBRATION_PATH.parent.mkdir(exist_ok=True)
    with open(CALIBRATION_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)

    log.info(
        f"Calibration: threshold={threshold:.4f} | recall={recall:.1%} | "
        f"p50={result['p50_score']:.4f} | saved to {CALIBRATION_PATH}"
    )
    return result


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        stream=sys.stdout,
    )
    result = calibrate()
    print(json.dumps(result, indent=2))
