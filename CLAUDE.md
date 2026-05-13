# CLAUDE.md

Guidance for Claude Code when working in this repository.

## Project Purpose

Automated daily briefing pipeline for Brazilian equity research — Capital Goods & Industrials.
Collects RSS news, filters by relevance via embedding-based screener (BGE-M3 + Qdrant),
analyzes with a local LLM (Qwen3.5:9b via Ollama), and outputs a sector-grouped Word document.
After each run, sends two emails: a full HTML briefing and an ops report with pipeline metrics.

Coverage: EMBR3, POMO4, RAPT4, FRAS3, MYPK3, WEGE3, INTB3, GGPS3, ARML3, MILS3.

---

## Running the Pipeline

```powershell
# Activate venv first
.venv\Scripts\activate

# Standard run
python main.py

# No HTTP enrichment (faster, uses only headlines for embeddings)
python main.py --no-enrich

# Smoke test — no enrichment, no API cost
python main.py --no-enrich --verbose

# Scheduled mode — runs at 07:00 on business days
python main.py --schedule
python main.py --schedule --schedule-hour 6

# Legacy screener v1 (keyword + Ollama) — kept for regression comparison
python main.py --legacy-screener
```

## Tests

```powershell
python -m pytest tests/ -v   # unit tests, no external deps (Qdrant mocked)
```

## Dataset & Calibration (run when adding new data)

```powershell
# 1. Ingest dataset + feedback/labeled/ into Qdrant (requires GPU/CUDA)
python -m data.ingest_history

# 2. Recalibrate discard threshold via LOO-CV
python -m data.calibrate_threshold

# 3. Export near-miss candidates from audit logs for manual review
python -m data.export_feedback_candidates

# 4. Review candidates in browser UI
streamlit run review_feedback.py
```

## Source Health Check

```powershell
# List all sources (9 RSS + 14 scrapers)
python -m tools.check_source --list

# Test one source by name (case + diacritic insensitive, fuzzy match)
python -m tools.check_source aeroin
python -m tools.check_source "Pipeline Valor"
python -m tools.check_source politica        # matches "Estadão Política"

# Batch tests
python -m tools.check_source --rss           # all RSS feeds (~3s)
python -m tools.check_source --scrapers      # all Playwright scrapers (slow)
python -m tools.check_source --all

# Options
python -m tools.check_source aeroin --lookback 48   # extend time window
python -m tools.check_source aeroin -v              # show URLs + tracebacks
```

Reports: ✅ OK / ⚠️ zero articles / ❌ error. Exit code 1 if any source fails — usable in CI.

---

## Environment (.env)

```
ANALYSIS_BACKEND=ollama          # "ollama" | "openai"
ANALYSIS_MODEL=qwen3.5:9b
OLLAMA_BASE_URL=http://localhost:11434
OPENAI_API_KEY=sk-proj-...       # only needed if ANALYSIS_BACKEND=openai
GPT_MODEL=gpt-4o
API_MAX_WORKERS=5
ENRICH_MAX_WORKERS=10
EMBEDDING_MODEL=BAAI/bge-m3
EMBEDDING_TOP_K=5
QDRANT_HOST=localhost
QDRANT_PORT=6333

# Email notifications (optional)
EMAIL_ENABLED=true
EMAIL_SMTP_HOST=smtp.gmail.com
EMAIL_SMTP_PORT=587
EMAIL_USERNAME=sender@gmail.com
EMAIL_PASSWORD=xxxx xxxx xxxx xxxx   # Google App Password (not account password)
EMAIL_FROM=sender@gmail.com
EMAIL_BRIEFING_TO=["analyst@firm.com","analyst@gmail.com"]
EMAIL_OPS_TO=["analyst@gmail.com"]
```

Qdrant runs in Docker: `docker compose up -d`

Playwright (para o módulo de scraping) precisa do Chromium baixado:

```powershell
pip install -r requirements.txt
playwright install chromium
```

---

## Architecture

Data flows top-to-bottom through `pipeline.py → run_pipeline()`. No layer reads back into a previous one.

```
Layer 1    fetcher/rss_fetcher.py      feedparser → list[Article]
Layer 1b   fetcher/rss_fetcher.py      trafilatura full-text (ThreadPool, ENRICH_MAX_WORKERS)
Layer 1c   scrapers/registry.py        Playwright scrapers (sites sem RSS) -> list[Article]
Layer 2    screener/screener_v2.py     BGE-M3 embeddings → Qdrant kNN → RelevanceScorer → list[ScoredArticle]
Layer 3    analyzer/tiered_dispatcher  ScoredArticle → ScreeningResult → Qwen3.5/OpenAI → list[AnalysisResult]
Layer 4    generator/doc_generator.py  python-docx → output/briefing_YYYYMMDD_HHMM.docx
Post-run   data/export_feedback_candidates.py  near-miss audit entries → feedback/candidates.jsonl
Post-run   notifier/email_sender.py    HTML briefing email + ops report email
```

