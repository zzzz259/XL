"""Strict protected-stage schemas and content bindings. Stdlib only; no media imports."""
import hashlib
import json
import math
import re
from pathlib import Path, PurePosixPath

from .input import CONFIG_ALGORITHM, DEPENDENCY_ALGORITHM, _json_copy
from .model import digest, render_key
from .package_protocol import read_bound_file


def fields(value, required, optional=()):
    if type(value) is not dict or not set(required) <= set(value) or set(value)-set(required)-set(optional):
        raise ValueError('protected schema fields')


def text(value, *, empty=False, cap=4096):
    if type(value) is not str or len(value)>cap or (not empty and not value) or '\0' in value:
        raise ValueError('protected text')
    return value


def integer(value, low=0, high=2**63-1):
    if type(value) is not int or not low<=value<=high:
        raise ValueError('protected integer')
    return value


def number(value):
    if type(value) not in (int,float) or not math.isfinite(value):
        raise ValueError('protected finite number')
    return value


def identifier(value):
    if type(value) is not str or re.fullmatch('[1-9][0-9]{0,63}',value) is None:
        raise ValueError('protected numeric identifier')
    return value


def hexdigest(value, size=64):
    if type(value) is not str or re.fullmatch(f'[0-9a-f]{{{size}}}',value) is None:
        raise ValueError('protected digest')
    return value


def sequence(value, cap=10000):
    if type(value) is not list or len(value)>cap:
        raise ValueError('protected sequence')
    return value


def relative(value):
    text(value)
    path=PurePosixPath(value)
    if '\\' in value or ':' in value or path.is_absolute() or '..' in path.parts or not path.parts or path.as_posix()!=value:
        raise ValueError('protected relative path')
    return value


def pairs(value):
    for pair in sequence(value):
        if type(pair) is not list or len(pair)!=2:raise ValueError('protected evidence pair')
        for item in pair:text(item,empty=True)


def scene_pairs(value):
    for pair in sequence(value):
        if type(pair) is not list or len(pair)!=2:raise ValueError('protected scene evidence pair')
        text(pair[0]);text(pair[1],empty=True,cap=16*1024**2 if pair[0]=='exact_container_bindings' else 65536)


def position(value):
    if value is not None:
        if len(sequence(value,3)) not in (2,3):raise ValueError('protected position')
        for coordinate in value:number(coordinate)


