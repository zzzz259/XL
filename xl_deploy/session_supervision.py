"""Verify OS session supervision using benign metadata, without reading environ.

There is no name-only exception: system-level unit identity, trusted binary,
UID, parentage and exact cgroup must all agree. Unknown evidence exempts nobody.
"""
from __future__ import annotations

import re
import stat
import subprocess
from pathlib import Path


def root_owned_nonwritable(info):
    return info.st_uid == 0 and not stat.S_IMODE(info.st_mode) & 0o022


def trusted_systemd_binary(value):
    if value not in {'/usr/lib/systemd/systemd', '/lib/systemd/systemd'}:
        return False
    try:
        binary = Path(value).resolve(strict=True)
        return (binary.name == 'systemd' and binary.is_file()
                and all(root_owned_nonwritable(path.stat()) for path in (binary, *binary.parents)))
    except OSError:
        return False


def process_identity(directory):
    """Read only comm/status/stat/cgroup; start time guards PID reuse."""
    status = dict(line.split(':', 1) for line in (directory / 'status').read_text().splitlines() if ':' in line)
    name = (directory / 'comm').read_text().strip()
    raw = (directory / 'stat').read_text()
    opening, closing = raw.index('('), raw.rindex(')')
    fields = raw[closing + 1:].split()
    pid, parent = int(status['Pid']), int(status['PPid'])
    uids = tuple(map(int, status['Uid'].split()))
    if (int(directory.name) != pid or int(raw[:opening].strip()) != pid
            or raw[opening + 1:closing] != name or status['Name'].strip() != name
            or int(fields[1]) != parent or len(uids) != 4):
        raise ValueError('inconsistent process identity metadata')
    groups = []
    for line in (directory / 'cgroup').read_text().splitlines():
        hierarchy, controllers, group = line.split(':', 2)
        if (hierarchy == '0' and not controllers) or 'name=systemd' in controllers.split(','):
            groups.append(group)
    if not groups or len(set(groups)) != 1:
        raise ValueError('missing or ambiguous systemd cgroup')
    return pid, parent, name, uids, groups[0], int(fields[19])


def verified_supervisors(proc, uid):
    """Return fingerprints for the exact manager and its sd-pam child only."""
    try:
        unit = f'user@{uid}.service'
        result = subprocess.run(['systemctl', '--system', 'show', unit, '--property=MainPID',
                                 '--property=ExecStart', '--property=ControlGroup'],
                                text=True, capture_output=True, check=True)
        values = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
        command = values['ExecStart']
        executable = re.findall(r'(?:\{|\s)path=([^;]+?)\s*;', command)
        if (len(executable) != 1 or not trusted_systemd_binary(executable[0].strip())
                or not re.search(r'argv\[\]=[^;]*\s--user(?:\s|;)', command)):
            return {}
        group = values['ControlGroup']
        if not group.startswith('/') or not group.endswith('/' + unit) or '..' in group.split('/'):
            return {}
        main_pid = int(values['MainPID'])
        if main_pid <= 1:
            return {}
        main = process_identity(proc / str(main_pid))
        allowed_groups = {group, group + '/init.scope'}
        if (main[1] != 1 or main[2] != 'systemd' or main[3] != (uid,) * 4
                or main[4] not in allowed_groups):
            return {}
        exemptions = {str(main_pid): main}
        for directory in proc.iterdir():
            if not directory.name.isdigit() or directory.name == str(main_pid):
                continue
            try:
                child = process_identity(directory)
                if (child[1] == main_pid and child[2] == '(sd-pam)' and child[3] == (uid,) * 4
                        and child[4] == main[4]):
                    exemptions[directory.name] = child
            except (OSError, ValueError, KeyError, IndexError):
                continue  # No exception granted; normal inspection still applies.
        return exemptions
    except (OSError, ValueError, KeyError, IndexError, subprocess.SubprocessError):
        return {}
