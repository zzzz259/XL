"""Fixed Linux category/TextAsset entry. Trusted caller configuration only.

No UnityPy import or output creation precedes request, lease and kernel checks.
This protocol does not authenticate AB membership; the pinned parent owns that.
"""
from contextlib import contextmanager, ExitStack
from dataclasses import fields
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import stat
import sys

from .executor import BoundaryError, Deferred, ResourcePolicy, verify_resource_boundary, check_admission, _disk_free
from .model import digest
from .source_io import NormalizedBundle, VerifiedBlob, open_normalized_bundle, MAX_BLOB_BYTES

REQUEST_BYTES = 65536
RESULT_BYTES = 65536
LIMIT_CAPS = dict(info_bytes=4*1024**2, block_bytes=8*1024**2, expanded_bytes=128*1024**2,
                  blocks=8192, nodes=1024, objects=100000, selected_bytes=16*1024**2, json_bytes=32*1024**2)
IDENTITY = ('operation_id','instance_id','source_id','scope_key','version','pin_digest')


def _keys(value, names):
    if type(value) is not dict or set(value) != set(names):
        raise ValueError('invalid fields')


def _hex(value, size):
    if type(value) is not str or re.fullmatch('[0-9a-f]{%d}' % size,value) is None:
        raise ValueError('invalid digest')


def _path(value):
    if type(value) is not str or not value or '\0' in value:
        raise ValueError('invalid path')
    path=Path(value)
    if not path.is_absolute() or str(path)!=value or '..' in path.parts or (os.name=='posix' and path.anchor!='/'):
        raise ValueError('canonical absolute path required')
    return path


def _integer(value, cap, minimum=1):
    if type(value) is not int or not minimum <= value <= cap:
        raise ValueError('invalid integer')


def validate_request(value):
    """Validate the v1 trusted envelope without opening sources or importing reader."""
    common={'schema_version',*IDENTITY,'mode','normalized_root','attempt_root','work_root','descriptor','policy','decrypt_key_hex'}
    if type(value) is not dict: raise ValueError('request object required')
    mode=value.get('mode')
    if mode=='category':
        _keys(value,common|{'category'}|( {'limits'} if 'limits' in value else set()))
        if value['category'] not in ('Data','Arts'):raise ValueError('unsupported category')
    elif mode=='textasset':
        _keys(value,common|{'exact_name'}|({'limits'} if 'limits' in value else set()))
        if value['exact_name']!='BaseFashion.lua':raise ValueError('unsupported TextAsset')
    else: raise ValueError('unsupported mode')
    if type(value['schema_version']) is not int or value['schema_version']!=1:raise ValueError('unsupported schema')
    for name in ('operation_id','scope_key','pin_digest'):_hex(value[name],64)
    for name in ('instance_id','source_id'):
        text=value[name]
        if type(text) is not str or not text.strip() or len(text)>256 or any(ord(c)<32 or 0xd800<=ord(c)<=0xdfff for c in text):
            raise ValueError('invalid scope')
    if value['scope_key']!=digest([value['instance_id'],value['source_id']]):raise ValueError('scope mismatch')
    if type(value['version']) is not str or re.fullmatch('[1-9][0-9]{0,63}',value['version']) is None:raise ValueError('invalid version')
    _hex(value['decrypt_key_hex'],32)
    paths={k:_path(value[k]) for k in ('normalized_root','attempt_root','work_root')}
    n,a,w=(paths[k] for k in ('normalized_root','attempt_root','work_root'))
    if a==n or a in n.parents or n in a.parents or w!=a/'work':raise ValueError('overlapping source/attempt roots')
    raw=value['policy'];_keys(raw,(f.name for f in fields(ResourcePolicy)))
    raw=dict(raw)
    for name in ('lock_path',):raw[name]=_path(raw[name])
    for name in ('updater_status_paths','disk_roots'):
        if type(raw[name]) is not list or not 0<len(raw[name])<=16:raise ValueError('invalid policy paths')
        raw[name]=tuple(_path(p) for p in raw[name])
    policy=ResourcePolicy(**raw)
    if policy.admission_bytes<640*1024**2 or policy.abort_bytes<128*1024**2 or policy.disk_free_bytes<1024**3 or policy.poll_seconds>.1:
        raise ValueError('weakened worker policy')
    limits=value.get('limits',LIMIT_CAPS)
    _keys(limits,LIMIT_CAPS)
    for name,cap in LIMIT_CAPS.items():_integer(limits[name],cap)
    d=value['descriptor'];_keys(d,(f.name for f in fields(NormalizedBundle)))
    wire=d['wire'];_keys(wire,(f.name for f in fields(VerifiedBlob)))
    for name,length in (('scope_key',64),('pin_digest',64),('wire_md5',32),('sha256',64)):_hex(wire[name],length)
    if any(wire[k]!=value[k] for k in ('scope_key','pin_digest')):raise ValueError('descriptor scope mismatch')
    _integer(wire['size'],MAX_BLOB_BYTES)
    remote=wire['remote_name']
    if type(remote) is not str or not (remote in ('data_'+wire['wire_md5']+'.json','arts_'+wire['wire_md5']+'.json') or remote==wire['wire_md5']+'.bundle'):raise ValueError('invalid remote name')
    if mode=='category' and remote!=value['category'].lower()+'_'+wire['wire_md5']+'.json':raise ValueError('category source mismatch')
    if mode=='textasset' and remote!=wire['wire_md5']+'.bundle':raise ValueError('TextAsset requires AB source')
    relative=wire['scope_key']+'/'+wire['pin_digest']
    if wire['locator']!=relative+'/'+digest([remote,wire['wire_md5'],wire['size']])+'.blob':raise ValueError('invalid wire locator')
    _hex(d['sha256'],64);_integer(d['size'],MAX_BLOB_BYTES);_integer(d['prefix_offset'],4095,0)
    if d['size']+d['prefix_offset']!=wire['size'] or d['locator']!=relative+'/'+d['sha256']+'.bundle':raise ValueError('invalid view locator')
    _integer(d['format_version'],8,6)
    for name in ('info_compressed_size','info_uncompressed_size'):_integer(d[name],limits['info_bytes'])
    _integer(d['flags'],0x7fffffff,0)
    for name in ('engine_version','player_version'):
        if type(d[name]) is not str or not 0<len(d[name])<=128 or any(ord(c)<32 for c in d[name]):raise ValueError('invalid engine/player version')
    descriptor=NormalizedBundle(**{**d,'wire':VerifiedBlob(**wire)})
    return dict(value=value,paths=paths,policy=policy,descriptor=descriptor,limits=dict(limits))


