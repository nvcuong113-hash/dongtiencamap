from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from quants import ai_engine, engine, db
from quants.calendar_vn import latest_completed_trading_day, is_live_market_window, is_exchange_trading_phase
from quants.providers import _vndirect_daily_quote_is_usable


TZ = ZoneInfo("Asia/Ho_Chi_Minh")


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", Path(tmp_path) / "off-session.sqlite3")
    db.init_db()
    return db.DB_PATH


def test_weekend_latest_completed_session_is_friday():
    sunday = datetime(2026, 10, 25, 10, 0, tzinfo=TZ)
    assert latest_completed_trading_day(sunday).isoformat() == "2026-10-23"
    assert not is_live_market_window(sunday)




def test_ho_se_close_cutoff_switches_to_completed_session_at_1445():
    before_close = datetime(2026, 10, 29, 14, 44, 59, tzinfo=TZ)
    after_close = datetime(2026, 10, 29, 14, 45, 0, tzinfo=TZ)

    assert is_exchange_trading_phase(before_close) is True
    assert is_live_market_window(before_close) is True
    assert latest_completed_trading_day(before_close).isoformat() == "2026-10-28"

    assert is_exchange_trading_phase(after_close) is False
    assert is_live_market_window(after_close) is False
    assert latest_completed_trading_day(after_close).isoformat() == "2026-10-29"


def test_vndirect_daily_fallback_is_usable_after_ho_se_close(monkeypatch):
    from datetime import timezone

    after_close = datetime(2026, 10, 29, 14, 48, 0, tzinfo=TZ)
    current_bar = datetime(2026, 10, 29, 0, 0, tzinfo=timezone.utc).timestamp()
    assert _vndirect_daily_quote_is_usable(current_bar, after_close) is True

    before_close = datetime(2026, 10, 29, 14, 44, 59, tzinfo=TZ)
    assert _vndirect_daily_quote_is_usable(current_bar, before_close) is False


def test_vnindex_uses_completed_daily_bar_after_1445_without_live_source_alerts(monkeypatch):
    from datetime import timezone
    from quants import providers

    after_close = datetime(2026, 10, 29, 14, 48, 0, tzinfo=TZ)
    prev_bar = datetime(2026, 10, 28, 0, 0, tzinfo=timezone.utc).timestamp()
    current_bar = datetime(2026, 10, 29, 0, 0, tzinfo=timezone.utc).timestamp()
    calls = []

    def fake_get_json(url, timeout=None):
        calls.append(url)
        return {"c": [1740.0, 1753.32], "v": [100_000_000, 120_000_000], "t": [prev_bar, current_bar]}

    monkeypatch.setattr(providers, "get_json", fake_get_json)
    monkeypatch.setattr(
        providers, "_fetch_vnstock_live_index_quote",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not query intraday after close")),
    )

    result, errors = providers.fetch_vnindex(now=after_close)

    assert result["valid"] is True
    assert result["decision_usable"] is True
    assert result["provider"] == "VNDIRECT-Daily"
    assert result["quote_date"] == "2026-10-29"
    assert errors == []
    assert len(calls) == 1


def test_stock_feeds_use_verified_completed_session_after_1445(monkeypatch):
    from datetime import timezone
    from quants import providers

    after_close = datetime(2026, 10, 29, 14, 48, 0, tzinfo=TZ)
    prev_bar = datetime(2026, 10, 28, 0, 0, tzinfo=timezone.utc).timestamp()
    current_bar = datetime(2026, 10, 29, 0, 0, tzinfo=timezone.utc).timestamp()
    calls = []

    def fake_get_json(url, timeout=None):
        calls.append(url)
        if "getliststockdata" in url:
            # VPS price has no provider timestamp, so it must not be accepted as live.
            return [{"sym": "FPT", "lastPrice": 58_000, "r": 57_000, "totalVolume": 1_000_000}]
        if "dchart-api.vndirect.com.vn" in url:
            return {
                "c": [57.0, 58.0], "v": [900_000, 1_100_000],
                "h": [57.5, 58.5], "l": [56.5, 57.5],
                "t": [prev_bar, current_bar],
            }
        raise AssertionError(f"Unexpected provider URL: {url}")

    monkeypatch.setattr(providers, "get_json", fake_get_json)
    monkeypatch.setattr(
        providers, "_fetch_vnstock_live_stock_quotes",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not query live OHLCV after close")),
    )

    feeds, errors = providers.fetch_stock_feeds(["FPT"], now=after_close)

    row = feeds["FPT"]
    assert row["provider"] == "VNDirect-Fallback"
    assert row["decision_usable"] is True
    assert row["freshness_verified"] is True
    assert row["freshness"] == "VERIFIED_COMPLETED_SESSION"
    assert row["quote_date"] == "2026-10-29"
    assert errors == []
    assert len(calls) == 2