def validate_selection(selected, source, tool_fingerprint):
    fields(selected, {'character_id','skin_id','root','provenance','matched_assets','candidate_bundles',
        'bundle_edges','asset_dependencies','asset_edges','diagnostics','config_fingerprint',
        'dependency_fingerprint','tool_fingerprint','render_key','lazy_input'})
    lazy=selected['lazy_input']
    fields(lazy, {'instance_id','source_id','version','character_id','skin_id','render_key'})
    for key in ('instance_id','source_id'):
        text(lazy[key],cap=256)
        if lazy[key]!=source[key]:raise ValueError('selection scope mismatch')
    if identifier(lazy['version'])!=source['version']:raise ValueError('selection version mismatch')
    for key in ('character_id','skin_id'):
        if identifier(selected[key])!=identifier(lazy[key]):raise ValueError('selection ID mismatch')
    for key in ('config_fingerprint','dependency_fingerprint','tool_fingerprint','render_key'):
        hexdigest(selected[key])
    if selected['tool_fingerprint']!=tool_fingerprint or lazy['render_key']!=selected['render_key']:
        raise ValueError('selection fingerprint binding')
    root=selected['root'];root_keys={'character_id','skin_id','entry_id','prefab_name','position','unresolved_reason'}
    fields(root,root_keys)
    for key in ('character_id','skin_id'):
        if identifier(root[key])!=selected[key]:raise ValueError('root selection identity')
    if root['entry_id'] is not None:identifier(root['entry_id'])
    prefab=relative(root['prefab_name'])
    if prefab!=prefab.casefold() or not prefab.rsplit('/',1)[-1].startswith('cardspine_') or not prefab.endswith('.prefab'):
        raise ValueError('selected cardspine prefab')
    position(root['position'])
    if root['unresolved_reason'] is not None:raise ValueError('unresolved selected root')
    provenance=selected['provenance']
    fields(provenance,root_keys|{'version_timestamp','evidence'}, {'field_evidence'})
    if integer(provenance['version_timestamp'],1)!=int(source['version']):raise ValueError('provenance version')
    text(provenance['evidence'])
    if 'field_evidence' in provenance:pairs(provenance['field_evidence'])
    for key in root_keys:
        original=provenance[key]
        if key=='prefab_name':original=relative(original).casefold()
        if key=='position':position(original)
        if key in ('skin_id','character_id') or key=='entry_id' and original is not None:identifier(original)
        if original!=root[key]:raise ValueError('provenance/root mismatch')
    candidates=sequence(selected['candidate_bundles'],32);names=set();wires=set();total=0
    for candidate in candidates:
        fields(candidate,{'name','wire_md5','size'})
        name=relative(candidate['name']);wire=hexdigest(candidate['wire_md5'],32)
        total+=integer(candidate['size'],1,128*1024**2)
        if name in names or wire in wires:raise ValueError('duplicate physical candidate')
        names.add(name);wires.add(wire)
    if not candidates or total>256*1024**2:raise ValueError('candidate budget')
    def asset(value):
        fields(value,{'full_path','bundle_name'})
        relative(value['full_path'])
        if value['bundle_name'] not in names:raise ValueError('asset outside candidate graph')
        return value['full_path'],value['bundle_name']
    matched=sequence(selected['matched_assets'])
    if len(matched)!=1:raise ValueError('unique selected asset required')
    asset(matched[0])
    if matched[0]['full_path'].casefold()!=prefab and matched[0]['full_path'].rsplit('/',1)[-1].casefold()!=prefab:
        raise ValueError('selected prefab path mismatch')
    nodes={asset(a) for a in sequence(selected['asset_dependencies'])}
    if len(nodes)!=len(selected['asset_dependencies']):raise ValueError('duplicate asset graph node')
    for edge in sequence(selected['bundle_edges']):
        if type(edge) is not list or len(edge)!=2 or any(n not in names for n in edge):raise ValueError('bundle graph edge')
    for edge in sequence(selected['asset_edges']):
        fields(edge,{'source','target'})
        if asset(edge['source']) not in nodes or asset(edge['target']) not in nodes:raise ValueError('asset graph edge')
    if sequence(selected['diagnostics']):raise ValueError('selected root diagnostics')
    expected_config=digest(dict(algorithm=CONFIG_ALGORITHM,**root))
    expected_dependency=digest({'algorithm': DEPENDENCY_ALGORITHM,'configured_prefab': prefab,
        'matched_assets': matched,'candidate_bundles': candidates,'bundle_edges': selected['bundle_edges'],
        'asset_dependencies': selected['asset_dependencies'],'asset_edges': selected['asset_edges'],'failures': []})
    if (selected['config_fingerprint'],selected['dependency_fingerprint'])!=(expected_config,expected_dependency):
        raise ValueError('selected fingerprints inconsistent')
    if render_key(source,selected)!=selected['render_key']:raise ValueError('selected render key inconsistent')


def bound_ref(value):
    fields(value,{'relative_path','size','sha256'})
    relative(value['relative_path']);integer(value['size'],1,64*1024**2);hexdigest(value['sha256'])


def validate_stage_result(value, request, kind):
    from .source_worker import IDENTITY
    common={'schema','stage','status',*IDENTITY,'capture_sha256','snapshot_digest','tool_fingerprint','lazy_input','counts'}
    fields(value,common|({'catalog','scene','ledger'} if kind=='arts' else {'manifest'}))
    if (value['schema'],value['stage'],value['status'])!=('portrait-package-stage-result/v1',kind,'ok'):
        raise ValueError('stage result header')
    for key in IDENTITY:
        if value[key]!=request['source'][key]:raise ValueError('stage source binding')
    for key in ('capture_sha256','snapshot_digest','tool_fingerprint'):
        if hexdigest(value[key])!=request[key]:raise ValueError('stage content binding')
    if value['lazy_input']!=request['selection']['lazy_input']:raise ValueError('stage selection binding')
    keys={'objects','entries','inputs','parts','output_bytes','expanded_bytes','multiple_memberships','builtin_edges'} if kind=='arts' else {'parts','resources','metadata_pairs'}
    fields(value['counts'],keys)
    caps={'objects':10000,'entries':10000,'inputs':128,'parts':128,'output_bytes':64*1024**2,
          'expanded_bytes':512*1024**2,'multiple_memberships':10000,'builtin_edges':200000,'resources':128,'metadata_pairs':128}
    for key,count in value['counts'].items():integer(count,0 if key in ('multiple_memberships','builtin_edges','metadata_pairs') else 1,caps[key])
    for key in ('catalog','scene','ledger') if kind=='arts' else ('manifest',):bound_ref(value[key])
    if kind=='arts' and any(value[k]['relative_path']!=k+'.json' for k in ('catalog','scene','ledger')):
        raise ValueError('fixed stage outputs required')


def boolean(value):
    if type(value) is not bool:raise ValueError('protected boolean')


