"""Disk-backed UnityFS metadata reader, for an already bounded child process.

Importing this module is stdlib-only. Opening a bundle imports UnityPy; this is
not a standalone worker and does not acquire a resource lease. Callers must run
it inside the protected executor boundary. Only NONE/LZ4/LZ4HC are supported.
"""
from contextlib import contextmanager, ExitStack
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import tempfile

from . import source_io
from .serialized_preflight import classify_node


@dataclass(frozen=True)
class ReaderLimits:
    info_bytes: int = 4 * 1024**2
    block_bytes: int = 8 * 1024**2
    expanded_bytes: int = 128 * 1024**2
    blocks: int = 8192
    nodes: int = 1024
    objects: int = 100000
    selected_bytes: int = 16 * 1024**2
    json_bytes: int = 32 * 1024**2

    def __post_init__(self):
        caps = (4*1024**2, 8*1024**2, 128*1024**2, 8192, 1024, 100000, 16*1024**2, 32*1024**2)
        for name, cap in zip(self.__dataclass_fields__, caps):
            value = getattr(self, name)
            if type(value) is not int or not 0 < value <= cap:
                raise ValueError('invalid reader limit: ' + name)


def _name(value):
    if (type(value) is not str or not value or len(value) > 255 or value in ('.','..')
            or any(c in value for c in '/\\:') or any(ord(c) < 32 for c in value)):
        raise ValueError('unsafe node or object name')
    return value


def _compression(flags, *, allowed, encrypted=False):
    if flags & ~allowed or flags & 0x3f not in (0, 2, 3):
        raise ValueError('unsupported flags or compression')
    if flags & 0x100 and not encrypted:
        raise ValueError('encrypted block without decryptor')


def validate_header(descriptor, limits):
    match=re.fullmatch(r'([1-9][0-9]{3})\.([0-9]+)\.([0-9]+)[abfp][0-9]+(?:c[0-9]+)?',descriptor.engine_version)
    if not match: raise ValueError('unsupported engine version')
    version=tuple(map(int,match.groups()))
    old=(version<(2020,) or (version[0]==2020 and version<(2020,3,34))
         or (version[0]==2021 and version<(2021,3,2)) or (version[0]==2022 and version<(2022,1,1)))
    flags=descriptor.flags
    if type(flags) is not int or flags<0: raise ValueError('invalid flags')
    _compression(flags & ~0x100,allowed=0x3ff if old else 0x17ff,encrypted=True)
    if not flags & 0x40: raise ValueError('separate directory metadata unsupported')
    for value in (descriptor.info_compressed_size,descriptor.info_uncompressed_size):
        if type(value) is not int or not 0<value<=limits.info_bytes: raise ValueError('metadata budget')


def parse_metadata(data, *, compressed_available, limits=ReaderLimits(), encrypted=False):
    """Validate the complete decompressed block table before writing any payload."""
    if type(data) is not bytes or len(data) > limits.info_bytes:
        raise ValueError('metadata budget')
    pos = 16
    def number(fmt):
        nonlocal pos
        size = struct.calcsize(fmt)
        if pos + size > len(data): raise ValueError('truncated block table')
        result = struct.unpack_from(fmt, data, pos); pos += size
        return result
    count, = number('>i')
    if not 0 < count <= limits.blocks: raise ValueError('block count')
    blocks = []
    expanded = compressed = 0
    for _ in range(count):
        u, c, flags = number('>IIH')
        _compression(flags, allowed=0x13f, encrypted=encrypted)
        if not 0 < min(u,c) <= max(u,c) <= limits.block_bytes:
            raise ValueError('block size budget')
        if flags & 0x3f == 0 and u != c: raise ValueError('raw block size mismatch')
        expanded += u; compressed += c
        if expanded > limits.expanded_bytes or compressed > compressed_available:
            raise ValueError('cumulative block budget')
        blocks.append((u,c,flags))
    if compressed != compressed_available: raise ValueError('unaccounted compressed bytes')
    count, = number('>i')
    if not 0 < count <= limits.nodes: raise ValueError('node count')
    nodes = []; names = set()
    for _ in range(count):
        offset, size, flags = number('>qqI')
        end = data.find(b'\0',pos,min(len(data),pos+256))
        if end < 0: raise ValueError('unterminated node name')
        try: name = _name(data[pos:end].decode('utf-8'))
        except UnicodeError as exc: raise ValueError('invalid node name') from exc
        pos = end + 1
        if name.casefold() in names or offset < 0 or size <= 0 or offset + size > expanded or flags & ~7:
            raise ValueError('invalid node range, flags or duplicate name')
        names.add(name.casefold()); nodes.append((offset,size,flags,name))
    end = 0
    for offset,size,_,_ in sorted(nodes):
        if offset < end: raise ValueError('overlapping nodes')
        end = offset + size
    if pos != len(data): raise ValueError('trailing metadata')
    return tuple(blocks), tuple(nodes)


