"""Only verified OS session supervision may avoid process-content inspection."""
import os
import stat
import subprocess
from types import SimpleNamespace

import pytest


def make_process(proc, pid, parent, name, uid, group):
    directory = proc / str(pid)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'comm').write_text(name + '\n')
    (directory / 'status').write_text(
        f'Name:\t{name}\nPid:\t{pid}\nPPid:\t{parent}\nUid:\t{uid} {uid} {uid} {uid}\n')
    (directory / 'stat').write_text(f'{pid} ({name}) S {parent} ' + '0 '*17 + '12345 0\n')
    (directory / 'cgroup').write_text(f'1:name=systemd:{group}\n2:memory:/user.slice\n')


@pytest.mark.parametrize('damage', [None, 'false-name', 'wrong-parent', 'wrong-uid',
                                     'wrong-cgroup', 'prefix-cgroup', 'untrusted-binary', 'application'])
def test_only_verified_session_supervisors_avoid_unreadable_content(tmp_path, monkeypatch, damage):
    from xl_deploy import session_supervision
    from xl_deploy.release_storage import ReleaseStorage
    uid = os.getuid() if hasattr(os, 'getuid') else 0
    group = f'/user.slice/user-{uid}.slice/user@{uid}.service'
    make_process(tmp_path, 920, 1, 'systemd', uid, group + '/init.scope')
    make_process(tmp_path, 959, 920, '(sd-pam)', uid, group + '/init.scope')
    if damage == 'false-name':
        make_process(tmp_path, 959, 920, 'manual-app', uid, group + '/init.scope')
    if damage == 'wrong-parent':
        make_process(tmp_path, 959, 111, '(sd-pam)', uid, group + '/init.scope')
    if damage == 'wrong-uid':
        make_process(tmp_path, 959, 920, '(sd-pam)', uid + 1, group + '/init.scope')
    if damage in {'wrong-cgroup', 'prefix-cgroup'}:
        make_process(tmp_path, 959, 920, '(sd-pam)', uid,
                     group + ('/different' if damage == 'wrong-cgroup' else '/init.scope/extra'))
    if damage == 'application':
        make_process(tmp_path, 1100, 920, 'python', uid, group + '/init.scope')
    output = (f'MainPID=920\nControlGroup={group}\n'
              'ExecStart={ path=/usr/lib/systemd/systemd ; argv[]=/usr/lib/systemd/systemd --user ; }\n')
    def systemctl(args, **kwargs):
        assert args[:3] == ['systemctl', '--system', 'show']
        assert args[3] == f'user@{uid}.service'
        return subprocess.CompletedProcess(args, 0, output, '')
    monkeypatch.setattr(session_supervision.subprocess, 'run', systemctl)
    # Trusted binary metadata is an OS boundary; separately tested below.
    monkeypatch.setattr(session_supervision, 'trusted_systemd_binary', lambda _: damage != 'untrusted-binary')
    inspected = []
    def unreadable(directory):
        inspected.append(directory.name)
        raise PermissionError('application evidence is unavailable')
    monkeypatch.setattr('xl_deploy.release_storage.process_paths', unreadable)
    manager = object.__new__(ReleaseStorage)
    if damage is None:
        assert manager._owned_process_references(tmp_path, uid) == []
        assert inspected == []
    else:
        with pytest.raises(PermissionError):
            manager._owned_process_references(tmp_path, uid)
        assert inspected


@pytest.mark.parametrize(('owner', 'mode', 'expected'), [
    (0, 0o755, True), (1000, 0o755, False), (0, 0o775, False), (0, 0o777, False)])
def test_root_owned_nonwritable_binary_metadata(owner, mode, expected):
    from xl_deploy.session_supervision import root_owned_nonwritable
    assert root_owned_nonwritable(SimpleNamespace(st_uid=owner, st_mode=stat.S_IFREG | mode)) is expected
