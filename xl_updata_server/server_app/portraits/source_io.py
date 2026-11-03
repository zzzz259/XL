"""Bounded immutable portrait wire sources and independent UnityFS parser views.

Transport is injected from trusted configuration. Bundle membership belongs to the
later pinned category preparer; accepting a hash.bundle is not membership proof.
"""
from __future__ import annotations

from contextlib import contextmanager, ExitStack
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import struct
import uuid
import time
from urllib.parse import urlsplit

from ..cdn import CategoryInfo, validate_remote_name
from .model import digest

MAX_VERSION_BYTES = 8 * 1024 * 1024
MAX_BLOB_BYTES = 128 * 1024 * 1024
MAX_INFO_BYTES = 16 * 1024 * 1024
CHUNK = 1024 * 1024


@dataclass(frozen=True)
class SourcePin:
    instance_id: str
    source_id: str
    scope_key: str
    cdn_base: str
    version: str
    version_file: str
    version_sha256: str
    raw_version_bytes: bytes
    categories: tuple[CategoryInfo, ...]
    pin_digest: str


@dataclass(frozen=True)
class VerifiedBlob:
    scope_key: str
    pin_digest: str
    remote_name: str
    locator: str
    size: int
    wire_md5: str
    sha256: str


@dataclass(frozen=True)
class NormalizedBundle:
    wire: VerifiedBlob
    locator: str
    size: int
    sha256: str
    prefix_offset: int
    format_version: int
    engine_version: str
    player_version: str
    info_compressed_size: int
    info_uncompressed_size: int
    flags: int


def _hex(value, length):
    if type(value) is not str or re.fullmatch('[0-9a-f]{%d}' % length, value) is None:
        raise ValueError('invalid content digest')
    return value


def _positive(value, cap):
    if type(value) is not int or not 0 < value <= cap:
        raise ValueError('invalid size or policy limit')
    return value