def strings(value):
    for item in sequence(value):text(item,empty=True)


def nums(value):
    for item in sequence(value,16):number(item)


def nullable(check):
    def validate(value):
        if value is not None:check(value)
    return validate


def array(check, cap=10000):
    def validate(value):
        for item in sequence(value,cap):check(item)
    return validate


def shape(schema):
    def validate(value):
        fields(value,schema)
        for key,check in schema.items():check(value[key])
    return validate


def ref(value):
    fields(value,{'bundle_hash','path_id'})
    hexdigest(value['bundle_hash'],32);integer(value['path_id'],-2**63)
    if not value['path_id']:raise ValueError('null exact identity')
    return value['bundle_hash'],value['path_id']


_string=lambda v:text(v,empty=True)
_int=lambda v:integer(v,-2**63)
_ref=nullable(ref)
_step=shape({'animation': _string,'loop': boolean,'evidence': _string})
_renderer=shape({'kind': _string,'enabled': boolean,'sorting_layer': _string,'sorting_order': _int,'transform': nums,
    'material': _ref,'materials': array(ref),'mesh': _ref,'color': nums,'flip_x': boolean,'flip_y': boolean,'mask_interaction': _int,'blend_mode': _string})
_spine=shape({'skeleton': ref,'atlas': _ref,'texture': _ref,'material': _ref,'default_animation': _string,'loop': boolean,
    'time_scale': number,'initial_skin': _string,'animations': strings,'skins': strings,'skeleton_json': _ref,'atlases': array(ref),
    'skeleton_scale': number,'animation_sequence': array(_step)})
_sprite=shape({'sprite': ref,'rect': nums,'pivot': nums,'texture': _ref,'pixels_per_unit': number})
_particle=shape({'system': ref,'material': _ref,'texture': _ref,'profile': _string,'config_json': lambda v:text(v,cap=1024**2)})
_transform=shape({'object_ref': ref,'local_position': nums,'local_rotation': nums,'local_scale': nums,'sibling_index': _int})
_part=shape({'part_id': text,'object_ref': ref,'game_object_name': _string,'hierarchy_path': _string,
    'renderer': nullable(_renderer),'spine': nullable(_spine),'sprite': nullable(_sprite),'particle': nullable(_particle),
    'transform_chain': array(_transform),'sorting_layer_value': _int,'renderer_depth': number,'stable_order': integer})
_scene=shape({'schema_version': integer,'game_version': _string,'version_timestamp': identifier,'entry_config_id': identifier,
    'character_id': identifier,'skin_id': identifier,'prefab': _ref,'prefab_container': _string,'display_position': position,
    'config_metadata': scene_pairs,'source_bundles': array(lambda v:hexdigest(v,32)),'parts': array(_part,128),
    'source_bundle_details': array(shape({'bundle_hash': lambda v:hexdigest(v,32),'path': _string,'dependencies': strings})),
    'asset_dependencies': array(shape({'source': ref,'field_path': text,'target': ref})),'available_assets': array(ref),
    'interactions': array(shape({'gesture': _string,'finger_count': integer,'target': _ref,'animation': _string,'callback': _string,
        'fallback': _string,'collider': _ref,'track': integer,'sequence': array(_step),'gate': _string,'evidence': strings})),
    'diagnostics': array(shape({'code': _string,'message': _string,'severity': _string,'part_id': _string,'source': _ref,'details': pairs}))})


def validate_scene(scene, selection=None):
    _json_copy(scene,max_bytes=16*1024**2)
    _scene(scene)
    if scene['schema_version']!=8:raise ValueError('protected scene schema')
    if len({p['part_id'] for p in scene['parts']})!=len(scene['parts']):raise ValueError('duplicate scene part')
    if selection is not None:
        for key in ('character_id','skin_id'):
            if scene[key]!=selection[key]:raise ValueError('scene selection mismatch')
        if scene['version_timestamp']!=selection['lazy_input']['version']:raise ValueError('scene version mismatch')
        if scene['display_position']!=selection['root']['position']:raise ValueError('scene position mismatch')
        if scene['entry_config_id']!=(selection['root']['entry_id'] or selection['skin_id']):raise ValueError('scene config entry mismatch')
    return [p for p in scene['parts'] if (p['renderer'] is None or p['renderer']['enabled'])
            and any(p[k] is not None for k in ('spine','sprite','particle'))]


