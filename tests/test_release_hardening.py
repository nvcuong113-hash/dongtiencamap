from __future__ import annotations

import hashlib
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from quants import db, engine, ai_engine, audit
from quants.calendar_vn import add_trading_days, is_trading_day
from quants.providers import _quote_from_raw, _value_billion, _vndirect_daily_quote_is_usable


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", Path(tmp_path) / "release-hardening.sqlite3")
    db.init_db()
    return db.DB_PATH


def test_invalid_index_hard_blocks_signal_and_rs_is_not_zero_assumed():
    row = {
        "volume_available": True, "volume": 100_000, "is_real_deal": True,
        "flow_coverage_pct": 70, "ma20_vol": 100_000, "ma20w": 10_000,
        "vwap_position": "Trên VWAP", "rs_vni": None, "vol_ratio": 1.5,
        "buy_ratio": 70, "bottom_score": 70, "top_score": 20,
        "foreign_net_today": 0, "weekly_trend": "Uptrend Tuần",
        "wyckoff": "SOS / Breakout", "change_pct": 2, "structure_available": True,
        "rr_ratio": 2,
    }
    result = engine._score(row, index_valid=False, vni_change=0.0)
    assert result[1] == 0
    assert result[3] is None
    assert result[-1] == "CHỜ DỮ LIỆU VN-INDEX"
    assert row["ensemble_decision_state"] == "INSUFFICIENT_DATA"


def test_missing_structure_does_not_fabricate_stop_target_or_rr():
    result = engine._combat_trading_metrics({
        "price": 25_000, "ma20_vol": 500_000, "history_available": False,
        "support": None, "resistance": None,
    })
    assert result["structure_available"] is False
    assert result["stop_loss_price"] is None
    assert result["target_price"] is None
    assert result["rr_ratio"] is None


def test_proxy_or_low_coverage_flow_cannot_confirm_breakout():
    row = {
        "is_real_deal": True, "flow_coverage_pct": 12,
        "volume_available": True, "volume": 1_000_000, "price": 10_500,
        "high": 10_600, "low": 10_100, "change_pct": 2.2,
        "vol_ratio": 1.4, "buy_ratio": 70, "vwap_position": "Trên VWAP",
        "bottom_score": 60, "top_score": 25, "weekly_trend": "Uptrend Tuần",
        "history_available": True, "support": 9_800, "resistance": 11_000,
    }
    assert not engine._has_confirmed_flow(row)
    assert engine.classify_wyckoff(row) != "SOS / Breakout"


def test_ai_forecasts_reject_nonfinite_and_out_of_range_values():
    assert ai_engine._validated_forecast(float("nan"), 2) is None
    assert ai_engine._validated_forecast(float("inf"), 3) is None
    assert ai_engine._validated_forecast(500, 2) is None
    assert ai_engine._validated_forecast(-500, 7) is None
    assert ai_engine._validated_forecast(7, 2) == 7
    assert ai_engine._validated_forecast(18, 7) == 18
    assert ai_engine._validated_forecast(17, 2) is None
    assert ai_engine._validated_forecast(60, 7) is None


def test_ai_position_size_is_bounded_and_requires_data_confirmation():
    assert ai_engine._safe_position_size("300% NAV", True) == "20% NAV (giới hạn tối đa 20%)"
    assert ai_engine._safe_position_size("20% NAV", False).startswith("0% NAV")
    assert ai_engine._safe_position_size("tùy ý", True).startswith("Tối đa 20% NAV")


def test_enforce_predictions_replaces_bad_ai_numbers_and_blocks_buy_without_real_flow(isolated_db, monkeypatch):
    monkeypatch.setattr(ai_engine, "_load_today_cached_ai_predictions", lambda: {})
    row = {
        "symbol": "ABC", "trade_date": "2026-10-23", "price": 10_000,
        "volume": 100_000, "volume_available": True, "index_valid": True,
        "session_current": True, "is_real_deal": False, "flow_coverage_pct": None,
        "structure_available": True, "support": 9_500, "resistance": 11_000,
        "bottom_score": 55, "top_score": 30, "signal_score": 60, "p_win": 60,
        "rs_vni": 1.0, "flow_confidence": "THẤP (Ước tính từ VWAP)",
        "action": "QUAN SÁT", "rr_ratio": 2,
    }
    out = ai_engine.enforce_predictions([row], {
        "ABC": {"vsa": "test", "action": "MUA MẠNH", "position_size_suggestion": "300% NAV",
                "pred_t2_pct": 500, "pred_t3_pct": float("nan"), "pred_t7_pct": 5,
                "_provider": "Gemini", "_model": "fixture"}
    })[0]
    assert out["pred_t2"] != 500
    assert math.isfinite(out["pred_t3"])
    assert -14 <= out["pred_t7"] <= 18
    assert "CHƯA CÓ DÒNG TIỀN THỰC" in out["ai_action"]
    assert out["position_size_suggestion"].startswith("0% NAV")
    assert out["_prediction_sources"][2] == "Fallback định lượng"
    assert out["_prediction_sources"][7] == "AI"


