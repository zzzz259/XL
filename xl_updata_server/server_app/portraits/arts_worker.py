"""Protected selected Arts stage. Import is stdlib-only."""
import math

from .source_worker import IDENTITY, _hex, _keys, validate_request


def validate_package_request(value):
    common = {'schema', 'stage', 'source', 'descriptors', 'selection', 'capture_sha256',
              'snapshot_digest', 'tool_fingerprint', 'deadline'}
    _keys(value, common)
    from .input import _json_copy
    _json_copy(value,max_bytes=65536,max_nodes=2048)
    if value['schema'] != 'portrait-package-stage/v1' or value['stage'] != 'arts':
        raise ValueError('invalid package stage')
    if type(value['deadline']) not in (int, float) or not math.isfinite(value['deadline']):
        raise ValueError('invalid package deadline')
    for key in ('capture_sha256', 'snapshot_digest', 'tool_fingerprint'):
        _hex(value[key], 64)
    descriptors = value['descriptors']
    if type(descriptors) is not list or not 0 < len(descriptors) <= 32:
        raise ValueError('candidate count limit')
    checked = []
    for descriptor in descriptors:
        checked.append(validate_request({**value['source'], 'descriptor': descriptor}))
    if len({c['descriptor'].wire.wire_md5 for c in checked}) != len(checked):
        raise ValueError('duplicate physical candidate')
    if sum(c['descriptor'].wire.size for c in checked) > 256 * 1024**2:
        raise ValueError('aggregate candidate byte limit')
    from .package_validation import validate_selection
    selected = value['selection']
    validate_selection(selected,value['source'],value['tool_fingerprint'])
    candidates = selected.get('candidate_bundles')
    actual = sorted((d['descriptor'].wire.wire_md5, d['descriptor'].wire.size,
                     d['descriptor'].wire.remote_name) for d in checked)
    if type(candidates) is not list or actual != sorted((b['wire_md5'], b['size'], b['wire_md5']+'.bundle') for b in candidates):
        raise ValueError('candidate descriptor membership mismatch')
    return checked