def test_vnindex_stale_daily_fallback_returns_explicit_warning_after_close(monkeypatch):
    from datetime import timezone
    from quants import providers

    after_close = datetime(2026, 10, 29, 14, 48, 0, tzinfo=TZ)
    stale_bar = datetime(2026, 10, 28, 0, 0, tzinfo=timezone.utc).timestamp()
    monkeypatch.setattr(providers, "get_json", lambda *_args, **_kwargs: {
        "c": [1740.0, 1753.32], "v": [100_000_000, 120_000_000],
        "t": [stale_bar, stale_bar],
    })
    result, errors = providers.fetch_vnindex(now=after_close)
    assert result["valid"] is False
    assert result["decision_usable"] is False
    assert errors
    assert "cần phiên hoàn tất 2026-10-29" in errors[-1]


def test_stock_feeds_unverified_daily_fallback_is_reported_after_close(monkeypatch):
    from datetime import timezone
    from quants import providers

    after_close = datetime(2026, 10, 29, 14, 48, 0, tzinfo=TZ)
    stale_bar = datetime(2026, 10, 28, 0, 0, tzinfo=timezone.utc).timestamp()

    def fake_get_json(url, timeout=None):
        if "getliststockdata" in url:
            return [{"sym": "FPT", "lastPrice": 58_000, "r": 57_000, "totalVolume": 1_000_000}]
        if "dchart-api.vndirect.com.vn" in url:
            return {
                "c": [57.0, 58.0], "v": [900_000, 1_100_000],
                "h": [57.5, 58.5], "l": [56.5, 57.5],
                "t": [stale_bar, stale_bar],
            }
        if "apipubaws.tcbs.com.vn" in url:
            return {}
        raise AssertionError(f"Unexpected provider URL: {url}")

    monkeypatch.setattr(providers, "get_json", fake_get_json)
    feeds, errors = providers.fetch_stock_feeds(["FPT"], now=after_close)
    assert feeds["FPT"]["decision_usable"] is False
    assert any("FPT:" in error and "cần phiên hoàn tất 2026-10-29" in error for error in errors)
    assert "VNDirect daily chưa xác minh" in feeds["FPT"]["freshness_reason"]


def test_daily_fallback_on_weekend_accepts_only_latest_completed_bar():
    from datetime import timezone

    sunday = datetime(2026, 10, 25, 10, 0, tzinfo=TZ)
    friday_bar = datetime(2026, 10, 23, 0, 0, tzinfo=timezone.utc).timestamp()
    thursday_bar = datetime(2026, 10, 22, 0, 0, tzinfo=timezone.utc).timestamp()
    assert _vndirect_daily_quote_is_usable(friday_bar, sunday) is True
    assert _vndirect_daily_quote_is_usable(thursday_bar, sunday) is False