**Dataclass chain:** `Article` → `ScoredArticle` → `AnalysisResult`

**Stats chain:** `pipeline.py` collects `PipelineStats` (per-source counts, tier breakdown,
sentiments) throughout the run and passes it to `notifier/email_sender.py` at the end.

### Screener v2 internals

```
Article.title + full_text[:4000]
    → SentenceTransformer (BAAI/bge-m3, GPU, 1024-dim, normalized)
    → Qdrant kNN (k=5, label_filter="positive")
    → _compute_score(): mean cosine sim to top-k positives
    → _assign_tier(): discard if score < threshold | high ≥0.85 | medium ≥0.55 | low_review ≥threshold
    → classify_tickers(): weighted vote from neighbor payloads + keyword fallback
```

Threshold is the **global minimum** — anything below it is discarded regardless of tier.
Calibrated by `data/calibrate_threshold.py` (LOO-CV, P5 of positive scores) and stored in
`config/calibration.json`. Can be edited manually to tune; run calibrate_threshold to reset.
All three tiers except `discard` go to LLM analysis.

### calibration.json fields

| Field | Active? | Meaning |
|---|---|---|
| `threshold` | **Yes — only this one** | Minimum score to not be discarded. Edit manually to tune. |
| `recall_at_threshold` | No (metadata) | % of known positives that score above threshold |
| `k` | No (metadata) | Neighbors used in LOO-CV calibration run |
| `n_train` | No (metadata) | Qdrant points at calibration time |
| `p5_score` | No (metadata) | = threshold (calibration picks P5 of LOO scores) |
| `p50_score` | No (metadata) | Median score — where a typical positive article lands |
| `p95_score` | No (metadata) | Top-end scores for articles very similar to training set |
| `failed_points` | No (metadata) | LOO errors (0 = clean run) |

### Near-miss review range

`data/export_feedback_candidates.py` controls which discarded articles appear in the Streamlit
review UI via two offsets relative to the threshold:

```python
_NEAR_MISS_MIN_OFFSET = -0.05   # threshold - 0.05  (current: 0.518 - 0.05 = 0.468)
_NEAR_MISS_MAX_OFFSET = +0.35   # threshold + 0.35  (current: 0.518 + 0.35 = 0.868)
```

Articles that were **approved** (score ≥ threshold, tier ≠ discard) are exported separately
by `export_approved()` with `candidate_type="approved"`.

### Email notifications (notifier/)

Two emails sent automatically at end of each `run_pipeline()` when `EMAIL_ENABLED=true`:

- **Email 1 — Briefing** → `EMAIL_BRIEFING_TO`: full HTML briefing (sectors, sentiments,
  summaries, links). Mirrors `SECTOR_GROUPS` from `doc_generator.py`. No attachment.
- **Email 2 — Ops Report** → `EMAIL_OPS_TO`: pipeline metrics — per-source table
  (fetched / approved / sent to LLM / avg score / tier pills), tier breakdown, sentiment counts.
  Subject shows ✅ OK or ❌ ERRO depending on pipeline outcome.

Both emails fail silently (WARNING log, pipeline continues).

`PipelineStats` / `SourceStats` dataclasses live in `notifier/email_sender.py` and are
imported by `pipeline.py`.

### Ollama / Qwen3.5 notes

- `"think": False` must be a **top-level** field in the API payload, not inside `options`.
  Placing it inside `options` is silently ignored by Ollama 0.20.7.
- `format: "json"` is also top-level. With thinking enabled, `message.content` is empty
  (tokens exhausted by thinking); only `message.thinking` is populated.

### Document structure (doc_generator.py)

Grouped by sector only — no per-ticker subheadings. Each article assigned to exactly one
sector (first ticker match wins). Items show: sentiment indicator + title + source/date/link.

```
Resumo Executivo    → sentiment counts + action-required table
Aeroespacial        → EMBR3
Automotivo          → POMO4, RAPT4, FRAS3, MYPK3
Energia e Tecnologia → WEGE3, INTB3
Outros              → GGPS3, ARML3, MILS3
Macro / Setorial    → articles with no matched ticker
```

### Streamlit management app (review_feedback.py)

Three tabs:

