from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from quants import providers

TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def test_vnstock_builder_accepts_only_fresh_same_session_minute_bars():
    now = datetime(2026, 10, 28, 10, 0, 50, tzinfo=TZ)
    board = {"symbol": "GEG", "ref_price": 11.95}
    bars = pd.DataFrame([
        {"time": datetime(2026, 10, 28, 9, 59, 0), "open": 11.8, "high": 11.9, "low": 11.7, "close": 11.85, "volume": 100},
        {"time": datetime(2026, 10, 28, 10, 0, 0), "open": 11.85, "high": 11.9, "low": 11.8, "close": 11.9, "volume": 250},
    ])
    row = providers._build_vnstock_live_equity_row("GEG", board, bars, "kbs", now=now)
    assert row is not None
    assert row["symbol"] == "GEG"
    assert row["price"] == 11900
    assert row["ref_price"] == 11950
    assert row["volume"] == 350
    assert row["decision_usable"] is True
    assert row["freshness_verified"] is True
    assert row["source_timestamp"] == "2026-10-28T10:00:00+07:00"
    assert row["vwap"] is None


def test_vnstock_builder_rejects_stale_or_previous_session_bars():
    now = datetime(2026, 10, 28, 10, 10, 0, tzinfo=TZ)
    board = {"symbol": "GEG", "ref_price": 11.95}
    stale = pd.DataFrame([
        {"time": datetime(2026, 10, 28, 10, 0, 0), "open": 11.8, "high": 11.9, "low": 11.7, "close": 11.9, "volume": 100},
    ])
    previous_session = pd.DataFrame([
        {"time": datetime(2026, 10, 27, 14, 59, 0), "open": 11.8, "high": 11.9, "low": 11.7, "close": 11.9, "volume": 100},
    ])
    assert providers._build_vnstock_live_equity_row("GEG", board, stale, "vci", now=now) is None
    assert providers._build_vnstock_live_equity_row("GEG", board, previous_session, "vci", now=now) is None


def test_fetch_stock_feeds_uses_vnstock_only_for_unverified_symbols(monkeypatch):
    now = datetime(2026, 10, 28, 10, 0, 40, tzinfo=TZ)

    def fake_get_json(url, timeout=None):
        assert "getliststockdata" in url
        return [
            {"sym": "GEG", "lastPrice": 11.8, "r": 11.95, "totalVolume": 100000},
            {"sym": "FPT", "lastPrice": 55.2, "r": 55.8, "totalVolume": 100000,
             "timestamp": "2026-10-28T10:00:20+07:00"},
        ]

    fallback_row = {
        "symbol": "GEG", "price": 11800, "ref_price": 11950, "change_pct": -1.255,
        "volume": 1000, "volume_available": True, "provider": "VNStock-KBS-1m",
        "source_label": "VNStock-KBS (OHLCV 1m có timestamp)",
        "decision_usable": True, "freshness_verified": True,
        "freshness": "SOURCE_TIMESTAMP_VERIFIED", "freshness_age_seconds": 40,
        "source_timestamp": "2026-10-28T10:00:00+07:00", "freshness_reason": "verified",
    }
    monkeypatch.setattr(providers, "get_json", fake_get_json)
    monkeypatch.setattr(providers, "_fetch_vnstock_live_stock_quotes", lambda symbols, now=None: ({"GEG": fallback_row}, []))
    monkeypatch.setattr(providers, "_vndirect_daily_quote_is_usable", lambda *_args, **_kwargs: False)
    feeds, errors = providers.fetch_stock_feeds(["GEG", "FPT"], now=now)

    assert feeds["GEG"]["decision_usable"] is True
    assert feeds["GEG"]["source_label"] == "VNStock-KBS (OHLCV 1m có timestamp)"
    assert feeds["FPT"]["provider"] == "VPS"
    assert feeds["FPT"]["decision_usable"] is True
    assert not any("FPT" in err for err in errors)


