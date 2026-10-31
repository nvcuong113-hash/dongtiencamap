from __future__ import annotations

from quants.smf_v2.order_flow import analyze_order_flow_sample
from quants.smf_v2.evidence import aggregate_independent_groups, extract_row_evidence
from quants.smf_v2.shadow import build_shadow_report


def sample_report(**updates):
    value = {
        "trade_sample_status": "SAMPLE_ONLY_COMPLETENESS_UNVERIFIED",
        "trade_sample_source_requested": "kbs",
        "trade_sample_source_reported": "kbs",
        "trade_sample_source_match": True,
        "trade_sample_rows_received": 100,
        "trade_sample_rows_valid": 100,
        "trade_sample_truncated": False,
        "trade_sample_volume": 10000,
        "trade_sample_buy_volume": 7000,
        "trade_sample_sell_volume": 2000,
        "trade_sample_auction_volume": 500,
        "trade_sample_unknown_volume": 500,
        "trade_sample_classified_volume_pct": 90.0,
        "trade_sample_net_aggressive_volume": 5000,
        "trade_sample_net_aggressive_value_billion": 0.12,
        "trade_sample_value_billion": 0.5,
        "trade_sample_vwap": 25000.0,
        "trade_sample_first_time": "2026-10-30T09:15:00+07:00",
        "trade_sample_last_time": "2026-10-30T10:30:00+07:00",
        "trade_sample_latest_age_seconds": 20.0,
        "trade_sample_coverage": "SESSION_COVERAGE_UNVERIFIED",
    }
    value.update(updates)
    return value


def test_analyzes_only_sample_and_keeps_non_voting_guardrail():
    row = {"symbol": "HPG", "trade_date": "2026-10-30", "trade_sample_status": "SAMPLE_ONLY_COMPLETENESS_UNVERIFIED", "trade_sample_report": sample_report()}
    result = analyze_order_flow_sample(row)
    assert result["status"] == "ANALYZED_SAMPLE"
    assert result["sample_bias"] == "BUY_DOMINANT_SAMPLE"
    assert result["sample_buy_share_pct"] == 77.78
    assert result["sample_window_minutes"] == 75.0
    assert result["sample_only"] is True
    assert result["session_complete"] is False
    assert result["contributes_to_consensus"] is False


def test_sell_dominant_sample_is_not_a_trade_signal():
    report = sample_report(
        trade_sample_buy_volume=1000,
        trade_sample_sell_volume=7000,
        trade_sample_auction_volume=1000,
        trade_sample_unknown_volume=1000,
        trade_sample_classified_volume_pct=80.0,
        trade_sample_net_aggressive_volume=-6000,
        trade_sample_net_aggressive_value_billion=-0.2,
    )
    result = analyze_order_flow_sample({"trade_sample_status": "SAMPLE_ONLY_COMPLETENESS_UNVERIFIED", "trade_sample_report": report})
    assert result["sample_bias"] == "SELL_DOMINANT_SAMPLE"
    assert result["polarity"] == "BEARISH"
    assert result["contributes_to_consensus"] is False


def test_missing_sample_is_explicitly_unavailable():
    result = analyze_order_flow_sample({"symbol": "IDC", "trade_sample_status": "SKIPPED_AUTH_NOT_VERIFIED"})
    assert result["status"] == "UNAVAILABLE"
    assert result["sample_available"] is False
    assert result["polarity"] == "UNKNOWN"


def test_mismatched_source_and_stale_data_do_not_get_directional_bias():
    report = sample_report(trade_sample_source_match=False, trade_sample_latest_age_seconds=800)
    result = analyze_order_flow_sample({"trade_sample_status": "SAMPLE_ONLY_COMPLETENESS_UNVERIFIED", "trade_sample_report": report})
    assert result["status"] == "SAMPLE_DIAGNOSTIC_ONLY"
    assert result["sample_bias"] == "UNKNOWN"
    assert "SOURCE_NOT_VERIFIED" in result["reason_codes"]
    assert "STALE_OR_UNVERIFIED_SAMPLE_TIME" in result["reason_codes"]


