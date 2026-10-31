"""Validated portrait metadata; fingerprints are supplied by the input preparer."""
import copy
import hashlib
import json
import re

PROFILE = {'format': 'png', 'scale': 2, 'scope': 'full'}
FINGERPRINTS = ('config_fingerprint', 'dependency_fingerprint', 'tool_fingerprint')

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()

def _text(value):
    return isinstance(value, str) and bool(value.strip())

def _id(value):
    return isinstance(value, str) and re.fullmatch(r'[1-9][0-9]*', value) is not None

def _hash(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None

def render_key(catalog, row):
    return digest([catalog['instance_id'], catalog['source_id'], row['character_id'], row['skin_id'], PROFILE, *[row[k] for k in FINGERPRINTS]])

def validate_catalog(payload: dict) -> dict:
    """Return an independent validated snapshot, or raise ValueError."""
    if not isinstance(payload, dict): raise ValueError('catalog must be an object')
    result=copy.deepcopy(payload)
    if type(result.get('schema_version')) is not int or result['schema_version'] != 1: raise ValueError('unsupported schema')
    for key in ('instance_id','source_id','version'):
        if not _text(result.get(key)): raise ValueError('invalid '+key)
    if not isinstance(result.get('skins'),list): raise ValueError('skins must be a list')
    seen=set(); ordinals={}
    for row in result['skins']:
        if not isinstance(row,dict): raise ValueError('skin must be an object')
        for key in ('character_id','skin_id'):
            if not _id(row.get(key)): raise ValueError('invalid '+key)
        if type(row.get('ordinal')) is not int or row['ordinal'] <= 0: raise ValueError('invalid ordinal')
        identity=(row['character_id'],row['skin_id'])
        if identity in seen: raise ValueError('duplicate skin identity')
        seen.add(identity)
        for key in ('instance_id','source_id'):
            if key in row and row[key] != result[key]: raise ValueError('skin scope mismatch')
        if row.get('family','cardspine') != 'cardspine': raise ValueError('unsupported family')
        if not isinstance(row.get('name'),str): raise ValueError('invalid name')
        if row.get('state') not in ('not_generated','published','blocked','pending','running','failed'): raise ValueError('invalid state')
        if not isinstance(row.get('diagnostics'),list) or not all(isinstance(x,str) for x in row['diagnostics']): raise ValueError('invalid diagnostics')
        for key in FINGERPRINTS:
            if not _hash(row.get(key)): raise ValueError('invalid '+key)
        if row.get('profile') != PROFILE or type(row['profile'].get('scale')) is not int: raise ValueError('unsupported profile')
        if row.get('render_key') != render_key(result,row): raise ValueError('invalid render_key')
        for key in ('artifact_path','source_version'):
            if key not in row or (row[key] is not None and not _text(row[key])): raise ValueError('invalid '+key)
        if 'artifact_sha256' not in row or (row['artifact_sha256'] is not None and not _hash(row['artifact_sha256'])): raise ValueError('invalid artifact hash')
        if row['state']=='published' and not all(row[k] for k in ('artifact_path','artifact_sha256','source_version')): raise ValueError('published artifact incomplete')
        ordinals.setdefault(row['character_id'],[]).append(row)
    for rows in ordinals.values():
        if [r['ordinal'] for r in sorted(rows,key=lambda r:int(r['skin_id']))] != list(range(1,len(rows)+1)): raise ValueError('invalid numeric skin ordering')
    result['skins'].sort(key=lambda r:(int(r['character_id']),int(r['skin_id'])))
    return result

def build_catalog(instance_id: str, source_id: str, version: str, skins: list[dict]) -> dict:
    catalog=dict(schema_version=1,instance_id=instance_id,source_id=source_id,version=version,skins=[])
    if not isinstance(skins,list): raise ValueError('skins must be a list')
    for item in skins:
        if not isinstance(item,dict): raise ValueError('skin must be an object')
        if item.get('family') != 'cardspine': continue
        row=copy.deepcopy(item)
        for key in ('character_id','skin_id'):
            if not _id(row.get(key)): raise ValueError('invalid '+key)
        catalog['skins'].append(row)
    catalog['skins'].sort(key=lambda r:(int(r['character_id']),int(r['skin_id'])))
    counts={}
    for row in catalog['skins']:
        c=row['character_id']; counts[c]=counts.get(c,0)+1; row['ordinal']=counts[c]
        for key,value in dict(state='not_generated',artifact_path=None,artifact_sha256=None,source_version=None,diagnostics=[]).items(): row.setdefault(key,copy.deepcopy(value))
        row['profile']=copy.deepcopy(PROFILE)
        if not all(_hash(row.get(k)) for k in FINGERPRINTS): raise ValueError('invalid input fingerprint')
        row['render_key']=render_key(catalog,row)
    return validate_catalog(catalog)
