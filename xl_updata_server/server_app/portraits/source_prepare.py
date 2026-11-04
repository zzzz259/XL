"""Explicit metadata-only composition. No publication, job, or renderer imports.

Runtime manifests are trusted deployment configuration, never queued job input.
The caller must establish startup PYTHONPATH and the shared ResourceSlot first.
"""
from contextlib import contextmanager
from functools import wraps
from dataclasses import dataclass, asdict
import gc
import hashlib
import importlib
import inspect
import json
import math
import os
import re
from pathlib import Path
import shutil
import sys
import tempfile
import time

from . import source_io as io
from .executor import ResourceSlot, BoundaryError, Deferred, check_admission, verify_resource_boundary
from .input import build_input_snapshot, _catalog
from .model import digest, validate_catalog
from .source_worker import validate_request, IDENTITY
from ..lua_decoder import classify_lua_bytes, decode_chinese, FIXED_HEAD

CAP = 16*1024**2
JVM_OPTIONS = ('-Xms16m','-Xmx64m','-XX:ActiveProcessorCount=1','-XX:+UseSerialGC','-Dfile.encoding=UTF-8')

class PreparationError(ValueError):
    """Permanent source/configuration data failure or finite deadline expiry."""


def _typed_errors(error_type, message):
    """Public boundaries preserve resource/cleanup exceptions and redact data."""
    def decorate(function):
        @wraps(function)
        def call(*args, **kwargs):
            try: return function(*args, **kwargs)
            except (PreparationError, Deferred, BoundaryError): raise
            except (ValueError, KeyError, TypeError, AttributeError, OSError, ImportError, RecursionError) as exc:
                raise error_type(message) from exc
        return call
    return decorate


@dataclass(frozen=True)
class DeadlineTransport:
    """Trusted synchronous adapter, no background work may outlive a method.

    open(name, *, timeout) -> handle; read(handle,n, *, timeout) -> bytes;
    close(handle, *, timeout) -> None. Every operation MUST finish or raise
    TimeoutError within timeout seconds. close must release locally even at zero
    budget (it may not perform a network handshake). No implicit context-manager
    enter/exit or hidden reads. Quantum <= .1 bounds stop/updater observation.
    Implementations are deployment configuration, not untrusted job callables.
    """
    open: object
    read: object
    close: object
    quantum_seconds: float = .1

    def validate(self):
        if type(self.quantum_seconds) not in (int,float) or not 0<self.quantum_seconds<=.1:
            raise BoundaryError('invalid transport cancellation quantum')
        try:
            for function,args in ((self.open,('name',)),(self.read,(object(),1)),(self.close,(object(),))):
                inspect.signature(function).bind(*args,timeout=.01)
        except (ValueError,TypeError) as exc:
            raise BoundaryError('deadline transport signature required') from exc


