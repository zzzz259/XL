"""Small stdlib-only bindings shared by protected package phases."""
import hashlib
from pathlib import Path, PurePosixPath

from . import source_io as io
from .executor import BoundaryError, Deferred
from .source_prepare import PreparationError, _bounded


class StopLatch:
    """Boolean cancellation callback; retain terminal classification across recovery."""
    def __init__(self, guard):
        self.guard = guard
        self.reason = None

    def __call__(self):
        if self.reason is None:
            try:
                self.guard()
            except (Deferred, BoundaryError, PreparationError) as exc:
                self.reason = exc
        return self.reason is not None

    def raise_if_stopped(self):
        if self.reason is not None:
            raise self.reason


def read_bound_file(root, ref, *, cap):
    if type(ref) is not dict or set(ref) != {'relative_path', 'size', 'sha256'}:
        raise ValueError('invalid bound file reference')
    relative = ref['relative_path']
    if type(relative) is not str or '\\' in relative or ':' in relative:
        raise ValueError('unsafe bound file path')
    path = PurePosixPath(relative)
    if path.is_absolute() or '..' in path.parts or path.as_posix() != relative or not path.parts:
        raise ValueError('unsafe bound file path')
    if type(ref['size']) is not int or not 0 < ref['size'] <= cap:
        raise ValueError('bound file size limit')
    io._hex(ref['sha256'], 64)
    data = _bounded(Path(root).joinpath(*path.parts), cap)
    if len(data) != ref['size'] or hashlib.sha256(data).hexdigest() != ref['sha256']:
        raise ValueError('bound file content mismatch')
    return data


def select_input(document, lazy_input, arts):
    """Match full immutable selection and rederive candidate membership/closure."""
    from .input import _json_copy, build_input_snapshot
    lazy_input = _json_copy(lazy_input, max_bytes=4096)
    snapshot = document['source_snapshot']
    matches = [s for s in snapshot['skins'] if s['lazy_input'] == lazy_input]
    if len(matches) != 1:
        raise PreparationError('unknown exact lazy selection')
    selected = matches[0]
    rows = [r for r in document['catalog']['skins'] if
            (r['character_id'], r['skin_id']) == (selected['character_id'], selected['skin_id'])]
    if len(rows) != 1 or rows[0]['state'] != 'not_generated' or rows[0]['family'] != 'cardspine':
        raise PreparationError('selected skin unavailable')
    row = rows[0]
    if any(row[k] != selected[k] for k in ('render_key', 'config_fingerprint',
                                          'dependency_fingerprint', 'tool_fingerprint')):
        raise PreparationError('selected catalog binding mismatch')
    rebuilt = build_input_snapshot([selected['provenance']], arts,
        version=snapshot['version'], instance_id=snapshot['instance_id'], source_id=snapshot['source_id'],
        source_manifest=snapshot['source_manifest'], tool_fingerprint=selected['tool_fingerprint'])
    if rebuilt['source_snapshot']['skins'] != [selected]:
        raise PreparationError('selected root or Arts closure mismatch')
    candidates = selected['candidate_bundles']
    if not 0 < len(candidates) <= 32 or sum(b['size'] for b in candidates) > 256 * 1024**2:
        raise PreparationError('aggregate candidate budget')
    if len({b['wire_md5'] for b in candidates}) != len(candidates):
        raise PreparationError('duplicate physical candidate')
    return selected
