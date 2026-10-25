from __future__ import annotations
import hashlib, hmac, sqlite3, re, os, secrets, json
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
    buy_vol INTEGER, sell_vol INTEGER, price REAL, volume INTEGER, is_real_deal INTEGER
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
    lai_intent TEXT, warning TEXT, analyzed_by TEXT, sentiment_by_symbol TEXT
);
CREATE TABLE IF NOT EXISTS media_symbol_daily(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recorded_at TEXT NOT NULL, symbol TEXT NOT NULL, article_count INTEGER DEFAULT 0,
    pr_level TEXT, retail_buzz TEXT, sentiment TEXT, lai_intent TEXT, warning TEXT
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
'''

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

def _admin_reset_password() -> str | None:
    value = os.getenv("QUANTS_ADMIN_RESET_PASSWORD", "").strip()
    if value:
        return value
    try:
        import streamlit as st
        value = str(st.secrets.get("QUANTS_ADMIN_RESET_PASSWORD", "")).strip()
        return value or None
    except Exception:
        return None

def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(
        str(DB_PATH), timeout=30, check_same_thread=False, factory=_ClosingConnection
    )
    c.row_factory = sqlite3.Row
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
    for table, expected_columns in _schema_columns(SCHEMA).items():
        existing = {row["name"] for row in c.execute(f'PRAGMA table_info("{table}")').fetchall()}
        for column, sql_type in expected_columns.items():
            if column == "id" or column in existing or (table == "users" and column == "status"):
                continue
            c.execute(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {sql_type}')
            existing.add(column)

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

def init_db():
    with connect() as c:
        c.executescript(SCHEMA)
        _migrate_legacy_columns(c)
        _backfill_legacy_values(c)

        c.executescript("""
            CREATE INDEX IF NOT EXISTS idx_snap_sym_date ON stock_snapshots(symbol, trade_date, id);
            CREATE INDEX IF NOT EXISTS idx_flow_sym_date ON flow_snapshots(symbol, trade_date, id);
            CREATE INDEX IF NOT EXISTS idx_pred_status ON predictions(status, horizon, trade_date);
            CREATE INDEX IF NOT EXISTS idx_user_port ON user_portfolios(user_id);
            CREATE INDEX IF NOT EXISTS idx_media_daily_symbol_time ON media_symbol_daily(symbol, recorded_at);
            CREATE INDEX IF NOT EXISTS idx_predictions_symbol_horizon_date ON predictions(symbol, horizon, trade_date);
        """)

        cols = [r["name"] for r in c.execute("PRAGMA table_info(users)").fetchall()]
        if "status" not in cols:
            c.execute("ALTER TABLE users ADD COLUMN status TEXT DEFAULT 'APPROVED'")
        c.execute("UPDATE users SET status = 'APPROVED' WHERE status IS NULL")

        cur = c.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()
        created_admin = cur is None
        if created_admin:
            now_str = datetime.now(timezone.utc).isoformat()
            default_admin_pw = os.getenv("QUANTS_INITIAL_ADMIN_PASSWORD", "").strip() or _admin_reset_password()
            if not default_admin_pw:
                # Never ship a known/default credential. The operator can set
                # QUANTS_ADMIN_RESET_PASSWORD and rerun init_db to set a usable password.
                default_admin_pw = secrets.token_urlsafe(32)
            c.execute(
                "INSERT INTO users(username, password_hash, full_name, role, status, cash_balance, created_at) VALUES (?,?,?,?,?,?,?)",
                ("admin", _hash_pw(default_admin_pw), "Quản Trị Viên (Admin)", "ADMIN", "APPROVED", 500000000.0, now_str)
            )
            cur = c.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()
            if not os.getenv("QUANTS_INITIAL_ADMIN_PASSWORD", "").strip() and not _admin_reset_password():
                c.execute(
                    "INSERT INTO system_events(ts,level,component,message) VALUES (?,?,?,?)",
                    (now_str, "WARN", "auth", "Admin được khởi tạo với mật khẩu ngẫu nhiên không công bố. Đặt QUANTS_ADMIN_RESET_PASSWORD rồi khởi động lại để thiết lập mật khẩu quản trị.")
                )
        else:
            c.execute("UPDATE users SET role='ADMIN', status='APPROVED' WHERE username='admin'")

        reset_password = _admin_reset_password()
        if reset_password:
            c.execute("UPDATE users SET password_hash = ? WHERE username = 'admin'", (_hash_pw(reset_password),))

        admin_id = cur["id"]
        portfolio_count = c.execute("SELECT COUNT(*) FROM user_portfolios WHERE user_id = ?", (admin_id,)).fetchone()[0]
        if created_admin and portfolio_count == 0:
            now_str = datetime.now(timezone.utc).isoformat()
            c.executemany(
                "INSERT INTO user_portfolios(user_id, symbol, buy_price, volume, updated_at) VALUES (?,?,?,?,?)",
                [
                    (admin_id, "IDC", 33440.0, 4500, now_str),
                    (admin_id, "FPT", 62980.0, 2000, now_str)
                ]
            )

def register_user(username: str, password: str, full_name: str = "") -> tuple[bool, str]:
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

def list_all_users() -> pd.DataFrame:
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
            c.execute("DELETE FROM user_portfolios WHERE user_id = ? AND user_id NOT IN (SELECT id FROM users WHERE username='admin')", (int(user_id),))
            c.execute("DELETE FROM users WHERE id = ? AND username != 'admin'", (int(user_id),))
        return True
    except Exception:
        return False

def create_auth_session(user_id: int) -> str:
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
            created_dt = datetime.fromisoformat(str(row["session_created_at"]).replace("Z", "+00:00"))
            if created_dt.tzinfo is None:
                created_dt = created_dt.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - created_dt > timedelta(days=SESSION_TTL_DAYS):
                c.execute("DELETE FROM auth_sessions WHERE token_hash=?", (token_hash,))
                return None
        except Exception:
            pass
    if str(row["status"] or "APPROVED").upper() not in ("APPROVED", "ACTIVE"):
        return None
    out = dict(row)
    out.pop("session_created_at", None)
    return out

def revoke_auth_session(token: str) -> None:
    if not token or len(token) > 256:
        return
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
