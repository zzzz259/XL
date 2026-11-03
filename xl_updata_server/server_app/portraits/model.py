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


def validate_job(payload):
    """Copy a fixed-profile job; lease acceptance remains the store's concern."""
    if not isinstance(payload, dict): raise ValueError('job must be an object')
    job = copy.deepcopy(payload)
    if type(job.get('schema_version')) is not int or job['schema_version'] not in (1, 2):
        raise ValueError('invalid job schema')
    if not all(_text(job.get(k)) for k in ('instance_id', 'source_id', 'version')):
        raise ValueError('invalid job scope/version')
    if not all(_id(job.get(k)) for k in ('character_id', 'skin_id')):
        raise ValueError('invalid job identity')
    if job.get('profile') != PROFILE or type(job['profile'].get('scale')) is not int:
        raise ValueError('unsupported job profile')
    if not all(_hash(job.get(k)) for k in ('job_id', 'render_key')):
        raise ValueError('invalid job digest')
    if job['job_id'] != digest([job[k] for k in ('instance_id','source_id','version','character_id','skin_id','render_key')]):
        raise ValueError('job id mismatch')
    if job.get('reason') not in ('new_character','new_skin','changed_inputs','manual_backfill'):
        raise ValueError('invalid job reason')
    if 'attempt' in job and (type(job['attempt']) is not int or not 1 <= job['attempt'] < 2**63):
        raise ValueError('invalid attempt')
    if job['schema_version'] == 2:
        validate_capture(job)
    return job

# Artifact fields deliberately do not include desired tool/render metadata.
ARTIFACT_FIELDS = ('artifact_path','artifact_sha256','source_version','archive_path',
    'archive_sha256','archive_profile','delivery_profile','width','height','archive_width',
    'archive_height','artifact_lineage','artifact_render_key','artifact_tool_fingerprint',
    'job_id','attempt','input_snapshot_digest','source_capture_ref','artifact_event','plan_id')
KNOWN_FIELDS = ('artifact_render_key','artifact_tool_fingerprint','job_id','attempt',
                'input_snapshot_digest','source_capture_ref')



def desired_generation(catalog, execution_plan_id=None):
    """Identity of desired metadata/execution, independent of mutable outcomes."""
    metadata=copy.deepcopy(catalog)
    for key in ('desired_generation','desired_execution_plan_id'):
        metadata.pop(key,None)
    metadata['publication_protocol']=2
    for row in metadata['skins']:
        for key in (*ARTIFACT_FIELDS,'state','diagnostics'): row.pop(key,None)
    return digest([metadata,execution_plan_id])


def validate_capture(job):
    from .store import validate_source_reference
    if not all(_hash(job.get(k)) for k in ('input_snapshot_digest','tool_fingerprint')):
        raise ValueError('invalid pinned execution hashes')
    if not isinstance(job.get('source_capture_ref'),dict):
        raise ValueError('missing prepared source')
    validate_source_reference(job)


def validate_artifact(catalog, row):
    base=('artifact_path','artifact_sha256','source_version')
    if not row.get('artifact_path'):
        if any(row.get(k) is not None for k in ARTIFACT_FIELDS):
            raise ValueError('provenance without artifact')
        return
    if not all(row.get(k) for k in base): raise ValueError('partial artifact')
    known=any(row.get(k) is not None for k in KNOWN_FIELDS)
    if not known:
        if row.get('artifact_lineage') not in (None,'legacy_unknown'):
            raise ValueError('invalid legacy lineage')
        row['artifact_lineage']='legacy_unknown'
        return
    if row.get('artifact_lineage') != 'known' or not all(row.get(k) is not None for k in KNOWN_FIELDS):
        raise ValueError('partial known lineage')
    job=dict(schema_version=2,instance_id=catalog['instance_id'],source_id=catalog['source_id'],
        version=row['source_version'],character_id=row['character_id'],skin_id=row['skin_id'],
        render_key=row['artifact_render_key'],tool_fingerprint=row['artifact_tool_fingerprint'],
        profile=PROFILE,reason='manual_backfill',**{k:row[k] for k in ('job_id','attempt','input_snapshot_digest','source_capture_ref')})
    validate_job(job)
    from .store import validate_result
    validate_result(job,row['attempt'],dict(row,**job,status='success',diagnostics=[]))
    if row.get('plan_id') is not None and not _hash(row['plan_id']):
        raise ValueError('invalid accepted plan identity')
    event=row.get('artifact_event')
    if event is not None and (type(event) is not dict or set(event)!={'database_id','id'} or
            not _hash(event['database_id']) or type(event['id']) is not int or event['id']<1):
        raise ValueError('invalid committed event reference')


def render_key(catalog, row):
    return digest([catalog['instance_id'], catalog['source_id'], row['character_id'], row['skin_id'], PROFILE, *[row[k] for k in FINGERPRINTS]])

def validate_catalog(payload: dict) -> dict:
    """Return an independent validated snapshot, or raise ValueError."""
    if not isinstance(payload, dict): raise ValueError('catalog must be an object')
    result=copy.deepcopy(payload)
    if type(result.get('schema_version')) is not int or result['schema_version'] != 1: raise ValueError('unsupported schema')
    if 'publication_protocol' in result and (type(result['publication_protocol']) is not int or result['publication_protocol']!=2):
        raise ValueError('unsupported publication protocol')
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
        validate_artifact(result,row)
        ordinals.setdefault(row['character_id'],[]).append(row)
    for rows in ordinals.values():
        if [r['ordinal'] for r in sorted(rows,key=lambda r:int(r['skin_id']))] != list(range(1,len(rows)+1)): raise ValueError('invalid numeric skin ordering')
    result['skins'].sort(key=lambda r:(int(r['character_id']),int(r['skin_id'])))
    if 'desired_generation' in result or 'desired_execution_plan_id' in result:
        if result.get('publication_protocol')!=2 or not _hash(result.get('desired_generation')) or 'desired_execution_plan_id' not in result:
            raise ValueError('invalid desired generation identity')
        plan=result['desired_execution_plan_id']
        if plan is not None and not _hash(plan): raise ValueError('invalid desired execution plan')
        if result['desired_generation']!=desired_generation(result,plan):
            raise ValueError('desired generation metadata mismatch')
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
