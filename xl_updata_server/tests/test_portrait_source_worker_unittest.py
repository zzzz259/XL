import copy
import dataclasses
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from contextlib import contextmanager
from types import SimpleNamespace

from server_app.portraits import source_io as sio
from server_app.portraits.executor import ResourcePolicy, Deferred, BoundaryError
from server_app.portraits.model import digest
try:
    from server_app.portraits import source_worker as sw
except ImportError:
    sw = None


def request_for(root):
    scope = digest(['instance', 'source'])
    sha = 'a'*64
    md5 = 'b'*32
    pin = 'c'*64
    remote = md5+'.bundle'
    wire = sio.VerifiedBlob(scope,pin,remote,scope+'/'+pin+'/'+digest([remote,md5,64])+'.blob',64,md5,sha)
    view = sio.NormalizedBundle(wire,scope+'/'+pin+'/'+sha+'.bundle',64,sha,0,8,'2021.3.32f1','5.x.x',20,20,64)
    policy = ResourcePolicy(root/'lock',(root/'status',),(root,),stage_seconds=10)
    return dict(schema_version=1,operation_id='d'*64,instance_id='instance',source_id='source',scope_key=scope,
                version='123',pin_digest=pin,mode='textasset',exact_name='BaseFashion.lua',
                normalized_root=str(root/'normalized'),attempt_root=str(root/'attempt'),work_root=str(root/'attempt'/'work'),
                descriptor=dataclasses.asdict(view),policy={k: [str(x) for x in v] if type(v) is tuple else str(v) if isinstance(v,Path) else v for k,v in dataclasses.asdict(policy).items()},
                decrypt_key_hex='78'*16)


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(sw, 'protected source_worker interface missing')
        self.tmp = tempfile.TemporaryDirectory(dir='/var/tmp' if sys.platform=='linux' else Path.cwd())
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        for name in ('normalized','attempt','attempt/work'):
            (self.root/name).mkdir(mode=0o700)
        self.req = request_for(self.root)
        self.request = self.root/'request.json'
        self.result = self.root/'attempt/result.json'

    def save(self, value=None):
        self.request.write_text(json.dumps(self.req if value is None else value),encoding='utf-8')

    @unittest.skipUnless(sys.platform=='linux','Linux hardlink contract')
    def test_hardlinked_source_rejected_before_heavy_import(self):
        source=self.root/'normalized/input';source.write_bytes(b'input');os.link(source,self.root/'alias')
        self.save()
        @contextmanager
        def opened(*args,**kwargs):
            with source.open('rb') as stream:yield stream
        with patch.object(sw,'_guard'),patch.object(sw,'_disk_free',return_value=2**40),patch.object(sw,'open_normalized_bundle',opened),patch.object(sw.importlib,'import_module',side_effect=AssertionError('heavy')):
            with self.assertRaises(ValueError):sw.run_request(self.req,request_path=self.request,result_path=self.result)
        self.assertFalse(self.result.exists())
    @unittest.skipUnless(sys.platform=='linux','Linux worker boundary')
    def test_fixed_failure_exit_codes_and_canonical_paths(self):
        self.save()
        for error,code in ((Deferred('private details'),2),(BoundaryError('private details'),3),(ValueError('private details'),1)):
            with patch.object(sw,'run_request',side_effect=error):
                self.assertEqual(sw.main(['--request',str(self.request),'--result',str(self.result)]),code)
    @unittest.skipUnless(sys.platform=='linux','Linux path canonicalization')
    def test_double_slash_cannot_alias_roots(self):
        with self.assertRaises(ValueError):sw._path('//var/tmp/alias')

    def test_interface_and_import_have_no_heavy_dependencies(self):
        code = ('import sys; from server_app.portraits import source_worker; '
                'assert not any(x.split(".")[0] in ("UnityPy","PIL","PySide6") for x in sys.modules); '
                'assert not any(x == "server_app.portraits.bundle_reader" or '
                'x.startswith("server_app.portraits.bundle_reader.") for x in sys.modules)')
        self.assertEqual(subprocess.run([sys.executable,'-c',code],capture_output=True).returncode,0)

    def test_valid_category_descriptor_protocol(self):
        req=copy.deepcopy(self.req);req.pop('exact_name');req.update(mode='category',category='Data')
        wire=req['descriptor']['wire'];wire['remote_name']='data_'+wire['wire_md5']+'.json'
        wire['locator']=wire['scope_key']+'/'+wire['pin_digest']+'/'+digest([wire['remote_name'],wire['wire_md5'],wire['size']])+'.blob'
        self.assertEqual(sw.validate_request(req)['descriptor'].wire.remote_name,wire['remote_name'])

    def test_invalid_envelopes_are_rejected(self):
        changes = [('schema_version',True),('mode','shell'),('exact_name','Other.lua'),('version','01'),('scope_key','f'*64),('pin_digest','B'*64),('operation_id','short'),('decrypt_key_hex','bad'),('extra',0)]
        for key,value in changes:
            with self.subTest(key=key):
                req=copy.deepcopy(self.req);req[key]=value
                with self.assertRaises(ValueError):sw.validate_request(req)
        for key,value in [('memory_bytes',True),('admission_bytes',1),('abort_bytes',1),('disk_free_bytes',1),('poll_seconds',1),('stage_seconds',float('inf'))]:
            req=copy.deepcopy(self.req);req['policy'][key]=value
            with self.subTest(policy=key),self.assertRaises(ValueError):sw.validate_request(req)
        req=copy.deepcopy(self.req);del req['policy']['abort_bytes']
        with self.assertRaises(ValueError):sw.validate_request(req)
        for key,value in [('size',True),('locator','../x'),('wire',{})]:
            req=copy.deepcopy(self.req);req['descriptor'][key]=value
            with self.subTest(descriptor=key),self.assertRaises(ValueError):sw.validate_request(req)

    @unittest.skipUnless(sys.platform=='linux','Linux filesystem contract')
    def test_bounded_strict_request_and_no_output(self):
        for data in (b'{"a":1,"a":2}',b'{"a":NaN}',b'[]',b'\xff',b'x'*65537,b'['*1000):
            self.request.write_bytes(data)
            self.assertNotEqual(sw.main(['--request',str(self.request),'--result',str(self.result)]),0)
            self.assertFalse(self.result.exists())
        self.request.unlink();os.mkfifo(self.request)
        self.assertNotEqual(sw.main(['--request',str(self.request),'--result',str(self.result)]),0)

    @unittest.skipUnless(sys.platform=='linux','Linux filesystem contract')
    def test_no_lease_refuses_before_reader_import(self):
        self.save()
        with patch.dict(os.environ,{},clear=True),patch.object(sw.importlib,'import_module',side_effect=AssertionError('heavy import')):
            self.assertNotEqual(sw.main(['--request',str(self.request),'--result',str(self.result)]),0)
        self.assertFalse(self.result.exists())

    @unittest.skipUnless(sys.platform=='linux','Linux filesystem contract')
    def test_unsafe_attempt_or_stale_result_refuses_without_guard(self):
        self.save()
        self.result.write_bytes(b'prior')
        with patch.object(sw,'_guard',side_effect=AssertionError('guard should not run')):
            self.assertNotEqual(sw.main(['--request',str(self.request),'--result',str(self.result)]),0)
        self.assertEqual(self.result.read_bytes(),b'prior')
        self.result.unlink();(self.root/'attempt/work').rmdir();(self.root/'attempt/work').symlink_to(self.root/'normalized',target_is_directory=True)
        self.assertNotEqual(sw.main(['--request',str(self.request),'--result',str(self.result)]),0)

    @unittest.skipUnless(sys.platform=='linux','Linux filesystem contract')
    def test_guard_failure_never_imports_or_creates_output(self):
        self.save()
        for error in ('update','memory','disk','unbounded','lease'):
            with patch.object(sw,'_guard',side_effect=ValueError(error)),patch.object(sw.importlib,'import_module',side_effect=AssertionError('heavy')):
                self.assertNotEqual(sw.main(['--request',str(self.request),'--result',str(self.result)]),0)
            self.assertEqual(list((self.root/'attempt').iterdir()),[self.root/'attempt/work'])

    @unittest.skipUnless(sys.platform=='linux','Linux filesystem contract')
    def test_exact_output_bytes_identity_and_category_preservation(self):
        events=[]
        @contextmanager
        def opened(descriptor,**kwargs):
            events.append('open')
            self.assertEqual(kwargs['decrypt_key'],b'x'*16)
            yield SimpleNamespace(extract_textasset=lambda name: dict(data=b'\xff\x00raw',name=name,cab='CAB-test',path_id=42,source_sha256='a'*64,wire_sha256='a'*64,wire_md5='b'*32),
                  read_category=lambda category: dict(metadata={'m_Name':category,'dirs':['Case'],'bundles':[],'assets':[],'extra':[1]},cab='CAB-cat',path_id=43,containers=['A','B'],source_sha256='a'*64,wire_sha256='a'*64))
        fake=SimpleNamespace(open_bundle=opened,ReaderLimits=lambda **kw:SimpleNamespace(selected_bytes=16*1024**2,json_bytes=32*1024**2))
        self.save()
        with patch.object(sw,'open_normalized_bundle',side_effect=lambda *a,**kw:self.request.open('rb')),patch.object(sw,'_disk_free',return_value=2**40),patch.object(sw,'_guard',side_effect=lambda *a:events.append('guard')),patch.object(sw.importlib,'import_module',return_value=fake):
            self.assertEqual(sw.main(['--request',str(self.request),'--result',str(self.result)]),0)
        result=json.loads(self.result.read_text());output=self.root/'attempt'/result['output']['relative_path']
        self.assertEqual(events,['guard','open']);self.assertEqual(output.read_bytes(),b'\xff\x00raw')
        self.assertEqual(result['output']['sha256'],hashlib.sha256(output.read_bytes()).hexdigest())
        for key in ('operation_id','instance_id','source_id','scope_key','version','pin_digest'):self.assertEqual(result[key],self.req[key])
        self.assertEqual(result['evidence']['path_id'],42)
        self.result.unlink();output.unlink()
        self.req.pop('exact_name');self.req.update(mode='category',category='Data')
        wire=self.req['descriptor']['wire'];wire['remote_name']='data_'+wire['wire_md5']+'.json'
        wire['locator']=wire['scope_key']+'/'+wire['pin_digest']+'/'+digest([wire['remote_name'],wire['wire_md5'],wire['size']])+'.blob';self.save()
        with patch.object(sw,'open_normalized_bundle',side_effect=lambda *a,**kw:self.request.open('rb')),patch.object(sw,'_disk_free',return_value=2**40),patch.object(sw,'_guard'),patch.object(sw.importlib,'import_module',return_value=fake):
            self.assertEqual(sw.main(['--request',str(self.request),'--result',str(self.result)]),0)
        result=json.loads(self.result.read_text());tree=json.loads((self.root/'attempt/category.json').read_text())
        self.assertEqual(tree['dirs'],['Case']);self.assertEqual(tree['extra'],[1]);self.assertEqual(result['counts'],dict(dirs=1,bundles=0,assets=0))





class LeaseTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform=='linux','Linux flock contract')
    def test_live_exact_ofd_required_before_kernel_or_reader(self):
        import fcntl
        with tempfile.TemporaryDirectory(dir='/var/tmp' if sys.platform=='linux' else Path.cwd()) as tmp:
            root=Path(tmp).resolve();p=ResourcePolicy(root/'lock',(root/'status',),(root,))
            owner=os.open(p.lock_path,os.O_RDWR|os.O_CREAT,0o600);fcntl.flock(owner,fcntl.LOCK_EX)
            other=os.open(p.lock_path,os.O_RDWR)
            try:
                env={'XL_PORTRAIT_RESOURCE_LOCK_FD':str(owner),'XL_PORTRAIT_RESOURCE_LOCK_PATH':str(p.lock_path)}
                with patch.dict(os.environ,env,clear=True),patch.object(sw,'verify_resource_boundary') as boundary,patch.object(sw,'check_admission') as admission:
                    sw._guard(p);boundary.assert_called_once_with(p);admission.assert_called_once_with(p,running=True)
                for raw,path in [(str(other),str(p.lock_path)),('999999',str(p.lock_path)),(str(owner),str(root/'wrong')),('',str(p.lock_path))]:
                    with patch.dict(os.environ,{'XL_PORTRAIT_RESOURCE_LOCK_FD':raw,'XL_PORTRAIT_RESOURCE_LOCK_PATH':path},clear=True),patch.object(sw,'verify_resource_boundary',side_effect=AssertionError('too early')):
                        with self.assertRaises(BoundaryError):sw._guard(p)
                os.close(other);other=None
                fcntl.flock(owner,fcntl.LOCK_UN)
                with patch.dict(os.environ,env,clear=True),self.assertRaises(BoundaryError):sw._guard(p)
            finally:
                if other is not None:os.close(other)
                os.close(owner)


