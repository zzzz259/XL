import unittest
from types import SimpleNamespace

from bot_app.service import resolve_instance_id


class ServiceInstanceIdTests(unittest.TestCase):
    def test_configured_instance_id_takes_precedence_over_legacy_tier(self):
        args = SimpleNamespace(instance_id=None, tier="test")
        config = SimpleNamespace(instance=SimpleNamespace(instance_id="test-bot"))

        self.assertEqual(resolve_instance_id(args, config), "test-bot")

    def test_explicit_instance_id_takes_precedence_over_config_and_tier(self):
        args = SimpleNamespace(instance_id="copy-b", tier="test")
        config = SimpleNamespace(instance=SimpleNamespace(instance_id="test-bot"))

        self.assertEqual(resolve_instance_id(args, config), "copy-b")

    def test_legacy_tier_is_used_when_config_has_default_instance_id(self):
        args = SimpleNamespace(instance_id=None, tier="test")
        config = SimpleNamespace(instance=SimpleNamespace(instance_id="default"))

        self.assertEqual(resolve_instance_id(args, config), "test")
