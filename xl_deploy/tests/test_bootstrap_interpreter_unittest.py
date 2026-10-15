import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "xl_deploy" / "scripts" / "bootstrap.sh"


class BootstrapInterpreterSelectionTests(unittest.TestCase):
    def test_toml_inventory_uses_python_with_tomllib(self):
        script = SCRIPT.read_text(encoding="utf-8")

        self.assertIn("INVENTORY_PYTHON", script)
        self.assertIn("import tomllib", script)
        self.assertRegex(
            script,
            r'\$CURRENT/main/\.venvs/xl-deploy-poll\.service/bin/python',
        )
        self.assertRegex(
            script,
            r'\$CURRENT/test/\.venvs/xl-deploy-poll\.service/bin/python',
        )
        self.assertIn("python3.11", script)
        self.assertIn('"$INVENTORY_PYTHON" - "$config_path"', script)
        self.assertIn(
            '"$INVENTORY_PYTHON" "$REPO_ROOT/xl_deploy/operator_inventory.py"',
            script,
        )

    def test_system_python3_is_not_used_for_toml_or_inventory_parsing(self):
        script = SCRIPT.read_text(encoding="utf-8")

        self.assertNotIn('python3 - "$config_path"', script)
        self.assertNotIn(' | python3 "$REPO_ROOT/xl_deploy/operator_inventory.py"', script)


if __name__ == "__main__":
    unittest.main()
