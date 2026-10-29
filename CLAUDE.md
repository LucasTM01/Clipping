# CLAUDE.md

Guidance for Claude Code when working in this repository.

## Project Purpose

Automated daily briefing pipeline for Brazilian equity research — Capital Goods & Industrials.
Collects RSS news, filters by relevance via embedding-based screener (BGE-M3 + Qdrant),
analyzes with a local LLM (Qwen3.5:9b via Ollama), and outputs a sector-grouped Word document.
After each run, sends two emails: a sector-grouped briefing and an ops report with pipeline metrics.

The briefing email has two versions, selected by `--clipping_version` (default **v2**):
- **v2** (default) — "Safra's Morning Notes - AI View - Capital Goods": ENGLISH, factual,
  one line per article grouped by sector. NO investment opinion and NO ticker/company
  attribution — just the news, translated/condensed from the headline (+ subtitle/body
  only when too generic). Mirrors the analyst's `.docx` clipping format. Starts with a
  process-stats strip (news read / approved / in briefing). Built by `analyzer/briefing_v2.py`.
- **v1** (`--clipping_version v1`) — legacy Portuguese HTML briefing with sentiment badges,
  per-article thesis summaries and an action table. Code kept intact in `email_sender.py`.

Optionally, a **deep dive** (3rd email) runs a two-stage analysis on the approved articles:
Stage A (LOCAL Qwen) summarizes each article, extracts disclosed numbers (with YoY/QoQ/MoM
when present) and assigns an English group; Stage B (API — DeepSeek default, OpenAI optional)
produces a macro synthesis connecting the day's stories. Enable via `DEEP_DIVE_ENABLED=true`
or `--deep-dive`.

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

# Briefing email version (default v2 = English "AI View"; v1 = legacy PT briefing)
python main.py                          # v2 by default
python main.py --clipping_version v1    # send the legacy v1 briefing instead

# Legacy screener v1 (keyword + Ollama) — kept for regression comparison
python main.py --legacy-screener

# Deep dive — force the detailed 2-stage analysis (3rd email), ignoring .env
python main.py --deep-dive
python main.py --no-deep-dive   # disable even if DEEP_DIVE_ENABLED=true
```

## Tests

```powershell
python -m pytest tests/ scrapers/tests/ -v   # unit + scraper parser tests (no network)
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

## Ticker reclassification (fix sector misclassification)

The dataset is heavily EMBR3-biased (≈56% of ticker-labeled articles) and most macro
articles are labeled with no ticker, so the kNN voter falls back to EMBR3 by default —
"everything becomes Aerospace". To rebalance, reclassify article tickers and re-ingest:

```powershell
streamlit run reclassify_tickers.py   # review/correct tickers; empty selection = macro
python -m data.ingest_history         # picks up feedback/labeled/ticker_corrections.jsonl
python -m data.calibrate_threshold
```

The tool auto-flags **suspicious** records using STRONG company identifiers (company
name/brand, not generic sector keywords): macro articles that actually name a covered
company, missing tickers, or label/text mismatches. Corrections are written to
`feedback/labeled/ticker_corrections.jsonl` with `_reclassified=true`; `ingest_history`
applies those tickers verbatim (empty list = macro, no keyword backfill).

## Source Health Check

```powershell
# List all sources (10 RSS + 19 scrapers)
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
BRIEFING_MAX_ARTICLES=50         # top-N by score; 0 = no limit

# Email notifications (optional)
EMAIL_ENABLED=true
EMAIL_SMTP_HOST=smtp.gmail.com
EMAIL_SMTP_PORT=587
EMAIL_USERNAME=sender@gmail.com
EMAIL_PASSWORD=xxxx xxxx xxxx xxxx   # Google App Password (not account password)
EMAIL_FROM=sender@gmail.com
EMAIL_BRIEFING_TO=["analyst@firm.com","analyst@gmail.com"]
EMAIL_OPS_TO=["analyst@gmail.com"]

# Deep dive — detailed 2-stage analysis (3rd email)
DEEP_DIVE_ENABLED=false
SUMMARY_BACKEND=ollama            # Stage A (per-article): "ollama" | "openai" | "deepseek"
SUMMARY_MODEL=                    # override; "" → analysis_model (ollama) / backend default
SYNTHESIS_BACKEND=deepseek        # Stage B (macro synthesis): "deepseek" | "openai"
SYNTHESIS_MODEL=                  # override; "" → backend default
DEEP_DIVE_MAX_WORKERS=3           # Stage A parallelism (Ollama queues requests)
DEEP_DIVE_MAX_FULL_TEXT=6000      # chars of full_text fed to Stage A
DEEP_DIVE_TO=["analyst@gmail.com"]

# DeepSeek (OpenAI-compatible API — reuses the openai SDK with a custom base_url)
DEEPSEEK_API_KEY=sk-...
DEEPSEEK_MODEL=deepseek-chat
DEEPSEEK_BASE_URL=https://api.deepseek.com
```

