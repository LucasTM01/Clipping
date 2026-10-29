"""CVM RAD scraper — Fatos Relevantes e Comunicados ao Mercado.

Consulta o portal RAD da CVM (https://www.rad.cvm.gov.br) pelos Fatos
Relevantes e Comunicados ao Mercado das empresas da cobertura publicados
dentro da janela `since`, baixa os PDFs e os injeta no pipeline como Articles
normais (screener → LLM → briefing).

Estratégia (só HTTP, sem browser):
- A tela de consulta (frmConsultaExternaCVM.aspx) carrega a tabela via AJAX de
  ``frmConsultaExternaCVM.aspx/ListarDocumentos`` (POST JSON, público). Uma
  única chamada com categoria "todas IPE" (``IPE_-1_-1_-1``) e período
  ``dataDe..dataAte`` traz os documentos de TODAS as companhias; filtramos
  localmente pelos códigos CVM da cobertura e pelas categorias de interesse.
- ``dados`` vem como texto: linhas separadas por ``&*``, campos por ``$&``:
      [0] código CVM "02008-7" (= 20087)  [1] companhia  [2] categoria
      [3] tipo  [4] espécie/assunto  [5] data referência  [6] data entrega
      [10] HTML de ações com OpenDownloadDocumentos('seq','versão','protocolo','IPE')
- PDF via ``frmDownloadDocumento.aspx?numSequencia=..&numProtocolo=..`` (HTTP
  direto; o servidor responde content-type text/html mas o corpo é o PDF).
- Extração de texto com pdfplumber (falha silenciosa se PDF for imagem).

A versão anterior automatizava o formulário ASP.NET via Playwright com
seletores que não casavam com a página ("não foi possível selecionar
categoria") — nunca coletou um documento.
"""

from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from html import unescape
from typing import Optional

import requests

from fetcher.rss_fetcher import Article

logger = logging.getLogger(__name__)

_BASE_URL = "https://www.rad.cvm.gov.br/ENET/frmConsultaExternaCVM.aspx"
_LIST_URL = f"{_BASE_URL}/ListarDocumentos"
_DOWNLOAD_URL = (
    "https://www.rad.cvm.gov.br/ENET/frmDownloadDocumento.aspx"
    "?Tela=ext&numSequencia={seq}&numVersao={ver}&numProtocolo={prot}"
    "&descTipo={tipo}&CodigoInstituicao=1"
)
_VIEW_URL = (
    "https://www.rad.cvm.gov.br/ENET/frmExibirArquivoIPEExterno.aspx"
    "?NumeroProtocoloEntrega={prot}"
)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0.0.0 Safari/537.36"
    ),
}

# Ticker → (cvm_code, display_name)
_CVM_COMPANIES: dict[str, tuple[int, str]] = {
    "EMBR3": (20087, "Embraer S.A."),
    "POMO4": (8451,  "Marcopolo S.A."),
    "RAPT4": (14109, "Randoncorp S.A."),
    "FRAS3": (6211,  "Frasle Mobility S.A."),
    "MYPK3": (11932, "Iochpe-Maxion S.A."),
    "WEGE3": (5410,  "WEG S.A."),
    "INTB3": (25453, "Intelbras S.A."),
    "GGPS3": (25712, "GPS Participações S.A."),
    "ARML3": (26069, "Armac Locação, Logística e Serviços S.A."),
    "MILS3": (22012, "Mills Locação, Serviços e Logística S.A."),
}

# Categorias a monitorar (exatamente como aparecem no portal)
_CATEGORIES = ["Fato Relevante", "Comunicado ao Mercado"]

# Máximo de caracteres extraídos do PDF (para não sobrecarregar o LLM)
_MAX_PDF_CHARS = 8_000

_ROW_SEP = "&*"
_FIELD_SEP = "$&"
_TAG_RE = re.compile(r"<[^>]+>")
_DOWNLOAD_ARGS_RE = re.compile(
    r"OpenDownloadDocumentos\(\s*'(\d+)'\s*,\s*'(\d+)'\s*,\s*'(\d+)'\s*,\s*'(\w+)'\s*\)"
)
_DATE_TIME_RE = re.compile(r"\d{2}/\d{2}/\d{4}(?:\s+\d{2}:\d{2})?")


