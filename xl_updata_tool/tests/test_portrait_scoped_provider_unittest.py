import inspect
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from app.features.preview.portrait_config.asset_index import AssetIndex, IndexedAsset
from app.features.preview.portrait_config.model import AssetRef
from app.features.preview.portrait_config.unity_scene import parse_unity_scene

H = 'a' * 32
C = 'b' * 32

class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.active = False
        self.closed = 0
        self.obj = SimpleNamespace(path_id=1, type=SimpleNamespace(name='Material'),
            assets_file=SimpleNamespace(name='CAB-' + C, externals=[]))
        def tree():
            self.assertTrue(self.active)
            return {'m_Name': 'material'}
        self.obj.read_typetree = tree

    @contextmanager
    def provider(self, path):
        self.assertFalse(self.active)
        self.active = True
        try:
            yield SimpleNamespace(objects=[self.obj])
        finally:
            self.active = False
            self.closed += 1

    def test_scoped_parse(self):
        self.assertIn('environment_provider', inspect.signature(parse_unity_scene).parameters)
        with patch('app.features.preview.portrait_config.unity_scene._load_environment', side_effect=AssertionError('default loader forbidden')):
            result = parse_unity_scene(Path('private.bundle'), H, 1, environment_provider=self.provider)
        self.assertEqual(result.version_timestamp, 1)
        self.assertEqual(self.closed, 1)

    def test_all_index_metadata_paths_use_scoped_provider(self):
        self.assertIn('environment_provider', inspect.signature(AssetIndex).parameters)
        asset = IndexedAsset('material', '', H, 1, (), 1, Path('private.bundle'), True, '', 'Material', C)
        index = AssetIndex(1, (asset,), environment_provider=self.provider)
        with patch('app.features.preview.portrait_config.unity_scene._load_environment', side_effect=AssertionError('default loader forbidden')):
            self.assertEqual(index.find_external('CAB-' + C, 1), (asset,))
            self.assertEqual(index.object_tree(AssetRef(H, 1)), {'m_Name': 'material'})
            self.assertEqual(index.object_references(asset), ())
            self.assertEqual(self.closed, 3)
            index.object_tree(AssetRef(H, 1))
            self.assertEqual(self.closed, 3)

    def test_protected_provider_failure_propagates_after_close(self):
        self.assertIn('environment_provider', inspect.signature(AssetIndex).parameters)
        @contextmanager
        def broken(path):
            try:
                raise RuntimeError('typed boundary')
                yield
            finally:
                self.closed += 1
        index = AssetIndex(1, (), environment_provider=broken)
        with self.assertRaisesRegex(RuntimeError, 'typed boundary'):
            index._serialized_identities(Path('private.bundle'))
        self.assertEqual(self.closed, 1)
        with self.assertRaisesRegex(RuntimeError, 'typed boundary'):
            parse_unity_scene(Path('private.bundle'), H, environment_provider=broken)
        self.assertEqual(self.closed, 2)

if __name__ == '__main__':
    unittest.main()
