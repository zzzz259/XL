import tempfile
import unittest
from pathlib import Path

from xl_deploy.planner import plan_deployment
from xl_deploy.runner import CommandRunner


class RendererDependencyPreflightTests(unittest.TestCase):
    def test_backend_preflight_installs_and_checks_canvas_in_candidate_release(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            release = root / "release"
            renderer = release / "xl_updata_server" / "renderer"
            renderer.mkdir(parents=True)
            (renderer / "package.json").write_text('{"dependencies":{"canvas":"^3.2.3"}}', encoding="utf-8")
            (release / "xl_updata_server" / "requirements.txt").write_text("", encoding="utf-8")
            (release / "xl_qqbot").mkdir()
            (release / "xl_qqbot" / "requirements.txt").write_text("", encoding="utf-8")
            commands = []
            runner = CommandRunner(executor=lambda args, **kwargs: commands.append((args, kwargs)))
            plan = plan_deployment(
                "main", "a" * 40, ["xl_updata_server/server_app/processor.py"]
            )

            runner.preflight(
                plan,
                release,
                root / "shared" / "config.toml",
                root / "shared" / "data",
            )

            npm_commands = [(args, kwargs) for args, kwargs in commands if args[0] == "npm"]
            canvas_checks = [(args, kwargs) for args, kwargs in commands if args[0] == "node"]
            self.assertEqual(len(npm_commands), 1)
            self.assertEqual(
                npm_commands[0][0],
                ["npm", "install", "--omit=dev", "--no-audit", "--no-fund"],
            )
            self.assertEqual(npm_commands[0][1]["cwd"], renderer)
            self.assertEqual(len(canvas_checks), 1)
            self.assertEqual(canvas_checks[0][0], ["node", "-e", "require('canvas')"])
            self.assertEqual(canvas_checks[0][1]["cwd"], renderer)
