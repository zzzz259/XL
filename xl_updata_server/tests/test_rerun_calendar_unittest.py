"""Calendar contracts: resource cycles are dates, versions are identities."""
import importlib
import importlib.util
import unittest
from datetime import date, datetime

BOTTOMS = {"1": {"bottom_type": 201}, "2": {"bottom_type": 301}}
NAMES = {"101": "甲", "102": "乙", "103": "丙", "104": "丁"}


def pool(identifier, character=101, *, special=False):
    return {"id": identifier, "type": 2, "bottom_main_type": 2 if special else 1,
            "bottom_up": 2 if special else 1, "main_card_ids": [character], "sort": identifier}


class CalendarTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec("server_app.rerun_calendar"), "calendar implementation missing")
        self.api = importlib.import_module("server_app.rerun_calendar")
        self.tz = self.api.SHANGHAI

    def merge(self, previous=None, pools=None, version="v1", when="2026-11-20T00:00:00+08:00", **kwargs):
        return self.api.merge_calendar(previous or {}, pools or [pool(10)], BOTTOMS, NAMES,
                                       source_version=version, observed_at=datetime.fromisoformat(when), **kwargs)

    def test_exact_resource_cycle_and_pool_window(self):
        self.assertEqual(self.api.cycle_for_date(date(2026, 12, 10), date(2026, 11, 20)),
                         (date(2026, 11, 20), date(2026, 12, 11)))
        self.assertEqual(self.api.cycle_for_date(date(2026, 12, 11), date(2026, 11, 20))[0], date(2026, 12, 11))
        self.assertEqual(tuple(x.isoformat() for x in self.api.pool_window(date(2026, 11, 20))),
                         ("2026-11-24T10:00:00+08:00", "2026-12-15T05:00:00+08:00"))

    def test_negative_cycles_cross_year(self):
        self.assertEqual(self.api.cycle_for_date(date(2026, 11, 19), date(2026, 11, 20))[0], date(2026, 10, 30))
        self.assertEqual(self.api.cycle_for_date(date(2027, 1, 1), date(2026, 11, 20))[0], date(2027, 1, 1))

    def test_anchor_must_be_friday_and_naive_observation_is_rejected(self):
        with self.assertRaises(ValueError):
            self.api.cycle_for_date(date(2026, 11, 20), date(2026, 11, 19))
        with self.assertRaises(ValueError):
            self.api.pool_window(date(2026, 11, 19))
        with self.assertRaisesRegex(ValueError, "aware"):
            self.api.merge_calendar({}, [pool(10)], BOTTOMS, NAMES, source_version="v", observed_at=datetime(2026, 11, 20))

    def test_datetime_values_are_rejected_as_cycle_natural_dates(self):
        instant = datetime.fromisoformat("2026-11-20T00:00:00+08:00")
        with self.assertRaises(TypeError):
            self.api.cycle_for_date(instant, date(2026, 11, 20))
        with self.assertRaises(TypeError):
            self.api.cycle_for_date(date(2026, 11, 20), instant)
        with self.assertRaises(TypeError):
            self.api.pool_window(instant)

    def test_utc_observation_uses_beijing_natural_date(self):
        result = self.merge(when="2026-11-19T16:00:00+00:00")
        self.assertEqual(result["pools"]["10"]["cycle_start"], "2026-11-20")

    def test_reparse_preserves_first_seen_version_cycle_and_version_first_seen(self):
        original = self.merge(version="999")
        original_seen = original["versions"]["999"]["first_seen_at"]
        result = self.merge(original, [dict(pool(10), sort=2), pool(20, 102)], version="1", when="2026-12-11T23:00:00+08:00")
        self.assertEqual(result["pools"]["10"]["first_seen_version"], "999")
        self.assertEqual(result["pools"]["10"]["cycle_start"], "2026-11-20")
        self.assertEqual(result["pools"]["20"]["cycle_start"], "2026-12-11")
        again = self.merge(result, version="999", when="2027-01-01T00:00:00+08:00")
        self.assertEqual(again["versions"]["999"]["first_seen_at"], original_seen)

    def test_hotfix_version_in_same_cycle_shares_pool_window(self):
        result = self.merge(self.merge(), [pool(20, 102)], version="hotfix", when="2026-12-10T23:59:59+08:00")
        self.assertEqual(result["pools"]["10"]["start_at"], result["pools"]["20"]["start_at"])
        self.assertEqual(result["pools"]["20"]["date_source"], "version_cycle")
        self.assertEqual(result["pools"]["20"]["gacha_id"], 20)

    def test_historical_import_requires_explicit_cycle_mapping_but_keeps_archived(self):
        skipped = self.merge(historical_import=True)
        self.assertEqual(skipped["pools"], {})
        self.assertTrue(skipped["diagnostics"])
        seeded = {"archived_pool_ids": [10], "versions": {"old": {"cycle_start": "2026-10-30", "first_seen_at": None}}}
        result = self.merge(seeded, [pool(10), pool(20, 102)], version="old", historical_import=True)
        self.assertNotIn("start_at", result["pools"]["10"])
        self.assertEqual(result["pools"]["20"]["start_at"], "2026-11-03T10:00:00+08:00")

    def test_conflicting_up_or_type_retains_reliable_semantics_with_diagnostic(self):
        original = self.merge()
        result = self.merge(original, [pool(10, 102, special=True)], version="v2")
        self.assertEqual(result["pools"]["10"]["main_card_ids"], [101])
        self.assertEqual(result["pools"]["10"]["bottom_main_type"], 1)
        self.assertEqual(original["diagnostics"], [])
        self.assertTrue(any(item["reason"] == "pool_semantic_conflict" for item in result["diagnostics"]))

    def test_malformed_bottomup_updates_preserve_reliable_reference(self):
        original = self.merge()
        project = importlib.import_module("server_app.rerun_schedule").project_calendar
        now = datetime.fromisoformat("2026-11-20T00:00:00+08:00")
        for value in (None, [], "broken", {}, {"bottom_type": None}):
            with self.subTest(value=value):
                result = self.api.merge_calendar(original, [pool(20, 102)], {"1": value}, NAMES,
                    source_version="malformed", observed_at=now)
                self.assertEqual(result["bottomups"]["1"], {"bottom_type": 201})
                self.assertEqual(set(result["pools"]), {"10", "20"})
                self.assertTrue(any(item["reason"] == "bottom_up_semantic_conflict"
                                    for item in result["diagnostics"]))
                self.assertEqual([item["gacha_id"] for item in project(result, now=now)["events"]], [10, 20])
        self.assertEqual(original["bottomups"]["1"], {"bottom_type": 201})
        self.assertEqual(original["diagnostics"], [])

    def test_explicit_unknown_override_retains_pool_for_lifecycle_audit(self):
        previous = {"overrides": [{"gacha_id": 10, "pool_kind": "unknown", "source": "operator review"}]}
        result = self.merge(previous)
        self.assertIn("10", result["pools"])
        self.assertEqual(result["pools"]["10"]["cycle_start"], "2026-11-20")

    def test_nonpositive_and_boolean_ids_are_skipped_but_numeric_strings_remain_valid(self):
        for invalid in (0, -1, "0", "-1", True, False):
            with self.subTest(identifier=invalid):
                result = self.merge(pools=[pool(invalid), pool("40", "104")])
                self.assertEqual(set(result["pools"]), {"40"})
                self.assertTrue(any(item["reason"] == "invalid_gacha_id" for item in result["diagnostics"]))
            with self.subTest(character=invalid):
                result = self.merge(pools=[pool(20, invalid), pool("40", "104")])
                self.assertEqual(set(result["pools"]), {"40"})
                self.assertTrue(any(item["reason"] == "invalid_main_character_id" for item in result["diagnostics"]))

    def test_invalid_records_do_not_poison_valid_pool(self):
        unknown = dict(pool(20), bottom_up=77)
        missing = dict(pool(30), main_card_ids=[])
        result = self.merge(pools=[unknown, missing, pool(40, 104)])
        self.assertEqual(set(result["pools"]), {"40"})
        self.assertEqual(len(result["diagnostics"]), 2)


class CalendarProjectionTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec("server_app.rerun_calendar"), "calendar implementation missing")
        self.api = importlib.import_module("server_app.rerun_calendar")
        module = importlib.import_module("server_app.rerun_schedule")
        self.assertTrue(callable(getattr(module, "project_calendar", None)), "calendar projection missing")
        self.project = module.project_calendar

    def calendar(self, archived=(), dated=()):
        previous = {"archived_pool_ids": [x["id"] for x in archived]}
        result = self.api.merge_calendar(previous, list(archived), BOTTOMS, NAMES, source_version="archive",
                                         observed_at=datetime.fromisoformat("2026-11-20T00:00:00+08:00"), historical_import=True)
        for version, cycle, records in dated:
            result = self.api.merge_calendar(result, records, BOTTOMS, NAMES, source_version=version,
                                             observed_at=datetime.fromisoformat(cycle + "T00:00:00+08:00"))
        return result

    def project_at(self, calendar, at="2026-11-20T00:00:00+08:00", **kwargs):
        return self.project(calendar, now=datetime.fromisoformat(at), **kwargs)

    def test_one_two_three_same_cycle_pools_group_once(self):
        for count in (1, 2, 3):
            with self.subTest(count=count):
                records = [pool(100, 101), pool(110, 102), pool(120, 103, special=True)][:count]
                if count == 3:
                    records[-1]["main_card_ids"] = [103, 104]
                result = self.project_at(self.calendar(dated=[("v1", "2026-11-20", records)]))
                groups = result["render"]["known"]
                self.assertEqual(len(groups), 1)
                self.assertEqual(len(groups[0]["pools"]), count)
                self.assertEqual(groups[0]["startsAt"], "2026.11.24 10:00")
                self.assertEqual(groups[0]["endsAt"], "2026.12.15 05:00")
                self.assertEqual(groups[0]["badge"], "已知")
                self.assertTrue(all(x["label"] for x in groups[0]["pools"]))
                self.assertEqual(result["schema_version"], 2)
                self.assertEqual(result["source_version"], "v1")
                if count == 3:
                    self.assertEqual(groups[0]["pools"][-1]["names"], ["丙", "丁"])
                    self.assertEqual(len(result["queue"]), 2)

    def test_multiple_future_known_cycles_and_exact_expiry_only_hide_display(self):
        calendar = self.calendar(dated=[("v1", "2026-11-20", [pool(100)]), ("v2", "2026-12-11", [pool(110, 102)])])
        calendar["pools"]["120"] = pool(120, 103)  # Unknown cycle must not enter lifecycle.
        calendar["pools"]["130"] = dict(pool(130, 104), cycle_start="2026-12-18",
            start_at="2026-12-22T10:00:00+08:00", end_at="2027-01-12T05:00:00+08:00")
        calendar["pools"]["140"] = dict(pool(140, 104), cycle_start="2026-11-20",
            start_at=None, end_at="2026-12-15T05:00:00+08:00")
        before = self.project_at(calendar, "2026-12-15T04:59:59+08:00")
        self.assertEqual(len(before["events"]), 2)
        self.assertEqual(len(before["diagnostics"]), 3)
        expired = self.project_at(calendar, "2026-12-15T05:00:00+08:00")
        self.assertEqual(len(before["render"]["known"]), 2)
        self.assertEqual(len(expired["render"]["known"]), 1)
        self.assertEqual(before["queue"], expired["queue"])
        self.assertEqual(before["events"], expired["events"])

    def test_actual_future_first_rerun_removes_actual_id_and_later_rerun_never_requeues(self):
        calendar = self.calendar([pool(10), pool(20, 102), pool(30, 103)],
            [("v1", "2026-11-20", [pool(100, 102), pool(110, 102)])])
        result = self.project_at(calendar)
        self.assertEqual([x["character_id"] for x in result["queue"]], [101, 103])
        self.assertEqual([x["event_kind"] for x in result["events"]][-2:], ["first_rerun", "later_rerun"])
        self.assertTrue(any(x["reason"] == "out_of_order_first_rerun" for x in result["anomalies"]))
        self.assertEqual(result["forecasts"][0]["start_at"], "2026-12-15T10:00:00+08:00")
        self.assertEqual(result["render"]["known"][0]["pools"][0]["label"], "首复刻")
        # A later actual rerun consumes a normal cycle, without requeueing 102.
        future = self.api.merge_calendar(calendar, [pool(120, 102)], BOTTOMS, NAMES,
            source_version="future", observed_at=datetime.fromisoformat("2026-12-11T00:00:00+08:00"))
        projected = self.project_at(future)
        self.assertEqual([x["character_id"] for x in projected["queue"]], [101, 103])
        self.assertEqual(projected["forecasts"][0]["start_at"], "2027-01-05T10:00:00+08:00")

    def test_special_does_not_shift_normal_forecast_base_or_consume_slot(self):
        calendar = self.calendar([pool(10), pool(20, 102)], [("v1", "2026-11-20", [pool(100, 101)]),
            ("v2", "2026-12-11", [pool(110, 102, special=True)])])
        result = self.project_at(calendar)
        self.assertEqual([x["character_id"] for x in result["queue"]], [102])
        self.assertEqual(result["forecasts"][0]["start_at"], "2026-12-15T10:00:00+08:00")

    def test_time_does_not_fulfill_queue_or_slide_forecast_dates(self):
        calendar = self.calendar([pool(10), pool(20, 102), pool(30, 103)], [("v1", "2026-11-20", [pool(100)])])
        before = self.project_at(calendar)
        later = self.project_at(calendar, "2027-01-26T05:00:00+08:00")
        self.assertEqual(before["forecasts"], later["forecasts"])
        self.assertEqual(before["queue"], later["queue"])
        self.assertEqual(later["render"]["known"], [])
        self.assertEqual(later["render"]["firstReruns"], [])
        self.assertEqual(later["queue"][0]["character_id"], 102)

    def test_empty_known_can_show_forecast_and_render_limit_keeps_complete_history(self):
        calendar = self.calendar([pool(10), pool(20, 102), pool(30, 103)], [("v1", "2026-11-20", [pool(100)])])
        result = self.project_at(calendar, "2026-12-15T05:00:00+08:00", forecast_limit=1)
        self.assertEqual(result["render"]["known"], [])
        self.assertEqual(len(result["render"]["firstReruns"]), 1)
        self.assertEqual(len(result["forecasts"]), 2)
        self.assertEqual(result["render"]["next"]["name"], "乙")

    def test_no_dated_normal_does_not_fabricate_dates_without_reliable_base(self):
        calendar = self.calendar([pool(10)])
        result = self.project_at(calendar)
        self.assertEqual(result["forecasts"], [])
        self.assertEqual(len(result["queue"]), 1)
        calendar["last_reliable_cycle_start"] = "2026-11-20"
        self.assertEqual(self.project_at(calendar)["forecasts"][0]["start_at"], "2026-12-15T10:00:00+08:00")

    def test_archived_normal_version_mapping_can_supply_reliable_forecast_base(self):
        calendar = self.calendar([pool(10)])
        calendar["versions"]["archive"] = {"cycle_start": "2026-10-30", "first_seen_at": None}
        result = self.project_at(calendar)
        self.assertEqual(len(result["forecasts"]), 1)
        self.assertEqual(result["forecasts"][0]["start_at"], "2026-11-24T10:00:00+08:00")
        self.assertEqual(result["render"]["known"], [])

    def test_no_default_anchor_double_mutation_and_calendar_overrides_pass_through(self):
        calendar = self.calendar([pool(10), pool(20, 102)], [("v1", "2026-11-20", [pool(100)])])
        calendar["overrides"] = [{"gacha_id": 20, "exclude_from_prediction": True, "source": "test verified"}]
        result = self.project_at(calendar)
        self.assertEqual(result["queue"], [])
        self.assertEqual(len(result["events"]), 3)
        self.assertTrue(any(x["reason"] == "manual_override_applied" for x in result["anomalies"]))

    def test_dated_unknown_override_keeps_event_and_anomaly_without_consuming_normal_slot(self):
        calendar = self.calendar([pool(10), pool(20, 102)], [
            ("v1", "2026-11-20", [pool(100)]), ("v2", "2026-12-11", [pool(110, 102)])])
        calendar["overrides"] = [{"gacha_id": 110, "pool_kind": "unknown", "source": "operator review"}]
        result = self.project_at(calendar)
        self.assertEqual([item["gacha_id"] for item in result["events"]], [10, 20, 100, 110])
        unknown = result["events"][-1]
        self.assertEqual(unknown["pool_kind"], "unknown")
        self.assertEqual(unknown["event_kind"], "unknown")
        self.assertEqual(unknown["manual_override_source"], "operator review")
        self.assertTrue(any(item["gacha_id"] == 110 and item["reason"] == "manual_override_applied"
                            for item in result["anomalies"]))
        self.assertEqual([item["character_id"] for item in result["queue"]], [102])
        self.assertEqual([item["gacha_id"] for item in result["known"]], [100])
        self.assertEqual(len(result["render"]["known"]), 1)
        self.assertEqual(result["forecast_base_cycle_start"], "2026-11-20")
        self.assertEqual(result["forecasts"][0]["start_at"], "2026-12-15T10:00:00+08:00")
        self.assertFalse(any(item.get("gacha_id") == 110 for item in result["diagnostics"]))

    def test_direct_dated_invalid_ids_cannot_inject_lifecycle(self):
        for invalid in (0, -1, "0", "-1", True, False):
            for field in ("id", "main_card_ids"):
                with self.subTest(value=invalid, field=field):
                    calendar = self.calendar(dated=[("v1", "2026-11-20", [pool("100", "101")])])
                    malformed = dict(calendar["pools"]["100"], id=200, gacha_id=200, main_card_ids=[102])
                    malformed[field] = [invalid] if field == "main_card_ids" else invalid
                    calendar["pools"]["malformed"] = malformed
                    result = self.project_at(calendar)
                    self.assertEqual([item["gacha_id"] for item in result["events"]], [100])
                    self.assertEqual([item["character_id"] for item in result["queue"]], [101])
                    self.assertEqual(len(result["known"]), 1)
                    self.assertEqual(len(result["diagnostics"]), 1)

    def test_direct_archived_invalid_ids_roles_and_classification_are_excluded(self):
        invalid_fields = (
            {"id": 0}, {"id": -1}, {"id": True},
            {"main_card_ids": [0]}, {"main_card_ids": [-1]}, {"main_card_ids": [False]},
            {"main_card_ids": []}, {"bottom_up": 77},
        )
        for fields in invalid_fields:
            with self.subTest(fields=fields):
                calendar = self.calendar([pool(10)])
                invalid = dict(pool(60, 102), **fields)
                calendar["pools"]["invalid_seed"] = invalid
                calendar["archived_pool_ids"].append(str(invalid["id"]))
                # An intentional unknown override remains complete audit evidence.
                calendar["pools"]["70"] = dict(pool(70, 103), bottom_up=77, main_card_ids=[])
                calendar["archived_pool_ids"].append("70")
                calendar["overrides"] = [{"gacha_id": 70, "pool_kind": "unknown", "source": "operator review"}]
                result = self.project_at(calendar)
                self.assertEqual([item["gacha_id"] for item in result["events"]], [10, 70])
                self.assertEqual([item["character_id"] for item in result["queue"]], [101])
                self.assertEqual(result["events"][-1]["event_kind"], "unknown")
                self.assertTrue(any(item["gacha_id"] == 70 and item["reason"] == "manual_override_applied"
                                    for item in result["anomalies"]))
                self.assertEqual(result["known"], [])
                self.assertEqual(len(result["diagnostics"]), 1)
                self.assertEqual(result["diagnostics"][0]["reason"], "invalid_calendar_pool")

    def test_naive_now_and_nonpositive_render_limit_rejected(self):
        with self.assertRaisesRegex(ValueError, "aware"):
            self.project(self.calendar(), now=datetime(2026, 11, 20))
        with self.assertRaises(ValueError):
            self.project_at(self.calendar(), forecast_limit=0)


if __name__ == "__main__":
    unittest.main()