def _required_field(path):
    field=path.casefold()
    if 'm_texenvs.' in field:
        prop=re.sub('[^a-z0-9]','',field.split('m_texenvs.',1)[1].split('.',1)[0])
        return field.endswith('.m_texture') and not any(x in prop for x in ('bump','normalmap','emission','noise','flow','mask','distort','displacement'))
    if 'alphatexture' in field or 'secondarytextures' in field:return False
    return any(x in field for x in ('skeletondataasset','skeletonjson','atlasassets','atlasfile','m_materials','m_sprite','m_rd.texture','m_renderdata.texture')) or field in ('texture','maintexture')


def required_inputs(scene, assets, parts=None):
    """Protected JSON equivalent of accepted required_scene_assets, no environment reopen."""
    pending=[];explicit=set()
    for p in scene['parts'] if parts is None else parts:
        if p['renderer'] is not None and not p['renderer']['enabled']:continue
        spine=p['spine'];sprite=p['sprite'];particle=p['particle'];renderer=p['renderer']
        if spine:
            explicit.update(ref(v) for v in [spine['skeleton_json'],spine['atlas'],spine['texture'],*spine['atlases']] if v is not None)
            pending.extend(ref(v) for v in [spine['skeleton'],spine['skeleton_json'],spine['atlas'],spine['texture'],spine['material'],*spine['atlases']] if v is not None)
            if renderer:pending.extend(ref(v) for v in [renderer['material'],*renderer['materials']] if v is not None)
        if sprite:
            pending.extend(ref(v) for v in (sprite['sprite'],sprite['texture']) if v is not None)
            if sprite['texture']:explicit.add(ref(sprite['texture']))
        if particle and particle['profile']!='unsupported' and particle['texture']:
            explicit.add(ref(particle['texture']));pending.append(ref(particle['texture']))
    outgoing={}
    for e in scene['asset_dependencies']:
        if _required_field(e['field_path']):outgoing.setdefault(ref(e['source']),[]).append(ref(e['target']))
    seen=set();required=set()
    while pending:
        key=pending.pop()
        if key in seen:continue
        seen.add(key);asset=assets.get(key)
        if asset is None and key in explicit:raise ValueError('missing explicit scene input')
        if asset and asset['asset_type'] in ('TextAsset','Texture2D'):required.add(key)
        pending.extend(outgoing.get(key,()))
    return required


def _external_cab(value):
    value=text(value).strip().replace('\\','/')
    match=re.fullmatch(r'(?i)(?:archive:/)?(?:CAB-)?([0-9a-f]{32})(?:/CAB-([0-9a-f]{32}))?/?',value)
    if match is None or match[2] and match[1].lower()!=match[2].lower():raise ValueError('exact external CAB identity')
    return match[1].lower()


def _pointer_resolver(catalog, assets):
    owners={row['bundle_hash']:row for row in catalog['serialized_files']};cabs={}
    for row in owners.values():cabs.setdefault(row['serialized_file'][4:],[]).append(row['bundle_hash'])
    def resolve(owner,pointer):
        wire=owner;pid=pointer['path_id'];fid=pointer['file_id']
        if fid:
            external=owners[owner]['externals'][fid-1]
            if external=='Resources/unity_builtin_extra':return None
            candidates=cabs.get(_external_cab(external),[])
            if len(candidates)>1:raise ValueError('ambiguous external CAB owner')
            if not candidates:return None
            wire=candidates[0]
        if pid and (wire,pid) not in assets:raise ValueError('exact pointer target absent')
        return (wire,pid) if pid else None
    return resolve