Qdrant runs in Docker: `docker compose up -d`. `main.py` sobe tudo sozinho antes do
pipeline: `ensure_qdrant()` (Docker Desktop via `docker desktop start` → `docker compose up -d`,
para o container ao final se foi ele quem subiu) e `ensure_ollama()` (`ollama serve` em
background se `OLLAMA_BASE_URL` local não responde; fica rodando ao final; log em
`output/ollama_serve.log`). Falha em subir qualquer um NÃO aborta o run — Qdrant fora →
pipeline falha na triagem e o email de ops sai com ❌ ERRO; Ollama fora → fallback.

Playwright precisa do Chromium e do Chrome instalados:

```powershell
pip install -r requirements.txt
playwright install chromium   # para scrapers headless
playwright install chrome     # para enrich_with_playwright (Reuters/Bloomberg)
```

O `enrich_with_playwright` usa primeiro o perfil dedicado do pipeline
(`~/.clipping/pipeline_profile`, ver `tools/setup_pipeline_browser.py`), depois o perfil
real do Chrome (falha se o Chrome estiver aberto, e sempre no Chrome 136+). Hoje Reuters
(401) e Bloomberg (403) bloqueiam ambos — após 3 bloqueios seguidos num domínio o enrich
pula o resto dele, então custa ~1s por run.

---

## Architecture

Data flows top-to-bottom through `pipeline.py → run_pipeline()`. No layer reads back into a previous one.

```
Layer 1    fetcher/rss_fetcher.py      requests (timeout) + feedparser → list[Article]
Layer 1b   fetcher/rss_fetcher.py      trafilatura full-text (ThreadPool, ENRICH_MAX_WORKERS)
                                        skips _NO_ENRICH_DOMAINS (reuters.com, bloomberg.com)
                                        and _NEVER_ENRICH_DOMAINS (ft.com, weg.net, news.google.com)
Layer 1b'  fetcher/rss_fetcher.py      enrich_with_playwright: Reuters/Bloomberg via Chrome
                                        pipeline profile → sequential; gives up per domain after 3× 401/403
Layer 1c   scrapers/registry.py        scrapers (sites sem RSS) -> list[Article] — see scrapers/SITES.md
                                        WordPressScraper (REST API): Autodata, Canal Solar,
                                          Diário do Transporte, Aerospace Global News
                                        GoogleNewsScraper (site: query): Reuters sections, WEG News
                                        BloombergScraper: news sitemap + Industries RSS
                                        CVMScraper: ListarDocumentos JSON + PDF (pdfplumber)
Layer 2    screener/screener_v2.py     BGE-M3 embeddings → Qdrant kNN → RelevanceScorer → list[ScoredArticle]
Layer 3    analyzer/tiered_dispatcher  ScoredArticle → ScreeningResult → Qwen3.5/OpenAI → list[AnalysisResult]
Layer 4    generator/doc_generator.py  python-docx → output/briefing_YYYYMMDD_HHMM.docx
Post-run   data/export_feedback_candidates.py  near-miss audit entries → feedback/candidates.jsonl
Post-run   analyzer/briefing_v2.py     (v2 default) LLM → English factual one-liners + sector
Post-run   notifier/email_sender.py    briefing email (v2 default / v1 legacy) + ops report email
Post-run   data/drive_sync.py          articles table → DataHandler lake (parquet+csv) via G: mount;
                                        if G: is unreachable (S4U scheduled task) → stage in
                                        output/datahandler_export/ + `rclone copyto` (Drive API,
                                        remote `gdrive`, DATAHANDLER_REMOTE) — updates files in place
Post-run   analyzer/deep_dive.py        (opt) Stage A local summaries+numbers+group →
                                        Stage B API macro synthesis → detailed 3rd email
```

