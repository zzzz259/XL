import importlib.util
import json
import tempfile
import unittest
from datetime import UTC
from pathlib import Path


def run_owned(pipeline):
    """Exercise enabled direct fixtures through the actual updater owner boundary."""
    import uuid

    from server_app.portraits.core_outcomes import CoreJournal
    pub = pipeline.publication
    if pub is None:
        return pipeline.run_once()
    if not pub.acquire_run():
        raise AssertionError('fixture updater owner busy')
    try:
        pub.set_guard(True)
        journal = CoreJournal(pub)
        journal.recover()
        context = journal.begin_run(str(uuid.uuid4()), trigger='manual')
        with context.activate():
            return pipeline.run_once()
    finally:
        pub.set_guard(False)
        pub.release_run()


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('server_app.portraits.publication'),
                             'post-core publication handoff is missing')
        from server_app.portraits.publication import Publication
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.pub = Publication(self.root, instance_id='test', source_id='fixed', cdn_base='https://fixture.invalid/Android/Bundles')
        self.raw = json.dumps({'timestamp': 123, 'data': [
            {'name': name, 'hash': char*32, 'size': 2, 'ver': 1}
            for name, char in [('Data', 'a'), ('Arts', 'b')]]}).encode()

    def write(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding='utf-8')

    def core(self, legacy=False):
        snapshot = {'version': 123, 'characters': {'1': {'name': 'A'}}, 'index': [], 'words': {}}
        self.write('character_data/current.json', snapshot)
        if not legacy:
            self.write('current_version.json', {'version': 123})
            self.write('versions/123/character_data/current.json', snapshot)
            self.write('versions/123/manifest.json', {'version': 123, 'baseline': True})

    def test_capture_is_not_authority_and_recovery_is_exact(self):
        pin = self.pub.capture(self.raw, version=123, version_file='original.json')
        self.assertEqual(self.pub.list_requests(), [])
        with self.assertRaises((ValueError, OSError)):
            self.pub.complete(123)
        self.core()
        ref = self.pub.complete(123)
        request = self.pub.read_request(ref)
        self.assertEqual(request['pin']['version_file'], 'original.json')
        self.assertEqual(request['core']['kind'], 'published-version')
        self.assertEqual(request['pin']['pin_digest'], pin.pin_digest)
        self.assertEqual(self.pub.complete(123), ref)
        self.write('current_version.json', {'version': 124})
        self.assertEqual(self.pub.complete(123), ref)
        self.assertEqual(len(self.pub.list_requests(limit=1)), 1)
        self.assertEqual(self.pub.list_requests(after=123), [])

    def test_legacy_snapshot_has_explicit_proof(self):
        self.core(legacy=True)
        self.pub.capture(self.raw, version=123, version_file='original.json')
        value = self.pub.read_request(self.pub.complete(123))
        self.assertEqual(value['core']['kind'], 'legacy-snapshot')
        self.assertFalse((self.root/'versions').exists())
        self.assertFalse((self.root/'update_events.jsonl').exists())

    def test_mismatch_and_scope_and_tamper_rejected(self):
        self.core()
        self.pub.capture(self.raw, version=123, version_file='original.json')
        self.write('versions/123/manifest.json', {'version': 999})
        with self.assertRaises(ValueError): self.pub.complete(123)
        self.assertEqual(self.pub.list_requests(), [])
        self.write('versions/123/manifest.json', {'version': 123})
        ref = self.pub.complete(123)
        from server_app.portraits.publication import Publication
        other = Publication(self.root, instance_id='other', source_id='fixed', cdn_base=self.pub.cdn_base)
        with self.assertRaises(ValueError): other.read_request(ref)
        (self.pub.root/ref.locator).write_bytes(b'{}')
        with self.assertRaises(ValueError): self.pub.read_request(ref)

    def test_conflicting_version_pin_is_not_replaced(self):
        self.pub.capture(self.raw, version=123, version_file='original.json')
        with self.assertRaises(ValueError):
            self.pub.capture(self.raw+b' ', version=123, version_file='original.json')

    def test_request_write_crash_recovers_from_frozen_core(self):
        from unittest.mock import patch
        self.core()
        self.pub.capture(self.raw, version=123, version_file='original.json')
        with patch.object(self.pub, '_write_request', side_effect=OSError('injected')), self.assertRaises(OSError):
            self.pub.complete(123)
        self.write('current_version.json', {'version': 124})
        self.assertEqual(self.pub.read_request(self.pub.complete(123))['version'], '123')

    def test_cards_exact_manifest_without_delivery_claim(self):
        self.core()
        self.write('outbox/123/manifest.json', {'version': 123, 'characters': [{'character_id': '1', 'filename': '1.png'}]})
        (self.root/'outbox/123/1.png').write_bytes(b'fixture')
        self.pub.capture(self.raw, version=123, version_file='original.json')
        self.pub.stage_cards(123, reason='no-new-output')
        value = self.pub.read_request(self.pub.complete(123))
        self.assertEqual(value['cards']['kind'], 'batch')
        self.assertEqual(value['cards']['keys'], ['game-card:123:1:1.png'])
        self.assertNotIn('delivered', value['cards'])
        self.assertTrue((self.root/'outbox/123/1.png').exists())



