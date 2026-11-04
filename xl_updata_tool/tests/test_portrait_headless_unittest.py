import contextlib
import io
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image


class PortraitHeadlessTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ('input', 'output', 'work'):
            (self.root / name).mkdir()
        self.request = dict(schema_version=1, job_id='job-1', version='123',
            character_id='role', skin_id='game-skin',
            input_root=str(self.root / 'input'), output_root=str(self.root / 'output'),
            work_root=str(self.root / 'work'), package_path=str(self.root / 'input/package'),
            profile=dict(format='png', scale=2, scope='full'))

    def api(self):
        self.assertIsNotNone(importlib.util.find_spec('app.features.preview.portrait_headless'),
                             'headless entry is missing')
        from app.features.preview import portrait_headless
        return portrait_headless

    def test_working_rows_are_strict_resource_configuration(self):
        api = self.api()
        for value in (True, -1, 1025, 1.5, '128'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                api.validate_request(dict(self.request, png_working_rows=value))

    def package(self, with_sprite=False):
        from app.features.preview.portrait_package import publish_portrait_packages
        from app.features.preview.portrait_config.asset_index import AssetIndex, IndexedAsset
        from app.features.preview.portrait_config.model import (
            AssetRef, PortraitScene, ScenePart, SpineState, SpriteState, RendererState, AssetDependency)
        refs = [AssetRef('bundle', i) for i in range(1, 5)]
        material = self.root / 'material/assets'
        material.mkdir(parents=True)
        (material / 'body.skel').write_bytes(b'binary-skeleton')
        (material / 'body.atlas').write_text('body.png\nsize:2,2\nfilter:Linear,Linear\nbody\nbounds:0,0,2,2\n')
        Image.new('RGBA', (2, 2), (100, 50, 20, 128)).save(material / 'body.png')
        assets = tuple(IndexedAsset(name, 'assets/source.prefab', 'bundle', i, (), 123,
            self.root / 'source.bundle', True, '', kind) for i, name, kind in (
                (1, 'body.skel', 'TextAsset'), (2, 'body.atlas', 'TextAsset'), (3, 'body', 'Texture2D')))
        spine = SpineState(refs[0], default_animation='idle', loop=True,
            initial_skin='motion_stander', skeleton_json=refs[0], atlases=(refs[1],), texture=refs[2])
        part = ScenePart('body', refs[3], 'body', renderer=RendererState('Spine', True), spine=spine)
        parts = (part,)
        if with_sprite:
            sprite_ref = AssetRef('bundle', 6)
            refs.append(sprite_ref)
            assets += (IndexedAsset('overlay', 'assets/source.prefab', 'bundle', 6, (), 123,
                self.root / 'source.bundle', True, '', 'Sprite'),)
            parts += (ScenePart('overlay', sprite_ref, 'overlay', renderer=RendererState('Sprite', True),
                sprite=SpriteState(sprite_ref, (0, 0, 2, 2), (0.5, 0.5), refs[2])),)
        scene = PortraitScene('v1', '123', 'game-skin', 'role', 'game-skin', None,
            ('bundle',), parts, tuple(refs),
            asset_dependencies=(AssetDependency(refs[1], 'texture', refs[2]),),
            prefab_container='assets/cardspine/source.prefab')
        def metadata(*args):
            return dict(internal_skins=['default', 'motion_stander'], animations={'idle': 2.5}, query_ok=True)
        summary = publish_portrait_packages((scene,), AssetIndex(123, assets), material.parent,
            self.root / 'input', metadata_provider=metadata)
        self.assertEqual(summary.published, 1, summary)
        moved = self.root / 'input/package'
        shutil.move(str(summary.manifest_paths[0].parent), moved)
        return moved

    def test_package_sprite_is_materialized_during_real_preparation(self):
        api = self.api()
        self.package(with_sprite=True)
        job = api.plan_request(self.request, self.root / 'work/planning')
        self.assertEqual(job.sprite_sources, ())
        sprites = [source for source in job.sources if source.source_kind == 'static_sprite']
        self.assertEqual(len(sprites), 1)
        sprite = sprites[0]
        self.assertTrue(sprite.skeleton_path.is_file())
        self.assertTrue(sprite.atlas_path.is_file())
        self.assertIn('skeleton', json.loads(sprite.skeleton_path.read_text()))
        self.assertTrue(tuple(sprite.atlas_path.parent.glob('*.png')))

    def test_rejects_schema_profile_and_escape(self):
        api = self.api()
        for changes in (dict(schema_version=2), dict(schema_version=True),
                dict(profile=dict(format='mp4', scale=2, scope='full')),
                dict(profile=dict(format='png', scale=2, scope='basic')),
                dict(profile=dict(format='png', scale=1, scope='full')),
                dict(package_path=str(self.root / 'elsewhere')),
                dict(job_id='../escape')):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                api.validate_request(dict(self.request, **changes))

    def test_import_and_package_planning_reject_qt_in_fresh_interpreter(self):
        self.api()
        self.package()
        code = '''import importlib.abc, sys, json
class BlockQt(importlib.abc.MetaPathFinder):
 def find_spec(self, fullname, path=None, target=None):
  if fullname == 'PySide6' or fullname.startswith('PySide6.'):
   raise RuntimeError('Qt import forbidden: ' + fullname)
sys.meta_path.insert(0, BlockQt())
from app.features.preview.portrait_headless import plan_request
plan_request(json.loads(sys.argv[1]), sys.argv[2])
assert not any(x.startswith('PySide6') for x in sys.modules)
'''
        run = subprocess.run([sys.executable, '-c', code, json.dumps(self.request),
            str(self.root / 'work/planning')], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)

    def test_portable_plan_preserves_builtin_sources(self):
        api = self.api()
        self.package()
        job = api.plan_request(self.request, self.root / 'work/planning')
        self.assertEqual(job.settings.scale, 2)
        self.assertEqual(job.settings.format.lower(), 'png')
        self.assertEqual(job.export_mode, 'builtin')
        self.assertEqual(job.render_scope, 'full')
        self.assertFalse(job.interaction_id)
        self.assertEqual(job.sources[0].skin, 'motion_stander')
        self.assertEqual(job.sources[0].animation, 'idle')
        self.assertEqual(job.sources[0].affine, (1., 0., 0., 1., 0., 0.))
        self.request['version'] = '999'
        with self.assertRaisesRegex(ValueError, 'identity'):
            api.plan_request(self.request, self.root / 'work/planning')

    def native(self, mode, schema=1, capability='valid'):
        from app.features.preview.scene_export_adapter import write_scene_descriptor
        script = self.root / 'native.py'
        script.write_text('''import json,sys,os
from pathlib import Path
from PIL import Image
if os.environ.get('XL_PORTRAIT_RESOURCE_LOCK_FD'):
 fd=int(os.environ['XL_PORTRAIT_RESOURCE_LOCK_FD'])
 assert os.fstat(fd).st_ino==Path(os.environ['XL_PORTRAIT_RESOURCE_LOCK_PATH']).stat().st_ino
capability=__CAPABILITY__
if sys.argv[1]=='--capabilities':
 if capability=='old': sys.exit(1)
 print(json.dumps(dict(phase='capabilities',protocol_version=1,descriptor_schemas=[1,2],png_working_rows_max=True if capability=='malformed' else 1024)))
 sys.exit(0)
Path(__file__).with_suffix('.rendered').write_text('started')
p=json.loads(Path(sys.argv[1]).read_text())
assert p['settings']['scale'] == 2
mode=sys.argv[2]
if mode=='bounded': assert p['settings'].get('png_working_rows')==128
if p['scene_schema_version']==1: assert not p['settings'].get('png_working_rows')
if mode in ('valid','bounded','clamped','nolog'): Image.new('RGBA',(28,26),(100,30,20,255)).save(p['output'])
if mode=='transparent': Image.new('RGBA',(28,26),(0,0,0,0)).save(p['output'])
if mode=='corrupt': Path(p['output']).write_bytes(b'not png')
if mode=='failure': sys.exit(3)
if mode!='nolog': print(json.dumps(dict(phase='complete',output=p['output'],width=28,height=26,bounds=[0,0,8 if mode=='clamped' else 4,3])))
'''.replace('__CAPABILITY__',repr(capability)))
        def command(job, cli, temporary_dir):
            descriptor = write_scene_descriptor(job, temporary_dir)
            data=json.loads(descriptor.read_text())
            data['scene_schema_version']=schema
            descriptor.write_text(json.dumps(data))
            return [sys.executable, str(script), str(descriptor), mode]
        return command

    def test_bounded_rows_handshake_and_original_schema_are_preserved(self):
        api=self.api()
        self.package()
        self.request['png_working_rows']=128
        with patch.object(api,'build_scene_command',self.native('bounded',schema=2)):
            result=api.run_request(self.request)
        self.assertEqual(result['status'],'success',result)
        self.assertEqual(result['resource_mode'],'disk_rows')
        self.assertEqual(result['png_working_rows'],128)
        with patch.object(api,'build_scene_command',self.native('valid',schema=1,capability='old')):
            legacy=api.run_request(self.request)
        self.assertEqual(legacy['status'],'success',legacy)
        self.assertEqual(legacy['resource_mode'],'legacy_schema1')
        self.assertEqual(legacy['png_working_rows'],0)

    @unittest.skipUnless(os.name == 'posix', 'POSIX descriptor inheritance')
    def test_both_real_native_children_inherit_worker_lease(self):
        import fcntl
        api=self.api()
        self.package()
        self.request['png_working_rows']=128
        with (self.root/'lease').open('w') as lock:
            os.chmod(lock.name, 0o600)
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with patch.dict(os.environ, XL_PORTRAIT_RESOURCE_LOCK_FD=str(lock.fileno()), XL_PORTRAIT_RESOURCE_LOCK_PATH=lock.name):
                with patch.object(api,'build_scene_command',self.native('bounded',schema=2)):
                    result=api.run_request(self.request)
        self.assertEqual(result['status'],'success',result)

    def test_unsupported_bounded_native_never_starts_render(self):
        api=self.api()
        self.package()
        self.request['png_working_rows']=128
        target=self.root/'output/job-1.png'
        target.write_bytes(b'previous')
        for capability in ('old','malformed'):
            with self.subTest(capability=capability),patch.object(api,'build_scene_command',self.native('bounded',schema=2,capability=capability)):
                result=api.run_request(self.request)
                self.assertEqual(result['status'],'error',result)
                self.assertFalse((self.root/'native.rendered').exists())
                self.assertEqual(target.read_bytes(),b'previous')

    def test_real_child_png_and_failure_preserve_previous_artifact(self):
        api = self.api()
        self.package()
        target = self.root / 'output/job-1.png'
        target.write_bytes(b'previous')
        seen = []
        for mode in ('failure', 'missing', 'corrupt', 'transparent', 'clamped', 'nolog', 'valid'):
            with self.subTest(mode=mode), patch.object(api, 'build_scene_command', self.native(mode)):
                result = api.run_request(self.request)
                seen.append(result.get('work_directory'))
                if mode == 'valid':
                    self.assertEqual(result['status'], 'success', result)
                    self.assertEqual((result['width'], result['height']), (28, 26))
                    self.assertEqual(len(result['sha256']), 64)
                    # Windows temporary roots may use an 8.3 alias; publication returns the canonical path.
                    self.assertEqual(Path(result['staged_path']), target.resolve())
                    with Image.open(target) as image:
                        image.load()
                else:
                    self.assertEqual(result['status'], 'error', result)
                    self.assertTrue(result['diagnostics'])
                    self.assertEqual(target.read_bytes(), b'previous')
        self.assertEqual(len(set(seen)), 7)

    def test_package_identity_integrity_and_family_are_enforced(self):
        api = self.api()
        package = self.package()
        manifest = package / 'skin.json'
        original = manifest.read_text(encoding='utf-8')
        for field, value in (('character_id', 'other'), ('skin_id', 'other'),
                             ('version_timestamp', '999'), ('prefab_container', 'assets/battlespine/test.prefab')):
            data = json.loads(original)
            data['scene'][field] = value
            manifest.write_text(json.dumps(data), encoding='utf-8')
            with self.subTest(field=field), self.assertRaises(ValueError):
                api.plan_request(self.request, self.root / 'work/planning')
        manifest.write_text(original, encoding='utf-8')
        data = json.loads(original)
        texture = package / data['resources'][0]['path']
        texture.write_bytes(b'tampered resource')
        with self.assertRaisesRegex(ValueError, 'SHA256'):
            api.plan_request(self.request, self.root / 'work/planning')

    def test_native_clamp_and_oversized_canvas_evidence_are_rejected(self):
        api = self.api()
        self.package()
        job = api.plan_request(self.request, self.root / 'work/planning')
        for bounds in ([0, 0, 9000, 1], [0, 0, 6000, 6000], [0, 0, float('nan'), 3]):
            with self.subTest(bounds=bounds), self.assertRaises(ValueError):
                api.validate_png(self.root / 'absent.png', job,
                    dict(bounds=bounds, width=28, height=26))
    def test_cli_success_writes_metadata_inside_work_root(self):
        api = self.api()
        self.package()
        request = self.root / 'request.json'
        request.write_text(json.dumps(self.request))
        result = self.root / 'work/result.json'
        with patch.object(api, 'build_scene_command', self.native('valid')):
            self.assertEqual(api.main(['--request', str(request), '--result', str(result)]), 0)
        payload = json.loads(result.read_text())
        self.assertEqual(payload['status'], 'success')
        with Image.open(payload['staged_path']) as image:
            image.load()
            self.assertEqual(image.size, (28, 26))

    def test_cli_invalid_request_leaves_result_untouched(self):
        api = self.api()
        request = self.root / 'request.json'
        result = self.root / 'untrusted.json'
        result.write_bytes(b'previous')
        for changes in (dict(schema_version=999), dict(work_root='relative')):
            request.write_text(json.dumps(dict(self.request, **changes)))
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                code = api.main(['--request', str(request), '--result', str(result)])
            self.assertNotEqual(code, 0)
            self.assertEqual(result.read_bytes(), b'previous')
            self.assertTrue(stderr.getvalue())

    def test_cli_rejects_result_aliases_before_rendering(self):
        api = self.api()
        package = self.package()
        request = self.root / 'work/request.json'
        target = self.root / 'output/job-1.png'
        target.write_bytes(b'previous artifact')
        outside = self.root / 'outside.json'
        outside.write_bytes(b'outside')
        for mode in ('valid', 'failure'):
            for result in (target, package / 'skin.json', request, outside):
                with self.subTest(mode=mode, result=result):
                    request.write_text(json.dumps(self.request))
                    before = {p: p.read_bytes() for p in (target, package / 'skin.json', request, outside)}
                    with patch.object(api, 'build_scene_command', self.native(mode)), contextlib.redirect_stderr(io.StringIO()):
                        code = api.main(['--request', str(request), '--result', str(result)])
                    self.assertNotEqual(code, 0)
                    for path, contents in before.items():
                        self.assertEqual(path.read_bytes(), contents)
                    self.assertEqual(list((self.root / 'work').iterdir()), [request])

    def test_cli_rejects_result_symlink_escape(self):
        api = self.api()
        request = self.root / 'request.json'
        request.write_text(json.dumps(self.request))
        outside = self.root / 'outside'
        outside.mkdir()
        link = self.root / 'work/escape'
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            if sys.platform != 'win32':
                raise
            junction = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(outside)], capture_output=True, text=True)
            self.assertEqual(junction.returncode, 0, junction.stderr)
        result = link / 'result.json'
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertNotEqual(api.main(['--request', str(request), '--result', str(result)]), 0)
        self.assertFalse(result.exists())
        self.assertEqual(list((self.root / 'work').iterdir()), [link])

    def test_cli_error_is_explicit_json_and_nonzero(self):
        self.api()
        request = self.root / 'request.json'
        result = self.root / 'work/result.json'
        request.write_text(json.dumps(self.request))
        run = subprocess.run([sys.executable, '-m', 'app.features.preview.portrait_headless',
            '--request', str(request), '--result', str(result)], capture_output=True, text=True)
        self.assertNotEqual(run.returncode, 0)
        payload = json.loads(result.read_text())
        self.assertEqual(payload['status'], 'error')
        self.assertNotIn('sha256', payload)


if __name__ == '__main__':
    unittest.main()
