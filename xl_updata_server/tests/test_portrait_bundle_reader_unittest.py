import dataclasses
import gzip
import hashlib
import io
import json
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from types import SimpleNamespace

try:
    from server_app.portraits import bundle_reader as br
except ImportError:
    br = None

try:
    from server_app.portraits import serialized_preflight as sp
except ImportError:
    sp = None


def serialized_fixture(*, duplicate=False, metadata_one=False, negative_type=False, version=17):
    p=lambda fmt,*args:struct.pack('<'+fmt,*args)
    def text(value):
        data=p('i',len(value))+value
        return data+b'\0'*(-len(data)%4)
    payloads=[text(b'A')+text(b'first')]
    if duplicate:payloads.append(text(b'A')+text(b'second'))
    header_size=48 if version>=22 else 20
    meta=b'2021.3.32f1\0'+p('i?',19,False)+p('i',1)+p('i?h',49,False,-1)+b'\0'*16+p('i',len(payloads))
    off=0
    for data in payloads:
        meta+=b'\0'*(-(header_size+len(meta))%4)
        meta+=p('qqIi' if version>=22 else 'qIIi',42,off,len(data),-1 if negative_type else 0)
        off+=len(data)
    meta+=p('ii',0,0)+(p('i',0) if version>=20 else b'')+b'\0'
    start=(header_size+len(meta)+15)//16*16
    msize=1 if metadata_one else len(meta)
    raw=(struct.pack('>IIII',0,0,version,0)+b'\0'*4+struct.pack('>Iqqq',msize,start+off,start,0)
         if version>=22 else struct.pack('>IIII',msize,start+off,version,start)+b'\0'*4)+meta
    return raw+b'\0'*(start-len(raw))+b''.join(payloads)


def review_unityfs(node,name='CAB-review'):
    info=metadata(((len(node),len(node),0),),((0,len(node),0,name),))
    head=b'UnityFS\0'+struct.pack('>I',8)+b'5.x.x\0'+b'2021.3.32f1\0'
    start=(len(head)+20+15)//16*16
    return head+struct.pack('>QIII',start+len(info)+len(node),len(info),len(info),0x40)+b'\0'*(start-len(head)-20)+info+node


def actual_review_regressions():
    """Called by the separate capped Linux acceptance process, never test parent."""
    from server_app.portraits import source_io as sio
    from server_app.portraits.model import digest
    def run(node,extract=True,name='CAB-review'):
        raw=review_unityfs(node,name);sha=hashlib.sha256(raw).hexdigest();md5=hashlib.md5(raw).hexdigest()
        scope='a'*64;pin='b'*64;remote=md5+'.bundle';relative=scope+'/'+pin
        wire=sio.VerifiedBlob(scope,pin,remote,relative+'/'+digest([remote,md5,len(raw)])+'.blob',len(raw),md5,sha)
        view=sio.NormalizedBundle(wire,relative+'/'+sha+'.bundle',len(raw),sha,*sio._header(raw[:4096],len(raw)))
        with tempfile.TemporaryDirectory(dir='/var/tmp') as tmp:
            root=Path(tmp);path=root/view.locator;path.parent.mkdir(parents=True);path.write_bytes(raw)
            spill=root/'spill';spill.mkdir()
            try:
                with br.open_bundle(view,normalized_root=root,work_root=spill,decrypt_key=b'x'*16) as reader:
                    return reader.extract_textasset('A')['data'] if extract else 'opened'
            finally:
                assert not list(spill.iterdir()),'spill not cleaned'
                assert path.read_bytes()==raw,'source changed'
    for version in range(17,23):assert run(serialized_fixture(version=version))==b'first'
    cases=[('duplicate_pathid',serialized_fixture(duplicate=True)),('metadata_size_one',serialized_fixture(metadata_one=True)),
           ('negative_type_index',serialized_fixture(negative_type=True)),('gzip_node',gzip.compress(b'X'*1048576,mtime=0))]
    failures=[]
    for label,node in cases:
        try:run(node,extract=label!='gzip_node')
        except ValueError:pass
        else:failures.append(label)
    assert not failures,'malformed sources accepted: '+','.join(failures)
    # Even an explicitly opaque resource may not activate a nested gzip detector.
    try:run(gzip.compress(b'X'*1048576,mtime=0),extract=False,name='CAB-review.resS')
    except ValueError:pass
    else:raise AssertionError('wrapped resource accepted')
    print('actual review regression controls and rejection cases passed',flush=True)


