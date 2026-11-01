"""Pure, bounded portrait metadata preparation; candidate graphs are not PPtr graphs.

No I/O is performed. The caller must supply already verified original catalog/config
metadata. Wire MD5s identify candidate downloads; SHA256s pin local source evidence.
Exact object resolution and package creation belong to a later claimed job.
"""
import json
import math
import re
from collections import defaultdict

from .model import build_catalog, digest

SCHEMA = 'portrait-input-v1'
CONFIG_ALGORITHM = 'cardspine-config-v1'
DEPENDENCY_ALGORITHM = 'candidate-bundle-graph-v2'
MAX_ROOTS = 10000
MAX_ASSETS = 100000
MAX_BUNDLES = 100000
MAX_BYTES = 32 * 1024 * 1024
MAX_NODES = 1000000
MAX_DEPTH = 32
MAX_CANDIDATE_ITEMS = 250000


def _json_copy(value, *, max_bytes=MAX_BYTES, max_nodes=MAX_NODES):
    """Validate before recursive serialization: reject cycles and bounded-depth abuse."""
    stack = [(value, 0, False)]
    active = set()
    count = 0
    text_bytes = 0
    while stack:
        item, depth, leaving = stack.pop()
        if leaving:
            active.remove(id(item))
            continue
        count += 1
        if count > max_nodes or depth > MAX_DEPTH:
            raise ValueError('metadata count/depth limit')
        if type(item) in (dict, list):
            if id(item) in active:
                raise ValueError('cyclic metadata')
            if len(item) > max_nodes:
                raise ValueError('metadata count limit')
            active.add(id(item))
            stack.append((item, depth, True))
            if type(item) is dict:
                for key, child in item.items():
                    if type(key) is not str:
                        raise ValueError('metadata keys must be strings')
                    text_bytes += len(key.encode('utf-8'))
                    stack.append((child, depth + 1, False))
            else:
                stack.extend((child, depth + 1, False) for child in item)
        elif type(item) is str:
            text_bytes += len(item.encode('utf-8'))
        elif type(item) is float:
            if not math.isfinite(item):
                raise ValueError('nonfinite metadata number')
        elif type(item) not in (int, bool, type(None)):
            raise ValueError('metadata must be plain JSON')
        if text_bytes > max_bytes:
            raise ValueError('metadata byte limit')
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (ValueError, OverflowError, RecursionError) as exc:
        raise ValueError('invalid JSON metadata') from exc
    if len(encoded.encode('utf-8')) > max_bytes:
        raise ValueError('metadata byte limit')
    return json.loads(encoded)


def _text(value, label):
    if type(value) is not str or not value.strip() or value != value.strip():
        raise ValueError('invalid ' + label)
    return value


def _id(value, label):
    if type(value) is not str or re.fullmatch(r'[1-9][0-9]*', value) is None:
        raise ValueError('invalid ' + label)
    return value


def _hash(value, length):
    if type(value) is not str or re.fullmatch('[0-9a-f]{%d}' % length, value) is None:
        raise ValueError('invalid content hash')
    return value


def _path(value, *, empty=False):
    if empty and value == '':
        return ''
    value = _text(value, 'relative POSIX path')
    if '\\' in value or ':' in value or any(ord(c) < 32 for c in value):
        raise ValueError('unsafe catalog path')
    if any(part in ('', '.', '..') for part in value.split('/')):
        raise ValueError('unsafe catalog path')
    return value


def _size(value):
    if type(value) is not int or not 0 <= value < 2**63:
        raise ValueError('invalid declared size')
    return value


def _index(value, count):
    if type(value) is not int or not 0 <= value < count:
        raise ValueError('invalid catalog reference')
    return value


def _refs(value, count):
    if type(value) is not list:
        raise ValueError('invalid catalog dependency list')
    return sorted(set(_index(i, count) for i in value))