def test_closed_session_uses_verified_daily_ohlcv_not_stale_quote_cache():
    sunday = datetime(2026, 10, 25, 10, 0, tzinfo=TZ)
    bars = [
        {"date": "2026-10-21", "close": 9_700, "high": 9_800, "low": 9_600, "volume": 900_000},
        {"date": "2026-10-22", "close": 9_800, "high": 9_900, "low": 9_700, "volume": 950_000},
        {"date": "2026-10-23", "close": 10_000, "high": 10_100, "low": 9_750, "volume": 1_100_000},
    ]
    stale_quote = {
        "price": 9_000, "ref_price": 8_900, "change_pct": 1.1,
        "volume": 0, "volume_available": False, "provider": "VPS",
        "decision_usable": True, "vwap": 9_100, "vwap_available": True,
    }
    quote, normalized, base_date, reason = engine._normalize_closed_session_quote(stale_quote, bars, sunday)
    assert normalized is True
    assert base_date == "2026-10-23"
    assert quote["price"] == 10_000
    assert quote["ref_price"] == 9_800
    assert quote["volume"] == 1_100_000
    assert quote["vwap"] is None
    assert quote["freshness"] == "HISTORICAL_COMPLETED_SESSION"
    assert "2026-10-23" in reason

    usable, gate_reason = engine._forecast_data_gate(sunday, quote, bars, True, False, normalized)
    assert usable is True
    assert "đã xác minh" in gate_reason


def test_stale_history_does_not_unlock_closed_session_forecast():
    sunday = datetime(2026, 10, 25, 10, 0, tzinfo=TZ)
    bars = [
        {"date": "2026-10-21", "close": 9_700, "high": 9_800, "low": 9_600, "volume": 900_000},
        {"date": "2026-10-22", "close": 9_800, "high": 9_900, "low": 9_700, "volume": 950_000},
    ]
    quote, normalized, _, _ = engine._normalize_closed_session_quote({"price": 9_800}, bars, sunday)
    assert normalized is False
    usable, reason = engine._forecast_data_gate(sunday, quote, bars, True, False, normalized)
    assert usable is False
    assert "cần 2026-10-23" in reason


def test_enforce_predictions_forecasts_closed_session_but_blocks_new_position(isolated_db, monkeypatch):
    monkeypatch.setattr(ai_engine, "_load_today_cached_ai_predictions", lambda: {})
    row = {
        "symbol": "ABC", "trade_date": "2026-10-23", "price": 10_000,
        "volume": 1_100_000, "volume_available": True, "index_valid": True,
        "session_current": False, "market_open": False, "forecast_data_usable": True,
        "forecast_data_reason": "Dự báo từ phiên đóng cửa đã xác minh 2026-10-23",
        "forecast_base_type": "LAST_COMPLETED_SESSION",
        "is_real_deal": False, "flow_coverage_pct": None,
        "history_available": True, "structure_available": True,
        "support": 9_500, "resistance": 10_500,
        "bottom_score": 65, "top_score": 30, "signal_score": 65,
        "p_win": 60, "rs_vni": 1.2, "vol_ratio": 1.1,
        "flow_confidence": "THẤP", "action": "QUAN SÁT", "rr_ratio": 2,
    }
    out = ai_engine.enforce_predictions([row], {})[0]
    assert out["pred_t2"] is not None
    assert out["pred_t3"] is not None
    assert out["pred_t7"] is not None
    assert "CHỜ PHIÊN GIAO DỊCH" in out["ai_action"]
    assert out["position_size_suggestion"].startswith("0% NAV")


def test_save_prediction_audit_accepts_verified_closed_session_forecast(isolated_db):
    row = {
        "symbol": "ABC", "trade_date": "2026-10-23", "price": 10_000,
        "volume": 1_100_000, "volume_available": True, "index_valid": True,
        "session_current": False, "market_open": False, "forecast_data_usable": True,
        "action": "QUAN SÁT", "ai_action": "CHỜ PHIÊN GIAO DỊCH",
        "pred_t2": 1.0, "pred_t3": 1.5, "pred_t7": 2.0,
        "_prediction_sources": {2: "Fallback định lượng", 3: "Fallback định lượng", 7: "Fallback định lượng"},
        "ai_provider": "Fallback định lượng", "ai_model": "deterministic-fallback",
    }
    saved = ai_engine.save_prediction_audit([row], "Sideway")
    assert saved == 3


