from __future__ import annotations

from quants.smf_v2.macro_regime import analyze_macro_regime, analyze_sector_rotation


def make_series(factor, values, unit="index"):
    dates = [f"2026-10-{day:02d}" for day in range(26, 31)]
    return [{"factor": factor, "ts": d, "value": value, "change_pct": None,
             "unit": unit, "source": "Yahoo Finance test fixture", "impact": "PROXY"}
            for d, value in zip(dates, values)]


def test_macro_proxy_risk_off_uses_multiple_fresh_groups_only():
    rows = []
    rows += make_series("Emerging Markets ETF proxy", [100, 99, 98, 97, 95], "ETF")
    rows += make_series("S&P 500", [100, 99, 98, 97, 96], "index")
    rows += make_series("NASDAQ Composite", [100, 99, 98, 97, 96], "index")
    rows += make_series("VIX", [15, 16, 17, 18, 19], "index")
    rows += make_series("DXY", [100, 100.2, 100.4, 100.6, 101], "index")
    rows += make_series("US10Y", [4.0, 4.02, 4.05, 4.10, 4.15], "%")
    result = analyze_macro_regime(rows, as_of_date="2026-10-30")
    assert result["risk_tone"] == "RISK_OFF_PROXY"
    assert result["status"] == "PARTIAL"
    assert "MARKET_PROXY_ONLY_NOT_OFFICIAL_MACRO" in result["reason_codes"]


def test_future_macro_observations_are_excluded():
    rows = make_series("DXY", [100, 101, 102, 103, 104])
    rows += make_series("VIX", [15, 16, 17, 18, 19])
    result = analyze_macro_regime(rows, as_of_date="2026-10-28")
    assert result["latest_observation_date"] <= "2026-10-28"


def test_one_factor_cannot_create_risk_tone():
    result = analyze_macro_regime(make_series("DXY", [100, 101, 102, 103, 104]), as_of_date="2026-10-30")
    assert result["risk_tone"] == "INSUFFICIENT_DATA"


def test_sector_rotation_needs_three_watchlist_names_and_verified_sector_rs():
    rows = [
        {"symbol": "A", "sector": "Steel", "change_pct": 2, "rs_sector": 1.2, "rs_sector_status": "VERIFIED_SAME_SESSION"},
        {"symbol": "B", "sector": "Steel", "change_pct": 1, "rs_sector": 0.8, "rs_sector_status": "VERIFIED_SAME_SESSION"},
        {"symbol": "C", "sector": "Steel", "change_pct": 1.5, "rs_sector": 0.7, "rs_sector_status": "VERIFIED_SAME_SESSION"},
        {"symbol": "D", "sector": "Tech", "change_pct": 2, "rs_sector": 1, "rs_sector_status": "VERIFIED_SAME_SESSION"},
    ]
    result = analyze_sector_rotation(rows)
    steel = next(item for item in result["sectors"] if item["sector"] == "Steel")
    tech = next(item for item in result["sectors"] if item["sector"] == "Tech")
    assert steel["state"] == "LEADING_WATCHLIST_SECTOR"
    assert tech["state"] == "INSUFFICIENT_WATCHLIST_COVERAGE"


def test_shadow_report_contains_macro_and_sector_context_without_changing_consensus():
    from quants.smf_v2.shadow import build_shadow_report
    rows = [{"symbol": "NKG", "trade_date": "2026-10-30", "price": 100,
             "volume": 1000, "volume_available": True, "rs_vni": 0.2}]
    macro = make_series("DXY", [100, 100.1, 100.2, 100.3, 100.4])
    report = build_shadow_report(rows, macro_snapshots=macro)
    assert report["schema_version"] == "2.5"
    assert report["decision_impact"] == "NONE"
    assert report["macro_context"]["risk_tone"] == "INSUFFICIENT_DATA"
    assert report["sector_context"]["universe_scope"] == "CURRENT_WATCHLIST_ONLY"
    assert report["instruments"][0]["consensus"]["is_trade_signal"] is False


def test_missing_as_of_date_fails_closed_to_prevent_macro_lookahead():
    result = analyze_macro_regime(make_series("DXY", [100, 101, 102, 103, 104]))
    assert result["risk_tone"] == "INSUFFICIENT_DATA"
    assert result["factors"] == {}
    assert result["reason_codes"] == ["MISSING_OR_INVALID_AS_OF_DATE"]
