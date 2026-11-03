"""Miss-only acquisition and finite replay; configuration belongs to the owner."""
import math
import os
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from . import source_io as io
from .executor import (
    BoundaryError,
    Deferred,
    ResourceSlot,
    check_admission,
    verify_resource_boundary,
)
from .model import digest


class CacheMiss(Exception):
    """Name only. Raising this performs no IO."""
    def __init__(self, remote_name):
        self.remote_name = remote_name
        super().__init__('wire cache miss')


@dataclass
class FetchRequired(Deferred):
    scope_key: str
    pin_digest: str
    version: str
    remote_name: str
    wire_md5: str
    size: int
    provenance: str
    selection_ref: str

    def __setattr__(self, name, value):
        if name in self.__dataclass_fields__ and name in self.__dict__:
            raise AttributeError('immutable fetch field')
        super().__setattr__(name, value)

    def __delattr__(self, name):
        if name in self.__dataclass_fields__:
            raise AttributeError('immutable fetch field')
        super().__delattr__(name)


def miss_transport():
    from .source_prepare import DeadlineTransport
    def missing(name, *, timeout):
        raise CacheMiss(name)
    def unreachable(*args, timeout):
        raise BoundaryError('miss transport has no handle')
    return DeadlineTransport(missing, unreachable, unreachable)


def validate_fetch(pin, fetch):
    io._validate_pin(pin)
    if type(fetch) is not FetchRequired:
        raise ValueError('typed fetch required')
    if (fetch.scope_key, fetch.pin_digest, fetch.version) != (pin.scope_key, pin.pin_digest, pin.version):
        raise ValueError('fetch pin mismatch')
    io.validate_remote_name(fetch.remote_name)
    io._hex(fetch.wire_md5, 32)
    io._positive(fetch.size, io.MAX_BLOB_BYTES)
    io._hex(fetch.selection_ref, 64)
    if fetch.provenance == 'category':
        if not any((fetch.remote_name, fetch.wire_md5, fetch.size) ==
                   (f'{c.name.lower()}_{c.hash}.json', c.hash, c.size) for c in pin.categories):
            raise ValueError('category not in pin')
        if fetch.selection_ref != pin.pin_digest:
            raise ValueError('category provenance mismatch')
    elif fetch.provenance in ('Dataowner', 'lazyArts'):
        if fetch.remote_name != fetch.wire_md5 + '.bundle':
            raise ValueError('bundle identity mismatch')
    else:
        raise ValueError('unknown acquisition provenance')
    return fetch


def acquire_authorized(pin, *, remote_name, wire_md5, size, provenance, selection_ref, **kwargs):
    """Only call AFTER membership selection; this is not a membership authenticator."""
    fetch = validate_fetch(pin, FetchRequired(pin.scope_key, pin.pin_digest, pin.version,
                           remote_name, wire_md5, size, provenance, selection_ref))
    try:
        return io.acquire_blob(pin, remote_name=remote_name, wire_md5=wire_md5, size=size, **kwargs)
    except CacheMiss as exc:
        if exc.remote_name != remote_name:
            raise ValueError('transport miss identity mismatch') from exc
        raise fetch from exc


def _idle(slot):
    if type(slot) is not ResourceSlot:
        raise BoundaryError('owned ResourceSlot required')
    slot._require_owner()
    if slot.fd is None or slot.process is not None or slot.poisoned:
        raise BoundaryError('idle live lease required')
    os.fstat(slot.fd)


