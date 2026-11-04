"""Text-only atlas parsing and fresh manifest-consumer import boundaries."""

import faulthandler
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


class PortraitPackageImportTest(unittest.TestCase):
    def setUp(self):
        faulthandler.dump_traceback_later(20, exit=False)
        self.addCleanup(faulthandler.cancel_dump_traceback_later)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def parser(self):
        name = 'app.features.preview.atlas_pages'
        self.assertIsNotNone(importlib.util.find_spec(name), 'stdlib atlas helper is missing')
        from app.features.preview.atlas_pages import _atlas_texture_names
        return _atlas_texture_names

    def run_python(self, code, *args):
        result = subprocess.run([sys.executable, '-c', code, *map(str, args)],
                                capture_output=True, text=True, timeout=30, env=os.environ.copy())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def manifest(self, page='body.png', *, registered=True):
        from app.features.preview.portrait_config.model import (
            AssetRef, PortraitScene, RendererState, ScenePart, SpineState,
        )
        skeleton, atlas, texture, renderer = (AssetRef('bundle', i) for i in range(1, 5))
        spine = SpineState(skeleton, skeleton_json=skeleton, atlases=(atlas,), texture=texture)
        part = ScenePart('body', renderer, 'body', renderer=RendererState('Spine', True), spine=spine)
        scene = PortraitScene('v1', '123', 'skin', 'role', 'skin', None,
                              ('bundle',), (part,), (skeleton, atlas, texture, renderer))
        folder = self.root / 'body'
        folder.mkdir(exist_ok=True)
        files = [('body.skel', b'skeleton', skeleton),
                 ('body.atlas', (page + '\nsize: 2,2\n').encode(), atlas)]
        if registered:
            files.append(('body.png', b'fingerprinted texture bytes', texture))
        resources = []
        for name, data, ref in files:
            (folder / name).write_bytes(data)
            resources.append(dict(path='body/' + name, source=ref.to_dict(),
                                  sha256=hashlib.sha256(data).hexdigest()))
        manifest = dict(package_schema_version=2, scene=scene.to_dict(), resources=resources, inputs={},
                        parts=[dict(part_id='body', directory='body', skeleton_path='body/body.skel',
                                    atlas_paths=['body/body.atlas'], texture_paths=[])])
        path = self.root / 'skin.json'
        path.write_text(json.dumps(manifest), encoding='utf-8')
        return path

    def test_fresh_manifest_consumer_does_not_load_publication_query_or_media(self):
        path = self.manifest()
        self.run_python('''import importlib.abc, sys
blocked = ('app.features.preview.output_publisher', 'app.features.preview.spine_adapter',
           'app.features.preview.resource_catalog', 'PIL', 'PySide6')
class BlockHeavy(importlib.abc.MetaPathFinder):
 def find_spec(self, fullname, path=None, target=None):
  if any(fullname == name or fullname.startswith(name + '.') for name in blocked):
   raise AssertionError('unnecessary consumer import: ' + fullname)
sys.meta_path.insert(0, BlockHeavy())
from app.features.preview.portrait_package import load_portrait_package, package_selectors
package = load_portrait_package(sys.argv[1])
assert package.scene.skin_id == 'skin'
assert package_selectors(package) == ((), {}, ())
assert not any(name in sys.modules for name in blocked)
''', path)

    def test_atlas_pages_keep_suffix_filter_normalization_order_and_exact_deduplication(self):
        parser = self.parser()
        path = self.root / 'body.atlas.txt'
        path.write_text('  first.PNG  \nsize: 2,2\nregion\nfirst.PNG\nFIRST.PNG\n'
                        'dir//second.webp\nthird.JpEg\nfourth.jpg\nfifth.BMP\nsixth.gif\n'
                        'seventh.tga\nignored.svg\nignored.ktx\nC:/ignored.png\n\n', encoding='utf-8')
        self.assertEqual(parser(path), ('first.PNG', 'FIRST.PNG', 'dir/second.webp', 'third.JpEg',
                                       'fourth.jpg', 'fifth.BMP', 'sixth.gif', 'seventh.tga'))

    def test_atlas_invalid_utf8_uses_replacement(self):
        path = self.root / 'body.atlas'
        path.write_bytes(b'bad\xff.png\nvalid.png\n')
        self.assertEqual(self.parser()(path), ('bad\ufffd.png', 'valid.png'))

    def test_atlas_missing_or_directory_returns_empty(self):
        parser = self.parser()
        self.assertEqual(parser(self.root / 'missing.atlas'), ())
        self.assertEqual(parser(self.root), ())

    def test_atlas_read_oserror_returns_empty(self):
        parser = self.parser()
        path = self.root / 'body.atlas'
        path.write_text('body.png\n', encoding='utf-8')
        with patch.object(Path, 'read_text', side_effect=OSError('unreadable')):
            self.assertEqual(parser(path), ())

    def test_original_publisher_name_reexports_same_parser(self):
        self.assertIsNotNone(importlib.util.find_spec('app.features.preview.atlas_pages'),
                             'stdlib atlas helper is missing')
        path = self.root / 'compat.atlas'
        path.write_text('first.PNG\nsize: 2,2\nfirst.PNG\nsecond.jpg\n', encoding='utf-8')
        self.run_python('''import sys
from pathlib import Path
from app.features.preview.atlas_pages import _atlas_texture_names as parser
from app.features.preview.output_publisher import _atlas_texture_names as original
assert original is parser
assert original(Path(sys.argv[1])) == parser(Path(sys.argv[1])) == ('first.PNG', 'second.jpg')
''', path)

    def test_consumer_preserves_atlas_containment_and_fingerprint_checks(self):
        from app.features.preview.portrait_package import load_portrait_package
        for page, registered in (('../outside.png', True), ('/absolute.png', True),
                                 ('..\\outside.png', True),
                                 ('body.png', False)):
            with self.subTest(page=page, registered=registered):
                path = self.manifest(page, registered=registered)
                with self.assertRaises(ValueError):
                    load_portrait_package(path)


if __name__ == '__main__':
    unittest.main()
