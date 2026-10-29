"""
Ferramenta de reclassificação de tickers do dataset.

Corrige o viés setorial do screener: o dataset tem EMBR3 super-representado e
muitas notícias macro/setoriais labeladas sem ticker, o que faz o kNN cair em
EMBR3 por default. Aqui você revisa e corrige os tickers dos artigos; as
correções vão para `feedback/labeled/ticker_corrections.jsonl` e são aplicadas
automaticamente pelo `data.ingest_history` (precedência sobre o dataset bruto,
com tickers verbatim — lista vazia = macro).

Uso:
    streamlit run reclassify_tickers.py

Depois de salvar, reingira e recalibre:
    python -m data.ingest_history
    python -m data.calibrate_threshold
"""

import hashlib
import json
import re
from pathlib import Path

import streamlit as st

from config.settings import COVERAGE_UNIVERSE
from screener.ticker_classifier import _keyword_fallback

DATASET_PATH = Path("news_dataset/clipping_enriched.jsonl")
CORRECTIONS_PATH = Path("feedback/labeled/ticker_corrections.jsonl")

TICKERS = list(COVERAGE_UNIVERSE.keys())

# Identificadores FORTES (nome/marca da empresa) — usados para detectar suspeitas.
# Diferente das keywords genéricas de setor (ex.: "buses", "solar energy"), estes
# só casam quando a empresa em si é mencionada, evitando flag em notícia macro de
# concorrente. Keyword genérica entra só como dica (💡), não como suspeita.
STRONG_IDENTIFIERS: dict[str, list[str]] = {
    "ARML3": ["armac"],
    "MILS3": ["mills", "mills estruturas"],
    "WEGE3": ["weg"],
    "EMBR3": ["embraer", "eve air mobility", "c-390", "kc-390", "e-jet",
              "e190", "e195", "e175", "e170", "praetor", "phenom"],
    "POMO4": ["marcopolo", "volare", "paradiso"],
    "RAPT4": ["randoncorp", "randon"],
    "FRAS3": ["frasle", "fras-le"],
    "MYPK3": ["iochpe", "maxion"],
    "GGPS3": ["gps participações", "gps participacoes"],
    "INTB3": ["intelbras"],
}
_STRONG_PATTERNS = {
    t: [re.compile(r"\b" + re.escape(k) + r"\b") for k in ks]
    for t, ks in STRONG_IDENTIFIERS.items()
}


def _strong_tickers(text: str) -> list[str]:
    tl = text.lower()
    return [t for t, pats in _STRONG_PATTERNS.items() if any(p.search(tl) for p in pats)]

st.set_page_config(page_title="Reclassificação de Tickers", page_icon="🏷️", layout="wide")


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def _record_id(url: str, title: str) -> str:
    return hashlib.sha256((url + title).encode()).hexdigest()[:32]


def _norm_tickers(raw) -> list[str]:
    """Normaliza all_tickers (pode conter None) para lista de strings válidas."""
    if not isinstance(raw, list):
        raw = [raw]
    return [t for t in raw if t and t != "None"]