def _pairs(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError('duplicate JSON field')
        obj[key] = value
    return obj


def pin_source(raw_version_bytes, *, instance_id, source_id, cdn_base, version, version_file):
    """Pin exact bounded original bytes; never reconstruct source bytes from JSON."""
    if type(raw_version_bytes) is not bytes or not 0 < len(raw_version_bytes) <= MAX_VERSION_BYTES:
        raise ValueError('invalid raw version bytes')
    for value in (instance_id, source_id):
        if type(value) is not str or not value.strip() or len(value) > 4096:
            raise ValueError('invalid explicit source scope')
    if type(version) is not str or re.fullmatch(r'[1-9][0-9]{0,63}', version) is None:
        raise ValueError('invalid canonical version')
    validate_remote_name(version_file)
    if type(cdn_base) is not str or any(ord(c) < 33 for c in cdn_base) or '\\' in cdn_base:
        raise ValueError('invalid trusted CDN base')
    url = urlsplit(cdn_base)
    if url.scheme != 'https' or not url.hostname or url.username is not None or url.password is not None or url.query or url.fragment:
        raise ValueError('trusted CDN base must be HTTPS authority')
    try:
        url.port
        payload = json.loads(raw_version_bytes.decode('utf-8'), object_pairs_hook=_pairs,
                             parse_constant=lambda x: (_ for _ in ()).throw(ValueError('nonfinite JSON')))
    except (UnicodeError, RecursionError, TypeError) as exc:
        raise ValueError('invalid version JSON') from exc
    if type(payload) is not dict or type(payload.get('timestamp')) is not int or payload['timestamp'] != int(version):
        raise ValueError('pinned timestamp mismatch')
    if type(payload.get('data')) is not list:
        raise ValueError('missing category list')
    categories = []
    for row in payload['data']:
        if type(row) is not dict:
            raise ValueError('invalid category row')
        if row.get('name') not in ('Data', 'Arts'):
            continue
        _hex(row.get('hash'), 32)
        _positive(row.get('size'), MAX_BLOB_BYTES)
        revision = row.get('ver')
        if type(revision) is not int or not 0 <= revision < 2**63:
            raise ValueError('invalid category revision')
        categories.append(CategoryInfo(row['name'], row['hash'], row['size'], revision))
    if sorted(c.name for c in categories) != ['Arts', 'Data']:
        raise ValueError('require exactly one Data and Arts category')
    scope_key = digest([instance_id, source_id])
    raw_sha = hashlib.sha256(raw_version_bytes).hexdigest()
    base = cdn_base.rstrip('/')
    identity = digest([scope_key, base, version, version_file, raw_sha])
    return SourcePin(instance_id, source_id, scope_key, base, version, version_file,
                     raw_sha, raw_version_bytes, tuple(categories), identity)


def _validate_pin(pin):
    if type(pin) is not SourcePin:
        raise ValueError('expected source pin')
    if pin != pin_source(pin.raw_version_bytes, instance_id=pin.instance_id, source_id=pin.source_id,
                         cdn_base=pin.cdn_base, version=pin.version, version_file=pin.version_file):
        raise ValueError('inconsistent source pin')


def _unsafe(st):
    return stat.S_ISLNK(st.st_mode) or bool(getattr(st, 'st_file_attributes', 0) & 0x400)


if os.name == 'nt':
    import ctypes
    from ctypes import wintypes
    import msvcrt
    _kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    _create = _kernel.CreateFileW
    _create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    _create.restype = wintypes.HANDLE
    _close = _kernel.CloseHandle
    _close.argtypes = [wintypes.HANDLE]

    def _win_open(path, directory=False):
        # No delete sharing: the open object cannot be renamed/replaced while used.
        access = 0x80000000 if not directory else 0
        handle = _create(str(path), access, 3, None, 3, 0x00200000 | (0x02000000 if directory else 0), None)
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            st = path.lstat()
            if _unsafe(st) or (not stat.S_ISDIR(st.st_mode) if directory else not stat.S_ISREG(st.st_mode)):
                raise ValueError('link or nonregular cache path')
            return handle
        except BaseException:
            _close(handle)
            raise


@contextmanager
def _directory(root, relative='', *, create=False):
    """Hold no-follow directory handles through all operations beneath trusted root.

    POSIX operations use dir_fd; Windows holds each ancestor without delete sharing
    and rejects every reparse point, including junctions.
    """
    root = Path(os.path.abspath(root))
    if relative:
        validate_remote_name(relative)
    path = Path(root.anchor)
    parts = root.parts[1:] + (tuple(relative.split('/')) if relative else ())
    with ExitStack() as stack:
        if os.name == 'nt':
            handle = _win_open(path, True)
            stack.callback(_close, handle)
            for name in parts:
                parent = (path, handle)
                path = path / name
                if create:
                    try: path.mkdir()
                    except FileExistsError: pass
                handle = _win_open(path, True)
                stack.callback(_close, handle)
                # Reflush existing entries too: a racing creator or an earlier
                # failed flush must not let a retry trust an undurable mkdir.
                if create: _sync_directory(parent)
            yield path, handle
        else:
            fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            stack.callback(os.close, fd)
            for name in parts:
                parent = (path, fd)
                if create:
                    try: os.mkdir(name, dir_fd=fd)
                    except FileExistsError: pass
                fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                stack.callback(os.close, fd)
                if create: _sync_directory(parent)
                path = path / name
            yield path, fd


@contextmanager
def _read_file(directory, name):
    path, fd = directory
    if os.name == 'nt':
        for attempt in range(101):
            try:
                handle = _win_open(path / name)
                break
            except OSError as exc:
                # A concurrent verified writer retains DELETE access until its
                # rename handle closes. Readers never weaken no-delete sharing.
                if exc.winerror != 32 or attempt == 100: raise
                time.sleep(0.01)
        try:
            file_fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        except BaseException:
            _close(handle)
            raise
    else:
        file_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    with os.fdopen(file_fd, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('cache object must be regular')
        yield stream


def _stream(stream, size, *, output=None, skip=0):
    count = 0
    md5, sha = hashlib.md5(), hashlib.sha256()
    normalized = hashlib.sha256()
    while True:
        chunk = stream.read(min(CHUNK, size - count + 1))
        if not isinstance(chunk, bytes):
            raise ValueError('transport must return binary bytes')
        if not chunk:
            break
        start = count
        count += len(chunk)
        if count > size:
            raise ValueError('overlong source')
        md5.update(chunk); sha.update(chunk)
        if output is not None:
            view = chunk[max(0, skip - start):]
            output.write(view); normalized.update(view)
    if count != size:
        raise ValueError('truncated source')
    return md5.hexdigest(), sha.hexdigest(), normalized.hexdigest()


def _check(stream, size, wire_md5, sha256=None, **kwargs):
    result = _stream(stream, size, **kwargs)
    if result[0] != wire_md5 or (sha256 is not None and result[1] != sha256):
        raise ValueError('immutable content digest mismatch')
    return result


@contextmanager
def _partial(directory):
    path, fd = directory
    name = '.partial-' + uuid.uuid4().hex
    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, 'O_BINARY', 0)
    if os.name == 'nt':
        # Hold the same exclusive write/delete handle through publication.
        handle = _create(str(path / name), 0xC0010000, 1, None, 1, 0x80200000, None)
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try: file_fd = msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY)
        except BaseException:
            _close(handle)
            raise
    else:
        file_fd = os.open(name, flags, 0o600, dir_fd=fd)
    # Interrupted partials are intentionally retained and never consumed as objects.
    with os.fdopen(file_fd, 'w+b') as out:
        yield name, out
        out.flush(); os.fsync(out.fileno())
        _retire_partial(directory, name, out)
    # Windows delete disposition takes effect when the owned handle closes.
    _sync_directory(directory)


