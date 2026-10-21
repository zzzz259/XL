"""Conservative release retention; authority and live evidence are mandatory.

No raw directory deletion is used. All removal goes through Git worktree remove
after full validation, under the same lock as deployment/recovery.
"""
from __future__ import annotations

import json
import math
import os
import re
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


def process_paths(directory):
    """Read code-bearing process evidence without returning secret environment keys."""
    paths = [os.readlink(directory / name) for name in ('cwd', 'exe')]
    paths.append((directory / 'cmdline').read_bytes().decode('utf-8', errors='strict'))
    for item in (directory / 'environ').read_bytes().split(b'\0'):
        key, _, value = item.partition(b'=')
        if key in {b'PYTHONPATH', b'PYTHONHOME', b'VIRTUAL_ENV'}:
            paths.append(value.decode('utf-8', errors='strict'))
    paths.append((directory / 'maps').read_text(encoding='utf-8'))
    return paths


class ReleaseStorage:
    def __init__(self, repository, state: DeploymentState, current_root, *,
                 units=(), live_references=None):
        self.repository = Path(repository).absolute()
        self.state = state
        self.root = state.releases_root
        self.current_root = Path(current_root).absolute()
        self.authority_path = state.state_dir / 'release-storage.json'
        self.units = tuple(units)
        self.live_references = live_references or self._live_references

    def _git(self, *args):
        return subprocess.run(['git', *map(str, args)], cwd=self.repository,
                              text=True, capture_output=True, check=True).stdout

    def _direct(self, value):
        path = Path(value)
        if (not path.is_absolute() or path.parent != self.root or path.is_symlink()
                or path.resolve() != path or not path.is_dir() or not NAME.fullmatch(path.name)):
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
            for path in data[key]:
                self._direct(path)
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
            if latest['blocked']:
                report['blocked'] = latest['blocked']
                break
            if value not in latest['candidates']:
                continue
            try:
                changes = subprocess.run(['git', '-C', value, 'status', '--porcelain', '-z',
                                          '--untracked-files=normal'], check=True,
                                         text=True, capture_output=True).stdout.split('\0')
                if any(change and change != '?? .venvs/' for change in changes):
                    raise ValueError('worktree has source changes or unknown untracked data')
                if (Path(value) / '.venvs').is_symlink():
                    raise ValueError('venv root must not be a symlink')
                # One --force allows generated .venvs only; Git still refuses
                # locked worktrees (which require --force twice).
                self._git('worktree', 'remove', '--force', value)
                report['removed'].append(value)
                data = self._authority()
                data['releases'].pop(Path(value).name, None)
                self._save_authority(data)
            except Exception as exc:  # noqa: BLE001 - stop on uncertain state.
                report['blocked'].append(f'removal failed: {type(exc).__name__}')
                break
        return report

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
                path = self._direct(path)
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
                path = self._direct(value)
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

    def _live_references(self):
        """Inspect all owned processes and configured services, including shared Gateway.

        Failure to read an owned live process fails closed. Exited PIDs are harmless;
        hidden/restricted /proc is not a valid cleanup authority.
        """
        proc = Path('/proc')
        if os.name != 'posix' or not proc.is_dir() or not self.units:
            raise RuntimeError('Linux /proc and service inventory are required')
        references = []
        pattern = re.compile(re.escape(str(self.root)) + r'/[^/\s\x00;:"\'=]+')
        def extract(text):
            references.extend(Path(match.group(0)) for match in pattern.finditer(text))
        for unit in sorted(self.units):
            result = subprocess.run(['systemctl', '--user', 'show', unit, '--property=LoadState',
                                     '--property=ActiveState', '--property=MainPID',
                                     '--property=ExecStart', '--property=WorkingDirectory'],
                                    text=True, capture_output=True, check=True)
            values = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
            if not {'LoadState', 'ActiveState', 'MainPID', 'ExecStart', 'WorkingDirectory'} <= values.keys():
                raise RuntimeError('incomplete systemd evidence')
            if values['ActiveState'] in {'active', 'activating', 'reloading'} and int(values['MainPID']) <= 0:
                raise RuntimeError('active unit has no inspectable process')
            extract(result.stdout)
        for directory in proc.iterdir():
            if not directory.name.isdigit():
                continue
            try:
                if directory.stat().st_uid != os.getuid():
                    continue
                for evidence in process_paths(directory):
                    extract(evidence)
            except FileNotFoundError:
                if directory.exists():
                    # Kernel threads/zombies have no executable; ensure no runnable code.
                    status = (directory / 'status').read_text()
                    if not re.search(r'^State:\s+[ZX]', status, re.MULTILINE):
                        raise RuntimeError('live process references unavailable') from None
        return references
