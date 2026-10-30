from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from quants import dnse_provider

TZ = ZoneInfo("Asia/Ho_Chi_Minh")


class FakeDNSEClient:
    def __init__(self, trade=None, quote=None, bars=None, status=200):
        self.trade = trade if trade is not None else {
            "data": {"symbol": "HPG", "price": 22.55, "timestamp": "2026-10-30T10:00:00+07:00"}
        }
        self.quote = quote if quote is not None else {
            "data": {"symbol": "HPG", "referencePrice": 22.40,
                     "bidPrice": 22.50, "askPrice": 22.55}
        }
        self.bars = bars if bars is not None else {
            "data": [
                {"timestamp": "2026-10-30T09:59:00+07:00", "open": 22.4,
                 "high": 22.5, "low": 22.3, "close": 22.45, "volume": 1000},
                {"timestamp": "2026-10-30T10:00:00+07:00", "open": 22.45,
                 "high": 22.6, "low": 22.4, "close": 22.55, "volume": 2000},
            ]
        }
        self.status = status
        self.calls = []

    def get_latest_trade(self, **kwargs):
        self.calls.append(("get_latest_trade", kwargs))
        return self.status, self.trade

    def get_latest_quote(self, **kwargs):
        self.calls.append(("get_latest_quote", kwargs))
        return self.status, self.quote

    def get_ohlc(self, **kwargs):
        self.calls.append(("get_ohlc", kwargs))
        return self.status, self.bars

    def get_trades_volume_profile(self, **kwargs):
        self.calls.append(("get_trades_volume_profile", kwargs))
        return self.status, {"data": [{"price": 22.5, "buyVolume": 100, "sellVolume": 80}]}


def test_dnse_snapshot_normalizes_display_price_and_sums_same_session_volume(monkeypatch):
    now = datetime(2026, 10, 30, 10, 0, 30, tzinfo=TZ)
    client = FakeDNSEClient()
    monkeypatch.setattr(dnse_provider, "_setting", lambda name, default=None: {
        "QUANTS_MAX_QUOTE_AGE_SECONDS": "180",
        "DNSE_QUOTE_CACHE_SECONDS": "0",
        "DNSE_PRICE_DISPLAY_THRESHOLD": "300",
    }.get(name, default))
    row = dnse_provider.fetch_dnse_symbol("HPG", now=now, client=client, use_cache=False)
    assert row["price"] == 22550
    assert row["ref_price"] == 22400
    assert row["volume"] == 3000
    assert row["dnse_session_bar_count"] == 2
    assert row["freshness_verified"] is True
    assert row["decision_usable"] is True
    assert row["vwap"] is None
    assert [call[0] for call in client.calls] == ["get_latest_trade", "get_latest_quote", "get_ohlc"]
    assert client.calls[0][1]["board_id"] == "G1"
    assert client.calls[1][1]["board_id"] == "G1"


def test_dnse_stale_trade_is_not_decision_eligible(monkeypatch):
    now = datetime(2026, 10, 30, 10, 10, 0, tzinfo=TZ)
    client = FakeDNSEClient(trade={"data": {"price": 22.55, "timestamp": "2026-10-30T10:00:00+07:00"}})
    monkeypatch.setattr(dnse_provider, "_setting", lambda name, default=None: {
        "QUANTS_MAX_QUOTE_AGE_SECONDS": "180", "DNSE_QUOTE_CACHE_SECONDS": "0"
    }.get(name, default))
    row = dnse_provider.fetch_dnse_symbol("HPG", now=now, client=client, use_cache=False)
    assert row["freshness_verified"] is False
    assert row["decision_usable"] is False
    assert row["freshness"] == "SOURCE_TIMESTAMP_STALE"


def test_dnse_missing_reference_or_session_volume_stays_blocked(monkeypatch):
    now = datetime(2026, 10, 30, 10, 0, 30, tzinfo=TZ)
    client = FakeDNSEClient(
        quote={"data": {"bidPrice": 22.5, "askPrice": 22.6}},
        bars={"data": []},
    )
    monkeypatch.setattr(dnse_provider, "_setting", lambda name, default=None: {
        "QUANTS_MAX_QUOTE_AGE_SECONDS": "180", "DNSE_QUOTE_CACHE_SECONDS": "0"
    }.get(name, default))
    row = dnse_provider.fetch_dnse_symbol("HPG", now=now, client=client, use_cache=False)
    assert row["decision_usable"] is False
    assert row["volume_available"] is False
    assert "thiếu giá khớp hoặc giá tham chiếu" in row["freshness_reason"]