### Deep dive — two-stage detailed analysis (analyzer/deep_dive.py)

Runs after the standard emails when `DEEP_DIVE_ENABLED=true` (or `--deep-dive`). Consumes the
approved `ScoredArticle` list (same `scored` the briefing uses, post `BRIEFING_MAX_ARTICLES`).

```
Stage A (LOCAL — summary_backend, default ollama/Qwen)
  ensure_full_text()  → re-extract missing bodies via trafilatura (ThreadPool)
  _preprocess()       → collapse whitespace, truncate to DEEP_DIVE_MAX_FULL_TEXT
  summarize_batch()   → per-article JSON: {group, summary, sentiment, metrics[]}
                        metrics carry value + period + yoy/qoq/mom (only when explicit)
  → list[ArticleBrief]

Stage B (API — synthesis_backend, default deepseek; openai optional)
  synthesize()        → feeds ONLY the compact briefs (not full text) to the API
  → MarketSynthesis(overview, themes[])   # how the day's stories connect

notifier.send_deep_dive_email()  → groups by English DEEP_DIVE_GROUPS, renders a
  metrics table per article + the macro synthesis on top.
```

Groups (`config/prompts.py` → `DEEP_DIVE_GROUPS`): Aerospace & Defence, Automotive & Industrials,
Energy & Technology, Others, Macro. The model may propose a new group if none fit — it's preserved.
DeepSeek uses the OpenAI SDK pointed at `DEEPSEEK_BASE_URL`. All deep-dive failures are non-fatal
(WARNING log, pipeline continues). The docx and the first two emails are unchanged.

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

Keyword matching (`ticker_classifier._keyword_fallback`) uses **word-boundary regex**
(`\b`), not naive substring — so "weg" no longer matches "norwegian", "mills" no longer
matches "windmills", "6x1" matches only the isolated token. EMBR3 keywords are
company-specific only (no generic "aviação"/"aircraft"/"aerospace") to stop airline/
Boeing/Airbus macro news from defaulting to Embraer.

The LLM analysis step (Layer 3) classifies tickers **independently** from the article
text — the screener's predicted tickers are NOT passed into the prompt (avoids anchoring
on EMBR3). Screener tickers remain only as a fallback when the LLM call fails. The doc's
sector grouping (Layer 4) is driven by `AnalysisResult.tickers`, i.e. the LLM's call.

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

### Near-miss review range and daily limit

`data/export_feedback_candidates.py` controls which discarded articles appear in the Streamlit
review UI via two offsets relative to the threshold:

```python
_NEAR_MISS_MIN_OFFSET = -0.05   # threshold - 0.05
_NEAR_MISS_MAX_OFFSET = +0.35   # threshold + 0.35
_MAX_NEAR_MISS_PER_RUN = 100    # cap per pipeline run — keeps top scores, drops the rest
```

If more articles qualify than the cap, they are sorted by `relevance_score` descending and
only the top 100 (highest-scoring, most borderline) are written to the review file.

Override from the CLI: `python -m data.export_feedback_candidates --limit 50`

Articles that were **approved** (score ≥ threshold, tier ≠ discard) are exported separately
by `export_approved()` with `candidate_type="approved"` — no cap applied there.

### Email notifications (notifier/)

Two emails sent automatically at end of each `run_pipeline()` when `EMAIL_ENABLED=true`:

