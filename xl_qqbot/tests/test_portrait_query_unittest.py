import unittest
from bot_app.portrait_querier import parse_portrait_query, PortraitQuerier

class QueryTests(unittest.TestCase):
    def test_parse(self):
        exists = lambda name: name == '名字2'
        for text, expected in [('名字2', ('名字2', None)), ('名字 2', ('名字', 2)), ('名字0', ('名字', 0)), ('2', ('2', None)), ('  ', ('', None)), ('名字', ('名字', None))]:
            self.assertEqual(parse_portrait_query(text, exists), expected)

import hashlib
import json
from pathlib import Path
import tempfile
from PIL import Image

class CatalogTests(unittest.TestCase):
    def setUp(self):
        base = Path('scratch/unittest-tmp')
        base.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'catalog').mkdir()
        Image.new('RGBA', (2, 2), 'red').save(self.root / 'p.png')
        digest = hashlib.sha256((self.root / 'p.png').read_bytes()).hexdigest()
        self.catalog = dict(schema_version=1, instance_id='i', source_id='s', version='v', skins=[
            dict(character_id='100', skin_id=str(i), ordinal=i, name=str(i), state='not_generated' if i == 2 else 'published', artifact_path='p.png', artifact_sha256=digest, source_version='v', diagnostics=[]) for i in range(1, 4)])
        self.querier = PortraitQuerier(self.root, 'i', 's')
        self.publish()

    def publish(self):
        data = json.dumps(self.catalog).encode()
        (self.root / 'catalog/v.json').write_bytes(data)
        (self.root / 'current.json').write_text(json.dumps(dict(schema_version=1, version='v', catalog_path='catalog/v.json', sha256=hashlib.sha256(data).hexdigest())))

    def test_positions(self):
        self.assertEqual(self.querier.find('100', 1)['status'], 'found')
        self.assertEqual(self.querier.find('100', 2)['status'], 'not_generated')
        self.assertEqual(self.querier.find('100', 3)['skin_id'], '3')
        self.assertEqual(len(self.querier.find('100', 0)['available_skins']), 3)

    def test_scope_and_digest(self):
        self.assertEqual(PortraitQuerier(self.root, 'other', 's').find('100', 1)['status'], 'unavailable')
        (self.root / 'p.png').write_bytes(b'broken')
        self.assertEqual(self.querier.find('100', 1)['status'], 'unavailable')

    def test_stale_and_escape(self):
        self.catalog['skins'][0]['source_version'] = 'old'
        self.publish()
        self.assertEqual(self.querier.find('100', 1)['status'], 'stale')
        self.catalog['skins'][0]['artifact_path'] = '../outside.png'
        self.publish()
        self.assertEqual(self.querier.find('100', 1)['status'], 'unavailable')

    def test_non_png_valid_hash(self):
        (self.root / 'p.png').write_bytes(b'{}')
        self.catalog['skins'][0]['artifact_sha256'] = hashlib.sha256(b'{}').hexdigest()
        self.publish()
        self.assertEqual(self.querier.find('100', 1)['status'], 'unavailable')

    def test_missing_and_manifest_damage(self):
        (self.root / 'current.json').write_text('{')
        self.assertEqual(self.querier.find('100', 1)['status'], 'unavailable')

    def test_current_is_read_once(self):
        from unittest.mock import patch
        original = PortraitQuerier._read
        current_reads = []
        def read(path, limit):
            data = original(path, limit)
            if path.name == 'current.json':
                current_reads.append(path)
                path.write_text('{}')
            return data
        with patch.object(PortraitQuerier, '_read', staticmethod(read)):
            self.assertEqual(self.querier.find('100', 1)['status'], 'found')
        self.assertEqual(len(current_reads), 1)

    def test_symlink_escape(self):
        outside = self.root.parent / (self.root.name + '-outside.png')
        outside.write_bytes((self.root / 'p.png').read_bytes())
        self.addCleanup(lambda: outside.unlink(missing_ok=True))
        link = self.root / 'link.png'
        try:
            link.symlink_to(outside.resolve())
        except OSError:
            import subprocess
            outside_dir = self.root.parent / (self.root.name + '-outside-dir')
            outside_dir.mkdir()
            self.addCleanup(outside_dir.rmdir)
            junction = self.root / 'junction'
            subprocess.run(['cmd', '/c', 'mklink', '/J', str(junction.resolve()), str(outside_dir.resolve())], check=True, capture_output=True)
            self.addCleanup(junction.rmdir)
            link = junction / outside.name
            outside.rename(outside_dir / outside.name)
            self.addCleanup((outside_dir / outside.name).unlink)
            self._outside_moved = True
        self.catalog['skins'][0]['artifact_path'] = link.relative_to(self.root).as_posix()
        self.publish()
        self.assertEqual(self.querier.find('100', 1)['status'], 'unavailable')

    def test_reject_bool_schema(self):
        self.catalog['schema_version'] = True
        self.publish()
        self.assertEqual(self.querier.find('100', 1)['status'], 'unavailable')

    def test_reject_duplicate_identity(self):
        self.catalog['skins'][1]['skin_id'] = '1'
        self.publish()
        self.assertEqual(self.querier.find('100', 1)['status'], 'unavailable')

    def test_decode_budget(self):
        self.assertEqual(PortraitQuerier(self.root, 'i', 's', max_decode_pixels=1).find('100', 1)['status'], 'unavailable')

    def test_byte_budgets(self):
        for keyword in ('max_manifest_bytes', 'max_catalog_bytes', 'max_image_bytes'):
            with self.subTest(keyword=keyword):
                result = PortraitQuerier(self.root, 'i', 's', **{keyword: 1}).find('100', 1)
                self.assertEqual(result['status'], 'unavailable')
                self.assertIn('byte budget', result['reason'])

    def test_invalid_provenance(self):
        for version in (None, 123, '', '   '):
            self.catalog['skins'][0]['source_version'] = version
            self.publish()
            self.assertEqual(self.querier.find('100', 1)['status'], 'unavailable')

    def test_long_ordinal(self):
        with self.assertRaisesRegex(ValueError, 'portrait ordinal too long'):
            parse_portrait_query('名字' + '2' * 19, lambda _: False)

    def test_bomb_header(self):
        import struct
        import zlib
        data = bytearray((self.root / 'p.png').read_bytes())
        data[16:24] = struct.pack('>II', 100000, 100000)
        data[29:33] = struct.pack('>I', zlib.crc32(data[12:29]))
        (self.root / 'p.png').write_bytes(data)
        self.catalog['skins'][0]['artifact_sha256'] = hashlib.sha256(data).hexdigest()
        self.publish()
        self.assertEqual(self.querier.find('100', 1)['status'], 'unavailable')

    def test_read_boundary(self):
        path = self.root / 'boundary'
        path.write_bytes(b'1234')
        self.assertEqual(PortraitQuerier._read(path, 4), b'1234')
        with self.assertRaisesRegex(ValueError, 'byte budget'):
            PortraitQuerier._read(path, 3)

    def test_json_recursion(self):
        (self.root / 'current.json').write_text('[' * 2000 + ']' * 2000)
        self.assertEqual(self.querier.find('100', 1)['status'], 'unavailable')

    def test_model_version_interoperability(self):
        import importlib.util
        module_path = Path(__file__).resolve().parents[2] / 'xl_updata_server/server_app/portraits/model.py'
        spec = importlib.util.spec_from_file_location('portrait_contract_model', module_path)
        model = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(model)
        for source_version in ('release 1', '中文版本'):
            row = dict(self.catalog['skins'][0], family='cardspine', source_version=source_version,
                       config_fingerprint='a' * 64, dependency_fingerprint='b' * 64,
                       tool_fingerprint='c' * 64)
            self.catalog = model.build_catalog('i', 's', 'v', [row])
            self.publish()
            result = self.querier.find('100', 1)
            self.assertEqual(result['status'], 'stale')
            self.assertEqual(result['source_version'], source_version)
            self.assertEqual(result['image_path'], (self.root / 'p.png').resolve())
