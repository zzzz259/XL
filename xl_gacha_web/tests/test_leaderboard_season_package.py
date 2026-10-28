"""Per-season tar/gzip sealing with verification, retention, and isolated cleanup."""
import hashlib
import json
import tarfile
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

import leaderboard_season_package as season_package
import leaderboard_seasons as seasons


def entry(entity='9007199254740995', rank=1, score=100, **changes):
    value = {'entity_id': entity, 'rank': rank, 'score': score,
             'display_name': 'same name', 'guild_name': 'guild',
             'guild_uid': '9007199254740997', 'level': 80, 'raw_data': {'round': 3}}
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
             'lineup': [{'member_uid': '1001', 'slot': 0, 'power': 999, 'raw_data': {}}],
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
                          'reported_total': 2, 'entry_count': 1, 'member_count': 2},
             'raw_payloads': []}
    value.update(changes)
    return value


def save_round(store, run_id, scope='test:server1', mode='guild_war', season_id='42',
               trigger='ROUTINE', coverage='COMPLETE_REPORTED_RANGE',
               settle='LAST_OBSERVED', availability='ACTIVE',
               at='2026-10-20T00:00:00+00:00', pages=None):
    return store.save_run(run_id, scope=scope, mode=mode, season_id=season_id,
                          trigger=trigger, code_version='revision', coverage=coverage,
                          settle_evidence=settle, availability=availability,
                          collected_at_utc=at,
                          pages=[page()] if pages is None else pages)


class SeasonPackageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / 'data')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / 'seasonal_leaderboards.sqlite3'
        self.store = seasons.SeasonHistory(self.path)
        self.packages_dir = self.root / 'packages'
        self.packager = season_package.SeasonPackager(self.store, self.packages_dir)

    def seal(self, scope='test:server1', mode='guild_war', season_id='42', **changes):
        return self.packager.seal_season(scope, mode, season_id, **changes)

    def package_path(self, entry):
        return self.packages_dir / entry['package']

    def test_seal_creates_tar_with_sqlite_manifest_and_deduplicated_blobs(self):
        save_round(self.store, 'r1', pages=[page(battles=[battle(raw=b'shared-pb')])])
        save_round(self.store, 'r2', at='2026-10-20T01:00:00+00:00',
                   pages=[page(battles=[battle(raw=b'shared-pb', raw_payload=b'shared-pb')])])
        entry = self.seal()
        self.assertEqual(entry['status'], 'ARCHIVE_SEALED')
        package = self.package_path(entry)
        self.assertTrue(package.is_file())
        self.assertEqual(hashlib.sha256(package.read_bytes()).hexdigest(), entry['sha256'])
        with tarfile.open(package, 'r:gz') as tar:
            names = tar.getnames()
            manifest = json.loads(tar.extractfile('manifest.json').read())
            sqlite_bytes = tar.extractfile('season.sqlite3').read()
        self.assertIn('season.sqlite3', names)
        self.assertIn('manifest.json', names)
        blob_names = sorted(name for name in names if name.startswith('blobs/'))
        self.assertEqual(len(blob_names), 1)
        self.assertEqual(blob_names[0], 'blobs/' + hashlib.sha256(b'shared-pb').hexdigest() + '.pb')
        self.assertEqual(manifest['scope'], 'test:server1')
        self.assertEqual(manifest['mode'], 'guild_war')
        self.assertEqual(manifest['season_id'], '42')
        self.assertEqual(manifest['counts']['snapshots'], 2)
        self.assertEqual(manifest['counts']['battles'], 1)
        self.assertEqual(manifest['counts']['rank_entries'], 2)
        self.assertEqual(manifest['files']['season.sqlite3']['sha256'],
                         hashlib.sha256(sqlite_bytes).hexdigest())

    def test_seal_streams_blobs_one_at_a_time_and_package_stays_verified(self):
        first = [battle(uid=f'b-{number:04d}', raw=f'pb-{number}'.encode())
                 for number in range(6)]
        second = [battle(uid=f'b-{number:04d}', raw=f'pb-{number}'.encode())
                  for number in range(6, 12)]
        save_round(self.store, 'r1', pages=[page(battles=first)])
        save_round(self.store, 'r2', at='2026-10-20T01:00:00+00:00', pages=[page(battles=second)])
        active = {'count': 0, 'max': 0}
        original = seasons.SeasonHistory.iter_season_blobs

        def tracked(history, scope, mode, season_id):
            for sha, data in original(history, scope, mode, season_id):
                active['count'] += 1
                active['max'] = max(active['max'], active['count'])
                try:
                    yield sha, data
                finally:
                    active['count'] -= 1

        with mock.patch.object(seasons.SeasonHistory, 'iter_season_blobs', tracked):
            entry = self.seal()
        self.assertEqual(active['max'], 1)
        self.assertEqual(entry['counts']['battles'], 12)
        self.assertEqual(entry['counts']['raw_blobs'], 12)
        with tarfile.open(self.package_path(entry), 'r:gz') as tar:
            blob_names = sorted(name for name in tar.getnames() if name.startswith('blobs/'))
        self.assertEqual(len(blob_names), 12)
        with self.packager.open_season('test:server1', 'guild_war', '42') as package:
            for number in range(12):
                payload = f'pb-{number}'.encode()
                self.assertEqual(package.blob(hashlib.sha256(payload).hexdigest()), payload)
            self.assertEqual(len(package.battles()), 12)

    def test_writes_rejected_while_seal_runs_and_mark_released_on_failure(self):
        save_round(self.store, 'r1')

        def guarded(*args, **kwargs):
            with self.assertRaises((ValueError, KeyError)):
                save_round(self.store, 'mid-seal')
            with self.assertRaises(ValueError):
                self.store.begin_run('test:server1', 'guild_war', '42', trigger='ROUTINE',
                                     code_version='revision')
            raise RuntimeError('boom')

        with mock.patch.object(season_package.SeasonPackager, '_verify_package', side_effect=guarded), \
                self.assertRaises(season_package.SeasonPackageError):
            self.seal()
        save_round(self.store, 'r2')
        self.assertEqual(self.store.season_counts('test:server1', 'guild_war', '42')['runs'], 2)
        self.assertEqual(self.packager.lookup('test:server1', 'guild_war', '42'), None)
        self.assertEqual(list(self.packages_dir.glob('*.tar.gz')), [])

    def test_crash_recovery_marked_without_index_can_seal_again(self):
        save_round(self.store, 'r1')
        self.store.mark_sealed('test:server1', 'guild_war', '42')
        entry = self.seal()
        self.assertEqual(entry['status'], 'ARCHIVE_SEALED')
        self.assertEqual(self.store.season_counts('test:server1', 'guild_war', '42')['runs'], 0)

    def test_orphan_package_without_index_is_replaced_and_runtime_untouched(self):
        save_round(self.store, 'r1')
        orphan = self.packages_dir / 'season__test_server1__guild_war__42.tar.gz'
        orphan.write_bytes(b'orphan')
        entry = self.seal()
        package = self.package_path(entry)
        self.assertEqual(hashlib.sha256(package.read_bytes()).hexdigest(), entry['sha256'])
        self.assertGreater(entry['bytes'], len(b'orphan'))
        with self.packager.open_season('test:server1', 'guild_war', '42') as opened:
            self.assertEqual(len(opened.rank_entries()), 1)

    def test_seal_reports_space_reclaimed_and_updates_index(self):
        save_round(self.store, 'r1')
        entry = self.seal()
        self.assertIs(entry['space_reclaimed'], True)
        self.assertEqual(self.packager.lookup('test:server1', 'guild_war', '42')['space_reclaimed'],
                         True)
        self.assertEqual(self.seal(), entry)

    def test_seal_keeps_package_and_index_when_vacuum_fails_after_purge(self):
        save_round(self.store, 'r1')
        with mock.patch.object(seasons.SeasonHistory, '_reclaim_space', return_value=False):
            entry = self.seal()
        self.assertIs(entry['space_reclaimed'], False)
        self.assertEqual(self.store.season_counts('test:server1', 'guild_war', '42')['runs'], 0)
        indexed = self.packager.lookup('test:server1', 'guild_war', '42')
        self.assertIs(indexed['space_reclaimed'], False)
        package = self.package_path(entry)
        self.assertTrue(package.is_file())
        self.assertEqual(hashlib.sha256(package.read_bytes()).hexdigest(), entry['sha256'])
        with self.packager.open_season('test:server1', 'guild_war', '42') as opened:
            self.assertEqual(len(opened.rank_entries()), 1)
        self.assertEqual(self.seal(), entry)

    def test_purge_failure_after_index_rolls_back_and_allows_reseal(self):
        save_round(self.store, 'r1')
        def flaky(history, scope, mode, season_id):
            raise RuntimeError('purge failed')

        with mock.patch.object(seasons.SeasonHistory, 'purge_season', flaky), \
                self.assertRaises(season_package.SeasonPackageError):
            self.seal()
        self.assertEqual(self.store.season_counts('test:server1', 'guild_war', '42')['runs'], 1)
        self.assertEqual(self.packager.lookup('test:server1', 'guild_war', '42'), None)
        self.assertEqual(list(self.packages_dir.glob('*.tar.gz')), [])
        index = self.packages_dir / 'index.jsonl'
        if index.exists():
            for line in index.read_text(encoding='utf-8').splitlines():
                json.loads(line)
        entry = self.seal()
        self.assertEqual(entry['status'], 'ARCHIVE_SEALED')
        self.assertEqual(self.store.season_counts('test:server1', 'guild_war', '42')['runs'], 0)

    def test_crash_after_index_before_purge_resumes_by_purging(self):
        save_round(self.store, 'r1')
        counts = self.store.season_counts('test:server1', 'guild_war', '42')
        self.packager._append_index({'scope': 'test:server1', 'mode': 'guild_war',
                                     'season_id': '42',
                                     'package': 'season__test_server1__guild_war__42.tar.gz',
                                     'sha256': 'pending', 'bytes': 0,
                                     'sealed_at_utc': '2026-10-20T00:00:00+00:00',
                                     'status': 'ARCHIVE_SEALED', 'counts': counts})
        entry = self.seal()
        self.assertEqual(entry['status'], 'ARCHIVE_SEALED')
        self.assertEqual(self.store.season_counts('test:server1', 'guild_war', '42')['runs'], 0)

    def test_sealed_season_runtime_data_removed_but_package_and_index_kept(self):
        save_round(self.store, 'r1')
        before = self.store.season_counts('test:server1', 'guild_war', '42')
        entry = self.seal()
        self.assertTrue(self.package_path(entry).is_file())
        self.assertTrue((self.packages_dir / 'index.jsonl').is_file())
        self.assertEqual(self.store.season_counts('test:server1', 'guild_war', '42'),
                         {name: 0 for name in before})
        self.assertEqual(self.store.snapshots(scope='test:server1', mode='guild_war', season_id='42'), [])
        self.assertEqual(self.packager.lookup('test:server1', 'guild_war', '42')['status'], 'ARCHIVE_SEALED')
        self.assertEqual(self.packager.lookup('test:server1', 'guild_war', '42')['counts']['rank_entries'],
                         before['rank_entries'])
        self.assertEqual(len(self.packager.list_sealed()), 1)

    def test_packaged_season_reopens_and_queries_all_views_twice(self):
        at = [f'2026-10-20T0{hour}:00:00+00:00' for hour in range(3)]
        save_round(self.store, 'r1', pages=[page(membership=[member('1001'), member('1002')])], at=at[0])
        save_round(self.store, 'r2', trigger='PRE_END_30S', at=at[1],
                   pages=[page(membership=[member('1001')],
                               battles=[battle(uid='b-0002', round_no=2, raw=b'battle-pb-2')])])
        save_round(self.store, 'r3', trigger='POST_CLOSE_1H', availability='CLOSED', at=at[2],
                   settle='SETTLED_CANDIDATE', coverage='SERVER_VISIBLE_SLICE',
                   pages=[page(membership=[member('1001'), member('1002')],
                               battles=[battle(uid='b-0002', round_no=2, raw=b'changed'),
                                        battle(uid='b-0003', round_no=3, raw=b'battle-pb-3')])])
        snapshot_ids = [row['snapshot_id'] for row in self.store.snapshots()]
        exports = [self.store.export_snapshot(sid) for sid in snapshot_ids]
        history = self.store.member_history('test:server1', 'guild_war', '42')
        entry = self.seal()
        for _ in range(2):
            with self.packager.open_season('test:server1', 'guild_war', '42') as package:
                self.assertEqual(package.manifest()['counts'], entry['counts'])
                rank_rows = package.rank_entries()
                self.assertEqual([tuple(sorted(row.items())) for row in rank_rows],
                                 [tuple(sorted(row.items()))
                                  for exp in exports for row in exp['entries']])
                membership = package.snapshot_members(snapshot_ids[1])
                self.assertEqual([row['member_uid'] for row in membership], ['1001'])
                battles = package.battles()
                self.assertEqual([row['battle_uid'] for row in battles], ['b-0001', 'b-0002', 'b-0003'])
                formation = package.lineup('b-0002')
                self.assertEqual(formation['lineup'][0]['member_uid'], '1001')
                present = package.presence('b-0002')
                self.assertEqual({row['member_uid']: row['status'] for row in present},
                                 {'1001': 'PRESENT', '1002': 'ABSENT'})
                self.assertEqual(package.member_history(), history)
                self.assertEqual(package.blob(hashlib.sha256(b'battle-pb-3').hexdigest()), b'battle-pb-3')
                self.assertIsNone(package.blob(hashlib.sha256(b'missing').hexdigest()))

    def test_failed_verification_retains_runtime_data_and_cleans_temp(self):
        save_round(self.store, 'r1')
        before = self.store.season_counts('test:server1', 'guild_war', '42')
        with mock.patch.object(season_package.SeasonPackager, '_verify_package',
                               side_effect=RuntimeError('boom')), \
                self.assertRaises(season_package.SeasonPackageError):
            self.seal()
        self.assertEqual(self.store.season_counts('test:server1', 'guild_war', '42'), before)
        self.assertEqual(self.packager.lookup('test:server1', 'guild_war', '42'), None)
        self.assertEqual(list(self.packages_dir.glob('*.tar.gz')), [])
        self.assertEqual(list(self.packages_dir.glob('.seal-*')), [])
        self.assertEqual(list(self.packages_dir.glob('**/.seal-*')), [])

    def test_disk_space_check_failure_retains_runtime_data(self):
        save_round(self.store, 'r1')
        before = self.store.season_counts('test:server1', 'guild_war', '42')
        with self.assertRaises(season_package.SeasonPackageError):
            self.seal(min_free_bytes=10**15)
        self.assertEqual(self.store.season_counts('test:server1', 'guild_war', '42'), before)
        self.assertEqual(list(self.packages_dir.glob('*.tar.gz')), [])

    def test_path_escape_and_identity_confusion_are_rejected(self):
        save_round(self.store, 'r1')
        for scope, mode, season_id in [
                ('../escape', 'guild_war', '42'), ('test:server1', 'guild_war', '..\\evil'),
                ('test:server1', 'guild_war', 'a/b'), ('test:server1', 'guild_war', 'a\\b'),
                ('test:server1', 'guild_war', '.'), ('test:server1', 'guild_war', '..'),
                ('test:server1', 'bad mode', '42'), ('test:server1', 'guild_war', '0'),
                ('test\x00server1', 'guild_war', '42')]:
            with self.subTest(scope=scope, mode=mode, season_id=season_id), \
                    self.assertRaises((ValueError, season_package.SeasonPackageError)):
                self.seal(scope=scope, mode=mode, season_id=season_id)
        self.assertEqual(list(self.packages_dir.glob('*.tar.gz')), [])
        self.assertEqual(self.store.season_counts('test:server1', 'guild_war', '42')['runs'], 1)

    def test_isolated_cleanup_never_touches_other_seasons_or_modes(self):
        save_round(self.store, 'a1', pages=[page(membership=[member('1001')])], at='2026-10-20T00:00:00+00:00')
        save_round(self.store, 'b1', season_id='43', at='2026-10-20T00:30:00+00:00')
        save_round(self.store, 'c1', mode='illusion', at='2026-10-20T01:00:00+00:00')
        other_counts = {key: self.store.season_counts(*key) for key in [
            ('test:server1', 'guild_war', '43'), ('test:server1', 'illusion', '42')]}
        other_history = self.store.member_history('test:server1', 'guild_war', '43')
        self.seal(scope='test:server1', mode='guild_war', season_id='42')
        for key, counts in other_counts.items():
            self.assertEqual(self.store.season_counts(*key), counts)
        self.assertEqual(self.store.member_history('test:server1', 'guild_war', '43'), other_history)
        self.assertEqual(len(self.store.list_seasons()), 2)
        with closing(seasons.sqlite3.connect(self.path)) as db, db:
            self.assertGreater(db.execute('SELECT count(*) FROM raw_blobs').fetchone()[0], 0)

    def test_corrupted_package_is_refused_on_open(self):
        save_round(self.store, 'r1')
        entry = self.seal()
        package = self.package_path(entry)
        with package.open('ab') as handle:
            handle.write(b'garbage')
        with self.assertRaises(season_package.SeasonPackageError):
            self.packager.open_season('test:server1', 'guild_war', '42')

    def test_unknown_season_cannot_be_sealed_and_cleanup_is_idempotent(self):
        with self.assertRaises(season_package.SeasonPackageError):
            self.seal()
        save_round(self.store, 'r1')
        entry = self.seal()
        self.assertEqual(self.seal(), entry)
        self.assertEqual(self.packager.lookup('test:server1', 'guild_war', '42')['sha256'],
                         entry['sha256'])
        self.assertEqual(len(self.packager.list_sealed()), 1)

    def test_seal_after_seal_with_new_runtime_run_is_rejected_and_kept(self):
        save_round(self.store, 'r1')
        sealed = self.seal()
        save_round(self.store, 'r2', pages=[page(), page(index=1, entries=[entry(entity='9007199254740996', rank=2)])])
        with self.assertRaisesRegex(season_package.SeasonPackageError, 'not covered'):
            self.seal()
        counts = self.store.season_counts('test:server1', 'guild_war', '42')
        self.assertEqual(counts['runs'], 1)
        self.assertEqual(counts['rank_entries'], 2)
        self.assertEqual(len(self.store.snapshots()), 1)
        save_round(self.store, 'r3')
        self.assertEqual(self.store.season_counts('test:server1', 'guild_war', '42')['runs'], 2)
        with self.packager.open_season('test:server1', 'guild_war', '42') as package:
            self.assertEqual(package.manifest()['counts'], sealed['counts'])

    def test_recovery_rejects_uncovered_runtime_data_and_keeps_it(self):
        save_round(self.store, 'r1')
        stale_counts = self.store.season_counts('test:server1', 'guild_war', '42')
        save_round(self.store, 'r2', pages=[page(), page(index=1, entries=[entry(entity='9007199254740996', rank=2)])])
        self.packager._append_index({'scope': 'test:server1', 'mode': 'guild_war',
                                     'season_id': '42',
                                     'package': 'season__test_server1__guild_war__42.tar.gz',
                                     'sha256': 'pending', 'bytes': 0,
                                     'sealed_at_utc': '2026-10-20T00:00:00+00:00',
                                     'status': 'ARCHIVE_SEALED', 'counts': stale_counts})
        with self.assertRaisesRegex(season_package.SeasonPackageError, 'not covered'):
            self.seal()
        counts = self.store.season_counts('test:server1', 'guild_war', '42')
        self.assertEqual(counts['runs'], 2)
        self.assertEqual(counts['rank_entries'], 3)
        save_round(self.store, 'r3')
        self.assertEqual(self.store.season_counts('test:server1', 'guild_war', '42')['runs'], 3)

    def test_seal_rejects_count_race_after_lock_and_keeps_data(self):
        save_round(self.store, 'r1')
        real_counts = seasons.SeasonHistory.season_counts
        calls = {'seen': 0}

        def raced(history, scope, mode, season_id):
            calls['seen'] += 1
            current = real_counts(history, scope, mode, season_id)
            if calls['seen'] == 2:
                current = dict(current)
                current['runs'] += 1
            return current

        with mock.patch.object(seasons.SeasonHistory, 'season_counts', raced), \
                self.assertRaisesRegex(season_package.SeasonPackageError, 'changed'):
            self.seal()
        self.assertEqual(calls['seen'], 2)
        self.assertEqual(self.store.season_counts('test:server1', 'guild_war', '42')['runs'], 1)
        save_round(self.store, 'r2')
        self.assertEqual(self.store.season_counts('test:server1', 'guild_war', '42')['runs'], 2)

    def test_open_requires_matching_season_identity(self):
        save_round(self.store, 'r1')
        self.seal()
        with self.assertRaises(season_package.SeasonPackageError):
            self.packager.open_season('test:server1', 'guild_war', '43')


if __name__ == '__main__':
    unittest.main()