def validate_census(catalog, request):
    fields(catalog,{'schema','version_timestamp','assets','serialized_files','container_entries'})
    version=int(request['source']['version'])
    if catalog['schema']!='portrait-exact-catalog/v1' or integer(catalog['version_timestamp'],1)!=version:raise ValueError('census version')
    descriptors={d['wire']['wire_md5']:d for d in request['descriptors']}
    owners={};assets={};ordinals=set()
    for row in sequence(catalog['serialized_files'],32):
        fields(row,{'bundle_hash','serialized_file','externals','preload'})
        wire=hexdigest(row['bundle_hash'],32);cab=text(row['serialized_file'])
        if wire not in descriptors or wire in owners or re.fullmatch('CAB-[0-9a-f]{32}',cab) is None:raise ValueError('census owner')
        for external in sequence(row['externals'],4096):
            if external!='Resources/unity_builtin_extra':_external_cab(external)
        for pointer in sequence(row['preload'],100000):
            fields(pointer,{'file_id','path_id'});integer(pointer['file_id'],0,len(row['externals']));integer(pointer['path_id'],-2**63)
        owners[wire]=row
    if set(owners)!=set(descriptors):raise ValueError('census candidate set')
    for row in sequence(catalog['assets'],10000):
        fields(row,{'bundle_hash','serialized_file','path_id','name','asset_type','version_timestamp','source'})
        key=ref({k:row[k] for k in ('bundle_hash','path_id')})
        if key in assets or key[0] not in owners or row['serialized_file']!=owners[key[0]]['serialized_file'] or integer(row['version_timestamp'],1)!=version:
            raise ValueError('census physical identity')
        text(row['name'],empty=True);text(row['asset_type'])
        expected='normalized/'+descriptors[key[0]]['sha256']+'/'+row['serialized_file']+'#'+str(key[1])
        if row['source']!=expected:raise ValueError('census normalized source binding')
        assets[key]=row
    resolve=_pointer_resolver(catalog,assets)
    if sum(len(row['preload']) for row in owners.values())>100000:raise ValueError('aggregate preload limit')
    for wire,row in owners.items():
        for pointer in row['preload']:resolve(wire,pointer)
    edges=0
    for row in sequence(catalog['container_entries'],10000):
        fields(row,{'path','owner_bundle_hash','owner_serialized_file','asset_bundle_path_id','ordinal','asset','preload_index','preload_size'})
        owner=owners.get(row['owner_bundle_hash']);text(row['path'])
        if owner is None or row['owner_serialized_file']!=owner['serialized_file']:raise ValueError('container owner')
        ab=assets.get((row['owner_bundle_hash'],integer(row['asset_bundle_path_id'],-2**63)))
        if ab is None or ab['asset_type']!='AssetBundle':raise ValueError('container AssetBundle')
        key=(row['owner_bundle_hash'],row['asset_bundle_path_id'],integer(row['ordinal']))
        if key in ordinals:raise ValueError('duplicate container ordinal')
        ordinals.add(key)
        fields(row['asset'],{'file_id','path_id'});integer(row['asset']['file_id'],0,len(owner['externals']));integer(row['asset']['path_id'],-2**63)
        resolve(row['owner_bundle_hash'],row['asset'])
        if integer(row['preload_index'])+integer(row['preload_size'])>len(owner['preload']):raise ValueError('container preload range')
        edges+=1+row['preload_size']
        if edges>200000:raise ValueError('aggregate container edge limit')
    return assets


def identity_path(row):
    wire=hexdigest(row['wire_md5'],32);pid=integer(row['path_id'],-2**63)
    if not pid:raise ValueError('zero extracted PathID')
    name=relative(row['name'])
    if len(PurePosixPath(name).parts)!=1:raise ValueError('extracted leaf name')
    if row['asset_type']=='Texture2D' and PurePosixPath(name).suffix.casefold()!='.png':name+='.png'
    return f'_portrait_inputs/{wire}/{pid}/{name}'


def validate_extraction(catalog, scene, ledger, request, material_root):
    assets=validate_census(catalog,request);validate_scene(scene,request['selection'])
    selected=request['selection'];wanted=selected['matched_assets'][0]
    owner=next(row['wire_md5'] for row in selected['candidate_bundles'] if row['name']==wanted['bundle_name'])
    normalized=lambda path:path.replace('\\','/').strip('/').casefold()
    configured=selected['root']['prefab_name']
    entries=[row for row in catalog['container_entries'] if
        (normalized(row['path']) if '/' in configured else normalized(row['path']).rsplit('/',1)[-1])==configured]
    groups={(row['owner_bundle_hash'],row['owner_serialized_file'],normalized(row['path'])) for row in entries}
    if len(groups)!=1 or next(iter(groups))[::2]!=(owner,normalized(wanted['full_path'])) or scene['prefab_container']!=normalized(wanted['full_path']):
        raise ValueError('selected exact container owner/path')
    resolve=_pointer_resolver(catalog,assets);roots=set()
    for row in entries:
        target=resolve(row['owner_bundle_hash'],row['asset'])
        if target is None and row['asset']['path_id']:raise ValueError('selected direct pointer unresolved')
        if target and assets[target]['asset_type']=='GameObject':roots.add(target)
    if scene['prefab'] is None or roots!={ref(scene['prefab'])} or ref(scene['prefab'])[0]!=owner:
        raise ValueError('selected unique direct GameObject root')
    descriptors={d['wire']['wire_md5']:d for d in request['descriptors']};entries={};total=0
    for row in sequence(ledger,128):
        fields(row,{'wire_md5','source_sha256','cab','path_id','asset_type','name','size','sha256','output'})
        key=(hexdigest(row['wire_md5'],32),integer(row['path_id'],-2**63));asset=assets.get(key)
        if key in entries or asset is None:raise ValueError('extraction exact identity')
        if (row['cab'],row['asset_type'],row['name'])!=(asset['serialized_file'],asset['asset_type'],asset['name']):raise ValueError('extraction census mismatch')
        if row['asset_type'] not in ('TextAsset','Texture2D') or hexdigest(row['source_sha256'])!=descriptors[key[0]]['sha256']:raise ValueError('extraction normalized source')
        bound_ref(row['output'])
        if row['output']!={'relative_path':identity_path(row),'size':integer(row['size'],1,16*1024**2),'sha256':hexdigest(row['sha256'])}:raise ValueError('extraction output binding')
        read_bound_file(material_root,row['output'],cap=16*1024**2)
        entries[key]=row;total+=row['size']
    if total>64*1024**2 or set(entries)!=required_inputs(scene,assets):raise ValueError('exact selected extraction closure')
    actual={p.relative_to(material_root).as_posix() for p in Path(material_root).rglob('*') if p.is_file() or p.is_symlink()}
    if actual!={e['output']['relative_path'] for e in entries.values()}:raise ValueError('material filesystem closure')
    return assets