def test_low_classification_coverage_and_small_sample_are_gated():
    report = sample_report(
        trade_sample_rows_valid=12, trade_sample_buy_volume=1000,
        trade_sample_sell_volume=1000, trade_sample_auction_volume=4000,
        trade_sample_unknown_volume=4000, trade_sample_classified_volume_pct=20.0,
        trade_sample_net_aggressive_volume=0,
    )
    result = analyze_order_flow_sample({"trade_sample_status": "SAMPLE_ONLY_COMPLETENESS_UNVERIFIED", "trade_sample_report": report})
    assert result["status"] == "SAMPLE_DIAGNOSTIC_ONLY"
    assert "TOO_FEW_VALID_TRADES" in result["reason_codes"]
    assert "LOW_CLASSIFIED_VOLUME_COVERAGE" in result["reason_codes"]
    assert result["polarity"] == "UNKNOWN"


def test_inconsistent_sample_totals_are_invalid():
    report = sample_report(trade_sample_volume=2000)
    result = analyze_order_flow_sample({"trade_sample_status": "SAMPLE_ONLY_COMPLETENESS_UNVERIFIED", "trade_sample_report": report})
    assert result["status"] == "INVALID_SAMPLE_METRICS"
    assert result["quality_status"] == "INVALID"


def test_sample_evidence_is_persistable_but_never_consensus_voting():
    row = {
        "symbol": "HPG", "trade_date": "2026-10-30", "price": 25000,
        "volume": 1000000, "volume_available": True,
        "trade_sample_status": "SAMPLE_ONLY_COMPLETENESS_UNVERIFIED",
        "trade_sample_report": sample_report(),
        "rs_vni": 0.2,
    }
    records = extract_row_evidence(row)
    order_flow = next(item for item in records if item.feature == "time_sales_sample_imbalance")
    assert order_flow.engine == "order_flow_sample_v1"
    assert order_flow.evidence_group == "ORDER_FLOW"
    assert order_flow.polarity == "BULLISH"
    assert order_flow.quality_status == "PARTIAL"
    assert order_flow.contributes_to_consensus is False
    consensus = aggregate_independent_groups(records)
    assert "ORDER_FLOW" not in consensus["group_votes"]
    assert consensus["independent_group_count"] == 0


def test_shadow_report_exposes_p3_without_mutating_input_or_decisions():
    row = {
        "symbol": "HPG", "trade_date": "2026-10-30", "price": 25000,
        "volume": 1000000, "volume_available": True,
        "trade_sample_status": "SAMPLE_ONLY_COMPLETENESS_UNVERIFIED",
        "trade_sample_report": sample_report(),
        "bottom_score": 50, "top_score": 10, "signal_score": 45, "p_win": 52,
    }
    original = dict(row)
    report = build_shadow_report([row])
    assert row == original
    assert report["schema_version"] == "2.5"
    assert report["decision_impact"] == "NONE"
    instrument = report["instruments"][0]
    assert instrument["order_flow_sample"]["sample_bias"] == "BUY_DOMINANT_SAMPLE"
    assert not instrument["consensus"]["is_trade_signal"]
    evidence = next(item for item in instrument["evidence"] if item["feature"] == "time_sales_sample_imbalance")
    assert evidence["contributes_to_consensus"] is False


def test_classified_coverage_and_net_volume_must_reconcile():
    report = sample_report(trade_sample_classified_volume_pct=95.0)
    result = analyze_order_flow_sample({"trade_sample_status": "SAMPLE_ONLY_COMPLETENESS_UNVERIFIED", "trade_sample_report": report})
    assert result["status"] == "INVALID_SAMPLE_METRICS"
    assert result["reason_codes"] == ["CLASSIFIED_COVERAGE_MISMATCH"]

