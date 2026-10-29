"""Shared helpers for Playwright-based scrapers."""

from __future__ import annotations

import json
import logging
import os
import re
from contextlib import contextmanager
from datetime import datetime, time, timezone, timedelta
from pathlib import Path
from typing import Iterator, Optional

logger = logging.getLogger(__name__)

BR_TZ = timezone(timedelta(hours=-3))


_STEALTH_INIT_SCRIPT = """
    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
    Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
    window.chrome = {runtime: {}, loadTimes: () => {}, csi: () => {}, app: {}};
"""


@contextmanager
def browser_context(
    headless: bool = True,
    locale: str = "pt-BR",
    channel: str | None = None,
    stealth: bool = False,
) -> Iterator:
    """Yield a Playwright browser context. Caller opens pages under it.

    Args:
        headless: Run without visible window (default True).
        locale: Browser locale sent to sites.
        channel: Chrome channel to use (e.g. ``"chrome"`` for real Chrome).
            Required for sites with advanced bot detection (Bloomberg).
        stealth: Inject init script to hide headless/automation signals.
            Use together with ``channel="chrome"`` for maximum compatibility.
    """
    from playwright.sync_api import sync_playwright

    launch_args: list[str] = []
    if stealth:
        launch_args.append("--disable-blink-features=AutomationControlled")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless,
            channel=channel,
            args=launch_args if launch_args else None,
        )
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale=locale,
            viewport={"width": 1920, "height": 1080},
        )
        if stealth:
            ctx.add_init_script(_STEALTH_INIT_SCRIPT)
        try:
            yield ctx
        finally:
            ctx.close()
            browser.close()


def _find_chrome_user_data() -> str | None:
    """Localiza o diretório User Data do Chrome no Windows."""
    candidates = [
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "User Data"),
        os.path.join(os.environ.get("USERPROFILE", ""), "AppData", "Local", "Google", "Chrome", "User Data"),
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return None


PIPELINE_PROFILE = Path.home() / ".clipping" / "pipeline_profile"

_PERSISTENT_STEALTH_ARGS = ["--disable-blink-features=AutomationControlled"]
_PERSISTENT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def _launch_profile(p, user_data_dir: str, headless: bool, locale: str):
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=user_data_dir,
        headless=headless,
        channel="chrome",
        args=_PERSISTENT_STEALTH_ARGS,
        user_agent=_PERSISTENT_UA,
        locale=locale,
        viewport={"width": 1920, "height": 1080},
        timeout=30_000,
    )
    ctx.add_init_script(_STEALTH_INIT_SCRIPT)
    return ctx


def _launch_best_context(p, headless: bool, user_data_dir: str | None, locale: str):
    """Abre o melhor contexto disponível. Retorna (ctx, browser_ou_None)."""
    # --- Tentativa 1: perfil dedicado do pipeline (nunca está bloqueado) ---
    if PIPELINE_PROFILE.exists():
        try:
            ctx = _launch_profile(p, str(PIPELINE_PROFILE), headless, locale)
            logger.info("Playwright: usando perfil dedicado do pipeline (%s)", PIPELINE_PROFILE)
            return ctx, None
        except Exception as e:
            logger.warning("Playwright: perfil dedicado indisponível (%s).", e)

    # --- Tentativa 2: perfil real do Chrome (traz todos os cookies) ---
    # Falha se o Chrome estiver aberto (perfil bloqueado) e, no Chrome 136+,
    # sempre: automação no User Data padrão foi bloqueada pelo Google.
    if user_data_dir:
        try:
            ctx = _launch_profile(p, user_data_dir, headless, locale)
            logger.debug("Playwright: usando perfil real do Chrome (%s)", user_data_dir)
            return ctx, None
        except Exception as e:
            logger.warning(
                "Playwright: perfil Chrome indisponível (%s) — "
                "usando sessão stealth com cookies do Chrome.", str(e).splitlines()[0]
            )

    # --- Fallback: Chrome limpo + cookies extraídos do SQLite do Chrome ---
    # Extrai os cookies diretamente do banco SQLite do Chrome e injeta na
    # sessão limpa, para que Cloudflare/DataDome reconheçam a sessão existente.
    browser = p.chromium.launch(
        headless=headless,
        channel="chrome",
        args=_PERSISTENT_STEALTH_ARGS,
    )
    ctx = browser.new_context(
        user_agent=_PERSISTENT_UA,
        locale=locale,
        viewport={"width": 1920, "height": 1080},
    )
    ctx.add_init_script(_STEALTH_INIT_SCRIPT)

    try:
        from scrapers.chrome_cookies import get_playwright_cookies
        cookies = get_playwright_cookies([
            "reuters.com", ".reuters.com",
            "bloomberg.com", ".bloomberg.com",
        ])
        if cookies:
            ctx.add_cookies(cookies)
            logger.info(
                "Playwright: %d cookies do Chrome injetados na sessão stealth "
                "(Cloudflare + login Reuters/Bloomberg)", len(cookies)
            )
        else:
            logger.debug("Playwright: nenhum cookie extraído do Chrome — sessão anônima")
    except Exception as cookie_exc:
        logger.debug("Playwright: falha ao injetar cookies: %s", cookie_exc)

    logger.debug("Playwright: usando sessão stealth com cookies do Chrome")
    return ctx, browser


