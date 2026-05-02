"""
ETL: news_dataset/clipping_enriched_dummy.jsonl + feedback/labeled/*.jsonl -> Qdrant.
Idempotente: usa SHA-256(url + title) como ID estável.

Fontes:
  - news_dataset/clipping_enriched_dummy.jsonl  -> positivos (relevant_yes_no=true)
  - feedback/labeled/*.jsonl                    -> positivos e negativos revisados
    (_reviewed=true e relevant_yes_no=true|false)
"""

import hashlib
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

import torch

from config.settings import settings
from screener.embedding_store import EmbeddingStore, HistoricalExample

JSONL_PATH = Path(__file__).parent.parent / "news_dataset" / "clipping_enriched.jsonl"
FEEDBACK_LABELED_DIR = Path(__file__).parent.parent / "feedback" / "labeled"
log = logging.getLogger(__name__)


def _make_text(record: dict) -> str:
    body = (record.get("body") or "").strip()
    title = record.get("title", "")
    if body:
        return f"{title}\n\n{body[:4000]}"
    return title


def _record_id(record: dict) -> str:
    key = (record.get("url", "") + record.get("title", "")).encode()
    return hashlib.sha256(key).hexdigest()[:32]


def _parse_date(date_str: str | None) -> datetime | None:
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str, "%d/%m/%Y")
    except ValueError:
        return None


def _load_main_dataset(jsonl_path: Path) -> list[dict]:
    """Carrega positivos do dataset principal."""
    records = []
    with open(jsonl_path, encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            if r.get("relevant_yes_no") is True:
                r["_label"] = "positive"
                records.append(r)
    return records


def _load_feedback_labeled() -> list[dict]:
    """Carrega registros revisados de feedback/labeled/*.jsonl."""
    if not FEEDBACK_LABELED_DIR.exists():
        return []

    records = []
    for path in sorted(FEEDBACK_LABELED_DIR.glob("*.jsonl")):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                # Só ingere registros explicitamente revisados
                if not r.get("_reviewed"):
                    continue
                label = r.get("relevant_yes_no")
                if label is True:
                    r["_label"] = "positive"
                    records.append(r)
                elif label is False:
                    r["_label"] = "negative"
                    records.append(r)
    return records


def ingest(jsonl_path: Path = JSONL_PATH) -> dict:
    if not torch.cuda.is_available():
        raise RuntimeError(
            "GPU not available — embedding must run on CUDA. "
            "Check that your NVIDIA drivers and CUDA are correctly installed."
        )

    store = EmbeddingStore()
    before = store.count()

    main_records = _load_main_dataset(jsonl_path)
    feedback_records = _load_feedback_labeled()

    # Feedback pode sobrescrever/complementar o dataset principal
    # Dedup por ID: feedback tem precedência (permite corrigir rótulos)
    seen_ids: set[str] = set()
    all_records: list[dict] = []

    for r in feedback_records:
        rid = _record_id(r)
        if rid not in seen_ids:
            seen_ids.add(rid)
            all_records.append(r)

    for r in main_records:
        rid = _record_id(r)
        if rid not in seen_ids:
            seen_ids.add(rid)
            all_records.append(r)

    positives = [r for r in all_records if r["_label"] == "positive"]
    negatives = [r for r in all_records if r["_label"] == "negative"]

    fb_pos = sum(1 for r in feedback_records if r["_label"] == "positive")
    fb_neg = sum(1 for r in feedback_records if r["_label"] == "negative")

    log.info(
        f"Dataset: {len(positives)} positivos ({len(main_records)} dataset principal "
        f"+ {fb_pos} feedback) | {len(negatives)} negativos ({fb_neg} feedback)"
    )

    from sentence_transformers import SentenceTransformer  # import late: peso ~1.2GB
    model = SentenceTransformer(settings.embedding_model, device="cuda")

    texts = [_make_text(r) for r in all_records]
    log.info(f"Gerando embeddings para {len(texts)} artigos...")
    embeddings = model.encode(
        texts,
        batch_size=settings.embedding_batch_size,
        show_progress_bar=True,
        normalize_embeddings=True,
    )

    examples = [
        HistoricalExample(
            id=_record_id(r),
            title=r.get("title", ""),
            body=r.get("body"),
            embedding=embeddings[i],
            tickers=[r["ticker"]] if r.get("ticker") else [],
            sectors=[],
            label=r["_label"],
            published_at=_parse_date(r.get("date")),
            source=r.get("sender") or r.get("source", ""),
        )
        for i, r in enumerate(all_records)
    ]

    store.upsert(examples)
    after = store.count()

    with_ticker = sum(1 for r in positives if r.get("ticker"))
    result = {
        "before": before,
        "after": after,
        "new": after - before,
        "positives": len(positives),
        "negatives": len(negatives),
        "with_ticker": with_ticker,
    }
    log.info(
        f"Qdrant: {before} -> {after} vetores "
        f"(+{after - before} novos | {len(positives)} pos, {len(negatives)} neg)"
    )
    return result


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        stream=sys.stdout,
    )
    result = ingest()
    print(result)
