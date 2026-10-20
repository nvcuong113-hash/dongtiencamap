import unittest
from quants.providers import _extract_volume, _volume_details, _value_billion, _list

class ProviderNormalizationTests(unittest.TestCase):
    def test_volume_prefers_positive_variant_not_zero_first_field(self):
        self.assertEqual(_extract_volume({'totalVolume': 0, 'totalVol': 125000}), 1250000)

    def test_fvo_numeric_volume_is_supported(self):
        self.assertEqual(_extract_volume({'totalVolume': 0, 'fVo': 1200000}), 1200000)

    def test_boolean_fvo_is_not_mistaken_for_one_share(self):
        self.assertIsNone(_extract_volume({'totalVolume': 0, 'fVo': True, 'lot': 0}))

    def test_missing_volume_is_not_zero(self):
        self.assertIsNone(_extract_volume({'sym': 'ABC', 'lastPrice': 10000}))

    def test_lot_scale_matches_legacy_rule(self):
        self.assertEqual(_extract_volume({'lot': 30000}), 300000)

    def test_value_normalization(self):
        self.assertAlmostEqual(_value_billion({'totalValue': 125000000000}), 125.0)
        self.assertAlmostEqual(_value_billion({'totalValue': 125000000}), 125.0)
        self.assertIsNone(_value_billion({'price': 10000}))

    def test_nested_payload(self):
        self.assertEqual(_list({'data': {'items': [{'sym':'ABC'}]}}), [{'sym':'ABC'}])

if __name__ == '__main__': unittest.main()
