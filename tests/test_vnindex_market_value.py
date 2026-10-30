from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from quants import providers

TZ = ZoneInfo("Asia/Ho_Chi_Minh")


@pytest.fixture(autouse=True)
def clear_vnindex_value_cache():
    providers._VNINDEX_VALUE_CACHE.clear()
    providers._VNDIRECT_MARKET_VALUE_CACHE.clear()
    yield
    providers._VNINDEX_VALUE_CACHE.clear()
    providers._VNDIRECT_MARKET_VALUE_CACHE.clear()


def test_vps_market_value_parser_is_case_insensitive_and_accepts_provider_aliases():
    # Some feed versions use different casing; value below is in million VND.
    assert providers._index_value_billion({"TotalVal": 15_000_000}) == 15_000.0
    # Full VND amount from another feed schema.
    assert providers._index_value_billion({"TOTAL_VALUE": 15_000_000_000_000}) == 15_000.0
    assert providers._index_value_billion({"totalDealVal": 15_000_000}) == 15_000.0


def test_vnstock_trade_history_fills_missing_vnindex_market_value_for_exact_session(monkeypatch):
    current = datetime(2026, 10, 29, 15, 10, tzinfo=TZ)
    monkeypatch.setattr(providers, "_vnstock_has_paid_entitlement", lambda: True)

    class FakeIndex:
        def trade_history(self, limit=5, source=None):
            assert limit == 5
            assert source == "vci"
            return [
                {"trading_date": "2026-10-29", "total_value": 27_605_070_000_000},
                {"trading_date": "2026-10-28", "total_value": 18_000_000_000_000},
            ]

    class FakeMarket:
        def index(self, symbol):
            assert symbol == "VNINDEX"
            return FakeIndex()

    monkeypatch.setattr(providers, "_vnstock_market", lambda: FakeMarket())
    result = providers._vnstock_index_market_value(current)

    assert result["value_billion"] == pytest.approx(27_605.07)
    assert result["value_source"] == "VNstock index.trade_history (total_value, VND)"
    assert result["value_date"] == "2026-10-29"


def test_vnstock_market_value_fallback_does_not_borrow_previous_session(monkeypatch):
    current = datetime(2026, 10, 29, 15, 10, tzinfo=TZ)
    monkeypatch.setattr(providers, "_vnstock_has_paid_entitlement", lambda: True)

    class FakeIndex:
        def trade_history(self, limit=5, source=None):
            return [{"trading_date": "2026-10-28", "total_value": 18_000_000_000_000}]

    class FakeMarket:
        def index(self, symbol):
            return FakeIndex()

    monkeypatch.setattr(providers, "_vnstock_market", lambda: FakeMarket())
    result = providers._vnstock_index_market_value(current)

    assert result["value_billion"] is None
    assert result["value_date"] is None
    assert "không có total_value hợp lệ" in result["value_fallback_reason"]


def test_vps_index_quote_enriches_missing_value_from_vnstock_trade_history(monkeypatch):
    current = datetime(2026, 10, 29, 10, 1, tzinfo=TZ)
    monkeypatch.setattr(providers, "_vnstock_has_paid_entitlement", lambda: True)
    monkeypatch.setattr(providers, "_vndirect_market_value_billion", lambda *args, **kwargs: {
        "value_billion": None, "value_fallback_reason": "fixture: no Finfo row"
    })

    monkeypatch.setattr(
        providers,
        "get_json",
        lambda *args, **kwargs: [
            {
                "mc": "10",
                "cIndex": 1754.24,
                "oIndex": 1766.65,
                "vol": 403_400_000,
                "timestamp": "2026-10-29T10:00:00+07:00",
            }
        ],
    )

    class FakeIndex:
        def trade_history(self, limit=5, source=None):
            return [{"trading_date": "2026-10-29", "total_value": 13_500_000_000_000}]

    class FakeMarket:
        def index(self, symbol):
            return FakeIndex()

    monkeypatch.setattr(providers, "_vnstock_market", lambda: FakeMarket())
    result, errors = providers.fetch_vnindex(now=current)

    assert result["provider"] == "VPS-index-detail"
    assert result["value_billion"] == pytest.approx(13_500.0)
    assert result["value_source"] == "VNstock index.trade_history (total_value, VND)"
    assert result["value_date"] == "2026-10-29"
    assert errors == []


