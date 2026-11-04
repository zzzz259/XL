"""Explicit operational Linux runtime; never constructed from a portrait job.

Installation supplies hash-pinned FDD assemblies and dependencies. Namespace
setup belongs to the protected executor; this module never changes host mounts.
"""
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import json
import math
import select
import shutil
import subprocess
import time
import threading

from .resource_lease import inherited_lease_kwargs
from .processes import run_external_process, _StreamCapture, _drain_stream
from .portrait_dynamic_profile import DynamicProfile, clean_environment, require_invariant


@dataclass(frozen=True)
class RuntimeFile:
    path: str
    sha256: str

    def verify(self):
        path = Path(self.path)
        if (not path.is_absolute() or path.is_symlink() or not path.is_file()
                or not re.fullmatch('[0-9a-f]{64}', self.sha256)):
            raise ValueError('invalid runtime file identity')
        with path.open('rb') as stream:
            actual = hashlib.file_digest(stream, 'sha256').hexdigest()
        if actual != self.sha256:
            raise ValueError(f'runtime hash mismatch: {path.name}')
        return path


@dataclass(frozen=True)
class LinuxManagedRuntime:
    dotnet: RuntimeFile
    app_files: tuple[RuntimeFile, ...]
    native_files: tuple[RuntimeFile, ...]
    xvfb: RuntimeFile
    xkbcomp: RuntimeFile
    x_libraries: tuple[RuntimeFile, ...]
    runtime_files: tuple[RuntimeFile, ...] = ()
    dynamic_profile: DynamicProfile | None = None

    def __post_init__(self):
        for name in ('app_files', 'native_files', 'x_libraries', 'runtime_files'):
            if type(getattr(self, name)) is not tuple:
                raise ValueError('runtime manifests must be immutable tuples')
        if self.dynamic_profile is not None and type(self.dynamic_profile) is not DynamicProfile:
            raise ValueError('dynamic profile must be verified and immutable')

    def prepare(self, work, *, deadline, cancel_check=None):
        if not math.isfinite(deadline) or deadline <= time.monotonic():
            raise ValueError('runtime deadline expired')
        files = (self.dotnet, self.xvfb, self.xkbcomp,
                 *self.app_files, *self.native_files, *self.x_libraries, *self.runtime_files)
        for entry in files:
            if time.monotonic() >= deadline or (cancel_check and cancel_check()):
                raise TimeoutError('managed preparation stopped')
            entry.verify()
        names = [Path(entry.path).name for entry in self.app_files]
        for entry in self.app_files:
            with Path(entry.path).open('rb') as stream:
                if stream.read(4) == b'\x7fELF':
                    raise ValueError('staged app ELF/native asset forbidden')
        if self.dynamic_profile is not None:
            require_invariant(self.app_files)
        if len(set(names)) != len(names):
            raise ValueError('duplicate runtime app file')
        for stem in ('SpineViewerCLI', 'PortraitSceneCLI'):
            for suffix in ('.dll', '.deps.json', '.runtimeconfig.json'):
                if stem + suffix not in names:
                    raise ValueError('incomplete managed runtime')
            config = next(entry for entry in self.app_files
                          if Path(entry.path).name == stem + '.runtimeconfig.json')
            options = json.loads(Path(config.path).read_text()).get('runtimeOptions', {})
            if (options.get('framework', {}).get('name') != 'Microsoft.NETCore.App'
                    or 'includedFrameworks' in options):
                raise ValueError('runtime requires prepared framework-dependent configuration')
        clr_names = {Path(entry.path).name for entry in self.runtime_files}
        if not {'libcoreclr.so', 'libhostfxr.so', 'System.Private.CoreLib.dll'} <= clr_names:
            raise ValueError('runtime manifest must pin installed CLR and framework files')
        if sum(Path(entry.path).stat().st_size for entry in self.app_files) > 64 * 1024 * 1024:
            raise ValueError('private managed app exceeds copy budget')
        app = Path(work).resolve() / 'managed-app'
        app.mkdir(parents=True, exist_ok=False)
        for entry in self.app_files:
            if time.monotonic() >= deadline or (cancel_check and cancel_check()):
                raise TimeoutError('managed preparation stopped')
            target = app / Path(entry.path).name
            shutil.copyfile(entry.path, target)
            RuntimeFile(str(target), entry.sha256).verify()
        return ManagedLaunch(self, app, deadline, cancel_check)