def select_textasset(objects, exact_name, *, limits=ReaderLimits()):
    _name(exact_name)
    matches = []
    for count, obj in enumerate(objects, 1):
        if count > limits.objects: raise ValueError('object count limit')
        if obj.type.name == 'TextAsset' and obj.peek_name() == exact_name:
            matches.append(obj)
    if len(matches) != 1: raise ValueError('require exactly one selected TextAsset')
    obj = matches[0]
    if not 0 < obj.byte_size <= limits.selected_bytes: raise ValueError('selected object size')
    value = obj.read().m_Script
    data = value.encode('utf-8','surrogateescape') if isinstance(value,str) else bytes(value)
    if not 0 < len(data) <= limits.selected_bytes: raise ValueError('selected payload size')
    return dict(data=data, name=exact_name, cab=obj.assets_file.name, path_id=obj.path_id,
                size=len(data), sha256=hashlib.sha256(data).hexdigest())


def validate_texture_stream(tree, cab, resources, cap):
    stream = tree.get('m_StreamData')
    if not stream or not stream.get('path'):
        return
    path, offset, size = stream.get('path'), stream.get('offset'), stream.get('size')
    name = cab + '.resS'
    if path not in (name, 'archive:/' + cab + '/' + name):
        raise ValueError('texture resource owner/path mismatch')
    reader = resources.get(name)
    if (reader is None or type(offset) is not int or type(size) is not int
            or offset < 0 or not 0 < size <= cap or offset + size > reader.Length):
        raise ValueError('texture resource range limit')


