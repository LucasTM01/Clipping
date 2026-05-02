"""
Sprint 2a — Fetch article content for each record in clipping_dataset.jsonl.

For each record:
- Skips internal Safra PDFs (apiext-bi.safra.com.br) and mziq PDFs
- Decodes Microsoft Safelinks URLs
- Fetches HTML with httpx (async, 1 req/s per domain, timeout 10s)
- Extracts: title_original, subtitle_original, body  (original language)
- Keeps: title  (English version, already in data)
- Sets: relevant_yes_no = true  (all records in this base are relevant)

Output: news_dataset/clipping_enriched.jsonl  (checkpointed every 100 records)
"""

import asyncio
import json
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse, unquote, parse_qs

import httpx
import trafilatura
from bs4 import BeautifulSoup

_HERE = Path(__file__).parent
INPUT_FILE = _HERE / "clipping_dataset.jsonl"
OUTPUT_FILE = _HERE / "clipping_enriched.jsonl"
CHECKPOINT_EVERY = 100
REQUEST_TIMEOUT = 10
MIN_DELAY_PER_DOMAIN = 1.0  # seconds between requests to same domain

SKIP_DOMAINS = {"apiext-bi.safra.com.br", "api.mziq.com"}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
}


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def decode_url(url: str) -> str:
    """Decode Microsoft Safelinks to the real URL."""
    if "safelinks.protection.outlook.com" in url:
        params = parse_qs(urlparse(url).query)
        if "url" in params:
            return unquote(params["url"][0])
    return url


def should_skip(url: str | None) -> bool:
    if not url:
        return True
    domain = urlparse(url).netloc
    return domain in SKIP_DOMAINS


def extract_content(html: str) -> tuple[str | None, str | None, str | None]:
    """
    Returns (title_original, subtitle_original, body).
    title/subtitle from BeautifulSoup, body from trafilatura.
    """
    soup = BeautifulSoup(html, "html.parser")

    # --- title_original ---
    title_original: str | None = None
    h1 = soup.find("h1")
    if h1:
        title_original = h1.get_text(strip=True) or None
    if not title_original and soup.title:
        raw = soup.title.get_text(strip=True)
        # Strip site name suffixes like " | Valor Econômico"
        title_original = raw.split(" | ")[0].split(" - ")[0].strip() or None

    # --- subtitle_original ---
    subtitle_original: str | None = None
    SUBTITLE_SELECTORS = [
        "h2.subtitle", ".subtitle", ".subheadline", "h2.subheadline",
        ".summary", ".chapeu", ".standfirst", ".article-subtitle",
        ".content-subtitle", ".description", '[itemprop="description"]',
        "h2.article__subtitle",
    ]
    for sel in SUBTITLE_SELECTORS:
        elem = soup.select_one(sel)
        if elem:
            text = elem.get_text(strip=True)
            if text:
                subtitle_original = text
                break

    if not subtitle_original:
        # Fallback: meta description
        meta = soup.find("meta", attrs={"name": "description"})
        if meta:
            content = (meta.get("content") or "").strip()
            if content:
                subtitle_original = content

    # --- body ---
    body = trafilatura.extract(
        html,
        include_comments=False,
        include_tables=False,
        no_fallback=False,
    )

    return title_original, subtitle_original, body


class RateLimiter:
    """Per-domain rate limiter: enforces MIN_DELAY_PER_DOMAIN between requests."""

    def __init__(self) -> None:
        self._last: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock_for(self, domain: str) -> asyncio.Lock:
        if domain not in self._locks:
            self._locks[domain] = asyncio.Lock()
        return self._locks[domain]

    async def acquire(self, domain: str) -> None:
        async with self._lock_for(domain):
            now = time.monotonic()
            wait = MIN_DELAY_PER_DOMAIN - (now - self._last.get(domain, 0))
            if wait > 0:
                await asyncio.sleep(wait)
            self._last[domain] = time.monotonic()


async def fetch_one(
    client: httpx.AsyncClient,
    rate_limiter: RateLimiter,
    record: dict,
    index: int,
    total: int,
) -> dict:
    """Fetch a single record and return it enriched."""
    result = dict(record)
    result["relevant_yes_no"] = True  # all records in this base are relevant

    url = decode_url(record.get("url") or "")
    result["url"] = url  # store decoded URL

    if should_skip(url):
        result["title_original"] = None
        result["subtitle_original"] = None
        if result.get("body") is None:
            result["body"] = None
        return result

    domain = urlparse(url).netloc
    await rate_limiter.acquire(domain)

    try:
        resp = await client.get(url, timeout=REQUEST_TIMEOUT, follow_redirects=True)
        resp.raise_for_status()
        title_original, subtitle_original, body = extract_content(resp.text)
        result["title_original"] = title_original
        result["subtitle_original"] = subtitle_original
        result["body"] = body
        log(f"[{index}/{total}] OK  {domain}  {(title_original or '')[:50]}")
    except httpx.HTTPStatusError as e:
        log(f"[{index}/{total}] HTTP {e.response.status_code}  {url[:70]}")
        result["title_original"] = None
        result["subtitle_original"] = None
        result["body"] = None
    except Exception as e:
        log(f"[{index}/{total}] ERR {type(e).__name__}: {str(e)[:60]}  {url[:60]}")
        result["title_original"] = None
        result["subtitle_original"] = None
        result["body"] = None

    return result


def load_already_done(output_path: Path) -> set[str]:
    """Return set of URLs already present in the output file."""
    done: set[str] = set()
    if not output_path.exists():
        return done
    with open(output_path, encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
                if r.get("url"):
                    done.add(r["url"])
            except json.JSONDecodeError:
                pass
    return done


async def main() -> None:
    with open(INPUT_FILE, encoding="utf-8") as f:
        records = [json.loads(line) for line in f]

    total = len(records)
    log(f"Loaded {total} records from {INPUT_FILE}")

    # Resume support: skip already-processed URLs
    already_done = load_already_done(OUTPUT_FILE)
    if already_done:
        log(f"Resuming — {len(already_done)} records already in output, skipping them")

    # Decode URLs upfront for dedup check
    pending = []
    skip_count = 0
    for r in records:
        decoded_url = decode_url(r.get("url") or "")
        if decoded_url in already_done:
            skip_count += 1
            continue
        pending.append(r)

    log(f"Skipped {skip_count} already done. {len(pending)} to fetch.")

    rate_limiter = RateLimiter()
    results_buffer: list[dict] = []
    processed = 0

    output_mode = "a" if already_done else "w"
    out_file = open(OUTPUT_FILE, output_mode, encoding="utf-8")

    try:
        async with httpx.AsyncClient(headers=HEADERS, verify=False) as client:
            for i, record in enumerate(pending, start=len(already_done) + 1):
                enriched = await fetch_one(client, rate_limiter, record, i, total)
                results_buffer.append(enriched)
                processed += 1

                if len(results_buffer) >= CHECKPOINT_EVERY:
                    for r in results_buffer:
                        out_file.write(json.dumps(r, ensure_ascii=False) + "\n")
                    out_file.flush()
                    log(f"  Checkpoint: {processed} records saved to {OUTPUT_FILE}")
                    results_buffer.clear()

        # Flush remainder
        for r in results_buffer:
            out_file.write(json.dumps(r, ensure_ascii=False) + "\n")
        out_file.flush()
        log(f"Done. {processed} records processed. Output: {OUTPUT_FILE}")

    finally:
        out_file.close()


if __name__ == "__main__":
    asyncio.run(main())
