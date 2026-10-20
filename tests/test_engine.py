import unittest
from datetime import date, datetime
from quants.calendar_vn import add_trading_days
from quants.engine import u_shape_ratio, classify_wyckoff
from quants.media import extract_symbols

class TestCalendar(unittest.TestCase):
    def test_weekend_skipped(self):
        self.assertEqual(add_trading_days(date(2026,10,16),1),date(2026,10,19))
    def test_holiday_skipped(self):
        self.assertEqual(add_trading_days(date(2026,9,1),1),date(2026,9,4))

class TestEngine(unittest.TestCase):
    def test_u_shape_bounds(self):
        self.assertGreaterEqual(u_shape_ratio(datetime(2026,10,20,9,30)),.15)
        self.assertLessEqual(u_shape_ratio(datetime(2026,10,20,14,30)),1.0)
    def test_symbol_extract(self):
        self.assertEqual(extract_symbols('FPT tăng, HPG điều chỉnh và FPT có tin', ['FPT','HPG']),['FPT','HPG'])

if __name__=='__main__': unittest.main()