def metadata(blocks=((8, 8, 0),), nodes=((0, 8, 0, 'CAB-test'),)):
    result = b'\0' * 16 + struct.pack('>i', len(blocks))
    for row in blocks:
        result += struct.pack('>IIH', *row)
    result += struct.pack('>i', len(nodes))
    for offset, size, flags, name in nodes:
        result += struct.pack('>qqI', offset, size, flags) + name.encode() + b'\0'
    return result


class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(br, 'bundle_reader interface missing')

    def test_metadata_valid_and_exact(self):
        blocks, nodes = br.parse_metadata(metadata(), compressed_available=8)
        self.assertEqual(blocks, ((8, 8, 0),))
        self.assertEqual(nodes, ((0, 8, 0, 'CAB-test'),))

    def test_metadata_rejects_bad_counts_ranges_names_flags_and_budgets(self):
        bad = [b'', metadata()[:-1], metadata() + b'x', b'\0'*16 + struct.pack('>i', -1),
               metadata(blocks=((0,8,0),)), metadata(blocks=((8,8,1),)),
               metadata(blocks=((8,8,0x200),)), metadata(blocks=((8,8,0x100),)),
               metadata(nodes=((-1,8,0,'CAB'),)), metadata(nodes=((1,8,0,'CAB'),)),
               metadata(nodes=((0,4,0,'CAB'),(3,4,0,'other'))),
               metadata(nodes=((0,4,0,'CAB'),(4,4,0,'CAB')))]
        for name in ('../CAB', '/CAB', 'a/b', 'a\\b', 'C:x', '.', 'a\n'):
            bad.append(metadata(nodes=((0,8,0,name),)))
        for data in bad:
            with self.subTest(data=data), self.assertRaises(ValueError):
                br.parse_metadata(data, compressed_available=8)
        with self.assertRaises(ValueError):
            br.parse_metadata(metadata(), compressed_available=7)
        with self.assertRaises(ValueError):
            br.parse_metadata(metadata(), compressed_available=8, limits=br.ReaderLimits(expanded_bytes=7))

    def test_selected_only_and_surrogate_bytes(self):
        def obj(name, value, pid):
            return SimpleNamespace(type=SimpleNamespace(name='TextAsset'), path_id=pid,
                byte_size=100, assets_file=SimpleNamespace(name='CAB-test'),
                peek_name=lambda:name, read=value)
        skipped = obj('Other.lua', lambda:self.fail('unselected payload read'), 1)
        selected = obj('BaseFashion.lua', lambda:SimpleNamespace(m_Script='a\udcff'), 2)
        result = br.select_textasset([skipped, selected], 'BaseFashion.lua')
        self.assertEqual(result['data'], b'a\xff')
        self.assertEqual(result['path_id'], 2)
        self.assertEqual(result['sha256'], hashlib.sha256(b'a\xff').hexdigest())
        for values in ([skipped], [selected,selected]):
            with self.assertRaises(ValueError):br.select_textasset(values,'BaseFashion.lua')

    def test_parent_import_has_no_heavy_modules(self):
        code = "import sys; from server_app.portraits import bundle_reader; assert not any(n.split('.')[0] in ('UnityPy','PIL','PySide6','app') for n in sys.modules)"
        subprocess.run([sys.executable,'-c',code],check=True)

    def test_source_digest_and_locator_rejected_before_heavy_import(self):
        from server_app.portraits import source_io as sio
        self.assertTrue(hasattr(sio,'open_normalized_bundle'),'public verified normalized opener missing')
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            root=Path(tmp); wire=sio.VerifiedBlob('a'*64,'b'*64,'c'*32+'.bundle','unused',100,'c'*32,'d'*64)
            locator='a'*64+'/'+'b'*64+'/'+'e'*64+'.bundle'
            d=sio.NormalizedBundle(wire,locator,3,'e'*64,0,8,'2021.3.32f1','5.x.x',1,1,0x40)
            path=root/locator;path.parent.mkdir(parents=True);path.write_bytes(b'bad')
            for invalid in (d,dataclasses.replace(d,locator='../escape')):
                with self.assertRaises(ValueError):
                    with br.open_bundle(invalid,normalized_root=root,work_root=root,decrypt_key=b'x'*16):self.fail('opened')
            self.assertNotIn('UnityPy',sys.modules)

    def test_category_exact_local_pointer_and_full_metadata(self):
        tree=dict(m_Name='Data',dirs=['Assets/Lua'],bundles=[dict(name='MixedCase',hash='a'*32,size=4,deps=[])],
                  assets=[dict(name='BaseFashion.lua.bytes',dir=0,bundle=0,deps=[])],extra='retained')
        target=SimpleNamespace(peek_name=lambda:'Data',byte_size=50,read_typetree=lambda:tree,
                               assets_file=SimpleNamespace(name='CAB-test'),path_id=8)
        ptr=SimpleNamespace(m_FileID=0,m_PathID=8,deref=lambda:target)
        obj=SimpleNamespace(type=SimpleNamespace(name='AssetBundle'),byte_size=50,
            read=lambda:SimpleNamespace(m_Container=[('data',SimpleNamespace(asset=ptr))]))
        source=SimpleNamespace(sha256='a'*64,wire=SimpleNamespace(sha256='b'*64))
        reader=br.BundleReader(SimpleNamespace(get_objects=lambda:[obj]),source,br.ReaderLimits())
        self.assertEqual(reader.read_category('Data')['metadata'],tree)
        obj.read=lambda:SimpleNamespace(m_Container=[('data',SimpleNamespace(asset=ptr)),('alias',SimpleNamespace(asset=ptr))])
        try: result=reader.read_category('Data')
        except ValueError as exc: self.fail('same physical category aliases rejected: '+str(exc))
        self.assertEqual(result['containers'],['alias','data'])
        second=SimpleNamespace(**vars(target));second.path_id=9
        ptr2=SimpleNamespace(m_FileID=0,m_PathID=9,deref=lambda:second)
        obj.read=lambda:SimpleNamespace(m_Container=[('data',SimpleNamespace(asset=ptr)),('other',SimpleNamespace(asset=ptr2))])
        with self.assertRaises(ValueError):reader.read_category('Data')
        obj.read=lambda:SimpleNamespace(m_Container=[('data',SimpleNamespace(asset=ptr))])
        ptr.m_FileID=1
        with self.assertRaises(ValueError):reader.read_category('Data')
        ptr.m_FileID=0
        target.peek_name=lambda:'Arts'
        with self.assertRaises(ValueError):reader.read_category('Data')

    def test_selected_size_checked_before_read(self):
        obj=SimpleNamespace(type=SimpleNamespace(name='TextAsset'),peek_name=lambda:'a',byte_size=100,
                            read=lambda:self.fail('oversized payload read'))
        with self.assertRaises(ValueError):br.select_textasset([obj],'a',limits=br.ReaderLimits(selected_bytes=2))

    def test_closed_reader_rejects_operations(self):
        reader=br.BundleReader(None,None,br.ReaderLimits())
        self.assertTrue(hasattr(reader,'close'),'reader lifetime contract missing')
        reader.close()
        with self.assertRaises(ValueError):reader.extract_textasset('a')
        with self.assertRaises(ValueError):reader.read_category('Data')

    def test_limits_and_metadata_policy(self):
        for name in br.ReaderLimits.__dataclass_fields__:
            for value in (0,-1,True,float('nan'),'4',10**20):
                with self.subTest(name=name,value=value),self.assertRaises(ValueError):br.ReaderLimits(**{name:value})
        for blocks,nodes in (((),((0,8,0,'CAB'),)),(((8,8,0),),()),(((9,8,0),),((0,8,0,'CAB'),))):
            with self.assertRaises(ValueError):br.parse_metadata(metadata(blocks,nodes),compressed_available=8)
        self.assertEqual(br.parse_metadata(metadata(blocks=((8,8,0x102),)),compressed_available=8,encrypted=True)[0],((8,8,0x102),))

    def test_public_source_open_rechecks_bytes_and_header(self):
        from server_app.portraits import source_io as sio
        raw=json.dumps(dict(timestamp=123,data=[dict(name=k,hash='a'*32,size=1,ver=1) for k in ('Data','Arts')])).encode()
        pin=sio.pin_source(raw,instance_id='test',source_id='source',cdn_base='https://cdn.example',version='123',version_file='v.json')
        head=b'UnityFS\0'+struct.pack('>I',8)+b'5.x.x\0'+b'2021.3.32f1\0'
        blob=head+struct.pack('>QIII',len(head)+20+64,4,8,0x40)+b'x'*64
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            root=Path(tmp);md5=hashlib.md5(blob).hexdigest()
            wire=sio.acquire_blob(pin,remote_name=md5+'.bundle',wire_md5=md5,size=len(blob),cache_root=root/'wire',open_remote=lambda _:io.BytesIO(blob))
            view=sio.normalize_bundle(wire,cache_root=root/'wire',output_root=root/'views')
            with sio.open_normalized_bundle(view,normalized_root=root/'views') as stream:
                self.assertEqual(stream.read(),blob)
            self.assertTrue(stream.closed)
            for invalid in (dataclasses.replace(view,flags=0),dataclasses.replace(view,format_version=8.0),dataclasses.replace(view,prefix_offset=True),dataclasses.replace(view,wire=dataclasses.replace(wire,locator='bad'))):
                with self.assertRaises(ValueError):
                    with sio.open_normalized_bundle(invalid,normalized_root=root/'views'):self.fail('accepted')
            (root/'views'/view.locator).write_bytes(b'x'*len(blob))
            with self.assertRaises(ValueError):
                with sio.open_normalized_bundle(view,normalized_root=root/'views'):self.fail('accepted')

    def test_header_policy_rejects_before_decompression(self):
        self.assertTrue(hasattr(br,'validate_header'),'header policy interface missing')
        d=SimpleNamespace(engine_version='2021.3.32f1',flags=0x643,info_compressed_size=100,info_uncompressed_size=100)
        br.validate_header(d,br.ReaderLimits())
        for key,value in (('engine_version','unknown'),('flags',0x644),('flags',0x641),('flags',0x8000),('flags',0),('info_uncompressed_size',20*1024**2)):
            bad=SimpleNamespace(**vars(d));setattr(bad,key,value)
            with self.assertRaises(ValueError):br.validate_header(bad,br.ReaderLimits())


class SerializedPreflightTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(sp,'serialized preflight interface missing')

    def check(self,data,name='CAB-review'):
        return sp.classify_node(io.BytesIO(data),name,limits=br.ReaderLimits())

    def test_supported_controls_and_opaque_resource(self):
        for version in range(17,23):
            self.assertEqual(self.check(serialized_fixture(version=version)),'serialized')
        self.assertEqual(self.check(b'opaque pixels','CAB-review.resS'),'resource')

    def test_review_malformed_sources(self):
        for data in (serialized_fixture(duplicate=True),serialized_fixture(metadata_one=True),serialized_fixture(negative_type=True)):
            with self.subTest(data=data[:20]),self.assertRaises(ValueError):self.check(data)
        for name in ('CAB-review','CAB-review.resS'):
            with self.assertRaises(ValueError):self.check(gzip.compress(b'X'*1048576,mtime=0),name)

    def test_unsupported_headers_and_truncated_metadata(self):
        good=serialized_fixture()
        for offset,value in ((0,2**32-1),(4,len(good)-1),(8,16),(8,23),(12,20)):
            bad=bytearray(good);struct.pack_into('>I',bad,offset,value)
            with self.assertRaises(ValueError):self.check(bytes(bad))
        for data in (b'',good[:19],b'UnityFS\0'+b'0'*120,b'PK\x03\x04'+b'0'*120):
            with self.assertRaises(ValueError):self.check(data)

    def test_table_counts_indices_and_object_ranges(self):
        good=serialized_fixture()
        type_count=20+len(b'2021.3.32f1\0')+5
        obj_count=type_count+4+23
        row=(obj_count+4+3)&~3
        scripts=row+20
        for offset,fmt,values in ((type_count,'i',(-1,100001)),(obj_count,'i',(-1,100001)),
            (row+8,'I',(0xffffffff,)),(row+12,'I',(0xffffffff,0)),(row+16,'i',(-1,1)),
            (scripts,'i',(-1,)),(scripts+4,'i',(-1,)),(type_count+4+5,'h',(-2,0))):
            for value in values:
                bad=bytearray(good);struct.pack_into('<'+fmt,bad,offset,value)
                with self.subTest(offset=offset,value=value),self.assertRaises(ValueError):self.check(bytes(bad))

    def test_type_tree_record_and_string_bounds(self):
        def tree(version=17,*,count=1,strings=b'TextAsset\0Base\0',depth=0,offset=0):
            p=lambda fmt,*values:struct.pack('<'+fmt,*values)
            header_size=48 if version>=22 else 20
            node=p('hBBIIiii',1,depth,0,offset,10,-1,0,0)+(p('Q',0) if version>=19 else b'')
            meta=b'2021.3.32f1\0'+p('i?i',19,True,1)+p('i?h',49,False,-1)+b'\0'*16
            meta+=p('ii',count,len(strings))+node+strings
            if version>=21:meta+=p('i',0)  # type dependency list
            meta+=p('iii',0,0,0)+(p('i',0) if version>=20 else b'')+b'\0'
            end=(header_size+len(meta)+15)&~15
            header=(struct.pack('>IIII',0,0,version,0)+b'\0'*4+struct.pack('>Iqqq',len(meta),end,end,0)
                    if version>=22 else struct.pack('>IIII',len(meta),end,version,end)+b'\0'*4)
            return header+meta+b'\0'*(end-header_size-len(meta))
        for version in range(17,23):self.assertEqual(self.check(tree(version)),'serialized')
        for kwargs in (dict(count=-1),dict(count=0),dict(count=100001),dict(strings=b'no terminator'),dict(depth=1),dict(offset=999)):
            with self.subTest(kwargs=kwargs),self.assertRaises(ValueError):self.check(tree(**kwargs))

    def test_classifier_never_reads_opaque_resource_body(self):
        class HeaderOnly(io.BytesIO):
            def read(self,n=-1):
                if not 0<=n<=48:raise AssertionError('unbounded resource read')
                return super().read(n)
        self.assertEqual(sp.classify_node(HeaderOnly(b'X'*1024**2),'CAB.resS',limits=br.ReaderLimits()),'resource')
        for header in (b'\x1f\x8b',b'PK\x03\x04',b'UnityWeb',b'\0'*32+b'brotli'):
            with self.assertRaises(ValueError):sp.classify_node(HeaderOnly(header+b'X'*100),'CAB.resS',limits=br.ReaderLimits())


if __name__ == '__main__':
    unittest.main()
