import json
import unittest

from quants.table_config import (
    normalize_visible_columns,
    parse_saved_visible_columns,
    serialize_visible_columns,
)


class TableColumnConfigTests(unittest.TestCase):
    def setUp(self):
        self.available = ["Mã CP", "Ngành", "Giá Khớp", "Thay Đổi (%)", "Nguồn T+2"]

    def test_preserves_source_order_not_cookie_order(self):
        self.assertEqual(
            normalize_visible_columns(self.available, ["Nguồn T+2", "Mã CP"]),
            ["Mã CP", "Nguồn T+2"],
        )

    def test_drops_columns_removed_in_new_release(self):
        self.assertEqual(
            parse_saved_visible_columns('["Mã CP", "Đã xóa", "Nguồn T+2"]', self.available),
            ["Mã CP", "Nguồn T+2"],
        )

    def test_bad_cookie_defaults_to_all_columns(self):
        self.assertEqual(parse_saved_visible_columns("not-json", self.available), self.available)

    def test_empty_or_invalid_selection_defaults_to_all(self):
        self.assertEqual(normalize_visible_columns(self.available, []), self.available)
        self.assertEqual(normalize_visible_columns(self.available, ["not-a-column"]), self.available)

    def test_serialization_round_trip_supports_vietnamese_names(self):
        selected = ["Mã CP", "Thay Đổi (%)"]
        serialized = serialize_visible_columns(selected)
        self.assertEqual(json.loads(serialized), selected)

    def test_empty_available_columns_return_empty(self):
        self.assertEqual(normalize_visible_columns([], ["Mã CP"]), [])
        self.assertEqual(parse_saved_visible_columns("invalid", []), [])


if __name__ == "__main__":
    unittest.main()
