"""Projection and durable publication tests for rerun schedule snapshots."""

import hashlib
import json
import unittest
from datetime import datetime
from pathlib import Path

from server_app.rerun_schedule import (
    DEFAULT_ANCHOR,
    project_schedule,
    publish_schedule_snapshot,
    read_current_schedule,
    refresh_schedule_if_due,
)


QUEUE = [
    {"character_id": 10000214, "name": "朝雾", "debut_gacha_id": 24000060},
    {"character_id": 10000215, "name": "鎺", "debut_gacha_id": 24000062},
]
PNG = b"\x89PNG\r\n\x1a\nimage-bytes"


class RerunScheduleProjectionTests(unittest.TestCase):
    def test_anchor_start_and_end_shift_independently_by_21_days(self):
        result = project_schedule(
            {"queue": QUEUE, "events": [], "anomalies": []},
            source_version="134351472190013976",
            as_of=datetime.fromisoformat("2026-10-10T10:00:00+08:00"),
        )
        forecast = result["forecasts"][0]
        self.assertEqual(forecast["start_at"], "2026-10-13T10:00:00+08:00")
        self.assertEqual(forecast["end_at"], "2026-11-03T05:00:00+08:00")
        self.assertEqual(result["anchor"], DEFAULT_ANCHOR)
        self.assertEqual(result["render"]["next"]["name"], "朝雾")

    def test_render_row_limit_does_not_truncate_authoritative_queue(self):
        queue = [
            {"character_id": index, "name": f"角色{index}", "debut_gacha_id": index}
            for index in range(1, 26)
        ]
        result = project_schedule(
            {"queue": queue, "events": [], "anomalies": []},
            source_version="v1",
            as_of=datetime.fromisoformat("2026-10-10T10:00:00+08:00"),
            forecast_limit=12,
        )
        self.assertEqual(len(result["queue"]), 25)
        self.assertEqual(len(result["forecasts"]), 25)
        self.assertEqual(len(result["render"]["firstReruns"]), 12)

    def test_default_projection_leaves_classic_marker_dates_to_the_renderer(self):
        result = project_schedule(
            {"queue": QUEUE, "events": [], "anomalies": []},
            source_version="v1",
            as_of=datetime.fromisoformat("2026-10-10T10:00:00+08:00"),
        )
        self.assertNotIn("classicPoolDates", result["render"])

    def test_five_hour_gap_is_not_mislabeled_as_an_open_period(self):
        result = project_schedule(
            {"queue": QUEUE, "events": [], "anomalies": []},
            source_version="v1",
            as_of=datetime.fromisoformat("2026-10-13T05:00:00+08:00"),
        )
        self.assertEqual(result["period_state"], "between_periods")
        self.assertEqual(result["render"]["current"]["badge"], "上期已结束")
        self.assertEqual(result["render"]["next"]["startsAt"], "10.13 10:00")

    def test_first_cycle_boundary_is_open_through_0459_and_predicted_at_1000(self):
        expected = [
            ("2026-10-13T04:59:00+08:00", "confirmed_active"),
            ("2026-10-13T05:00:00+08:00", "between_periods"),
            ("2026-10-13T09:59:00+08:00", "between_periods"),
            ("2026-10-13T10:00:00+08:00", "predicted_period"),
        ]
        for instant, state in expected:
            with self.subTest(instant=instant):
                result = project_schedule(
                    {"queue": QUEUE, "events": [], "anomalies": []},
                    source_version="v1",
                    as_of=datetime.fromisoformat(instant),
                )
                self.assertEqual(result["period_state"], state)

    def test_later_gap_and_exhausted_queue_do_not_repeat_a_previous_forecast(self):
        gap = project_schedule(
            {"queue": QUEUE, "events": [], "anomalies": []},
            source_version="v1",
            as_of=datetime.fromisoformat("2026-11-03T05:00:00+08:00"),
        )
        active = project_schedule(
            {"queue": QUEUE, "events": [], "anomalies": []},
            source_version="v1",
            as_of=datetime.fromisoformat("2026-11-03T10:00:00+08:00"),
        )
        self.assertEqual(gap["period_state"], "between_periods")
        self.assertEqual(active["period_state"], "predicted_period")
        self.assertEqual(active["render"]["current"]["newName"], "鎺")
        self.assertEqual(active["render"]["next"]["name"], "暂无待预测角色")

    def test_schedule_snapshot_uses_immutable_png_and_hash_checked_pointer(self):
        with self.subTest("publish and read"):
            import tempfile

            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp) / "rerun_schedule"
                source = project_schedule(
                    {"queue": QUEUE, "events": [], "anomalies": []},
                    source_version="v1",
                    as_of=datetime.fromisoformat("2026-10-10T10:00:00+08:00"),
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
                as_of=datetime.fromisoformat("2026-10-10T10:00:00+08:00"),
            )
            original = publish_schedule_snapshot(root, source, PNG)
            with self.assertRaisesRegex(ValueError, "PNG"):
                publish_schedule_snapshot(root, {**source, "source_version": "v2"}, b"bad")
            current, image_path = read_current_schedule(root)
            self.assertEqual(current["snapshot_id"], original["snapshot_id"])
            self.assertEqual(image_path.read_bytes(), PNG)
            self.assertEqual(json.loads((root / "current.json").read_text(encoding="utf-8"))["source_version"], "v1")

    def test_refresh_reprojects_at_period_boundary_without_new_lua_data(self):
        from unittest.mock import patch
        import tempfile

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "rerun_schedule"
            initial = project_schedule(
                {"queue": QUEUE, "events": [], "anomalies": []},
                source_version="v1",
                as_of=datetime.fromisoformat("2026-10-12T10:00:00+08:00"),
            )
            publish_schedule_snapshot(root, initial, PNG)
            with patch("server_app.rerun_schedule.render_schedule_png", return_value=PNG) as renderer:
                changed = refresh_schedule_if_due(
                    root,
                    as_of=datetime.fromisoformat("2026-10-13T05:00:00+08:00"),
                    refresh_seconds=3600,
                    node_bin="node",
                )
            payload, _image = read_current_schedule(root)

        self.assertTrue(changed)
        self.assertEqual(payload["period_state"], "between_periods")
        renderer.assert_called_once()


if __name__ == "__main__":
    unittest.main()
