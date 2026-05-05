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

tab_db, tab_review = st.tabs(["📊 Base de Dados", "🏷️ Revisão"])


# ============================================================
# TAB 1 — BASE DE DADOS
# ============================================================

with tab_db:
    st.title("📊 Base de Dados")

    col_refresh, _ = st.columns([1, 5])
    with col_refresh:
        if st.button("🔄 Atualizar", use_container_width=True):
            st.cache_data.clear()
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
                n_lines = sum(1 for _ in open(path, encoding="utf-8"))
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

    if "index" not in st.session_state:
        unreviewed = [i for i, r in enumerate(st.session_state.records) if not r.get("_reviewed")]
        st.session_state.index = unreviewed[0] if unreviewed else 0

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
        st.stop()

    if remaining == 0:
        st.success("Todos os artigos foram revisados!")
        st.balloons()
        if st.button("Exportar para feedback/labeled/ e preparar ingest", type="primary"):
            out = export_labeled(records)
            st.success(f"Exportado: `{out}`")
            st.code(
                "python -m data.ingest_history\n"
                "python -m data.calibrate_threshold",
                language="bash",
            )
        st.stop()

    idx = st.session_state.index
    if idx >= total:
        idx = 0
        st.session_state.index = 0

    record = records[idx]

    # Navigation
    nav_col1, nav_col2, nav_col3 = st.columns([1, 3, 1])
    with nav_col1:
        if st.button("← Anterior", use_container_width=True):
            st.session_state.index = max(0, idx - 1)
            st.rerun()
    with nav_col2:
        st.markdown(
            f"<p style='text-align:center; color:gray; margin-top:8px;'>"
            f"Artigo {idx + 1} de {total} — {remaining} restantes</p>",
            unsafe_allow_html=True,
        )
    with nav_col3:
        if st.button("Próximo →", use_container_width=True):
            st.session_state.index = min(total - 1, idx + 1)
            st.rerun()

    # Article card
    status_icon = ""
    if record.get("_reviewed"):
        status_icon = "✅ " if record.get("relevant_yes_no") else "❌ "

    st.subheader(f"{status_icon}{record.get('title', '(sem título)')}")

    ctype = record.get("candidate_type", "near_miss")
    type_badge = "🟢 Aprovado" if ctype == "approved" else "🟡 Near-miss"
    tier_label = record.get("tier", "—")
    st.caption(f"{type_badge}  ·  Score: `{record.get('relevance_score', 0):.4f}`  ·  Tier: `{tier_label}`")

    meta_col1, meta_col2, meta_col3 = st.columns(3)
    meta_col1.markdown(f"**Fonte:** {record.get('source', '—')}")
    meta_col2.markdown(f"**Data:** {record.get('date', '—')}")
    meta_col3.markdown(f"**Tickers:** {', '.join(record.get('predicted_tickers', [])) or '—'}")

    url = record.get("url", "")
    if url:
        st.markdown(f"[Abrir artigo]({url})")

    if record.get("body"):
        with st.expander("Ver texto completo"):
            st.write(record["body"][:2000])

    st.divider()

    # Action buttons
    def _label(relevant: bool | None) -> None:
        records[idx]["relevant_yes_no"] = relevant
        records[idx]["_reviewed"] = True
        save_candidates(records)
        unreviewed = [i for i, r in enumerate(records) if not r.get("_reviewed")]
        st.session_state.index = unreviewed[0] if unreviewed else idx + 1
        st.rerun()

    btn1, btn2, btn3 = st.columns(3)
    with btn1:
        st.button("✅ Relevante", use_container_width=True, type="primary",
                  on_click=_label, args=(True,),
                  help="Artigo É relevante — o screener errou ao descartar")
    with btn2:
        st.button("❌ Não Relevante", use_container_width=True,
                  on_click=_label, args=(False,),
                  help="Artigo NÃO é relevante — confirma o descarte")
    with btn3:
        if st.button("⏭ Pular", use_container_width=True, help="Revisar depois"):
            st.session_state.index = min(total - 1, idx + 1)
            st.rerun()

    # Sidebar
    with st.sidebar:
        st.header("Navegação")

        filter_type = st.radio(
            "Tipo",
            ["Todos", "🟢 Aprovados", "🟡 Near-miss"],
            index=0,
        )

        filter_opt = st.radio(
            "Status",
            ["Todos", "Não revisados", "Relevantes", "Irrelevantes"],
            index=1,
        )

        # Aplica filtro de tipo
        if filter_type == "🟢 Aprovados":
            type_filtered = [i for i, r in enumerate(records) if r.get("candidate_type") == "approved"]
        elif filter_type == "🟡 Near-miss":
            type_filtered = [i for i, r in enumerate(records) if r.get("candidate_type", "near_miss") == "near_miss"]
        else:
            type_filtered = list(range(total))

        # Aplica filtro de status sobre o resultado do filtro de tipo
        if filter_opt == "Não revisados":
            visible = [i for i in type_filtered if not records[i].get("_reviewed")]
        elif filter_opt == "Relevantes":
            visible = [i for i in type_filtered if records[i].get("relevant_yes_no") is True]
        elif filter_opt == "Irrelevantes":
            visible = [i for i in type_filtered if records[i].get("relevant_yes_no") is False]
        else:
            visible = type_filtered

        if visible:
            titles = [
                f"{'✅' if records[i].get('relevant_yes_no') is True else '❌' if records[i].get('relevant_yes_no') is False else '⬜'} "
                f"{'🟢' if records[i].get('candidate_type') == 'approved' else '🟡'} "
                f"{records[i].get('title', '')[:45]}..."
                for i in visible
            ]
            chosen = st.selectbox("Ir para artigo", range(len(visible)), format_func=lambda x: titles[x])
            if st.button("Ir"):
                st.session_state.index = visible[chosen]
                st.rerun()

        st.divider()
        st.header("Exportar")
        if st.button("Salvar para labeled/", use_container_width=True):
            out = export_labeled(records)
            st.success(f"Salvo em:\n`{out}`")
            st.code(
                "python -m data.ingest_history\n"
                "python -m data.calibrate_threshold",
                language="bash",
            )
