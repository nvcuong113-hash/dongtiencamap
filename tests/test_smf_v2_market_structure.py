import unittest
from datetime import date, timedelta

from quants.smf_v2.market_structure import analyze_market_structure


def sample_bars():
    closes = [
        100, 102, 104, 102, 99, 97, 99, 102, 105, 103, 100, 98,
        101, 104, 107, 105, 102, 100, 103, 106, 109, 108, 106, 108,
        112, 115, 114, 112, 113, 116, 120, 119, 117, 121,
    ]
    bars = []
    start = date(2026, 1, 1)
    for i, close in enumerate(closes):
        previous = closes[i - 1] if i else close
        bars.append({
            "date": (start + timedelta(days=i)).isoformat(),
            "open": previous,
            "high": max(previous, close) + 1,
            "low": min(previous, close) - 1,
            "close": close,
            "volume": 1000,
        })
    return bars


class SMFV2MarketStructureTests(unittest.TestCase):
    def test_structure_returns_causal_confirmed_levels_and_breaks(self):
        bars = sample_bars()
        report = analyze_market_structure(bars, as_of_date=bars[-1]["date"], min_history=20)
        self.assertEqual(report["status"], "AVAILABLE")
        self.assertTrue(report["causal"])
        self.assertFalse(report["future_data_used"])
        self.assertEqual(report["bias"], "BULLISH")
        self.assertTrue(any(item["event"] == "BOS_UP" for item in report["events"]))
        for level_key in ("last_swing_high", "last_swing_low"):
            level = report[level_key]
            if level:
                self.assertLess(level["pivot_date"], level["confirmed_at"])

    def test_future_bars_are_excluded_by_as_of_date(self):
        bars = sample_bars()
        cutoff = bars[-5]["date"]
        report = analyze_market_structure(bars, as_of_date=cutoff, min_history=20)
        self.assertEqual(report["as_of_date"], cutoff)
        self.assertEqual(report["future_bars_excluded"], 4)
        self.assertTrue(all(event["date"] <= cutoff for event in report["events"]))
        self.assertFalse(report["future_data_used"])

    def test_pivots_are_confirmed_only_after_right_bars(self):
        bars = sample_bars()
        report = analyze_market_structure(bars, as_of_date=bars[-1]["date"], min_history=20, right_bars=2)
        for level_key in ("last_swing_high", "last_swing_low"):
            level = report[level_key]
            if level:
                pivot_index = next(i for i, bar in enumerate(bars) if bar["date"] == level["pivot_date"])
                confirmed_index = next(i for i, bar in enumerate(bars) if bar["date"] == level["confirmed_at"])
                self.assertEqual(confirmed_index - pivot_index, 2)

    def test_duplicate_and_invalid_bars_are_counted_and_removed(self):
        bars = sample_bars()
        bars.append(dict(bars[10]))
        bars.append({"date": "2026-02-10", "open": 10, "high": 8, "low": 9, "close": 10})
        report = analyze_market_structure(bars, as_of_date=bars[-1]["date"], min_history=20)
        self.assertEqual(report["duplicate_sessions_collapsed"], 1)
        self.assertEqual(report["invalid_bars_dropped"], 1)
        self.assertEqual(report["bars_used"], len(sample_bars()))

    def test_break_events_include_prior_atr_quality_and_retest_outcome(self):
        bars = sample_bars()
        report = analyze_market_structure(bars, as_of_date=bars[-1]["date"], min_history=20)
        break_events = [e for e in report["events"] if e["event"] in {"BOS_UP", "BOS_DOWN", "CHOCH_UP", "CHOCH_DOWN"}]
        self.assertTrue(break_events)
        for event in break_events:
            self.assertIn("break_distance_atr", event)
            self.assertIn(event["break_quality"], {"WEAK", "MODERATE", "STRONG"})
            self.assertLess(event["level_confirmed_at"], event["date"])
        retests = [e for e in report["events"] if e["event"].startswith("RETEST_")]
        self.assertTrue(any(e["event"] == "RETEST_HOLD_UP" for e in retests))
        self.assertTrue(any(e["event"] == "RETEST_FAIL_UP" for e in retests))

    def test_weekly_structure_is_available_on_sufficient_history(self):
        bars = []
        start = date(2026, 1, 1)
        day = start
        close = 100.0
        while len(bars) < 150:
            if day.weekday() < 5:
                previous = close
                close = 100 + (len(bars) * 0.15) + ((len(bars) % 9) - 4) * 0.8
                bars.append({
                    "date": day.isoformat(), "open": previous,
                    "high": max(previous, close) + 1.2,
                    "low": min(previous, close) - 1.2,
                    "close": close, "volume": 1000 + len(bars) * 5,
                })
            day += timedelta(days=1)
        report = analyze_market_structure(bars, as_of_date=bars[-1]["date"], min_history=40)
        self.assertEqual(report["status"], "AVAILABLE")
        weekly = report["timeframes"]["weekly"]
        self.assertEqual(weekly["timeframe"], "WEEKLY")
        self.assertEqual(weekly["status"], "AVAILABLE")
        self.assertGreaterEqual(weekly["bars_used"], 12)
        self.assertIn(report["confluence"]["status"], {
            "ALIGNED_BULLISH", "ALIGNED_BEARISH", "DIVERGENT", "MIXED_OR_TRANSITION", "PARTIAL"
        })
        self.assertIn("daily_structure_state", report["confluence"])
        self.assertIn("weekly_structure_state", report["confluence"])
        self.assertIn("event_context", report["confluence"])

    def test_partial_latest_week_is_excluded_before_friday(self):
        bars = []
        start = date(2026, 1, 5)  # Monday
        day = start
        close = 100.0
        while len(bars) < 100:
            if day.weekday() < 5:
                previous = close
                close += 0.2 if len(bars) % 2 else -0.1
                bars.append({
                    "date": day.isoformat(), "open": previous,
                    "high": max(previous, close) + 1,
                    "low": min(previous, close) - 1,
                    "close": close, "volume": 1000,
                })
            day += timedelta(days=1)
        # Cut at Wednesday in the latest week, so this week's partial aggregate
        # must not be used in weekly structure.
        wed = next(b["date"] for b in reversed(bars) if date.fromisoformat(b["date"]).weekday() == 2)
        report = analyze_market_structure(bars, as_of_date=wed, min_history=40)
        weekly = report["timeframes"]["weekly"]
        self.assertTrue(weekly["aggregation_meta"]["partial_latest_week_excluded"])
        self.assertLess(weekly["latest_observation_date"], wed)


    def test_weekly_completion_respects_configured_exchange_holiday(self):
        from datetime import date as date_type
        from unittest.mock import patch
        from quants.smf_v2.market_structure import _week_is_complete

        thursday = date_type(2026, 10, 29)
        with patch.dict("os.environ", {"QUANTS_NON_TRADING_DATES": ""}, clear=False):
            self.assertFalse(_week_is_complete(thursday))
        with patch.dict("os.environ", {"QUANTS_NON_TRADING_DATES": "2026-10-30"}, clear=False):
            self.assertTrue(_week_is_complete(thursday))

    def test_future_data_does_not_change_any_existing_timeframe_output(self):
        bars = sample_bars()
        cutoff = bars[-8]["date"]
        full = analyze_market_structure(bars, as_of_date=cutoff, min_history=20)
        truncated = analyze_market_structure(bars[:-7], as_of_date=cutoff, min_history=20)
        self.assertEqual(full["bias"], truncated["bias"])
        self.assertEqual(full["events"], truncated["events"])
        self.assertEqual(full["timeframes"]["weekly"]["bias"], truncated["timeframes"]["weekly"]["bias"])
        self.assertEqual(full["timeframes"]["weekly"]["events"], truncated["timeframes"]["weekly"]["events"])

    def test_insufficient_history_does_not_emit_bias_or_break_events(self):
        report = analyze_market_structure(sample_bars()[:15], as_of_date="2026-01-15")
        self.assertEqual(report["status"], "INSUFFICIENT_HISTORY")
        self.assertEqual(report["bias"], "UNKNOWN")
        self.assertEqual(report["events"], [])

    def test_bos_event_is_not_repeated_every_day_above_same_level(self):
        bars = sample_bars()
        report = analyze_market_structure(bars, as_of_date=bars[-1]["date"], min_history=20)
        bos_levels = [(item["date"], item["broken_level"]) for item in report["events"] if item["event"] == "BOS_UP"]
        self.assertLess(len(bos_levels), 10)
        self.assertEqual(len({(d, level) for d, level in bos_levels}), len(bos_levels))


if __name__ == "__main__":
    unittest.main()