def test_unresolved_symbol_stays_blocked_when_public_fallback_has_no_fresh_quote(monkeypatch):
    now = datetime(2026, 10, 28, 10, 0, 40, tzinfo=TZ)

    def fake_get_json(url, timeout=None):
        if "getliststockdata" in url:
            return [{"sym": "GEG", "lastPrice": 11.8, "r": 11.95, "totalVolume": 100000}]
        raise RuntimeError("offline in unit test")

    monkeypatch.setattr(providers, "get_json", fake_get_json)
    monkeypatch.setattr(providers, "_fetch_vnstock_live_stock_quotes", lambda symbols, now=None: ({}, ["GEG: no fresh fallback quote"]))
    monkeypatch.setattr(providers, "_vndirect_daily_quote_is_usable", lambda *_args, **_kwargs: False)
    feeds, errors = providers.fetch_stock_feeds(["GEG"], now=now)
    assert feeds["GEG"]["decision_usable"] is False
    assert feeds["GEG"]["freshness_verified"] is False
    assert "GEG" in " ".join(errors)


def test_vnstock_market_helpers_fail_closed_outside_live_window(monkeypatch):
    from quants.calendar_vn import is_live_market_window

    after_close = datetime(2026, 10, 28, 16, 0, 0, tzinfo=TZ)
    assert is_live_market_window(after_close) is False
    result, errors = providers._fetch_vnstock_live_stock_quotes(["GEG"], now=after_close)
    assert result == {}
    assert errors == []


def test_failed_vnstock_fallback_reason_is_attached_to_blocked_quote(monkeypatch):
    now = datetime(2026, 10, 28, 10, 0, 40, tzinfo=TZ)

    def fake_get_json(url, timeout=None):
        if "getliststockdata" in url:
            return [{"sym": "GEG", "lastPrice": 11.8, "r": 11.95, "totalVolume": 100000}]
        raise RuntimeError("offline in unit test")

    monkeypatch.setattr(providers, "get_json", fake_get_json)
    monkeypatch.setattr(
        providers, "_fetch_vnstock_live_stock_quotes",
        lambda symbols, now=None: ({}, [
            "GEG: KBS/VCI không có quote intraday được xác minh (Vnstock: API key required)"
        ])
    )
    monkeypatch.setattr(providers, "_vndirect_daily_quote_is_usable", lambda *_args, **_kwargs: False)
    feeds, errors = providers.fetch_stock_feeds(["GEG"], now=now)

    assert feeds["GEG"]["decision_usable"] is False
    assert "Dự phòng Vnstock KBS/VCI thất bại" in feeds["GEG"]["freshness_reason"]
    assert "API key required" in feeds["GEG"]["freshness_reason"]


def test_vnstock_login_license_error_falls_back_to_guest_without_retrying(monkeypatch):
    """A license-service failure must not suppress public VCI/KBS market routes."""
    import sys
    import types

    login_calls = []
    market_instance = object()
    fake_vnstock = types.ModuleType("vnstock")

    def fake_login(api_key):
        login_calls.append(api_key)
        raise RuntimeError("License service returned an unexpected response")

    fake_vnstock.login = fake_login
    fake_vnstock.Market = lambda: market_instance
    monkeypatch.setitem(sys.modules, "vnstock", fake_vnstock)
    monkeypatch.setattr(
        providers, "_runtime_setting",
        lambda name, default=None: "test-community-key" if name == "VNSTOCK_API_KEY" else default,
    )
    monkeypatch.setattr(providers, "_VNSTOCK_LOGIN_DONE", False)
    monkeypatch.setattr(providers, "_VNSTOCK_LOGIN_KEY", None)
    monkeypatch.setattr(providers, "_VNSTOCK_LOGIN_ATTEMPTED_KEY", None)
    monkeypatch.setattr(providers, "_VNSTOCK_LOGIN_ERROR", None)
    monkeypatch.setattr(providers, "_VNSTOCK_AUTH_MODE", "guest")

    first = providers._vnstock_market()
    second = providers._vnstock_market()

    assert first is market_instance and second is market_instance
    assert login_calls == ["test-community-key"]
    assert providers._VNSTOCK_AUTH_MODE == "guest_fallback_after_login_error"
    assert "License service returned an unexpected response" in providers._VNSTOCK_LOGIN_ERROR


