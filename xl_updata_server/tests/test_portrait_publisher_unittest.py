"""Pure publication boundaries: generated fixtures, no renderer or network."""
import copy
import hashlib
import importlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
from server_app.portraits.model import build_catalog, digest, PROFILE
from server_app.portraits.planning import plan_changes
from server_app.portraits.store import PortraitStore


class PublisherTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.stage = self.base / 'stage'; self.stage.mkdir()
        self.root = self.base / 'media'
        self.catalog = build_catalog('i', 's', 'v2', [dict(character_id='1', skin_id='2',
            family='cardspine', name='skin', **{k: 'a'*64 for k in
            ('config_fingerprint', 'dependency_fingerprint', 'tool_fingerprint')})])
        self.job = plan_changes(dict(self.catalog, skins=[]), self.catalog)[0]
        self.job['attempt'] = 1

    def publisher(self):
        try: return importlib.import_module('server_app.portraits.publisher')
        except ModuleNotFoundError: self.fail('publisher library is missing')

    def fixture(self, size=(40, 30), color=(230, 20, 50, 180)):
        path = self.stage / 'out.png'
        Image.new('RGBA', size, color).save(path)
        return dict(status='success', job_id=self.job['job_id'], staged_path=str(path),
                    width=size[0], height=size[1], sha256=hashlib.sha256(path.read_bytes()).hexdigest())

    def publish(self, result=None, **kwargs):
        return self.publisher().publish_png(self.job, result or self.fixture(), self.root,
                                            staging_root=self.stage, **kwargs)

    def test_registered_candidate_pins_full_execution_and_does_not_accept(self):
        from test_portrait_lineage_unittest import catalog, execution
        from server_app.portraits.reader import PortraitReader
        c=catalog(); p=execution(c); store=PortraitStore(self.base/'registered.db','i','s')
        pid=store.register_closed_plan(p)['plan_id']; self.job=store.claim('worker',0,10,allowed_plan_ids=[pid])
        raw=self.fixture(); raw.update({k:copy.deepcopy(self.job[k]) for k in
            ('schema_version','instance_id','source_id','version','character_id','skin_id','render_key','profile','reason','attempt','input_snapshot_digest','tool_fingerprint','source_capture_ref')})
        for key,value in [('attempt',2),('tool_fingerprint','f'*64),('input_snapshot_digest','f'*64),('source_capture_ref',{})]:
            with self.subTest(key=key),self.assertRaises(ValueError): self.publish(dict(raw,**{key:value}))
        out=self.publish(raw)
        self.assertEqual(out['artifact_render_key'],self.job['render_key'])
        self.assertEqual(out['source_capture_ref'],self.job['source_capture_ref'])
        self.assertNotEqual(out['source_capture_ref']['capture']['sha256'],out['input_snapshot_digest'])
        self.assertEqual(PortraitReader(self.base/'registered.db','i','s').read_events()['events'],[])
        self.assertFalse(store.finish(self.job['job_id'],1,out,owner='worker',now=11))
        self.assertFalse((self.root/'current.json').exists())
        self.assertEqual(PortraitReader(self.base/'registered.db','i','s').read_events()['events'],[])
        self.job=store.claim('worker',11,10,allowed_plan_ids=[pid])
        raw['attempt']=self.job['attempt']; out=self.publish(raw)
        self.assertTrue(store.finish(self.job['job_id'],self.job['attempt'],out,owner='worker',now=12))
        self.assertEqual(len(PortraitReader(self.base/'registered.db','i','s').read_events()['events']),1)

    def test_original_is_exact_and_public_metadata_has_no_local_paths(self):
        raw = self.fixture(); expected = Path(raw['staged_path']).read_bytes()
        out = self.publish(raw)
        self.assertEqual((self.root/out['archive_path']).read_bytes(), expected)
        self.assertEqual((self.root/out['artifact_path']).read_bytes(), expected)
        self.assertEqual(out['archive_profile'], PROFILE)
        self.assertEqual(out['delivery_profile'], 'original')
        self.assertEqual(out['source_version'], 'v2')
        self.assertNotIn(str(self.base), json.dumps(out))
        self.assertFalse((self.root/'current.json').exists())

    def test_rejects_corrupt_empty_transparent_and_mismatched_png(self):
        for kind in ('corrupt', 'empty', 'transparent', 'hash', 'dimensions', 'canvas'):
            with self.subTest(kind=kind):
                r = self.fixture(color=(0,0,0,0) if kind == 'transparent' else (1,2,3,255))
                if kind in ('corrupt', 'empty'):
                    Path(r['staged_path']).write_bytes(b'bad' if kind == 'corrupt' else b'')
                    r['sha256'] = hashlib.sha256(Path(r['staged_path']).read_bytes()).hexdigest()
                if kind == 'hash': r['sha256'] = 'b'*64
                if kind == 'dimensions': r['width'] += 1
                if kind == 'canvas': r['width'] = 64*1024*1024+1
                with self.assertRaises(ValueError): self.publish(r)

    def test_rejects_paths_and_job_or_result_identity(self):
        for field, value in [('staged_path', 'out.png'), ('staged_path', str(self.stage)),
                             ('job_id', 'b'*64), ('status', 'failed'), ('width', True)]:
            r = self.fixture(); r[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError): self.publish(r)
        outside = self.base/'outside.png'; outside.write_bytes((self.stage/'out.png').read_bytes())
        r = self.fixture(); r['staged_path'] = str(outside)
        with self.assertRaises(ValueError): self.publish(r)
        for field, value in [('attempt', 0), ('attempt', True), ('skin_id','02'),
                             ('profile', dict(PROFILE, scale=2.0)), ('job_id', 'c'*64)]:
            old = self.job; self.job = dict(old, **{field:value})
            with self.subTest(field=field), self.assertRaises(ValueError): self.publish()
            self.job = old
        del self.job['attempt']
        with self.assertRaises(ValueError): self.publish()

    def test_existing_wrong_digest_file_fails_closed(self):
        r = self.fixture(); dest = self.root/'archive'/f"{r['sha256']}.png"
        dest.parent.mkdir(parents=True); dest.write_bytes(b'collision')
        with self.assertRaises(ValueError): self.publish(r)
        self.assertEqual(dest.read_bytes(), b'collision')

    def test_delivery_fit_preserves_archive_and_alpha(self):
        r = self.fixture((400,300)); original = Path(r['staged_path']).read_bytes()
        out = self.publish(r, max_delivery_pixels=1200, max_delivery_bytes=200)
        self.assertEqual((self.root/out['archive_path']).read_bytes(), original)
        self.assertEqual(out['archive_sha256'], r['sha256'])
        self.assertEqual(out['delivery_profile'], 'fitted_png')
        path = self.root/out['artifact_path']
        self.assertLessEqual(path.stat().st_size, 200)
        with Image.open(path) as image:
            self.assertLessEqual(image.width*image.height, 1200)
            self.assertEqual(image.mode, 'RGBA')
            self.assertLess(image.getchannel('A').getextrema()[0], 255)
        self.assertEqual(self.publish(r, max_delivery_pixels=1200, max_delivery_bytes=200), out)

    def test_impossible_delivery_budget_rejected(self):
        for kwargs in (dict(max_delivery_bytes=1), dict(max_delivery_pixels=0), dict(max_delivery_pixels=True)):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError): self.publish(**kwargs)

    def test_thin_portrait_stays_inside_pixel_budget(self):
        out = self.publish(self.fixture((1,1000)), max_delivery_pixels=10)
        with Image.open(self.root/out['artifact_path']) as image:
            self.assertLessEqual(image.width*image.height,10)

    def test_sparse_alpha_fit_rejected_without_install_or_catalog_advance(self):
        p = self.publisher(); p.publish_catalog(self.catalog,self.root,promote=True)
        pointer = (self.root/'current.json').read_bytes()
        r = self.fixture((100,100),color=(0,0,0,0))
        with Image.open(r['staged_path']) as image:
            image.putpixel((50,50),(255,0,0,255)); image.save(r['staged_path'])
        original = Path(r['staged_path']).read_bytes()
        r['sha256'] = hashlib.sha256(original).hexdigest()
        with self.assertRaisesRegex(ValueError,'delivery.*visible|visible.*delivery'):
            self.publish(r,max_delivery_pixels=1)
        self.assertEqual(Path(r['staged_path']).read_bytes(),original)
        self.assertEqual(list((self.root/'archive').glob('*.png')),[])
        self.assertEqual(list((self.root/'artifacts').glob('*.png')),[])
        self.assertEqual((self.root/'current.json').read_bytes(),pointer)
        self.assertEqual(p.read_catalog(self.root),self.catalog)
        self.assertTrue(any(path.read_bytes()==original for path in (self.root/'archive').glob('.pending-*')))

    def test_fitted_encoded_png_revalidated_before_install(self):
        original_save = Image.Image.save
        for kind in ('corrupt','wrong_size','transparent'):
            with self.subTest(kind=kind):
                r = self.fixture()
                def faulty_save(image, stream, **kwargs):
                    if kind == 'corrupt': stream.write(b'not PNG'); return
                    size = (20,20) if kind == 'wrong_size' else image.size
                    color = (0,0,0,0) if kind == 'transparent' else (255,0,0,255)
                    with Image.new('RGBA',size,color) as encoded:
                        original_save(encoded,stream,**kwargs)
                with patch.object(Image.Image,'save',faulty_save):
                    with self.assertRaisesRegex(ValueError,'delivery'):
                        self.publish(r,max_delivery_pixels=12)
                self.assertEqual(list((self.root/'archive').glob('*.png')),[])
                self.assertEqual(list((self.root/'artifacts').glob('*.png')),[])

    def test_staged_junction_escape(self):
        outside = self.base/'outside'; outside.mkdir()
        r = self.fixture(); (outside/'out.png').write_bytes(Path(r['staged_path']).read_bytes())
        link = self.stage/'link'
        if os.name == 'nt':
            import subprocess
            subprocess.run(['cmd','/c','mklink','/J',str(link),str(outside)],check=True,capture_output=True)
            self.addCleanup(link.rmdir)
        else:
            link.symlink_to(outside,target_is_directory=True)
        r['staged_path'] = str(link/'out.png')
        with self.assertRaises(ValueError): self.publish(r)

    def test_copy_interruption_does_not_publish_or_move_pointer(self):
        p = self.publisher(); p.publish_catalog(self.catalog,self.root,promote=True)
        before = (self.root/'current.json').read_bytes()
        with patch.object(p.os,'fsync',side_effect=OSError('write interrupted')):
            with self.assertRaises(OSError): self.publish()
        self.assertEqual((self.root/'current.json').read_bytes(),before)
        self.assertEqual(list((self.root/'archive').glob('*.png')),[])

    def test_artifact_digest_collision_preserves_existing_content(self):
        r = self.fixture(); target = self.root/'artifacts'/f"{r['sha256']}.png"
        target.parent.mkdir(parents=True); target.write_bytes(b'collision')
        with self.assertRaises(ValueError): self.publish(r)
        self.assertEqual(target.read_bytes(),b'collision')

    def test_oversized_png_header_rejected_before_pixel_load(self):
        import struct
        import zlib
        p = self.stage/'out.png'
        header = struct.pack('>IIBBBBB',65536,65536,8,6,0,0,0)
        chunk = b'IHDR'+header
        p.write_bytes(b'\x89PNG\r\n\x1a\n'+struct.pack('>I',len(header))+chunk+struct.pack('>I',zlib.crc32(chunk)))
        r = dict(status='success',job_id=self.job['job_id'],staged_path=str(p),width=65536,height=65536,sha256=hashlib.sha256(p.read_bytes()).hexdigest())
        with patch.object(Image.Image,'load',side_effect=AssertionError('must not decode')):
            with self.assertRaises(ValueError): self.publish(r)

    def test_projection_retains_blocked_failed_and_baseline_ordinals(self):
        p = self.publisher()
        rows = [dict(self.catalog['skins'][0], skin_id=str(i), state=state)
                for i,state in [(2,'not_generated'),(10,'blocked'),(30,'failed')]]
        for row in rows: row.pop('render_key')
        catalog = build_catalog('i','s','v2',rows)
        projected = p.project_catalog(catalog,[])
        self.assertEqual([(r['skin_id'],r['ordinal'],r['state']) for r in projected['skins']],
                         [('2',1,'not_generated'),('10',2,'blocked'),('30',3,'failed')])

    def test_project_rejects_malformed_media_and_conflicting_accepted_attempt(self):
        p = self.publisher(); out = self.publish()
        for key,value in [('artifact_path','../escape.png'),('archive_sha256','x'),
                          ('source_version','wrong'),('width',True),('job_id','a'*64)]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                p.project_catalog(self.catalog,[dict(out,**{key:value})])
        with self.assertRaises(ValueError):
            p.project_catalog(self.catalog,[out,dict(out,diagnostics=['conflict'])])

    def test_concurrent_catalog_writers_leave_consistent_pointers(self):
        from concurrent.futures import ThreadPoolExecutor
        p = self.publisher()
        catalogs = []
        for i in range(5):
            c = copy.deepcopy(self.catalog); c['skins'][0]['diagnostics'] = [str(i)]; catalogs.append(c)
        with ThreadPoolExecutor(max_workers=5) as workers:
            pointers = list(workers.map(lambda c:p.publish_catalog(c,self.root,promote=True),catalogs))
        self.assertIn(p.read_catalog(self.root),catalogs)
        self.assertEqual(p.read_catalog(self.root),p.read_catalog(self.root,'v2'))
        for pointer,catalog in zip(pointers,catalogs):
            raw = (self.root/pointer['catalog_path']).read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(),pointer['sha256'])
            self.assertEqual(json.loads(raw),catalog)

    def test_projection_requires_current_exact_identity_and_preserves_slots(self):
        p = self.publisher(); out = self.publish()
        result = p.project_catalog(self.catalog, [out])
        self.assertEqual(result['skins'][0]['state'], 'published')
        self.assertIsNone(self.catalog['skins'][0]['artifact_path'])
        for field, value in [('version','v1'), ('render_key','b'*64), ('skin_id','3')]:
            stale = dict(out, **{field:value})
            if field == 'version': stale['source_version'] = value
            stale['job_id'] = digest([stale[k] for k in ('instance_id','source_id','version','character_id','skin_id','render_key')])
            self.assertEqual(p.project_catalog(self.catalog,[stale]), self.catalog)
        with self.assertRaises(ValueError): p.project_catalog(self.catalog,[dict(out, source_id='other')])

    def test_failed_and_pending_changes_retain_old_real_provenance(self):
        p = self.publisher(); old = p.project_catalog(self.catalog,[self.publish()])
        current = copy.deepcopy(old); current['version'] = 'v3'; current['skins'][0]['state'] = 'pending'
        result = p.project_catalog(current, [])
        self.assertEqual(result['skins'][0]['state'], 'published')
        self.assertEqual(result['skins'][0]['source_version'], 'v2')
        self.assertTrue(result['skins'][0]['diagnostics'])
        j = dict(self.job, version='v3'); j['job_id'] = digest([j[k] for k in ('instance_id','source_id','version','character_id','skin_id','render_key')])
        failed = dict(j, status='failed', diagnostics=['renderer failed'])
        row = p.project_catalog(current,[failed])['skins'][0]
        self.assertEqual(row['source_version'],'v2'); self.assertEqual(row['ordinal'],1)
        self.assertIn('renderer failed', row['diagnostics'])

    def test_store_fence_excludes_stale_attempt_from_projection(self):
        p = self.publisher(); store = PortraitStore(self.base/'jobs.db','i','s')
        j = dict(self.job); del j['attempt']
        store.enqueue_published_plan(dict(schema_version=1,instance_id='i',source_id='s',version='v2',published=True,jobs=[j]))
        first = store.claim('a',0,1); second = store.claim('b',1,10)
        self.job = first; rejected = self.publish()
        self.assertFalse(store.finish(first['job_id'],first['attempt'], rejected))
        self.assertEqual(p.project_catalog(self.catalog,[e['result'] for e in store.list_events()]),self.catalog)
        self.job = second; accepted = self.publish()
        self.assertTrue(store.finish(second['job_id'],second['attempt'],accepted))
        self.assertEqual(p.project_catalog(self.catalog,[e['result'] for e in store.list_events()])['skins'][0]['state'],'published')

    def test_immutable_catalog_pointer_and_late_old_version(self):
        p = self.publisher(); catalog = p.project_catalog(self.catalog,[self.publish()])
        pointer = p.publish_catalog(catalog,self.root,promote=True)
        self.assertEqual(pointer['catalog_path'], f"catalog/v2/{pointer['sha256']}.json")
        self.assertEqual(p.read_catalog(self.root),catalog)
        old_bytes = (self.root/pointer['catalog_path']).read_bytes()
        changed = copy.deepcopy(catalog); changed['skins'][0]['diagnostics'] = ['changed']
        next_pointer = p.publish_catalog(changed,self.root)
        self.assertNotEqual(pointer['sha256'],next_pointer['sha256'])
        self.assertEqual(p.read_catalog(self.root),catalog)
        self.assertEqual(p.read_catalog(self.root,'v2'),changed)
        self.assertEqual((self.root/pointer['catalog_path']).read_bytes(),old_bytes)
        old = dict(catalog, version='v1'); p.publish_catalog(old,self.root)
        self.assertEqual(p.read_catalog(self.root),catalog)

    def test_catalog_write_interruption_preserves_current(self):
        p = self.publisher(); p.publish_catalog(self.catalog,self.root,promote=True)
        before = (self.root/'current.json').read_bytes()
        changed = dict(self.catalog,version='v3')
        with patch.object(p.os, 'replace', side_effect=OSError('interrupted')):
            with self.assertRaises(OSError): p.publish_catalog(changed,self.root,promote=True)
        self.assertEqual((self.root/'current.json').read_bytes(),before)
        self.assertEqual(p.read_catalog(self.root),self.catalog)

    def test_current_write_interruption_keeps_old_immutable_revision(self):
        p = self.publisher(); p.publish_catalog(self.catalog,self.root,promote=True)
        changed = copy.deepcopy(self.catalog); changed['skins'][0]['diagnostics'] = ['next']
        replace = p.os.replace
        def interrupt_current(source, target):
            if Path(target).name == 'current.json': raise OSError('current write interrupted')
            return replace(source,target)
        with patch.object(p.os,'replace',side_effect=interrupt_current):
            with self.assertRaises(OSError): p.publish_catalog(changed,self.root,promote=True)
        self.assertEqual(p.read_catalog(self.root),self.catalog)
        self.assertEqual(p.read_catalog(self.root,'v2'),changed)

    def test_catalog_digest_collision_and_pointer_tampering_fail_closed(self):
        p = self.publisher(); pointer = p.publish_catalog(self.catalog,self.root,promote=True)
        path = self.root/pointer['catalog_path']; path.write_bytes(b'collision')
        before = (self.root/'current.json').read_bytes()
        with self.assertRaises(ValueError): p.publish_catalog(self.catalog,self.root,promote=True)
        self.assertEqual((self.root/'current.json').read_bytes(),before)
        with self.assertRaises(ValueError): p.read_catalog(self.root)
        pointer['catalog_path'] = 'catalog/v2/'+('b'*64)+'.json'
        (self.root/'current.json').write_text(json.dumps(pointer))
        with self.assertRaises(ValueError): p.read_catalog(self.root)


if __name__ == '__main__': unittest.main()
