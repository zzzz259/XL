import os
from pathlib import Path

import pytest

from xl_deploy.__main__ import load_runtime_config, read_secret


def write_config(root: Path) -> Path:
    config = root / "config.toml"
    config.write_text(
        '[github]\nowner="owner"\nrepo="repo"\n'
        '[deployment]\nroot="deploy"\nrepository="deploy/repository"\n'
        'state_dir="deploy/state"\nreleases_root="deploy/releases"\n'
        'current_root="deploy/current"\nbackend_config="backend/config.toml"\n'
        'backend_data="backend/data"\ntest_backend_config="test/backend.toml"\n'
        'test_backend_data="test/data"\n'
        '[router]\nbase_url="http://127.0.0.1:8784"\ntoken_file="secrets/router.token"\n',
        encoding="utf-8",
    )
    return config


def test_runtime_config_resolves_all_paths_relative_to_config_file(tmp_path):
    config = load_runtime_config(write_config(tmp_path))

    assert config.github_owner == "owner"
    assert config.github_repo == "repo"
    assert config.deployment_root == (tmp_path / "deploy").resolve()
    assert config.repository == (tmp_path / "deploy" / "repository").resolve()
    assert config.current_root == (tmp_path / "deploy" / "current").resolve()
    assert config.backend_config == (tmp_path / "backend" / "config.toml").resolve()
    assert config.test_backend_config == (tmp_path / "test" / "backend.toml").resolve()
    assert config.test_backend_data == (tmp_path / "test" / "data").resolve()
    assert config.router_base_url == "http://127.0.0.1:8784"


def test_runtime_config_rejects_non_loopback_router_control_url(tmp_path):
    path = write_config(tmp_path)
    path.write_text(path.read_text(encoding="utf-8").replace("127.0.0.1", "0.0.0.0"), encoding="utf-8")

    with pytest.raises(ValueError, match="loopback"):
        load_runtime_config(path)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8784@attacker.example",
        "http://user@127.0.0.1:8784",
        "http://127.0.0.1.evil.example:8784",
    ],
)
def test_runtime_config_rejects_urls_that_only_look_like_loopback(tmp_path, url):
    path = write_config(tmp_path)
    path.write_text(path.read_text(encoding="utf-8").replace("http://127.0.0.1:8784", url), encoding="utf-8")

    with pytest.raises(ValueError, match="loopback"):
        load_runtime_config(path)


def test_router_secret_file_must_not_be_group_or_world_readable(tmp_path):
    secret = tmp_path / "router.token"
    secret.write_text("private-token\n", encoding="utf-8")
    if os.name != "nt":
        secret.chmod(0o640)

        with pytest.raises(ValueError, match="permissions"):
            read_secret(secret)
    else:
        assert read_secret(secret) == "private-token"
