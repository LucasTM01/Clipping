"""
Módulo de notificações por email do pipeline de clipping.

Dois emails por execução (quando EMAIL_ENABLED=true):

  Email 1 — Briefing (corpo HTML, sem anexo)
    → lucas.melotti@safra.com.br + lucas.melotti.safra@gmail.com
    Conteúdo: artigos agrupados por setor, sentimento, links, resumos.

  Email 2 — Ops Report (corpo HTML)
    → lucas.melotti.safra@gmail.com
    Conteúdo: tabelas de desempenho (artigos por fonte, tiers, sentimentos, ações).

Configuração via .env:
    EMAIL_ENABLED=true
    EMAIL_SMTP_HOST=smtp.gmail.com
    EMAIL_SMTP_PORT=587
    EMAIL_USERNAME=...@gmail.com
    EMAIL_PASSWORD=xxxx xxxx xxxx xxxx   # App Password do Google
    EMAIL_FROM=...@gmail.com
    EMAIL_BRIEFING_TO=["lucas.melotti@safra.com.br","lucas.melotti.safra@gmail.com"]
    EMAIL_OPS_TO=["lucas.melotti.safra@gmail.com"]
"""

from __future__ import annotations

import logging
import smtplib
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from analyzer.api_analyzer import AnalysisResult
    from analyzer.deep_dive import ArticleBrief, MarketSynthesis
    from analyzer.briefing_v2 import BriefItemV2

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Stats dataclasses (populated by pipeline.py during the run)
# ---------------------------------------------------------------------------

@dataclass
class SourceStats:
    n_fetched: int = 0
    n_approved: int = 0       # tier != discard
    n_analyzed: int = 0       # sent to LLM
    scores: list[float] = field(default_factory=list)
    tier_counts: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    @property
    def avg_score(self) -> float:
        return sum(self.scores) / len(self.scores) if self.scores else 0.0


@dataclass
class PipelineStats:
    started_at: datetime
    finished_at: datetime | None = None
    n_fetched: int = 0
    per_source: dict[str, SourceStats] = field(default_factory=dict)
    tier_counts: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    n_analyzed: int = 0
    sentiment_counts: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    n_action_required: int = 0
    docx_path: str | None = None
    error: str | None = None

    def ensure_source(self, name: str) -> SourceStats:
        if name not in self.per_source:
            self.per_source[name] = SourceStats()
        return self.per_source[name]

    @property
    def elapsed(self) -> str:
        if not self.finished_at:
            return "—"
        delta = self.finished_at - self.started_at
        mins, secs = divmod(int(delta.total_seconds()), 60)
        return f"{mins}m {secs}s"

    @property
    def n_approved(self) -> int:
        return sum(s.n_approved for s in self.per_source.values())


# ---------------------------------------------------------------------------
# Briefing HTML builder (mirrors SECTOR_GROUPS from doc_generator.py)
# ---------------------------------------------------------------------------

_SENTIMENT_COLOR = {
    "Positivo": "#27ae60",
    "Negativo": "#c0392b",
    "Neutro": "#7f8c8d",
}
_SENTIMENT_BADGE = {
    "Positivo": ("✚", "#e8f8f0", "#27ae60"),
    "Negativo": ("−", "#fdf2f2", "#c0392b"),
    "Neutro": ("=", "#f5f5f5", "#7f8c8d"),
}


def _esc(text: str) -> str:
    """Minimal HTML escaping."""
    return (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
    )


