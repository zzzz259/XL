"""B 站定时密集检测：窗口判定（含跨零点）、配置校验。"""

import os
import tempfile
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from bot_app.bilibili import parse_burst_times, select_poll_interval
from bot_app.config import BilibiliConfig, BotConfig, Config, MessageConfig, TargetConfig, UploadConfig, WatchConfig, load_config

TZ = ZoneInfo("Asia/Shanghai")


def make_config(data_dir="/tmp/data", **bili_kwargs):
    return Config(
        bot=BotConfig(appid="1", secret="2"),
        watch=WatchConfig(outbox_dir="/tmp/outbox", interval_seconds=30, data_dir=data_dir),
        target=TargetConfig(group_openids=[], auto_learn_from_events=False),
        upload=UploadConfig(file_base_url=""),
        message=MessageConfig(template=""),
        bilibili=BilibiliConfig(**bili_kwargs),
    )


def at(hour, minute, tz=TZ):
    return datetime(2026, 9, 22, hour, minute, tzinfo=tz)


# ---------- parse_burst_times ----------

def test_parse_burst_times_ok():
    assert parse_burst_times(["10:00", "17:30"]) == [(10, 0), (17, 30)]
    assert parse_burst_times([]) == []
    assert parse_burst_times(None) == []


@pytest.mark.parametrize("bad", ["abc", "10", "10:0", "10:60", "25:00", "-1:00", "10:00:00"])
def test_parse_burst_times_invalid(bad):
    with pytest.raises(ValueError, match="burst_times"):
        parse_burst_times([bad])


# ---------- select_poll_interval ----------

TIMES = [(10, 0), (17, 0)]


def test_window_inside_uses_burst_interval():
    assert select_poll_interval(at(10, 0), 300, TIMES, 300, 60) == 60
    assert select_poll_interval(at(10, 4, ), 300, TIMES, 300, 60) == 60  # 窗口末尾内
    assert select_poll_interval(at(17, 2), 300, TIMES, 300, 60) == 60


def test_window_outside_uses_normal_interval():
    assert select_poll_interval(at(10, 5), 300, TIMES, 300, 60) == 300  # 窗口刚结束
    assert select_poll_interval(at(9, 59), 300, TIMES, 300, 60) == 300
    assert select_poll_interval(at(12, 0), 300, TIMES, 300, 60) == 300


def test_window_cross_midnight():
    late = [(23, 58)]
    assert select_poll_interval(at(23, 59), 300, late, 300, 60) == 60
    # 跨零点：第二天 00:02 仍处昨天 23:58 起的窗口内
    assert select_poll_interval(at(0, 2), 300, late, 300, 60) == 60
    assert select_poll_interval(at(0, 6), 300, late, 300, 60) == 300  # 窗口已结束


def test_empty_burst_times_falls_back_to_normal():
    assert select_poll_interval(at(10, 0), 300, [], 300, 60) == 300
    # window 为 0 视为关闭密集模式
    assert select_poll_interval(at(10, 0), 300, TIMES, 0, 60) == 300


# ---------- watcher 集成 ----------

class _FrozenDatetime(datetime):
    """固定时钟：now(tz) 返回预设时刻（tz 即配置时区，验证窗口判定对齐）。"""

    fixed = datetime(2026, 9, 22, 10, 1, tzinfo=TZ)

    @classmethod
    def now(cls, tz=None):
        return cls.fixed if tz is None else cls.fixed.astimezone(tz)


def make_watcher(tmp_path, monkeypatch, fixed, **bili_kwargs):
    from bot_app import bilibili as bilibili_module
    from bot_app.bilibili import BilibiliWatcher

    _FrozenDatetime.fixed = fixed
    monkeypatch.setattr(bilibili_module, "datetime", _FrozenDatetime)
    bili_kwargs.setdefault("timezone", "Asia/Shanghai")
    return BilibiliWatcher(make_config(data_dir=str(tmp_path), **bili_kwargs), sender=object())


def test_watcher_next_interval_inside_window(tmp_path, monkeypatch):
    watcher = make_watcher(tmp_path, monkeypatch, at(10, 1),
                           interval_seconds=300, burst_times=["10:00"],
                           burst_window_seconds=300, burst_interval_seconds=60)
    assert watcher.next_interval_seconds() == 60


def test_watcher_next_interval_outside_window(tmp_path, monkeypatch):
    watcher = make_watcher(tmp_path, monkeypatch, at(12, 0),
                           interval_seconds=300, burst_times=["10:00"],
                           burst_window_seconds=300, burst_interval_seconds=60)
    assert watcher.next_interval_seconds() == 300


def test_watcher_invalid_burst_times_raises_at_init(tmp_path):
    from bot_app.bilibili import BilibiliWatcher

    with pytest.raises(ValueError, match="burst_times"):
        BilibiliWatcher(make_config(data_dir=str(tmp_path), burst_times=["25:99"]), sender=object())


# ---------- 配置加载 ----------

def write_toml(content):
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".toml", delete=False, encoding="utf-8")
    f.write(content)
    f.close()
    return f.name


BASE = """
[bot]
appid = "1"
secret = "2"

[watch]
outbox_dir = "/tmp/outbox"
"""


def test_config_burst_defaults():
    path = write_toml(BASE)
    try:
        cfg = load_config(path)
        assert cfg.bilibili.burst_times == ["10:00", "17:00"]
        assert cfg.bilibili.burst_window_seconds == 300
        assert cfg.bilibili.burst_interval_seconds == 60
        assert cfg.bilibili.timezone == "Asia/Shanghai"
    finally:
        os.unlink(path)


def test_config_burst_override():
    path = write_toml(BASE + """
[bilibili]
burst_times = ["08:30"]
burst_window_seconds = 600
burst_interval_seconds = 30
timezone = "UTC"
""")
    try:
        cfg = load_config(path)
        assert cfg.bilibili.burst_times == ["08:30"]
        assert cfg.bilibili.burst_window_seconds == 600
        assert cfg.bilibili.burst_interval_seconds == 30
        assert cfg.bilibili.timezone == "UTC"
    finally:
        os.unlink(path)


def test_config_empty_burst_times_allowed():
    path = write_toml(BASE + """
[bilibili]
burst_times = []
""")
    try:
        cfg = load_config(path)
        assert cfg.bilibili.burst_times == []
    finally:
        os.unlink(path)


@pytest.mark.parametrize("bad", ["10:60", "25:00", "abc"])
def test_config_invalid_burst_time_raises(bad):
    path = write_toml(BASE + f"""
[bilibili]
burst_times = ["{bad}"]
""")
    try:
        with pytest.raises(ValueError, match="burst_times"):
            load_config(path)
    finally:
        os.unlink(path)


def test_config_invalid_timezone_raises():
    path = write_toml(BASE + """
[bilibili]
timezone = "Mars/Olympus"
""")
    try:
        with pytest.raises(ValueError, match="timezone"):
            load_config(path)
    finally:
        os.unlink(path)


@pytest.mark.parametrize("key", ["burst_window_seconds", "burst_interval_seconds"])
def test_config_nonpositive_burst_numbers_raise(key):
    path = write_toml(BASE + f"""
[bilibili]
{key} = 0
""")
    try:
        with pytest.raises(ValueError, match=key):
            load_config(path)
    finally:
        os.unlink(path)