def _retire_partial(directory, name, output):
    """Retire only this successful invocation's entry while its handle is held."""
    path, fd = directory
    try:
        found = (path / name).lstat() if os.name == 'nt' else os.stat(name,dir_fd=fd,follow_symlinks=False)
    except FileNotFoundError:
        return  # Windows winner renamed the open object; no partial remains.
    owned = os.fstat(output.fileno())
    if _unsafe(found) or (found.st_dev,found.st_ino) != (owned.st_dev,owned.st_ino):
        raise ValueError('owned partial identity changed; refusing cleanup')
    if os.name == 'nt':
        disposition = _kernel.SetFileInformationByHandle
        disposition.argtypes = [wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD]
        disposition.restype = wintypes.BOOL
        delete = wintypes.BOOL(True)
        if not disposition(msvcrt.get_osfhandle(output.fileno()),4,ctypes.byref(delete),ctypes.sizeof(delete)):
            raise ctypes.WinError(ctypes.get_last_error())
    else:
        os.unlink(name,dir_fd=fd)


def _sync_directory(directory):
    path, fd = directory
    if os.name == 'nt':
        # Directory FlushFileBuffers needs GENERIC_WRITE + BACKUP_SEMANTICS.
        handle = _create(str(path), 0x40000000, 3, None, 3, 0x02200000, None)
        if handle == ctypes.c_void_p(-1).value: raise ctypes.WinError(ctypes.get_last_error())
        try:
            flush = _kernel.FlushFileBuffers
            flush.argtypes = [wintypes.HANDLE]
            flush.restype = wintypes.BOOL
            if not flush(handle): raise ctypes.WinError(ctypes.get_last_error())
        finally: _close(handle)
    else:
        os.fsync(fd)


