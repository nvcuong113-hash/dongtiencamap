from __future__ import annotations

from quants.smf_v2.data_quality import normalize_daily_bars
from quants.smf_v2.market_structure import analyze_market_structure
from quants.smf_v2.vsa import analyze_vsa
from quants.smf_v2.orchestrator import analyze_shadow_engines


def _bar(day, close=10.0, volume=1000):
    return {
        "date": day, "open": close, "high": close + 1,
        "low": close - 1, "close": close, "volume": volume,
    }


def test_normalizer_excludes_future_bars_and_preserves_missing_volume():
    bars = [_bar("2026-10-28"), _bar("2026-10-29", volume=None),
            _bar("2026-10-30"), _bar("2026-10-31")]
    clean, qc = normalize_daily_bars(bars, "2026-10-30")
    assert [x["date"] for x in clean] == ["2026-10-28", "2026-10-29", "2026-10-30"]
    assert clean[1]["volume"] is None
    assert qc["future_bars_excluded"] == 1
    assert qc["missing_volume_bars"] == 1
    assert qc["latest_observation_date"] == "2026-10-30"


def test_normalizer_drops_invalid_ohlc_and_collapses_duplicate_session():
    first = _bar("2026-10-28", close=10)
    last = _bar("2026-10-28", close=11)
    invalid = {"date": "2026-10-28", "open": 10, "high": 9, "low": 8, "close": 9, "volume": 100}
    clean, qc = normalize_daily_bars([first, invalid, last], "2026-10-28")
    assert len(clean) == 1
    assert clean[0]["close"] == 11
    assert qc["invalid_bars_dropped"] == 1
    assert qc["duplicate_sessions_collapsed"] == 1


def test_invalid_explicit_as_of_fails_closed_in_both_engines():
    bars = [_bar("2026-10-28"), _bar("2026-10-29")]
    structure = analyze_market_structure(bars, as_of_date="not-a-date")
    vsa = analyze_vsa(bars, as_of_date="not-a-date")
    assert structure["status"] == "INVALID_AS_OF_DATE"
    assert vsa["status"] == "INVALID_AS_OF_DATE"
    assert structure["bars_used"] == vsa["bars_used"] == 0
    assert structure["future_data_used"] is False
    assert vsa["future_data_used"] is False
    malformed_suffix = "2026-10-28garbage"
    assert analyze_market_structure(bars, as_of_date=malformed_suffix)["status"] == "INVALID_AS_OF_DATE"
    assert analyze_vsa(bars, as_of_date=malformed_suffix)["status"] == "INVALID_AS_OF_DATE"


def test_shadow_orchestrator_returns_shared_qc_without_decision_impact():
    bars = [_bar(f"2026-09-{day:02d}") for day in range(1, 31)]
    result = analyze_shadow_engines(bars, as_of_date="2026-09-30")
    assert result["schema_version"] == "2.1"
    assert result["data_quality"]["status"] == "AVAILABLE"
    assert result["market_structure"]["as_of_date"] == "2026-09-30"
    assert result["vsa"]["as_of_date"] == "2026-09-30"
    assert result["mode"] == "SHADOW"
    assert result["decision_impact"] == "NONE"