def _safe_name(value):
    # Exact publisher filename projection, independent from Unity physical names.
    original=text(value)
    cleaned=re.sub(r'[<>:"/\\|?*\x00-\x1f]','_',original).strip(' .')
    if not cleaned:raise ValueError('empty portable name')
    if cleaned!=original:cleaned+='_'+hashlib.sha256(original.encode()).hexdigest()[:8]
    if cleaned.split('.')[0].upper() in {'CON','PRN','AUX','NUL',*(f'COM{i}' for i in range(1,10)),*(f'LPT{i}' for i in range(1,10))}:cleaned='_'+cleaned
    return cleaned[:140]


def validate_metadata(value):
    fields(value,{'query_ok','internal_skins','animations'},
           {'skin_query_stdout','skin_query_stderr','animation_query_stdout','animation_query_stderr','diagnostic'})
    if value['query_ok'] is not True:raise ValueError('actual successful metadata required')
    skins=sequence(value['internal_skins'],1024)
    if not skins or len({text(s) for s in skins})!=len(skins):raise ValueError('metadata skins')
    animations=value['animations']
    if type(animations) is not dict or not 0<len(animations)<=4096:raise ValueError('metadata animations')
    for name,duration in animations.items():
        text(name)
        if number(duration)<0:raise ValueError('metadata duration')
    for key in set(value)-{'query_ok','internal_skins','animations'}:text(value[key],empty=True,cap=65536)