- **Email 1 — Briefing** → `EMAIL_BRIEFING_TO`. Version chosen by `clipping_version`:
  - **v2 (default)** `_build_briefing_v2_html` — English, factual, one line per article,
    grouped by `BRIEFING_V2_SECTORS` (Aerospace & Defense / Automotive / Energy and
    Technology / Others). No opinion, no ticker/company attribution. Subject:
    `Safra's Morning Notes - AI View - Capital Goods`. Items generated by
    `analyzer/briefing_v2.py` via a dedicated LLM pass over the approved `ScoredArticle`s
    (same `scored` the docx uses, post `BRIEFING_MAX_ARTICLES`). Visual mirrors the
    analyst `.docx` (ink `#00003C`, Arial 10pt, bold sector + colon, bullets
    `text. — Source(hyperlink)`), preceded by a stats strip (read / approved / in briefing).
  - **v1** `_build_briefing_html` — legacy PT HTML (sectors, sentiment badges, thesis
    summaries, action table). Mirrors `SECTOR_GROUPS` from `doc_generator.py`.
    v2 requires the v2 screener; under `--legacy-screener` it falls back to v1.
- **Email 2 — Ops Report** → `EMAIL_OPS_TO`: pipeline metrics — per-source table
  (fetched / approved / sent to LLM / avg score / tier pills), tier breakdown, sentiment counts.
  Subject shows ✅ OK or ❌ ERRO depending on pipeline outcome.

