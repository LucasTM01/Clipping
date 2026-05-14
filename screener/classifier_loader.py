"""
Carrega o classificador LR treinado (screener/train_classifier.py) como singleton.

Retorna None se o modelo não existe — o screener cai back para kNN automaticamente.
"""

import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_MODEL_PATH = Path(__file__).parent.parent / "models" / "screener_clf.joblib"
_clf_cache: Any = None
_cache_loaded: bool = False


def load_classifier() -> Any | None:
    """
    Retorna o classificador sklearn carregado, ou None se não existir.
    Cached em memória após a primeira chamada.
    """
    global _clf_cache, _cache_loaded
    if _cache_loaded:
        return _clf_cache

    _cache_loaded = True
    if not _MODEL_PATH.exists():
        log.debug("models/screener_clf.joblib não encontrado — score_clf desativado")
        return None

    try:
        import joblib
        _clf_cache = joblib.load(_MODEL_PATH)
        log.info(f"Classificador LR carregado de {_MODEL_PATH}")
    except Exception as e:
        log.warning(f"Falha ao carregar classificador: {e}")
        _clf_cache = None

    return _clf_cache
