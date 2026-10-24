import json
from datetime import datetime

import pandas as pd

from quants import db, engine
from quants.calendar_vn import is_trading_day, trading_day_sql_filter


def test_weekend_and_configured_holiday_are_not_trading_days(monkeypatch):
    monkeypatch.setenv('QUANTS_NON_TRADING_DATES', '2026-10-26,invalid')
    assert is_trading_day(datetime(2026, 10, 23))  # Friday
    assert not is_trading_day(datetime(2026, 10, 24))  # Saturday
    assert not is_trading_day(datetime(2026, 10, 25))  # Sunday
    assert not is_trading_day(datetime(2026, 10, 26))  # configured exchange holiday
    assert "'2026-10-26'" in trading_day_sql_filter('trade_date')


def test_persist_skips_weekend_and_configured_holiday(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'weekend.sqlite3')
    db.init_db()
    monkeypatch.setenv('QUANTS_NON_TRADING_DATES', '2026-10-26')

    engine._persist([], datetime(2026, 10, 24, 10, 0))
    engine._persist([], datetime(2026, 10, 26, 10, 0))
    with db.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM stock_snapshots').fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM flow_snapshots').fetchone()[0] == 0


def test_delta_open_flow_unknown_without_baseline(monkeypatch):
    monkeypatch.setattr(engine, 'query', lambda *args, **kwargs: pd.DataFrame())
    assert engine._delta_open_flow('2026-10-26', 'NEW', 2.4) is None
    assert engine._delta_open_flow('2026-10-26', 'NEW', None) is None


def test_weekend_rows_do_not_pollute_foreign_or_flow_history(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'history.sqlite3')
    db.init_db()
    with db.connect() as conn:
        for day, foreign, flow in [
            ('2026-10-23', 10, 1.0),  # Friday trading session
            ('2026-10-24', 999, 100.0),  # stale weekend refresh
            ('2026-10-25', 888, 1000.0),  # stale weekend refresh
        ]:
            conn.execute(
                'INSERT INTO stock_snapshots(ts,trade_date,symbol,foreign_net_today,metrics_json) VALUES(?,?,?,?,?)',
                (day, day, 'ABC', foreign, json.dumps({'bottom_score': 40, 'top_score': 30, 'support': 90, 'resistance': 110})),
            )
            conn.execute(
                'INSERT INTO flow_snapshots(ts,trade_date,symbol,net_flow_billion) VALUES(?,?,?,?)',
                (day, day, 'ABC', flow),
            )

    assert engine._foreign_5d('ABC', 1, trade_date='2026-10-26') == 11
    monkeypatch.setattr(engine, 'summarize_flow_history', lambda current, prior: {'current': current, 'prior': prior})
    result = engine._historical_flow_features('ABC', '2026-10-26', 2.0)
    assert result == {'current': 2.0, 'prior': [1.0]}

    memory = engine._historical_reversal_memory('ABC', '2026-10-26', 95, 90, 110)
    assert memory['historical_bottom_peak_20d'] == 40
    assert memory['historical_top_peak_20d'] == 30
