"""
Exporta candidates dos audit logs para revisão manual no Streamlit.

Dois tipos de candidatos:
- near_miss: artigos descartados com score próximo ao threshold (potenciais falsos negativos)
- approved:  artigos aprovados que foram para o Word (validação dos verdadeiros positivos)

Ambos acumulam em feedback/candidates.jsonl com campo candidate_type para distinção.

Uso:
    python -m data.export_feedback_candidates          # processa todos os audits
    python -m data.export_feedback_candidates --today  # só o audit de hoje
"""

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

OUTPUT_DIR = Path(__file__).parent.parent / "output"
FEEDBACK_DIR = Path(__file__).parent.parent / "feedback"
CANDIDATES_PATH = FEEDBACK_DIR / "candidates.jsonl"
CALIBRATION_PATH = Path(__file__).parent.parent / "config" / "calibration.json"

# Score fora desse range não vale revisar:
# - Abaixo do mínimo: claramente irrelevante
# - Acima do máximo: já entrou no briefing como low_review ou melhor
_NEAR_MISS_MIN_OFFSET = -0.05   # threshold - 0.05
_NEAR_MISS_MAX_OFFSET = +0.35   # threshold + 0.35
_ABSOLUTE_MIN = 0.25


def _load_threshold() -> float:
    if CALIBRATION_PATH.exists():
        with open(CALIBRATION_PATH, encoding="utf-8") as f:
            return json.load(f).get("threshold", 0.40)
    return 0.40


def _load_existing_urls() -> set[str]:
    if not CANDIDATES_PATH.exists():
        return set()
    urls = set()
    with open(CANDIDATES_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    urls.add(json.loads(line).get("url", ""))
                except json.JSONDecodeError:
                    pass
    return urls


def export_candidates(audit_paths: list[Path] | None = None) -> int:
    """
    Processa os audit logs indicados e acumula near-misses em
    feedback/candidates.jsonl. Retorna o número de novos candidatos adicionados.
    """
    threshold = _load_threshold()
    score_min = max(_ABSOLUTE_MIN, threshold + _NEAR_MISS_MIN_OFFSET)
    score_max = threshold + _NEAR_MISS_MAX_OFFSET

    log.info(
        f"Near-miss range: [{score_min:.4f}, {score_max:.4f}] "
        f"(threshold={threshold:.4f})"
    )

    if audit_paths is None:
        audit_paths = sorted(OUTPUT_DIR.glob("screening_audit_*.jsonl"))

    if not audit_paths:
        log.warning("Nenhum audit log encontrado em output/")
        return 0

    existing_urls = _load_existing_urls()
    new_candidates: list[dict] = []

    for audit_path in audit_paths:
        with open(audit_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                url = entry.get("url", "")
                score = entry.get("relevance_score", 0.0)
                tier = entry.get("tier", "")

                if url in existing_urls:
                    continue
                if not (score_min <= score <= score_max):
                    continue
                if tier not in ("discard", "low_review"):
                    continue

                ts = entry.get("timestamp", "")
                date_str = ""
                if ts:
                    try:
                        date_str = datetime.fromisoformat(ts).strftime("%d/%m/%Y")
                    except ValueError:
                        pass

                candidate = {
                    "date": date_str,
                    "title": entry.get("title", ""),
                    "url": url,
                    "source": entry.get("source", ""),
                    "ticker": None,
                    "relevant_yes_no": None,
                    "body": None,
                    "relevance_score": round(score, 4),
                    "predicted_tickers": entry.get("predicted_tickers", []),
                    "tier": entry.get("tier", ""),
                    "candidate_type": "near_miss",
                    "_reviewed": False,
                }
                new_candidates.append(candidate)
                existing_urls.add(url)

    if not new_candidates:
        log.info("Nenhum novo near-miss candidato encontrado.")
        return 0

    _append_candidates(new_candidates)
    log.info(f"Adicionados {len(new_candidates)} near-miss candidatos -> {CANDIDATES_PATH}")
    return len(new_candidates)


def _append_candidates(records: list[dict]) -> None:
    """Persiste registros em feedback/candidates.jsonl (append)."""
    FEEDBACK_DIR.mkdir(exist_ok=True)
    with open(CANDIDATES_PATH, "a", encoding="utf-8") as f:
        for c in records:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")


def export_approved(audit_paths: list[Path] | None = None) -> int:
    """
    Exporta artigos aprovados (score >= threshold, tier != discard) do audit log
    para feedback/candidates.jsonl com candidate_type='approved'.

    Permite ao analista validar os verdadeiros positivos além dos near-misses.
    Retorna o número de novos registros adicionados.
    """
    threshold = _load_threshold()

    if audit_paths is None:
        audit_paths = sorted(OUTPUT_DIR.glob("screening_audit_*.jsonl"))

    if not audit_paths:
        log.warning("Nenhum audit log encontrado em output/")
        return 0

    existing_urls = _load_existing_urls()
    new_entries: list[dict] = []

    for audit_path in audit_paths:
        with open(audit_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                url = entry.get("url", "")
                score = entry.get("relevance_score", 0.0)
                tier = entry.get("tier", "discard")

                # Aprovados: passaram o threshold e não foram descartados
                if tier == "discard" or score < threshold:
                    continue
                if url in existing_urls:
                    continue

                ts = entry.get("timestamp", "")
                date_str = ""
                if ts:
                    try:
                        date_str = datetime.fromisoformat(ts).strftime("%d/%m/%Y")
                    except ValueError:
                        pass

                new_entries.append({
                    "date": date_str,
                    "title": entry.get("title", ""),
                    "url": url,
                    "source": entry.get("source", ""),
                    "ticker": None,
                    "relevant_yes_no": None,
                    "body": None,
                    "relevance_score": round(score, 4),
                    "predicted_tickers": entry.get("predicted_tickers", []),
                    "tier": tier,
                    "candidate_type": "approved",
                    "_reviewed": False,
                })
                existing_urls.add(url)

    if not new_entries:
        log.info("Nenhum novo artigo aprovado para exportar.")
        return 0

    _append_candidates(new_entries)
    log.info(f"Adicionados {len(new_entries)} artigos aprovados -> {CANDIDATES_PATH}")
    return len(new_entries)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        stream=sys.stdout,
    )

    parser = argparse.ArgumentParser(description="Exporta near-miss candidates para revisão")
    parser.add_argument("--today", action="store_true", help="Só o audit de hoje")
    args = parser.parse_args()

    if args.today:
        today = datetime.now().strftime("%Y%m%d")
        paths = [OUTPUT_DIR / f"screening_audit_{today}.jsonl"]
        paths = [p for p in paths if p.exists()]
    else:
        paths = None

    n_near_miss = export_candidates(paths)
    n_approved = export_approved(paths)
    print(f"\nNovos near-miss exportados: {n_near_miss}")
    print(f"Novos aprovados exportados:  {n_approved}")
    print(f"Arquivo: {CANDIDATES_PATH}")
    print("\nProximo passo:")
    print("  streamlit run review_feedback.py")