- **📊 Base de Dados** — dataset size, Qdrant count, feedback labeled stats
- **🏷️ Revisão** — classify near-miss candidates; sidebar with type/status filters and article navigator
- **📈 Dashboard** — live audit-log dashboard: health alerts for sources that stopped collecting
  or dropped in volume, daily volume chart, per-source stats table, heatmap (fonte × dia),
  tier distribution, ticker frequency

> Streamlit 1.57: use `width="stretch"` instead of `use_container_width=True` for buttons/dataframes.
> Do NOT call `st.rerun()` inside `on_click` callbacks — it's a no-op; Streamlit reruns automatically.

---

## Key Files

| File | Role |
|---|---|
| `config/settings.py` | All settings via pydantic BaseSettings + .env |
| `config/prompts.py` | LLM prompts — screening (legacy) and analysis |
| `config/calibration.json` | Calibrated threshold — edit `threshold` field to tune manually |
| `screener/embedding_store.py` | Qdrant wrapper (upsert, kNN search) |
| `screener/relevance_scorer.py` | Novelty detection scoring + tier assignment |
| `screener/ticker_classifier.py` | Weighted-vote ticker prediction from neighbors |
| `screener/screener_v2.py` | Main v2 entry: batch embed → score → audit log |
| `analyzer/tiered_dispatcher.py` | Routes ScoredArticles to analyze_batch |
| `analyzer/api_analyzer.py` | Ollama/OpenAI calls with JSON parsing + fallback |
| `generator/doc_generator.py` | Word document, sector-grouped |
| `scrapers/registry.py` | Playwright scraper registry — orchestrates non-RSS sources |
| `scrapers/aeroin.py` | AeroinScraper (https://aeroin.net/) |
| `scrapers/playwright_utils.py` | Browser context manager + datetime cascade extractor |
| `scrapers/SITES.md` | Per-site documentation (selectors, paginação, gotchas) |
| `data/ingest_history.py` | ETL: JSONL + feedback/labeled/ → Qdrant |
| `data/calibrate_threshold.py` | LOO-CV threshold calibration |
| `data/export_feedback_candidates.py` | Near-miss export from audit logs |
| `data/ingest_from_word.py` | Extract URLs from .docx + scrape → JSONL |
| `data/ingest_from_summaries.py` | Parse analyst text summaries → JSONL |
| `notifier/email_sender.py` | Email system — PipelineStats, briefing HTML, ops report HTML, SMTP |
| `tools/check_source.py` | Live health check CLI for individual RSS feeds and scrapers |
| `review_feedback.py` | Streamlit app: dataset stats, candidate labeling, pipeline dashboard |
| `news_dataset/clipping_enriched.jsonl` | Main dataset — positives (gitignored) |
| `feedback/candidates.jsonl` | Near-miss candidates (auto-generated, gitignored) |
| `feedback/labeled/reviewed.jsonl` | User-reviewed labels for ingest (gitignored) |

## Where to Make Common Changes

| What | Where |
|---|---|
| Add/remove RSS feeds | `config/settings.py` → `RSS_FEEDS` |
| Add ticker to coverage | `config/settings.py` → `COVERAGE_UNIVERSE` + `config/prompts.py` → `ANALYSIS_SYSTEM_PROMPT` + `generator/doc_generator.py` → `SECTOR_GROUPS` |
| Tune analysis prompt | `config/prompts.py` → `ANALYSIS_SYSTEM_PROMPT` / `ANALYSIS_USER_PROMPT` |
| Change sector grouping in doc | `generator/doc_generator.py` → `SECTOR_GROUPS` |
| Change tier boundaries | `screener/relevance_scorer.py` → `TIER_HIGH`, `TIER_MEDIUM` |
| Change near-miss review range | `data/export_feedback_candidates.py` → `_NEAR_MISS_MIN_OFFSET`, `_NEAR_MISS_MAX_OFFSET` |
| Tune screening threshold manually | `config/calibration.json` → `threshold` |
| Switch to OpenAI analysis | `.env` → `ANALYSIS_BACKEND=openai` |
| Enable/disable email | `.env` → `EMAIL_ENABLED=true/false` |
| Change email recipients | `.env` → `EMAIL_BRIEFING_TO`, `EMAIL_OPS_TO` |

## Outputs

- **Briefing:** `output/briefing_YYYYMMDD_HHMM.docx`
- **Log:** `output/pipeline_YYYYMMDD.log` (DEBUG, UTF-8)
- **Screening audit:** `output/screening_audit_YYYYMMDD.jsonl` — one entry per article with score, tier, tickers, rationale
- **Feedback candidates:** `feedback/candidates.jsonl` — near-misses for labeling
- **Email 1:** HTML briefing → lucas.melotti@safra.com.br + lucas.melotti.safra@gmail.com
- **Email 2:** Ops report → lucas.melotti.safra@gmail.com