def test_market_regime_uses_watchlist_breadth_and_discloses_narrow_rally():
    index = {"valid": True, "decision_usable": True, "change_pct": 1.4}
    rows = [
        {"symbol": f"A{i}", "price": 10_000, "change_pct": 1.0 if i < 3 else -0.2,
         "weekly_trend": "Dưới MA20W"}
        for i in range(8)
    ]
    regime = engine.classify_market_regime(index, rows)
    assert "đà tăng hẹp" in regime.lower() or "breadth yếu" in regime.lower()


def test_vnindex_does_not_trust_untimestamped_vps_cache_on_weekend(monkeypatch):
    from datetime import timezone
    from quants import providers

    sunday = datetime(2026, 10, 25, 10, 0, tzinfo=TZ)
    friday_ts = datetime(2026, 10, 23, 0, 0, tzinfo=timezone.utc).timestamp()
    thursday_ts = datetime(2026, 10, 22, 0, 0, tzinfo=timezone.utc).timestamp()
    calls = []

    def fake_get_json(url, timeout=None):
        calls.append(url)
        return {"c": [2000, 2010], "v": [10, 20], "t": [thursday_ts, friday_ts]}

    monkeypatch.setattr(providers, "get_json", fake_get_json)
    result, errors = providers.fetch_vnindex(now=sunday)
    assert result["valid"] is True
    assert result["provider"] == "VNDIRECT-Daily"
    assert result["quote_date"] == "2026-10-23"
    assert len(calls) == 1
    assert "bgapidatafeed.vps.com.vn" not in calls[0]


def test_vnindex_stale_daily_bar_does_not_unlock_forecast(monkeypatch):
    from datetime import timezone
    from quants import providers

    sunday = datetime(2026, 10, 25, 10, 0, tzinfo=TZ)
    thursday_ts = datetime(2026, 10, 22, 0, 0, tzinfo=timezone.utc).timestamp()
    monkeypatch.setattr(providers, "get_json", lambda *args, **kwargs: {
        "c": [2000, 2010], "v": [10, 20], "t": [thursday_ts - 86400, thursday_ts]
    })
    result, _ = providers.fetch_vnindex(now=sunday)
    assert result["valid"] is False
    assert result["decision_usable"] is False


def test_vps_index_value_in_million_vnd_is_normalized_to_billion():
    from quants.providers import _index_value_billion

    # VPS index-detail's `value` field can be returned in million VND.
    assert _index_value_billion({"value": 15_000_000}) == 15_000.0


def test_vps_index_value_already_in_vnd_is_normalized_to_billion():
    from quants.providers import _index_value_billion

    assert _index_value_billion({"totalValue": 15_000_000_000_000}) == 15_000.0
    assert _index_value_billion({"value": 15_000_000_000_000}) == 15_000.0


def test_vps_index_value_missing_is_not_fabricated_as_zero():
    from quants.providers import _index_value_billion

    assert _index_value_billion({"value": 0}) is None


def test_live_vnindex_feed_maps_value_and_volume_fields(monkeypatch):
    from quants import providers

    monday_live = datetime(2026, 10, 26, 10, 0, tzinfo=TZ)
    monkeypatch.setattr(providers, "get_json", lambda *args, **kwargs: [{
        "mc": "10", "cIndex": 1773.92, "oIndex": 1774.10,
        "vol": 245_322_360, "value": 15_000_000,
        "timestamp": "2026-10-26T10:00:00+07:00", "time": "10:00:00",
    }])
    result, errors = providers.fetch_vnindex(now=monday_live)
    assert errors == []
    assert result["price"] == 1773.92
    assert result["volume"] == 245_322_360
    assert result["value_billion"] == 15_000.0
    assert result["valid"] is True


def test_missing_volume_ratio_never_labels_low_volume_as_supply_dryup():
    row = {
        "price": 10_000, "high": 10_100, "low": 9_900, "change_pct": 0.1,
        "volume": 1_000_000, "volume_available": True, "vol_ratio": None,
        "history_available": True, "support": 9_900, "resistance": 11_000,
        "retest_count": 3, "supply_decay_score": 80, "rs_vni": 1.0,
        "is_real_deal": True, "flow_coverage_pct": 50, "buy_ratio": 60,
    }
    _, _, early_tag, _ = engine.compute_bottom_top_detectors(row)
    assert "thiếu xác nhận volume" in early_tag.lower()
    assert "cạn cung" not in early_tag.lower()


