from quants.smf_v2.vsa import analyze_vsa


def _bars():
    rows = []
    # Stable baseline: 50 completed sessions, moderate volume and narrow spread.
    for i in range(50):
        close = 100.0 - (i * 0.03)
        rows.append({
            "date": f"2026-01-{(i % 28) + 1:02d}" if False else None,
            "open": close - 0.2, "high": close + 0.5,
            "low": close - 0.5, "close": close, "volume": 1000,
        })
    # Replace with a valid consecutive calendar sequence.
    import datetime
    start = datetime.date(2026, 1, 1)
    for i, row in enumerate(rows):
        row["date"] = (start + datetime.timedelta(days=i)).isoformat()
    # A sharp decline over the five sessions leading into the final event bar.
    rows[44].update({"open": 105.2, "high": 105.5, "low": 104.5, "close": 105.0, "volume": 1000})
    for j, idx in enumerate(range(45, 50)):
        close = 97.0 - j * 1.3
        rows[idx].update({"open": close + 0.2, "high": close + 0.5,
                          "low": close - 0.5, "close": close, "volume": 1000})
    rows[-1].update({"open": 90.0, "high": 95.0, "low": 88.0,
                     "close": 94.0, "volume": 2500})
    return rows


def test_vsa_detects_selling_climax_candidate_without_claiming_verified_absorption():
    result = analyze_vsa(_bars(), as_of_date="2026-02-19")
    assert result["status"] == "AVAILABLE"
    assert result["causal"] is True
    assert result["future_data_used"] is False
    assert any(e["event"] == "SELLING_CLIMAX_CANDIDATE" for e in result["events"])
    assert result["polarity"] == "BULLISH"
    assert result["quality_status"] == "PARTIAL"
    assert "chưa đối chiếu" in result["quality_reason"]


def test_vsa_excludes_future_bars_and_reports_duplicates():
    bars = _bars()
    last_valid = dict(bars[-1])
    bars.append({**last_valid, "date": "2026-02-20", "close": 99.0, "high": 100.0})
    bars.append(last_valid)
    result = analyze_vsa(bars, as_of_date="2026-02-19")
    assert result["future_bars_excluded"] == 1
    assert result["duplicate_sessions_collapsed"] == 1
    assert result["latest_observation_date"] == "2026-02-19"


def test_vsa_rejects_missing_or_invalid_volume():
    bars = _bars()
    bars[-1]["volume"] = None
    result = analyze_vsa(bars, as_of_date="2026-02-19")
    assert result["status"] == "VOLUME_UNAVAILABLE"
    assert result["events"] == []


def test_vsa_handles_zero_range_baseline_without_crashing():
    import datetime
    start = datetime.date(2026, 1, 1)
    bars = []
    for i in range(45):
        px = 10.0 + i * 0.01
        bars.append({"date": (start + datetime.timedelta(days=i)).isoformat(),
                     "open": px, "high": px, "low": px, "close": px, "volume": 1000})
    result = analyze_vsa(bars, as_of_date=bars[-1]["date"])
    assert result["status"] == "AVAILABLE"
    assert result["metrics"]["range_ratio_vs_prior_median"] is None
    assert result["events"] == []
