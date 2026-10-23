import tempfile
import unittest
from pathlib import Path

from quants import db
from quants.ai_engine import enforce_predictions, run_ai, save_prediction_audit


class PredictionSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db_path = db.DB_PATH
        db.DB_PATH = Path(self.tmp.name) / 'predictions.sqlite3'
        db.init_db()

    def tearDown(self):
        db.DB_PATH = self.old_db_path
        self.tmp.cleanup()

    def test_missing_volume_blocks_all_forecasts_even_if_ai_returns_values(self):
        row = {'symbol': 'ABC', 'price': 10000, 'volume': None, 'volume_available': False,
               'pred_t2': 1, 'pred_t3': 2, 'pred_t7': 3}
        out = enforce_predictions([row], {'ABC': {'vsa': 'bullish', 'pred_t2_pct': 2,
                                                  'pred_t3_pct': 3, 'pred_t7_pct': 5}})[0]
        self.assertEqual(out['ai_action'], 'CHỜ DỮ LIỆU')
        self.assertIsNone(out['pred_t2'])
        self.assertIsNone(out['pred_t3'])
        self.assertIsNone(out['pred_t7'])

    def test_fallback_accepts_descriptive_flow_confidence(self):
        row = {'symbol': 'ABC', 'price': 10000, 'volume': 100000, 'volume_available': True,
               'bottom_score': 55, 'top_score': 30, 'signal_score': 50, 'rs_vni': 0.5,
               'flow_confidence': 'THẤP (Ước tính từ VWAP)', 'supply_decay_score': 45,
               'change_pct': -1.2, 'vwap_position': 'Dưới VWAP', 'wyckoff': 'Sideway / Tích lũy'}
        out = enforce_predictions([row], {})[0]
        self.assertIn('độ tin cậy flow thấp', out['ai_vsa'])
        self.assertIsNotNone(out['pred_t2'])

    def test_run_ai_does_not_send_rows_without_valid_volume(self):
        self.assertEqual(run_ai([{'symbol': 'ABC', 'price': 10000, 'volume': None,
                                  'volume_available': False}], 'Sideway', [], 'Gemini'), {})

    def test_audit_skips_missing_volume_and_deduplicates_repeated_sync(self):
        invalid = {'symbol': 'BAD', 'price': 10000, 'volume': None, 'volume_available': False,
                   'pred_t2': 1, 'pred_t3': 2, 'pred_t7': 3}
        valid = {'symbol': 'ABC', 'price': 10000, 'volume': 1000, 'volume_available': True,
                 'pred_t2': 1, 'pred_t3': 2, 'pred_t7': 3, 'ai_action': 'QUAN SÁT'}
        self.assertEqual(save_prediction_audit([invalid, valid], 'Sideway'), 3)
        self.assertEqual(save_prediction_audit([invalid, valid], 'Sideway'), 0)
        rows = db.query('SELECT symbol,horizon FROM predictions')
        self.assertEqual(len(rows), 3)
        self.assertEqual(set(rows['symbol']), {'ABC'})


if __name__ == '__main__':
    unittest.main()
