"""Publish the proprietary news table to the DataHandler data lake on Drive.

The Clipping pipeline owns `output/news_database.db`. DataHandler owns the
analytical layer (dashboard, charts, matrices, models) and reads a Parquet lake
that Google Drive syncs to every machine. This module is the one-way bridge:
after each run, the whole `articles` table is written into the lake as

    <DATA_DIR>/news/clipping/articles.parquet   (source of truth)
    <DATA_DIR>/news/clipping/articles.csv       (Excel-facing twin)

**Why the full table and not just the new rows.** `articles` is not append-only
in practice: `capital_goods` is filled in later by the Streamlit feedback loop,
so a row inserted last week can change today. An incremental sync keyed on
`created_at` would carry the headline and permanently lose the human label —
which is the single most valuable column for the classifier this feeds. A full
export costs about 0.1 s and 4 MB for 40 000 rows, so there is nothing to buy by
being clever, and a missed day heals itself on the next run.

**Why it lives inside the lake.** DataHandler discovers datasets by walking
`<DATA_DIR>/<sector>/<source>/*.parquet` with no whitelist, so writing here makes
the table appear in every existing tab — the data explorer, the chart builder,
the matrix, the statistical workbench and the forecaster — with no code on that
side. A folder outside `DATA_DIR` would be invisible to all of it.

**Two routes to the same files.** When the Drive for Desktop mount (``G:``) is
reachable, the files are written straight into it. It is *not* reachable from
the scheduled run: the task runs "whether the user is logged on or not" (S4U),
and Drive for Desktop only mounts ``G:`` inside the interactive session. In
that case the files are staged under ``output/datahandler_export/`` and pushed
with rclone through the Drive API, onto the same path in My Drive. rclone
updates an existing Drive file in place (same file ID), so every other machine
keeps reading the same ``G:\\...\\articles.parquet``.

Nothing here may break the briefing: the pipeline's job is the document, and a
Drive that is offline, still syncing or mounted on another letter is a normal
condition. Every failure path logs and returns ``None``.

Run standalone for a one-off backfill:

    python -m data.drive_sync
"""

from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

#: Where the table lands inside the lake: <DATA_DIR>/<sector>/<source>/<dataset>
SECTOR = "news"
SOURCE = "clipping"
DATASET = "articles"

#: Default mount of the DataHandler lake on this team's machines. Override with
#: DATAHANDLER_DATA_DIR in .env when Drive is mounted on another letter.
_DEFAULT_DATA_DIR = r"G:\My Drive\4. Trabalho\Safra_CG_db\data"

#: The same lake root as seen by rclone (remote "gdrive" = My Drive). Override
#: with DATAHANDLER_REMOTE; set it to an empty string to disable the API route.
_DEFAULT_REMOTE = "gdrive:4. Trabalho/Safra_CG_db/data"

#: Local staging root used when the mount is not reachable.
_STAGING_DIR = Path(__file__).resolve().parent.parent / "output" / "datahandler_export"

#: Where winget puts the rclone shim. The scheduled task does not necessarily
#: inherit the interactive PATH, so this is checked explicitly.
_WINGET_RCLONE = Path.home() / "AppData" / "Local" / "Microsoft" / "WinGet" / "Links" / "rclone.exe"

#: rclone's default config location on Windows, passed explicitly for the same
#: reason. RCLONE_CONFIG in the environment still wins.
_RCLONE_CONFIG = Path.home() / "AppData" / "Roaming" / "rclone" / "rclone.conf"

_RCLONE_TIMEOUT_S = 600

#: Columns parsed as instants. Stored UTC-normalised and tz-naive, which is what
#: the rest of the lake holds and what DuckDB reads back without surprises.
_TIMESTAMP_COLS = ("published_at", "created_at")


