"""Resource primitives; Linux integration tests use only small owned children."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch


@unittest.skipUnless(sys.platform == 'linux', 'Linux resource control fixtures')
class ExecutorTests(unittest.TestCase):
    def api(self):
        self.assertIsNotNone(importlib.util.find_spec('server_app.portraits.executor'),
                             'resource executor is missing')
        from server_app.portraits import executor
        return executor

    def test_policy_validation(self):
        e = self.api()
        with tempfile.TemporaryDirectory() as d:
            args = dict(lock_path=Path(d)/'lease', updater_status_paths=(Path(d)/'status',), disk_roots=(Path(d),))
            p = e.ResourcePolicy(**args)
            self.assertEqual(p.memory_bytes, 536870912)
            for value in (True, 0, -1, float('inf'), float('nan'), '10'):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    e.ResourcePolicy(**args, poll_seconds=value)
            with self.assertRaises(ValueError):
                e.ResourcePolicy(**{**args, 'updater_status_paths': ()})
            with self.assertRaises(ValueError):
                e.ResourcePolicy(**{**args, 'lock_path': Path('relative')})

    def test_effective_controller_limits(self):
        e = self.api()
        with tempfile.TemporaryDirectory() as d:
            r = Path(d); (r/'child').mkdir()
            def put(name, root, child):
                (r/name).write_text(root); (r/'child'/name).write_text(child)
            put('memory.max', '268435456', 'max')
            put('memory.swap.max', '0', 'max')
            self.assertEqual(e._controller_limits(r/'child', r, 2, 1), (268435456, 0))
            put('memory.max', 'max', 'max')
            with self.assertRaises(e.BoundaryError): e._controller_limits(r/'child', r, 2, 1)
            put('memory.max', 'bad', '268435456')
            with self.assertRaises(e.BoundaryError): e._controller_limits(r/'child', r, 2, 0)
            put('memory.limit_in_bytes', '268435456', '9223372036854771712')
            put('memory.memsw.limit_in_bytes', '268435456', '9223372036854771712')
            put('memory.use_hierarchy', '1', '1')
            self.assertEqual(e._controller_limits(r/'child', r, 1, 0), (268435456, 0))
            with self.assertRaises(e.BoundaryError): e._controller_limits(r/'child', r, 1, 1)
            put('memory.use_hierarchy', '0', '1')
            with self.assertRaises(e.BoundaryError): e._controller_limits(r/'child', r, 1, 0)

    def test_actual_membership_mount_mapping(self):
        e = self.api()
        mounts = '30 20 0:29 / /sys/fs/cgroup rw - cgroup2 cgroup rw\n'
        self.assertEqual(e._memory_location('0::/a/b\n', mounts), (Path('/sys/fs/cgroup/a/b'), Path('/sys/fs/cgroup'), 2))
        for membership in ('0::/../escape\n', '0::/a\n0::/b\n', ''):
            with self.assertRaises(e.BoundaryError): e._memory_location(membership, mounts)
        with self.assertRaises(e.BoundaryError):
            e._memory_location('0::/a', mounts.replace('/ /sys', '/hidden /sys'))

    def test_v2_true_root_without_nonroot_controls(self):
        e = self.api()
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); leaf = root/'service'; leaf.mkdir()
            (leaf/'memory.max').write_text('268435456')
            (leaf/'memory.swap.max').write_text('0')
            # Missing files alone never establish that the mount is a true root.
            with self.assertRaises((e.BoundaryError, OSError)):
                e._controller_limits(leaf, root, 2, 1)
            self.assertEqual(e._controller_limits(leaf, root, 2, 1, hierarchy_root=True), (268435456, 0))
            (leaf/'memory.max').write_text('max')
            with self.assertRaises(e.BoundaryError): e._controller_limits(leaf, root, 2, 1, hierarchy_root=True)
            with self.assertRaises(e.BoundaryError): e._controller_limits(root, root, 2, 1, hierarchy_root=True)
            (leaf/'memory.max').unlink()
            with self.assertRaises((e.BoundaryError, OSError)): e._controller_limits(leaf, root, 2, 1, hierarchy_root=True)
            (leaf/'memory.max').write_text('268435456')
            (root/'memory.max').write_text('garbage')
            with self.assertRaises(e.BoundaryError): e._controller_limits(leaf, root, 2, 1)
            (root/'memory.max').write_text('268435456')
            # A namespace-exposed nonroot missing only swap must not be skipped.
            with self.assertRaises((e.BoundaryError, OSError)): e._controller_limits(leaf, root, 2, 1)

    def test_namespace_identity_is_required_for_root_exemption(self):
        e = self.api()
        from types import SimpleNamespace
        self.assertTrue(hasattr(e, '_initial_cgroup_namespace'))
        for inode, expected in ((4026531835, True), (4026533000, False)):
            with patch.object(e.os, 'stat', return_value=SimpleNamespace(st_ino=inode)):
                self.assertEqual(e._initial_cgroup_namespace(), expected)
        with patch.object(e.os, 'stat', side_effect=OSError('unreadable namespace')):
            with self.assertRaises(OSError): e._initial_cgroup_namespace()


@unittest.skipUnless(sys.platform == 'linux', 'Linux kernel integration')
class LinuxExecutorTests(ExecutorTests):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ.get('XL_EXECUTOR_TEST_ROOT', '/var/tmp'))
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.e = self.api()
        self.status = self.root/'status'; self.status.write_text('{"updating":false}')
        self.policy = self.e.ResourcePolicy(lock_path=self.root/'lease', updater_status_paths=(self.status,), disk_roots=(self.root,), poll_seconds=.02, term_seconds=.15, kill_seconds=.5)

    def test_admission_strict_status(self):
        for content in ('{}', '{"updating":0}', '{', '{"updating":true}'):
            self.status.write_text(content)
            with self.assertRaises(self.e.Deferred): self.e.check_admission(self.policy)
        self.status.unlink()
        with self.assertRaises(self.e.Deferred): self.e.check_admission(self.policy)

    def test_exiting_proc_stat_race(self):
        with patch.object(self.e.Path, 'iterdir', return_value=iter([Path('/proc/123')])):
            with patch.object(self.e, '_read', side_effect=ProcessLookupError()):
                self.assertFalse(self.e._group_alive(123))

    def test_status_read_rejects_fifo_and_duplicate_keys(self):
        self.assertTrue(hasattr(self.e, '_read_status'), 'bounded regular-file status reader missing')
        fifo = self.root/'fifo'; os.mkfifo(fifo)
        with self.assertRaises(self.e.Deferred): self.e._read_status(fifo)
        self.status.write_text('{"updating":true,"updating":false}')
        with self.assertRaises(self.e.Deferred): self.e._read_status(self.status)

    def test_admission_memory_disk_and_tmpfs(self):
        with patch.object(self.e, '_meminfo', return_value={'MemAvailable':self.policy.admission_bytes-1}):
            with self.assertRaises(self.e.Deferred): self.e.check_admission(self.policy)
        with patch.object(self.e, '_disk_free', return_value=self.policy.disk_free_bytes-1):
            with self.assertRaises(self.e.Deferred): self.e.check_admission(self.policy)
        with patch.object(self.e, '_read', return_value='1 0 0:1 / /fake rw - tmpfs tmpfs rw'):
            with patch.object(self.e.Path, 'resolve', return_value=Path('/fake')):
                with self.assertRaises(self.e.Deferred): self.e._disk_free(Path('/fake'))

    def test_fail_closed_affinity_and_unsafe_lease(self):
        with patch.object(self.e.os, 'sched_setaffinity'), patch.object(self.e.os, 'sched_getaffinity', return_value={0,1}):
            with self.assertRaises(self.e.BoundaryError): self.e.verify_resource_boundary(self.policy)
        target = self.root/'target'; target.write_text('')
        link = self.root/'link'; link.symlink_to(target)
        with self.assertRaises(OSError): self.e._open_lease(link)
        folder = self.root/'folder'; folder.mkdir()
        with self.assertRaises((OSError,self.e.BoundaryError)): self.e._open_lease(folder)

    def test_metadata_process_real_fd_and_default_cancellation_semantics(self):
        tool = Path(__file__).resolve().parents[2]/'xl_updata_tool'
        sys.path.insert(0, str(tool))
        from app.platform.processes import run_external_process
        try:
            with self.e.ResourceSlot(self.policy) as slot:
                env = dict(os.environ, XL_PORTRAIT_RESOURCE_LOCK_FD=str(slot.fd), XL_PORTRAIT_RESOURCE_LOCK_PATH=str(self.policy.lock_path))
                code = "import os;fd=int(os.environ['XL_PORTRAIT_RESOURCE_LOCK_FD']);os.fstat(fd);print(os.getpgrp())"
                result = run_external_process([sys.executable,'-c',code],tool='lease-proof',env=env,capture_output=True,text=True,timeout=2)
                self.assertEqual(int(result.stdout.strip()), os.getpgrp())
                with self.assertRaises(ValueError): run_external_process([sys.executable,'-c','pass'],tool='lease-proof',env=env,cancel_tree=True,timeout=2)
            result = run_external_process([sys.executable,'-c','import os;print(os.getpgrp())'],tool='desktop-proof',capture_output=True,text=True,timeout=2)
            self.assertEqual(int(result.stdout.strip()), os.getpgrp())
            result = run_external_process([sys.executable,'-c','import os;print(os.getpgrp(),os.getpid())'],tool='desktop-tree-proof',capture_output=True,text=True,cancel_tree=True,timeout=2)
            self.assertEqual(*map(int,result.stdout.split()))
        finally: sys.path.remove(str(tool))

    def test_kernel_slot_and_inherited_affinity(self):
        boundary = self.e.verify_resource_boundary(self.policy)
        self.assertLessEqual(boundary.memory_bytes, 536870912)
        self.assertEqual(os.sched_getaffinity(0), {1})
        with self.e.ResourceSlot(self.policy) as slot:
            with self.assertRaises(self.e.Deferred):
                with self.e.ResourceSlot(self.policy): pass
            result = slot.run_stage([sys.executable, '-c', 'import os,json;print(json.dumps([sorted(os.sched_getaffinity(0)),open("/proc/self/cgroup").read()]))'], cwd=self.root, env=os.environ, timeout=2, log_root=self.root/'log')
            self.assertEqual(result.returncode, 0)
            affinity, cgroup = json.loads((self.root/'log'/'stdout.log').read_text())
            self.assertEqual(affinity, [1])
            self.assertEqual(cgroup, Path('/proc/self/cgroup').read_text())

    def test_timeout_stop_and_update_cancel_owned_group(self):
        for reason in ('timeout', 'stop', 'update'):
            self.status.write_text('{"updating":false}')
            event = threading.Event()
            trigger = None
            if reason != 'timeout':
                def update():
                    temporary = self.root/'status.new'
                    temporary.write_text('{"updating":true}')
                    os.replace(temporary, self.status)
                trigger = threading.Timer(.15, event.set if reason == 'stop' else update)
                trigger.start()
            try:
                with self.e.ResourceSlot(self.policy) as slot:
                    result = slot.run_stage([sys.executable, '-c', 'import time;time.sleep(10)'], cwd=self.root, env=os.environ, timeout=.3 if reason == 'timeout' else 2, log_root=self.root/reason, stop_event=event)
                    self.assertEqual(result.reason, reason)
            finally:
                if trigger: trigger.join()

    def test_leader_exit_descendant_and_bounded_logs(self):
        script = 'import subprocess,sys;subprocess.Popen([sys.executable,"-c","import time;time.sleep(10)"]);print("x"*2000000)'
        with self.e.ResourceSlot(self.policy) as slot:
            result = slot.run_stage([sys.executable, '-c', script], cwd=self.root, env=os.environ, timeout=2, log_root=self.root/'logs')
            self.assertEqual(result.reason, 'descendants')
            self.assertEqual((self.root/'logs'/'stdout.log').stat().st_size, self.policy.log_bytes)

    def test_cleanup_does_not_signal_other_processes(self):
        import subprocess
        other = subprocess.Popen([sys.executable,'-c','import time;time.sleep(10)'])
        try:
            with self.e.ResourceSlot(self.policy) as slot:
                result = slot.run_stage([sys.executable,'-c','import time;time.sleep(10)'],cwd=self.root,env=os.environ,timeout=.1,log_root=self.root/'isolated')
                self.assertEqual(result.reason, 'timeout')
            self.assertIsNone(other.poll())
        finally:
            other.terminate(); other.wait(timeout=2)

    def test_failed_cleanup_keeps_lease(self):
        from unittest.mock import Mock
        slot = self.e.ResourceSlot(self.policy).__enter__()
        fake = Mock(pid=99999999)
        try:
            with patch.object(self.e, '_group_alive', return_value=True), patch.object(self.e.os, 'killpg'):
                with self.assertRaises(self.e.CleanupError): slot._terminate(fake)
            with self.assertRaises(self.e.CleanupError): slot.__exit__(None,None,None)
            self.assertIsNotNone(slot.fd)
        finally:
            os.close(slot.fd)

    def test_slot_reentry_preserves_original_descriptor(self):
        slot = self.e.ResourceSlot(self.policy).__enter__(); original = slot.fd
        try:
            with self.assertRaises(self.e.BoundaryError): slot.__enter__()
            self.assertEqual(slot.fd, original)
        finally:
            os.close(original); slot.fd = None

    def test_slot_is_thread_confined(self):
        errors = []
        with self.e.ResourceSlot(self.policy) as slot:
            def other_thread():
                try: slot.run_stage([sys.executable,'-c','pass'],cwd=self.root,env=os.environ,timeout=2,log_root=self.root/'thread')
                except self.e.BoundaryError as exc: errors.append(exc)
            thread = threading.Thread(target=other_thread); thread.start(); thread.join(timeout=3)
            self.assertEqual(len(errors), 1)

    def test_reader_start_failures_terminate_real_child(self):
        real_popen = self.e.subprocess.Popen; real_start = threading.Thread.start; real_thread = threading.Thread
        for failed_reader, failure in ((1, 'start'), (2, 'start'), (1, 'construct'), (2, 'construct'), (1, 'after-start'), (2, 'after-start')):
            with self.subTest(failed_reader=failed_reader, failure=failure):
                children = []; started = []; streams = []; calls = 0
                def launch(*a, **kw):
                    process = real_popen(*a, **kw)
                    children.append(process); streams.extend((process.stdout, process.stderr))
                    return process
                def start(thread):
                    nonlocal calls
                    if failure == 'construct':
                        real_start(thread); started.append(thread); return
                    calls += 1
                    if calls == failed_reader and failure == 'start': raise RuntimeError('reader start failed')
                    real_start(thread); started.append(thread)
                    if calls == failed_reader: raise RuntimeError('reader start failed')
                def construct(*args, **kwargs):
                    nonlocal calls
                    if failure == 'construct':
                        calls += 1
                        if calls == failed_reader: raise RuntimeError('reader start failed')
                    return real_thread(*args, **kwargs)
                slot = self.e.ResourceSlot(self.policy).__enter__()
                try:
                    with patch.object(self.e.subprocess, 'Popen', side_effect=launch), patch.object(real_thread, 'start', start), patch.object(self.e.threading, 'Thread', side_effect=construct):
                        with self.assertRaisesRegex(RuntimeError, 'reader start failed'):
                            slot.run_stage([sys.executable,'-c','import time;time.sleep(20)'],cwd=self.root,env=os.environ,timeout=2,log_root=self.root/f'failed-reader-{failed_reader}-{failure}')
                    self.assertIsNotNone(children[0].poll(), 'launched child was not reaped')
                    self.assertIsNone(slot.process)
                    self.assertTrue(all(stream.closed for stream in streams))
                    self.assertTrue(all(not thread.is_alive() for thread in started))
                    slot.__exit__(None,None,None)
                finally:
                    # Test-owned emergency cleanup makes RED bounded too.
                    for child in children:
                        if child.poll() is None:
                            os.killpg(child.pid, 9); child.wait(timeout=2)
                    for thread in started: thread.join(timeout=2)
                    for stream in streams:
                        if not stream.closed: stream.close()
                    if slot.fd is not None: os.close(slot.fd); slot.fd = None

    def test_unowned_pipe_close_error_still_reaps_and_closes_all_outputs(self):
        real_popen = self.e.subprocess.Popen; real_fdopen = self.e.os.fdopen
        children = []; outputs = []
        class BadClose:
            def __init__(self, stream): self.stream = stream
            def close(self):
                self.stream.close()
                raise OSError('pipe close failed')
        def launch(*args, **kwargs):
            child = real_popen(*args, **kwargs); children.append(child)
            child.stdout = BadClose(child.stdout)
            return child
        def open_output(*args, **kwargs):
            stream = real_fdopen(*args, **kwargs); outputs.append(stream); return stream
        slot = self.e.ResourceSlot(self.policy).__enter__()
        try:
            with patch.object(self.e, 'check_admission'), patch.object(self.e.subprocess, 'Popen', side_effect=launch), patch.object(self.e.os, 'fdopen', side_effect=open_output), patch.object(threading.Thread, 'start', side_effect=RuntimeError('reader start failed')):
                with self.assertRaises((RuntimeError, self.e.BoundaryError, OSError)):
                    slot.run_stage([sys.executable,'-c','import time;time.sleep(20)'],cwd=self.root,env=os.environ,timeout=2,log_root=self.root/'close-error')
            self.assertIsNotNone(children[0].poll())
            self.assertTrue(all(output.closed for output in outputs), 'unowned diagnostics leaked')
            self.assertTrue(children[0].stderr.closed, 'second pipe cleanup skipped')
            self.assertIsNone(slot.process, 'confirmed child cleanup retained process')
            self.assertFalse(slot.poisoned)
            slot.__exit__(None,None,None)
        finally:
            for child in children:
                if child.poll() is None: os.killpg(child.pid, 9); child.wait(timeout=2)
                child.stderr.close()
            for output in outputs: output.close()
            if slot.fd is not None: os.close(slot.fd); slot.fd = None

    def test_diagnostic_write_failure_is_reported(self):
        real_open = self.e.os.fdopen
        class FailedLog:
            def __init__(self, file): self.file=file
            def write(self, data): raise OSError('test disk full')
            def close(self): self.file.close()
        def open_log(*a, **kw): return FailedLog(real_open(*a, **kw))
        with self.e.ResourceSlot(self.policy) as slot:
            with patch.object(self.e.os, 'fdopen', side_effect=open_log):
                # Status reads also use fdopen; keep the test focused on log I/O.
                with patch.object(self.e, 'check_admission'):
                    with self.assertRaises(self.e.BoundaryError):
                        slot.run_stage([sys.executable,'-c','print("diagnostic")'],cwd=self.root,env=os.environ,timeout=2,log_root=self.root/'badlog')

    def test_batch_pause_interruptible(self):
        event = threading.Event(); seen = []
        def attempt(n):
            seen.append(n)
            if n == 4: event.set()
        self.assertEqual(self.e.run_batch(range(9), attempt, event), 5)
        self.assertEqual(seen, list(range(5)))

    def test_dispatcher_crash_grandchild_keeps_kernel_lease(self):
        import subprocess
        code = '''
import os,sys,time,subprocess
from pathlib import Path
from server_app.portraits.executor import ResourcePolicy,ResourceSlot
r=Path(sys.argv[1]); p=ResourcePolicy(lock_path=r/'lease',updater_status_paths=(r/'status',),disk_roots=(r,))
with ResourceSlot(p) as slot:
 slot.run_stage([sys.executable,'-c',"import os,sys,time,subprocess;from pathlib import Path;fd=int(os.environ['XL_PORTRAIT_RESOURCE_LOCK_FD']);subprocess.Popen([sys.executable,'-c',\\\"import time;time.sleep(1.5)\\\"],pass_fds=(fd,));Path(sys.argv[1]).write_text('ready');time.sleep(10)",str(r/'ready')],cwd=r,env=os.environ,timeout=20,log_root=r/'crashlogs')
'''
        parent = subprocess.Popen([sys.executable, '-c', code, str(self.root)])
        self.addCleanup(lambda: parent.poll() is None and parent.kill())
        until = time.monotonic()+3
        while not (self.root/'ready').exists() and time.monotonic()<until: time.sleep(.02)
        self.assertTrue((self.root/'ready').exists())
        # Kill only the dispatcher and stage leader, leaving its grandchild.
        import signal
        children = Path(f'/proc/{parent.pid}/task/{parent.pid}/children').read_text().split()
        self.assertEqual(len(children), 1)
        parent.kill(); parent.wait(timeout=2)
        os.kill(int(children[0]), signal.SIGKILL)
        with self.assertRaises(self.e.Deferred):
            with self.e.ResourceSlot(self.policy): pass
        time.sleep(1.7)
        with self.e.ResourceSlot(self.policy): pass

    def test_tool_descriptor_hook_is_present(self):
        tool = Path(__file__).resolve().parents[2]/'xl_updata_tool'
        sys.path.insert(0, str(tool))
        try:
            self.assertIsNotNone(importlib.util.find_spec('app.platform.resource_lease'), 'tool FD helper missing')
            from app.platform.resource_lease import inherited_lease_kwargs
            with self.e.ResourceSlot(self.policy) as slot:
                env = dict(XL_PORTRAIT_RESOURCE_LOCK_FD=str(slot.fd), XL_PORTRAIT_RESOURCE_LOCK_PATH=str(self.policy.lock_path))
                self.assertEqual(inherited_lease_kwargs(env), {'pass_fds': (slot.fd,)})
                wrong_fd = os.open(self.policy.lock_path, os.O_RDWR)
                try:
                    with self.assertRaises(ValueError): inherited_lease_kwargs({**env, 'XL_PORTRAIT_RESOURCE_LOCK_FD':str(wrong_fd)})
                finally: os.close(wrong_fd)
                with patch.dict(os.environ, env):
                    with self.assertRaises(ValueError): inherited_lease_kwargs({})
                with self.assertRaises(ValueError): inherited_lease_kwargs({**env, 'XL_PORTRAIT_RESOURCE_LOCK_FD':'999999'})
                self.assertEqual(inherited_lease_kwargs({}), {})
        finally: sys.path.remove(str(tool))


if __name__ == '__main__': unittest.main()