def validate_portable_package(manifest, package_root, *, scene, ledger, material_root, assets=None):
    """Bind portable semantics to authenticated Stage A leaves, including rewritten atlases."""
    fields(manifest,{'package_schema_version','game_skin_id','scene','parts','inputs','resources'})
    if type(manifest['package_schema_version']) is not int or manifest['package_schema_version']!=2:
        raise ValueError('protected portable schema')
    parts=validate_scene(scene);validate_scene(manifest['scene'])
    if manifest['scene']!=scene or manifest['game_skin_id']!=scene['skin_id']:raise ValueError('full portable scene mismatch')
    leaves={(r['wire_md5'],r['path_id']):r for r in ledger}
    if len(leaves)!=len(ledger) or not leaves:raise ValueError('portable input ledger')
    if assets is None:assets={k:{'asset_type':r['asset_type']} for k,r in leaves.items()}
    if set(leaves)!=required_inputs(scene,assets):raise ValueError('portable selected input set')
    expected={};input_paths={k:[] for k in leaves}
    def leaf(source):
        key=ref(source)
        if key not in leaves:raise ValueError('portable unselected leaf')
        return key,leaves[key]
    def copy_record(path,source,rewritten=None):
        relative(path);key,row=leaf(source)
        if path in expected:raise ValueError('portable resource path collision')
        sha=row['sha256'] if rewritten is None else hashlib.sha256(rewritten).hexdigest()
        size=row['size'] if rewritten is None else len(rewritten)
        trace={'source': {'bundle_hash':key[0],'path_id':key[1]},'source_sha256': row['sha256'],
            'source_name': row['name'],'source_container': '','asset_type': row['asset_type']}
        expected[path]=dict(path=path,size=size,sha256=sha,**trace)
        input_paths[key].append(path)
        return path
    def filename(source):
        _,row=leaf(source)
        return _safe_name(PurePosixPath(identity_path(row)).name)
    package_parts=sequence(manifest['parts'],128)
    if len(package_parts)!=len(parts):raise ValueError('portable full part set')
    for part,saved in zip(parts,package_parts):
        common={'part_id','directory','unity_order','kind'}
        directory=f"part_{part['stable_order']:04d}_{hashlib.sha256(part['part_id'].encode()).hexdigest()[:12]}"
        renderer=part['renderer'];order=[part['sorting_layer_value'],renderer['sorting_order'] if renderer else 0,part['renderer_depth'],part['stable_order']]
        if type(saved) is not dict or saved.get('part_id')!=part['part_id'] or saved.get('directory')!=directory or saved.get('unity_order')!=order:
            raise ValueError('portable part identity/order')
        if len(sequence(saved['unity_order'],4))!=4:raise ValueError('portable order shape')
        for index in (0,1,3):_int(saved['unity_order'][index])
        number(saved['unity_order'][2])
        if part['spine']:
            spine=part['spine']
            fields(saved,common|{'skeleton_path','atlas_paths','texture_paths','internal_skins','animations','expression_selectors',
                'default_animation','loop','time_scale','initial_internal_skin','animation_sequence','metadata'})
            if saved['kind']!='spine':raise ValueError('portable part kind')
            skeleton=copy_record(directory+'/'+filename(spine['skeleton_json']),spine['skeleton_json'])
            atlas_paths=[];pages=[]
            textures=[k for k in required_inputs(scene,assets,[part]) if assets[k]['asset_type']=='Texture2D']
            for source in spine['atlases'] or ([spine['atlas']] if spine['atlas'] else []):
                _,row=leaf(source);raw=read_bound_file(material_root,row['output'],cap=16*1024**2)
                replacements={};local_pages=[]
                for line in raw.decode('utf-8').splitlines():
                    name=line.strip()
                    if not name or ':' in name or PurePosixPath(name).suffix.casefold() not in {'.bmp','.gif','.jpeg','.jpg','.png','.tga','.webp'}:continue
                    relative(name)
                    matches=[k for k in textures if PurePosixPath(identity_path(leaves[k])).name.casefold()==PurePosixPath(name).name.casefold()]
                    if len(matches)!=1:raise ValueError('atlas exact selected page identity')
                    k=matches[0];target=directory+'/'+_safe_name(PurePosixPath(name).name)
                    if target not in local_pages:
                        copy_record(target,{'bundle_hash':k[0],'path_id':k[1]});local_pages.append(target)
                    replacements[name]=_safe_name(PurePosixPath(name).name)
                if not replacements:raise ValueError('portable atlas pages missing')
                rewritten=('\n'.join(replacements.get(line.strip(),line) for line in raw.decode('utf-8-sig').splitlines())+'\n').encode('utf-8')
                atlas_paths.append(copy_record(directory+'/'+filename(source),source,rewritten));pages.extend(local_pages)
            if (saved['skeleton_path'],saved['atlas_paths'],saved['texture_paths'])!=(skeleton,atlas_paths,pages):raise ValueError('portable exact part paths')
            boolean(saved['loop']);number(saved['time_scale']);array(_step)(saved['animation_sequence'])
            validate_metadata(saved['metadata'])
            if saved['internal_skins']!=saved['metadata']['internal_skins'] or saved['animations']!=saved['metadata']['animations']:
                raise ValueError('portable metadata projection')
            expected_fields={'expression_selectors': [n for n in saved['internal_skins'] if n.startswith('motion_')],
                'default_animation': spine['default_animation'],'loop': spine['loop'],'time_scale': spine['time_scale'],
                'initial_internal_skin': spine['initial_skin'],'animation_sequence': spine['animation_sequence']}
            if any(saved[k]!=v for k,v in expected_fields.items()):raise ValueError('portable spine semantics')
        elif part['particle']:
            particle=part['particle'];with_texture=particle['texture'] is not None
            fields(saved,common|{'profile','particle','system','material'}|({'original_texture_path','texture_paths'} if with_texture else set()))
            if (saved['kind'],saved['profile'],saved['system'],saved['material'])!=('particle',particle['profile'],particle['system'],particle['material']):raise ValueError('portable particle semantics')
            ref(saved['system']);_ref(saved['material'])
            payload=json.loads(particle['config_json'],parse_constant=lambda _:(_ for _ in ()).throw(ValueError('nonfinite particle config')))
            if saved['particle']!=payload:raise ValueError('portable particle config')
            if with_texture:
                path=copy_record(directory+'/'+filename(particle['texture']),particle['texture'])
                if saved['original_texture_path']!=path or saved['texture_paths']!=[path]:raise ValueError('portable particle path')
        else:
            sprite=part['sprite']
            fields(saved,common|{'original_texture_path','internal_skins','expression_selectors','animations','generation',
                'skeleton_path','atlas_paths','texture_paths'})
            if saved['kind']!='static_sprite':raise ValueError('portable sprite kind')
            path=copy_record(directory+'/source/'+filename(sprite['texture']),sprite['texture'])
            if saved['original_texture_path']!=path:raise ValueError('portable sprite original input')
            generation={'alpha_mode': 'straight','pma': False,'coordinate_space': 'part_local','skeleton_scale': 0.01,
                'rect': sprite['rect'],'pivot': sprite['pivot'],'pixels_per_unit': sprite['pixels_per_unit']}
            if saved['generation']!=generation or saved['internal_skins']!=['default'] or saved['expression_selectors']!=[] or saved['animations']!={'idle':0.0}:
                raise ValueError('portable sprite generation binding')
            stem=directory+'/static_'+hashlib.sha256(part['part_id'].encode()).hexdigest()[:16]
            if (saved['skeleton_path'],saved['atlas_paths'],saved['texture_paths'])!=(stem+'.json',[stem+'.atlas'],[stem+'.png']):raise ValueError('portable generated paths')
            for suffix in ('.json','.atlas','.png'):expected[stem+suffix]=None
    inputs=manifest['inputs']
    if type(inputs) is not dict or set(inputs)!={f'{w}:{p}' for w,p in leaves}:raise ValueError('portable input closure')
    for key,row in leaves.items():
        paths=input_paths[key]
        if not paths:raise ValueError('portable unused selected input')
        trace={'source': {'bundle_hash':key[0],'path_id':key[1]},'source_sha256': row['sha256'],'source_name': row['name'],
                   'source_container': '','asset_type': row['asset_type'],'path': paths[0],'paths': paths}
        actual_input=inputs[f'{key[0]}:{key[1]}']
        if actual_input!=trace:raise ValueError('portable source input binding')
        ref(actual_input['source'])
    seen=set();total=0
    for resource in sequence(manifest['resources'],128):
        if type(resource) is not dict:raise ValueError('portable resource record')
        path=relative(resource.get('path'))
        if path in seen or path not in expected:raise ValueError('portable declared resource closure')
        seen.add(path)
        if expected[path] is None:fields(resource,{'path','size','sha256'})
        else:
            if resource!=expected[path]:raise ValueError('portable resource provenance/content binding')
            ref(resource['source'])
        size=integer(resource['size'],1,16*1024**2);total+=size
        read_bound_file(package_root,{'relative_path': path,'size': size,'sha256': hexdigest(resource['sha256'])},cap=16*1024**2)
    if total>64*1024**2 or seen!=set(expected):raise ValueError('portable resource set/budget')
    actual={p.relative_to(package_root).as_posix() for p in Path(package_root).rglob('*') if p.is_file() or p.is_symlink()}
    if actual!={'skin.json',*seen}:raise ValueError('portable filesystem closure')


