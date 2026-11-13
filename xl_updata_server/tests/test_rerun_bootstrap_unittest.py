"""Upgrade existing current data without a CDN update or immutable-version rewrite."""
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from server_app.rerun_refresh import RerunScheduleRefresher
from server_app.rerun_schedule import publish_schedule_snapshot, read_current_schedule

NOW = datetime.fromisoformat('2026-11-13T12:00:00+08:00')
PNG = b'\x89PNG\r\n\x1a\nbootstrap'


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)
        self.schedule = self.data / 'rerun_schedule'
        self.schedule.mkdir()
        self.pointer = self.data / 'current_version.json'
        self.pointer.write_text('{"version":123}', encoding='utf-8')
        self.inputs = {'source_version':'123', 'bottomups':{'1':{'bottom_type':201}},
                       'names':{'10000227':'菲欧','10000174':'司礼者'},
                       'pools':[{'id':24000060,'type':2,'bottom_main_type':1,'bottom_up':1,'main_card_ids':[10000174]},
                                {'id':24000090,'type':2,'bottom_main_type':1,'bottom_up':1,'main_card_ids':[10000227]}]}
        self.input_file = self.schedule / 'bootstrap-input.json'
        self.write_input()

    def write_input(self):
        self.input_file.write_text(json.dumps(self.inputs,ensure_ascii=False),encoding='utf-8')

    def test_existing_current_version_is_imported_without_game_update_or_version_rewrite(self):
        with patch('server_app.rerun_refresh.render_schedule_png',return_value=PNG):
            result = RerunScheduleRefresher(self.schedule).refresh(NOW)
        self.assertEqual(result['status'], 'rendered')
        self.assertEqual(result['payload']['render']['known'][0]['pools'][0]['name'], '菲欧')
        self.assertEqual(result['payload']['render']['next']['name'], '司礼者')
        self.assertEqual(self.pointer.read_text(), '{"version":123}')
        self.assertFalse((self.data / 'versions').exists())
        self.assertFalse((self.data / 'update_events.jsonl').exists())

    def test_source_mismatch_retains_old_snapshot_and_does_not_create_calendar(self):
        old = publish_schedule_snapshot(self.schedule,{'source_version':'123','render':{}},PNG)
        self.inputs['source_version']='124'
        self.write_input()
        self.assertEqual(RerunScheduleRefresher(self.schedule).refresh(NOW)['status'],'failed')
        self.assertFalse((self.schedule / 'calendar.json').exists())
        self.assertEqual(read_current_schedule(self.schedule)[0]['snapshot_id'],old['snapshot_id'])

    def test_existing_calendar_ignores_later_bootstrap_input_edits(self):
        refresh = RerunScheduleRefresher(self.schedule)
        with patch('server_app.rerun_refresh.render_schedule_png',return_value=PNG):
            self.assertEqual(refresh.refresh(NOW)['status'],'rendered')
        before = (self.schedule / 'calendar.json').read_bytes()
        self.inputs['pools']=[]
        self.write_input()
        self.assertEqual(refresh.refresh(NOW,force=True)['status'],'no_change')
        self.assertEqual((self.schedule / 'calendar.json').read_bytes(),before)

    def test_unmapped_import_does_not_assign_current_date_to_unknown_pool(self):
        self.inputs['pools'].append({'id':24000092,'type':2,'bottom_main_type':1,
                                    'bottom_up':1,'main_card_ids':[10000300]})
        self.write_input()
        with patch('server_app.rerun_refresh.render_schedule_png',return_value=PNG):
            result = RerunScheduleRefresher(self.schedule).refresh(NOW)
        self.assertEqual(result['status'],'rendered')
        calendar = json.loads((self.schedule / 'calendar.json').read_text(encoding='utf-8'))
        self.assertNotIn('24000092',calendar['pools'])
        self.assertIn('historical_cycle_unknown',[item['reason'] for item in calendar['diagnostics']])

    def test_actual_first_event_supplies_cycle_for_other_imported_pools(self):
        self.inputs['pools'].append({'id':24000092,'type':2,'bottom_main_type':1,
                                    'bottom_up':1,'main_card_ids':[10000300]})
        self.write_input()
        (self.data / 'update_events.jsonl').write_text(
            '{"event":"start","version":123,"at":"2026-11-20T00:00:00+08:00"}\n',encoding='utf-8')
        with patch('server_app.rerun_refresh.render_schedule_png',return_value=PNG):
            result = RerunScheduleRefresher(self.schedule).refresh(NOW)
        self.assertEqual(result['status'],'rendered')
        calendar = json.loads((self.schedule / 'calendar.json').read_text(encoding='utf-8'))
        self.assertEqual(calendar['pools']['24000092']['cycle_start'],'2026-11-20')


if __name__=='__main__':
    unittest.main()
