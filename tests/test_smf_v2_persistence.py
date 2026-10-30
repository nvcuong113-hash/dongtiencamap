import json
import sqlite3

from quants.db import SCHEMA
from quants.smf_v2.persistence import persist_market_bars, persist_shadow_report


def test_market_bars_append_only_and_deduplicated():
    connection = sqlite3.connect(":memory:")
    connection.executescript(SCHEMA)
    bars = [
        {"date": "2026-10-28", "open": 10, "high": 11, "low": 9.5, "close": 10.5, "volume": 1000},
        {"date": "2026-10-29", "open": 10.5, "high": 12, "low": 10, "close": 11.5, "volume": 1200},
        {"date": "2026-10-31", "open": 11.5, "high": 12, "low": 11, "close": 11.7, "volume": 800},
    ]
    assert persist_market_bars(connection, "abc", bars, "provider-x", "2026-10-30T09:00:00+07:00", "2026-10-30") == 2
    assert persist_market_bars(connection, "ABC", bars[:2], "provider-x", "2026-10-30T10:00:00+07:00", "2026-10-30") == 0
    revised = [{**bars[1], "close": 11.6, "high": 12.1}]
    assert persist_market_bars(connection, "ABC", revised, "provider-x", "2026-10-30T11:00:00+07:00", "2026-10-30") == 1
    assert connection.execute("SELECT COUNT(*) FROM market_bars").fetchone()[0] == 3
    assert connection.execute("SELECT COUNT(*) FROM market_bars WHERE session_date='2026-10-31'").fetchone()[0] == 0


def test_shadow_report_persisted_with_evidence(tmp_path):
    db_path = tmp_path / "smf.sqlite3"
    connection = sqlite3.connect(db_path)
    connection.executescript(SCHEMA)
    connection.commit()
    connection.close()
    report = {
        "generated_at": "2026-10-30T10:00:00+00:00", "schema_version": "2.1",
        "mode": "SHADOW", "decision_impact": "NONE", "instrument_count": 1,
        "evidence_count": 1,
        "instruments": [{"symbol": "ABC", "session_date": "2026-10-30", "evidence": [{
            "evidence_id": "e1", "engine": "vsa_volume_v2", "evidence_group": "VOLUME_VSA",
            "feature": "effort_result", "polarity": "BULLISH", "quality_status": "PARTIAL",
            "strength_label": "MEDIUM", "summary": "test", "source_name": "provider-x",
            "source_fields": ["close", "volume"], "contributes_to_consensus": True,
        }]}],
    }
    run_id = persist_shadow_report(report, str(db_path))
    connection = sqlite3.connect(db_path)
    assert connection.execute("SELECT as_of_date FROM smf_v2_runs WHERE id=?", (run_id,)).fetchone()[0] == "2026-10-30"
    row = connection.execute("SELECT evidence_group,quality_status FROM smf_v2_evidence WHERE run_id=?", (run_id,)).fetchone()
    assert row == ("VOLUME_VSA", "PARTIAL")
    connection.close()
