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
        self.assertEqual(config.rerun_schedule_anchor["new_character_id"], 10000224)
        self.assertEqual(config.rerun_schedule_history_through_gacha_id, 24000087)
        self.assertFalse(hasattr(config, "rerun_schedule_refresh_seconds"))
        self.assertEqual(config.rerun_schedule_overrides, ())

    def test_rerun_schedule_anchor_can_be_overridden_from_toml(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        config_path = Path(temp_dir.name) / "config.toml"
        config_path.write_text(
            '''[paths]\ndata_dir = "data"\n[cdn]\ncategories = ["Arts", "Data"]\n[security]\nassetbundle_key = "yunguihaowan1234"\n[rerun_schedule]\nenabled = false\nhistory_through_gacha_id = 8\nrefresh_seconds = 900\n[rerun_schedule.anchor]\nnew_character_id = 7\nnew_character_name = "新角色"\nrerun_character_id = 8\nrerun_character_name = "复刻角色"\nstart_at = "2026-01-01T10:00:00+08:00"\nend_at = "2026-01-22T05:00:00+08:00"\nsource = "operator_confirmed"\n[[rerun_schedule.overrides]]\ngacha_id = 77\npool_kind = "normal"\ncharacter_id = 10000214\nsource = "confirmed in announcement"\n''',
            encoding="utf-8",
        )
        config = load_config(config_path)
        self.assertFalse(config.rerun_schedule_enabled)
        self.assertEqual(config.rerun_schedule_anchor["new_character_id"], 7)
        self.assertEqual(config.rerun_schedule_history_through_gacha_id, 8)
        self.assertEqual(config.rerun_schedule_overrides[0]["gacha_id"], 77)

    def test_polling_setting_is_authoritative_for_test_environment(self):
        config = self._load('environment = "test"\ninstance_id = "test-copy-2"\n')
        self.assertTrue(config.cdn_poll_enabled)
        self.assertEqual(config.instance_id, "test-copy-2")

    def test_polling_can_be_disabled_for_any_environment(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        path = Path(temp_dir.name) / "config.toml"
        path.write_text(
            '[server]\nenvironment="main"\ninstance_id="worker-a"\n'
            '[polling]\nenabled=false\n[paths]\ndata_dir="data"\n'
            '[cdn]\ncategories=["Arts", "Data"]\n'
            '[security]\nassetbundle_key="yunguihaowan1234"\n',
            encoding="utf-8",
        )
        config = load_config(path)
        self.assertFalse(config.cdn_poll_enabled)
        self.assertEqual(config.instance_id, "worker-a")


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


_PORTRAIT_MINIMAL = """[server]
burst_anchor = "2026-09-18T10:00:00+08:00"

[paths]
data_dir = "data"

[cdn]
categories = ["Arts", "Data"]

[security]
assetbundle_key = "yunguihaowan1234"
"""


def test_portraits_disabled_by_default(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(_PORTRAIT_MINIMAL, encoding="utf-8")
    config = load_config(config_path)
    assert config.portrait_enabled is False
    assert config.portrait_tool_dir is None
    assert config.portrait_work_dir is None
    assert config.portrait_outbox_dir is None
    assert config.portrait_memory_max_bytes == 512 * 1024 * 1024
    assert config.portrait_memory_high_bytes == 384 * 1024 * 1024
    assert config.portrait_cpu_affinity == (0,)
    assert config.portrait_timeout_seconds == 900
    assert config.portrait_png_working_rows == 128


def test_portraits_section_parses_paths_and_limits(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        _PORTRAIT_MINIMAL
        + """[portraits]
enabled = true
tool_dir = "../xl_updata_tool"
dotnet_path = "tools/dotnet/dotnet"
scene_cli_path = "tools/scene/PortraitSceneCLI.dll"
query_cli_path = "tools/scene/SpineViewerCLI.dll"
xvfb_run_path = "tools/xvfb/xvfb-run"
assetstudio_cli = "tools/as/AssetStudioCLI"
assetstudio_dotnet = "tools/as/dotnet"
work_dir = "portrait-work"
outbox_dir = "portrait-outbox"
memory_max_bytes = 268435456
memory_high_bytes = 134217728
cpu_affinity = [1, 2]
timeout_seconds = 600
job_timeout_seconds = 1800
map_timeout_seconds = 600
png_working_rows = 64
""",
        encoding="utf-8",
    )
    config = load_config(config_path)
    assert config.portrait_enabled is True
    assert config.portrait_tool_dir == (tmp_path / "../xl_updata_tool").resolve()
    assert config.portrait_dotnet_path == (tmp_path / "tools/dotnet/dotnet").resolve()
    assert config.portrait_query_cli_path == (tmp_path / "tools/scene/SpineViewerCLI.dll").resolve()
    assert config.portrait_xvfb_run_path == (tmp_path / "tools/xvfb/xvfb-run").resolve()
    assert config.portrait_assetstudio_cli == (tmp_path / "tools/as/AssetStudioCLI").resolve()
    assert config.portrait_assetstudio_dotnet == (tmp_path / "tools/as/dotnet").resolve()
    assert config.portrait_work_dir == (tmp_path / "portrait-work").resolve()
    assert config.portrait_outbox_dir == (tmp_path / "portrait-outbox").resolve()
    assert config.portrait_memory_max_bytes == 256 * 1024 * 1024
    assert config.portrait_memory_high_bytes == 128 * 1024 * 1024
    assert config.portrait_cpu_affinity == (1, 2)
    assert config.portrait_timeout_seconds == 600
    assert config.portrait_job_timeout_seconds == 1800
    assert config.portrait_map_timeout_seconds == 600
    assert config.portrait_png_working_rows == 64


def test_portraits_rejects_invalid_limits(tmp_path):
    for snippet in (
        "cpu_affinity = []\n",
        'cpu_affinity = ["0"]\n',
        "memory_max_bytes = 0\n",
        "memory_high_bytes = 536870912\nmemory_max_bytes = 268435456\n",
        "timeout_seconds = 0\n",
        "png_working_rows = 1025\n",
    ):
        config_path = tmp_path / "config.toml"
        config_path.write_text(
            _PORTRAIT_MINIMAL + "[portraits]\nenabled = true\n" + snippet,
            encoding="utf-8",
        )
        try:
            load_config(config_path)
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid portrait settings accepted: {snippet!r}")
