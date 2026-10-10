import json
import tempfile
import unittest
from pathlib import Path

from xl_deploy.__main__ import (
    PollerRuntimeConfig,
    _validate_backend_runtime_configs,
    load_runtime_config,
)
from xl_deploy.runner import CommandRunner


def write_config(root: Path, *, test_config="/srv/test/config.toml", test_data="/srv/test/data"):
    path = root / "deploy.toml"
    path.write_text(
        """[github]
owner = "owner"
repo = "repo"

[deployment]
root = "/srv/deploy"
repository = "/srv/repo"
state_dir = "/srv/deploy/state"
releases_root = "/srv/deploy/releases"
current_root = "/srv/deploy/current"
backend_config = "/srv/main/config.toml"
backend_data = "/srv/main/data"
test_backend_config = """ + json.dumps(test_config) + """
test_backend_data = """ + json.dumps(test_data) + """

[router]
base_url = "http://127.0.0.1:8784"
token_file = "/srv/deploy/router.token"
""",
        encoding="utf-8",
    )
    return path


class TestRuntimeIsolation(unittest.TestCase):
    def test_runtime_config_keeps_test_and_main_paths_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = load_runtime_config(
                write_config(root, test_config="test/config.toml", test_data="test/data")
            )

            self.assertEqual(runtime.test_backend_config, (root / "test/config.toml").resolve())
            self.assertEqual(runtime.test_backend_data, (root / "test/data").resolve())
        self.assertNotEqual(runtime.backend_config, runtime.test_backend_config)
        self.assertNotEqual(runtime.backend_data, runtime.test_backend_data)

    def test_runtime_config_rejects_config_or_data_aliasing(self):
        for test_config, test_data in (
            ("/srv/main/config.toml", "/srv/test/data"),
            ("/srv/test/config.toml", "/srv/main/data"),
        ):
            with (
                self.subTest(test_config=test_config, test_data=test_data),
                tempfile.TemporaryDirectory() as directory,
                self.assertRaisesRegex(ValueError, "distinct"),
            ):
                load_runtime_config(
                    write_config(
                        Path(directory), test_config=test_config, test_data=test_data
                    )
                )

    def test_runtime_config_files_must_match_environment_and_external_data_mapping(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            main_config = root / "main.toml"
            test_config = root / "test.toml"
            for config_path, environment, port, data_dir in (
                (main_config, "main", 8790, "main-data"),
                (test_config, "test", 8791, "test-data"),
            ):
                config_path.write_text(
                    f'[server]\nenvironment = "{environment}"\n'
                    f'[paths]\ndata_dir = "{data_dir}"\n'
                    '[api]\nenabled = true\nhost = "127.0.0.1"\n'
                    f'port = {port}\ntoken_env = "XL_UPDATE_API_TOKEN"\n',
                    encoding="utf-8",
                )
            runtime = PollerRuntimeConfig(
                github_owner="o", github_repo="r", deployment_root=root,
                repository=root, state_dir=root / "state", releases_root=root / "releases",
                current_root=root / "current", backend_config=main_config,
                backend_data=root / "main-data", test_backend_config=test_config,
                test_backend_data=root / "test-data", router_base_url="http://127.0.0.1:8784",
                router_token_file=root / "router.token",
            )

            _validate_backend_runtime_configs(runtime)

            test_config.write_text(
                test_config.read_text(encoding="utf-8").replace('environment = "test"', 'environment = "main"'),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "wrong environment"):
                _validate_backend_runtime_configs(runtime)

    def test_backend_health_check_uses_branch_release_config_and_loopback_port(self):
        for unit, branch, config, environment, port in (
            (
                "xl-updata-server-test.service", "test", "/srv/test/config.toml", "test", 8791
            ),
            (
                "xl-updata-server.service", "main", "/srv/main/config.toml", "main", 8790
            ),
        ):
            with self.subTest(unit=unit):
                commands = []
                runner = CommandRunner(
                    executor=lambda args, commands=commands, **kwargs: commands.append((args, kwargs)),
                    backend_config_path="/srv/main/config.toml",
                    test_backend_config_path="/srv/test/config.toml",
                    deployment_root="/srv/deploy",
                )
                requests = []
                runner._wait_for_http_health = lambda request, checked_unit, requests=requests: requests.append(
                    (request.full_url, checked_unit)
                )
                runner.health_check((unit,))

                health_command = commands[-1][0]
                expected_python = Path("/srv/deploy") / "current" / branch / ".venvs" / unit
                self.assertEqual(Path(health_command[0]).parent.parent, expected_python)
                configured_path = Path(health_command[health_command.index("--config") + 1])
                self.assertEqual(configured_path.parts[-3:], Path(config).parts[-3:])
                self.assertEqual(health_command[-1], environment)
                self.assertEqual(requests, [(f"http://127.0.0.1:{port}/healthz", unit)])

    def test_bootstrap_installs_test_server_but_not_debug_worker(self):
        script = (Path(__file__).parents[1] / "scripts" / "bootstrap.sh").read_text(
            encoding="utf-8"
        )
        unit_list = script.split("unit_names=(", 1)[1].split(")", 1)[0]
        self.assertIn("xl-updata-server-test.service", unit_list)
        self.assertIn("xl-updata.slice", unit_list)
        self.assertNotIn("xl-qqbot-debug.service", unit_list)

    def test_backend_units_use_separate_paths_and_shared_one_core_slice(self):
        deploy_dir = Path(__file__).parents[1] / "deploy"
        main = (deploy_dir / "xl-updata-server.service").read_text(encoding="utf-8")
        test = (deploy_dir / "xl-updata-server-test.service").read_text(encoding="utf-8")
        budget = (deploy_dir / "xl-updata.slice").read_text(encoding="utf-8")

        self.assertIn("Slice=xl-updata.slice", main)
        self.assertIn("Slice=xl-updata.slice", test)
        self.assertIn("CPUQuota=100%", budget)
        self.assertIn("current/main", main)
        self.assertIn("current/test", test)
        self.assertIn("/home/admin/xl_updata_server/config.toml", main)
        self.assertIn("/home/admin/xl_updata_server-test/config.toml", test)
        self.assertIn("/home/admin/.config/xl_updata_server/api.env", main)
        self.assertIn("/home/admin/.config/xl_updata_server-test/api.env", test)


if __name__ == "__main__":
    unittest.main()
