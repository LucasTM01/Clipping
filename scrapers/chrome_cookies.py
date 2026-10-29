"""
Extrai cookies do Chrome para injeção em sessões Playwright sem perfil.

Quando o Chrome está aberto e o perfil está bloqueado, o pipeline usa uma
sessão Playwright limpa (sem cookies). Este módulo lê os cookies diretamente
do banco SQLite do Chrome e os retorna no formato que o Playwright espera
para `context.add_cookies()`.

Fluxo de decriptação (Chrome v80+, Windows):
  1. Lê a chave AES-256 do arquivo `Local State` (base64 + prefixo "DPAPI")
  2. Decripta a chave com DPAPI via ctypes (CryptUnprotectData)
  3. Decripta cada cookie com AES-256-GCM (nonce de 12 bytes, tag de 16 bytes)

Leitura do banco: usa URI ?mode=ro&nolock=1 para ler o WAL database do Chrome
sem copiar o arquivo e sem conflito de locks.

Requer: cryptography (AES-GCM — normalmente já instalada como dependência
transitiva de qdrant-client). Não requer pywin32 — usa ctypes direto.
"""

from __future__ import annotations

import base64
import ctypes
import ctypes.wintypes
import json
import logging
import os
import sqlite3
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DPAPI via ctypes (sem dependência de pywin32)
# ---------------------------------------------------------------------------

class _DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", ctypes.wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_char)),
    ]


def _dpapi_decrypt(data: bytes) -> bytes:
    """Decripta dados com Windows DPAPI (CryptUnprotectData) via ctypes."""
    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = _DATA_BLOB(ctypes.sizeof(buf), buf)
    blob_out = _DATA_BLOB()

    ok = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(blob_in),
        None, None, None, None,
        0,                          # CRYPTPROTECT_CURRENT_USER (implicit)
        ctypes.byref(blob_out),
    )
    if not ok:
        raise ctypes.WinError()

    result = ctypes.string_at(blob_out.pbData, blob_out.cbData)
    ctypes.windll.kernel32.LocalFree(blob_out.pbData)
    return result


# ---------------------------------------------------------------------------
# Caminhos do Chrome
# ---------------------------------------------------------------------------

def _chrome_user_data() -> Optional[Path]:
    local = os.environ.get("LOCALAPPDATA", "")
    profile = os.environ.get("USERPROFILE", "")
    for base in [local, os.path.join(profile, "AppData", "Local")]:
        p = Path(base) / "Google" / "Chrome" / "User Data"
        if p.exists():
            return p
    return None


def _cookies_db(user_data: Path) -> Optional[Path]:
    for rel in ("Default/Network/Cookies", "Default/Cookies"):
        p = user_data / rel.replace("/", os.sep)
        if p.exists():
            return p
    return None


# ---------------------------------------------------------------------------
# Chave AES
# ---------------------------------------------------------------------------

def _load_aes_key(user_data: Path) -> Optional[bytes]:
    """Lê e decripta a chave AES-256 do Chrome via DPAPI (ctypes)."""
    local_state = user_data / "Local State"
    if not local_state.exists():
        return None
    try:
        with open(local_state, encoding="utf-8") as f:
            state = json.load(f)
        encoded = state.get("os_crypt", {}).get("encrypted_key", "")
        if not encoded:
            return None
        # base64 → remove prefixo b"DPAPI" (5 bytes) → decriptar
        encrypted = base64.b64decode(encoded)[5:]
        return _dpapi_decrypt(encrypted)
    except Exception as exc:
        log.debug(
            "chrome_cookies: erro ao carregar chave AES: %s "
            "(se rodar como Administrador, DPAPI não tem acesso às chaves do usuário — "
            "rode o pipeline sem elevação ou use um perfil dedicado via tools/setup_pipeline_browser.py)",
            exc,
        )
        return None


# ---------------------------------------------------------------------------
# Decriptação de cookie
# ---------------------------------------------------------------------------

def _decrypt_value(encrypted_value: bytes, key: bytes) -> str:
    """Decripta valor de cookie (AES-256-GCM v10/v11 ou DPAPI legado)."""
    if not encrypted_value:
        return ""
    try:
        if encrypted_value[:3] in (b"v10", b"v11"):
            # Chrome v80+: prefixo(3) + nonce(12) + ciphertext+tag
            nonce = encrypted_value[3:15]
            ciphertext = encrypted_value[15:]
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            return AESGCM(key).decrypt(nonce, ciphertext, None).decode("utf-8", errors="replace")
        # Formato legado: DPAPI por valor individual
        return _dpapi_decrypt(encrypted_value).decode("utf-8", errors="replace")
    except Exception as exc:
        log.debug("chrome_cookies: falha ao decriptar cookie: %s", exc)
        return ""


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------

_SAMESITE_MAP = {-1: "None", 0: "None", 1: "Lax", 2: "Strict"}
_FILETIME_EPOCH_US = 11_644_473_600_000_000   # diferença µs entre 1601 e 1970


def _filetime_to_unix(ft: int) -> float:
    if ft <= 0:
        return -1
    return (ft - _FILETIME_EPOCH_US) / 1_000_000


def get_playwright_cookies(domains: list[str]) -> list[dict]:
    """
    Retorna cookies do Chrome para os domínios indicados no formato Playwright.

    Lê o banco SQLite diretamente (mode=ro&nolock=1) — funciona com Chrome aberto.

    Args:
        domains: Ex: ["reuters.com", ".reuters.com"].
                 Usa LIKE '%domain%' para cobrir subdomínios.

    Returns:
        Lista de dicts para `context.add_cookies()`. Vazia se Chrome não
        encontrado, usuário não logado, ou erro de decriptação.
    """
    user_data = _chrome_user_data()
    if not user_data:
        log.debug("chrome_cookies: diretório User Data não encontrado")
        return []

    aes_key = _load_aes_key(user_data)
    if aes_key is None:
        log.debug("chrome_cookies: chave AES indisponível")
        return []

    db_path = _cookies_db(user_data)
    if not db_path:
        log.debug("chrome_cookies: arquivo Cookies não encontrado")
        return []

    # Conecta em modo read-only sem file lock — safe com Chrome aberto
    # WAL databases suportam leitores concorrentes com nolock=1
    uri = "file:///" + db_path.as_posix().lstrip("/") + "?mode=ro&nolock=1"
    cookies: list[dict] = []
    try:
        conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
        placeholders = " OR ".join("host_key LIKE ?" for _ in domains)
        params = [f"%{d}%" for d in domains]
        rows = conn.execute(
            f"SELECT name, encrypted_value, host_key, path, "
            f"       expires_utc, is_httponly, is_secure, samesite "
            f"FROM cookies WHERE {placeholders}",
            params,
        ).fetchall()
        conn.close()
    except Exception as exc:
        log.debug("chrome_cookies: erro ao ler banco: %s", exc)
        return []

    for name, enc_val, host_key, path, expires_utc, httponly, secure, samesite in rows:
        value = _decrypt_value(enc_val, aes_key)
        if not value:
            continue
        cookie: dict = {
            "name":     name,
            "value":    value,
            "domain":   host_key,
            "path":     path or "/",
            "httpOnly": bool(httponly),
            "secure":   bool(secure),
            "sameSite": _SAMESITE_MAP.get(samesite, "None"),
        }
        ts = _filetime_to_unix(expires_utc)
        if ts > 0:
            cookie["expires"] = ts
        cookies.append(cookie)

    log.debug(
        "chrome_cookies: %d cookies extraídos para domínios %s",
        len(cookies), domains,
    )
    return cookies
