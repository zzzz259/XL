"""Read-only, instance-scoped portrait catalog lookup."""
import hashlib
import io
import json
import os
import warnings
from pathlib import Path, PureWindowsPath
import re

from PIL import Image


def parse_portrait_query(query, exact_name_exists):
    text = query.strip()
    if not text or text.isdecimal() or exact_name_exists(text):
        return text, None
    match = re.fullmatch(r'(.*?)([0-9]+)', text)
    if match and match[1].strip():
        if len(match[2]) > 18:
            raise ValueError('portrait ordinal too long')
        return match[1].strip(), int(match[2])
    return text, None


class PortraitQuerier:
    def __init__(self, root: Path, instance_id: str, source_id: str, *, max_decode_pixels: int = 8_388_608, max_manifest_bytes: int = 65536,
                 max_catalog_bytes: int = 4 * 1024 * 1024, max_image_bytes: int = 20 * 1024 * 1024):
        self.root = Path(root).resolve()
        self.instance_id = instance_id
        self.source_id = source_id
        if type(max_decode_pixels) is not int or max_decode_pixels < 1:
            raise ValueError("invalid decode pixel budget")
        self.max_decode_pixels = max_decode_pixels
        for name, limit in (("max_manifest_bytes", max_manifest_bytes), ("max_catalog_bytes", max_catalog_bytes), ("max_image_bytes", max_image_bytes)):
            if type(limit) is not int or limit < 1:
                raise ValueError("invalid byte budget")
            setattr(self, name, limit)

    def _path(self, value):
        if not isinstance(value, str) or not value or '\\' in value:
            raise ValueError('invalid relative path')
        p = Path(value)
        if p.is_absolute() or PureWindowsPath(value).drive or '..' in p.parts:
            raise ValueError('unsafe path')
        resolved = (self.root / p).resolve()
        if not resolved.is_relative_to(self.root):
            raise ValueError('path escapes media root')
        return resolved

    @staticmethod
    def _read(path, limit):
        with path.open('rb') as stream:
            if os.fstat(stream.fileno()).st_size > limit:
                raise ValueError('file exceeds byte budget')
            data = stream.read(limit + 1)
            if len(data) > limit:
                raise ValueError('file exceeds byte budget')
            return data

    @staticmethod
    def _hash(data, expected):
        if not isinstance(expected, str) or not re.fullmatch('[0-9a-fA-F]{64}', expected):
            raise ValueError('invalid digest')
        if hashlib.sha256(data).hexdigest() != expected.lower():
            raise ValueError('digest mismatch')

    def find(self, character_id: str, ordinal: int) -> dict:
        result = dict(status='unavailable', image_path=None, name=None, skin_id=None,
                      ordinal=ordinal, source_version=None, catalog_version=None,
                      available_skins=[], reason='catalog unavailable')
        try:
            current = json.loads(self._read(self._path('current.json'), self.max_manifest_bytes))
            version = current['version']
            if type(current['schema_version']) is not int or current['schema_version'] != 1 or not isinstance(version, str) or not version.strip():
                raise ValueError('invalid manifest')
            sha = current['sha256']
            if not isinstance(sha, str) or not re.fullmatch('[0-9a-fA-F]{64}', sha):
                raise ValueError('invalid catalog digest')
            if current['catalog_path'] not in (f'catalog/{version}.json', f'catalog/{version}/{sha.lower()}.json'):
                raise ValueError('catalog version path mismatch')
            raw = self._read(self._path(current['catalog_path']), self.max_catalog_bytes)
            self._hash(raw, current['sha256'])
            catalog = json.loads(raw)
            if (type(catalog['schema_version']) is not int or catalog['schema_version'] != 1 or catalog['version'] != version
                    or catalog['instance_id'] != self.instance_id
                    or catalog['source_id'] != self.source_id):
                raise ValueError('catalog scope mismatch')
            entries = catalog['skins']
            if not isinstance(entries, list):
                raise ValueError('invalid skins')
            identities = set()
            for entry in entries:
                for field in ('character_id', 'skin_id'):
                    if not isinstance(entry[field], str) or not re.fullmatch('[1-9][0-9]*', entry[field]):
                        raise ValueError('invalid canonical identity')
                identity = (entry['character_id'], entry['skin_id'])
                if identity in identities:
                    raise ValueError('duplicate skin identity')
                identities.add(identity)
            skins = [s for s in entries if s['character_id'] == character_id]
            skins.sort(key=lambda s: int(s['skin_id']))
            for index, skin in enumerate(skins, 1):
                if type(skin['ordinal']) is not int or skin['ordinal'] != index:
                    raise ValueError('invalid configured ordinal')
                if not isinstance(skin['name'], str) or not isinstance(skin['state'], str):
                    raise ValueError('invalid skin')
            result.update(catalog_version=version, available_skins=[
                {k: s[k] for k in ('ordinal', 'name', 'skin_id', 'state')} for s in skins])
            if type(ordinal) is not int or ordinal < 1 or ordinal > len(skins):
                return dict(result, status='out_of_range', reason='ordinal outside configured skins')
            skin = skins[ordinal - 1]
            result.update({k: skin[k] for k in ('name', 'skin_id', 'source_version')})
            if skin['state'] != 'published':
                return dict(result, status='blocked' if skin['state'] == 'blocked' else 'not_generated',
                            reason=skin['state'])
            provenance = skin['source_version']
            if not isinstance(provenance, str) or not provenance.strip():
                raise ValueError('invalid published source_version')
            path = self._path(skin['artifact_path'])
            data = self._read(path, self.max_image_bytes)
            self._hash(data, skin['artifact_sha256'])
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                return self._decode_result(data, result, skin, version, path)
        except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError, Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
            return dict(result, status="unavailable", image_path=None, reason=str(error))

    def _decode_result(self, data, result, skin, version, path):
        with Image.open(io.BytesIO(data)) as picture:
            if picture.format != 'PNG':
                raise ValueError('artifact is not PNG')
            pixels = picture.width * picture.height
            picture.verify()
            if pixels > self.max_decode_pixels:
                raise ValueError('PNG exceeds decode pixel budget')
        with Image.open(io.BytesIO(data)) as picture:
            picture.load()
        return dict(result, status='found' if skin['source_version'] == version else 'stale',
                    image_path=path, reason='published')