def _lake_dir(explicit: str | Path | None = None) -> Path | None:
    """Resolve the lake root, or ``None`` when it is not reachable right now."""
    if explicit:
        candidate = Path(explicit)
    else:
        configured = os.environ.get("DATAHANDLER_DATA_DIR", "").strip().strip('"')
        if not configured:
            try:
                from config.settings import settings

                configured = (getattr(settings, "datahandler_data_dir", "") or "").strip()
            except Exception:  # noqa: BLE001 — settings must never break the sync
                configured = ""
        candidate = Path(configured or _DEFAULT_DATA_DIR)

    try:
        if not candidate.is_dir():
            log.info("drive_sync: lake não encontrado em %s — sync ignorado", candidate)
            return None
    except OSError as exc:  # a Drive letter that exists but is not responding
        log.warning("drive_sync: lake inacessível (%s) — sync ignorado", exc)
        return None
    return candidate


def _remote_root(explicit: str | None = None) -> str:
    """The lake root as an rclone remote path, or "" when the API route is off."""
    if explicit is not None:
        return explicit.strip()
    configured = os.environ.get("DATAHANDLER_REMOTE")
    if configured is None:
        try:
            from config.settings import settings

            configured = getattr(settings, "datahandler_remote", None)
        except Exception:  # noqa: BLE001 — settings must never break the sync
            configured = None
    if configured is None:
        return _DEFAULT_REMOTE
    return configured.strip().strip('"')


def _rclone_exe() -> str | None:
    configured = os.environ.get("RCLONE_EXE", "").strip().strip('"')
    if configured:
        return configured if Path(configured).is_file() else None
    found = shutil.which("rclone")
    if found:
        return found
    return str(_WINGET_RCLONE) if _WINGET_RCLONE.is_file() else None


def _push_with_rclone(files: list[Path], remote_dir: str) -> bool:
    """Upload each file onto ``remote_dir/<name>``, replacing it in place."""
    exe = _rclone_exe()
    if exe is None:
        log.warning("drive_sync: rclone não encontrado (defina RCLONE_EXE) — sync via API ignorado")
        return False

    for local in files:
        target = f"{remote_dir.rstrip('/')}/{local.name}"
        command = [exe, "copyto", str(local), target, "--retries", "3", "--stats-log-level", "DEBUG"]
        if not os.environ.get("RCLONE_CONFIG") and _RCLONE_CONFIG.is_file():
            command += ["--config", str(_RCLONE_CONFIG)]
        try:
            result = subprocess.run(
                command, capture_output=True, text=True, timeout=_RCLONE_TIMEOUT_S,
                encoding="utf-8", errors="replace",
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            log.warning("drive_sync: rclone falhou em %s (%s)", local.name, exc)
            return False
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip().splitlines()[-3:]
            log.warning(
                "drive_sync: rclone terminou com código %s em %s: %s",
                result.returncode, local.name, " | ".join(detail),
            )
            return False
    return True


def _db_path(explicit: str | Path | None = None) -> Path:
    if explicit:
        return Path(explicit)
    from config.settings import settings

    return Path(settings.news_db_path)


def _read_articles(db_path: Path):
    """The whole `articles` table, typed for analysis rather than for storage."""
    import pandas as pd

    with sqlite3.connect(db_path) as conn:
        df = pd.read_sql_query("SELECT * FROM articles", conn)

    if df.empty:
        return df

    # `date` is a plain calendar day (YYYY-MM-DD). Making it a real date rather
    # than text is what lets the chart engine pick it up as the time axis
    # without guessing.
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], format="%Y-%m-%d", errors="coerce")

    for column in _TIMESTAMP_COLS:
        if column in df.columns:
            parsed = pd.to_datetime(df[column], errors="coerce", utc=True, format="ISO8601")
            df[column] = parsed.dt.tz_localize(None)

    # SQLite NULLs make pandas read this as float. A nullable integer keeps the
    # label honest: 0 and 1 are answers, missing is missing — not 0.0 and NaN.
    if "capital_goods" in df.columns:
        df["capital_goods"] = df["capital_goods"].astype("Int64")

    if "relevance_score" in df.columns:
        df["relevance_score"] = df["relevance_score"].astype("float64")

    # Every dataset in the lake carries when it was written; keeping the
    # convention means this table behaves like the others downstream.
    df["scraped_at"] = datetime.now(timezone.utc).replace(tzinfo=None)
    return df


