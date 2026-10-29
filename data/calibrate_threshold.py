"""
LOO-CV no dataset positivo para calibrar o threshold e os tier boundaries.

Para cada ponto POSITIVO no Qdrant:
  1. Busca k+2 vizinhos POSITIVOS usando o vetor armazenado (Qdrant faz o ANN).
  2. Remove o self-match pelo original_id -> top-k positivos = LOO positive neighbors.
  3. Busca k_neg vizinhos NEGATIVOS (sem exclusao de self).
  4. Aplica a mesma formula de _compute_score:
       score = (pos_mean - neg_mean + 1) / 2   (se houver negativos)
               pos_mean                          (fallback)

O threshold e P5 dos scores LOO (>=95% de recall nos positivos de treino).
Tier boundaries sao calibradas proporcionalmente a distribuicao real:
  - tier_medium = P25 (bottom 20% dos positivos ficam em low_review)
  - tier_high   = P75 (top 25% dos positivos ficam em high)

Salva em config/calibration.json:
{
  "threshold":   float,   # P5  -- discard boundary
  "tier_medium": float,   # P25 -- low_review/medium boundary
  "tier_high":   float,   # P75 -- medium/high boundary
  "recall_at_threshold": float,
  "k":     int,
  "k_neg": int,
  "n_train": int,
  "p5_score":  float,
  "p25_score": float,
  "p50_score": float,
  "p75_score": float,
  "p95_score": float,
  "failed_points": int
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


def _loo_score_margin(
    client,
    collection: str,
    point_id: int,
    original_id: str,
    k_pos: int,
    k_neg: int,
    pos_filter,
    neg_filter,
) -> float | None:
    """
    Computa o score LOO de um ponto positivo usando a mesma formula de _compute_score:
      (pos_mean - neg_mean + 1) / 2   quando ha negativos
       pos_mean                        caso contrario

    Usa o vetor armazenado no Qdrant (query by point_id) para evitar recarregar o modelo.
    """
    # Positivos: exclui o proprio ponto pelo original_id
    pos_results = client.query_points(
        collection_name=collection,
        query=point_id,
        limit=k_pos + 2,        # margem para remover self + possiveis ties
        with_payload=True,
        query_filter=pos_filter,
    )
    pos_neighbors = [
        p.score
        for p in pos_results.points
        if p.payload.get("original_id") != original_id
    ][:k_pos]

    if not pos_neighbors:
        return None

    pos_mean = float(np.mean(pos_neighbors))

    # Negativos (sem exclusao de self -- nunca e um ponto positivo)
    if k_neg > 0:
        neg_results = client.query_points(
            collection_name=collection,
            query=point_id,
            limit=k_neg,
            with_payload=False,
            query_filter=neg_filter,
        )
        if neg_results.points:
            neg_mean = float(np.mean([p.score for p in neg_results.points]))
            return min(max((pos_mean - neg_mean + 1.0) / 2.0, 0.0), 1.0)

    # Fallback: sem negativos no Qdrant
    return min(max(pos_mean, 0.0), 1.0)


def calibrate(
    k: int | None = None,
    k_neg: int | None = None,
    target_recall: float = 0.95,
) -> dict:
    """
    Calibra threshold e tier boundaries por LOO-CV nos positivos do Qdrant.

    Args:
        k: Vizinhos positivos (default: settings.embedding_top_k).
        k_neg: Vizinhos negativos (default: settings.embedding_top_k_neg).
        target_recall: Fracao de positivos que deve passar o threshold (default 0.95).
    """
    from qdrant_client.models import Filter, FieldCondition, MatchValue

    k     = k     or settings.embedding_top_k
    k_neg = k_neg or settings.embedding_top_k_neg

    store = EmbeddingStore()
    client = store._client
    collection = settings.qdrant_collection

    n_total = store.count()
    if n_total == 0:
        raise RuntimeError("Qdrant esta vazio -- rode data/ingest_history.py primeiro.")

    pos_filter = Filter(must=[FieldCondition(key="label", match=MatchValue(value="positive"))])
    neg_filter = Filter(must=[FieldCondition(key="label", match=MatchValue(value="negative"))])

    # Conta negativos disponiveis para mensagem de log informativa
    neg_count = client.count(
        collection_name=collection,
        count_filter=neg_filter,
        exact=True,
    ).count

    log.info(
        f"Calibrando com LOO-CV (k={k}, k_neg={k_neg}) | "
        f"total={n_total}, negativos={neg_count}"
    )
    if neg_count == 0:
        log.warning(
            "Nenhum negativo no Qdrant -- calibracao usa apenas positivos (pos-only scoring). "
            "Ingira exemplos negativos via data/ingest_history.py para usar margin scoring."
        )

    # Paginar APENAS pontos positivos
    all_pos_points: list[tuple[int, str]] = []  # (qdrant_id, original_id)
    offset = None
    while True:
        batch, next_offset = client.scroll(
            collection_name=collection,
            limit=200,
            offset=offset,
            with_payload=["original_id"],
            with_vectors=False,
            scroll_filter=pos_filter,
        )
        all_pos_points.extend(
            (p.id, p.payload.get("original_id", "")) for p in batch
        )
        if next_offset is None:
            break
        offset = next_offset

    n_positives = len(all_pos_points)
    log.info(f"Positivos para LOO-CV: {n_positives}. Iniciando scoring...")

    loo_scores: list[float] = []
    failed = 0

    for i, (point_id, original_id) in enumerate(all_pos_points):
        score = _loo_score_margin(
            client, collection, point_id, original_id,
            k_pos=k, k_neg=k_neg,
            pos_filter=pos_filter, neg_filter=neg_filter,
        )
        if score is None:
            failed += 1
            continue
        loo_scores.append(score)
        if (i + 1) % 100 == 0:
            log.info(f"  {i+1}/{n_positives} processados...")

    if not loo_scores:
        raise RuntimeError("Nenhum score LOO calculado.")

    arr = np.array(loo_scores)

    # Threshold: percentil = (1 - target_recall) * 100
    p_pct     = (1.0 - target_recall) * 100.0   # 5.0 para recall=0.95
    threshold = float(np.percentile(arr, p_pct))
    recall    = float(np.mean(arr >= threshold))

    # Tier boundaries derivadas da distribuicao real dos positivos
    p25 = float(np.percentile(arr, 25))
    p50 = float(np.percentile(arr, 50))
    p75 = float(np.percentile(arr, 75))
    p95 = float(np.percentile(arr, 95))

    # Garantir que boundaries respeitem a ordenacao
    tier_medium = max(p25, threshold)   # nunca abaixo do threshold
    tier_high   = max(p75, tier_medium) # nunca abaixo de tier_medium

    result = {
        "threshold":           round(threshold, 4),
        "tier_medium":         round(tier_medium, 4),
        "tier_high":           round(tier_high, 4),
        "recall_at_threshold": round(recall, 4),
        "k":                   k,
        "k_neg":               k_neg,
        "n_train":             len(loo_scores),
        "p5_score":            round(float(np.percentile(arr, 5)), 4),
        "p25_score":           round(p25, 4),
        "p50_score":           round(p50, 4),
        "p75_score":           round(p75, 4),
        "p95_score":           round(p95, 4),
        "failed_points":       failed,
    }

    CALIBRATION_PATH.parent.mkdir(exist_ok=True)
    with open(CALIBRATION_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)

    log.info(
        f"Calibration OK: threshold={threshold:.4f} recall={recall:.1%} | "
        f"tier_medium={tier_medium:.4f} tier_high={tier_high:.4f} | "
        f"p50={p50:.4f} p95={p95:.4f} | failed={failed} | "
        f"saved -> {CALIBRATION_PATH}"
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
