import re
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path

from build import prepare_assets


class Ids(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []

    def handle_starttag(self, tag, attrs):
        self.ids.extend(value for key, value in attrs if key == 'id')


class MobileAssetsTests(unittest.TestCase):
    def test_mobile_entry_preserves_real_controls_and_has_no_duplicate_ids(self):
        root = Path(__file__).resolve().parents[1]
        desktop = root.parent / 'xl_gacha_web'
        with tempfile.TemporaryDirectory() as folder:
            assets = Path(folder) / 'assets'
            prepare_assets(desktop, root, assets)
            html = (assets / 'index.html').read_text(encoding='utf-8')
            self.assertIn('mobile-ui.css', html, 'APK still uses the desktop entry')
            self.assertIn('mobile-ui.js', html)
            self.assertNotIn('href="/theme.css"', html)
            parser = Ids()
            parser.feed(html)
            self.assertEqual(len(parser.ids), len(set(parser.ids)))
            consumers = ('app.js', 'local.js', 'login.js', 'image-export.js', 'catalog-ui.js')
            for name in consumers:
                script = (desktop / 'static' / name).read_text(encoding='utf-8')
                required = set(re.findall(r"\$\(['\"]([^'\"]+)['\"]\)", script))
                self.assertFalse(required - set(parser.ids), f'{name} missing {required - set(parser.ids)}')
            for name in ('mobile-ui.js', 'mobile-ui.css', 'android-bridge.js'):
                self.assertTrue((assets / name).is_file())
            self.assertNotIn('https://fonts.', html)

    def test_bilibili_entry_is_enabled_only_by_the_build_option(self):
        root = Path(__file__).resolve().parents[1]
        desktop = root.parent / 'xl_gacha_web'
        with tempfile.TemporaryDirectory() as folder:
            for enabled in (False, True):
                assets = Path(folder) / str(enabled)
                prepare_assets(desktop, root, assets, bilibili_enabled=enabled)
                html = (assets / 'index.html').read_text(encoding='utf-8')
                button = re.search(r'<button id="bili-start"[^>]*>', html).group()
                self.assertEqual(' hidden ' in button, not enabled)


if __name__ == '__main__':
    unittest.main()
