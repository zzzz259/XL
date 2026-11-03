import importlib.util
import os
import tempfile
import unittest
from pathlib import Path


class CoreOutcomesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('server_app.portraits.core_outcomes'), 'durable journal API missing')
        from server_app.portraits import core_outcomes
        return core_outcomes

    def owner(self):
        fd = os.open(self.root / 'portrait_updater_owner.lock', os.O_CREAT | os.O_RDWR)
        self.addCleanup(os.close, fd)
        from server_app.portraits.publication import Publication
        owner = Publication(self.root, instance_id='test', source_id='test', cdn_base='https://example.test')
        owner._run_handle, owner._run_pid = fd, os.getpid()
        return owner

    def test_atomic_immutable_promotion_and_separate_late_links(self):
        m = self.module()
        journal = m.CoreJournal(self.owner())
        a = journal.begin_run('a', trigger='manual')
        a.bind_target(99, classification='processed', announcement_eligible=True)
        first = a.finish(phase='published', source_state='pending')
        b = journal.begin_run('b', trigger='scheduled')
        b.bind_target(1, classification='processed', announcement_eligible=True)
        second = b.finish(phase='published', source_state='pending')
        self.assertLess(first['promotion'], second['promotion'])
        self.assertEqual(first, a.finish(phase='published', source_state='pending'))
        with self.assertRaises(ValueError):
            a.finish(phase='failed', source_state='failed')
        reader = m.CoreReader(journal.path, scope=journal.scope)
        rows = reader.read_outcomes()
        self.assertEqual(2, len(rows))
        journal.fail_source('a', code='source_invalid')
        self.assertEqual([], reader.read_outcomes(after_sequence=rows[-1]['sequence']))
        self.assertEqual('a', reader.read_source_links()[0]['run_id'])
        self.assertEqual(second, reader.latest_promotion())

    def test_recovery_conservative_same_version_and_owner_required(self):
        m = self.module()
        owner = self.owner()
        journal = m.CoreJournal(owner)
        a = journal.begin_run('old', trigger='manual')
        a.bind_target(42, classification='processed', announcement_eligible=True)
        a.finish(phase='published', source_state='pending')
        b = journal.begin_run('new', trigger='manual')
        b.bind_target(42, classification='processed', announcement_eligible=True)
        owner._run_pid = -1
        with self.assertRaises(ValueError):
            journal.recover()
        owner._run_pid = os.getpid()
        journal.recover()
        reader = m.CoreReader(journal.path, scope=owner.scope)
        self.assertEqual('interrupted', reader.read_run('new')['phase'])
        self.assertIsNone(reader.read_run('new')['promotion'])
        self.assertEqual('42', reader.read_run('new')['version'])

    def test_readonly_identity_scope_byte_cap_and_missing(self):
        m = self.module()
        missing = self.root / 'missing.sqlite'
        with self.assertRaises(FileNotFoundError):
            m.CoreReader(missing, scope='a'*64).read_outcomes()
        self.assertFalse(missing.exists())
        journal = m.CoreJournal(self.owner())
        ctx = journal.begin_run('a', trigger='manual')
        ctx.finish(phase='interrupted', source_state='not_requested')
        reader = m.CoreReader(journal.path, scope=journal.scope)
        identity = reader.identity()
        with self.assertRaises(ValueError):
            m.CoreReader(journal.path, scope='b'*64).read_outcomes()
        with self.assertRaises(ValueError):
            m.CoreReader(journal.path, scope=journal.scope, journal_id='replaced').read_outcomes()
        with self.assertRaises(ValueError):
            reader.read_outcomes(max_bytes=1)
        self.assertEqual(identity, reader.identity())



