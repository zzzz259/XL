import importlib
import tempfile
import unittest
from pathlib import Path

class PreparationTests(unittest.TestCase):
    def test_composition_api_exists(self):
        self.assertIsNotNone(importlib.util.find_spec('server_app.portraits.source_prepare'), 'missing composition API')

    def test_metadata_is_scoped_immutable_and_tamper_checked(self):
        from server_app.portraits.source_io import store_metadata, read_metadata
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            ref = store_metadata(b'original\r\n', data_root=root, scope_key='a'*64, pin_digest='b'*64)
            self.assertEqual(read_metadata(ref, data_root=root), b'original\r\n')
            before = sorted(str(p) for p in root.rglob('*'))
            self.assertEqual(ref, store_metadata(b'original\r\n', data_root=root, scope_key='a'*64, pin_digest='b'*64))
            self.assertEqual(before, sorted(str(p) for p in root.rglob('*')))
            (root/ref.locator).write_bytes(b'corrupt')
            with self.assertRaises(ValueError): read_metadata(ref, data_root=root)
            with self.assertRaises(ValueError): store_metadata(b'original\r\n', data_root=root, scope_key='a'*64, pin_digest='b'*64)

    def test_exact_owner_rejects_ambiguity_and_bad_refs(self):
        from server_app.portraits.source_prepare import select_owner
        tree = dict(dirs=['Assets/Lua'], bundles=[dict(name='logical',hash='c'*32,size=42,deps=[])], assets=[dict(name='BaseFashion.lua.bytes',dir=0,bundle=0,deps=[])])
        self.assertEqual(select_owner(tree)['wire_md5'], 'c'*32)
        tree['assets'].append(dict(name='basefashion.lua.bytes',dir=0,bundle=0,deps=[]))
        with self.assertRaises(ValueError): select_owner(tree)
        tree['assets'].pop(); tree['assets'][0]['bundle']=True
        with self.assertRaises(ValueError): select_owner(tree)

    def test_guardian_reason_precedes_exit_status(self):
        from server_app.portraits.source_prepare import check_stage, PreparationError
        from server_app.portraits.executor import Deferred, BoundaryError, StageResult
        for code,reason,error in [(0,'stop',Deferred),(0,'timeout',PreparationError),(2,'exit',Deferred),(3,'exit',BoundaryError),(1,'exit',PreparationError),(0,'descendants',BoundaryError)]:
            with self.subTest(code=code,reason=reason), self.assertRaises(error):check_stage(StageResult(code,reason))
        check_stage(StageResult(0,'exit'))


# Fake only process execution and kernel admission. Real pin, source IO, protocol,
# CAS, Lua parser and input builder run, including digest and scope validation.
import dataclasses
import hashlib
import io as bytesio
import json
import os
import sys
import threading
from unittest.mock import patch
from contextlib import ExitStack
from server_app.portraits import source_prepare as sp
from server_app.portraits import source_io as sio
from server_app.portraits.executor import ResourceSlot, ResourcePolicy, StageResult
from test_portrait_source_io_unittest import bundle

