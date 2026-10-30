from __future__ import annotations
import hashlib, hmac, sqlite3, re, os, secrets, json, math, threading
from datetime import datetime, timezone, timedelta
import pandas as pd
from .config import DB_PATH

SCHEMA = '''
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS stock_snapshots(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT, trade_date TEXT, symbol TEXT, sector TEXT,
    price REAL, change_pct REAL, volume INTEGER, ma20_vol REAL, projected_volume REAL,
    vwap REAL, vwap_position TEXT, buy_vol INTEGER, sell_vol INTEGER,
    buy_ratio REAL, net_flow_billion REAL, delta_flow_open REAL,
    rs_vni REAL, rs_sector REAL, foreign_net_today INTEGER, foreign_net_5d INTEGER,
    quality_score REAL, signal_score REAL, agreement_pct REAL, p_win REAL,
    conviction TEXT, wyckoff_phase TEXT, action_rec TEXT,
    ai_vsa TEXT, ai_action TEXT, pred_t2 REAL, pred_t3 REAL, pred_t7 REAL,
    source_label TEXT, is_real_deal INTEGER, metrics_json TEXT
);
CREATE TABLE IF NOT EXISTS flow_snapshots(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT, trade_date TEXT, symbol TEXT, net_flow_billion REAL,
    buy_vol INTEGER, sell_vol INTEGER, price REAL, volume INTEGER, is_real_deal INTEGER,
    flow_status TEXT, flow_source TEXT, quote_freshness TEXT,
    quote_freshness_verified INTEGER, quote_source_timestamp TEXT,
    quote_provider TEXT, crosscheck_status TEXT
);
CREATE TABLE IF NOT EXISTS market_bars(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL, session_date TEXT NOT NULL,
    open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL,
    volume INTEGER, source_name TEXT NOT NULL, captured_at TEXT NOT NULL,
    quality_status TEXT NOT NULL, payload_hash TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS smf_v2_runs(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    generated_at TEXT NOT NULL, as_of_date TEXT, schema_version TEXT NOT NULL,
    mode TEXT NOT NULL, decision_impact TEXT NOT NULL,
    instrument_count INTEGER NOT NULL DEFAULT 0, evidence_count INTEGER NOT NULL DEFAULT 0,
    report_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS smf_v2_evidence(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL, evidence_id TEXT NOT NULL, symbol TEXT NOT NULL, session_date TEXT,
    engine TEXT, evidence_group TEXT, feature TEXT, polarity TEXT, quality_status TEXT,
    strength_label TEXT, summary TEXT, source_name TEXT, source_fields_json TEXT,
    contributes_to_consensus INTEGER NOT NULL DEFAULT 0, payload_json TEXT,
    UNIQUE(run_id, evidence_id),
    FOREIGN KEY(run_id) REFERENCES smf_v2_runs(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS predictions(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT, trade_date TEXT, symbol TEXT, horizon INTEGER,
    base_price REAL, predicted_pct REAL, predicted_action TEXT, p_win REAL,
    signal_score REAL, quality_score REAL, regime TEXT,
    ai_provider TEXT, ai_model TEXT, feature_json TEXT,
    status TEXT DEFAULT 'OPEN', settled_at TEXT, settle_trade_date TEXT,
    actual_price REAL, actual_pct REAL, abs_error_pct REAL, outcome TEXT
);
CREATE TABLE IF NOT EXISTS prediction_versions(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL, trade_date TEXT NOT NULL, symbol TEXT NOT NULL,
    horizon INTEGER NOT NULL, base_price REAL NOT NULL, predicted_pct REAL NOT NULL,
    predicted_action TEXT, p_win REAL, signal_score REAL, quality_score REAL,
    regime TEXT, ai_provider TEXT, ai_model TEXT, feature_json TEXT,
    version_hash TEXT NOT NULL UNIQUE, is_current INTEGER NOT NULL DEFAULT 1,
    version_type TEXT DEFAULT 'CURRENT_SNAPSHOT'
);
CREATE TABLE IF NOT EXISTS model_performance(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    computed_at TEXT, horizon INTEGER, regime_group TEXT, sample_count INTEGER,
    direction_scored_count INTEGER,
    win_rate_pct REAL, mae_pct REAL, avg_return_pct REAL, bias_pct REAL,
    coverage_pct REAL, notes TEXT
);
CREATE TABLE IF NOT EXISTS media_articles(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url_hash TEXT UNIQUE, published_at TEXT, crawled_at TEXT, fetched_at TEXT, source TEXT,
    title TEXT, summary TEXT, url TEXT, symbols TEXT, symbols_json TEXT, raw_text TEXT,
    sentiment TEXT, sentiment_score REAL, pr_level TEXT, retail_buzz TEXT,
    lai_intent TEXT, warning TEXT, analyzed_by TEXT, sentiment_by_symbol TEXT,
    event_type TEXT, event_confidence REAL, source_reliability REAL,
    novelty_score REAL, attention_score REAL
);
CREATE TABLE IF NOT EXISTS media_symbol_daily(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recorded_at TEXT NOT NULL, symbol TEXT NOT NULL, article_count INTEGER DEFAULT 0,
    pr_level TEXT, retail_buzz TEXT, sentiment TEXT, lai_intent TEXT, warning TEXT,
    attention_score REAL, baseline_count REAL, independent_sources INTEGER
);
CREATE TABLE IF NOT EXISTS settings(
    key TEXT PRIMARY KEY, value TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS macro_snapshots(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT, factor TEXT, value REAL, change_pct REAL,
    unit TEXT, source TEXT, impact TEXT, analysis TEXT
);
CREATE TABLE IF NOT EXISTS system_events(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT, level TEXT, component TEXT, message TEXT
);
CREATE TABLE IF NOT EXISTS users(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    full_name TEXT,
    role TEXT DEFAULT 'USER',
    status TEXT DEFAULT 'APPROVED',
    cash_balance REAL DEFAULT 0.0,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS auth_sessions(
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS user_portfolios(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    buy_price REAL NOT NULL,
    volume INTEGER NOT NULL,
    updated_at TEXT,
    FOREIGN KEY(user_id) REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS user_preferences(
    user_id INTEGER NOT NULL,
    preference_key TEXT NOT NULL,
    preference_value TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(user_id, preference_key),
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS app_meta(
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
'''