All emails fail silently (WARNING log, pipeline continues).

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
| `analyzer/deep_dive.py` | Deep dive: Stage A local summaries+numbers+group, Stage B API synthesis |
| `analyzer/briefing_v2.py` | Briefing v2: per-article English factual one-liner + sector (LLM); source short-name map |
| `generator/doc_generator.py` | Word document, sector-grouped |
| `scrapers/registry.py` | Playwright scraper registry — orchestrates non-RSS sources |
| `scrapers/aeroin.py` | AeroinScraper (https://aeroin.net/) |
| `scrapers/cvm.py` | CVMScraper — Fatos Relevantes + Comunicados ao Mercado (rad.cvm.gov.br, HTTP only) |
| `scrapers/wordpress.py` | WordPressScraper — generic WP REST API source (one instance per site) |
| `scrapers/google_news.py` | GoogleNewsScraper — `site:` query + link decoding, for sites that block bots (Reuters, WEG) |
| `scrapers/bloomberg.py` | BloombergScraper — news sitemap + Industries RSS, keyword-split into Energy/Transportation/Industries |
| `scrapers/randoncorp.py` | RandoncorpScraper — Randoncorp blog (day-precision dates) |
| `scrapers/playwright_utils.py` | Browser context manager + datetime cascade extractor + `persistent_browser_context()` (Chrome real profile) |
| `scrapers/SITES.md` | Per-site documentation (selectors, paginação, gotchas) |
| `data/ingest_history.py` | ETL: JSONL + feedback/labeled/ → Qdrant |
| `data/calibrate_threshold.py` | LOO-CV threshold calibration |
| `data/export_feedback_candidates.py` | Near-miss export from audit logs |
| `data/ingest_from_word.py` | Extract URLs from .docx + scrape → JSONL |
| `data/ingest_from_summaries.py` | Parse analyst text summaries → JSONL |
| `notifier/email_sender.py` | Email system — PipelineStats, briefing HTML, ops report HTML, SMTP |
| `tools/check_source.py` | Live health check CLI for individual RSS feeds and scrapers |
| `review_feedback.py` | Streamlit app: dataset stats, candidate labeling, pipeline dashboard |
| `reclassify_tickers.py` | Streamlit app: review/correct dataset tickers to fix EMBR3 bias → `ticker_corrections.jsonl` |
| `news_dataset/clipping_enriched.jsonl` | Main dataset — positives (gitignored) |
| `feedback/candidates.jsonl` | Near-miss candidates (auto-generated, gitignored) |
| `feedback/labeled/reviewed.jsonl` | User-reviewed labels for ingest (gitignored) |

## Where to Make Common Changes

| What | Where |
|---|---|
| Add/remove RSS feeds | `config/settings.py` → `RSS_FEEDS` |
| Add a WordPress site | `scrapers/registry.py` → `WordPressScraper("Name", "https://site")` + `SCRAPERS_ENABLED` |
| Add a Reuters section | `scrapers/registry.py` → `ReutersScraper("Name", "/section/path/")` + `SCRAPERS_ENABLED` |
| Tune Bloomberg section split | `scrapers/bloomberg.py` → `_TRANSPORTATION_RE` / `_ENERGY_RE` |
| Add ticker to coverage | `config/settings.py` → `COVERAGE_UNIVERSE` + `config/prompts.py` → `ANALYSIS_SYSTEM_PROMPT` + `generator/doc_generator.py` → `SECTOR_GROUPS` |
| Tune analysis prompt | `config/prompts.py` → `ANALYSIS_SYSTEM_PROMPT` / `ANALYSIS_USER_PROMPT` |
| Tune ticker keywords (word-boundary) | `config/settings.py` → `COVERAGE_UNIVERSE[...]["keywords"]` |
| Tune ticker disambiguation rules (LLM) | `config/prompts.py` → `ANALYSIS_SYSTEM_PROMPT` (CLASSIFICAÇÃO DE TICKERS block) |
| Tune reclassification suspicion flags | `reclassify_tickers.py` → `STRONG_IDENTIFIERS` |
| Reclassify dataset tickers (UI) | `streamlit run reclassify_tickers.py` → re-ingest + recalibrate |
| Change sector grouping in doc | `generator/doc_generator.py` → `SECTOR_GROUPS` |
| Change tier boundaries | `screener/relevance_scorer.py` → `TIER_HIGH`, `TIER_MEDIUM` |
| Change near-miss review range | `data/export_feedback_candidates.py` → `_NEAR_MISS_MIN_OFFSET`, `_NEAR_MISS_MAX_OFFSET` |
| Change daily near-miss cap | `data/export_feedback_candidates.py` → `_MAX_NEAR_MISS_PER_RUN` |
| Add blocked enrich domain | `fetcher/rss_fetcher.py` → `_NO_ENRICH_DOMAINS` (trafilatura skip, Playwright tries) or `_NEVER_ENRICH_DOMAINS` (no fetch at all) |
| Tune screening threshold manually | `config/calibration.json` → `threshold` |
| Switch to OpenAI analysis | `.env` → `ANALYSIS_BACKEND=openai` |
| Enable/disable email | `.env` → `EMAIL_ENABLED=true/false` |
| Change email recipients | `.env` → `EMAIL_BRIEFING_TO`, `EMAIL_OPS_TO` |
| Switch briefing email version | CLI `--clipping_version v1\|v2` (default v2) |
| Tune v2 briefing text/sectors | `config/prompts.py` → `BRIEFING_V2_SYSTEM_PROMPT` / `BRIEFING_V2_SECTORS` |
| Change v2 source short names | `analyzer/briefing_v2.py` → `_SOURCE_DISPLAY` |
| Change v2 email visual | `notifier/email_sender.py` → `_build_briefing_v2_html` |
| Enable/disable deep dive (3rd email) | `.env` → `DEEP_DIVE_ENABLED` or CLI `--deep-dive`/`--no-deep-dive` |
| Switch deep dive synthesis provider | `.env` → `SYNTHESIS_BACKEND=deepseek\|openai` |
| Change deep dive groups | `config/prompts.py` → `DEEP_DIVE_GROUPS` (+ prompt) |
| Tune deep dive prompts | `config/prompts.py` → `DEEP_DIVE_SUMMARY_*` / `SYNTHESIS_*` |
| Change deep dive recipients | `.env` → `DEEP_DIVE_TO` |

## Outputs

- **Briefing:** `output/briefing_YYYYMMDD_HHMM.docx`
- **Log:** `output/pipeline_YYYYMMDD.log` (DEBUG, UTF-8)
- **Screening audit:** `output/screening_audit_YYYYMMDD.jsonl` — one entry per article with score, tier, tickers, rationale
- **Feedback candidates:** `feedback/candidates.jsonl` — near-misses for labeling
- **Email 1:** Briefing (v2 "AI View" English by default, or v1 legacy PT via `--clipping_version v1`) → lucas.melotti@safra.com.br + lucas.melotti.safra@gmail.com
- **Email 2:** Ops report → lucas.melotti.safra@gmail.com
- **Email 3 (opt):** Deep dive — detailed per-article numbers + macro synthesis → `DEEP_DIVE_TO`
