"""Offline tests for the generic WordPress REST API scraper."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

from scrapers.wordpress import WordPressScraper, html_to_text, parse_posts

_NOW = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)
_SINCE_24H = _NOW - timedelta(hours=24)


def _post(n: int, date_gmt: str, title: str = "") -> dict:
    return {
        "date_gmt": date_gmt,
        "link": f"https://www.autodata.com.br/noticias/2026/10/29/post-{n}/{n}/",
        "title": {"rendered": title or f"Post {n} &#8211; Stellantis"},
        "excerpt": {"rendered": "<p>Resumo</p>"},
        "content": {"rendered": f"<p>Primeiro par&aacute;grafo {n}.</p><p>Segundo&nbsp;par.</p><script>x()</script>"},
    }


def _resp(payload, status: int = 200) -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.json.return_value = payload
    r.raise_for_status = MagicMock()
    return r


def test_html_to_text() -> None:
    text = html_to_text("<p>Ol&aacute;</p><p>mundo&nbsp;<b>novo</b></p><script>bad()</script>")
    assert text == "Olá\n\nmundo novo"


def test_parse_posts_fields() -> None:
    [(url, title, pub, body)] = parse_posts([_post(1, "2026-10-29T10:00:00")])
    assert url.endswith("/post-1/1/")
    assert title == "Post 1 – Stellantis"
    assert pub == datetime(2026, 10, 29, 10, 0, tzinfo=timezone.utc)
    assert body == "Primeiro parágrafo 1.\n\nSegundo par."


def test_parse_posts_skips_incomplete() -> None:
    assert parse_posts([{"link": "x"}, "garbage", _post(2, "not-a-date")]) == []


def test_parse_posts_falls_back_to_excerpt() -> None:
    p = _post(3, "2026-10-29T10:00:00")
    p["content"] = {"rendered": ""}
    assert parse_posts([p])[0][3] == "Resumo"


def test_fetch_stops_at_since() -> None:
    page1 = [_post(1, "2026-10-29T11:00:00"), _post(2, "2026-10-28T13:00:00"), _post(3, "2026-10-27T09:00:00")]
    scraper = WordPressScraper("Autodata", "https://www.autodata.com.br", max_pages=5)
    with patch("scrapers.wordpress.requests.get", return_value=_resp(page1)) as mock_get:
        articles = scraper.fetch_articles(_SINCE_24H)

    assert [a.url.split("/")[-2] for a in articles] == ["1", "2"]
    assert mock_get.call_count == 1
    assert mock_get.call_args.args[0] == "https://www.autodata.com.br/wp-json/wp/v2/posts"
    assert all(a.source == "Autodata" and a.language == "pt-br" for a in articles)


def test_fetch_paginates_full_pages() -> None:
    full = [_post(i, "2026-10-29T11:00:00") for i in range(50)]
    last = [_post(100, "2026-10-29T10:00:00")]
    scraper = WordPressScraper("Diário do Transporte", "https://diariodotransporte.com.br", max_pages=5)
    with patch("scrapers.wordpress.requests.get", side_effect=[_resp(full), _resp(last)]) as mock_get:
        articles = scraper.fetch_articles(_SINCE_24H)

    assert len(articles) == 51
    assert mock_get.call_count == 2
    assert mock_get.call_args.kwargs["params"]["page"] == 2


def test_fetch_http_error_returns_empty() -> None:
    bad = MagicMock()
    bad.status_code = 503
    bad.raise_for_status.side_effect = Exception("503")
    with patch("scrapers.wordpress.requests.get", return_value=bad):
        assert WordPressScraper("Canal Solar", "https://canalsolar.com.br").fetch_articles(_SINCE_24H) == []


def test_api_base_override() -> None:
    s = WordPressScraper("X", "https://www.site.com/noticias", api_base="https://painel.site.com")
    assert s.api_url == "https://painel.site.com/wp-json/wp/v2/posts"


def test_scraper_satisfies_base_protocol() -> None:
    from scrapers.base import BaseScraper
    assert isinstance(WordPressScraper("X", "https://example.com"), BaseScraper)