def test_market_value_fallback_failure_does_not_break_valid_index_quote(monkeypatch):
    current = datetime(2026, 10, 29, 15, 10, tzinfo=TZ)
    monkeypatch.setattr(providers, "_vndirect_market_value_billion", lambda *args, **kwargs: {
        "value_billion": None, "value_fallback_reason": "Finfo unavailable"
    })
    monkeypatch.setattr(providers, "_vnstock_has_paid_entitlement", lambda: True)

    class FakeIndex:
        def trade_history(self, limit=5, source=None):
            raise RuntimeError("VNSTOCK_TIER_ROUTE: không có quyền sử dụng")

    class FakeMarket:
        def index(self, symbol):
            return FakeIndex()

    monkeypatch.setattr(providers, "_vnstock_market", lambda: FakeMarket())
    quote = {"price": 1754.24, "value_billion": None, "valid": True}
    result = providers._enrich_vnindex_market_value(quote, current)

    assert result["price"] == 1754.24
    assert result["value_billion"] is None
    assert "Finfo unavailable" in result["value_fallback_reason"]
    assert "VNSTOCK_TIER_ROUTE" in result["value_fallback_reason"]


def test_community_tier_does_not_call_paid_index_trade_history(monkeypatch):
    current = datetime(2026, 10, 29, 15, 10, tzinfo=TZ)

    class FakeIndex:
        def trade_history(self, *args, **kwargs):
            raise AssertionError("Community must not call this paid-tier route")

    class FakeMarket:
        def index(self, symbol):
            raise AssertionError("Community must short-circuit before creating a market client")

    monkeypatch.setattr(providers, "_vnstock_has_paid_entitlement", lambda: False)
    monkeypatch.setattr(providers, "_vnstock_market", lambda: FakeMarket())
    result = providers._vnstock_index_market_value(current)

    assert result["value_billion"] is None
    assert result["value_source"] is None
    assert result["value_date"] is None
    assert "Cộng đồng không mở" in result["value_fallback_reason"]


def test_vndirect_finfo_market_turnover_returns_total_matched_plus_put_through(monkeypatch):
    current = datetime(2026, 10, 29, 15, 10, tzinfo=TZ)
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{
                "code": "VNINDEX", "date": "2026-10-29",
                "accumulatedVal": 27_605_070_000_000,
                "nmValue": 24_000_000_000_000,
                "ptValue": 3_605_070_000_000,
                "accumulatedVol": 900_000_000,
            }]}

    def fake_get(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return FakeResponse()

    monkeypatch.setattr(providers.SESSION, "get", fake_get)
    result = providers._vndirect_market_value_billion(current)

    assert captured["url"].endswith("/v4/vnmarket_prices")
    assert "date:gte:2026-10-29" in captured["params"]["q"]
    assert "date:lte:2026-10-29" in captured["params"]["q"]
    assert result["value_billion"] == pytest.approx(27_605.07)
    assert result["matched_value_billion"] == pytest.approx(24_000.0)
    assert result["put_through_value_billion"] == pytest.approx(3_605.07)
    assert result["value_date"] == "2026-10-29"


def test_vndirect_finfo_rejects_stale_or_wrong_date_rows(monkeypatch):
    current = datetime(2026, 10, 29, 15, 10, tzinfo=TZ)

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{
                "code": "VNINDEX", "date": "2026-10-28",
                "accumulatedVal": 18_000_000_000_000,
            }]}

    monkeypatch.setattr(providers.SESSION, "get", lambda *args, **kwargs: FakeResponse())
    result = providers._vndirect_market_value_billion(current)
    assert result["value_billion"] is None
    assert result["value_date"] is None
    assert "2026-10-29" in result["value_fallback_reason"]


def test_vndirect_is_preferred_for_community_vnindex_market_value(monkeypatch):
    current = datetime(2026, 10, 29, 15, 10, tzinfo=TZ)
    monkeypatch.setattr(providers, "_vndirect_market_value_billion", lambda *args, **kwargs: {
        "value_billion": 12_345.6, "value_source": "VNDIRECT Finfo fixture", "value_date": "2026-10-29"
    })
    monkeypatch.setattr(providers, "_vnstock_index_market_value", lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("Vnstock paid-tier route should not be needed when Finfo responds")
    ))
    quote = {"price": 1754.24, "value_billion": None, "valid": True}
    result = providers._enrich_vnindex_market_value(quote, current)
    assert result["value_billion"] == pytest.approx(12_345.6)
    assert result["value_source"] == "VNDIRECT Finfo fixture"


def test_vndirect_finfo_uses_last_completed_session_before_open(monkeypatch):
    current = datetime(2026, 10, 30, 8, 8, tzinfo=TZ)
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{
                "code": "VNINDEX", "date": "2026-10-29",
                "accumulatedVal": 21_000_000_000_000,
            }]}

    def fake_get(url, **kwargs):
        captured.update(kwargs)
        return FakeResponse()

    monkeypatch.setattr(providers.SESSION, "get", fake_get)
    result = providers._vndirect_market_value_billion(current)
    assert "date:gte:2026-10-29" in captured["params"]["q"]
    assert result["value_date"] == "2026-10-29"
    assert result["value_billion"] == pytest.approx(21_000.0)
