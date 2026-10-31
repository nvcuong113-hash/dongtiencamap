from __future__ import annotations

import sqlite3
import hashlib
from pathlib import Path

import pytest

from quants import db
from quants.schema_audit import audit_database


def test_connect_enables_foreign_key_enforcement(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "foreign-keys.sqlite3")
    db.init_db()
    with db.connect() as c:
        assert c.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert c.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_macro_factor_id'").fetchone()
        with pytest.raises(sqlite3.IntegrityError):
            c.execute(
                "INSERT INTO auth_sessions(token_hash,user_id,created_at) VALUES(?,?,?)",
                ("orphan-token", 987654, "2026-10-31T00:00:00+00:00"),
            )


def test_schema_audit_passes_for_fresh_database_and_is_read_only(tmp_path, monkeypatch):
    path = tmp_path / "audit.sqlite3"
    monkeypatch.setattr(db, "DB_PATH", path)
    db.init_db()
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    report = audit_database(path, Path(__file__).resolve().parents[1])
    assert report["schema"]["schema_compatible"] is True
    assert report["schema"]["missing_tables"] == []
    assert report["static_sql_audit"]["failed_count"] == 0
    assert report["application_connection_foreign_keys"] == 1
    assert report["data_checks"]["admin_count"] == 1
    assert path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() == before
    assert report["table_access_inventory"]["table_count_referenced"] >= 18


def test_schema_audit_reports_missing_tables_without_crashing(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as c:
        c.execute("CREATE TABLE media_articles (id INTEGER PRIMARY KEY, title TEXT)")
    report = audit_database(path, Path(__file__).resolve().parents[1])
    assert report["overall_schema_status"] == "FAIL"
    assert "users" in report["schema"]["missing_tables"]
    assert report["data_checks"]["admin_count"] is None


def test_schema_audit_flags_baseline_data_continuity_changes(tmp_path, monkeypatch):
    baseline = tmp_path / "baseline.sqlite3"
    current = tmp_path / "current.sqlite3"
    monkeypatch.setattr(db, "DB_PATH", baseline)
    db.init_db()
    with db.connect() as c:
        c.execute("INSERT INTO media_articles(url_hash,title) VALUES(?,?)", ("hash-old", "Bài cũ"))
    # Create a separate current DB without copying the baseline row.
    monkeypatch.setattr(db, "DB_PATH", current)
    db.init_db()
    report = audit_database(current, Path(__file__).resolve().parents[1], baseline)
    assert report["data_continuity_review_required"] is True
    assert report["production_readiness"] == "DATA_CONTINUITY_REVIEW_REQUIRED"
    assert report["baseline_comparison"]["media_articles"]["baseline_keys_missing_current"] == 1


def test_schema_audit_detects_same_name_index_with_wrong_definition(tmp_path, monkeypatch):
    path = tmp_path / "bad-index.sqlite3"
    monkeypatch.setattr(db, "DB_PATH", path)
    db.init_db()
    with db.connect() as c:
        c.execute("DROP INDEX idx_macro_factor_id")
        c.execute("CREATE INDEX idx_macro_factor_id ON macro_snapshots(ts,id)")
    report = audit_database(path, Path(__file__).resolve().parents[1])
    assert report["schema"]["index_definition_mismatches"]["macro_snapshots"]["idx_macro_factor_id"]
    assert report["overall_schema_status"] == "FAIL"
