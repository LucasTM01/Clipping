"""DB4 (relevant news) — grouping, take matching and the parquet/xlsx pair."""

import sys
from pathlib import Path

import openpyxl

_SCRIPTS = Path(__file__).resolve().parents[1] / ".claude" / "skills" / "whatsapp-clipping-ingest" / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import relevant_news as rn  # noqa: E402


def _item(date="2026-09-28", headline="Embraer delivers jets", url="https://a.com/1", **kw):
    return {"date": date, "section": "Aerospace & Defense", "headline": headline,
            "url": url, "sender": "Luiza Mussi", "source_msg_type": "clipping_new", **kw}


def test_one_row_per_date_headline_extra_urls_kept():
    rows = rn.build_rows([_item(), _item(url="https://b.com/2"), _item(url="https://a.com/1")],
                         set(), "now")
    assert len(rows) == 1
    assert rows[0]["link"] == "https://a.com/1"
    assert rows[0]["other_links"] == "https://b.com/2"


def test_skips_existing_ids_internal_pdfs_and_missing_urls():
    existing = {rn.news_id("2026-09-28", "Embraer delivers jets")}
    rows = rn.build_rows([
        _item(),
        _item(headline="Safra note", url="https://apiext-bi.safra.com.br/x.pdf"),
        _item(headline="No link", url=""),
        _item(headline="WEG results", url="https://c.com/3"),
    ], existing, "now")
    assert [r["headline"] for r in rows] == ["WEG results"]


def test_take_flags_headline_then_same_day_link_only():
    rows = rn.build_rows([
        _item(headline="Embraer delivers jets", url="https://a.com/1"),
        _item(headline="Airbus delivered 25 aircraft to 17 customers", url="https://b.com/2"),
        _item(headline="Randon volumes", url="https://c.com/3"),
        _item(headline="No take here", url="https://d.com/4"),
    ], set(), "now")
    takes = [
        {"date": "2026-09-28", "headline": "Embraer delivers jets", "link": "https://a.com/1",
         "marker": "our_take", "company": "Embraer"},
        {"date": "2026-09-28", "headline": "Embraer delivers jets", "link": "https://a.com/1",
         "marker": "our_take", "company": "WEG"},
        # reworded headline, same link, same day -> link match
        {"date": "2026-09-28", "headline": "Airbus delivered 25 aircraft", "link": "https://b.com/2",
         "marker": "weekly_reminder", "company": ""},
        # same link on another day is a different item -> ignored
        {"date": "2026-09-20", "headline": "Research note", "link": "https://c.com/3",
         "marker": "our_take", "company": "Randon"},
    ]
    stats = rn.apply_take_flags(rows, takes)
    by = {r["headline"]: r for r in rows}
    assert by["Embraer delivers jets"]["take_match"] == "headline"
    assert by["Embraer delivers jets"]["take_companies"] == "Embraer; WEG"
    assert by["Airbus delivered 25 aircraft to 17 customers"]["take_match"] == "link"
    assert by["Airbus delivered 25 aircraft to 17 customers"]["take_markers"] == "weekly_reminder"
    assert not by["Randon volumes"]["has_take"]
    assert stats == {"headline": 1, "link": 1, "none": 2}
    assert rn.unmatched_takes(rows, takes) == {"our_take": 1}


def test_flags_take_datetime_cells():
    rows = rn.build_rows([_item()], set(), "now")
    from datetime import datetime
    rn.apply_take_flags(rows, [{"date": datetime(2026, 9, 28), "headline": "Embraer delivers jets",
                                "link": "", "marker": "our_take", "company": "Embraer"}])
    assert rows[0]["has_take"]


def test_write_roundtrip_parquet_and_xlsx(tmp_path):
    pq, xl = tmp_path / "r.parquet", tmp_path / "r.xlsx"
    rows = rn.build_rows([_item(), _item(date="2026-09-27", headline="WEG results",
                                         url="https://c.com/3")], set(), "now")
    rows[0]["has_take"] = True
    rn.write_rows(rows, pq, xl)

    assert rn.check_written(2, pq, xl) == []
    back = rn.read_rows(pq)
    assert [r["date"] for r in back] == ["2026-09-27", "2026-09-28"]  # sorted, ISO strings
    assert sum(r["has_take"] for r in back) == 1
    wb = openpyxl.load_workbook(xl)
    assert wb.sheetnames == [rn.SHEET]
    assert wb[rn.SHEET]["B2"].value == "2026-09-27"  # string, joins with DB3 dates
    assert not list(tmp_path.glob("*.tmp"))

    # appending keeps existing rows and adds only new ids
    new = rn.build_rows([_item(), _item(headline="Tupy order", url="https://e.com/5")],
                        {r["news_id"] for r in back}, "later")
    assert [r["headline"] for r in new] == ["Tupy order"]
    rn.write_rows(back + new, pq, xl)
    assert rn.check_written(3, pq, xl) == []
