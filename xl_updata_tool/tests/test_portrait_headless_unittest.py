"""Tests for the single-call, no-Qt, resource-limited portrait render entry."""
import contextlib
import io
import json
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
        for name in ('workspace', 'output', 'work'):
            (self.root / name).mkdir()
        lua = self.root / 'workspace/lua/123'
        lua.mkdir(parents=True)
        (lua / 'BaseFashion.lua').write_text(
            'return {\n  [1] = { id = "1", show_spine = "cardspine_10051_3", show_spine_position = "0,0" },\n}\n',
            encoding='utf-8')
        bundles = self.root / 'workspace/bundles/123'
        bundles.mkdir(parents=True)
        (self.root / 'workspace/assets_map.json').write_text(json.dumps(
            {'version_timestamp': 123, 'assets': []}))
        self.request = dict(schema_version=2, job_id='job-1', version='123',
            character_id='role', skin_id='game-skin',
            workspace_root=str(self.root / 'workspace'), output_root=str(self.root / 'output'),
            work_root=str(self.root / 'work'),
            dotnet_path=str(self.root / 'dotnet'), scene_cli_path=str(self.root / 'scene/PortraitSceneCLI.dll'),
            profile=dict(format='png', scale=2, scope='full'))
        for tool in (self.request['dotnet_path'], self.request['scene_cli_path']):
            Path(tool).parent.mkdir(parents=True, exist_ok=True)
            Path(tool).write_bytes(b'tool')

    def api(self):
        import importlib.util
        self.assertIsNotNone(importlib.util.find_spec('app.features.preview.portrait_headless'),
                             'headless entry is missing')
        from app.features.preview import portrait_headless
        return portrait_headless

    # ---- request validation -------------------------------------------------

    def test_profile_schema_identity_and_roots_are_strict(self):
        api = self.api()
        for changes in (dict(schema_version=1), dict(schema_version=True),
                dict(profile=dict(format='mp4', scale=2, scope='full')),
                dict(profile=dict(format='png', scale=1, scope='full')),
                dict(profile=dict(format='png', scale=2, scope='basic')),
                dict(job_id='../escape'), dict(character_id='bad id'),
                dict(version='12a'), dict(skin_id=''),
                dict(workspace_root='relative/path'),
                dict(workspace_root=str(self.root / 'output')),
                dict(output_root=str(self.root / 'workspace')),
                dict(work_root=str(self.root / 'workspace')),
                dict(dotnet_path='relative'),
                dict(scene_cli_path=str(self.root / 'missing/PortraitSceneCLI.dll')),
                dict(png_working_rows=-1), dict(png_working_rows=1025),
                dict(png_working_rows='128'), dict(png_working_rows=True),
                dict(limits={'timeout_seconds': 0}), dict(limits={'timeout_seconds': '60'}),
                dict(limits={'unknown_field': 1})):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                api.validate_request(dict(self.request, **changes))

    def test_rows_default_and_limits_default(self):
        api = self.api()
        request = api.validate_request(dict(self.request))
        self.assertEqual(request['png_working_rows'], 128)
        self.assertEqual(request['limits'].memory_max_bytes, 512 * 1024 * 1024)
        self.assertEqual(request['limits'].cpu_affinity, (0,))
        request = api.validate_request(dict(self.request, png_working_rows=0))
        self.assertEqual(request['png_working_rows'], 0)

    def test_roots_must_not_overlap_each_other(self):
        api = self.api()
        nested = self.root / 'workspace/inner'
        nested.mkdir()
        for key in ('output_root', 'work_root'):
            with self.subTest(key=key), self.assertRaises(ValueError):
                api.validate_request(dict(self.request, **{key: str(nested)}))

    def test_manual_package_input_replaces_workspace(self):
        api = self.api()
        package = self.root / 'input/package/skin.json'
        package.parent.mkdir(parents=True)
        package.write_text('{}')
        request = dict(self.request, input_root=str(self.root / 'input'),
                       package_path=str(package))
        request.pop('workspace_root')
        validated = api.validate_request(request)
        self.assertEqual(validated['package_path'], str(package.resolve()))
        with self.assertRaises(ValueError):
            api.validate_request(dict(self.request, package_path=str(self.root / 'input/package/skin.json')))

    # ---- workspace planning -------------------------------------------------

    def test_workspace_layout_and_skin_selection_are_enforced(self):
        api = self.api()
        with self.assertRaisesRegex(ValueError, 'BaseFashion'):
            api.plan_request(dict(self.request, skin_id='absent'), self.root / 'work/attempt')
        lua = self.root / 'workspace/lua/123/BaseFashion.lua'
        backup = lua.with_name(lua.name + '.bak')
        lua.rename(backup)
        with self.assertRaisesRegex(ValueError, 'BaseFashion'):
            api.plan_request(self.request, self.root / 'work/attempt')
        backup.rename(lua)
        bundles = self.root / 'workspace/bundles'
        bundles_backup = self.root / 'workspace/bundles.bak'
        bundles.rename(bundles_backup)
        with self.assertRaisesRegex(ValueError, 'bundles'):
            api.plan_request(self.request, self.root / 'work/attempt')
        bundles_backup.rename(bundles)
        (self.root / 'workspace/assets_map.json').unlink()
        with self.assertRaisesRegex(ValueError, 'catalog'):
            api.plan_request(self.request, self.root / 'work/attempt')

    # ---- render lifecycle with a fake native CLI ----------------------------

    def package(self):
        from app.features.preview.portrait_package import publish_portrait_packages
        from app.features.preview.portrait_config.asset_index import AssetIndex, IndexedAsset
        from app.features.preview.portrait_config.model import (
            AssetRef, PortraitScene, ScenePart, SpineState, RendererState, AssetDependency)
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
        scene = PortraitScene('v1', '123', 'game-skin', 'role', 'game-skin', None,
            ('bundle',), (part,), tuple(refs),
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

    def native(self, mode, schema=2, capability='valid'):
        from app.features.preview.scene_export_adapter import write_scene_descriptor
        script = self.root / 'native.py'
        script.write_text('''import json,sys,time
from pathlib import Path
from PIL import Image
capability=__CAPABILITY__
if sys.argv[1]=='--capabilities':
 if capability=='old': sys.exit(1)
 print(json.dumps(dict(phase='capabilities',protocol_version=1,descriptor_schemas=[1,2],png_working_rows_max=1024)))
 sys.exit(0)
p=json.loads(Path(sys.argv[1]).read_text())
assert p['settings']['scale'] == 2
mode=sys.argv[2]
if mode=='bounded': assert p['settings'].get('png_working_rows')==128
if p['scene_schema_version']==1: assert not p['settings'].get('png_working_rows')
if mode=='slow': time.sleep(30)
if mode in ('valid','bounded','clamped','nolog','slow'): Image.new('RGBA',(28,26),(100,30,20,255)).save(p['output'])
if mode=='transparent': Image.new('RGBA',(28,26),(0,0,0,0)).save(p['output'])
if mode=='corrupt': Path(p['output']).write_bytes(b'not png')
if mode=='failure': sys.exit(3)
if mode not in ('nolog',): print(json.dumps(dict(phase='complete',output=p['output'],width=28,height=26,bounds=[0,0,8 if mode=='clamped' else 4,3])))
'''.replace('__CAPABILITY__', repr(capability)))
        def command(job, cli, temporary_dir):
            descriptor = write_scene_descriptor(job, temporary_dir)
            data = json.loads(descriptor.read_text())
            data['scene_schema_version'] = schema
            descriptor.write_text(json.dumps(data))
            return [sys.executable, str(script), str(descriptor), mode]
        return command

    def manual_request(self):
        package = self.package()
        request = dict(self.request, input_root=str(self.root / 'input'),
                       package_path=str(package / 'skin.json'))
        request.pop('workspace_root')
        return request

    def test_manual_plan_builds_exactly_one_builtin_full_png_at_2x(self):
        api = self.api()
        request = self.manual_request()
        job, metadata_incomplete = api.plan_request(request, self.root / 'work/planning')
        self.assertEqual(metadata_incomplete, 0)
        self.assertEqual(job.settings.scale, 2)
        self.assertEqual(job.settings.format.lower(), 'png')
        self.assertEqual(job.export_mode, 'builtin')
        self.assertEqual(job.render_scope, 'full')
        self.assertFalse(job.interaction_id)
        self.assertEqual(job.sources[0].skin, 'motion_stander')
        self.assertEqual(job.sources[0].animation, 'idle')
        request['version'] = '999'
        with self.assertRaisesRegex(ValueError, 'identity'):
            api.plan_request(request, self.root / 'work/planning')

    def test_import_and_package_planning_reject_qt_in_fresh_interpreter(self):
        self.api()
        request = self.manual_request()
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
        run = subprocess.run([sys.executable, '-c', code, json.dumps(request),
            str(self.root / 'work/planning')], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)

    def test_render_success_publishes_identity_output_with_resource_evidence(self):
        api = self.api()
        request = self.manual_request()
        with (patch.object(api, 'build_scene_command', self.native('bounded')),
              patch('app.platform.portrait_render_process.resolve_systemd_run', return_value=None)):
            result = api.run_request(request)
        self.assertEqual(result['status'], 'success', result)
        self.assertEqual((result['width'], result['height']), (28, 26))
        self.assertEqual(len(result['sha256']), 64)
        target = self.root / 'output/role_game-skin.png'
        self.assertEqual(Path(result['artifact_path']), target.resolve())
        self.assertEqual(result['sha256'],
                         __import__('hashlib').sha256(target.read_bytes()).hexdigest())
        self.assertEqual(result['png_working_rows'], 128)
        self.assertFalse(result['resource']['timed_out'])
        self.assertIn('elapsed_seconds', result['resource'])
        self.assertEqual(result['resource']['limits']['memory_max_bytes'], 512 * 1024 * 1024)
        with Image.open(target) as image:
            image.load()
            self.assertEqual(image.size, (28, 26))

    def test_failure_timeout_and_bad_png_preserve_previous_artifact(self):
        api = self.api()
        request = self.manual_request()
        target = self.root / 'output/role_game-skin.png'
        target.write_bytes(b'previous')
        cases = [('failure', {}), ('corrupt', {}), ('transparent', {}),
                 ('nolog', {}), ('slow', dict(limits={'timeout_seconds': 1, 'cpu_affinity': [0]}))]
        for mode, extra in cases:
            merged = dict(request, **extra)
            with self.subTest(mode=mode), \
                    patch.object(api, 'build_scene_command', self.native(mode)), \
                    patch('app.platform.portrait_render_process.resolve_systemd_run', return_value=None):
                result = api.run_request(merged)
            self.assertEqual(result['status'], 'error', result)
            self.assertTrue(result['diagnostics'])
            self.assertEqual(target.read_bytes(), b'previous')
            self.assertNotIn('sha256', result)
            if mode == 'slow':
                self.assertTrue(result['resource']['timed_out'])

    def test_package_identity_integrity_and_family_are_enforced(self):
        api = self.api()
        request = self.manual_request()
        package = self.root / 'input/package'
        manifest = package / 'skin.json'
        original = manifest.read_text(encoding='utf-8')
        for field, value in (('character_id', 'other'), ('skin_id', 'other'),
                             ('version_timestamp', '999'),
                             ('prefab_container', 'assets/battlespine/test.prefab')):
            data = json.loads(original)
            data['scene'][field] = value
            manifest.write_text(json.dumps(data), encoding='utf-8')
            with self.subTest(field=field), self.assertRaises(ValueError):
                api.plan_request(request, self.root / 'work/planning')
        manifest.write_text(original, encoding='utf-8')
        data = json.loads(original)
        texture = package / data['resources'][0]['path']
        texture.write_bytes(b'tampered resource')
        with self.assertRaisesRegex(ValueError, 'SHA256'):
            api.plan_request(request, self.root / 'work/planning')

    def test_serial_calls_use_distinct_work_directories(self):
        api = self.api()
        request = self.manual_request()
        seen = []
        for _ in range(2):
            with patch.object(api, 'build_scene_command', self.native('valid')), \
                    patch('app.platform.portrait_render_process.resolve_systemd_run', return_value=None):
                result = api.run_request(request)
            self.assertEqual(result['status'], 'success', result)
            seen.append(result['work_directory'])
        self.assertEqual(len(set(seen)), 2)

    # ---- CLI -----------------------------------------------------------------

    def test_cli_success_writes_result_inside_work_root(self):
        api = self.api()
        request = self.manual_request()
        request_file = self.root / 'request.json'
        request_file.write_text(json.dumps(request))
        result = self.root / 'work/result.json'
        with patch.object(api, 'build_scene_command', self.native('valid')), \
                patch('app.platform.portrait_render_process.resolve_systemd_run', return_value=None):
            self.assertEqual(api.main(['--request', str(request_file), '--result', str(result)]), 0)
        payload = json.loads(result.read_text())
        self.assertEqual(payload['status'], 'success')
        with Image.open(payload['artifact_path']) as image:
            image.load()
            self.assertEqual(image.size, (28, 26))

    def test_cli_invalid_request_leaves_result_untouched(self):
        api = self.api()
        request_file = self.root / 'request.json'
        result = self.root / 'untrusted.json'
        result.write_bytes(b'previous')
        for changes in (dict(schema_version=999), dict(work_root='relative')):
            request_file.write_text(json.dumps(dict(self.request, **changes)))
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                code = api.main(['--request', str(request_file), '--result', str(result)])
            self.assertNotEqual(code, 0)
            self.assertEqual(result.read_bytes(), b'previous')
            self.assertTrue(stderr.getvalue())

    def test_cli_rejects_result_escapes_before_rendering(self):
        api = self.api()
        request = self.manual_request()
        request_file = self.root / 'work/request.json'
        request_file.write_text(json.dumps(request))
        target = self.root / 'output/role_game-skin.png'
        target.write_bytes(b'previous artifact')
        outside = self.root / 'outside.json'
        outside.write_bytes(b'outside')
        for result in (target, outside, request_file):
            with self.subTest(result=result):
                before = {p: p.read_bytes() for p in (target, outside, request_file)}
                with patch.object(api, 'build_scene_command', self.native('valid')), \
                        patch('app.platform.portrait_render_process.resolve_systemd_run', return_value=None), \
                        contextlib.redirect_stderr(io.StringIO()):
                    code = api.main(['--request', str(request_file), '--result', str(result)])
                self.assertNotEqual(code, 0)
                for path, contents in before.items():
                    self.assertEqual(path.read_bytes(), contents)

    def test_cli_error_is_explicit_json_and_nonzero(self):
        self.api()
        request_file = self.root / 'request.json'
        request_file.write_text(json.dumps(self.request))
        result = self.root / 'work/result.json'
        run = subprocess.run([sys.executable, '-m', 'app.features.preview.portrait_headless',
            '--request', str(request_file), '--result', str(result)], capture_output=True, text=True)
        self.assertNotEqual(run.returncode, 0)
        payload = json.loads(result.read_text())
        self.assertEqual(payload['status'], 'error')
        self.assertNotIn('sha256', payload)


if __name__ == '__main__':
    unittest.main()