def _validate_basefashion(text):
    """Validate a bounded complete data-table subset before the actual parser.

    Supports assignment/local assignment (optional return BaseFashion), or a
    single returned table. Values: scalar, nested table, or function() return
    T(scalar,...) end. No executable general Lua or unrelated trailing tables.
    Original decoded bytes remain unchanged; this validator never executes Lua.
    """
    token=re.compile(r"""(?P<space>\s+)|(?P<comment>--\[(?P<eq>=*)\[.*?\](?P=eq)\]|--(?!\[=*\[)[^\n]*)|(?P<string>"(?:\\[^\n]|[^"\\\r\n])*"|'(?:\\[^\n]|[^'\\\r\n])*')|(?P<number>[-+]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][-+]?[0-9]+)?)|(?P<name>[A-Za-z_][A-Za-z_0-9]*)|(?P<punct>[{}\[\](),;=])""",re.S)
    def lex():
        pos=0;count=0
        while pos<len(text):
            match=token.match(text,pos)
            if match is None:raise PreparationError('invalid BaseFashion token')
            pos=match.end();kind=match.lastgroup
            if kind not in ('space','comment'):
                count+=1
                if count>1000000:raise PreparationError('BaseFashion token limit')
                yield kind,match.group()
        while True:yield 'eof',''
    lexer=lex();look=[next(lexer),next(lexer)];root_fields=[]
    def peek(offset=0):return look[offset][1]
    def take(expected=None):
        kind,value=look[0]
        if kind=='eof' or (expected is not None and value!=expected):raise PreparationError('incomplete BaseFashion structure')
        look[0]=look[1];look[1]=next(lexer)
        return kind,value
    def scalar():
        kind,value=take()
        if kind not in ('number','string') and value not in ('true','false','nil'):
            raise PreparationError('unsupported BaseFashion scalar')
    def value(depth):
        if peek()=='{':table(depth+1)
        elif peek()=='function':
            for part in ('function','(',')','return','T','('):take(part)
            if peek()!=')':
                scalar()
                while peek()==',':take(',');scalar()
            for part in (')','end'):take(part)
        else:scalar()
    def direct_field(key):
        # Retain only the four direct fields that determine portrait ownership.
        # Unknown/nested fields are validated but never flattened into this map.
        if peek()=='nil':take('nil');return None
        if key=='show_spine_position':
            take('{');coordinates=[]
            while peek()!='}':
                kind,number=take()
                if kind!='number':raise PreparationError('invalid direct portrait position')
                number=float(number)
                if not math.isfinite(number):raise PreparationError('nonfinite direct portrait position')
                coordinates.append(number)
                if len(coordinates)>3:raise PreparationError('invalid direct portrait position length')
                if peek() in (',',';'):take()
                elif peek()!='}':raise PreparationError('invalid direct portrait position separator')
            take('}')
            if len(coordinates) not in (2,3):raise PreparationError('invalid direct portrait position length')
            return tuple(coordinates)
        kind,literal=take()
        if key in ('card_id','id'):
            if kind!='number' or re.fullmatch('[1-9][0-9]*',literal) is None:
                raise PreparationError('invalid direct portrait identity')
            return literal
        if kind!='string' or '\\' in literal:raise PreparationError('unsupported direct portrait prefab')
        return literal[1:-1]
    def table(depth,root=False):
        if depth>32:raise PreparationError('BaseFashion nesting limit')
        take('{');keys=set();count=0;fields={}
        while peek()!='}':
            key=None
            if peek()=='[':
                take('[');kind,key=take();take(']');take('=')
                if kind not in ('number','string'):raise PreparationError('invalid table key')
                if root and (kind!='number' or re.fullmatch('[1-9][0-9]*',key) is None):raise PreparationError('invalid BaseFashion entry key')
                if kind=='string':
                    if '\\' in key:raise PreparationError('unsupported escaped table field')
                    key=key[1:-1]
            elif look[0][0]=='name' and peek(1)=='=':
                _,key=take();take('=')
                if root:raise PreparationError('BaseFashion entry key required')
            elif root:raise PreparationError('BaseFashion keyed entries required')
            if key is not None:
                if key in keys:raise PreparationError('duplicate BaseFashion field')
                keys.add(key)
            if root and peek()!='{':raise PreparationError('BaseFashion entry table required')
            if root:
                root_fields.append((key,table(depth+1)))
            elif depth==1 and key in ('card_id','id','show_spine','show_spine_position'):
                fields[key]=direct_field(key)
            else:value(depth)
            count+=1
            if root and count>10000:raise PreparationError('BaseFashion entry limit')
            if peek() in (',',';'):take()
            elif peek()!='}':raise PreparationError('missing BaseFashion separator')
        take('}')
        if root and not count:raise PreparationError('empty BaseFashion table')
        return fields
    returned=peek()=='return'
    if returned:take('return')
    else:
        if peek()=='local':take('local')
        take('BaseFashion');take('=')
    table(0,root=True)
    if peek()==';':take(';')
    if not returned and peek()=='return':
        take('return');take('BaseFashion')
        if peek()==';':take(';')
    if look[0][0]!='eof':raise PreparationError('trailing BaseFashion content')
    return root_fields


