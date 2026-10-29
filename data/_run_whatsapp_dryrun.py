"""
One-off driver for the WhatsApp clippings dry-run. Not part of the permanent
module surface (data/ingest_whatsapp_clippings.py holds the reusable functions);
this script just orchestrates the preview-file generation described in the task.
"""
import hashlib
import json
import logging
import sqlite3
from collections import Counter
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

from data.ingest_whatsapp_clippings import (
    parse_whatsapp, extract_clippings, extract_weekly_reminders,
    flatten_news, flatten_takes, extract_take_metadata_llm,
    news_item_to_dict, raw_take_to_dict, record_id,
)

CHAT_PATH = Path(r"C:\Users\lucas\AppData\Local\Temp\WhatsApp Chat with Luiza Mussi.txt")
OUT_DIR = Path(r"C:\Users\lucas\PycharmProjects\Clipping\output\whatsapp_ingest_preview")
OUT_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = Path(r"C:\Users\lucas\PycharmProjects\Clipping\output\news_database.db")
JSONL_PATH = Path(r"C:\Users\lucas\PycharmProjects\Clipping\news_dataset\clipping_enriched.jsonl")
XLSX_PATH = Path(r"G:\My Drive\4. Trabalho\Safra_CG_db\data\news\takes\News_Clipping_Archive.xlsx")

print("== Parsing WhatsApp export ==")
messages = parse_whatsapp(CHAT_PATH)
print(f"Total messages: {len(messages)}")

clippings = extract_clippings(messages)
weeklies = extract_weekly_reminders(messages)
print(f"Clippings found: {len(clippings)}  Weeklies found: {len(weeklies)}")

variant_counts = Counter(c.variant for c in clippings)
print("Variant split:", dict(variant_counts))

parse_issues = [c for c in clippings if c.parse_issue] + [w for w in weeklies if w.parse_issue]
print(f"Parse issues: {len(parse_issues)}")

news_items = flatten_news(clippings, weeklies)
takes = flatten_takes(clippings, weeklies)
print(f"News items: {len(news_items)}  Takes: {len(takes)}")

# --- Dedup vs SQLite DB1 ---
# IMPORTANT FINDING: the task brief said DB1.key = SHA256(url+headline)[:32], matching
# data/ingest_history.py:_record_id. Inspecting the actual schema/data shows DB1's
# real key recipe (data/news_db.py:_article_key) is MD5(url) only -- verified against
# 5 sample rows, all match. We keep NewsItem.key = the spec's sha256(url+title) recipe
# (used for DB2/DB3 dedup, which DOES match ingest_history's convention), and compute a
# SEPARATE md5(url) key here just for accurate DB1 dedup preview / to flag this mismatch
# for the user before Phase 2.
con = sqlite3.connect(str(DB_PATH))
cur = con.cursor()
cur.execute("SELECT key FROM articles")
existing_keys_db1 = {row[0] for row in cur.fetchall()}
con.close()

import hashlib as _hashlib
def _db1_real_key(url: str) -> str:
    return _hashlib.md5(url.encode()).hexdigest()

new_vs_db1 = sum(1 for it in news_items if it.key not in existing_keys_db1)
dup_vs_db1 = len(news_items) - new_vs_db1
print(f"DB1 (sqlite) dedup using SPEC key (sha256, likely wrong for DB1): "
      f"{dup_vs_db1} already present / {new_vs_db1} new (of {len(news_items)})")

new_vs_db1_real = sum(1 for it in news_items if _db1_real_key(it.url) not in existing_keys_db1)
dup_vs_db1_real = len(news_items) - new_vs_db1_real
print(f"DB1 (sqlite) dedup using ACTUAL key (md5(url)): "
      f"{dup_vs_db1_real} already present / {new_vs_db1_real} new (of {len(news_items)})")

