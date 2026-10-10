"""Run one branch polling pass: ``python -m xl_deploy --config ...``."""

from __future__ import annotations

import argparse
import logging
import os
import stat
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

from xl_deploy.config import GitHubConfig
from xl_deploy.github import GitHubClient
from xl_deploy.poller import BranchPoller, PollCursorStore
from xl_deploy.router_client import RouterControlClient, validate_loopback_url
from xl_deploy.runner import CommandRunner
from xl_deploy.state import DeploymentState
from xl_deploy.transaction import DeploymentPaths, DeploymentTransaction

LOGGER = logging.getLogger("xl_deploy")


@dataclass(frozen=True)
class PollerRuntimeConfig:
    github_owner: str
    github_repo: str
    deployment_root: Path
    repository: Path
    state_dir: Path
    releases_root: Path
    current_root: Path
    backend_config: Path
    backend_data: Path
    test_backend_config: Path
    test_backend_data: Path
    router_base_url: str
    router_token_file: Path


def load_runtime_config(config_path: str | Path) -> PollerRuntimeConfig:
    config_file = Path(config_path).expanduser().resolve()
    try:
        values = tomllib.loads(config_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise RuntimeError("deployment configuration cannot be loaded") from exc
    github = _section(values, "github")
    deployment = _section(values, "deployment")
    router = _section(values, "router")
    try:
        result = PollerRuntimeConfig(
            github_owner=str(github["owner"]),
            github_repo=str(github["repo"]),
            deployment_root=_path(config_file, deployment["root"]),
            repository=_path(config_file, deployment["repository"]),
            state_dir=_path(config_file, deployment["state_dir"]),
            releases_root=_path(config_file, deployment["releases_root"]),
            current_root=_path(config_file, deployment["current_root"]),
            backend_config=_path(config_file, deployment["backend_config"]),
            backend_data=_path(config_file, deployment["backend_data"]),
            test_backend_config=_path(config_file, deployment["test_backend_config"]),
            test_backend_data=_path(config_file, deployment["test_backend_data"]),
            router_base_url=str(router["base_url"]),
            router_token_file=_path(config_file, router["token_file"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("deployment configuration is incomplete or invalid") from exc
    if not result.github_owner.strip() or not result.github_repo.strip():
        raise ValueError("GitHub owner and repository must be non-empty")
    if result.backend_config == result.test_backend_config:
        raise ValueError("test and production backend config paths must be distinct")
    if result.backend_data == result.test_backend_data:
        raise ValueError("test and production backend data paths must be distinct")
    for path in (result.backend_config, result.backend_data,
                 result.test_backend_config, result.test_backend_data):
        try:
            path.relative_to(result.releases_root.resolve())
        except ValueError:
            continue
        raise ValueError("backend config and data paths must remain outside immutable releases")
    validate_loopback_url(result.router_base_url)
    return result


def read_secret(path: str | Path) -> str:
    secret_path = Path(path)
    info = secret_path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("router token must be a regular file")
    if os.name != "nt" and stat.S_IMODE(info.st_mode) & 0o077:
        raise ValueError("router token file permissions must exclude group and other access")
    value = secret_path.read_text(encoding="utf-8").strip()
    if not value or "\n" in value or "\r" in value:
        raise ValueError("router token file is empty or invalid")
    return value


def build_poller(config_path: str | Path) -> BranchPoller:
    runtime = load_runtime_config(config_path)
    _validate_backend_runtime_configs(runtime)
    try:
        github_token = os.environ["GITHUB_TOKEN"].strip()
    except KeyError as exc:
        raise RuntimeError("GITHUB_TOKEN environment variable is required") from exc
    github = GitHubClient(
        GitHubConfig(owner=runtime.github_owner, repo=runtime.github_repo, token=github_token)
    )
    router_token = read_secret(runtime.router_token_file)
    router = RouterControlClient(runtime.router_base_url, router_token)
    runner = CommandRunner(
        repository=runtime.repository,
        health_token=router_token,
        backend_config_path=runtime.backend_config,
        test_backend_config_path=runtime.test_backend_config,
        deployment_root=runtime.deployment_root,
    )
    state = DeploymentState(
        runtime.state_dir,
        releases_root=runtime.releases_root,
        current_path=runtime.current_root,
    )
    def transaction_for(branch: str) -> DeploymentTransaction:
        config_path, data_path = (
            (runtime.test_backend_config, runtime.test_backend_data)
            if branch == "test"
            else (runtime.backend_config, runtime.backend_data)
        )
        paths = DeploymentPaths(
            releases_root=runtime.releases_root,
            current_path=runtime.current_root,
            config_path=config_path,
            data_path=data_path,
        )
        return DeploymentTransaction(state, runner, router, paths)

    return BranchPoller(
        github,
        runner,
        transaction_for,
        PollCursorStore(runtime.state_dir / "poll-cursors.json"),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Poll GitHub branches and deploy verified updates")
    parser.add_argument("--config", default="/home/admin/xl_deploy/config.toml")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        results = build_poller(args.config).poll_all()
    except Exception as exc:  # noqa: BLE001 - avoid logging credentials from nested transport errors.
        LOGGER.error("deployment poll failed (%s)", type(exc).__name__)
        return 1
    failed = False
    for result in results:
        LOGGER.info("branch=%s status=%s sha=%s", result.branch, result.status, result.sha or "-")
        if result.status in {"error", "ci_failed", "recovery_pending"} or result.status.startswith("deployment_"):
            failed = True
    return 1 if failed else 0


def _section(config: dict, name: str) -> dict:
    value = config.get(name)
    if not isinstance(value, dict):
        raise TypeError(f"missing [{name}] configuration section")
    return value


def _path(config_file: Path, value: object) -> Path:
    path = Path(str(value)).expanduser()
    return path.resolve() if path.is_absolute() else (config_file.parent / path).resolve()


def _validate_backend_runtime_configs(runtime: PollerRuntimeConfig) -> None:
    expected = (
        (runtime.backend_config, runtime.backend_data, "main", 8790),
        (runtime.test_backend_config, runtime.test_backend_data, "test", 8791),
    )
    for config_path, expected_data, environment, port in expected:
        try:
            values = tomllib.loads(config_path.read_text(encoding="utf-8"))
            paths = _section(values, "paths")
            server = _section(values, "server")
            api = _section(values, "api")
            actual_data = _path(config_path, paths["data_dir"])
        except (OSError, UnicodeError, tomllib.TOMLDecodeError, KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(f"{environment} backend config is missing or invalid") from exc
        if actual_data != expected_data:
            raise ValueError(f"{environment} backend config data_dir does not match deployment mapping")
        if server.get("environment") != environment:
            raise ValueError(f"{environment} backend config has the wrong environment")
        if (
            api.get("enabled") is not True
            or api.get("host") != "127.0.0.1"
            or api.get("port") != port
            or api.get("token_env") != "XL_UPDATE_API_TOKEN"
        ):
            raise ValueError(f"{environment} backend API must use its assigned loopback port and token variable")


if __name__ == "__main__":
    sys.exit(main())