@unittest.skipUnless(sys.platform=='linux','protected Linux composition')
class CompositionBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir='/var/tmp');self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.work=self.root/'work';self.work.mkdir()
        self.server=Path(sp.__file__).resolve().parents[2]
        self.tool=self.server.parent/'xl_updata_tool'
        self.runtime=sp.SourceRuntime(Path('/usr/bin/python3'),self.server,self.tool,(),Path('/usr/bin/true'),Path('/etc/hostname'),Path('/etc/hostname'),Path('/usr/bin/prlimit'),(),(), '78'*16)
        wire=bundle(); self.md5=hashlib.md5(wire).hexdigest(); self.wire=wire
        raw=json.dumps(dict(timestamp=123,data=[dict(name=k,hash=self.md5,size=len(wire),ver=1) for k in ('Data','Arts')])).encode()
        self.pin=sio.pin_source(raw,instance_id='i',source_id='s',cdn_base='https://local.invalid',version='123',version_file='version.json')
        self.slot=ResourceSlot(ResourcePolicy(self.root/'lock',(self.root/'quiet',),(self.root,)))
        self.slot.fd=os.open(self.root/'lock',os.O_CREAT|os.O_RDWR,0o600);self.addCleanup(os.close,self.slot.fd)
        self.calls=[];self.mutation=None;self.java_data=b'local BaseFashion = { [1] = {id=1,card_id=1,show_spine="cardspine_1.prefab",show_spine_position={1,2,3}}, }'
        self.raw=b'\x1bLua'+b'original';self.stage_result=None
        self.real_runtime=sp._runtime
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        for name in ('check_admission','verify_resource_boundary'):self.stack.enter_context(patch.object(sp,name))
        self.stack.enter_context(patch.object(sp,'_runtime',return_value={'PYTHONPATH':str(self.tool)}))
        self.stack.enter_context(patch.object(self.slot,'run_stage',side_effect=self.stage))

    def stage(self,command,**kw):
        self.calls.append(command)
        self.assertLessEqual(kw['timeout'],120)
        if self.stage_result:return self.stage_result
        if command[0]==str(self.runtime.prlimit):
            self.assertEqual(command[:4],[str(self.runtime.prlimit),'--fsize=16777216:16777216','--',str(self.runtime.java)])
            self.assertEqual(command[4:9],list(sp.JVM_OPTIONS))
            self.assertEqual(len(self.calls),4)
            (Path(command[command.index('--output')+1])/'BaseFashion.lua').write_bytes(self.java_data)
            return StageResult(0,'exit')
        self.assertEqual(command[1:3],['-m','server_app.portraits.source_worker'])
        request=json.loads(Path(command[4]).read_text());result_path=Path(command[6]);attempt=result_path.parent
        self.assertEqual(set(p.name for p in attempt.iterdir()),{'work'})
        self.assertEqual(Path(command[4]).stat().st_mode&0o777,0o600)
        selection=request.get('category',request.get('exact_name'))
        if selection=='Data':
            data=sp._encoded(dict(dirs=['Assets/Lua'],bundles=[dict(name='lua',hash=self.md5,size=len(self.wire),deps=[])],assets=[dict(name='BaseFashion.lua.bytes',dir=0,bundle=0,deps=[])]))
        elif selection=='Arts':
            data=sp._encoded(dict(dirs=['Assets'],bundles=[dict(name='portrait',hash=self.md5,size=50,deps=[])],assets=[dict(name='cardspine_1.prefab',dir=0,bundle=0,deps=[])]))
        else:data=self.raw
        name='BaseFashion.lua' if selection=='BaseFashion.lua' else 'category.json'
        (attempt/name).write_bytes(data)
        result=dict(schema_version=1,status='ok',**{k:request[k] for k in sp.IDENTITY},mode=request['mode'],selection=selection,output=dict(relative_path=name,size=len(data),sha256=hashlib.sha256(data).hexdigest()),evidence=dict(source_sha256=request['descriptor']['sha256'],wire_sha256=request['descriptor']['wire']['sha256']))
        if self.mutation:self.mutation(result,attempt)
        result_path.write_bytes(sp._encoded(result))
        return StageResult(0,'exit')

    def prepare(self, *, transport=None, timeout_seconds=360):
        if transport is None:transport=sp.DeadlineTransport(lambda name,**kw:bytesio.BytesIO(self.wire),lambda stream,n,**kw:stream.read(n),lambda stream,**kw:stream.close())
        return sp.prepare_source(self.slot,self.pin,data_root=self.root/'data',work_root=self.work,runtime=self.runtime,tool_fingerprint='a'*64,open_remote=transport,stop_event=threading.Event(),timeout_seconds=timeout_seconds)

    def test_complete_capture_retry_and_no_global_mutation(self):
        before=(list(sys.path),dict(os.environ))
        ref=self.prepare(); document=sp.read_prepared_source(ref,data_root=self.root/'data')
        self.assertEqual((ref.assigned,ref.available,ref.blocked,ref.unassigned),(1,1,0,0))
        self.assertEqual(document['source_snapshot']['skins'][0]['root']['position'],[1,2,3])
        self.assertEqual((list(sys.path),dict(os.environ)),before)
        self.assertFalse(list(self.work.iterdir()))
        self.calls=[];self.assertEqual(self.prepare(),ref)
        with self.assertRaises(ValueError):sp.read_prepared_source(dataclasses.replace(ref,version='124'),data_root=self.root/'data')
        with self.assertRaises(ValueError):sp.read_prepared_source(dataclasses.replace(ref,snapshot_digest='b'*64),data_root=self.root/'data')
        (self.root/'data'/ref.capture.locator).write_bytes(b'corrupt')
        with self.assertRaises(ValueError):sp.read_prepared_source(ref,data_root=self.root/'data')

    def test_result_identity_path_hash_and_alias_refused(self):
        for change in [lambda r,p:r.update(operation_id='0'*64),lambda r,p:r.update(selection='wrong'),lambda r,p:r['output'].update(relative_path='../escape'),lambda r,p:r['output'].update(sha256='0'*64),lambda r,p:os.link(p/'category.json',p/'alias')]:
            with self.subTest(change=change):
                self.calls=[];self.mutation=change
                with self.assertRaises(ValueError):self.prepare()
                self.assertEqual(len(self.calls),1)
                self.assertFalse(list(self.work.iterdir()))

    def test_stage_failure_has_no_next_phase(self):
        for result,error in [(StageResult(2,'exit'),sp.Deferred),(StageResult(3,'exit'),sp.BoundaryError),(StageResult(0,'stop'),sp.Deferred),(StageResult(0,'timeout'),sp.PreparationError)]:
            self.calls=[];self.stage_result=result
            with self.subTest(result=result),self.assertRaises(error):self.prepare()
            self.assertEqual(len(self.calls),1)

    def test_bad_decode_cannot_capture(self):
        for data in (b'',b'\xff',b'local arbitrary = {}'):
            self.calls=[];self.java_data=data
            with self.subTest(data=data),self.assertRaises(ValueError):self.prepare()
            self.assertEqual(len(self.calls),4)
            self.assertFalse(list(self.work.iterdir()))

    def test_unknown_lua_stops_before_java(self):
        self.raw=b'not lua'
        with self.assertRaises(ValueError):self.prepare()
        self.assertEqual(len(self.calls),3)

    def test_capture_requires_complete_bound_metadata(self):
        ref=self.prepare()
        capture=sp._load(sio.read_metadata(ref.capture,data_root=self.root/'data'))
        capture['metadata'].pop('raw')
        changed=sio.store_metadata(sp._encoded(capture),data_root=self.root/'data',scope_key=self.pin.scope_key,pin_digest=self.pin.pin_digest)
        with self.assertRaises(ValueError):sp.read_prepared_source(dataclasses.replace(ref,capture=changed),data_root=self.root/'data')

    def test_capture_manifest_must_match_snapshot(self):
        ref=self.prepare()
        capture=sp._load(sio.read_metadata(ref.capture,data_root=self.root/'data'))
        bogus=sio.store_metadata(b'{}',data_root=self.root/'data',scope_key=self.pin.scope_key,pin_digest=self.pin.pin_digest)
        capture['metadata']['manifest']=dataclasses.asdict(bogus)
        changed=sio.store_metadata(sp._encoded(capture),data_root=self.root/'data',scope_key=self.pin.scope_key,pin_digest=self.pin.pin_digest)
        with self.assertRaises(ValueError):sp.read_prepared_source(dataclasses.replace(ref,capture=changed),data_root=self.root/'data')

    def test_java_nonzero_is_permanent_not_worker_deferred(self):
        original=self.stage
        def failed_java(command,**kw):
            if command[0]==str(self.runtime.prlimit):return StageResult(2,'exit')
            return original(command,**kw)
        with patch.object(self.slot,'run_stage',side_effect=failed_java),self.assertRaises(sp.PreparationError):self.prepare()

    def test_plain_basefashion_assignment_bypasses_java(self):
        self.raw=self.java_data.removeprefix(b'local ')
        ref=self.prepare()
        self.assertEqual(ref.assigned,1)
        self.assertEqual(len(self.calls),3)

    def test_heavy_parent_is_refused_before_source_work(self):
        with patch.dict(sys.modules, {'UnityPy': object()}),self.assertRaises(sp.BoundaryError):self.prepare()
        self.assertEqual(self.calls, [])

    def test_unconfirmed_child_cleanup_retains_owned_attempt(self):
        from server_app.portraits.executor import CleanupError
        def fail(command,**kw):
            self.slot.poisoned=True
            self.request_path=Path(command[4])
            raise CleanupError('unconfirmed reap')
        with patch.object(self.slot,'run_stage',side_effect=fail),self.assertRaises(CleanupError):self.prepare()
        self.assertTrue(self.request_path.is_file())
        request=json.loads(self.request_path.read_text())
        self.assertTrue(Path(request['attempt_root']).is_dir())

    def test_rejects_wrong_truncated_and_trailing_lua_tables(self):
        good=self.java_data
        for data in (good.replace(b'BaseFashion',b'NotBaseFashion'),good[:-1]+b'[2]={id=2,',good+b' Other = {[99]={card_id=99}}',good[:-1]+b'[2]={id=2,broken=} }'):
            self.calls=[];self.java_data=data
            with self.subTest(data=data[-70:]),self.assertRaises(sp.PreparationError):self.prepare()

    def test_source_errors_are_exact_preparation_error(self):
        for case in ('utf8','missing_output','bad_result'):
            self.calls=[]
            if case=='utf8':self.java_data=b'\xff'
            elif case=='missing_output':self.mutation=lambda r,p:(p/'category.json').unlink()
            else:self.mutation=lambda r,p:r.pop('output')
            try:self.prepare()
            except Exception as exc:self.assertIs(type(exc),sp.PreparationError)
            else:self.fail('malformed source accepted')

    def test_missing_runtime_is_exact_boundary_error(self):
        runtime=dataclasses.replace(self.runtime,runtime_manifest=tuple((str(p),'a'*64) for p in (self.runtime.python,self.runtime.java,self.runtime.prlimit,self.runtime.unluac,self.runtime.opmap)))
        with patch.object(sp,'_sha',side_effect=FileNotFoundError('private path')):
            try:self.real_runtime(runtime,'a'*64)
            except Exception as exc:self.assertIs(type(exc),sp.BoundaryError)
            else:self.fail('missing runtime accepted')

    def test_legacy_transport_rejected_before_source_work(self):
        with self.assertRaises(sp.BoundaryError):self.prepare(transport=lambda _:bytesio.BytesIO(self.wire))
        self.assertEqual(self.calls,[])

    def test_transport_open_and_read_deadline_no_threads(self):
        import time
        for phase in ('open','read'):
            self.calls=[]; bounds=[]; handles=[]
            def opened(name,*,timeout):
                bounds.append(timeout)
                if phase=='open':time.sleep(timeout);raise TimeoutError('open deadline')
                stream=bytesio.BytesIO(self.wire);handles.append(stream);return stream
            def read(stream,n,*,timeout):
                bounds.append(timeout);time.sleep(timeout);raise TimeoutError('read deadline')
            transport=sp.DeadlineTransport(opened,read,lambda stream,**kw:stream.close())
            threads=threading.enumerate();start=time.monotonic()
            with self.assertRaises(sp.PreparationError):
                self.prepare(transport=transport,timeout_seconds=.05)
            self.assertLess(time.monotonic()-start,.5)
            self.assertTrue(all(0<t<=.05 for t in bounds))
            self.assertEqual(threading.enumerate(),threads)
            self.assertTrue(all(h.closed for h in handles))
            self.assertEqual(self.calls,[])

    def test_supported_root_forms_and_nested_entry_text(self):
        body=self.java_data.removeprefix(b'local BaseFashion = ')
        for source in (b'return '+body,b'local BaseFashion = '+body+b'; return BaseFashion;',b'-- ignored [99]={}\nBaseFashion = '+body):
            self.calls=[];self.raw=source
            ref=self.prepare()
            self.assertEqual(ref.assigned,1)
        # Nested keyed tables must not become additional parser roots.
        self.calls=[];self.raw=self.java_data.replace(b'id=1,',b'id=1, nested={[99]={text="[98]={"}},',1)
        self.assertEqual(self.prepare().assigned,1)

    def test_transport_cancellation_releases_open_handle(self):
        import time
        event=threading.Event(); handles=[]
        def opened(name,*,timeout):
            stream=bytesio.BytesIO(self.wire);handles.append(stream);event.set();return stream
        transport=sp.DeadlineTransport(opened,lambda stream,n,**kw:stream.read(n),lambda stream,**kw:stream.close())
        start=time.monotonic()
        with self.assertRaises(sp.Deferred):
            sp.prepare_source(self.slot,self.pin,data_root=self.root/'data',work_root=self.work,runtime=self.runtime,tool_fingerprint='a'*64,open_remote=transport,stop_event=event,timeout_seconds=1)
        self.assertLess(time.monotonic()-start,.5)
        self.assertTrue(handles[0].closed)
        self.assertEqual(self.calls,[])

    def test_capture_invalid_json_is_exact_preparation_error(self):
        ref=self.prepare()
        bad=sio.store_metadata(b'{',data_root=self.root/'data',scope_key=self.pin.scope_key,pin_digest=self.pin.pin_digest)
        try:sp.read_prepared_source(dataclasses.replace(ref,capture=bad),data_root=self.root/'data')
        except Exception as exc:self.assertIs(type(exc),sp.PreparationError)
        else:self.fail('malformed capture accepted')

    def test_unclosed_long_comment_is_not_a_valid_root(self):
        self.java_data=b'--[[ unfinished comment\n'+self.java_data
        with self.assertRaises(sp.PreparationError):sp._validate_basefashion(self.java_data.decode())
        with self.assertRaises(sp.PreparationError):self.prepare()

    def test_nested_and_quoted_fields_cannot_substitute_direct_semantics(self):
        for shadow in (b'nested={card_id=999},',b'nested={id=999},',b'nested={show_spine="cardspine_999.prefab"},',b'nested={show_spine_position={9,8,7}},',b'note="card_id=999",',b"note='show_spine=\"cardspine_999.prefab\"',"):
            self.calls=[]
            self.raw=self.java_data.replace(b'{id=1,',b'{'+shadow+b'id=1,')
            with self.subTest(shadow=shadow),self.assertRaises(sp.PreparationError):self.prepare()

    def test_transport_close_failure_preserves_resource_exception(self):
        from server_app.portraits.executor import CleanupError
        for phase,error,close_error in (('open',sp.Deferred('stop'),TimeoutError),('read',sp.Deferred('update'),OSError),('read',sp.BoundaryError('boundary'),TimeoutError),('read',CleanupError('cleanup'),OSError)):
            self.calls=[]; event=threading.Event();handles=[]
            def opened(name,*,timeout):
                stream=bytesio.BytesIO(self.wire);handles.append(stream)
                if phase=='open':event.set()
                return stream
            def read(stream,n,*,timeout):raise error
            def close(stream,*,timeout):
                stream.close();raise close_error('private details')
            transport=sp.DeadlineTransport(opened,read,close)
            try:sp.prepare_source(self.slot,self.pin,data_root=self.root/'data',work_root=self.work,runtime=self.runtime,tool_fingerprint='a'*64,open_remote=transport,stop_event=event)
            except Exception as exc:
                self.assertIs(type(exc),type(error))
                self.assertNotIn('private details',str(exc))
            else:self.fail('resource failure accepted')
            self.assertTrue(handles[0].closed)
            self.assertEqual(self.calls,[])

    def test_close_only_failure_checks_cancellation_priority(self):
        event=threading.Event();handles=[]
        def opened(name,*,timeout):
            stream=bytesio.BytesIO(self.wire);handles.append(stream);return stream
        def close(stream,*,timeout):
            stream.close();event.set();raise TimeoutError('private close')
        transport=sp.DeadlineTransport(opened,lambda stream,n,**kw:stream.read(n),close)
        try:sp.prepare_source(self.slot,self.pin,data_root=self.root/'data',work_root=self.work,runtime=self.runtime,tool_fingerprint='a'*64,open_remote=transport,stop_event=event)
        except Exception as exc:self.assertIs(type(exc),sp.Deferred)
        else:self.fail('stop accepted')
        self.assertTrue(handles[0].closed)

if __name__ == '__main__': unittest.main()