def _scope(value, instance_id, source_id, version, *, required=False):
    for key, expected in (('instance_id',instance_id),('source_id',source_id),('version',version)):
        if (required or key in value) and value.get(key) != expected:
            raise ValueError('source manifest scope/version mismatch')


def _coordinate(value):
    if type(value) not in (int,float):
        raise ValueError('invalid show_spine_position')
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        raise ValueError('invalid show_spine_position')
    return int(value) if type(value) is float and value.is_integer() else value


def _manifest(value, instance_id, source_id, version):
    value = _json_copy(value, max_bytes=1024*1024, max_nodes=20000)
    if type(value) is not dict:
        raise ValueError('source manifest must be an object')
    _scope(value,instance_id,source_id,version,required=True)
    _path(value.get('version_file'))
    _hash(value.get('version_sha256'),64)
    categories = value.get('categories')
    if type(categories) is not dict:
        raise ValueError('missing pinned categories')
    for category in ('Data','Arts'):
        row = categories.get(category)
        if type(row) is not dict:
            raise ValueError('missing pinned category')
        _scope(row,instance_id,source_id,version)
        _text(row.get('name'),'category name')
        _hash(row.get('wire_md5'),32)
        _hash(row.get('sha256'),64)
        _size(row.get('size'))
        _path(row.get('locator'))
    config = value.get('config')
    if type(config) is not dict:
        raise ValueError('missing pinned config')
    _scope(config,instance_id,source_id,version)
    _hash(config.get('wire_md5'),32)  # Owning Data bundle, not decoded Lua bytes.
    _hash(config.get('decoded_sha256'),64)
    _text(config.get('decoder'),'immutable decoder identity')
    _path(config.get('locator'))
    return value


def _catalog(value):
    value = _json_copy(value)
    if type(value) is not dict or any(type(value.get(k)) is not list for k in ('dirs','bundles','assets')):
        raise ValueError('invalid original Arts catalog')
    if len(value['assets']) > MAX_ASSETS or len(value['bundles']) > MAX_BUNDLES:
        raise ValueError('catalog row limit')
    dirs = [_path(d,empty=True) for d in value['dirs']]
    bundles = []
    names = set()
    for row in value['bundles']:
        if type(row) is not dict:
            raise ValueError('invalid bundle row')
        name = _path(row.get('name'))
        if name in names:
            raise ValueError('duplicate stable bundle name')
        names.add(name)
        bundles.append(dict(name=name,wire_md5=_hash(row.get('hash'),32),size=_size(row.get('size')),
                            deps=_refs(row.get('deps'),len(value['bundles']))))
    assets = []
    asset_identities = set()
    for row in value['assets']:
        if type(row) is not dict:
            raise ValueError('invalid asset row')
        name = _path(row.get('name'))
        directory = dirs[_index(row.get('dir'),len(dirs))]
        full_path = directory + '/' + name if directory else name
        bundle = _index(row.get('bundle'),len(bundles))
        identity = (full_path,bundles[bundle]['name'])
        if identity in asset_identities:
            raise ValueError('duplicate stable asset identity')
        asset_identities.add(identity)
        assets.append(dict(full_path=full_path,bundle=bundle,
                           deps=_refs(row.get('deps'),len(value['assets']))))
    return bundles, assets


def _asset_identity(asset, bundles):
    return dict(full_path=asset['full_path'],bundle_name=bundles[asset['bundle']]['name'])


