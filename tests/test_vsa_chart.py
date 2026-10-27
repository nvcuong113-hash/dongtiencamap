import unittest

from quants.vsa_chart import (
    build_vsa_plotly_figure,
    extract_chart_levels,
    prepare_vsa_bars,
)


class VSAChartModuleTests(unittest.TestCase):
    def setUp(self):
        self.sample_bars = [
            {"date": f"2026-09-{i:02d}", "close": 30000 + i * 200, "high": 30400 + i * 200, "low": 29800 + i * 200, "volume": 1_000_000 + i * 10_000}
            for i in range(1, 26)
        ]
        self.live_row = {
            "symbol": "IDC",
            "trade_date": "2026-09-26",
            "price": 36200,
            "ref_price": 35000,
            "high": 36500,
            "low": 34900,
            "volume": 2_100_000,
            "vwap": 35800,
            "vwap_available": True,
            "support": 32500,
            "resistance": 36800,
            "stop_loss_price": 31800,
            "target_price": 38500,
            "wyckoff": "SOS / Breakout",
            "early_signal": "🟢 Ứng Viên Tạo Đáy",
        }

    def test_prepare_vsa_bars_merges_live_row_and_computes_ma20(self):
        prepared = prepare_vsa_bars(self.sample_bars, self.live_row, lookback=60)
        self.assertEqual(len(prepared), 26)
        last = prepared[-1]
        self.assertEqual(last["date"], "2026-09-26")
        self.assertEqual(last["close"], 36200)
        self.assertEqual(last["open"], 35000)
        self.assertEqual(last["volume"], 2_100_000)
        self.assertIsNotNone(last["ma20"])
        self.assertIsNotNone(last["ma20_vol"])
        self.assertGreater(last["vol_ratio"], 1.5)

    def test_prepare_vsa_bars_filters_corrupt_entries_and_respects_lookback(self):
        noisy = self.sample_bars + [
            {"date": "", "close": 30000, "volume": 100},
            {"date": "2026-09-28", "close": -10, "volume": 100},
            {"date": "2026-09-29", "close": None, "volume": 100},
        ]
        prepared = prepare_vsa_bars(noisy, None, lookback=20)
        self.assertEqual(len(prepared), 20)
        self.assertTrue(all(b["high"] >= b["close"] >= b["low"] > 0 for b in prepared))

    def test_extract_chart_levels_rejects_invalid_and_respects_vwap_flag(self):
        row = dict(self.live_row, vwap_available=False, stop_loss_price=-5)
        levels = extract_chart_levels(row, {"Giá trigger": 35900, "Mục tiêu 1": 39000})
        self.assertIsNone(levels["vwap"])
        self.assertIsNone(levels["stop_loss"])
        self.assertEqual(levels["entry_trigger"], 35900)
        self.assertEqual(levels["target_price"], 39000)
        self.assertEqual(levels["support"], 32500)

    def test_build_vsa_plotly_figure_renders_traces_and_handles_empty(self):
        prepared = prepare_vsa_bars(self.sample_bars, self.live_row, lookback=30)
        fig = build_vsa_plotly_figure("IDC", prepared, self.live_row, {"Giá trigger": 35800})
        self.assertGreaterEqual(len(fig.data), 4)
        empty_fig = build_vsa_plotly_figure("IDC", [], self.live_row)
        self.assertEqual(len(empty_fig.data), 0)


if __name__ == "__main__":
    unittest.main()