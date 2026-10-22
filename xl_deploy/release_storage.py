"""Conservative release retention; authority and live evidence are mandatory.

No raw directory deletion is used. All removal goes through Git worktree remove
after full validation, under the same lock as deployment/recovery.
"""
from __future__ import annotations

import json
import math
import os
import re
import shlex
import subprocess
import tempfile
import time
from pathlib import Path

from xl_deploy.state import DeploymentState

NAME = re.compile(r'([0-9a-f]{40})(?:-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})?\Z')
JOURNAL_PHASES = frozenset({
    'staging', 'staged', 'preflight_passed', 'preflight_failed', 'announcing_start',
    'notice_failed', 'pausing', 'draining', 'stopping', 'stopped', 'switching', 'switched',
    'starting', 'checking', 'resuming', 'announcing_completion', 'completion_notice_pending',
    'release_note_pending', 'completion_notice_failed', 'completed', 'rolling_back',
    'rollback_failed', 'recovery_notice_pending', 'rolled_back', 'aborted', 'no_op',
})


def _code_path(value):
    """Resolve aliases before filtering; selected code paths must be interpretable."""
    path = Path(value)
    if not path.is_absolute():
        raise ValueError('code-bearing path is not absolute')
    try:
        return str(path.resolve(strict=True))
    except (OSError, RuntimeError):
        raise ValueError('code-bearing path cannot be resolved') from None


def _dependency_paths(value, *, strict=True):
    """Preserve lexical, intermediate-container and final dependency identities.

    Venv executables commonly resolve to a base Python outside the release. A
    parent alias or a chain of file aliases must not erase the venv container.
    """
    original = Path(value)
    if not original.is_absolute():
        raise ValueError('code-bearing path is not absolute')
    pending, visited, references = [original], set(), {}
    try:
        while pending:
            path = pending.pop()
            if path in visited:
                continue
            visited.add(path)
            if len(visited) > 64:
                raise ValueError('code-bearing alias chain is too complex')
            references[str(path)] = None
            references[str(path.resolve(strict=strict))] = None
            for ancestor in (path, *path.parents):
                remainder = path.relative_to(ancestor)
                # Resolve the container separately, retaining the executable's
                # lexical location before following its final symlink.
                references[str(ancestor.resolve(strict=strict) / remainder)] = None
                if ancestor.is_symlink() or getattr(ancestor, 'is_junction', lambda: False)():
                    target = ancestor.readlink()
                    if os.name == 'nt':
                        text = str(target)
                        if text.startswith('\\\\?\\UNC\\'):
                            target = Path('\\\\' + text[8:])
                        elif text.startswith('\\\\?\\'):
                            target = Path(text[4:])
                    if not target.is_absolute():
                        target = ancestor.parent / target
                    pending.append(target / remainder)
    except (OSError, RuntimeError):
        raise ValueError('code-bearing path cannot be resolved') from None
    return list(references)


def _argument_paths(arguments):
    paths = []
    for argument in arguments:
        value = argument.partition('=')[2] if '=' in argument else argument
        if Path(value).is_absolute():
            paths.extend(_dependency_paths(value, strict=False))
    return paths


def _environment_paths(items, cwd):
    paths = []
    for item in items:
        key, _, value = item.partition('=')
        if key in {'PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV'}:
            for entry in value.split(os.pathsep):
                paths.extend(_dependency_paths(entry or cwd))
    return paths


def process_paths(directory):
    """Return only absolute dependency paths, never raw argv or secret environment."""
    cwd_link = os.readlink(directory / 'cwd')
    cwd = _code_path(cwd_link)
    paths = _dependency_paths(cwd_link)
    paths.extend(_dependency_paths(os.readlink(directory / 'exe')))
    arguments = (directory / 'cmdline').read_bytes().decode('utf-8', errors='strict').split('\0')
    paths.extend(_argument_paths(arguments))
    selected = []
    for item in (directory / 'environ').read_bytes().split(b'\0'):
        key, _, value = item.partition(b'=')
        if key in {b'PYTHONPATH', b'PYTHONHOME', b'VIRTUAL_ENV'}:
            selected.append(key.decode() + '=' + value.decode('utf-8', errors='strict'))
    paths.extend(_environment_paths(selected, cwd))
    for line in (directory / 'maps').read_text(encoding='utf-8').splitlines():
        fields = line.split(None, 5)
        if len(fields) == 6 and Path(fields[5]).is_absolute():
            paths.extend(_dependency_paths(fields[5], strict=False))
    return paths


