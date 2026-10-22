import unittest

from gacha_core.gacha import GachaError, decode_records, fields, merge_records


class CoreTests(unittest.TestCase):
    def test_record_mapping_drops_private_player_and_item_ids(self):
        drop = b"\x08\x02\x10\x2a\x18\x01\x20\x58"
        record = b"\x08\x07\x12\x08" + drop + b"\x18\x64\x20\x63\x28\x03\x30\x08"
        result = decode_records(b"\x0a" + bytes([len(record)]) + record)
        self.assertEqual(
            result,
            [
                {
                    "pool_id": 7,
                    "timestamp": 100,
                    "index": 3,
                    "record_id": 8,
                    "drops": [{"tuple_type": 2, "item_id": 42, "count": 1}],
                }
            ],
        )
        self.assertEqual(len(merge_records([result, result])), 1)

    def test_truncated_payload_is_not_reported_as_empty_success(self):
        with self.assertRaises(GachaError):
            fields(b"\x0a\x05x")
