from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone

from quants import db


def _legacy_user_db(path, *, with_session_table=True, session_columns=("token_hash", "user_id")):
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            full_name TEXT,
            role TEXT DEFAULT 'USER',
            created_at TEXT
        )"""
    )
    conn.execute(
        "INSERT INTO users(username,password_hash,full_name,role,created_at) VALUES(?,?,?,?,?)",
        ("legacy_user", "legacy-password-hash", "Legacy User", "USER", "2026-10-30T10:00:00+00:00"),
    )
    if with_session_table:
        definitions = {
            "token_hash": "TEXT PRIMARY KEY",
            "user_id": "INTEGER",
            "created_at": "TEXT",
        }
        columns = ",".join(f'"{name}" {definitions[name]}' for name in session_columns)
        conn.execute(f"CREATE TABLE auth_sessions ({columns})")
        if "token_hash" in session_columns and "user_id" in session_columns:
            token_hash = hashlib.sha256(b"legacy-session-token").hexdigest()
            names = [name for name in session_columns if name in {"token_hash", "user_id", "created_at"}]
            values = [token_hash if name == "token_hash" else 1 if name == "user_id" else None for name in names]
            conn.execute(
                f"INSERT INTO auth_sessions({','.join(names)}) VALUES({','.join('?' for _ in names)})",
                values,
            )
    conn.commit()
    conn.close()


def test_session_restore_repairs_missing_user_and_session_columns_then_invalidates_untimed_session(tmp_path, monkeypatch):
    path = tmp_path / "legacy_users_sessions.sqlite3"
    _legacy_user_db(path)
    monkeypatch.setattr(db, "DB_PATH", path)

    # Old cookie hash exists, but the old sessions table had no created_at.
    # The restore path must repair schema and fail closed instead of throwing.
    assert db.get_user_by_session("legacy-session-token") is None

    with db.connect() as conn:
        user = conn.execute(
            "SELECT id,username,password_hash,status,cash_balance FROM users WHERE username='legacy_user'"
        ).fetchone()
        assert user["username"] == "legacy_user"
        assert user["password_hash"] == "legacy-password-hash"
        assert user["status"] == "APPROVED"
        assert user["cash_balance"] == 0.0
        user_cols = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
        session_cols = {row["name"] for row in conn.execute("PRAGMA table_info(auth_sessions)")}
        assert {"status", "cash_balance"} <= user_cols
        assert {"token_hash", "user_id", "created_at"} <= session_cols
        assert conn.execute("SELECT COUNT(*) FROM auth_sessions").fetchone()[0] == 0

    # New sessions after migration carry a valid timestamp and can be restored.
    token = db.create_auth_session(int(user["id"]))
    restored = db.get_user_by_session(token)
    assert restored is not None
    assert restored["username"] == "legacy_user"
    assert restored["cash_balance"] == 0.0

    db.revoke_auth_session(token)
    assert db.get_user_by_session(token) is None


def test_session_restore_creates_missing_auth_sessions_table(tmp_path, monkeypatch):
    path = tmp_path / "users_without_sessions.sqlite3"
    _legacy_user_db(path, with_session_table=False)
    monkeypatch.setattr(db, "DB_PATH", path)

    assert db.get_user_by_session("unknown-token") is None
    token = db.create_auth_session(1)
    restored = db.get_user_by_session(token)
    assert restored is not None and restored["id"] == 1


def test_session_restore_repairs_empty_partial_auth_sessions_table(tmp_path, monkeypatch):
    path = tmp_path / "partial_sessions.sqlite3"
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL
        )"""
    )
    conn.execute("INSERT INTO users(username,password_hash) VALUES('user1','hash')")
    conn.execute("CREATE TABLE auth_sessions (legacy_id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()
    monkeypatch.setattr(db, "DB_PATH", path)

    token = db.create_auth_session(1)
    restored = db.get_user_by_session(token)
    assert restored is not None
    with db.connect() as conn:
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(auth_sessions)")}
        assert {"token_hash", "user_id", "created_at"} <= cols


def test_malformed_or_future_session_timestamp_is_rejected(tmp_path, monkeypatch):
    path = tmp_path / "bad_session_timestamp.sqlite3"
    monkeypatch.setattr(db, "DB_PATH", path)
    db.init_db()
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO users(username,password_hash,full_name,role,status,cash_balance,created_at) VALUES(?,?,?,?,?,?,?)",
            ("user2", "hash", "User 2", "USER", "APPROVED", 0, datetime.now(timezone.utc).isoformat()),
        )
        user_id = conn.execute("SELECT id FROM users WHERE username='user2'").fetchone()[0]
    token = db.create_auth_session(user_id)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    with db.connect() as conn:
        conn.execute("UPDATE auth_sessions SET created_at='not-a-date' WHERE token_hash=?", (token_hash,))
    assert db.get_user_by_session(token) is None

    future_token = db.create_auth_session(user_id)
    future_hash = hashlib.sha256(future_token.encode()).hexdigest()
    with db.connect() as conn:
        conn.execute("UPDATE auth_sessions SET created_at='2999-01-01T00:00:00+00:00' WHERE token_hash=?", (future_hash,))
    assert db.get_user_by_session(future_token) is None
