from datetime import datetime

from server_app.config import load_config


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


def test_load_config_last_friday_anchor(tmp_path):
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
    anchor = load_config(config_path).burst_anchor
    assert anchor.weekday() == 4  # 周五
    assert (anchor.hour, anchor.minute) == (10, 0)
    assert anchor.tzinfo is not None
    # 锚点必须是"最近一个"周五：不晚于现在，且距今不足 7 天
    from datetime import datetime, timedelta

    now = datetime.now(anchor.tzinfo)
    assert anchor <= now
    assert now - anchor < timedelta(days=7)