def run_request(value, *, request_path, result_path):
    import os
    import time
    from contextlib import ExitStack, contextmanager
    from pathlib import Path

    from .source_io import open_normalized_bundle
    from .source_prepare import PreparationError, _sha
    from .source_worker import _directory, _encoded, _guard, _path, _publish
    checked = validate_package_request(value)
    first = checked[0]
    paths = first['paths']
    attempt, work = paths['attempt_root'], paths['work_root']
    normalized = paths['normalized_root']
    if _path(str(result_path)) != attempt/'result.json' or attempt in _path(str(request_path)).parents:
        raise ValueError('invalid stage request/result placement')
    def guard():
        _guard(first['policy'])
        if time.monotonic() >= value['deadline']:
            raise PreparationError('package shared deadline')
    with ExitStack() as stack:
        directory = stack.enter_context(_directory(attempt, private=True))
        workfd = stack.enter_context(_directory(work, private=True))
        views = attempt/'views'
        viewfd = stack.enter_context(_directory(views, private=True))
        if set(os.listdir(directory)) != {'work', 'views'} or os.listdir(workfd):
            raise ValueError('new owned stage required')
        expected = {c['descriptor'].wire.wire_md5+'.bundle' for c in checked}
        if set(os.listdir(viewfd)) != expected:
            raise ValueError('private view set mismatch')
        guard()
        by_path = {}
        for item in checked:
            d = item['descriptor']
            with open_normalized_bundle(d, normalized_root=normalized) as stream:
                if os.fstat(stream.fileno()).st_nlink != 1:
                    raise ValueError('normalized hardlink alias')
            path = views/(d.wire.wire_md5+'.bundle')
            if path.is_symlink() or path.stat().st_nlink != 1 or path.stat().st_size != d.size or _sha(path) != d.sha256:
                raise ValueError('private view identity mismatch')
            by_path[path] = d
        from .bundle_reader import ReaderLimits, open_bundle
        limits = ReaderLimits(objects=10000, json_bytes=16*1024**2)
        @contextmanager
        def provider(path):
            guard()
            d = by_path.get(Path(path))
            if d is None:
                raise ValueError('unverified metadata reopen')
            with open_bundle(d, normalized_root=normalized, work_root=work,
                             decrypt_key=bytes.fromhex(value['source']['decrypt_key_hex']), limits=limits) as reader:
                yield reader
            guard()
        catalog = {'schema': 'portrait-exact-catalog/v1', 'version_timestamp': int(value['source']['version']),
                       'assets': [], 'serialized_files': [], 'container_entries': []}
        preload_count = 0
        census_bytes = 0
        expanded_bytes = 0
        for path in by_path:
            with provider(path) as reader:
                census = reader.exact_census(catalog['version_timestamp'])
                expanded_bytes += reader.expanded_bytes
                if expanded_bytes > 512*1024**2:
                    raise ValueError('aggregate expanded candidate byte limit')
            preload_count += sum(len(s['preload']) for s in census['serialized_files'])
            if (len(catalog['assets'])+len(census['assets']) > 10000
                    or len(catalog['container_entries'])+len(census['container_entries']) > 10000
                    or preload_count > 100000):
                raise ValueError('aggregate census limit')
            for key in census:
                for record in census[key]:
                    census_bytes += len(_encoded(record))
                    if census_bytes > 16*1024**2-4096:
                        raise ValueError('aggregate exact catalog byte limit')
                    catalog[key].append(record)
        catalog_ref = _publish(directory, 'catalog.json', _encoded(catalog), 16*1024**2)
        from app.features.preview.portrait_config.asset_index import AssetIndex
        from app.features.preview.portrait_config.builder import build_portrait_scenes
        from app.features.preview.portrait_config.extracted_paths import (
            identity_output_path,
        )
        from app.features.preview.portrait_config.lua_config import PortraitRoot
        from app.features.preview.portrait_config.scope_policy import (
            required_scene_assets,
        )
        from app.features.preview.scene_capabilities import portrait_scene_capabilities
        index = AssetIndex.from_exact_catalog(attempt/'catalog.json', views, catalog['version_timestamp'],
                                              environment_provider=provider)
        memberships = {}
        builtin_edges = 0
        for entry in index.container_entries:
            for edge in entry.edges:
                if edge.target:
                    memberships.setdefault(edge.target,set()).add(entry.path)
                builtin_edges += edge.unresolved_reason == 'engine_builtin'
        selected = value['selection']; raw = selected['root']; provenance = selected['provenance']
        root = PortraitRoot(skin_id=raw['skin_id'], version_timestamp=catalog['version_timestamp'],
            prefab_name=raw['prefab_name'], position=tuple(raw['position']) if raw['position'] is not None else None,
            metadata=tuple((k, v) for k, v in (('card_id',raw['character_id']),('entry_id',raw['entry_id'])) if v is not None),
            evidence=provenance.get('evidence',''), field_evidence=tuple(tuple(p) for p in provenance.get('field_evidence',())),
            unresolved_reason=raw['unresolved_reason'])
        scenes = build_portrait_scenes(catalog['version_timestamp'], [root], index)
        guard()
        if len(scenes) != 1:
            raise ValueError('selected scene count')
        scene = scenes[0]
        if ((scene.character_id,scene.skin_id) != (selected['character_id'],selected['skin_id'])
                or not portrait_scene_capabilities(scene).can_export):
            raise ValueError('selected scene incomplete or unsupported')
        required = required_scene_assets(scene,index)
        if not 0 < len(required) <= 128:
            raise ValueError('selected extraction count limit')
        materials = attempt/'materials'; materials.mkdir(mode=0o700)
        grouped = {}
        for ref in required:
            matches = index.find_exact(ref.bundle_hash,ref.path_id)
            if len(matches) != 1:
                raise ValueError('required physical owner unavailable')
            asset = matches[0]
            grouped.setdefault(asset.bundle_path,[]).append(asset)
        ledger = []; total = 0
        for path, assets in sorted(grouped.items()):
            with provider(path) as reader:
                for asset in sorted(assets,key=lambda a:a.path_id):
                    guard()
                    extracted = reader.extract_selected(cab='CAB-'+asset.serialized_file,
                        path_id=asset.path_id,asset_type=asset.asset_type,name=asset.name)
                    data = extracted.pop('data'); total += len(data)
                    if total > 64*1024**2:
                        raise ValueError('aggregate extracted bytes')
                    relative = identity_output_path(asset)
                    target = materials.joinpath(*relative.parts)
                    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    with _directory(target.parent,private=True) as output:
                        bound = _publish(output,target.name,data,limits.selected_bytes)
                    bound['relative_path'] = relative.as_posix()
                    ledger.append(dict(**extracted,output=bound))
        scene_ref = _publish(directory,'scene.json',_encoded(scene.to_dict()),16*1024**2)
        ledger_ref = _publish(directory,'ledger.json',_encoded(ledger),65536)
        guard()
        result = dict(schema='portrait-package-stage-result/v1',stage='arts',status='ok',
            **{k:value['source'][k] for k in IDENTITY},capture_sha256=value['capture_sha256'],
            snapshot_digest=value['snapshot_digest'],tool_fingerprint=value['tool_fingerprint'],
            lazy_input=selected['lazy_input'],catalog=catalog_ref,scene=scene_ref,ledger=ledger_ref,
            counts={'objects': len(catalog['assets']),'entries': len(catalog['container_entries']),
                        'inputs': len(ledger),'parts': len(scene.parts),'output_bytes': total, 'expanded_bytes': expanded_bytes, 'multiple_memberships': sum(len(v)>1 for v in memberships.values()), 'builtin_edges': builtin_edges})
        from .package_validation import (
            validate_arts_counts,
            validate_extraction,
            validate_stage_result,
        )
        validate_stage_result(result,value,'arts')
        validate_extraction(catalog,scene.to_dict(),ledger,value,materials)
        validate_arts_counts(result['counts'],catalog,scene.to_dict(),ledger)
        _publish(directory,'result.json',_encoded(result),65536)
        return result


def main(argv=None):
    import sys

    from .executor import BoundaryError, Deferred
    from .source_worker import _load_request, _path
    try:
        args = sys.argv[1:] if argv is None else argv
        if len(args) != 4 or args[0] != '--request' or args[2] != '--result':
            raise ValueError('expected fixed request/result paths')
        request, result = _path(args[1]), _path(args[3])
        run_request(_load_request(request),request_path=request,result_path=result)
        return 0
    except Exception as exc:  # noqa: BLE001 - fixed worker exit taxonomy
        print('portrait Arts worker refused: '+type(exc).__name__,file=sys.stderr)
        return 2 if isinstance(exc,Deferred) else 3 if isinstance(exc,BoundaryError) else 1


if __name__ == '__main__':
    raise SystemExit(main())
