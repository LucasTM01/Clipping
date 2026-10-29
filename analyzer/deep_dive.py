"""
Dinâmica de análise em dois estágios para o briefing detalhado (3º email).

Estágio A — LOCAL (Qwen via Ollama por padrão):
    Para cada notícia aprovada gera um resumo, classifica o grupo, extrai os números
    divulgados (com variação YoY/QoQ/MoM quando presentes) e o sentimento. Roda no
    modelo local (RTX 3060) para minimizar custo de API. Antes, garante texto completo
    (re-extração via trafilatura) e faz um pré-processamento que economiza janela de
    contexto.

Estágio B — API potente (DeepSeek por padrão, OpenAI opcional):
    Recebe TODOS os resumos estruturados do Estágio A e produz uma síntese macro
    conectando as notícias. Só os resumos compactos vão à API (não o texto completo),
    o que mantém o custo baixo.

Saída consumida por notifier.email_sender.send_deep_dive_email.

Tudo falha de forma graciosa (log WARNING, retorna fallback) — o pipeline continua.
"""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime

import requests

from analyzer.api_analyzer import _parse_json_response
from config.prompts import (
    DEEP_DIVE_GROUPS,
    DEEP_DIVE_SUMMARY_SYSTEM_PROMPT,
    DEEP_DIVE_SUMMARY_USER_PROMPT,
    SYNTHESIS_SYSTEM_PROMPT,
    SYNTHESIS_USER_PROMPT,
)
from config.settings import settings
from screener.relevance_scorer import ScoredArticle

log = logging.getLogger(__name__)

_MIN_BODY_CHARS = 200
_SENTIMENTS = {"positivo", "negativo", "neutro"}


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class Metric:
    label: str
    value: str
    period: str | None = None
    yoy: str | None = None
    qoq: str | None = None
    mom: str | None = None


@dataclass
class ArticleBrief:
    source: str
    title: str
    url: str
    published_at: str
    tickers: list[str]
    group: str
    summary: str
    sentiment: str
    metrics: list[Metric] = field(default_factory=list)
    relevance_score: float | None = None
    tier: str | None = None


@dataclass
class Theme:
    title: str
    detail: str
    tickers: list[str] = field(default_factory=list)


@dataclass
class MarketSynthesis:
    overview: str
    themes: list[Theme] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Public orchestration
# ---------------------------------------------------------------------------

def run_deep_dive(
    scored: list[ScoredArticle],
) -> tuple[list[ArticleBrief], MarketSynthesis | None]:
    """Executa Estágio A (local) e Estágio B (API). Retorna (briefs, synthesis)."""
    if not scored:
        return [], None

    ensure_full_text(scored)

    briefs = summarize_batch(scored)
    if not briefs:
        return [], None

    synthesis = synthesize(briefs)
    return briefs, synthesis


# ---------------------------------------------------------------------------
# Pré-processamento de texto (economiza janela de contexto)
# ---------------------------------------------------------------------------

def ensure_full_text(scored: list[ScoredArticle]) -> None:
    """Re-extrai (in-place) o corpo de artigos aprovados sem texto completo."""
    from fetcher.rss_fetcher import _extract_full_text

    missing = [
        s for s in scored
        if len((s.article.full_text or "").strip()) < _MIN_BODY_CHARS
    ]
    if not missing:
        return

    log.info(f"Deep dive: re-extraindo corpo de {len(missing)} artigos sem texto…")
    with ThreadPoolExecutor(max_workers=settings.enrich_max_workers) as pool:
        futures = {pool.submit(_extract_full_text, s.article.url): s for s in missing}
        for future in as_completed(futures):
            s = futures[future]
            try:
                text = future.result()
                if text and len(text.strip()) >= _MIN_BODY_CHARS:
                    s.article.full_text = text
            except Exception as e:
                log.debug(f"Re-extração falhou ({s.article.url}): {e}")


def _preprocess(text: str) -> str:
    """Normaliza espaços e trunca para o limite configurado."""
    text = text or ""
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()[: settings.deep_dive_max_full_text]


# ---------------------------------------------------------------------------
# Estágio A — resumo por notícia (local por padrão)
# ---------------------------------------------------------------------------

