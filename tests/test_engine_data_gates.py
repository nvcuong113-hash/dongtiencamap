import unittest
from quants.engine import classify_wyckoff, _score

class MissingDataGateTests(unittest.TestCase):
    def test_missing_volume_blocks_wyckoff_signal(self):
        r={'volume_available':False,'change_pct':1.2,'vol_ratio':None,'buy_ratio':None,'bounce':0.9,'price':10000,'weekly':{'support':9000,'resistance':12000,'drop_from_peak':0.1},'trading_value_billion':None,'ma20_vol':None,'high':10500,'vwap':10000,'net_flow_billion':None}
        self.assertEqual(classify_wyckoff(r),'Thiếu dữ liệu volume/flow')

    def test_missing_volume_score_is_zero_not_fabricated(self):
        r={'volume_available':False,'is_real_deal':False,'vol_ratio':None,'buy_ratio':None,'vwap_position':'Trên VWAP','weekly':{'bias':'BULLISH'}}
        score = _score(r)
        self.assertEqual(score[1], 0)  # signal_score is zero when volume is unavailable
        self.assertEqual(score[-1], 'CHỜ DỮ LIỆU')

if __name__ == '__main__': unittest.main()
