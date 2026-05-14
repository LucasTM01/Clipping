"""
Treina um classificador LogisticRegression sobre os embeddings do Qdrant.

Uso:
    python -m screener.train_classifier

Salva:
    models/screener_clf.joblib        — modelo sklearn
    models/screener_clf_meta.json     — métricas de CV + metadados
"""

import json
import logging
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

MODELS_DIR = Path(__file__).parent.parent / "models"


def train() -> dict:
    try:
        import joblib
        from sklearn.linear_model import LogisticRegression
        from sklearn.model_selection import StratifiedKFold, cross_validate
        from sklearn.metrics import make_scorer, roc_auc_score
    except ImportError:
        log.error("scikit-learn e joblib são necessários: pip install scikit-learn joblib")
        sys.exit(1)

    from screener.embedding_store import EmbeddingStore

    log.info("Buscando embeddings do Qdrant (scroll completo)...")
    store = EmbeddingStore()
    all_points = store.scroll_all()

    if not all_points:
        log.error("Qdrant está vazio. Rode python -m data.ingest_history primeiro.")
        sys.exit(1)

    X_list, y_list = [], []
    for vec, label in all_points:
        X_list.append(vec)
        y_list.append(1 if label == "positive" else 0)

    X = np.array(X_list, dtype=np.float32)
    y = np.array(y_list, dtype=np.int32)

    n_pos = int(y.sum())
    n_neg = int((y == 0).sum())
    log.info(f"Dataset: {n_pos} positivos, {n_neg} negativos ({len(y)} total)")

    if n_neg < 10:
        log.error(f"Apenas {n_neg} negativos — insuficiente para treinar. "
                  "Revise near-misses no Streamlit e rode ingest_history.")
        sys.exit(1)

    clf = LogisticRegression(
        class_weight="balanced",
        max_iter=1000,
        C=1.0,
        solver="lbfgs",
        n_jobs=-1,
    )

    log.info("Avaliando com StratifiedKFold (k=5)...")
    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    scoring = {
        "precision": "precision",
        "recall": "recall",
        "f1": "f1",
        "roc_auc": "roc_auc",
    }
    cv_results = cross_validate(clf, X, y, cv=cv, scoring=scoring, return_train_score=False)

    metrics = {
        "precision": float(np.mean(cv_results["test_precision"])),
        "recall": float(np.mean(cv_results["test_recall"])),
        "f1": float(np.mean(cv_results["test_f1"])),
        "roc_auc": float(np.mean(cv_results["test_roc_auc"])),
    }

    log.info(
        f"CV médio — Precision: {metrics['precision']:.3f} | Recall: {metrics['recall']:.3f} "
        f"| F1: {metrics['f1']:.3f} | AUC-ROC: {metrics['roc_auc']:.3f}"
    )

    log.info("Treinando modelo final em todo o dataset...")
    clf.fit(X, y)

    MODELS_DIR.mkdir(exist_ok=True)
    model_path = MODELS_DIR / "screener_clf.joblib"
    joblib.dump(clf, model_path)

    meta = {
        "trained_at": datetime.now().isoformat(),
        "n_positive": n_pos,
        "n_negative": n_neg,
        "metrics_cv": metrics,
        "clf_threshold": 0.5,
        "model_params": clf.get_params(),
    }
    meta_path = MODELS_DIR / "screener_clf_meta.json"
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)

    log.info(f"Modelo salvo em {model_path}")
    log.info(f"Metadados salvos em {meta_path}")
    return meta


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        stream=sys.stdout,
    )
    result = train()
    print("\nResumo:")
    print(f"  Positivos:  {result['n_positive']}")
    print(f"  Negativos:  {result['n_negative']}")
    print(f"  F1 (CV):    {result['metrics_cv']['f1']:.3f}")
    print(f"  AUC-ROC:    {result['metrics_cv']['roc_auc']:.3f}")
    print(f"  Precision:  {result['metrics_cv']['precision']:.3f}")
    print(f"  Recall:     {result['metrics_cv']['recall']:.3f}")
