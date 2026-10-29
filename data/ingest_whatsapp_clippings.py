"""
DRY-RUN ingestion pipeline: WhatsApp chat export -> preview of what would be
written to the three production news databases.

This module NEVER writes to output/news_database.db, news_dataset/clipping_enriched.jsonl,
or the Drive xlsx. It only parses the chat export and produces preview files under
output/whatsapp_ingest_preview/ for manual validation before a future Phase 2 (real writes).

Pipeline:
  parse_whatsapp()            -> list[Message]
  extract_clippings()         -> list[Clipping]      (Category A: daily "Clipping Capital Goods")
  extract_weekly_reminders()  -> list[Clipping]       (Category B: "weekly reminder" — same shape)
  flatten_news()               -> list[NewsItem]       (DB1 + DB2 preview rows)
  flatten_takes()               -> list[RawTake]        (DB3 preview rows, pre-LLM)
  extract_take_metadata_llm()  -> dict                  (per-take company/stance/topics via Ollama)
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import requests

from config.settings import settings

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Regexes
# ---------------------------------------------------------------------------

# WhatsApp exports differ by platform, and the same chat has arrived in both:
#   Android: "7/20/26, 08:36 - Luiza Mussi: body"
#   iOS:     "[7/20/26, 8:36:30 AM] Luiza Mussi: body"
# iOS also emits system lines as "[7/8/26, 9:37:48 PM] - text" (no sender); those must be
# recognised as message boundaries, or they get glued onto the previous message's body.
# `\s` covers the narrow no-break space iOS puts before AM/PM.
_HEADER_RE = re.compile(
    r"^(?P<date>\d{1,2}/\d{1,2}/\d{2}), (?P<time>\d{1,2}:\d{2})(?:\s?[AP]M)? - "
    r"(?P<sender>[^:]+): (?P<body>.*)$"
)
_HEADER_IOS_RE = re.compile(
    r"^\[(?P<date>\d{1,2}/\d{1,2}/\d{2}), (?P<time>\d{1,2}:\d{2})(?::\d{2})?(?:\s?[AP]M)?\] "
    r"(?P<sender>[^:\-][^:]*?): (?P<body>.*)$"
)
_SYSTEM_IOS_RE = re.compile(
    r"^\[(?P<date>\d{1,2}/\d{1,2}/\d{2}), (?P<time>\d{1,2}:\d{2})(?::\d{2})?(?:\s?[AP]M)?\] - "
    r"(?P<body>.*)$"
)


def _match_header(line: str):
    """Return (date, time, sender, body) for a message-start line, else None."""
    for rx in (_HEADER_RE, _HEADER_IOS_RE):
        m = rx.match(line)
        if m:
            return m.group("date"), m.group("time"), m.group("sender").strip(), m.group("body")
    m = _SYSTEM_IOS_RE.match(line)
    if m:
        return m.group("date"), m.group("time"), "System", m.group("body")
    return None

_CLIPPING_MARK_RE = re.compile(r"Clipping Capital Goods")
_CLIPPING_DATE_RE = re.compile(r"(\d{1,2}/\d{1,2}/\d{4})")
_WEEKLY_RE = re.compile(r"weekly reminder", re.IGNORECASE)

# A "section header" line: mostly bold markup, no bullet, no URL, short.
# e.g. "*RESEARCH*", "*Aviation:*", "*MAIN HIGHLIGHTS:*", "*Aerospace & Defense:*"
_SECTION_HEADER_RE = re.compile(r"^\*+\s*([^*]+?)\s*\*+\s*:?\s*$")

_BULLET_RE = re.compile(r"^[-•]\s+(.*)$")
_URL_LINE_RE = re.compile(r"^\s*(https?://\S+)\s*$")
_URL_INLINE_RE = re.compile(r"https?://\S+")

# "Our take." (old, period) or "*Our take:*" / "*Our take*:" (new, colon+asterisks)
_TAKE_MARKER_RE = re.compile(
    r"^\*?\s*Our take\s*\*?\s*[:.]\s*(.*)$", re.IGNORECASE
)

# Meta-headers that introduce a group of subsections but are not sections
# themselves (no items are ever attached directly to them).
_META_HEADERS = {
    "principais notícias", "principais noticias",
}

# Known section names, used as a fallback for the rare plain-text clipping
# sub-variant (e.g. 1/21/25) that omits *bold* markup entirely — a bare line
# like "Aviation" with no asterisks. Only lines that (after stripping a
# trailing colon) case-insensitively equal one of these are treated as a
# section header when the bold-markup regex doesn't match.
_KNOWN_SECTION_NAMES = {
    "research", "main highlights", "aviation", "automotive", "auto parts",
    "aerospace & defense", "aerospace and defense", "others",
    "energy and technology", "energy & technology",
    "relatório", "relatórios", "relatorio", "relatorios",
    "principais notícias", "principais noticias",
}

_SAFELINKS_HOST_HINT = "safelinks.protection.outlook.com"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Message:
    line_no: int
    date: str          # M/D/YY as in source
    time: str
    sender: str
    body: str          # full body incl. continuation lines, joined by \n


@dataclass
class NewsItemDraft:
    headline: str
    body: str | None
    urls: list[str]
    take: str | None


@dataclass
class Section:
    name: str
    items: list[NewsItemDraft] = field(default_factory=list)


@dataclass
class Clipping:
    date: str            # DD/MM/YYYY (from clipping header, Category A)
                          # or clipping_date_msg-derived DD/MM/YYYY for weekly (Category B)
    sender: str
    variant: str          # "old" | "new" | "weekly" | "unrecognized"
    sections: list[Section]
    line_no: int
    parse_issue: str | None = None


@dataclass
class NewsItem:
    key: str
    date: str            # DD/MM/YYYY
    sender: str
    section: str
    headline: str
    body: str | None
    url: str
    source_msg_type: str  # "clipping_old" | "clipping_new" | "weekly_reminder"


@dataclass
class RawTake:
    date: str
    section: str
    headline: str
    url: str | None
    take_text: str
    marker: str            # "our_take" | "weekly_reminder"
    source_msg_type: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def resolve_url(raw_url: str) -> str:
    """Decode safelinks-wrapped URLs to the underlying canonical URL."""
    raw_url = raw_url.strip().rstrip(")")  # trailing punctuation from prose
    try:
        parsed = urlparse(raw_url)
    except ValueError:
        return raw_url
    if _SAFELINKS_HOST_HINT in parsed.netloc:
        qs = parse_qs(parsed.query)
        target = qs.get("url")
        if target:
            return unquote(target[0])
    return raw_url


def record_id(url: str, title: str) -> str:
    """Same recipe as data/ingest_history.py:_record_id — sha256(url+title)[:32]."""
    key = (url + title).encode()
    return hashlib.sha256(key).hexdigest()[:32]


# ---------------------------------------------------------------------------
# 1. parse_whatsapp
# ---------------------------------------------------------------------------

def parse_whatsapp(path: str | Path) -> list[Message]:
    """Split the raw export into logical messages: header line + continuation lines."""
    path = Path(path)
    raw_lines = path.read_text(encoding="utf-8").splitlines()

    messages: list[Message] = []
    current: Message | None = None
    current_body_lines: list[str] = []

    def _flush():
        if current is not None:
            current.body = "\n".join(current_body_lines)
            messages.append(current)

    for i, line in enumerate(raw_lines, start=1):
        # WhatsApp exports use ‎ (LRM) before dates sometimes; strip invisible marks.
        clean = line.replace("‎", "").replace("﻿", "")
        header = _match_header(clean)
        if header:
            _flush()
            date, time, sender, body = header
            current = Message(line_no=i, date=date, time=time, sender=sender, body="")
            current_body_lines = [body]
        else:
            if current is not None:
                current_body_lines.append(line.replace("‎", "").replace("﻿", ""))
            # else: stray line before first header — ignore (shouldn't happen)
    _flush()

    return messages


# ---------------------------------------------------------------------------
# Section/item parsing shared by clippings and weekly reminders
# ---------------------------------------------------------------------------

def _parse_sections(body_lines: list[str]) -> list[Section]:
    """
    Line-based state machine over a message body (after the header line(s) are
    excluded by the caller). Recognizes:
      - section header lines (bold-only lines) -> new Section
      - bullet lines "- ..." -> new NewsItemDraft (headline)
      - non-bullet lines before a URL -> appended to item body
      - URL lines -> appended to item.urls
      - "Our take" marker lines -> start/continue item.take
    """
    sections: list[Section] = []
    current_section: Section | None = None
    current_item: NewsItemDraft | None = None
    in_take = False

    def _start_section(name: str):
        nonlocal current_section, current_item, in_take
        current_section = Section(name=name)
        sections.append(current_section)
        current_item = None
        in_take = False

    def _close_item():
        nonlocal current_item, in_take
        current_item = None
        in_take = False

    for raw_line in body_lines:
        line = raw_line.strip()

        if not line:
            continue

        header_m = _SECTION_HEADER_RE.match(line)
        bare_name = None
        if not header_m and not _BULLET_RE.match(line) and not _URL_LINE_RE.match(line):
            candidate_norm = line.lower().rstrip(":").strip()
            if candidate_norm in _KNOWN_SECTION_NAMES:
                bare_name = line.rstrip(":").strip()

        if header_m or bare_name:
            name = header_m.group(1).strip() if header_m else bare_name
            name_norm = name.lower().rstrip(":").strip()
            if name_norm in _META_HEADERS:
                # Meta-header: does not start a real section, just resets state.
                current_item = None
                in_take = False
                continue
            _start_section(name)
            continue

        bullet_m = _BULLET_RE.match(line)
        if bullet_m:
            if current_section is None:
                # Item appears before any recognized section header — bucket
                # it under an implicit "Unsectioned" section.
                _start_section("Unsectioned")
            headline_text = bullet_m.group(1).strip()
            # Inline URL on the same line as the bullet (rare but seen).
            inline_urls = _URL_INLINE_RE.findall(headline_text)
            if inline_urls:
                headline_text = _URL_INLINE_RE.sub("", headline_text).strip()
            current_item = NewsItemDraft(
                headline=headline_text, body=None, urls=list(inline_urls), take=None
            )
            current_section.items.append(current_item)
            in_take = False
            continue

        url_m = _URL_LINE_RE.match(line)
        if url_m and current_item is not None:
            current_item.urls.append(url_m.group(1))
            in_take = False
            continue

        take_m = _TAKE_MARKER_RE.match(line)
        if take_m and current_item is not None:
            take_text = take_m.group(1).strip()
            current_item.take = take_text
            in_take = True
            continue

        # Continuation line: either more take text, or body text before the URL.
        if current_item is not None:
            if in_take:
                current_item.take = ((current_item.take or "") + " " + line).strip()
            elif not current_item.urls:
                # Body text between headline and URL.
                current_item.body = ((current_item.body or "") + " " + line).strip()
            else:
                # Text after URL(s) that isn't a take marker — treat as take
                # continuation only if a take already started; otherwise ignore
                # (typically boilerplate like "Mais informações no e-mail").
                pass

    return sections


def _detect_clipping_variant(sections: list[Section]) -> str:
    names = {s.name.strip().lower() for s in sections}
    if any("main highlights" in n for n in names):
        return "new"
    if "research" in names:
        return "old"
    # Some messages (e.g. 03/02/2025) skip both RESEARCH and MAIN HIGHLIGHTS and
    # go straight to "Principais notícias" subsections — treat as "new" variant
    # (same subsection naming as new format) but flag distinctly if needed.
    return "new" if any(
        n in {"aerospace & defense", "aerospace and defense", "aviation"} for n in names
    ) else "old"


# ---------------------------------------------------------------------------
# 2/3. extract_clippings / extract_weekly_reminders
# ---------------------------------------------------------------------------

def extract_clippings(messages: list[Message]) -> list[Clipping]:
    clippings: list[Clipping] = []
    for msg in messages:
        if not _CLIPPING_MARK_RE.search(msg.body):
            continue
        # Date: first DD/MM/YYYY found on the header line containing the mark.
        header_region = msg.body.split("\n", 4)
        header_text = "\n".join(header_region[:3])  # header may span 2-3 lines
        date_m = _CLIPPING_DATE_RE.search(header_text)
        if not date_m:
            # fall back: search whole body
            date_m = _CLIPPING_DATE_RE.search(msg.body)
        clip_date = date_m.group(1) if date_m else None
        if clip_date:
            # Headers occasionally omit zero-padding ("22/1/2026"); keep DD/MM/YYYY.
            d, m, y = clip_date.split("/")
            clip_date = f"{int(d):02d}/{int(m):02d}/{y}"

        body_lines = msg.body.split("\n")
        sections = _parse_sections(body_lines)
        variant = _detect_clipping_variant(sections)

        parse_issue = None
        if not clip_date:
            parse_issue = "no date found in header"
        elif not sections:
            parse_issue = "no sections parsed"

        clippings.append(
            Clipping(
                date=clip_date or "",
                sender=msg.sender,
                variant=variant,
                sections=sections,
                line_no=msg.line_no,
                parse_issue=parse_issue,
            )
        )
    return clippings


def extract_weekly_reminders(messages: list[Message]) -> list[Clipping]:
    weeklies: list[Clipping] = []
    for msg in messages:
        if not _WEEKLY_RE.search(msg.body):
            continue
        # Weekly reminders have no explicit clipping date header — use the
        # message's own send date (M/D/YY -> DD/MM/YYYY).
        clip_date = _to_ddmmyyyy(msg.date)

        body_lines = msg.body.split("\n")
        sections = _parse_sections(body_lines)

        parse_issue = None if sections else "no sections parsed"

        weeklies.append(
            Clipping(
                date=clip_date,
                sender=msg.sender,
                variant="weekly",
                sections=sections,
                line_no=msg.line_no,
                parse_issue=parse_issue,
            )
        )
    return weeklies


def _to_ddmmyyyy(mdyy: str) -> str:
    """'1/15/25' -> '15/01/2025'."""
    m, d, y = mdyy.split("/")
    yyyy = ("20" + y) if len(y) == 2 else y
    return f"{int(d):02d}/{int(m):02d}/{yyyy}"


# ---------------------------------------------------------------------------
# 4. flatten_news
# ---------------------------------------------------------------------------

_SECTIONS_TO_SKIP_AS_NEWS = {
    "relatórios", "relatorios", "relatório", "relatorio",  # proprietary Safra reports, not public news items
}


def _is_report_section(name_norm: str) -> bool:
    """Matches 'Relatório(s)' section headers regardless of accent/pluralization."""
    return name_norm.startswith("relat")


def flatten_news(clippings: list[Clipping], weeklies: list[Clipping]) -> list[NewsItem]:
    items: list[NewsItem] = []
    seen: set[tuple[str, str, str]] = set()  # (date, section-normalized-url, headline) dedup within-run

    def _process(clip_list: list[Clipping], msg_type_map: dict[str, str]):
        for clip in clip_list:
            msg_type = msg_type_map.get(clip.variant, clip.variant)
            for section in clip.sections:
                sec_name_norm = section.name.strip().lower().rstrip(":")
                if _is_report_section(sec_name_norm):
                    continue
                if sec_name_norm in _META_HEADERS:
                    continue
                for draft in section.items:
                    if not draft.urls:
                        continue  # no URL -> can't build a NewsItem (see edge_cases)
                    for url in draft.urls:
                        canon_url = resolve_url(url)
                        dedup_key = (clip.date, canon_url, draft.headline.strip())
                        if dedup_key in seen:
                            continue  # same item repeated (e.g. MAIN HIGHLIGHTS + section, or resend)
                        seen.add(dedup_key)
                        key = record_id(canon_url, draft.headline.strip())
                        items.append(
                            NewsItem(
                                key=key,
                                date=clip.date,
                                sender=clip.sender,
                                section=section.name.strip(),
                                headline=draft.headline.strip(),
                                body=draft.body,
                                url=canon_url,
                                source_msg_type=msg_type,
                            )
                        )

    _process(clippings, {"old": "clipping_old", "new": "clipping_new"})
    _process(weeklies, {"weekly": "weekly_reminder"})
    return items


# ---------------------------------------------------------------------------
# 5. flatten_takes
# ---------------------------------------------------------------------------

def flatten_takes(clippings: list[Clipping], weeklies: list[Clipping]) -> list[RawTake]:
    takes: list[RawTake] = []

    def _process(clip_list: list[Clipping], marker: str, msg_type_map: dict[str, str]):
        for clip in clip_list:
            msg_type = msg_type_map.get(clip.variant, clip.variant)
            for section in clip.sections:
                sec_name_norm = section.name.strip().lower().rstrip(":")
                if sec_name_norm in _META_HEADERS:
                    continue
                for draft in section.items:
                    if not draft.take:
                        continue
                    url = resolve_url(draft.urls[0]) if draft.urls else None
                    takes.append(
                        RawTake(
                            date=clip.date,
                            section=section.name.strip(),
                            headline=draft.headline.strip(),
                            url=url,
                            take_text=draft.take.strip(),
                            marker=marker,
                            source_msg_type=msg_type,
                        )
                    )

    _process(clippings, "our_take", {"old": "clipping_old", "new": "clipping_new"})
    _process(weeklies, "weekly_reminder", {"weekly": "weekly_reminder"})
    return takes


# ---------------------------------------------------------------------------
# 6. extract_take_metadata_llm
# ---------------------------------------------------------------------------

_COMPANY_VOCAB = [
    "Armac", "Mills", "WEG", "Embraer", "Marcopolo", "Randoncorp",
    "Frasle", "Iochpe-Maxion", "GPS Participações", "Intelbras",
]

_TAKE_LLM_SYSTEM_PROMPT = f"""You are a financial analyst assistant. You will be given a
short "take" (analyst commentary) from an equity research clipping about Brazilian
Capital Goods & Industrials companies. Extract structured metadata.

