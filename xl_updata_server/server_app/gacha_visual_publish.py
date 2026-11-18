"""Validate a complete visual update and publish its immutable public files."""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import re
import tempfile
from pathlib import Path

LOGGER = logging.getLogger(__name__)
MAX_MAPPING = 1024 * 1024
MAX_IMAGE = 4 * 1024 * 1024
MAX_IMAGES = 2048
MAX_TOTAL = 32 * 1024 * 1024
VISUAL_GROUPS = ('characters', 'fashions', 'account_items')
_VERSION = re.compile(r'[1-9][0-9]{0,19}')
_SHA256 = re.compile(r'[0-9a-f]{64}')
_IMAGE_PATH = re.compile(r'assets/(?:portraits|account|account-rect)/([0-9a-f]{64})\.(png|webp)')


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _version(value) -> str:
    _require(isinstance(value, str) and _VERSION.fullmatch(value) is not None, 'invalid visual version')
    return value


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _public_path(root: Path, relative: str) -> Path:
    path = root / relative
    _require(path.resolve().is_relative_to(root.resolve()), 'visual path escapes publication directory')
    return path


def _read(root: Path, relative: str, limit: int) -> bytes:
    with _public_path(root, relative).open('rb') as handle:
        raw = handle.read(limit + 1)
    _require(len(raw) <= limit, 'oversize visual artifact')
    return raw


def _bounded_int(value, maximum: int) -> bool:
    return type(value) is int and 0 < value <= maximum


def _check_record(record, maximum: int) -> None:
    _require(isinstance(record, dict) and _bounded_int(record.get('size'), maximum), 'invalid visual size')
    digest = record.get('sha256')
    _require(isinstance(digest, str) and _SHA256.fullmatch(digest) is not None, 'invalid visual checksum')


def _verify(raw: bytes, record: dict) -> None:
    _require(len(raw) == record['size'] and _digest(raw) == record['sha256'], 'visual integrity check failed')


def _check_entry(group: str, entry: dict, files: dict) -> set[str]:
    _require(isinstance(entry, dict), 'invalid visual mapping entry')
    required = 'rect_path' if group == 'fashions' else 'path'
    _require(required in entry, 'visual mapping has no image path')
    paths = set()
    for field in ('path', 'rect_path'):
        if field not in entry:
            continue
        path = entry[field]
        _require(field != 'rect_path' or group == 'fashions', 'unexpected rect_path')
        _require(isinstance(path, str) and path in files, 'visual mapping references an undeclared image')
        paths.add(path)

    if group == 'characters':
        if 'name' in entry:
            name = entry['name']
            _require(
                isinstance(name, str) and bool(name.strip()) and len(name.encode('utf-16-le')) <= 240
                and all(not (ord(char) < 32 or 127 <= ord(char) <= 159) for char in name),
                'invalid character name',
            )
        rarity = entry.get('rarity')
        _require(rarity is None or _bounded_int(rarity, 5), 'invalid visual rarity')
    if group == 'account_items':
        _require(type(entry.get('type')) is int and entry['type'] in (108, 109), 'invalid account item type')
        effect = entry.get('effect_value')
        _require(effect is None or isinstance(effect, str) and re.fullmatch(r'[0-9]{1,10}', effect) is not None,
                 'invalid frame effect')

    if 'source_size' in entry or 'crop_rect' in entry:
        _require(group != 'account_items' or entry['type'] == 108, 'unexpected frame crop')
        size = entry.get('source_size')
        _require(isinstance(size, list) and len(size) == 2 and all(_bounded_int(n, 4096) for n in size),
                 'invalid image canvas')
        if 'crop_rect' in entry:
            rect = entry['crop_rect']
            _require(isinstance(rect, list) and len(rect) == 4 and all(type(n) is int for n in rect),
                     'invalid crop rectangle')
            x, y, width, height = rect
            _require(x >= 0 and y >= 0 and width > 0 and height > 0
                     and x + width <= size[0] and y + height <= size[1], 'crop outside the image canvas')
    return paths


def _check_mapping(mapping: dict, version: str) -> dict:
    _require(isinstance(mapping, dict)
             and mapping.get('format') == 'starfall-gacha-visual-assets'
             and type(mapping.get('schema_version')) is int and mapping['schema_version'] == 1
             and mapping.get('version') == version
             and not (set(mapping) - {'format', 'schema_version', 'version', 'files', *VISUAL_GROUPS}),
             'invalid visual mapping')
    files = mapping.get('files')
    _require(isinstance(files, dict) and 0 < len(files) <= MAX_IMAGES, 'invalid visual file count')
    for relative, record in files.items():
        path = _IMAGE_PATH.fullmatch(relative)
        _check_record(record, MAX_IMAGE)
        _require(path is not None and path[1] == record['sha256'], 'invalid content-addressed image path')
    _require(sum(record['size'] for record in files.values()) <= MAX_TOTAL, 'visual image payload exceeds 32 MiB')
    referenced = set()
    for group in VISUAL_GROUPS:
        entries = mapping.get(group, {})
        _require(isinstance(entries, dict) and len(entries) <= 20000, 'invalid visual mapping count')
        for item_id, entry in entries.items():
            _require(re.fullmatch(r'[1-9][0-9]{0,15}', item_id) is not None and int(item_id) <= 2**53 - 1,
                     'invalid visual mapping ID')
            referenced.update(_check_entry(group, entry, files))
    _require(referenced == set(files), 'visual mapping contains unused images')
    return files


