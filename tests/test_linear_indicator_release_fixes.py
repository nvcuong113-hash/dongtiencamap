from datetime import datetime
from zoneinfo import ZoneInfo

from quants import engine, providers
from quants.decision_report import _compute_continuous_linear_score
from quants.practical_flow import aggregate_sector_rotation


def _bar(date, close=100.0, volume=100.0, high=None, low=None):
    return {
        "date": date, "close": close, "volume": volume,
        "high": high if high is not None else close + 1,
        "low": low if low is not None else close - 1,
    }


def test_ma20_volume_baseline_excludes_target_session():
    # 21 prior observations at 100 shares, target session has a 10x volume spike.
    bars = []
    for i in range(21):
        bars.append(_bar(f"2026-09-{i + 1:02d}", volume=100.0))
    bars.append(_bar("2026-10-01", volume=1000.0))
    result = engine._weekly_and_structure_features(
        bars, now=datetime(2026, 10, 1, 16, 0, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh")),
        target_date="2026-10-01",
    )
    assert result["ma20_vol"] == 100.0


def test_weekly_closes_excludes_partial_current_week_on_tuesday():
    bars = [
        _bar("2026-09-04", close=100),
        _bar("2026-09-11", close=101),
        _bar("2026-09-18", close=102),
        _bar("2026-09-25", close=103),
        _bar("2026-10-02", close=104),
        _bar("2026-10-09", close=105),
        _bar("2026-10-16", close=106),
        _bar("2026-10-23", close=107),
        _bar("2026-10-26", close=108),  # Monday of the current week
        _bar("2026-10-27", close=109),  # Tuesday: partial week
    ]
    now = datetime(2026, 10, 27, 16, 0, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
    got = engine._weekly_closes_by_calendar(bars, now=now)
    assert got == [100, 101, 102, 103, 104, 105, 106, 107]


def test_weekly_closes_includes_current_week_after_friday_close():
    bars = [
        _bar("2026-10-16", close=106),
        _bar("2026-10-23", close=107),
        _bar("2026-10-30", close=108),
    ]
    now = datetime(2026, 10, 30, 15, 5, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
    assert engine._weekly_closes_by_calendar(bars, now=now) == [106, 107, 108]


def test_insufficient_factor_stack_does_not_change_directional_signal(monkeypatch):
    row = {
        "volume_available": True, "volume": 100000, "is_real_deal": False,
        "ma20_vol": 100000, "ma20w": None, "vwap_position": "Không có VWAP thực",
        "rs_vni": 1.0, "vol_ratio": 1.2, "buy_ratio": 50, "bottom_score": 50,
        "top_score": 30, "foreign_net_today": 0, "weekly_trend": "Chưa đủ lịch sử tuần/MA20W",
        "change_pct": 1.0, "session_current": True, "forecast_data_usable": True,
    }

    def fake_ensemble(score):
        return lambda features: {
            "factor": {"factor_coverage_pct": 44.4},
            "ensemble_score": score,
            "decision_state": "INSUFFICIENT_DATA",
        }

    monkeypatch.setattr(engine, "model_ensemble", fake_ensemble(0))
    low = engine._score(dict(row), index_valid=True, vni_change=0)[1]
    monkeypatch.setattr(engine, "model_ensemble", fake_ensemble(100))
    high = engine._score(dict(row), index_valid=True, vni_change=0)[1]
    assert low == high


def test_linear_composite_respects_zero_bottom_top_and_zero_persistence():
    row = {
        "rs_vni": 0, "vol_ratio": 1, "change_pct": 0, "bottom_score": 0,
        "top_score": 0, "is_real_deal": True, "flow_coverage_pct": 80,
        "net_flow_billion": 1.0, "flow_persistence_3_sessions_pct": 0,
    }
    got = _compute_continuous_linear_score(row, score=50, quality=83, macro_risk=0, levels=None)
    assert got["structure_component"] == 0
    assert got["flow_component"] == 0
    assert got["linear_score"] == 50.0
    assert got["quality_component"] == 0
    assert got["composite_method"] == "DEDUPED_HEURISTIC_V2"


def test_missing_sector_flow_is_not_reported_as_zero():
    got = aggregate_sector_rotation([
        {"sector": "Tech", "net_flow_billion": None, "is_real_deal": False},
        {"sector": "Tech", "net_flow_billion": None, "is_real_deal": False},
    ])["Tech"]
    assert got["sector_real_net_flow_billion"] is None
    assert got["sector_proxy_net_flow_billion"] is None
    assert got["sector_positive_flow_breadth_pct"] is None
    assert got["sector_watchlist_coverage"] == 0


def test_sector_benchmark_fails_closed_without_credentials(monkeypatch):
    monkeypatch.setattr(providers, "_ssi_credentials", lambda: ("", ""))
    got = providers.fetch_sector_benchmark_returns(
        ["Ngân hàng", "Khác"], "2026-10-27",
        now=datetime(2026, 10, 27, 16, 0, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh")),
    )
    assert got["Ngân hàng"]["rs_sector"] is None
    assert got["Ngân hàng"]["rs_sector_status"] == "SSI_CREDENTIALS_NOT_CONFIGURED"
    assert got["Khác"]["rs_sector"] is None
    assert got["Khác"]["rs_sector_status"] == "NO_MAPPED_EXTERNAL_BENCHMARK"


def test_sector_benchmark_uses_same_session_external_return_only(monkeypatch):
    monkeypatch.setattr(providers, "_ssi_credentials", lambda: ("id", "secret"))
    monkeypatch.setattr(providers, "_ssi_get_token", lambda: "token")
    monkeypatch.setattr(providers, "_ssi_index_codes", lambda token: {"VNFIN": "VNAllShare Financials"})
    monkeypatch.setattr(providers, "_ssi_index_history", lambda code, token: [
        {"date": "2026-10-26", "close": 100.0},
        {"date": "2026-10-27", "close": 101.0},
    ])
    got = providers.fetch_sector_benchmark_returns(
        ["Ngân hàng"], "2026-10-27",
        now=datetime(2026, 10, 27, 16, 0, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh")),
    )["Ngân hàng"]
    assert got["rs_sector"] == 1.0
    assert got["rs_sector_status"] == "VERIFIED_SAME_SESSION"
    assert got["rs_sector_source"] == "SSI FastConnect Data / DailyIndex"


def test_sector_benchmark_rejects_date_mismatch(monkeypatch):
    monkeypatch.setattr(providers, "_ssi_credentials", lambda: ("id", "secret"))
    monkeypatch.setattr(providers, "_ssi_get_token", lambda: "token")
    monkeypatch.setattr(providers, "_ssi_index_codes", lambda token: {"VNFIN": "VNAllShare Financials"})
    monkeypatch.setattr(providers, "_ssi_index_history", lambda code, token: [
        {"date": "2026-10-23", "close": 100.0},
        {"date": "2026-10-26", "close": 101.0},
    ])
    got = providers.fetch_sector_benchmark_returns(
        ["Ngân hàng"], "2026-10-27",
        now=datetime(2026, 10, 27, 16, 0, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh")),
    )["Ngân hàng"]
    assert got["rs_sector"] is None
    assert got["rs_sector_status"] == "BENCHMARK_DATE_MISMATCH"


def test_linear_composite_does_not_double_count_rs_volume_or_bottom_top():
    base = {
        "change_pct": 0.5, "is_real_deal": False, "net_flow_billion": None,
        "flow_persistence_3_sessions_pct": None, "bottom_score": 50, "top_score": 30,
    }
    a = _compute_continuous_linear_score(
        {**base, "rs_vni": -3.0, "vol_ratio": 0.5}, score=60, quality=95, macro_risk=0, levels=None
    )
    b = _compute_continuous_linear_score(
        {**base, "rs_vni": 3.0, "vol_ratio": 2.5, "bottom_score": 100, "top_score": 0},
        score=60, quality=20, macro_risk=0, levels=None
    )
    assert a["linear_score"] == b["linear_score"]
    assert a["rs_component"] == b["rs_component"] == 0
    assert a["vol_component"] == b["vol_component"] == 0
    assert a["rs_component_diagnostic_excluded"] != b["rs_component_diagnostic_excluded"]
    assert a["vol_component_diagnostic_excluded"] != b["vol_component_diagnostic_excluded"]


def test_sector_real_flow_breadth_does_not_mix_proxy_rows():
    got = aggregate_sector_rotation([
        {"sector": "Banks", "net_flow_billion": 2.0, "is_real_deal": True},
        {"sector": "Banks", "net_flow_billion": 1.0, "is_real_deal": True},
        {"sector": "Banks", "net_flow_billion": -100.0, "is_real_deal": False},
    ])["Banks"]
    assert got["sector_positive_flow_breadth_pct"] == 100.0
    assert got["sector_proxy_positive_flow_breadth_pct"] == 0.0
    assert got["sector_flow_state"] == "REAL_FLOW_INFLOW_BREADTH"


def test_sector_benchmark_does_not_assume_daily_index_is_realtime(monkeypatch):
    monkeypatch.setattr(providers, "_ssi_credentials", lambda: ("id", "secret"))
    def must_not_call():
        raise AssertionError("DailyIndex must not be used as a realtime source without validation")
    monkeypatch.setattr(providers, "_ssi_get_token", must_not_call)
    got = providers.fetch_sector_benchmark_returns(
        ["Ngân hàng"], "2026-10-27",
        now=datetime(2026, 10, 27, 10, 0, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh")),
    )["Ngân hàng"]
    assert got["rs_sector"] is None
    assert got["rs_sector_status"] == "INTRADAY_BENCHMARK_NOT_VERIFIED"


def test_watchlist_peer_score_excludes_self_and_requires_three_other_peers():
    three = [
        {"symbol": "A", "sector": "Tech", "signal_score": 90, "volume_available": True},
        {"symbol": "B", "sector": "Tech", "signal_score": 50, "volume_available": True},
        {"symbol": "C", "sector": "Tech", "signal_score": 40, "volume_available": True},
    ]
    engine._assign_watchlist_peer_scores(three)
    assert all(row["score_vs_sector"] is None for row in three)

    four = [
        {"symbol": "A", "sector": "Tech", "signal_score": 90, "volume_available": True},
        {"symbol": "B", "sector": "Tech", "signal_score": 50, "volume_available": True},
        {"symbol": "C", "sector": "Tech", "signal_score": 40, "volume_available": True},
        {"symbol": "D", "sector": "Tech", "signal_score": 30, "volume_available": True},
    ]
    engine._assign_watchlist_peer_scores(four)
    assert four[0]["score_vs_sector"] == 50.0  # 90 - mean(50, 40, 30)
    assert four[0]["score_vs_sector_status"] == "WATCHLIST_PEER_AVERAGE"
