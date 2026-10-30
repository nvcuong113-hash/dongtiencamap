from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd

from quants import db
from quants import media
from quants import audit


def test_init_db_creates_all_runtime_tables_and_columns(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'quants.sqlite3')
    db.init_db()
    with db.connect() as c:
        tables = {r['name'] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {'stock_snapshots', 'flow_snapshots', 'predictions', 'model_performance',
                'media_articles', 'media_symbol_daily', 'settings', 'users', 'user_portfolios'} <= tables
        media_cols = {r['name'] for r in c.execute('PRAGMA table_info(media_articles)')}
        assert {'url_hash', 'published_at', 'fetched_at', 'symbols', 'symbols_json', 'raw_text'} <= media_cols
        perf_cols = {r['name'] for r in c.execute('PRAGMA table_info(model_performance)')}
        assert {'computed_at', 'horizon', 'coverage_pct'} <= perf_cols
    assert isinstance(media.media_summary(), pd.DataFrame)


def test_legacy_media_schema_is_migrated_without_deleting_rows(tmp_path, monkeypatch):
    path = tmp_path / 'legacy.sqlite3'
    c = sqlite3.connect(path)
    c.execute('CREATE TABLE media_articles (id INTEGER PRIMARY KEY, published_at TEXT, title TEXT, url TEXT, symbols TEXT)')
    c.execute("INSERT INTO media_articles(published_at,title,url,symbols) VALUES('2026-10-22','Legacy headline','https://example.test/a','[\"IDC\"]')")
    c.commit(); c.close()
    monkeypatch.setattr(db, 'DB_PATH', path)
    db.init_db()
    with db.connect() as c:
        row = c.execute('SELECT title,symbols FROM media_articles WHERE id=1').fetchone()
        cols = {r['name'] for r in c.execute('PRAGMA table_info(media_articles)')}
        assert row['title'] == 'Legacy headline'
        assert c.execute('SELECT url_hash FROM media_articles WHERE id=1').fetchone()[0]
        assert 'symbols_json' in cols and 'raw_text' in cols and 'fetched_at' in cols
        assert c.execute('SELECT symbols_json FROM media_articles WHERE id=1').fetchone()[0] == '[\"IDC\"]'
        assert c.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='media_symbol_daily'").fetchone()[0] == 1
    db.init_db()  # migration must be idempotent
    with db.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM media_articles').fetchone()[0] == 1


def test_normalized_prediction_performance_uses_current_schema(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'predictions.sqlite3')
    db.init_db()
    with db.connect() as c:
        c.execute("""INSERT INTO predictions(created_at,trade_date,symbol,horizon,base_price,predicted_pct,p_win,regime,status,actual_price,actual_pct)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                  (datetime.now(timezone.utc).isoformat(), '2026-10-01', 'IDC', 2, 10000, 2.0, 65, 'Bullish Expansion', 'SETTLED', 10200, 2.0))
    results = audit.build_performance()
    assert len(results) == 12
    all_t2 = next(r for r in results if r['horizon'] == 2 and r['regime_group'] == 'All')
    assert all_t2['sample_count'] == 1
    assert all_t2['win_rate_pct'] == 100.0
    with db.connect() as c:
        row = c.execute("SELECT horizon,coverage_pct FROM model_performance WHERE regime_group='All' AND horizon=2 ORDER BY id DESC LIMIT 1").fetchone()
        assert row['horizon'] == 2 and row['coverage_pct'] == 100.0


def test_media_crawl_persists_hash_and_summary(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    from quants import media as media_module

    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'media.sqlite3')
    db.init_db()
    published = datetime.now(timezone.utc).isoformat()
    item = {'source': 'Fixture', 'title': 'IDC công bố kết quả kinh doanh tăng mạnh',
            'url': 'https://example.test/idc-news', 'summary': 'IDC tăng mạnh', 'published_at': published}
    monkeypatch.setattr(media_module, 'fetch_rss', lambda *args, **kwargs: ([item], None))
    count, errors = media_module.crawl_media(['IDC'], hours=48)
    assert count == 1
    assert not errors
    recent = media_module.recent_media(hours=48, symbol='IDC')
    assert len(recent) == 1
    assert recent.iloc[0]['symbols'] == '["IDC"]'
    summary = media_module.media_summary()
    assert not summary.empty
    assert summary.iloc[0]['symbol'] == 'IDC'
    # Re-crawling the same URL must not duplicate article records in legacy schemas.
    media_module.crawl_media(['IDC'], hours=48)
    assert len(media_module.recent_media(hours=48, symbol='IDC')) == 1


def test_settle_predictions_uses_normalized_schema(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'settle.sqlite3')
    db.init_db()
    with db.connect() as c:
        c.execute("""INSERT INTO predictions(created_at,trade_date,symbol,horizon,base_price,predicted_pct,p_win,regime,status)
                     VALUES(?,?,?,?,?,?,?,?,?)""",
                  ('2026-10-01T10:00:00+07:00', '2026-10-01', 'IDC', 2, 10000, 2.0, 65, 'Bullish', 'OPEN'))
    monkeypatch.setattr(audit, 'fetch_history', lambda *args, **kwargs: (
        [{'date': '2026-10-01', 'close': 10000}, {'date': '2026-10-02', 'close': 10100},
         {'date': '2026-10-05', 'close': 10200}], 'fixture', []))
    assert audit.settle_predictions() == 1
    with db.connect() as c:
        row = c.execute("SELECT status,actual_price,actual_pct,outcome,settle_trade_date FROM predictions LIMIT 1").fetchone()
        assert row['status'] == 'SETTLED'
        assert row['actual_price'] == 10200
        assert abs(row['actual_pct'] - 2.0) < 1e-9
        assert row['outcome'] == 'WIN'
        assert row['settle_trade_date'] == '2026-10-05'


def test_snapshot_insert_populates_legacy_required_recorded_at(tmp_path, monkeypatch):
    from quants.engine import _insert_snapshot_compat

    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'legacy_snapshot.sqlite3')
    db.init_db()
    with db.connect() as c:
        c.execute("ALTER TABLE stock_snapshots ADD COLUMN recorded_at TEXT NOT NULL DEFAULT 'legacy-default'")
        ts = '2026-10-23T08:00:00+07:00'
        _insert_snapshot_compat(c, 'stock_snapshots', ['ts', 'symbol'], [ts, 'IDC'])
        row = c.execute("SELECT ts,recorded_at FROM stock_snapshots WHERE symbol='IDC'").fetchone()
        assert row['ts'] == ts
        assert row['recorded_at'] == ts


def test_full_snapshot_persist_handles_legacy_not_null_recorded_at(tmp_path, monkeypatch):
    from quants.engine import _persist

    path = tmp_path / 'legacy_full.sqlite3'
    c = sqlite3.connect(path)
    c.execute('CREATE TABLE stock_snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT, recorded_at TEXT NOT NULL)')
    c.execute('CREATE TABLE flow_snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT, recorded_at TEXT NOT NULL)')
    c.commit(); c.close()
    monkeypatch.setattr(db, 'DB_PATH', path)
    db.init_db()
    row = {
        'trade_date': '2026-10-23',
        'symbol':'IDC','sector':'Bất động sản KCN','price':33400.0,'change_pct':1.2,'volume':100000,
        'ma20_vol':90000.0,'projected_volume':110000.0,'vwap':33200.0,'vwap_position':'Trên VWAP',
        'buy_vol':60000,'sell_vol':40000,'buy_ratio':60.0,'net_flow_billion':0.668,
        'delta_flow_open':0.2,'rs_vni':0.5,'rs_sector':0.3,'foreign_net_today':1000,
        'foreign_net_5d':2000,'quality_score':80.0,'signal_score':75.0,'agreement_pct':80,
        'p_win':70.0,'conviction':'CAO','wyckoff':'SOS / Breakout','action':'QUAN SÁT',
        'ai_vsa':'fixture','ai_action':'QUAN SÁT','pred_t2':1.0,'pred_t3':1.5,'pred_t7':2.0,
        'source_label':'fixture','is_real_deal':True,
    }
    now = datetime(2026, 10, 23, 15, 5, tzinfo=ZoneInfo('Asia/Ho_Chi_Minh'))
    _persist([row], now)
    with db.connect() as c:
        stock = c.execute("SELECT symbol,ts,recorded_at FROM stock_snapshots WHERE symbol='IDC'").fetchone()
        flow = c.execute("SELECT symbol,ts,recorded_at FROM flow_snapshots WHERE symbol='IDC'").fetchone()
        assert stock is not None and flow is not None
        assert stock['recorded_at'] == stock['ts']
        assert flow['recorded_at'] == flow['ts']


def test_learning_status_uses_normalized_predictions_and_settings(tmp_path, monkeypatch):
    from quants.learning import learning_status, train_calibrator

    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'learning.sqlite3')
    db.init_db()
    status = learning_status()
    assert set(status) == {'T+2', 'T+3', 'T+7'}
    assert status['T+2']['matured_samples'] == 0
    result = train_calibrator('T+2', min_samples=1)
    assert result['active'] is False
    assert 'sample_count' in result


def test_prediction_audit_writes_normalized_horizon_rows(tmp_path, monkeypatch):
    from quants.ai_engine import save_prediction_audit

    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'prediction_audit.sqlite3')
    db.init_db()
    rows = [{
        'symbol': 'IDC', 'price': 33400, 'volume': 1000, 'volume_available': True, 'pred_t2': 1.2, 'pred_t3': 1.8, 'pred_t7': 3.0,
        'ai_action': 'QUAN SÁT', 'p_win': 65, 'signal_score': 70, 'quality_score': 80,
        'ai_provider': 'fixture', 'ai_model': 'fixture-model', 'bottom_score': 70,
        'top_score': 30, 'early_signal': 'test', 'trade_date': '2026-10-23',
        'index_valid': True, 'session_current': True
    }]
    assert save_prediction_audit(rows, 'Bullish') == 3
    with db.connect() as c:
        rows_db = c.execute('SELECT horizon,predicted_pct,status FROM predictions ORDER BY horizon').fetchall()
        assert [(r['horizon'], r['predicted_pct'], r['status']) for r in rows_db] == [
            (2, 1.2, 'OPEN'), (3, 1.8, 'OPEN'), (7, 3.0, 'OPEN')]


def test_snapshot_insert_skips_optional_columns_missing_from_runtime_schema(tmp_path, monkeypatch):
    """A lagging/legacy snapshot schema must not crash Streamlit's compute path."""
    from quants.engine import _insert_snapshot_compat

    path = tmp_path / 'partial_snapshot.sqlite3'
    conn = sqlite3.connect(path)
    conn.execute('CREATE TABLE stock_snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, symbol TEXT, recorded_at TEXT NOT NULL)')
    conn.commit(); conn.close()
    monkeypatch.setattr(db, 'DB_PATH', path)
    with db.connect() as c:
        _insert_snapshot_compat(
            c, 'stock_snapshots', ['ts', 'symbol', 'not_in_legacy_schema'],
            ['2026-10-29T10:00:00+07:00', 'IDC', 123],
        )
        row = c.execute('SELECT ts,symbol,recorded_at FROM stock_snapshots').fetchone()
        assert row['ts'] == '2026-10-29T10:00:00+07:00'
        assert row['symbol'] == 'IDC'
        assert row['recorded_at'] == row['ts']


def test_init_db_backfills_audit_ledger_from_historical_stock_snapshots(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'snapshot_backfill.sqlite3')
    db.init_db()
    with db.connect() as c:
        c.execute("""INSERT INTO stock_snapshots(
            ts, trade_date, symbol, price, pred_t2, pred_t3, pred_t7,
            source_label, metrics_json, wyckoff_phase
        ) VALUES(?,?,?,?,?,?,?,?,?,?)""", (
            '2026-10-20T15:00:00+07:00', '2026-10-20', 'IDC', 33000,
            1.2, 2.3, 4.5, 'VNstock-KBS', '{"forecast_data_usable": true}', 'Accumulation'
        ))
    db.init_db()
    with db.connect() as c:
        rows = c.execute("SELECT trade_date,symbol,horizon,base_price,predicted_pct,status,ai_provider FROM predictions ORDER BY horizon").fetchall()
        assert len(rows) == 3
        assert [r['horizon'] for r in rows] == [2, 3, 7]
        assert all(r['status'] == 'OPEN' for r in rows)
        assert all(r['ai_provider'] == 'Snapshot backfill' for r in rows)
        assert [r['predicted_pct'] for r in rows] == [1.2, 2.3, 4.5]
    # A second initialization must not duplicate the recovered forecast rows.
    db.init_db()
    with db.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM predictions WHERE symbol='IDC'").fetchone()[0] == 3


def test_settlement_skips_horizons_not_yet_matured_at_preopen(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'immature.sqlite3')
    db.init_db()
    with db.connect() as c:
        c.execute("""INSERT INTO predictions(created_at,trade_date,symbol,horizon,base_price,predicted_pct,status)
                     VALUES(?,?,?,?,?,?,?)""",
                  ('2026-10-29T15:00:00+07:00', '2026-10-29', 'BSR', 2, 27650, 1.2, 'OPEN'))

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            frozen = datetime(2026, 10, 30, 8, 8, tzinfo=ZoneInfo('Asia/Ho_Chi_Minh'))
            return frozen.astimezone(tz) if tz else frozen.replace(tzinfo=None)

    monkeypatch.setattr(audit, 'datetime', FrozenDateTime)
    monkeypatch.setattr(audit, 'fetch_history', lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError('Không được gọi lịch sử trước khi T+2 hoàn tất')
    ))

    assert audit.settle_predictions() == 0
    with db.connect() as c:
        row = c.execute("SELECT status,actual_pct FROM predictions WHERE symbol='BSR'").fetchone()
        assert row['status'] == 'OPEN'
        assert row['actual_pct'] is None


def test_init_db_recovers_missing_prediction_from_version_history(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'version_backfill.sqlite3')
    db.init_db()
    with db.connect() as c:
        c.execute("""INSERT INTO prediction_versions(
            created_at,trade_date,symbol,horizon,base_price,predicted_pct,
            predicted_action,p_win,signal_score,quality_score,regime,
            ai_provider,ai_model,feature_json,version_hash,is_current,version_type
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
            '2026-10-20T15:00:00+07:00', '2026-10-20', 'BSR', 3, 27000,
            2.5, 'WATCH', 61, 55, 60, 'Sideway', 'Gemini', 'test-model', '{}',
            'test-version-hash-1', 1, 'CURRENT_SNAPSHOT'
        ))
    db.init_db()
    with db.connect() as c:
        row = c.execute("SELECT symbol,horizon,base_price,predicted_pct,status,ai_provider FROM predictions WHERE symbol='BSR'").fetchone()
        assert row is not None
        assert row['horizon'] == 3 and row['base_price'] == 27000
        assert row['predicted_pct'] == 2.5 and row['status'] == 'OPEN'
        assert row['ai_provider'] == 'Gemini'


def test_list_all_users_repairs_legacy_cash_balance_when_startup_cache_is_stale(tmp_path, monkeypatch):
    """Admin panel must self-heal a legacy users table if cached init_db was skipped."""
    path = tmp_path / 'legacy_users_without_cash_balance.sqlite3'
    legacy_hash = db._hash_pw('secure-test-password')
    conn = sqlite3.connect(path)
    conn.execute('''CREATE TABLE users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        full_name TEXT,
        role TEXT DEFAULT 'USER',
        status TEXT DEFAULT 'APPROVED',
        created_at TEXT
    )''')
    conn.execute(
        "INSERT INTO users(username,password_hash,full_name,role,status,created_at) VALUES(?,?,?,?,?,?)",
        ('admin', legacy_hash, 'Admin', 'ADMIN', 'APPROVED', '2026-10-30T00:00:00+00:00'),
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(db, 'DB_PATH', path)

    # Intentionally do NOT call init_db(): this reproduces the stale Streamlit cache path.
    result = db.list_all_users()
    assert len(result) == 1
    assert result.iloc[0]['username'] == 'admin'
    assert result.iloc[0]['cash_balance'] == 0.0

    with db.connect() as c:
        cols = {row['name'] for row in c.execute('PRAGMA table_info(users)').fetchall()}
        stored = c.execute('SELECT username,password_hash FROM users WHERE id=1').fetchone()
        assert 'cash_balance' in cols
        assert stored['username'] == 'admin'
        assert stored['password_hash'] == legacy_hash


def test_authenticate_and_register_repair_legacy_user_columns(tmp_path, monkeypatch):
    path = tmp_path / 'legacy_users_auth.sqlite3'
    conn = sqlite3.connect(path)
    conn.execute('''CREATE TABLE users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        full_name TEXT,
        role TEXT DEFAULT 'USER',
        created_at TEXT
    )''')
    conn.commit()
    conn.close()
    monkeypatch.setattr(db, 'DB_PATH', path)

    created, message = db.register_user('newuser', 'test-password', 'New User')
    assert created, message
    # Register places the account in pending state; authentication remains blocked.
    user, auth_message = db.authenticate_user('newuser', 'test-password')
    assert user is None
    assert 'chờ' in auth_message.lower()
    with db.connect() as c:
        columns = {row['name'] for row in c.execute('PRAGMA table_info(users)').fetchall()}
        assert {'status', 'cash_balance'} <= columns



def test_foreign_5d_repairs_legacy_stock_snapshots_missing_foreign_column(tmp_path, monkeypatch):
    """The exact foreign-flow SELECT must self-heal when startup init_db was cached."""
    from quants.engine import _foreign_5d

    path = tmp_path / 'legacy_stock_snapshot_no_foreign.sqlite3'
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE stock_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT,
        trade_date TEXT,
        symbol TEXT,
        recorded_at TEXT NOT NULL
    )""")
    conn.executemany(
        "INSERT INTO stock_snapshots(ts,trade_date,symbol,recorded_at) VALUES(?,?,?,?)",
        [
            ('2026-10-28T15:00:00+07:00', '2026-10-28', 'IDC', 'legacy-1'),
            ('2026-10-29T15:00:00+07:00', '2026-10-29', 'IDC', 'legacy-2'),
        ],
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(db, 'DB_PATH', path)

    # Deliberately skip db.init_db() to reproduce the stale Streamlit cache case.
    with db.connect() as c:
        total_before_backfill = _foreign_5d(c, 'IDC', 300, trade_date='2026-10-30')
        assert total_before_backfill == 300  # old rows stay unknown, not zero-filled
        columns = {r['name'] for r in c.execute('PRAGMA table_info(stock_snapshots)')}
        assert 'foreign_net_today' in columns
        # Existing legacy rows and values survive the additive migration.
        assert c.execute('SELECT COUNT(*) FROM stock_snapshots').fetchone()[0] == 2
        c.execute("UPDATE stock_snapshots SET foreign_net_today=100 WHERE trade_date='2026-10-28'")
        c.execute("UPDATE stock_snapshots SET foreign_net_today=200 WHERE trade_date='2026-10-29'")
        total = _foreign_5d(c, 'IDC', 300, trade_date='2026-10-30')
        assert total == 600


def test_foreign_5d_repairs_missing_stock_snapshots_table(tmp_path, monkeypatch):
    """A database with no snapshot table must not crash the watchlist compute path."""
    from quants.engine import _foreign_5d

    path = tmp_path / 'missing_stock_snapshot_table.sqlite3'
    conn = sqlite3.connect(path)
    conn.execute('CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT)')
    conn.commit()
    conn.close()
    monkeypatch.setattr(db, 'DB_PATH', path)

    with db.connect() as c:
        assert _foreign_5d(c, 'IDC', 125, trade_date='2026-10-30') == 125
        columns = {r['name'] for r in c.execute('PRAGMA table_info(stock_snapshots)')}
        assert {'symbol', 'trade_date', 'foreign_net_today'} <= columns
        assert c.execute('SELECT COUNT(*) FROM stock_snapshots').fetchone()[0] == 0


def test_stock_snapshot_schema_repair_is_idempotent_and_preserves_unknowns(tmp_path, monkeypatch):
    path = tmp_path / 'snapshot_idempotent.sqlite3'
    conn = sqlite3.connect(path)
    conn.execute('CREATE TABLE stock_snapshots (id INTEGER PRIMARY KEY, symbol TEXT, trade_date TEXT)')
    conn.execute("INSERT INTO stock_snapshots(symbol,trade_date) VALUES('NKG','2026-10-29')")
    conn.commit()
    conn.close()
    monkeypatch.setattr(db, 'DB_PATH', path)

    with db.connect() as c:
        first = db.ensure_stock_snapshots_schema_compatibility(c)
        second = db.ensure_stock_snapshots_schema_compatibility(c)
        assert first == second
        row = c.execute('SELECT symbol,trade_date,foreign_net_today FROM stock_snapshots').fetchone()
        assert row['symbol'] == 'NKG'
        assert row['trade_date'] == '2026-10-29'
        assert row['foreign_net_today'] is None



def test_foreign_5d_supports_legacy_snapshot_table_without_explicit_id(tmp_path, monkeypatch):
    """Use SQLite rowid when a pre-contract snapshot table has no id column."""
    from quants.engine import _foreign_5d

    path = tmp_path / 'legacy_snapshot_without_id.sqlite3'
    conn = sqlite3.connect(path)
    conn.execute('CREATE TABLE stock_snapshots (trade_date TEXT, symbol TEXT)')
    conn.execute("INSERT INTO stock_snapshots(trade_date,symbol) VALUES('2026-10-29','IDC')")
    conn.commit()
    conn.close()
    monkeypatch.setattr(db, 'DB_PATH', path)

    with db.connect() as c:
        assert _foreign_5d(c, 'IDC', 75, trade_date='2026-10-30') == 75
        cols = {r['name'] for r in c.execute('PRAGMA table_info(stock_snapshots)')}
        assert 'id' not in cols
        assert 'foreign_net_today' in cols
