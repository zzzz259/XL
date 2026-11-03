"""Explicit owned lazy package operation; never schedules or publishes globally."""
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .executor import BoundaryError, ResourceSlot


@dataclass(frozen=True)
class PackageRuntime:
    manifest_path: Path
    manifest_sha256: str


@dataclass(frozen=True)
class PreparedPackageRef:
    schema: str
    scope_key: str
    pin_digest: str
    version: str
    capture_sha256: str
    snapshot_digest: str
    tool_fingerprint: str
    character_id: str
    skin_id: str
    render_key: str
    package_path: Path
    input_root: Path
    manifest_size: int
    manifest_sha256: str
    stage_evidence: tuple


@contextmanager
def prepare_lazy_package(slot, ready_ref, prepared_ref, lazy_input, *, publication,
                         data_root, work_root, runtime, package_runtime, open_remote,
                         stop_event, timeout_seconds=360):
    if type(slot) is not ResourceSlot:
        raise BoundaryError('caller ResourceSlot required')
    import hashlib
    import math
    import os
    import shutil
    import sys
    import tempfile
    import time
    from dataclasses import asdict

    from . import source_io as io
    from .arts_worker import validate_package_request
    from .executor import (
        CleanupError,
        Deferred,
        check_admission,
        verify_resource_boundary,
    )
    from .model import digest
    from .package_protocol import read_bound_file, select_input
    from .package_validation import (
        validate_arts_counts,
        validate_extraction,
        validate_package_counts,
        validate_portable_package,
        validate_stage_result,
    )
    from .source_prepare import (
        DeadlineTransport,
        PreparationError,
        _bounded,
        _encoded,
        _load,
        _runtime,
        _sha,
        check_stage,
        read_prepared_source,
    )
    slot._require_owner()
    if slot.fd is None or slot.process is not None or slot.poisoned:
        raise BoundaryError('active idle owned lease required')
    os.fstat(slot.fd)
    verify_resource_boundary(slot.policy)
    if any(m.split('.')[0] in ('UnityPy','PIL','PySide6','PyQt5','numpy') for m in sys.modules):
        raise BoundaryError('heavy parent import')
    if type(timeout_seconds) not in (int,float) or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise PreparationError('finite shared deadline required')
    deadline = time.monotonic()+timeout_seconds
    def guard():
        slot._require_owner()
        if slot.fd is None or slot.process is not None or slot.poisoned:
            raise CleanupError('owned package slot unavailable')
        if stop_event is not None and stop_event.is_set():
            raise Deferred('stop')
        if time.monotonic() >= deadline:
            raise PreparationError('package shared deadline')
        check_admission(slot.policy,running=True)
    guard()
    # Publication and input references have intentionally distinct scope domains.
    request = publication.read_request(ready_ref)
    pin = publication.load_pin(request['version'])
    if (prepared_ref.instance_id,prepared_ref.source_id,prepared_ref.version,prepared_ref.pin_digest) != (
            pin.instance_id,pin.source_id,pin.version,pin.pin_digest):
        raise PreparationError('ready/prepared pin binding mismatch')
    env = _runtime(runtime,prepared_ref.tool_fingerprint)
    if type(open_remote) is not DeadlineTransport:
        raise BoundaryError('deadline transport required')
    open_remote.validate()
    data_root,work_root = Path(data_root),Path(work_root)
    if not data_root.is_absolute() or not work_root.is_absolute():
        raise BoundaryError('absolute storage required')
    document = read_prepared_source(prepared_ref,data_root=data_root)
    manifest = document['source_snapshot']['source_manifest']
    arts = _load(io.read_metadata(io.MetadataRef(**manifest['categories']['Arts']['extracted']),data_root=data_root))
    selected = select_input(document,lazy_input,arts)
    del document, arts
    if type(package_runtime) is not PackageRuntime:
        raise BoundaryError('trusted package runtime required')
    runtime_data = _bounded(package_runtime.manifest_path,2*1024**2)
    if hashlib.sha256(runtime_data).hexdigest() != package_runtime.manifest_sha256:
        raise BoundaryError('package runtime manifest mismatch')
    native = _load(runtime_data)
    pinned = dict(runtime.runtime_manifest)
    native_entries = [e for group in native.values() for e in ([group] if type(group) is dict else group)]
    if any(pinned.get(e['path']) != e['sha256'] for e in native_entries):
        raise BoundaryError('managed runtime missing from trusted runtime identity')
    for filename in ('SpineViewerCLI.dll','PortraitSceneCLI.dll'):
        actual = [e['sha256'] for e in native['app_files'] if Path(e['path']).name == filename]
        expected = [sha for name,sha in runtime.tool_manifest if Path(name).name == filename]
        if len(actual) != 1 or actual != expected:
            raise BoundaryError('actual managed tool fingerprint mismatch')
    for executable in ('/usr/bin/unshare','/usr/bin/mount','/usr/bin/sh'):
        if pinned.get(executable) != _sha(executable):
            raise BoundaryError('namespace tool identity mismatch')
    guard()
    with io._directory(work_root):
        area = Path(tempfile.mkdtemp(prefix='lazy-package-',dir=work_root))
    try:
        stage_a = area/'arts';stage_a.mkdir(mode=0o700)
        for name in ('views','work'):(stage_a/name).mkdir(mode=0o700)
        def budget():
            guard();return min(open_remote.quantum_seconds,deadline-time.monotonic())
        class Guarded:
            def __init__(self,handle):self.handle=handle
            def read(self,n):
                try:data=open_remote.read(self.handle,n,timeout=budget())
                except TimeoutError as exc:
                    guard();raise PreparationError('package transport timeout') from exc
                guard();return data
        @contextmanager
        def remote(name):
            handle=None
            try:
                try:handle=open_remote.open(name,timeout=budget())
                except TimeoutError as exc:
                    guard();raise PreparationError('package transport timeout') from exc
                guard();yield Guarded(handle)
            finally:
                if handle is not None:
                    open_remote.close(handle,timeout=max(0,min(open_remote.quantum_seconds,deadline-time.monotonic())))
            guard()
        descriptors=[]
        for candidate in selected['candidate_bundles']:
            guard()
            wire=candidate['wire_md5'];name=wire+'.bundle'
            blob=io.acquire_blob(pin,remote_name=name,wire_md5=wire,size=candidate['size'],cache_root=data_root/'wire',open_remote=remote)
            guard()
            view=io.normalize_bundle(blob,cache_root=data_root/'wire',output_root=data_root/'normalized')
            with io.open_normalized_bundle(view,normalized_root=data_root/'normalized') as source,(stage_a/'views'/name).open('xb') as output:
                shutil.copyfileobj(source,output,1024**2);output.flush();os.fsync(output.fileno())
            if _sha(stage_a/'views'/name)!=view.sha256:
                raise PreparationError('private normalized copy mismatch')
            descriptors.append(asdict(view))
        policy=asdict(slot.policy);policy['lock_path']=str(policy['lock_path'])
        for key in ('updater_status_paths','disk_roots'):policy[key]=list(map(str,policy[key]))
        source={'schema_version': 1,'operation_id': digest([area.name,pin.pin_digest,selected['render_key']]),
            'instance_id': pin.instance_id,'source_id': pin.source_id,'scope_key': pin.scope_key,'version': pin.version,
            'pin_digest': pin.pin_digest,'mode': 'textasset','exact_name': 'BaseFashion.lua',
            'normalized_root': str(data_root/'normalized'),'attempt_root': str(stage_a),'work_root': str(stage_a/'work'),
            'policy': policy,'decrypt_key_hex': runtime.decrypt_key_hex}
        a={'schema': 'portrait-package-stage/v1','stage': 'arts','source': source,'descriptors': descriptors,
            'selection': selected,'capture_sha256': prepared_ref.capture.sha256,'snapshot_digest': prepared_ref.snapshot_digest,
            'tool_fingerprint': prepared_ref.tool_fingerprint,'deadline': deadline}
        validate_package_request(a)
        def save(path,value,cap=65536):
            data=_encoded(value)
            if len(data)>cap:raise PreparationError('package protocol byte limit')
            with path.open('xb') as stream:
                os.chmod(path,0o600);stream.write(data);stream.flush();os.fsync(stream.fileno())
        def stage(command,log):
            guard()
            result=slot.run_stage(command,cwd=area,env=env,timeout=min(slot.policy.stage_seconds,deadline-time.monotonic()),log_root=area/log,stop_event=stop_event)
            check_stage(result);guard()
            return result
        save(area/'arts-request.json',a)
        first=stage([str(runtime.python),'-m','server_app.portraits.arts_worker','--request',str(area/'arts-request.json'),'--result',str(stage_a/'result.json')],'arts-logs')
        ar=_load(_bounded(stage_a/'result.json',65536))
        def bind(result,kind):
            validate_stage_result(result,a,kind)
        bind(ar,'arts')
        catalog=_load(read_bound_file(stage_a,ar['catalog'],cap=16*1024**2))
        scene_document=_load(read_bound_file(stage_a,ar['scene'],cap=16*1024**2))
        ledger=_load(read_bound_file(stage_a,ar['ledger'],cap=65536))
        exact_assets=validate_extraction(catalog,scene_document,ledger,a,stage_a/'materials')
        validate_arts_counts(ar['counts'],catalog,scene_document,ledger)
        stage_b=area/'package';stage_b.mkdir(mode=0o700)
        runtime_ref={'relative_path': 'runtime.json','size': len(runtime_data),'sha256': package_runtime.manifest_sha256}
        with (area/'runtime.json').open('xb') as stream:stream.write(runtime_data);stream.flush();os.fsync(stream.fileno())
        b={'schema': 'portrait-package-stage/v1','stage': 'package','arts_request': a,'arts_result': ar,
               'attempt_root': str(stage_b),'runtime_manifest': runtime_ref,'caller_uid': os.getuid(),
               'namespace_tools': {p:pinned[p] for p in ('/usr/bin/mount','/usr/bin/sh')},'python': str(runtime.python),'python_sha256': pinned[str(runtime.python)]}
        save(area/'package-request.json',b)
        second=stage(['/usr/bin/unshare','--user','--map-root-user','--mount','--pid','--fork','--mount-proc',
            str(runtime.python),'-m','server_app.portraits.package_worker','--request',str(area/'package-request.json'),'--result',str(stage_b/'result.json')],'package-logs')
        br=_load(_bounded(stage_b/'result.json',65536));bind(br,'package')
        raw=read_bound_file(stage_b,br['manifest'],cap=2*1024**2)
        package_doc=_load(raw);package_path=stage_b/br['manifest']['relative_path'];package_root=package_path.parent
        validate_portable_package(package_doc,package_root,scene=scene_document,ledger=ledger,
                                  material_root=stage_a/'materials',assets=exact_assets)
        validate_package_counts(br['counts'],package_doc)
        guard()
        yield PreparedPackageRef('portrait-prepared-package/v1',pin.scope_key,pin.pin_digest,pin.version,
            prepared_ref.capture.sha256,prepared_ref.snapshot_digest,prepared_ref.tool_fingerprint,
            selected['character_id'],selected['skin_id'],selected['render_key'],package_path,package_root,
            len(raw),hashlib.sha256(raw).hexdigest(),tuple(sorted({**ar['counts'],**br['counts'],
                'catalog_sha256':ar['catalog']['sha256'],'scene_sha256':ar['scene']['sha256'],
                'ledger_sha256':ar['ledger']['sha256'],'arts_reaped':first.reason=='exit',
                'package_reaped':second.reason=='exit'}.items())))
    finally:
        _cleanup_attempt(slot,area)


def _cleanup_attempt(slot,area):
    import shutil

    from .executor import CleanupError
    slot._require_owner()
    if slot.poisoned or slot.process is not None or slot.fd is None:
        raise CleanupError('retained package attempt after unconfirmed ownership or reap')
    try:shutil.rmtree(area)
    except OSError as exc:raise CleanupError('owned package cleanup failed') from exc
