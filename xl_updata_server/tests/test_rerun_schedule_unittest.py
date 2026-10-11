"""Projection and durable publication tests for rerun schedule snapshots."""

import hashlib
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from server_app.rerun_schedule import (
    DEFAULT_ANCHOR,
    project_schedule,
    publish_schedule_snapshot,
    read_current_schedule,
)

QUEUE = [
    {"character_id": 10000214, "name": "朝雾", "debut_gacha_id": 24000060},
    {"character_id": 10000215, "name": "鎺", "debut_gacha_id": 24000062},
]
PNG = b"\x89PNG\r\n\x1a\nimage-bytes"


class RerunScheduleProjectionTests(unittest.TestCase):
    def test_version_snapshot_does_not_advance_with_wall_clock(self):
        class OctoberDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 10, 11, 12, tzinfo=tz)

        class JanuaryDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2027, 1, 10, 12, tzinfo=tz)

        history = {
            "queue": QUEUE,
            "events": [],
            "anomalies": [],
        }
        with patch("server_app.rerun_schedule.datetime", OctoberDateTime):
            before = project_schedule(history, source_version="134351472190013976")
        with patch("server_app.rerun_schedule.datetime", JanuaryDateTime):
            much_later = project_schedule(history, source_version="134351472190013976")

        self.assertEqual(before["render"]["current"]["newName"], "雾铃")
        self.assertEqual(before["render"]["current"]["rerunName"], "朝雾")
        self.assertEqual(much_later["render"]["current"]["newName"], "雾铃")
        self.assertEqual(much_later["render"]["current"]["rerunName"], "朝雾")
        self.assertEqual(before["render"]["next"]["name"], "鎺")
        self.assertEqual(much_later["render"]["next"]["name"], "鎺")
        self.assertEqual(much_later["forecasts"][0]["period_index"], 1)
        self.assertNotEqual(before["generated_at"], much_later["generated_at"])
        self.assertEqual(before["forecasts"], much_later["forecasts"])

    def test_anchor_start_and_end_shift_independently_by_21_days(self):
        result = project_schedule(
            {"queue": QUEUE, "events": [], "anomalies": []},
            source_version="134351472190013976",
        )
        forecast = result["forecasts"][0]
        self.assertEqual(forecast["start_at"], "2026-11-03T10:00:00+08:00")
        self.assertEqual(forecast["end_at"], "2026-11-24T05:00:00+08:00")
        self.assertEqual(result["anchor"], DEFAULT_ANCHOR)
        self.assertEqual(result["render"]["next"]["name"], "鎺")

    def test_render_row_limit_does_not_truncate_authoritative_queue(self):
        queue = [
            {"character_id": index, "name": f"角色{index}", "debut_gacha_id": index}
            for index in range(1, 26)
        ]
        result = project_schedule(
            {"queue": queue, "events": [], "anomalies": []},
            source_version="v1",
            forecast_limit=12,
        )
        self.assertEqual(len(result["queue"]), 26)
        self.assertEqual(len(result["forecasts"]), 26)
        self.assertEqual(len(result["render"]["firstReruns"]), 12)

    def test_default_projection_leaves_classic_marker_dates_to_the_renderer(self):
        result = project_schedule(
            {"queue": QUEUE, "events": [], "anomalies": []},
            source_version="v1",
        )
        self.assertNotIn("classicPoolDates", result["render"])

    def test_version_snapshot_does_not_advance_after_anchor_calendar_dates(self):
        result = project_schedule(
            {"queue": QUEUE, "events": [], "anomalies": []},
            source_version="v1",
        )
        self.assertEqual(result["period_state"], "version_snapshot")
        self.assertEqual(result["render"]["current"]["newName"], "雾铃")
        self.assertEqual(result["render"]["current"]["rerunName"], "朝雾")
        self.assertEqual(result["render"]["current"]["startsAt"], "2026.10.13 10:00")
        self.assertEqual(result["render"]["next"]["startsAt"], "11.03 10:00")

    def test_exhausted_queue_does_not_change_confirmed_current_anchor(self):
        result = project_schedule(
            {"queue": [], "events": [], "anomalies": []},
            source_version="v1",
        )
        self.assertEqual(result["period_state"], "version_snapshot")
        self.assertEqual(result["render"]["current"]["newName"], "雾铃")
        self.assertEqual(result["render"]["next"]["name"], "雾铃")

    def test_schedule_snapshot_uses_immutable_png_and_hash_checked_pointer(self):
        with self.subTest("publish and read"):
            import tempfile

            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp) / "rerun_schedule"
                source = project_schedule(
                    {"queue": QUEUE, "events": [], "anomalies": []},
                    source_version="v1",
                )
                published = publish_schedule_snapshot(root, source, PNG)
                loaded, image_path = read_current_schedule(root)
                self.assertEqual(loaded["render_sha256"], hashlib.sha256(PNG).hexdigest())
                self.assertEqual(image_path.read_bytes(), PNG)
                self.assertEqual(published["snapshot_id"], loaded["snapshot_id"])

    def test_invalid_png_does_not_replace_the_current_snapshot(self):
        import tempfile

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "rerun_schedule"
            source = project_schedule(
                {"queue": QUEUE, "events": [], "anomalies": []},
                source_version="v1",
            )
            original = publish_schedule_snapshot(root, source, PNG)
            with self.assertRaisesRegex(ValueError, "PNG"):
                publish_schedule_snapshot(root, {**source, "source_version": "v2"}, b"bad")
            current, image_path = read_current_schedule(root)
            self.assertEqual(current["snapshot_id"], original["snapshot_id"])
            self.assertEqual(image_path.read_bytes(), PNG)
            self.assertEqual(json.loads((root / "current.json").read_text(encoding="utf-8"))["source_version"], "v1")

if __name__ == "__main__":
    unittest.main()
