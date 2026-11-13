"""Local refresh shares the update/drain boundary without creating game jobs."""
import json
import tempfile
import threading
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from server_app.daemon import UpdateDaemon
from server_app.state import StateStore
from server_app.update_coordinator import UpdateCoordinator


class LocalRefresher:
    def __init__(self):
        self.calls = 0
        self.started = threading.Event()
        self.release = threading.Event()
        self.release.set()

    def is_due(self, _now):
        return self.calls == 0

    def refresh(self, _now):
        self.calls += 1
        self.started.set()
        self.release.wait(timeout=3)
        return {'status': 'rendered'}


class CoordinatorRefreshTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = StateStore(Path(self.temp.name) / 'state.sqlite')
        self.now = datetime(2026, 11, 13, 5, tzinfo=UTC)
        self.config = SimpleNamespace(environment='test', instance_id='test', cdn_poll_enabled=False,
                                      normal_interval_seconds=3600, burst_interval_seconds=60,
                                      burst_duration_seconds=1200, burst_anchor=self.now,
                                      rerun_schedule_enabled=True)
        self.refresher = LocalRefresher()
        self.coordinator = UpdateCoordinator(self.config, None, self.store, clock=lambda: self.now,
                                             rerun_refresher=self.refresher)

    def test_refresh_runs_with_cdn_poll_disabled_without_game_job_or_schedule_check(self):
        self.assertEqual(self.coordinator.run_rerun_refresh_if_due(self.now)['status'], 'rendered')
        self.assertIsNone(self.coordinator.run_rerun_refresh_if_due(self.now))
        self.assertIsNone(self.store.load().last_check)
        self.assertIsNone(self.coordinator.status()['last_run'])
        self.assertIsNone(self.coordinator.job_store.latest())

    def test_busy_update_defers_without_consuming_daily_refresh(self):
        self.coordinator._run_lock.acquire()
        try:
            self.assertEqual(self.coordinator.run_rerun_refresh_if_due(self.now)['status'], 'deferred')
            self.assertEqual(self.refresher.calls, 0)
        finally:
            self.coordinator._run_lock.release()
        self.assertEqual(self.coordinator.run_rerun_refresh_if_due(self.now)['status'], 'rendered')

    def test_stop_waits_for_refresh_and_prevents_new_work(self):
        self.refresher.release.clear()
        thread = threading.Thread(target=self.coordinator.run_rerun_refresh_if_due, args=(self.now,))
        thread.start()
        self.assertTrue(self.refresher.started.wait(timeout=1))
        self.assertEqual(self.coordinator.status()['active_source'], 'rerun_refresh')
        self.coordinator.stop_accepting()
        self.assertFalse(self.coordinator.wait_idle(timeout=0.01))
        self.refresher.release.set()
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertTrue(self.coordinator.wait_idle(timeout=1))
        self.assertIsNone(self.coordinator.enqueue_manual())

    def test_daemon_calls_local_refresh_in_its_existing_loop(self):
        stop = threading.Event()
        UpdateDaemon(self.config, None, self.store, clock=lambda: self.now,
                     sleeper=lambda _seconds: stop.set(), coordinator=self.coordinator).run(stop)
        self.assertEqual(self.refresher.calls, 1)

    def test_existing_version_bootstrap_runs_with_cdn_disabled_without_game_jobs(self):
        root = self.store.path.parent
        pointer = root / 'current_version.json'
        pointer.write_text('{"version":123}',encoding='utf-8')
        schedule = root / 'rerun_schedule'
        schedule.mkdir()
        (schedule / 'bootstrap-input.json').write_text(json.dumps({
            'source_version':'123','bottomups':{'1':{'bottom_type':201}},'names':{'10000227':'菲欧'},
            'pools':[{'id':24000090,'type':2,'bottom_main_type':1,'bottom_up':1,'main_card_ids':[10000227]}]}),encoding='utf-8')
        pipeline = Mock()
        pipeline.run_once.side_effect = AssertionError('bootstrap must not run a game update')
        coordinator = UpdateCoordinator(self.config,pipeline,self.store,clock=lambda:self.now)
        with patch('server_app.rerun_refresh.render_schedule_png',return_value=b'\x89PNG\r\n\x1a\ntest'):
            result = coordinator.run_rerun_refresh_if_due(self.now)
        self.assertEqual(result['status'],'rendered')
        pipeline.run_once.assert_not_called()
        self.assertIsNone(coordinator.job_store.latest())
        self.assertIsNone(self.store.load().last_check)
        self.assertEqual(pointer.read_text(),'{"version":123}')
        self.assertTrue((schedule/'calendar.json').is_file())


if __name__ == '__main__':
    unittest.main()