def _build_briefing_html(analyses: list[AnalysisResult]) -> str:
    from generator.doc_generator import SECTOR_GROUPS, _TICKER_TO_SECTOR
    from config.settings import DOCUMENT_TITLE, DOCUMENT_SUBTITLE

    date_str = datetime.now().strftime("%d/%m/%Y")

    # Group articles by sector
    sector_articles: dict[str, list[AnalysisResult]] = {name: [] for name, _ in SECTOR_GROUPS}
    sector_articles["Macro / Setorial"] = []

    for a in analyses:
        sector = None
        for t in (a.tickers or []):
            if t in _TICKER_TO_SECTOR:
                sector = _TICKER_TO_SECTOR[t]
                break
        if sector and sector in sector_articles:
            sector_articles[sector].append(a)
        else:
            sector_articles["Macro / Setorial"].append(a)

    n_pos = sum(1 for a in analyses if a.sentiment == "Positivo")
    n_neg = sum(1 for a in analyses if a.sentiment == "Negativo")
    n_neu = sum(1 for a in analyses if a.sentiment == "Neutro")
    n_action = sum(1 for a in analyses if a.action_required)

    parts = ["""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
  body{font-family:Arial,sans-serif;font-size:14px;color:#1a1a1a;background:#fff;margin:0;padding:0}
  .wrap{max-width:760px;margin:0 auto;padding:24px}
  h1{color:#003366;font-size:22px;margin:0 0 4px}
  .sub{color:#666;font-size:13px;margin:0 0 20px}
  .kpi-row{display:flex;gap:12px;margin:16px 0}
  .kpi{background:#f0f4f8;border-radius:6px;padding:12px 18px;flex:1;text-align:center}
  .kpi .val{font-size:22px;font-weight:bold;color:#003366}
  .kpi .lbl{font-size:11px;color:#666;margin-top:2px}
  .section-title{background:#003366;color:#fff;padding:8px 14px;font-size:15px;
    font-weight:bold;margin:24px 0 0;border-radius:4px 4px 0 0}
  .article{border:1px solid #e0e0e0;border-top:none;padding:14px 16px;background:#fff}
  .article+.article{border-top:none}
  .article:last-child{border-radius:0 0 4px 4px}
  .badge{display:inline-block;padding:2px 8px;border-radius:10px;
    font-size:12px;font-weight:bold;margin-right:8px}
  .art-title{font-size:15px;font-weight:bold;margin:0 0 4px}
  .art-title a{text-decoration:none;color:#003366}
  .art-meta{font-size:11px;color:#888;margin-bottom:8px}
  .art-summary{font-size:13px;color:#333;line-height:1.5}
  .action-box{background:#fff3cd;border:1px solid #ffc107;border-radius:4px;
    padding:8px 12px;margin-top:8px;font-size:12px;color:#7b5a00}
  .exec-table{width:100%;border-collapse:collapse;margin:16px 0}
  .exec-table th{background:#003366;color:#fff;padding:8px 12px;text-align:left;font-size:12px}
  .exec-table td{padding:8px 12px;border-bottom:1px solid #e0e0e0;font-size:13px}
  .exec-table tr:last-child td{border-bottom:none}
  .footer{margin-top:32px;font-size:11px;color:#aaa;text-align:center}
  .empty{color:#aaa;font-style:italic;padding:14px 16px;border:1px solid #e0e0e0;border-top:none}
</style>
</head><body><div class="wrap">
"""]

    # Header
    parts.append(f"""
<h1>📰 {_esc(DOCUMENT_TITLE)}</h1>
<p class="sub">{_esc(DOCUMENT_SUBTITLE)} — {date_str}</p>
""")

    # KPI row
    parts.append(f"""
<div class="kpi-row">
  <div class="kpi"><div class="val">{len(analyses)}</div><div class="lbl">Total de artigos</div></div>
  <div class="kpi" style="background:#e8f8f0"><div class="val" style="color:#27ae60">{n_pos}</div><div class="lbl">Positivos ✚</div></div>
  <div class="kpi" style="background:#fdf2f2"><div class="val" style="color:#c0392b">{n_neg}</div><div class="lbl">Negativos −</div></div>
  <div class="kpi" style="background:#fff3cd"><div class="val" style="color:#e67e22">{n_action}</div><div class="lbl">Ações recomendadas</div></div>
</div>
""")

    # Action required summary table
    action_items = [a for a in analyses if a.action_required]
    if action_items:
        parts.append("""<div class="section-title">🔔 Ações Recomendadas</div>""")
        parts.append('<table class="exec-table"><tr><th>Ticker</th><th>Título</th><th>Ação</th></tr>')
        for a in action_items:
            tickers = ", ".join(a.tickers) if a.tickers else "—"
            action_txt = _esc(a.action_detail or "Verificar")
            parts.append(
                f'<tr><td><b>{_esc(tickers)}</b></td>'
                f'<td><a href="{_esc(a.url)}">{_esc(a.title[:80])}</a></td>'
                f'<td>{action_txt}</td></tr>'
            )
        parts.append("</table>")

    # Sector sections
    all_sectors = [(name, tickers) for name, tickers in SECTOR_GROUPS]
    all_sectors.append(("Macro / Setorial", []))

    for sector_name, _ in all_sectors:
        arts = sector_articles.get(sector_name, [])
        parts.append(f'<div class="section-title">{_esc(sector_name)}</div>')

        if not arts:
            parts.append('<div class="empty">Sem novidades neste setor hoje.</div>')
            continue

        for a in arts:
            sym, bg, fg = _SENTIMENT_BADGE.get(a.sentiment, ("=", "#f5f5f5", "#7f8c8d"))
            badge = (
                f'<span class="badge" style="background:{bg};color:{fg};border:1px solid {fg}">'
                f'{sym} {_esc(a.sentiment)}</span>'
            )
            tickers_str = " · ".join(a.tickers) if a.tickers else ""
            pub = a.published_at[:10] if a.published_at else ""
            source_meta = " · ".join(filter(None, [_esc(a.source), pub, tickers_str]))

            parts.append(f"""
<div class="article">
  {badge}
  <div class="art-title"><a href="{_esc(a.url)}">{_esc(a.title)}</a></div>
  <div class="art-meta">{source_meta}</div>
  <div class="art-summary">{_esc(a.summary or "")}</div>
""")
            if a.action_required and a.action_detail:
                parts.append(
                    f'  <div class="action-box">⚠️ <b>Ação:</b> {_esc(a.action_detail)}</div>'
                )
            parts.append("</div>")

    parts.append("""
<div class="footer">Gerado automaticamente pelo pipeline Clipping — Equity Research</div>
</div></body></html>""")

    return "".join(parts)


