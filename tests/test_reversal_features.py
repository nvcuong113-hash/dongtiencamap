import unittest
from quants.reversal import (
    classify_trend_state, count_independent_retests, compute_reversal_features,
    media_divergence_score, evaluate_forward_events,
)
from quants.engine import classify_wyckoff, compute_bottom_top_detectors, _weekly_and_structure_features


class ReversalFeatureTests(unittest.TestCase):
    def test_downtrend_is_not_automatically_accumulation(self):
        row = {
            'volume_available': True, 'volume': 100000, 'price': 9000,
            'high': 9200, 'low': 8800, 'change_pct': -1.2,
            'vol_ratio': 0.9, 'buy_ratio': 48, 'vwap_position': 'Dưới VWAP',
            'bottom_score': 30, 'top_score': 20, 'weekly_trend': 'Dưới MA20W',
            'ma10w': 10000, 'ma20w': 10500, 'ret5': -6,
        }
        row['trend_state'] = classify_trend_state(9000, 10000, 10500, -6, 'Dưới MA20W')
        self.assertEqual(row['trend_state'], 'DOWNTREND')
        self.assertEqual(classify_wyckoff(row), 'Markdown / Xu hướng giảm')

    def test_retests_count_independent_tests_not_consecutive_candles(self):
        bars = [
            {'low': 99, 'high': 101, 'close': 100},
            {'low': 99, 'high': 101, 'close': 100},
            {'low': 98, 'high': 100, 'close': 99},
            {'low': 101, 'high': 104, 'close': 103},
            {'low': 102, 'high': 105, 'close': 104},
            {'low': 99, 'high': 101, 'close': 100},
        ]
        self.assertEqual(count_independent_retests(bars, 100, min_gap=3), 2)

    def test_features_are_bounded_and_separate(self):
        f = compute_reversal_features({
            'price': 100, 'support': 99, 'resistance': 110, 'change_pct': -1.0,
            'vol_ratio': 1.3, 'close_position': 0.2, 'retest_count': 3,
            'supply_decay_score': 70, 'downside_eff': 1.5, 'upside_eff': 0.5,
            'weekly_trend': 'Dưới MA20W', 'ma10w': 105, 'ma20w': 108,
            'vwap_position': 'Dưới VWAP', 'media_price_divergence': 75,
        })
        for key in ('downtrend_exhaustion', 'support_test_quality', 'demand_confirmation',
                    'distribution_risk', 'failed_rebound_risk', 'media_price_divergence'):
            self.assertGreaterEqual(f[key], 0)
            self.assertLessEqual(f[key], 100)
        self.assertEqual(f['trend_state'], 'DOWNTREND')

    def test_media_price_divergence_detects_positive_news_weak_price(self):
        self.assertGreater(media_divergence_score(0.8, -3.0), 50)
        self.assertLess(media_divergence_score(0.0, -3.0), 50)

    def test_forward_event_labels_use_future_only_for_evaluation(self):
        bars = [
            {'date': 'd1', 'close': 100, 'high': 101, 'low': 99},
            {'date': 'd2', 'close': 98, 'high': 100, 'low': 97},
            {'date': 'd3', 'close': 103, 'high': 106, 'low': 98},
            {'date': 'd4', 'close': 95, 'high': 104, 'low': 93},
        ]
        labels = evaluate_forward_events(bars, horizon=2, threshold_pct=5)
        self.assertEqual(labels[0]['date'], 'd1')
        self.assertTrue(labels[0]['bottom_event'])
        self.assertTrue(labels[1]['top_event'])
        self.assertIn('mfe_pct', labels[0])
        self.assertIn('mae_pct', labels[0])
        self.assertEqual(labels[0]['bottom_lead_time_sessions'], 2)

    def test_structure_retest_count_is_non_negative(self):
        bars = []
        for i in range(65):
            low = 100 - (i % 5 == 0) * 1
            bars.append({'date': f'2026-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}', 'close': 101, 'high': 102, 'low': low, 'volume': 1000})
        out = _weekly_and_structure_features(bars)
        self.assertGreaterEqual(out['retest_count'], 0)
        self.assertIsNotNone(out['ma20w'])


if __name__ == '__main__':
    unittest.main()
