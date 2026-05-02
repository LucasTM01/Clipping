"""
Converte um arquivo de texto com resumos de notícias para JSONL,
no formato compatível com ingest_history.py.

Formato de entrada (um resumo por linha):
    Embraer (ERJ; OP; TP US$38.00) becomes the 3rd OEM... (Aeroin)
    China is preparing to compete in commercial aircraft. (Folha)

O script extrai o texto, a fonte do último (...), infere o ticker por
keywords e gera um registro JSON por linha.

Uso:
    python -m data.ingest_from_summaries summaries.txt
    python -m data.ingest_from_summaries summaries.txt --date 30/04/2026
    python -m data.ingest_from_summaries summaries.txt --output news_dataset/batch1.jsonl
"""

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

# Allow running as `python -m data.ingest_from_summaries`
sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import COVERAGE_UNIVERSE

DEFAULT_OUTPUT = Path(__file__).parent.parent / "news_dataset" / "clipping_enriched.jsonl"

# Build keyword → ticker map from COVERAGE_UNIVERSE (lowercase)
_KEYWORD_TO_TICKER: dict[str, str] = {}
for ticker, info in COVERAGE_UNIVERSE.items():
    for kw in info["keywords"]:
        _KEYWORD_TO_TICKER[kw.lower()] = ticker


def _infer_ticker(text: str) -> str | None:
    text_lower = text.lower()
    # Longer keywords first to avoid partial matches overriding specific ones.
    # Use word boundaries for short keywords (≤5 chars) to prevent false matches
    # like "bus" in "Airbus" or "weg" in "owego".
    for kw in sorted(_KEYWORD_TO_TICKER, key=len, reverse=True):
        if len(kw) <= 5:
            if re.search(r'\b' + re.escape(kw) + r'\b', text_lower):
                return _KEYWORD_TO_TICKER[kw]
        else:
            if kw in text_lower:
                return _KEYWORD_TO_TICKER[kw]
    return None


def _parse_line(line: str) -> tuple[str, str] | None:
    """Returns (summary_text, source) or None if line is blank/unparseable."""
    line = line.strip()
    if not line:
        return None
    # Match trailing (Source) — last parenthesized group at end of line
    match = re.search(r'\(([^()]+)\)\s*$', line)
    if match:
        source = match.group(1).strip()
        summary = line[:match.start()].strip().rstrip('.,;').strip()
    else:
        source = ""
        summary = line
    if not summary:
        return None
    return summary, source


def _record_id(title: str) -> str:
    key = title.encode("utf-8")
    return hashlib.sha256(key).hexdigest()[:32]


def parse_summaries(text_path: Path, date_str: str) -> list[dict]:
    records = []
    with open(text_path, encoding="utf-8") as f:
        for raw_line in f:
            parsed = _parse_line(raw_line)
            if parsed is None:
                continue
            summary, source = parsed
            ticker = _infer_ticker(summary)
            uid = _record_id(summary)
            records.append({
                "date": date_str,
                "title": summary,
                "url": f"summary://{uid}",
                "source": source,
                "ticker": ticker,
                "relevant_yes_no": True,
                "body": None,
            })
    return records


def write_jsonl(records: list[dict], output_path: Path) -> int:
    existing_urls: set[str] = set()
    if output_path.exists():
        with open(output_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        r = json.loads(line)
                        existing_urls.add(r.get("url", ""))
                    except json.JSONDecodeError:
                        pass

    new_records = [r for r in records if r["url"] not in existing_urls]

    if new_records:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "a", encoding="utf-8") as f:
            for r in new_records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    return len(new_records)


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest analyst summaries into JSONL dataset")
    parser.add_argument("input", type=Path, help="Text file with one summary per line")
    parser.add_argument(
        "--date",
        default=datetime.now().strftime("%d/%m/%Y"),
        help="Date for all records (DD/MM/YYYY). Defaults to today.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Output JSONL file (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print parsed records without writing")
    args = parser.parse_args()

    if not args.input.exists():
        print(f"Error: file not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    records = parse_summaries(args.input, args.date)

    if not records:
        print("No records parsed. Check the input file format.")
        sys.exit(0)

    no_ticker = [r for r in records if r["ticker"] is None]

    if args.dry_run:
        for r in records:
            print(json.dumps(r, ensure_ascii=False))
        print(f"\n--- {len(records)} records | {len(no_ticker)} without ticker ---")
        return

    added = write_jsonl(records, args.output)
    print(f"Parsed {len(records)} records → {added} new added to {args.output}")
    if no_ticker:
        print(f"  {len(no_ticker)} records with no ticker inferred:")
        for r in no_ticker:
            print(f"    - {r['title'][:80]}")
    print("\nNext steps:")
    print("  python -m data.ingest_history")
    print("  python -m data.calibrate_threshold")


if __name__ == "__main__":
    main()
