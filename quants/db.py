from __future__ import annotations
import hashlib, sqlite3
from datetime import datetime, timezone
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
    win_rate_pct REAL, mae_pct REAL, avg_return_pct REAL, bias_pct REAL, notes TEXT
);
CREATE TABLE IF NOT EXISTS media_articles(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url_hash TEXT UNIQUE, published_at TEXT, crawled_at TEXT, source TEXT,
    title TEXT, summary TEXT, url TEXT, symbols TEXT,
    sentiment TEXT, sentiment_score REAL, pr_level TEXT, retail_buzz TEXT,
    lai_intent TEXT, warning TEXT, analyzed_by TEXT
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

def _hash_pw(password: str) -> str:
    return hashlib.sha256(f"quants_v16_salt_{password}".encode("utf-8")).hexdigest()

def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(DB_PATH), timeout=30, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c

def init_db():
    with connect() as c:
        # Create tables first; defer indexes until legacy tables are migrated.
        c.executescript(SCHEMA)

        # CREATE TABLE IF NOT EXISTS does not add columns to existing databases.
        # Older databases may lack trade_date, causing index creation to fail.
        for table in ("stock_snapshots", "flow_snapshots", "predictions"):
            cols = {r["name"] for r in c.execute(f"PRAGMA table_info({table})").fetchall()}
            if "trade_date" not in cols:
                c.execute(f"ALTER TABLE {table} ADD COLUMN trade_date TEXT")

        c.executescript("""
            CREATE INDEX IF NOT EXISTS idx_snap_sym_date ON stock_snapshots(symbol, trade_date, id);
            CREATE INDEX IF NOT EXISTS idx_flow_sym_date ON flow_snapshots(symbol, trade_date, id);
            CREATE INDEX IF NOT EXISTS idx_pred_status ON predictions(status, horizon, trade_date);
            CREATE INDEX IF NOT EXISTS idx_user_port ON user_portfolios(user_id);
        """)

        cols = [r["name"] for r in c.execute("PRAGMA table_info(users)").fetchall()]
        if "status" not in cols:
            c.execute("ALTER TABLE users ADD COLUMN status TEXT DEFAULT 'APPROVED'")
            c.execute("UPDATE users SET status = 'APPROVED' WHERE status IS NULL")

        cur = c.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()
        if not cur:
            now_str = datetime.now(timezone.utc).isoformat()
            c.execute(
                "INSERT INTO users(username, password_hash, full_name, role, status, cash_balance, created_at) VALUES (?,?,?,?,?,?,?)",
                ("admin", _hash_pw("admin123"), "Quản Trị Viên (Admin)", "ADMIN", "APPROVED", 500000000.0, now_str)
            )
            admin_id = c.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()["id"]
            c.executemany(
                "INSERT INTO user_portfolios(user_id, symbol, buy_price, volume, updated_at) VALUES (?,?,?,?,?)",
                [
                    (admin_id, "IDC", 33440.0, 4500, now_str),
                    (admin_id, "FPT", 62980.0, 2000, now_str)
                ]
            )
        else:
            c.execute("UPDATE users SET role='ADMIN', status='APPROVED' WHERE username='admin'")

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
            "SELECT id, username, full_name, role, status, cash_balance FROM users WHERE username = ? AND password_hash = ?",
            (username, _hash_pw(password))
        ).fetchone()
        if not row:
            return None, "Sai tên đăng nhập hoặc mật khẩu! (Tài khoản mẫu: admin / admin123)"
        user_dict = dict(row)
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

def event(level, component, message):
    try:
        with connect() as c:
            c.execute(
                'INSERT INTO system_events(ts,level,component,message) VALUES (?,?,?,?)',
                (datetime.now(timezone.utc).isoformat(), level, component, str(message)[:900])
            )
    except Exception:
        pass
