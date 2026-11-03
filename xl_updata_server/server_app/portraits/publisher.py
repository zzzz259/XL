"""Local immutable media preparation and durable-result catalog projection.

publish_png only prepares a candidate. The caller MUST pass it to store.finish
with the claimed attempt, then project only results read from the durable store.
Neither preparation nor projection promotes an active generation. PNG geometry
evidence belongs to the native runner; this boundary verifies its declared size.
Interrupted temporary files are retained; cleanup is outside this library.
"""
from contextlib import contextmanager
import copy
import hashlib
import math
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import stat
import uuid
import warnings


from .model import PROFILE, ARTIFACT_FIELDS, desired_generation, _hash, validate_catalog, validate_job
from .store import _json, MAX_PLAN_BYTES

MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_CANVAS_PIXELS = 64 * 1024 * 1024
_IDENTITY = ('schema_version','instance_id','source_id','version','character_id','skin_id',
             'render_key','job_id','profile','reason','attempt')
_MEDIA = ('artifact_path','artifact_sha256','archive_path','archive_sha256','archive_profile',
          'delivery_profile','width','height','archive_width','archive_height','source_version')


def _positive(value, label):
    if type(value) is not int or value < 1: raise ValueError('invalid '+label)
    return value


def _relative(value):
    if (not isinstance(value, str) or not value or '\\' in value or
            PureWindowsPath(value).drive or PurePosixPath(value).is_absolute() or
            any(p in ('', '.', '..') for p in value.split('/'))):
        raise ValueError('unsafe media path')
    return value


def _path(root, relative):
    target = root / _relative(relative)
    resolved = target.resolve()
    if resolved == root or not resolved.is_relative_to(root):
        raise ValueError('path escapes media root')
    return target


