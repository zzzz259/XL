import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from server_app.config import load_config
from run_server import healthcheck


BASE = '''[paths]
data_dir = "data"

[cdn]
categories = ["Arts", "Data"]

[security]
assetbundle_key = "yunguihaowan1234"
'''


class TestEnvironmentConfig(unittest.TestCase):
    def load_text(self, text: str):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        config_path = Path(directory.name) / "config.toml"
        config_path.write_text(text + BASE, encoding="utf-8")
        return load_config(config_path)

    def test_legacy_configuration_defaults_to_main_polling_and_api_off(self):
        config = self.load_text('[server]\ntimezone = "Asia/Shanghai"\n')

        self.assertEqual(config.environment, "main")
        self.assertTrue(config.cdn_poll_enabled)
        self.assertFalse(config.api_enabled)
        self.assertEqual(config.api_host, "127.0.0.1")

    def test_test_environment_forces_cdn_polling_off_even_when_enabled(self):
        config = self.load_text(
            '[server]\nenvironment = "test"\n'
            '[polling]\nenabled = true\n'
        )

        self.assertEqual(config.environment, "test")
        self.assertFalse(config.cdn_poll_enabled)

    def test_main_environment_respects_polling_disable_setting(self):
        config = self.load_text(
            '[server]\nenvironment = "main"\n'
            '[polling]\nenabled = false\n'
        )

        self.assertEqual(config.environment, "main")
        self.assertFalse(config.cdn_poll_enabled)

    def test_unknown_or_blank_environment_is_rejected(self):
        for value in ("staging", ""):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "environment"):
                    self.load_text(f'[server]\nenvironment = "{value}"\n')

    def test_api_configuration_accepts_loopback_and_valid_secret_variable(self):
        config = self.load_text(
            '[server]\nenvironment = "test"\n'
            '[api]\nenabled = true\nhost = "127.0.0.1"\n'
            'port = 8791\ntoken_env = "XL_TEST_UPDATE_API_TOKEN"\n'
        )

        self.assertTrue(config.api_enabled)
        self.assertEqual(config.api_host, "127.0.0.1")
        self.assertEqual(config.api_port, 8791)
        self.assertEqual(config.api_token_env, "XL_TEST_UPDATE_API_TOKEN")

    def test_enabled_api_rejects_non_loopback_host_bad_port_and_bad_secret_name(self):
        invalid_api = (
            ('host = "0.0.0.0"\nport = 8791\ntoken_env = "XL_TOKEN"', "loopback"),
            ('host = "127.0.0.1"\nport = 70000\ntoken_env = "XL_TOKEN"', "port"),
            ('host = "127.0.0.1"\nport = true\ntoken_env = "XL_TOKEN"', "integer"),
            ('host = "127.0.0.1"\nport = 8791\ntoken_env = "bad-name"', "token_env"),
        )
        for settings, message in invalid_api:
            with self.subTest(settings=settings):
                with self.assertRaisesRegex(ValueError, message):
                    self.load_text('[api]\nenabled = true\n' + settings + "\n")

    def test_read_only_healthcheck_rejects_wrong_expected_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.toml"
            config_path.write_text('[server]\nenvironment = "test"\n' + BASE, encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "expected 'main'"):
                healthcheck(str(config_path), expected_environment="main")

    def test_read_only_healthcheck_allows_empty_first_test_install(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            jar = root / "unluac.jar"
            opmap = root / "opmap"
            jar.touch()
            opmap.mkdir()
            config = SimpleNamespace(
                environment="test",
                data_dir=root / "not-created-yet",
                unluac_jar=jar,
                unluac_opmap=opmap,
                java_bin="java",
                render_cards=False,
            )
            with patch("run_server.load_config", return_value=config), patch(
                "run_server._executable_available", return_value=True
            ):
                self.assertEqual(healthcheck("unused.toml", expected_environment="test"), 0)


if __name__ == "__main__":
    unittest.main()
