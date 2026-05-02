"""
Extrai notícias de um arquivo Word (.docx) com hyperlinks e ingere no dataset.

Cada parágrafo não-vazio é tratado como uma notícia relevante:
- Se tiver URL (hyperlink): scrape com trafilatura para obter título e body reais
- Se não tiver URL: usa o texto do parágrafo como título, body=None

Uso:
    python -m data.ingest_from_word data/new_summaries.docx
    python -m data.ingest_from_word data/new_summaries.docx --date 01/01/2025
    python -m data.ingest_from_word data/new_summaries.docx --dry-run
    python -m data.ingest_from_word data/new_summaries.docx --output news_dataset/batch1.jsonl
"""

import argparse
import json
import logging
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import trafilatura
from docx import Document
from docx.oxml.ns import qn

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import settings
from data.ingest_from_summaries import (
    DEFAULT_OUTPUT,
    _infer_ticker,
    _parse_line,
    _record_id,
    write_jsonl,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Word parsing
# ---------------------------------------------------------------------------

def _extract_url(para, rels: dict) -> str | None:
    """Extrai a primeira URL de um parágrafo — suporta dois formatos do Word."""
    # Formato 1: w:hyperlink com r:id referenciando relacionamento
    for hl in para._element.findall(".//" + qn("w:hyperlink")):
        rId = hl.get(qn("r:id"))
        if rId and rId in rels:
            return rels[rId].target_ref

    # Formato 2: field code  HYPERLINK "url"  (instrText)
    for it in para._element.findall(".//" + qn("w:instrText")):
        if it.text and "HYPERLINK" in it.text:
            m = re.search(r'HYPERLINK\s+"([^"]+)"', it.text)
            if m:
                return m.group(1)

    return None


def parse_word_doc(docx_path: Path) -> list[tuple[str, str | None]]:
    """
    Lê o .docx e retorna lista de (summary_text, url_or_None) por parágrafo.
    """
    doc = Document(docx_path)
    rels = doc.part.rels
    entries: list[tuple[str, str | None]] = []

    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        url = _extract_url(para, rels)
        entries.append((text, url))

    return entries


# ---------------------------------------------------------------------------
# Scraping
# ---------------------------------------------------------------------------

def _scrape(url: str) -> tuple[str | None, str | None]:
    """Returns (title, body) scraped from url, or (None, None) on failure."""
    try:
        downloaded = trafilatura.fetch_url(url, timeout=15)
        if not downloaded:
            return None, None
        metadata = trafilatura.extract(
            downloaded,
            include_comments=False,
            include_tables=False,
            output_format="json",
            with_metadata=True,
        )
        if metadata:
            data = json.loads(metadata)
            title = data.get("title") or None
            body = data.get("text") or None
            return title, body
    except Exception as e:
        log.debug(f"Scrape failed for {url}: {e}")
    return None, None


def scrape_all(
    entries: list[tuple[str, str | None]],
    max_workers: int,
) -> list[tuple[str, str | None, str | None, str | None]]:
    """
    Returns list of (summary, url, scraped_title, scraped_body).
    Parallelizes URL scraping.
    """
    # Separate items with and without URL
    with_url = [(i, s, u) for i, (s, u) in enumerate(entries) if u]
    without_url = [(i, s) for i, (s, u) in enumerate(entries) if not u]

    results: dict[int, tuple[str | None, str | None]] = {}

    log.info(f"Scraping {len(with_url)} URLs with {max_workers} workers...")
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_to_idx = {pool.submit(_scrape, u): i for i, _, u in with_url}
        done = 0
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            results[idx] = future.result()
            done += 1
            if done % 50 == 0:
                log.info(f"  {done}/{len(with_url)} scraped")

    for i, _ in without_url:
        results[i] = (None, None)

    output = []
    for i, (summary, url) in enumerate(entries):
        scraped_title, scraped_body = results.get(i, (None, None))
        output.append((summary, url, scraped_title, scraped_body))

    return output


# ---------------------------------------------------------------------------
# Record building
# ---------------------------------------------------------------------------

def build_records(
    scraped: list[tuple[str, str | None, str | None, str | None]],
    date_str: str,
) -> list[dict]:
    """Only includes articles where scraping succeeded (has real title + body).
    Articles without body are skipped to avoid polluting the dataset with
    analyst summaries (English) instead of real article content (Portuguese)."""
    records = []
    skipped = 0
    for summary, url, scraped_title, scraped_body in scraped:
        if not scraped_body:
            skipped += 1
            continue

        parsed = _parse_line(summary)
        clean_summary, source = parsed if parsed else (summary, "")

        ticker = _infer_ticker(clean_summary)

        records.append({
            "date": date_str,
            "title": scraped_title or clean_summary,
            "url": url or f"summary://{_record_id(clean_summary)}",
            "source": source,
            "ticker": ticker,
            "relevant_yes_no": True,
            "body": scraped_body,
        })

    if skipped:
        log.info(f"Skipped {skipped} articles where scraping failed (no body)")
    return records


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        stream=sys.stdout,
    )

    parser = argparse.ArgumentParser(
        description="Ingest news from a Word .docx with hyperlinks into the screening dataset"
    )
    parser.add_argument("input", type=Path, help="Path to .docx file")
    parser.add_argument(
        "--date",
        default=datetime.now().strftime("%d/%m/%Y"),
        help="Date for all records (DD/MM/YYYY). Default: today.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Output JSONL file (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=settings.enrich_max_workers,
        help=f"Parallel scrape workers (default: {settings.enrich_max_workers})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and print first 10 records without scraping or writing",
    )
    args = parser.parse_args()

    if not args.input.exists():
        print(f"Error: file not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    log.info(f"Parsing {args.input}...")
    entries = parse_word_doc(args.input)
    log.info(f"  {len(entries)} paragraphs | {sum(1 for _, u in entries if u)} with URL")

    if args.dry_run:
        print("\n--- DRY RUN: first 10 entries (no scraping) ---\n")
        for summary, url in entries[:10]:
            parsed = _parse_line(summary)
            clean, source = parsed if parsed else (summary, "")
            ticker = _infer_ticker(clean)
            print(f"Summary : {clean[:80]}")
            print(f"Source  : {source}")
            print(f"Ticker  : {ticker}")
            print(f"URL     : {url or '(none)'}")
            print()
        return

    scraped = scrape_all(entries, max_workers=args.workers)

    scraped_ok = sum(1 for _, _, _, body in scraped if body)
    log.info(f"Scrape complete: {scraped_ok}/{len(scraped)} articles with body")

    records = build_records(scraped, args.date)
    added = write_jsonl(records, args.output)

    skipped = len(scraped) - len(records)
    print(f"\nDone: {scraped_ok} scraped | {skipped} skipped (no body) | {added} new added to {args.output}")
    no_ticker = sum(1 for r in records if not r["ticker"])
    if no_ticker:
        print(f"  {no_ticker} records with no ticker (will go to Macro/Setorial section)")
    print("\nNext steps:")
    print("  python -m data.ingest_history")
    print("  python -m data.calibrate_threshold")


if __name__ == "__main__":
    main()
