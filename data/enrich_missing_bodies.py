"""
Re-enriquece registros com corpo vazio em news_dataset/clipping_enriched.jsonl.

Para cada registro cujos campos body/full_text/content/text estão todos vazios,
tenta baixar e extrair o texto completo via trafilatura. Se o texto extraído
tiver mais de 100 caracteres, atualiza o campo 'body' do registro.

Reescreve o arquivo JSONL inteiro ao final (operação idempotente — pode ser
rodado múltiplas vezes sem efeito colateral).

Uso:
    python -m data.enrich_missing_bodies
    python -m data.enrich_missing_bodies --limit 200   # testar com subset
    python -m data.enrich_missing_bodies --workers 8   # paralelismo
"""

import argparse
import json
import logging
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import trafilatura

JSONL_PATH = Path(__file__).parent.parent / "news_dataset" / "clipping_enriched.jsonl"
_MIN_BODY_CHARS = 100

log = logging.getLogger(__name__)


def _has_body(record: dict) -> bool:
    """Retorna True se o registro já tem texto de corpo."""
    for field in ("body", "full_text", "content", "text"):
        val = record.get(field)
        if val and len(val.strip()) >= _MIN_BODY_CHARS:
            return True
    return False


def _fetch_body(url: str) -> str:
    """Baixa e extrai o texto de um URL via trafilatura. Retorna '' em caso de erro."""
    try:
        html = trafilatura.fetch_url(url)
        if not html:
            return ""
        text = trafilatura.extract(
            html,
            include_comments=False,
            include_tables=False,
            no_fallback=False,
        )
        return (text or "").strip()
    except Exception as exc:
        log.debug(f"trafilatura falhou para {url}: {exc}")
        return ""


def _enrich_record(record: dict) -> tuple[dict, bool]:
    """
    Tenta enriquecer um registro sem corpo.
    Retorna (record_atualizado, foi_enriquecido).
    """
    url = record.get("url", "")
    if not url:
        return record, False

    body = _fetch_body(url)
    if len(body) >= _MIN_BODY_CHARS:
        record = {**record, "body": body}
        return record, True

    return record, False


def enrich(
    jsonl_path: Path = JSONL_PATH,
    limit: int | None = None,
    max_workers: int = 5,
) -> dict:
    """
    Carrega o JSONL, enriquece registros sem corpo e reescreve o arquivo.

    Args:
        jsonl_path: Caminho para o arquivo JSONL.
        limit: Máximo de registros sem corpo a processar (None = todos).
        max_workers: Threads para download paralelo.

    Returns:
        Dict com estatísticas: n_total, n_missing, n_attempted, n_enriched, n_failed.
    """
    if not jsonl_path.exists():
        raise FileNotFoundError(f"JSONL não encontrado: {jsonl_path}")

    log.info(f"Carregando {jsonl_path.name}...")
    records: list[dict] = []
    with open(jsonl_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))

    n_total = len(records)
    missing_idx = [i for i, r in enumerate(records) if not _has_body(r)]
    n_missing = len(missing_idx)

    log.info(f"Total: {n_total} registros | Sem corpo: {n_missing} ({n_missing/n_total:.1%})")

    if not missing_idx:
        log.info("Nenhum registro sem corpo — nada a fazer.")
        return {"n_total": n_total, "n_missing": 0, "n_attempted": 0, "n_enriched": 0, "n_failed": 0}

    if limit is not None:
        missing_idx = missing_idx[:limit]
    n_attempted = len(missing_idx)

    log.info(f"Tentando enriquecer {n_attempted} registros com {max_workers} threads...")

    n_enriched = 0
    n_failed = 0

    # Submete downloads em paralelo
    futures = {
        idx: None for idx in missing_idx
    }
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_to_idx = {
            pool.submit(_enrich_record, records[idx]): idx
            for idx in missing_idx
        }
        done = 0
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            done += 1
            try:
                updated_record, enriched = future.result()
                records[idx] = updated_record
                if enriched:
                    n_enriched += 1
                else:
                    n_failed += 1
            except Exception as exc:
                log.warning(f"Falha inesperada no índice {idx}: {exc}")
                n_failed += 1

            if done % 50 == 0 or done == n_attempted:
                log.info(
                    f"  {done}/{n_attempted} processados | "
                    f"enriquecidos={n_enriched} falhas={n_failed}"
                )

    # Reescreve o JSONL inteiro
    log.info(f"Reescrevendo {jsonl_path.name} com {n_enriched} corpos adicionados...")
    tmp_path = jsonl_path.with_suffix(".jsonl.tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    tmp_path.replace(jsonl_path)

    result = {
        "n_total": n_total,
        "n_missing": n_missing,
        "n_attempted": n_attempted,
        "n_enriched": n_enriched,
        "n_failed": n_failed,
    }
    log.info(
        f"Concluído: {n_enriched}/{n_attempted} enriquecidos "
        f"({n_enriched/n_attempted:.1%} taxa de recuperação)"
    )
    return result


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        stream=sys.stdout,
    )

    parser = argparse.ArgumentParser(description="Re-enriquece registros com corpo vazio.")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Máximo de registros a processar (default: todos)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=5,
        help="Threads de download paralelo (default: 5)",
    )
    args = parser.parse_args()

    result = enrich(limit=args.limit, max_workers=args.workers)
    print(json.dumps(result, indent=2))