DB_SCHEMA_VERSION = "20261031_v168_absolute_path_bootstrap_admin_v1"
_DB_BOOTSTRAP_LOCK = threading.RLock()
SESSION_TTL_DAYS = 7
PASSWORD_HASH_ITERATIONS = 310_000

def _hash_pw(password: str) -> str:
    """Hash passwords with per-password salt and a deliberately slow KDF."""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", str(password).encode("utf-8"), salt, PASSWORD_HASH_ITERATIONS
    )
    return "pbkdf2_sha256${}${}${}".format(
        PASSWORD_HASH_ITERATIONS, salt.hex(), digest.hex()
    )


def _verify_pw(password: str, stored_hash: str) -> tuple[bool, bool]:
    """Return (valid, needs_upgrade); accept the former deterministic hash once."""
    stored = str(stored_hash or "")
    if stored.startswith("pbkdf2_sha256$"):
        try:
            scheme, iterations, salt_hex, digest_hex = stored.split("$", 3)
            if scheme != "pbkdf2_sha256":
                return False, False
            rounds = int(iterations)
            if rounds < 100_000 or rounds > 2_000_000:
                return False, False
            salt = bytes.fromhex(salt_hex)
            expected = bytes.fromhex(digest_hex)
            actual = hashlib.pbkdf2_hmac("sha256", str(password).encode("utf-8"), salt, rounds)
            return hmac.compare_digest(actual, expected), False
        except (TypeError, ValueError):
            return False, False
    # Legacy SHA-256 format from earlier releases. Upgrade it after a successful login.
    legacy = hashlib.sha256(f"quants_v16_salt_{password}".encode("utf-8")).hexdigest()
    return hmac.compare_digest(legacy, stored), True


class _ClosingConnection(sqlite3.Connection):
    """A sqlite connection that commits/rolls back and always closes in `with`."""
    def __exit__(self, exc_type, exc, tb):
        try:
            return super().__exit__(exc_type, exc, tb)
        finally:
            self.close()

def _read_secret(name: str) -> str | None:
    """Read a secret from environment first, then Streamlit Secrets."""
    value = os.getenv(name, "").strip()
    if value:
        return value
    try:
        import streamlit as st
        value = str(st.secrets.get(name, "")).strip()
        return value or None
    except Exception:
        return None


def _admin_reset_password() -> str | None:
    return _read_secret("QUANTS_ADMIN_RESET_PASSWORD")


def _initial_admin_password() -> str | None:
    return _read_secret("QUANTS_INITIAL_ADMIN_PASSWORD")


def connect():
    # Resolve again because tests and controlled maintenance tasks may override
    # DB_PATH after import. The production default itself is already absolute.
    database_path = DB_PATH.expanduser().resolve()
    database_path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(
        str(database_path), timeout=30, check_same_thread=False, factory=_ClosingConnection
    )
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout=30000")
    return c

def _schema_columns(schema: str) -> dict[str, dict[str, str]]:
    tables: dict[str, dict[str, str]] = {}
    current_table = None
    for line in schema.splitlines():
        stripped = line.strip()
        match = re.match(r"CREATE TABLE IF NOT EXISTS\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", stripped, flags=re.IGNORECASE)
        if match:
            current_table = match.group(1)
            tables[current_table] = {}
            continue
        if current_table is None:
            continue
        if stripped.startswith(");"):
            current_table = None
            continue
        for fragment in stripped.rstrip(",").split(","):
            col_match = re.match(r"([A-Za-z_][A-Za-z0-9_]*)\s+(INTEGER|REAL|TEXT|BLOB|NUMERIC)\b", fragment.strip(), flags=re.IGNORECASE)
            if col_match:
                tables[current_table][col_match.group(1)] = col_match.group(2).upper()
    return tables

