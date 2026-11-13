import unittest

from gacha_core.analysis import analyze_history


def record(pool, index, drops, stamp=None):
    return {
        "pool_id": pool,
        "pool_name": f"池{pool}",
        "timestamp": stamp or index * 100,
        "time_beijing": f"2026-10-{index:02d} 00:00:00",
        "index": index,
        "record_id": 1,
        "drops": [
            {"item_id": card, "name": f"角色{card}", "rarity": star, "count": count}
            for card, star, count in drops
        ],
    }


META = {
    1: {
        "category": "limited",
        "pity_group": "limited",
        "featured_ids": [11],
        "hard_pity": 70,
    },
    2: {
        "category": "limited",
        "pity_group": "limited",
        "featured_ids": [22],
        "hard_pity": 70,
    },
    3: {
        "category": "collection",
        "pity_group": "collection",
        "featured_ids": [33],
        "hard_pity": 70,
    },
    4: {
        "category": "normal",
        "pity_group": "normal",
        "featured_ids": [],
        "hard_pity": 70,
    },
    5: {
        "category": "beginner",
        "pity_group": "beginner",
        "featured_ids": [],
        "hard_pity": None,
    },
}


class AnalysisTests(unittest.TestCase):
    def test_ring_wrap_preserves_cross_pool_interval_and_guarantee(self):
        rows = [
            record(1, 79, [(99, 5, 1), (5, 3, 3)], stamp=100),
            record(1, 80, [(5, 3, 10)], stamp=200),
            record(2, 1, [(5, 3, 9), (22, 5, 1)], stamp=300),
        ]
        result = analyze_history(rows, META)
        event = result["pools"][0]["events"][0]
        self.assertEqual(event["pulls_since_five"], 23)
        self.assertEqual(event["carried_pulls"], 13)
        self.assertTrue(event["interval_exact"])
        self.assertTrue(event["guaranteed"])

    def test_real_gap_at_ring_boundary_still_invalidates_interval(self):
        rows = [
            record(1, 79, [(99, 5, 1), (5, 3, 3)], stamp=100),
            record(2, 1, [(22, 5, 1)], stamp=200),
        ]
        event = analyze_history(rows, META)["pools"][0]["events"][0]
        self.assertEqual(event["pulls_since_five"], 1)
        self.assertFalse(event["interval_exact"])
        self.assertIsNone(event["guaranteed"])

    def test_average_is_all_visible_pulls_per_five_including_first_and_tail(self):
        rows = [
            record(4, 1, [(5, 3, 3), (99, 5, 1), (5, 3, 6)]),
            record(4, 2, [(99, 5, 1), (5, 3, 9)]),
        ]
        stats = analyze_history(rows, META)["categories"]["normal"]
        self.assertEqual(stats["average_five_interval"], 10)
        first = analyze_history(rows[:1], META)["categories"]["normal"]
        self.assertEqual(first["average_five_interval"], 10)
        empty = analyze_history([], META)["categories"]["normal"]
        self.assertIsNone(empty["average_five_interval"])

    def test_average_rounds_half_up_consistently_with_android(self):
        rows = [record(4, 1, [(99, 5, 8), (5, 3, 333)])]
        self.assertEqual(
            analyze_history(rows, META)["categories"]["normal"][
                "average_five_interval"
            ],
            42.63,
        )

    def test_cross_pool_interval_and_guaranteed_up_after_off_banner(self):
        rows = [
            record(1, 1, [(5, 3, 3), (99, 5, 1), (5, 3, 2)]),
            record(2, 2, [(5, 3, 4), (22, 5, 1), (5, 3, 2)]),
        ]
        result = analyze_history(rows, META)
        newest = result["pools"][0]
        event = newest["events"][0]
        self.assertEqual(event["pulls_since_five"], 7)
        self.assertEqual(event["carried_pulls"], 2)
        self.assertTrue(event["interval_exact"])
        self.assertEqual(event["outcome"], "up")
        self.assertTrue(event["guaranteed"])
        self.assertEqual(result["categories"]["limited"]["pull_count"], 13)
        self.assertEqual(result["categories"]["limited"]["since_five"], 2)
        self.assertFalse(result["categories"]["limited"]["next_guaranteed"])

    def test_collection_does_not_consume_limited_pity_or_guarantee(self):
        rows = [
            record(1, 1, [(99, 5, 1)]),
            record(3, 1, [(33, 5, 1)], stamp=150),
            record(2, 2, [(22, 5, 1)]),
        ]
        result = analyze_history(rows, META)
        event = next(p for p in result["pools"] if p["pool_id"] == 2)["events"][0]
        self.assertEqual(event["pulls_since_five"], 1)
        self.assertTrue(event["guaranteed"])
        self.assertEqual(result["categories"]["collection"]["five_count"], 1)

    def test_gap_invalidates_interval_and_guarantee_until_another_star(self):
        rows = [record(1, 1, [(99, 5, 1)]), record(2, 4, [(22, 5, 1)])]
        event = analyze_history(rows, META)["pools"][0]["events"][0]
        self.assertFalse(event["interval_exact"])
        self.assertIsNone(event["guaranteed"])

    def test_first_star_is_lower_bound_and_multidrop_order_is_preserved(self):
        row = record(1, 10, [(5, 3, 3), (99, 5, 1), (11, 5, 1)])
        events = analyze_history([row], META)["pools"][0]["events"]
        self.assertEqual([e["pulls_since_five"] for e in events], [4, 1])
        self.assertFalse(events[0]["interval_exact"])
        self.assertTrue(events[1]["interval_exact"])
        self.assertTrue(events[1]["guaranteed"])

    def test_no_five_and_beginner_are_not_fabricated_into_pity(self):
        result = analyze_history(
            [record(4, 1, [(5, 3, 9)]), record(5, 1, [(5, 3, 10)], stamp=150)], META
        )
        self.assertFalse(result["categories"]["normal"]["since_five_exact"])
        self.assertEqual(result["categories"]["normal"]["since_five"], 9)
        self.assertIsNone(result["categories"]["normal"]["next_guaranteed"])
        self.assertEqual(result["categories"]["beginner"]["pull_count"], 10)
        self.assertEqual(
            sum(v["pull_count"] for v in result["categories"].values()), 19
        )

    def test_unknown_pool_does_not_receive_a_false_off_banner_label(self):
        result = analyze_history([record(999, 1, [(99, 5, 1)])], META)
        self.assertEqual(result["pools"][0]["category"], "unknown")
        self.assertEqual(result["pools"][0]["events"][0]["outcome"], "unknown")

    def test_input_order_does_not_change_analysis(self):
        rows = [record(1, 1, [(99, 5, 1)]), record(2, 2, [(22, 5, 1)])]
        self.assertEqual(analyze_history(rows, META), analyze_history(rows[::-1], META))

    def test_carry_label_excludes_previous_records_in_the_same_pool(self):
        rows = [
            record(1, 1, [(99, 5, 1), (5, 3, 2)]),
            record(2, 2, [(5, 3, 3)]),
            record(2, 3, [(5, 3, 4), (22, 5, 1)]),
        ]
        event = analyze_history(rows, META)["pools"][0]["events"][0]
        self.assertEqual(event["pulls_since_five"], 10)
        self.assertEqual(event["carried_pulls"], 2)