def _atomic_write(df, path: Path, kind: str) -> None:
    """Temp file + replace, so a reader on another machine never sees a partial file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    if kind == "parquet":
        df.to_parquet(tmp, index=False, engine="pyarrow")
    else:
        # utf-8-sig so Excel opens accented headlines (ç, õ, ã) correctly.
        df.to_csv(tmp, index=False, encoding="utf-8-sig")
    os.replace(tmp, path)


def sync_to_drive(
    db_path: str | Path | None = None,
    data_dir: str | Path | None = None,
    *,
    write_csv: bool = True,
    remote: str | None = None,
) -> dict[str, Any] | None:
    """Export `articles` into the DataHandler lake. Returns a summary, or ``None``.

    Writes through the ``G:`` mount when it is reachable, otherwise uploads via
    rclone to ``remote`` (default ``DATAHANDLER_REMOTE``). ``None`` means "not
    done, and that is fine" — no route to the lake, no database yet, nothing to
    export. Anything that would be a real bug still raises.
    """
    lake = _lake_dir(data_dir)
    remote_root = "" if lake is not None else _remote_root(remote)
    if lake is None and not remote_root:
        return None

    source_db = _db_path(db_path)
    if not source_db.exists():
        log.info("drive_sync: base de notícias ausente em %s — sync ignorado", source_db)
        return None

    try:
        import pandas  # noqa: F401
        import pyarrow  # noqa: F401
    except ImportError as exc:
        log.warning("drive_sync: %s — instale com `pip install pandas pyarrow`", exc)
        return None

    try:
        df = _read_articles(source_db)
    except (sqlite3.Error, OSError) as exc:
        log.warning("drive_sync: não foi possível ler %s (%s)", source_db, exc)
        return None

    if df.empty:
        log.info("drive_sync: nenhuma notícia na base — nada a exportar")
        return None

    # Via the mount the files land in the lake directly; via the API they are
    # staged locally first and uploaded after.
    target_dir = (lake if lake is not None else _STAGING_DIR) / SECTOR / SOURCE
    parquet_path = target_dir / f"{DATASET}.parquet"
    csv_path = target_dir / f"{DATASET}.csv"

    try:
        _atomic_write(df, parquet_path, "parquet")
        if write_csv:
            _atomic_write(df, csv_path, "csv")
    except OSError as exc:
        # Drive mid-sync, file locked by Excel, disk full: all recoverable next run.
        log.warning("drive_sync: falha ao escrever em %s (%s)", target_dir, exc)
        return None

    destination = str(parquet_path)
    if lake is None:
        log.info("drive_sync: lake não montado — enviando via rclone para %s", remote_root)
        remote_dir = f"{remote_root.rstrip('/')}/{SECTOR}/{SOURCE}"
        uploads = [parquet_path] + ([csv_path] if write_csv else [])
        if not _push_with_rclone(uploads, remote_dir):
            return None
        destination = f"{remote_dir}/{parquet_path.name}"

    summary = {
        "rows": int(len(df)),
        "path": destination,
        "via": "mount" if lake is not None else "rclone",
        "size_mb": round(parquet_path.stat().st_size / 1024 / 1024, 2),
        "date_min": str(df["date"].min().date()) if df["date"].notna().any() else "",
        "date_max": str(df["date"].max().date()) if df["date"].notna().any() else "",
        "labelled": int(df["capital_goods"].notna().sum()) if "capital_goods" in df else 0,
    }
    log.info(
        "drive_sync: %s linhas -> %s (%s MB, %s a %s, %s rotuladas)",
        f"{summary['rows']:,}".replace(",", "."), destination, summary["size_mb"],
        summary["date_min"], summary["date_max"], summary["labelled"],
    )
    return summary


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    result = sync_to_drive()
    if result:
        rows = f"{result['rows']:,}".replace(",", ".")
        print(
            f"news/clipping/articles: {rows} linhas, {result['size_mb']} MB "
            f"({result['date_min']} a {result['date_max']}, "
            f"{result['labelled']} rotuladas)"
        )
    else:
        print("Nada sincronizado — veja o log acima.")
