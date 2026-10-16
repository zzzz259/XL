from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from bot_app.config import load_config
from bot_app.proactive_outbox import ProactiveOutbox
from bot_app.status_monitor import (
    Status, StatusHistory, StatusMonitor, StatusSample, classify_sample,
)
from bot_app.status_monitor_runtime import build_status_monitor


class StatusClassificationTests(unittest.TestCase):
    def test_integer_one_on_accepted_login_confirms_maintenance(self):
        self.assertEqual(
            classify_sample(StatusSample(True, 0, True, 1, False)),
            Status.MAINTENANCE_CONFIRMED,
        )

    def test_unmarked_login_and_reachable_gateway_is_only_open_candidate(self):
        sample = StatusSample(True, 0, False, None, True)
        self.assertEqual(classify_sample(sample), Status.OPEN_CANDIDATE_GATEWAY_REACHABLE)
        self.assertEqual(
            classify_sample(StatusSample(True, 0, False, None, False)), Status.UNKNOWN
        )

    def test_null_and_wrong_type_flags_are_unknown(self):
        for value in (None, "1", True, 1.0, 2):
            with self.subTest(value=value):
                self.assertEqual(
                    classify_sample(StatusSample(True, 0, True, value, True)),
                    Status.UNKNOWN,
                )

    def test_rejected_login_is_unknown(self):
        self.assertEqual(
            classify_sample(StatusSample(False, 401, True, 1, True)), Status.UNKNOWN
        )


class StatusMonitorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path.cwd())
        root = Path(self.temp.name)
        self.history = StatusHistory(root / "server_status.sqlite3")
        self.outbox = ProactiveOutbox(root / "outbox")
        self.delays = []

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def test_first_valid_sample_sets_baseline_without_broadcast(self):
        monitor = StatusMonitor(
            lambda: StatusSample(True, 0, True, 1, False), self.history,
            self.outbox, ["debug"], sleeper=self._sleep,
        )
        self.assertFalse(await monitor.observe_once())
        self.assertEqual(monitor.confirmed_state, Status.MAINTENANCE_CONFIRMED)
        self.assertEqual(self.outbox.count(), 0)

    async def test_matching_candidate_after_three_seconds_enqueues_per_group_once(self):
        samples = iter([
            StatusSample(True, 0, False, None, True, observed_at=100.0),
            StatusSample(True, 0, False, None, True, observed_at=103.0),
            StatusSample(True, 0, False, None, True, observed_at=130.0),
        ])
        monitor = StatusMonitor(
            lambda: next(samples), self.history, self.outbox,
            ["debug", "test"], sleeper=self._sleep,
        )
        monitor.set_confirmed_state(Status.MAINTENANCE_CONFIRMED)
        self.assertTrue(await monitor.observe_once())
        self.assertEqual(self.delays, [3.0])
        self.assertEqual(self.outbox.count(), 2)
        self.assertEqual({row.recipient for row in self.outbox.list_pending()}, {"debug", "test"})
        self.assertFalse(await monitor.observe_once())
        self.assertEqual(self.outbox.count(), 2)

    async def test_unknown_confirmation_does_not_transition(self):
        samples = iter([
            StatusSample(True, 0, False, None, True),
            StatusSample(False, 401, False, None, False),
        ])
        monitor = StatusMonitor(
            lambda: next(samples), self.history, self.outbox,
            ["debug"], sleeper=self._sleep,
        )
        monitor.set_confirmed_state(Status.MAINTENANCE_CONFIRMED)
        self.assertFalse(await monitor.observe_once())
        self.assertEqual(monitor.confirmed_state, Status.MAINTENANCE_CONFIRMED)
        self.assertEqual(self.outbox.count(), 0)

    async def test_inconsistent_confirmation_does_not_transition(self):
        samples = iter([
            StatusSample(True, 0, False, None, True),
            StatusSample(True, 0, True, 1, True),
        ])
        monitor = StatusMonitor(
            lambda: next(samples), self.history, self.outbox,
            ["debug"], sleeper=self._sleep,
        )
        monitor.set_confirmed_state(Status.MAINTENANCE_CONFIRMED)
        self.assertFalse(await monitor.observe_once())
        self.assertEqual(self.outbox.count(), 0)

    async def test_probe_exception_is_sanitized_in_history(self):
        def probe():
            raise RuntimeError("token=private-value")

        monitor = StatusMonitor(probe, self.history, self.outbox, ["debug"], sleeper=self._sleep)
        self.assertFalse(await monitor.observe_once())
        with self.history._connect() as connection:
            row = connection.execute("SELECT status, error_class FROM samples").fetchone()
        self.assertEqual(tuple(row), (Status.UNKNOWN.value, "PROBE_ERROR"))
        self.assertEqual(self.outbox.count(), 0)

    async def test_malformed_sample_fields_are_unknown_and_safely_recorded(self):
        malformed = StatusSample("yes", 0, False, None, True)
        monitor = StatusMonitor(
            lambda: malformed, self.history, self.outbox, ["debug"], sleeper=self._sleep
        )

        self.assertFalse(await monitor.observe_once())
        self.assertEqual(self.history.sample_count(), 1)
        self.assertEqual(self.outbox.count(), 0)
        with self.history._connect() as connection:
            row = connection.execute("SELECT status, accepted FROM samples").fetchone()
        self.assertEqual(tuple(row), (Status.UNKNOWN.value, 0))

    async def test_slow_sampling_skips_missed_tick_before_next_probe(self):
        starts = []
        stop_event = asyncio.Event()

        async def probe():
            starts.append(asyncio.get_running_loop().time())
            if len(starts) == 1:
                await asyncio.sleep(0.2)
            else:
                stop_event.set()
            return StatusSample(True, 0, False, None, True)

        monitor = StatusMonitor(
            probe, self.history, self.outbox, ["debug"], interval_seconds=0.15
        )
        await monitor.run(stop_event)

        self.assertEqual(len(starts), 2)
        self.assertGreaterEqual(starts[1] - starts[0], 0.34)

    async def test_transition_uses_test_notification_service_when_configured(self):
        class RecordingNotificationClient:
            def __init__(self):
                self.calls = []

            async def enqueue(self, **request):
                self.calls.append(request)
                return []

        samples = iter([
            StatusSample(True, 0, False, None, True, observed_at=100.0),
            StatusSample(True, 0, False, None, True, observed_at=103.0),
        ])
        notification_client = RecordingNotificationClient()
        monitor = StatusMonitor(
            lambda: next(samples), self.history, None,
            ["debug", "test"], sleeper=self._sleep,
            notification_client=notification_client,
        )
        monitor.set_confirmed_state(Status.MAINTENANCE_CONFIRMED)

        self.assertTrue(await monitor.observe_once())

        self.assertEqual(len(notification_client.calls), 1)
        request = notification_client.calls[0]
        self.assertEqual(request["instance_id"], "default")
        self.assertEqual(
            [item["recipient"] for item in request["messages"]], ["debug", "test"]
        )
        self.assertEqual(request["event_key"], "game-status:MAINTENANCE_CONFIRMED:OPEN_CANDIDATE_GATEWAY_REACHABLE:100000")

    async def _sleep(self, seconds):
        self.delays.append(seconds)