def validate_package_counts(counts, manifest):
    resources={r['path']:r for r in manifest['resources']}
    pairs={(resources[p['skeleton_path']]['sha256'],resources[p['atlas_paths'][0]]['sha256'])
           for p in manifest['parts'] if p['kind']=='spine'}
    expected={'parts': len(manifest['parts']),'resources': len(resources),'metadata_pairs': len(pairs)}
    if counts!=expected or any(type(v) is not int for v in counts.values()):raise ValueError('package count binding')


def validate_arts_counts(counts, catalog, scene, ledger):
    owners={r['bundle_hash']:r for r in catalog['serialized_files']}
    cabs={r['serialized_file'][4:]:r['bundle_hash'] for r in catalog['serialized_files']}
    memberships={};builtin=0
    for entry in catalog['container_entries']:
        owner=owners[entry['owner_bundle_hash']]
        pointers=[entry['asset'],*owner['preload'][entry['preload_index']:entry['preload_index']+entry['preload_size']]]
        for p in pointers:
            wire=entry['owner_bundle_hash'];pid=p['path_id']
            if p['file_id']:
                external=owner['externals'][p['file_id']-1]
                if external=='Resources/unity_builtin_extra':
                    builtin+=bool(pid);continue
                matches=re.findall('(?i)CAB-([0-9a-f]{32})',external)
                wire=cabs.get(matches[0].lower() if matches else external.lower())
            if wire and pid:memberships.setdefault((wire,pid),set()).add(entry['path'].replace('\\','/').strip('/').casefold())
    expected={'objects': len(catalog['assets']),'entries': len(catalog['container_entries']),'inputs': len(ledger),
        'parts': len(scene['parts']),'output_bytes': sum(r['size'] for r in ledger),'builtin_edges': builtin,
        'multiple_memberships': sum(len(v)>1 for v in memberships.values())}
    if any(type(counts[k]) is not int or counts[k]!=v for k,v in expected.items()):raise ValueError('Arts count binding')