def fetch_once(slot, pin, fetch, *, data_root, work_root, runtime, tool_fingerprint,
               stop_event, timeout_seconds=60):
    """Call only after complete prepare unwind. No lifecycle/store authority."""
    from .source_download_worker import make_request, validate_request
    from .source_prepare import (
        PreparationError,
        _bounded,
        _encoded,
        _load,
        _runtime,
        check_stage,
    )
    _idle(slot)
    validate_fetch(pin, fetch)
    if type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError('finite fetch deadline required')
    verify_resource_boundary(slot.policy)
    check_admission(slot.policy, running=True)
    if stop_event is not None and stop_event.is_set():
        raise Deferred('stop')
    env = _runtime(runtime, tool_fingerprint)
    data_root, work_root = Path(data_root), Path(work_root)
    if not data_root.is_absolute() or not work_root.is_absolute():
        raise BoundaryError('absolute storage required')
    with io._directory(work_root):
        area = Path(tempfile.mkdtemp(prefix='source-fetch-', dir=work_root))
    attempt = area/'attempt'
    attempt.mkdir(mode=0o700)
    (attempt/'work').mkdir(mode=0o700)
    with io._directory(data_root/'wire', create=True):
        pass
    request = make_request(pin, fetch, slot.policy, data_root/'wire', attempt)
    validate_request(request)
    data = _encoded(request)
    if len(data) > 65536:
        raise PreparationError('download request byte limit')
    with (area/'request.json').open('xb') as stream:
        os.chmod(area/'request.json', 0o600)
        stream.write(data); stream.flush(); os.fsync(stream.fileno())
    result = slot.run_stage([str(runtime.python), '-m', 'server_app.portraits.source_download_worker',
                            '--request', str(area/'request.json'), '--result', str(attempt/'result.json')],
                           cwd=area, env=env, timeout=min(timeout_seconds, slot.policy.stage_seconds),
                           log_root=area/'logs', stop_event=stop_event)
    check_stage(result)
    _idle(slot)
    value = _load(_bounded(attempt/'result.json', 65536))
    if set(value) != {'schema', 'status', 'request_digest', 'blob'} or value['schema'] != 'portrait-download-result/v1' or value['status'] != 'ok' or value['request_digest'] != digest(request):
        raise PreparationError('download result binding mismatch')
    def no_remote(name):
        raise PreparationError('download reported absent CAS object')
    blob = io.acquire_blob(pin, remote_name=fetch.remote_name, wire_md5=fetch.wire_md5,
                           size=fetch.size, cache_root=data_root/'wire', open_remote=no_remote)
    if value['blob'] != asdict(blob):
        raise PreparationError('download CAS evidence mismatch')
    return blob


def replay(pin, prepare, fetch, *, max_fetches, max_wire_bytes, timeout_seconds, stop_event=None):
    """Callbacks close over the SAME pin. Context preparations must unwind first.

    No implicit network retries: one attempt per object. Cooperative Deferred
    escapes to the owner without consuming a persistent failure counter.
    """
    io._validate_pin(pin)
    io._positive(max_fetches, 10000)
    io._positive(max_wire_bytes, io.MAX_BLOB_BYTES*10000)
    if type(timeout_seconds) not in (int,float) or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError('finite replay deadline required')
    deadline = time.monotonic()+timeout_seconds
    seen = set(); total = 0
    while True:
        if stop_event is not None and stop_event.is_set():
            raise Deferred('stop')
        remaining = deadline-time.monotonic()
        if remaining <= 0:
            raise ValueError('replay deadline')
        try:
            return prepare(pin, remaining)
        except FetchRequired as required:
            validate_fetch(pin, required)
            key = (required.remote_name, required.wire_md5, required.size)
            if key in seen or len(seen) >= max_fetches or total+required.size > max_wire_bytes:
                raise ValueError('repeated miss or replay budget')
            remaining = deadline-time.monotonic()
            if remaining <= 0:
                raise ValueError('replay deadline')
            blob = fetch(pin, required, remaining)
            if type(blob) is not io.VerifiedBlob or (blob.scope_key, blob.pin_digest, blob.remote_name, blob.wire_md5, blob.size) != (pin.scope_key, pin.pin_digest, *key):
                raise ValueError('replay fetch evidence mismatch')
            seen.add(key); total += required.size
