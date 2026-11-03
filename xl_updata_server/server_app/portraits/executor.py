"""Unprivileged Linux resource primitives, for fixed trusted worker commands only.

No service/queue lifecycle or heavyweight imports. Configuration is administrator
owned, never derived from a job/chat. Descendants must preserve the inherited
lease descriptor and process group; the tool hooks enforce this for known calls.
An eventual system unit must use KillMode=control-group for arbitrary escapes.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import threading
import time


class BoundaryError(RuntimeError):
    """Heavy work cannot safely start in this environment."""


class Deferred(RuntimeError):
    """Retryable admission failure; caller must not claim/finish a job."""


class CleanupError(BoundaryError):
    """Owned processes could not be confirmed dead; slot remains poisoned."""


def _positive(value, label, *, integer=False):
    if (type(value) not in ((int,) if integer else (int, float))
            or not math.isfinite(value) or value <= 0):
        raise ValueError(f'{label} must be positive and finite')


def _absolute(value):
    if not isinstance(value, (str, Path)) or not Path(value).is_absolute() or '..' in Path(value).parts:
        raise ValueError('explicit absolute path required')
    return Path(value)


@dataclass(frozen=True)
class ResourcePolicy:
    lock_path: Path
    updater_status_paths: tuple[Path, ...]
    disk_roots: tuple[Path, ...]
    memory_bytes: int = 268435456
    admission_bytes: int = 320 * 1024**2
    abort_bytes: int = 64 * 1024**2
    disk_free_bytes: int = 1024**3
    poll_seconds: float = .1
    stage_seconds: float = 120
    term_seconds: float = 2
    kill_seconds: float = 2
    log_bytes: int = 1024**2

    def __post_init__(self):
        object.__setattr__(self, 'lock_path', _absolute(self.lock_path))
        for key in ('updater_status_paths', 'disk_roots'):
            values = getattr(self, key)
            if type(values) is not tuple or not values:
                raise ValueError(f'{key} must be a nonempty tuple')
            object.__setattr__(self, key, tuple(_absolute(v) for v in values))
        for key in ('memory_bytes', 'admission_bytes', 'abort_bytes', 'disk_free_bytes', 'log_bytes'):
            _positive(getattr(self, key), key, integer=True)
        if self.memory_bytes > 268435456 or self.abort_bytes > self.admission_bytes:
            raise ValueError('unsafe memory budget')
        for key in ('poll_seconds', 'stage_seconds', 'term_seconds', 'kill_seconds'):
            _positive(getattr(self, key), key)


def _read(path, limit=65536):
    with Path(path).open('rb') as stream:
        value = stream.read(limit + 1)
    if len(value) > limit:
        raise BoundaryError(f'oversized control file: {path}')
    return value.decode('utf-8', errors='strict')


def _meminfo():
    result = {}
    for line in _read('/proc/meminfo').splitlines():
        match = re.fullmatch(r'(MemAvailable|SwapTotal):\s+(\d+) kB', line)
        if match:
            if match[1] in result: raise BoundaryError('duplicate meminfo field')
            result[match[1]] = int(match[2]) * 1024
    if set(result) != {'MemAvailable', 'SwapTotal'}:
        raise BoundaryError('missing actual MemAvailable/SwapTotal')
    return result


def _mount_field(value):
    return re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), value)


def _memory_location(membership, mountinfo):
    candidates = []
    for line in membership.splitlines():
        parts = line.split(':', 2)
        if len(parts) != 3: raise BoundaryError('malformed cgroup membership')
        if parts[0] == '0' and parts[1] == '': candidates.append((2, parts[2]))
        elif 'memory' in parts[1].split(','): candidates.append((1, parts[2]))
    # Hybrid hosts use v1 memory even when a unified hierarchy also exists.
    v1 = [c for c in candidates if c[0] == 1]
    candidates = v1 or candidates
    if len(candidates) != 1: raise BoundaryError('ambiguous memory controller membership')
    version, group = candidates[0]
    if not group.startswith('/') or '..' in Path(group).parts:
        raise BoundaryError('unsafe cgroup path')
    mounts = []
    for line in mountinfo.splitlines():
        fields = line.split()
        try:
            sep = fields.index('-'); kind = fields[sep+1]; options = fields[sep+3].split(',')
            if (version == 2 and kind == 'cgroup2') or (version == 1 and kind == 'cgroup' and 'memory' in options):
                root, mount = _mount_field(fields[3]), Path(_mount_field(fields[4]))
                # A subtree/namespace mount hides ancestors. Never infer their limits.
                if root != '/' or not mount.is_absolute() or '..' in mount.parts:
                    raise BoundaryError('hidden cgroup ancestors or unsafe mount')
                mounts.append(mount)
        except (ValueError, IndexError) as exc:
            raise BoundaryError('malformed mountinfo') from exc
    if len(mounts) != 1: raise BoundaryError('ambiguous memory controller mount')
    return mounts[0] / group.lstrip('/'), mounts[0], version


def _limit(path, version):
    text = _read(path).strip()
    if version == 2 and text == 'max': return math.inf
    if not re.fullmatch(r'\d+', text) or int(text) <= 0:
        raise BoundaryError(f'invalid memory limit: {path}')
    value = int(text)
    return math.inf if version == 1 and value >= 2**60 else value


def _initial_cgroup_namespace():
    # Linux PROC_CGROUP_INIT_INO (include/linux/proc_ns.h). PID 1 may itself
    # be namespaced, so comparing our namespace with PID 1 is insufficient.
    # Unknown kernel identities fail closed when root controls are absent.
    return os.stat('/proc/self/ns/cgroup').st_ino == 0xEFFFFFFB


def _controller_limits(leaf, root, version, host_swap, *, hierarchy_root=False):
    if leaf != root and root not in leaf.parents:
        raise BoundaryError('controller path escape')
    levels = [leaf, *list(leaf.parents)[:len(leaf.parts)-len(root.parts)]]
    memory = []; swap = []
    for level in levels:
        if level.is_symlink(): raise BoundaryError('symlink controller')
        if version == 2 and level == root and hierarchy_root:
            # The full hierarchy root has neither non-root limit file. A
            # namespace exposing an ordinary group has these files: read its
            # real limits, never discard them just because it is a mount root.
            # Only the jointly absent pair at a full mount in the initial
            # namespace is exempt; namespace roots default to no exemption.
            if not (root/'memory.max').exists() and not (root/'memory.swap.max').exists():
                continue
        if version == 1 and _read(level/'memory.use_hierarchy').strip() != '1':
            raise BoundaryError('nonhierarchical v1 accounting')
        memory.append(_limit(level/('memory.max' if version == 2 else 'memory.limit_in_bytes'), version))
        if version == 2 and host_swap:
            text = _read(level/'memory.swap.max').strip()
            if text == 'max': swap.append(math.inf)
            elif re.fullmatch(r'\d+', text): swap.append(int(text))
            else: raise BoundaryError('invalid swap controller')
    effective = min(memory, default=math.inf)
    if not math.isfinite(effective): raise BoundaryError('memory controller is unbounded')
    # v1 memsw == memory is NOT proof of zero swap (resident pages may swap).
    if host_swap and (version == 1 or min(swap, default=math.inf) != 0):
        raise BoundaryError('zero swap not proven')
    return effective, 0


@dataclass(frozen=True)
class ResourceBoundary:
    memory_bytes: int
    swap_bytes: int
    controller: str


def verify_resource_boundary(policy):
    if sys.platform != 'linux': raise BoundaryError('Linux required')
    try:
        os.sched_setaffinity(0, {1})
        if os.sched_getaffinity(0) != {1}: raise BoundaryError('CPU1 affinity ineffective')
        leaf, root, version = _memory_location(_read('/proc/self/cgroup'), _read('/proc/self/mountinfo', 1024**2))
        memory, swap = _controller_limits(leaf, root, version, _meminfo()['SwapTotal'],
                                          hierarchy_root=version == 2 and _initial_cgroup_namespace())
        if memory > policy.memory_bytes: raise BoundaryError('actual memory cap exceeds policy')
        return ResourceBoundary(memory, swap, str(leaf))
    except (OSError, UnicodeError, ValueError) as exc:
        raise BoundaryError(f'kernel boundary unavailable: {exc}') from exc


def _disk_free(path):
    actual = path.resolve(strict=True)
    matches = []
    for line in _read('/proc/self/mountinfo', 1024**2).splitlines():
        fields = line.split(); sep = fields.index('-')
        mount = Path(_mount_field(fields[4]))
        if actual == mount or mount in actual.parents: matches.append((len(mount.parts), fields[sep+1]))
    if not matches or max(matches)[1] in ('tmpfs', 'ramfs'):
        raise Deferred('working disk must not be tmpfs/ramfs')
    fs = os.statvfs(actual)
    return fs.f_bavail * fs.f_frsize


def _read_status(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result: raise ValueError('duplicate updater field')
            result[key] = value
        return result
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        with os.fdopen(fd, 'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError('updater status must be a regular file')
            data = stream.read(16385)
        if len(data) > 16384: raise ValueError('oversized updater status')
        payload = json.loads(data, object_pairs_hook=unique)
        if not isinstance(payload, dict) or type(payload.get('updating')) is not bool:
            raise ValueError('invalid updater status')
        return payload['updating']
    except (OSError, ValueError, UnicodeError) as exc:
        raise Deferred(f'updater status unavailable: {exc}') from exc


def check_admission(policy, *, running=False):
    try:
        for path in policy.updater_status_paths:
            if _read_status(path): raise Deferred('update')
        threshold = policy.abort_bytes if running else policy.admission_bytes
        if _meminfo()['MemAvailable'] < threshold: raise Deferred('memory')
        if any(_disk_free(root) < policy.disk_free_bytes for root in policy.disk_roots):
            raise Deferred('disk')
    except (OSError, ValueError, UnicodeError, BoundaryError) as exc:
        raise Deferred(f'admission unavailable: {exc}') from exc


def _open_lease(path):
    # Traverse every component without following links, including parent races.
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd); fd = child
        result = os.open(path.name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=fd)
        info = os.fstat(result)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1 or info.st_mode & 0o077:
            os.close(result); raise BoundaryError('lease must be private owned regular file')
        return result
    finally: os.close(fd)


def _group_alive(group):
    # Ignore zombies: no executable work or inherited descriptors remain there.
    for entry in Path('/proc').iterdir():
        if not entry.name.isdecimal(): continue
        try:
            fields = _read(entry/'stat', 4096).rsplit(')', 1)[1].split()
            if int(fields[2]) == group and fields[0] != 'Z': return True
        except (FileNotFoundError, ProcessLookupError): continue
    return False


@dataclass(frozen=True)
class StageResult:
    returncode: int
    reason: str


class ResourceSlot:
    """Thread-confined host lease. Close only: descendants share the flock."""
    def __init__(self, policy):
        self.policy = policy; self.fd = None; self.process = None; self.poisoned = False
        self.owner_thread = threading.get_ident()

    def _require_owner(self):
        if threading.get_ident() != self.owner_thread:
            raise BoundaryError('resource slot is confined to its owning thread')

    def __enter__(self):
        import fcntl
        self._require_owner()
        if self.fd is not None or self.poisoned: raise BoundaryError('slot already entered or poisoned')
        verify_resource_boundary(self.policy)
        try:
            self.fd = _open_lease(self.policy.lock_path)
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            check_admission(self.policy)
        except BaseException as exc:
            if self.fd is not None: os.close(self.fd); self.fd = None
            if isinstance(exc, BlockingIOError): raise Deferred('lease busy') from exc
            raise
        return self

    def __exit__(self, *args):
        self._require_owner()
        if self.poisoned or self.process is not None:
            raise CleanupError('slot held after unconfirmed cleanup')
        if self.fd is not None: os.close(self.fd); self.fd = None

    def _terminate(self, process):
        for sig, duration in ((signal.SIGTERM, self.policy.term_seconds), (signal.SIGKILL, self.policy.kill_seconds)):
            if _group_alive(process.pid):
                try: os.killpg(process.pid, sig)
                except ProcessLookupError: pass
            until = time.monotonic() + duration
            while time.monotonic() < until:
                process.poll()
                if not _group_alive(process.pid):
                    process.wait(timeout=self.policy.kill_seconds); return
                time.sleep(self.policy.poll_seconds)
        self.poisoned = True
        raise CleanupError('owned process group survived bounded cleanup')

    def run_stage(self, command, *, cwd, env, timeout, log_root, stop_event=None):
        self._require_owner()
        if self.fd is None or self.process is not None or self.poisoned: raise BoundaryError('slot not available')
        if type(command) not in (list, tuple) or not command or any(type(a) is not str or '\0' in a for a in command):
            raise ValueError('trusted argument list required')
        exe = _absolute(command[0]); cwd = _absolute(cwd); log_root = _absolute(log_root)
        if not exe.is_file() or not os.access(exe, os.X_OK): raise BoundaryError('executable unavailable')
        _positive(timeout, 'timeout')
        if timeout > self.policy.stage_seconds: raise ValueError('timeout exceeds policy')
        check_admission(self.policy)
        if stop_event is not None and stop_event.is_set(): return StageResult(-1, 'stop')
        log_root.mkdir(mode=0o700)  # Private new per-attempt directory; never overwrite.
        if log_root.is_symlink() or log_root.stat().st_uid != os.getuid(): raise BoundaryError('unsafe diagnostics root')
        child_env = dict(env)
        child_env['XL_PORTRAIT_RESOURCE_LOCK_FD'] = str(self.fd)
        child_env['XL_PORTRAIT_RESOURCE_LOCK_PATH'] = str(self.policy.lock_path)
        files = []; readers = []; log_errors = []
        def drain(stream, target):
            kept = 0
            try:
                while True:
                    chunk = stream.read(8192)
                    if not chunk: break
                    piece = chunk[:max(0, self.policy.log_bytes-kept)]
                    try:
                        if not log_errors: target.write(piece)
                    except OSError as exc:
                        log_errors.append(exc)
                    kept += len(piece)
            except OSError as exc:
                log_errors.append(exc)
            finally:
                for resource in (stream, target):
                    try: resource.close()
                    except OSError as exc: log_errors.append(exc)
        try:
            for name in ('stdout.log', 'stderr.log'):
                files.append(os.fdopen(os.open(log_root/name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), 'wb', buffering=0))
            process = subprocess.Popen(command, cwd=cwd, env=child_env, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       start_new_session=True, pass_fds=(self.fd,))
            self.process = process
            try:
                # Establish cleanup immediately after launch, before allocating
                # or starting readers. Register each reader before start so a
                # partially completed start is also visible to cleanup.
                for stream, target in zip((process.stdout, process.stderr), files):
                    reader = threading.Thread(target=drain, args=(stream, target), daemon=True)
                    readers.append(reader); reader.start()
                deadline = time.monotonic() + timeout; reason = 'exit'
                while process.poll() is None:
                    if stop_event is not None and stop_event.is_set(): reason = 'stop'; break
                    if time.monotonic() >= deadline: reason = 'timeout'; break
                    try: check_admission(self.policy, running=True)
                    except Deferred as exc: reason = str(exc); break
                    time.sleep(self.policy.poll_seconds)
                if reason == 'exit' and _group_alive(process.pid): reason = 'descendants'
            finally:
                try:
                    self._terminate(process)
                except BaseException as exc:
                    self.poisoned = True
                    raise CleanupError('owned process cleanup unconfirmed') from exc
                finally:
                    for index, (stream, target) in enumerate(zip((process.stdout, process.stderr), files)):
                        reader = readers[index] if index < len(readers) else None
                        if reader is not None and reader.ident is not None:
                            reader.join(timeout=self.policy.kill_seconds)
                        else:
                            # A failed close must not skip the other pipe or
                            # diagnostic, nor retain an already reaped child.
                            for resource in (stream, target):
                                try: resource.close()
                                except OSError as exc: log_errors.append(exc)
                    if any(reader.is_alive() for reader in readers):
                        self.poisoned = True
                        raise CleanupError('diagnostic pipe held outside owned group')
                if not self.poisoned: self.process = None
            if log_errors: raise BoundaryError(f'diagnostic I/O failed: {log_errors[0]}')
            return StageResult(process.returncode, reason)
        finally:
            if not readers:
                for target in files: target.close()


def run_batch(items, attempt, stop_event):
    """Serial attempts; after five, wait three seconds interruptibly."""
    completed = 0
    for item in items:
        if stop_event.is_set(): break
        attempt(item); completed += 1
        if completed % 5 == 0 and stop_event.wait(3): break
    return completed
