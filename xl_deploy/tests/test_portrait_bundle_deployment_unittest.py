import tempfile
import unittest
from pathlib import Path

from xl_deploy.runner import ALLOWED_UNITS, BRANCH_UNITS, CommandRunner

ROOT = Path(__file__).resolve().parents[1]


class PortraitDeploymentTests(unittest.TestCase):
    def test_stage_includes_preview_and_private_layout_without_desktop_tree(self):
        calls = []
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            runner = CommandRunner(
                repository=root, executor=lambda args, **kwargs: calls.append(args)
            )
            runner.stage(
                __import__("types").SimpleNamespace(sha="a" * 40), root / "release"
            )
        sparse = next(args for args in calls if args[:2] == ["git", "sparse-checkout"])
        for path in (
            "/xl_updata_tool/app/features/preview/",
            "/xl_updata_tool/runtime/portrait/",
        ):
            self.assertIn(path, sparse)
        self.assertNotIn("/xl_updata_tool/tools/SpineViewer/", sparse)

    def test_companions_are_standalone_and_capped(self):
        for tier in ("main", "test"):
            unit = "xl-portrait-" + tier + ".service"
            self.assertNotIn(unit, ALLOWED_UNITS)
            self.assertNotIn(unit, BRANCH_UNITS[tier])
            text = (ROOT / "deploy" / unit).read_text()
            for line in (
                "CPUQuota=100%",
                "CPUAffinity=1",
                "MemoryMax=256M",
                "MemorySwapMax=0",
                "LimitCORE=0",
                "KillMode=control-group",
                "TimeoutStopSec=30",
            ):
                self.assertIn(line, text)
            self.assertNotIn("[Install]", text)
            self.assertIn("server_app.portraits.cli daemon --enable --config", text)
            self.assertNotIn("/current/", text)
            self.assertIn("/usr/bin/env ${PORTRAIT_PYTHON}", text)
            self.assertIn("EnvironmentFile=", text)

    def test_reader_roots_same_revision(self):
        for tier in ("main", "test"):
            for suffix in ("jobs", "notify"):
                text = (
                    ROOT / "deploy" / f"xl-qqbot-{tier}-{suffix}.service"
                ).read_text()
                line = next(
                    line
                    for line in text.splitlines()
                    if line.startswith("Environment=PYTHONPATH=")
                )
                self.assertIn(f"/current/{tier}/xl_updata_server", line)


if __name__ == "__main__":
    unittest.main()