def test_empirical_intraday_profile_requires_five_prior_sessions():
    class FakeCursor:
        def __init__(self, rows):
            self.rows = rows

        def fetchall(self):
            return self.rows

    class FakeConnection:
        def __init__(self, rows):
            self.rows = rows

        def execute(self, *_args):
            return FakeCursor(self.rows)

    now = datetime(2026, 10, 26, 10, 0, tzinfo=TZ)
    short = []
    for day in ("2026-10-20", "2026-10-21", "2026-10-22", "2026-10-23"):
        short.append({"trade_date": day, "ts": f"{day}T10:00:00+07:00", "volume": 400})
        short.append({"trade_date": day, "ts": f"{day}T14:59:00+07:00", "volume": 1000})
    ratio, count = engine._empirical_intraday_volume_progress(FakeConnection(short), now, "ABC")
    assert ratio is None
    assert count == 4


def test_empirical_intraday_profile_uses_prior_session_median_not_generic_curve():
    class FakeCursor:
        def __init__(self, rows):
            self.rows = rows

        def fetchall(self):
            return self.rows

    class FakeConnection:
        def __init__(self, rows):
            self.rows = rows

        def execute(self, *_args):
            return FakeCursor(self.rows)

    now = datetime(2026, 10, 23, 10, 0, tzinfo=TZ)
    historical = []
    days = ["2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15", "2026-10-16", "2026-10-19"]
    for i, day in enumerate(days):
        opening_progress = 300 + i * 20
        final_volume = 1000
        historical.extend([
            {"trade_date": day, "ts": f"{day}T10:00:00+07:00", "volume": opening_progress},
            {"trade_date": day, "ts": f"{day}T14:59:00+07:00", "volume": final_volume},
        ])
    ratio, count = engine._empirical_intraday_volume_progress(FakeConnection(historical), now, "ABC")
    assert count == 6
    assert ratio == pytest.approx(0.35)


def test_purged_calibration_split_removes_overlapping_training_labels():
    from datetime import date, timedelta
    from quants.learning import _purged_time_split
    from quants.calendar_vn import add_trading_days

    sessions = []
    day = date(2026, 1, 5)
    while len(sessions) < 45:
        if day.weekday() < 5:
            sessions.append(day)
        day += timedelta(days=1)
    records = [{"id": i, "created_at": f"{d.isoformat()}T16:00:00", "trade_date": d.isoformat(), "p_win": 60,
                "predicted_pct": 1.0, "actual": 0.5} for i, d in enumerate(sessions)]
    train, holdout, meta = _purged_time_split(records, horizon=7, train_fraction=0.8)
    first_test = date.fromisoformat(meta["test_start_date"])
    assert holdout
    assert all(add_trading_days(date.fromisoformat(row["trade_date"]), 7) < first_test for row in train)
    assert meta["purged_train_samples"] > 0


def test_run_ai_eligibility_uses_valid_completed_session_offline(monkeypatch):
    row = {
        "symbol": "ABC", "trade_date": "2026-10-23", "price": 10_000,
        "volume": 1_000_000, "volume_available": True, "index_valid": True,
        "session_current": False, "market_open": False, "forecast_data_usable": True,
        "forecast_data_reason": "Dữ liệu đóng cửa được xác minh", "forecast_base_type": "LAST_COMPLETED_SESSION",
        "change_pct": 1.0, "signal_score": 60,
    }
    monkeypatch.setattr(ai_engine, "_load_today_cached_ai_predictions", lambda *_args: {})
    monkeypatch.setattr(ai_engine.time, "sleep", lambda *_args: None)
    monkeypatch.setattr(ai_engine, "_generate_with_fallback", lambda *args, **kwargs: (
        '{"ABC":{"vsa":"Ứng viên","action":"QUAN SÁT","pred_t2_pct":1.0,'
        '"pred_t3_pct":1.2,"pred_t7_pct":1.8,"rationale":"fixture"}}',
        "Fixture", "fixture-model"
    ))
    result = ai_engine.run_ai([row], "Sideway", [], provider="Fixture")
    assert "ABC" in result
    assert result["ABC"]["pred_t2_pct"] == 1.0