def test_dnse_future_timestamp_is_blocked(monkeypatch):
    now = datetime(2026, 10, 30, 10, 0, 30, tzinfo=TZ)
    client = FakeDNSEClient(trade={"data": {"price": 22.55, "timestamp": "2026-10-30T10:05:00+07:00"}})
    monkeypatch.setattr(dnse_provider, "_setting", lambda name, default=None: {
        "QUANTS_MAX_QUOTE_AGE_SECONDS": "180", "DNSE_QUOTE_CACHE_SECONDS": "0"
    }.get(name, default))
    row = dnse_provider.fetch_dnse_symbol("HPG", now=now, client=client, use_cache=False)
    assert row["freshness"] == "SOURCE_TIMESTAMP_IN_FUTURE"
    assert row["decision_usable"] is False


def test_dnse_volume_profile_probe_is_sanitized():
    client = FakeDNSEClient()
    result = dnse_provider.fetch_dnse_volume_profile("HPG", trade_date="2026-10-30", client=client)
    assert result["status"] == "OK"
    assert result["row_count"] == 1
    assert "buyVolume" in result["field_names"]
    assert "api_secret" not in str(result).lower()
    assert client.calls[-1][1]["board_id"] == "G1"


def _fake_dnse_row(symbol, stamp):
    return {
        "symbol": symbol, "price": 22560, "ref_price": 22400, "change_pct": 0.714,
        "volume": 3000, "volume_available": True, "provider": "DNSE",
        "source_label": "DNSE LightSpeed REST", "decision_usable": True,
        "freshness_verified": True, "freshness": "SOURCE_TIMESTAMP_VERIFIED",
        "source_timestamp": stamp.isoformat(), "freshness_age_seconds": 2.0,
        "freshness_reason": "fresh", "dnse_status": "READY", "dnse_session_bar_count": 2,
        "dnse_bid_price": 22550, "dnse_ask_price": 22560,
        "raw_fields": {"trade": ["price", "timestamp"], "quote": ["referencePrice"], "ohlc_bar_count": 2},
    }


def test_dnse_shadow_does_not_replace_primary_quote(monkeypatch):
    from quants import providers, dnse_provider
    now = datetime(2026, 10, 28, 10, 0, 40, tzinfo=TZ)
    stamp = now - timedelta(seconds=2)
    monkeypatch.setattr(providers, "get_json", lambda url, timeout=None: [
        {"sym": "HPG", "lastPrice": 22.55, "r": 22.40, "totalVolume": 100000,
         "timestamp": stamp.isoformat()}
    ])
    monkeypatch.setattr(providers, "_runtime_setting", lambda name, default=None: default)
    monkeypatch.setattr(providers, "_fetch_vnstock_live_stock_quotes", lambda symbols, now=None: ({}, []))
    monkeypatch.setattr(dnse_provider, "dnse_market_data_enabled", lambda: True, raising=False)
    monkeypatch.setattr(dnse_provider, "dnse_use_for_decisions", lambda: False, raising=False)
    monkeypatch.setattr(dnse_provider, "fetch_dnse_stock_quotes", lambda symbols, now=None: ({"HPG": _fake_dnse_row("HPG", stamp)}, []), raising=False)
    feeds, errors = providers.fetch_stock_feeds(["HPG"], now=now)
    assert feeds["HPG"]["provider"] == "VPS"
    assert feeds["HPG"]["dnse_shadow"]["price"] == 22560
    assert feeds["HPG"]["crosscheck_status"] == "DNSE_PRICE_MATCH"
    assert feeds["HPG"]["feeds"]["DNSE"]["provider"] == "DNSE"


