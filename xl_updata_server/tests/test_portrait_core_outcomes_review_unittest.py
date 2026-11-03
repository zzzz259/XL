"""Independent-review regressions for the core journal contract."""
import json
import os
import shutil
import sqlite3
import unittest
from contextlib import ExitStack, closing
from types import SimpleNamespace
from unittest.mock import patch

import test_portrait_core_outcomes_unittest as core_fixture


class ReviewTests(unittest.TestCase):
    setUp = core_fixture.CoreOutcomesTests.setUp
    owner = core_fixture.CoreOutcomesTests.owner

    def journal(self):
        from server_app.portraits.core_outcomes import CoreJournal
        return CoreJournal(self.owner())

    def append(self, journal, run):
        ctx = journal.begin_run(run, trigger='manual')
        ctx.bind_target(123, classification='processed', announcement_eligible=True)
        ctx.finish(phase='published', source_state='pending')
        journal.fail_source(run, code='source_invalid')

    def test_persisted_stream_cursors_reject_inplace_backup_restore_and_regrowth(self):
        from server_app.portraits.core_outcomes import CoreReader
        journal = self.journal()
        self.append(journal, 'a')
        backup = self.root/'backup.sqlite'
        with closing(sqlite3.connect(journal.path)) as source, closing(sqlite3.connect(backup)) as target:
            source.backup(target)
        self.append(journal, 'b')
        reader = CoreReader(journal.path, scope=journal.scope)
        self.assertTrue(hasattr(reader, 'read_outcomes_page'), 'fenced page API missing')
        cursors = []
        for name in ('read_outcomes_page', 'read_source_links_page'):
            page = getattr(reader, name)()
            cursors.append(json.loads(json.dumps(page['cursor'])))
        # Same inode restore defeats a file-id-only fence. Same UUID survives backup.
        shutil.copyfile(backup, journal.path)
        self.append(journal, 'c')
        self.append(journal, 'd')
        for name, cursor in zip(('read_outcomes_page', 'read_source_links_page'), cursors):
            with self.subTest(stream=name), self.assertRaises(ValueError):
                getattr(CoreReader(journal.path, scope=journal.scope), name)(cursor=cursor)

    def test_fenced_cursor_allows_wal_append_and_checks_unconsumed_highwater(self):
        from server_app.portraits.core_outcomes import CoreReader
        journal = self.journal()
        self.append(journal, 'a')
        backup = self.root/'backup.sqlite'
        with closing(sqlite3.connect(journal.path)) as source, closing(sqlite3.connect(backup)) as target:
            source.backup(target)
        self.append(journal, 'b')
        reader = CoreReader(journal.path, scope=journal.scope)
        self.assertTrue(hasattr(reader, 'read_outcomes_page'), 'fenced page API missing')
        first = reader.read_outcomes_page(limit=1)
        self.append(journal, 'c')
        second = CoreReader(journal.path, scope=journal.scope).read_outcomes_page(cursor=json.loads(json.dumps(first['cursor'])))
        self.assertEqual(['b', 'c'], [r['run_id'] for r in second['items']])
        shutil.copyfile(backup, journal.path)
        self.append(journal, 'replacement')
        with self.assertRaises(ValueError):
            reader.read_outcomes_page(cursor=first['cursor'])

    def test_optional_resolution_storage_failure_preserves_ordinary_success(self):
        from server_app.pipeline import ProcessResult, UpdatePipeline
        from server_app.portraits.core_outcomes import CoreReader
        for kind in ('ready', 'failed', 'bootstrap'):
            with self.subTest(kind=kind):
                journal = self.journal()
                pub = journal.publication
                ctx = journal.begin_run(kind, trigger='manual')
                ctx.bind_target(123, classification='bootstrap' if kind == 'bootstrap' else 'processed', announcement_eligible=kind != 'bootstrap')
                pin = SimpleNamespace(pin_digest='a'*64)
                result = ProcessResult(version_timestamp=123, processed=kind != 'bootstrap', schedule_enabled=False,
                                       portrait_pin=pin if kind == 'ready' else None,
                                       portrait_ready_ref=pin if kind == 'bootstrap' else None)
                pipeline = UpdatePipeline(self.root, lambda _, result=result: result, publication=pub)
                with ExitStack() as stack:
                    stack.enter_context(ctx.activate())
                    for target in ('publish_version', 'write_current_pointer'):
                        stack.enter_context(patch('server_app.pipeline.'+target))
                    for target in ('_sync_current_character_data', '_carry_forward_cards'):
                        stack.enter_context(patch.object(pipeline, target))
                    stack.enter_context(patch.object(pipeline, '_sync_current_gacha_catalog', return_value=None))
                    stack.enter_context(patch.object(pub, 'attest_published'))
                    stack.enter_context(patch.object(pub, 'complete', return_value=pin))
                    stack.enter_context(patch.object(pub, 'read_request', return_value={'version': 123}))
                    stack.enter_context(patch.object(journal, 'fail_source' if kind == 'failed' else 'link_source', side_effect=OSError('private source storage failure')))
                    returned = pipeline.run_once()
                self.assertIsNone(returned.error)
                self.assertIn('portrait: request deferred', returned.warnings)
                self.assertNotIn('private', str(returned.warnings))
                self.assertIn(CoreReader(journal.path, scope=journal.scope).read_run(kind)['phase'], ('published', 'no_change'))

    def test_reader_rejects_malformed_ready_ref_cards_and_sql_promotion(self):
        from server_app.portraits.core_outcomes import CoreReader
        journal = self.journal()
        self.append(journal, 'a')
        reader = CoreReader(journal.path, scope=journal.scope)
        from copy import deepcopy

        from server_app.portraits.core_outcomes import _chain
        pin, sha = 'a'*64, 'b'*64
        base = {'run_id': 'a', 'scope': journal.scope, 'promotion': 1, 'state': 'ready',
                'request_id': 'c'*64, 'pin_digest': pin, 'cards': {'kind': 'none', 'reason': 'baseline'},
                'ref': {'scope_key': journal.scope, 'pin_digest': pin, 'size': 10, 'sha256': sha,
                        'locator': f'metadata/{journal.scope}/{pin}/{sha}.jsonbytes'}}
        def install(value):
            raw = json.dumps(value)
            with closing(sqlite3.connect(journal.path)) as db:
                db.execute('UPDATE source_links SET payload=?,prefix=?', (raw, _chain('0'*64, 1, raw)))
                db.commit()
        install(base)
        self.assertEqual('ready', reader.read_source_links()[0]['state'])
        variants = []
        for key in ('request_id', 'pin_digest', 'ref', 'cards'):
            value = deepcopy(base)
            value[key] = None
            variants.append(value)
        for key, bad in (('scope_key', 'd'*64), ('pin_digest', 'd'*64), ('size', True), ('locator', 'wrong')):
            value = deepcopy(base)
            value['ref'][key] = bad
            variants.append(value)
        value = deepcopy(base)
        del value['ref']['sha256']
        variants.append(value)
        for cards in ({'kind': 'none'}, {'kind': 'none', 'reason': None}, {'kind': 'batch'}, {'kind': 'handoff_unresolved'}):
            value = deepcopy(base)
            value['cards'] = cards
            variants.append(value)
        for index, value in enumerate(variants):
            with self.subTest(malformed=index):
                install(value)
                with self.assertRaises(ValueError):
                    reader.read_source_links()
        with closing(sqlite3.connect(journal.path)) as db:
            db.execute('UPDATE outcomes SET promotion=99')
            db.commit()
        with self.assertRaises(ValueError):
            reader.read_outcomes()

    def test_fenced_cursor_accepts_live_wal_but_rejects_same_uuid_file_replacement(self):
        from server_app.portraits.core_outcomes import CoreReader
        journal = self.journal()
        self.append(journal, 'a')
        reader = CoreReader(journal.path, scope=journal.scope)
        pages = [reader.read_outcomes_page(), reader.read_source_links_page()]
        with closing(sqlite3.connect(journal.path)) as held:
            held.execute('BEGIN')
            held.execute('SELECT COUNT(*) FROM outcomes').fetchone()
            self.append(journal, 'b')
            for name, page in zip(('read_outcomes_page', 'read_source_links_page'), pages):
                self.assertEqual(['b'], [row['run_id'] for row in getattr(reader, name)(cursor=page['cursor'])['items']])
        replacement = self.root/'replacement.sqlite'
        with closing(sqlite3.connect(journal.path)) as source, closing(sqlite3.connect(replacement)) as target:
            source.backup(target)
        os.replace(replacement, journal.path)
        for name, page in zip(('read_outcomes_page', 'read_source_links_page'), pages):
            with self.assertRaises(ValueError):
                getattr(CoreReader(journal.path, scope=journal.scope), name)(cursor=page['cursor'])

    def test_cursor_schema_is_strict_and_late_link_does_not_invalidate_core_cursor(self):
        from server_app.portraits.core_outcomes import CoreReader
        journal = self.journal()
        ctx = journal.begin_run('late', trigger='manual')
        ctx.bind_target(123, classification='processed', announcement_eligible=True)
        ctx.finish(phase='published', source_state='pending')
        reader = CoreReader(journal.path, scope=journal.scope)
        core = reader.read_outcomes_page()['cursor']
        source = reader.read_source_links_page()['cursor']
        malformed = dict(core, schema=True)
        with self.assertRaises(ValueError):
            reader.read_outcomes_page(cursor=malformed)
        journal.fail_source('late', code='source_invalid')
        self.assertEqual([], reader.read_outcomes_page(cursor=core)['items'])
        self.assertEqual('late', reader.read_source_links_page(cursor=source)['items'][0]['run_id'])

    def test_reader_rejects_malformed_journal_incarnation(self):
        from server_app.portraits.core_outcomes import CoreReader
        journal = self.journal()
        self.append(journal, 'a')
        with closing(sqlite3.connect(journal.path)) as db:
            db.execute('UPDATE identity SET journal_id=NULL')
            db.commit()
        with self.assertRaises(ValueError):
            CoreReader(journal.path, scope=journal.scope).read_outcomes_page()


