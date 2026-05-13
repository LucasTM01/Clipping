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
