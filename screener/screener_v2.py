"""
Screener v2: triagem baseada em BGE-M3 + Qdrant + novelty detection.

Substitui screener/local_llm.py como padrão. O pipeline legacy continua
disponível via --legacy-screener.
"""

import json
import logging
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import torch

from fetcher.rss_fetcher import Article
from screener.embedding_store import EmbeddingStore
from screener.relevance_scorer import RelevanceScorer, ScoredArticle
from config.settings import settings

log = logging.getLogger(__name__)

AUDIT_DIR = Path(__file__).parent.parent / "output"


def screen_articles_v2(articles: list[Article]) -> list[ScoredArticle]:
    """
    Triagem por embedding + novelty detection.

    Retorna apenas artigos com tier != 'discard'. Artigos descartados são
    registrados no audit log mas não chegam ao briefing.
    """
    if not articles:
        return []

    if not torch.cuda.is_available():
        log.warning("GPU indisponível — embedding no CPU pode ser lento.")

    store = EmbeddingStore()
    if store.count() == 0:
        log.warning(
            "Qdrant está vazio. Rode data/ingest_history.py antes de usar o screener v2. "
            "Retornando lista vazia."
        )
        return []

    from sentence_transformers import SentenceTransformer
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = SentenceTransformer(settings.embedding_model, device=device)
    scorer = RelevanceScorer(store)

    # Dedup por URL
    seen_urls: set[str] = set()
    unique: list[Article] = []
    for art in articles:
        if art.url not in seen_urls:
            seen_urls.add(art.url)
            unique.append(art)
    if len(unique) < len(articles):
        log.debug(f"Dedup: {len(articles)} -> {len(unique)} artigos únicos")

    # Embeddings em batch
    texts = [
        f"{a.title}\n\n{a.full_text[:4000]}" if a.full_text.strip() else a.title
        for a in unique
    ]
    embeddings: np.ndarray = model.encode(
        texts,
        batch_size=settings.embedding_batch_size,
        show_progress_bar=False,
        normalize_embeddings=True,
    )

    # Scoring + audit
    audit_path = AUDIT_DIR / f"screening_audit_{datetime.now().strftime('%Y%m%d')}.jsonl"
    AUDIT_DIR.mkdir(exist_ok=True)

    passed: list[ScoredArticle] = []
    with open(audit_path, "a", encoding="utf-8") as audit_file:
        for article, emb in zip(unique, embeddings):
            candidate = scorer.build_candidate(article, emb)
            scored = scorer.score(candidate)

            sent = scored.tier in ("high", "medium")
            audit_entry = {
                "timestamp": datetime.now().isoformat(),
                "title": article.title,
                "url": article.url,
                "source": article.source,
                "relevance_score": round(scored.relevance_score, 4),
                "tier": scored.tier,
                "predicted_tickers": scored.predicted_tickers,
                "ticker_confidence": {k: round(v, 3) for k, v in scored.ticker_confidence.items()},
                "rationale": scored.rationale,
                "sent_to_analysis": sent,
            }
            audit_file.write(json.dumps(audit_entry, ensure_ascii=False) + "\n")

            if scored.tier != "discard":
                passed.append(scored)

    n_high = sum(1 for s in passed if s.tier == "high")
    n_medium = sum(1 for s in passed if s.tier == "medium")
    n_review = sum(1 for s in passed if s.tier == "low_review")
    n_discard = len(unique) - len(passed)
    log.info(
        f"Screener v2: {len(unique)} artigos -> "
        f"high={n_high} medium={n_medium} low_review={n_review} discard={n_discard} "
        f"| audit: {audit_path.name}"
    )
    return passed
