"""Regression coverage for first-rerun lifecycle reconstruction."""

import unittest

from server_app.gacha_history import rebuild_gacha_history


def pool(gacha_id, character_id, *, kind=1, bottom_type=201, pool_type=2, main_ids=None):
    return {
        "id": gacha_id,
        "type": pool_type,
        "card_ids": [character_id],
        "main_card_ids": [character_id] if main_ids is None else main_ids,
        "bottom_up": kind,
        "bottom_main_type": kind,
        "sort": gacha_id,
        "bottom_type": bottom_type,
    }


class GachaHistoryTests(unittest.TestCase):
    def test_report_aggregate_regression_fixture_yields_the_confirmed_eleven_queue(self):
        # This synthetic fixture encodes the report's confirmed aggregate counts
        # and final queue; it is not represented as a re-audit of the missing ZIP.
        final_ids = [
            10000214, 10000215, 10000174, 10000183, 10000144,
            10000216, 10000217, 10000218, 10000221, 10000222, 10000223,
        ]
        debut_ids = list(range(10000001, 10000030)) + final_ids
        normal_bottom = {1: {"bottom_type": 201}}
        special_bottom = {2: {"bottom_type": 301}}
        pools = [pool(index, character_id) for index, character_id in enumerate(debut_ids, 1)]
        pools.extend(
            pool(100 + index, character_id)
            for index, character_id in enumerate(debut_ids[:29], 1)
        )
        pools.extend(
            pool(200 + index, 20000000 + index % 8, kind=2, bottom_type=301)
            for index in range(1, 17)
        )
        result = rebuild_gacha_history(pools, {**normal_bottom, **special_bottom})
        kinds = [event["pool_kind"] for event in result["events"]]
        event_kinds = [event["event_kind"] for event in result["events"]]
        special_ids = {
            character_id
            for event in result["events"] if event["pool_kind"] == "special"
            for character_id in event["character_ids"]
        }

        self.assertEqual(len(result["events"]), 85)
        self.assertEqual(kinds.count("normal"), 69)
        self.assertEqual(kinds.count("special"), 16)
        self.assertEqual(event_kinds.count("debut"), 40)
        self.assertEqual(event_kinds.count("first_rerun"), 29)
        self.assertEqual(len(special_ids), 8)
        self.assertEqual([item["character_id"] for item in result["queue"]], final_ids)

    def test_out_of_order_first_rerun_removes_actual_then_later_rerun_is_ignored(self):
        pools = [
            pool(10, 101),
            pool(20, 102),
            pool(30, 103),
            pool(40, 102),  # FIFO expected 101; actual 102 is corrected.
            pool(50, 101),
            pool(60, 102),  # second rerun; no queue mutation.
        ]
        result = rebuild_gacha_history(pools, {1: {"bottom_type": 201}}, {101: "甲", 102: "乙", 103: "丙"})

        self.assertEqual([item["character_id"] for item in result["queue"]], [103])
        self.assertEqual([event["event_kind"] for event in result["events"]], [
            "debut", "debut", "debut", "first_rerun", "first_rerun", "later_rerun",
        ])
        self.assertEqual(result["anomalies"][0]["reason"], "out_of_order_first_rerun")
        self.assertEqual(result["anomalies"][0]["expected_character_id"], 101)
        self.assertEqual(result["anomalies"][0]["actual_character_id"], 102)

    def test_special_and_unknown_pools_never_change_normal_queue(self):
        pools = [
            pool(10, 101),
            pool(20, 201, kind=2, bottom_type=301),
            pool(30, 999, kind=3, bottom_type=401),
            pool(40, 303, main_ids=[303, 304]),
        ]
        result = rebuild_gacha_history(pools, {1: {"bottom_type": 201}, 2: {"bottom_type": 301}}, {})

        self.assertEqual([item["character_id"] for item in result["queue"]], [101])
        self.assertEqual([event["pool_kind"] for event in result["events"]], ["normal", "special", "unknown", "unknown"])
        self.assertEqual(len(result["anomalies"]), 2)

    def test_unknown_pool_type_and_missing_main_ids_are_retained_as_diagnostics(self):
        pools = [
            {"id": 1, "type": 2, "bottom_up": 77, "bottom_main_type": 1, "main_card_ids": [1]},
            {"id": 2, "type": 2, "bottom_up": 1, "bottom_main_type": 1, "card_ids": [2]},
        ]
        result = rebuild_gacha_history(pools, {1: {"bottom_type": 201}}, {})

        self.assertEqual(len(result["events"]), 2)
        self.assertTrue(all(event["pool_kind"] == "unknown" for event in result["events"]))
        self.assertEqual(len(result["anomalies"]), 2)

    def test_id_scoped_override_can_correct_pool_kind_and_records_its_source(self):
        result = rebuild_gacha_history(
            [pool(77, 999, kind=3, bottom_type=401)],
            {1: {"bottom_type": 201}},
            {999: "测试角色"},
            overrides=[{
                "gacha_id": 77,
                "pool_kind": "normal",
                "character_id": 10000214,
                "source": "operator verified against source notice",
            }],
        )
        self.assertEqual([item["character_id"] for item in result["queue"]], [10000214])
        override = next(item for item in result["anomalies"] if item["reason"] == "manual_override_applied")
        self.assertEqual(override["source"], "operator verified against source notice")
        self.assertEqual(override["gacha_id"], 77)


if __name__ == "__main__":
    unittest.main()
