import threading
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from server_app.daemon import UpdateDaemon
from server_app.pipeline import ProcessResult
from server_app.state import ScheduleState


class CountingPipeline:
    def __init__(self):
        self.calls = 0

    def run_once(self):
        self.calls += 1
        return ProcessResult(version_timestamp=123, processed=False)


class MemoryStateStore:
    def __init__(self):
        self.state = ScheduleState()
        self.saved = []

    def load(self):
        return self.state

    def save(self, state):
        self.state = state
        self.saved.append(state)


class TestDaemonEnvironment(unittest.TestCase):
    def run_one_iteration(self, environment, poll_enabled):
        temp_dir = tempfile.TemporaryDirectory()
        event = threading.Event()
        pipeline = CountingPipeline()
        state = MemoryStateStore()
        state.path = Path(temp_dir.name) / "state.sqlite"
        config = type("Config", (), {
            "environment": environment,
            "cdn_poll_enabled": poll_enabled,
            "normal_interval_seconds": 3600,
            "burst_interval_seconds": 60,
            "burst_duration_seconds": 1200,
            "burst_anchor": datetime(2026, 1, 1, tzinfo=timezone.utc),
        })()

        def stop_after_iteration(_seconds):
            event.set()

        daemon = UpdateDaemon(
            config,
            pipeline,
            state,
            clock=lambda: datetime(2026, 10, 10, tzinfo=timezone.utc),
            sleeper=stop_after_iteration,
        )
        with patch("server_app.daemon.time.monotonic", return_value=3601):
            daemon.run(event)
        temp_dir.cleanup()
        return pipeline.calls

    def test_test_environment_never_scheduled_cdn_polls(self):
        calls = self.run_one_iteration("test", poll_enabled=False)

        self.assertEqual(calls, 0)

    def test_main_environment_runs_scheduled_cdn_poll_when_enabled(self):
        calls = self.run_one_iteration("main", poll_enabled=True)

        self.assertEqual(calls, 1)

    def test_one_daemon_iteration_does_not_trigger_schedule_reprojection(self):
        calls = self.run_one_iteration("test", poll_enabled=False)

        self.assertEqual(calls, 0)


if __name__ == "__main__":
    unittest.main()
