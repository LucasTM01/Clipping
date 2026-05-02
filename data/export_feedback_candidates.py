"""
Exporta near-miss candidates dos audit logs para revisão manual.

Lê todos os output/screening_audit_*.jsonl, extrai artigos na faixa de score
próxima ao threshold (near-misses) e acumula em feedback/candidates.jsonl.

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
_NEAR_MISS_MIN_OFFSET = -0.15   # threshold - 0.15
_NEAR_MISS_MAX_OFFSET = +0.05   # threshold + 0.05
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
                    "_reviewed": False,
                }
                new_candidates.append(candidate)
                existing_urls.add(url)

    if not new_candidates:
        log.info("Nenhum novo candidato encontrado.")
        return 0

    FEEDBACK_DIR.mkdir(exist_ok=True)
    with open(CANDIDATES_PATH, "a", encoding="utf-8") as f:
        for c in new_candidates:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")

    log.info(f"Adicionados {len(new_candidates)} candidatos -> {CANDIDATES_PATH}")
    return len(new_candidates)


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

    n = export_candidates(paths)
    print(f"\nNovos candidatos exportados: {n}")
    print(f"Arquivo: {CANDIDATES_PATH}")
    print("\nProximo passo:")
    print("  1. Abrir feedback/candidates.jsonl")
    print("  2. Para cada artigo: definir relevant_yes_no=true ou false")
    print("     e mudar _reviewed para true")
    print("  3. Copiar o arquivo revisado para feedback/labeled/")
    print("  4. python -m data.ingest_history")
    print("  5. python -m data.calibrate_threshold")
