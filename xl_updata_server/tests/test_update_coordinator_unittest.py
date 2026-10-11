import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from server_app.pipeline import ProcessResult
from server_app.state import StateStore
from server_app.update_coordinator import UpdateCoordinator


class BlockingPipeline:
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls = 0

    def run_once(self):
        self.calls += 1
        self.started.set()
        self.release.wait(timeout=2)
        return ProcessResult(version_timestamp=555, processed=True, character_count=7)


class ImmediatePipeline:
    def __init__(self):
        self.calls = 0

    def run_once(self):
        self.calls += 1
        return ProcessResult(version_timestamp=555, processed=True)


class WarningPipeline:
    def run_once(self):
        return ProcessResult(
            version_timestamp=555,
            processed=True,
            warnings=("rerun_schedule: malformed Lua table key",),
        )


def make_config(environment="main", polling=True):
    return SimpleNamespace(
        environment=environment,
        cdn_poll_enabled=polling and environment == "main",
        normal_interval_seconds=3600,
        burst_interval_seconds=60,
        burst_duration_seconds=1200,
        burst_anchor=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


class TestUpdateCoordinator(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.database = Path(self.temp_dir.name) / "state.sqlite"
        self.now = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)

    def test_manual_and_scheduled_runs_share_single_flight_and_persist_state(self):
        pipeline = BlockingPipeline()
        state_store = StateStore(self.database)
        coordinator = UpdateCoordinator(
            make_config(), pipeline, state_store, clock=lambda: self.now
        )
        job_id = coordinator.enqueue_manual()
        self.assertIsNotNone(job_id)
        self.assertTrue(pipeline.started.wait(timeout=1))

        scheduled = coordinator.run_scheduled_if_due(self.now)
        duplicate = coordinator.enqueue_manual()
        self.assertIsNone(scheduled)
        self.assertIsNone(duplicate)
        self.assertEqual(pipeline.calls, 1)
        self.assertEqual(coordinator.status()["active_job_id"], job_id)

        pipeline.release.set()
        self.assertTrue(coordinator.wait_idle(timeout=2))
        self.assertEqual(coordinator.get_job(job_id)["status"], "succeeded")
        self.assertEqual(state_store.load().last_version_timestamp, 555)
        self.assertEqual(state_store.load().last_check, self.now)

    def test_test_environment_manual_run_works_but_scheduled_run_is_suppressed(self):
        pipeline = ImmediatePipeline()
        coordinator = UpdateCoordinator(
            make_config("test", polling=False),
            pipeline,
            StateStore(self.database),
            clock=lambda: self.now,
        )

        self.assertIsNone(coordinator.run_scheduled_if_due(self.now))
        self.assertEqual(pipeline.calls, 0)
        job_id = coordinator.enqueue_manual()
        self.assertIsNotNone(job_id)
        self.assertTrue(coordinator.wait_idle(timeout=2))

        self.assertEqual(pipeline.calls, 1)
        self.assertEqual(coordinator.get_job(job_id)["status"], "succeeded")

    def test_stopping_coordinator_rejects_new_manual_runs(self):
        coordinator = UpdateCoordinator(
            make_config("test", polling=False),
            ImmediatePipeline(),
            StateStore(self.database),
            clock=lambda: self.now,
        )
        coordinator.stop_accepting()

        self.assertFalse(coordinator.status()["accepting"])
        self.assertIsNone(coordinator.enqueue_manual())

    def test_schedule_warning_is_visible_in_persisted_successful_job(self):
        coordinator = UpdateCoordinator(
            make_config("test", polling=False),
            WarningPipeline(),
            StateStore(self.database),
            clock=lambda: self.now,
        )

        job_id = coordinator.enqueue_manual()
        self.assertIsNotNone(job_id)
        self.assertTrue(coordinator.wait_idle(timeout=2))

        job = coordinator.get_job(job_id)
        self.assertEqual(job["status"], "succeeded")
        self.assertEqual(
            job["result"]["warnings"],
            ["rerun_schedule: malformed Lua table key"],
        )

    def test_scheduled_run_is_persisted_for_status_after_restart(self):
        coordinator = UpdateCoordinator(
            make_config(), ImmediatePipeline(), StateStore(self.database), clock=lambda: self.now
        )

        result = coordinator.run_scheduled_if_due(self.now)

        self.assertTrue(result.processed)
        latest = coordinator.job_store.latest()
        self.assertEqual(latest["trigger_source"], "scheduled")
        self.assertEqual(latest["status"], "succeeded")
        restarted = UpdateCoordinator(
            make_config(), ImmediatePipeline(), StateStore(self.database), clock=lambda: self.now
        )
        self.assertEqual(restarted.status()["last_run"]["job_id"], latest["job_id"])


if __name__ == "__main__":
    unittest.main()