class IntegrationTests(unittest.TestCase):
    def test_pipeline_only_completes_after_mirrors_and_warning_does_not_fail_core(self):
        from unittest.mock import Mock

        from server_app.pipeline import ProcessResult, UpdatePipeline
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            from server_app.portraits.publication import Publication
            pub = Publication(root, instance_id='test', source_id='fixed', cdn_base='https://fixture.invalid')
            raw = json.dumps({'timestamp': 123, 'data': [{'name': name, 'hash': char*32, 'size': 2, 'ver': 1} for name, char in [('Data', 'a'), ('Arts', 'b')]]}).encode()
            pin = pub.capture(raw, version=123, version_file='fixture.json', bootstrap=False)
            pub.attest_published = Mock()
            def process(staging):
                (staging/'character_data/current.json').write_text('{}')
                return ProcessResult(processed=True, version_timestamp=123, schedule_enabled=False, portrait_pin=pin)
            pipeline = UpdatePipeline(root, process, publication=pub)
            def complete(version, **kwargs):
                self.assertTrue((root/'current_version.json').is_file())
                self.assertTrue((root/'character_data/current.json').is_file())
                self.assertTrue((root/'versions/123/character_cards').is_dir())
                raise OSError('secret must not reach status')
            pub.complete = Mock(side_effect=complete)
            result = run_owned(pipeline)
            self.assertTrue(result.processed)
            self.assertIsNone(result.error)
            self.assertIn('portrait: request deferred', result.warnings)
            self.assertNotIn('secret', str(result.warnings))

    def test_default_off_has_no_publication(self):
        from server_app.pipeline import UpdatePipeline
        self.assertIsNone(UpdatePipeline('unused').publication)

    def test_guard_policy_has_explicit_paths(self):
        from server_app.portraits.executor import ResourcePolicy
        self.assertIn('updater_status_paths', ResourcePolicy.__dataclass_fields__)
        self.assertNotIn('updater_guard_paths', ResourcePolicy.__dataclass_fields__)