class PipelineCoreTests(unittest.TestCase):
    setUp = CoreOutcomesTests.setUp
    module = CoreOutcomesTests.module
    owner = CoreOutcomesTests.owner
    def test_post_processor_failure_preserves_version_counts_and_closes(self):
        from unittest.mock import patch

        from server_app.pipeline import ProcessResult, UpdatePipeline
        m = self.module()
        owner = self.owner()
        journal = m.CoreJournal(owner)
        ctx = journal.begin_run('pipeline', trigger='manual')
        def processor(staging):
            ctx.bind_target(123, classification='processed', announcement_eligible=True)
            return ProcessResult(version_timestamp=123, processed=True, card_count=4)
        pipeline = UpdatePipeline(self.root, processor, publication=owner)
        with ctx.activate(), patch('server_app.pipeline.publish_version', side_effect=OSError('disk')):
            result = pipeline.run_once()
        self.assertEqual(123, result.version_timestamp)
        self.assertEqual(4, result.card_count)
        row = m.CoreReader(journal.path, scope=owner.scope).read_run('pipeline')
        self.assertEqual('failed', row['phase'])
        self.assertIsNone(row['promotion'])

    def test_core_committed_before_source_and_capture_missing_still_closes(self):
        from unittest.mock import patch

        from server_app.pipeline import ProcessResult, UpdatePipeline
        m = self.module()
        owner = self.owner()
        journal = m.CoreJournal(owner)
        ctx = journal.begin_run('pipeline', trigger='manual')
        ctx.bind_target(123, classification='processed', announcement_eligible=True)
        result = ProcessResult(version_timestamp=123, processed=True, schedule_enabled=False)
        pipeline = UpdatePipeline(self.root, lambda staging: result, publication=owner)
        with ctx.activate(), patch('server_app.pipeline.publish_version'), patch('server_app.pipeline.write_current_pointer'), patch.object(pipeline, '_sync_current_character_data'), patch.object(pipeline, '_sync_current_gacha_catalog', return_value='gacha warning'), patch.object(pipeline, '_carry_forward_cards'):
            returned = pipeline.run_once()
        self.assertIsNone(returned.error)
        row = m.CoreReader(journal.path, scope=owner.scope).read_run('pipeline')
        self.assertIsNotNone(row, 'core completion missing')
        self.assertEqual('published', row['phase'])
        self.assertEqual('pending', row['source_state'])
        self.assertEqual('failed', m.CoreReader(journal.path, scope=owner.scope).read_source_links()[0]['state'])
        self.assertEqual(1, row['promotion'])


class JournalHardeningTests(unittest.TestCase):
    setUp = CoreOutcomesTests.setUp
    module = CoreOutcomesTests.module
    owner = CoreOutcomesTests.owner

    def test_reject_duck_owner_and_malformed_row(self):
        import sqlite3
        from types import SimpleNamespace
        m = self.module()
        owner = self.owner()
        duck = SimpleNamespace(**owner.__dict__)
        with self.assertRaises(ValueError):
            m.CoreJournal(duck).begin_run('fake', trigger='manual')
        journal = m.CoreJournal(owner)
        journal.begin_run('real', trigger='manual').finish(phase='interrupted', source_state='not_requested')
        with sqlite3.connect(journal.path) as db:
            db.execute("UPDATE outcomes SET payload=?", ('{"scope":"'+owner.scope+'","run_id":"real"}',))
        db.close()
        with self.assertRaises(ValueError):
            m.CoreReader(journal.path, scope=owner.scope).read_outcomes()

    def test_bootstrap_once_noop_never_mints_and_context_resets(self):
        m = self.module()
        journal = m.CoreJournal(self.owner())
        ctx = journal.begin_run('bootstrap', trigger='manual')
        ctx.bind_target(42, classification='bootstrap', announcement_eligible=False)
        with ctx.activate():
            self.assertIs(ctx, m.current_run())
        self.assertIsNone(m.current_run())
        from unittest.mock import patch
        with patch.object(journal.publication, 'read_request', return_value={'version': 42}):
            first = ctx.finish(phase='no_change', source_state='pending', bootstrap_ref=object())
        self.assertEqual(1, first['promotion'])
        second = journal.begin_run('noop', trigger='scheduled')
        second.bind_target(42, classification='bootstrap', announcement_eligible=False)
        self.assertIsNone(second.finish(phase='no_change', source_state='pending')['promotion'])