def test_vnstock_no_api_key_uses_guest_without_login(monkeypatch):
    import sys
    import types

    login_calls = []
    market_instance = object()
    fake_vnstock = types.ModuleType("vnstock")
    fake_vnstock.login = lambda key: login_calls.append(key)
    fake_vnstock.Market = lambda: market_instance
    monkeypatch.setitem(sys.modules, "vnstock", fake_vnstock)
    monkeypatch.setattr(providers, "_runtime_setting", lambda name, default=None: default)
    monkeypatch.setattr(providers, "_VNSTOCK_LOGIN_DONE", False)
    monkeypatch.setattr(providers, "_VNSTOCK_LOGIN_KEY", None)
    monkeypatch.setattr(providers, "_VNSTOCK_LOGIN_ATTEMPTED_KEY", None)
    monkeypatch.setattr(providers, "_VNSTOCK_LOGIN_ERROR", None)
    monkeypatch.setattr(providers, "_VNSTOCK_AUTH_MODE", "guest")

    assert providers._vnstock_market() is market_instance
    assert login_calls == []
    assert providers._VNSTOCK_AUTH_MODE == "guest"


def test_vnstock_public_fallback_continues_when_login_license_service_fails(monkeypatch):
    """A failed optional login must not prevent public VCI OHLCV from being tried."""
    import sys
    import types

    now = datetime(2026, 10, 28, 10, 0, 50, tzinfo=TZ)
    login_calls = []

    class FakeEquity:
        def ohlcv(self, **kwargs):
            assert kwargs == {"length": 400, "interval": "1m", "source": "vci"}
            return pd.DataFrame([{
                "time": datetime(2026, 10, 28, 10, 0, 0),
                "open": 55.0, "high": 55.2, "low": 54.9,
                "close": 55.2, "volume": 500,
            }])

    class FakeMarket:
        def quote(self, symbols, source):
            # Production uses KBS for the reference quote and VCI only for minute OHLCV.
            assert symbols == ["FPT"]
            assert source == "kbs"
            return pd.DataFrame([{"symbol": "FPT", "reference_price": 55.8}])

        def equity(self, symbol):
            assert symbol == "FPT"
            return FakeEquity()

    fake_vnstock = types.ModuleType("vnstock")
    def fail_login(api_key):
        login_calls.append(api_key)
        raise RuntimeError("License service returned an unexpected response")
    fake_vnstock.login = fail_login
    fake_vnstock.Market = FakeMarket
    monkeypatch.setitem(sys.modules, "vnstock", fake_vnstock)
    monkeypatch.setattr(
        providers, "_runtime_setting",
        lambda name, default=None: "test-community-key" if name == "VNSTOCK_API_KEY" else default,
    )
    monkeypatch.setattr(providers, "_VNSTOCK_LOGIN_DONE", False)
    monkeypatch.setattr(providers, "_VNSTOCK_LOGIN_KEY", None)
    monkeypatch.setattr(providers, "_VNSTOCK_LOGIN_ATTEMPTED_KEY", None)
    monkeypatch.setattr(providers, "_VNSTOCK_LOGIN_ERROR", None)
    monkeypatch.setattr(providers, "_VNSTOCK_AUTH_MODE", "guest")

    rows, errors = providers._fetch_vnstock_live_stock_quotes(["FPT"], now=now)

    assert errors == []
    assert rows["FPT"]["decision_usable"] is True
    assert rows["FPT"]["freshness_verified"] is True
    assert rows["FPT"]["provider"] == "VNStock-VCI-1m"
    assert rows["FPT"]["source_auth_mode"] == "guest_fallback_after_login_error"
    assert "License service returned an unexpected response" in rows["FPT"]["source_auth_warning"]
    assert login_calls == ["test-community-key"]