class ReleaseStorage:
    def __init__(self, repository, state: DeploymentState, current_root, *,
                 units=(), live_references=None):
        self.repository = Path(repository).absolute()
        self.state = state
        self.root = state.releases_root
        self.current_root = Path(current_root).absolute()
        self.authority_path = state.state_dir / 'release-storage.json'
        # Controller interpreter dependencies exist even between oneshot runs;
        # retention evidence is deliberately separate from restart ownership.
        self.units = tuple(sorted(set(units) | {'xl-deploy-poll.service'}))
        self.live_references = live_references or self._live_references

    def _git(self, *args):
        return subprocess.run(['git', *map(str, args)], cwd=self.repository,
                              text=True, capture_output=True, check=True).stdout

    def _direct(self, value, *, pinned_legacy=()):
        path = Path(value)
        known_name = NAME.fullmatch(path.name) or (
            str(path) in pinned_legacy and re.fullmatch(r'[0-9a-f]{40}-[a-z0-9][a-z0-9-]{0,63}', path.name))
        if (not path.is_absolute() or path.parent != self.root or path.is_symlink()
                or path.resolve() != path or not path.is_dir() or not known_name):
            raise ValueError(f'unsafe release path: {path}')
        return path

    def _authority(self):
        if self.authority_path.is_symlink():
            raise ValueError('authority must not be a symlink')
        data = json.loads(self.authority_path.read_text(encoding='utf-8'))
        if (not isinstance(data, dict) or set(data) != {'schema', 'pins', 'rollback', 'releases'}
                or data['schema'] != 1 or not isinstance(data['releases'], dict)):
            raise ValueError('invalid release-storage authority')
        for key in ('pins', 'rollback'):
            if not isinstance(data[key], list) or any(not isinstance(p, str) for p in data[key]):
                raise ValueError('invalid protected references')
        for key in ('pins', 'rollback'):
            for path in data[key]:
                self._direct(path, pinned_legacy=data['pins'])
        for name, record in data['releases'].items():
            if (not NAME.fullmatch(name) or not isinstance(record, dict)
                    or set(record) != {'branch', 'sha', 'created_at'}
                    or record['branch'] not in {'test', 'main', 'debug'}
                    or record['sha'] != name[:40]
                    or type(record['created_at']) not in (float, int)
                    or not math.isfinite(record['created_at']) or record['created_at'] <= 0):
                raise ValueError('invalid release record')
        return data

    def _save_authority(self, data):
        descriptor, temporary = tempfile.mkstemp(prefix='release-storage.', suffix='.tmp',
                                                 dir=self.state.state_dir)
        try:
            with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
                json.dump(data, stream, indent=2, sort_keys=True)
                stream.write('\n')
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.authority_path)
            self.state._fsync_directory(self.state.state_dir)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def record(self, plan, path):
        """Called under the deployment lock. Missing authority never auto-enables cleanup."""
        if not self.authority_path.exists():
            return
        data = self._authority()
        path = self._direct(path)
        if plan.branch not in {'debug', 'test', 'main'} or path.name[:40] != plan.sha:
            raise ValueError('invalid staged release identity')
        data['releases'][path.name] = {'branch': plan.branch, 'sha': plan.sha, 'created_at': time.time()}
        self._save_authority(data)

    def cleanup(self, *, apply=False, include_legacy=False):
        with self.state.lock():
            return self.cleanup_locked(apply=apply, include_legacy=include_legacy)

    def cleanup_locked(self, *, apply=False, include_legacy=False):
        """Caller MUST hold state.lock; re-evaluate all evidence before each removal."""
        report = self._snapshot(include_legacy)
        report['removed'] = []
        report['apply'] = apply
        if not apply or report['blocked']:
            return report
        for value in list(report['candidates']):
            latest = self._snapshot(include_legacy)
            for key in ('protected', 'retained', 'candidates'):
                report[key] = latest[key]
            if latest['blocked']:
                report['blocked'] = latest['blocked']
                break
            if value not in latest['candidates']:
                continue
            try:
                self._validate_disposable(Path(value))
                # One --force allows generated .venvs only; Git still refuses
                # locked worktrees (which require --force twice).
                self._git('worktree', 'remove', '--force', value)
                report['removed'].append(value)
                data = self._authority()
                data['releases'].pop(Path(value).name, None)
                self._save_authority(data)
                report['candidates'].pop(value, None)
            except Exception as exc:  # noqa: BLE001 - stop on uncertain state.
                report['blocked'].append(f'removal failed: {type(exc).__name__}')
                break
        return report

    def _validate_disposable(self, path):
        def git_output(*args):
            return subprocess.run(['git', '-C', str(path), *args], check=True,
                                  text=True, capture_output=True).stdout
        if git_output('status', '--porcelain', '-z', '--untracked-files=no'):
            raise ValueError('worktree has source changes')
        files = []
        for options in ((), ('--ignored',)):
            files.extend(git_output('ls-files', '--others', '--exclude-standard',
                                    '-z', *options).split('\0'))
        generated = (Path('.venvs'), Path('xl_updata_server/renderer/node_modules'))
        checked = set()
        for value in filter(None, files):
            relative = Path(value.rstrip('/'))
            candidate = path / relative
            root = next((root for root in generated if relative == root or root in relative.parents), None)
            if root is not None:
                generated_path = path / root
                if root not in checked and (not generated_path.is_dir() or generated_path.is_symlink()
                        or generated_path.resolve() != generated_path.absolute()):
                    raise ValueError('generated root must be a real directory')
                checked.add(root)
                continue
            if (relative.parent.name == '__pycache__' and candidate.is_file()
                    and candidate.suffix in {'.pyc', '.pyo'} and not candidate.is_symlink()
                    and candidate.resolve() == candidate.absolute()
                    and relative.parts[0] in {'xl_deploy', 'xl_qqbot', 'xl_updata_server', 'xl_updata_tool'}):
                continue
            raise ValueError('worktree has unknown untracked or ignored data')

    def _snapshot(self, include_legacy):
        report = {'protected': {}, 'retained': {}, 'candidates': {}, 'blocked': []}
        try:
            if (self.root is None or not self.root.is_absolute() or self.root.is_symlink()
                    or not self.root.is_dir() or self.root.resolve() != self.root
                    or self.current_root.is_symlink() or not self.current_root.is_dir()
                    or self.state.state_dir.is_symlink()):
                raise ValueError('invalid release/current/state root')
            data = self._authority()
            journal = self.state.load()
            if journal is None or self.state.journal_path.is_symlink():
                raise ValueError('missing journal authority')
            if (journal.phase not in JOURNAL_PHASES or journal.branch not in {'debug', 'test', 'main'}
                    or not re.fullmatch('[0-9a-f]{40}', journal.sha)
                    or Path(journal.candidate_release).name[:40] != journal.sha):
                raise ValueError('unknown journal identity or phase')
            registrations = {}
            for block in self._git('worktree', 'list', '--porcelain', '-z').split('\0\0'):
                fields = block.strip('\0').split('\0')
                row = {f.partition(' ')[0]: f.partition(' ')[2] for f in fields if f}
                if 'worktree' in row:
                    registrations[Path(row['worktree'])] = row
            releases = {}
            for path in self.root.iterdir():
                path = self._direct(path, pinned_legacy=data['pins'])
                row = registrations.get(path)
                if (row is None or row.get('HEAD') != path.name[:40]
                        or 'locked' in row or 'prunable' in row):
                    raise ValueError(f'unverified worktree: {path}')
                # Cross-check worktree .git ownership, not just a matching path/name.
                head = subprocess.run(['git', '-C', str(path), 'rev-parse', 'HEAD'],
                                      check=True, text=True, capture_output=True).stdout.strip()
                if head != row['HEAD'] or not (path / '.git').is_file() or (path / '.git').is_symlink():
                    raise ValueError('worktree identity mismatch')
                common = subprocess.run(['git', '-C', str(path), 'rev-parse', '--path-format=absolute',
                                         '--git-common-dir'], check=True, text=True,
                                        capture_output=True).stdout.strip()
                expected_common = self._git('rev-parse', '--path-format=absolute', '--git-common-dir').strip()
                if Path(common).resolve() != Path(expected_common).resolve():
                    raise ValueError('worktree belongs to another repository')
                releases[path] = data['releases'].get(path.name)
            def protect(value, reason):
                path = self._direct(value, pinned_legacy=data['pins'])
                if path not in releases:
                    raise ValueError('protected reference is not a registered release')
                report['protected'].setdefault(str(path), []).append(reason)
            pointers = list(self.current_root.iterdir())
            if not any(p.name == 'main' for p in pointers):
                raise ValueError('main current pointer is required')
            for pointer in pointers:
                if pointer.name not in {'debug', 'test', 'main'}:
                    raise ValueError('unknown current pointer')
                target = self.state.get_current(current_path=pointer)
                if target is None:
                    raise ValueError('missing current pointer target')
                protect(target, f'current:{pointer.name}')
            for key in ('pins', 'rollback'):
                for path in data[key]:
                    protect(path, key)
            # Even a completed journal is the durable most recent rollback handle.
            for value in (journal.previous_release, journal.candidate_release):
                if value:
                    protect(value, f'journal:{journal.phase}')
            for reference in self.live_references():
                reference = Path(reference)
                if not reference.is_absolute():
                    raise ValueError('invalid live reference')
                resolved = reference.resolve()
                for candidate in (reference, resolved):
                    if candidate.is_relative_to(self.root):
                        part = candidate.relative_to(self.root).parts
                        if not part:
                            raise ValueError('ambiguous live release reference')
                        protect(self.root / part[0], 'live-process-or-unit')
            groups = {}
            now = time.time()
            for path, record in releases.items():
                if record is None:
                    if not include_legacy:
                        report['retained'][str(path)] = ['legacy-requires-opt-in']
                        continue
                    branch, created = 'legacy', path.stat().st_mtime
                else:
                    branch, created = record['branch'], record['created_at']
                groups.setdefault(branch, []).append((created, path))
                if now - created < 86400:
                    report['retained'][str(path)] = ['age-floor:24h']
            for branch, entries in groups.items():
                if branch == 'legacy':
                    continue  # Explicit opt-in; no invented branch provenance.
                for _, path in sorted(entries, key=lambda entry: (entry[0], str(entry[1])), reverse=True)[:3]:
                    report['retained'].setdefault(str(path), []).append(f'recent-three:{branch}')
            for path in releases:
                key = str(path)
                if key not in report['protected'] and key not in report['retained']:
                    report['candidates'][key] = ['verified-worktree:outside-retention']
        except Exception as exc:  # noqa: BLE001 - default to no deletions on any ambiguity.
            report['blocked'].append(f'{type(exc).__name__}: {exc}')
            report['candidates'] = {}
        return report

    def _service_references(self):
        references = []
        for unit in self.units:
            result = subprocess.run(['systemctl', '--user', 'show', unit, '--property=LoadState',
                                     '--property=ActiveState', '--property=MainPID',
                                     '--property=ExecStart', '--property=WorkingDirectory',
                                     '--property=Environment'],
                                    text=True, capture_output=True, check=True)
            values = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
            if not {'LoadState', 'ActiveState', 'MainPID', 'ExecStart', 'WorkingDirectory', 'Environment'} <= values.keys():
                raise RuntimeError('incomplete systemd evidence')
            if values['ActiveState'] in {'active', 'activating', 'reloading'} and int(values['MainPID']) <= 0:
                raise RuntimeError('active unit has no inspectable process')
            cwd = values['WorkingDirectory']
            if cwd:
                references.extend(_dependency_paths(cwd))
            command = values['ExecStart']
            # Do not guess systemd escape encodings that could hide an alias.
            if '\\x' in command:
                raise ValueError('escaped systemd code paths require explicit inspection')
            executables = re.findall(r'(?:\{|\s)path=([^;]+?)\s*;', command)
            if command and not executables:
                raise ValueError('systemd executable paths cannot be interpreted')
            for executable in executables:
                references.extend(_dependency_paths(executable.strip()))
            for arguments in re.findall(r'argv\[\]=(.*?)\s*;', command):
                references.extend(_argument_paths(shlex.split(arguments, posix=os.name != 'nt')))
            references.extend(_environment_paths(
                shlex.split(values['Environment'], posix=os.name != 'nt'), cwd))
        return references

    def _live_references(self):
        """Inspect all owned processes and configured services, including shared Gateway.

        Failure to read an owned live process fails closed. Exited PIDs are harmless;
        hidden/restricted /proc is not a valid cleanup authority.
        """
        proc = Path('/proc')
        if os.name != 'posix' or not proc.is_dir() or not self.units:
            raise RuntimeError('Linux /proc and service inventory are required')
        references = self._service_references()
        for directory in proc.iterdir():
            if not directory.name.isdigit():
                continue
            try:
                if directory.stat().st_uid != os.getuid():
                    continue
                references.extend(process_paths(directory))
            except FileNotFoundError:
                if directory.exists():
                    # Kernel threads/zombies have no executable; ensure no runnable code.
                    status = (directory / 'status').read_text()
                    if not re.search(r'^State:\s+[ZX]', status, re.MULTILINE):
                        raise RuntimeError('live process references unavailable') from None
        return references
