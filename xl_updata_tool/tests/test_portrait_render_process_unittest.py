import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.platform.portrait_render_process import (
    RenderProcessLimits,
    resolve_systemd_run,
    run_render_process,
    systemd_scope_command,
)


class RenderProcessLimitsTest(unittest.TestCase):
    def test_defaults_match_single_skin_budget(self):
        limits = RenderProcessLimits()
        self.assertEqual(limits.memory_max_bytes, 512 * 1024 * 1024)
        self.assertEqual(limits.memory_high_bytes, 384 * 1024 * 1024)
        self.assertEqual(limits.cpu_affinity, (0,))

    def test_rejects_broken_limits(self):
        for changes in (
            dict(memory_max_bytes=True),
            dict(memory_max_bytes=1024),
            dict(memory_max_bytes='512'),
            dict(memory_high_bytes=0),
            dict(memory_high_bytes=512 * 1024 * 1024 + 1),
            dict(cpu_affinity=()),
            dict(cpu_affinity=(True,)),
            dict(cpu_affinity=(-1,)),
            dict(cpu_affinity='0'),
            dict(timeout_seconds=0.2),
            dict(timeout_seconds='30'),
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                RenderProcessLimits(**changes)


class SystemdScopeCommandTest(unittest.TestCase):
    def test_scope_command_carries_resource_properties(self):
        limits = RenderProcessLimits(cpu_affinity=(1,), timeout_seconds=600)
        argv = systemd_scope_command(['/bin/render', 'job.json'], 'fallstar-portrait-abc', limits)
        self.assertEqual(argv[0], 'systemd-run')
        self.assertIn('--wait', argv)
        self.assertIn('--unit=fallstar-portrait-abc', argv)
        joined = ' '.join(argv)
        self.assertIn(f'MemoryMax={512 * 1024 * 1024}', joined)
        self.assertIn(f'MemoryHigh={384 * 1024 * 1024}', joined)
        self.assertIn('MemorySwapMax=0', joined)
        self.assertIn('CPUAffinity=1', joined)
        self.assertIn('NoNewPrivileges=yes', joined)
        self.assertLess(argv.index('--'), len(argv) - 2)
        self.assertEqual(argv[argv.index('--') + 1:], ['/bin/render', 'job.json'])

    def test_scope_command_sets_runtime_max_sec(self):
        """systemd 单元自身必须有兜底超时：即使内层 Python 被杀，
        原生单元也会在 timeout+60s 后由 systemd 自动终止。"""
        limits = RenderProcessLimits(timeout_seconds=600)
        argv = systemd_scope_command(['/bin/render'], 'unit-t', limits)
        self.assertIn('RuntimeMaxSec=660', ' '.join(argv))
        default_argv = systemd_scope_command(['/bin/render'], 'unit-d', RenderProcessLimits())
        self.assertIn(f'RuntimeMaxSec={900 + 60}', ' '.join(default_argv))

    def test_scope_command_user_manager_flag(self):
        limits = RenderProcessLimits()
        user_argv = systemd_scope_command(['/bin/render'], 'unit-u', limits, user=True)
        root_argv = systemd_scope_command(['/bin/render'], 'unit-r', limits, user=False)
        self.assertEqual(user_argv[1], '--user')
        self.assertNotIn('--user', root_argv)
        self.assertEqual(user_argv[2], '--wait')
        self.assertEqual(root_argv[1], '--wait')

    def test_resolve_systemd_run_prefers_host_tool(self):
        self.assertIsNone(resolve_systemd_run('never'))
        self.assertIsNone(resolve_systemd_run(None))
        with patch('app.platform.portrait_render_process.os.name', 'posix'), \
                patch('app.platform.portrait_render_process.shutil.which', return_value='/usr/bin/systemd-run'):
            self.assertEqual(resolve_systemd_run('auto'), '/usr/bin/systemd-run')
        with patch('app.platform.portrait_render_process.os.name', 'posix'), \
                patch('app.platform.portrait_render_process.shutil.which', return_value=None):
            self.assertIsNone(resolve_systemd_run('auto'))


class RunRenderProcessTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.limits = RenderProcessLimits(timeout_seconds=10, cpu_affinity=(0,))

    def test_fallback_runs_one_child_and_reports_exit_code(self):
        with patch('app.platform.portrait_render_process.resolve_systemd_run', return_value=None):
            result = run_render_process(
                [sys.executable, '-c', 'import sys; sys.exit(3)'],
                work_dir=self.root / 'work', env={}, limits=self.limits)
        self.assertEqual(result.returncode, 3)
        self.assertFalse(result.timed_out)
        self.assertIsNone(result.peak_memory_bytes)
        self.assertIsNone(result.unit)

    def test_fallback_applies_cpu_affinity_on_posix(self):
        from pathlib import PureWindowsPath
        from types import SimpleNamespace
        from app.platform.portrait_render_process import _run_fallback

        with patch('app.platform.processes.run_external_process') as run, \
                patch('app.platform.portrait_render_process.os.name', 'posix'), \
                patch('app.platform.portrait_render_process.Path', PureWindowsPath):
            run.return_value = SimpleNamespace(
                returncode=0, xl_log_dir=str(self.root / 'work' / 'process-logs'))
            result = _run_fallback(
                [sys.executable, '-c', 'pass'], work_dir=self.root / 'work', env={},
                limits=RenderProcessLimits(cpu_affinity=(1,)))

        self.assertEqual(result.returncode, 0)
        set_affinity = run.call_args.kwargs['preexec_fn']
        with patch('app.platform.portrait_render_process.os.sched_setaffinity', create=True) as set_affinity_mock:
            set_affinity()
        set_affinity_mock.assert_called_once_with(0, {1})

    def test_timeout_kills_process_tree_and_marks_timed_out(self):
        marker = self.root / 'child-alive'
        with patch('app.platform.portrait_render_process.resolve_systemd_run', return_value=None):
            result = run_render_process(
                [sys.executable, '-c',
                 f'from pathlib import Path; Path({str(marker)!r}).write_text("x"); import time; time.sleep(30)'],
                work_dir=self.root / 'work', env={}, limits=RenderProcessLimits(timeout_seconds=1))
        self.assertTrue(result.timed_out)

    def test_empty_command_rejected_before_any_process(self):
        with self.assertRaises(ValueError):
            run_render_process([], work_dir=self.root / 'work', env={}, limits=self.limits)

    def test_environment_scrubs_loader_injection(self):
        with patch('app.platform.portrait_render_process.resolve_systemd_run', return_value=None), \
                patch('app.platform.portrait_render_process._run_fallback') as fallback:
            fallback.return_value = None
            env = dict(os.environ, LD_PRELOAD='/bad.so', LD_AUDIT='/bad.so',
                       DOTNET_STARTUP_HOOKS='/bad.dll')
            run_render_process([sys.executable, '-c', 'pass'], work_dir=self.root / 'work',
                               env=env, limits=self.limits)
        passed = fallback.call_args.kwargs['env']
        self.assertNotIn('LD_PRELOAD', passed)
        self.assertNotIn('LD_AUDIT', passed)
        self.assertNotIn('DOTNET_STARTUP_HOOKS', passed)

    def test_systemd_path_queries_peak_memory_and_result(self):
        class _Proc:
            returncode = 0

            def poll(self):
                return 0

            def wait(self, timeout=None):
                return 0

            def kill(self):
                return None

        show = subprocess.CompletedProcess(
            ['systemctl'], 0, stdout='404746240\n0\nsuccess\n')
        with patch('app.platform.portrait_render_process.resolve_systemd_run', return_value='/usr/bin/systemd-run'), \
                patch('app.platform.portrait_render_process.subprocess.Popen', return_value=_Proc()) as popen, \
                patch('app.platform.portrait_render_process.subprocess.run', return_value=show) as run:
            result = run_render_process(
                ['/bin/render', 'job.json'], work_dir=self.root / 'work', env={}, limits=self.limits)
        argv = popen.call_args.args[0]
        self.assertEqual(argv[0], '/usr/bin/systemd-run')
        self.assertTrue(any(str(a).startswith('--unit=fallstar-portrait-') for a in argv))
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.peak_memory_bytes, 404746240)
        self.assertFalse(result.timed_out)
        self.assertIsNotNone(result.unit)
        shown_unit = run.call_args_list[0].args[0][2]
        self.assertEqual(result.unit, shown_unit)

    def test_systemd_timeout_kills_unit_before_show(self):
        waits = {'count': 0}

        class _SlowProcess:
            returncode = None

            def poll(self):
                waits['count'] += 1
                return None

            def wait(self, timeout=None):
                return None

            def kill(self):
                return None

        with patch('app.platform.portrait_render_process.resolve_systemd_run', return_value='/usr/bin/systemd-run'), \
                patch('app.platform.portrait_render_process.subprocess.Popen', return_value=_SlowProcess()), \
                patch('app.platform.portrait_render_process.subprocess.run', return_value=subprocess.CompletedProcess([], 0)) as run:
            result = run_render_process(
                ['/bin/render'], work_dir=self.root / 'work', env={},
                limits=RenderProcessLimits(timeout_seconds=1))
        self.assertTrue(result.timed_out)
        self.assertIsNone(result.peak_memory_bytes)
        commands = [call.args[0] for call in run.call_args_list]
        # The unit is killed and stopped on timeout.
        self.assertTrue(any(cmd[:2] == ['systemctl', 'kill'] for cmd in commands))
        self.assertTrue(any(cmd[:2] == ['systemctl', 'stop'] for cmd in commands))


if __name__ == '__main__':
    unittest.main()
