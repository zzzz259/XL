"""Refresh behavior: local input only, view dedupe, safe failures and 05:00 day."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from server_app.rerun_refresh import RerunScheduleRefresher, refresh_day
from server_app.rerun_schedule import publish_schedule_snapshot, read_current_schedule

PNG = b'\x89PNG\r\n\x1a\nrefresh-test'


def now(value='2026-11-13T05:00:00+08:00'):
    return datetime.fromisoformat(value)


def projected(source='v1', name='司礼者'):
    return {'schema_version': 2, 'source_version': source, 'queue': [],
            'render': {'known': [], 'firstReruns': [{'name': name, 'date': '2026.11.24'}]}}


class RefreshTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'rerun_schedule'
        self.root.mkdir()
        self.calendar = self.root / 'calendar.json'
        self.calendar.write_text(json.dumps({'source_version': 'v1'}), encoding='utf-8')

    def refresher(self):
        return RerunScheduleRefresher(self.root)

    def test_refresh_day_changes_at_beijing_five_including_utc_input(self):
        self.assertEqual(refresh_day(now('2026-11-13T04:59:59+08:00')), '2026-11-12')
        self.assertEqual(refresh_day(now()), '2026-11-13')
        self.assertEqual(refresh_day(now('2026-11-12T21:00:00+00:00')), '2026-11-13')
        with self.assertRaises(ValueError):
            refresh_day(datetime(2026, 11, 13))

    def test_first_refresh_renders_once_next_day_identical_view_is_not_rendered(self):
        refresh = self.refresher()
        with patch('server_app.rerun_refresh.project_calendar', return_value=projected()), \
             patch('server_app.rerun_refresh.render_schedule_png', return_value=PNG) as renderer:
            self.assertTrue(refresh.is_due(now()))
            self.assertEqual(refresh.refresh(now())['status'], 'rendered')
            self.assertFalse(refresh.is_due(now()))
            old_manifest = (self.root / 'current.json').read_bytes()
            self.assertEqual(refresh.refresh(now() + timedelta(days=1))['status'], 'no_change')
            self.assertEqual(renderer.call_count, 1)
            self.assertEqual((self.root / 'current.json').read_bytes(), old_manifest)
            self.assertFalse(self.refresher().is_due(now() + timedelta(days=1)))

    def test_new_source_same_view_reuses_validated_png_but_updates_manifest(self):
        refresh = self.refresher()
        with patch('server_app.rerun_refresh.project_calendar', return_value=projected()), \
             patch('server_app.rerun_refresh.render_schedule_png', return_value=PNG):
            refresh.refresh(now())
        self.calendar.write_text('{"source_version":"v2"}', encoding='utf-8')
        with patch('server_app.rerun_refresh.project_calendar', return_value=projected('v2')), \
             patch('server_app.rerun_refresh.render_schedule_png') as renderer:
            self.assertEqual(refresh.refresh(now(), force=True)['status'], 'reused')
            renderer.assert_not_called()
        payload, image = read_current_schedule(self.root)
        self.assertEqual(payload['source_version'], 'v2')
        self.assertEqual(image.read_bytes(), PNG)

    def test_expired_view_change_redraws_once_without_deleting_calendar(self):
        refresh = self.refresher()
        with patch('server_app.rerun_refresh.project_calendar', return_value=projected()), \
             patch('server_app.rerun_refresh.render_schedule_png', return_value=PNG):
            refresh.refresh(now())
        before = self.calendar.read_bytes()
        with patch('server_app.rerun_refresh.project_calendar', return_value=projected(name='云罗')), \
             patch('server_app.rerun_refresh.render_schedule_png', return_value=PNG) as renderer:
            self.assertEqual(refresh.refresh(now() + timedelta(days=1))['status'], 'rendered')
            self.assertEqual(renderer.call_count, 1)
        self.assertEqual(self.calendar.read_bytes(), before)

    def test_failure_preserves_snapshot_and_retries_no_sooner_than_five_minutes(self):
        old = publish_schedule_snapshot(self.root, projected(), PNG)
        refresh = self.refresher()
        with patch('server_app.rerun_refresh.project_calendar', return_value=projected(name='云罗')), \
             patch('server_app.rerun_refresh.render_schedule_png', side_effect=RuntimeError('bad render')):
            self.assertEqual(refresh.refresh(now())['status'], 'failed')
        self.assertFalse(refresh.is_due(now() + timedelta(minutes=4, seconds=59)))
        self.assertTrue(self.refresher().is_due(now() + timedelta(minutes=5)))
        self.assertEqual(read_current_schedule(self.root)[0]['snapshot_id'], old['snapshot_id'])

    def test_bad_image_hash_cannot_be_reused_even_with_equal_render_model(self):
        refresh = self.refresher()
        with patch('server_app.rerun_refresh.project_calendar', return_value=projected()), \
             patch('server_app.rerun_refresh.render_schedule_png', return_value=PNG):
            refresh.refresh(now())
        payload, image = read_current_schedule(self.root)
        image.write_bytes(b'corrupt')
        with patch('server_app.rerun_refresh.project_calendar', return_value=projected()), \
             patch('server_app.rerun_refresh.render_schedule_png', return_value=PNG + b'fixed') as renderer:
            self.assertEqual(refresh.refresh(now(), force=True)['status'], 'rendered')
            renderer.assert_called_once()
        self.assertNotEqual(read_current_schedule(self.root)[0]['snapshot_id'], payload['snapshot_id'])

    def test_missing_calendar_is_a_noop_without_durable_state_or_game_events(self):
        self.calendar.unlink()
        self.assertEqual(self.refresher().refresh(now())['status'], 'deferred')
        self.assertEqual(list(self.root.iterdir()), [])

    def test_malformed_state_does_not_prevent_updater_startup(self):
        (self.root / 'refresh-state.json').write_text('[]', encoding='utf-8')
        self.assertTrue(self.refresher().is_due(now()))

    def test_legacy_manifest_without_render_model_is_replaced_from_calendar(self):
        publish_schedule_snapshot(self.root, {'schema_version': 1, 'source_version': 'v1'}, PNG)
        with patch('server_app.rerun_refresh.project_calendar', return_value=projected()), \
             patch('server_app.rerun_refresh.render_schedule_png', return_value=PNG) as renderer:
            self.assertEqual(self.refresher().refresh(now())['status'], 'rendered')
            renderer.assert_called_once()


if __name__ == '__main__':
    unittest.main()