def test_prediction_audit_requires_observed_session_and_skips_weekend(isolated_db):
    base = {
        "symbol": "ABC", "price": 10_000, "volume": 100_000, "volume_available": True,
        "pred_t2": 1, "pred_t3": 2, "pred_t7": 3, "action": "QUAN SÁT",
        "ai_provider": "Fallback định lượng", "trade_date": "2026-10-23",
    }
    assert ai_engine.save_prediction_audit([{**base, "session_current": False}], "Sideway") == 0
    assert ai_engine.save_prediction_audit([{**base, "trade_date": "2026-10-25"}], "Sideway") == 0
    future_day = datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).date() + timedelta(days=1)
    while not is_trading_day(future_day):
        future_day += timedelta(days=1)
    assert ai_engine.save_prediction_audit([{**base, "trade_date": future_day.isoformat()}], "Sideway") == 0
    assert ai_engine.save_prediction_audit([base], "Sideway") == 3
    assert ai_engine.save_prediction_audit([base], "Sideway") == 0


def _persist_row(trade_date="2026-10-23", price=10_000):
    return {
        "trade_date": trade_date, "symbol": "ABC", "sector": "Test", "price": price,
        "change_pct": 1, "volume": 100_000, "ma20_vol": 90_000,
        "projected_volume": 110_000, "vwap": 9_950, "vwap_position": "Trên VWAP",
        "buy_vol": 60_000, "sell_vol": 40_000, "buy_ratio": 60,
        "net_flow_billion": 0.2, "delta_flow_open": 0.1, "rs_vni": 0.8,
        "rs_sector": 0.2, "foreign_net_today": 100, "foreign_net_5d": 500,
        "quality_score": 75, "signal_score": 65, "agreement_pct": 80, "p_win": 63,
        "conviction": "TRUNG BÌNH", "wyckoff": "Candidate", "action": "QUAN SÁT",
        "ai_vsa": "test", "ai_action": "QUAN SÁT", "pred_t2": 1,
        "pred_t3": 1.5, "pred_t7": 2, "source_label": "fixture",
        "is_real_deal": False,
    }


def test_persist_keeps_intraday_history_and_upserts_after_close(isolated_db):
    tz = ZoneInfo("Asia/Ho_Chi_Minh")
    row = _persist_row()
    assert engine._persist([row], datetime(2026, 10, 23, 10, 0, tzinfo=tz)) == 1
    assert engine._persist([{**row, "price": 10_100}], datetime(2026, 10, 23, 11, 0, tzinfo=tz)) == 1
    assert engine._persist([{**row, "price": 10_200}], datetime(2026, 10, 23, 15, 5, tzinfo=tz)) == 1
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM stock_snapshots").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM flow_snapshots").fetchone()[0] == 2
        assert conn.execute("SELECT price FROM stock_snapshots ORDER BY id DESC LIMIT 1").fetchone()[0] == 10_200


def test_persist_does_not_write_nonempty_rows_on_weekend_or_configured_holiday(isolated_db, monkeypatch):
    monkeypatch.setenv("QUANTS_NON_TRADING_DATES", "2026-10-26")
    tz = ZoneInfo("Asia/Ho_Chi_Minh")
    row = _persist_row()
    assert engine._persist([row], datetime(2026, 10, 24, 10, 0, tzinfo=tz)) == 0
    assert engine._persist([{**row, "trade_date": "2026-10-26"}], datetime(2026, 10, 26, 10, 0, tzinfo=tz)) == 0
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM stock_snapshots").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM flow_snapshots").fetchone()[0] == 0


def test_connection_context_manager_closes_sqlite_connection(isolated_db):
    with db.connect() as conn:
        conn.execute("SELECT 1")
    with pytest.raises(Exception):
        conn.execute("SELECT 1")