class StatusMonitorRuntimeTests(unittest.TestCase):
    def _write_config(self, root: Path, *, status_section: str = "", groups: str = ""):
        data_dir = (root / "data").as_posix()
        config_path = root / "config.toml"
        config_path.write_text(
            '[bot]\nappid="app"\nsecret="secret"\n'
            '[watch]\noutbox_dir="outbox"\ndata_dir="' + data_dir + '"\n'
            + groups + status_section,
            encoding="utf-8",
        )
        return load_config(str(config_path))

    def test_monitor_config_defaults_off_with_30_second_and_3_second_intervals(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            config = self._write_config(Path(directory))
            self.assertFalse(config.server_status.enabled)
            self.assertEqual(config.server_status.interval_seconds, 30)
            self.assertEqual(config.server_status.confirm_delay_seconds, 3)

    def test_legacy_recipient_tiers_do_not_gate_and_invalid_intervals_are_rejected(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            root = Path(directory)
            config = self._write_config(
                root, status_section='[server_status]\nrecipient_tiers=["production"]\n'
            )
            self.assertEqual(config.server_status.recipient_tiers, ("production",))
            with self.assertRaisesRegex(ValueError, "interval_seconds"):
                self._write_config(
                    root, status_section='[server_status]\ninterval_seconds=0\n'
                )

    def test_enabled_monitor_requires_credentials_file(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, "credentials_file"):
                self._write_config(root, status_section='[server_status]\nenabled=true\n')

    def test_enabled_monitor_loads_credentials_file_setting(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            root = Path(directory)
            config = self._write_config(
                root,
                status_section='[server_status]\nenabled=true\ncredentials_file="probe.toml"\n',
            )
            self.assertEqual(config.server_status.credentials_file, "probe.toml")

    def test_disabled_monitor_never_calls_provider_factory(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            root = Path(directory)
            config = self._write_config(root)
            outbox = ProactiveOutbox(root / "outbox")
            monitor = build_status_monitor(
                config, outbox,
                lambda _config: self.fail("disabled monitor must not create a probe"),
            )
            self.assertIsNone(monitor)

    def test_enabled_monitor_without_provider_fails_instead_of_silently_disabling(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            root = Path(directory)
            config = self._write_config(
                root,
                status_section=(
                    '[server_status]\nenabled=true\ncredentials_file="probe.toml"\n'
                ),
            )
            with self.assertRaisesRegex(RuntimeError, "probe provider"):
                build_status_monitor(config, ProactiveOutbox(root / "outbox"), None)

    def test_enabled_monitor_uses_separate_history_and_debug_test_groups(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            root = Path(directory)
            config = self._write_config(
                root,
                groups='[groups]\ndebug=["debug-group", "shared"]\ntest=["test-group", "shared"]\n',
                status_section=(
                    '[server_status]\nenabled=true\ncredentials_file="probe.toml"\n'
                    'recipient_tiers=["debug", "test"]\n'
                ),
            )
            outbox = ProactiveOutbox(root / "outbox")
            monitor = build_status_monitor(
                config, outbox,
                lambda _config: lambda: StatusSample(True, 0, True, 1, False),
            )
            self.assertIsNotNone(monitor)
            self.assertEqual(monitor.recipients, ("debug-group", "shared", "test-group"))
            self.assertEqual(monitor.history.path, root / "data" / "server_status.sqlite3")


if __name__ == "__main__":
    unittest.main()
