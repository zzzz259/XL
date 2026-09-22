import os
import tempfile

import pytest

from bot_app.config import load_config


def write_toml(content: str) -> str:
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".toml", delete=False)
    f.write(content)
    f.close()
    return f.name


def test_load_config_minimal():
    path = write_toml(
        """
[bot]
appid = "123"
secret = "secret"

[watch]
outbox_dir = "/tmp/outbox"
"""
    )
    try:
        cfg = load_config(path)
        assert cfg.bot.appid == "123"
        assert cfg.bot.secret == "secret"
        assert cfg.watch.outbox_dir == "/tmp/outbox"
        assert cfg.watch.interval_seconds == 30
        assert cfg.watch.data_dir == "./data"
        assert cfg.target.group_openids == []
        assert cfg.target.auto_learn_from_events
        assert cfg.upload.file_base_url == ""
        # template 默认为空 = 只发图不配文字
        assert cfg.message.template == ""
        assert cfg.watch.character_data.endswith("current.json")
        assert cfg.watch.versions_dir.endswith("versions")
    finally:
        os.unlink(path)


def test_missing_appid():
    path = write_toml(
        """
[bot]
secret = "secret"
"""
    )
    try:
        with pytest.raises(ValueError, match="appid"):
            load_config(path)
    finally:
        os.unlink(path)


def test_missing_secret():
    path = write_toml(
        """
[bot]
appid = "123"
"""
    )
    try:
        with pytest.raises(ValueError, match="secret"):
            load_config(path)
    finally:
        os.unlink(path)


def test_missing_file():
    with pytest.raises(FileNotFoundError):
        load_config("nonexistent_config.toml")


def test_interval_must_be_positive():
    path = write_toml(
        """
[bot]
appid = "123"
secret = "secret"

[watch]
outbox_dir = "/tmp/outbox"
interval_seconds = 0
"""
    )
    try:
        with pytest.raises(ValueError, match="interval"):
            load_config(path)
    finally:
        os.unlink(path)
