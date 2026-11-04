"""Versioned guarded HTTPS worker. No network imports/actions before _guard."""
import base64
import os
import sys
from contextlib import ExitStack, contextmanager
from dataclasses import asdict, fields

from . import source_io as io
from .executor import BoundaryError, Deferred, ResourcePolicy, _disk_free
from .model import digest
from .source_transfer import FetchRequired, validate_fetch
from .source_worker import (
    _directory,
    _encoded,
    _guard,
    _keys,
    _load_request,
    _path,
    _publish,
)


def make_request(pin, fetch, policy, cache_root, attempt_root):
    raw=asdict(policy)
    raw['lock_path']=str(raw['lock_path'])
    for key in ('updater_status_paths','disk_roots'):raw[key]=list(map(str,raw[key]))
    return {'schema': 'portrait-download/v1','operation_id': digest([str(attempt_root),asdict(fetch)]),
                'pin': {'instance_id': pin.instance_id,'source_id': pin.source_id,'cdn_base': pin.cdn_base,
                         'version': pin.version,'version_file': pin.version_file,
                         'raw': base64.b64encode(pin.raw_version_bytes).decode('ascii')},
                'fetch': asdict(fetch),'policy': raw,'cache_root': str(cache_root),
                'attempt_root': str(attempt_root),'work_root': str(attempt_root/'work')}


def validate_request(value):
    _keys(value,('schema','operation_id','pin','fetch','policy','cache_root','attempt_root','work_root'))
    if value['schema']!='portrait-download/v1':raise ValueError('unsupported download schema')
    _keys(value['pin'],('instance_id','source_id','cdn_base','version','version_file','raw'))
    raw=value['pin']['raw']
    if type(raw) is not str or len(raw)>49152:raise ValueError('bounded pin encoding required')
    binary=base64.b64decode(raw,validate=True)
    if base64.b64encode(binary).decode('ascii')!=raw:raise ValueError('canonical pin encoding required')
    pin=io.pin_source(binary,**{k:v for k,v in value['pin'].items() if k!='raw'})
    _keys(value['fetch'],(f.name for f in fields(FetchRequired)))
    fetch=validate_fetch(pin,FetchRequired(**value['fetch']))
    paths={k:_path(value[k]) for k in ('cache_root','attempt_root','work_root')}
    cache,attempt,work=(paths[k] for k in ('cache_root','attempt_root','work_root'))
    if cache==attempt or cache in attempt.parents or attempt in cache.parents or work!=attempt/'work':
        raise ValueError('overlapping download roots')
    if value['operation_id']!=digest([str(attempt),asdict(fetch)]):raise ValueError('operation mismatch')
    _keys(value['policy'],(f.name for f in fields(ResourcePolicy)))
    raw=dict(value['policy']);raw['lock_path']=_path(raw['lock_path'])
    for key in ('updater_status_paths','disk_roots'):
        if type(raw[key]) is not list or not 0<len(raw[key])<=16:raise ValueError('policy paths')
        raw[key]=tuple(_path(p) for p in raw[key])
    policy=ResourcePolicy(**raw)
    if policy.admission_bytes<640*1024**2 or policy.abort_bytes<128*1024**2 or policy.disk_free_bytes<1024**3 or policy.poll_seconds>.1:
        raise ValueError('weakened worker policy')
    if any(not any(p==root or root in p.parents for root in policy.disk_roots) for p in (cache,attempt)):
        raise ValueError('unmonitored download storage')
    return pin,fetch,policy,paths


def validate_headers(headers,size):
    pairs=list(headers.items())
    if len(pairs)>64 or sum(len(k)+len(v)+4 for k,v in pairs)>16384:
        raise ValueError('response header budget')
    for key in ('Content-Length','Content-Encoding','Transfer-Encoding'):
        if len(headers.get_all(key,[]))>1:raise ValueError('duplicate framing header')
    if headers.get('Content-Encoding','identity').strip().lower()!='identity':raise ValueError('encoded response')
    if headers.get('Transfer-Encoding') is not None:raise ValueError('transfer framing unsupported')
    length=headers.get('Content-Length')
    if length is not None and (not length.isascii() or not length.isdecimal() or int(length)!=size):
        raise ValueError('advertised size mismatch')


@contextmanager
def https_remote(pin,fetch):
    # Called only after live kernel/lease/disk guard. HTTPSConnection never uses
    # ambient proxies or follows redirects; its default context verifies TLS.
    import http.client
    import ssl
    from urllib.parse import urlsplit
    url=urlsplit(pin.cdn_base+'/'+fetch.remote_name)
    connection=http.client.HTTPSConnection(url.hostname,url.port,timeout=10,context=ssl.create_default_context())
    with ExitStack() as cleanup:
        cleanup.callback(connection.close)
        connection.request('GET',url.path,headers={'Accept-Encoding':'identity','Connection':'close'})
        response=connection.getresponse()
        # A will_close response can own a socket detached from the connection.
        # ExitStack also closes the connection if response.close itself fails.
        cleanup.callback(response.close)
        if response.status!=200:raise ValueError('non-success or redirect response')
        validate_headers(response.headers,fetch.size)
        # Read raw bounded body to detect bytes beyond Content-Length too.
        class Bounded:
            def read(self,n):
                if type(n) is not int or not 0<n<=io.CHUNK:raise ValueError('bounded read required')
                return response.fp.read(n)
        yield Bounded()


def run_request(value,*,request_path,result_path):
    pin,fetch,policy,paths=validate_request(value)
    attempt,work,cache=(paths[k] for k in ('attempt_root','work_root','cache_root'))
    request_path=_path(str(request_path));result_path=_path(str(result_path))
    if result_path!=attempt/'result.json' or request_path.parent!=attempt.parent or request_path.name!='request.json':
        raise ValueError('request/result placement')
    with ExitStack() as stack:
        directory=stack.enter_context(_directory(attempt,private=True))
        workfd=stack.enter_context(_directory(work,private=True))
        stack.enter_context(_directory(cache))
        if set(os.listdir(directory))!={'work'} or os.listdir(workfd):raise ValueError('new private attempt required')
        _guard(policy)
        if any(_disk_free(p)<policy.disk_free_bytes for p in (cache,attempt,work)):raise Deferred('working disk admission')
        def remote(name):
            if name!=fetch.remote_name:raise ValueError('remote name mismatch')
            return https_remote(pin,fetch)
        blob=io.acquire_blob(pin,remote_name=fetch.remote_name,wire_md5=fetch.wire_md5,
                             size=fetch.size,cache_root=cache,open_remote=remote)
        result={'schema': 'portrait-download-result/v1','status': 'ok','request_digest': digest(value),'blob': asdict(blob)}
        _publish(directory,'result.json',_encoded(result),65536)
        return result


def main(argv=None):
    try:
        args=sys.argv[1:] if argv is None else argv
        if len(args)!=4 or args[0]!='--request' or args[2]!='--result':raise ValueError('worker arguments')
        if sys.platform!='linux':raise BoundaryError('Linux required')
        request=_path(args[1]);result=_path(args[3])
        run_request(_load_request(request),request_path=request,result_path=result)
        return 0
    except Exception as exc:  # noqa: BLE001 - worker exit protocol redacts all payloads
        print('portrait download refused: '+type(exc).__name__,file=sys.stderr)
        return 2 if isinstance(exc,Deferred) else 3 if isinstance(exc,BoundaryError) else 1


if __name__=='__main__':raise SystemExit(main())
