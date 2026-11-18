import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server_app.cdn import CdnClient
from server_app.config import load_config


class GachaVisualRuntimeTests(unittest.TestCase):
    def test_version_metadata_retains_other_for_fairygui_pages(self):
        client = CdnClient('https://example.test/Bundles')
        payload = {'timestamp': 123, 'data': [
            {'name': category, 'hash': str(n), 'ver': n}
            for n, category in enumerate(('Arts', 'Data', 'Other', 'Video'), 1)
        ]}
        with patch.object(client, '_get_bytes', return_value=json.dumps(payload).encode()) as fetch:
            catalog = client.fetch_version_catalog('version.json')
        self.assertEqual([category.name for category in catalog.categories], ['Arts', 'Data', 'Other'])
        fetch.assert_called_once_with('https://example.test/Bundles/version.json')

    def test_visual_work_is_explicitly_enabled_per_instance(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'config.toml'
            path.write_text('', encoding='utf-8')
            self.assertFalse(load_config(path).gacha_visual_enabled)
            path.write_text('[gacha_visual_assets]\nenabled = true\n', encoding='utf-8')
            self.assertTrue(load_config(path).gacha_visual_enabled)
            path.write_text('[gacha_visual_assets]\nenabled = "true"\n', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'enabled must be a boolean'):
                load_config(path)