# ---------------------------------------------------------------------------
# Ops report HTML builder
# ---------------------------------------------------------------------------

def _build_ops_html(stats: PipelineStats) -> str:
    date_str = stats.started_at.strftime("%d/%m/%Y %H:%M")
    n_approved = stats.n_approved
    n_fetched = stats.n_fetched
    approval_rate = f"{n_approved / n_fetched * 100:.1f}%" if n_fetched else "—"

    parts = [f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
  body{{font-family:Arial,sans-serif;font-size:13px;color:#1a1a1a;background:#f9f9f9;margin:0;padding:0}}
  .wrap{{max-width:760px;margin:0 auto;padding:24px}}
  h1{{color:#003366;font-size:18px;margin:0 0 4px}}
  .sub{{color:#888;font-size:12px;margin:0 0 20px}}
  .kpi-row{{display:flex;gap:10px;margin:16px 0;flex-wrap:wrap}}
  .kpi{{background:#fff;border:1px solid #dde;border-radius:6px;padding:10px 16px;
    flex:1;min-width:120px;text-align:center}}
  .kpi .val{{font-size:20px;font-weight:bold;color:#003366}}
  .kpi .lbl{{font-size:11px;color:#888;margin-top:2px}}
  h2{{font-size:14px;color:#003366;margin:24px 0 8px;border-bottom:2px solid #003366;
    padding-bottom:4px}}
  table{{width:100%;border-collapse:collapse;background:#fff;border:1px solid #e0e0e0;
    border-radius:4px;overflow:hidden}}
  th{{background:#003366;color:#fff;padding:8px 10px;text-align:left;font-size:12px}}
  td{{padding:7px 10px;border-bottom:1px solid #eee;font-size:12px}}
  tr:last-child td{{border-bottom:none}}
  tr:nth-child(even) td{{background:#f8fafc}}
  .ok{{color:#27ae60;font-weight:bold}}
  .warn{{color:#e67e22;font-weight:bold}}
  .bad{{color:#c0392b;font-weight:bold}}
  .pill{{display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;
    font-weight:bold;margin:2px}}
  .footer{{margin-top:32px;font-size:11px;color:#bbb;text-align:center}}
</style>
</head><body><div class="wrap">
<h1>🔧 Clipping — Relatório Operacional</h1>
<p class="sub">{date_str} · Duração: {stats.elapsed}</p>
"""]

    # KPIs
    parts.append(f"""
<div class="kpi-row">
  <div class="kpi"><div class="val">{n_fetched:,}</div><div class="lbl">Coletados</div></div>
  <div class="kpi"><div class="val">{n_approved:,}</div><div class="lbl">Aprovados (não-discard)</div></div>
  <div class="kpi"><div class="val">{stats.n_analyzed}</div><div class="lbl">Analisados pelo LLM</div></div>
  <div class="kpi"><div class="val">{approval_rate}</div><div class="lbl">Taxa de aprovação</div></div>
  <div class="kpi"><div class="val">{stats.n_action_required}</div><div class="lbl">Ações recomendadas</div></div>
</div>
""")

    # Per-source table
    parts.append("<h2>📡 Por fonte</h2>")
    parts.append("""
<table>
<tr>
  <th>Fonte</th>
  <th style="text-align:right">Coletados</th>
  <th style="text-align:right">Aprovados</th>
  <th style="text-align:right">→ LLM</th>
  <th style="text-align:right">Score médio</th>
  <th>Tiers</th>
</tr>
""")
    sorted_sources = sorted(
        stats.per_source.items(), key=lambda kv: kv[1].n_fetched, reverse=True
    )
    for source, s in sorted_sources:
        rate_cls = "ok" if s.n_approved > 0 else "warn"
        score_str = f"{s.avg_score:.3f}" if s.scores else "—"
        tier_pills = " ".join(
            f'<span class="pill" style="{_tier_style(t)}">{t}: {c}</span>'
            for t, c in sorted(s.tier_counts.items())
            if c > 0
        )
        parts.append(
            f'<tr><td><b>{_esc(source)}</b></td>'
            f'<td style="text-align:right">{s.n_fetched}</td>'
            f'<td style="text-align:right" class="{rate_cls}">{s.n_approved}</td>'
            f'<td style="text-align:right">{s.n_analyzed}</td>'
            f'<td style="text-align:right">{score_str}</td>'
            f'<td>{tier_pills}</td></tr>'
        )
    parts.append("</table>")

    # Tier breakdown
    parts.append("<h2>🎯 Distribuição de tiers (global)</h2>")
    tier_order = [("high", "#27ae60"), ("medium", "#2980b9"),
                  ("low_review", "#e67e22"), ("discard", "#95a5a6")]
    pills = ""
    for tier, color in tier_order:
        count = stats.tier_counts.get(tier, 0)
        if count:
            pct = count / n_fetched * 100 if n_fetched else 0
            pills += (
                f'<span class="pill" style="background:{color}20;color:{color};'
                f'border:1px solid {color}">'
                f'{tier}: {count} ({pct:.1f}%)</span> '
            )
    parts.append(f'<p style="margin:8px 0">{pills or "—"}</p>')

    # Sentiment breakdown
    if stats.sentiment_counts:
        parts.append("<h2>💬 Sentimentos (artigos analisados)</h2>")
        sent_colors = {"Positivo": "#27ae60", "Negativo": "#c0392b", "Neutro": "#7f8c8d"}
        s_pills = ""
        for sent, count in sorted(stats.sentiment_counts.items()):
            color = sent_colors.get(sent, "#333")
            s_pills += (
                f'<span class="pill" style="background:{color}20;color:{color};'
                f'border:1px solid {color}">{sent}: {count}</span> '
            )
        parts.append(f'<p style="margin:8px 0">{s_pills}</p>')

    parts.append(f"""
<div class="footer">Pipeline Clipping — Equity Research &nbsp;·&nbsp; {date_str}</div>
</div></body></html>""")

    return "".join(parts)


def _tier_style(tier: str) -> str:
    colors = {
        "high": ("27ae60", "e8f8f0"),
        "medium": ("2980b9", "eaf4fb"),
        "low_review": ("e67e22", "fef5ea"),
        "discard": ("95a5a6", "f5f5f5"),
    }
    fg, bg = colors.get(tier, ("333", "eee"))
    return f"background:#{bg};color:#{fg};border:1px solid #{fg}"


# ---------------------------------------------------------------------------
# SMTP helpers
# ---------------------------------------------------------------------------

def _send(msg: MIMEMultipart, to: list[str]) -> None:
    from config.settings import settings

    host = settings.email_smtp_host
    port = settings.email_smtp_port
    username = settings.email_username
    password = settings.email_password

    with smtplib.SMTP(host, port, timeout=30) as server:
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login(username, password)
        server.sendmail(msg["From"], to, msg.as_string())

    log.info(f"Email '{msg['Subject']}' enviado para {to}")


def send_deep_dive_email(
    briefs: list["ArticleBrief"],
    synthesis: "MarketSynthesis | None",
    to: list[str],
) -> None:
    """Email 3: análise detalhada (números por notícia) + síntese macro."""
    from config.settings import settings

    try:
        date_str = datetime.now().strftime("%d/%m/%Y")
        subject = f"📊 Clipping — Análise Detalhada — {date_str}"

        html = _build_deep_dive_html(briefs, synthesis)

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = settings.email_from
        msg["To"] = ", ".join(to)
        msg.attach(MIMEText(html, "html", "utf-8"))

        _send(msg, to)
    except Exception as exc:
        log.warning(f"send_deep_dive_email falhou (não crítico): {exc}", exc_info=True)


def _build_deep_dive_html(
    briefs: list["ArticleBrief"],
    synthesis: "MarketSynthesis | None",
) -> str:
    from analyzer.deep_dive import DEEP_DIVE_GROUPS  # type: ignore
    from config.settings import DOCUMENT_TITLE, DOCUMENT_SUBTITLE

    date_str = datetime.now().strftime("%d/%m/%Y")

    # Group briefs preserving canonical order, then any extra group at the end
    grouped: dict[str, list] = {g: [] for g in DEEP_DIVE_GROUPS}
    for b in briefs:
        grouped.setdefault(b.group, []).append(b)
    group_order = list(DEEP_DIVE_GROUPS) + [g for g in grouped if g not in DEEP_DIVE_GROUPS]

    n_pos = sum(1 for b in briefs if b.sentiment == "Positivo")
    n_neg = sum(1 for b in briefs if b.sentiment == "Negativo")
    n_metrics = sum(len(b.metrics) for b in briefs)

    parts = ["""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
  body{font-family:Arial,sans-serif;font-size:14px;color:#1a1a1a;background:#fff;margin:0;padding:0}
  .wrap{max-width:780px;margin:0 auto;padding:24px}
  h1{color:#003366;font-size:22px;margin:0 0 4px}
  .sub{color:#666;font-size:13px;margin:0 0 20px}
  .kpi-row{display:flex;gap:12px;margin:16px 0}
  .kpi{background:#f0f4f8;border-radius:6px;padding:12px 18px;flex:1;text-align:center}
  .kpi .val{font-size:22px;font-weight:bold;color:#003366}
  .kpi .lbl{font-size:11px;color:#666;margin-top:2px}
  .synth{background:#f7f9fc;border:1px solid #d8e2ec;border-left:4px solid #003366;
    border-radius:4px;padding:14px 18px;margin:18px 0}
  .synth h2{color:#003366;font-size:16px;margin:0 0 8px}
  .synth p{line-height:1.6;margin:0 0 10px;color:#222}
  .theme{margin:10px 0;padding-left:12px;border-left:3px solid #2980b9}
  .theme .t-title{font-weight:bold;color:#003366}
  .theme .t-tickers{font-size:11px;color:#2980b9;margin-left:6px}
  .section-title{background:#003366;color:#fff;padding:8px 14px;font-size:15px;
    font-weight:bold;margin:26px 0 0;border-radius:4px 4px 0 0}
  .article{border:1px solid #e0e0e0;border-top:none;padding:14px 16px;background:#fff}
  .article:last-child{border-radius:0 0 4px 4px}
  .badge{display:inline-block;padding:2px 8px;border-radius:10px;
    font-size:12px;font-weight:bold;margin-right:8px}
  .art-title{font-size:15px;font-weight:bold;margin:0 0 4px}
  .art-title a{text-decoration:none;color:#003366}
  .art-meta{font-size:11px;color:#888;margin-bottom:8px}
  .art-summary{font-size:13px;color:#333;line-height:1.5}
  .metrics{width:100%;border-collapse:collapse;margin:10px 0 2px}
  .metrics th{background:#eef2f7;color:#003366;padding:5px 8px;text-align:left;font-size:11px;
    border-bottom:1px solid #d8e2ec}
  .metrics td{padding:5px 8px;border-bottom:1px solid #eee;font-size:12px}
  .metrics tr:last-child td{border-bottom:none}
  .pos{color:#27ae60;font-weight:bold}
  .neg{color:#c0392b;font-weight:bold}
  .empty{color:#aaa;font-style:italic;padding:14px 16px;border:1px solid #e0e0e0;border-top:none}
  .footer{margin-top:32px;font-size:11px;color:#aaa;text-align:center}
</style>
</head><body><div class="wrap">
"""]

    parts.append(f"""
<h1>📊 {_esc(DOCUMENT_TITLE)} — Análise Detalhada</h1>
<p class="sub">{_esc(DOCUMENT_SUBTITLE)} — {date_str}</p>
<div class="kpi-row">
  <div class="kpi"><div class="val">{len(briefs)}</div><div class="lbl">Notícias</div></div>
  <div class="kpi" style="background:#e8f8f0"><div class="val" style="color:#27ae60">{n_pos}</div><div class="lbl">Positivos ✚</div></div>
  <div class="kpi" style="background:#fdf2f2"><div class="val" style="color:#c0392b">{n_neg}</div><div class="lbl">Negativos −</div></div>
  <div class="kpi"><div class="val">{n_metrics}</div><div class="lbl">Números extraídos</div></div>
</div>
""")

    # Synthesis (Stage B)
    if synthesis and (synthesis.overview or synthesis.themes):
        parts.append('<div class="synth"><h2>🔭 Síntese do dia</h2>')
        for para in (synthesis.overview or "").split("\n"):
            if para.strip():
                parts.append(f"<p>{_esc(para.strip())}</p>")
        for th in synthesis.themes:
            tk = f'<span class="t-tickers">{_esc(" · ".join(th.tickers))}</span>' if th.tickers else ""
            parts.append(
                f'<div class="theme"><span class="t-title">{_esc(th.title)}</span>{tk}'
                f'<div>{_esc(th.detail)}</div></div>'
            )
        parts.append("</div>")

    # Per-group detailed articles
    for group in group_order:
        arts = grouped.get(group, [])
        if not arts:
            continue
        parts.append(f'<div class="section-title">{_esc(group)}</div>')
        for b in arts:
            sym, bg, fg = _SENTIMENT_BADGE.get(b.sentiment, ("=", "#f5f5f5", "#7f8c8d"))
            badge = (
                f'<span class="badge" style="background:{bg};color:{fg};border:1px solid {fg}">'
                f'{sym} {_esc(b.sentiment)}</span>'
            )
            tickers_str = " · ".join(b.tickers) if b.tickers else ""
            pub = b.published_at[:10] if b.published_at else ""
            meta = " · ".join(filter(None, [_esc(b.source), pub, tickers_str]))
            parts.append(f"""
<div class="article">
  {badge}
  <div class="art-title"><a href="{_esc(b.url)}">{_esc(b.title)}</a></div>
  <div class="art-meta">{meta}</div>
  <div class="art-summary">{_esc(b.summary or "")}</div>
""")
            parts.append(_metrics_table_html(b.metrics))
            parts.append("</div>")

    parts.append("""
<div class="footer">Estágio A (resumos + números): modelo local · Estágio B (síntese): API ·
Pipeline Clipping — Equity Research</div>
</div></body></html>""")

    return "".join(parts)


def _metrics_table_html(metrics: list) -> str:
    if not metrics:
        return ""
    rows = []
    for m in metrics:
        rows.append(
            "<tr>"
            f"<td>{_esc(m.label)}</td>"
            f"<td><b>{_esc(m.value)}</b></td>"
            f"<td>{_esc(m.period or '—')}</td>"
            f"<td>{_delta_html(m.yoy)}</td>"
            f"<td>{_delta_html(m.qoq)}</td>"
            f"<td>{_delta_html(m.mom)}</td>"
            "</tr>"
        )
    return (
        '<table class="metrics"><tr>'
        "<th>Indicador</th><th>Valor</th><th>Período</th>"
        "<th>YoY</th><th>QoQ</th><th>MoM</th></tr>"
        + "".join(rows) + "</table>"
    )


def _delta_html(val: str | None) -> str:
    if not val:
        return "—"
    cls = ""
    if val.strip().startswith("+") or "alta" in val.lower():
        cls = "pos"
    elif val.strip().startswith("-") or "−" in val or "queda" in val.lower():
        cls = "neg"
    return f'<span class="{cls}">{_esc(val)}</span>' if cls else _esc(val)


# ---------------------------------------------------------------------------
# Briefing v2 — "Safra's Morning Notes - AI View" (inglês, factual, por setor)
# Replica o visual do .docx de referência: cor #00003C, Arial 10pt, setores em
# negrito com dois-pontos, bullets "texto. — Fonte (hyperlink)".
# ---------------------------------------------------------------------------

_V2_INK = "#00003C"


def _build_briefing_v2_html(items: list["BriefItemV2"], stats: "PipelineStats | None") -> str:
    from config.prompts import BRIEFING_V2_SECTORS

    date_str = datetime.now().strftime("%B %d, %Y")

    # Agrupa por setor preservando a ordem canônica; setores extras vão ao fim.
    grouped: dict[str, list] = {s: [] for s in BRIEFING_V2_SECTORS}
    for it in items:
        grouped.setdefault(it.sector, []).append(it)
    sector_order = list(BRIEFING_V2_SECTORS) + [s for s in grouped if s not in BRIEFING_V2_SECTORS]

    n_read = stats.n_fetched if stats else len(items)
    n_approved = stats.n_approved if stats else len(items)

    parts = [f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
  body{{font-family:Arial,Helvetica,sans-serif;font-size:13px;color:{_V2_INK};
    background:#fff;margin:0;padding:0;line-height:1.45}}
  .wrap{{max-width:720px;margin:0 auto;padding:24px}}
  .date{{font-size:13px;font-weight:bold;color:{_V2_INK};margin:0 0 10px}}
  .greet{{font-size:13px;color:{_V2_INK};margin:0 0 14px}}
  .stats{{border:1px solid #d7d7e0;border-radius:4px;background:#f6f6fb;
    padding:8px 12px;margin:0 0 18px;font-size:12px;color:{_V2_INK}}}
  .stats b{{font-size:13px}}
  .sector{{font-size:13px;font-weight:bold;color:{_V2_INK};margin:16px 0 4px}}
  ul{{margin:0 0 6px;padding-left:20px}}
  li{{font-size:13px;color:{_V2_INK};margin:4px 0;line-height:1.45}}
  a{{color:{_V2_INK};text-decoration:underline}}
  .footer{{margin-top:26px;font-size:11px;font-style:italic;color:#666}}
</style>
</head><body><div class="wrap">
<div class="date">{date_str}</div>
<div class="greet">Good morning!<br>Here is a summary of the news in our Capital Goods
daily. You can find details below:</div>
"""]

    # Process stats (não existe no .docx de referência — pedido do usuário)
    parts.append(f"""
<div class="stats">
  <b>{n_read}</b> news read &nbsp;·&nbsp; <b>{n_approved}</b> approved &nbsp;·&nbsp;
  <b>{len(items)}</b> in this briefing
</div>
""")

    if not items:
        parts.append('<p style="font-style:italic;color:#666">No relevant news today.</p>')
    else:
        for sector in sector_order:
            arts = grouped.get(sector, [])
            if not arts:
                continue
            parts.append(f'<div class="sector">{_esc(sector)}:</div><ul>')
            for it in arts:
                text = _esc(it.text.strip())
                link = f'<a href="{_esc(it.url)}">{_esc(it.source)}</a>' if it.url else _esc(it.source)
                parts.append(f"<li>{text} &mdash; {link}</li>")
            parts.append("</ul>")

    parts.append("""
<div class="footer">Generated automatically — AI View · Equity Research.
If you wish to be removed from this mailing list, please let us know.</div>
</div></body></html>""")

    return "".join(parts)


def send_briefing_v2_email(
    items: list["BriefItemV2"],
    stats: "PipelineStats | None",
    to: list[str],
) -> None:
    """Email briefing v2 (default): inglês, factual, por setor."""
    from config.settings import settings

    try:
        subject = "Safra's Morning Notes - AI View - Capital Goods"
        html = _build_briefing_v2_html(items, stats)

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = settings.email_from
        msg["To"] = ", ".join(to)
        msg.attach(MIMEText(html, "html", "utf-8"))

        _send(msg, to)
    except Exception as exc:
        log.warning(f"send_briefing_v2_email falhou (não crítico): {exc}", exc_info=True)


def send_briefing_email(analyses: list[AnalysisResult], to: list[str]) -> None:
    """Email 1: briefing completo como HTML no corpo."""
    from config.settings import settings

    try:
        date_str = datetime.now().strftime("%d/%m/%Y")
        subject = f"📰 Clipping — Capital Goods & Industrials — {date_str}"

        html = _build_briefing_html(analyses)

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = settings.email_from
        msg["To"] = ", ".join(to)
        msg.attach(MIMEText(html, "html", "utf-8"))

        _send(msg, to)
    except Exception as exc:
        log.warning(f"send_briefing_email falhou (não crítico): {exc}", exc_info=True)


def send_ops_report(stats: PipelineStats, to: list[str]) -> None:
    """Email 2: relatório operacional com métricas do pipeline."""
    from config.settings import settings

    try:
        date_str = stats.started_at.strftime("%d/%m/%Y %H:%M")
        status = "✅ OK" if not stats.error else "❌ ERRO"
        subject = f"🔧 Clipping Ops {status} — {date_str}"

        html = _build_ops_html(stats)

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = settings.email_from
        msg["To"] = ", ".join(to)
        msg.attach(MIMEText(html, "html", "utf-8"))

        _send(msg, to)
    except Exception as exc:
        log.warning(f"send_ops_report falhou (não crítico): {exc}", exc_info=True)