def test_password_hash_uses_unique_salted_kdf_and_legacy_hash_is_detected():
    first = db._hash_pw("test-password")
    second = db._hash_pw("test-password")
    assert first.startswith("pbkdf2_sha256$")
    assert first != second
    assert db._verify_pw("test-password", first) == (True, False)
    legacy = hashlib.sha256("quants_v16_salt_test-password".encode()).hexdigest()
    assert db._verify_pw("test-password", legacy) == (True, True)


def test_trading_day_arithmetic_skips_configured_holiday(monkeypatch):
    monkeypatch.setenv("QUANTS_NON_TRADING_DATES", "2026-10-26")
    assert add_trading_days(datetime(2026, 10, 23).date(), 1).isoformat() == "2026-10-27"
    assert not is_trading_day(datetime(2026, 10, 26).date())


def test_provider_does_not_fabricate_vwap_and_value_units_are_vnd():
    quote = _quote_from_raw({"sym": "ABC", "lastPrice": 25.0, "r": 24.0, "totalVolume": 1000}, "fixture")
    assert quote["vwap"] is None
    assert quote["vwap_available"] is False
    assert _value_billion({"totalValue": 200_000_000}) == 0.2


def test_sell_flow_label_requires_real_flow_confirmation():
    row = {
        "volume_available": True, "volume": 1_000_000, "price": 9_700,
        "high": 9_700, "low": 9_600, "change_pct": -2.2, "vol_ratio": 1.4,
        "buy_ratio": 35, "vwap_position": "Dưới VWAP", "bottom_score": 30,
        "top_score": 65, "history_available": True, "support": 9_500,
        "resistance": 10_500, "is_real_deal": False, "flow_coverage_pct": None,
    }
    assert engine.classify_wyckoff(row) == "Markdown Candidate / Chưa xác nhận dòng tiền bán"
    row.update({"is_real_deal": True, "flow_coverage_pct": 60})
    assert engine.classify_wyckoff(row) == "Markdown / Xả Hàng"


def test_position_size_is_zero_when_ai_does_not_recommend_new_buy(isolated_db, monkeypatch):
    monkeypatch.setattr(ai_engine, "_load_today_cached_ai_predictions", lambda: {})
    row = {
        "symbol": "ABC", "trade_date": "2026-10-23", "price": 10_000,
        "volume": 100_000, "volume_available": True, "index_valid": True,
        "session_current": True, "is_real_deal": True, "flow_coverage_pct": 60,
        "structure_available": True, "support": 9_500, "resistance": 11_000,
        "action": "QUAN SÁT", "p_win": 60, "signal_score": 60,
    }
    out = ai_engine.enforce_predictions([row], {
        "ABC": {"action": "QUAN SÁT", "position_size_suggestion": "15% NAV",
                "pred_t2_pct": 1, "pred_t3_pct": 1, "pred_t7_pct": 2,
                "_provider": "Fixture", "_model": "fixture"}
    })[0]
    assert out["position_size_suggestion"].startswith("0% NAV")



