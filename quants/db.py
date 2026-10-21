from __future__ import annotations
import json, sqlite3, hashlib
from datetime import datetime
from pathlib import Path
from contextlib import contextmanager
from .config import DB_PATH

SCHEMA = '''
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- 1. BẢNG QUẢN LÝ NGƯỜI DÙNG
CREATE TABLE IF NOT EXISTS users(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    full_name TEXT,
    role TEXT DEFAULT 'USER',
    cash_balance REAL DEFAULT 0,
    created_at TEXT
);

-- 2. BẢNG QUẢN LÝ DANH MỤC RIÊNG CỦA TỪNG USER
CREATE TABLE IF NOT EXISTS user_portfolios(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    buy_price REAL NOT NULL,
    volume INTEGER NOT NULL,
    updated_at TEXT,
    UNIQUE(user_id, symbol),
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS stocks(symbol TEXT PRIMARY KEY, sector TEXT NOT NULL, benchmark_ma20_vol INTEGER DEFAULT 5000000, weekly_trend TEXT DEFAULT 'Chưa xác định', weekly_ma_tag TEXT, weekly_support REAL, weekly_resistance REAL, weekly_bias TEXT DEFAULT 'NEUTRAL', updated_at TEXT);
CREATE TABLE IF NOT EXISTS market_snapshots(id INTEGER PRIMARY KEY, recorded_at TEXT NOT NULL, provider TEXT, price REAL, ref_price REAL, change_pct REAL, volume INTEGER, value_billion REAL, foreign_net INTEGER, regime TEXT, raw_json TEXT);
CREATE TABLE IF NOT EXISTS stock_snapshots(id INTEGER PRIMARY KEY, recorded_at TEXT NOT NULL, symbol TEXT NOT NULL, sector TEXT, price REAL, ref_price REAL, change_pct REAL, vwap REAL, vwap_position TEXT, volume INTEGER, projected_volume INTEGER, vol_ratio REAL, buy_vol INTEGER, sell_vol INTEGER, mb_ratio REAL, net_flow_billion REAL, delta_flow_open REAL, quality_score REAL, signal_score REAL, score_vs_sector REAL, agreement_pct REAL, wyckoff_phase TEXT, is_real_deal INTEGER, foreign_net_today INTEGER, foreign_net_5d INTEGER, source_label TEXT, divergence_pct REAL, metrics_json TEXT);
CREATE INDEX IF NOT EXISTS ix_stock_snapshots_symbol_time ON stock_snapshots(symbol, recorded_at);
CREATE TABLE IF NOT EXISTS sector_snapshots(id INTEGER PRIMARY KEY, recorded_at TEXT, sector_name TEXT, net_flow_billion REAL, delta_flow REAL, flow_intensity REAL, market_share_pct REAL, avg_price_change REAL, leader_symbol TEXT, flow_status TEXT, rank_score REAL);
CREATE TABLE IF NOT EXISTS media_articles(id INTEGER PRIMARY KEY, published_at TEXT, fetched_at TEXT, source TEXT, title TEXT, url TEXT UNIQUE, summary TEXT, symbols_json TEXT, sentiment TEXT, pr_level TEXT, retail_buzz TEXT, lai_intent TEXT, warning TEXT, raw_text TEXT);
CREATE INDEX IF NOT EXISTS ix_media_published ON media_articles(published_at);
CREATE TABLE IF NOT EXISTS media_symbol_daily(id INTEGER PRIMARY KEY, recorded_at TEXT, symbol TEXT, article_count INTEGER, pr_level TEXT, retail_buzz TEXT, sentiment TEXT, lai_intent TEXT, warning TEXT, UNIQUE(recorded_at,symbol));
CREATE TABLE IF NOT EXISTS macro_snapshots(id INTEGER PRIMARY KEY, recorded_at TEXT, factor TEXT, value REAL, change_pct REAL, source TEXT, impact TEXT, confidence TEXT, details_json TEXT);
CREATE TABLE IF NOT EXISTS predictions(id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, session_date TEXT NOT NULL, symbol TEXT NOT NULL, base_price REAL NOT NULL, regime TEXT, provider TEXT, p_win REAL, signal_score REAL, quality REAL, agreement REAL, conviction INTEGER, prediction_status TEXT, pred_t2 REAL, target_t2 TEXT, actual_t2 REAL, pred_t3 REAL, target_t3 TEXT, actual_t3 REAL, pred_t7 REAL, target_t7 TEXT, actual_t7 REAL, ai_provider TEXT, ai_model TEXT, ai_vsa TEXT, ai_action TEXT, key_levels TEXT, input_json TEXT, model_version TEXT, UNIQUE(session_date,symbol));
CREATE INDEX IF NOT EXISTS ix_predictions_symbol ON predictions(symbol,created_at);
CREATE TABLE IF NOT EXISTS flow_snapshots(id INTEGER PRIMARY KEY, recorded_at TEXT, session_date TEXT, symbol TEXT, net_flow_billion REAL, source TEXT, buy_vol INTEGER, sell_vol INTEGER, UNIQUE(recorded_at,symbol));
CREATE TABLE IF NOT EXISTS model_performance(id INTEGER PRIMARY KEY, calculated_at TEXT, horizon TEXT, regime_group TEXT, sample_count INTEGER, win_rate_pct REAL, mae_pct REAL, avg_return_pct REAL, bias_pct REAL, coverage_pct REAL);
CREATE TABLE IF NOT EXISTS system_events(id INTEGER PRIMARY KEY, recorded_at TEXT, level TEXT, component TEXT, message TEXT, details_json TEXT);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT, updated_at TEXT);
'''