@dataclass(frozen=True)
class SourceRuntime:
    python: Path
    server_root: Path
    tool_root: Path
    dependency_roots: tuple[Path, ...]
    java: Path
    unluac: Path
    opmap: Path
    prlimit: Path
    # Relative tool paths and absolute runtime paths, each with a deployment SHA.
    tool_manifest: tuple[tuple[str,str], ...]
    runtime_manifest: tuple[tuple[str,str], ...]
    decrypt_key_hex: str
    environment: tuple[tuple[str,str], ...] = ()

@dataclass(frozen=True)
class PreparedSourceRef:
    schema: str
    instance_id: str
    source_id: str
    version: str
    pin_digest: str
    tool_fingerprint: str
    capture: io.MetadataRef
    snapshot_digest: str
    assigned: int
    available: int
    blocked: int
    unassigned: int


def _sha(path):
    with Path(path).open('rb') as stream: return hashlib.file_digest(stream,'sha256').hexdigest()


def _encoded(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')


def _load(data):
    return json.loads(data.decode('utf-8'),object_pairs_hook=io._pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(PreparationError('nonfinite JSON')))


def _bounded(path, cap):
    with io._directory(path.parent) as directory, io._read_file(directory,path.name) as stream:
        info=os.fstat(stream.fileno())
        if info.st_nlink!=1 or not 0<info.st_size<=cap: raise PreparationError('invalid bounded output')
        data=stream.read(cap+1)
    if not 0<len(data)<=cap: raise PreparationError('invalid bounded output')
    return data


def select_owner(tree):
    bundles,assets=_catalog(tree)
    selected=[a for a in assets if a['full_path'].casefold()=='assets/lua/basefashion.lua.bytes']
    if len(selected)!=1: raise PreparationError('require unique exact BaseFashion owner')
    asset=selected[0]; owner=dict(bundles[asset['bundle']])
    owner['asset']=asset
    return owner


def _stage_failure(message, stage_name, log_root):
    if stage_name is None:
        return PreparationError(message)
    if stage_name not in ('source', 'lua_decode', 'download', 'arts', 'package', 'render'):
        raise BoundaryError('invalid diagnostic stage')
    diagnostic = stage_name + ': ' + message
    if log_root is not None:
        try:
            with io._directory(log_root) as directory, io._read_file(directory, 'stderr.log') as stream:
                raw = stream.read(4096).decode('utf-8', errors='replace')
        except OSError:
            diagnostic += '; stderr unavailable'
        else:
            # Child text may contain raw input, paths, URLs or secrets. Retain
            # only fixed exception-class tokens; redact every dynamic detail.
            allowed = ('ValueError', 'TypeError', 'OSError', 'RuntimeError',
                       'MemoryError', 'TimeoutError', 'PreparationError',
                       'BoundaryError', 'ImportError', 'FileNotFoundError')
            found = [name for name in allowed if re.search(r'\b' + name + r'\b', raw)]
            if raw:
                diagnostic += '; stderr: ' + ','.join(found) + ' [details redacted]'
    return PreparationError(diagnostic[:512])


def check_stage(stage, *, worker=True, stage_name=None, log_root=None):
    if stage.reason=='timeout': raise _stage_failure('stage timeout', stage_name, log_root)
    if stage.reason=='descendants': raise BoundaryError('stage descendants')
    if stage.reason!='exit': raise Deferred(stage.reason)
    if not worker and stage.returncode!=0: raise _stage_failure('decoder stage failed', stage_name, log_root)
    if stage.returncode==2: raise Deferred('worker deferred')
    if stage.returncode==3: raise BoundaryError('worker boundary unavailable')
    if stage.returncode!=0: raise _stage_failure('stage failed', stage_name, log_root)


@_typed_errors(BoundaryError, 'trusted runtime unavailable or malformed')
def _runtime(runtime, tool_fingerprint):
    if type(runtime) is not SourceRuntime: raise BoundaryError('trusted runtime required')
    roots=(runtime.server_root,runtime.tool_root,*runtime.dependency_roots)
    executables=(runtime.python,runtime.java,runtime.prlimit)
    files=(*executables,runtime.unluac,runtime.opmap)
    for path in (*roots,*files):
        if not isinstance(path,Path) or not path.is_absolute() or '..' in path.parts:
            raise BoundaryError('absolute trusted runtime paths required')
    if any(not p.is_dir() for p in roots) or any(not p.is_file() for p in files):
        raise BoundaryError('runtime unavailable')
    if any(not os.access(p,os.X_OK) for p in executables): raise BoundaryError('runtime not executable')
    manifest=dict(runtime.runtime_manifest)
    if not all(str(p) in manifest for p in files): raise BoundaryError('runtime identity missing')
    for path,sha in runtime.runtime_manifest:
        io._hex(sha,64)
        if not Path(path).is_absolute() or _sha(path)!=sha: raise BoundaryError('runtime identity mismatch')
    tool=dict(runtime.tool_manifest)
    if not tool or len(tool)!=len(runtime.tool_manifest) or digest(tool)!=tool_fingerprint:
        raise BoundaryError('render bundle identity mismatch')
    for name,sha in tool.items():
        io.validate_remote_name(name); io._hex(sha,64)
        if _sha(runtime.tool_root/name)!=sha: raise BoundaryError('render bundle file mismatch')
    env=dict(runtime.environment)
    if any(k.startswith(('PYTHON','XL_PORTRAIT_RESOURCE_LOCK')) for k in env):
        raise BoundaryError('reserved runtime environment')
    env.update(PYTHONPATH=os.pathsep.join(map(str,roots)),PYTHONDONTWRITEBYTECODE='1')
    return env


@_typed_errors(PreparationError, 'invalid source preparation data or output')
def prepare_source(slot,pin,*,data_root,work_root,runtime,tool_fingerprint,open_remote,stop_event,timeout_seconds=360):
    """Capture verified inputs only. All children share the caller's live lease."""
    if any(m.split('.')[0] in ('UnityPy','PIL','PySide6','PyQt5','numpy') for m in sys.modules):
        raise BoundaryError('heavy parent import')
    if type(open_remote) is not DeadlineTransport:raise BoundaryError('deadline transport required')
    open_remote.validate()
    io._validate_pin(pin)
    if any(len(s)>256 or any(ord(c)<32 or 0xd800<=ord(c)<=0xdfff for c in s) for s in (pin.instance_id,pin.source_id)):
        raise PreparationError('worker incompatible scope')
    if type(timeout_seconds) not in (int,float) or not math.isfinite(timeout_seconds) or timeout_seconds<=0:
        raise PreparationError('finite deadline required')
    deadline=time.monotonic()+timeout_seconds
    if type(slot) is not ResourceSlot: raise BoundaryError('caller ResourceSlot required')
    slot._require_owner()
    if slot.fd is None or slot.process is not None or slot.poisoned: raise BoundaryError('active idle lease required')
    try:os.fstat(slot.fd)
    except OSError as exc:raise BoundaryError('resource lease unavailable') from exc
    verify_resource_boundary(slot.policy)
    def guard():
        if stop_event is not None and stop_event.is_set(): raise Deferred('stop')
        if time.monotonic()>=deadline: raise PreparationError('preparation timeout')
        check_admission(slot.policy,running=True)
    guard()
    env=_runtime(runtime,tool_fingerprint)
    data_root=Path(data_root); work_root=Path(work_root)
    if not data_root.is_absolute() or not work_root.is_absolute(): raise BoundaryError('absolute storage required')
    def store(data):
        guard()
        return io.store_metadata(data,data_root=data_root,scope_key=pin.scope_key,pin_digest=pin.pin_digest)
    def budget():
        guard()
        return min(open_remote.quantum_seconds,deadline-time.monotonic())
    def transport_call(function,*args):
        try:result=function(*args,timeout=budget())
        except TimeoutError as exc:
            guard()  # Cancellation/update wins over transport timeout.
            raise PreparationError('source transport timeout') from exc
        guard();return result
    class Guarded:
        def __init__(self,handle):self.handle=handle
        def read(self,n):return transport_call(open_remote.read,self.handle,n)
    @contextmanager
    def remote(name):
        # Do not call guard after open until its handle is inside try/finally.
        handle=None
        try:
            try:handle=open_remote.open(name,timeout=budget())
            except TimeoutError as exc:
                guard();raise PreparationError('source transport open timeout') from exc
            guard();yield Guarded(handle)
        finally:
            if handle is not None:
                active=sys.exc_info()[1]
                try:open_remote.close(handle,timeout=max(0,min(open_remote.quantum_seconds,deadline-time.monotonic())))
                except Exception as close_error:
                    # Keep source/resource outcome, while exposing a sanitized close
                    # diagnostic. Never turn an observed retryable stop into bad data.
                    diagnostic=PreparationError('source transport close failed: '+type(close_error).__name__)
                    if isinstance(active,(PreparationError,Deferred,BoundaryError)):
                        raise active from diagnostic
                    try:guard()
                    except (PreparationError,Deferred,BoundaryError) as outcome:
                        raise outcome from diagnostic
                    if isinstance(close_error,(Deferred,BoundaryError)):raise
                    raise PreparationError('source transport close failed') from diagnostic
        guard()
    def acquire(name,md5,size,provenance,selection_ref):
        guard()
        from .source_transfer import acquire_authorized
        blob=acquire_authorized(pin,remote_name=name,wire_md5=md5,size=size,provenance=provenance,selection_ref=selection_ref,cache_root=data_root/'wire',open_remote=remote)
        guard()
        view=io.normalize_bundle(blob,cache_root=data_root/'wire',output_root=data_root/'normalized')
        guard(); return blob,view
    def stage(argv,area,log, *, worker=True):
        guard()
        result=slot.run_stage(argv,cwd=area,env=env,timeout=min(slot.policy.stage_seconds,deadline-time.monotonic()),log_root=log,stop_event=stop_event)
        check_stage(result,worker=worker,stage_name='source' if worker else 'lua_decode',log_root=log); guard()
    @contextmanager
    def attempt_directory(prefix, parent):
        # No TemporaryDirectory finalizer: an unconfirmed live child retains inputs.
        path=Path(tempfile.mkdtemp(prefix=prefix,dir=parent))
        try: yield path
        finally:
            if not slot.poisoned and slot.process is None: shutil.rmtree(path)
    metadata={}; blobs={}
    metadata['version']=asdict(store(pin.raw_version_bytes))
    with attempt_directory('source-prepare-',work_root) as temp:
        area=Path(temp)
        def worker(view,mode,selection):
            with attempt_directory('worker-',area) as child:
                attempt=Path(child); (attempt/'work').mkdir(mode=0o700)
                policy=asdict(slot.policy)
                policy['lock_path']=str(policy['lock_path'])
                for k in ('updater_status_paths','disk_roots'):policy[k]=list(map(str,policy[k]))
                request=dict(schema_version=1,operation_id=digest([attempt.name,pin.pin_digest,mode,selection]),instance_id=pin.instance_id,source_id=pin.source_id,scope_key=pin.scope_key,version=pin.version,pin_digest=pin.pin_digest,mode=mode,normalized_root=str(data_root/'normalized'),attempt_root=str(attempt),work_root=str(attempt/'work'),descriptor=asdict(view),policy=policy,decrypt_key_hex=runtime.decrypt_key_hex)
                request['category' if mode=='category' else 'exact_name']=selection
                validate_request(request)
                request_path=area/(attempt.name+'.request.json')
                with request_path.open('xb') as f:os.chmod(request_path,0o600);f.write(_encoded(request))
                try:
                    stage([str(runtime.python),'-m','server_app.portraits.source_worker','--request',str(request_path),'--result',str(attempt/'result.json')],area,area/(attempt.name+'-logs'))
                    result=_load(_bounded(attempt/'result.json',65536))
                    if result.get('schema_version')!=1 or result.get('status')!='ok' or result.get('mode')!=mode or result.get('selection')!=selection or any(result.get(k)!=request[k] for k in IDENTITY):raise PreparationError('worker result identity mismatch')
                    output=result['output']; name='category.json' if mode=='category' else 'BaseFashion.lua'
                    if output['relative_path']!=name:raise PreparationError('worker output path mismatch')
                    data=_bounded(attempt/name,32*1024**2 if mode=='category' else CAP)
                    if type(output['size']) is not int or len(data)!=output['size'] or hashlib.sha256(data).hexdigest()!=output['sha256']:raise PreparationError('worker output mismatch')
                    if result['evidence']['source_sha256']!=view.sha256 or result['evidence']['wire_sha256']!=view.wire.sha256:raise PreparationError('worker evidence mismatch')
                    return store(data)
                finally:
                    if not slot.poisoned and slot.process is None:request_path.unlink(missing_ok=True)
        # Keep Arts on disk through every heavy phase.
        for category in pin.categories:
            blob,view=acquire(f'{category.name.lower()}_{category.hash}.json',category.hash,category.size,'category',pin.pin_digest)
            blobs[category.name]=asdict(blob)
            metadata[category.name]=asdict(worker(view,'category',category.name))
        tree=_load(io.read_metadata(io.MetadataRef(**metadata['Data']),data_root=data_root))
        owner=select_owner(tree); del tree; gc.collect()
        blob,view=acquire(owner['wire_md5']+'.bundle',owner['wire_md5'],owner['size'],'Dataowner',digest([metadata['Data'],owner]))
        blobs['owner']=asdict(blob)
        raw_ref=worker(view,'textasset','BaseFashion.lua'); metadata['raw']=asdict(raw_ref)
        raw=io.read_metadata(raw_ref,data_root=data_root)
        kind,fixed=classify_lua_bytes(raw)
        if kind=='unknown':
            _validate_basefashion(raw.decode('utf-8'))
            kind,fixed='plaintext',raw
        fixed_sha=hashlib.sha256(fixed).hexdigest()
        if kind=='bytecode':
            binary=area/'binary'; decoded=area/'decoded'; binary.mkdir(mode=0o700); decoded.mkdir(mode=0o700)
            (binary/'BaseFashion.lua').write_bytes(fixed)
            stage([str(runtime.prlimit),'--fsize=16777216:16777216','--',str(runtime.java),*JVM_OPTIONS,'-jar',str(runtime.unluac),str(binary),'--output',str(decoded),'--opmap',str(runtime.opmap)],area,area/'java-logs',worker=False)
            decoded_bytes=_bounded(decoded/'BaseFashion.lua',CAP)
        else: decoded_bytes=fixed
        text=decode_chinese(decoded_bytes.decode('utf-8')).replace('\r\n','\n').replace('\r','\n')
        root_fields=_validate_basefashion(text)
        canonical=text.encode('utf-8')
        if not 0<len(canonical)<=CAP:raise PreparationError('decoded size')
        decoded_ref=store(canonical); metadata['decoded']=asdict(decoded_ref)
        decoder=dict(raw_sha256=raw_ref.sha256,fixed_sha256=fixed_sha,decoded_sha256=decoded_ref.sha256,header_sha256=hashlib.sha256(FIXED_HEAD).hexdigest(),algorithm_sha256=hashlib.sha256((inspect.getsource(classify_lua_bytes)+inspect.getsource(decode_chinese)).encode()).hexdigest(),runtime=dict(runtime.runtime_manifest),jvm_options=list(JVM_OPTIONS))
        guard()
        try:parser=importlib.import_module('app.features.preview.portrait_config.lua_config')
        except ImportError as exc:raise BoundaryError('trusted Lua parser unavailable') from exc
        if runtime.tool_root.resolve() not in Path(parser.__file__).resolve().parents:raise BoundaryError('Lua parser origin mismatch')
        if any(m.split('.')[0] in ('UnityPy','PIL','PySide6','PyQt5','numpy') for m in sys.modules):raise BoundaryError('heavy parent import')
        layout=area/'lua'/pin.version; layout.mkdir(parents=True); (layout/'BaseFashion.lua').write_bytes(canonical)
        parsed=parser.load_portrait_roots(layout.parent,int(pin.version))
        if [item.skin_id for item in parsed]!=[key for key,_ in root_fields]:raise PreparationError('BaseFashion parsed root identity mismatch')
        for item,(_,direct) in zip(parsed,root_fields):
            actual=dict(item.metadata)
            if (actual.get('card_id'),actual.get('entry_id'),item.prefab_name,item.position)!=(direct.get('card_id'),direct.get('id'),direct.get('show_spine'),direct.get('show_spine_position')):
                raise PreparationError('BaseFashion direct field semantics mismatch')
        records=[]
        for item in parsed:
            fields=dict(item.metadata)
            if 'card_id' not in fields and not (item.prefab_name or '').casefold().startswith('cardspine_'):continue
            records.append(dict(skin_id=item.skin_id,character_id=fields.get('card_id'),entry_id=fields.get('entry_id'),prefab_name=item.prefab_name,position=list(item.position) if item.position else None,unresolved_reason=item.unresolved_reason,version_timestamp=item.version_timestamp,evidence=decoded_ref.locator+'#'+item.skin_id))
        if not records:raise PreparationError('no meaningful BaseFashion roots')
        manifest=dict(instance_id=pin.instance_id,source_id=pin.source_id,version=pin.version,pin_digest=pin.pin_digest,version_file=metadata['version']['locator'],version_sha256=pin.version_sha256,categories={k:dict(name=k,wire_md5=blobs[k]['wire_md5'],size=blobs[k]['size'],sha256=blobs[k]['sha256'],locator='wire/'+blobs[k]['locator'],extracted=metadata[k]) for k in ('Data','Arts')},config=dict(wire_md5=owner['wire_md5'],decoded_sha256=decoded_ref.sha256,decoder=digest(decoder),decoder_identity=decoder,locator=decoded_ref.locator,raw=metadata['raw'],owner=owner))
        arts=_load(io.read_metadata(io.MetadataRef(**metadata['Arts']),data_root=data_root))
        document=build_input_snapshot(records,arts,version=pin.version,instance_id=pin.instance_id,source_id=pin.source_id,source_manifest=manifest,tool_fingerprint=tool_fingerprint)
        metadata['manifest']=asdict(store(_encoded(manifest)))
        capture=store(_encoded(dict(schema='portrait-prepared-v1',metadata=metadata,blobs=blobs,document=document)))
        rows=document['catalog']['skins']
        ref=PreparedSourceRef('portrait-prepared-v1',pin.instance_id,pin.source_id,pin.version,pin.pin_digest,tool_fingerprint,capture,document['source_snapshot']['snapshot_digest'],len(rows),sum(r['state']=='not_generated' for r in rows),sum(r['state']=='blocked' for r in rows),len(document['source_snapshot']['unassigned_roots']))
        read_prepared_source(ref,data_root=data_root); guard(); return ref


@_typed_errors(PreparationError, 'invalid prepared source capture')
def read_prepared_source(ref,*,data_root):
    """Verify all required files and digest bindings without consulting latest state."""
    if type(ref) is not PreparedSourceRef or ref.schema!='portrait-prepared-v1':raise PreparationError('invalid prepared reference')
    scope=digest([ref.instance_id,ref.source_id])
    if (ref.capture.scope_key,ref.capture.pin_digest)!=(scope,ref.pin_digest):raise PreparationError('capture scope mismatch')
    capture=_load(io.read_metadata(ref.capture,data_root=data_root))
    if capture['schema']!=ref.schema:raise PreparationError('capture schema mismatch')
    if set(capture)!= {'schema','metadata','blobs','document'} or set(capture['metadata'])!= {'version','Data','Arts','raw','decoded','manifest'} or set(capture['blobs'])!= {'Data','Arts','owner'}:
        raise PreparationError('incomplete capture')
    for value in capture['metadata'].values():
        metadata=io.MetadataRef(**value)
        if (metadata.scope_key,metadata.pin_digest)!=(scope,ref.pin_digest):raise PreparationError('metadata scope mismatch')
        io.read_metadata(metadata,data_root=data_root)
    document=capture['document']; snapshot=dict(document['source_snapshot'])
    expected=snapshot.pop('snapshot_digest')
    if digest(snapshot)!=expected or expected!=ref.snapshot_digest:raise PreparationError('snapshot digest mismatch')
    for k in ('instance_id','source_id','version'):
        if snapshot[k]!=getattr(ref,k) or document['catalog'][k]!=getattr(ref,k):raise PreparationError('snapshot scope mismatch')
    manifest=snapshot['source_manifest']; metadata=capture['metadata']
    stored_manifest=_load(io.read_metadata(io.MetadataRef(**metadata['manifest']),data_root=data_root))
    if stored_manifest!=manifest or manifest['pin_digest']!=ref.pin_digest:
        raise PreparationError('manifest binding mismatch')
    if manifest['version_file']!=metadata['version']['locator'] or manifest['version_sha256']!=metadata['version']['sha256']:
        raise PreparationError('version binding mismatch')
    config=manifest['config']
    if config['raw']!=metadata['raw'] or config['locator']!=metadata['decoded']['locator'] or config['decoded_sha256']!=metadata['decoded']['sha256'] or config['wire_md5']!=capture['blobs']['owner']['wire_md5'] or digest(config['decoder_identity'])!=config['decoder']:
        raise PreparationError('config binding mismatch')
    for key in ('Data','Arts'):
        category=manifest['categories'][key]; blob=capture['blobs'][key]
        if category['extracted']!=metadata[key] or category['locator']!='wire/'+blob['locator'] or any(category[k]!=blob[k] for k in ('size','wire_md5','sha256')):
            raise PreparationError('category binding mismatch')
    validate_catalog(document['catalog'])
    if any(r['tool_fingerprint']!=ref.tool_fingerprint for r in document['catalog']['skins']):raise PreparationError('tool mismatch')
    for b in capture['blobs'].values():
        if (b['scope_key'],b['pin_digest'])!=(scope,ref.pin_digest):raise PreparationError('wire scope mismatch')
        relative=f'{scope}/{ref.pin_digest}'
        name=digest([b['remote_name'],b['wire_md5'],b['size']])+'.blob'
        if b['locator']!=relative+'/'+name:raise PreparationError('wire locator mismatch')
        with io._directory(Path(data_root)/'wire',relative) as directory,io._read_file(directory,name) as stream:
            io._check(stream,b['size'],b['wire_md5'],b['sha256'])
    rows=document['catalog']['skins']
    counts=(len(rows),sum(r['state']=='not_generated' for r in rows),sum(r['state']=='blocked' for r in rows),len(document['source_snapshot']['unassigned_roots']))
    if counts!=(ref.assigned,ref.available,ref.blocked,ref.unassigned):raise PreparationError('capture counters mismatch')
    return document