def _publish(directory, partial, target, output):
    path, fd = directory
    output.flush(); os.fsync(output.fileno())
    _sync_directory(directory)
    installed = True
    if os.name == 'nt':
        # Rename the verified open object itself, never reopen a replaceable path.
        class RenameInfo(ctypes.Structure):
            _fields_ = [('ReplaceIfExists', wintypes.BOOL), ('RootDirectory', wintypes.HANDLE),
                        ('FileNameLength', wintypes.DWORD), ('FileName', wintypes.WCHAR * 1)]
        encoded = str(path / target).encode('utf-16-le')
        buffer = ctypes.create_string_buffer(RenameInfo.FileName.offset + len(encoded) + 2)
        info = RenameInfo.from_buffer(buffer)
        info.ReplaceIfExists = False
        info.RootDirectory = None
        info.FileNameLength = len(encoded)
        ctypes.memmove(ctypes.addressof(buffer) + RenameInfo.FileName.offset, encoded, len(encoded))
        rename = _kernel.SetFileInformationByHandle
        rename.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        rename.restype = wintypes.BOOL
        if not rename(msvcrt.get_osfhandle(output.fileno()), 3, buffer, len(buffer)):
            code = ctypes.get_last_error()
            if code not in (80, 183): raise ctypes.WinError(code)
            installed = False
    else:
        # Owned randomized temporary entry, under held no-follow directory fd.
        try: os.link(partial, target, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
        except FileExistsError: installed = False
    _sync_directory(directory)
    return installed


@contextmanager
def _published_file(directory, target, output, installed):
    if installed:
        # Winner still holds its write/delete handle; verify those exact bytes.
        found = (directory[0] / target).lstat() if os.name == 'nt' else os.stat(target,dir_fd=directory[1],follow_symlinks=False)
        owned = os.fstat(output.fileno())
        if _unsafe(found) or (found.st_dev,found.st_ino) != (owned.st_dev,owned.st_ino):
            raise ValueError('published object identity changed')
        output.seek(0)
        yield output
    else:
        with _read_file(directory,target) as stream: yield stream


def acquire_blob(pin, *, remote_name, wire_md5, size, cache_root, open_remote):
    """Stream-check wire bytes, publish without replacing existing immutable data.

    open_remote(remote_name) is a trusted binary context factory bound to pin.cdn_base.
    Category identities are checked here. AB category ownership is checked later.
    """
    _validate_pin(pin)
    validate_remote_name(remote_name)
    _hex(wire_md5, 32); _positive(size, MAX_BLOB_BYTES)
    if remote_name != wire_md5 + '.bundle':
        if not any(remote_name == f'{c.name.lower()}_{c.hash}.json' and wire_md5 == c.hash and size == c.size for c in pin.categories):
            raise ValueError('remote identity not allowed by pin')
    relative = f'{pin.scope_key}/{pin.pin_digest}'
    # Include remote identity: equal content from separate remote resources stays bound.
    name = digest([remote_name, wire_md5, size]) + '.blob'
    with _directory(cache_root, relative, create=True) as directory:
        try:
            with _read_file(directory, name) as stream:
                hashes = _check(stream, size, wire_md5)
        except FileNotFoundError:
            with _partial(directory) as (partial, output):
                with open_remote(remote_name) as stream:
                    hashes = _check(stream, size, wire_md5, output=output)
                installed = _publish(directory, partial, name, output)
                with _published_file(directory,name,output,installed) as stream:
                    _check(stream, size, wire_md5, hashes[1])
        else:
            # A previous attempt may have installed these verified bytes but
            # failed the final-entry flush. Cache reuse must complete it too.
            _sync_directory(directory)
    return VerifiedBlob(pin.scope_key, pin.pin_digest, remote_name, relative + '/' + name,
                        size, wire_md5, hashes[1])


def _header(first, wire_size):
    offsets = [i for i in range(len(first)) if first.startswith(b'UnityFS\0', i)]
    if len(offsets) != 1:
        raise ValueError('require exactly one bounded UnityFS header')
    offset = offsets[0]; pos = offset + 8
    def number(fmt):
        nonlocal pos
        n = struct.calcsize(fmt)
        if pos + n > len(first): raise ValueError('truncated UnityFS header')
        value = struct.unpack_from(fmt, first, pos)[0]; pos += n
        return value
    def text():
        nonlocal pos
        end = first.find(b'\0', pos, min(len(first), pos + 256))
        if end < 0: raise ValueError('unterminated UnityFS version')
        try: value = first[pos:end].decode('ascii')
        except UnicodeError as exc: raise ValueError('invalid UnityFS version') from exc
        if not value or any(ord(c) < 32 for c in value): raise ValueError('invalid UnityFS version')
        pos = end + 1
        return value
    fmt = number('>I')
    if fmt not in (6, 7, 8): raise ValueError('unsupported UnityFS format')
    player, engine = text(), text()
    length, compressed, uncompressed, flags = number('>Q'), number('>I'), number('>I'), number('>I')
    _positive(compressed, MAX_INFO_BYTES); _positive(uncompressed, MAX_INFO_BYTES)
    # Formats >=7 align the payload start. Metadata may be stored at the end (0x80).
    header_size = pos - offset
    if fmt >= 7: header_size = (header_size + 15) & ~15
    if length != wire_size - offset or header_size + compressed > length:
        raise ValueError('inconsistent UnityFS file/info sizes')
    return offset, fmt, engine, player, compressed, uncompressed, flags


def normalize_bundle(verified_blob, *, cache_root, output_root):
    """Reverify wire input while copying; never repair immutable original bytes."""
    d = verified_blob
    if type(d) is not VerifiedBlob: raise ValueError('expected verified blob descriptor')
    for value, n in ((d.scope_key,64),(d.pin_digest,64),(d.wire_md5,32),(d.sha256,64)): _hex(value,n)
    _positive(d.size, MAX_BLOB_BYTES); validate_remote_name(d.remote_name)
    expected = f'{d.scope_key}/{d.pin_digest}/' + digest([d.remote_name,d.wire_md5,d.size]) + '.blob'
    if d.locator != expected: raise ValueError('inconsistent scoped blob locator')
    parent, name = d.locator.rsplit('/', 1)
    with _directory(cache_root, parent) as directory, _read_file(directory,name) as stream:
        _check(stream,d.size,d.wire_md5,d.sha256)
        stream.seek(0); evidence = _header(stream.read(4096),d.size); offset = evidence[0]
        stream.seek(0)
        relative = f'{d.scope_key}/{d.pin_digest}'
        with _directory(output_root, relative, create=True) as dest:
            with _partial(dest) as (partial, output):
                hashes = _check(stream,d.size,d.wire_md5,d.sha256,output=output,skip=offset)
                normalized_sha = hashes[2]; target = normalized_sha + '.bundle'
                # Separate filename and storage; never mutate wire bytes.
                if os.path.abspath(dest[0] / target) == os.path.abspath(directory[0] / name):
                    raise ValueError('normalization must produce independent file')
                installed = _publish(dest,partial,target,output)
                with _published_file(dest,target,output,installed) as check:
                    source_stat, view_stat = os.fstat(stream.fileno()), os.fstat(check.fileno())
                    if (source_stat.st_dev,source_stat.st_ino) == (view_stat.st_dev,view_stat.st_ino):
                        raise ValueError('parser view aliases immutable wire input')
                    actual = _stream(check,d.size-offset)
                    if actual[1] != normalized_sha: raise ValueError('conflicting normalized target')
    return NormalizedBundle(d,relative+'/'+target,d.size-offset,normalized_sha,*evidence)