class ProcessorTests(PublicationTests):
    def processor(self):
        from types import SimpleNamespace

        from server_app.processor import ProductionUpdateProcessor
        config = SimpleNamespace(data_dir=self.root, instance_id='test', portrait_source_id='fixed',
                                 cdn_base=self.pub.cdn_base, portrait_sources_enabled=True,
                                 rerun_schedule_enabled=False, render_cards=False,
                                 assetbundle_key=b'', java_bin='unused', unluac_jar=Path('unused'), unluac_opmap=Path('unused'))
        processor = ProductionUpdateProcessor(config)
        from unittest.mock import Mock

        from server_app.cdn import UpdateInfo
        processor.client = Mock()
        processor.client.fetch_update_info.return_value = UpdateInfo(123, 'original.json')
        processor.client.fetch_version_bytes.return_value = self.raw
        return processor

    def test_both_noops_bootstrap_once_without_finish(self):
        for legacy in (False, True):
            with self.subTest(legacy=legacy):
                self.core(legacy=legacy)
                if legacy: (self.root/'current_version.json').unlink(missing_ok=True)
                processor = self.processor()
                from server_app.pipeline import UpdatePipeline
                result = run_owned(UpdatePipeline(self.root, processor))
                self.assertFalse(result.processed)
                self.assertFalse(result.warnings)
                self.assertFalse((self.root/'update_events.jsonl').exists())
                self.assertEqual(len(self.pub.list_requests()), 1)
                run_owned(UpdatePipeline(self.root, processor))
                self.assertLessEqual(processor.client.fetch_version_bytes.call_count, 1)

    def test_actual_data_path_survives_strict_pin_failure_single_fetch(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        processor = self.processor()
        processor.client.fetch_version_bytes.return_value = json.dumps({'timestamp':123,'data':[{'name':'Data','hash':'a'*32,'size':2,'ver':1}]}).encode()
        import server_app.processor as module
        from server_app.pipeline import UpdatePipeline
        with patch.object(module, 'read_catalog', return_value=object()), \
             patch.object(module, 'select_lua_assets', return_value=[]), \
             patch.object(module, 'bundle_hashes_for_assets', return_value=('a'*32,)), \
             patch.object(processor, '_download_hashes', return_value=()), \
             patch.object(module, 'extract_lua_files'), \
             patch.object(module, 'decode_lua_directory', return_value=SimpleNamespace(failed=0, success=1)), \
             patch.object(module, 'parse_character_snapshot', return_value={'characters': {'1': {'name':'A'}}, 'index':[], 'words':{}}), \
             patch.object(module, '_attempt_rerun_schedule', return_value=(None, ())), \
             patch.object(module, 'stage_gacha_catalog', return_value=()):
            result = run_owned(UpdatePipeline(self.root, processor))
        self.assertTrue(result.processed, result.error)
        self.assertEqual(processor.client.fetch_version_bytes.call_count, 1)
        processor.client.fetch_version_catalog.assert_not_called()
        self.assertIn('portrait: invalid or unavailable source pin', result.warnings)
        self.assertEqual(self.pub.list_requests(), [])
        self.assertTrue((self.root/'current_version.json').exists())

    def test_guard_defers_old_work_after_legacy_finish(self):
        from unittest.mock import patch

        from server_app.portraits.executor import (
            Deferred,
            ResourcePolicy,
            check_admission,
        )
        self.assertTrue(self.pub.acquire_run())
        self.addCleanup(self.pub.release_run)
        self.pub.set_guard(True)
        self.write('update_status.json', {'updating': False})
        policy = ResourcePolicy(lock_path=self.root/'slot', updater_status_paths=(self.pub.guard_path, self.root/'update_status.json'), disk_roots=(self.root,))
        with patch('server_app.portraits.executor._meminfo', return_value={'MemAvailable':2**40}), \
             patch('server_app.portraits.executor._disk_free', return_value=2**40):
            with self.assertRaises(Deferred): check_admission(policy)
            self.pub.set_guard(False)
            check_admission(policy)
        self.assertFalse((self.root/'update_events.jsonl').exists())

    def test_actual_success_pin_and_finish_precedes_authority(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        processor = self.processor()
        import server_app.processor as module
        from server_app.pipeline import UpdatePipeline
        original = processor._write_update_status
        observed = []
        def status(**kwargs):
            original(**kwargs)
            if not kwargs['updating']:
                observed.append(self.pub.list_requests())
        with patch.object(module, 'read_catalog', return_value=object()), \
             patch.object(module, 'select_lua_assets', return_value=[]), \
             patch.object(module, 'bundle_hashes_for_assets', return_value=('a'*32,)), \
             patch.object(processor, '_download_hashes', return_value=()), \
             patch.object(module, 'extract_lua_files'), \
             patch.object(module, 'decode_lua_directory', return_value=SimpleNamespace(failed=0, success=1)), \
             patch.object(module, 'parse_character_snapshot', return_value={'characters': {'1': {'name':'A'}}, 'index':[], 'words':{}}), \
             patch.object(module, '_attempt_rerun_schedule', return_value=(None, ())), \
             patch.object(module, 'stage_gacha_catalog', return_value=()), \
             patch.object(processor, '_write_update_status', side_effect=status):
            result = run_owned(UpdatePipeline(self.root, processor))
        self.assertTrue(result.processed, result.error)
        self.assertEqual(observed, [[]])
        request = self.pub.read_request(self.pub.list_requests()[0])
        import hashlib
        self.assertEqual(request['pin']['raw']['sha256'], hashlib.sha256(self.raw).hexdigest())
        self.assertEqual(request['cards'], {'kind':'none', 'reason':'baseline'})
        processor.client.fetch_version_catalog.assert_not_called()
        self.assertEqual(processor.client.fetch_version_bytes.call_count, 1)

    def test_coordinator_holds_guard_through_publication_and_overlap(self):
        import threading
        from datetime import datetime
        from types import SimpleNamespace
        from unittest.mock import patch

        from server_app.pipeline import ProcessResult, UpdatePipeline
        from server_app.portraits.executor import (
            Deferred,
            ResourcePolicy,
            check_admission,
        )
        from server_app.state import StateStore
        from server_app.update_coordinator import UpdateCoordinator
        entered, release = threading.Event(), threading.Event()
        def process(staging):
            self.write('update_status.json', {'updating':False})  # legacy processor finish
            return ProcessResult(processed=True, version_timestamp=123, schedule_enabled=False)
        pipeline = UpdatePipeline(self.root, process, publication=self.pub)
        def hold(version):
            entered.set()
            release.wait(5)
        pipeline._carry_forward_cards = hold
        config = SimpleNamespace(environment='test', instance_id='test', cdn_poll_enabled=True,
                                 normal_interval_seconds=1, burst_interval_seconds=1, burst_duration_seconds=1,
                                 burst_anchor=datetime(2026,1,1,tzinfo=UTC))
        coordinator = UpdateCoordinator(config, pipeline, StateStore(self.root/'state.sqlite'))
        policy = ResourcePolicy(lock_path=self.root/'slot', updater_status_paths=(self.pub.guard_path, self.root/'update_status.json'), disk_roots=(self.root,))
        job = coordinator.enqueue_manual()
        try:
            self.assertIsNotNone(job)
            self.assertTrue(entered.wait(3))
            self.assertIsNone(coordinator.enqueue_manual())
            self.assertIsNone(coordinator.run_scheduled_if_due(datetime.now(UTC)))
            with self.assertRaises(Deferred): check_admission(policy)
        finally:
            release.set()
            self.assertTrue(coordinator.wait_idle(5))
        with patch('server_app.portraits.executor._meminfo', return_value={'MemAvailable':2**40}), \
             patch('server_app.portraits.executor._disk_free', return_value=2**40):
            check_admission(policy)
        self.assertFalse((self.root/'update_events.jsonl').exists())

    def test_invalid_pending_card_evidence_never_becomes_no_card(self):
        self.core()
        self.pub.capture(self.raw, version=123, version_file='original.json', bootstrap=False)
        with self.assertRaises(ValueError): self.pub.complete(123)
        self.assertEqual(self.pub.list_requests(), [])

    def test_invalid_legacy_core_does_not_fetch_or_authorize(self):
        self.write('character_data/current.json', {'version':123, 'characters':{}, 'index':[], 'words':{}})
        processor = self.processor()
        from server_app.pipeline import UpdatePipeline
        result = run_owned(UpdatePipeline(self.root, processor))
        self.assertFalse(result.processed)
        self.assertTrue(result.warnings)
        processor.client.fetch_version_bytes.assert_not_called()
        self.assertEqual(self.pub.list_requests(), [])
        self.assertEqual(self.pub.read_failure(123)['state'], 'failed')

    def test_symlink_and_raw_tamper_fail_closed(self):
        from server_app.portraits.source_io import MetadataRef
        self.core()
        self.pub.capture(self.raw, version=123, version_file='original.json')
        rawref = MetadataRef(**self.pub._record('pins',123)['raw'])
        rawpath = self.pub.root/rawref.locator
        rawpath.write_bytes(self.raw+b' ')
        with self.assertRaises(ValueError): self.pub.complete(123)
        rawpath.write_bytes(self.raw)
        manifest = self.root/'versions/123/manifest.json'
        manifest.rename(manifest.with_suffix('.original'))
        manifest.symlink_to(manifest.with_suffix('.original'))
        with self.assertRaises((OSError, ValueError)): self.pub.complete(123)
        self.assertEqual(self.pub.list_requests(), [])

    def test_bounded_page_and_malformed_index_fail_closed(self):
        self.core()
        self.pub.capture(self.raw, version=123, version_file='original.json')
        self.pub.complete(123)
        with self.assertRaises(ValueError): self.pub.list_requests(limit=101)
        with self.assertRaises(ValueError): self.pub.list_requests(after=-1)
        from unittest.mock import patch
        with patch('os.scandir', side_effect=AssertionError('no history scans')):
            self.assertEqual(len(self.pub.list_requests(limit=1)), 1)
        with self.pub._index(write=True) as db:
            db.execute("UPDATE ready SET reference='{}'")
        with self.assertRaises((ValueError, TypeError)): self.pub.list_requests()

    def test_no_current_authority_does_not_repromote_pending_old_version(self):
        self.core()
        self.pub.capture(self.raw, version=123, version_file='original.json')
        self.write('current_version.json', {'version':124})
        self.assertEqual(self.pub.reconcile(), [(1,123,'failed')])
        self.assertEqual(json.loads((self.root/'current_version.json').read_text())['version'],124)
        self.assertEqual(self.pub.list_requests(), [])

    def test_card_manifest_is_frozen_before_watcher_can_observe_it(self):
        from types import SimpleNamespace
        processor = self.processor()
        from server_app.processor import _write_outbox
        self.pub.capture(self.raw, version=123, version_file='original.json', bootstrap=False)
        image = self.root/'角色_长图.png'
        image.write_bytes(b'fixture-not-decoded')
        observations = []
        def freeze(raw):
            observations.append((self.root/'outbox/123/manifest.json').exists())
            self.pub.stage_cards(123, reason='no-new-output', manifest_bytes=raw)
        _write_outbox(self.root, 123, SimpleNamespace(character_outputs=(('1', image),)),
                      {'1': {'name':'A'}}, {'1'}, before_publish=freeze)
        self.assertEqual(observations, [False])
        import hashlib
        evidence = self.pub._record('cards',123)
        self.assertEqual(evidence['manifest_sha256'], hashlib.sha256((self.root/'outbox/123/manifest.json').read_bytes()).hexdigest())
        self.assertIsNotNone(processor.publication)

    def test_late_recovery_has_monotonic_ready_sequence(self):
        from unittest.mock import patch
        self.core()
        self.pub.capture(self.raw, version=123, version_file='original.json')
        with patch.object(self.pub, '_write_request', side_effect=OSError('crash')), self.assertRaises(OSError):
            self.pub.complete(123)
        raw_b = json.loads(self.raw)
        raw_b['timestamp'] = 124
        self.pub.capture(json.dumps(raw_b).encode(), version=124, version_file='new.json')
        snapshot = {'version':124,'characters':{'1':{'name':'B'}},'index':[],'words':{}}
        self.write('current_version.json', {'version':124})
        self.write('character_data/current.json', snapshot)
        self.write('versions/124/character_data/current.json', snapshot)
        self.write('versions/124/manifest.json', {'version':124})
        self.pub.complete(124)
        first = self.pub.list_requests(limit=1)[0]
        self.assertEqual(first.version, '124')
        self.pub.complete(123)
        late = self.pub.list_requests(after=first.sequence, limit=1)
        self.assertEqual([entry.version for entry in late], ['123'])
        self.assertGreater(late[0].sequence, first.sequence)
        self.assertEqual(self.pub.list_requests(after=late[0].sequence), [])

    def test_request_stored_before_discovery_registration_recovers(self):
        from unittest.mock import patch
        self.core()
        self.pub.capture(self.raw, version=123, version_file='original.json')
        with patch.object(self.pub, '_register_ready', side_effect=OSError('crash')), self.assertRaises(OSError):
            self.pub.complete(123)
        self.assertEqual(self.pub.list_requests(), [])
        self.assertEqual(self.pub.reconcile(), [(1,123,'ready')])
        self.assertEqual(len(self.pub.list_requests()), 1)
        first = self.pub.list_requests()[0]
        self.pub.complete(123)
        self.assertEqual(self.pub.list_requests()[0].sequence, first.sequence)

    def test_pin_index_intent_recovers_before_pin_pointer(self):
        from unittest.mock import patch
        self.core()
        original = self.pub._put
        def crash(kind, version, value):
            if kind == 'pins': raise OSError('crash')
            return original(kind, version, value)
        with patch.object(self.pub, '_put', side_effect=crash), self.assertRaises(OSError):
            self.pub.capture(self.raw, version=123, version_file='original.json')
        self.assertEqual(self.pub.reconcile(), [(1,123,'ready')])
        self.assertEqual(self.pub.load_pin(123).raw_version_bytes, self.raw)

    def test_failed_final_core_phase_never_authorizes_recovery_or_noop(self):
        from unittest.mock import patch

        from server_app.pipeline import ProcessResult, UpdatePipeline
        def process(staging):
            pin = self.pub.capture(self.raw, version=123, version_file='original.json', bootstrap=False)
            snapshot = {'version':123,'characters':{'1':{'name':'A'}},'index':[],'words':{}}
            (staging/'character_data/current.json').write_text(json.dumps(snapshot))
            (staging/'manifest.json').write_text(json.dumps({'version':123}))
            self.pub.stage_cards(123, reason='baseline')
            return ProcessResult(processed=True,version_timestamp=123,portrait_pin=pin,schedule_enabled=False)
        pipeline = UpdatePipeline(self.root, process, publication=self.pub)
        with patch.object(pipeline, '_carry_forward_cards', side_effect=OSError('final phase failed')), self.assertLogs('server_app.pipeline', level='ERROR'):
            result = run_owned(pipeline)
        self.assertTrue(result.error)
        self.assertEqual(self.pub.reconcile(), [(1,123,'failed')])
        self.assertEqual(self.pub.list_requests(), [])
        bootstrap = run_owned(UpdatePipeline(self.root, self.processor()))
        self.assertTrue(bootstrap.warnings)
        self.assertEqual(self.pub.list_requests(), [])

    def test_card_retry_replays_exact_manifest_and_conflict_blocks_authority(self):
        from types import SimpleNamespace
        self.processor()
        from server_app.processor import _write_outbox
        self.core()
        self.pub.capture(self.raw, version=123, version_file='original.json')
        image = self.root/'1.png'
        image.write_bytes(b'original')
        report = SimpleNamespace(character_outputs=(('1',image),))
        def freeze(raw):
            self.pub.stage_cards(123, reason='no-new-output', manifest_bytes=raw)
            return self.pub.frozen_card_manifest(123)
        _write_outbox(self.root,123,report,{'1':{'name':'A'}},{'1'},before_publish=freeze)
        first = (self.root/'outbox/123/manifest.json').read_bytes()
        _write_outbox(self.root,123,report,{'1':{'name':'A'}},{'1'},before_publish=freeze)
        self.assertEqual((self.root/'outbox/123/manifest.json').read_bytes(),first)
        image.write_bytes(b'changed')
        def isolated(raw):
            try: return freeze(raw)
            except ValueError: return None
        _write_outbox(self.root,123,report,{'1':{'name':'A'}},{'1'},before_publish=isolated)
        self.assertEqual((self.root/'outbox/123/1.png').read_bytes(),b'changed')
        with self.assertRaises(ValueError): self.pub.complete(123)

    def test_result_repr_never_contains_raw_pin(self):
        from server_app.pipeline import ProcessResult
        pin = self.pub.capture(self.raw,version=123,version_file='original.json')
        logged = repr(ProcessResult(portrait_pin=pin))
        self.assertNotIn('raw_version_bytes',logged)
        self.assertNotIn(repr(self.raw),logged)

    def coordinator_config(self):
        from datetime import UTC, datetime
        from types import SimpleNamespace
        return SimpleNamespace(data_dir=self.root, environment='test', instance_id='test',
                               cdn_poll_enabled=True, normal_interval_seconds=1, burst_interval_seconds=1,
                               burst_duration_seconds=1, burst_anchor=datetime(2026,1,1,tzinfo=UTC))

    def test_manual_guard_failure_finishes_job_and_releases_admission(self):
        from types import SimpleNamespace
        from unittest.mock import patch

        from server_app.pipeline import ProcessResult
        from server_app.state import StateStore
        from server_app.update_coordinator import UpdateCoordinator
        pipeline = SimpleNamespace(publication=self.pub,run_once=lambda:ProcessResult())
        coordinator = UpdateCoordinator(self.coordinator_config(),pipeline,StateStore(self.root/'state.sqlite'))
        with patch.object(self.pub,'set_guard',side_effect=OSError('unavailable')), self.assertRaises(OSError):
            coordinator.enqueue_manual()
        self.assertEqual(coordinator.job_store.latest()['status'],'failed')
        self.assertFalse(coordinator.status()['running'])
        self.assertIsNotNone(coordinator.enqueue_manual())
        self.assertTrue(coordinator.wait_idle(3))
        self.assertEqual(coordinator.job_store.latest()['status'],'succeeded')

    def test_enabled_cli_once_holds_guard_until_pipeline_returns(self):
        import sys
        from types import SimpleNamespace
        from unittest.mock import patch
        self.processor()  # import real ordinary dependencies in this isolated test process
        import run_server
        from server_app.pipeline import ProcessResult
        seen = []
        def run():
            seen.append(json.loads(self.pub.guard_path.read_text())['updating'])
            self.write('update_status.json',{'updating':False})
            return ProcessResult(error='fixture failure')
        pipeline = SimpleNamespace(publication=self.pub,run_once=run)
        with patch.object(run_server.logging,'basicConfig'), \
             patch.object(sys,'argv',['run_server.py','--once']), \
             patch.object(run_server,'load_config',return_value=self.coordinator_config()), \
             patch.object(run_server,'ProductionUpdateProcessor'), \
             patch.object(run_server,'UpdatePipeline',return_value=pipeline):
            self.assertEqual(run_server.main(),1)
        self.assertEqual(seen,[True])
        self.assertFalse(json.loads(self.pub.guard_path.read_text())['updating'])
        self.assertFalse((self.root/'update_events.jsonl').exists())

    def test_normal_completed_pipeline_proof_recovers_request_write_failure(self):
        from unittest.mock import patch

        from server_app.pipeline import ProcessResult, UpdatePipeline
        def process(staging):
            pin = self.pub.capture(self.raw,version=123,version_file='original.json',bootstrap=False)
            snapshot = {'version':123,'characters':{'1':{'name':'A'}},'index':[],'words':{}}
            (staging/'character_data/current.json').write_text(json.dumps(snapshot))
            (staging/'manifest.json').write_text(json.dumps({'version':123}))
            self.pub.stage_cards(123,reason='baseline')
            return ProcessResult(processed=True,version_timestamp=123,portrait_pin=pin,schedule_enabled=False)
        with patch.object(self.pub,'_write_request',side_effect=OSError('request crash')):
            result = run_owned(UpdatePipeline(self.root,process,publication=self.pub))
        self.assertTrue(result.processed)
        self.assertIsNone(result.error)
        self.assertIn('portrait: request deferred',result.warnings)
        self.write('current_version.json',{'version':124})
        self.assertEqual(self.pub.reconcile(),[(1,123,'ready')])
        self.assertEqual(self.pub.read_request(self.pub.list_requests()[0])['version'],'123')
        self.assertEqual(json.loads((self.root/'current_version.json').read_text())['version'],124)

    def test_disabled_cli_once_keeps_direct_path_without_guard_or_jobs(self):
        import sys
        from types import SimpleNamespace
        from unittest.mock import patch
        self.processor()
        import run_server
        from server_app.pipeline import ProcessResult
        pipeline = SimpleNamespace(publication=None,run_once=lambda:ProcessResult())
        with patch.object(run_server.logging,'basicConfig'), \
             patch.object(sys,'argv',['run_server.py','--once']), \
             patch.object(run_server,'load_config',return_value=self.coordinator_config()), \
             patch.object(run_server,'ProductionUpdateProcessor'), \
             patch.object(run_server,'UpdatePipeline',return_value=pipeline), \
             patch.object(run_server,'UpdateCoordinator') as coordinator:
            self.assertEqual(run_server.main(),0)
        coordinator.assert_not_called()
        self.assertFalse(self.pub.guard_path.exists())
        self.assertFalse((self.root/'state.sqlite').exists())

    def test_independent_coordinators_cannot_clear_live_owner_or_recover_its_job(self):
        import threading
        from types import SimpleNamespace

        from server_app.pipeline import ProcessResult
        from server_app.portraits.publication import Publication
        from server_app.state import StateStore
        from server_app.update_coordinator import UpdateCoordinator
        entered, release = threading.Event(), threading.Event()
        def held():
            self.write('update_status.json',{'updating':True})
            entered.set()
            release.wait(5)
            return ProcessResult()
        config = self.coordinator_config()
        a = UpdateCoordinator(config,SimpleNamespace(publication=self.pub,run_once=held),StateStore(self.root/'state.sqlite'))
        job = a.enqueue_manual()
        try:
            self.assertTrue(entered.wait(3))
            other = Publication(self.root,instance_id='test',source_id='fixed',cdn_base=self.pub.cdn_base)
            b = UpdateCoordinator(config,SimpleNamespace(publication=other,run_once=lambda:ProcessResult()),StateStore(self.root/'state.sqlite'))
            self.assertEqual(a.get_job(job)['status'],'running')
            self.assertEqual(b.run_controlled_once().error,'update already running')
            self.assertIsNone(b.enqueue_manual())
            from datetime import datetime
            self.assertIsNone(b.run_scheduled_if_due(datetime.now().astimezone()))
            self.assertTrue(json.loads(self.pub.guard_path.read_text())['updating'])
            self.assertTrue(json.loads((self.root/'update_status.json').read_text())['updating'])
            self.assertEqual(a.get_job(job)['status'],'running')
        finally:
            release.set()
            self.assertTrue(a.wait_idle(3))
        self.assertIsNone(b.run_controlled_once().error)
        self.assertFalse(json.loads(self.pub.guard_path.read_text())['updating'])
        self.assertEqual(a.get_job(job)['status'],'succeeded')

    def test_os_owner_excludes_separate_process_and_nonowner_guard_write(self):
        import subprocess
        import sys

        from server_app.portraits.publication import Publication
        self.assertTrue(self.pub.acquire_run())
        try:
            self.pub.set_guard(True)
            other = Publication(self.root,instance_id='test',source_id='fixed',cdn_base=self.pub.cdn_base)
            with self.assertRaises(ValueError): other.set_guard(False)
            code = ('import sys; from server_app.portraits.publication import Publication; '
                    'p=Publication(sys.argv[1],instance_id="test",source_id="fixed",cdn_base="https://fixture.invalid/Android/Bundles"); '
                    'owned=p.acquire_run(); print("owned" if owned else "busy"); '
                    'p.release_run() if owned else None')
            probe = subprocess.run([sys.executable,'-c',code,str(self.root)],capture_output=True,text=True,timeout=5,check=True)
            self.assertEqual(probe.stdout.strip(),'busy')
            self.assertTrue(json.loads(self.pub.guard_path.read_text())['updating'])
            self.pub.set_guard(False)
        finally:
            self.pub.release_run()
        probe = subprocess.run([sys.executable,'-c',code,str(self.root)],capture_output=True,text=True,timeout=5,check=True)
        self.assertEqual(probe.stdout.strip(),'owned')

    def test_abandoned_job_repair_waits_for_acquired_os_ownership(self):
        from datetime import datetime
        from types import SimpleNamespace

        from server_app.pipeline import ProcessResult
        from server_app.state import StateStore, UpdateJobStore
        from server_app.update_coordinator import UpdateCoordinator
        store = StateStore(self.root/'state.sqlite')
        jobs = UpdateJobStore(store.path)
        jobs.create('abandoned','manual',datetime.now().astimezone())
        jobs.mark_running('abandoned',datetime.now().astimezone())
        coordinator = UpdateCoordinator(self.coordinator_config(),SimpleNamespace(publication=self.pub,run_once=lambda:ProcessResult()),store)
        self.assertEqual(jobs.get('abandoned')['status'],'running')
        self.assertIsNone(coordinator.run_controlled_once().error)
        self.assertEqual(jobs.get('abandoned')['status'],'interrupted')

    def test_installed_proof_sync_failure_must_heal_before_ready(self):
        from unittest.mock import patch

        from server_app.portraits import source_io
        self.core()
        self.pub.capture(self.raw,version=123,version_file='original.json',bootstrap=False)
        self.pub.stage_cards(123,reason='baseline')
        leaf = self.pub.root/self.pub.scope/'proofs'
        real_sync = source_io._sync_directory
        def failing(directory):
            if directory[0] == leaf and (leaf/'123.json').exists():
                raise OSError('post-install proof leaf sync failed')
            return real_sync(directory)
        with patch.object(source_io,'_sync_directory',side_effect=failing):
            with self.assertRaises(OSError): self.pub.attest_published(123)
            self.assertTrue((leaf/'123.json').exists())
            partials = set(self.pub.root.rglob('.partial-*'))
            for _ in range(2):
                with self.assertRaises(OSError): self.pub.complete(123)
                self.assertEqual(self.pub.list_requests(),[])
                self.assertEqual(set(self.pub.root.rglob('.partial-*')),partials)
        first = self.pub.complete(123)
        self.assertEqual(self.pub.complete(123),first)
        self.assertEqual(len(self.pub.list_requests()),1)
        self.assertEqual(set(self.pub.root.rglob('.partial-*')),partials)

    def test_installed_request_sync_failure_must_heal_before_registration(self):
        from unittest.mock import patch

        from server_app.portraits import source_io
        self.core()
        self.pub.capture(self.raw,version=123,version_file='original.json',bootstrap=False)
        self.pub.stage_cards(123,reason='baseline')
        self.pub.attest_published(123)
        leaf = self.pub.root/self.pub.scope/'requests'
        real_sync = source_io._sync_directory
        def failing(directory):
            if directory[0] == leaf and (leaf/'123.json').exists():
                raise OSError('post-install request leaf sync failed')
            return real_sync(directory)
        with patch.object(source_io,'_sync_directory',side_effect=failing):
            with self.assertRaises(OSError): self.pub.complete(123)
            self.assertTrue((leaf/'123.json').exists())
            partials = set(self.pub.root.rglob('.partial-*'))
            with self.assertRaises(OSError): self.pub.complete(123)
            self.assertEqual(self.pub.list_requests(),[])
            self.assertEqual(set(self.pub.root.rglob('.partial-*')),partials)
        first = self.pub.complete(123)
        self.assertEqual(self.pub.complete(123),first)
        self.assertEqual(len(self.pub.list_requests()),1)
        self.assertEqual(set(self.pub.root.rglob('.partial-*')),partials)

    def test_recover_pin_and_ready_heal_existing_record_barriers(self):
        from unittest.mock import patch

        from server_app.portraits import source_io
        self.core()
        self.pub.capture(self.raw,version=123,version_file='original.json',bootstrap=False)
        self.pub.stage_cards(123,reason='baseline')
        self.pub.attest_published(123)
        real_sync = source_io._sync_directory
        for kind in ('pins','cards'):
            with self.subTest(kind=kind):
                leaf = self.pub.root/self.pub.scope/kind
                def failing(directory, leaf=leaf):
                    if directory[0] == leaf:
                        raise OSError('existing record sync failed')
                    return real_sync(directory)
                with patch.object(source_io,'_sync_directory',side_effect=failing):
                    if kind == 'pins':
                        with self.assertRaises(OSError): self.pub.load_pin(123,recover=True)
                    with self.assertRaises(OSError): self.pub.complete(123)
                    self.assertEqual(self.pub.reconcile(),[(1,123,'retry-storage')])
                    self.assertEqual(self.pub.list_requests(),[])
        self.pub.complete(123)
        with patch.object(source_io,'_sync_directory',side_effect=AssertionError('reader must stay readonly')):
            self.pub.read_request(self.pub.list_requests()[0])
