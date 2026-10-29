"""Offline tests for the CVM RAD scraper (ListarDocumentos parsing)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from scrapers.cvm import (
    CVMScraper,
    _parse_cvm_code,
    build_list_payload,
    parse_documents,
    select_documents,
)

_ACTIONS = (
    "<i class='fi-page-search' onclick=OpenPopUpVer('frmExibirArquivoIPEExterno.aspx?"
    "NumeroProtocoloEntrega={prot}')></i><i class='fi-download' "
    "onclick=OpenDownloadDocumentos('{seq}','1','{prot}','IPE')></i>"
)


def _row(code: str, company: str, categoria: str, tipo: str, assunto: str, entrega: str, seq: str, prot: str) -> str:
    ymd = entrega[6:10] + entrega[3:5] + entrega[0:2]
    return "$&".join([
        code, company, categoria, tipo,
        f"<spanOrder>{assunto}</spanOrder> - ",
        f"<spanOrder>{ymd}</spanOrder> {entrega[:10]}",
        f"<spanOrder>{ymd}</spanOrder> {entrega}",
        "Ativo", "1", "AP",
        _ACTIONS.format(seq=seq, prot=prot),
        assunto, "",
    ])


_DADOS = "&*".join([
    _row("01410-9", "RANDONCORP S.A.", "Fato Relevante", " - ", "Edital da OPA", "23/10/2026 21:01", "111", "1001"),
    _row("02008-7", "EMBRAER S.A.", "Reunião da Administração", "Conselho", "Ata", "28/10/2026 19:05", "222", "1002"),
    _row("05410-0", "NORFOLK SOUTHERN CORP", "Comunicado ao Mercado", "Outros", "8-K", "28/10/2026 08:00", "333", "1003"),
    _row("00541-0", "WEG S.A.", "Comunicado ao Mercado", "Outros Comunicados", "Aquisição", "28/10/2026 12:41", "444", "1004"),
    _row("00541-0", "WEG S.A.", "Fato Relevante", " - ", "Antigo", "01/10/2026 09:00", "555", "1005"),
    "linha-quebrada$&sem campos",
])

_SINCE = datetime(2026, 10, 22, tzinfo=timezone.utc)


def test_parse_cvm_code_includes_check_digit() -> None:
    assert _parse_cvm_code("02008-7") == 20087
    assert _parse_cvm_code("00541-0") == 5410
    assert _parse_cvm_code("") is None


def test_parse_documents_fields() -> None:
    docs = parse_documents(_DADOS)
    assert len(docs) == 5
    randon = docs[0]
    assert randon.cvm_code == 14109
    assert randon.categoria == "Fato Relevante"
    assert randon.tipo == ""
    assert randon.assunto == "Edital da OPA"
    assert randon.delivered_at == datetime(2026, 10, 24, 0, 1, tzinfo=timezone.utc)
    assert (randon.seq, randon.version, randon.protocol, randon.doc_type) == ("111", "1", "1001", "IPE")


def test_select_documents_filters_coverage_category_and_window() -> None:
    selected = select_documents(parse_documents(_DADOS), _SINCE)
    assert [(t, d.protocol) for t, _, d in selected] == [("RAPT4", "1001"), ("WEGE3", "1004")]


def test_build_list_payload_queries_all_ipe_categories() -> None:
    p = build_list_payload("22/10/2026", "29/10/2026")
    assert p["categoria"] == "IPE_-1_-1_-1"
    assert (p["dataDe"], p["dataAte"], p["empresa"]) == ("22/10/2026", "29/10/2026", "")


def _resp(json_payload=None, content: bytes = b"") -> MagicMock:
    r = MagicMock()
    r.json.return_value = json_payload
    r.content = content
    r.raise_for_status = MagicMock()
    return r


def test_fetch_articles_builds_articles() -> None:
    listing = _resp({"d": {"temErro": False, "msgErro": "", "dados": _DADOS}})
    with patch("scrapers.cvm.requests.post", return_value=listing), \
         patch("scrapers.cvm.requests.get", return_value=_resp(content=b"%PDF-1.7 fake")), \
         patch("scrapers.cvm._extract_pdf_text", return_value="Texto do PDF"):
        articles = CVMScraper().fetch_articles(_SINCE)

    assert [a.source for a in articles] == ["CVM — RAPT4", "CVM — WEGE3"]
    randon = articles[0]
    assert randon.title == "[Fato Relevante] Documento: Edital da OPA"
    assert randon.url.endswith("NumeroProtocoloEntrega=1001")
    assert "Empresa: Randoncorp S.A." in randon.full_text
    assert randon.full_text.endswith("Texto do PDF")


def test_fetch_articles_portal_error_returns_empty() -> None:
    listing = _resp({"d": {"temErro": True, "msgErro": "Sessão expirada", "dados": ""}})
    with patch("scrapers.cvm.requests.post", return_value=listing):
        assert CVMScraper().fetch_articles(_SINCE) == []


def test_fetch_articles_skips_non_pdf_body() -> None:
    listing = _resp({"d": {"temErro": False, "dados": _DADOS}})
    with patch("scrapers.cvm.requests.post", return_value=listing), \
         patch("scrapers.cvm.requests.get", return_value=_resp(content=b"<html>erro</html>")):
        articles = CVMScraper().fetch_articles(_SINCE)
    assert len(articles) == 2
    assert all(a.full_text.startswith("Empresa:") for a in articles)


def test_scraper_satisfies_base_protocol() -> None:
    from scrapers.base import BaseScraper
    assert isinstance(CVMScraper(), BaseScraper)
