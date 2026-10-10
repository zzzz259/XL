"""Atomic version publication and last-known-good schedule retention tests."""

import json
import tempfile
import unittest
from pathlib import Path

from server_app.pipeline import ProcessResult, UpdatePipeline
from server_app.rerun_schedule import publish_schedule_snapshot


PNG = b"\x89PNG\r\n\x1a\nversioned schedule"
PAYLOAD = {
    "schema_version": 1,
    "source_version": "123",
    "timezone": "Asia/Shanghai",
    "cycle_days": 21,
    "anchor": {},
    "queue": [],
    "forecasts": [],
    "events": [],
    "anomalies": [],
    "generated_at": "2026-10-10T10:00:00+08:00",
    "render": {},
}


class RerunPipelineTests(unittest.TestCase):
    def test_schedule_is_in_version_before_pointer_and_mirrored_to_stable_location(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory) / "data"

            def processor(staging):
                publish_schedule_snapshot(staging / "rerun_schedule", PAYLOAD, PNG)
                return ProcessResult(version_timestamp=123, processed=True)

            result = UpdatePipeline(data, processor).run_once()

            self.assertTrue(result.processed)
            version_json = data / "versions" / "123" / "rerun_schedule" / "current.json"
            root_json = data / "rerun_schedule" / "current.json"
            self.assertTrue(version_json.is_file())
            self.assertTrue(root_json.is_file())
            self.assertTrue((data / "rerun_schedule" / "current.png").is_file())
            self.assertEqual(json.loads(root_json.read_text(encoding="utf-8"))["source_version"], "123")
            self.assertEqual(json.loads((data / "current_version.json").read_text())["version"], 123)

    def test_missing_new_schedule_carries_last_good_snapshot_to_new_version(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory) / "data"
            old_schedule = data / "versions" / "122" / "rerun_schedule"
            publish_schedule_snapshot(old_schedule, {**PAYLOAD, "source_version": "122"}, PNG)
            data.mkdir(parents=True, exist_ok=True)
            (data / "current_version.json").write_text('{"version":122}', encoding="utf-8")

            result = UpdatePipeline(
                data,
                lambda _stage: ProcessResult(version_timestamp=123, processed=True),
            ).run_once()

            self.assertTrue(result.processed)
            carried = data / "versions" / "123" / "rerun_schedule" / "current.json"
            self.assertTrue(carried.is_file())
            self.assertEqual(json.loads(carried.read_text(encoding="utf-8"))["source_version"], "122")


if __name__ == "__main__":
    unittest.main()