class BundleReader:
    def __init__(self, bundle, source, limits):
        self._bundle = bundle
        self.source = source
        self.limits = limits

    def close(self):
        self._bundle = None

    def _require_open(self):
        if self._bundle is None: raise ValueError('reader is closed')

    @property
    def expanded_bytes(self):
        self._require_open()
        return self._bundle.expanded_bytes

    @property
    def objects(self):
        """Borrow objects only while this reader's owning context is entered."""
        self._require_open()
        objects = []
        identities = set()
        owners = set()
        for obj in self._bundle.get_objects():
            if len(objects) >= self.limits.objects:
                raise ValueError('object count limit')
            if type(obj.path_id) is not int or not -(2**63) <= obj.path_id < 2**63 or obj.path_id == 0:
                raise ValueError('invalid physical PathID')
            if type(obj.byte_size) is not int or not 0 < obj.byte_size <= self.limits.selected_bytes:
                raise ValueError('metadata object byte budget')
            key = (str(obj.assets_file.name), obj.path_id)
            if key in identities:
                raise ValueError('duplicate physical object')
            owners.add(id(obj.assets_file))
            if len(owners) > 1:
                raise ValueError('multiple SerializedFile ownership unsupported')
            identities.add(key)
            objects.append(obj)
        return tuple(objects)

    def exact_census(self, version_timestamp):
        """Detached physical census with the complete owner-qualified preload ledger."""
        objects = self.objects
        if not objects:
            raise ValueError('empty physical bundle')
        abobjects = [obj for obj in objects if obj.type.name == 'AssetBundle']
        if len(abobjects) != 1 or not 0 < abobjects[0].byte_size <= self.limits.selected_bytes:
            raise ValueError('require one bounded AssetBundle')
        sf = objects[0].assets_file
        wire = self.source.wire.wire_md5
        cab = str(sf.name)
        if re.fullmatch(r'(?i)CAB-[0-9a-f]{32}', cab) is None:
            raise ValueError('unsupported SerializedFile name')
        def pointer(p):
            f, n = p.m_FileID, p.m_PathID
            if type(f) is not int or type(n) is not int or not 0 <= f <= len(sf.externals) or not -(2**63) <= n < 2**63:
                raise ValueError('invalid raw pointer')
            return dict(file_id=f, path_id=n)
        if len(sf.externals) > 4096:
            raise ValueError('external count limit')
        ab = abobjects[0].read()
        if len(ab.m_PreloadTable) > 100000 or len(ab.m_Container) > 10000:
            raise ValueError('container ledger limit')
        preload = [pointer(p) for p in ab.m_PreloadTable]
        entries = []
        for ordinal, (path, info) in enumerate(ab.m_Container):
            if type(path) is not str or not 0 < len(path) <= 4096:
                raise ValueError('container path limit')
            start, count = info.preloadIndex, info.preloadSize
            if type(start) is not int or type(count) is not int or min(start, count) < 0 or start + count > len(preload):
                raise ValueError('invalid preload range')
            entries.append(dict(path=path, owner_bundle_hash=wire, owner_serialized_file=cab,
                asset_bundle_path_id=abobjects[0].path_id, ordinal=ordinal, asset=pointer(info.asset),
                preload_index=start, preload_size=count))
        assets = []
        for obj in objects:
            name = obj.peek_name() or ''
            if type(name) is not str or len(name) > 4096:
                raise ValueError('object name limit')
            assets.append(dict(bundle_hash=wire, serialized_file=cab, path_id=obj.path_id,
                name=name, asset_type=obj.type.name, version_timestamp=version_timestamp,
                source='normalized/' + self.source.sha256 + '/' + cab + '#' + str(obj.path_id)))
        return dict(assets=assets, serialized_files=[dict(bundle_hash=wire, serialized_file=cab,
            externals=[str(e.path) for e in sf.externals], preload=preload)], container_entries=entries)

    def extract_selected(self, *, cab, path_id, asset_type, name):
        """Decode one census-authenticated leaf; never dereference external objects."""
        if asset_type not in ('TextAsset', 'Texture2D') or type(path_id) is not int:
            raise ValueError('unsupported selected identity')
        matches = [obj for obj in self.objects if obj.path_id == path_id
                   and str(obj.assets_file.name).casefold() == cab.casefold()]
        if len(matches) != 1:
            raise ValueError('selected physical identity missing or ambiguous')
        obj = matches[0]
        if obj.type.name != asset_type or (obj.peek_name() or '') != name:
            raise ValueError('selected type/name mismatch')
        if not 0 < obj.byte_size <= self.limits.selected_bytes:
            raise ValueError('selected object byte budget')
        if asset_type == 'TextAsset':
            value = obj.read().m_Script
            data = value.encode('utf-8', 'surrogateescape') if isinstance(value, str) else bytes(value)
        else:
            # Dimensions are read before UnityPy's image property allocates RGBA.
            tree = obj.read_typetree()
            width, height = tree.get('m_Width'), tree.get('m_Height')
            if (type(width) is not int or type(height) is not int or min(width, height) <= 0
                    or max(width, height) > 4096 or width * height > 4194304):
                raise ValueError('selected texture pixel budget')
            validate_texture_stream(tree, str(obj.assets_file.name), self._bundle.files, self.limits.selected_bytes)
            del tree
            import io
            cap = self.limits.selected_bytes
            class BoundedOutput(io.BytesIO):
                def write(self, value):
                    if self.tell() + len(value) > cap:
                        raise ValueError('encoded texture byte budget')
                    return super().write(value)
            with BoundedOutput() as output:
                image = obj.read().image
                try:
                    if image.size != (width, height):
                        raise ValueError('decoded texture dimension mismatch')
                    image.save(output, format='PNG')
                    data = output.getvalue()
                finally:
                    image.close()
        if not 0 < len(data) <= self.limits.selected_bytes:
            raise ValueError('selected output byte budget')
        return dict(data=data, size=len(data), sha256=hashlib.sha256(data).hexdigest(),
                    wire_md5=self.source.wire.wire_md5, source_sha256=self.source.sha256,
                    cab=cab, path_id=path_id, asset_type=asset_type, name=name)

    def extract_textasset(self, exact_name):
        self._require_open()
        result = select_textasset(self._bundle.get_objects(),exact_name,limits=self.limits)
        result['source_sha256'] = self.source.sha256
        result['wire_sha256'] = self.source.wire.sha256
        result['wire_md5'] = self.source.wire.wire_md5
        return result

    def read_category(self, category):
        self._require_open()
        if category not in ('Data','Arts'): raise ValueError('unsupported category')
        candidates = {}
        for count,obj in enumerate(self._bundle.get_objects(),1):
            if count > self.limits.objects: raise ValueError('object count limit')
            if obj.type.name != 'AssetBundle': continue
            if obj.byte_size > self.limits.selected_bytes: raise ValueError('container size limit')
            for container, info in obj.read().m_Container:
                pointer = info.asset
                # Categories must live in this physical source; never resolve externals.
                if pointer.m_FileID != 0 or pointer.m_PathID == 0:
                    raise ValueError('external or null category pointer')
                target = pointer.deref()
                if target.peek_name() != category: continue
                if not 0 < target.byte_size <= self.limits.selected_bytes:
                    raise ValueError('category object size limit')
                if type(container) is not str or not container or len(container)>4096:
                    raise ValueError('invalid category container evidence')
                identity=(target.assets_file.name,target.path_id)
                if identity not in candidates: candidates[identity]=(target,set())
                candidates[identity][1].add(container)
        if len(candidates) != 1: raise ValueError('require exactly one category object')
        obj,containers = next(iter(candidates.values()))
        tree = obj.read_typetree()
        if not isinstance(tree,dict) or tree.get('m_Name') != category or any(type(tree.get(k)) is not list for k in ('dirs','bundles','assets')):
            raise ValueError('invalid category metadata')
        # Validate without projecting away raw case, dependency lists or extra fields.
        from .input import _json_copy, _catalog
        tree = _json_copy(tree,max_bytes=self.limits.json_bytes)
        _catalog(tree)
        encoded = json.dumps(tree,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')
        return dict(metadata=tree,sha256=hashlib.sha256(encoded).hexdigest(),size=len(encoded),
                    containers=sorted(containers),cab=obj.assets_file.name,path_id=obj.path_id,
                    source_sha256=self.source.sha256,wire_sha256=self.source.wire.sha256)


def _advise(stream):
    stream.flush()
    if hasattr(os,'posix_fadvise'):
        os.posix_fadvise(stream.fileno(),0,0,os.POSIX_FADV_DONTNEED)


@contextmanager
def open_bundle(normalized, *, normalized_root, work_root, decrypt_key, limits=ReaderLimits()):
    """Open one verified normalized bundle; close every stream on exit or failure.

    work_root must be trusted real disk in the bounded child. Anonymous payload
    and CAB files live in a new owned directory; names never come from metadata.
    """
    d = normalized
    if type(d) is not source_io.NormalizedBundle or type(limits) is not ReaderLimits:
        raise ValueError('expected normalized descriptor and reader limits')
    if type(decrypt_key) is not bytes or len(decrypt_key)!=16: raise ValueError('trusted 16 byte decrypt key required')
    with ExitStack() as stack:
        source = stack.enter_context(source_io.open_normalized_bundle(d,normalized_root=normalized_root))
        validate_header(d,limits)
        work_root=Path(os.path.abspath(work_root))
        if work_root.resolve(strict=True)!=work_root or not work_root.is_dir(): raise ValueError('unsafe working root')
        if os.name=='posix':
            # Trusted storage must be disk; the future protected worker additionally
            # verifies its complete policy/cgroup before calling this library.
            from .executor import _disk_free
            _disk_free(work_root)
        work = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix='portrait-reader-',dir=work_root)))
        # All heavy imports remain inside this child-only operation.
        import UnityPy
        from UnityPy.files.BundleFile import BundleFile
        from UnityPy.files.SerializedFile import SerializedFile
        from UnityPy.streams import EndianBinaryReader
        from UnityPy.enums import ArchiveFlags, ArchiveFlagsOld, FileType
        from UnityPy.helpers import ArchiveStorageManager, ImportHelper
        if UnityPy.__version__ != '1.25.0': raise ValueError('unsupported UnityPy runtime')
        UnityPy.set_assetbundle_decrypt_key(decrypt_key)
        def disk():
            return stack.enter_context(tempfile.TemporaryFile(dir=work))
        class ScopedEnvironment(UnityPy.Environment):
            def load_file(self,*args,**kwargs): raise ValueError('ambient file loading forbidden')
            def find_file(self,*args,**kwargs): raise ValueError('external file resolution forbidden')
        class DiskBundle(BundleFile):
            def read_fs(self, reader):
                size=reader.read_long(); cs=reader.read_u_int(); us=reader.read_u_int(); flags=reader.read_u_int()
                if size != d.size or (cs,us,flags) != (d.info_compressed_size,d.info_uncompressed_size,d.flags):
                    raise ValueError('header changed')
                version=self.parse_version()
                old=(version<(2020,) or (version[0]==2020 and version<(2020,3,34))
                     or (version[0]==2021 and version<(2021,3,2)) or (version[0]==2022 and version<(2022,1,1)))
                self.dataflags=(ArchiveFlagsOld if old else ArchiveFlags)(flags)
                allowed=0x3ff if old else 0x17ff
                # 0x100 in the header is compatibility, not block encryption.
                _compression(flags & ~0x100,allowed=allowed,encrypted=True)
                if not flags & 0x40: raise ValueError('separate directory metadata unsupported')
                if self.dataflags & self.dataflags.UsesAssetBundleEncryption:
                    if reader.Position + 70 > reader.Length: raise ValueError('truncated encryption header')
                    self.decryptor=ArchiveStorageManager.ArchiveStorageDecryptor(reader)
                if self.version>=7 or (version[0]==2019 and version>=(2019,4,15)): reader.align_stream(16)
                start=reader.Position
                tail=bool(flags & 0x80)
                if start + cs > size: raise ValueError('metadata range')
                if tail: reader.Position=size-cs
                info=reader.read_bytes(cs)
                if len(info)!=cs: raise ValueError('truncated metadata')
                if tail: reader.Position=start
                data=self.decompress_data(info,us,self.dataflags)
                if len(data)!=us: raise ValueError('metadata expansion mismatch')
                if not old and flags & 0x200: reader.align_stream(16)
                available=(size-cs if tail else size)-reader.Position
                blocks,nodes=parse_metadata(data,compressed_available=available,limits=limits,encrypted=self.decryptor is not None)
                self.expanded_bytes=sum(b[0] for b in blocks)
                estimate=sum(b[0] for b in blocks)+sum(n[1] for n in nodes)
                if shutil.disk_usage(work).free < estimate + 16*1024**2: raise ValueError('insufficient spill disk')
                output=disk()
                for index,(u,c,f) in enumerate(blocks):
                    raw=reader.read_bytes(c)
                    if len(raw)!=c: raise ValueError('truncated block')
                    decoded=self.decompress_data(raw,u,f,index)
                    if len(decoded)!=u: raise ValueError('block expansion mismatch')
                    output.write(decoded)
                    if index%32==0: _advise(output)
                _advise(output); output.seek(0)
                return nodes,EndianBinaryReader(output)
            def read_files(self, reader, nodes):
                for offset,size,flags,name in nodes:
                    output=disk();reader.Position=offset;remaining=size
                    while remaining:
                        chunk=reader.read_bytes(min(1024**2,remaining))
                        if not chunk: raise ValueError('truncated node')
                        output.write(chunk);remaining-=len(chunk)
                    _advise(output);output.seek(0)
                    kind=classify_node(output,name,limits=limits)
                    node_reader=EndianBinaryReader(output)
                    parsed=ImportHelper.parse_file(node_reader,self,name,
                        typ=FileType.AssetsFile if kind=='serialized' else FileType.ResourceFile)
                    if isinstance(parsed,SerializedFile):
                        if len(parsed.objects)>limits.objects: raise ValueError('object count limit')
                        for obj in parsed.objects.values():
                            if obj.byte_start<0 or obj.byte_size<0 or obj.byte_start+obj.byte_size>size:
                                raise ValueError('object outside CAB')
                    parsed.flags=flags;self.files[name]=parsed
                    if kind == 'resource':
                        self.environment.register_cab(name, parsed)
        source.seek(0)
        bundle=DiskBundle(EndianBinaryReader(source),ScopedEnvironment(path=str(work)),name=d.wire.wire_md5+'.bundle')
        result=BundleReader(bundle,d,limits)
        try: yield result
        finally: result.close()
