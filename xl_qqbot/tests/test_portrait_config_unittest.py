"""[portraits] 配置：默认关闭、enabled 开关、布尔校验。"""

import os
import tempfile

import pytest

from bot_app.config import load_config


def write_toml(content: str) -> str:
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".toml", delete=False)
    f.write(content)
    f.close()
    return f.name


def test_portraits_disabled_by_default():
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
        assert cfg.portraits.enabled is False
    finally:
        os.unlink(path)


def test_portraits_enabled_flag():
    path = write_toml(
        """
[bot]
appid = "123"
secret = "secret"

[watch]
outbox_dir = "/tmp/outbox"

[portraits]
enabled = true
"""
    )
    try:
        cfg = load_config(path)
        assert cfg.portraits.enabled is True
    finally:
        os.unlink(path)


def test_portraits_enabled_must_be_bool():
    path = write_toml(
        """
[bot]
appid = "123"
secret = "secret"

[watch]
outbox_dir = "/tmp/outbox"

[portraits]
enabled = "yes"
"""
    )
    try:
        with pytest.raises(ValueError, match="portraits"):
            load_config(path)
    finally:
        os.unlink(path)