def summarize_batch(scored: list[ScoredArticle]) -> list[ArticleBrief]:
    backend = settings.summary_backend.lower()
    model = _resolve_model(backend, settings.summary_model)
    workers = max(1, settings.deep_dive_max_workers)

    log.info(
        f"Deep dive Estágio A: resumindo {len(scored)} artigos "
        f"(backend={backend}, modelo={model}, workers={workers})"
    )

    briefs: list[ArticleBrief] = []
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_summarize_one, s, backend, model): s for s in scored
        }
        for future in as_completed(futures):
            s = futures[future]
            done += 1
            try:
                b = future.result()
                briefs.append(b)
                log.debug(
                    f"Deep dive A [{done}/{len(scored)}] {b.group} | "
                    f"{b.sentiment} | {len(b.metrics)} números | {s.article.title[:55]}"
                )
            except Exception as e:
                log.warning(
                    f"Deep dive A [{done}/{len(scored)}] FALHOU '{s.article.title[:50]}': {e}"
                )
                briefs.append(_fallback_brief(s))

    ok = sum(1 for b in briefs if "Resumo indisponível" not in b.summary)
    log.info(f"Deep dive Estágio A: {ok}/{len(briefs)} resumos OK (backend={backend})")
    return briefs


def _summarize_one(scored: ScoredArticle, backend: str, model: str) -> ArticleBrief:
    art = scored.article
    user_prompt = DEEP_DIVE_SUMMARY_USER_PROMPT.format(
        title=art.title,
        source=art.source,
        published_at=art.published_at,
        tickers=", ".join(scored.predicted_tickers) if scored.predicted_tickers else "Nenhum",
        full_text=_preprocess(art.full_text) or art.title,
    )

    parsed = _chat_json(
        backend, model,
        DEEP_DIVE_SUMMARY_SYSTEM_PROMPT, user_prompt,
        max_tokens=900, temperature=0.1,
    )
    if not parsed:
        log.warning(f"Estágio A: JSON inválido para '{art.title[:60]}' — usando fallback")
        return _fallback_brief(scored)

    return ArticleBrief(
        source=art.source,
        title=art.title,
        url=art.url,
        published_at=art.published_at,
        tickers=scored.predicted_tickers,
        group=_normalize_group(parsed.get("group", ""), scored.predicted_tickers),
        summary=parsed.get("summary") or "Resumo indisponível.",
        sentiment=_normalize_sentiment(parsed.get("sentiment")),
        metrics=_parse_metrics(parsed.get("metrics")),
        relevance_score=scored.relevance_score,
        tier=scored.tier,
    )


# ---------------------------------------------------------------------------
# Estágio B — síntese macro (API)
# ---------------------------------------------------------------------------

def synthesize(briefs: list[ArticleBrief]) -> MarketSynthesis | None:
    backend = settings.synthesis_backend.lower()
    if backend == "ollama":
        log.warning("synthesis_backend=ollama não suportado no Estágio B; usando deepseek.")
        backend = "deepseek"

    # Fallback: DeepSeek configurado mas sem chave → tenta OpenAI
    if backend == "deepseek" and not settings.deepseek_api_key:
        if settings.openai_api_key:
            log.info("DEEPSEEK_API_KEY ausente — fallback para OpenAI no Estágio B.")
            backend = "openai"
        else:
            log.warning("Estágio B: DEEPSEEK_API_KEY e OPENAI_API_KEY ausentes — síntese ignorada.")
            return None

    model = _resolve_model(backend, settings.synthesis_model)

    log.info(
        f"Deep dive Estágio B: sintetizando {len(briefs)} resumos "
        f"(backend={backend}, modelo={model})"
    )

    user_prompt = SYNTHESIS_USER_PROMPT.format(
        date=datetime.now().strftime("%d/%m/%Y"),
        briefs=_format_briefs_for_synthesis(briefs),
    )

    try:
        parsed = _chat_json(
            backend, model,
            SYNTHESIS_SYSTEM_PROMPT, user_prompt,
            max_tokens=800, temperature=0.3,
        )
    except Exception as e:
        log.warning(f"Estágio B falhou: {e}")
        return None

    if not parsed:
        log.warning("Estágio B: JSON inválido — sem síntese.")
        return None

    # Aceita tanto "summary" (novo formato simples) quanto "overview" (legado)
    text = (parsed.get("summary") or parsed.get("overview") or "").strip()
    if not text:
        log.warning("Estágio B: campo summary vazio.")
        return None

    return MarketSynthesis(overview=text, themes=[])