@unittest.skipUnless(os.name == 'posix', 'real directory fsync requires Linux')
class DirectoryDurabilityTests(unittest.TestCase):
    setUp = ReviewTests.setUp
    owner = ReviewTests.owner
    journal = ReviewTests.journal
    def test_first_and_existing_parent_flush_failure_blocks_begin_until_healed(self):
        from server_app.portraits import source_io
        journal = self.journal()
        real = source_io._sync_directory
        visits = []
        def fail_parent(directory):
            if directory[0] == self.root:
                visits.append(directory[0])
                raise OSError('ancestor flush')
            return real(directory)
        for _ in range(2):
            with patch.object(source_io, '_sync_directory', side_effect=fail_parent), self.assertRaises(OSError):
                journal.begin_run('a', trigger='manual')
        self.assertEqual(2, len(visits))
        with patch.object(source_io, '_sync_directory', wraps=real) as sync:
            journal.begin_run('a', trigger='manual')
        self.assertIn(self.root, [call.args[0][0] for call in sync.call_args_list])
        self.assertIn(journal.path.parent, [call.args[0][0] for call in sync.call_args_list])

    def test_leaf_barrier_precedes_terminal_visibility_and_retry_heals(self):
        from server_app.portraits import source_io
        from server_app.portraits.core_outcomes import CoreReader
        journal = self.journal()
        ctx = journal.begin_run('a', trigger='manual')
        ctx.bind_target(123, classification='processed', announcement_eligible=True)
        real = source_io._sync_directory
        observations = []
        def fail_leaf(directory):
            if directory[0] == journal.path.parent:
                observations.append(CoreReader(journal.path, scope=journal.scope).read_run('a'))
                raise OSError('leaf entry barrier')
            return real(directory)
        for _ in range(2):
            with patch.object(source_io, '_sync_directory', side_effect=fail_leaf), self.assertRaises(OSError):
                ctx.finish(phase='published', source_state='pending')
            self.assertIsNone(CoreReader(journal.path, scope=journal.scope).read_run('a'))
        self.assertEqual([None, None], observations)
        outcome = ctx.finish(phase='published', source_state='pending')
        self.assertEqual(1, outcome['promotion'])
        with patch.object(source_io, '_sync_directory', side_effect=fail_leaf), self.assertRaises(OSError):
            journal.fail_source('a', code='source_invalid')
        self.assertEqual([], CoreReader(journal.path, scope=journal.scope).read_source_links())
        journal.fail_source('a', code='source_invalid')
        self.assertEqual(1, len(CoreReader(journal.path, scope=journal.scope).read_source_links()))


    def test_first_leaf_barrier_failure_does_not_publish_schema_or_run(self):
        from server_app.portraits import source_io
        journal = self.journal()
        real = source_io._sync_directory
        def fail_leaf(directory):
            if directory[0] == journal.path.parent:
                # SQLite has created DB/WAL paths, but schema and run are uncommitted.
                self.assertTrue(journal.path.exists())
                self.assertTrue(self.root.joinpath('portrait_sources', journal.scope, 'core_outcomes.sqlite-wal').exists())
                with closing(sqlite3.connect(journal.path.as_uri()+'?mode=ro', uri=True)) as reader:
                    self.assertEqual([], reader.execute("SELECT name FROM sqlite_master WHERE name='runs'").fetchall())
                raise OSError('initial DB entry flush')
            return real(directory)
        with patch.object(source_io, '_sync_directory', side_effect=fail_leaf), self.assertRaises(OSError):
            journal.begin_run('initial', trigger='manual')
        journal.begin_run('initial', trigger='manual')

    def test_real_batch_ready_link_preserves_native_card_shapes(self):
        from server_app.portraits.core_outcomes import CoreJournal, CoreReader
        from server_app.portraits.publication import Publication
        pub = Publication(self.root, instance_id='test', source_id='test', cdn_base='https://example.test')
        self.assertTrue(pub.acquire_run())
        self.addCleanup(pub.release_run)
        journal = CoreJournal(pub)
        ctx = journal.begin_run('batch', trigger='manual')
        ctx.bind_target(123, classification='processed', announcement_eligible=True)
        raw = json.dumps({'timestamp': 123, 'data': [{'name': name, 'hash': char*32, 'size': 2, 'ver': 1} for name, char in [('Data', 'a'), ('Arts', 'b')]]}).encode()
        pin = pub.capture(raw, version=123, version_file='fixture.json', bootstrap=False)
        def write(relative, value):
            path = self.root/relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(value))
        snapshot = {'version': 123, 'characters': {'1': {'name': 'A'}}, 'index': [], 'words': {}}
        write('character_data/current.json', snapshot)
        write('versions/123/character_data/current.json', snapshot)
        write('versions/123/manifest.json', {'version': 123})
        write('current_version.json', {'version': 123})
        write('outbox/123/manifest.json', {'version': 123, 'characters': [{'character_id': '1', 'filename': '1.png'}]})
        (self.root/'outbox/123/1.png').write_bytes(b'local-metadata-hash-fixture')
        cards = pub.stage_cards(123, reason='no-new-output', expected_batch=True)
        ctx.finish(phase='published', source_state='pending', cards=cards, pin_digest=pin.pin_digest)
        pub.attest_published(123)
        ref = pub.complete(123)
        journal.link_source('batch', ref)
        reader = CoreReader(journal.path, scope=journal.scope)
        self.assertEqual(cards, reader.read_source_links_page()['items'][0]['cards'])
        self.assertEqual(cards, reader.read_outcomes_page()['items'][0]['cards'])

    def test_optional_link_failure_keeps_coordinator_job_succeeded(self):
        from server_app.pipeline import UpdatePipeline
        from server_app.portraits.core_outcomes import CoreJournal, CoreReader
        from server_app.portraits.publication import Publication
        from server_app.state import StateStore
        from server_app.update_coordinator import UpdateCoordinator
        from test_update_coordinator_unittest import make_config
        pub = Publication(self.root, instance_id='test', source_id='test', cdn_base='https://example.test')
        process = core_fixture.NativeCoreBridgeTests.processor(self, pub)
        coordinator = UpdateCoordinator(make_config(), UpdatePipeline(self.root, process, publication=pub), StateStore(self.root/'state.sqlite'))
        with patch.object(CoreJournal, 'link_source', side_effect=OSError('link unavailable')):
            result = coordinator.run_controlled_once()
        self.assertIsNone(result.error)
        self.assertEqual('succeeded', coordinator.job_store.latest()['status'])
        reader = CoreReader(CoreJournal(pub).path, scope=pub.scope)
        self.assertEqual('published', reader.read_outcomes()[0]['phase'])
        self.assertEqual([], reader.read_source_links())
