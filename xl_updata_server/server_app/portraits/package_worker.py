"""Second guarded phase: real managed metadata and private portable packages."""
import os
import sys
import time
from pathlib import Path

from .arts_worker import validate_package_request
from .executor import BoundaryError, Deferred
from .package_protocol import StopLatch, read_bound_file
from .package_validation import (
    validate_arts_counts,
    validate_extraction,
    validate_package_counts,
    validate_portable_package,
    validate_stage_result,
)
from .source_prepare import PreparationError, _load, _sha
from .source_worker import (
    IDENTITY,
    _directory,
    _encoded,
    _guard,
    _keys,
    _path,
    _publish,
)


def run_request(value, *, request_path, result_path):
    _keys(value, {'schema','stage','arts_request','arts_result','attempt_root','runtime_manifest',
                  'caller_uid','namespace_tools','python','python_sha256'})
    if value['schema'] != 'portrait-package-stage/v1' or value['stage'] != 'package':
        raise ValueError('invalid package stage')
    a = value['arts_request']; checked = validate_package_request(a); first = checked[0]
    source = a['source']; ar = value['arts_result']
    attempt = _path(value['attempt_root']); area = attempt.parent
    stage_a = first['paths']['attempt_root']
    if (attempt != area/'package' or stage_a != area/'arts' or _path(str(result_path)) != attempt/'result.json'
            or _path(str(request_path)) != area/'package-request.json'):
        raise ValueError('invalid package stage paths')
    validate_stage_result(ar,a,'arts')
    if type(value['caller_uid']) is not int or value['caller_uid'] <= 0:
        raise BoundaryError('unprivileged namespace caller required')
    def guard():
        _guard(first['policy'])
        if time.monotonic() >= a['deadline']:
            raise PreparationError('package shared deadline')
    guard()
    if 'UnityPy' in sys.modules:
        raise BoundaryError('UnityPy forbidden in metadata phase')
    if Path('/proc/self/uid_map').read_text().split() != ['0',str(value['caller_uid']),'1']:
        raise BoundaryError('private user namespace required')
    with _directory(attempt,private=True) as directory:
        if os.listdir(directory):
            raise ValueError('new private metadata attempt required')
        native = _load(read_bound_file(area,value['runtime_manifest'],cap=2*1024**2))
        scene_raw = read_bound_file(stage_a,ar['scene'],cap=16*1024**2)
        scene_document = _load(scene_raw)
        catalog = _load(read_bound_file(stage_a,ar['catalog'],cap=16*1024**2))
        ledger = _load(read_bound_file(stage_a,ar['ledger'],cap=65536))
        exact_assets = validate_extraction(catalog,scene_document,ledger,a,stage_a/'materials')
        validate_arts_counts(ar['counts'],catalog,scene_document,ledger)
        identities = {(row['wire_md5'],row['path_id']) for row in ledger}
        from .private_namespace import prepare_namespace
        prepare_namespace(value,native,attempt,guard,a['deadline'])
        from app.platform.portrait_dynamic_profile import guarded_runtime
        runtime = guarded_runtime(native)
        del native
        from app.features.preview.portrait_config.asset_index import AssetIndex
        from app.features.preview.portrait_config.model import PortraitScene
        from app.features.preview.portrait_config.scope_policy import (
            required_scene_assets,
        )
        from app.features.preview.portrait_package import (
            load_portrait_package,
            publish_portrait_packages,
        )
        from app.platform.portrait_linux_runtime import (
            PrivateDisplay,
        )
        def forbidden(_):raise BoundaryError('metadata stage source reopen forbidden')
        index=AssetIndex.from_exact_catalog(stage_a/'catalog.json',stage_a/'views',int(source['version']),environment_provider=forbidden)
        scene=PortraitScene.from_dict(_load(scene_raw))
        if {(r.bundle_hash,r.path_id) for r in required_scene_assets(scene,index)}!=identities:
            raise ValueError('required scene extraction closure mismatch')
        stop=StopLatch(guard)
        managed=attempt/'managed';managed.mkdir(mode=0o700)
        for name in ('home','cache'):(attempt/name).mkdir(mode=0o700)
        try:
            launch=runtime.prepare(managed,deadline=a['deadline'],cancel_check=stop)
        finally:stop.raise_if_stopped()
        launch.env.update(HOME=str(attempt/'home'),XDG_CACHE_HOME=str(attempt/'cache'))
        metadata_pairs = []
        def metadata_provider(skeleton, atlas):
            try:result = launch.metadata_provider(skeleton, atlas)
            finally:stop.raise_if_stopped()
            metadata_pairs.append((str(skeleton),str(atlas)))
            return result
        try:
            with PrivateDisplay(launch):
                summary=publish_portrait_packages((scene,),index,stage_a/'materials',attempt/'output',
                    metadata_provider=metadata_provider,cancel_check=stop)
                stop.raise_if_stopped()
        finally:stop.raise_if_stopped()
        if (summary.published,summary.failed,summary.cancelled,summary.metadata_incomplete)!=(1,0,False,0):
            raise ValueError('complete metadata package required')
        package=load_portrait_package(summary.manifest_paths[0])
        validate_portable_package(package.manifest,package.manifest_path.parent,scene=scene_document,
                                  ledger=ledger,material_root=stage_a/'materials',assets=exact_assets)
        if package.scene.to_dict()!=scene.to_dict():
            raise ValueError('portable scene identity mismatch')
        if 'UnityPy' in sys.modules or launch.display is not None:
            raise BoundaryError('metadata child runtime not quiescent')
        guard()
        manifest=summary.manifest_paths[0]
        result=dict(schema='portrait-package-stage-result/v1',stage='package',status='ok',
            **{k:source[k] for k in IDENTITY},**{k:a[k] for k in ('capture_sha256','snapshot_digest','tool_fingerprint')},
            lazy_input=a['selection']['lazy_input'],manifest={'relative_path': manifest.relative_to(attempt).as_posix(),
            'size': manifest.stat().st_size,'sha256': _sha(manifest)},counts={'parts': len(package.scene.parts),'resources': len(package.resources),'metadata_pairs': len(metadata_pairs)})
        validate_stage_result(result,a,'package')
        validate_package_counts(result['counts'],package.manifest)
        _publish(directory,'result.json',_encoded(result),65536)
        return result


def main(argv=None):
    from .source_worker import _load_request
    try:
        args=sys.argv[1:] if argv is None else argv
        if len(args)!=4 or args[0]!='--request' or args[2]!='--result':raise ValueError('fixed request/result required')
        request,result=_path(args[1]),_path(args[3])
        run_request(_load_request(request),request_path=request,result_path=result)
        return 0
    except Exception as exc:  # noqa: BLE001 - fixed worker exit taxonomy
        print('portrait package worker refused: '+type(exc).__name__,file=sys.stderr)
        return 2 if isinstance(exc,Deferred) else 3 if isinstance(exc,BoundaryError) else 1


if __name__=='__main__':raise SystemExit(main())