def _closure(selected, bundles, assets):
    """Union asset-dependency owner bundles and their bundle graph, cycle safely."""
    asset_seen = set()
    todo = [selected]
    while todo:
        index = todo.pop()
        if index in asset_seen:
            continue
        asset_seen.add(index)
        todo.extend(assets[index]['deps'])
    todo = [assets[i]['bundle'] for i in asset_seen]
    bundle_seen = set()
    while todo:
        index = todo.pop()
        if index in bundle_seen:
            continue
        bundle_seen.add(index)
        todo.extend(bundles[index]['deps'])
    nodes = sorted(({k:bundles[i][k] for k in ('name','wire_md5','size')} for i in bundle_seen), key=lambda n:n['name'])
    edges = sorted({(bundles[i]['name'],bundles[j]['name']) for i in bundle_seen for j in bundles[i]['deps']})
    # Stable identities retain exact original case; indices never escape this module.
    asset_nodes = sorted((_asset_identity(assets[i],bundles) for i in asset_seen),key=lambda n:(n['full_path'],n['bundle_name']))
    asset_edges = sorted({(assets[i]['full_path'],bundles[assets[i]['bundle']]['name'],
                           assets[j]['full_path'],bundles[assets[j]['bundle']]['name'])
                          for i in asset_seen for j in assets[i]['deps']})
    asset_edges = [dict(source=dict(full_path=sp,bundle_name=sb),target=dict(full_path=tp,bundle_name=tb))
                   for sp,sb,tp,tb in asset_edges]
    return nodes, [list(e) for e in edges], asset_nodes, asset_edges