def _migrate_legacy_columns(c: sqlite3.Connection) -> None:
    """Add absent non-primary-key columns without rewriting legacy records."""
    for table, expected_columns in _schema_columns(SCHEMA).items():
        existing = {row["name"] for row in c.execute(f'PRAGMA table_info("{table}")').fetchall()}
        for column, sql_type in expected_columns.items():
            if column == "id" or column in existing:
                continue
            c.execute(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {sql_type}')
            existing.add(column)


def _create_indexes_safely(c: sqlite3.Connection) -> None:
    """Create indexes only when a legacy table contains every indexed column."""
    definitions = (
        ("idx_snap_sym_date", "stock_snapshots", ("symbol", "trade_date", "id")),
        ("idx_market_bars_symbol_date", "market_bars", ("symbol", "session_date", "captured_at")),
        ("idx_smf_v2_runs_generated", "smf_v2_runs", ("generated_at",)),
        ("idx_smf_v2_evidence_symbol_date", "smf_v2_evidence", ("symbol", "session_date", "evidence_group")),
        ("idx_smf_v2_evidence_run", "smf_v2_evidence", ("run_id", "evidence_group")),
        ("idx_flow_sym_date", "flow_snapshots", ("symbol", "trade_date", "id")),
        ("idx_pred_status", "predictions", ("status", "horizon", "trade_date")),
        ("idx_user_port", "user_portfolios", ("user_id",)),
        ("idx_user_preferences_updated", "user_preferences", ("user_id", "updated_at")),
        ("idx_media_daily_symbol_time", "media_symbol_daily", ("symbol", "recorded_at")),
        ("idx_predictions_symbol_horizon_date", "predictions", ("symbol", "horizon", "trade_date")),
        ("idx_prediction_versions_symbol_horizon_date", "prediction_versions", ("symbol", "horizon", "trade_date", "is_current")),
        ("idx_auth_sessions_user_created", "auth_sessions", ("user_id", "created_at")),
    )
    for name, table, columns in definitions:
        actual = {row["name"] for row in c.execute(f'PRAGMA table_info("{table}")').fetchall()}
        if set(columns) <= actual:
            cols_sql = ", ".join(f'"{column}"' for column in columns)
            c.execute(f'CREATE INDEX IF NOT EXISTS "{name}" ON "{table}" ({cols_sql})')


def _meta_get(c: sqlite3.Connection, key: str) -> str | None:
    row = c.execute("SELECT value FROM app_meta WHERE key=?", (key,)).fetchone()
    return str(row["value"]) if row else None


def _meta_set(c: sqlite3.Connection, key: str, value: str) -> None:
    c.execute(
        "INSERT INTO app_meta(key,value,updated_at) VALUES(?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (key, str(value), datetime.now(timezone.utc).isoformat()),
    )


def _ensure_users_unique_username(c: sqlite3.Connection) -> None:
    duplicates = c.execute(
        "SELECT LOWER(TRIM(username)) AS username_key, COUNT(*) AS n "
        "FROM users WHERE username IS NOT NULL AND TRIM(username)!='' "
        "GROUP BY LOWER(TRIM(username)) HAVING COUNT(*)>1 LIMIT 5"
    ).fetchall()
    if duplicates:
        names = ", ".join(str(row["username_key"]) for row in duplicates)
        raise sqlite3.DatabaseError(
            "Không thể tạo unique index cho users.username vì có username trùng: " + names
        )
    c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username_unique ON users(username)")


def _initialize_admin(c: sqlite3.Connection) -> dict:
    """Create the canonical admin once; apply a configured reset secret once per value."""
    now_str = datetime.now(timezone.utc).isoformat()
    row = c.execute("SELECT id FROM users WHERE LOWER(username)='admin' ORDER BY id LIMIT 1").fetchone()
    created = row is None
    initial_password = _initial_admin_password()
    reset_password = _admin_reset_password()
    if created:
        password = initial_password or reset_password or secrets.token_urlsafe(32)
        c.execute(
            "INSERT INTO users(username,password_hash,full_name,role,status,cash_balance,created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            ("admin", _hash_pw(password), "Quản Trị Viên (Admin)", "ADMIN", "APPROVED", 500000000.0, now_str),
        )
        _meta_set(c, "admin_bootstrap_status", "READY" if (initial_password or reset_password) else "RESET_REQUIRED")
        if not initial_password and not reset_password:
            c.execute(
                "INSERT INTO system_events(ts,level,component,message) VALUES(?,?,?,?)",
                (now_str, "WARN", "auth", "Admin đã được tạo với mật khẩu ngẫu nhiên chưa công bố. Cần cấu hình QUANTS_ADMIN_RESET_PASSWORD để thiết lập mật khẩu quản trị."),
            )
        row = c.execute("SELECT id FROM users WHERE LOWER(username)='admin' ORDER BY id LIMIT 1").fetchone()
    else:
        c.execute("UPDATE users SET username='admin', role='ADMIN', status='APPROVED' WHERE id=?", (row["id"],))

    if reset_password:
        previous_hash = _meta_get(c, "admin_reset_secret_hash")
        same_secret = bool(previous_hash and _verify_pw(reset_password, previous_hash)[0])
        # Keep only a salted, slow password hash; never store a fast unsalted
        # fingerprint of a potentially human-chosen reset password.
        if not same_secret:
            c.execute("UPDATE users SET password_hash=? WHERE id=?", (_hash_pw(reset_password), row["id"]))
            _meta_set(c, "admin_reset_secret_hash", _hash_pw(reset_password))
            _meta_set(c, "admin_bootstrap_status", "READY")
            c.execute(
                "INSERT INTO system_events(ts,level,component,message) VALUES(?,?,?,?)",
                (now_str, "INFO", "auth", "Đã áp dụng cấu hình reset mật khẩu Admin một lần."),
            )
    elif initial_password and created:
        _meta_set(c, "admin_bootstrap_status", "READY")

    return {"created": created, "password_configured": bool(initial_password or reset_password),
            "setup_required": _meta_get(c, "admin_bootstrap_status") == "RESET_REQUIRED"}


def _schema_health(c: sqlite3.Connection) -> dict:
    expected = _schema_columns(SCHEMA)
    actual_tables = {row["name"] for row in c.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    missing_tables = sorted(set(expected) - actual_tables)
    missing_columns = {}
    for table, expected_columns in expected.items():
        if table not in actual_tables:
            continue
        actual_columns = {row["name"] for row in c.execute(f'PRAGMA table_info("{table}")').fetchall()}
        absent = set(expected_columns) - actual_columns
        # stock_snapshots without id is a supported legacy layout; callers use rowid.
        if table == "stock_snapshots":
            absent.discard("id")
        if absent:
            missing_columns[table] = sorted(absent)
    return {"missing_tables": missing_tables, "missing_columns": missing_columns,
            "healthy": not missing_tables and not missing_columns}

def ensure_stock_snapshots_schema_compatibility(c: sqlite3.Connection | None = None) -> set[str]:
    """Ensure the runtime stock-snapshot table has the current additive schema.

    This guard is intentionally independent of Streamlit's cached ``init_db``.
    It is safe to call from the analytical hot path: CREATE TABLE IF NOT EXISTS
    and PRAGMA inspection are idempotent, and only absent columns are added.
    Existing observations are never rewritten; newly added historical values
    remain NULL (unknown), not synthetic zeroes.

    Pass an existing connection to participate in its transaction. When omitted,
    the helper manages its own short-lived connection.
    """
    if c is None:
        with connect() as owned:
            return ensure_stock_snapshots_schema_compatibility(owned)

    match = re.search(
        r"CREATE TABLE IF NOT EXISTS\s+stock_snapshots\s*\(.*?\);",
        SCHEMA,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if match is None:
        raise sqlite3.DatabaseError("stock_snapshots schema definition is missing")

    # Also repairs a database where the table itself was never created.
    c.execute(match.group(0))
    expected = _schema_columns(match.group(0)).get("stock_snapshots", {})
    existing = {
        row["name"]
        for row in c.execute('PRAGMA table_info("stock_snapshots")').fetchall()
    }
    for column, sql_type in expected.items():
        # An existing legacy table without an id column is still queryable via
        # SQLite rowid; do not add a nullable pseudo-primary-key column.
        if column == "id" or column in existing:
            continue
        c.execute(
            f'ALTER TABLE "stock_snapshots" ADD COLUMN "{column}" {sql_type}'
        )
        existing.add(column)

    required = {"symbol", "trade_date", "foreign_net_today"}
    missing = required - existing
    if missing:
        raise sqlite3.DatabaseError(
            "stock_snapshots schema is incompatible; missing columns: "
            + ", ".join(sorted(missing))
        )
    return existing


def _backfill_legacy_values(c: sqlite3.Connection) -> None:
    media_cols = {r["name"] for r in c.execute('PRAGMA table_info("media_articles")').fetchall()}
    if {"symbols", "symbols_json"} <= media_cols:
        c.execute("UPDATE media_articles SET symbols_json=symbols WHERE (symbols_json IS NULL OR symbols_json='') AND symbols IS NOT NULL")
        c.execute("UPDATE media_articles SET symbols=symbols_json WHERE (symbols IS NULL OR symbols='') AND symbols_json IS NOT NULL")
    if {"fetched_at", "crawled_at"} <= media_cols:
        c.execute("UPDATE media_articles SET fetched_at=crawled_at WHERE fetched_at IS NULL AND crawled_at IS NOT NULL")
    if {"id", "url", "url_hash"} <= media_cols:
        rows = c.execute("SELECT id,url FROM media_articles WHERE (url_hash IS NULL OR url_hash='') AND url IS NOT NULL AND url!=''").fetchall()
        for row in rows:
            digest = hashlib.sha256(str(row["url"]).strip().encode("utf-8")).hexdigest()
            exists = c.execute("SELECT 1 FROM media_articles WHERE url_hash=? LIMIT 1", (digest,)).fetchone()
            if not exists:
                try:
                    c.execute("UPDATE media_articles SET url_hash=? WHERE id=?", (digest, row["id"]))
                except sqlite3.IntegrityError:
                    pass

def _backfill_flow_provenance(c: sqlite3.Connection) -> None:
    """Classify legacy flow rows from their paired stock-snapshot provenance.

    Existing numeric values are retained for audit. Unverifiable rows receive a
    non-eligible status and are excluded from historical flow features.
    """
    flow_cols = {row["name"] for row in c.execute('PRAGMA table_info("flow_snapshots")').fetchall()}
    stock_cols = {row["name"] for row in c.execute('PRAGMA table_info("stock_snapshots")').fetchall()}
    if not {"id", "flow_status", "flow_source", "quote_freshness", "quote_freshness_verified",
            "quote_source_timestamp", "quote_provider", "crosscheck_status"} <= flow_cols:
        return
    if not {"id", "metrics_json", "ts", "symbol", "trade_date"} <= stock_cols:
        return
    rows = c.execute("""
        SELECT f.id, f.ts, f.trade_date, f.symbol, f.net_flow_billion,
               f.buy_vol, f.sell_vol, f.is_real_deal,
               (SELECT s.metrics_json FROM stock_snapshots s
                WHERE s.symbol=f.symbol AND s.trade_date=f.trade_date AND s.ts=f.ts
                ORDER BY s.id DESC LIMIT 1) AS metrics_json
        FROM flow_snapshots f
        WHERE f.flow_status IS NULL OR TRIM(f.flow_status)=''
    """).fetchall()
    for row in rows:
        try:
            metrics = json.loads(row["metrics_json"] or "{}")
            if not isinstance(metrics, dict):
                metrics = {}
        except (TypeError, ValueError, json.JSONDecodeError):
            metrics = {}
        verified = bool(metrics.get("quote_freshness_verified") is True
                        and str(metrics.get("quote_source_timestamp") or "").strip())
        net_flow = row["net_flow_billion"]
        if net_flow is None:
            status = "NO_FLOW"
        elif verified and bool(row["is_real_deal"]) and row["buy_vol"] is not None and row["sell_vol"] is not None:
            status = "REAL_DEAL_VERIFIED"
        elif (verified and str(metrics.get("vwap_source") or "").upper() == "REAL"
              and "Ước tính từ VWAP" in str(metrics.get("flow_confidence") or "")
              and row["buy_vol"] is not None and row["sell_vol"] is not None):
            status = "VERIFIED_PROXY_ESTIMATE"
        else:
            status = "LEGACY_UNVERIFIED"
        source_label = str(metrics.get("source_label") or "")
        flow_source = str(metrics.get("flow_source") or "")
        if not flow_source and "Flow:" in source_label:
            flow_source = source_label.split("Flow:", 1)[1].strip()
        provider = str(metrics.get("quote_provider") or "")
        if not provider and source_label:
            provider = source_label.split("|", 1)[0].strip()
        c.execute("""UPDATE flow_snapshots SET flow_status=?, flow_source=?,
            quote_freshness=?, quote_freshness_verified=?, quote_source_timestamp=?,
            quote_provider=?, crosscheck_status=? WHERE id=?""", (
            status, flow_source or None, metrics.get("quote_freshness"), int(verified),
            metrics.get("quote_source_timestamp"), provider or None,
            metrics.get("crosscheck_status"), row["id"],
        ))


def _backfill_prediction_versions(c: sqlite3.Connection) -> None:
    """Preserve existing one-row-per-horizon predictions as explicit baseline versions."""
    columns = {row["name"] for row in c.execute('PRAGMA table_info("predictions")').fetchall()}
    if not {"id", "created_at", "trade_date", "symbol", "horizon", "base_price", "predicted_pct"} <= columns:
        return
    rows = c.execute("SELECT * FROM predictions ORDER BY id").fetchall()
    for row in rows:
        symbol = str(row["symbol"] or "").upper()
        if not symbol or not row["trade_date"] or row["horizon"] is None or row["base_price"] is None or row["predicted_pct"] is None:
            continue
        exists = c.execute(
            "SELECT 1 FROM prediction_versions WHERE trade_date=? AND UPPER(symbol)=? AND horizon=? LIMIT 1",
            (row["trade_date"], symbol, row["horizon"]),
        ).fetchone()
        if exists:
            continue
        values = {
            "source": "LEGACY_BASELINE", "created_at": row["created_at"],
            "trade_date": row["trade_date"], "symbol": symbol, "horizon": row["horizon"],
            "base_price": row["base_price"], "predicted_pct": row["predicted_pct"],
            "predicted_action": row["predicted_action"], "p_win": row["p_win"],
            "signal_score": row["signal_score"], "quality_score": row["quality_score"],
            "regime": row["regime"], "ai_provider": row["ai_provider"], "ai_model": row["ai_model"],
            "feature_json": row["feature_json"],
        }
        digest = hashlib.sha256(json.dumps(values, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()
        c.execute("""INSERT OR IGNORE INTO prediction_versions(
            created_at, trade_date, symbol, horizon, base_price, predicted_pct,
            predicted_action, p_win, signal_score, quality_score, regime,
            ai_provider, ai_model, feature_json, version_hash, is_current, version_type
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,'LEGACY_BASELINE')""", (
            row["created_at"] or datetime.now(timezone.utc).isoformat(), row["trade_date"], symbol,
            row["horizon"], row["base_price"], row["predicted_pct"], row["predicted_action"],
            row["p_win"], row["signal_score"], row["quality_score"], row["regime"],
            row["ai_provider"], row["ai_model"], row["feature_json"], digest,
        ))


def _backfill_predictions_from_versions(c: sqlite3.Connection) -> int:
    """Restore one current forecast per symbol/date/horizon from version history.

    Version rows are revisions, not separate orders. The newest explicitly-current
    revision is used to reconstruct a missing ledger row; if older databases have no
    current marker, the newest revision is used. Outcomes remain unknown/OPEN.
    """
    columns = {row["name"] for row in c.execute('PRAGMA table_info("prediction_versions")').fetchall()}
    required = {"id", "is_current", "created_at", "trade_date", "symbol", "horizon",
                "base_price", "predicted_pct", "version_type", "feature_json"}
    if not required <= columns:
        return 0
    rows = c.execute("""
        SELECT * FROM prediction_versions
        ORDER BY COALESCE(is_current, 0) DESC, created_at DESC, id DESC
    """).fetchall()
    inserted = 0
    seen = set()
    for row in rows:
        symbol = str(row["symbol"] or "").strip().upper()
        trade_date = str(row["trade_date"] or "")[:10]
        try:
            horizon = int(row["horizon"])
            base_price = float(row["base_price"])
            predicted_pct = float(row["predicted_pct"])
            datetime.fromisoformat(trade_date)
        except (TypeError, ValueError):
            continue
        key = (trade_date, symbol, horizon)
        if (not symbol or horizon not in (2, 3, 7) or base_price <= 0
                or not math.isfinite(base_price) or not math.isfinite(predicted_pct) or key in seen):
            continue
        seen.add(key)
        exists = c.execute(
            "SELECT 1 FROM predictions WHERE trade_date=? AND UPPER(symbol)=? AND horizon=? LIMIT 1",
            key,
        ).fetchone()
        if exists:
            continue
        features = {
            "audit_origin": "PREDICTION_VERSION_BACKFILL",
            "version_id": int(row["id"]),
            "version_type": row["version_type"],
            "source_feature_json": row["feature_json"],
            "note": "Khôi phục từ lịch sử phiên bản dự báo; chưa có kết quả quyết toán được lưu trong version.",
        }
        c.execute("""INSERT INTO predictions(
            created_at, trade_date, symbol, horizon, base_price, predicted_pct,
            predicted_action, p_win, signal_score, quality_score, regime,
            ai_provider, ai_model, feature_json, status
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,'OPEN')""", (
            row["created_at"], trade_date, symbol, horizon, base_price, predicted_pct,
            row["predicted_action"], row["p_win"], row["signal_score"], row["quality_score"],
            row["regime"], row["ai_provider"] or "Version history backfill",
            row["ai_model"] or "legacy-version", json.dumps(features, ensure_ascii=False),
        ))
        inserted += 1
    return inserted


def _backfill_predictions_from_snapshots(c: sqlite3.Connection) -> int:
    """Recover auditable forecasts from retained stock snapshots, idempotently.

    A snapshot containing pred_t2/pred_t3/pred_t7 is evidence that a forecast was
    produced at that time. It can be restored into the normalized prediction
    ledger when the corresponding row is missing. This does not invent outcomes;
    T+N remains OPEN until a later completed exchange session is available.
    """
    snapshot_columns = {row["name"] for row in c.execute('PRAGMA table_info("stock_snapshots")').fetchall()}
    required = {"id", "ts", "trade_date", "symbol", "price", "pred_t2", "pred_t3", "pred_t7"}
    if not required <= snapshot_columns:
        return 0
    rows = c.execute("""
        SELECT s.* FROM stock_snapshots s
        JOIN (
            SELECT UPPER(symbol) AS symbol_key, trade_date, MAX(id) AS max_id
            FROM stock_snapshots
            WHERE symbol IS NOT NULL AND trade_date IS NOT NULL
              AND (pred_t2 IS NOT NULL OR pred_t3 IS NOT NULL OR pred_t7 IS NOT NULL)
            GROUP BY UPPER(symbol), trade_date
        ) latest ON latest.max_id=s.id
        ORDER BY s.trade_date, UPPER(s.symbol)
    """).fetchall()
    inserted = 0
    for row in rows:
        symbol = str(row["symbol"] or "").strip().upper()
        trade_date = str(row["trade_date"] or "")[:10]
        try:
            base_price = float(row["price"] or 0)
        except (TypeError, ValueError):
            continue
        if not symbol or len(trade_date) != 10 or not math.isfinite(base_price) or base_price <= 0:
            continue
        try:
            datetime.fromisoformat(trade_date)
        except ValueError:
            continue
        try:
            meta = json.loads(row["metrics_json"] or "{}") if "metrics_json" in row.keys() else {}
        except Exception:
            meta = {}
        if not isinstance(meta, dict):
            meta = {}
        if meta.get("forecast_data_usable") is False:
            continue
        regime = (meta.get("market_regime") or meta.get("regime") or meta.get("market_phase")
                  or row["wyckoff_phase"] or "Unknown")
        for horizon, column in ((2, "pred_t2"), (3, "pred_t3"), (7, "pred_t7")):
            raw_prediction = row[column]
            if raw_prediction is None:
                continue
            try:
                predicted_pct = float(raw_prediction)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(predicted_pct):
                continue
            exists = c.execute(
                "SELECT 1 FROM predictions WHERE trade_date=? AND UPPER(symbol)=? AND horizon=? LIMIT 1",
                (trade_date, symbol, horizon),
            ).fetchone()
            if exists:
                continue
            created_at = str(row["ts"] or trade_date)
            features = {
                "audit_origin": "STOCK_SNAPSHOT_BACKFILL",
                "snapshot_id": int(row["id"]),
                "snapshot_ts": created_at,
                "source_label": row["source_label"] if "source_label" in row.keys() else None,
                "forecast_data_usable": meta.get("forecast_data_usable"),
                "forecast_data_reason": meta.get("forecast_data_reason"),
                "note": "Khôi phục từ forecast đã lưu trong stock_snapshots; chưa có kết quả quyết toán tại thời điểm backfill.",
            }
            c.execute("""INSERT INTO predictions(
                created_at, trade_date, symbol, horizon, base_price, predicted_pct,
                predicted_action, p_win, signal_score, quality_score, regime,
                ai_provider, ai_model, feature_json, status
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,'OPEN')""", (
                created_at, trade_date, symbol, horizon, base_price, predicted_pct,
                row["action_rec"] if "action_rec" in row.keys() else None,
                row["p_win"] if "p_win" in row.keys() else None,
                row["signal_score"] if "signal_score" in row.keys() else None,
                row["quality_score"] if "quality_score" in row.keys() else None,
                str(regime), "Snapshot backfill", "legacy-snapshot", json.dumps(features, ensure_ascii=False),
            ))
            inserted += 1
    return inserted


def init_db():
    """Create/migrate the configured database and seed the admin idempotently."""
    with _DB_BOOTSTRAP_LOCK:
        with connect() as c:
            c.executescript(SCHEMA)
            # Serialize cross-session/process migrations and the one-time Admin seed.
            c.execute("BEGIN IMMEDIATE")
            _migrate_legacy_columns(c)
            _backfill_legacy_values(c)
            _backfill_flow_provenance(c)
            _backfill_predictions_from_versions(c)
            _backfill_predictions_from_snapshots(c)
            _backfill_prediction_versions(c)
            # Some very old tables do not have an id column. Their non-key data is
            # preserved; indexes that require id are skipped rather than aborting
            # the entire database bootstrap.
            _create_indexes_safely(c)
            _ensure_users_unique_username(c)
            c.execute("UPDATE users SET status='APPROVED' WHERE status IS NULL")
            admin_state = _initialize_admin(c)
            health = _schema_health(c)
            if not health["healthy"]:
                raise sqlite3.DatabaseError(
                    "Database migration chưa hoàn tất: " + json.dumps(health, ensure_ascii=False)
                )
            _meta_set(c, "db_schema_version", DB_SCHEMA_VERSION)
            _meta_set(c, "db_path", str(DB_PATH.expanduser().resolve()))
        return {"db_path": str(DB_PATH.expanduser().resolve()),
                "schema_version": DB_SCHEMA_VERSION,
                "admin_created": admin_state["created"],
                "admin_password_configured": admin_state["password_configured"],
                "admin_setup_required": admin_state["setup_required"],
                "status": "READY"}


def ensure_database_ready() -> dict:
    """Uncached, cheap readiness gate for Streamlit app startup.

    It checks the resolved database file itself on every script rerun. If the file
    was replaced, is empty, has an old schema version, or is missing any declared
    table/column, the idempotent migration runs before app modules are imported.
    This prevents Streamlit cache reuse from silently bypassing bootstrap.
    """
    with _DB_BOOTSTRAP_LOCK:
        path = DB_PATH.expanduser().resolve()
        existed = path.is_file() and path.stat().st_size > 0
        needs_bootstrap = not existed
        if existed:
            try:
                with connect() as c:
                    health = _schema_health(c)
                    table_names = {
                        row["name"] for row in c.execute(
                            "SELECT name FROM sqlite_master WHERE type='table'"
                        ).fetchall()
                    }
                    version = _meta_get(c, "db_schema_version") if "app_meta" in table_names else None
                    admin_exists = False
                    if "users" in table_names:
                        user_cols = {row["name"] for row in c.execute('PRAGMA table_info("users")').fetchall()}
                        if "username" in user_cols:
                            admin_exists = c.execute(
                                "SELECT 1 FROM users WHERE LOWER(username)='admin' LIMIT 1"
                            ).fetchone() is not None
                    reset_password = _admin_reset_password()
                    reset_pending = False
                    if reset_password and "app_meta" in table_names:
                        previous_hash = _meta_get(c, "admin_reset_secret_hash")
                        reset_pending = not previous_hash or not _verify_pw(reset_password, previous_hash)[0]
                    elif reset_password:
                        reset_pending = True
                    needs_bootstrap = (
                        not health["healthy"] or version != DB_SCHEMA_VERSION
                        or not admin_exists or reset_pending
                    )
            except sqlite3.DatabaseError:
                needs_bootstrap = True
        result = init_db() if needs_bootstrap else {
            "db_path": str(path), "schema_version": DB_SCHEMA_VERSION,
            "admin_created": False, "admin_password_configured": False,
            "admin_setup_required": False, "status": "READY"}
        # Re-check after every migration. Never allow the dashboard to run against
        # a half-created or wrong-path SQLite file.
        with connect() as c:
            health = _schema_health(c)
            if not health["healthy"]:
                raise sqlite3.DatabaseError(
                    "Database không đạt schema contract sau bootstrap: "
                    + json.dumps(health, ensure_ascii=False)
                )
            row = c.execute("SELECT value FROM app_meta WHERE key='db_schema_version'").fetchone()
            if not row or row["value"] != DB_SCHEMA_VERSION:
                raise sqlite3.DatabaseError("DB schema version chưa được ghi nhận sau bootstrap")
            result["admin_setup_required"] = _meta_get(c, "admin_bootstrap_status") == "RESET_REQUIRED"
        return result

def register_user(username: str, password: str, full_name: str = "") -> tuple[bool, str]:
    _ensure_users_schema_compatibility()
    username = username.strip().lower()
    if not username or len(username) < 3:
        return False, "Tên đăng nhập phải có ít nhất 3 ký tự."
    if not password or len(password) < 6:
        return False, "Mật khẩu phải có ít nhất 6 ký tự."
    try:
        with connect() as c:
            exists = c.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
            if exists:
                return False, "Tên đăng nhập này đã tồn tại."
            now_str = datetime.now(timezone.utc).isoformat()
            c.execute(
                "INSERT INTO users(username, password_hash, full_name, role, status, cash_balance, created_at) VALUES (?,?,?,?,?,?,?)",
                (username, _hash_pw(password), full_name.strip() or username, "USER", "PENDING", 0.0, now_str)
            )
        return True, "Đăng ký thành công! Tài khoản đang chờ Quản trị viên (Admin) phê duyệt."
    except Exception as e:
        return False, f"Lỗi hệ thống: {e}"

def authenticate_user(username: str, password: str) -> tuple[dict | None, str]:
    _ensure_users_schema_compatibility()
    username = username.strip().lower()
    with connect() as c:
        row = c.execute(
            "SELECT id, username, full_name, role, status, cash_balance, password_hash FROM users WHERE username = ?",
            (username,)
        ).fetchone()
        if not row:
            return None, "Sai tên đăng nhập hoặc mật khẩu!"
        valid, needs_upgrade = _verify_pw(password, row["password_hash"])
        if not valid:
            return None, "Sai tên đăng nhập hoặc mật khẩu!"
        user_dict = dict(row)
        if needs_upgrade or not str(row["password_hash"]).startswith("pbkdf2_sha256$"):
            c.execute("UPDATE users SET password_hash=? WHERE id=?", (_hash_pw(password), row["id"]))
        user_dict.pop("password_hash", None)
        st_code = str(user_dict.get("status") or "APPROVED").upper()
        if st_code == "PENDING" and user_dict.get("role") != "ADMIN":
            return None, "⏳ Tài khoản của bạn đang chờ Admin phê duyệt. Vui lòng liên hệ Quản trị viên!"
        if st_code == "REJECTED" and user_dict.get("role") != "ADMIN":
            return None, "🚫 Tài khoản của bạn đã bị khóa hoặc từ chối quyền truy cập."
        return user_dict, "OK"

def _ensure_users_schema_compatibility() -> None:
    """Repair essential legacy users columns even when Streamlit init cache is stale.

    This is deliberately narrow and idempotent: it only adds absent columns,
    preserves existing account rows and never recreates or overwrites credentials.
    """
    with connect() as c:
        c.execute(
            """CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                full_name TEXT,
                role TEXT DEFAULT 'USER',
                status TEXT DEFAULT 'APPROVED',
                cash_balance REAL DEFAULT 0.0,
                created_at TEXT
            )"""
        )
        existing = {row["name"] for row in c.execute('PRAGMA table_info("users")').fetchall()}
        migrations = {
            "username": "TEXT",
            "password_hash": "TEXT",
            "full_name": "TEXT",
            "role": "TEXT DEFAULT 'USER'",
            "status": "TEXT DEFAULT 'APPROVED'",
            "cash_balance": "REAL DEFAULT 0.0",
            "created_at": "TEXT",
        }
        for column, definition in migrations.items():
            if column not in existing:
                c.execute(f'ALTER TABLE users ADD COLUMN "{column}" {definition}')
                existing.add(column)
        required = {"id", "username", "full_name", "role", "status", "cash_balance", "created_at"}
        missing = required - existing
        if missing:
            raise sqlite3.DatabaseError(
                "users schema is incompatible; missing columns: " + ", ".join(sorted(missing))
            )
        c.execute("UPDATE users SET status='APPROVED' WHERE status IS NULL")
        _ensure_users_unique_username(c)

def list_all_users() -> pd.DataFrame:
    # Defensive repair covers deployments where Streamlit cached the previous
    # init_db() result under an unchanged cache key.
    _ensure_users_schema_compatibility()
    return query("SELECT id, username, full_name, role, status, cash_balance, created_at FROM users ORDER BY id DESC")

def update_user_status(user_id: int, new_status: str, new_role: str | None = None) -> bool:
    try:
        with connect() as c:
            if new_role:
                c.execute("UPDATE users SET status = ?, role = ? WHERE id = ? AND username != 'admin'", (new_status, new_role, int(user_id)))
            else:
                c.execute("UPDATE users SET status = ? WHERE id = ? AND username != 'admin'", (new_status, int(user_id)))
        return True
    except Exception:
        return False

def delete_user_account(user_id: int) -> bool:
    try:
        with connect() as c:
            # SQLite FK enforcement is not enabled globally in legacy deployments, so
            # clean per-user preferences explicitly before deleting the account.
            c.execute("DELETE FROM user_preferences WHERE user_id = ? AND user_id NOT IN (SELECT id FROM users WHERE username='admin')", (int(user_id),))
            c.execute("DELETE FROM user_portfolios WHERE user_id = ? AND user_id NOT IN (SELECT id FROM users WHERE username='admin')", (int(user_id),))
            c.execute("DELETE FROM users WHERE id = ? AND username != 'admin'", (int(user_id),))
        return True
    except Exception:
        return False

def _ensure_auth_sessions_schema_compatibility() -> set[str]:
    """Repair legacy account/session schemas before session-cookie queries.

    Streamlit may reuse a cached init_db() result across deployments. Session
    restoration therefore performs its own idempotent, additive schema check.
    Existing account rows and password hashes are never rewritten. Legacy
    sessions without a trustworthy created_at timestamp are invalidated by
    get_user_by_session() and require a fresh login.
    """
    _ensure_users_schema_compatibility()
    with connect() as c:
        c.execute(
            """CREATE TABLE IF NOT EXISTS auth_sessions (
                token_hash TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            )"""
        )
        existing = {
            row["name"]
            for row in c.execute('PRAGMA table_info("auth_sessions")').fetchall()
        }
        migrations = {
            "token_hash": "TEXT",
            "user_id": "INTEGER",
            "created_at": "TEXT",
        }
        for column, definition in migrations.items():
            if column not in existing:
                c.execute(
                    f'ALTER TABLE "auth_sessions" ADD COLUMN "{column}" {definition}'
                )
                existing.add(column)
        required = {"token_hash", "user_id", "created_at"}
        missing = required - existing
        if missing:
            raise sqlite3.DatabaseError(
                "auth_sessions schema is incompatible; missing columns: "
                + ", ".join(sorted(missing))
            )
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_auth_sessions_user_created "
            "ON auth_sessions(user_id, created_at)"
        )
    return existing


def create_auth_session(user_id: int) -> str:
    _ensure_auth_sessions_schema_compatibility()
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    now_utc = datetime.now(timezone.utc)
    cutoff_iso = (now_utc - timedelta(days=SESSION_TTL_DAYS)).isoformat()
    with connect() as c:
        c.execute("DELETE FROM auth_sessions WHERE created_at < ?", (cutoff_iso,))
        c.execute(
            "INSERT INTO auth_sessions(token_hash, user_id, created_at) VALUES (?,?,?)",
            (token_hash, int(user_id), now_utc.isoformat()),
        )
    return token

def get_user_by_session(token: str) -> dict | None:
    if not token or len(token) > 256:
        return None
    _ensure_auth_sessions_schema_compatibility()
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with connect() as c:
        row = c.execute(
            """SELECT u.id, u.username, u.full_name, u.role, u.status, u.cash_balance, s.created_at AS session_created_at
               FROM auth_sessions s JOIN users u ON u.id=s.user_id
               WHERE s.token_hash=? LIMIT 1""",
            (token_hash,),
        ).fetchone()
        if not row:
            return None
        try:
            created_raw = row["session_created_at"]
            if not created_raw:
                raise ValueError("session timestamp is missing")
            created_dt = datetime.fromisoformat(str(created_raw).replace("Z", "+00:00"))
            if created_dt.tzinfo is None:
                created_dt = created_dt.replace(tzinfo=timezone.utc)
            age = datetime.now(timezone.utc) - created_dt
            if age < -timedelta(minutes=5) or age > timedelta(days=SESSION_TTL_DAYS):
                c.execute("DELETE FROM auth_sessions WHERE token_hash=?", (token_hash,))
                return None
        except (TypeError, ValueError, OverflowError):
            # A session without a parseable creation time cannot be safely
            # trusted for a persistent login. Invalidate it and require login.
            c.execute("DELETE FROM auth_sessions WHERE token_hash=?", (token_hash,))
            return None
    if str(row["status"] or "APPROVED").upper() not in ("APPROVED", "ACTIVE"):
        return None
    out = dict(row)
    out.pop("session_created_at", None)
    return out

def revoke_auth_session(token: str) -> None:
    if not token or len(token) > 256:
        return
    _ensure_auth_sessions_schema_compatibility()
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with connect() as c:
        c.execute("DELETE FROM auth_sessions WHERE token_hash=?", (token_hash,))

def get_user_portfolio(user_id: int) -> list[dict]:
    with connect() as c:
        rows = c.execute(
            "SELECT symbol, buy_price, volume FROM user_portfolios WHERE user_id = ? ORDER BY id ASC",
            (int(user_id),)
        ).fetchall()
        return [dict(r) for r in rows]

def save_user_portfolio(user_id: int, portfolio_items: list[dict], cash_balance: float):
    now_str = datetime.now(timezone.utc).isoformat()
    with connect() as c:
        c.execute("UPDATE users SET cash_balance = ? WHERE id = ?", (float(cash_balance), int(user_id)))
        c.execute("DELETE FROM user_portfolios WHERE user_id = ?", (int(user_id),))
        valid_rows = [
            (int(user_id), str(it["symbol"]).strip().upper(), float(it["buy_price"]), int(it["volume"]), now_str)
            for it in portfolio_items
            if str(it.get("symbol", "")).strip() and int(it.get("volume", 0)) > 0
        ]
        if valid_rows:
            c.executemany(
                "INSERT INTO user_portfolios(user_id, symbol, buy_price, volume, updated_at) VALUES (?,?,?,?,?)",
                valid_rows
            )

def _decode_preference_value(raw_value: str):
    """Decode JSON-backed preference values while tolerating legacy plain text."""
    try:
        return json.loads(raw_value)
    except (json.JSONDecodeError, TypeError, ValueError):
        return raw_value


def get_user_preferences(user_id: int) -> dict:
    """Return all preferences owned by one user ID, decoding JSON values."""
    uid = int(user_id)
    if uid <= 0:
        return {}
    with connect() as c:
        rows = c.execute(
            "SELECT preference_key, preference_value FROM user_preferences WHERE user_id = ?",
            (uid,),
        ).fetchall()
    return {str(row["preference_key"]): _decode_preference_value(row["preference_value"]) for row in rows}


def get_user_preference(user_id: int, preference_key: str, default=None):
    """Read one preference scoped by the authenticated user's database ID."""
    uid = int(user_id)
    key = str(preference_key or "").strip()
    if uid <= 0 or not key:
        return default
    with connect() as c:
        row = c.execute(
            "SELECT preference_value FROM user_preferences WHERE user_id = ? AND preference_key = ? LIMIT 1",
            (uid, key),
        ).fetchone()
    if row is None:
        return default
    return _decode_preference_value(row["preference_value"])


def save_user_preference(user_id: int, preference_key: str, value) -> None:
    """Insert/update a JSON-serializable preference for one user only."""
    uid = int(user_id)
    key = str(preference_key or "").strip()
    if uid <= 0:
        raise ValueError("user_id must be a positive database ID")
    if not key or len(key) > 100:
        raise ValueError("preference_key must contain 1-100 characters")
    try:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("preference value must be JSON serializable") from exc
    now = datetime.now(timezone.utc).isoformat()
    with connect() as c:
        exists = c.execute("SELECT 1 FROM users WHERE id = ?", (uid,)).fetchone()
        if exists is None:
            raise ValueError("user_id does not exist")
        c.execute(
            "INSERT INTO user_preferences(user_id, preference_key, preference_value, updated_at) "
            "VALUES (?,?,?,?) ON CONFLICT(user_id, preference_key) DO UPDATE SET "
            "preference_value=excluded.preference_value, updated_at=excluded.updated_at",
            (uid, key, encoded, now),
        )


def delete_user_preference(user_id: int, preference_key: str) -> None:
    """Delete a single preference without affecting other users."""
    uid = int(user_id)
    key = str(preference_key or "").strip()
    if uid <= 0 or not key:
        return
    with connect() as c:
        c.execute(
            "DELETE FROM user_preferences WHERE user_id = ? AND preference_key = ?",
            (uid, key),
        )


def query(sql, params=()):
    with connect() as c:
        return pd.read_sql_query(sql, c, params=params)

def event(level, component, message, extra=None):
    try:
        full_msg = str(message)
        if extra is not None:
            try:
                extra_str = json.dumps(extra, ensure_ascii=False, default=str)
            except Exception:
                extra_str = str(extra)
            full_msg = f"{full_msg} | {extra_str}"
        with connect() as c:
            c.execute(
                'INSERT INTO system_events(ts,level,component,message) VALUES (?,?,?,?)',
                (datetime.now(timezone.utc).isoformat(), str(level), str(component), full_msg[:900])
            )
    except Exception:
        pass