def test_dnse_can_be_promoted_only_when_row_is_eligible(monkeypatch):
    from quants import providers, dnse_provider
    now = datetime(2026, 10, 28, 10, 0, 40, tzinfo=TZ)
    stamp = now - timedelta(seconds=2)
    monkeypatch.setattr(providers, "get_json", lambda url, timeout=None: [
        {"sym": "HPG", "lastPrice": 22.55, "r": 22.40, "totalVolume": 100000,
         "timestamp": stamp.isoformat()}
    ])
    monkeypatch.setattr(providers, "_fetch_vnstock_live_stock_quotes", lambda symbols, now=None: ({}, []))
    monkeypatch.setattr(dnse_provider, "dnse_market_data_enabled", lambda: True, raising=False)
    monkeypatch.setattr(dnse_provider, "dnse_use_for_decisions", lambda: True, raising=False)
    monkeypatch.setattr(dnse_provider, "fetch_dnse_stock_quotes", lambda symbols, now=None: ({"HPG": _fake_dnse_row("HPG", stamp)}, []), raising=False)
    feeds, errors = providers.fetch_stock_feeds(["HPG"], now=now)
    assert feeds["HPG"]["provider"] == "DNSE"
    assert feeds["HPG"]["decision_usable"] is True
    assert feeds["HPG"]["crosscheck_status"] == "DNSE_PRICE_MATCH"


def test_dnse_invalid_row_does_not_replace_valid_primary_quote(monkeypatch):
    from quants import providers, dnse_provider
    now = datetime(2026, 10, 28, 10, 0, 40, tzinfo=TZ)
    stamp = now - timedelta(seconds=2)
    monkeypatch.setattr(providers, "get_json", lambda url, timeout=None: [
        {"sym": "HPG", "lastPrice": 22.55, "r": 22.40, "totalVolume": 100000,
         "timestamp": stamp.isoformat()}
    ])
    monkeypatch.setattr(providers, "_fetch_vnstock_live_stock_quotes", lambda symbols, now=None: ({}, []))
    invalid = _fake_dnse_row("HPG", stamp)
    invalid["decision_usable"] = False
    monkeypatch.setattr(dnse_provider, "dnse_market_data_enabled", lambda: True, raising=False)
    monkeypatch.setattr(dnse_provider, "dnse_use_for_decisions", lambda: True, raising=False)
    monkeypatch.setattr(dnse_provider, "fetch_dnse_stock_quotes", lambda symbols, now=None: ({"HPG": invalid}, []), raising=False)
    feeds, errors = providers.fetch_stock_feeds(["HPG"], now=now)
    assert feeds["HPG"]["provider"] == "VPS"
    assert feeds["HPG"]["decision_usable"] is True


def test_dnse_daily_history_normalizes_and_validates_ohlcv(monkeypatch):
    class DailyClient:
        def __init__(self):
            self.calls = []

        def get_ohlc(self, **kwargs):
            self.calls.append(kwargs)
            return 200, {"data": [
                {"timestamp": f"2026-10-{day:02d}T00:00:00+07:00",
                 "open": 22.0, "high": 23.0, "low": 21.5,
                 "close": 22.5, "volume": 1000 + day}
                for day in range(20, 26)
            ]}

    monkeypatch.setattr(dnse_provider, "_setting", lambda name, default=None: {
        "DNSE_DAILY_RESOLUTION": "1D", "DNSE_PRICE_DISPLAY_THRESHOLD": "300"
    }.get(name, default))
    client = DailyClient()
    bars = dnse_provider.fetch_dnse_daily_history("HPG", days=30, client=client)
    assert len(bars) == 6
    assert bars[0] == {
        "date": "2026-10-20", "open": 22000.0, "high": 23000.0,
        "low": 21500.0, "close": 22500.0, "volume": 1020,
    }
    assert client.calls[0]["bar_type"] == "STOCK"
    assert client.calls[0]["query"]["resolution"] == "1D"
    assert client.calls[0]["query"]["symbol"] == "HPG"


def test_dnse_daily_history_rejects_bad_bars_and_deduplicates(monkeypatch):
    class DailyClient:
        def get_ohlc(self, **kwargs):
            good = {"timestamp": "2026-10-20", "open": 22, "high": 23,
                    "low": 21, "close": 22.5, "volume": 100}
            rows = [dict(good) for _ in range(5)]
            rows.append({"timestamp": "2026-10-21", "open": 22, "high": 20,
                         "low": 21, "close": 22, "volume": 100})
            return 200, {"data": rows}

    monkeypatch.setattr(dnse_provider, "_setting", lambda name, default=None: default)
    # Five duplicate dates collapse to one session; invalid OHLC is discarded.
    assert dnse_provider.fetch_dnse_daily_history("HPG", client=DailyClient()) == []