def _format_briefs_for_synthesis(briefs: list[ArticleBrief]) -> str:
    lines: list[str] = []
    for i, b in enumerate(briefs, 1):
        tickers = ", ".join(b.tickers) if b.tickers else "—"
        lines.append(
            f"[{i}] ({b.group} | {b.sentiment} | {tickers} | {b.source}) "
            f"{b.title}\n    {b.summary}"
        )
        if b.metrics:
            nums = "; ".join(_metric_inline(m) for m in b.metrics)
            lines.append(f"    Números: {nums}")
    return "\n".join(lines)


def _metric_inline(m: Metric) -> str:
    parts = [f"{m.label}: {m.value}"]
    if m.period:
        parts.append(f"({m.period})")
    deltas = [f"YoY {m.yoy}" if m.yoy else "", f"QoQ {m.qoq}" if m.qoq else "",
              f"MoM {m.mom}" if m.mom else ""]
    deltas = [d for d in deltas if d]
    if deltas:
        parts.append("[" + ", ".join(deltas) + "]")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Backends de chat (JSON)
# ---------------------------------------------------------------------------

def _chat_json(
    backend: str, model: str, system: str, user: str, *,
    max_tokens: int, temperature: float,
) -> dict | None:
    if backend == "ollama":
        return _ollama_chat_json(model, system, user, max_tokens, temperature)
    return _openai_chat_json(backend, model, system, user, max_tokens, temperature)


def _ollama_chat_json(
    model: str, system: str, user: str, max_tokens: int, temperature: float,
) -> dict | None:
    payload = {
        "model": model,
        "think": False,
        "format": "json",
        "stream": False,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "options": {"temperature": temperature, "num_predict": max_tokens},
    }
    resp = requests.post(f"{settings.ollama_base_url}/api/chat", json=payload, timeout=300)
    resp.raise_for_status()
    content = resp.json().get("message", {}).get("content", "")
    return _parse_json_response(content)


def _openai_chat_json(
    backend: str, model: str, system: str, user: str, max_tokens: int, temperature: float,
) -> dict | None:
    from openai import OpenAI

    if backend == "deepseek":
        if not settings.deepseek_api_key:
            raise RuntimeError("DEEPSEEK_API_KEY não configurada no .env")
        client = OpenAI(api_key=settings.deepseek_api_key, base_url=settings.deepseek_base_url)
    elif backend == "openai":
        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY não configurada no .env")
        client = OpenAI(api_key=settings.openai_api_key)
    else:
        raise ValueError(f"Backend desconhecido: {backend}")

    response = client.chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    return _parse_json_response(response.choices[0].message.content or "")


def _resolve_model(backend: str, override: str) -> str:
    if override:
        return override
    if backend == "ollama":
        return settings.summary_model or settings.analysis_model
    if backend == "deepseek":
        return settings.deepseek_model
    return settings.gpt_model


# ---------------------------------------------------------------------------
# Parsing / normalização
# ---------------------------------------------------------------------------

def _normalize_group(raw: str, tickers: list[str]) -> str:
    raw = (raw or "").strip()
    for g in DEEP_DIVE_GROUPS:
        if raw.lower() == g.lower():
            return g
    if raw:
        return raw  # grupo novo proposto pelo modelo — preserva
    return "Macro" if not tickers else "Others"


def _normalize_sentiment(raw) -> str:
    s = str(raw or "").strip().lower()
    if s in _SENTIMENTS:
        return s.capitalize()
    return "Neutro"


def _parse_metrics(raw) -> list[Metric]:
    if not isinstance(raw, list):
        return []
    out: list[Metric] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()
        value = str(item.get("value") or "").strip()
        if not label or not value:
            continue
        out.append(Metric(
            label=label,
            value=value,
            period=_clean_opt(item.get("period")),
            yoy=_clean_opt(item.get("yoy")),
            qoq=_clean_opt(item.get("qoq")),
            mom=_clean_opt(item.get("mom")),
        ))
    return out


def _clean_opt(v) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    if not s or s.lower() in ("null", "none", "n/a", "—", "-"):
        return None
    return s


def _fallback_brief(scored: ScoredArticle) -> ArticleBrief:
    art = scored.article
    return ArticleBrief(
        source=art.source,
        title=art.title,
        url=art.url,
        published_at=art.published_at,
        tickers=scored.predicted_tickers,
        group=_normalize_group("", scored.predicted_tickers),
        summary="[Resumo indisponível — falha no Estágio A] " + art.title,
        sentiment="Neutro",
        metrics=[],
        relevance_score=scored.relevance_score,
        tier=scored.tier,
    )
