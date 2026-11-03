import json
import shutil
import sqlite3
import unittest
from contextlib import closing
from unittest.mock import patch

from server_app.portraits.core_outcomes import (
    CoreJournal,
    CoreReader,
    _chain,
    _validate,
)
from test_portrait_publication_unittest import PublicationTests as Fixtures


class RecoveryTests(unittest.TestCase):
    setUp = Fixtures.setUp
    write = Fixtures.write
    core = Fixtures.core

    def setup_run(self, policy=False, *, batch=False):
        self.assertTrue(self.pub.acquire_run())
        self.addCleanup(self.pub.release_run)
        self.pin = self.pub.capture(self.raw, version=123, version_file='original.json', bootstrap=False)
        if batch:
            self.write('outbox/123/manifest.json', {'version': 123, 'characters': [
                {'character_id': '1', 'filename': '1.png'}]})
            (self.root/'outbox/123/1.png').write_bytes(b'local card fixture bytes')
        self.cards = self.pub.stage_cards(123, reason='disabled-cards', expected_batch=batch)
        self.core()
        self.journal = CoreJournal(self.pub)
        self.reader = CoreReader(self.journal.path, scope=self.pub.scope)
        self.ctx = self.journal.begin_run('a', trigger='manual')
        self.ctx.bind_target(123, classification='processed', announcement_eligible=True)
        self.assertIn('source_recovery', __import__('inspect').signature(self.ctx.finish).parameters,
                      'schema3 producer policy is missing')
        self.ctx.finish(phase='published', source_state='pending', pin_digest=self.pin.pin_digest,
                        cards=self.cards, source_recovery={'kind': 'published_attestation', 'schedule_enabled': policy})
        self.cursor = self.reader.read_outcomes_page()['cursor']

    def resolve(self, **kwargs):
        self.assertTrue(hasattr(self.journal, 'resolve_pending_source'), 'original-run resolver missing')
        return self.journal.resolve_pending_source('a', outcomes_cursor=self.cursor, **kwargs)

    def test_frozen_false_policy_and_exact_replay(self):
        self.setup_run(False)
        self.write('versions/123/rerun_schedule/current.json', {'version': 123})
        result = self.resolve()
        self.assertEqual('ready', result['status'])
        request = self.pub.read_request(result['ref'])
        self.assertFalse(any('rerun_schedule' in a['path'] for a in request['core']['artifacts']))
        self.assertEqual(result, self.resolve())
        self.assertEqual(1, len(self.reader.read_source_links()))
        self.assertEqual(1, self.reader.latest_promotion()['promotion'])

    def test_same_version_new_token_blocks_fresh_but_not_existing_proof(self):
        self.setup_run()
        b = self.journal.begin_run('b', trigger='manual')
        b.bind_target(123, classification='processed', announcement_eligible=True)
        b.finish(phase='published', source_state='pending')
        self.assertEqual('superseded', self.resolve()['status'])
        self.pub.attest_published(123, schedule_enabled=False)
        self.write('current_version.json', {'version': 124})
        with patch.object(self.pub, '_prove', side_effect=AssertionError('must reuse')):
            self.assertEqual('ready', self.resolve()['status'])
        self.assertEqual('b', self.reader.latest_promotion()['run_id'])

    def test_ambiguous_proof_and_request_install_heal_exactly(self):
        self.setup_run(True)
        original = self.pub._put
        def fault(kind, version, value):
            original(kind, version, value)
            if kind == 'proofs':
                raise OSError('visible before flush returned')
        with patch.object(self.pub, '_put', side_effect=fault), self.assertRaises(OSError):
            self.resolve()
        proof = self.pub._record('proofs', 123)
        self.write('current_version.json', {'version': 124})
        with patch.object(self.pub, '_prove', side_effect=AssertionError('must reuse')):
            with patch.object(self.journal, 'link_source', side_effect=OSError('link')), self.assertRaises(OSError):
                self.resolve()
            ref = self.pub.complete(123)
            self.assertEqual(ref, self.resolve()['ref'])
        self.assertEqual(proof, self.pub._record('proofs', 123))
        self.assertEqual(1, len(self.pub.list_requests()))

    def test_cursor_owner_and_terminal_failure(self):
        self.setup_run()
        with self.assertRaises(ValueError):
            self.journal.resolve_pending_source('a', outcomes_cursor=None)
        for key, value in [('file_id', [0, 0]), ('prefix', 'a'*64), ('stream', 'source_links')]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.journal.resolve_pending_source('a', outcomes_cursor=dict(self.cursor, **{key: value}))
        self.journal.fail_source('a', code='source_invalid')
        self.assertEqual('failed', self.resolve()['status'])
        self.pub.release_run()
        with self.assertRaises(ValueError):
            self.resolve()
        self.assertTrue(self.pub.acquire_run())

    def test_schema2_exact_read_and_writer_refusal(self):
        self.setup_run()
        with sqlite3.connect(self.journal.path) as db:
            row = json.loads(db.execute('SELECT payload FROM outcomes').fetchone()[0])
            row.pop('source_recovery')
            raw = json.dumps(row)
            db.execute('UPDATE identity SET schema=2')
            db.execute('UPDATE outcomes SET payload=?,prefix=?', (raw, _chain('0'*64, 1, raw)))
        self.assertNotIn('source_recovery', self.reader.read_run('a'))
        before = self.journal.path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'rollout'):
            self.journal.begin_run('new', trigger='manual')
        self.assertEqual(before, self.journal.path.read_bytes())

    def test_strict_policy_and_authentic_card_pin(self):
        self.setup_run()
        for policy in [1, 1.0, None, 'false']:
            with self.subTest(policy=policy), self.assertRaises(ValueError):
                self.ctx.finish(phase='published', source_state='pending', pin_digest=self.pin.pin_digest,
                                cards=self.cards, source_recovery={'kind': 'published_attestation', 'schedule_enabled': policy})

    def pipeline_policy(self, enabled):
        from server_app.pipeline import ProcessResult, UpdatePipeline
        self.assertTrue(self.pub.acquire_run())
        self.addCleanup(self.pub.release_run)
        pin = self.pub.capture(self.raw, version=123, version_file='original.json', bootstrap=False)
        cards = self.pub.stage_cards(123, reason='disabled-cards')
        journal = CoreJournal(self.pub)
        reader = CoreReader(journal.path, scope=self.pub.scope)
        run_id = str(enabled)
        ctx = journal.begin_run(run_id, trigger='manual')
        def process(staging):
            ctx.bind_target(123, classification='processed', announcement_eligible=True)
            target = staging / 'character_data'
            target.mkdir(exist_ok=True)
            (target / 'current.json').write_text(json.dumps({'version':123, 'characters':{'1':{}}, 'index':[], 'words':{}}))
            (staging/'manifest.json').write_text(json.dumps({'version':123}))
            schedule = staging/'rerun_schedule'
            schedule.mkdir()
            (schedule/'current.json').write_text(json.dumps({'version':123}))
            return ProcessResult(version_timestamp=123, processed=True, schedule_enabled=enabled,
                                 portrait_pin=pin, card_intent=cards)
        pipeline = UpdatePipeline(self.root, process, publication=self.pub)
        def fail(*args, **kwargs):
            self.assertEqual({'kind':'published_attestation', 'schedule_enabled':enabled},
                             reader.read_run(run_id)['source_recovery'])
            self.assertEqual(123, json.loads((self.root/'current_version.json').read_text())['version'])
            raise OSError('pre-proof')
        with ctx.activate(), patch.object(self.pub, 'attest_published', side_effect=fail):
            result = pipeline.run_once()
        self.assertIsNone(result.error)
        self.assertIn('portrait: request deferred', result.warnings)
        self.assertEqual({'kind':'published_attestation', 'schedule_enabled':enabled},
                         reader.read_run(run_id).get('source_recovery'))
        # A changed later ProcessResult/config is not an input to this API.
        changed = ProcessResult(schedule_enabled=not enabled)
        self.assertNotEqual(enabled, changed.schedule_enabled)
        recovered = journal.resolve_pending_source(run_id, outcomes_cursor=reader.read_outcomes_page()['cursor'])
        proof = self.pub.read_request(recovered['ref'])['core']
        self.assertEqual(enabled, any('rerun_schedule' in item['path'] for item in proof['artifacts']))

    def test_pipeline_false_policy(self):
        self.pipeline_policy(False)

    def test_pipeline_true_policy(self):
        self.pipeline_policy(True)

    def test_unknown_pin_only_existing_ready_and_failed_parent_not_rescued(self):
        self.setup_run()
        unknown = self.journal.begin_run('unknown', trigger='manual')
        unknown.bind_target(123, classification='processed', announcement_eligible=True)
        unknown.finish(phase='published', source_state='pending')
        cursor = self.reader.read_outcomes_page()['cursor']
        self.assertEqual('prerequisite', self.journal.resolve_pending_source('unknown', outcomes_cursor=cursor)['status'])
        self.assertEqual([], self.pub.list_requests())
        self.pub.attest_published(123)
        ref = self.pub.complete(123)
        self.assertEqual(ref, self.journal.resolve_pending_source('unknown', outcomes_cursor=cursor, ready_ref=ref)['ref'])
        self.assertIsNone(self.reader.read_run('unknown')['pin_digest'])
        failed = self.journal.begin_run('failed', trigger='manual')
        failed.bind_target(123, classification='processed', announcement_eligible=True)
        failed.finish(phase='failed', source_state='not_requested')
        self.assertEqual('prerequisite', self.journal.resolve_pending_source('failed', outcomes_cursor=cursor, ready_ref=ref)['status'])
        self.assertEqual(1, len(self.reader.read_source_links()))

    def test_forged_bootstrap_candidate_and_native_card_mismatch(self):
        self.setup_run()
        path = self.pub.root/self.pub.scope/'pins/123.json'
        record = json.loads(path.read_text())
        record['bootstrap'] = True
        path.write_text(json.dumps(record))
        self.assertEqual('prerequisite', self.resolve()['status'])
        record['bootstrap'] = False
        path.write_text(json.dumps(record))
        cards_path = self.pub.root/self.pub.scope/'cards/123.json'
        cards_path.write_text(json.dumps({'kind':'none', 'reason':'baseline'}))
        with self.assertRaisesRegex(ValueError, 'pin/card'):
            self.resolve()

    def test_corrupt_proof_request_and_known_intent_are_not_missing(self):
        self.setup_run()
        self.pub.attest_published(123)
        proof_path = self.pub.root/self.pub.scope/'proofs/123.json'
        original = proof_path.read_bytes()
        proof_path.write_bytes(b'{broken')
        with self.assertRaises(ValueError):
            self.resolve()
        proof_path.write_bytes(original)
        ref = self.pub.complete(123)
        request_path = self.pub.root/self.pub.scope/'requests/123.json'
        original_request = request_path.read_bytes()
        request_path.write_bytes(b'{broken')
        with self.assertRaises(ValueError):
            self.resolve()
        # Candidate native refs still authenticate original frozen card intent.
        request_path.write_bytes(original_request)
        with patch.object(self.pub, 'read_request', wraps=self.pub.read_request):
            self.assertEqual('ready', self.resolve(ready_ref=ref)['status'])

    def test_schema_shapes_and_source_policy_cross_fields(self):
        self.setup_run()
        row = self.reader.read_run('a')
        row.pop('sequence')
        mutations = [dict(row, phase='failed', promotion=None), dict(row, classification='bootstrap'),
                     dict(row, pin_digest=None), dict(row, source_state='not_requested'),
                     dict(row, cards={'kind':'handoff_unresolved'}),
                     dict(row, cards={'kind':'none','reason':'bootstrap-history-unavailable'})]
        for value in [1, 1.0, None, 'false']:
            mutations.append(dict(row, source_recovery={'kind':'published_attestation','schedule_enabled':value}))
        mutations.append(dict(row, source_recovery={'kind':'published_attestation','schedule_enabled':False,'extra':0}))
        for mutated in mutations:
            with self.subTest(mutated=mutated), self.assertRaises(ValueError):
                _validate(mutated, self.pub.scope, 'a', 'outcomes', 3)
        legacy = dict(row)
        legacy.pop('source_recovery')
        _validate(legacy, self.pub.scope, 'a', 'outcomes', 2)
        with self.assertRaises(ValueError):
            _validate(row, self.pub.scope, 'a', 'outcomes', 2)

    def test_request_visible_before_flush_failure(self):
        self.setup_run()
        original = self.pub._put
        def fault(kind, version, value):
            original(kind, version, value)
            if kind == 'requests':
                raise OSError('request installed')
        with patch.object(self.pub, '_put', side_effect=fault), self.assertRaises(OSError):
            self.resolve()
        recorded = self.pub._record('requests', 123)
        self.write('current_version.json', {'version':124})
        with patch.object(self.pub, '_prove', side_effect=AssertionError('must reuse')):
            self.assertEqual(recorded['sha256'], self.resolve()['ref'].sha256)

    def test_noop_candidate_links_original_token(self):
        self.setup_run()
        self.pub.attest_published(123)
        ref = self.pub.complete(123)
        noop = self.journal.begin_run('n', trigger='manual')
        noop.bind_target(123, classification='noop', announcement_eligible=False)
        row = noop.finish(phase='no_change', source_state='pending', pin_digest=self.pin.pin_digest, cards=self.cards)
        self.assertIsNone(row['source_recovery'])
        self.assertIsNone(row['promotion'])
        self.journal.link_source('n', ref)
        self.assertEqual(ref, self.journal.resolve_pending_source('n', outcomes_cursor=self.cursor)['ref'])
        self.assertEqual(ref, self.resolve(ready_ref=ref)['ref'])
        self.assertEqual([None, 1], [r['promotion'] for r in self.reader.read_source_links()])

    def test_missing_frozen_pin_bytes_is_storage_error_not_absent_policy(self):
        self.setup_run()
        with patch('server_app.portraits.publication.read_metadata', side_effect=FileNotFoundError('lost frozen raw')), self.assertRaises(FileNotFoundError):
            self.resolve()

    def test_resolver_rejects_restore_regrowth_and_physical_replacement(self):
        self.setup_run()
        backup = self.root/'backup.sqlite'
        with closing(sqlite3.connect(self.journal.path)) as source, closing(sqlite3.connect(backup)) as target:
            source.backup(target)
        self.journal.begin_run('b', trigger='manual').finish(phase='interrupted', source_state='not_requested')
        self.cursor = self.reader.read_outcomes_page()['cursor']
        shutil.copyfile(backup, self.journal.path)
        for run_id in ('c', 'd'):
            self.journal.begin_run(run_id, trigger='manual').finish(phase='interrupted', source_state='not_requested')
        with self.assertRaisesRegex(ValueError, 'history'):
            self.resolve()
        self.cursor = self.reader.read_outcomes_page()['cursor']
        replacement = self.root/'replacement.sqlite'
        with closing(sqlite3.connect(self.journal.path)) as source, closing(sqlite3.connect(replacement)) as target:
            source.backup(target)
        replacement.replace(self.journal.path)
        with self.assertRaisesRegex(ValueError, 'incarnation'):
            self.resolve()
        self.assertEqual([], self.pub.list_requests())

    def test_absent_policy_and_bootstrap_noop_cannot_mint_proof(self):
        self.setup_run()
        with closing(sqlite3.connect(self.journal.path)) as db:
            row = json.loads(db.execute('SELECT payload FROM outcomes').fetchone()[0])
            row['source_recovery'] = None
            raw = json.dumps(row)
            db.execute('UPDATE outcomes SET payload=?,prefix=?', (raw, _chain('0'*64,1,raw)))
            db.commit()
        self.cursor = self.reader.read_outcomes_page()['cursor']
        self.assertEqual('prerequisite', self.resolve()['status'])
        self.assertEqual('missing_original_policy', self.resolve().get('code'))
        for classification in ('noop', 'bootstrap'):
            ctx = self.journal.begin_run(classification, trigger='manual')
            ctx.bind_target(123, classification=classification, announcement_eligible=False)
            outcome = ctx.finish(phase='no_change', source_state='pending')
            self.assertIsNone(outcome['source_recovery'])
            self.assertIsNone(outcome['promotion'])
            self.assertEqual('prerequisite', self.journal.resolve_pending_source(classification, outcomes_cursor=self.cursor)['status'])
        self.assertEqual([], self.pub.list_requests())
        with self.assertRaises(FileNotFoundError):
            self.pub._record('proofs',123)

    def test_schema2_fenced_shape_and_schema1_remain_strict(self):
        self.setup_run()
        with closing(sqlite3.connect(self.journal.path)) as db:
            row = json.loads(db.execute('SELECT payload FROM outcomes').fetchone()[0])
            row.pop('source_recovery')
            raw = json.dumps(row)
            db.execute('UPDATE identity SET schema=2')
            db.execute('UPDATE outcomes SET payload=?,prefix=?', (raw, _chain('0'*64,1,raw)))
            db.commit()
        cursor = self.reader.read_outcomes_page()['cursor']
        self.assertEqual([], self.reader.read_outcomes_page(cursor=cursor)['items'])
        with closing(sqlite3.connect(self.journal.path)) as db:
            row['source_recovery'] = None
            raw = json.dumps(row)
            db.execute('UPDATE outcomes SET payload=?,prefix=?', (raw, _chain('0'*64,1,raw)))
            db.commit()
        with self.assertRaises(ValueError):
            self.reader.read_outcomes_page()
        with closing(sqlite3.connect(self.journal.path)) as db:
            db.execute('UPDATE identity SET schema=1')
            db.commit()
        with self.assertRaises(ValueError):
            self.reader.read_outcomes_page()

    def test_required_phase_failure_has_no_policy_or_attestation(self):
        from server_app.pipeline import ProcessResult, UpdatePipeline
        self.setup_run()
        ctx = self.journal.begin_run('required', trigger='manual')
        ctx.bind_target(123, classification='processed', announcement_eligible=True)
        result = ProcessResult(version_timestamp=123, processed=True, portrait_pin=self.pin, card_intent=self.cards)
        pipeline = UpdatePipeline(self.root, lambda _:result, publication=self.pub)
        with ctx.activate(), patch('server_app.pipeline.publish_version', side_effect=OSError('required')), patch.object(self.pub,'attest_published') as attest:
            returned = pipeline.run_once()
        self.assertIsNotNone(returned.error)
        attest.assert_not_called()
        row = self.reader.read_run('required')
        self.assertIsNone(row['source_recovery'])
        self.assertIsNone(row['promotion'])

    def invalid_batch(self, mode):
        self.setup_run(batch=True)
        frozen = self.pub.root/self.cards['manifest']['locator']
        if mode == 'missing':
            frozen.unlink()
        elif mode == 'corrupt':
            frozen.write_bytes(b'corrupt frozen manifest')
        else:
            self.pub._put('card_conflicts', 123, {'scope': self.pub.scope, 'version': '123', 'state': 'failed'})
        expected = FileNotFoundError if mode == 'missing' else ValueError
        with patch.object(self.pub, '_prove', wraps=self.pub._prove) as prove:
            with self.assertRaises(expected):
                self.resolve()
            prove.assert_not_called()
        for kind in ('proofs', 'requests'):
            self.assertFalse((self.pub.root/self.pub.scope/kind/'123.json').exists(), kind)
        self.assertEqual([], self.pub.list_requests())
        self.assertEqual([], self.reader.read_source_links())
        self.assertEqual(1, self.reader.latest_promotion()['promotion'])

    def test_missing_batch_manifest_precedes_fresh_proof(self):
        self.invalid_batch('missing')

    def test_corrupt_batch_manifest_precedes_fresh_proof(self):
        self.invalid_batch('corrupt')

    def test_permanent_batch_conflict_precedes_fresh_proof(self):
        self.invalid_batch('conflict')

    def test_valid_batch_fresh_recovery_and_existing_proof_ready_replay(self):
        self.setup_run(batch=True)
        with patch.object(self.pub, '_write_request', side_effect=OSError('request fault')), self.assertRaises(OSError):
            self.resolve()
        proof = self.pub._record('proofs', 123)
        self.write('current_version.json', {'version': 124})
        with patch.object(self.pub, '_prove', side_effect=AssertionError('must reuse frozen proof')):
            with patch.object(self.journal, 'link_source', side_effect=OSError('link fault')), self.assertRaises(OSError):
                self.resolve()
            request = self.pub._record('requests', 123)
            result = self.resolve()
            self.assertEqual(result, self.resolve())
        self.assertEqual('ready', result['status'])
        self.assertEqual(self.cards, self.pub.read_request(result['ref'])['cards'])
        self.assertEqual(proof, self.pub._record('proofs', 123))
        self.assertEqual(request, self.pub._record('requests', 123))
        self.assertEqual(1, len(self.reader.read_source_links()))

    def test_first_bootstrap_existing_request_repairs_optional_link_failure(self):
        self.assertTrue(self.pub.acquire_run())
        self.addCleanup(self.pub.release_run)
        pin = self.pub.capture(self.raw, version=123, version_file='original.json', bootstrap=True)
        self.core()
        ref = self.pub.complete(123)
        request = self.pub.read_request(ref)
        journal = CoreJournal(self.pub)
        reader = CoreReader(journal.path, scope=journal.scope)
        ctx = journal.begin_run('bootstrap', trigger='manual')
        ctx.bind_target(123, classification='bootstrap', announcement_eligible=False)
        row = ctx.finish(phase='no_change', source_state='pending', pin_digest=pin.pin_digest,
                         cards=request['cards'], bootstrap_ref=ref)
        self.assertEqual(1, row['promotion'])
        self.assertIsNone(row['source_recovery'])
        with patch.object(journal, 'link_source', side_effect=OSError('optional link storage')), self.assertRaises(OSError):
            journal.link_source('bootstrap', ref)
        with patch.object(self.pub, '_prove', side_effect=AssertionError('no fresh bootstrap proof')), patch.object(self.pub, 'complete', side_effect=AssertionError('reuse installed request only')):
            descriptor = self.pub.root/self.pub.scope/'requests/123.json'
            original_descriptor = descriptor.read_bytes()
            descriptor.unlink()
            self.assertEqual('prerequisite', journal.resolve_pending_source('bootstrap', outcomes_cursor=reader.read_outcomes_page()['cursor'])['status'])
            descriptor.write_bytes(original_descriptor)
            with self.pub._index(write=True) as db:
                db.execute('DELETE FROM ready WHERE version=?', ('123',))
            resolved = journal.resolve_pending_source('bootstrap', outcomes_cursor=reader.read_outcomes_page()['cursor'])
            self.assertEqual('ready', resolved['status'])
            self.assertEqual(ref, resolved['ref'])
            self.assertEqual(ref, self.pub.list_requests()[0].ref)
            # This recovery hook only expands the promoted bootstrap case.
            noop = journal.begin_run('noop', trigger='manual')
            noop.bind_target(123, classification='noop', announcement_eligible=False)
            noop.finish(phase='no_change', source_state='pending')
            self.assertEqual('prerequisite', journal.resolve_pending_source('noop', outcomes_cursor=reader.read_outcomes_page()['cursor'], ready_ref=ref)['status'])
        self.assertEqual([1], [link['promotion'] for link in reader.read_source_links()])
        self.assertEqual(1, reader.latest_promotion()['promotion'])


del Fixtures