# ---------------------------------------------------------------------------
# PDF extraction
# ---------------------------------------------------------------------------

def _extract_pdf_text(pdf_bytes: bytes, max_chars: int = _MAX_PDF_CHARS) -> str:
    """Extrai texto de um PDF usando pdfplumber. Retorna '' se falhar."""
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            pages_text = []
            for pg in pdf.pages:
                t = pg.extract_text()
                if t:
                    pages_text.append(t)
            text = "\n\n".join(pages_text)
        return text[:max_chars]
    except ImportError:
        logger.warning("pdfplumber não instalado — sem extração de texto do PDF. "
                       "Instale com: pip install pdfplumber")
        return ""
    except Exception as e:
        logger.warning(f"CVM: falha ao extrair texto do PDF: {e}")
        return ""


# ---------------------------------------------------------------------------
# Date parsing helpers
# ---------------------------------------------------------------------------

_BR_TZ = timezone(timedelta(hours=-3))

def _parse_date_entrega(date_str: str) -> Optional[datetime]:
    """Parseia 'DD/MM/YYYY HH:MM' ou 'DD/MM/YYYY' do portal CVM."""
    date_str = date_str.strip()
    for fmt in ("%d/%m/%Y %H:%M", "%d/%m/%Y"):
        try:
            dt = datetime.strptime(date_str, fmt)
            return dt.replace(tzinfo=_BR_TZ).astimezone(timezone.utc)
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# ListarDocumentos parsing
# ---------------------------------------------------------------------------

@dataclass
class _Document:
    cvm_code: int
    company: str
    categoria: str
    tipo: str
    assunto: str
    delivered_at: Optional[datetime]
    seq: str
    version: str
    protocol: str
    doc_type: str


def _clean(s: str) -> str:
    return " ".join(unescape(_TAG_RE.sub(" ", s)).split())


def _parse_cvm_code(raw: str) -> Optional[int]:
    """'02008-7' → 20087 (o dígito após o hífen faz parte do código CVM)."""
    digits = re.sub(r"\D", "", raw)
    return int(digits) if digits else None


def build_list_payload(date_de: str, date_ate: str) -> dict:
    """Corpo do POST ListarDocumentos: todas as categorias IPE, todas as empresas."""
    return {
        "dataDe": date_de,
        "dataAte": date_ate,
        "empresa": "",
        "setorAtividade": "-1",
        "categoriaEmissor": "-1",
        "situacaoEmissor": "-1",
        "tipoParticipante": "-1",
        "dataReferencia": "",
        "categoria": "IPE_-1_-1_-1",
        "periodo": "2",
        "horaIni": "",
        "horaFim": "",
        "palavraChave": "",
        "ultimaDtRef": "false",
        "tipoEmpresa": "0",
        "token": "",
        "versaoCaptcha": "",
    }


def parse_documents(dados: str) -> list[_Document]:
    """Converte o campo ``dados`` do ListarDocumentos em documentos."""
    docs: list[_Document] = []
    for row in dados.split(_ROW_SEP):
        f = row.split(_FIELD_SEP)
        if len(f) < 11:
            continue
        code = _parse_cvm_code(f[0])
        dl = _DOWNLOAD_ARGS_RE.search(f[10])
        if code is None or not dl:
            continue
        # f[6] = "<spanOrder>20261027</spanOrder> 27/10/2026 18:09"
        dates = _DATE_TIME_RE.findall(_clean(f[6]))
        assunto = _clean(f[4]).strip(" -")
        docs.append(_Document(
            cvm_code=code,
            company=_clean(f[1]),
            categoria=_clean(f[2]),
            tipo=_clean(f[3]).strip(" -"),
            assunto=assunto,
            delivered_at=_parse_date_entrega(dates[-1]) if dates else None,
            seq=dl.group(1),
            version=dl.group(2),
            protocol=dl.group(3),
            doc_type=dl.group(4),
        ))
    return docs


