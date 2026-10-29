"""Tests for data/drive_sync.py — both routes to the lake, no network."""

import sqlite3
import subprocess
from pathlib import Path

import pytest

pytest.importorskip("pandas")
pytest.importorskip("pyarrow")

from data import drive_sync


@pytest.fixture
def news_db(tmp_path: Path) -> Path:
    db = tmp_path / "news.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE articles (id INTEGER, date TEXT, headline TEXT, "
            "published_at TEXT, created_at TEXT, capital_goods INTEGER, relevance_score REAL)"
        )
        conn.execute(
            "INSERT INTO articles VALUES (1, '2026-09-28', 'Embraer entrega jatos', "
            "'2026-09-28T10:00:00Z', '2026-09-28T11:00:00Z', 1, 0.8)"
        )
    return db


@pytest.fixture
def staging(tmp_path: Path, monkeypatch) -> Path:
    target = tmp_path / "staging"
    monkeypatch.setattr(drive_sync, "_STAGING_DIR", target)
    return target


def test_mount_route_writes_into_lake(news_db, tmp_path, staging, monkeypatch):
    lake = tmp_path / "lake"
    lake.mkdir()
    monkeypatch.setattr(drive_sync, "_push_with_rclone", lambda *a: pytest.fail("rclone must not run"))

    result = drive_sync.sync_to_drive(db_path=news_db, data_dir=lake)

    assert result["via"] == "mount"
    assert (lake / "news" / "clipping" / "articles.parquet").is_file()
    assert (lake / "news" / "clipping" / "articles.csv").is_file()
    assert not staging.exists()


def test_rclone_route_when_lake_missing(news_db, tmp_path, staging, monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(drive_sync, "_rclone_exe", lambda: "rclone")
    monkeypatch.setattr(drive_sync.subprocess, "run", fake_run)

    result = drive_sync.sync_to_drive(
        db_path=news_db, data_dir=tmp_path / "missing", remote="gdrive:lake"
    )

    assert result["via"] == "rclone"
    assert result["path"] == "gdrive:lake/news/clipping/articles.parquet"
    assert (staging / "news" / "clipping" / "articles.parquet").is_file()
    targets = [c[3] for c in calls]
    assert targets == [
        "gdrive:lake/news/clipping/articles.parquet",
        "gdrive:lake/news/clipping/articles.csv",
    ]
    assert all(c[1] == "copyto" for c in calls)


def test_rclone_failure_returns_none(news_db, tmp_path, staging, monkeypatch):
    monkeypatch.setattr(drive_sync, "_rclone_exe", lambda: "rclone")
    monkeypatch.setattr(
        drive_sync.subprocess, "run",
        lambda command, **kw: subprocess.CompletedProcess(command, 1, "", "401 unauthorized"),
    )

    assert drive_sync.sync_to_drive(
        db_path=news_db, data_dir=tmp_path / "missing", remote="gdrive:lake"
    ) is None


def test_rclone_missing_returns_none(news_db, tmp_path, staging, monkeypatch):
    monkeypatch.setattr(drive_sync, "_rclone_exe", lambda: None)

    assert drive_sync.sync_to_drive(
        db_path=news_db, data_dir=tmp_path / "missing", remote="gdrive:lake"
    ) is None


def test_empty_remote_disables_api_route(news_db, tmp_path, staging, monkeypatch):
    monkeypatch.setattr(drive_sync, "_push_with_rclone", lambda *a: pytest.fail("rclone must not run"))

    assert drive_sync.sync_to_drive(
        db_path=news_db, data_dir=tmp_path / "missing", remote=""
    ) is None
