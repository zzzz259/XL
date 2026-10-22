"""Retention tests use disposable real Git worktrees; no server is contacted."""
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from xl_deploy.runner import CommandRunner
from xl_deploy.state import DeploymentState, Journal


def git(repo, *args):
    return subprocess.run(['git', '-c', 'user.name=Storage Test', '-c',
                           'user.email=storage@example.invalid', *map(str, args)],
                          cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


def process_link_reader(directory, cwd, executable):
    """Fake only proc links, leaving real dependency symlinks inspectable."""
    original = os.readlink
    def readlink(path, *args, **kwargs):
        if Path(path) == directory / 'cwd':
            return str(cwd)
        if Path(path) == directory / 'exe':
            return str(executable)
        return original(path, *args, **kwargs)
    return readlink


@pytest.fixture
def source(tmp_path):
    repo = tmp_path / 'source'
    repo.mkdir()
    git(repo, 'init')
    (repo / '.gitignore').write_text('*.db\nlogs/\nxl_updata_server/data/\n'
                                    '__pycache__/\nxl_updata_server/renderer/node_modules/\n')
    for name in ('xl_deploy/a.py', 'xl_qqbot/a.py', 'xl_updata_server/a.py',
                 'xl_updata_tool/tools/lua/tool.jar', 'xl_updata_tool/tools/windows.exe',
                 'xl_gacha_web/a.py', 'xl_gacha_android/a.js', 'README.md'):
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('fixture', encoding='utf-8')
    git(repo, 'add', '.')
    git(repo, 'commit', '-m', 'fixture')
    git(repo, 'remote', 'add', 'origin', repo)
    return repo, git(repo, 'rev-parse', 'HEAD')


def test_stage_materializes_only_runtime_allowlist(source, tmp_path):
    repo, sha = source
    path = tmp_path / 'releases' / sha
    CommandRunner(repository=repo).stage(SimpleNamespace(sha=sha, branch='test'), path)
    assert (path / 'xl_updata_tool/tools/lua/tool.jar').is_file()
    assert (path / 'xl_qqbot/a.py').is_file()
    assert (path / 'README.md').is_file()
    assert not (path / 'xl_updata_tool/tools/windows.exe').exists()
    assert not (path / 'xl_gacha_web').exists()
    assert not (path / 'xl_gacha_android').exists()
    assert git(path, 'rev-parse', 'HEAD') == sha


def test_sparse_server_retains_shared_headless_character_parser(source, tmp_path):
    repo, _ = source
    tool_app = Path(__file__).resolve().parents[2] / 'xl_updata_tool/app'
    shutil.copytree(tool_app, repo / 'xl_updata_tool/app',
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    git(repo, 'add', '.')
    git(repo, 'commit', '-m', 'shared parser fixture')
    sha = git(repo, 'rev-parse', 'HEAD')
    path = tmp_path / 'releases' / sha
    CommandRunner(repository=repo).stage(SimpleNamespace(sha=sha, branch='test'), path)
    result = subprocess.run([sys.executable, '-I', '-c',
        ('import sys; sys.path.insert(0, sys.argv[1]); '
        'from app.features.characters.parser import load_character_data; '
        'assert callable(load_character_data)'), str(path / 'xl_updata_tool')],
        cwd=path, text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
    assert not (path / 'xl_updata_tool/app/features/characters/page.py').exists()


@pytest.fixture
def storage(source, tmp_path):
    from xl_deploy.release_storage import ReleaseStorage
    repo, sha = source
    root = tmp_path / 'releases'
    root.mkdir()
    current = tmp_path / 'current'
    current.mkdir()
    state = DeploymentState(tmp_path / 'state', releases_root=root, current_path=current)
    state.state_dir.mkdir()
    authority = {'schema': 1, 'pins': [], 'rollback': [], 'releases': {}}
    paths = []
    for index in range(8):
        path = root / f'{sha}-{uuid.uuid4()}'
        git(repo, 'worktree', 'add', '--detach', path, sha)
        paths.append(path)
        authority['releases'][path.name] = {'branch': 'test', 'sha': sha,
                                              'created_at': time.time() - (10-index)*86400}
    # Use the existing adapter seam on Windows where directory symlinks need privilege.
    class Pointer:
        def read(self, pointer):
            return paths[0] if pointer.name == 'main' else None
    if os.name == 'nt':
        state.pointer_adapter = Pointer()
        (current / 'main').write_text('adapter fixture')
    else:
        (current / 'main').symlink_to(paths[0], target_is_directory=True)
    state.save(Journal('tx', 'test', sha, 'completed', str(paths[1]), str(paths[7]),
                       (), (), (), False))
    authority['pins'] = [str(paths[2])]
    authority['rollback'] = [str(paths[3])]
    (state.state_dir / 'release-storage.json').write_text(json.dumps(authority))
    manager = ReleaseStorage(repo, state, current, live_references=lambda: [paths[4] / 'xl_qqbot/a.py'])
    return manager, paths, authority


def test_current_old_gateway_rollback_and_three_recent_survive(storage):
    manager, paths, _ = storage
    report = manager.cleanup()
    assert not report['blocked']
    assert report['candidates'] == {}
    assert set(report['protected']) == {str(p) for p in paths[:5]} | {str(paths[7])}
    assert str(paths[5]) in report['retained']
    assert all(p.exists() for p in paths)


def test_legacy_opt_in_and_real_worktree_removal(storage):
    manager, paths, authority = storage
    authority['pins'] = []
    authority['rollback'] = []
    authority['releases'].pop(paths[2].name)
    os.utime(paths[2], (time.time() - 3*86400, time.time() - 3*86400))
    manager.authority_path.write_text(json.dumps(authority))
    report = manager.cleanup()
    assert str(paths[2]) in report['retained']
    assert str(paths[3]) in report['candidates']
    report = manager.cleanup(apply=True, include_legacy=True)
    assert not report['blocked']
    assert not paths[2].exists() and not paths[3].exists()
    assert str(paths[3]) not in git(manager.repository, 'worktree', 'list', '--porcelain')
    assert paths[0].exists() and paths[4].exists()


@pytest.mark.parametrize('damage', ['missing', 'corrupt', 'escape', 'journal', 'missing-journal'])
def test_unknown_authority_blocks_all_deletion(storage, damage):
    manager, paths, authority = storage
    if damage == 'missing':
        manager.authority_path.unlink()
    elif damage == 'corrupt':
        manager.authority_path.write_text('{}')
    elif damage == 'escape':
        authority['pins'] = [str(paths[0].parent.parent)]
        manager.authority_path.write_text(json.dumps(authority))
    elif damage == 'journal':
        manager.state.journal_path.write_text('{}')
    else:
        manager.state.journal_path.unlink()
    report = manager.cleanup(apply=True)
    assert report['blocked']
    assert all(p.exists() for p in paths)


def test_unfinished_journal_and_age_floor(storage):
    manager, paths, authority = storage
    authority['pins'] = []
    authority['rollback'] = []
    authority['releases'][paths[2].name]['created_at'] = time.time()
    manager.authority_path.write_text(json.dumps(authority))
    journal = manager.state.load()
    from dataclasses import replace
    manager.state.save(replace(journal, phase='stopping', previous_release=str(paths[3]),
                               candidate_release=str(paths[4])))
    report = manager.cleanup(apply=True)
    assert not report['blocked']
    assert paths[2].exists() and paths[3].exists() and paths[4].exists()
    assert not paths[1].exists()


def test_unknown_directory_blocks_even_other_valid_candidates(storage):
    manager, paths, _ = storage
    (paths[0].parent / 'unknown-data').mkdir()
    assert manager.cleanup(apply=True)['blocked']
    assert all(path.exists() for path in paths)


def test_generated_venv_can_be_removed_but_modified_source_cannot(storage):
    manager, paths, authority = storage
    authority['pins'] = []
    authority['rollback'] = []
    manager.authority_path.write_text(json.dumps(authority))
    (paths[2] / '.venvs').mkdir()
    (paths[2] / '.venvs/generated').write_text('generated')
    report = manager.cleanup(apply=True)
    assert not report['blocked']
    assert not paths[2].exists()


def test_modified_source_cannot_be_removed(storage):
    manager, paths, authority = storage
    authority['pins'] = []
    authority['rollback'] = []
    (paths[3] / 'xl_deploy/a.py').write_text('operator change')
    manager.authority_path.write_text(json.dumps(authority))
    assert manager.cleanup(apply=True)['blocked']
    assert paths[3].exists()


def test_process_legacy_cwd_protects_pythonpath_without_leaking_secrets(tmp_path):
    from xl_deploy.release_storage import process_paths
    directory = tmp_path / 'proc' / '123'
    directory.mkdir(parents=True)
    (directory / 'cmdline').write_bytes(b'python\0/home/admin/control/start.py\0')
    (directory / 'environ').write_bytes(
        f'API_TOKEN=do-not-return-this\0PYTHONPATH={tmp_path}\0'.encode())
    (directory / 'maps').write_text(f'000-001 r--p 000 00:00 0 {tmp_path}/mapped.so\n')
    # os.readlink is the only OS-specific boundary: process cwd is outside release.
    from unittest.mock import patch
    with patch('xl_deploy.release_storage.os.readlink',
               side_effect=process_link_reader(directory, tmp_path, sys.executable)):
        paths = process_paths(directory)
    assert str(tmp_path) in paths
    assert any('mapped.so' in path for path in paths)
    assert not any('do-not-return-this' in path for path in paths)


def test_live_evidence_changes_before_apply_prevents_removal(storage):
    manager, paths, authority = storage
    authority['pins'] = []
    authority['rollback'] = []
    manager.authority_path.write_text(json.dumps(authority))
    calls = []
    def live():
        calls.append(True)
        return [] if len(calls) == 1 else [paths[2], paths[3], paths[4]]
    manager.live_references = live
    report = manager.cleanup(apply=True)
    assert not report['blocked']
    assert paths[2].exists() and paths[3].exists() and paths[4].exists()
    assert str(paths[2]) in report['protected']
    assert str(paths[2]) not in report['candidates']


def test_unknown_journal_phase_blocks_cleanup(storage):
    from dataclasses import replace
    manager, paths, _ = storage
    manager.state.save(replace(manager.state.load(), phase='unrecognized-state'))
    assert manager.cleanup(apply=True)['blocked']
    assert all(path.exists() for path in paths)


def test_report_tracks_removal_even_if_metadata_write_fails(storage, monkeypatch):
    manager, paths, authority = storage
    authority['pins'] = []
    authority['rollback'] = []
    manager.authority_path.write_text(json.dumps(authority))
    def fail_write(data):
        raise OSError('full disk')
    monkeypatch.setattr(manager, '_save_authority', fail_write)
    report = manager.cleanup(apply=True)
    assert report['blocked']
    assert len(report['removed']) == 1
    removed = Path(report['removed'][0])
    assert removed in paths[2:4]
    assert not removed.exists()
    assert sum(path.exists() for path in paths[2:4]) == 1


@pytest.mark.parametrize('relative', ['operator.db', 'xl_updata_server/data/account.db',
                                     'logs/operator.log'])
def test_ignored_operator_data_blocks_removal(storage, relative):
    manager, paths, authority = storage
    authority['pins'] = []
    authority['rollback'] = []
    manager.authority_path.write_text(json.dumps(authority))
    for path in paths[2:4]:
        data = path / relative
        data.parent.mkdir(parents=True, exist_ok=True)
        data.write_text('preserve operator data')
    report = manager.cleanup(apply=True)
    assert report['blocked']
    assert all((path / relative).read_text() == 'preserve operator data' for path in paths[2:4])


def test_generated_renderer_and_python_cache_are_disposable(storage):
    manager, paths, authority = storage
    authority['pins'] = []
    authority['rollback'] = []
    manager.authority_path.write_text(json.dumps(authority))
    for relative in ('xl_updata_server/renderer/node_modules/package/index.js',
                     'xl_deploy/__pycache__/a.cpython-313.pyc',
                     '.venvs/bot/lib/site-packages/pkg/__pycache__/module.cpython-313.pyc'):
        file = paths[2] / relative
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(b'generated')
    report = manager.cleanup(apply=True)
    assert not report['blocked']
    assert not paths[2].exists()


def test_poll_service_remains_retention_dependency_without_restart_ownership(storage):
    from xl_deploy.release_storage import ReleaseStorage
    manager, _, _ = storage
    declared = {'xl-custom-bot.service', 'xl-custom-gateway.service'}
    storage = ReleaseStorage(manager.repository, manager.state, manager.current_root, units=declared)
    assert 'xl-deploy-poll.service' in storage.units
    assert declared == {'xl-custom-bot.service', 'xl-custom-gateway.service'}


def make_alias(alias, target):
    if os.name == 'nt':
        result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(alias), str(target)],
                                check=False, capture_output=True)
        assert result.returncode == 0, result.stderr
    else:
        alias.symlink_to(target, target_is_directory=True)


def test_alias_only_pythonpath_preserves_old_release(storage, tmp_path):
    from unittest.mock import patch

    from xl_deploy.release_storage import process_paths
    manager, paths, authority = storage
    authority['pins'] = []
    authority['rollback'] = []
    manager.authority_path.write_text(json.dumps(authority))
    alias = tmp_path / 'old-code-alias'
    make_alias(alias, paths[2])
    directory = tmp_path / 'fake-proc'
    directory.mkdir()
    (directory / 'cmdline').write_bytes(b'python\0-m\0xl_qqbot\0')
    (directory / 'environ').write_bytes(
        f'API_TOKEN=secret\0PYTHONPATH={alias}{os.pathsep}{tmp_path}\0'.encode())
    (directory / 'maps').write_text('')
    def live():
        with patch('xl_deploy.release_storage.os.readlink',
                   side_effect=process_link_reader(directory, tmp_path, sys.executable)):
            return [Path(path) for path in process_paths(directory)]
    manager.live_references = live
    report = manager.cleanup(apply=True)
    assert not report['blocked']
    assert paths[2].exists()
    assert str(paths[2]) in report['protected']


def test_inactive_poll_exec_alias_preserves_old_release(storage, tmp_path, monkeypatch):
    manager, paths, authority = storage
    authority['pins'] = []
    authority['rollback'] = []
    manager.authority_path.write_text(json.dumps(authority))
    alias = tmp_path / 'old-poll-alias'
    make_alias(alias, paths[2])
    python = alias / '.venvs/poll/bin/python'
    python.parent.mkdir(parents=True)
    python.write_text('generated interpreter')
    original_run = subprocess.run
    inspected = []
    def run(args, **kwargs):
        if args[0] != 'systemctl':
            return original_run(args, **kwargs)
        inspected.append(args[3])
        assert args[3] == 'xl-deploy-poll.service'
        output = (f'LoadState=loaded\nActiveState=inactive\nMainPID=0\n'
                  f'ExecStart={{ path={python} ; argv[]={python} -m xl_deploy ; }}\n'
                  f'WorkingDirectory={tmp_path}\nEnvironment=\n')
        return subprocess.CompletedProcess(args, 0, output, '')
    monkeypatch.setattr(subprocess, 'run', run)
    manager.live_references = manager._service_references
    report = manager.cleanup(apply=True)
    assert not report['blocked']
    assert inspected and paths[2].exists()
    assert str(paths[2]) in report['protected']


@pytest.mark.parametrize('access', ['direct', 'directory-alias', 'file-alias'])
@pytest.mark.parametrize('link_kind', [
    pytest.param('file', marks=pytest.mark.skipif(os.name == 'nt', reason='Windows lacks file-symlink privilege')),
    'directory-container',
])
def test_inactive_poll_symlinked_interpreter_preserves_release(storage, tmp_path, monkeypatch, access, link_kind):
    manager, paths, authority = storage
    authority['pins'] = []
    authority['rollback'] = []
    manager.authority_path.write_text(json.dumps(authority))
    venv_python = paths[2] / '.venvs/poll/bin/python'
    venv_python.parent.mkdir(parents=True)
    if link_kind == 'file':
        external = Path(sys.executable)
        venv_python.symlink_to(external)
    else:
        # Real junctions exercise the identical dependency traversal on Windows,
        # where file symbolic links require a privilege this machine lacks.
        external = tmp_path / 'external-system-runtime'
        external.mkdir()
        make_alias(venv_python, external)
    executable = venv_python
    if access == 'directory-alias':
        alias = tmp_path / 'external-venv'
        make_alias(alias, venv_python.parent)
        executable = alias / 'python'
    elif access == 'file-alias':
        executable = tmp_path / 'external-python'
        if link_kind == 'file':
            executable.symlink_to(venv_python)
        else:
            make_alias(executable, venv_python)
    assert executable.resolve() == external.resolve()
    assert not executable.resolve().is_relative_to(paths[2])
    original_run = subprocess.run
    def run(args, **kwargs):
        if args[0] != 'systemctl':
            return original_run(args, **kwargs)
        output = (f'LoadState=loaded\nActiveState=inactive\nMainPID=0\n'
                  f'ExecStart={{ path={executable} ; argv[]={executable} -m xl_deploy ; }}\n'
                  f'WorkingDirectory={tmp_path}\nEnvironment=\n')
        return subprocess.CompletedProcess(args, 0, output, '')
    monkeypatch.setattr(subprocess, 'run', run)
    manager.live_references = manager._service_references
    report = manager.cleanup(apply=True)
    assert not report['blocked']
    assert paths[2].exists()
    assert str(paths[2]) in report['protected']


@pytest.mark.parametrize('suffix', ['control-bootstrap', 'control-plane-source'])
def test_custom_legacy_name_requires_permanent_explicit_pin(storage, suffix):
    manager, paths, authority = storage
    sha = paths[0].name[:40]
    legacy = paths[0].parent / f'{sha}-{suffix}'
    git(manager.repository, 'worktree', 'add', '--detach', legacy, sha)
    assert manager.cleanup(apply=True, include_legacy=True)['blocked']
    assert legacy.exists()
    authority['pins'].append(str(legacy))
    manager.authority_path.write_text(json.dumps(authority))
    report = manager.cleanup(apply=True, include_legacy=True)
    assert not report['blocked']
    assert str(legacy) in report['protected']
    assert str(legacy) not in report['candidates']
    assert legacy.exists()


@pytest.mark.skipif(os.name == 'nt', reason='Windows directory symlinks require privileges')
def test_release_symlink_escape_fails_closed(storage, tmp_path):
    manager, paths, _ = storage
    outside = tmp_path / 'outside'
    outside.mkdir()
    link = paths[0].parent / ('a' * 40)
    link.symlink_to(outside, target_is_directory=True)
    assert manager.cleanup(apply=True)['blocked']
    assert outside.is_dir() and all(path.is_dir() for path in paths)