class IntegrationTests(unittest.TestCase):
    setUp = CoreOutcomesTests.setUp
    module = CoreOutcomesTests.module
    owner = CoreOutcomesTests.owner

    def test_processor_events_are_bound_before_start(self):
        import json
        from types import SimpleNamespace
        from unittest.mock import patch

        from server_app.pipeline import ProcessResult
        from server_app.processor import ProductionUpdateProcessor
        m = self.module()
        owner = self.owner()
        journal = m.CoreJournal(owner)
        ctx = journal.begin_run('events', trigger='manual')
        processor = ProductionUpdateProcessor.__new__(ProductionUpdateProcessor)
        processor.config = SimpleNamespace(data_dir=self.root)
        processor.publication = owner
        processor.client = SimpleNamespace(fetch_update_info=lambda: SimpleNamespace(timestamp=123))
        def process(*args):
            lines = [json.loads(v) for v in (self.root / 'update_events.jsonl').read_text().splitlines()]
            self.assertEqual('events', lines[0]['run_id'])
            self.assertEqual('123', ctx.target_version())
            return ProcessResult(version_timestamp=123)
        with ctx.activate(), patch.object(processor, '_process', side_effect=process):
            processor(self.root)
        events = [json.loads(v) for v in (self.root / 'update_events.jsonl').read_text().splitlines()]
        self.assertEqual(['events', 'events'], [v['run_id'] for v in events])

    def test_journal_commit_failure_is_visible_not_synthetic_failed(self):
        from unittest.mock import patch

        from server_app.pipeline import ProcessResult, UpdatePipeline
        m = self.module()
        owner = self.owner()
        journal = m.CoreJournal(owner)
        ctx = journal.begin_run('commit', trigger='manual')
        ctx.bind_target(123, classification='noop', announcement_eligible=False)
        pipeline = UpdatePipeline(self.root, lambda _: ProcessResult(), publication=owner)
        original_finish = ctx.finish
        attempts = []
        def fail_once(**kwargs):
            attempts.append(1)
            if len(attempts) == 1:
                raise OSError('journal disk')
            return original_finish(**kwargs)
        with ctx.activate(), patch.object(ctx, 'finish', side_effect=fail_once), self.assertRaises(OSError):
            pipeline.run_once()


class DeferredBootstrapTests(unittest.TestCase):
    setUp = CoreOutcomesTests.setUp
    module = CoreOutcomesTests.module
    owner = CoreOutcomesTests.owner

    def test_unproved_bootstrap_does_not_mint(self):
        journal = self.module().CoreJournal(self.owner())
        ctx = journal.begin_run('bootstrap_pending', trigger='manual')
        ctx.bind_target(42, classification='bootstrap', announcement_eligible=False)
        self.assertIsNone(ctx.finish(phase='no_change', source_state='pending')['promotion'])