@contextmanager
def persistent_browser_context(
    headless: bool = True,
    user_data_dir: str | None = None,
    locale: str = "pt-BR",
) -> Iterator:
    """Contexto Playwright com sessão persistente (cookies) para sites protegidos.

    Hierarquia de tentativas (cada uma faz fallback para a próxima):

    1. Perfil dedicado do pipeline (~/.clipping/pipeline_profile/) — perfil
       separado que nunca está aberto durante o pipeline. Configurado via
       tools/setup_pipeline_browser.py (login manual único).

    2. Perfil real do Chrome do usuário — traz todos os cookies e sessões.
       Falha se Chrome estiver aberto (perfil bloqueado) ou no Chrome 136+.

    3. Sessão stealth + cookies extraídos do SQLite do Chrome — injetados no
       browser limpo via DPAPI. Sem cookies, vira sessão stealth anônima.

    O browser é aberto ANTES do ``yield``: uma exceção dentro do bloco ``with``
    do chamador é propagada normalmente, sem disparar a próxima tentativa.

    Args:
        headless: Roda sem janela visível (default True).
        user_data_dir: Caminho explícito do User Data do Chrome. Se None,
            detecta automaticamente no Windows.
        locale: Locale enviado aos sites.
    """
    from playwright.sync_api import sync_playwright

    if user_data_dir is None:
        user_data_dir = _find_chrome_user_data()

    with sync_playwright() as p:
        ctx, browser = _launch_best_context(p, headless, user_data_dir, locale)
        try:
            yield ctx
        finally:
            ctx.close()
            if browser is not None:
                browser.close()


_DATE_TEXT_RE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")


def extract_published_at(html: str) -> Optional[datetime]:
    """Extract publication datetime from article HTML.

    Cascade: JSON-LD datePublished -> meta article:published_time -> <time datetime>
    -> visible DD/MM/YYYY text (rounded up to start-of-day in BR_TZ).
    Always returns timezone-aware datetime in UTC (or None if nothing parseable).
    """
    dt = _from_jsonld(html) or _from_meta(html) or _from_time_tag(html)
    if dt is not None:
        return _to_utc(dt)

    dt = _from_visible_text(html)
    if dt is not None:
        return _to_utc(dt)

    return None


def _to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=BR_TZ)
    return dt.astimezone(timezone.utc)


def _parse_iso(s: str) -> Optional[datetime]:
    s = s.strip()
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _from_jsonld(html: str) -> Optional[datetime]:
    for match in re.finditer(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html, flags=re.DOTALL | re.IGNORECASE,
    ):
        raw = match.group(1).strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for node in _walk_jsonld(data):
            if not isinstance(node, dict):
                continue
            for key in ("datePublished", "dateCreated", "uploadDate"):
                if key in node:
                    dt = _parse_iso(str(node[key]))
                    if dt is not None:
                        return dt
    return None


def _walk_jsonld(node):
    if isinstance(node, list):
        for item in node:
            yield from _walk_jsonld(item)
    elif isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk_jsonld(v)


def _from_meta(html: str) -> Optional[datetime]:
    patterns = [
        r'<meta[^>]+property=["\']article:published_time["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']article:published_time["\']',
        r'<meta[^>]+name=["\']article:published_time["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+property=["\']og:article:published_time["\'][^>]+content=["\']([^"\']+)["\']',
    ]
    for pat in patterns:
        m = re.search(pat, html, flags=re.IGNORECASE)
        if m:
            dt = _parse_iso(m.group(1))
            if dt is not None:
                return dt
    return None


def _from_time_tag(html: str) -> Optional[datetime]:
    m = re.search(r'<time[^>]+datetime=["\']([^"\']+)["\']', html, flags=re.IGNORECASE)
    if m:
        return _parse_iso(m.group(1))
    return None


def _from_visible_text(html: str) -> Optional[datetime]:
    m = _DATE_TEXT_RE.search(html)
    if not m:
        return None
    day, month, year = (int(x) for x in m.groups())
    try:
        return datetime.combine(
            datetime(year, month, day).date(), time(0, 0), tzinfo=BR_TZ
        )
    except ValueError:
        return None


_REL_TIME_RE = re.compile(
    r"(?:há\s+)?(\d+)\s+(minuto|hora|dia)s?"
    r"|(?P<agora>agora|há\s+pouco)"
    r"|(?P<ontem>ontem)",
    flags=re.IGNORECASE,
)


def parse_relative_time_pt(text: str, now: datetime) -> Optional[datetime]:
    """Convert PT-BR relative time text to an approximate datetime.

    Handles patterns produced by O Globo's listing:
    "Agora", "Há N minutos", "Há N horas", "Há N dias", "Ontem".
    Returns None for unrecognised text.
    """
    text = text.strip()
    m = _REL_TIME_RE.search(text)
    if m is None:
        return None

    if m.group("agora"):
        return now
    if m.group("ontem"):
        yesterday = (now.astimezone(BR_TZ) - timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return yesterday.astimezone(timezone.utc)

    n = int(m.group(1))
    unit = m.group(2).lower()
    if unit.startswith("minuto"):
        return now - timedelta(minutes=n)
    if unit.startswith("hora"):
        return now - timedelta(hours=n)
    if unit.startswith("dia"):
        return now - timedelta(days=n)
    return None