@contextmanager
def _directory(path, *, private=False):
    fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
            os.close(fd);fd=child
        info=os.fstat(fd)
        if private and (info.st_uid!=os.getuid() or info.st_mode&0o077):raise ValueError('private owned attempt required')
        yield fd
    finally:os.close(fd)


def _pairs(pairs):
    obj={}
    for k,v in pairs:
        if k in obj:raise ValueError('duplicate JSON field')
        obj[k]=v
    return obj


def _load_request(path):
    with _directory(path.parent) as parent:
        fd=os.open(path.name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=parent)
        with os.fdopen(fd,'rb') as stream:
            info=os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_size>REQUEST_BYTES:raise ValueError('bounded regular request required')
            data=stream.read(REQUEST_BYTES+1)
    if len(data)>REQUEST_BYTES:raise ValueError('oversized request')
    obj=json.loads(data.decode('utf-8'),object_pairs_hook=_pairs,
                   parse_constant=lambda _:(_ for _ in ()).throw(ValueError('nonfinite JSON')))
    count=0
    def visit(v,depth=0):
        nonlocal count
        count+=1
        if count>2048 or depth>16:raise ValueError('request complexity')
        if isinstance(v,dict):
            for child in v.values():visit(child,depth+1)
        elif isinstance(v,list):
            for child in v:visit(child,depth+1)
    visit(obj)
    return obj


def _guard(policy):
    if sys.platform!='linux':raise BoundaryError('Linux required')
    for key in ('XL_PORTRAIT_RESOURCE_LOCK_FD','XL_PORTRAIT_RESOURCE_LOCK_PATH'):
        if not os.environ.get(key):raise BoundaryError('inherited lease required')
    if os.environ['XL_PORTRAIT_RESOURCE_LOCK_PATH']!=str(policy.lock_path):raise BoundaryError('lease policy mismatch')
    try:
        from app.platform.resource_lease import inherited_lease_kwargs
        if not inherited_lease_kwargs().get('pass_fds'):raise ValueError('live inherited lease required')
    except (ImportError, OSError, ValueError) as exc:
        raise BoundaryError('inherited lease unavailable') from exc
    verify_resource_boundary(policy)
    check_admission(policy,running=True)


