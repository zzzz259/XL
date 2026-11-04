import hashlib
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


class LaunchTest(unittest.TestCase):
    def test_command_selection_overrides_never_reach_process_runner(self):
        from app.platform.portrait_linux_runtime import ManagedLaunch
        from unittest.mock import Mock
        runtime = Mock(native_files=(), x_libraries=(), dynamic_profile=None)
        runtime.dotnet.path = '/trusted/dotnet'
        launch = ManagedLaunch(runtime, Path('/private/app'), __import__('time').monotonic()+5, None)
        launch.display = object()
        with patch('app.platform.portrait_linux_runtime.run_external_process') as run:
            for option, value in (('executable', '/unverified'), ('shell', True), ('close_fds', False)):
                with self.subTest(option=option), self.assertRaises(ValueError):
                    launch.run([*launch.query_prefix], **{option: value})
            self.assertFalse(run.called)

    def test_bounded_process_exposes_truncation_and_managed_output_rejects_it(self):
        from app.platform.processes import run_external_process
        from app.platform.portrait_linux_runtime import ManagedLaunch
        from app.features.preview.spine_adapter import SpineQueryRunner
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as directory:
            result = run_external_process([sys.executable, '-c',
                'import sys; sys.stdout.buffer.write(b"Skin: default\\n"*7000); sys.stderr.buffer.write(b"e"*70000)'],
                tool='probe', capture_output=True, text=True, timeout=5,
                max_output_bytes=65536, log_root=directory)
        self.assertTrue(getattr(result, 'xl_stdout_truncated', False))
        self.assertTrue(getattr(result, 'xl_stderr_truncated', False))
        self.assertEqual(result.xl_stdout_bytes, 98000)
        self.assertEqual(result.xl_stderr_bytes, 70000)
        runtime = Mock(native_files=(), x_libraries=(), dynamic_profile=None)
        runtime.dotnet.path = '/trusted/dotnet'
        launch = ManagedLaunch(runtime, Path('/private/app'), __import__('time').monotonic()+5, None)
        launch.display = object()
        with patch('app.platform.portrait_linux_runtime.run_external_process', return_value=result):
            skins = SpineQueryRunner(launch=launch).query_skins('body.skel', 'body.atlas')
            self.assertFalse(skins.ok)
            self.assertEqual(skins.skin_names, ())
            self.assertIn('truncated', skins.error)

    def test_display_readiness_requires_complete_frame_and_keeps_fd(self):
        from app.platform.portrait_linux_runtime import PrivateDisplay
        from unittest.mock import Mock
        import os
        display = PrivateDisplay(Mock())
        display.launch.remaining.return_value = 5
        display.process = Mock()
        display.process.poll.return_value = None
        read_fd, write_fd = os.pipe()
        self.addCleanup(os.close, write_fd)
        self.addCleanup(lambda: os.close(read_fd))
        display.read_fd = read_fd
        self.assertTrue(hasattr(display, '_read_display_number'))
        with patch('app.platform.portrait_linux_runtime.select.select', return_value=([read_fd], [], [])), patch(
                'app.platform.portrait_linux_runtime.os.read', side_effect=[b'1', b'23', b'\n']) as read:
            self.assertEqual(display._read_display_number(), '123')
        self.assertEqual(read.call_count, 3)
        os.fstat(read_fd)  # Xvfb can still write after the readiness frame.

    def test_display_partial_eof_oversize_and_dead_server_rejected(self):
        from app.platform.portrait_linux_runtime import PrivateDisplay
        from unittest.mock import Mock
        for chunks, dead in (([b'12', b''], False), ([b'1'*64], False), ([b'12\n13\n'], False),
                             ([b'12\n'], True), ([b''], False)):
            display = PrivateDisplay(Mock())
            display.launch.remaining.return_value = 5
            display.process = Mock()
            display.process.poll.return_value = 1 if dead else None
            display.read_fd = 99
            self.assertTrue(hasattr(display, '_read_display_number'))
            with self.subTest(chunks=chunks, dead=dead), patch(
                    'app.platform.portrait_linux_runtime.select.select', return_value=([99], [], [])), patch(
                    'app.platform.portrait_linux_runtime.os.read', side_effect=chunks), self.assertRaises((ValueError, TimeoutError)):
                display._read_display_number()

    def test_display_fd_closes_only_after_successful_reap(self):
        from app.platform.portrait_linux_runtime import PrivateDisplay
        from unittest.mock import Mock
        import os
        display = PrivateDisplay(None)
        read_fd, write_fd = os.pipe()
        self.addCleanup(os.close, write_fd)
        display.read_fd = read_fd
        process = Mock()
        process.poll.return_value = None
        def not_reaped(*args, **kwargs):
            os.fstat(read_fd)
            raise subprocess.TimeoutExpired('Xvfb', 2)
        process.wait.side_effect = not_reaped
        display.process = process
        with self.assertRaises(subprocess.TimeoutExpired):
            display.close()
        os.fstat(read_fd)
        self.assertIs(display.process, process)
        process.wait.side_effect = lambda *args, **kwargs: os.fstat(read_fd)
        display.close()
        with self.assertRaises(OSError):
            os.fstat(read_fd)
        self.assertIsNone(display.read_fd)

    def test_display_does_not_accept_late_ready_frame(self):
        from app.platform.portrait_linux_runtime import PrivateDisplay
        from unittest.mock import Mock
        display = PrivateDisplay(Mock())
        display.launch.remaining.return_value = 60
        display.process = Mock()
        display.process.poll.return_value = None
        display.read_fd = 99
        with patch('app.platform.portrait_linux_runtime.time.monotonic', side_effect=[0, 0, 16]), patch(
                'app.platform.portrait_linux_runtime.select.select', return_value=([99], [], [])), patch(
                'app.platform.portrait_linux_runtime.os.read', return_value=b'12\n'), self.assertRaises(TimeoutError):
            display._read_display_number()

    def test_launch_environment_does_not_inherit_loader_injection(self):
        from app.platform.portrait_linux_runtime import ManagedLaunch
        from unittest.mock import Mock
        import os
        runtime = Mock(native_files=(), x_libraries=(), dynamic_profile=None)
        runtime.dotnet.path = '/trusted/dotnet'
        with patch.dict(os.environ, {'LD_PRELOAD': '/bad.so', 'LD_AUDIT': '/bad.so',
                                    'DOTNET_ROOT': '/bad', 'DISPLAY': ':99'}):
            launch = ManagedLaunch(runtime, Path('/private/app'), __import__('time').monotonic()+5, None)
            self.assertFalse('LD_PRELOAD' in launch.env)
            self.assertFalse('LD_AUDIT' in launch.env)
            self.assertFalse('DISPLAY' in launch.env)
            self.assertEqual(launch.env['DOTNET_ROOT'], str(Path('/trusted')))
            self.assertEqual(os.environ['DISPLAY'], ':99')

    def test_managed_launch_rejects_escape_options(self):
        from app.platform.portrait_linux_runtime import ManagedLaunch
        from unittest.mock import Mock
        runtime = Mock(native_files=(), x_libraries=(), dynamic_profile=None)
        runtime.dotnet.path = '/trusted/dotnet'
        launch = ManagedLaunch(runtime, Path('/private/app'), __import__('time').monotonic()+5, None)
        for option in ('env', 'cwd', 'preexec_fn', 'pass_fds', 'start_new_session', 'process_group', 'cancel_tree'):
            with self.subTest(option=option), self.assertRaises(ValueError):
                launch.run([*launch.query_prefix], **{option: True})
        with self.assertRaises(ValueError):
            launch.run(['/other/dotnet', launch.query_prefix[1]])

    def test_runtime_does_not_accept_mutable_manifest_lists(self):
        from app.platform.portrait_linux_runtime import LinuxManagedRuntime, RuntimeFile
        entry = RuntimeFile('/fixed', '0' * 64)
        with self.assertRaisesRegex(ValueError, 'tuple'):
            LinuxManagedRuntime(entry, [], (), entry, entry, ())

    def test_cancelled_managed_success_is_rejected(self):
        from app.platform.portrait_linux_runtime import ManagedLaunch
        from unittest.mock import Mock
        runtime = Mock(native_files=(), x_libraries=(), dynamic_profile=None)
        runtime.dotnet.path = '/trusted/dotnet'
        launch = ManagedLaunch(runtime, Path('/private/app'), __import__('time').monotonic()+5, None)
        launch.display = object()
        result = subprocess.CompletedProcess([], 0, 'Skin: default', '')
        result.xl_cancelled = True
        with patch('app.platform.portrait_linux_runtime.run_external_process', return_value=result):
            with self.assertRaisesRegex(TimeoutError, 'cancel'):
                launch.run([*launch.query_prefix, 'query'], capture_output=True, text=True)

    def test_poisoned_display_retains_process(self):
        from app.platform.portrait_linux_runtime import PrivateDisplay
        from unittest.mock import Mock
        display = PrivateDisplay(None)
        process = Mock()
        process.poll.return_value = None
        process.wait.side_effect = subprocess.TimeoutExpired('Xvfb', 2)
        display.process = process
        with self.assertRaises(subprocess.TimeoutExpired):
            display.close()
        self.assertIs(display.process, process)

    def test_headless_explicit_launch_uses_verified_prefix(self):
        from app.features.preview import portrait_headless as api
        from unittest.mock import Mock
        launch = Mock()
        launch.native_prefix = ('/trusted/dotnet', '/private/PortraitSceneCLI.dll')
        launch.run.return_value = subprocess.CompletedProcess([], 1, '', 'failed')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ('input', 'output', 'work'):
                (root / name).mkdir()
            payload = dict(schema_version=1, job_id='job', version='1', character_id='1', skin_id='1',
                input_root=str(root/'input'), output_root=str(root/'output'), work_root=str(root/'work'),
                package_path=str(root/'input/package'), profile=api.PROFILE, png_working_rows=128)
            def descriptor(job, work):
                path = work / 'scene-test.json'
                path.write_text(__import__('json').dumps(dict(output=str(work/'render.png'),
                    scene_schema_version=2, settings={})))
                return path
            with patch.object(api, 'plan_request', return_value=object()), patch(
                    'app.features.preview.scene_export_adapter.write_scene_descriptor', side_effect=descriptor):
                result = api.run_request(payload, launch=launch)
            self.assertEqual(result['status'], 'error')
            self.assertEqual(launch.run.call_args.args[0], [*launch.native_prefix, '--capabilities'])

    def test_headless_checks_stop_after_png_validation_before_publish(self):
        from app.features.preview import portrait_headless as api
        from unittest.mock import Mock
        import json
        launch = Mock()
        launch.native_prefix = ('/trusted/dotnet', '/private/PortraitSceneCLI.dll')
        launch.run.side_effect = [subprocess.CompletedProcess([], 0, json.dumps(dict(
            phase='capabilities', protocol_version=1, descriptor_schemas=[2], png_working_rows_max=1024)), ''),
            subprocess.CompletedProcess([], 0, '', '')]
        launch.remaining.side_effect = TimeoutError('deadline')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ('input', 'output', 'work'):
                (root / name).mkdir()
            payload = dict(schema_version=1, job_id='job', version='1', character_id='1', skin_id='1',
                input_root=str(root/'input'), output_root=str(root/'output'), work_root=str(root/'work'),
                package_path=str(root/'input/package'), profile=api.PROFILE, png_working_rows=128)
            def descriptor(job, work):
                path = work / 'scene-test.json'
                path.write_text(json.dumps(dict(output=str(work/'render.png'), scene_schema_version=2, settings={})))
                return path
            with patch.object(api, 'plan_request', return_value=object()), patch(
                    'app.features.preview.scene_export_adapter.write_scene_descriptor', side_effect=descriptor), patch.object(
                    api, 'validate_png', return_value={}), patch.object(api, 'publish_scene_result') as publish:
                result = api.run_request(payload, launch=launch)
            self.assertEqual(result['status'], 'error')
            publish.assert_not_called()

    def test_prepare_private_verified_app_and_deadline(self):
        from app.platform import portrait_linux_runtime as api
        self.assertTrue(hasattr(api, 'LinuxManagedRuntime'))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source'
            source.mkdir()
            entries = []
            for name in ('SpineViewerCLI.dll', 'PortraitSceneCLI.dll',
                         'SpineViewerCLI.runtimeconfig.json', 'PortraitSceneCLI.runtimeconfig.json',
                         'SpineViewerCLI.deps.json', 'PortraitSceneCLI.deps.json'):
                path = source / name
                path.write_bytes(b'{}' if name.endswith('json') else b'fixed')
                entries.append(api.RuntimeFile(str(path), hashlib.sha256(path.read_bytes()).hexdigest()))
            runtime = api.LinuxManagedRuntime(entries[0], tuple(entries), (), entries[0], entries[0], ())
            with self.assertRaises(ValueError):
                runtime.prepare(root / 'work', deadline=0)
            # Non-FDD runtime configs must fail before a subprocess starts.
            with self.assertRaisesRegex(ValueError, 'framework'):
                runtime.prepare(root / 'work', deadline=__import__('time').monotonic() + 10)
            import json
            fixed = []
            for entry in entries:
                path = Path(entry.path)
                if path.name.endswith('.runtimeconfig.json'):
                    path.write_text(json.dumps({'runtimeOptions': {'framework': {'name': 'Microsoft.NETCore.App'}}}))
                fixed.append(api.RuntimeFile(str(path), hashlib.sha256(path.read_bytes()).hexdigest()))
            runtime = api.LinuxManagedRuntime(fixed[0], tuple(fixed), (), fixed[0], fixed[0], ())
            with self.assertRaisesRegex(ValueError, 'CLR'):
                runtime.prepare(root / 'work', deadline=__import__('time').monotonic() + 10)

    def test_display_stop_reaps_before_return(self):
        from app.platform import portrait_linux_runtime as api
        self.assertTrue(hasattr(api, 'PrivateDisplay'))
        from unittest.mock import Mock
        display = api.PrivateDisplay(None)
        process = Mock()
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired('Xvfb', 2), 0]
        display.process = process
        display.close()
        process.terminate.assert_called_once()
        process.kill.assert_called_once()
        self.assertEqual(process.wait.call_count, 2)
        self.assertIsNone(display.process)

    def test_explicit_query_prefix_and_failed_table(self):
        from app.features.preview.spine_adapter import SpineQueryRunner
        calls = []

        class Launch:
            query_prefix = ('/trusted/dotnet', '/private/SpineViewerCLI.dll')

            def run(self, command, **kwargs):
                calls.append((command, kwargs))
                return subprocess.CompletedProcess(command, 0, 'unknown output', '')

        runner = SpineQueryRunner(launch=Launch())
        result = runner.query_skins('body.skel', 'body.atlas')
        self.assertFalse(result.ok)
        self.assertEqual(calls[0][0][:2], list(Launch.query_prefix))
        self.assertEqual(calls[0][0][-1], '--skin')
        self.assertTrue(Path(calls[0][0][3]).is_absolute())

    def test_provider_propagates_launch_and_actual_tables(self):
        from app.features.preview.portrait_package import query_portrait_metadata
        calls = []

        class Launch:
            query_prefix = ('/trusted/dotnet', '/private/SpineViewerCLI.dll')

            def run(self, command, **kwargs):
                calls.append(command)
                output = 'Skin: default' if command[-1] == '--skin' else 'bad table'
                return subprocess.CompletedProcess(command, 0, output, '')

        result = query_portrait_metadata('body.skel', 'body.atlas', launch=Launch())
        self.assertFalse(result['query_ok'])
        self.assertEqual(result['internal_skins'], ['default'])
        self.assertEqual([c[-1] for c in calls], ['--skin', '--animation'])

    def test_explicit_log_root(self):
        from app.platform.processes import run_external_process
        with tempfile.TemporaryDirectory() as directory:
            result = run_external_process([sys.executable, '-c', 'print("ok")'],
                tool='probe', log_root=directory, capture_output=True, text=True, timeout=5)
            self.assertEqual(result.stdout.strip(), 'ok')
            self.assertTrue(Path(result.xl_log_dir).is_relative_to(directory))
            self.assertTrue((Path(result.xl_log_dir) / 'command.json').is_file())

    def test_runtime_identity_is_frozen_and_hash_checked(self):
        self.assertIsNotNone(importlib.util.find_spec('app.platform.portrait_linux_runtime'))
        from app.platform.portrait_linux_runtime import RuntimeFile
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'tool'
            path.write_bytes(b'fixed')
            entry = RuntimeFile(str(path), hashlib.sha256(b'fixed').hexdigest())
            self.assertEqual(entry.verify(), path)
            with self.assertRaises(AttributeError):
                entry.path = 'changed'
            path.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'hash'):
                entry.verify()
            with self.assertRaises(ValueError):
                RuntimeFile('relative', entry.sha256).verify()


if __name__ == '__main__':
    unittest.main()