def actual_acceptance():
    """Explicit integration entry: parent and fixed child in one real capped unit."""
    from server_app.portraits.executor import ResourceSlot, verify_resource_boundary
    from test_portrait_bundle_reader_unittest import review_unityfs
    import struct
    import time
    def serialized(name=b'BaseFashion.lua',duplicate=False):
        p=lambda fmt,*a:struct.pack('<'+fmt,*a)
        def text(v):
            b=p('i',len(v))+v
            return b+b'\0'*(-len(b)%4)
        payload=text(name)+text(b'\xff\x00raw')
        payloads=[payload,payload] if duplicate else [payload]
        meta=b'2021.3.32f1\0'+p('i?',19,False)+p('i',1)+p('i?h',49,False,-1)+b'\0'*16+p('i',len(payloads))
        for index,b in enumerate(payloads):
            meta+=b'\0'*(-(20+len(meta))%4);meta+=p('qIIi',42,index*len(b),len(b),0)
        meta+=p('ii',0,0)+b'\0';start=(20+len(meta)+15)//16*16
        raw=struct.pack('>IIII',len(meta),start+sum(map(len,payloads)),17,start)+b'\0'*4+meta
        return raw+b'\0'*(start-len(raw))+b''.join(payloads)
    root=Path(tempfile.mkdtemp(prefix='portrait-worker-',dir='/var/tmp'));os.chmod(root,0o700)
    (root/'normalized').mkdir(mode=0o700)
    status=root/'status';status.write_text('{"updating":false}')
    policy=ResourcePolicy(root/'lock',(status,),(root,),stage_seconds=15)
    boundary=verify_resource_boundary(policy);group=Path(boundary.controller)
    records=[]
    print(json.dumps(dict(event='kernel',root=str(root),boundary=dataclasses.asdict(boundary),affinity=sorted(os.sched_getaffinity(0)),memory_max=(group/'memory.max').read_text().strip(),swap_max=(group/'memory.swap.max').read_text().strip(),cpu_max=(group/'cpu.max').read_text().strip())),flush=True)
    with ResourceSlot(policy) as slot:
        for case in ('valid','wrong-metadata','duplicate','env-drop','env-tamper','wrong-ofd','update-active','status-missing','low-memory','low-disk','valid-next'):
            base=root/case;base.mkdir(mode=0o700);attempt=base/'attempt';attempt.mkdir(mode=0o700);(attempt/'work').mkdir(mode=0o700)
            req=request_for(base);req['normalized_root']=str(root/'normalized')
            req['policy']={k:[str(x) for x in v] if type(v) is tuple else str(v) if isinstance(v,Path) else v for k,v in dataclasses.asdict(policy).items()}
            raw=review_unityfs(serialized(duplicate=case=='duplicate'));sha=hashlib.sha256(raw).hexdigest();md5=hashlib.md5(raw).hexdigest();scope=req['scope_key'];pin=req['pin_digest'];remote=md5+'.bundle';rel=scope+'/'+pin
            wire=sio.VerifiedBlob(scope,pin,remote,rel+'/'+digest([remote,md5,len(raw)])+'.blob',len(raw),md5,sha)
            view=sio.NormalizedBundle(wire,rel+'/'+sha+'.bundle',len(raw),sha,*sio._header(raw[:4096],len(raw)))
            path=root/'normalized'/view.locator;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
            req['descriptor']=dataclasses.asdict(view)
            if case=='update-active':
                active=base/'active-status';active.write_text('{"updating":true}');req['policy']['updater_status_paths']=[str(active)]
            if case=='status-missing':req['policy']['updater_status_paths']=[str(base/'missing-status')]
            if case=='low-memory':req['policy'].update(admission_bytes=2**60,abort_bytes=2**60)
            if case=='low-disk':req['policy']['disk_free_bytes']=2**60
            if case=='wrong-metadata':req['descriptor']['info_uncompressed_size']+=1
            request=base/'request.json';request.write_text(json.dumps(req));result=attempt/'result.json'
            # Import trap is active before fixed entry, and writes only when reader/UnityPy is attempted.
            marker=base/'heavy-imported'
            wrapper='import os,sys,runpy\nclass Trap:\n def find_spec(self,name,*args):\n  if name in ("UnityPy","server_app.portraits.bundle_reader"):\n   open('+repr(str(marker))+',"a").write(name+"\\n")\nsys.meta_path.insert(0,Trap())\n'
            if case=='env-drop':wrapper+='os.environ.pop("XL_PORTRAIT_RESOURCE_LOCK_FD",None)\n'
            if case=='env-tamper':wrapper+='os.environ["XL_PORTRAIT_RESOURCE_LOCK_PATH"]+=".wrong"\n'
            if case=='wrong-ofd':wrapper+='os.environ["XL_PORTRAIT_RESOURCE_LOCK_FD"]=str(os.open(os.environ["XL_PORTRAIT_RESOURCE_LOCK_PATH"],os.O_RDWR))\n'
            wrapper+='sys.argv=["source_worker","--request",'+repr(str(request))+',"--result",'+repr(str(result))+'];runpy.run_module("server_app.portraits.source_worker",run_name="__main__")'
            stage=slot.run_stage(['/usr/bin/python3','-c',wrapper],cwd=Path.cwd(),env=dict(os.environ),timeout=10,log_root=base/'logs')
            success=case in ('valid','valid-next')
            expected=0 if success else 2 if case in ('update-active','status-missing','low-memory','low-disk') else 3 if case in ('env-drop','env-tamper','wrong-ofd') else 1
            assert stage.returncode==expected,(case,stage)
            if success:
                got=json.loads(result.read_text());out=attempt/got['output']['relative_path']
                assert out.read_bytes()==b'\xff\x00raw'
                assert all(got[k]==req[k] for k in IDENTITY_TEST)
                assert got['output']['sha256']==hashlib.sha256(out.read_bytes()).hexdigest()
                assert got['evidence']['path_id']==42
            else:assert not result.exists()
            if case in ('env-drop','env-tamper','wrong-ofd','wrong-metadata','update-active','status-missing','low-memory','low-disk'):assert not marker.exists(),case
            assert hashlib.sha256(path.read_bytes()).hexdigest()==sha
            assert not list((attempt/'work').iterdir())
            records.append(dict(case=case,stage=dataclasses.asdict(stage),heavy_imported=marker.exists(),source_sha256=sha,result=json.loads(result.read_text()) if result.exists() else None,stderr=(base/'logs/stderr.log').read_text()))
    report=dict(root=str(root),cases=records,peak=(group/'memory.peak').read_text().strip(),events=(group/'memory.events').read_text(),swap_current=(group/'memory.swap.current').read_text().strip())
    (root/'evidence.json').write_text(json.dumps(report,sort_keys=True))
    output=Path.cwd()/'scratch/portrait-source-worker-acceptance';output.mkdir(exist_ok=True)
    with (output/'events.jsonl').open('a') as stream:stream.write(json.dumps(dict(invocation_id=os.environ.get('INVOCATION_ID'),**report),sort_keys=True)+'\n')
    print(json.dumps(report,sort_keys=True),flush=True)

IDENTITY_TEST=('operation_id','instance_id','source_id','scope_key','version','pin_digest')

if __name__=='__main__':unittest.main()


