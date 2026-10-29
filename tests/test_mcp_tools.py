from __future__ import annotations

import json
from pathlib import Path

import pytest

from quants import db
from quants import mcp_tools


@pytest.fixture()
def mcp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "mcp_test.sqlite3")
    db.init_db()
    with db.connect() as conn:
        # Deliberately vary timestamp/freshness fields so diagnostics cannot silently infer them.
        conn.execute(
            """INSERT INTO stock_snapshots
            (ts,trade_date,symbol,sector,price,change_pct,volume,vwap,vwap_position,buy_vol,sell_vol,
             buy_ratio,net_flow_billion,delta_flow_open,rs_vni,rs_sector,quality_score,signal_score,p_win,
             wyckoff_phase,action_rec,ai_vsa,ai_action,pred_t2,pred_t3,pred_t7,source_label,is_real_deal,metrics_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("2026-10-29T10:00:00+07:00", "2026-10-29", "IDC", "KCN", 33600, 1.2, 200000,
             33100, "Trên VWAP", 120000, 80000, 0.6, 2.5, 1.1, 2.0, 1.0, 71, 77, 53,
             "Markup", "QUAN SÁT", "test", "QUAN SÁT", 0.3, 0.6, 1.2, "VCI intraday | Hist:VNDIRECT", 0,
             json.dumps({"source_timestamp": "2026-10-29T10:00:00+07:00", "freshness_age_seconds": 30,
                         "freshness_verified": True, "decision_usable": True, "forecast_data_usable": True,
                         "bottom_score": 68, "top_score": 32, "flow_confidence": "MEDIUM",
                         "feature_availability_pct": 88.0, "source_timestamp_candidate_fields": ["time"]})),
        )
        conn.execute(
            """INSERT INTO stock_snapshots (ts,trade_date,symbol,price,change_pct,volume,source_label,metrics_json)
               VALUES (?,?,?,?,?,?,?,?)""",
            ("2026-10-29T10:00:00+07:00", "2026-10-29", "NKG", 9450, -1.8, 0,
             "Hist:VNDIRECT", json.dumps({"source_timestamp": None, "freshness_verified": False,
                                           "decision_usable": False, "forecast_data_usable": False,
                                           "forecast_data_reason": "NO_INTRADAY_BAR_PROVIDER",
                                           "feature_availability_pct": 83.0})),
        )
        conn.execute("INSERT INTO system_events(ts,level,component,message) VALUES (?,?,?,?)",
                     ("2026-10-29T10:01:00+07:00", "WARN", "providers", "API error api_key=sk-testsecret12345678901234567890"))
        conn.execute(
            """INSERT INTO media_articles (url_hash,published_at,crawled_at,source,title,summary,url,symbols,symbols_json,raw_text,sentiment,sentiment_score,event_type)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("hash1", "2026-10-29T09:30:00+07:00", "2026-10-29T09:30:00+07:00", "Test RSS",
             "IDC mở rộng khu công nghiệp", "Bản tóm tắt thử nghiệm", "https://example.invalid/news/1",
             "IDC", '["IDC"]', "nội dung dài không được trả về toàn văn", "Trung tính", 0.0, "Company"),
        )
        conn.execute("INSERT INTO macro_snapshots(ts,factor,value,change_pct,unit,source,impact,analysis) VALUES (?,?,?,?,?,?,?,?)",
                     ("2026-10-29T08:00:00+07:00", "DXY", 101.2, 0.1, "index", "test source", "Neutral", "test analysis"))
        # Two settled predictions: one directionally correct, one incorrect.
        conn.execute(
            """INSERT INTO predictions (created_at,trade_date,symbol,horizon,base_price,predicted_pct,actual_pct,abs_error_pct,status,settled_at,outcome)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            ("2026-10-01", "2026-10-01", "IDC", 3, 30000, 2.0, 1.0, 1.0, "SETTLED", "2026-10-06", "WIN"),
        )
        conn.execute(
            """INSERT INTO predictions (created_at,trade_date,symbol,horizon,base_price,predicted_pct,actual_pct,abs_error_pct,status,settled_at,outcome)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            ("2026-10-02", "2026-10-02", "NKG", 3, 10000, 1.0, -2.0, 3.0, "SETTLED", "2026-10-07", "LOSS"),
        )
        conn.execute("INSERT INTO model_performance(computed_at,horizon,regime_group,sample_count,direction_scored_count,win_rate_pct,mae_pct,avg_return_pct,bias_pct,coverage_pct,notes) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                     ("2026-10-28", 3, "SIDEWAYS", 20, 18, 55.5, 1.25, 0.2, -0.1, 80.0, "scorecard"))
    return db.DB_PATH


def payload(response: str) -> dict:
    value = json.loads(response)
    assert "ok" in value
    return value


def test_radar_latest_and_data_health_preserve_missing_timestamp(mcp_db):
    radar = payload(mcp_tools.get_radar(limit=10))
    assert radar["ok"] is True
    symbols = {row["symbol"] for row in radar["data"]["rows"]}
    assert symbols == {"IDC", "NKG"}

    health = payload(mcp_tools.data_health())["data"]
    assert health["symbols_missing_timestamp"] == ["NKG"]
    assert health["symbols_blocked"][0]["reason"] == "NO_INTRADAY_BAR_PROVIDER"
    assert health["missing_or_zero_volume_count"] == 1


def test_exclusion_and_source_compare_do_not_invent_a_second_provider(mcp_db):
    exclusion = payload(mcp_tools.explain_exclusion("nkg"))["data"]
    assert exclusion["found"] is True
    assert "NO_INTRADAY_BAR_PROVIDER" in exclusion["stored_reasons"]

    compare = payload(mcp_tools.compare_sources("IDC"))["data"]
    assert "VCI intraday | Hist:VNDIRECT" in compare["distinct_stored_source_labels"]
    assert "không thể xác nhận chênh lệch giá độc lập" in compare["limitation"]


def test_invalid_symbol_is_rejected_without_sql_execution(mcp_db):
    result = payload(mcp_tools.dispatch_tool("get_flow_features", {"symbol": "IDC; DROP TABLE users"}))
    assert result["ok"] is False
    with db.connect() as conn:
        assert conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users'").fetchone()


def test_logs_redact_credentials_and_media_search_omits_raw_text(mcp_db):
    logs = payload(mcp_tools.get_system_logs(component="providers"))["data"]["rows"]
    assert logs
    assert "sk-testsecret" not in logs[0]["message"]
    assert "[REDACTED" in logs[0]["message"]

    media = payload(mcp_tools.media_search(symbol="IDC"))["data"]
    assert media["count"] == 1
    assert media["rows"][0]["title"] == "IDC mở rộng khu công nghiệp"
    assert "raw_text" not in media["rows"][0]


def test_history_features_macro_backtest_and_scorecard(mcp_db):
    assert payload(mcp_tools.get_ticker_history("IDC"))["data"]["count"] == 1
    features = payload(mcp_tools.get_flow_features("IDC"))["data"]["features"]
    assert features["bottom_score"] == 68
    assert features["freshness_verified"] is True
    assert payload(mcp_tools.macro_snapshot("DXY"))["data"]["count"] == 1

    bt = payload(mcp_tools.run_backtest(horizon=3))["data"]["summary"]
    assert bt["records_with_prediction_and_actual"] == 2
    assert bt["directional_accuracy_pct"] == 50.0
    assert bt["mae_pct_points"] == 2.0
    perf = payload(mcp_tools.get_model_performance(3))["data"]
    assert perf["count"] == 1
    assert perf["rows"][0]["sample_count"] == 20


def test_tool_dispatch_is_allowlisted(mcp_db):
    result = payload(mcp_tools.dispatch_tool("execute_sql", {"sql": "DROP TABLE users"}))
    assert result["ok"] is False
    assert "allowlist" in result["data"]["error"]
