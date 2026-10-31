from __future__ import annotations

from quants.smf_v2.fusion import fuse_instrument_evidence, summarize_fusion
from quants.smf_v2.shadow import build_shadow_report


def ev(group, polarity, *, quality="VERIFIED", contributes=True, feature="test", engine="test"):
    return {
        "evidence_group": group,
        "polarity": polarity,
        "quality_status": quality,
        "contributes_to_consensus": contributes,
        "feature": feature,
        "engine": engine,
        "source_name": engine,
    }


def test_ohlcv_engines_are_collapsed_into_one_source_family():
    item = {"symbol": "NKG", "evidence": [
        ev("PRICE_ACTION", "BULLISH", engine="market_structure_v2"),
        ev("VOLUME_VSA", "BULLISH", engine="vsa_volume_v2"),
        ev("ACCUMULATION_DISTRIBUTION", "BULLISH", quality="PARTIAL", contributes=False,
           engine="accumulation_distribution_v1"),
    ]}
    result = fuse_instrument_evidence(item)
    assert result["verified_family_count"] == 1
    assert result["status"] == "SINGLE_VERIFIED_FAMILY"
    assert result["verified_source_families"][0]["family"] == "PRICE_VOLUME_HISTORY"
    assert result["technical_cluster"]["polarity"] == "BULLISH"
    assert result["is_trade_signal"] is False


def test_two_independent_verified_families_can_align_without_creating_a_trade_signal():
    item = {"symbol": "IDC", "evidence": [
        ev("PRICE_ACTION", "BULLISH", engine="price_history"),
        ev("RELATIVE_STRENGTH", "BULLISH", engine="benchmark_series"),
    ]}
    result = fuse_instrument_evidence(item)
    assert result["status"] == "CROSS_SOURCE_ALIGNMENT"
    assert result["direction"] == "BULLISH"
    assert result["verified_family_count"] == 2
    assert result["decision_impact"] == "NONE"
    assert result["calibrated_probability"] is None


def test_conflicting_independent_families_are_not_averaged_away():
    item = {"symbol": "LPB", "evidence": [
        ev("PRICE_ACTION", "BULLISH", engine="price_history"),
        ev("RELATIVE_STRENGTH", "BEARISH", engine="benchmark_series"),
    ]}
    result = fuse_instrument_evidence(item)
    assert result["status"] == "CROSS_SOURCE_CONFLICT"
    assert result["direction"] == "CONFLICTED"
    assert result["is_trade_signal"] is False


def test_capped_time_sales_sample_never_becomes_verified_order_flow_family():
    item = {"symbol": "HPG", "evidence": [
        ev("ORDER_FLOW", "BULLISH", quality="PARTIAL", contributes=False,
           feature="time_sales_sample_imbalance", engine="order_flow_sample_v1"),
    ], "order_flow_sample": {"status": "CANDIDATE", "sample_only": True}}
    result = fuse_instrument_evidence(item)
    assert result["verified_family_count"] == 0
    assert result["status"] == "INSUFFICIENT_INDEPENDENT_EVIDENCE"
    assert result["excluded_evidence_counts"]["NON_VOTING_EVIDENCE"] == 1


def test_fusion_summary_counts_statuses_and_never_promotes_decisions():
    instruments = [
        {"evidence_fusion": {"status": "CROSS_SOURCE_ALIGNMENT", "verified_source_families": [{"family": "A"}] }},
        {"evidence_fusion": {"status": "CROSS_SOURCE_CONFLICT", "verified_source_families": [{"family": "A"}] }},
        {"evidence_fusion": {"status": "INSUFFICIENT_INDEPENDENT_EVIDENCE", "verified_source_families": []}},
    ]
    summary = summarize_fusion(instruments)
    assert summary["cross_source_aligned_count"] == 1
    assert summary["cross_source_conflicted_count"] == 1
    assert summary["is_trade_signal"] is False
    assert summary["decision_impact"] == "NONE"


def test_shadow_report_includes_p6_without_changing_legacy_decision_fields():
    row = {
        "symbol": "NKG", "trade_date": "2026-10-30", "price": 100,
        "volume": 1000, "volume_available": True,
        "signal_score": 77, "bottom_score": 68, "top_score": 32,
        "quote_freshness_verified": True, "index_valid": True,
        "rs_vni": 1.0, "rs_sector": 0.8,
    }
    original = dict(row)
    report = build_shadow_report([row])
    assert report["schema_version"] == "2.6"
    assert report["as_of_date"] == "2026-10-30"
    assert report["decision_impact"] == "NONE"
    assert report["instruments"][0]["evidence_fusion"]["is_trade_signal"] is False
    assert row == original
