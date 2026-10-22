import importlib.util
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "build_portable", Path(__file__).parents[1] / "scripts/build_portable.py"
)
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


class ResourceTests(unittest.TestCase):
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
