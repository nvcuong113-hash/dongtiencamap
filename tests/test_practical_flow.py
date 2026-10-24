import unittest
from quants.practical_flow import summarize_flow_history, classify_practical_flow, aggregate_sector_rotation


class PracticalFlowTests(unittest.TestCase):
    def test_flow_history_uses_newest_first_prior_sessions(self):
        got = summarize_flow_history(3.0, [2.0, -1.0, 4.0, 5.0, 6.0])
        self.assertEqual(got["flow_sum_3_sessions_billion"], 4.0)
        self.assertEqual(got["flow_sum_5_sessions_billion"], 13.0)
        self.assertEqual(got["flow_acceleration_billion"], 1.0)
        self.assertAlmostEqual(got["flow_persistence_3_sessions_pct"], 66.7, places=1)

    def test_missing_flow_is_not_fabricated(self):
        got = summarize_flow_history(None, [])
        self.assertIsNone(got["flow_sum_3_sessions_billion"])
        self.assertIsNone(got["flow_acceleration_billion"])

    def test_proxy_flow_does_not_claim_absorption(self):
        row = {"price": 100, "support": 99, "resistance": 110, "change_pct": -2.2,
               "vol_ratio": 1.6, "high": 103, "low": 96, "net_flow_billion": 1.2,
               "is_real_deal": False}
        got = classify_practical_flow(row)
        self.assertEqual(got["practical_flow_setup"], "SELLING_CLIMAX_CANDIDATE")
        self.assertEqual(got["practical_flow_confidence"], "MEDIUM")

    def test_sector_separates_real_and_proxy_flow(self):
        rows = [
            {"sector":"Steel", "net_flow_billion":2, "is_real_deal":True},
            {"sector":"Steel", "net_flow_billion":-1, "is_real_deal":True},
            {"sector":"Steel", "net_flow_billion":4, "is_real_deal":False},
        ]
        got = aggregate_sector_rotation(rows)["Steel"]
        self.assertEqual(got["sector_real_net_flow_billion"], 1.0)
        self.assertEqual(got["sector_proxy_net_flow_billion"], 4.0)
        self.assertEqual(got["sector_watchlist_coverage"], 3)


if __name__ == "__main__":
    unittest.main()