@unittest.skipUnless(os.name == 'posix', 'real Publication metadata durability requires Linux EXT4')
class NativeCoreBridgeTests(unittest.TestCase):
    setUp = CoreOutcomesTests.setUp

    def publication(self):
        from server_app.portraits.publication import Publication
        return Publication(self.root, instance_id='test', source_id='test', cdn_base='https://example.test')

    def processor(self, pub, version=123):
        import json

        from server_app.pipeline import ProcessResult
        from server_app.portraits.core_outcomes import current_run
        def process(staging):
            ctx = current_run()
            self.assertIsNotNone(ctx)
            ctx.bind_target(version, classification='processed', announcement_eligible=True)
            raw = json.dumps({'timestamp': version, 'data': [{'name': name, 'hash': char*32, 'size': 2, 'ver': 1} for name, char in [('Data', 'a'), ('Arts', 'b')]]}).encode()
            pin = pub.capture(raw, version=version, version_file='fixture.json', bootstrap=False)
            cards = pub.stage_cards(version, reason='no-new-output')
            (staging/'character_data').mkdir(parents=True, exist_ok=True)
            (staging/'character_data/current.json').write_text(json.dumps({'version': version, 'characters': {'1': {'name': 'A'}}, 'index': [], 'words': {}}))
            (staging/'manifest.json').write_text(json.dumps({'version': version, 'baseline': False}))
            return ProcessResult(version_timestamp=version, processed=True, portrait_pin=pin, card_intent=cards, schedule_enabled=False)
        return process

    def test_actual_manual_worker_context_and_ready_link(self):
        from server_app.pipeline import UpdatePipeline
        from server_app.portraits.core_outcomes import (
            CoreJournal,
            CoreReader,
            current_run,
        )
        from server_app.state import StateStore
        from server_app.update_coordinator import UpdateCoordinator
        from test_update_coordinator_unittest import make_config
        pub = self.publication()
        coordinator = UpdateCoordinator(make_config(), UpdatePipeline(self.root, self.processor(pub), publication=pub), StateStore(self.root/'state.sqlite'))
        job_id = coordinator.enqueue_manual()
        self.assertTrue(coordinator.wait_idle(10))
        self.assertIsNone(current_run())
        reader = CoreReader(CoreJournal(pub).path, scope=pub.scope)
        row = reader.read_run(job_id)
        self.assertEqual('published', row['phase'])
        self.assertEqual(1, row['promotion'])
        self.assertEqual(job_id, reader.read_source_links()[0]['run_id'])
        self.assertIsNone(pub._run_handle)

    def test_late_ready_discovery_keeps_original_token(self):
        from unittest.mock import patch

        from server_app.pipeline import UpdatePipeline
        from server_app.portraits.core_outcomes import CoreJournal, CoreReader
        pub = self.publication()
        self.assertTrue(pub.acquire_run())
        self.addCleanup(pub.release_run)
        pub.set_guard(True)
        journal = CoreJournal(pub)
        first = journal.begin_run('a', trigger='manual')
        with first.activate(), patch.object(pub, 'complete', side_effect=OSError('source deferred')):
            result = UpdatePipeline(self.root, self.processor(pub), publication=pub).run_once()
        self.assertIsNone(result.error)
        reader = CoreReader(journal.path, scope=pub.scope)
        self.assertEqual([], reader.read_source_links())
        second = journal.begin_run('b', trigger='manual')
        with second.activate():
            UpdatePipeline(self.root, self.processor(pub, version=124), publication=pub).run_once()
        cursor = reader.read_outcomes()[-1]['sequence']
        journal.link_source('a', pub.complete(123))
        self.assertEqual([], reader.read_outcomes(after_sequence=cursor))
        links = reader.read_source_links()
        self.assertEqual(['b', 'a'], [r['run_id'] for r in links])
        self.assertEqual([2, 1], [r['promotion'] for r in links])
        self.assertEqual('b', reader.latest_promotion()['run_id'])

    def test_required_fault_matrix_closed_without_promotion(self):
        from unittest.mock import patch

        from server_app.pipeline import UpdatePipeline
        from server_app.portraits.core_outcomes import CoreJournal, CoreReader
        pub = self.publication()
        self.assertTrue(pub.acquire_run())
        self.addCleanup(pub.release_run)
        journal = CoreJournal(pub)
        for index, target in enumerate(('publish_version', 'write_current_pointer', '_sync_current_character_data', '_carry_forward_cards')):
            with self.subTest(target=target):
                ctx = journal.begin_run(target, trigger='manual')
                pipeline = UpdatePipeline(self.root, self.processor(pub, version=200+index), publication=pub)
                fault = patch('server_app.pipeline.'+target, side_effect=OSError('required phase')) if not target.startswith('_') else patch.object(pipeline, target, side_effect=OSError('required phase'))
                with ctx.activate(), fault as injected:
                    result = pipeline.run_once()
                injected.assert_called_once()
                self.assertEqual(200+index, result.version_timestamp)
                row = CoreReader(journal.path, scope=pub.scope).read_run(target)
                self.assertEqual('failed', row['phase'])
                self.assertIsNone(row['promotion'])
        self.assertEqual([], CoreReader(journal.path, scope=pub.scope).read_source_links())

class ContextBoundaryTests(unittest.TestCase):
    setUp = CoreOutcomesTests.setUp
    module = CoreOutcomesTests.module
    owner = CoreOutcomesTests.owner

    def test_context_cannot_authorize_another_publication_object(self):
        from unittest.mock import Mock

        from server_app.pipeline import UpdatePipeline
        m = self.module()
        journal = m.CoreJournal(self.owner())
        ctx = journal.begin_run('scope', trigger='manual')
        processor = Mock()
        with ctx.activate(), self.assertRaises(ValueError):
            UpdatePipeline(self.root, processor, publication=self.owner()).run_once()
        processor.assert_not_called()
        with ctx.activate(), self.assertRaises(ValueError):
            UpdatePipeline(self.root/'other', processor, publication=journal.publication).run_once()
        processor.assert_not_called()
