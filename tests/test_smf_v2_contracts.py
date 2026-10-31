import unittest

from quants.smf_v2.contracts import DataEnvelope, EvidenceRecord
from quants.smf_v2.evidence import aggregate_independent_groups, extract_row_evidence
from quants.smf_v2.shadow import build_shadow_report


class SMFV2ContractTests(unittest.TestCase):
    def test_data_envelope_keeps_missing_values_missing(self):
        envelope = DataEnvelope.from_quant_row({
            "symbol": "abc", "trade_date": "2026-10-30", "price": None,
            "volume": None, "volume_available": False,
            "net_flow_billion": None, "flow_status": "NO_FLOW",
        })
        self.assertEqual(envelope.symbol, "ABC")
        self.assertIsNone(envelope.price)
        self.assertIsNone(envelope.volume)
        self.assertEqual(envelope.flow_status, "NO_FLOW")
        self.assertEqual(envelope.quote_quality_status, "INVALID")
        self.assertEqual(envelope.volume_quality_status, "MISSING")
        self.assertEqual(envelope.validate(), [])

    def test_data_envelope_rejects_bad_values(self):
        envelope = DataEnvelope(
            symbol="ABC", session_date="2026-99-99", observed_at=None,
            price=-1, volume=-2, quote_source=None, history_source=None,
            quote_timestamp_verified=False, quote_quality_status="INVALID",
            volume_quality_status="MISSING", flow_status="NO_FLOW",
            flow_source=None, flow_coverage_pct=120,
        )
        errors = envelope.validate()
        self.assertEqual(len(errors), 4)

    def test_data_envelope_rejects_invalid_observation_timestamp_and_status(self):
        envelope = DataEnvelope(
            symbol="ABC", session_date="2026-10-30", observed_at="not-a-timestamp",
            price=10, volume=100, quote_source="x", history_source="y",
            quote_timestamp_verified=True, quote_quality_status="FRESHISH",
            volume_quality_status="GOOD", flow_status="MAGIC_FLOW",
            flow_source="z", flow_coverage_pct=20,
        )
        errors = envelope.validate()
        self.assertEqual(len(errors), 4)

    def test_proxy_flow_never_votes(self):
        row = {
            "symbol": "NKG", "trade_date": "2026-10-30", "price": 10000,
            "volume": 1000000, "volume_available": True,
            "net_flow_billion": 12.5, "is_real_deal": False,
            "flow_status": "VERIFIED_PROXY_ESTIMATE", "flow_coverage_pct": 0,
        }
        records = extract_row_evidence(row)
        flow = next(x for x in records if x.evidence_group == "ORDER_FLOW")
        self.assertEqual(flow.polarity, "UNKNOWN")
        self.assertEqual(flow.quality_status, "PROXY")
        self.assertFalse(flow.contributes_to_consensus)
        self.assertEqual(aggregate_independent_groups(records)["group_votes"], {})

    def test_inconsistent_legacy_real_flow_label_is_downgraded_to_proxy(self):
        envelope = DataEnvelope.from_quant_row({
            "symbol": "ABC", "trade_date": "2026-10-30", "net_flow_billion": 2.0,
            "is_real_deal": False, "flow_coverage_pct": 80,
            "flow_status": "REAL_DEAL_VERIFIED",
        })
        self.assertEqual(envelope.flow_status, "VERIFIED_PROXY_ESTIMATE")

    def test_verified_deal_flow_can_vote_only_with_minimum_coverage(self):
        row = {
            "symbol": "LPB", "trade_date": "2026-10-30", "net_flow_billion": -4.2,
            "is_real_deal": True, "flow_coverage_pct": 42,
            "flow_status": "REAL_DEAL_VERIFIED", "flow_source": "trade history",
        }
        records = extract_row_evidence(row)
        flow = next(x for x in records if x.evidence_group == "ORDER_FLOW")
        self.assertEqual(flow.polarity, "BEARISH")
        self.assertTrue(flow.contributes_to_consensus)
        self.assertEqual(flow.quality_status, "VERIFIED")

    def test_related_flags_count_once_per_evidence_family(self):
        row = {
            "symbol": "NKG", "trade_date": "2026-10-30", "volume_available": True,
            "history_available": True, "daily_pattern_status": "AVAILABLE",
            "stopping_volume_event": True, "no_supply_candidate": True,
            "bullish_rsi_divergence": True, "bullish_macd_divergence": True,
            "pivot_breakout_confirmed": True,
        }
        records = extract_row_evidence(row)
        volume = [x for x in records if x.evidence_group == "VOLUME_BEHAVIOR"]
        momentum = [x for x in records if x.feature == "momentum_divergence_cluster"]
        patterns = [x for x in records if x.feature == "price_pattern_cluster"]
        self.assertEqual(len(volume), 1)
        self.assertEqual(len(momentum), 1)
        self.assertEqual(len(patterns), 1)
        self.assertEqual(volume[0].polarity, "BULLISH")
        self.assertFalse(volume[0].contributes_to_consensus)
        self.assertEqual(momentum[0].polarity, "BULLISH")
        self.assertEqual(patterns[0].polarity, "BULLISH")
        self.assertEqual({x.evidence_group for x in momentum + patterns}, {"PRICE_ACTION"})

    def test_v2_vsa_is_visible_but_partial_does_not_cast_consensus_vote(self):
        row = {
            "symbol": "NKG", "trade_date": "2026-10-30",
            "smf_v2_vsa": {
                "status": "AVAILABLE", "events": [{"event": "SELLING_CLIMAX_CANDIDATE"}],
                "polarity": "BULLISH", "quality_status": "PARTIAL", "strength": "MEDIUM",
                "quality_reason": "Chưa đối chiếu nguồn thứ hai",
            },
        }
        records = extract_row_evidence(row)
        vsa = next(x for x in records if x.evidence_group == "VOLUME_VSA")
        self.assertEqual(vsa.polarity, "BULLISH")
        self.assertEqual(vsa.quality_status, "PARTIAL")
        self.assertTrue(vsa.contributes_to_consensus)
        from quants.smf_v2.evidence import aggregate_independent_groups
        self.assertEqual(aggregate_independent_groups(records)["independent_group_count"], 0)

    def test_legacy_composite_scores_are_diagnostic_only(self):
        row = {"symbol": "IDC", "bottom_score": 90, "top_score": 10, "signal_score": 80, "p_win": 75}
        records = extract_row_evidence(row)
        legacy = next(x for x in records if x.evidence_group == "LEGACY_COMPOSITE")
        self.assertFalse(legacy.contributes_to_consensus)
        self.assertEqual(legacy.polarity, "UNKNOWN")

    def test_same_group_conflicting_evidence_is_not_double_counted(self):
        records = [
            EvidenceRecord("1", "ABC", "2026-10-30", "x", "PRICE_ACTION", "a", "BULLISH", "VERIFIED", "MEDIUM", "a"),
            EvidenceRecord("2", "ABC", "2026-10-30", "x", "PRICE_ACTION", "b", "BEARISH", "VERIFIED", "MEDIUM", "b"),
            EvidenceRecord("3", "ABC", "2026-10-30", "x", "ORDER_FLOW", "c", "BULLISH", "VERIFIED", "MEDIUM", "c"),
        ]
        result = aggregate_independent_groups(records)
        self.assertEqual(result["independent_group_count"], 2)
        self.assertEqual(result["group_votes"]["PRICE_ACTION"], "CONFLICTED")
        self.assertEqual(result["bullish_group_count"], 1)
        self.assertEqual(result["neutral_or_conflicted_group_count"], 1)

    def test_price_structure_momentum_and_patterns_share_one_price_action_vote(self):
        row = {
            "symbol": "ABC", "trade_date": "2026-10-30", "history_available": True,
            "daily_pattern_status": "AVAILABLE", "trend_state": "UPTREND",
            "smf_v2_market_structure": {
                "status": "AVAILABLE", "bias": "BULLISH", "latest_event": "BOS_UP",
            },
            "bullish_rsi_divergence": True,
            "bearish_macd_divergence": True,
            "double_top_candidate": True,
        }
        records = extract_row_evidence(row)
        price_records = [x for x in records if x.evidence_group == "PRICE_ACTION"]
        consensus = aggregate_independent_groups(records)
        self.assertGreaterEqual(len(price_records), 2)
        self.assertEqual(consensus["independent_group_count"], 1)
        # Native structure is now PARTIAL until independently corroborated, so it
        # cannot cast a VERIFIED vote that would mask the conflicting legacy evidence.
        self.assertEqual(consensus["group_votes"]["PRICE_ACTION"], "BEARISH")

    def test_native_market_structure_has_correct_engine_lineage_and_partial_quality(self):
        row = {
            "symbol": "NKG", "trade_date": "2026-10-30",
            "smf_v2_market_structure": {
                "status": "AVAILABLE", "bias": "BULLISH", "latest_event": "BOS_UP",
                "events": [{"event": "BOS_UP", "break_quality": "MODERATE"}],
                "confluence": {"status": "ALIGNED_BULLISH", "weekly_bias": "BULLISH"},
                "last_swing_high": {"price": 12000}, "last_swing_low": {"price": 11000},
            },
        }
        record = next(x for x in extract_row_evidence(row) if x.feature == "multi_timeframe_market_structure")
        self.assertEqual(record.engine, "market_structure_v2")
        self.assertEqual(record.quality_status, "PARTIAL")
        self.assertEqual(record.strength_label, "MEDIUM")
        self.assertTrue(record.contributes_to_consensus)
        self.assertEqual(aggregate_independent_groups([record])["independent_group_count"], 0)

    def test_shadow_report_does_not_mutate_legacy_row_or_emit_trade_signal(self):
        row = {
            "symbol": "NKG", "trade_date": "2026-10-30", "price": 10000,
            "volume": 1000000, "volume_available": True,
            "quote_freshness_verified": True, "index_valid": True,
            "history_available": True, "trend_state": "UPTREND", "rs_vni": 1.2,
            "net_flow_billion": 5, "is_real_deal": False,
            "flow_status": "VERIFIED_PROXY_ESTIMATE", "flow_coverage_pct": 0,
            "bottom_score": 80, "top_score": 10, "signal_score": 70, "p_win": 62,
        }
        original = dict(row)
        report = build_shadow_report([row])
        self.assertEqual(row, original)
        self.assertEqual(report["mode"], "SHADOW")
        self.assertEqual(report["decision_impact"], "NONE")
        item = report["instruments"][0]
        self.assertEqual(item["legacy_scores_diagnostic_only"]["signal_score"], 70)
        self.assertFalse(item["consensus"]["is_trade_signal"])
        self.assertIsNone(item["consensus"]["calibrated_probability"])

    def test_evidence_ids_are_stable_for_same_input(self):
        row = {"symbol": "FPT", "trade_date": "2026-10-30", "trend_state": "DOWNTREND", "history_available": True}
        self.assertEqual(
            [x.evidence_id for x in extract_row_evidence(row)],
            [x.evidence_id for x in extract_row_evidence(row)],
        )


if __name__ == "__main__":
    unittest.main()
