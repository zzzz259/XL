"""Normalized per-season leaderboard history with full membership and battle identity."""
import hashlib
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

import leaderboard_seasons as seasons


def entry(entity='9007199254740995', rank=1, score=100, **changes):
    value = {'entity_id': entity, 'rank': rank, 'score': score,
             'display_name': 'same name', 'guild_name': 'guild',
             'guild_uid': '9007199254740997', 'level': 80,
             'raw_data': {'round': 3}}
    value.update(changes)
    return value


def member(uid='1001', **changes):
    value = {'member_uid': uid, 'display_name': 'member-' + uid, 'score': 10,
             'level': 60, 'raw_data': {'faceId': 7}}
    value.update(changes)
    return value


def battle(uid='b-0001', round_no=1, raw=b'battle-pb-1', **changes):
    value = {'battle_uid': uid, 'round': round_no,
             'formation': {'boss_name': '强袭者', 'boss_level': 30, 'frontline': 3},
             'lineup': [{'member_uid': '1001', 'slot': 0, 'power': 999,
                         'raw_data': {'hero': 1}}],
             'presence': [{'member_uid': '1001', 'status': 'PRESENT'},
                          {'member_uid': '1002', 'status': 'ABSENT'}],
             'raw_payload': raw}
    value.update(changes)
    return value


def page(index=0, **changes):
    value = {'entries': [entry(rank=1 + index)],
             'membership': [member('1001'), member('1002')],
             'battles': [battle()],
             'evidence': {'request_index': index, 'echoed_index': index,
                          'reported_total': 2, 'entry_count': 1, 'member_count': 2,
                          'source_rpc': 2307},
             'raw_payloads': [f'page-pb-{index}'.encode()]}
    value.update(changes)
    return value


def save_round(store, run_id, scope='test:server1', mode='guild_war', season_id='42',
               trigger='ROUTINE', coverage='COMPLETE_REPORTED_RANGE',
               settle='LAST_OBSERVED', availability='ACTIVE',
               at='2026-10-20T00:00:00+00:00', pages=None, metadata=None):
    return store.save_run(run_id, scope=scope, mode=mode, season_id=season_id,
                          trigger=trigger, code_version='revision', coverage=coverage,
                          settle_evidence=settle, availability=availability,
                          collected_at_utc=at,
                          pages=[page()] if pages is None else pages,
                          metadata={'phase': 2} if metadata is None else metadata)


class SeasonHistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / 'data')
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'seasonal_leaderboards.sqlite3'
        self.store = seasons.SeasonHistory(self.path)

    def table_names(self):
        with closing(sqlite3.connect(self.path)) as db:
            return {row[0] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}

    def test_schema_tables_roundtrip_and_persistent_reopen(self):
        saved = save_round(self.store, 'run1')
        self.assertEqual(self.table_names(), seasons.TABLE_NAMES)
        with closing(sqlite3.connect(self.path)) as db, db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 1)
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])
        reopened = seasons.SeasonHistory(self.path)
        data = reopened.export_snapshot(saved['snapshot_id'])
        self.assertEqual(data['season_id'], '42')
        self.assertEqual(data['entries'][0]['entity_id'], '9007199254740995')
        self.assertEqual(data['entries'][0]['score'], 100)
        self.assertEqual(data['membership'][1]['member_uid'], '1002')
        self.assertEqual(data['battles'][0]['battle_uid'], 'b-0001')
        self.assertEqual(data['battles'][0]['formation']['boss_level'], 30)
        self.assertEqual(data['battles'][0]['lineup'][0]['power'], 999)
        self.assertEqual(data['battles'][0]['presence'][1]['status'], 'ABSENT')
        self.assertEqual(data['page_evidence'][0]['request_index'], 0)
        self.assertEqual(data['trigger'], 'ROUTINE')
        self.assertEqual(json.loads(json.dumps(data))['membership'], data['membership'])

    def test_full_membership_is_saved_for_every_snapshot(self):
        save_round(self.store, 'r1', pages=[page(membership=[member('1001'), member('1002')])])
        save_round(self.store, 'r2', pages=[page(membership=[member('1002'), member('1003')])])
        found = [row['snapshot_id'] for row in self.store.snapshots()]
        first = {row['member_uid'] for row in self.store.export_snapshot(found[0])['membership']}
        second = {row['member_uid'] for row in self.store.export_snapshot(found[1])['membership']}
        self.assertEqual(first, {'1001', '1002'})
        self.assertEqual(second, {'1002', '1003'})
        self.assertNotEqual(first, second)

    def test_raw_payloads_deduplicated_by_sha256_across_runs(self):
        save_round(self.store, 'r1', pages=[page(battles=[], raw_payloads=[b'same-pb', b'one'])])
        save_round(self.store, 'r2', pages=[page(battles=[], raw_payloads=[b'same-pb', b'two'])])
        with closing(sqlite3.connect(self.path)) as db, db:
            blobs = db.execute('SELECT sha256, byte_length FROM raw_blobs ORDER BY sha256').fetchall()
            links = db.execute('SELECT count(*) FROM snapshot_blobs').fetchone()[0]
        self.assertEqual(len(blobs), 3)
        self.assertEqual(links, 4)
        self.assertEqual({row[1] for row in blobs}, {len(b'one'), len(b'same-pb')})

    def test_battle_content_deduplicated_by_battle_uid_across_snapshots(self):
        first = battle(formation={'boss_name': 'first', 'boss_level': 30, 'frontline': 3})
        second = battle(formation={'boss_name': 'changed', 'boss_level': 31, 'frontline': 4},
                        raw=b'battle-pb-2')
        save_round(self.store, 'r1', pages=[page(battles=[first])])
        save_round(self.store, 'r2', pages=[page(battles=[second])])
        with closing(sqlite3.connect(self.path)) as db, db:
            count = db.execute('SELECT count(*) FROM battles').fetchone()[0]
            links = db.execute('SELECT count(*) FROM snapshot_battles').fetchone()[0]
            stored = json.loads(db.execute('SELECT payload FROM guild_formations').fetchone()[0])
        self.assertEqual(count, 1)
        self.assertEqual(links, 2)
        self.assertEqual(stored['boss_name'], 'first')

    def test_battle_uids_are_permanent_linked_and_exceed_99(self):
        batch = [battle(uid=f'b-{number:04d}', round_no=number) for number in range(150)]
        save_round(self.store, 'r1', pages=[page(battles=batch[:75])])
        save_round(self.store, 'r2', pages=[page(battles=batch[75:])])
        found = [row['snapshot_id'] for row in self.store.snapshots()]
        self.assertEqual(len(self.store.export_snapshot(found[1])['battles']), 75)
        with closing(sqlite3.connect(self.path)) as db, db:
            self.assertEqual(db.execute('SELECT count(*) FROM battles').fetchone()[0], 150)
            self.assertEqual(db.execute('SELECT count(*) FROM snapshot_battles').fetchone()[0], 150)
        uids = [row['battle_uid'] for row in self.store.list_battles('test:server1', 'guild_war', '42')]
        self.assertEqual(len(uids), 150)
        self.assertEqual(len(set(uids)), 150)

    def test_season_blob_stats_and_iteration_are_scoped_and_ordered(self):
        save_round(self.store, 'r1', pages=[page(battles=[battle(uid='b-0001', raw=b'alpha')],
                                                raw_payloads=[b'page-a'])])
        save_round(self.store, 'r2', pages=[page(battles=[battle(uid='b-0002', raw=b'beta')],
                                                raw_payloads=[b'page-a', b'page-b'])])
        save_round(self.store, 'other', season_id='43',
                   pages=[page(battles=[battle(uid='b-0003', raw=b'other')])])
        expected = {hashlib.sha256(b'alpha').hexdigest(): b'alpha',
                    hashlib.sha256(b'beta').hexdigest(): b'beta',
                    hashlib.sha256(b'page-a').hexdigest(): b'page-a',
                    hashlib.sha256(b'page-b').hexdigest(): b'page-b'}
        stats = self.store.season_blob_stats('test:server1', 'guild_war', '42')
        self.assertEqual(stats, {'count': 4, 'bytes': sum(len(data) for data in expected.values())})
        items = list(self.store.iter_season_blobs('test:server1', 'guild_war', '42'))
        self.assertEqual([sha for sha, _ in items], sorted(expected))
        self.assertEqual(dict(items), expected)
        with self.assertRaises(KeyError):
            self.store.season_blob_stats('test:server1', 'guild_war', '99')
        with self.assertRaises(KeyError):
            list(self.store.iter_season_blobs('test:server1', 'guild_war', '99'))

    def test_member_history_tracks_dropout_newcomer_and_rejoin(self):
        at = [f'2026-10-20T0{hour}:00:00+00:00' for hour in range(4)]
        save_round(self.store, 's1', pages=[page(membership=[member('1001'), member('1002')])], at=at[0])
        save_round(self.store, 's2', pages=[page(membership=[member('1001')])], at=at[1])
        save_round(self.store, 's3', pages=[page(membership=[member('1001'), member('1002'), member('1003')])], at=at[2])
        history = {row['member_uid']: row for row in
                   self.store.member_history('test:server1', 'guild_war', '42')}
        self.assertEqual(set(history), {'1001', '1002', '1003'})
        self.assertEqual(history['1001']['events'], [])
        self.assertEqual(history['1001']['first_seen_at_utc'], at[0])
        self.assertEqual(history['1001']['last_seen_at_utc'], at[2])
        self.assertEqual(history['1002']['events'],
                         [{'type': 'FIRST_ABSENCE', 'snapshot_id': history['1002']['first_seen_snapshot_id'] + 1,
                           'at_utc': at[1]},
                          {'type': 'REJOIN', 'snapshot_id': history['1002']['first_seen_snapshot_id'] + 2,
                           'at_utc': at[2]}])
        self.assertEqual(history['1003']['first_seen_at_utc'], at[2])
        self.assertEqual(history['1003']['events'], [])

    def test_partial_snapshot_never_marks_absence(self):
        at = [f'2026-10-20T0{hour}:00:00+00:00' for hour in range(4)]
        save_round(self.store, 's1', pages=[page(membership=[member('1001'), member('1002')])], at=at[0])
        save_round(self.store, 's2', coverage='PARTIAL',
                   pages=[page(membership=[member('1001')])], at=at[1])
        save_round(self.store, 's3', pages=[page(membership=[member('1001')])], at=at[2])
        save_round(self.store, 's4', pages=[page(membership=[member('1001'), member('1002')])], at=at[3])
        history = {row['member_uid']: row for row in
                   self.store.member_history('test:server1', 'guild_war', '42')}
        self.assertEqual(history['1002']['events'],
                         [{'type': 'FIRST_ABSENCE', 'snapshot_id': history['1002']['first_seen_snapshot_id'] + 2,
                           'at_utc': at[2]},
                          {'type': 'REJOIN', 'snapshot_id': history['1002']['first_seen_snapshot_id'] + 3,
                           'at_utc': at[3]}])
        save_round(self.store, 's5', coverage='PARTIAL', pages=[page(membership=[member('1004')])], at=at[3])
        self.assertNotIn('1004', {row['member_uid']: row for row in
                                  self.store.member_history('test:server1', 'guild_war', '42')})

    def test_all_trigger_labels_are_accepted_and_stored(self):
        for index, trigger in enumerate(seasons.TRIGGERS):
            save_round(self.store, f't{index}', trigger=trigger)
        found = self.store.snapshots()
        self.assertEqual([row['trigger'] for row in found], list(seasons.TRIGGERS))
        for bad in ('PRE_END_45S', 'routine', '', 'ROUTINE '):
            with self.assertRaises(ValueError):
                save_round(self.store, 'bad-' + bad.strip() or 'empty', trigger=bad)

    def test_coverage_and_settle_are_independent_labels(self):
        save_round(self.store, 'live', coverage='PARTIAL', settle='LIVE',
                   availability='ACTIVE')
        save_round(self.store, 'candidate', coverage='SERVER_VISIBLE_SLICE',
                   settle='SETTLED_CANDIDATE', availability='SETTLED_CANDIDATE')
        found = {row['run_id']: row for row in self.store.snapshots()}
        self.assertEqual((found['live']['coverage'], found['live']['settle_evidence']),
                         ('PARTIAL', 'LIVE'))
        self.assertEqual((found['candidate']['coverage'], found['candidate']['settle_evidence']),
                         ('SERVER_VISIBLE_SLICE', 'SETTLED_CANDIDATE'))
        for coverage, settle in [('FULL', 'LIVE'), ('PARTIAL', 'SETTLED_VERIFIED'),
                                 ('COMPLETE_REPORTED_RANGE', 'LAST_OBSERVED')]:
            if coverage == 'FULL' or settle == 'SETTLED_VERIFIED':
                with self.assertRaises(ValueError):
                    save_round(self.store, f'bad-{coverage}-{settle}',
                               coverage=coverage, settle=settle)

    def test_run_id_is_idempotent_but_conflicting_content_rejected(self):
        first = save_round(self.store, 'run', pages=[page(index=0)])
        self.assertEqual(save_round(self.store, 'run', pages=[page(index=0)]), first)
        self.assertEqual(len(self.store.snapshots()), 1)
        with self.assertRaises(ValueError):
            save_round(self.store, 'run', pages=[page(index=1)])
        self.assertEqual(len(self.store.snapshots()), 1)

    def test_sql_failure_rolls_back_every_table(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("CREATE TRIGGER fail_entry BEFORE INSERT ON rank_entries "
                       "BEGIN SELECT RAISE(ABORT, 'injected'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            save_round(self.store, 'failed')
        with closing(sqlite3.connect(self.path)) as db, db:
            for table in seasons.TABLE_NAMES:
                self.assertEqual(db.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0], 0)

    def test_page_checkpoints_interrupt_marker_and_resume(self):
        run_id = self.store.begin_run('test:server1', 'guild_war', '42', trigger='ROUTINE',
                                      code_version='revision', collected_at_utc='2026-10-20T00:00:00+00:00')
        state = self.store.append_page(run_id, entries=[entry()], membership=[member('1001')])
        self.assertEqual(state['page_index'], 0)
        state = self.store.append_page(run_id, membership=[member('1001'), member('1002')],
                                       evidence={'request_index': 1})
        self.assertEqual(state['next_page'], 2)
        self.assertEqual(self.store.run_state(run_id)['status'], 'RUNNING')
        with self.assertRaises(ValueError):
            self.store.append_page(run_id, page_index=5)
        self.store.interrupt_run(run_id, 'PAGE_BUDGET_EXHAUSTED')
        interrupted = self.store.run_state(run_id)
        self.assertEqual(interrupted['status'], 'INTERRUPTED')
        self.assertEqual(interrupted['interrupt_reason'], 'PAGE_BUDGET_EXHAUSTED')
        self.assertEqual(interrupted['next_page'], 2)
        resumed = self.store.begin_run('test:server1', 'guild_war', '42', trigger='PRE_END_60S',
                                       code_version='revision',
                                       collected_at_utc='2026-10-20T01:00:00+00:00')
        state = self.store.run_state(resumed)
        self.assertEqual(state['status'], 'RUNNING')
        self.assertEqual(state['next_page'], 2)
        self.assertEqual(state['checkpoint']['resume_from'], run_id)
        state = self.store.append_page(resumed, membership=[member('1001'), member('1002'), member('1003')])
        self.assertEqual(state['page_index'], 2)
        self.store.finish_run(resumed, coverage='COMPLETE_REPORTED_RANGE',
                              settle_evidence='LAST_OBSERVED', availability='SETTLING')
        history = {row['member_uid']: row for row in
                   self.store.member_history('test:server1', 'guild_war', '42')}
        self.assertEqual(history['1003']['first_seen_at_utc'], '2026-10-20T01:00:00+00:00')
        self.assertEqual(history['1001']['first_seen_at_utc'], '2026-10-20T00:00:00+00:00')
        self.assertEqual(len(self.store.export_snapshot(state['snapshot_id'])['page_evidence']), 1)

    def test_restart_between_rounds_preserves_history(self):
        save_round(self.store, 'r1', pages=[page(membership=[member('1001')])],
                   at='2026-10-20T00:00:00+00:00')
        del self.store
        self.store = seasons.SeasonHistory(self.path)
        save_round(self.store, 'r2', pages=[page(membership=[member('1001'), member('1002')])],
                   at='2026-10-20T01:00:00+00:00')
        history = {row['member_uid']: row for row in
                   self.store.member_history('test:server1', 'guild_war', '42')}
        self.assertEqual(history['1002']['first_seen_at_utc'], '2026-10-20T01:00:00+00:00')
        self.assertEqual(len(self.store.snapshots()), 2)

    def test_schedule_slots_plan_fire_and_validate(self):
        slot = self.store.plan_schedule_slot('test:server1', 'guild_war', '42', 'PRE_END_300S',
                                             '2026-10-20T21:55:00+00:00')
        self.assertEqual(slot['status'], 'SCHEDULED')
        fired = self.store.fire_schedule_slot('test:server1', 'guild_war', '42', 'PRE_END_300S',
                                              run_id='t9')
        self.assertEqual(fired['status'], 'FIRED')
        self.assertEqual(fired['run_id'], 't9')
        found = self.store.schedule_slots('test:server1', 'guild_war', '42')
        self.assertEqual([row['slot'] for row in found], ['PRE_END_300S'])
        for bad in ('ROUTINE2', 'pre_end_300s'):
            with self.assertRaises(ValueError):
                self.store.plan_schedule_slot('test:server1', 'guild_war', '42', bad,
                                              '2026-10-20T21:55:00+00:00')

    def test_sealed_season_rejects_new_runs_but_keeps_data(self):
        save_round(self.store, 'r1')
        self.store.mark_sealed('test:server1', 'guild_war', '42')
        with self.assertRaises(ValueError):
            save_round(self.store, 'r2')
        with closing(sqlite3.connect(self.path)) as db:
            sealed = db.execute('SELECT sealed_at_utc FROM seasons').fetchone()[0]
        self.assertTrue(sealed)
        self.assertEqual(len(self.store.snapshots()), 1)

    def test_invalid_identity_and_bounded_input_rejected_atomically(self):
        bad_pages = [
            [page(entries=[{'entity_id': 'x', 'score': 2**63}])],
            [page(entries=[entry(), entry()])],
            [page(membership=[member('1001'), member('1001')])],
            [page(battles=[battle(uid='')])],
            [page(battles=[battle(presence=[{'member_uid': '1', 'status': 'MAYBE'}])])],
            [page(raw_payloads=[b'x' * (4 * 1024 * 1024 + 1)])],
        ]
        for index, pages in enumerate(bad_pages):
            with self.subTest(index=index), self.assertRaises((ValueError, TypeError)):
                save_round(self.store, f'bad{index}', pages=pages)
        for season_id in (0, -1, '0', 'a\x00b'):
            with self.assertRaises(ValueError):
                save_round(self.store, f'bad-season-{season_id!r}', season_id=season_id)
        self.assertEqual(self.store.snapshots(), [])

    def test_unknown_fields_are_removed_at_every_persisted_level(self):
        evil = page()
        evil['entries'][0]['raw_data']['jwt'] = 'SECRET1'
        evil['entries'][0]['credential'] = 'SECRET2'
        evil['membership'][0]['raw_data']['token'] = 'SECRET3'
        evil['battles'][0]['lineup'][0]['raw_data']['uin'] = 9007199254740995
        evil['battles'][0]['presence'][0]['note'] = 'SECRET4'
        evil['evidence']['session'] = 'SECRET5'
        saved = save_round(self.store, 'safe', pages=[evil], metadata={'phase': 2, 'secret': 'SECRET6'})
        exported = json.dumps(self.store.export_snapshot(saved['snapshot_id']))
        self.assertNotIn('SECRET', exported)
        self.assertNotIn(b'SECRET', self.path.read_bytes())

    def test_decoded_record_fields_survive_normalization_without_pb(self):
        cards = [{'cardUid': 11, 'uin': '9007199254740993', 'cardId': 101, 'level': 60,
                  'quality': 5, 'grade': 7, 'fashionId': 900, 'position': 1}]
        skills = [{'skillId': 4001, 'level': 3}]
        formation_value = {'stageId': 51506311, 'fighterUin': '9007199254740993',
                           'fighterName': '战士', 'cardInfos': cards,
                           'damageHp': '9223372036854775807', 'displayDamageHp': '1234567',
                           'battleUid': '12345678901234567', 'skillLevels': skills,
                           'battleTime': 65, 'version': 2}
        lineup_row = {'member_uid': '9007199254740993', 'slot': 0, 'power': 999,
                      'raw_data': {'fighterName': '战士', 'cardInfos': cards,
                                   'skillLevels': skills,
                                   'damageHp': '9223372036854775807',
                                   'battleUid': '12345678901234567',
                                   'sessionToken': 'SECRET-A'}}
        member_row = member('77', raw_data={'uin': '77', 'round': 2,
                                            'score': '9223372036854775807',
                                            'cards': [{'cardId': '7', 'skinId': '0'}],
                                            'jwt': 'SECRET-B'})
        entry_row = entry(raw_data={'battleUid': '12345678901234567', 'stageId': 51506311,
                                    'credential': 'SECRET-C'})
        battle_row = battle(uid='b-0007', formation=formation_value, lineup=[lineup_row],
                            presence=[{'member_uid': '9007199254740993', 'status': 'PRESENT'}],
                            raw_payload=None)
        saved = save_round(self.store, 'real',
                           pages=[page(entries=[entry_row], membership=[member_row],
                                       battles=[battle_row], raw_payloads=[])])
        data = self.store.export_snapshot(saved['snapshot_id'])
        got = data['battles'][0]
        self.assertEqual(got['formation'], formation_value)
        self.assertEqual(got['lineup'][0]['raw_data']['cardInfos'], cards)
        self.assertEqual(got['lineup'][0]['raw_data']['skillLevels'], skills)
        self.assertNotIn('SECRET-A', json.dumps(got['lineup']))
        self.assertEqual(data['membership'][0]['raw_data']['cards'], [{'cardId': '7', 'skinId': '0'}])
        self.assertEqual(data['membership'][0]['raw_data']['score'], '9223372036854775807')
        self.assertEqual(data['entries'][0]['raw_data']['battleUid'], '12345678901234567')
        exported = json.dumps(data)
        self.assertNotIn('SECRET', exported)
        with self.assertRaises(ValueError):
            save_round(self.store, 'object-leak',
                       pages=[page(membership=[member('9', raw_data={'score': object()})])])
        with self.assertRaises(ValueError):
            save_round(self.store, 'object-leak-2',
                       pages=[page(membership=[member('10', raw_data={'cardInfos': object()})])])

    def test_records_decoder_output_roundtrips_through_seasons(self):
        from gacha_core import leaderboard_records as records
        from gacha_core.gacha import blob as pb_blob
        from gacha_core.gacha import integer as pb_int

        def card_info(card_id, level=10, quality=4, grade=6, fashion_id=700, position=3):
            return (pb_int(3, card_id) + pb_int(4, level) + pb_int(5, quality)
                    + pb_int(6, grade) + pb_int(7, fashion_id) + pb_int(8, position))

        body = pb_blob(1, (pb_int(1, 51506311) + pb_int(2, 9007199254740993)
                           + pb_blob(3, '战士'.encode())
                           + pb_blob(4, card_info(101))
                           + pb_int(6, 9223372036854775807) + pb_int(7, 12345678901234567)
                           + pb_blob(8, pb_int(1, 4001) + pb_int(2, 3))
                           + pb_int(9, 65) + pb_int(10, 2)))
        rows = records.decode_guild_recommendations(body, 51506311)
        battle_row = {'battle_uid': rows[0]['battleUid'], 'round': 1,
                      'formation': rows[0], 'lineup': [], 'presence': [],
                      'raw_payload': b''}
        saved = save_round(self.store, 'decoded', pages=[page(battles=[battle_row],
                                                              raw_payloads=[])])
        data = self.store.export_snapshot(saved['snapshot_id'])
        self.assertEqual(data['battles'][0]['formation'], rows[0])
        self.assertEqual(data['battles'][0]['raw_sha256'],
                         hashlib.sha256(b'').hexdigest())

    def test_empty_business_pb_is_a_valid_empty_pool_blob(self):
        saved = save_round(self.store, 'empty',
                           pages=[page(battles=[battle(uid='b-0', raw_payload=b'')],
                                       raw_payloads=[b''])])
        data = self.store.export_snapshot(saved['snapshot_id'])
        self.assertEqual(data['battles'][0]['raw_sha256'], hashlib.sha256(b'').hexdigest())
        blobs = dict(self.store.iter_season_blobs('test:server1', 'guild_war', '42'))
        self.assertEqual(blobs[hashlib.sha256(b'').hexdigest()], b'')

    def test_all_writes_reject_sealed_season(self):
        run_id = self.store.begin_run('test:server1', 'guild_war', '42', trigger='ROUTINE',
                                      code_version='revision',
                                      collected_at_utc='2026-10-20T00:00:00+00:00')
        self.store.append_page(run_id, entries=[entry()], membership=[member('1001')])
        self.store.plan_schedule_slot('test:server1', 'guild_war', '42', 'PRE_END_300S',
                                      '2026-10-20T21:55:00+00:00')
        token = self.store.mark_sealed('test:server1', 'guild_war', '42')
        self.assertTrue(token)
        with self.assertRaises(ValueError):
            self.store.begin_run('test:server1', 'guild_war', '42', trigger='ROUTINE',
                                 code_version='revision')
        with self.assertRaises(ValueError):
            save_round(self.store, 'blocked')
        with self.assertRaises(ValueError):
            self.store.append_page(run_id, membership=[member('1002')])
        with self.assertRaises(ValueError):
            self.store.finish_run(run_id, coverage='PARTIAL', settle_evidence='LIVE',
                                  availability='ACTIVE')
        with self.assertRaises(ValueError):
            self.store.interrupt_run(run_id, 'BLOCKED')
        with self.assertRaises(ValueError):
            self.store.plan_schedule_slot('test:server1', 'guild_war', '42', 'PRE_END_60S',
                                          '2026-10-20T21:58:00+00:00')
        with self.assertRaises(ValueError):
            self.store.fire_schedule_slot('test:server1', 'guild_war', '42', 'PRE_END_300S',
                                          run_id='r9')
        self.assertEqual(len(self.store.snapshots()), 1)
        self.assertEqual(self.store.snapshots()[0]['membership_count'], 1)
        self.assertTrue(self.store.unseal_season('test:server1', 'guild_war', '42', token))
        save_round(self.store, 'after-unseal')

    def test_purge_requires_seal_mark_and_unseal_needs_matching_token(self):
        save_round(self.store, 'r1')
        with self.assertRaisesRegex(ValueError, 'not sealed'):
            self.store.purge_season('test:server1', 'guild_war', '42')
        token = self.store.mark_sealed('test:server1', 'guild_war', '42')
        self.assertFalse(self.store.unseal_season('test:server1', 'guild_war', '42', 'bogus'))
        with self.assertRaises(ValueError):
            save_round(self.store, 'still-sealed')
        self.assertTrue(self.store.unseal_season('test:server1', 'guild_war', '42', token))
        save_round(self.store, 'r2')
        self.store.mark_sealed('test:server1', 'guild_war', '42')
        deleted = self.store.purge_season('test:server1', 'guild_war', '42')
        self.assertEqual(deleted['runs'], 2)
        with self.assertRaises(KeyError):
            self.store.purge_season('test:server1', 'guild_war', '42')

    def test_purge_reclaims_file_space_and_keeps_other_seasons_intact(self):
        fat = [battle(uid=f'b-{number:04d}',
                      raw=f'fat-{number}-'.encode() + b'x' * (256 * 1024))
               for number in range(8)]
        save_round(self.store, 'fat1', pages=[page(battles=fat[:4])])
        save_round(self.store, 'fat2', pages=[page(battles=fat[4:])])
        save_round(self.store, 'other', season_id='43',
                   pages=[page(membership=[member('5001')])],
                   at='2026-10-20T02:00:00+00:00')
        other_history = self.store.member_history('test:server1', 'guild_war', '43')
        other_counts = self.store.season_counts('test:server1', 'guild_war', '43')
        before = self.path.stat().st_size
        self.assertGreater(before, 2 * 1024 * 1024)
        self.store.mark_sealed('test:server1', 'guild_war', '42')
        result = self.store.purge_season('test:server1', 'guild_war', '42')
        self.assertTrue(result['vacuumed'])
        self.assertEqual(result['runs'], 2)
        after = self.path.stat().st_size
        self.assertLess(after, before)
        self.assertLess(after, 1024 * 1024)
        self.assertEqual(self.store.season_counts('test:server1', 'guild_war', '43'), other_counts)
        self.assertEqual(self.store.member_history('test:server1', 'guild_war', '43'), other_history)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])
        save_round(self.store, 'after', season_id='43')
        self.assertEqual(self.store.season_counts('test:server1', 'guild_war', '43')['runs'], 2)

    def test_purge_reports_cleaned_when_vacuum_fails(self):
        save_round(self.store, 'r1')
        save_round(self.store, 'other', season_id='43')
        self.store.mark_sealed('test:server1', 'guild_war', '42')
        with mock.patch.object(seasons.SeasonHistory, '_reclaim_space', return_value=False):
            result = self.store.purge_season('test:server1', 'guild_war', '42')
        self.assertFalse(result['vacuumed'])
        self.assertEqual(result['runs'], 1)
        self.assertEqual(self.store.season_counts('test:server1', 'guild_war', '42')['runs'], 0)
        self.assertEqual(self.store.season_counts('test:server1', 'guild_war', '43')['runs'], 1)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_cannot_initialize_over_an_unrelated_database(self):
        other = Path(self.tmp.name) / 'other.sqlite3'
        with closing(sqlite3.connect(other)) as db, db:
            db.execute('CREATE TABLE existing(value TEXT)')
        with self.assertRaises(ValueError):
            seasons.SeasonHistory(other)
        with closing(sqlite3.connect(other)) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