class ManagedLaunch:
    def __init__(self, runtime, app, deadline, cancel_check):
        self.runtime, self.app, self.deadline = runtime, app, deadline
        self.cancel_check = cancel_check
        self.query_prefix = (runtime.dotnet.path, str(app / 'SpineViewerCLI.dll'))
        self.native_prefix = (runtime.dotnet.path, str(app / 'PortraitSceneCLI.dll'))
        self.display = None
        self.env = dict(os.environ)
        if getattr(runtime, 'dynamic_profile', None) is not None:
            self.env = clean_environment(self.env)
            self.env.update(runtime.dynamic_profile.environment(app.parent))
        for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'DOTNET_ADDITIONAL_DEPS',
                    'DOTNET_SHARED_STORE', 'DOTNET_STARTUP_HOOKS', 'LD_PRELOAD', 'LD_AUDIT'):
            self.env.pop(key, None)
        self.env.update(LIBGL_ALWAYS_SOFTWARE='1', GALLIUM_DRIVER='llvmpipe',
            LP_NUM_THREADS='1', DOTNET_PROCESSOR_COUNT='1', DOTNET_EnableDiagnostics='0',
            DOTNET_CLI_TELEMETRY_OPTOUT='1',
            DOTNET_ROOT=str(Path(runtime.dotnet.path).parent), DOTNET_MULTILEVEL_LOOKUP='0',
            LD_LIBRARY_PATH=':'.join(dict.fromkeys(str(Path(e.path).parent)
                for e in (*runtime.native_files, *runtime.x_libraries))))

    def remaining(self, timeout=None):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0 or (self.cancel_check and self.cancel_check()):
            raise TimeoutError('managed stage stopped')
        return min(remaining, timeout) if timeout is not None else remaining

    def run(self, command, *, timeout=None, **kwargs):
        if tuple(command[:2]) not in (self.query_prefix, self.native_prefix):
            raise ValueError('command does not match trusted managed runtime')
        if set(kwargs) - {'capture_output', 'text', 'check'}:
            raise ValueError('managed launch options are fixed')
        if self.display is None:
            raise ValueError('private display is not active')
        result = run_external_process(command, tool='PortraitManagedCLI',
            cwd=self.app, env=self.env, timeout=self.remaining(timeout),
            cancel_check=self.cancel_check, log_root=self.app.parent / 'process-logs',
            max_output_bytes=65536, tool_log_paths=(self.app / 'logs/cli.log',), **kwargs)
        if getattr(result, 'xl_cancelled', False):
            raise TimeoutError('managed stage cancelled')
        if getattr(result, 'xl_stdout_truncated', False) or getattr(result, 'xl_stderr_truncated', False):
            raise ValueError('managed semantic output was truncated; inspect bounded diagnostics')
        self.remaining()
        return result

    def metadata_provider(self, skeleton_path, atlas_path):
        from app.features.preview.portrait_package import query_portrait_metadata
        return query_portrait_metadata(skeleton_path, atlas_path, launch=self)


class PrivateDisplay:
    """Owned Xvfb in executor's private mount namespace; close before cleanup."""
    def __init__(self, launch):
        self.launch = launch
        self.process = None
        self.log = None
        self.reader = None
        self.read_fd = None

    def _read_display_number(self):
        startup = time.monotonic() + min(15, self.launch.remaining())
        frame = bytearray()
        while True:
            remaining = min(self.launch.remaining(), startup - time.monotonic())
            if remaining <= 0 or self.process.poll() is not None:
                raise TimeoutError('private display did not become ready')
            if not select.select([self.read_fd], [], [], min(.05, remaining))[0]:
                continue
            chunk = os.read(self.read_fd, 64 - len(frame))
            if not chunk:
                raise ValueError('private display readiness ended before complete frame')
            frame.extend(chunk)
            if len(frame) >= 64:
                raise ValueError('private display readiness frame exceeds limit')
            if b'\n' in frame:
                self.launch.remaining()
                if time.monotonic() >= startup:
                    raise TimeoutError('private display readiness arrived after deadline')
                if not re.fullmatch(rb'[0-9]+\n', frame) or self.process.poll() is not None:
                    raise ValueError('invalid private display readiness')
                return frame[:-1].decode('ascii')

    def __enter__(self):
        import resource
        launch = self.launch
        runtime = launch.runtime
        if os.name != 'posix' or resource.getrlimit(resource.RLIMIT_CORE)[0] != 0:
            raise ValueError('protected display requires Linux and disabled core dumps')
        if not inherited_lease_kwargs(launch.env):
            raise ValueError('protected display requires live resource lease')
        # A complete installed xkbcomp or namespace-only execution view is
        # supplied by trusted executor setup. Never mutate /usr/bin here.
        RuntimeFile('/usr/bin/xkbcomp', runtime.xkbcomp.sha256).verify()
        runtime.xvfb.verify()
        self.read_fd, write_fd = os.pipe()
        self.log = _StreamCapture(launch.app.parent / 'xvfb.log', 65536)
        try:
            env = dict(launch.env, LD_LIBRARY_PATH=':'.join(dict.fromkeys(
                str(Path(e.path).parent) for e in runtime.x_libraries)))
            fds = inherited_lease_kwargs(env)['pass_fds'] + (write_fd,)
            self.process = subprocess.Popen([runtime.xvfb.path, '-displayfd', str(write_fd),
                '-screen', '0', '128x128x24', '-nolisten', 'tcp', '-noreset',
                *(['-fp', runtime.dynamic_profile.font_argument] if getattr(runtime, 'dynamic_profile', None) is not None else [])],
                env=env, pass_fds=fds, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            self.reader = threading.Thread(target=_drain_stream, args=(
                self.process.stdout, self.log, 'xvfb', None, 'utf-8', 'replace'), daemon=True)
            self.reader.start()
            os.close(write_fd)
            write_fd = None
            value = self._read_display_number()
            launch.env['DISPLAY'] = ':' + value
            launch.display = self
            return launch
        except BaseException:
            self.close()
            raise
        finally:
            if write_fd is not None:
                os.close(write_fd)

    def close(self):
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=2)
            else:
                self.process.wait(timeout=2)
            self.process = None  # Poisoned cleanup retains ownership on error.
        if self.read_fd is not None:
            os.close(self.read_fd)
            self.read_fd = None
        if self.log is not None:
            if self.reader is not None:
                self.reader.join(timeout=2)
                if self.reader.is_alive():
                    raise RuntimeError('private display diagnostic pipe did not close')
            self.log.finish()
            self.log = None
        if self.launch is not None:
            self.launch.env.pop('DISPLAY', None)
            self.launch.display = None

    def __exit__(self, *exc):
        self.close()
