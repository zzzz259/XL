import copy
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

import leaderboard_archive


def sample(**changes):
    value = {'scope': 'test:server1', 'mode': 'illusion', 'season_id': '9007199254740993',
                 'entity_kind': 'player', 'metadata': {'phase': 2, 'endStamp': 1700000010},
                 'availability': 'ACTIVE', 'coverage': 'PARTIAL', 'settle_evidence': 'LIVE',
                 'collection_error': None, 'pages_fetched': 1,
                 'collected_at_utc': '2026-10-24T00:00:00+00:00', 'source_rpc': 123,
                 'source_hash': None, 'member_entries': [],
                 'entries': [{'rank': 1, 'entity_id': 9007199254740995,
                               'display_name': 'same name', 'score': 9223372036854775807,
                               'guild_uid': 9007199254740997, 'guild_name': 'guild', 'level': 80,
                               'raw_data': {'scoreDiff': -10}}],
                 'page_evidence': [{'request_index': 0, 'echoed_index': 0, 'count': 1,
                                     'source_hash': None}]}
    value.update(changes)
    return value


class LeaderboardArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / 'data')
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'leaderboards.sqlite3'
        self.store = leaderboard_archive.LeaderboardArchive(self.path)

    def test_roundtrip_int64_ids_pages_and_persistent_reopen(self):
        saved = self.store.save_run('run1', [sample()], code_version='revision')
        self.assertEqual(saved['attempts'], 1)
        sid, = saved['snapshot_ids']
        data = leaderboard_archive.LeaderboardArchive(self.path).export_snapshot(sid)
        self.assertEqual(data['season_id'], '9007199254740993')
        self.assertEqual(data['entries'][0]['entity_id'], '9007199254740995')
        self.assertEqual(data['entries'][0]['guild_uid'], '9007199254740997')
        self.assertEqual(data['entries'][0]['score'], 9223372036854775807)
        self.assertEqual(data['page_evidence'][0]['source_hash'], None)
        self.assertEqual(data['code_version'], 'revision')
        self.assertEqual(json.loads(json.dumps(data))['entries'], data['entries'])
        with closing(sqlite3.connect(self.path)) as db, db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 1)
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_closed_no_season_error_and_notice_are_observations_only(self):
        old = self.store.save_run('old', [sample()])['snapshot_ids']
        for number, change in enumerate([
                {'availability': 'CLOSED'}, {'season_id': None},
                {'availability': 'NOTICE'}, {'availability': 'NOT_OPEN'},
                {'availability': 'UNKNOWN', 'collection_error': 'PROTOCOL_ERROR'}]):
            result = sample(entries=[], page_evidence=[], **change)
            self.assertEqual(self.store.save_run(str(number), [result])['snapshot_ids'], [])
        self.assertEqual([row['snapshot_id'] for row in self.store.snapshots()], old)
        with closing(sqlite3.connect(self.path)) as db, db:
            self.assertEqual(db.execute('SELECT count(*) FROM attempts').fetchone()[0], 6)
        self.assertEqual(len(self.store.list_seasons()), 1)

    def test_valid_empty_and_partial_error_snapshot_have_distinct_labels(self):
        empty = sample(entries=[], coverage='COMPLETE_REPORTED_RANGE', availability='EMPTY')
        partial = sample(collection_error='PAGE_BUDGET', coverage='PARTIAL')
        saved = self.store.save_run('run', [empty, partial])
        self.assertEqual(len(saved['snapshot_ids']), 2)
        self.assertEqual(self.store.export_snapshot(saved['snapshot_ids'][0])['entries'], [])
        self.assertEqual(self.store.export_snapshot(saved['snapshot_ids'][1])['collection_error'], 'PAGE_BUDGET')

    def test_history_and_filters_isolate_scope_mode_season_and_member_kind(self):
        member = {'entity_id': '42', 'rank': 1, 'display_name': 'same name', 'score': -20}
        values = [sample(member_entries=[member]), sample(scope='test:server2'),
                  sample(mode='nightmare'), sample(season_id='next')]
        saved = self.store.save_run('history', values)
        self.assertEqual(len(self.store.list_seasons()), 5)
        found = self.store.snapshots(scope='test:server1', mode='illusion', season_id=9007199254740993)
        self.assertEqual(len(found), 2)
        member_snapshot = next(row for row in found if row['entity_kind'] == 'guild_member')
        data = self.store.export_snapshot(member_snapshot['snapshot_id'])
        self.assertEqual(data['member_entries'][0]['entity_id'], '42')
        self.assertEqual(data['metadata']['member_scope'], 'UNKNOWN')
        self.assertEqual(len(self.store.snapshots()), len(saved['snapshot_ids']))

    def test_run_id_is_idempotent_but_conflicting_content_or_version_rejected(self):
        original = sample()
        first = self.store.save_run('run', [original], code_version='a')
        self.assertEqual(self.store.save_run('run', [copy.deepcopy(original)], code_version='a'), first)
        for values, version in [([sample(coverage='INCONSISTENT')], 'a'), ([original], 'b')]:
            with self.assertRaises(ValueError):
                self.store.save_run('run', values, code_version=version)
        self.assertEqual(len(self.store.snapshots()), 1)

    def test_unknown_fields_are_removed_at_every_persisted_level(self):
        value = sample(password='SECRET1')
        value['metadata']['accessToken'] = 'SECRET2'
        value['entries'][0]['raw_data']['jwt'] = 'SECRET3'
        value['page_evidence'][0]['device_id'] = 'SECRET4'
        value['entries'][0]['credential'] = 'SECRET5'
        sid, = self.store.save_run('safe', [value])['snapshot_ids']
        exported = json.dumps(self.store.export_snapshot(sid))
        self.assertNotIn('SECRET', exported)
        self.assertNotIn(b'SECRET', self.path.read_bytes())

    def test_invalid_input_is_bounded_and_never_partially_saved(self):
        bad_values = [sample(entries=[{'entity_id': 'x', 'score': 2**63}]),
                      sample(entries=[{'entity_id': 'x', 'score': True}]),
                      sample(scope=''), sample(season_id=0),
                      sample(entries=[{'entity_id': 'x', 'score': 1.2}]),
                      sample(entries=[{'entity_id': 'x', 'display_name': 'x'*4097}]),
                      sample(entries=[{'entity_id': 'x'}]*1001),
                      sample(settle_evidence='SETTLED_VERIFIED'),
                      sample(entries=[{'entity_id': 'x'}, {'entity_id': 'x'}])]
        for index, bad in enumerate(bad_values):
            with self.subTest(index=index), self.assertRaises((ValueError, TypeError)):
                self.store.save_run(str(index), [sample(), bad])
        self.assertEqual(self.store.snapshots(), [])
        with closing(sqlite3.connect(self.path)) as db, db:
            self.assertEqual(db.execute('SELECT count(*) FROM runs').fetchone()[0], 0)

    def test_sql_failure_rolls_back_all_tables(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("CREATE TRIGGER fail_entry BEFORE INSERT ON entries BEGIN SELECT RAISE(ABORT, 'injected'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.save_run('failed', [sample()])
        with closing(sqlite3.connect(self.path)) as db, db:
            for table in ('runs', 'attempts', 'seasons', 'snapshots', 'entries', 'page_evidence'):
                self.assertEqual(db.execute('SELECT count(*) FROM ' + table).fetchone()[0], 0)

    def test_unknown_schema_version_fails_without_modification(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('PRAGMA user_version=99')
        with self.assertRaises(ValueError):
            leaderboard_archive.LeaderboardArchive(self.path)
        with closing(sqlite3.connect(self.path)) as db, db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 99)

    def test_missing_snapshot_raises_keyerror(self):
        with self.assertRaises(KeyError):
            self.store.export_snapshot(1234)

    def test_no_ranking_with_known_season_remains_attempt_only(self):
        saved = self.store.save_run('not-fetched', [sample(coverage='NO_RANKING')])
        self.assertEqual(saved['snapshot_ids'], [])
        self.assertEqual(self.store.list_seasons(), [])

    def test_active_metadata_without_any_rank_page_is_not_empty_snapshot(self):
        value = sample(entries=[], member_entries=[], pages_fetched=0, page_evidence=[])
        self.assertEqual(self.store.save_run('metadata-only', [value])['snapshot_ids'], [])

    def test_attempt_keeps_page_evidence_even_when_season_is_unknown(self):
        value = sample(season_id=None, source_rpc=[2001, 2002])
        self.store.save_run('observation', [value])
        with closing(sqlite3.connect(self.path)) as db:
            attempt = json.loads(db.execute('SELECT payload FROM attempts').fetchone()[0])
        self.assertEqual(attempt['page_evidence'], value['page_evidence'])
        self.assertEqual(attempt['source_rpc'], [2001, 2002])
        self.assertEqual(attempt['entries_count'], 1)
        self.assertNotIn('entries', attempt)

    def test_member_snapshot_keeps_inconsistent_and_partial_coverage(self):
        for coverage in ('PARTIAL', 'INCONSISTENT'):
            value = sample(member_entries=[{'entity_id': '10', 'score': 5}],
                           coverage=coverage, collection_error='PAGE_BUDGET')
            saved = self.store.save_run(coverage, [value])
            rows = [self.store.export_snapshot(sid) for sid in saved['snapshot_ids']]
            member = next(row for row in rows if row['entity_kind'] == 'guild_member')
            self.assertEqual(member['coverage'], coverage)

    def test_error_cannot_claim_complete_and_all_collector_evidence_survives(self):
        value = sample(coverage='COMPLETE_REPORTED_RANGE', collection_error='PAGE_BUDGET')
        value['metadata'].update(rewardStages=[1, 2], time_basis='LOCAL_CLOCK')
        value['page_evidence'][0]['is_recheck'] = True
        value['entries'][0]['raw_data']['uin'] = 9007199254740995
        sid, = self.store.save_run('partial', [value])['snapshot_ids']
        data = self.store.export_snapshot(sid)
        self.assertEqual(data['coverage'], 'PARTIAL')
        self.assertEqual(data['metadata']['rewardStages'], [1, 2])
        self.assertTrue(data['page_evidence'][0]['is_recheck'])
        self.assertEqual(data['entries'][0]['raw_data']['uin'], '9007199254740995')

    def test_cannot_initialize_over_an_unrelated_database(self):
        other = Path(self.tmp.name) / 'other.sqlite3'
        with closing(sqlite3.connect(other)) as db, db:
            db.execute('CREATE TABLE existing(value TEXT)')
        with self.assertRaises(ValueError):
            leaderboard_archive.LeaderboardArchive(other)
        with closing(sqlite3.connect(other)) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(), [('existing',)])


if __name__ == '__main__':
    unittest.main()
