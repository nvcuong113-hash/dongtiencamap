from __future__ import annotations

from datetime import date, timedelta

from quants.smf_v2.accumulation_distribution import analyze_accumulation_distribution


def flat_bars(n=70):
    rows = []
    for i in range(n):
        close = 100.0
        session = (date(2026, 7, 1) + timedelta(days=i)).isoformat()
        rows.append({"date": session,
                     "open": close, "high": 101.0, "low": 99.0, "close": close, "volume": 1000})
    return rows


def test_insufficient_history_fails_closed():
    result = analyze_accumulation_distribution(flat_bars(30))
    assert result["status"] == "INSUFFICIENT_HISTORY"
    assert result["state"] == "UNKNOWN"
    assert result["future_data_used"] is False


def test_candidate_events_use_prior_only_bars_and_form_accumulation_state():
    bars = flat_bars(70)
    # Establish a five-session decline ending in a high-volume stopping bar.
    for offset, close in enumerate([105, 104, 102, 100], start=45):
        bars[offset].update(open=close + 1, high=close + 3, low=close - 2, close=close, volume=1000)
    bars[49].update(open=99, high=100, low=90, close=95, volume=3000)
    # A low-volume test close to the candidate low, with no close below it.
    bars[51].update(open=95, high=97, low=91, close=96, volume=500)
    result = analyze_accumulation_distribution(bars, as_of_date=bars[-1]["date"])
    event_names = {item["event"] for item in result["events"]}
    assert "SELLING_CLIMAX_CANDIDATE" in event_names or "STOPPING_VOLUME_CANDIDATE" in event_names
    assert "TEST_CANDIDATE" in event_names
    assert result["state"] == "ACCUMULATION_CANDIDATE"
    assert result["causal"] is True
    assert result["future_data_used"] is False
    assert result["quality_status"] in {"PARTIAL", "UNVERIFIED"}


def test_future_bars_are_excluded_from_state_replay():
    bars = flat_bars(70)
    result = analyze_accumulation_distribution(bars, as_of_date=bars[59]["date"])
    assert result["bars_used"] == 60
    assert result["as_of_date"] == bars[59]["date"]


def test_accumulation_distribution_evidence_is_non_voting():
    from quants.smf_v2.evidence import aggregate_independent_groups, extract_row_evidence

    row = {
        "symbol": "NKG", "trade_date": "2026-10-30", "price": 100,
        "volume": 1000, "volume_available": True,
        "smf_v2_accumulation_distribution": {
            "status": "AVAILABLE", "quality_status": "PARTIAL",
            "state": "ACCUMULATION_CANDIDATE", "state_strength": "LOW",
            "bullish_setup_score": 3, "bearish_setup_score": 0,
            "latest_events": [{"event": "TEST_CANDIDATE"}],
        },
    }
    records = extract_row_evidence(row)
    item = next(x for x in records if x.feature == "wyckoff_behavior_state")
    assert item.polarity == "BULLISH"
    assert item.contributes_to_consensus is False
    assert "ACCUMULATION_DISTRIBUTION" not in aggregate_independent_groups(records)["group_votes"]
