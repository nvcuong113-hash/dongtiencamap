from __future__ import annotations

from datetime import datetime, timezone

from quants import providers


def _epoch(day: int) -> int:
    return int(datetime(2026, 10, day, 3, 0, tzinfo=timezone.utc).timestamp())


def test_vndirect_history_preserves_real_open_and_complete_ohlcv(monkeypatch):
    payload = {
        "t": [_epoch(d) for d in range(20, 25)],
        "o": [10.0, 10.1, 10.2, 10.3, 10.4],
        "h": [10.3, 10.4, 10.5, 10.6, 10.7],
        "l": [9.9, 10.0, 10.1, 10.2, 10.3],
        "c": [10.2, 10.3, 10.4, 10.5, 10.6],
        "v": [1000, 1100, 1200, 1300, 1400],
    }
    monkeypatch.setattr(providers, "get_json", lambda *args, **kwargs: payload)
    monkeypatch.setattr("quants.dnse_provider.dnse_market_data_enabled", lambda: False)

    bars, source, errors = providers.fetch_history("NKG", days=30)

    assert source == "VNDIRECT"
    assert errors == []
    assert len(bars) == 5
    assert all(set(("date", "open", "high", "low", "close", "volume")) <= set(row) for row in bars)
    assert bars[0]["open"] == 10000.0
    assert bars[0]["close"] == 10200.0


def test_vndirect_missing_open_falls_back_to_tcbs_without_fabricating(monkeypatch):
    vndirect_payload = {
        "t": [_epoch(d) for d in range(20, 25)],
        "h": [10.3] * 5, "l": [9.9] * 5, "c": [10.2] * 5, "v": [1000] * 5,
    }
    tcbs_payload = {"data": [
        {"tradingDate": f"2026-10-{day}", "open": 10.0, "high": 10.3,
         "low": 9.9, "close": 10.2, "volume": 1000}
        for day in range(20, 25)
    ]}
    responses = iter([vndirect_payload, tcbs_payload])
    calls = {"n": 0}
    def fake_get_json(*args, **kwargs):
        calls["n"] += 1
        return next(responses)
    monkeypatch.setattr(providers, "get_json", fake_get_json)
    monkeypatch.setattr("quants.dnse_provider.dnse_market_data_enabled", lambda: False)

    bars, source, errors = providers.fetch_history("NKG", days=30)

    assert source == "TCBS"
    assert len(bars) == 5
    assert bars[0]["open"] == 10000.0
    assert errors == []
    assert calls["n"] == 2
    from quants.smf_v2.data_quality import normalize_daily_bars
    normalized, quality = normalize_daily_bars(bars, as_of_date="2026-10-24")
    assert len(normalized) == 5
    assert quality["invalid_bars_dropped"] == 0


def test_invalid_ohlc_rows_are_dropped_instead_of_close_imputation(monkeypatch):
    payload = {
        "t": [_epoch(d) for d in range(20, 25)],
        "o": [10.0] * 5,
        "h": [9.5] * 5,  # Invalid: high below open/close.
        "l": [9.0] * 5,
        "c": [10.2] * 5,
        "v": [1000] * 5,
    }
    monkeypatch.setattr(providers, "get_json", lambda *args, **kwargs: payload)
    monkeypatch.setattr("quants.dnse_provider.dnse_market_data_enabled", lambda: False)

    bars, source, errors = providers.fetch_history("NKG", days=30)

    assert bars == []
    assert source == "Unavailable"
    assert len(errors) == 2


def test_provider_bars_pass_smf_v2_quality_and_native_structure_evidence(monkeypatch):
    from datetime import date, timedelta
    import sqlite3
    from quants.db import SCHEMA
    from quants.smf_v2.orchestrator import analyze_shadow_engines
    from quants.smf_v2.evidence import extract_row_evidence
    from quants.smf_v2.persistence import persist_market_bars

    start = date(2026, 7, 1)
    bars = []
    for i in range(80):
        close = 100 + i * 0.08 + (2 if (i // 6) % 2 == 0 else -2)
        bars.append({
            "date": (start + timedelta(days=i)).isoformat(),
            "open": close - 0.3, "high": close + 1, "low": close - 1,
            "close": close, "volume": 1000 + (i % 5) * 200,
        })
    result = analyze_shadow_engines(bars, as_of_date=bars[-1]["date"])
    assert result["data_quality"]["status"] == "AVAILABLE"
    assert result["data_quality"]["bars_used"] == 80
    assert result["market_structure"]["status"] == "AVAILABLE"
    row = {
        "symbol": "NKG", "trade_date": bars[-1]["date"],
        "smf_v2_market_structure": result["market_structure"],
        "smf_v2_vsa": result["vsa"],
    }
    evidence = extract_row_evidence(row)
    assert any(item.source_name == "market_structure_v2" for item in evidence)

    connection = sqlite3.connect(":memory:")
    connection.executescript(SCHEMA)
    inserted = persist_market_bars(
        connection, "NKG", bars, "provider-fixture", "2026-09-18T16:00:00+07:00",
        as_of_date=bars[-1]["date"],
    )
    assert inserted == 80
    assert connection.execute("SELECT COUNT(*) FROM market_bars WHERE symbol='NKG'").fetchone()[0] == 80
