import hashlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from server_app.gacha_catalog import publish_catalog, read_published_catalog
from server_app.pipeline import ProcessResult, UpdatePipeline


def catalog(version):
    return {
        'schema_version': 1,
        'version': str(version),
        'cards': {'101': ['测试五星', 5]},
        'pools': {'201': '测试池'},
        'selectors': [1],
        'pool_meta': {'201': {'category': 'limited', 'pity_group': 'limited',
                              'featured_ids': [101], 'featured_names': ['测试五星'], 'hard_pity': 70}},
    }


def candidate(directory, version, card_ids, *, base=None, write=None):
    mapping = {'format': 'starfall-gacha-visual-assets', 'schema_version': 1,
               'version': str(version), 'characters': {}, 'files': {}}
    for card_id in card_ids:
        with io.BytesIO() as output:
            Image.new('RGBA', (2, 2), (card_id, 20, 40, 255)).save(output, format='PNG')
            raw = output.getvalue()
        digest = hashlib.sha256(raw).hexdigest()
        relative = f'assets/portraits/{digest}.png'
        mapping['characters'][str(card_id)] = {'path': relative}
        mapping['files'][relative] = {'size': len(raw), 'sha256': digest}
        if write is None or card_id in write:
            image_path = directory / relative
            image_path.parent.mkdir(parents=True, exist_ok=True)
            image_path.write_bytes(raw)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'candidate.json').write_text(json.dumps({'base_sha256': base, 'mapping': mapping}), encoding='utf-8')
    return mapping


class VisualPublicationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.public = self.root / 'gacha_config'

    def descriptor(self):
        current, _ = read_published_catalog(self.public)
        return current.get('visual_assets')

    def process(self, version, card_ids, *, base=None, write=None):
        def processor(staging):
            publish_catalog(catalog(version), staging / 'gacha_config')
            candidate(staging / 'gacha_visual_assets', version, card_ids, base=base, write=write)
            return ProcessResult(version_timestamp=version, processed=True, schedule_enabled=False)

        result = UpdatePipeline(self.root, processor).run_once()
        self.assertTrue(result.processed, result.error)
        return result

    def test_complete_mapping_is_published_before_pointer_and_only_adds_blobs(self):
        self.process(100, [101])
        old = self.descriptor()
        self.assertIsNotNone(old, 'published catalog must link the complete visual manifest')
        first = json.loads((self.public / old['file']).read_bytes())
        old_picture = self.public / next(iter(first['files']))
        old_stat = old_picture.stat()
        self.process(101, [101, 102], base=old['sha256'], write={102})
        descriptor = self.descriptor()
        raw = (self.public / descriptor['file']).read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), descriptor['sha256'])
        self.assertEqual(set(json.loads(raw)['characters']), {'101', '102'})
        self.assertEqual(old_picture.stat().st_mtime_ns, old_stat.st_mtime_ns)
        self.assertEqual(len(list((self.public / 'assets' / 'portraits').glob('*.png'))), 2)
        self.assertNotIn('visual_assets', json.loads((self.public / 'current.json').read_bytes()))

    def test_failed_visual_batch_keeps_last_complete_mapping_with_new_catalog(self):
        self.process(100, [101])
        old = self.descriptor()
        self.assertIsNotNone(old, 'published catalog must link the complete visual manifest')
        self.process(101, [101, 102], base=old['sha256'], write=set())
        latest, _ = read_published_catalog(self.public)
        self.assertEqual(latest['version'], '101')
        self.assertEqual(latest['visual_assets'], old)
        self.assertFalse((self.public / 'visual-assets-101.json').exists())

    def test_retry_uses_prestaged_catalog_bytes_after_pointer_write_failed(self):
        publish_catalog(catalog(100), self.public)
        snapshot = self.root / 'versions' / '101'
        replace = os.replace

        def fail_catalog_pointer(source, target):
            if Path(target) == self.public / 'current.json':
                raise OSError('pointer failure')
            replace(source, target)

        with patch('server_app.gacha_catalog.os.replace', side_effect=fail_catalog_pointer):
            self.process(101, [101])
        saved = (self.public / 'catalog-101.json').read_bytes()
        self.assertEqual(read_published_catalog(self.public)[0]['version'], '100')
        (snapshot / 'gacha_visual_assets' / 'candidate.json').unlink()
        result = UpdatePipeline(self.root, lambda _: ProcessResult()).run_once()
        self.assertEqual(result.warnings, ())
        self.assertEqual(read_published_catalog(self.public)[0]['version'], '101')
        self.assertEqual((self.public / 'catalog-101.json').read_bytes(), saved)
        self.assertEqual(self.descriptor()['version'], '101')

    def test_retry_rejects_malformed_published_catalog_and_keeps_pointer(self):
        publish_catalog(catalog(100), self.public)
        (self.public / 'catalog-101.json').write_bytes(b'[]\n')

        with self.assertRaisesRegex(ValueError, 'invalid published catalog'):
            publish_catalog(catalog(101), self.public, resume_existing=True)

        self.assertEqual(read_published_catalog(self.public)[0]['version'], '100')
