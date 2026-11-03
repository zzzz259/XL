import dataclasses, hashlib, io, json, struct, tempfile, unittest, os, subprocess, sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from server_app import cdn
try:
    from server_app.portraits import source_io as sio
except ImportError:
    sio = None

def md5(b): return hashlib.md5(b).hexdigest()
def sha(b): return hashlib.sha256(b).hexdigest()
def raw(**changes):
    obj=dict(timestamp=123,data=[dict(name=k,hash=md5(k.encode()),size=len(k),ver=8) for k in ('Data','Arts')]);obj.update(changes)
    return json.dumps(obj,indent=2).encode()
def pin(b=None,**changes):
    args=dict(instance_id='instance/unsafe',source_id='source',cdn_base='https://cdn.example/Android/Bundles',version='123',version_file='versions_vA162.D409.O169.V6_123.json');args.update(changes)
    return sio.pin_source(raw() if b is None else b,**args)
def bundle(prefix=b'',fmt=8,payload=b'a'*64):
    h=b'UnityFS\0'+struct.pack('>I',fmt)+b'5.x.x\0'+b'2021.3.32f1c1\0'
    return prefix+h+struct.pack('>QIII',len(h)+20+len(payload),4,8,0x643)+payload

class SourceTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(sio,'source_io interface missing')
        self.tmp=tempfile.TemporaryDirectory(dir=Path.cwd());self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'wire';self.out=Path(self.tmp.name)/'views'
    def acquire(self,data,p=None):
        return sio.acquire_blob(p or pin(),remote_name=md5(data)+'.bundle',wire_md5=md5(data),size=len(data),cache_root=self.root,open_remote=lambda n:io.BytesIO(data))
    def test_pin_exact_raw_immutable_scope(self):
        b=raw();p=pin(b);self.assertEqual(p.raw_version_bytes,b);self.assertEqual(p.version_sha256,sha(b))
        self.assertNotEqual(p.version_sha256,pin(json.dumps(json.loads(b)).encode()).version_sha256)
        self.assertNotEqual(p.scope_key,pin(source_id='other').scope_key)
        self.assertEqual([c.name for c in p.categories],['Data','Arts']);self.assertEqual(p.categories[0].version,8)
        with self.assertRaises(dataclasses.FrozenInstanceError): p.version='9'
    def test_pin_rejections(self):
        for b in (b'{"timestamp":123,"timestamp":123}',b'[]',b'{',raw(timestamp=True),raw(timestamp=124),raw(data=[]),raw(data=json.loads(raw())['data']*2),raw(data=[dict(name='Data',hash='A'*32,size=4,ver=8)]),b' '*(8*1024*1024+1)):
            with self.subTest(b=b[:60]),self.assertRaises(ValueError):pin(b)
        for args in (dict(version='01'),dict(version=True),dict(cdn_base='http://cdn.example'),dict(cdn_base='https://a@cdn.example'),dict(version_file='../a'),dict(version_file='a?b'),dict(version_file='a\\b')):
            with self.subTest(args=args),self.assertRaises(ValueError):pin(**args)
    def test_blob_verified_hit_scope_bounded_reads(self):
        data=b'z'*(2*1024*1024+3)
        class Stream(io.BytesIO):
            def read(self,n=-1):
                if not 0<n<=1024*1024:raise AssertionError(n)
                return super().read(n)
        p=pin();d=sio.acquire_blob(p,remote_name=md5(data)+'.bundle',wire_md5=md5(data),size=len(data),cache_root=self.root,open_remote=lambda n:Stream(data))
        self.assertEqual((d.sha256,d.wire_md5,d.size,d.pin_digest),(sha(data),md5(data),len(data),p.pin_digest));self.assertEqual((self.root/d.locator).read_bytes(),data)
        with patch.object(io,'BytesIO',side_effect=AssertionError('no download')):self.assertEqual(self.acquire(data),d)
        self.assertNotEqual(self.acquire(data,pin(source_id='other')).locator,d.locator)
    def test_failures_no_final_retry_no_overwrite(self):
        data=b'payload';p=pin();h=md5(data)
        for content in (data[:-1],data+b'x',b'x'*len(data)):
            with self.assertRaises(ValueError):sio.acquire_blob(p,remote_name=h+'.bundle',wire_md5=h,size=len(data),cache_root=self.root,open_remote=lambda n:io.BytesIO(content))
        class Broken(io.BytesIO):
            def read(self,n):raise OSError('interrupted')
        with self.assertRaises(OSError):sio.acquire_blob(p,remote_name=h+'.bundle',wire_md5=h,size=len(data),cache_root=self.root,open_remote=lambda n:Broken())
        self.assertFalse(list(self.root.rglob('*.blob')))
        d=self.acquire(data);path=self.root/d.locator;path.write_bytes(b'corrupt')
        with self.assertRaises(ValueError):self.acquire(data)
        self.assertEqual(path.read_bytes(),b'corrupt')
    def test_identity_policy_category_binding(self):
        p=pin();name='data_'+md5(b'Data')+'.json'
        d=sio.acquire_blob(p,remote_name=name,wire_md5=md5(b'Data'),size=4,cache_root=self.root,open_remote=lambda n:io.BytesIO(b'Data'));self.assertEqual(d.remote_name,name)
        for n,h,size in (('../x','a'*32,1),('other.json','a'*32,1),('a'*32+'.bundle','b'*32,1),(name,md5(b'Data'),5),('a'*32+'.bundle','a'*32,True),('a'*32+'.bundle','a'*32,129*1024*1024)):
            with self.assertRaises(ValueError):sio.acquire_blob(p,remote_name=n,wire_md5=h,size=size,cache_root=self.root,open_remote=lambda n:self.fail('transport called'))
    def test_concurrent_idempotent(self):
        with ThreadPoolExecutor(max_workers=4) as pool:rows=list(pool.map(lambda _:self.acquire(b'x'*123456),range(8)))
        self.assertTrue(all(d==rows[0] for d in rows))
    def test_symlink_rejected(self):
        target=Path(self.tmp.name)/'real';target.mkdir()
        try:self.root.symlink_to(target,target_is_directory=True)
        except OSError as exc:
            if os.name != 'nt' or exc.winerror != 1314: raise
            # Standard and controlled runs lack SeCreateSymbolicLinkPrivilege.
            # Junction is the available real directory reparse-point attack fixture.
            result=subprocess.run(['cmd','/c','mklink','/J',str(self.root),str(target)],capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertTrue(self.root.is_junction())
        with self.assertRaises((ValueError,OSError)):self.acquire(b'x')
        self.assertEqual(list(target.iterdir()),[])
    def test_normalization_offsets_copy_truthful(self):
        for prefix in (b'',b'P'*32):
            data=bundle(prefix);d=self.acquire(data);n=sio.normalize_bundle(d,cache_root=self.root,output_root=self.out)
            self.assertEqual((n.prefix_offset,n.size,n.sha256,n.format_version,n.flags),(len(prefix),len(data)-len(prefix),sha(data[len(prefix):]),8,0x643));self.assertEqual(n.wire,d);self.assertEqual((n.player_version,n.engine_version),('5.x.x','2021.3.32f1c1'))
            self.assertEqual((self.root/d.locator).read_bytes(),data);self.assertEqual((self.out/n.locator).read_bytes(),data[len(prefix):])
            self.assertEqual(sio.normalize_bundle(d,cache_root=self.root,output_root=self.out),n)
    def test_bad_header_tamper(self):
        valid=bundle();bad=bytearray(valid);off=valid.index(b'2021.3.32f1c1\0')+len(b'2021.3.32f1c1\0');bad[off:off+8]=struct.pack('>Q',9999)
        for data in (b'UnityFS\0',bundle(fmt=5),bundle(payload=b'UnityFS\0'),bytes(bad),b'Q'*4096+valid,b'UnityWeb\0bad',valid[:20]):
            d=self.acquire(data)
            with self.subTest(data=data[:24]),self.assertRaises(ValueError):sio.normalize_bundle(d,cache_root=self.root,output_root=self.out)
        d=self.acquire(valid);(self.root/d.locator).write_bytes(b'x'*len(valid))
        with self.assertRaises(ValueError):sio.normalize_bundle(d,cache_root=self.root,output_root=self.out)
    def test_info_abuse(self):
        data=bytearray(bundle());off=data.index(b'2021.3.32f1c1\0')+len(b'2021.3.32f1c1\0')
        for field,val in ((8,0),(8,2**32-1),(12,2**32-1)):
            bad=bytearray(data);bad[off+field:off+field+4]=struct.pack('>I',val);d=self.acquire(bytes(bad))
            with self.assertRaises(ValueError):sio.normalize_bundle(d,cache_root=self.root,output_root=self.out)

    def test_existing_nonregular_targets_and_forged_locator(self):
        d=self.acquire(b'abc');path=self.root/d.locator
        path.unlink();path.mkdir()
        with self.assertRaises((ValueError,OSError)):self.acquire(b'abc')
        with self.assertRaises(ValueError):sio.normalize_bundle(dataclasses.replace(d,locator='../escape'),cache_root=self.root,output_root=self.out)
    def test_normalized_conflict_is_not_overwritten(self):
        d=self.acquire(bundle());n=sio.normalize_bundle(d,cache_root=self.root,output_root=self.out)
        target=self.out/n.locator;target.write_bytes(b'bad')
        with self.assertRaises(ValueError):sio.normalize_bundle(d,cache_root=self.root,output_root=self.out)
        self.assertEqual(target.read_bytes(),b'bad')
    @unittest.skipUnless(os.name == 'nt','Windows sharing contract')
    def test_partial_cannot_be_modified_while_acquiring(self):
        data=b'wire'
        def transport(name):
            partial=next(self.root.rglob('.partial-*'))
            with self.assertRaises(OSError):
                with partial.open('wb') as evil:evil.write(b'bad')
            return io.BytesIO(data)
        sio.acquire_blob(pin(),remote_name=md5(data)+'.bundle',wire_md5=md5(data),size=len(data),cache_root=self.root,open_remote=transport)
    def test_directory_sync_failure_prevents_publication(self):
        with patch.object(sio,'_sync_directory',create=True,side_effect=OSError('sync failed')):
            with self.assertRaises(OSError):self.acquire(b'sync bytes')
        self.assertFalse(list(self.root.rglob('*.blob')))
    def test_existing_parser_view_must_not_alias_wire_inode(self):
        data=bundle();d=self.acquire(data)
        target=self.out/d.scope_key/d.pin_digest/(sha(data)+'.bundle')
        target.parent.mkdir(parents=True);os.link(self.root/d.locator,target)
        with self.assertRaises(ValueError):sio.normalize_bundle(d,cache_root=self.root,output_root=self.out)
    def test_bad_category_types_and_independent_pins(self):
        rows=json.loads(raw())['data']
        for field,value in (('size',True),('size',0),('size',-1),('ver',True),('hash','A'*32)):
            changed=[dict(r) for r in rows];changed[0][field]=value
            with self.assertRaises(ValueError):pin(raw(data=changed))
        self.assertEqual(pin(),pin());self.assertIsNot(pin().categories,pin().categories)

    def test_fresh_import_rejects_heavy_dependencies(self):
        code = """import importlib.abc,sys
class Block(importlib.abc.MetaPathFinder):
 def find_spec(self,fullname,path=None,target=None):
  if fullname.split('.')[0] in ('UnityPy','PIL','PySide6'):
   raise RuntimeError('heavy dependency imported: '+fullname)
sys.meta_path.insert(0,Block())
from server_app.portraits.source_io import pin_source,acquire_blob,normalize_bundle
print('stdlib-only source import OK')
"""
        result=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
    def test_supported_formats_and_nonconstant_prefix(self):
        for fmt in (6,7,8):
            data=bundle(b'prefix!',fmt);d=self.acquire(data)
            n=sio.normalize_bundle(d,cache_root=self.root,output_root=self.out)
            self.assertEqual((n.prefix_offset,n.format_version),(7,fmt))

    def test_successful_retries_and_races_leave_only_final_storage(self):
        data=bundle(payload=b'x'*(1024*1024));d=self.acquire(data)
        prior=self.out/'.partial-previous-attempt';prior.parent.mkdir(parents=True);prior.write_bytes(b'previous attempt')
        rows=[];final_identity=None;wire_identity=(self.root/d.locator).stat().st_ino
        for _ in range(3):
            rows.append(sio.normalize_bundle(d,cache_root=self.root,output_root=self.out))
            self.assertEqual(list(self.out.rglob('.partial-*')),[prior])
            current=(self.out/rows[0].locator).stat()
            identity=(current.st_dev,current.st_ino,current.st_size)
            if final_identity is None:final_identity=identity
            self.assertEqual(identity,final_identity)
            self.assertEqual(sum(p.stat().st_size for p in self.out.rglob('*.bundle')),rows[0].size)
        with ThreadPoolExecutor(max_workers=4) as pool:
            rows+=list(pool.map(lambda _:sio.normalize_bundle(d,cache_root=self.root,output_root=self.out),range(8)))
        self.assertTrue(all(r==rows[0] for r in rows))
        self.assertEqual(list(self.out.rglob('.partial-*')),[prior])
        self.assertEqual((self.root/d.locator).stat().st_ino,wire_identity)
        self.assertEqual((self.root/d.locator).read_bytes(),data)
        self.assertEqual(list(self.root.rglob('.partial-*')),[])
        finals=list(self.out.rglob('*.bundle'));self.assertEqual(len(finals),1)
        self.assertEqual(finals[0].read_bytes(),data)
        self.assertEqual(prior.read_bytes(),b'previous attempt')
        self.assertNotEqual((self.root/d.locator).stat().st_ino,finals[0].stat().st_ino)
    def test_fresh_concurrent_writers_retire_only_owned_successful_partials(self):
        data=bundle(payload=b'z'*123456);d=self.acquire(data)
        with ThreadPoolExecutor(max_workers=4) as pool:
            rows=list(pool.map(lambda _:sio.normalize_bundle(d,cache_root=self.root,output_root=self.out),range(8)))
        self.assertTrue(all(r==rows[0] for r in rows))
        self.assertEqual(list(self.out.rglob('.partial-*')),[])
        self.assertEqual(len(list(self.out.rglob('*.bundle'))),1)
        self.assertEqual(sum(p.stat().st_size for p in self.out.rglob('*') if p.is_file()),rows[0].size)
    def test_new_nested_directories_sync_parents_in_creation_order(self):
        fresh=Path(self.tmp.name)/'ancestor'/'nested'/'wire';self.root=fresh
        calls=[];real=sio._sync_directory
        def sync(directory):calls.append(directory[0]);return real(directory)
        with patch.object(sio,'_sync_directory',side_effect=sync):d=self.acquire(b'durable')
        expected=list(reversed(fresh.parents))+[fresh,fresh/d.scope_key]
        self.assertEqual(calls[:len(expected)],expected)
        self.assertTrue(all(p.exists() for p in expected))
        leaf=(fresh/d.locator).parent
        self.assertEqual(calls[-1],leaf)
    def test_required_ancestor_sync_failure_prevents_transport_and_publication(self):
        for stop in range(5):
            self.root=Path(self.tmp.name)/('attempt'+str(stop))/'nested'/'wire'
            calls=[];real=sio._sync_directory
            fail_at=len(Path(self.tmp.name).parents)+stop+1
            def sync(directory):
                calls.append(directory[0])
                if len(calls)==fail_at:raise OSError('ancestor flush failed')
                return real(directory)
            with patch.object(sio,'_sync_directory',side_effect=sync):
                with self.assertRaises(OSError):
                    sio.acquire_blob(pin(),remote_name=md5(b'x')+'.bundle',wire_md5=md5(b'x'),size=1,cache_root=self.root,open_remote=lambda n:self.fail('transport before durable parents'))
            self.assertEqual(len(calls),fail_at)
            self.assertFalse(list(Path(self.tmp.name).rglob('*.blob')))

    def test_cache_retry_requires_failed_post_install_leaf_sync_to_recover(self):
        p=pin();data=b'cache retry durability';h=md5(data)
        leaf=self.root/p.scope_key/p.pin_digest
        leaf.mkdir(parents=True);prior=leaf/'.partial-prior-attempt';prior.write_bytes(b'prior bytes')
        args=dict(remote_name=h+'.bundle',wire_md5=h,size=len(data),cache_root=self.root)
        calls=0;real=sio._sync_directory
        def first_sync(directory):
            nonlocal calls
            if directory[0]==leaf:
                calls+=1
                if calls==2:raise OSError('post-install leaf sync failed')
            return real(directory)
        with patch.object(sio,'_sync_directory',side_effect=first_sync):
            with self.assertRaisesRegex(OSError,'post-install'):
                sio.acquire_blob(p,**args,open_remote=lambda n:io.BytesIO(data))
        finals=list(leaf.glob('*.blob'));self.assertEqual(len(finals),1)
        final=finals[0];identity=(final.stat().st_dev,final.stat().st_ino)
        partials={f.name:(f.read_bytes(),f.stat().st_ino) for f in leaf.glob('.partial-*')}
        self.assertIn(prior.name,partials)
        def no_transport(name):self.fail('retained final must be reused without transport')
        def unavailable(directory):
            if directory[0]==leaf:raise OSError('leaf sync still unavailable')
            return real(directory)
        with patch.object(sio,'_sync_directory',side_effect=unavailable):
            with self.assertRaisesRegex(OSError,'still unavailable'):
                sio.acquire_blob(p,**args,open_remote=no_transport)
        synced=[]
        def recovered(directory):synced.append(directory[0]);return real(directory)
        with patch.object(sio,'_sync_directory',side_effect=recovered):
            result=sio.acquire_blob(p,**args,open_remote=no_transport)
        self.assertIn(leaf,synced)
        self.assertEqual(self.root/result.locator,final)
        self.assertEqual((final.stat().st_dev,final.stat().st_ino),identity)
        self.assertEqual(final.read_bytes(),data)
        self.assertEqual(result.sha256,sha(data))
        self.assertEqual({f.name:(f.read_bytes(),f.stat().st_ino) for f in leaf.glob('.partial-*')},partials)

class CdnTests(unittest.TestCase):
    def test_bounded_raw_safe_legacy(self):
        self.assertTrue(hasattr(cdn.CdnClient,'fetch_version_bytes'),'raw fetch interface missing')
        c=cdn.CdnClient('https://cdn.example/base')
        with patch('urllib.request.urlopen',return_value=io.BytesIO(b'12345')):self.assertEqual(c.fetch_version_bytes('versions/a.json',max_bytes=5),b'12345')
        with patch('urllib.request.urlopen',return_value=io.BytesIO(b'123456')):
            with self.assertRaises(ValueError):c.fetch_version_bytes('a.json',max_bytes=5)
        for n in ('../x','/x','a?x','a#x','a\\x','https://evil/a','a//x','a\nx'):
            with self.assertRaises(ValueError):c.fetch_version_bytes(n)
        with patch.object(c,'_get_bytes',return_value=raw()):self.assertEqual(c.fetch_version_catalog('a').timestamp,123)
