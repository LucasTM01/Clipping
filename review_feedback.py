"""
Aplicativo de gestão do pipeline de clipping.

Abas:
  📊 Base de Dados — visão geral do dataset e Qdrant
  🏷️ Revisão       — classificar near-miss candidates

Uso:
    streamlit run review_feedback.py
"""

import json
from pathlib import Path

import streamlit as st

CANDIDATES_PATH = Path("feedback/candidates.jsonl")
LABELED_DIR = Path("feedback/labeled")
DATASET_PATH = Path("news_dataset/clipping_enriched.jsonl")

st.set_page_config(
    page_title="Clipping — Gestão",
    page_icon="📰",
    layout="wide",
)


# ---------------------------------------------------------------------------
# Shared data loading
# ---------------------------------------------------------------------------

@st.cache_data(ttl=60)
def load_dataset() -> list[dict]:
    if not DATASET_PATH.exists():
        return []
    records = []
    with open(DATASET_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return records


@st.cache_data(ttl=60)
def load_labeled() -> list[dict]:
    records = []
    if not LABELED_DIR.exists():
        return records
    for path in sorted(LABELED_DIR.glob("*.jsonl")):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    return records


def load_candidates() -> list[dict]:
    if not CANDIDATES_PATH.exists():
        return []
    records = []
    with open(CANDIDATES_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return records


def save_candidates(records: list[dict]) -> None:
    with open(CANDIDATES_PATH, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def export_labeled(records: list[dict]) -> Path:
    LABELED_DIR.mkdir(parents=True, exist_ok=True)
    out_path = LABELED_DIR / "reviewed.jsonl"
    reviewed = [r for r in records if r.get("_reviewed")]
    with open(out_path, "w", encoding="utf-8") as f:
        for r in reviewed:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return out_path


@st.cache_data(ttl=120)
def load_audit_logs() -> list[dict]:
    records = []
    for path in sorted(Path("output").glob("screening_audit_*.jsonl")):
        date_str = path.stem.replace("screening_audit_", "")  # YYYYMMDD
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            r = json.loads(line)
                            r["_run_date"] = date_str
                            records.append(r)
                        except json.JSONDecodeError:
                            pass
        except OSError:
            pass
    return records


@st.cache_data(ttl=60)
def get_qdrant_count() -> int | None:
    try:
        from qdrant_client import QdrantClient
        from config.settings import settings
        client = QdrantClient(host=settings.qdrant_host, port=settings.qdrant_port, timeout=3)
        return client.count(collection_name=settings.qdrant_collection).count
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------

tab_db, tab_review, tab_stats = st.tabs(["📊 Base de Dados", "🏷️ Revisão", "📈 Estatísticas"])


# ============================================================
# TAB 1 — BASE DE DADOS
# ============================================================

with tab_db:
    st.title("📊 Base de Dados")

    col_refresh, _ = st.columns([1, 5])
    with col_refresh:
        if st.button("🔄 Atualizar", width="stretch"):
            st.cache_data.clear()
            st.session_state.pop("records", None)   # força reload dos candidatos
            st.rerun()

    dataset = load_dataset()
    labeled = load_labeled()
    candidates = load_candidates()

    # --- Qdrant ---
    qdrant_count = get_qdrant_count()

    st.subheader("Qdrant (vetores em memória)")
    if qdrant_count is None:
        st.warning("Qdrant não conectado — verifique se o Docker está rodando.")
    else:
        st.metric("Vetores no Qdrant", f"{qdrant_count:,}")

    st.divider()

    # --- Dataset principal ---
    st.subheader("Dataset Principal — `news_dataset/clipping_enriched.jsonl`")

    if not dataset:
        st.warning("Arquivo não encontrado.")
    else:
        n_total = len(dataset)
        n_relevant = sum(1 for r in dataset if r.get("relevant_yes_no") is True)
        n_irrelevant = sum(1 for r in dataset if r.get("relevant_yes_no") is False)
        n_null = sum(1 for r in dataset if r.get("relevant_yes_no") is None)
        n_with_title = sum(1 for r in dataset if (r.get("title") or "").strip())
        n_with_body = sum(1 for r in dataset if (r.get("body") or "").strip())
        n_with_ticker = sum(1 for r in dataset if r.get("ticker"))

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total", f"{n_total:,}")
        c2.metric("Relevantes ✅", f"{n_relevant:,}")
        c3.metric("Irrelevantes ❌", f"{n_irrelevant:,}")
        c4.metric("Sem label", f"{n_null:,}")

        c5, c6, c7, _ = st.columns(4)
        c5.metric("Com headline", f"{n_with_title:,}")
        c6.metric("Com body", f"{n_with_body:,}")
        c7.metric("Com ticker", f"{n_with_ticker:,}")

        # Breakdown por ticker
        from collections import Counter
        ticker_counts = Counter(
            r.get("ticker") for r in dataset if r.get("ticker")
        )
        if ticker_counts:
            st.markdown("**Distribuição por ticker:**")
            cols = st.columns(min(len(ticker_counts), 5))
            for i, (ticker, count) in enumerate(sorted(ticker_counts.items())):
                cols[i % len(cols)].metric(ticker, count)

    st.divider()

    # --- Feedback labeled ---
    st.subheader("Feedback Revisado — `feedback/labeled/`")

    if not labeled:
        st.info("Nenhum arquivo em `feedback/labeled/` ainda.")
    else:
        n_lab_total = len(labeled)
        n_lab_pos = sum(1 for r in labeled if r.get("relevant_yes_no") is True and r.get("_reviewed"))
        n_lab_neg = sum(1 for r in labeled if r.get("relevant_yes_no") is False and r.get("_reviewed"))
        n_lab_skip = n_lab_total - n_lab_pos - n_lab_neg

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total revisados", f"{n_lab_total:,}")
        c2.metric("Positivos ✅", f"{n_lab_pos:,}")
        c3.metric("Negativos ❌", f"{n_lab_neg:,}")
        c4.metric("Pulados", f"{n_lab_skip:,}")

        # List files
        with st.expander("Arquivos em feedback/labeled/"):
            for path in sorted(LABELED_DIR.glob("*.jsonl")):
                size_kb = path.stat().st_size // 1024
                with open(path, encoding="utf-8") as fh:
                    n_lines = sum(1 for _ in fh)
                st.markdown(f"- `{path.name}` — {n_lines} registros, {size_kb} KB")

    st.divider()

    # --- Candidates (near-miss + aprovados) ---
    st.subheader("Candidatos para Revisão — `feedback/candidates.jsonl`")

    n_cand = len(candidates)
    n_cand_rev = sum(1 for r in candidates if r.get("_reviewed"))
    n_cand_pending = n_cand - n_cand_rev
    n_approved_cand = sum(1 for r in candidates if r.get("candidate_type") == "approved")
    n_nearmiss_cand = n_cand - n_approved_cand

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Total", f"{n_cand:,}")
    c2.metric("🟢 Aprovados", f"{n_approved_cand:,}")
    c3.metric("🟡 Near-miss", f"{n_nearmiss_cand:,}")
    c4.metric("Revisados", f"{n_cand_rev:,}")
    c5.metric("Pendentes", f"{n_cand_pending:,}", delta=f"-{n_cand_pending}" if n_cand_pending else None)

    if n_cand_pending > 0:
        st.info(f"Há {n_cand_pending} candidatos aguardando revisão. Vá para a aba **🏷️ Revisão**.")


# ============================================================
# TAB 2 — REVISÃO
# ============================================================

with tab_review:
    st.title("🏷️ Revisão de Candidatos")
    st.caption("Classifique os artigos near-miss para melhorar o screener")

    if "records" not in st.session_state:
        st.session_state.records = load_candidates()

    records = st.session_state.records
    total = len(records)
    reviewed_count = sum(1 for r in records if r.get("_reviewed"))
    relevant_count = sum(1 for r in records if r.get("relevant_yes_no") is True)
    irrelevant_count = sum(1 for r in records if r.get("relevant_yes_no") is False)
    remaining = total - reviewed_count

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total", total)
    col2.metric("Revisados", reviewed_count)
    col3.metric("Relevantes", relevant_count)
    col4.metric("Irrelevantes", irrelevant_count)

    if total > 0:
        st.progress(reviewed_count / total, text=f"{reviewed_count}/{total} revisados")

    st.divider()

    if total == 0:
        st.warning("Nenhum candidato encontrado em `feedback/candidates.jsonl`.")
        st.info("Execute o pipeline primeiro: `python main.py --no-enrich`")
    else:
        # ── Inline filters + export ─────────────────────────────────────────
        fc1, fc2, fc3 = st.columns([2, 2, 1])
        with fc1:
            filter_type = st.selectbox(
                "Tipo",
                ["Todos", "🟢 Aprovados", "🟡 Near-miss"],
                index=0,
                key="rv_filter_type",
            )
        with fc2:
            filter_opt = st.selectbox(
                "Status",
                ["Todos", "Não revisados", "Relevantes", "Irrelevantes"],
                index=1,
                key="rv_filter_opt",
            )
        with fc3:
            st.write("")  # spacer for vertical alignment
            if st.button("💾 Salvar para labeled/", width="stretch", key="rv_export"):
                out = export_labeled(records)
                st.success(f"Salvo: `{out}`")
                st.code(
                    "python -m data.ingest_history\n"
                    "python -m data.calibrate_threshold",
                    language="bash",
                )

        # ── Compute visible indices from filters ────────────────────────────
        if filter_type == "🟢 Aprovados":
            type_filtered = [i for i, r in enumerate(records) if r.get("candidate_type") == "approved"]
        elif filter_type == "🟡 Near-miss":
            type_filtered = [i for i, r in enumerate(records) if r.get("candidate_type", "near_miss") == "near_miss"]
        else:
            type_filtered = list(range(total))

        if filter_opt == "Não revisados":
            visible = [i for i in type_filtered if not records[i].get("_reviewed")]
        elif filter_opt == "Relevantes":
            visible = [i for i in type_filtered if records[i].get("relevant_yes_no") is True]
        elif filter_opt == "Irrelevantes":
            visible = [i for i in type_filtered if records[i].get("relevant_yes_no") is False]
        else:
            visible = type_filtered

        # ── Pagination ──────────────────────────────────────────────────────
        _PAGE_SIZE = 100
        n_pages = max(1, (len(visible) + _PAGE_SIZE - 1) // _PAGE_SIZE)
        st.session_state["_n_pages"] = n_pages

        if "rv_page" not in st.session_state:
            st.session_state.rv_page = 0
        # Clamp page index when filter changes shrink the page count
        if st.session_state.rv_page >= n_pages:
            st.session_state.rv_page = 0

        page = st.session_state.rv_page
        page_visible = visible[page * _PAGE_SIZE : (page + 1) * _PAGE_SIZE]

        # Store page_visible for bulk callbacks — avoids closure issues
        st.session_state["_page_visible"] = page_visible

        # ── Callbacks ───────────────────────────────────────────────────────
        def _select_all_page() -> None:
            for i in st.session_state.get("_page_visible", []):
                st.session_state[f"sel_{i}"] = True

        def _deselect_all_page() -> None:
            for i in st.session_state.get("_page_visible", []):
                st.session_state[f"sel_{i}"] = False

        def _bulk_label(relevant: bool) -> None:
            selected = [
                i for i in st.session_state.get("_page_visible", [])
                if st.session_state.get(f"sel_{i}", False)
            ]
            if not selected:
                return
            updated = list(st.session_state.records)
            for i in selected:
                updated[i] = {**updated[i], "relevant_yes_no": relevant, "_reviewed": True}
                st.session_state[f"sel_{i}"] = False  # uncheck after labeling
            st.session_state.records = updated
            save_candidates(updated)

        def _go_prev() -> None:
            st.session_state.rv_page = max(0, st.session_state.rv_page - 1)

        def _go_next() -> None:
            st.session_state.rv_page = min(
                st.session_state.get("_n_pages", 1) - 1,
                st.session_state.rv_page + 1,
            )

        # ── All-reviewed banner ─────────────────────────────────────────────
        if remaining == 0:
            st.success("Todos os artigos foram revisados!")
            if st.button(
                "Exportar para feedback/labeled/ e preparar ingest",
                type="primary",
                key="rv_export_all",
            ):
                out = export_labeled(records)
                st.success(f"Exportado: `{out}`")
                st.code(
                    "python -m data.ingest_history\n"
                    "python -m data.calibrate_threshold",
                    language="bash",
                )

        if not visible:
            st.info(
                "Nenhum artigo corresponde aos filtros selecionados. "
                "Mude o filtro de **Status** para **Todos** para ver candidatos já revisados."
            )
        else:
            # Count selected on this page (computed before widgets render)
            n_selected = sum(
                1 for i in page_visible
                if st.session_state.get(f"sel_{i}", False)
            )

            # ── Toolbar ─────────────────────────────────────────────────────
            tb1, tb2, tb3, tb4, tb5, tb6 = st.columns([1.2, 1.2, 2, 2, 0.9, 0.9])
            with tb1:
                st.button(
                    "☑ Todos",
                    width="stretch",
                    on_click=_select_all_page,
                    key="rv_sel_all",
                    help="Seleciona todos os artigos desta página",
                )
            with tb2:
                st.button(
                    "⬜ Nenhum",
                    width="stretch",
                    on_click=_deselect_all_page,
                    key="rv_sel_none",
                    help="Desmarca todos",
                )
            with tb3:
                st.button(
                    f"✅ Relevante ({n_selected})",
                    width="stretch",
                    type="primary",
                    on_click=_bulk_label,
                    args=(True,),
                    disabled=n_selected == 0,
                    key="rv_bulk_rel",
                    help="Marca selecionados como Relevantes",
                )
            with tb4:
                st.button(
                    f"❌ Irrelevante ({n_selected})",
                    width="stretch",
                    on_click=_bulk_label,
                    args=(False,),
                    disabled=n_selected == 0,
                    key="rv_bulk_irr",
                    help="Marca selecionados como Não Relevantes",
                )
            with tb5:
                st.button(
                    "◀",
                    width="stretch",
                    on_click=_go_prev,
                    disabled=page == 0,
                    key="rv_prev",
                    help="Página anterior",
                )
            with tb6:
                st.button(
                    "▶",
                    width="stretch",
                    on_click=_go_next,
                    disabled=page >= n_pages - 1,
                    key="rv_next",
                    help="Próxima página",
                )

            st.caption(
                f"{len(visible)} artigos visíveis · página **{page + 1}/{n_pages}**"
                f" · {n_selected} selecionados · {remaining} pendentes"
            )

            st.divider()

            # ── Column headers ──────────────────────────────────────────────
            hc0, hc1, hc2, hc3, hc4, hc5, hc6 = st.columns([0.5, 0.7, 5, 1.5, 1, 1.2, 2])
            hc0.markdown("**☑**")
            hc1.markdown("**Tipo**")
            hc2.markdown("**Título**")
            hc3.markdown("**Tickers**")
            hc4.markdown("**Score**")
            hc5.markdown("**Tier**")
            hc6.markdown("**Fonte / Data**")
            st.divider()

            # ── Article list (current page only — max 25 rows) ──────────────
            for i in page_visible:
                r = records[i]
                rv_val = r.get("relevant_yes_no")
                reviewed = r.get("_reviewed", False)
                status_icon = (
                    "✅" if (reviewed and rv_val is True)
                    else "❌" if (reviewed and rv_val is False)
                    else "⬜"
                )
                type_icon = "🟢" if r.get("candidate_type") == "approved" else "🟡"
                title = r.get("title", "(sem título)")
                tickers = ", ".join(r.get("predicted_tickers", [])) or "—"
                score = r.get("relevance_score", 0)
                tier = r.get("tier", "—")
                source = r.get("source", "—")
                date = r.get("date", "—")
                url = r.get("url", "")

                c0, c1, c2, c3, c4, c5, c6 = st.columns([0.5, 0.7, 5, 1.5, 1, 1.2, 2])
                with c0:
                    st.checkbox(
                        label="sel",
                        key=f"sel_{i}",
                        label_visibility="collapsed",
                    )
                with c1:
                    st.markdown(f"{status_icon} {type_icon}")
                with c2:
                    title_md = f"**{title[:90]}{'…' if len(title) > 90 else ''}**"
                    if url:
                        title_md += f" [🔗]({url})"
                    st.markdown(title_md)
                with c3:
                    st.markdown(tickers)
                with c4:
                    st.markdown(f"`{score:.3f}`")
                with c5:
                    st.markdown(f"`{tier}`")
                with c6:
                    st.markdown(f"{source[:22]}  \n{date}")

                if r.get("body"):
                    with st.expander("Ver texto", expanded=False):
                        st.write(r["body"][:2000])

                st.divider()


# ============================================================
# TAB 3 — ESTATÍSTICAS / DASHBOARD
# ============================================================


def _build_audit_df(records: list[dict]):
    """Defensive DataFrame construction — guarantees expected columns/dtypes."""
    import pandas as pd

    df = pd.DataFrame(records)
    if df.empty:
        return df

    defaults = {
        "source": "",
        "tier": "",
        "title": "",
        "url": "",
        "rationale": "",
        "relevance_score": 0.0,
        "sent_to_analysis": False,
        "predicted_tickers": None,
    }
    for col, default in defaults.items():
        if col not in df.columns:
            df[col] = default

    df["date"] = pd.to_datetime(df["_run_date"], format="%Y%m%d", errors="coerce")
    if "timestamp" in df.columns:
        df["ts"] = pd.to_datetime(df["timestamp"], errors="coerce")
    else:
        df["ts"] = df["date"]

    df["source"] = df["source"].fillna("").astype(str).replace("", "(desconhecido)")
    df["tier"] = df["tier"].fillna("").astype(str).replace("", "discard")
    df["sent_to_analysis"] = df["sent_to_analysis"].fillna(False).astype(bool)
    df["relevance_score"] = pd.to_numeric(df["relevance_score"], errors="coerce").fillna(0.0)

    df = df.dropna(subset=["date"]).copy()
    return df


def _section(title: str, render):
    """Wrap a dashboard section in its own try/except."""
    try:
        render()
    except Exception as exc:
        st.error(f"❌ Falha em '{title}': {exc}")


def _render_stats() -> None:
    """Conteúdo do dashboard de estatísticas.

    Usa `return` antecipado em vez de `st.stop()` — st.stop() dentro de um
    context manager de tab (with tab_stats:) levanta StopException que pode
    corromper o registro de widgets de outros tabs, causando que callbacks
    on_click não disparem mesmo em tabs já renderizados.
    """
    import pandas as pd
    import numpy as np

    audit_records = load_audit_logs()
    if not audit_records:
        st.warning(
            "Nenhum `output/screening_audit_*.jsonl` encontrado. "
            "Execute o pipeline primeiro."
        )
        return

    df_full = _build_audit_df(audit_records)
    if df_full.empty:
        st.warning("Audit logs vazios após parsing.")
        return

    # --- Date range filter ---
    min_d = df_full["date"].min().date()
    max_d = df_full["date"].max().date()
    if min_d == max_d:
        date_range = (min_d, max_d)
        st.caption(f"Período disponível: **{min_d}** (apenas 1 dia)")
    else:
        date_range = st.slider(
            "Período",
            min_value=min_d,
            max_value=max_d,
            value=(min_d, max_d),
            format="YYYY-MM-DD",
        )

    mask = (df_full["date"].dt.date >= date_range[0]) & (df_full["date"].dt.date <= date_range[1])
    df = df_full[mask].copy()

    if df.empty:
        st.warning("Nenhum registro no período selecionado.")
        return

    # =========================================================
    # KPIs
    # =========================================================
    def _render_kpis():
        n_dias = df["date"].nunique()
        n_total = len(df)
        n_fontes = df["source"].nunique()
        n_analisados = int(df["sent_to_analysis"].sum())
        approval_rate = (n_analisados / n_total * 100) if n_total else 0
        avg_per_day = n_total / n_dias if n_dias else 0
        last_run = df["date"].max().strftime("%Y-%m-%d")

        m1, m2, m3, m4, m5, m6 = st.columns(6)
        m1.metric("📅 Dias", n_dias)
        m2.metric("📰 Total artigos", f"{n_total:,}")
        m3.metric("🌐 Fontes", n_fontes)
        m4.metric("📊 Média/dia", f"{avg_per_day:,.0f}")
        m5.metric("🤖 Enviados ao LLM", f"{n_analisados}", delta=f"{approval_rate:.1f}%")
        m6.metric("🕐 Último run", last_run)

    _section("KPIs", _render_kpis)
    st.divider()

    # =========================================================
    # ALERTAS DE SAÚDE DO SCRAPING
    # =========================================================
    def _render_alerts():
        st.subheader("🚨 Alertas de saúde do scraping")
        st.caption(
            "Compara últimos 3 dias vs. histórico para detectar fontes que pararam de coletar "
            "ou caíram drasticamente em volume."
        )

        last_global_date = df_full["date"].max()
        cutoff_recent = last_global_date - pd.Timedelta(days=3)

        per_source = (
            df_full.groupby("source")
            .agg(
                total=("source", "size"),
                ultimo=("date", "max"),
                dias_ativos=("date", "nunique"),
            )
            .reset_index()
        )

        # Média histórica por dia ativo
        daily = df_full.groupby(["source", "date"]).size().reset_index(name="cnt")
        hist_avg = daily.groupby("source")["cnt"].mean().rename("media_hist")

        # Média recente (últimos 3 dias do dataset)
        recent = (
            daily[daily["date"] >= cutoff_recent]
            .groupby("source")["cnt"]
            .mean()
            .rename("media_recente")
        )

        per_source = per_source.merge(hist_avg, on="source", how="left")
        per_source = per_source.merge(recent, on="source", how="left")
        per_source["media_recente"] = per_source["media_recente"].fillna(0)

        per_source["dias_sem_aparecer"] = (last_global_date - per_source["ultimo"]).dt.days
        per_source["queda_pct"] = np.where(
            per_source["media_hist"] > 0,
            (1 - per_source["media_recente"] / per_source["media_hist"]) * 100,
            0,
        )

        # Classifica
        def _status(row):
            if row["dias_ativos"] < 2:
                return "⚪ Pouca data"
            if row["dias_sem_aparecer"] >= 7:
                return "🔴 Inativa (>7d)"
            if row["dias_sem_aparecer"] >= 3:
                return "🟠 Sem registros (3-6d)"
            if row["queda_pct"] >= 60 and row["media_hist"] >= 5:
                return "🟡 Volume caiu >60%"
            return "🟢 OK"

        per_source["status"] = per_source.apply(_status, axis=1)

        critical = per_source[per_source["status"].isin(
            ["🔴 Inativa (>7d)", "🟠 Sem registros (3-6d)", "🟡 Volume caiu >60%"]
        )].sort_values(["status", "media_hist"], ascending=[True, False])

        if critical.empty:
            st.success("✅ Todas as fontes ativas estão operando dentro do esperado.")
        else:
            st.warning(f"⚠️ {len(critical)} fonte(s) requerem atenção:")
            display = critical[[
                "status", "source", "ultimo", "dias_sem_aparecer",
                "media_hist", "media_recente", "queda_pct", "total",
            ]].copy()
            display.columns = [
                "Status", "Fonte", "Última coleta", "Dias parada",
                "Média hist./dia", "Média 3d", "Queda %", "Total",
            ]
            display["Última coleta"] = display["Última coleta"].dt.strftime("%Y-%m-%d")
            display["Média hist./dia"] = display["Média hist./dia"].round(1)
            display["Média 3d"] = display["Média 3d"].round(1)
            display["Queda %"] = display["Queda %"].round(0).astype(int).astype(str) + "%"
            st.dataframe(display, use_container_width=True, hide_index=True)

    _section("Alertas", _render_alerts)
    st.divider()

    # =========================================================
    # FUNIL DE CONVERSÃO
    # =========================================================
    def _render_funnel():
        st.subheader("🔄 Funil de conversão")
        n_total = len(df)
        n_not_discard = int((df["tier"] != "discard").sum())
        n_sent = int(df["sent_to_analysis"].sum())

        c1, c2, c3 = st.columns(3)
        c1.metric("Coletados", f"{n_total:,}", help="Total de artigos que passaram pelo screener")
        c2.metric(
            "Aprovados (não-discard)",
            f"{n_not_discard:,}",
            delta=f"{n_not_discard/n_total*100:.1f}%" if n_total else None,
        )
        c3.metric(
            "Enviados ao LLM",
            f"{n_sent:,}",
            delta=f"{n_sent/n_total*100:.1f}%" if n_total else None,
        )

    _section("Funil", _render_funnel)
    st.divider()

    # =========================================================
    # VOLUME DIÁRIO
    # =========================================================
    def _render_volume():
        st.subheader("📊 Volume diário")

        col_a, col_b = st.columns(2)

        with col_a:
            st.markdown("**Total de artigos coletados por dia**")
            daily_total = df.groupby("date").size().reset_index(name="artigos")
            daily_total = daily_total.set_index("date")
            st.line_chart(daily_total, height=280)

        with col_b:
            st.markdown("**Por tier (área empilhada)**")
            tier_daily = (
                df.groupby(["date", "tier"]).size().unstack(fill_value=0).sort_index()
            )
            preferred_order = ["high", "medium", "low_review", "discard"]
            ordered_cols = [c for c in preferred_order if c in tier_daily.columns]
            ordered_cols += [c for c in tier_daily.columns if c not in preferred_order]
            tier_daily = tier_daily[ordered_cols]
            st.area_chart(tier_daily, height=280)

    _section("Volume diário", _render_volume)
    st.divider()

    # =========================================================
    # SAÚDE POR FONTE — TABELA DETALHADA
    # =========================================================
    def _render_source_table():
        st.subheader("🌐 Saúde por fonte")
        st.caption("Volume, qualidade e atividade — clique nas colunas para ordenar")

        last_d = df_full["date"].max()
        per_source = (
            df.groupby("source")
            .agg(
                total=("source", "size"),
                dias_ativos=("date", "nunique"),
                aprovados=("sent_to_analysis", "sum"),
                score_medio=("relevance_score", "mean"),
                ultimo=("date", "max"),
            )
            .reset_index()
        )
        per_source["aprovados"] = per_source["aprovados"].astype(int)
        per_source["taxa_aprov"] = (
            per_source["aprovados"] / per_source["total"] * 100
        ).round(1)
        per_source["media_dia"] = (
            per_source["total"] / per_source["dias_ativos"]
        ).round(1)
        per_source["dias_parada"] = (last_d - per_source["ultimo"]).dt.days
        per_source["score_medio"] = per_source["score_medio"].round(3)

        per_source = per_source.sort_values("total", ascending=False)
        display = per_source[[
            "source", "total", "media_dia", "dias_ativos",
            "aprovados", "taxa_aprov", "score_medio", "ultimo", "dias_parada",
        ]].copy()
        display.columns = [
            "Fonte", "Total", "Média/dia", "Dias ativos",
            "→LLM", "Taxa aprov %", "Score médio", "Última coleta", "Dias parada",
        ]
        display["Última coleta"] = display["Última coleta"].dt.strftime("%Y-%m-%d")
        st.dataframe(display, use_container_width=True, hide_index=True, height=400)

    _section("Saúde por fonte", _render_source_table)
    st.divider()

    # =========================================================
    # HEATMAP FONTE × DIA — visual gaps
    # =========================================================
    def _render_heatmap():
        st.subheader("🗓️ Atividade fonte × dia")
        st.caption("Quadrados em branco = fonte não produziu nada naquele dia (potencial falha)")

        try:
            import altair as alt
        except ImportError:
            st.info("Altair não disponível — pulando heatmap.")
            return

        # Limita a top 20 fontes para legibilidade
        top_sources = df["source"].value_counts().head(20).index.tolist()
        sub = df[df["source"].isin(top_sources)]

        heat = (
            sub.groupby(["source", "date"]).size().reset_index(name="artigos")
        )
        heat["date_str"] = heat["date"].dt.strftime("%Y-%m-%d")

        chart = (
            alt.Chart(heat)
            .mark_rect()
            .encode(
                x=alt.X("date_str:O", title="Dia", axis=alt.Axis(labelAngle=-45)),
                y=alt.Y("source:N", title="Fonte", sort=top_sources),
                color=alt.Color(
                    "artigos:Q",
                    scale=alt.Scale(scheme="viridis"),
                    legend=alt.Legend(title="Artigos"),
                ),
                tooltip=["source", "date_str", "artigos"],
            )
            .properties(height=max(300, len(top_sources) * 22))
        )
        st.altair_chart(chart, use_container_width=True)

    _section("Heatmap", _render_heatmap)
    st.divider()

    # =========================================================
    # TOP FONTES — VOLUME E QUALIDADE
    # =========================================================
    def _render_top_sources():
        st.subheader("🏆 Top fontes")

        col_a, col_b = st.columns(2)

        with col_a:
            st.markdown("**Por volume (top 15)**")
            vol = df["source"].value_counts().head(15).rename("artigos")
            st.bar_chart(vol, height=400)

        with col_b:
            st.markdown("**Por qualidade — % não-discard (mín. 20 artigos)**")
            grp = df.groupby("source").agg(
                total=("source", "size"),
                bons=("tier", lambda s: (s != "discard").sum()),
            )
            grp = grp[grp["total"] >= 20]
            grp["taxa"] = (grp["bons"] / grp["total"] * 100).round(1)
            quality = grp.sort_values("taxa", ascending=False).head(15)["taxa"]
            if quality.empty:
                st.info("Nenhuma fonte com ≥20 artigos ainda.")
            else:
                st.bar_chart(quality, height=400)

    _section("Top fontes", _render_top_sources)
    st.divider()

    # =========================================================
    # DISTRIBUIÇÃO POR TIER
    # =========================================================
    def _render_tier():
        st.subheader("🎯 Distribuição por tier")
        tier_counts = df["tier"].value_counts()
        order = ["high", "medium", "low_review", "discard"]
        tier_counts = tier_counts.reindex([t for t in order if t in tier_counts.index])

        c1, c2 = st.columns([1, 2])
        with c1:
            for tier in tier_counts.index:
                count = int(tier_counts[tier])
                pct = count / len(df) * 100
                emoji = {"high": "🟢", "medium": "🟡", "low_review": "🟠", "discard": "⚫"}.get(tier, "⚪")
                st.metric(f"{emoji} {tier}", f"{count:,}", delta=f"{pct:.1f}%")
        with c2:
            st.bar_chart(tier_counts.rename("artigos"), height=300)

    _section("Tiers", _render_tier)
    st.divider()

    # =========================================================
    # TICKERS DETECTADOS
    # =========================================================
    def _render_tickers():
        st.subheader("🏷️ Tickers detectados")

        # Explode a lista de predicted_tickers
        rows = []
        for _, row in df.iterrows():
            tickers = row.get("predicted_tickers")
            if isinstance(tickers, list):
                for t in tickers:
                    rows.append({"date": row["date"], "ticker": t, "tier": row["tier"]})

        if not rows:
            st.info("Nenhum ticker classificado no período.")
            return

        tdf = pd.DataFrame(rows)

        col_a, col_b = st.columns(2)
        with col_a:
            st.markdown("**Frequência total**")
            tcounts = tdf["ticker"].value_counts().rename("menções")
            st.bar_chart(tcounts, height=320)
        with col_b:
            st.markdown("**Evolução por dia**")
            t_daily = tdf.groupby(["date", "ticker"]).size().unstack(fill_value=0).sort_index()
            st.line_chart(t_daily, height=320)

    _section("Tickers", _render_tickers)


with tab_stats:
    st.title("📈 Dashboard — Saúde do Pipeline")
    st.caption("Histórico de coleta, screening e detecção de problemas de scraping")

    col_refresh2, _ = st.columns([1, 5])
    with col_refresh2:
        if st.button("🔄 Atualizar", width="stretch", key="refresh_stats"):
            st.cache_data.clear()
            st.rerun()

    _render_stats()
