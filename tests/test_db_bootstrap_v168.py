from __future__ import annotations

import os
import sqlite3
from pathlib import Path
import ast
import tomllib

import pytest

from quants import db
from quants.config import resolve_db_path, PROJECT_ROOT


def test_config_default_db_path_is_absolute_and_project_anchored():
    from quants.config import DB_PATH
    assert DB_PATH.is_absolute()
    assert DB_PATH == (PROJECT_ROOT / "data" / "quants_pro.sqlite3").resolve()


def test_relative_db_override_is_anchored_to_project_not_working_directory(tmp_path):
    assert resolve_db_path("data/custom.sqlite3", PROJECT_ROOT) == (PROJECT_ROOT / "data/custom.sqlite3").resolve()
    assert resolve_db_path("/tmp/persistent/quants.sqlite3", PROJECT_ROOT) == Path("/tmp/persistent/quants.sqlite3")
    assert resolve_db_path(None, tmp_path / "repo", working_dir=tmp_path / "repo") == (tmp_path / "repo/data/quants_pro.sqlite3").resolve()


def test_db_path_keeps_existing_parent_checkout_legacy_database(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    legacy = tmp_path / "data" / "quants_pro.sqlite3"
    legacy.parent.mkdir()
    legacy.write_bytes(b"legacy database placeholder")
    assert resolve_db_path(None, repo, working_dir=repo) == legacy.resolve()


def test_db_path_refuses_to_guess_between_two_existing_database_files(tmp_path):
    repo = tmp_path / "repo"
    (repo / "data").mkdir(parents=True)
    (tmp_path / "data").mkdir()
    (repo / "data" / "quants_pro.sqlite3").write_bytes(b"database A")
    (tmp_path / "data" / "quants_pro.sqlite3").write_bytes(b"database B")
    with pytest.raises(RuntimeError, match="nhiều SQLite database"):
        resolve_db_path(None, repo, working_dir=repo)


def test_app_bootstrap_is_called_before_other_quants_modules_are_imported():
    app_path = Path(__file__).resolve().parents[1] / "app.py"
    tree = ast.parse(app_path.read_text(encoding="utf-8"))
    bootstrap_line = next(
        node.lineno for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id == "ensure_database_ready"
    )
    project_imports = [
        node.lineno for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
        and node.module.startswith("quants.") and node.module not in ("quants.db", "quants.config")
    ]
    assert project_imports and bootstrap_line < min(project_imports)


def test_bootstrap_migrates_bundled_media_only_db_and_preserves_rows(tmp_path, monkeypatch):
    path = tmp_path / "deployed.sqlite3"
    c = sqlite3.connect(path)
    c.execute("""CREATE TABLE media_articles (
        id INTEGER PRIMARY KEY AUTOINCREMENT, url_hash TEXT UNIQUE, published_at TEXT,
        crawled_at TEXT, fetched_at TEXT, source TEXT, title TEXT, summary TEXT, url TEXT,
        symbols TEXT, symbols_json TEXT, raw_text TEXT, sentiment TEXT, sentiment_score REAL,
        pr_level TEXT, retail_buzz TEXT, lai_intent TEXT, warning TEXT, analyzed_by TEXT,
        sentiment_by_symbol TEXT, event_type TEXT, event_confidence REAL,
        source_reliability REAL, novelty_score REAL, attention_score REAL
    )""")
    c.execute("""CREATE TABLE media_symbol_daily (
        id INTEGER PRIMARY KEY AUTOINCREMENT, recorded_at TEXT NOT NULL DEFAULT '',
        symbol TEXT NOT NULL DEFAULT '', article_count INTEGER DEFAULT 0, pr_level TEXT,
        retail_buzz TEXT, sentiment TEXT, lai_intent TEXT, warning TEXT,
        attention_score REAL, baseline_count REAL, independent_sources INTEGER
    )""")
    c.execute("INSERT INTO media_articles(id,title,symbols) VALUES(1,'legacy','IDC')")
    c.commit()
    c.close()

    monkeypatch.setattr(db, "DB_PATH", path)
    monkeypatch.setenv("QUANTS_INITIAL_ADMIN_PASSWORD", "A-Strong-First-Admin-Password-2026!")
    monkeypatch.delenv("QUANTS_ADMIN_RESET_PASSWORD", raising=False)
    result = db.ensure_database_ready()

    assert result["status"] == "READY"
    assert result["admin_created"] is True
    with db.connect() as c:
        assert c.execute("SELECT title,symbols,symbols_json FROM media_articles WHERE id=1").fetchone()[:] == ("legacy", "IDC", "IDC")
        assert c.execute("SELECT COUNT(*) FROM media_symbol_daily").fetchone()[0] == 0
        for table in ("users", "auth_sessions", "stock_snapshots", "flow_snapshots", "macro_snapshots", "app_meta"):
            assert c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
        assert c.execute("SELECT LOWER(username) FROM users WHERE role='ADMIN'").fetchone()[0] == "admin"
    user, message = db.authenticate_user("Admin", "A-Strong-First-Admin-Password-2026!")
    assert user and user["role"] == "ADMIN" and message == "OK"


def test_bootstrap_detects_missing_table_even_when_schema_marker_is_current(tmp_path, monkeypatch):
    path = tmp_path / "deleted-table.sqlite3"
    monkeypatch.setattr(db, "DB_PATH", path)
    monkeypatch.setenv("QUANTS_INITIAL_ADMIN_PASSWORD", "Initial-Strong-Pass-2026!")
    db.ensure_database_ready()
    with db.connect() as c:
        c.execute("DROP TABLE macro_snapshots")
        c.execute("INSERT INTO media_articles(title) VALUES('preserve me')")
    result = db.ensure_database_ready()
    assert result["status"] == "READY"
    with db.connect() as c:
        assert c.execute("SELECT title FROM media_articles WHERE title='preserve me'").fetchone()
        assert c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='macro_snapshots'").fetchone()


def test_admin_created_without_secret_is_explicitly_marked_for_reset(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "admin-reset-required.sqlite3")
    monkeypatch.delenv("QUANTS_INITIAL_ADMIN_PASSWORD", raising=False)
    monkeypatch.delenv("QUANTS_ADMIN_RESET_PASSWORD", raising=False)
    result = db.ensure_database_ready()
    assert result["admin_created"] is True
    assert result["admin_setup_required"] is True
    user, message = db.authenticate_user("admin", "admin123")
    assert user is None
    assert message == "Sai tên đăng nhập hoặc mật khẩu!"


def test_admin_reset_secret_is_applied_once_not_on_every_rerun(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "admin-reset-once.sqlite3")
    monkeypatch.setenv("QUANTS_INITIAL_ADMIN_PASSWORD", "Initial-Strong-Pass-2026!")
    monkeypatch.delenv("QUANTS_ADMIN_RESET_PASSWORD", raising=False)
    db.ensure_database_ready()

    monkeypatch.setenv("QUANTS_ADMIN_RESET_PASSWORD", "One-Time-Reset-Pass-2026!")
    db.ensure_database_ready()
    user, _ = db.authenticate_user("admin", "One-Time-Reset-Pass-2026!")
    assert user
    with db.connect() as c:
        reset_marker = c.execute("SELECT value FROM app_meta WHERE key='admin_reset_secret_hash'").fetchone()[0]
        assert reset_marker.startswith("pbkdf2_sha256$")

    # An intentional later password change must survive app reruns while the same
    # reset secret remains configured; the secret is a one-shot operation.
    with db.connect() as c:
        c.execute("UPDATE users SET password_hash=? WHERE username='admin'", (db._hash_pw("Changed-After-Reset-2026!"),))
    db.ensure_database_ready()
    user, _ = db.authenticate_user("admin", "Changed-After-Reset-2026!")
    assert user
    user, _ = db.authenticate_user("admin", "One-Time-Reset-Pass-2026!")
    assert user is None


def test_changed_reset_secret_applies_as_a_new_one_time_reset(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "admin-reset-new-secret.sqlite3")
    monkeypatch.setenv("QUANTS_INITIAL_ADMIN_PASSWORD", "Initial-Strong-Pass-2026!")
    db.ensure_database_ready()
    monkeypatch.setenv("QUANTS_ADMIN_RESET_PASSWORD", "First-Reset-Pass-2026!")
    db.ensure_database_ready()
    monkeypatch.setenv("QUANTS_ADMIN_RESET_PASSWORD", "Second-Reset-Pass-2026!")
    db.ensure_database_ready()
    user, _ = db.authenticate_user("admin", "Second-Reset-Pass-2026!")
    assert user
    old, _ = db.authenticate_user("admin", "First-Reset-Pass-2026!")
    assert old is None


def test_bootstrap_is_repeatable_and_does_not_duplicate_admin(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "repeatable.sqlite3")
    monkeypatch.setenv("QUANTS_INITIAL_ADMIN_PASSWORD", "Stable-Admin-Pass-2026!")
    one = db.ensure_database_ready()
    two = db.ensure_database_ready()
    assert one["status"] == two["status"] == "READY"
    with db.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM users WHERE username='admin'").fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM app_meta WHERE key='db_schema_version'").fetchone()[0] == 1


def test_secrets_example_is_valid_toml():
    example = Path(__file__).resolve().parents[1] / ".streamlit" / "secrets.toml.example"
    parsed = tomllib.loads(example.read_text(encoding="utf-8"))
    assert parsed["GEMINI_API_KEY"] == "replace-with-a-new-key"
    assert parsed["DNSE_API_KEY"] == "replace-with-dnse-api-key"
    assert "QUANTS_INITIAL_ADMIN_PASSWORD" in example.read_text(encoding="utf-8")