def test_daily_fallback_is_not_accepted_as_live_intraday_quote(monkeypatch):
    monkeypatch.setenv("QUANTS_NON_TRADING_DATES", "")
    from datetime import timezone

    bar_ts = datetime(2026, 10, 26, 0, 0, tzinfo=timezone.utc).timestamp()
    during_session = datetime(2026, 10, 26, 10, 30, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
    after_close = datetime(2026, 10, 26, 15, 10, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
    prior_bar_ts = datetime(2026, 10, 23, 0, 0, tzinfo=timezone.utc).timestamp()
    assert _vndirect_daily_quote_is_usable(bar_ts, during_session) is False
    assert _vndirect_daily_quote_is_usable(bar_ts, after_close) is True
    assert _vndirect_daily_quote_is_usable(prior_bar_ts, after_close) is False


def test_stale_ai_cache_is_not_reused_after_price_or_signal_changes(isolated_db, monkeypatch):
    stale = {
        "vsa": "PHÂN TÍCH CŨ", "action": "MUA MẠNH", "position_size_suggestion": "20% NAV",
        "pred_t2_pct": 6, "pred_t3_pct": 8, "pred_t7_pct": 12,
        "_cache_complete": True, "_cached_price": 12_000, "_cached_vol_ratio": 1.0,
        "_cached_signal": "old signal", "_provider": "Fixture", "_model": "old-model",
    }
    monkeypatch.setattr(ai_engine, "_load_today_cached_ai_predictions", lambda: {"ABC": stale})
    row = {
        "symbol": "ABC", "trade_date": "2026-10-23", "price": 10_000,
        "volume": 100_000, "volume_available": True, "index_valid": True,
        "session_current": True, "is_real_deal": False, "flow_coverage_pct": None,
        "structure_available": True, "support": 9_500, "resistance": 11_000,
        "early_signal": "new signal", "vol_ratio": 1.4, "bottom_score": 55,
        "top_score": 30, "signal_score": 60, "p_win": 60, "rs_vni": 1.0,
        "flow_confidence": "THẤP", "action": "QUAN SÁT", "rr_ratio": 2,
    }
    out = ai_engine.enforce_predictions([row], {})[0]
    assert "PHÂN TÍCH CŨ" not in out["ai_vsa"]
    assert out["_prediction_sources"] == {2: "Fallback định lượng", 3: "Fallback định lượng", 7: "Fallback định lượng"}
    assert out["position_size_suggestion"].startswith("0% NAV")


def test_persist_skips_lunch_recess_and_late_night_cached_quotes(isolated_db):
    tz = ZoneInfo("Asia/Ho_Chi_Minh")
    row = _persist_row()
    assert engine._persist([row], datetime(2026, 10, 23, 12, 0, tzinfo=tz)) == 0
    assert engine._persist([row], datetime(2026, 10, 23, 22, 0, tzinfo=tz)) == 0
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM stock_snapshots").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM flow_snapshots").fetchone()[0] == 0



def test_settlement_never_uses_future_dated_market_bars(isolated_db, monkeypatch):
    today = datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).date()
    base = today - timedelta(days=10)
    while not is_trading_day(base):
        base -= timedelta(days=1)
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO predictions(created_at,trade_date,symbol,horizon,base_price,predicted_pct,regime,status)
               VALUES(?,?,?,?,?,?,?,?)""",
            (datetime.now(timezone.utc).isoformat(), base.isoformat(), "ABC", 2, 100.0, 2.0, "Sideway", "OPEN"),
        )
    future = [today + timedelta(days=i) for i in (1, 2, 3)]
    monkeypatch.setattr(audit, "fetch_history", lambda *args, **kwargs: (
        [{"date": day.isoformat(), "close": 105.0 + i} for i, day in enumerate(future)], "fixture", []
    ))
    assert audit.settle_predictions() == 0
    with db.connect() as conn:
        assert conn.execute("SELECT status,actual_pct FROM predictions LIMIT 1").fetchone()["status"] == "OPEN"


def test_worker_forecast_job_runs_complete_pipeline_without_external_calls(monkeypatch):
    import worker
    calls = []
    index = {"valid": True, "decision_usable": True, "change_pct": 0.4, "provider": "fixture"}
    rows = [{"symbol": "ABC", "trade_date": "2026-10-23", "price": 10_000}]
    monkeypatch.setattr(worker, "init_db", lambda: calls.append("init"))
    monkeypatch.setattr(worker, "fetch_vnindex", lambda: (index, []))
    monkeypatch.setattr(worker, "compute_quant", lambda *args: (rows, []))
    monkeypatch.setattr(worker, "fetch_macro", lambda: ([], []))
    monkeypatch.setattr(worker, "run_ai", lambda *args: calls.append("ai") or {})
    monkeypatch.setattr(worker, "enforce_predictions", lambda r, ai: calls.append("enforce") or r)
    monkeypatch.setattr(worker, "save_prediction_audit", lambda r, regime: calls.append(("audit", regime)) or 3)
    monkeypatch.setattr(worker, "event", lambda *args, **kwargs: None)
    worker.run_once("forecast")
    assert calls[0] == "init"
    assert "ai" in calls and "enforce" in calls
    assert any(isinstance(c, tuple) and c[0] == "audit" for c in calls)


def test_settlement_skips_non_finite_forecast_values(isolated_db, monkeypatch):
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO predictions(created_at,trade_date,symbol,horizon,base_price,predicted_pct,regime,status)
               VALUES(?,?,?,?,?,?,?,?)""",
            (datetime.now(timezone.utc).isoformat(), "2026-10-09", "ABC", 2, 100.0, float("inf"), "Sideway", "OPEN"),
        )
    monkeypatch.setattr(audit, "fetch_history", lambda *args, **kwargs: (
        [{"date": "2026-10-12", "close": 101}], "fixture", []
    ))
    assert audit.settle_predictions() == 0
    with db.connect() as conn:
        row = conn.execute("SELECT status,actual_pct FROM predictions LIMIT 1").fetchone()
        assert row["status"] == "OPEN"
        assert row["actual_pct"] is None
