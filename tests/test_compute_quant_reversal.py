import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from quants import db, engine


class ComputeQuantReversalIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_path = db.DB_PATH
        db.DB_PATH = Path(self.tmp.name) / 'integration.sqlite3'
        db.init_db()

    def tearDown(self):
        db.DB_PATH = self.old_path
        self.tmp.cleanup()

    def test_valid_scan_adds_reversal_features_and_persists_compatibly(self):
        feed = {'ABC': {
            'symbol': 'ABC', 'price': 10000, 'ref_price': 9900, 'change_pct': 1.01,
            'volume': 250000, 'volume_available': True, 'vwap': 9950, 'high': 10200,
            'low': 9800, 'foreign_net': 100, 'source_label': 'fixture', 'provider': 'fixture',
            'divergence_pct': None, 'feed_warning': False,
        }}
        bars = []
        for i in range(90):
            close = 10000 + (i - 70) * 20
            bars.append({'date': f'2026-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}',
                         'close': close, 'high': close * 1.01, 'low': close * 0.99,
                         'volume': 200000 + i * 100})
        now = datetime(2026, 10, 23, 10, 0, tzinfo=ZoneInfo('Asia/Ho_Chi_Minh'))
        with patch.object(engine, 'fetch_stock_feeds', return_value=(feed, [])), \
             patch.object(engine, 'fetch_history', return_value=(bars, 'fixture', [])), \
             patch.object(engine, 'fetch_deal_flow', return_value=None):
            rows, errors = engine.compute_quant(['ABC'], {'valid': True, 'change_pct': 0.2}, now)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        for key in ('trend_state', 'downtrend_exhaustion', 'support_test_quality',
                    'demand_confirmation', 'distribution_risk', 'failed_rebound_risk',
                    'media_price_divergence', 'bottom_signal_memory', 'top_signal_memory',
                    'heuristic_score', 'p_win_status', 'calibrated_p_win'):
            self.assertIn(key, row)
        self.assertEqual(row['p_win_status'], 'HEURISTIC_NOT_CALIBRATED')
        self.assertIsNone(row['calibrated_p_win'])
        self.assertTrue(all(0 <= row[k] <= 100 for k in (
            'downtrend_exhaustion', 'support_test_quality', 'demand_confirmation',
            'distribution_risk', 'failed_rebound_risk', 'media_price_divergence')))
        saved = db.query('SELECT metrics_json FROM stock_snapshots WHERE symbol=?', ('ABC',))
        self.assertEqual(len(saved), 1)
        metrics = json.loads(saved.iloc[0]['metrics_json'])
        self.assertEqual(metrics['p_win_status'], 'HEURISTIC_NOT_CALIBRATED')
        self.assertIn('trend_state', metrics)

    def test_media_context_tracks_attention_and_sentiment_price_divergence(self):
        from datetime import datetime, timedelta, timezone
        recent = (datetime.now(timezone.utc) - timedelta(hours=4)).isoformat()
        with db.connect() as c:
            c.execute("""INSERT INTO media_articles(url_hash,published_at,crawled_at,source,title,url,summary,symbols_json,sentiment)
                         VALUES(?,?,?,?,?,?,?,?,?)""",
                      ('hash-media-1', recent, recent, 'fixture', 'Tin tích cực ABC', 'https://example.invalid/1',
                       'Lợi nhuận tăng', '[\"ABC\"]', 'Tích cực'))
        context = engine._media_context('ABC', '2026-10-23', -2.5)
        self.assertEqual(context['media_article_count_7d'], 1)
        self.assertEqual(context['media_sentiment_score_7d'], 1.0)
        self.assertGreater(context['media_price_divergence'], 50)
        # With no prior-week baseline, attention change is unknown rather than fabricated as +100%.
        self.assertIsNone(context['media_attention_change'])

    def test_historical_signal_memory_reads_prior_days_and_decays(self):
        with db.connect() as c:
            c.execute('''INSERT INTO stock_snapshots(ts,trade_date,symbol,metrics_json)
                         VALUES(?,?,?,?)''', ('2026-10-20', '2026-10-20', 'ABC', json.dumps({
                             'bottom_score': 80, 'top_score': 25, 'support': 100, 'resistance': 120
                         })))
            c.execute('''INSERT INTO stock_snapshots(ts,trade_date,symbol,metrics_json)
                         VALUES(?,?,?,?)''', ('2026-10-21', '2026-10-21', 'ABC', json.dumps({
                             'bottom_score': 60, 'top_score': 35, 'support': 101, 'resistance': 121
                         })))
        memory = engine._historical_reversal_memory('ABC', '2026-10-23', 97, 102, 120)
        self.assertEqual(memory['historical_bottom_peak_20d'], 80)
        self.assertGreater(memory['bottom_signal_memory'], 0)
        self.assertTrue(memory['support_break_event'])
        self.assertEqual(memory['previous_support'], 101)


if __name__ == '__main__':
    unittest.main()