@st.cache_data(ttl=120)
def load_dataset() -> list[dict]:
    if not DATASET_PATH.exists():
        return []
    out = []
    with open(DATASET_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def load_corrections() -> dict[str, dict]:
    """Correções já salvas, indexadas por record_id (para resume)."""
    out: dict[str, dict] = {}
    if not CORRECTIONS_PATH.exists():
        return out
    with open(CORRECTIONS_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            rid = _record_id(r.get("url", ""), r.get("title", ""))
            out[rid] = r
    return out


@st.cache_data(ttl=120)
def analyze_dataset(dataset: list[dict]) -> list[dict]:
    """Anota cada artigo com tickers atuais, sugestão por keyword e suspeita."""
    rows = []
    for idx, r in enumerate(dataset):
        if r.get("relevant_yes_no") is not True:
            continue  # só positivos vão ao Qdrant
        title = str(r.get("title") or "")
        body = str(r.get("body") or "")
        url = str(r.get("url") or "")
        current = _norm_tickers(r.get("all_tickers") or ([r.get("ticker")] if r.get("ticker") else []))
        text = f"{title} {body}"
        kw = list(_keyword_fallback(text).keys())          # dica genérica (pode ter ruído)
        strong = _strong_tickers(text)                      # empresa realmente citada

        # Suspeita baseada em IDENTIFICADOR FORTE (não em keyword genérica):
        missing = [t for t in strong if t not in current]   # cita empresa mas falta no label
        ghost = [t for t in current if t not in strong]      # tem no label mas empresa não citada
        suspicious, reason = False, ""
        if not current and strong:
            suspicious, reason = True, f"macro mas cita {', '.join(strong)}"
        elif missing:
            suspicious, reason = True, f"falta no label: {', '.join(missing)}"
        elif ghost:
            suspicious, reason = True, f"no label sem citação: {', '.join(ghost)}"

        rows.append({
            "idx": idx,
            "id": _record_id(url, title),
            "title": title,
            "url": url,
            "body": body,
            "source": str(r.get("sender") or r.get("source") or ""),
            "date": str(r.get("date") or ""),
            "current": current,
            "kw": kw,
            "suspicious": suspicious,
            "reason": reason,
        })
    return rows


def append_corrections(dataset: list[dict], new: list[dict]) -> None:
    """Faz merge das novas correções com as existentes (por id) e regrava o arquivo."""
    existing = load_corrections()
    for rec in new:
        rid = _record_id(rec["url"], rec["title"])
        existing[rid] = rec
    CORRECTIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CORRECTIONS_PATH, "w", encoding="utf-8") as f:
        for rec in existing.values():
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

st.title("🏷️ Reclassificação de Tickers do Dataset")
st.caption(
    "Corrija os tickers para rebalancear o viés EMBR3. Multiselect **vazio = macro** "
    "(sem ticker). As correções vão para `feedback/labeled/ticker_corrections.jsonl`."
)

dataset = load_dataset()
if not dataset:
    st.error(f"Dataset não encontrado em `{DATASET_PATH}`.")
    st.stop()

rows = analyze_dataset(dataset)
corrections = load_corrections()

# --- métricas topo ---
n_pos = len(rows)
n_susp = sum(1 for r in rows if r["suspicious"])
n_done = sum(1 for r in rows if r["id"] in corrections)
m1, m2, m3, m4 = st.columns(4)
m1.metric("Positivos no dataset", f"{n_pos:,}")
m2.metric("⚠️ Suspeitos", f"{n_susp:,}")
m3.metric("✅ Já corrigidos", f"{n_done:,}")
m4.metric("Tickers cobertos", len(TICKERS))

st.divider()

# --- filtros ---
fc1, fc2, fc3, fc4 = st.columns([1.4, 1.4, 2, 1])
with fc1:
    only_suspicious = st.checkbox("Apenas suspeitos ⚠️", value=True)
with fc2:
    hide_done = st.checkbox("Esconder já corrigidos", value=True)
with fc3:
    ticker_filter = st.selectbox("Ticker atual", ["(todos)"] + TICKERS + ["(macro/sem ticker)"])
with fc4:
    search = st.text_input("Buscar", "")

visible = rows
if only_suspicious:
    visible = [r for r in visible if r["suspicious"]]
if hide_done:
    visible = [r for r in visible if r["id"] not in corrections]
if ticker_filter == "(macro/sem ticker)":
    visible = [r for r in visible if not r["current"]]
elif ticker_filter != "(todos)":
    visible = [r for r in visible if ticker_filter in r["current"]]
if search.strip():
    s = search.lower()
    visible = [r for r in visible if s in r["title"].lower() or s in r["body"].lower()]

# --- paginação ---
PAGE_SIZE = 20
n_pages = max(1, (len(visible) + PAGE_SIZE - 1) // PAGE_SIZE)
if "rc_page" not in st.session_state:
    st.session_state.rc_page = 0
st.session_state.rc_page = min(st.session_state.rc_page, n_pages - 1)

pc1, pc2, pc3 = st.columns([1, 3, 1])
with pc1:
    if st.button("◀ Anterior", width="stretch", disabled=st.session_state.rc_page == 0):
        st.session_state.rc_page -= 1
        st.rerun()
with pc2:
    st.markdown(
        f"<div style='text-align:center'>{len(visible)} artigos · "
        f"página {st.session_state.rc_page + 1}/{n_pages}</div>",
        unsafe_allow_html=True,
    )
with pc3:
    if st.button("Próxima ▶", width="stretch", disabled=st.session_state.rc_page >= n_pages - 1):
        st.session_state.rc_page += 1
        st.rerun()

page = st.session_state.rc_page
page_rows = visible[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]

st.divider()

if not page_rows:
    st.info("Nenhum artigo corresponde aos filtros.")
else:
    with st.form(key=f"rc_form_{page}"):
        for r in page_rows:
            rid = r["id"]
            saved = corrections.get(rid)
            default = _norm_tickers(saved.get("all_tickers")) if saved else r["current"]

            c1, c2 = st.columns([3, 2])
            with c1:
                badge = "⚠️ " if r["suspicious"] else ""
                link = f" [🔗]({r['url']})" if r["url"] else ""
                st.markdown(f"{badge}**{r['title'][:140]}**{link}")
                meta = f"{r['source'][:30]} · {r['date']}"
                if r["reason"]:
                    meta += f" · _{r['reason']}_"
                st.caption(meta)
                if r["body"]:
                    with st.expander("Ver texto"):
                        st.write(r["body"][:2500])
            with c2:
                st.multiselect(
                    f"Tickers (atual: {', '.join(r['current']) or 'macro'})",
                    options=TICKERS,
                    default=[t for t in default if t in TICKERS],
                    key=f"ms_{r['idx']}",
                    help="Vazio = macro/setorial (sem ticker)",
                )
                if r["kw"]:
                    st.caption(f"💡 keyword sugere: {', '.join(r['kw'])}")
            st.divider()

        submitted = st.form_submit_button("💾 Salvar página", type="primary", width="stretch")

    if submitted:
        new = []
        for r in page_rows:
            sel = st.session_state.get(f"ms_{r['idx']}", [])
            orig = dataset[r["idx"]]
            new.append({
                "date": orig.get("date"),
                "title": r["title"],
                "url": r["url"],
                "source": r["source"],
                "body": orig.get("body"),
                "relevant_yes_no": True,
                "all_tickers": sel,
                "_reviewed": True,
                "_reclassified": True,
            })
        append_corrections(dataset, new)
        st.success(f"Salvas {len(new)} correções em `{CORRECTIONS_PATH}`.")
        st.code(
            "python -m data.ingest_history\npython -m data.calibrate_threshold",
            language="bash",
        )
