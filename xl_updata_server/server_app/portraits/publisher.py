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

from PIL import Image

from .model import PROFILE, _hash, validate_catalog, validate_job
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
    job = validate_job(job)
    if 'attempt' not in job: raise ValueError('explicit claimed attempt required')
    _positive(max_delivery_bytes,'delivery byte budget'); _positive(max_delivery_pixels,'delivery pixel budget')
    if not isinstance(result,dict) or result.get('status') != 'success' or result.get('job_id') != job['job_id']:
        raise ValueError('result status/job mismatch')
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
    return output


def _validate_media(result):
    for path_key, hash_key, directory in (('artifact_path','artifact_sha256','artifacts'),('archive_path','archive_sha256','archive')):
        if not _hash(result.get(hash_key)) or result.get(path_key) != f'{directory}/{result[hash_key]}.png':
            raise ValueError('invalid content-addressed media identity')
    if result.get('source_version') != result['version'] or result.get('archive_profile') != PROFILE:
        raise ValueError('invalid archive provenance')
    if result.get('delivery_profile') not in ('original','fitted_png'): raise ValueError('invalid delivery profile')
    for key in ('width','height','archive_width','archive_height'): _positive(result.get(key),key)


def project_catalog(catalog, accepted_results):
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
        if row['state'] != 'published' and all(row.get(k) for k in ('artifact_path','artifact_sha256','source_version')):
            row['diagnostics'] = list(row['diagnostics']) + [f"retained previous artifact: {row['state']}"]
            row['state'] = 'published'
    return validate_catalog(result)


def _version(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_-]+',value): raise ValueError('unsafe catalog version')
    return value


@contextmanager
def _lock(root):
    path = _path(root,'.publisher.lock')
    with path.open('a+b') as stream:
        if stream.tell() == 0: stream.write(b'\0'); stream.flush()
        stream.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(stream.fileno(),msvcrt.LK_LOCK,1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(),fcntl.LOCK_EX)
        try: yield
        finally:
            stream.seek(0)
            if os.name == 'nt': msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
            else: fcntl.flock(stream.fileno(),fcntl.LOCK_UN)


def _atomic_json(root, relative, raw):
    target = _path(root,relative); temporary = _temporary(root,target.parent.relative_to(root).as_posix() if target.parent != root else 'catalog')
    with temporary.open('xb') as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary,target); _sync_directory(target.parent)


def publish_catalog(catalog, data_root, *, promote=False):
    """Install a hashed immutable revision, then version and optional current pointers."""
    catalog = validate_catalog(catalog); version = _version(catalog['version'])
    if type(promote) is not bool: raise ValueError('promotion must be explicit boolean')
    for row in catalog['skins']:
        for key in ('artifact_path','archive_path'):
            if row.get(key) is not None: _relative(row[key])
    raw = _json(catalog,MAX_PLAN_BYTES).encode('utf-8'); sha = hashlib.sha256(raw).hexdigest()
    root = Path(data_root).resolve(); root.mkdir(parents=True,exist_ok=True)
    relative = f'catalog/{version}/{sha}.json'
    pointer = dict(schema_version=1,version=version,catalog_path=relative,sha256=sha)
    with _lock(root):
        temporary = _temporary(root,f'catalog/{version}')
        with temporary.open('xb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        _install(temporary,_path(root,relative),sha)
        pointer_raw = _json(pointer,65536).encode('utf-8')
        _atomic_json(root,f'catalog/{version}.json',pointer_raw)
        if promote: _atomic_json(root,'current.json',pointer_raw)
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
