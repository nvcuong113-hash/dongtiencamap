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