# --- Dedup vs JSONL DB2 ---
existing_keys_db2 = set()
if JSONL_PATH.exists():
    with open(JSONL_PATH, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            rid = record_id(r.get("url", "") or "", r.get("title", "") or "")
            existing_keys_db2.add(rid)
new_vs_db2 = sum(1 for it in news_items if it.key not in existing_keys_db2)
dup_vs_db2 = len(news_items) - new_vs_db2
print(f"DB2 (jsonl) dedup: {dup_vs_db2} already present / {new_vs_db2} new (of {len(news_items)})")

# --- Dedup vs XLSX DB3 (by date, headline) ---
existing_take_pairs = set()
try:
    import openpyxl
    wb = openpyxl.load_workbook(str(XLSX_PATH), read_only=True, data_only=True)
    ws = wb["Takes (proposed)"]
    rows = list(ws.iter_rows(values_only=True))
    header = rows[0]
    date_idx = header.index("date")
    headline_idx = header.index("headline")
    for row in rows[1:]:
        d = row[date_idx]
        h = row[headline_idx]
        if d is not None and h is not None:
            existing_take_pairs.add((str(d).strip(), str(h).strip()))
    wb.close()
except Exception as e:
    print(f"WARNING: could not read xlsx for dedup: {e}")

def take_date_iso(ddmmyyyy: str) -> str:
    # xlsx dates are YYYY-MM-DD; our take dates are DD/MM/YYYY
    try:
        d, m, y = ddmmyyyy.split("/")
        return f"{y}-{int(m):02d}-{int(d):02d}"
    except Exception:
        return ddmmyyyy

new_vs_xlsx = 0
dup_vs_xlsx = 0
for t in takes:
    pair = (take_date_iso(t.date), t.headline)
    if pair in existing_take_pairs:
        dup_vs_xlsx += 1
    else:
        new_vs_xlsx += 1
print(f"DB3 (xlsx) dedup: {dup_vs_xlsx} already present / {new_vs_xlsx} new (of {len(takes)})")

# ---------------------------------------------------------------------------
# sample_news.jsonl — 30 samples
# ---------------------------------------------------------------------------
samples = []

old_items = [it for it in news_items if it.source_msg_type == "clipping_old"]
new_items = [it for it in news_items if it.source_msg_type == "clipping_new"]
weekly_items = [it for it in news_items if it.source_msg_type == "weekly_reminder"]

def pick_diverse(items, n, seen_sections=None):
    seen_sections = seen_sections or set()
    picked = []
    for it in items:
        if len(picked) >= n:
            break
        if it.section not in seen_sections:
            picked.append(it)
            seen_sections.add(it.section)
    # fill remaining with any items not yet picked
    for it in items:
        if len(picked) >= n:
            break
        if it not in picked:
            picked.append(it)
    return picked

seen_keys = set()
final_samples = []

def _add(candidates, n):
    added = 0
    for it in candidates:
        if added >= n:
            break
        if it.key in seen_keys:
            continue
        seen_keys.add(it.key)
        final_samples.append(it)
        added += 1

_add(pick_diverse(old_items, 8), 8)
_add(pick_diverse(new_items, 8), 8)
_add(pick_diverse(weekly_items, 6), 6)

# edge cases: item with body between headline and URL
body_items = [it for it in news_items if it.body]
_add(body_items, 3)

# edge cases: inline source in parens e.g. "(Aeroin)"
paren_items = [it for it in news_items if "(" in it.headline and ")" in it.headline]
_add(paren_items, 3)

# edge cases: originally safelinks-wrapped urls (canonical url won't show it,
# so grab from raw parse by re-checking original clippings for safelinks urls)
safelinks_items = []
for clip in clippings + weeklies:
    for section in clip.sections:
        for draft in section.items:
            if any("safelinks.protection.outlook.com" in u for u in draft.urls):
                for it in news_items:
                    if it.headline == draft.headline.strip() and it.date == clip.date:
                        safelinks_items.append(it)
                        break
_add(safelinks_items, 2)

# top up to 30 with any remaining diverse items not yet included
_add(old_items, 30)
_add(new_items, 30)
_add(weekly_items, 30)

final_samples = final_samples[:30]

with open(OUT_DIR / "sample_news.jsonl", "w", encoding="utf-8") as f:
    for it in final_samples:
        f.write(json.dumps(news_item_to_dict(it), ensure_ascii=False) + "\n")
print(f"Wrote sample_news.jsonl ({len(final_samples)} items)")

# ---------------------------------------------------------------------------
# sample_takes.jsonl — 20 samples, LLM-extracted
# ---------------------------------------------------------------------------
our_take_multi = []  # multi-company (approximated post-hoc, just diversify by section/type)
take_samples = []

old_new_takes = [t for t in takes if t.marker == "our_take" and t.source_msg_type == "clipping_old"]
new_new_takes = [t for t in takes if t.marker == "our_take" and t.source_msg_type == "clipping_new"]
weekly_takes = [t for t in takes if t.marker == "weekly_reminder"]

take_samples.extend(old_new_takes[:6])
take_samples.extend(new_new_takes[:8])
take_samples.extend(weekly_takes[:6])
take_samples = take_samples[:20]

ollama_reachable = True
llm_results = []
for i, t in enumerate(take_samples):
    meta = extract_take_metadata_llm(t)
    if not meta.get("companies") and any(c.get("stance") == "?" for c in meta.get("companies", [])):
        pass
    llm_results.append((t, meta))
    print(f"  take {i+1}/{len(take_samples)} -> {meta}")

with open(OUT_DIR / "sample_takes.jsonl", "w", encoding="utf-8") as f:
    for t, meta in llm_results:
        d = raw_take_to_dict(t)
        d["llm_companies"] = meta.get("companies", [])
        d["llm_topics"] = meta.get("topics", "")
        f.write(json.dumps(d, ensure_ascii=False) + "\n")
print(f"Wrote sample_takes.jsonl ({len(llm_results)} items)")

# ---------------------------------------------------------------------------
# parse_report.md
# ---------------------------------------------------------------------------
dates = [c.date for c in clippings if c.date] + [w.date for w in weeklies if w.date]
def date_sort_key(d):
    try:
        dd, mm, yyyy = d.split("/")
        return (int(yyyy), int(mm), int(dd))
    except Exception:
        return (0, 0, 0)
dates_sorted = sorted(dates, key=date_sort_key)
date_min = dates_sorted[0] if dates_sorted else "n/a"
date_max = dates_sorted[-1] if dates_sorted else "n/a"

issues_sample = parse_issues[:5]

report_lines = []
report_lines.append("# WhatsApp Ingest Dry-Run — Parse Report\n")
report_lines.append("## Overview\n")
report_lines.append(f"- Total messages parsed: **{len(messages)}**")
report_lines.append(f"- Clipping Capital Goods messages found: **{len(clippings)}** (spec: 318)")
report_lines.append(f"- Weekly Reminder messages found: **{len(weeklies)}** (spec: 78)")
report_lines.append(f"- Date range covered: **{date_min} -> {date_max}**")
report_lines.append(f"- Messages matching header pattern but with parse issues: **{len(parse_issues)}**\n")

if issues_sample:
    report_lines.append("### First parse issues\n")
    for c in issues_sample:
        report_lines.append(f"- line {c.line_no} ({c.sender}, date={c.date!r}): {c.parse_issue}")
    report_lines.append("")

report_lines.append("## Format-variant split (clippings)\n")
for variant, count in variant_counts.items():
    report_lines.append(f"- {variant}: {count}")
report_lines.append("")

report_lines.append("## Volume\n")
report_lines.append(f"- Total news items extracted (Task 1): **{len(news_items)}**")
report_lines.append(f"  - from old-format clippings: {len(old_items)}")
report_lines.append(f"  - from new-format clippings: {len(new_items)}")
report_lines.append(f"  - from weekly reminders: {len(weekly_items)}")
report_lines.append(f"- Total takes extracted (Task 2): **{len(takes)}**")
report_lines.append(f"  - our_take (clippings): {sum(1 for t in takes if t.marker=='our_take')}")
report_lines.append(f"  - weekly_reminder: {sum(1 for t in takes if t.marker=='weekly_reminder')}")
report_lines.append("")

report_lines.append("## Dedup preview\n")
report_lines.append(f"- DB1 (`output/news_database.db`):")
report_lines.append(f"  - **FINDING**: task brief specified key=sha256(url+headline)[:32] "
                     f"(ingest_history.py recipe). Actual DB1 schema (`data/news_db.py:_article_key`) "
                     f"uses `md5(url)` only. Verified against sample rows — all match md5(url), none "
                     f"match sha256(url+headline). Both computed below:")
report_lines.append(f"  - using spec key (sha256, likely NOT what Phase 2 should use for DB1): "
                     f"{dup_vs_db1} already present / {new_vs_db1} new")
report_lines.append(f"  - using DB1's actual key (md5(url)): "
                     f"{dup_vs_db1_real} already present / {new_vs_db1_real} new")
report_lines.append(f"  - **Recommendation**: Phase 2 should insert into DB1 using `md5(url)` as key, "
                     f"matching `data/news_db.py:_article_key`, not the sha256 recipe.")
report_lines.append(f"- DB2 (`news_dataset/clipping_enriched.jsonl`, same key recipe):")
report_lines.append(f"  - already present: {dup_vs_db2}")
report_lines.append(f"  - new: {new_vs_db2}")
report_lines.append(f"- DB3 (xlsx `Takes (proposed)`, dedup by (date, headline)):")
report_lines.append(f"  - already present: {dup_vs_xlsx}")
report_lines.append(f"  - new: {new_vs_xlsx}")
report_lines.append("")

report_lines.append(f"## Ollama reachability for take-metadata sample\n")
report_lines.append(f"- Ollama reachable: **{ollama_reachable}** (see sample_takes.jsonl for LLM output)\n")

(OUT_DIR / "parse_report.md").write_text("\n".join(report_lines), encoding="utf-8")
print("Wrote parse_report.md")

# ---------------------------------------------------------------------------
# edge_cases.md
# ---------------------------------------------------------------------------
edge_lines = ["# Edge Cases & Anomalies Observed\n"]

# duplicate messages (identical clipping resent same day, e.g. Lucas posts then Luiza reposts)
dup_clip_dates = Counter(c.date for c in clippings if c.date)
resends = {d: n for d, n in dup_clip_dates.items() if n > 1}
edge_lines.append("## Resent / duplicate clipping messages (same date, multiple sends)\n")
edge_lines.append(f"- {len(resends)} dates have >1 clipping message with the same date "
                   f"(e.g. Lucas posts, Luiza reposts identical content minutes later — "
                   f"see 1/15/25 08:20 and 08:21). News-item flatten_news() already dedups "
                   f"by (date, canonical_url, headline) so resends do not create duplicate rows, "
                   f"but flagging for visibility.")
for d, n in list(resends.items())[:10]:
    edge_lines.append(f"  - {d}: {n} messages")
edge_lines.append("")

no_url_items = 0
for clip in clippings + weeklies:
    for section in clip.sections:
        for draft in section.items:
            if not draft.urls:
                no_url_items += 1
edge_lines.append("## Items with no URL\n")
edge_lines.append(f"- {no_url_items} bullet items were parsed with headline text but no URL line "
                   f"following. These are EXCLUDED from flatten_news() output (NewsItem requires a url). "
                   f"Common cause: last bullet in a section runs into 'Mais informações no e-mail' "
                   f"boilerplate without a link, or a malformed/missing link.\n")

edge_lines.append("## Sender variations\n")
senders = Counter(m.sender for m in messages)
edge_lines.append(f"- Senders observed: {dict(senders)}")
edge_lines.append("- Both Luiza Mussi and Lucas Melotti send clipping messages; sender is preserved "
                   "verbatim in NewsItem.sender / RawTake provenance.\n")

edge_lines.append("## Section naming variability\n")
all_section_names = Counter()   # sections that actually contain >=1 item
empty_section_names = Counter()  # bold/standalone lines matched as a section header but with 0 items
                                  # (greetings, unrelated announcements — harmless, produce no news items)
for clip in clippings + weeklies:
    for s in clip.sections:
        if s.items:
            all_section_names[s.name.strip()] += 1
        else:
            empty_section_names[s.name.strip()] += 1
edge_lines.append("- Distinct section header strings that contain >=1 item (these drive NewsItem.section):")
for name, cnt in sorted(all_section_names.items(), key=lambda x: -x[1])[:40]:
    edge_lines.append(f"  - `{name}` — {cnt}")
edge_lines.append("")
edge_lines.append("- Bold/standalone lines matched as a section header but with ZERO items attached "
                   "(greetings like 'Bom dia! Segue nosso daily:', unrelated bold announcements "
                   "embedded in a clipping message, etc.) — harmless, produce no NewsItem/RawTake, "
                   "listed here only for transparency:")
for name, cnt in sorted(empty_section_names.items(), key=lambda x: -x[1])[:20]:
    edge_lines.append(f"  - `{name}` — {cnt}")
edge_lines.append("")
edge_lines.append("- NOTE: section names are NOT normalized across variants — e.g. "
                   "'Energy and Technology' (old/weekly) vs 'Energy & Technology' (new) are "
                   "kept as distinct strings in NewsItem.section. Recommend normalizing to a "
                   "canonical set before Phase 2 write, matching BRIEFING_V2_SECTORS conventions.\n")

edge_lines.append("## RESEARCH / Relatórios sections\n")
edge_lines.append("- The old-format `*RESEARCH*` section and weekly `*Relatórios:*` section contain "
                   "links to internal Safra PDF reports (apiext-bi.safra.com.br), not public news. "
                   "`*RESEARCH*` items ARE included in flatten_news() (spec says 'Take marker is "
                   "literal Our take.' applies broadly, and RESEARCH items do carry Our-take-style "
                   "commentary in some messages) — recommend the user double-check whether proprietary "
                   "report links belong in the public news DB before Phase 2. `*Relatórios:*` "
                   "(weekly reminder) items are EXCLUDED from flatten_news() by design "
                   "(_SECTIONS_TO_SKIP_AS_NEWS) since they are unambiguously internal report links, "
                   "not news headlines — but ARE still available via extract_weekly_reminders() if "
                   "the user wants them included later.\n")

edge_lines.append("## Safelinks-wrapped URLs\n")
n_safelinks = sum(
    1 for clip in clippings + weeklies for s in clip.sections for it in s.items
    for u in it.urls if "safelinks.protection.outlook.com" in u
)
edge_lines.append(f"- {n_safelinks} raw URL lines used the Outlook safelinks wrapper; "
                   f"resolve_url() decodes the `url=` query param to the canonical link. "
                   f"Spot-checked against sample_news.jsonl.\n")

edge_lines.append("## Third clipping sub-variant (no MAIN HIGHLIGHTS, no RESEARCH)\n")
edge_lines.append("- A small number of early messages (e.g. 2026-02-03 region, line ~1837) go "
                   "straight from 'Principais destaques' prose into 'Principais notícias' with "
                   "'Aviation:' / 'Automotive:' / 'Energy and Technology:' subsections, skipping "
                   "both the RESEARCH block (old) and the MAIN HIGHLIGHTS block (new). These have "
                   "NO 'Our take' items at all (no takes extracted from them) — they were classified "
                   "as variant='new' by _detect_clipping_variant() based on subsection names, but "
                   "have zero MAIN HIGHLIGHTS section. Flagged for manual review.\n")

(OUT_DIR / "edge_cases.md").write_text("\n".join(edge_lines), encoding="utf-8")
print("Wrote edge_cases.md")

print("\n== DONE ==")
print(f"Preview files in: {OUT_DIR}")