@contextmanager
def connect():
    path = Path(DB_PATH); path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), timeout=30)
    con.row_factory = sqlite3.Row
    try:
        con.execute('PRAGMA foreign_keys=ON;')
        yield con
        con.commit()
    finally: con.close()

def init_db():
    with connect() as c:
        c.executescript(SCHEMA)
        c.execute("INSERT OR IGNORE INTO stocks(symbol,sector) VALUES (?,?)", ('VNINDEX','Chỉ số thị trường'))
        # Tạo tài khoản quản trị mặc định (admin / admin123) nếu chưa có
        default_admin_hash = hash_pw("admin123")
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        c.execute(
            "INSERT OR IGNORE INTO users(username, password_hash, full_name, role, cash_balance, created_at) "
            "VALUES(?,?,?,?,?,?)",
            ("admin", default_admin_hash, "Quản Trị Viên", "ADMIN", 0.0, now_str)
        )

def event(level, component, message, details=None):
    with connect() as c:
        c.execute('INSERT INTO system_events(recorded_at,level,component,message,details_json) VALUES(datetime(\'now\'),?,?,?,?)', (level,component,message,json.dumps(details or {},ensure_ascii=False,default=str)))

def query(sql, params=()):
    import pandas as pd
    with connect() as c: return pd.read_sql_query(sql,c,params=params)

def execute(sql, params=()):
    with connect() as c: return c.execute(sql,params).rowcount

def upsert_stock(symbol, sector):
    with connect() as c: c.execute('INSERT INTO stocks(symbol,sector,updated_at) VALUES(?,?,datetime(\'now\')) ON CONFLICT(symbol) DO UPDATE SET sector=excluded.sector,updated_at=excluded.updated_at',(symbol,sector))

# ============================================================================
# CÁC HÀM XÁC THỰC VÀ QUẢN LÝ USER / PORTFOLIO
# ============================================================================
def hash_pw(password: str) -> str:
    salt = "QUANTS_PRO_SECRET_SALT_2026"
    return hashlib.sha256((str(password) + salt).encode('utf-8')).hexdigest()

def register_user(username: str, password: str, full_name: str = "") -> tuple[bool, str]:
    u = str(username or "").strip().lower()
    p = str(password or "").strip()
    if not u or not p:
        return False, "Tên đăng nhập và mật khẩu không được để trống."
    if len(p) < 6:
        return False, "Mật khẩu phải có tối thiểu 6 ký tự."
    
    pw_h = hash_pw(p)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with connect() as c:
            c.execute(
                "INSERT INTO users(username, password_hash, full_name, role, cash_balance, created_at) VALUES(?,?,?,?,?,?)",
                (u, pw_h, full_name.strip() or u, "USER", 0.0, now_str)
            )
        return True, "Đăng ký thành công!"
    except sqlite3.IntegrityError:
        return False, "Tên đăng nhập đã tồn tại trên hệ thống."
    except Exception as e:
        return False, f"Lỗi hệ thống: {e}"

def authenticate_user(username: str, password: str) -> dict | None:
    u = str(username or "").strip().lower()
    pw_h = hash_pw(str(password or "").strip())
    df = query("SELECT id, username, full_name, role, cash_balance FROM users WHERE username=? AND password_hash=?", (u, pw_h))
    if not df.empty:
        return df.iloc[0].to_dict()
    return None

def get_user_portfolio(user_id: int) -> list[dict]:
    df = query("SELECT symbol, buy_price, volume FROM user_portfolios WHERE user_id=? ORDER BY symbol ASC", (user_id,))
    if not df.empty:
        return df.to_dict('records')
    return []

def save_user_portfolio(user_id: int, portfolio_list: list[dict], cash_balance: float):
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with connect() as c:
        c.execute("UPDATE users SET cash_balance=? WHERE id=?", (float(cash_balance), int(user_id)))
        c.execute("DELETE FROM user_portfolios WHERE user_id=?", (int(user_id),))
        for it in portfolio_list:
            sym = str(it.get("symbol", "")).strip().upper()
            bp = float(it.get("buy_price", 0))
            vol = int(it.get("volume", 0))
            if sym and bp > 0 and vol > 0:
                c.execute(
                    "INSERT INTO user_portfolios(user_id, symbol, buy_price, volume, updated_at) VALUES(?,?,?,?,?)",
                    (int(user_id), sym, bp, vol, now_str)
                )