def build_input_snapshot(roots, arts_catalog, *, version, instance_id, source_id,
                         source_manifest, tool_fingerprint, character_names=None):
    """Return independent JSON catalog and source snapshot, with no packages or jobs.

    Root dictionaries use explicit character_id (never filename-derived), canonical
    skin_id/entry_id, optional position [x,y] or [x,y,z], prefab_name, family and evidence.
    A null position means absent; malformed positions fail the entire preparation.
    Source publication and first-baseline detection are the caller's responsibility.
    """
    for value, label in ((version,'version'),(instance_id,'instance_id'),(source_id,'source_id')):
        _text(value,label)
    _hash(tool_fingerprint,64)
    source_manifest = _manifest(source_manifest,instance_id,source_id,version)
    roots = _json_copy(roots)
    if type(roots) is not list or len(roots) > MAX_ROOTS:
        raise ValueError('invalid root count')
    character_names = _json_copy(character_names if character_names is not None else {})
    if type(character_names) is not dict or any(not isinstance(n,str) for n in character_names.values()):
        raise ValueError('invalid character names')
    bundles, assets = _catalog(arts_catalog)
    by_path, by_basename = defaultdict(list), defaultdict(list)
    for i, asset in enumerate(assets):
        by_path[asset['full_path'].casefold()].append(i)
        by_basename[asset['full_path'].rsplit('/',1)[-1].casefold()].append(i)
    rows, prepared, unassigned = [], [], []
    identities = set()
    candidate_items = 0
    for original in roots:
        if type(original) is not dict:
            raise ValueError('invalid root record')
        for field in ('version','version_timestamp'):
            if field in original and str(original[field]) != version:
                raise ValueError('root version mismatch')
        skin_id = _id(original.get('skin_id'),'skin_id')
        character_id = original.get('character_id')
        if character_id is not None:
            _id(character_id,'character_id')
        entry_id = original.get('entry_id')
        if entry_id is not None:
            _id(entry_id,'entry_id')
        prefab = original.get('prefab_name')
        if prefab is not None:
            prefab = _path(prefab).casefold()
        family = original.get('family')
        if family in ('battlespine','eventcover'):
            continue
        if family not in (None,'cardspine'):
            raise ValueError('unknown portrait family')
        basename = prefab.rsplit('/',1)[-1] if prefab else None
        if basename and not (basename.startswith('cardspine_') and basename.endswith('.prefab')):
            # Structural known non-character families are excluded; never guess ownership.
            if basename.startswith(('battlespine_','eventcovers_','eventcover_')):
                continue
            raise ValueError('unsupported configured prefab')
        position = original.get('position')
        if position is not None:
            if type(position) is not list or len(position) not in (2,3):
                raise ValueError('invalid show_spine_position')
            position = [_coordinate(x) for x in position]
        reason = original.get('unresolved_reason')
        if reason is not None:
            _text(reason,'unresolved_reason')
        diagnostics = original.get('diagnostics',[])
        if type(diagnostics) is not list or any(type(d) is not str for d in diagnostics):
            raise ValueError('invalid root diagnostics')
        normalized = dict(character_id=character_id,skin_id=skin_id,entry_id=entry_id,prefab_name=prefab,position=position,unresolved_reason=reason)
        if character_id is None:
            unassigned.append(dict(root=normalized,provenance=original,diagnostics=sorted(set(diagnostics+['missing_character_id']))))
            continue
        identity = (character_id,skin_id)
        if identity in identities:
            raise ValueError('duplicate configured skin')
        identities.add(identity)
        failures = []
        if reason:
            failures.append(reason)
        matches = (by_path if prefab and '/' in prefab else by_basename).get(prefab,[]) if prefab else []
        if not prefab:
            failures.append('missing_show_spine')
        elif not matches:
            failures.append('missing_prefab')
        elif len(matches) != 1:
            failures.append('ambiguous_prefab')
        candidates = sorted((_asset_identity(assets[i],bundles) for i in matches), key=lambda a:(a['full_path'],a['bundle_name']))
        nodes, edges, asset_nodes, asset_edges = _closure(matches[0],bundles,assets) if len(matches)==1 else ([],[],[],[])
        candidate_items += len(nodes)+len(edges)+len(asset_nodes)+len(asset_edges)+len(candidates)
        if candidate_items > MAX_CANDIDATE_ITEMS:
            raise ValueError('aggregate candidate metadata limit')
        config_hash = digest(dict(algorithm=CONFIG_ALGORITHM,**normalized))
        dependency_hash = digest(dict(algorithm=DEPENDENCY_ALGORITHM,configured_prefab=prefab,
                                     matched_assets=candidates,candidate_bundles=nodes,bundle_edges=edges,
                                     asset_dependencies=asset_nodes,asset_edges=asset_edges,
                                     failures=sorted(set(failures))))
        row = dict(character_id=character_id,skin_id=skin_id,name=character_names.get(character_id,''),family='cardspine',
                   state='blocked' if failures else 'not_generated',diagnostics=sorted(set(diagnostics+failures)),
                   config_fingerprint=config_hash,dependency_fingerprint=dependency_hash,tool_fingerprint=tool_fingerprint)
        rows.append(row)
        prepared.append(dict(character_id=character_id,skin_id=skin_id,root=normalized,provenance=original,
                             matched_assets=candidates,candidate_bundles=nodes,bundle_edges=edges,asset_dependencies=asset_nodes,asset_edges=asset_edges,
                             diagnostics=row['diagnostics'],config_fingerprint=config_hash,dependency_fingerprint=dependency_hash,
                             tool_fingerprint=tool_fingerprint))
    catalog = build_catalog(instance_id,source_id,version,rows)
    prepared.sort(key=lambda r:(int(r['character_id']),int(r['skin_id'])))
    # Equal numeric skins remain diagnostic inventory; canonical retained content
    # gives them a total order, including differing entry identity or provenance.
    unassigned.sort(key=lambda r:(int(r['root']['skin_id']),json.dumps(r,sort_keys=True,separators=(',',':'),ensure_ascii=False)))
    for skin, row in zip(prepared,catalog['skins']):
        skin['render_key'] = row['render_key']
        skin['lazy_input'] = dict(instance_id=instance_id,source_id=source_id,version=version,
                                 character_id=skin['character_id'],skin_id=skin['skin_id'],render_key=row['render_key'])
    snapshot = dict(schema=SCHEMA,instance_id=instance_id,source_id=source_id,version=version,
                    config_algorithm=CONFIG_ALGORITHM,dependency_algorithm=DEPENDENCY_ALGORITHM,
                    source_manifest=source_manifest,skins=prepared,unassigned_roots=unassigned)
    snapshot = _json_copy(snapshot)
    snapshot['snapshot_digest'] = digest(snapshot)
    return dict(catalog=catalog,source_snapshot=snapshot)