def read_visual_mapping(root: Path, descriptor: dict) -> dict:
    """Read the manifest pinned by a catalog, and verify its published files."""
    _check_record(descriptor, MAX_MAPPING)
    version = _version(descriptor.get('version'))
    filename = f'visual-assets-{version}.json'
    _require(type(descriptor.get('schema_version')) is int and descriptor['schema_version'] == 1
             and descriptor.get('file') == filename, 'invalid visual descriptor')
    raw = _read(root, filename, descriptor['size'])
    _verify(raw, descriptor)
    mapping = json.loads(raw)
    for relative, record in _check_mapping(mapping, version).items():
        _verify(_read(root, relative, record['size']), record)
    return mapping


def read_active_visual(public_dir: Path) -> tuple[dict | None, str | None]:
    if not (public_dir / 'current.json').is_file():
        return None, None
    from .gacha_catalog import read_published_catalog

    catalog, _ = read_published_catalog(public_dir)
    descriptor = catalog.get('visual_assets')
    if descriptor is None:
        return None, None
    return read_visual_mapping(public_dir, descriptor), descriptor['sha256']


def _immutable(root: Path, relative: str, raw: bytes) -> None:
    path = _public_path(root, relative)
    if path.exists():
        _require(_read(root, relative, len(raw)) == raw, 'immutable visual artifact conflict')
        return
    path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    parent = path.parent
    while parent != root:
        parent.chmod(0o755)
        parent = parent.parent
    fd, temporary = tempfile.mkstemp(prefix='.visual-', suffix='.part', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)
        os.rename(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _validate_raster(relative: str, raw: bytes) -> None:
    from PIL import Image

    expected_format = 'PNG' if relative.endswith('.png') else 'WEBP'
    try:
        with Image.open(io.BytesIO(raw), formats=[expected_format]) as image:
            _require(0 < image.width <= 4096 and 0 < image.height <= 4096, 'visual image dimensions out of bounds')
            image.verify()
    except (SyntaxError, Image.DecompressionBombError) as error:
        raise ValueError('invalid visual raster') from error


def publish_visual_candidate(
    public_dir: Path, staging: Path | None, version: str, previous: dict | None
) -> dict | None:
    """Called with the catalog publication lock held, before switching current.json."""
    old_mapping = read_visual_mapping(public_dir, previous) if previous is not None else {}
    if staging is None or not (staging / 'candidate.json').is_file():
        return previous
    try:
        document = json.loads(_read(staging, 'candidate.json', MAX_MAPPING + 4096))
        expected_base = previous['sha256'] if previous is not None else None
        _require(document['base_sha256'] == expected_base, 'visual base changed during processing')
        mapping = document['mapping']
        files = _check_mapping(mapping, version)
        if previous is not None:
            _require(int(version) > int(previous['version']), 'visual version must increase')
        for group in (*VISUAL_GROUPS, 'files'):
            current = mapping.get(group, {})
            _require(all(current.get(key) == value for key, value in old_mapping.get(group, {}).items()),
                     'visual update must retain previous images and mappings')
        raw_mapping = (json.dumps(mapping, ensure_ascii=False, allow_nan=False, sort_keys=True,
                                  separators=(',', ':')) + '\n').encode('utf-8')
        _require(len(raw_mapping) <= MAX_MAPPING, 'visual manifest exceeds 1 MiB')
        for relative, record in files.items():
            if old_mapping.get('files', {}).get(relative) == record:
                continue
            raw = _read(staging, relative, record['size'])
            _verify(raw, record)
            _validate_raster(relative, raw)
            _immutable(public_dir, relative, raw)
        filename = f'visual-assets-{version}.json'
        _immutable(public_dir, filename, raw_mapping)
        descriptor = {'schema_version': 1, 'version': version, 'file': filename,
                      'sha256': _digest(raw_mapping), 'size': len(raw_mapping)}
        read_visual_mapping(public_dir, descriptor)
        return descriptor
    except (OSError, ValueError, KeyError, TypeError) as error:
        LOGGER.warning('stage=gacha_visual_publish version=%s status=skipped fallback=last_known_good error=%s',
                       version, error)
        return previous
