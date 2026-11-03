"""Manual immutable metadata and independent durable Reader projection."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from server_app.portraits import publisher as pub
from server_app.portraits.model import build_catalog, digest
from server_app.portraits.reader import PortraitReader
from server_app.portraits.store import PortraitStore
from test_portrait_lineage_unittest import catalog, execution
from test_portrait_store_v2_unittest import result


class ManualCatalogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'media'
        self.db = Path(self.tmp.name) / 'db'
        self.store = PortraitStore(self.db, 'i', 's')
        self.reader = PortraitReader(self.db, 'i', 's')

    def install(self, c, pid='e'*64):
        self.assertTrue(hasattr(pub, 'install_manual_desired'), 'missing manual installer')
        return pub.install_manual_desired(c, self.root, execution_plan_id=pid,
            expected_current=pub.catalog_token(self.root),
            expected_version=pub.version_catalog_token(self.root, c['version']))

    def test_stage_snapshot_is_stable_sanitized_pointerless_and_barrier_fenced(self):
        self.assertTrue(hasattr(pub, 'stage_desired_snapshot'), 'missing desired staging API')
        c = catalog('A')
        candidate = pub.project_catalog(c, [result(dict(execution(c)['jobs'][0], attempt=1), 'success')])
        stage = pub.stage_desired_snapshot(candidate, self.root, execution_plan_id='e'*64)
        ref = stage['desired_catalog_ref']
        snapshot = json.loads((self.root / ref['locator']).read_bytes())
        self.assertIsNone(snapshot['skins'][0]['artifact_path'])
        self.assertEqual(snapshot['skins'][0]['state'], 'not_generated')
        self.assertEqual(snapshot['desired_execution_plan_id'], 'e'*64)
        # Identical requests reuse the reference; no pointer or acceptance exists.
        self.assertEqual(pub.stage_desired_snapshot(candidate, self.root, execution_plan_id='e'*64), stage)
        self.assertIsNone(pub.catalog_token(self.root))
        self.assertIsNone(pub.version_catalog_token(self.root, 'A'))
        with patch.object(pub, '_sync_directory', side_effect=OSError('stage barrier')):
            with self.assertRaises(OSError):
                pub.stage_desired_snapshot(candidate, self.root, execution_plan_id='e'*64)
        self.assertEqual(pub.stage_desired_snapshot(candidate, self.root, execution_plan_id='e'*64), stage)
        for pid in (None, 'bad'):
            with self.assertRaises(ValueError): pub.stage_desired_snapshot(c, self.root, execution_plan_id=pid)

    def finish(self, manifest, status='success', now=0):
        pid = self.store.register_closed_plan(manifest)['plan_id']
        claim = self.store.claim('w', now, 10, allowed_plan_ids=[pid])
        self.assertIsNotNone(claim)
        self.assertTrue(self.store.finish(claim['job_id'], claim['attempt'],
            result(claim, status), owner='w', now=now+1))
        return pid, self.store.seal_plan(pid)

    def test_absent_history_seed_ref_idempotence_and_current_isolation(self):
        c = catalog('C', 'd')
        pub.publish_desired(c, self.root, expected_current=None, execution_plan_id='a'*64)
        before = pub.catalog_token(self.root)
        manifest = execution(catalog('A'))
        manifest.update(kind='manual', request_id='history-A')
        pid = digest(['i', 's', 'manual', 'history-A'])
        installed = self.install(catalog('A'), pid)
        self.assertEqual(installed['disposition'], 'version_seeded')
        ref = installed['desired_catalog_ref']
        raw = (self.root / ref['locator']).read_bytes()
        self.assertEqual(digest(json.loads(raw)), ref['sha256'])
        self.assertEqual(ref['revision'], ref['sha256'])
        self.assertEqual(json.loads(raw)['desired_execution_plan_id'], pid)
        again = self.install(catalog('A'), pid)
        self.assertEqual(again['desired_catalog_ref'], ref)
        self.assertEqual(again['disposition'], 'immutable_only')
        manifest['desired_catalog_ref'] = ref
        actual, sealed = self.finish(manifest)
        self.assertEqual(actual, pid)
        pub.project_accepted(self.reader, self.root)
        self.assertEqual(pub.catalog_token(self.root), before)
        self.assertEqual(pub.read_catalog(self.root, 'A')['skins'][0]['state'], 'published')
        self.assertIsNotNone(pub.read_catalog(self.root, 'A')['skins'][0]['artifact_path'])
        self.assertEqual(self.store.seal_plan(actual), sealed)

    def test_same_current_failure_manual_success_preserves_sealed_owner_every_order(self):
        c = catalog('C')
        manifest = execution(c)
        owner, sealed = self.finish(manifest, 'failed')
        for order in ((0, 1), (1, 0)):
            root = self.root / str(order[0])
            pub.publish_desired(c, root, expected_current=None, execution_plan_id=owner)
            manual = copy.deepcopy(manifest)
            manual.update(kind='manual', request_id='retry')
            if order == (0, 1):
                actual, manual_sealed = self.finish(manual, now=2)
            pid = actual
            pub.install_manual_desired(c, root, execution_plan_id=pid,
                expected_current=pub.catalog_token(root), expected_version=pub.version_catalog_token(root, 'C'))
            for after in (*order, 0, 1):
                pub.project_accepted(self.reader, root, after_id=after, limit=1)
            current = pub.read_catalog(root)
            row = current['skins'][0]
            self.assertEqual(current['desired_execution_plan_id'], owner)
            self.assertEqual((row['state'], row['diagnostics']), ('failed', ['test']))
            self.assertEqual((row['plan_id'], row['attempt']), (pid, 2))
        self.assertEqual(self.store.seal_plan(owner), sealed)
        self.assertEqual(self.store.seal_plan(actual), manual_sealed)
        self.assertEqual(len(self.store.list_events()), 2)

    def test_divergent_same_version_never_promotes_version_generation(self):
        c = catalog('C')
        pub.publish_desired(c, self.root, expected_current=None, execution_plan_id='a'*64)
        current = pub.read_catalog(self.root)
        pending = copy.deepcopy(c)
        pending['skins'][0].update(state='pending', diagnostics=['other generation'])
        pub.publish_desired(pending, self.root, expected_current=pub.catalog_token(self.root), execution_plan_id='b'*64)
        pub._atomic_json(self.root, 'current.json', self.pointer(current))
        self.finish(execution(c))
        pub.project_accepted(self.reader, self.root)
        self.assertEqual(pub.read_catalog(self.root)['desired_execution_plan_id'], 'a'*64)
        self.assertEqual(pub.read_catalog(self.root)['skins'][0]['state'], 'not_generated')
        self.assertEqual(pub.read_catalog(self.root, 'C')['desired_execution_plan_id'], 'b'*64)
        self.assertEqual(pub.read_catalog(self.root, 'C')['skins'][0]['state'], 'pending')
        pub.project_accepted(self.reader, self.root, after_id=1)
        self.assertEqual(pub.read_catalog(self.root)['desired_execution_plan_id'], 'a'*64)

    @staticmethod
    def pointer(c):
        sha = digest(c)
        return json.dumps(dict(schema_version=1, version=c['version'],
            catalog_path=f"catalog/{c['version']}/{sha}.json", sha256=sha)).encode()

    def test_same_current_missing_version_recovers_actual_and_requires_baseline(self):
        c = catalog('C')
        pub.publish_desired(c, self.root, expected_current=None)
        before = pub.catalog_token(self.root)
        with self.assertRaisesRegex(ValueError, 'bootstrap'):
            self.install(c)
        self.assertEqual(pub.catalog_token(self.root), before)
        pub.publish_desired(c, self.root, expected_current=before, execution_plan_id='a'*64)
        before = pub.catalog_token(self.root)
        (self.root / 'catalog/C.json').unlink()
        changed = copy.deepcopy(c)
        changed['skins'][0]['name'] = 'manual metadata'
        installed = self.install(changed)
        self.assertEqual(installed['disposition'], 'current_version_recovered')
        self.assertEqual(pub.version_catalog_token(self.root, 'C'), before)
        self.assertEqual(pub.catalog_token(self.root), before)
        self.assertNotEqual(installed['desired_catalog_ref']['sha256'], before)

    def test_tokens_scope_plan_and_dangling_fail_closed(self):
        c = catalog('C')
        pub.publish_desired(c, self.root, expected_current=None, execution_plan_id='a'*64)
        self.assertTrue(hasattr(pub, 'version_catalog_token'))
        for current, version, pid, incoming in (
                (None, pub.version_catalog_token(self.root, 'C'), 'e'*64, c),
                (pub.catalog_token(self.root), None, 'e'*64, c),
                (pub.catalog_token(self.root), pub.version_catalog_token(self.root, 'C'), None, c),
                (pub.catalog_token(self.root), pub.version_catalog_token(self.root, 'C'), 'e'*64, dict(c, instance_id='other'))):
            with self.assertRaises(ValueError):
                pub.install_manual_desired(incoming, self.root, execution_plan_id=pid,
                    expected_current=current, expected_version=version)
        pointer = self.root / 'catalog/C.json'
        original = pointer.read_bytes()
        pointer.write_text('{"schema_version":1}', encoding='utf-8')
        with self.assertRaises(ValueError): pub.version_catalog_token(self.root, 'C')
        pointer.write_bytes(original)
        target = self.root / json.loads(original)['catalog_path']
        target.unlink()
        with self.assertRaises(FileNotFoundError): pub.version_catalog_token(self.root, 'C')

    def test_partial_projection_replay_repairs_current_without_revision_gate(self):
        c = catalog('C')
        pub.publish_desired(c, self.root, expected_current=None, execution_plan_id='a'*64)
        self.finish(execution(c))
        original = pub._atomic_json
        def crash(root, relative, raw):
            if relative == 'current.json': raise OSError('current barrier')
            return original(root, relative, raw)
        with patch.object(pub, '_atomic_json', side_effect=crash):
            with self.assertRaises(OSError): pub.project_accepted(self.reader, self.root)
        self.assertNotEqual(pub.catalog_token(self.root), pub.version_catalog_token(self.root, 'C'))
        pub.project_accepted(self.reader, self.root)
        self.assertEqual(pub.catalog_token(self.root), pub.version_catalog_token(self.root, 'C'))

    def test_selected_missing_keeps_unselected_metadata_and_states(self):
        template = catalog('C')['skins'][0]
        rows = [dict(template, skin_id=str(n)) for n in (1, 2, 3)]
        rows[2].update(state='blocked', diagnostics=['unselected'])
        c = build_catalog('i', 's', 'C', rows)
        original = execution(c)
        job = original['jobs'][0]
        job.update(skin_id='1', render_key=c['skins'][0]['render_key'])
        job['job_id'] = digest([job[k] for k in ('instance_id','source_id','version','character_id','skin_id','render_key')])
        original['expected_jobset_digest'] = digest(original['jobs'])
        owner, owner_sealed = self.finish(original)
        pub.publish_desired(c, self.root, expected_current=None, execution_plan_id=owner)
        pub.project_accepted(self.reader, self.root)
        before = pub.read_catalog(self.root)
        self.assertEqual(before['skins'][0]['state'], 'published')
        selected = dict(c, skins=[c['skins'][1]])
        manifest = execution(selected)
        manifest.update(kind='manual', request_id='selected-second')
        pid, sealed = self.finish(manifest, now=2)
        self.install(c, pid)
        pub.project_accepted(self.reader, self.root)
        after = pub.read_catalog(self.root)
        self.assertEqual(after['skins'][0], before['skins'][0])
        self.assertEqual(after['skins'][2], before['skins'][2])
        self.assertEqual(after['skins'][1]['state'], 'not_generated')
        self.assertIsNotNone(after['skins'][1]['artifact_path'])
        self.assertEqual(sealed['counts']['success'], 1)
        self.assertEqual(self.store.seal_plan(owner), owner_sealed)

    def test_after_current_write_interruption_and_directory_barrier_replay(self):
        c = catalog('C')
        pub.publish_desired(c, self.root, expected_current=None, execution_plan_id='a'*64)
        self.finish(execution(c))
        original = pub._atomic_json
        def crash(root, relative, raw):
            original(root, relative, raw)
            if relative == 'current.json': raise OSError('current written before return')
        with patch.object(pub, '_atomic_json', side_effect=crash):
            with self.assertRaises(OSError): pub.project_accepted(self.reader, self.root)
        after = pub.catalog_token(self.root)
        pub.project_accepted(self.reader, self.root)
        self.assertEqual(pub.catalog_token(self.root), after)
        with patch.object(pub, '_sync_directory', side_effect=OSError('directory barrier')):
            with self.assertRaises(OSError): self.install(c)
        self.assertEqual(pub.catalog_token(self.root), after)
        with patch.object(pub, '_sync_directory', wraps=pub._sync_directory) as synced:
            self.install(c)
            self.assertGreaterEqual(synced.call_count, 3)

    def test_history_same_keys_reuses_media_without_changing_current_generation(self):
        c = catalog('C')
        pub.publish_desired(c, self.root, expected_current=None, execution_plan_id='a'*64)
        before = pub.read_catalog(self.root)
        self.install(catalog('A'))
        self.finish(execution(catalog('A')))
        pub.project_accepted(self.reader, self.root)
        current = pub.read_catalog(self.root)
        self.assertEqual(current['desired_generation'], before['desired_generation'])
        self.assertEqual(current['skins'][0]['state'], 'not_generated')
        self.assertEqual(current['skins'][0]['source_version'], 'A')

    def test_manual_failure_never_changes_canonical_state_or_fabricates_media(self):
        c = catalog('C')
        c['skins'][0].update(state='blocked', diagnostics=['canonical'])
        pub.publish_desired(c, self.root, expected_current=None, execution_plan_id='a'*64)
        before = pub.catalog_token(self.root)
        manifest = execution(c)
        manifest.update(kind='manual', request_id='failed-manual')
        pid, sealed = self.finish(manifest, 'failed')
        self.install(c, pid)
        pub.project_accepted(self.reader, self.root)
        self.assertEqual(pub.catalog_token(self.root), before)
        self.assertEqual(sealed['counts']['failed'], 1)

    def test_installer_faults_replay_identical_ref_and_barriers(self):
        c = catalog('A')
        self.assertTrue(hasattr(pub, 'install_manual_desired'))
        original = pub._atomic_json
        def crash(root, relative, raw):
            raise OSError('before version pointer')
        with patch.object(pub, '_atomic_json', side_effect=crash):
            with self.assertRaises(OSError): self.install(c)
        self.assertIsNone(pub.version_catalog_token(self.root, 'A'))
        snapshots = list((self.root / 'catalog/A').glob('*.json'))
        self.assertEqual(len(snapshots), 1)
        with patch.object(pub, '_sync_directory', wraps=pub._sync_directory) as synced:
            installed = self.install(c)
            self.assertGreaterEqual(synced.call_count, 4)
        self.assertEqual(installed['desired_catalog_ref']['locator'], snapshots[0].relative_to(self.root).as_posix())
        def crash_after(root, relative, raw):
            original(root, relative, raw)
            raise OSError('after version pointer')
        # A second absent version tests the pointer-written/return-lost gap.
        with patch.object(pub, '_atomic_json', side_effect=crash_after):
            with self.assertRaises(OSError): self.install(catalog('B'))
        installed_b = self.install(catalog('B'))
        self.assertEqual(installed_b['disposition'], 'immutable_only')
        self.assertIsNone(pub.catalog_token(self.root))
        self.assertEqual(installed_b['desired_catalog_ref']['sha256'], pub.version_catalog_token(self.root, 'B'))

    def test_missing_result_version_blocks_cursor_and_fake_reader_rejected(self):
        self.finish(execution(catalog('A')))
        with self.assertRaisesRegex(ValueError, 'desired version not published'):
            pub.project_accepted(self.reader, self.root)
        with self.assertRaises(TypeError): pub.project_accepted(dict(accepted=True), self.root)
        self.install(catalog('A'))
        self.assertEqual(len(pub.project_accepted(self.reader, self.root)['events']), 1)

    def test_locked_hook_expires_and_sanitizes_valid_artifact_and_generation(self):
        c = catalog('A')
        self.assertTrue(hasattr(pub, 'install_manual_desired'))
        candidate = pub.project_catalog(c, [result(dict(execution(c)['jobs'][0], attempt=1), 'success')])
        candidate.update(desired_generation='caller data', desired_execution_plan_id='caller data')
        with pub.locked_mutation(self.root) as mutation:
            installed = mutation.install_manual_desired(candidate, execution_plan_id='e'*64,
                expected_current=None, expected_version=None)
        immutable = json.loads((self.root / installed['desired_catalog_ref']['locator']).read_bytes())
        self.assertEqual(immutable['desired_execution_plan_id'], 'e'*64)
        self.assertIsNone(immutable['skins'][0]['artifact_path'])
        with self.assertRaises(RuntimeError):
            mutation.install_manual_desired(c, execution_plan_id='e'*64,
                expected_current=None, expected_version=None)

    def test_cross_version_current_and_version_views_converge_after_replay_and_crash(self):
        self.finish(execution(catalog('A')))
        for interrupted in (False, True):
            root = self.root / str(interrupted)
            pub.publish_desired(catalog('A'), root, expected_current=None, execution_plan_id='a'*64)
            pub.publish_desired(catalog('B'), root, expected_current=pub.catalog_token(root), execution_plan_id='b'*64)
            self.assertEqual(pub.catalog_token(root), pub.version_catalog_token(root, 'B'))
            before = pub.catalog_token(root)
            original = pub._atomic_json
            def crash(root, relative, raw):
                if relative == 'current.json': raise OSError('before current pointer')
                return original(root, relative, raw)
            if interrupted:
                with patch.object(pub, '_atomic_json', side_effect=crash):
                    with self.assertRaises(OSError): pub.project_accepted(self.reader, root)
                self.assertEqual(pub.catalog_token(root), before)
                self.assertEqual(pub.read_catalog(root, 'B')['skins'][0]['source_version'], 'A')
            for _ in range(2):
                page = pub.project_accepted(self.reader, root)
                self.assertEqual(len(page['events']), 1)
                self.assertEqual(pub.read_catalog(root)['skins'][0]['source_version'], 'A')
                self.assertEqual(pub.read_catalog(root, 'B')['skins'][0]['source_version'], 'A')
                self.assertEqual(pub.catalog_token(root), pub.version_catalog_token(root, 'B'))

    def test_new_multilevel_root_and_cached_ref_sync_complete_ancestry_in_order(self):
        root = Path(self.tmp.name).resolve() / 'new-parent' / 'new-root'
        expected = [root / 'catalog/A', root / 'catalog', root, *root.parents]
        refs = []
        for _ in range(2):
            with patch.object(pub, '_sync_directory', wraps=pub._sync_directory) as synced:
                refs.append(pub.stage_desired_snapshot(catalog('A'), root, execution_plan_id='e'*64))
            self.assertEqual([call.args[0] for call in synced.call_args_list], expected)
            self.assertIsNone(pub.catalog_token(root))
            self.assertIsNone(pub.version_catalog_token(root, 'A'))
        self.assertEqual(refs[0], refs[1])

    def test_ancestor_barrier_failure_blocks_ref_and_all_pointer_writers_until_healed(self):
        for operation in ('stage', 'manual', 'automatic'):
            root = Path(self.tmp.name).resolve() / operation / 'new-parent' / 'new-root'
            def publish():
                if operation == 'stage':
                    return pub.stage_desired_snapshot(catalog('A'), root, execution_plan_id='e'*64)
                if operation == 'manual':
                    return pub.install_manual_desired(catalog('A'), root, execution_plan_id='e'*64,
                        expected_current=None, expected_version=None)
                return pub.publish_desired(catalog('A'), root, execution_plan_id='e'*64, expected_current=None)
            original = pub._sync_directory
            def fail(path):
                if path == root.parent: raise OSError('ancestor barrier')
                return original(path)
            # First creation and cached immutable retry must both propagate.
            for _ in range(2):
                with patch.object(pub, '_sync_directory', side_effect=fail):
                    with self.assertRaisesRegex(OSError, 'ancestor barrier'): publish()
                self.assertIsNone(pub.catalog_token(root))
                self.assertIsNone(pub.version_catalog_token(root, 'A'))
            with patch.object(pub, '_sync_directory', wraps=pub._sync_directory) as synced:
                publish()
            self.assertIn(root.parent, [call.args[0] for call in synced.call_args_list])


if __name__ == '__main__':
    unittest.main()
