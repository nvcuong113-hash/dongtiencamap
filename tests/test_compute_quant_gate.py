import unittest
from unittest.mock import patch
from datetime import datetime
from zoneinfo import ZoneInfo
from quants import engine
from quants.ai_engine import enforce_predictions, run_ai

class ComputeQuantGateTests(unittest.TestCase):
    def test_missing_volume_produces_wait_not_fake_flow(self):
        feed={'ABC': {'symbol':'ABC','price':10000,'ref_price':9900,'change_pct':1.01,'volume':0,'volume_available':False,'volume_field':None,'vwap':10000,'high':10100,'low':9800,'foreign_net':None,'source_label':'VPS','provider':'VPS','divergence_pct':None,'feed_warning':False}}
        bars=[{'date':'2026-10-01','close':9900,'high':10000,'low':9800,'volume':1000} for _ in range(8)]
        with patch.object(engine,'fetch_stock_feeds',return_value=(feed,[])), \
             patch.object(engine,'fetch_history',return_value=(bars,'fixture',[])), \
             patch.object(engine,'fetch_deal_flow',return_value=None), \
             patch.object(engine,'_store_rows',return_value=None):
            rows,errors=engine.compute_quant(['ABC'], {'valid':True,'change_pct':0.2}, datetime(2026,10,20,10,0,tzinfo=ZoneInfo('Asia/Ho_Chi_Minh')))
        self.assertEqual(len(rows),1)
        r=rows[0]
        self.assertEqual(r['action'],'CHỜ DỮ LIỆU')
        self.assertIsNone(r['net_flow_billion'])
        self.assertIsNone(r['buy_vol'])
        self.assertIsNone(r['p_win'])
        self.assertEqual(r['signal_score'],0)
        self.assertFalse(r['is_real_deal'])
        guarded=enforce_predictions([r], {})[0]
        self.assertEqual(guarded['ai_action'],'CHỜ DỮ LIỆU')
        self.assertIsNone(guarded['pred_t2'])

    def test_ai_does_not_call_provider_when_all_volume_missing(self):
        rows=[{'symbol':'ABC','volume':0,'volume_available':False}]
        result=run_ai(rows,'UNKNOWN',[],provider='Gemini')
        self.assertIn('ABC',result['_ai_diagnostics']['blocked_missing_volume'])

if __name__ == '__main__': unittest.main()
