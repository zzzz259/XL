import unittest
from html.parser import HTMLParser
from pathlib import Path


class MobilePageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []
        self.tabs = []
        self.assets = []
        self.dialogs = 0

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if "id" in values:
            self.ids.append(values["id"])
        if values.get("data-tab"):
            self.tabs.append(values["data-tab"])
        if tag == "dialog":
            self.dialogs += 1
        if tag == "script" and values.get("src"):
            self.assets.append(values["src"])
        if tag == "link" and values.get("href"):
            self.assets.append(values["href"])


class MobileAssetTests(unittest.TestCase):
    def test_mobile_shell_is_semantic_local_and_independent(self):
        root = Path(__file__).resolve().parents[1]
        page = root / "web" / "index.html"
        parser = MobilePageParser()
        parser.feed(page.read_text(encoding="utf-8"))
        self.assertEqual(len(parser.ids), len(set(parser.ids)))
        self.assertCountEqual(parser.tabs, ["records", "query", "profile"])
        for element_id in ("view-records", "view-query", "view-profile", "account-summary", "career-summary", "pity-summary", "pool-list", "record-floating-actions", "login-options", "login-progress", "raw-records-details"):
            self.assertIn(element_id, parser.ids)
        self.assertNotIn("view-overview", parser.ids)
        self.assertNotIn("view-pools", parser.ids)
        self.assertNotIn("login-dialog", parser.ids)
        self.assertGreaterEqual(parser.dialogs, 3)
        self.assertTrue(parser.assets)
        for asset in parser.assets:
            self.assertFalse(asset.startswith(("http://", "https://", "//")))
            name = asset.split("?", 1)[0].split("#", 1)[0].lstrip("/")
            if name.endswith((".js", ".css", ".svg", ".json")):
                self.assertTrue((root / "web" / name).is_file() or name in {"demo.json", "favicon.svg", "image-export.js"})
        self.assertNotIn("/app.js", parser.assets)
        self.assertNotIn("/style.css", parser.assets)
        self.assertNotIn("/theme.css", parser.assets)

    def test_controller_referenced_ids_exist(self):
        import re

        root = Path(__file__).resolve().parents[1]
        parser = MobilePageParser()
        parser.feed((root / "web" / "index.html").read_text(encoding="utf-8"))
        declared = set(parser.ids)
        for name in ("mobile-app.js", "mobile-render.js", "mobile-image.js"):
            source = (root / "web" / name).read_text(encoding="utf-8")
            referenced = set(re.findall(r"\$\('([A-Za-z0-9-]+)'\)", source))
            self.assertFalse(referenced - declared, f"{name}: {sorted(referenced - declared)}")


if __name__ == "__main__":
    unittest.main()
