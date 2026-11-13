import importlib.util
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "build_portable", Path(__file__).parents[1] / "scripts/build_portable.py"
)
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


class ResourceTests(unittest.TestCase):
    def test_unsupported_build_python_keeps_friendly_early_error(self):
        with (
            patch.object(build.sys, "platform", "win32"),
            patch.object(build.sys, "version_info", (3, 10)),
            patch.object(build.struct, "calcsize", return_value=8),
            patch.object(
                build.sys, "argv", ["build_portable.py", "--output", "unused"]
            ),
            self.assertRaisesRegex(SystemExit, "Python 3.11"),
        ):
            build.main()

    def test_packaged_readme_links_point_to_repository_guides(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            build.prepare_instructions(Path(__file__).parents[1], output)
            text = (output / "使用说明.md").read_text(encoding="utf-8")
            links = re.findall(r"\]\(([^)]+)\)", text)
            self.assertIn(
                "https://github.com/zzzz259/XL/blob/debug/docs/gacha-user-guide.md",
                links,
            )
            self.assertTrue(
                all(
                    link.startswith("https://github.com/zzzz259/XL/blob/debug/docs/")
                    for link in links
                )
            )

    def test_packaged_data_uses_exact_resource_allowlist(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "data").mkdir()
            (root / "static").mkdir()
            for name in (
                "catalog.json",
                "update-source.json",
                "app.ico",
                "private.json",
            ):
                (root / "data" / name).write_text("synthetic")
            for name in build.STATIC_FILES:
                (root / "static" / name).write_text("synthetic")
            (root / "static" / "private.log").write_text("not a resource")
            output = root / "package"
            build.prepare_resources(root, output)
            self.assertEqual(
                sorted(p.name for p in (output / "data").iterdir()),
                ["app.ico", "catalog.json", "update-source.json"],
            )
            self.assertFalse((output / "static" / "private.log").exists())