def test_dnse_official_sdk_trade_quote_and_security_definition_schema(monkeypatch):
    now = datetime(2026, 10, 30, 10, 0, 30, tzinfo=TZ)

    class OfficialShapeClient(FakeDNSEClient):
        def __init__(self):
            super().__init__(
                trade={"data": {
                    "symbol": "HPG", "matchPrice": 22.55, "matchQtty": 100,
                    "totalVolumeTraded": 3000, "time": "2026-10-30T10:00:00+07:00",
                    "highestPrice": 22.6, "lowestPrice": 22.3, "openPrice": 22.4,
                }},
                quote={"data": {
                    "symbol": "HPG", "bid": [{"price": 22.5, "qtty": 500}],
                    "offer": [{"price": 22.55, "qtty": 600}],
                    "totalBidQtty": 500, "totalOfferQtty": 600,
                    "time": "2026-10-30T10:00:00+07:00",
                }},
            )

        def get_security_definition(self, **kwargs):
            self.calls.append(("get_security_definition", kwargs))
            return 200, {"data": {
                "symbol": "HPG", "boardId": "G1", "basicPrice": 22.4,
                "ceilingPrice": 24.0, "floorPrice": 20.8,
            }}

    monkeypatch.setattr(dnse_provider, "_setting", lambda name, default=None: {
        "QUANTS_MAX_QUOTE_AGE_SECONDS": "180", "DNSE_QUOTE_CACHE_SECONDS": "0",
        "DNSE_PRICE_DISPLAY_THRESHOLD": "300", "DNSE_DEFAULT_BOARD_ID": "G1",
    }.get(name, default))
    client = OfficialShapeClient()
    row = dnse_provider.fetch_dnse_symbol("HPG", now=now, client=client, use_cache=False)
    assert row["price"] == 22550
    assert row["ref_price"] == 22400
    assert row["dnse_bid_price"] == 22500
    assert row["dnse_ask_price"] == 22550
    assert row["decision_usable"] is True
    assert [item[0] for item in client.calls] == [
        "get_latest_trade", "get_latest_quote", "get_ohlc", "get_security_definition"
    ]
    assert client.calls[-1][1] == {"symbol": "HPG", "board_id": None}


def test_dnse_diagnostics_after_close_treats_stale_quote_as_expected(monkeypatch):
    import sys
    import types
    import importlib.metadata
    import importlib.util
    from datetime import datetime

    class ProbeClient:
        def get_latest_trade(self, **kwargs):
            return 200, {"data": {"symbol": "HPG", "matchPrice": 22.55,
                                  "time": "2026-10-30T14:30:00+07:00"}}

        def get_latest_quote(self, **kwargs):
            return 200, {"data": {"symbol": "HPG", "time": "2026-10-30T14:30:00+07:00"}}

        def get_ohlc(self, **kwargs):
            return 200, {"data": []}

    monkeypatch.setitem(sys.modules, "dnse", types.SimpleNamespace(DNSEClient=object))
    monkeypatch.setattr(importlib.metadata, "version", lambda name: "2.4.1", raising=False)
    monkeypatch.setattr(dnse_provider, "dnse_credentials_configured", lambda: True)
    monkeypatch.setattr(dnse_provider, "dnse_market_data_enabled", lambda: True)
    monkeypatch.setattr(dnse_provider, "dnse_use_for_decisions", lambda: False)
    monkeypatch.setattr(dnse_provider, "_get_client", lambda: ProbeClient())
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: types.SimpleNamespace(origin="/venv/site-packages/dnse/__init__.py"))

    result = dnse_provider.run_dnse_diagnostics(
        "HPG", now=datetime(2026, 10, 30, 14, 47, tzinfo=TZ)
    )
    assert result["market_state"] == "AFTER_CLOSE"
    assert result["import_ok"] is True
    assert result["client_init_ok"] is True
    assert result["api_probe_ok"] is True
    assert result["quote_status"] == "CONNECTED_AFTER_CLOSE_STALE_EXPECTED"
    assert result["decision_mode"] == "SHADOW"


def test_dnse_diagnostics_preserves_sdk_import_error(monkeypatch):
    import sys
    import types
    import importlib.metadata
    import importlib.util
    from datetime import datetime

    monkeypatch.setitem(sys.modules, "dnse", None)
    monkeypatch.setattr(importlib.metadata, "version", lambda name: "2.4.1", raising=False)
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    result = dnse_provider.run_dnse_diagnostics(
        "HPG", now=datetime(2026, 10, 30, 14, 47, tzinfo=TZ)
    )
    assert result["module_found"] is False
    assert result["import_ok"] is False
    assert result["error"].startswith("SDK_IMPORT_")
