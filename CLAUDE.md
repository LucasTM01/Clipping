# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Purpose

Automated daily briefing pipeline for Brazilian equity research covering Capital Goods & Industrials. Runs every business day, collects news from RSS feeds, filters for relevance to a 9-ticker coverage universe, generates GPT-based analysis, and outputs a formatted Word document.

## Running the Pipeline

```powershell
# Activate venv first
.venv\Scripts\activate

# Standard run (requires OPENAI_API_KEY in .env)
python main.py

# Fast smoke test — no API calls, no HTTP enrichment
python main.py --no-llm --no-enrich

# Full debug output
python main.py --no-llm --no-enrich --verbose

# Scheduled mode — runs at 07:00 on business days, polling every 60s
python main.py --schedule
python main.py --schedule --schedule-hour 6
```

## Environment Setup

Create `.env` in the project root (see `.env.example`):

```
OPENAI_API_KEY=sk-proj-...
GPT_MODEL=gpt-4o           # or gpt-4o-mini for lower cost
API_MAX_WORKERS=5          # parallel GPT calls
ENRICH_MAX_WORKERS=10      # parallel HTTP fetches
OLLAMA_BASE_URL=http://localhost:11434   # only needed if not using --no-llm
OLLAMA_MODEL=llama3.1:8b
```

## Architecture: 5-Layer Pipeline

Data flows strictly top-to-bottom through `pipeline.py → run_pipeline()`. No layer reaches back into a previous one.

```
Layer 1   fetcher/rss_fetcher.py    feedparser → list[Article]
Layer 1b  fetcher/rss_fetcher.py    trafilatura full-text fetch (parallelized, 10 workers)
Layer 2   screener/local_llm.py     keyword prefilter → Ollama LLM → list[ScreeningResult]
Layer 3   analyzer/api_analyzer.py  OpenAI GPT → list[AnalysisResult]  (parallelized, 5 workers)
Layer 4   generator/doc_generator.py  python-docx → output/briefing_YYYYMMDD_HHMM.docx
```

**Key dataclass chain:** `Article` → `ScreeningResult` (wraps Article) → `AnalysisResult` (flat, doc-ready)

## Where to Make Common Changes

| What to change | Where |
|---|---|
| Add/remove RSS feeds | `config/settings.py` → `RSS_FEEDS` |
| Add a ticker to coverage | `config/settings.py` → `COVERAGE_UNIVERSE` (add ticker dict with `company`, `sector`, `keywords`) |
| Add sector keywords for macro filter | `config/settings.py` → `RELEVANT_SECTORS` |
| Tune GPT analysis prompt | `config/prompts.py` → `ANALYSIS_SYSTEM_PROMPT` / `ANALYSIS_USER_PROMPT` |
| Tune Ollama screening prompt | `config/prompts.py` → `SCREENING_SYSTEM_PROMPT` / `SCREENING_USER_PROMPT` |
| Change Word document layout | `generator/doc_generator.py` |
| Change concurrency limits | `config/settings.py` or `.env` via `API_MAX_WORKERS` / `ENRICH_MAX_WORKERS` |

## Screening Logic (Layer 2)

Two-stage filter in `screen_articles()`:
1. **Keyword prefilter** — checks `title + full_text[:1000]` against `COVERAGE_UNIVERSE[ticker]["keywords"]` and `RELEVANT_SECTORS`. Free, instant, runs always.
2. **Ollama LLM** — only called when keyword stage finds sector matches but *no direct ticker match*. Disabled entirely with `--no-llm` or if Ollama has >5 consecutive failures (auto-fallback).

Articles with a direct keyword→ticker match skip the LLM entirely (confidence = 0.95). Ollama results below `SCREENER_CONFIDENCE_THRESHOLD` (0.6) are discarded.

## Parallelism Design

- **Layer 1b** (`enrich_full_text`): `ThreadPoolExecutor(ENRICH_MAX_WORKERS)` — each thread calls `trafilatura.fetch_url()` independently. Articles are mutated in-place (thread-safe because each thread writes to a different object).
- **Layer 3** (`analyze_batch`): `ThreadPoolExecutor(API_MAX_WORKERS)` — single `OpenAI` client instance shared across threads (thread-safe by design). Each call goes to `_analyze_one()`. Result order is non-deterministic (uses `as_completed`); the document groups by ticker afterward so order doesn't matter.
- **Layer 2** (Ollama screening): intentionally sequential — Ollama is a single local process and parallel requests don't improve throughput.

## GPT Response Contract

The GPT prompt (`ANALYSIS_USER_PROMPT`) uses `response_format={"type": "json_object"}`, which forces the model to return valid JSON. Expected keys: `tickers`, `summary`, `sentiment` (`"Positivo"/"Negativo"/"Neutro"`), `action_required` (bool), `action_detail` (str|null), `category` (`"company"/"macro_sectoral"`). If parsing fails, `_fallback_analysis()` generates a stub result so the pipeline never crashes on a bad API response.

## Output

- **Document:** `output/briefing_YYYYMMDD_HHMM.docx`
- **Log:** `output/pipeline_YYYYMMDD.log` (UTF-8, DEBUG level)
- The log is also mirrored to stdout (INFO level) with a UTF-8 wrapper to handle Windows cp1252 console encoding.