def _hash_file(path, limit=MAX_ARCHIVE_BYTES):
    h = hashlib.sha256(); total = 0
    with path.open('rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode): raise ValueError('not a regular file')
        if os.fstat(stream.fileno()).st_size > limit: raise ValueError('file exceeds byte limit')
        while block := stream.read(1024*1024):
            total += len(block)
            if total > limit: raise ValueError('file exceeds byte limit')
            h.update(block)
    return h.hexdigest()


def _temporary(root, directory):
    path = _path(root, directory+'/.pending-'+uuid.uuid4().hex)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Resolve again after mkdir to catch preexisting links/junctions.
    _path(root, path.relative_to(root).as_posix())
    return path


def _sync_directory(path):
    if os.name != 'nt':
        fd = os.open(path, os.O_RDONLY)
        try: os.fsync(fd)
        finally: os.close(fd)


def _sync_directory_chain(path):
    """Heal directory entries bottom-up through the resolved filesystem anchor.

    Existing ancestors may have been created by an interrupted prior call, so
    existence alone is not durability evidence. Work is bounded by path depth.
    The platform-specific directory barrier remains the existing helper.
    """
    for directory in (path,*path.parents):
        _sync_directory(directory)


def _install(temporary, target, expected):
    """Atomic no-clobber creation; competing identical writers are idempotent."""
    try: os.link(temporary, target)
    except FileExistsError: pass
    if _hash_file(target) != expected: raise ValueError('immutable content mismatch')
    _sync_directory(target.parent)


def _copy_snapshot(source, temporary, expected):
    h = hashlib.sha256(); total = 0
    with source.open('rb') as src, temporary.open('xb') as dst:
        info = os.fstat(src.fileno())
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_ARCHIVE_BYTES:
            raise ValueError('invalid staged file size/type')
        while block := src.read(1024*1024):
            total += len(block)
            if total > MAX_ARCHIVE_BYTES: raise ValueError('archive exceeds byte limit')
            h.update(block); dst.write(block)
        dst.flush(); os.fsync(dst.fileno())
    if h.hexdigest() != expected: raise ValueError('staged PNG digest mismatch')


def _decode(path, expected):
    from PIL import Image
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(path) as image:
                if image.format != 'PNG' or image.size != expected:
                    raise ValueError('PNG format/dimensions mismatch')
                if image.width*image.height > MAX_CANVAS_PIXELS:
                    raise ValueError('PNG exceeds canvas pixel limit')
                if getattr(image, 'is_animated', False): raise ValueError('PNG must be static')
                image.verify()
            with Image.open(path) as image:
                image.load()
                rgba = image.convert('RGBA')
            if rgba.getchannel('A').getbbox() is None:
                rgba.close(); raise ValueError('PNG contains no visible pixels')
            return rgba
    except (OSError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValueError('invalid PNG') from exc


def publish_png(job, result, data_root, *, staging_root,
                max_delivery_bytes=20*1024*1024, max_delivery_pixels=8_388_608):
    """Prepare immutable files and return a candidate, never an acceptance claim."""
    from PIL import Image
    job = validate_job(job)
    if 'attempt' not in job: raise ValueError('explicit claimed attempt required')
    _positive(max_delivery_bytes,'delivery byte budget'); _positive(max_delivery_pixels,'delivery pixel budget')
    if not isinstance(result,dict) or result.get('status') != 'success' or result.get('job_id') != job['job_id']:
        raise ValueError('result status/job mismatch')
    if job['schema_version'] == 2:
        for key in _IDENTITY + ('input_snapshot_digest','tool_fingerprint','source_capture_ref'):
            if _json(result.get(key),MAX_PLAN_BYTES) != _json(job.get(key),MAX_PLAN_BYTES):
                raise ValueError('staged execution identity mismatch: '+key)
    dimensions = tuple(_positive(result.get(k),k) for k in ('width','height'))
    if dimensions[0]*dimensions[1] > MAX_CANVAS_PIXELS: raise ValueError('canvas exceeds pixel limit')
    expected = result.get('sha256')
    if not _hash(expected): raise ValueError('invalid PNG digest')
    stage = Path(staging_root)
    value = result.get('staged_path')
    if not stage.is_absolute() or not stage.is_dir(): raise ValueError('explicit absolute staging root required')
    if not isinstance(value,str) or not Path(value).is_absolute(): raise ValueError('absolute staged path required')
    stage = stage.resolve(); source = Path(value).resolve()
    if source == stage or not source.is_relative_to(stage) or not source.is_file():
        raise ValueError('staged path escapes staging root or is not a file')
    root = Path(data_root).resolve(); root.mkdir(parents=True,exist_ok=True)
    snapshot = _temporary(root,'archive')
    _copy_snapshot(source,snapshot,expected)
    rgba = _decode(snapshot,dimensions)
    try:
        delivery = snapshot; delivery_size = dimensions; profile = 'original'
        if snapshot.stat().st_size > max_delivery_bytes or math.prod(dimensions) > max_delivery_pixels:
            profile = 'fitted_png'
            scale = min(1.0, math.sqrt(max_delivery_pixels / math.prod(dimensions)))
            width, height = (max(1,int(n*scale)) for n in dimensions)
            # One dimension may round up to one for extremely thin portraits.
            if width*height > max_delivery_pixels:
                if width >= height: width = max_delivery_pixels // height
                else: height = max_delivery_pixels // width
            while True:
                delivery = _temporary(root,'artifacts')
                with rgba.resize((width,height),Image.Resampling.LANCZOS) as fitted:
                    with delivery.open('xb') as stream:
                        fitted.save(stream,format='PNG',compress_level=9)
                        stream.flush(); os.fsync(stream.fileno())
                if delivery.stat().st_size <= max_delivery_bytes:
                    delivery_size = (width,height); break
                if width == height == 1: raise ValueError('impossible PNG delivery byte budget')
                width, height = max(1,int(width*.8)), max(1,int(height*.8))
            # Validate the encoded derivative itself: resampling can quantize
            # sparse visible alpha to zero even when the source is valid.
            if math.prod(delivery_size) > max_delivery_pixels:
                raise ValueError('fitted delivery exceeds pixel budget')
            try:
                with _decode(delivery,delivery_size): pass
            except ValueError as exc:
                raise ValueError(f'invalid fitted delivery: {exc}') from exc
        delivery_hash = _hash_file(delivery,min(max_delivery_bytes,MAX_ARCHIVE_BYTES))
        archive_path = f'archive/{expected}.png'; artifact_path = f'artifacts/{delivery_hash}.png'
        _path(root,'artifacts').mkdir(exist_ok=True)
        _install(snapshot,_path(root,archive_path),expected)
        _install(delivery,_path(root,artifact_path),delivery_hash)
    finally: rgba.close()
    output = {k:copy.deepcopy(job[k]) for k in _IDENTITY}
    output.update(status='success',source_version=job['version'],archive_path=archive_path,
        archive_sha256=expected,archive_profile=copy.deepcopy(PROFILE),archive_width=dimensions[0],
        archive_height=dimensions[1],artifact_path=artifact_path,artifact_sha256=delivery_hash,
        width=delivery_size[0],height=delivery_size[1],delivery_profile=profile,diagnostics=[])
    if job['schema_version'] == 2:
        output.update({k:copy.deepcopy(job[k]) for k in ('input_snapshot_digest','tool_fingerprint','source_capture_ref')})
        output.update(artifact_render_key=job['render_key'],artifact_tool_fingerprint=job['tool_fingerprint'])
    return output


def _validate_media(result):
    for path_key, hash_key, directory in (('artifact_path','artifact_sha256','artifacts'),('archive_path','archive_sha256','archive')):
        if not _hash(result.get(hash_key)) or result.get(path_key) != f'{directory}/{result[hash_key]}.png':
            raise ValueError('invalid content-addressed media identity')
    if result.get('source_version') != result['version'] or result.get('archive_profile') != PROFILE:
        raise ValueError('invalid archive provenance')
    if result.get('delivery_profile') not in ('original','fitted_png'): raise ValueError('invalid delivery profile')
    for key in ('width','height','archive_width','archive_height'): _positive(result.get(key),key)


def _project_legacy(catalog, accepted_results):
    """Project trusted durable result objects (not staged files or attempt output).

    Input order is irrelevant: the greatest accepted attempt wins per exact job.
    A current pending/failed slot with an old artifact is exposed as published,
    with its original source_version and diagnostics so readers return stale.
    """
    result = validate_catalog(catalog)
    rows = {(r['character_id'],r['skin_id']):r for r in result['skins']}
    chosen = {}
    for item in accepted_results:
        job = validate_job(item)
        if 'attempt' not in job: raise ValueError('accepted result requires attempt')
        if any(job[k] != result[k] for k in ('instance_id','source_id')): raise ValueError('result scope mismatch')
        if item.get('status') not in ('success','failed','blocked','cancelled'): raise ValueError('invalid terminal result')
        diagnostics = item.get('diagnostics',[])
        if not isinstance(diagnostics,list) or not all(isinstance(x,str) for x in diagnostics): raise ValueError('invalid result diagnostics')
        if item['status'] == 'success': _validate_media(item)
        row = rows.get((job['character_id'],job['skin_id']))
        if row is None or job['version'] != result['version'] or job['render_key'] != row['render_key']: continue
        previous = chosen.get(job['job_id'])
        if previous is not None and previous['attempt'] == job['attempt'] and previous != item:
            raise ValueError('conflicting accepted attempt')
        if previous is None or previous['attempt'] < job['attempt']: chosen[job['job_id']] = copy.deepcopy(item)
    for item in chosen.values():
        row = rows[(item['character_id'],item['skin_id'])]
        if item['status'] == 'success':
            row.update({k:copy.deepcopy(item[k]) for k in _MEDIA})
            row.update(state='published',diagnostics=copy.deepcopy(item.get('diagnostics',[])))
        else:
            row['state'] = 'blocked' if item['status']=='blocked' else 'failed'
            row['diagnostics'] = list(item.get('diagnostics',[])) or [item['status']]
    for row in result['skins']:
        if row.get('artifact_lineage')!='known' and row['state'] != 'published' and all(row.get(k) for k in ('artifact_path','artifact_sha256','source_version')):
            row['diagnostics'] = list(row['diagnostics']) + [f"retained previous artifact: {row['state']}"]
            row['state'] = 'published'
    return validate_catalog(result)



def carry_forward(desired, previous):
    """Copy actual artifact provenance; desired metadata/state stay desired."""
    desired=validate_catalog(desired)
    if previous is None: return desired
    previous=validate_catalog(previous)
    if any(desired[k]!=previous[k] for k in ('instance_id','source_id')):
        raise ValueError('carry scope mismatch')
    old={(r['character_id'],r['skin_id']):r for r in previous['skins']}
    for row in desired['skins']:
        prior=old.get((row['character_id'],row['skin_id']))
        if prior and prior['profile']==row['profile'] and prior.get('artifact_path'):
            if _matching(row):
                prior_matches=(prior.get('artifact_lineage')=='known' and
                    prior.get('artifact_render_key')==row['render_key'] and
                    prior.get('artifact_tool_fingerprint')==row['tool_fingerprint'])
                if not prior_matches: continue
                if _rank(row,desired['version'])>=_rank(prior,desired['version']): continue
            for k in ARTIFACT_FIELDS:
                row.pop(k,None)
                if k in prior: row[k]=copy.deepcopy(prior[k])
    return validate_catalog(desired)


def _matching(row):
    return (row.get('artifact_lineage')=='known' and
        row.get('artifact_render_key')==row['render_key'] and
        row.get('artifact_tool_fingerprint')==row['tool_fingerprint'])


def _rank(row, version):
    # One total order, shared by carry and projection. Within one durable store,
    # later accepted attempts necessarily have later committed event IDs.
    event=row.get('artifact_event') or {}
    return (row['source_version']==version,event.get('id',0),row['job_id'],row['attempt'])


def project_catalog(catalog, accepted_results):
    """Trusted-data compatibility helper, NOT an acceptance boundary.

    Runtime must use project_accepted, which obtains its own durable Reader page.
    Wire-1 keeps the historical state convention. Wire-2 never calls reuse a
    successful desired execution, and stores actual capture/job provenance.
    """
    catalog=validate_catalog(catalog)
    legacy=[r for r in accepted_results if r.get('schema_version')==1]
    if legacy or (not accepted_results and any(r.get('artifact_lineage')=='legacy_unknown' for r in catalog['skins'])):
        catalog=_project_legacy(catalog,legacy)
    rows={(r['character_id'],r['skin_id']):r for r in catalog['skins']}
    from .store import validate_result
    for item in accepted_results:
        if item.get('schema_version')==1: continue
        # Durable result envelopes omit reason; reason is not artifact identity.
        job=validate_job(dict(item,reason=item.get('reason','manual_backfill')))
        validate_result(job,job['attempt'],item)
        if any(job[k]!=catalog[k] for k in ('instance_id','source_id')):
            raise ValueError('result scope mismatch')
        row=rows.get((job['character_id'],job['skin_id']))
        if row is None or job['render_key']!=row['render_key'] or job['tool_fingerprint']!=row['tool_fingerprint']: continue
        expected_plan=catalog.get('desired_execution_plan_id')
        owns_state=(job['version']==catalog['version'] and
            (expected_plan is None or item.get('plan_id')==expected_plan))
        if item['status']!='success':
            if owns_state and (expected_plan is not None or not _matching(row)):
                row.update(state='blocked' if item['status']=='blocked' else 'failed',diagnostics=copy.deepcopy(item['diagnostics']))
            continue
        # A closed plan owns its terminal state independently of which accepted
        # artifact wins. Foreign newer media must not suppress that evidence.
        if owns_state and expected_plan is not None:
            row.update(state='published',diagnostics=copy.deepcopy(item['diagnostics']))
        candidate={k:copy.deepcopy(item[k]) for k in ARTIFACT_FIELDS if k in item}
        candidate['artifact_lineage']='known'
        if _matching(row) and _rank(row,catalog['version'])>_rank(candidate,catalog['version']): continue
        for k in ARTIFACT_FIELDS: row.pop(k,None)
        row.update(candidate)
        if owns_state and expected_plan is None:
            row.update(state='published',diagnostics=copy.deepcopy(item['diagnostics']))
    return validate_catalog(catalog)

def _version(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_-]+',value): raise ValueError('unsafe catalog version')
    return value


@contextmanager
def _lock(root):
    path = _path(root,'.publisher.lock')
    with path.open('a+b') as stream:
        stream.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(stream.fileno(),msvcrt.LK_LOCK,1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(),fcntl.LOCK_EX)
        try:
            # Windows byte locks may cover EOF. Initialize only after ownership;
            # concurrent openers must never write a byte another writer locked.
            if os.fstat(stream.fileno()).st_size == 0:
                stream.write(b'\0'); stream.flush()
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt': msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
            else: fcntl.flock(stream.fileno(),fcntl.LOCK_UN)


def _atomic_json(root, relative, raw):
    target = _path(root,relative); temporary = _temporary(root,target.parent.relative_to(root).as_posix() if target.parent != root else 'catalog')
    with temporary.open('xb') as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary,target); _sync_directory(target.parent)


def _install_catalog(catalog, root):
    """Install and durably heal one immutable revision without pointer changes."""
    catalog = validate_catalog(catalog); version = _version(catalog['version'])
    for row in catalog['skins']:
        for key in ('artifact_path','archive_path'):
            if row.get(key) is not None: _relative(row[key])
    raw = _json(catalog,MAX_PLAN_BYTES).encode('utf-8'); sha = hashlib.sha256(raw).hexdigest()
    relative = f'catalog/{version}/{sha}.json'
    pointer = dict(schema_version=1,version=version,catalog_path=relative,sha256=sha)
    temporary = _temporary(root,f'catalog/{version}')
    with temporary.open('xb') as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    _install(temporary,_path(root,relative),sha)
    # Persist every containing entry, including newly made root ancestors.
    # Identical cached revisions repeat the complete chain to heal a crash.
    _sync_directory_chain(_path(root,'catalog'))
    return pointer


def _write_version_pointer(root, pointer):
    _atomic_json(root,f"catalog/{pointer['version']}.json",_json(pointer,65536).encode('utf-8'))


def _write_current_pointer(root, pointer):
    _atomic_json(root,'current.json',_json(pointer,65536).encode('utf-8'))


def _write_catalog(catalog, root, *, promote=False):
    """Shared legacy/lifecycle writer; projection chooses pointers separately."""
    if type(promote) is not bool: raise ValueError('promotion must be explicit boolean')
    pointer = _install_catalog(catalog,root)
    _write_version_pointer(root,pointer)
    if promote: _write_current_pointer(root,pointer)
    return pointer



def read_catalog(data_root, version=None):
    """Read one pointer snapshot and verify its immutable target."""
    import json
    root = Path(data_root).resolve()
    pointer_path = 'current.json' if version is None else f'catalog/{_version(version)}.json'
    def read(relative,limit):
        with _path(root,relative).open('rb') as stream:
            if os.fstat(stream.fileno()).st_size > limit: raise ValueError('catalog exceeds byte limit')
            data = stream.read(limit+1)
            if len(data)>limit: raise ValueError('catalog exceeds byte limit')
            return data
    pointer = json.loads(read(pointer_path,65536))
    if type(pointer.get('schema_version')) is not int or pointer['schema_version'] != 1: raise ValueError('invalid pointer schema')
    pointed_version = _version(pointer.get('version')); sha = pointer.get('sha256')
    if version is not None and pointed_version != version: raise ValueError('version pointer mismatch')
    if not _hash(sha) or pointer.get('catalog_path') != f'catalog/{pointed_version}/{sha}.json': raise ValueError('invalid immutable catalog path')
    raw = read(pointer['catalog_path'],MAX_PLAN_BYTES)
    if hashlib.sha256(raw).hexdigest() != sha: raise ValueError('catalog digest mismatch')
    catalog = validate_catalog(json.loads(raw))
    if catalog['version'] != pointed_version: raise ValueError('catalog version mismatch')
    return catalog


def publish_catalog(catalog, data_root, *, promote=False):
    """Legacy snapshot writer only; new callers use publish_desired/project_accepted."""
    root=Path(data_root).resolve(); root.mkdir(parents=True,exist_ok=True)
    with _lock(root):
        for snapshot in (validate_catalog(catalog),_optional_catalog(root),_optional_catalog(root,catalog['version'])):
            if snapshot and (snapshot.get('publication_protocol')==2 or any(r.get('artifact_lineage')=='known' for r in snapshot['skins'])):
                raise ValueError('new lineage requires locked desired/accepted APIs')
        return _write_catalog(catalog,root,promote=promote)


def _optional_catalog(root,version=None):
    try: return read_catalog(root,version)
    except FileNotFoundError:
        pointer=root/('current.json' if version is None else f'catalog/{_version(version)}.json')
        if pointer.exists(): raise
        return None


def catalog_token(data_root):
    """Opaque canonical current snapshot token; None means no current pointer."""
    from .model import digest
    current=_optional_catalog(Path(data_root).resolve())
    return None if current is None else digest(current)


def version_catalog_token(data_root, version):
    """Exact version snapshot token; only an absent pointer returns None."""
    from .model import digest
    catalog=_optional_catalog(Path(data_root).resolve(),_version(version))
    return None if catalog is None else digest(catalog)


def _desired_snapshot(catalog, execution_plan_id):
    incoming=copy.deepcopy(catalog)
    if not isinstance(incoming,dict): raise ValueError('catalog must be an object')
    for key in ('desired_generation','desired_execution_plan_id'): incoming.pop(key,None)
    desired=validate_catalog(incoming)
    desired['publication_protocol']=2
    for row in desired['skins']:
        for key in ARTIFACT_FIELDS: row.pop(key,None)
        row.update(artifact_path=None,artifact_sha256=None,source_version=None)
        if row['state']=='published': row['state']='not_generated'
    desired['desired_execution_plan_id']=execution_plan_id
    desired['desired_generation']=desired_generation(desired,execution_plan_id)
    return validate_catalog(desired)


def _desired_ref(pointer):
    return {'revision':pointer['sha256'],'locator':pointer['catalog_path'],'sha256':pointer['sha256']}


def stage_desired_snapshot(catalog,data_root,*,execution_plan_id):
    """Trusted immutable metadata staging before closed-plan registration.

    Never writes a pointer or grants acceptance/promotion authority. No CAS or
    publisher/core lock is needed for the shared atomic no-clobber installer.
    Caller authenticates and freezes plan/source identity before use.
    """
    if not _hash(execution_plan_id): raise ValueError('explicit desired execution plan required')
    desired=_desired_snapshot(catalog,execution_plan_id)
    root=Path(data_root).resolve(); root.mkdir(parents=True,exist_ok=True)
    return {'desired_catalog_ref':_desired_ref(_install_catalog(desired,root))}


@contextmanager
def locked_mutation(data_root):
    """Trusted bounded coordinator hook. Publisher FIRST; raw updater OS mutex
    must then be acquired NONBLOCKING, token rechecked, metadata committed, and
    updater released in finally. Only bounded metadata DB transactions are allowed;
    no heavy work, long DB transactions, guard/recovery or network.
    Context object is invalid after exit; callers must not nest publisher locks.
    """
    root=Path(data_root).resolve(); root.mkdir(parents=True,exist_ok=True)
    with _lock(root):
        mutation=_LockedMutation(root)
        try: yield mutation
        finally: mutation.active=False


class _LockedMutation:
    def __init__(self,root): self.root=root; self.active=True
    def publish_desired(self,catalog,*,expected_current,execution_plan_id=None):
        if not self.active: raise RuntimeError('closed publisher mutation')
        if catalog_token(self.root)!=expected_current: raise ValueError('current pointer conflict; reread and retry')
        if execution_plan_id is not None and not _hash(execution_plan_id):
            raise ValueError('invalid desired execution plan')
        desired=_desired_snapshot(catalog,execution_plan_id)
        current=_optional_catalog(self.root)
        prior=_optional_catalog(self.root,desired['version'])
        # Read both snapshots under lock; preserve any accepted change committed
        # since lifecycle prepared its desired metadata. Existing version wins.
        desired=carry_forward(desired,current)
        desired=carry_forward(desired,prior)
        for snapshot in (current,prior):
            if snapshot and snapshot.get('desired_generation')==desired['desired_generation']:
                states={(r['character_id'],r['skin_id']):r for r in snapshot['skins']}
                for row in desired['skins']:
                    saved=states[(row['character_id'],row['skin_id'])]
                    row.update(state=saved['state'],diagnostics=copy.deepcopy(saved['diagnostics']))
        return _write_catalog(desired,self.root,promote=True)

    def install_manual_desired(self,catalog,*,execution_plan_id,expected_current,expected_version):
        if not self.active: raise RuntimeError('closed publisher mutation')
        if not _hash(execution_plan_id): raise ValueError('explicit manual execution plan required')
        desired=_desired_snapshot(catalog,execution_plan_id)
        if catalog_token(self.root)!=expected_current:
            raise ValueError('current pointer conflict; reread and retry')
        if version_catalog_token(self.root,desired['version'])!=expected_version:
            raise ValueError('version pointer conflict; reread and retry')
        current=_optional_catalog(self.root)
        prior=_optional_catalog(self.root,desired['version'])
        for snapshot in (current,prior):
            if snapshot and any(snapshot[k]!=desired[k] for k in ('instance_id','source_id')):
                raise ValueError('manual catalog scope mismatch')
        same_current=current is not None and current['version']==desired['version']
        if same_current and current.get('desired_execution_plan_id') is None:
            raise ValueError('manual reuse requires authenticated explicit baseline bootstrap')
        pointer=_install_catalog(desired,self.root)
        disposition='immutable_only'
        if prior is None:
            if same_current:
                canonical=_install_catalog(current,self.root)
                _write_version_pointer(self.root,canonical)
                disposition='current_version_recovered'
            else:
                _write_version_pointer(self.root,pointer)
                disposition='version_seeded'
        return {'desired_catalog_ref':_desired_ref(pointer),'disposition':disposition}


def publish_desired(catalog,data_root,*,expected_current,execution_plan_id=None):
    """Trusted lifecycle promotion, with mandatory optimistic current token.
    A conflict requires lifecycle revalidation, never blind automatic promotion.
    execution_plan_id is the trusted closed-plan identity; a different plan
    starts a new desired generation even with identical render metadata. None
    supports legacy automatic same-metadata publication, not manual execution
    fencing. Caller-derived generation fields are ignored and recomputed.
    Caller JSON (including published=True) grants no lifecycle authority.
    """
    with locked_mutation(data_root) as mutation:
        return mutation.publish_desired(catalog,expected_current=expected_current,execution_plan_id=execution_plan_id)


def install_manual_desired(catalog,data_root,*,execution_plan_id,expected_current,expected_version):
    """Trusted metadata-only installer, never a current promotion or acceptance.

    Caller authenticates source/selection/plan and freezes request identity before
    use. Both snapshot tokens are mandatory CAS fences, distinct from external
    core authority. Registration and accepted events remain separate operations.
    Same-current legacy None binding requires a real lifecycle baseline first.
    """
    with locked_mutation(data_root) as mutation:
        return mutation.install_manual_desired(catalog,execution_plan_id=execution_plan_id,
            expected_current=expected_current,expected_version=expected_version)


def project_accepted(reader,data_root,*,after_id=0,limit=16,max_bytes=4*1024*1024,cursor=None):
    """Read a bounded durable StoreV2 page, then merge latest under one lock.
    Returned cursor may be checkpointed ONLY after this call returns. Failure
    leaves it unchanged so replay repairs partial version/current writes.
    """
    from .reader import PortraitReader
    if type(reader) is not PortraitReader: raise TypeError('real PortraitReader required')
    page=reader.read_events(after_id,limit,max_bytes,cursor=cursor)
    results=[]
    for event in page['events']:
        if event['provenance']!='registered': continue
        item=copy.deepcopy(event['result'])
        item['plan_id']=event['plan_id']
        item['artifact_event']=dict(database_id=page['cursor']['database_id'],id=event['id'])
        results.append(item)
    root=Path(data_root).resolve(); root.mkdir(parents=True,exist_ok=True)
    with _lock(root):
        current=_optional_catalog(root)
        versions={r['version'] for r in results}
        if current is not None and results: versions.add(current['version'])
        for version in sorted(versions):
            catalog=_optional_catalog(root,version)
            if catalog is None: raise ValueError('desired version not published: '+version)
            if (catalog['instance_id'],catalog['source_id'])!=(reader.instance_id,reader.source_id):
                raise ValueError('reader/catalog scope mismatch')
            merged=project_catalog(catalog,results)
            _write_version_pointer(root,_install_catalog(merged,root))
        if current is not None and results:
            if (current['instance_id'],current['source_id'])!=(reader.instance_id,reader.source_id):
                raise ValueError('reader/catalog scope mismatch')
            # Always start from actual current, even after a version-only crash.
            # A different same-version generation remains independently owned.
            merged=project_catalog(current,results)
            _write_current_pointer(root,_install_catalog(merged,root))
    return page
