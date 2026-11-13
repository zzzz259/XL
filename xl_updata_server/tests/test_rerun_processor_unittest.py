"""Decoded Lua -> historical migration -> staged calendar/schedule boundary."""
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from server_app.processor import _attempt_rerun_schedule, process_rerun_schedule
from server_app.rerun_schedule import read_current_schedule

PNG = b'\x89PNG\r\n\x1a\nlocal-processor-test'
NOW = datetime.fromisoformat('2026-11-13T12:00:00+08:00')


class RerunScheduleProcessorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.lua = self.root / 'lua'
        self.lua.mkdir()
        self.stage = self.root / 'stage'
        self.config = SimpleNamespace(rerun_schedule_enabled=True,
                                      rerun_schedule_history_through_gacha_id=24000087,
                                      rerun_schedule_forecast_limit=20,
                                      data_dir=self.root / 'data', node_bin='node')

    def write_pools(self, pools, *, mixed_case=False):
        text = ', '.join(f'[{gid}]={{id={gid},type=2,sort=1,bottom_up=1,'
                         f'bottom_main_type=1,main_card_ids={{{cid}}}}}' for gid, cid in pools)
        (self.lua / ('BaseGacha.lua' if mixed_case else 'basegacha.lua')).write_text(
            f'BaseGacha = {{{text}}}', encoding='utf-8')
        (self.lua / ('BaseGachaBottomUp.lua' if mixed_case else 'basegachabottomup.lua')).write_text(
            'BaseGachaBottomUp = { [1] = { bottom_type=201 } }', encoding='utf-8')

    def process(self, characters=None):
        with patch('server_app.rerun_refresh.render_schedule_png', return_value=PNG):
            return process_rerun_schedule(self.lua, self.stage, 'v-current', characters or {},
                                         self.config, now=NOW)

    def test_full_history_consumed_once_latest_pools_replace_old_anchor(self):
        self.write_pools([(24000015,10000214),(24000016,10000215),(24000017,10000212),
                          (24000086,10000223),(24000087,10000212),(24000088,10000224),
                          (24000089,10000214),(24000090,10000227),(24000091,10000215)])
        names = {str(cid): {'name': name} for cid,name in [(10000214,'朝雾'),(10000215,'鎺'),
                 (10000212,'雪莉'),(10000223,'罗蕾娜'),(10000224,'雾铃'),(10000227,'菲欧')]}
        payload = self.process(names)
        known = payload['render']['known']
        self.assertEqual(len(known), 1)
        self.assertEqual([item['name'] for item in known[0]['pools']], ['菲欧','鎺'])
        self.assertEqual(known[0]['startsAt'], '2026.11.03 10:00')
        self.assertEqual(known[0]['endsAt'], '2026.11.24 05:00')
        self.assertEqual([item['character_id'] for item in payload['queue']], [10000223,10000224,10000227])
        self.assertEqual(max(event['gacha_id'] for event in payload['events']), 24000091)
        calendar = json.loads((self.stage / 'rerun_schedule/calendar.json').read_text(encoding='utf-8'))
        self.assertEqual(calendar['pools']['24000088']['cycle_start'], '2026-10-09')
        self.assertEqual(calendar['pools']['24000090']['cycle_start'], '2026-10-30')
        self.assertFalse((self.config.data_dir / 'rerun_schedule/calendar.json').exists())

    def test_single_archived_pool_has_png_without_inventing_old_dates(self):
        self.write_pools([(24000060,10000214)])
        payload = self.process({'10000214':{'name':'朝雾'}})
        self.assertEqual(payload['queue'][0]['character_id'], 10000214)
        self.assertEqual(payload['render']['known'], [])
        self.assertEqual(payload['forecasts'], [])
        published,image = read_current_schedule(self.stage / 'rerun_schedule')
        self.assertEqual(published['source_version'], 'v-current')
        self.assertEqual(image.read_bytes(), PNG)

    def test_mixed_case_lua_names_on_case_sensitive_filesystem(self):
        self.write_pools([(24000060,10000214)], mixed_case=True)
        self.assertEqual(self.process()['queue'][0]['character_id'], 10000214)

    def test_computed_word_lookup_does_not_block_name_resolution(self):
        self.write_pools([(24000060,10000214)])
        (self.lua / 'basecard.lua').write_text(
            'BaseCard = { [10000214] = { name=function() return T(90214) end } }', encoding='utf-8')
        (self.lua / 'baseword_cn.lua').write_text(
            'BaseWord_cn = { [90214] = { name="朝雾" } }\n'
            'function lookup(locale, word_id) return BaseWord_cn[locale .. word_id] end', encoding='utf-8')
        self.assertEqual(self.process()['queue'][0]['name'], '朝雾')

    def test_parse_failure_is_nonfatal_warning(self):
        with patch('server_app.processor.process_rerun_schedule', side_effect=ValueError('malformed Lua table key')):
            result,warnings = _attempt_rerun_schedule(self.lua,self.stage,'v2',{},self.config)
        self.assertIsNone(result)
        self.assertEqual(warnings, ('rerun_schedule: malformed Lua table key',))

    def test_render_failure_keeps_new_calendar_staged_for_retry(self):
        self.write_pools([(24000090,10000227)])
        with patch('server_app.rerun_refresh.render_schedule_png', side_effect=RuntimeError('render failed')):
            result,warnings = _attempt_rerun_schedule(self.lua,self.stage,'v2',{},self.config)
        self.assertIsNone(result)
        self.assertTrue(warnings)
        self.assertTrue((self.stage / 'rerun_schedule/calendar.json').is_file())
        self.assertTrue((self.stage / 'rerun_schedule/refresh-state.json').is_file())
        self.assertFalse((self.config.data_dir / 'rerun_schedule/calendar.json').exists())

    def test_first_start_is_used_even_when_retry_crosses_cycle_boundary(self):
        from server_app.processor import _version_observed_at
        (self.root / 'update_events.jsonl').write_text(
            '{"event":"start","version":123,"at":"2026-12-10T23:59:00+08:00"}\n'
            '{"event":"start","version":123,"at":"2026-12-11T01:00:00+08:00"}\n', encoding='utf-8')
        observed = _version_observed_at(self.root, '123', datetime.fromisoformat('2026-12-11T12:00:00+08:00'))
        self.assertEqual(observed.isoformat(), '2026-12-10T23:59:00+08:00')

    def test_missing_gacha_lua_is_nonfatal_skip(self):
        self.assertIsNone(process_rerun_schedule(self.lua,self.stage,'v1',{},self.config))
        self.assertFalse((self.stage / 'rerun_schedule').exists())

    def test_bootstrap_skips_malformed_id_without_losing_healthy_pool(self):
        from server_app.processor import _bootstrap_rerun_calendar
        pools = [{'id': None, 'type': 2}, {'id': 0, 'type': 2},
                 {'id': 24000060, 'type': 2, 'main_card_ids': [10000214]}]
        calendar = _bootstrap_rerun_calendar(pools, self.config)
        self.assertEqual(set(calendar['pools']), {'24000060'})

    def test_invalid_dated_seed_does_not_supply_normal_forecast_base(self):
        self.write_pools([(24000060,10000214),(24000090,0)])
        payload = self.process()
        self.assertEqual([item['character_id'] for item in payload['queue']], [10000214])
        self.assertEqual(payload['forecasts'], [])


if __name__ == '__main__':
    unittest.main()
