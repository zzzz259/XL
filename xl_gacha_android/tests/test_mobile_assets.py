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
        if tag == "img" and values.get("src"):
            self.assets.append(values["src"])


class MobileAssetTests(unittest.TestCase):
    def test_mobile_shell_is_semantic_local_and_independent(self):
        root = Path(__file__).resolve().parents[1]
        page = root / "web" / "index.html"
        parser = MobilePageParser()
        parser.feed(page.read_text(encoding="utf-8"))
        self.assertEqual(len(parser.ids), len(set(parser.ids)))
        self.assertCountEqual(parser.tabs, ["records", "query", "profile"])
        for element_id in ("view-records", "view-query", "view-profile", "account-summary", "career-summary", "pool-list", "record-floating-actions", "login-options", "login-progress", "raw-records-details", "query-flip", "query-front", "manual-panel", "manual-open", "manual-open-label", "manual-help", "manual-help-dialog", "characters-toggle", "career-evaluation", "recent-evaluation", "career-tags", "account-button-avatar", "profile-account-avatar", "gugu-button", "gugu-idle", "gugu-reaction", "gugu-poster"):
            self.assertIn(element_id, parser.ids)
        for removed_id in ("account-quick-stats", "record-account-updated", "characters-detail", "manual-dialog"):
            self.assertNotIn(removed_id, parser.ids)
        for removed_id in ("pity-summary", "compact-toggle", "remember-login", "login-profile-options", "forget-login", "demo-button", "tap-diagnostics", "bili-diagnostics"):
            self.assertNotIn(removed_id, parser.ids)
        self.assertNotIn("view-overview", parser.ids)
        self.assertNotIn("view-pools", parser.ids)
        self.assertNotIn("login-dialog", parser.ids)
        self.assertGreaterEqual(parser.dialogs, 3)
        self.assertTrue(parser.assets)
        for asset in parser.assets:
            self.assertFalse(asset.startswith(("http://", "https://", "//")))
            name = asset.split("?", 1)[0].split("#", 1)[0].lstrip("/")
            if name.endswith((".js", ".css", ".svg", ".json", ".png", ".webp")):
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

    def test_mobile_instrumentation_javascript_is_valid(self):
        import json
        import re
        import subprocess

        root = Path(__file__).resolve().parents[1]
        source = (root / "tests" / "MobileUiIntegration.java").read_text(encoding="utf-8")
        scripts = []
        for match in re.finditer(r'\b(?:check|await|evaluate)\("', source):
            literal = source[match.end() - 1:]
            script, end = json.JSONDecoder().raw_decode(literal)
            if literal[end:].lstrip().startswith("+"):
                continue
            scripts.append(script)
        self.assertTrue(scripts)
        for script in scripts:
            with self.subTest(script=script[:80]):
                result = subprocess.run(["node", "--input-type=module", "--check"], input=script, encoding="utf-8", capture_output=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_bundled_manifest_matches_disk(self):
        import hashlib
        import json

        root = Path(__file__).resolve().parents[1]
        manifest = json.loads((root / "web" / "assets" / "manifest.json").read_text(encoding="utf-8"))
        catalog = json.loads((root.parent / "xl_gacha_web" / "data" / "catalog.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["resource_version"], "134359843258232660")
        self.assertEqual(set(manifest["characters"]), set(catalog["cards"]))
        self.assertEqual(len(manifest["fashions"]), 312)
        self.assertEqual(len(manifest["account_items"]), 99)
        self.assertEqual(len(manifest["files"]), 418)
        for path, expected in manifest["files"].items():
            self.assertTrue(path.startswith("assets/"), path)
            self.assertNotIn("..", path)
            source = root / "web" / path
            self.assertTrue(source.is_file(), path)
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), expected, path)


if __name__ == "__main__":
    unittest.main()
