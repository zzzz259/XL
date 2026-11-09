"""Resource-limited single render subprocess for the portrait scene CLI.

Linux enforcement uses the host's systemd transient units (MemoryMax /
MemoryHigh / CPUAffinity); nothing here creates cgroup hierarchies, mount
namespaces, or verifies executables. Without systemd-run the launcher falls
back to a plain process-group-killed subprocess with affinity set, and peak
memory evidence is unavailable.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess
import time
import uuid

DEFAULT_TIMEOUT_SECONDS = 900.0


DEFAULT_MEMORY_MAX_BYTES = 512 * 1024 * 1024
DEFAULT_MEMORY_HIGH_BYTES = 384 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class RenderProcessLimits:
    memory_max_bytes: int = DEFAULT_MEMORY_MAX_BYTES
    memory_high_bytes: int = DEFAULT_MEMORY_HIGH_BYTES
    cpu_affinity: tuple[int, ...] = (0,)
    timeout_seconds: float = 900.0
    unit_prefix: str = 'fallstar-portrait'

    def __post_init__(self):
        if type(self.memory_max_bytes) is not int or not (64 * 1024 * 1024 <= self.memory_max_bytes <= 8 * 1024 ** 3):
            raise ValueError('memory_max_bytes must be an integer between 64 MiB and 8 GiB')
        if type(self.memory_high_bytes) is not int or not (0 < self.memory_high_bytes <= self.memory_max_bytes):
            raise ValueError('memory_high_bytes must be a positive integer no larger than memory_max_bytes')
        if (not isinstance(self.cpu_affinity, tuple) or not self.cpu_affinity
                or any(type(core) is not int or core < 0 for core in self.cpu_affinity)):
            raise ValueError('cpu_affinity must be a non-empty tuple of non-negative integers')
        if not isinstance(self.timeout_seconds, (int, float)) or not (1.0 <= float(self.timeout_seconds) <= 24 * 3600):
            raise ValueError('timeout_seconds must be within 1..86400')


@dataclass(frozen=True, slots=True)
class RenderProcessResult:
    returncode: int | None
    timed_out: bool
    peak_memory_bytes: int | None
    unit: str | None
    elapsed_seconds: float
    stdout_path: Path
    stderr_path: Path


def resolve_systemd_run(value='auto') -> str | None:
    if value in (None, 'never'):
        return None
    if isinstance(value, (str, os.PathLike)) and value != 'auto':
        path = str(value)
        return path if os.path.isfile(path) else None
    if os.name != 'posix':
        return None
    return shutil.which('systemd-run')


def systemd_scope_command(command, unit, limits, *, user=False, setenv=()):
    affinity = ','.join(str(core) for core in limits.cpu_affinity)
    manager_flag = ('--user',) if user else ()
    # --pipe forwards the service stdout/stderr to this process; --setenv=NAME
    # (no value) propagates one caller variable into the unit environment.
    return [
        'systemd-run', *manager_flag, '--wait', '--pipe', f'--unit={unit}', '--collect',
        *(f'--setenv={name}' for name in setenv),
        '-p', f'MemoryMax={limits.memory_max_bytes}',
        '-p', f'MemoryHigh={limits.memory_high_bytes}',
        '-p', 'MemorySwapMax=0',
        '-p', f'CPUAffinity={affinity}',
        '-p', 'LimitCORE=0',
        '-p', 'NoNewPrivileges=yes',
        '--',
        *map(str, command),
    ]


def _systemctl(*args, user=False):
    return ['systemctl', *(('--user',) if user else ()), *args]


def _systemd_show(unit, fields, *, user=False):
    probe = subprocess.run(
        _systemctl('show', unit, '-p', ','.join(fields), '--value', user=user),
        capture_output=True, text=True, timeout=15, check=False)
    if probe.returncode != 0:
        return None
    lines = (probe.stdout or '').splitlines()
    if len(lines) != len(fields):
        return None
    return dict(zip(fields, (line.strip() for line in lines)))


def _parse_peak(value):
    try:
        peak = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return peak if peak > 0 else None


def _run_systemd(argv, *, work_dir, env, limits, unit, user=False):
    started = time.monotonic()
    stdout_path = Path(work_dir) / 'stdout.log'
    stderr_path = Path(work_dir) / 'stderr.log'
    timed_out = False
    returncode = None
    peak = None

    def poll_peak():
        # MemoryPeak is only available while the unit's cgroup exists, so the
        # peak is sampled during execution instead of after collection.
        nonlocal peak
        shown = _systemd_show(unit, ('MemoryPeak',), user=user)
        if shown:
            value = _parse_peak(shown.get('MemoryPeak'))
            if value is not None:
                peak = value

    deadline = time.monotonic() + limits.timeout_seconds
    with stdout_path.open('wb') as stdout, stderr_path.open('wb') as stderr:
        process = subprocess.Popen(argv, cwd=work_dir, env=env, stdout=stdout, stderr=stderr)
        while process.poll() is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            poll_peak()
            time.sleep(min(0.5, max(0.05, remaining)))
        if timed_out:
            subprocess.run(_systemctl('kill', '--kill-whom=all', unit, user=user),
                           capture_output=True, timeout=15, check=False)
            subprocess.run(_systemctl('stop', unit, user=user),
                           capture_output=True, timeout=15, check=False)
            process.kill()
            try:
                returncode = process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                returncode = None
        else:
            returncode = process.returncode
    poll_peak()
    shown = None if timed_out else _systemd_show(unit, ('MemoryPeak', 'ExecMainStatus', 'Result'), user=user)
    if shown:
        value = _parse_peak(shown.get('MemoryPeak'))
        if value is not None:
            peak = value
        if shown.get('Result') == 'success':
            returncode = 0
        else:
            try:
                returncode = int(str(shown.get('ExecMainStatus')).strip())
            except (TypeError, ValueError):
                pass
    subprocess.run(_systemctl('reset-failed', unit, user=user), capture_output=True, timeout=15, check=False)
    return RenderProcessResult(
        returncode=returncode, timed_out=timed_out, peak_memory_bytes=peak, unit=unit,
        elapsed_seconds=time.monotonic() - started, stdout_path=stdout_path, stderr_path=stderr_path)


def _run_fallback(command, *, work_dir, env, limits):
    from .processes import run_external_process
    started = time.monotonic()
    logs = Path(work_dir) / 'process-logs'
    timed_out = False
    try:
        popen_kwargs = {}
        if os.name == 'posix':
            def set_affinity():
                os.sched_setaffinity(0, set(limits.cpu_affinity))

            popen_kwargs['preexec_fn'] = set_affinity
        completed = run_external_process(
            list(map(str, command)), tool='PortraitSceneCLI', cwd=work_dir, env=dict(env),
            timeout=limits.timeout_seconds, cancel_tree=True, max_output_bytes=10 * 1024 * 1024,
            log_root=logs, **popen_kwargs)
        returncode = completed.returncode
        stdout_path = Path(completed.xl_log_dir) / 'stdout.log'
        stderr_path = Path(completed.xl_log_dir) / 'stderr.log'
    except subprocess.TimeoutExpired:
        timed_out = True
        returncode = None
        stdout_path = logs / 'stdout.log'
        stderr_path = logs / 'stderr.log'
    return RenderProcessResult(
        returncode=returncode, timed_out=timed_out, peak_memory_bytes=None, unit=None,
        elapsed_seconds=time.monotonic() - started, stdout_path=stdout_path, stderr_path=stderr_path)


def run_render_process(command, *, work_dir, env, limits, systemd_run='auto', systemd_user=None):
    """Run one render command under the host's resource limits.

    Exactly one child process tree is started per call. Serial execution is
    the caller's responsibility; this function never forks workers. Unprivileged
    callers use their per-user systemd manager (``systemd-run --user``), which
    needs no polkit authentication; root uses the system manager.
    """
    if not command:
        raise ValueError('render command must not be empty')
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    env = dict(env or os.environ)
    for key in ('LD_PRELOAD', 'LD_AUDIT', 'DOTNET_STARTUP_HOOKS'):
        env.pop(key, None)
    systemd = resolve_systemd_run(systemd_run)
    if systemd is not None:
        user = os.geteuid() != 0 if (systemd_user is None and hasattr(os, 'geteuid')) else bool(systemd_user)
        unit = f'{limits.unit_prefix}-{uuid.uuid4().hex[:12]}'
        setenv = tuple(name for name in (
            'PATH', 'LD_LIBRARY_PATH', 'HOME', 'LANG', 'LC_ALL', 'TZ',
            'DISPLAY', 'XAUTHORITY', 'WAYLAND_DISPLAY', 'XDG_RUNTIME_DIR') if env.get(name))
        argv = systemd_scope_command(command, unit, limits, user=user, setenv=setenv)
        argv[0] = systemd
        return _run_systemd(argv, work_dir=work, env=env, limits=limits, unit=unit, user=user)
    return _run_fallback(command, work_dir=work, env=env, limits=limits)