def _encoded(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')


def _publish(directory,name,data,cap):
    if type(data) is not bytes or not 0<len(data)<=cap:raise ValueError('output budget')
    pending='.'+name+'.pending'
    fd=os.open(pending,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=directory)
    published=False
    try:
        with os.fdopen(fd,'wb') as stream:
            stream.write(data);stream.flush();os.fsync(stream.fileno())
        os.link(pending,name,src_dir_fd=directory,dst_dir_fd=directory,follow_symlinks=False)
        published=True
        os.unlink(pending,dir_fd=directory);os.fsync(directory)
    except BaseException:
        if published:os.unlink(name,dir_fd=directory)
        try:os.unlink(pending,dir_fd=directory)
        except FileNotFoundError:pass
        raise
    return dict(relative_path=name,size=len(data),sha256=hashlib.sha256(data).hexdigest())


def run_request(value, *, request_path, result_path):
    """Run once; return bound result only after durable no-clobber publication."""
    checked=validate_request(value)
    if sys.platform!='linux':raise BoundaryError('Linux required')
    paths=checked['paths'];attempt=paths['attempt_root'];normalized=paths['normalized_root'];work=paths['work_root']
    request_path=_path(str(request_path));result_path=_path(str(result_path))
    if result_path!=attempt/'result.json' or request_path==normalized or normalized in request_path.parents or request_path==attempt or attempt in request_path.parents:
        raise ValueError('unsafe request/result placement')
    with ExitStack() as stack:
        directory=stack.enter_context(_directory(attempt,private=True))
        workfd=stack.enter_context(_directory(work,private=True))
        stack.enter_context(_directory(normalized))
        if set(os.listdir(directory))!={'work'} or os.listdir(workfd):raise ValueError('new empty attempt required')
        _guard(checked['policy'])
        for path in (attempt,work,normalized):
            if _disk_free(path)<checked['policy'].disk_free_bytes:raise Deferred('working disk admission')
        # Accepted opener verifies content, locator and header before heavy import.
        with open_normalized_bundle(checked['descriptor'],normalized_root=normalized) as source:
            if os.fstat(source.fileno()).st_nlink!=1:raise ValueError('source hardlink alias')
        reader=importlib.import_module('server_app.portraits.bundle_reader')
        limits=reader.ReaderLimits(**checked['limits'])
        with reader.open_bundle(checked['descriptor'],normalized_root=normalized,work_root=work,
                                decrypt_key=bytes.fromhex(value['decrypt_key_hex']),limits=limits) as bundle:
            if value['mode']=='textasset':
                extracted=bundle.extract_textasset('BaseFashion.lua');data=extracted['data'];name='BaseFashion.lua';cap=limits.selected_bytes
                counts={}
            else:
                extracted=bundle.read_category(value['category']);tree=extracted['metadata'];data=_encoded(tree);name='category.json';cap=limits.json_bytes
                counts={k:len(tree[k]) for k in ('dirs','bundles','assets')}
            evidence={k:extracted[k] for k in ('cab','path_id','source_sha256','wire_sha256')}
            if value['mode']=='category':evidence['containers']=extracted['containers']
        # Any source/reader failure above leaves no artifact/result.
        output=_publish(directory,name,data,cap)
        result=dict(schema_version=1,status='ok',**{k:value[k] for k in IDENTITY},mode=value['mode'],
                    selection=value.get('category',value.get('exact_name')),output=output,evidence=evidence,counts=counts)
        _publish(directory,'result.json',_encoded(result),RESULT_BYTES)
        return result


def main(argv=None):
    try:
        args=sys.argv[1:] if argv is None else argv
        if len(args)!=4 or args[0]!='--request' or args[2]!='--result':raise ValueError('expected --request PATH --result PATH')
        if sys.platform!='linux':raise BoundaryError('Linux required')
        request=_path(args[1]);result=_path(args[3])
        run_request(_load_request(request),request_path=request,result_path=result)
        return 0
    except Exception as exc:
        # Never include payload, key, path, metadata or library exception text.
        print('portrait source worker refused: '+type(exc).__name__,file=sys.stderr)
        return 2 if isinstance(exc,Deferred) else 3 if isinstance(exc,BoundaryError) else 1


if __name__=='__main__':raise SystemExit(main())

