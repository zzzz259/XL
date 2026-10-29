import tempfile
import unittest
from pathlib import Path

from build import ANDROID_FILES, STATIC_FILES, prepare_assets
from toolchain import executable, sdk_paths


class ToolchainTests(unittest.TestCase):
    def test_apk_assets_exclude_local_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            desktop = root / "desktop"
            android = root / "android"
            (desktop / "static").mkdir(parents=True)
            (desktop / "data").mkdir()
            (android / "web").mkdir(parents=True)
            for name in STATIC_FILES:
                (desktop / "static" / name).write_text("synthetic")
            for name in ("catalog.json", "update-source.json", "private.json"):
                (desktop / "data" / name).write_text("synthetic")
            (desktop / "static" / "private.log").write_text("private")
            for name in (*ANDROID_FILES, "mobile-index.html"):
                (android / "web" / name).write_text("synthetic")
            assets = root / "assets"
            prepare_assets(desktop, android, assets)
            self.assertFalse((assets / "private.json").exists())
            self.assertFalse((assets / "private.log").exists())
            self.assertTrue((assets / "catalog.json").exists())

    def test_standard_and_legacy_sdk_layout(self):
        for platform, tools in [("platforms", "35.0.0"), ("platform", "android-15")]:
            with (
                self.subTest(platform=platform),
                tempfile.TemporaryDirectory() as folder,
            ):
                root = Path(folder)
                jar = root / platform / "android-35/android.jar"
                jar.parent.mkdir(parents=True)
                jar.touch()
                build = root / "build-tools" / tools
                (build / "lib").mkdir(parents=True)
                (build / "lib/d8.jar").touch()
                executable(build, "aapt2").touch()
                self.assertEqual(sdk_paths(root), (jar, build))
