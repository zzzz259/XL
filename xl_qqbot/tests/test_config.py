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


def test_load_config_parses_instance_identity():
    path = write_toml(
        '''
[bot]
appid = "123"
secret = "secret"

[instance]
id = "test-blue"

[watch]
outbox_dir = "/tmp/outbox"
'''
    )
    try:
        cfg = load_config(path)
        assert cfg.instance.instance_id == "test-blue"
    finally:
        os.unlink(path)


def test_gateway_config_routes_groups_to_explicit_instance_ports():
    path = write_toml(
        '''
[bot]
appid = "123"
secret = "secret"

[watch]
outbox_dir = "/tmp/outbox"

[router.group_instances]
"group-one" = "test-blue"
"group-two" = "test-green"

[router.instance_ports]
test-blue = 8812
test-green = 8813

[gateway.instance_tokens]
test-blue = "test-blue-instance-token-00000000"
test-green = "test-green-instance-token-000000"
'''
    )
    try:
        cfg = load_config(path)
        assert cfg.router.group_instances == {
            "group-one": "test-blue",
            "group-two": "test-green",
        }
        assert cfg.router.instance_ports == {"test-blue": 8812, "test-green": 8813}
    finally:
        os.unlink(path)


def test_gateway_config_rejects_group_route_without_instance_worker():
    path = write_toml(
        '''
[bot]
appid = "123"
secret = "secret"

[watch]
outbox_dir = "/tmp/outbox"

[router.group_instances]
"group-one" = "missing"

[router.instance_ports]
test-blue = 8812
'''
    )
    try:
        with pytest.raises(ValueError, match="unknown instance"):
            load_config(path)
    finally:
        os.unlink(path)


def test_gateway_config_rejects_duplicate_instance_ports():
    path = write_toml(
        '''
[bot]
appid = "123"
secret = "secret"

[watch]
outbox_dir = "/tmp/outbox"

[router.instance_ports]
test-blue = 8812
test-green = 8812
'''
    )
    try:
        with pytest.raises(ValueError, match="ports must be unique"):
            load_config(path)
    finally:
        os.unlink(path)


def test_gateway_config_rejects_duplicate_group_after_normalization():
    path = write_toml(
        '''
[bot]
appid = "123"
secret = "secret"

[watch]
outbox_dir = "/tmp/outbox"

[router.group_instances]
"group-one" = "test-blue"
" group-one " = "test-green"

[router.instance_ports]
test-blue = 8812
test-green = 8813
'''
    )
    try:
        with pytest.raises(ValueError, match="group IDs must be unique"):
            load_config(path)
    finally:
        os.unlink(path)
