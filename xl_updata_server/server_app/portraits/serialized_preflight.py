"""Bounded structural preflight for Unity SerializedFile versions 17 through 22.

This reads headers and metadata tables only, never object payloads. Actual
typetree/object interpretation remains UnityPy's job after this validation.
Classification intentionally does not use an archive/decompression detector.
"""
import re
import struct


class _Metadata:
    def __init__(self, data, endian, base):
        self.data=data; self.endian=endian; self.base=base; self.pos=0

    def take(self, size):
        if size<0 or size>len(self.data)-self.pos: raise ValueError('serialized metadata boundary')
        start=self.pos;self.pos+=size
        return self.data[start:self.pos]

    def number(self, fmt):
        return struct.unpack(self.endian+fmt,self.take(struct.calcsize(fmt)))[0]

    def count(self, cap, minimum=1):
        value=self.number('i')
        if not 0<=value<=cap or value*minimum>len(self.data)-self.pos:
            raise ValueError('serialized metadata count')
        return value

    def align(self):
        self.take(-(self.base+self.pos)%4)

    def string(self, cap=4096):
        end=self.data.find(b'\0',self.pos,min(len(self.data),self.pos+cap+1))
        if end<0: raise ValueError('unterminated serialized metadata string')
        value=self.data[self.pos:end];self.pos=end+1
        return value

    def boolean(self):
        value=self.number('B')
        if value not in (0,1): raise ValueError('invalid serialized boolean')
        return bool(value)


def _type_tree(meta, version, limits, total_nodes):
    count=meta.count(limits.objects)
    string_size=meta.number('i')
    stride=32 if version>=19 else 24
    if count==0 or string_size<0 or string_size>limits.info_bytes or total_nodes+count>limits.objects:
        raise ValueError('type tree budget')
    # Both sizes are checked before allocating a table or a string buffer.
    if count*stride+string_size>len(meta.data)-meta.pos:
        raise ValueError('type tree outside metadata')
    nodes=meta.take(count*stride);strings=meta.take(string_size)
    previous=-1
    for i in range(count):
        # hBBIIiii[Q]: version,depth,flags,type/name string offsets,size,index,flags,hash.
        _,depth,_,typ,name,_,_,_=struct.unpack_from(meta.endian+'hBBIIiii',nodes,i*stride)
        if depth>128 or (i==0 and depth!=0) or (i>0 and (depth==0 or depth>previous+1)):
            raise ValueError('invalid type tree hierarchy')
        previous=depth
        for offset in (typ,name):
            if not offset & 0x80000000:
                if offset>=len(strings) or strings.find(b'\0',offset,min(len(strings),offset+4097))<0:
                    raise ValueError('type tree string outside buffer')
    return total_nodes+count


def preflight_serialized(stream, *, size, limits):
    """Validate original row identities and boundaries before UnityPy dict creation."""
    stream.seek(0);header=stream.read(min(size,48))
    if len(header)<20: raise ValueError('truncated serialized header')
    metadata_size,file_size,version,data_offset=struct.unpack_from('>IIII',header)
    if version not in range(17,23): raise ValueError('unsupported SerializedFile version (require 17..22)')
    if header[16] not in (0,1) or header[17:20]!=b'\0'*3:
        raise ValueError('invalid serialized endian/reserved bytes')
    base=20
    if version>=22:
        if len(header)<48: raise ValueError('truncated extended serialized header')
        metadata_size,file_size,data_offset,_=struct.unpack_from('>Iqqq',header,20)
        base=48
    if file_size!=size or not 0<metadata_size<=limits.info_bytes or not base+metadata_size<=data_offset<=size:
        raise ValueError('invalid serialized metadata/data regions')
    stream.seek(base);data=stream.read(metadata_size)
    if len(data)!=metadata_size: raise ValueError('truncated serialized metadata')
    meta=_Metadata(data,'>' if header[16] else '<',base)
    engine=meta.string(255)
    if re.fullmatch(rb'[1-9][0-9]{3}\.[0-9]+\.[0-9]+[abfp][0-9]+(?:c[0-9]+)?',engine) is None:
        raise ValueError('unsupported serialized engine version')
    meta.number('i')  # BuildTarget, still interpreted/validated by UnityPy.
    has_tree=meta.boolean()
    type_count=meta.count(limits.objects,23)
    total_nodes=0
    script_indices=[]
    def read_type(is_ref):
        nonlocal total_nodes
        class_id=meta.number('i');meta.boolean()
        script_index=meta.number('h')
        if script_index < -1: raise ValueError('invalid script type index')
        # -1 is a legitimate sentinel; unlike Object.type_id it is not a list index.
        if not is_ref:script_indices.append(script_index)
        if (is_ref and script_index>=0) or class_id==114:meta.take(16)
        meta.take(16)
        if has_tree:
            total_nodes=_type_tree(meta,version,limits,total_nodes)
            if version>=21:
                if is_ref:
                    for _ in range(3):meta.string()
                else:
                    count=meta.count(limits.objects,4)
                    for _ in range(count):
                        dependency=meta.number('i')
                        if not 0<=dependency<type_count:raise ValueError('invalid type dependency index')
    for _ in range(type_count):read_type(False)
    object_count=meta.count(limits.objects,24 if version>=22 else 20)
    ids=set();ranges=[]
    for _ in range(object_count):
        meta.align();path_id=meta.number('q')
        start=meta.number('q' if version>=22 else 'I');length=meta.number('I');type_id=meta.number('i')
        if path_id in ids:raise ValueError('duplicate serialized PathID')
        ids.add(path_id)
        if not 0<=type_id<type_count:raise ValueError('invalid object type index')
        if start<0 or length==0 or start+length>size-data_offset:
            raise ValueError('serialized object outside data region')
        ranges.append((start,start+length))
    end=0
    for start,stop in sorted(ranges):
        if start<end:raise ValueError('overlapping serialized objects')
        end=stop
    script_count=meta.count(limits.objects,12)
    for index in script_indices:
        if index>=script_count:raise ValueError('script type index outside script table')
    for _ in range(script_count):
        file_index=meta.number('i')
        if file_index<0:raise ValueError('negative script file index')
        meta.align();meta.number('q')
    external_count=meta.count(limits.objects,22)
    for _ in range(external_count):
        meta.string();meta.take(16);meta.number('i');meta.string()
    if version>=20:
        ref_count=meta.count(limits.objects,23)
        for _ in range(ref_count):read_type(True)
    meta.string()  # userInformation
    if meta.pos!=len(data):raise ValueError('unconsumed serialized metadata')
    stream.seek(0)
    return dict(version=version,objects=object_count,types=type_count,metadata_size=metadata_size)


def classify_node(stream, name, *, limits):
    """Classify only verified disk bytes; never recursively open/decompress nodes."""
    stream.seek(0,2);size=stream.tell();stream.seek(0)
    header=stream.read(min(size,48));stream.seek(0)
    if (header.startswith((b'\x1f\x8b',b'PK\x03\x04',b'PK\x05\x06',b'Unity',b'TuanjieWebData'))
            or header[32:38]==b'brotli'):
        raise ValueError('nested archive/wrapper unsupported')
    if name.endswith(('.resS','.resource')):
        return 'resource'
    # In this deliberately narrow reader every non-resource node must be a
    # supported SerializedFile. Malformed CABs never fall back to opaque bytes.
    preflight_serialized(stream,size=size,limits=limits)
    return 'serialized'