def test_compute_quant_can_score_verified_friday_close_on_sunday(isolated_db, monkeypatch):
    from datetime import date, timedelta

    sunday = datetime(2026, 10, 25, 10, 0, tzinfo=TZ)
    bars = []
    day = date(2026, 8, 1)
    idx = 0
    while day <= date(2026, 10, 23):
        if day.weekday() < 5:
            close = 10_000 + idx * 10
            bars.append({
                "date": day.isoformat(), "close": close, "high": close * 1.015,
                "low": close * 0.985, "volume": 500_000 + idx * 2_000,
            })
            idx += 1
        day += timedelta(days=1)
    original_last = bars[-1]
    feed = {"ABC": {
        "symbol": "ABC", "price": 8_800, "ref_price": 8_700, "change_pct": 1.15,
        "volume": 0, "volume_available": False, "vwap": 8_900, "vwap_available": True,
        "high": 9_000, "low": 8_600, "foreign_net": None, "source_label": "VPS cache",
        "provider": "VPS", "decision_usable": True,
    }}
    monkeypatch.setattr(engine, "fetch_stock_feeds", lambda *_args: (feed, []))
    monkeypatch.setattr(engine, "fetch_history", lambda *_args: (bars, "fixture", []))
    monkeypatch.setattr(engine, "fetch_deal_flow", lambda *_args: None)
    monkeypatch.setattr(engine, "_store_rows", lambda *_args: None)
    rows, errors = engine.compute_quant(
        ["ABC"], {"valid": True, "decision_usable": True, "change_pct": 0.4}, sunday
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["trade_date"] == "2026-10-23"
    assert row["price"] == original_last["close"]
    assert row["forecast_base_type"] == "LAST_COMPLETED_SESSION"
    assert row["forecast_data_usable"] is True
    assert row["session_current"] is False
    assert row["action"] != "CHỜ DỮ LIỆU"


def test_vps_stock_time_only_timestamp_is_accepted_only_when_fresh_in_live_window():
    from quants.providers import _quote_from_raw

    now = datetime(2026, 10, 26, 10, 0, tzinfo=TZ)
    raw = {
        "sym": "GEG", "lastPrice": 11850, "r": 11950,
        "totalVolume": 100000, "time": "09:59:00",
    }
    quote = _quote_from_raw(raw, "VPS", now=now)
    assert quote is not None
    assert quote["decision_usable"] is True
    assert quote["freshness"] == "SOURCE_TIME_OF_DAY_ASSUMED_TODAY"
    assert quote["freshness_verified"] is False
    assert quote["freshness_age_seconds"] == pytest.approx(60.0)


def test_vps_stock_time_only_timestamp_stale_or_off_session_is_blocked():
    from quants.providers import _quote_from_raw

    live_now = datetime(2026, 10, 26, 10, 0, tzinfo=TZ)
    stale = _quote_from_raw({
        "sym": "GEG", "lastPrice": 11850, "r": 11950,
        "totalVolume": 100000, "time": "09:50:00",
    }, "VPS", now=live_now)
    assert stale is not None
    assert stale["decision_usable"] is False
    assert stale["freshness"] == "SOURCE_TIMESTAMP_STALE"

    sunday = datetime(2026, 10, 25, 10, 0, tzinfo=TZ)
    off_session = _quote_from_raw({
        "sym": "GEG", "lastPrice": 11850, "r": 11950,
        "totalVolume": 100000, "time": "09:59:00",
    }, "VPS", now=sunday)
    assert off_session is not None
    assert off_session["decision_usable"] is False
    assert off_session["freshness"] == "SOURCE_TIMESTAMP_UNAVAILABLE"
