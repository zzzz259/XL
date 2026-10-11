import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from server_app.state import UpdateJobStore


class TestUpdateJobStore(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.store = UpdateJobStore(Path(self.temp_dir.name) / "state.sqlite")

    def test_job_lifecycle_persists_bounded_result_fields(self):
        now = datetime(2026, 10, 10, tzinfo=timezone.utc)
        self.store.create("job-1", "manual", now)
        self.store.mark_running("job-1", now)
        self.store.finish(
            "job-1",
            "succeeded",
            now,
            {
                "version_timestamp": 123,
                "processed": True,
                "character_count": 4,
                "error": None,
            },
        )

        job = self.store.get("job-1")
        self.assertEqual(job["status"], "succeeded")
        self.assertEqual(job["trigger_source"], "manual")
        self.assertEqual(job["result"]["version_timestamp"], 123)
        self.assertTrue(job["result"]["processed"])
        self.assertNotIn("token", job["result"])

    def test_unknown_job_returns_none(self):
        self.assertIsNone(self.store.get("missing"))
        self.assertIsNone(self.store.latest())

    def test_job_warnings_are_bounded_and_persisted_as_strings(self):
        now = datetime(2026, 10, 10, tzinfo=timezone.utc)
        self.store.create("job-warnings", "manual", now)
        self.store.finish(
            "job-warnings",
            "succeeded",
            now,
            {
                "warnings": ["schedule parse failed", "x" * 600, object()],
            },
        )

        warnings = self.store.get("job-warnings")["result"]["warnings"]
        self.assertEqual(len(warnings), 2)
        self.assertEqual(warnings[0], "schedule parse failed")
        self.assertEqual(len(warnings[1]), 500)

    def test_latest_job_returns_persisted_status_after_restart(self):
        now = datetime(2026, 10, 10, tzinfo=timezone.utc)
        self.store.create("scheduled-1", "scheduled", now)
        self.store.mark_running("scheduled-1", now)
        self.store.finish("scheduled-1", "succeeded", now, {"processed": True})

        self.assertEqual(self.store.latest()["job_id"], "scheduled-1")
        self.assertEqual(self.store.latest()["status"], "succeeded")

    def test_restart_marks_queued_and_running_jobs_interrupted(self):
        now = datetime(2026, 10, 10, tzinfo=timezone.utc)
        self.store.create("queued", "manual", now)
        self.store.create("running", "manual", now)
        self.store.mark_running("running", now)

        self.store.mark_incomplete_interrupted(now)

        self.assertEqual(self.store.get("queued")["status"], "interrupted")
        self.assertEqual(self.store.get("running")["status"], "interrupted")


if __name__ == "__main__":
    unittest.main()
