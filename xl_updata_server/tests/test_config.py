from datetime import datetime
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server_app.config import load_config


class FixedBurstAnchorConfigTests(unittest.TestCase):
    def _load(self, server_fields=""):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        config_path = Path(temp_dir.name) / "config.toml"
        config_path.write_text(
            f'''[server]
timezone = "Asia/Shanghai"
{server_fields}
[paths]
data_dir = "data"

[cdn]
categories = ["Arts", "Data"]

[security]
assetbundle_key = "yunguihaowan1234"
''',
            encoding="utf-8",
        )
        return load_config(config_path)

    def test_missing_anchor_uses_fixed_epoch_even_after_restart_date_changes(self):
        class LaterRestartDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 10, 16, 12, 0, tzinfo=tz)

        with patch("server_app.config.datetime", LaterRestartDatetime):
            anchor = self._load().burst_anchor

        self.assertEqual(anchor, datetime.fromisoformat("2026-10-09T10:00:00+08:00"))

    def test_dynamic_anchor_aliases_are_rejected(self):
        for alias in ("last_friday", "auto"):
            with self.subTest(alias=alias):
                with self.assertRaisesRegex(ValueError, "timezone-aware ISO datetime"):
                    self._load(f'burst_anchor = "{alias}"\n')

    def test_rerun_schedule_configuration_has_safe_defaults_and_aware_anchor(self):
        config = self._load()
        self.assertTrue(config.rerun_schedule_enabled)
        self.assertEqual(config.rerun_schedule_anchor["new_character_id"], 10000223)
        self.assertEqual(config.rerun_schedule_refresh_seconds, 3600)
        self.assertEqual(config.rerun_schedule_overrides, ())

    def test_rerun_schedule_anchor_can_be_overridden_from_toml(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        config_path = Path(temp_dir.name) / "config.toml"
        config_path.write_text(
            '''[paths]\ndata_dir = "data"\n[cdn]\ncategories = ["Arts", "Data"]\n[security]\nassetbundle_key = "yunguihaowan1234"\n[rerun_schedule]\nenabled = false\nrefresh_seconds = 900\n[rerun_schedule.anchor]\nnew_character_id = 7\nnew_character_name = "新角色"\nrerun_character_id = 8\nrerun_character_name = "复刻角色"\nstart_at = "2026-01-01T10:00:00+08:00"\nend_at = "2026-01-22T05:00:00+08:00"\nsource = "operator_confirmed"\n[[rerun_schedule.overrides]]\ngacha_id = 77\npool_kind = "normal"\ncharacter_id = 10000214\nsource = "confirmed in announcement"\n''',
            encoding="utf-8",
        )
        config = load_config(config_path)
        self.assertFalse(config.rerun_schedule_enabled)
        self.assertEqual(config.rerun_schedule_refresh_seconds, 900)
        self.assertEqual(config.rerun_schedule_anchor["new_character_id"], 7)
        self.assertEqual(config.rerun_schedule_overrides[0]["gacha_id"], 77)


def test_load_config_parses_intervals_and_anchor(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """[server]
normal_interval_seconds = 3600
burst_interval_seconds = 60
burst_duration_seconds = 1200
burst_anchor = "2026-09-18T10:00:00+08:00"
timezone = "Asia/Shanghai"

[paths]
data_dir = "data"

[cdn]
base = "https://example.test/bundles"
categories = ["Arts", "Data"]

[security]
assetbundle_key = "yunguihaowan1234"
""",
        encoding="utf-8",
    )
    loaded = load_config(config_path)
    assert loaded.normal_interval_seconds == 3600
    assert loaded.burst_duration_seconds == 1200
    assert loaded.burst_anchor == datetime.fromisoformat("2026-09-18T10:00:00+08:00")
    assert loaded.selected_categories == ("Arts", "Data")
    assert loaded.assetbundle_key == b"yunguihaowan1234"


def test_load_config_defaults_render_cards_enabled(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """[server]
burst_anchor = "2026-09-18T10:00:00+08:00"

[paths]
data_dir = "data"

[cdn]
categories = ["Arts", "Data"]

[security]
assetbundle_key = "yunguihaowan1234"
""",
        encoding="utf-8",
    )
    assert load_config(config_path).render_cards is True


def test_load_config_parses_cards_disabled(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """[server]
burst_anchor = "2026-09-18T10:00:00+08:00"

[paths]
data_dir = "data"

[cdn]
categories = ["Arts", "Data"]

[security]
assetbundle_key = "yunguihaowan1234"

[cards]
enabled = false
""",
        encoding="utf-8",
    )
    assert load_config(config_path).render_cards is False


def test_load_config_rejects_dynamic_anchor_alias(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """[server]
burst_anchor = "last_friday"

[paths]
data_dir = "data"

[cdn]
categories = ["Arts", "Data"]

[security]
assetbundle_key = "yunguihaowan1234"
""",
        encoding="utf-8",
    )
    try:
        load_config(config_path)
    except ValueError as error:
        assert "timezone-aware ISO datetime" in str(error)
    else:
        raise AssertionError("dynamic burst anchors must be rejected")