def select_documents(docs: list[_Document], since: datetime) -> list[tuple[str, str, _Document]]:
    """Filtra pela cobertura, categorias e janela. Retorna (ticker, nome, doc)."""
    by_code = {code: (ticker, name) for ticker, (code, name) in _CVM_COMPANIES.items()}
    out: list[tuple[str, str, _Document]] = []
    for doc in docs:
        if doc.cvm_code not in by_code or doc.categoria not in _CATEGORIES:
            continue
        if doc.delivered_at is not None and doc.delivered_at < since:
            continue
        ticker, name = by_code[doc.cvm_code]
        out.append((ticker, name, doc))
    return out


# ---------------------------------------------------------------------------
# Main scraper class
# ---------------------------------------------------------------------------

class CVMScraper:
    """Scraper de Fatos Relevantes e Comunicados ao Mercado do portal RAD/CVM."""

    name: str = "CVM Filings"
    base_url: str = _BASE_URL
    language: str = "pt-br"

    def fetch_articles(self, since: datetime) -> list[Article]:
        """
        Coleta documentos regulatórios da CVM para todas as empresas da cobertura.

        Args:
            since: Janela temporal — apenas documentos entregues após este datetime.

        Returns:
            Lista de Articles com texto do PDF extraído e URL do documento no RAD.
        """
        now = datetime.now(timezone.utc)
        date_de = since.astimezone(_BR_TZ).strftime("%d/%m/%Y")
        date_ate = now.astimezone(_BR_TZ).strftime("%d/%m/%Y")

        logger.info(
            f"CVM: buscando documentos de {date_de} a {date_ate} "
            f"para {len(_CVM_COMPANIES)} empresas × {len(_CATEGORIES)} categorias"
        )

        try:
            resp = requests.post(
                _LIST_URL,
                json=build_list_payload(date_de, date_ate),
                headers={**_HEADERS, "Content-Type": "application/json; charset=utf-8"},
                timeout=120,
            )
            resp.raise_for_status()
            payload = resp.json().get("d") or {}
        except Exception as e:
            logger.error(f"CVM: falha ao consultar ListarDocumentos: {e}")
            return []

        if payload.get("temErro"):
            logger.error(f"CVM: portal retornou erro: {payload.get('msgErro', '')[:200]}")
            return []

        all_docs = parse_documents(payload.get("dados") or "")
        selected = select_documents(all_docs, since)
        logger.info(
            f"CVM: {len(all_docs)} documentos no período, {len(selected)} da cobertura"
        )

        articles: list[Article] = []
        for ticker, company_name, doc in selected:
            try:
                articles.append(self._to_article(ticker, company_name, doc))
            except Exception as e:
                logger.warning(f"CVM {ticker}: falha no documento {doc.protocol}: {e}")

        logger.info(f"CVM: total de {len(articles)} documentos coletados")
        return articles

    def _to_article(self, ticker: str, company_name: str, doc: _Document) -> Article:
        tipo = doc.tipo or "Documento"
        if doc.assunto:
            title = f"[{doc.categoria}] {tipo}: {doc.assunto}"
        else:
            title = f"[{doc.categoria}] {tipo} — {company_name}"

        pdf_text = self._download_pdf_text(doc)
        published_at = doc.delivered_at or datetime.now(timezone.utc)

        return Article(
            source=f"CVM — {ticker}",
            title=title[:300],  # garante limite razoável
            url=_VIEW_URL.format(prot=doc.protocol),
            published_at=published_at.isoformat(),
            full_text=(
                f"Empresa: {company_name}\n"
                f"Categoria: {doc.categoria}\n"
                f"Tipo: {tipo}\n"
                f"Assunto: {doc.assunto}\n\n"
                f"{pdf_text}"
            ).strip(),
            language=self.language,
        )

    def _download_pdf_text(self, doc: _Document) -> str:
        url = _DOWNLOAD_URL.format(
            seq=doc.seq, ver=doc.version, prot=doc.protocol, tipo=doc.doc_type
        )
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=60)
            resp.raise_for_status()
        except Exception as e:
            logger.warning(f"CVM: falha ao baixar PDF {doc.protocol}: {e}")
            return ""
        if not resp.content.startswith(b"%PDF"):
            logger.debug(f"CVM: documento {doc.protocol} não é PDF")
            return ""
        return _extract_pdf_text(resp.content)