Companies you may reference MUST come verbatim from this vocabulary (or be omitted if none apply):
{", ".join(_COMPANY_VOCAB)}

Return ONLY a JSON object with this exact shape:
{{
  "companies": [{{"name": "<one of the vocabulary above>", "stance": "Positive|Negative|Neutral"}}],
  "topics": "<short comma-separated topics, e.g. 'Aftermarket, Tariffs'>"
}}

Stance has exactly three values. When a take cuts both ways — an offsetting positive and
negative, or a mixed/balanced read — use "Neutral". Do not invent a fourth value.

An empty companies list is valid if the take is purely macro with no company-specific stance.
"""

# Stances that are meaningful in the analyst's own spreadsheet but that this pipeline
# folds into the three-value vocabulary. "Mixed" is a real label in the existing data
# (32 rows) and reads as a balanced call, so it maps to Neutral rather than being dropped.
_STANCE_ALIASES = {"mixed": "Neutral", "balanced": "Neutral"}

_TAKE_LLM_USER_TEMPLATE = """Headline: {headline}
Section: {section}
Take: {take_text}

Extract companies (from the vocabulary) with stance, and short topics."""


def extract_take_metadata_llm(raw_take: RawTake, timeout: int = 120) -> dict:
    """
    Calls local Ollama (ANALYSIS_MODEL) the same way analyzer/api_analyzer.py's
    _analyze_one_ollama does: think=False top-level, format=json top-level.
    Falls back to a heuristic (keyword match + stance='?') if Ollama is unreachable.
    """
    url = f"{settings.ollama_base_url}/api/chat"
    payload = {
        "model": settings.analysis_model,
        "think": False,
        "messages": [
            {"role": "system", "content": _TAKE_LLM_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": _TAKE_LLM_USER_TEMPLATE.format(
                    headline=raw_take.headline,
                    section=raw_take.section,
                    take_text=raw_take.take_text,
                ),
            },
        ],
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.1, "num_predict": 400},
    }
    try:
        resp = requests.post(url, json=payload, timeout=timeout)
        resp.raise_for_status()
        content = resp.json().get("message", {}).get("content", "")
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
        parsed = json.loads(content)
        companies = parsed.get("companies", [])
        # Validate vocabulary + stance. An unrecognized company name is dropped (it would
        # not match anything downstream), but an off-vocabulary stance only means the
        # model picked a synonym — fold it in rather than losing the company entirely.
        clean_companies = []
        for c in companies:
            name = c.get("name")
            if name not in _COMPANY_VOCAB:
                continue
            raw_stance = str(c.get("stance") or "Neutral").strip()
            stance = _STANCE_ALIASES.get(raw_stance.lower(), raw_stance)
            if stance not in ("Positive", "Negative", "Neutral"):
                log.debug(f"unmapped stance {raw_stance!r} -> Neutral for {name}")
                stance = "Neutral"
            clean_companies.append({"name": name, "stance": stance})
        topics_raw = parsed.get("topics", "")
        if isinstance(topics_raw, list):
            topics_raw = ", ".join(str(t) for t in topics_raw)
        return {"companies": clean_companies, "topics": str(topics_raw)[:200]}
    except Exception as e:  # noqa: BLE001 — deliberately broad for a dry-run fallback
        log.warning(f"Ollama unreachable/failed for take metadata extraction: {e}")
        return _heuristic_take_metadata(raw_take)


def _heuristic_take_metadata(raw_take: RawTake) -> dict:
    from screener.ticker_classifier import _keyword_fallback
    from config.settings import COVERAGE_UNIVERSE

    text = f"{raw_take.headline} {raw_take.take_text}"
    ticker_hits = _keyword_fallback(text)
    company_by_ticker = {t: v["company"] for t, v in COVERAGE_UNIVERSE.items()}
    companies = [
        {"name": company_by_ticker[t], "stance": "?"}
        for t in ticker_hits
        if t in company_by_ticker
    ]
    return {"companies": companies, "topics": raw_take.section}


# ---------------------------------------------------------------------------
# Serialization helpers for preview files
# ---------------------------------------------------------------------------

def news_item_to_dict(item: NewsItem) -> dict:
    return {
        "key": item.key,
        "date": item.date,
        "sender": item.sender,
        "section": item.section,
        "headline": item.headline,
        "body": item.body,
        "url": item.url,
        "source_msg_type": item.source_msg_type,
    }


def raw_take_to_dict(take: RawTake) -> dict:
    return {
        "date": take.date,
        "section": take.section,
        "headline": take.headline,
        "url": take.url,
        "take_text": take.take_text,
        "marker": take.marker,
        "source_msg_type": take.source_msg_type,
    }
