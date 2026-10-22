"""Run one branch polling pass: ``python -m xl_deploy --config ...``."""

from __future__ import annotations

import argparse
import json
import logging
import os
import stat
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

from xl_deploy.config import DeploymentInventory, GitHubConfig
from xl_deploy.github import GitHubClient
from xl_deploy.poller import BranchPoller, PollCursorStore
from xl_deploy.release_storage import ReleaseStorage
from xl_deploy.router_client import RouterControlClient, validate_loopback_url
from xl_deploy.runner import ALLOWED_UNITS, CommandRunner
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
    inventory: DeploymentInventory | None = None


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
            inventory=(DeploymentInventory.from_mapping(values["inventory"], base_dir=config_file.parent)
                       if "inventory" in values else None),
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
    if result.inventory is not None:
        result.inventory.validate(releases_root=result.releases_root)
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
    if runtime.inventory is None:
        _validate_backend_runtime_configs(runtime)
    else:
        runtime.inventory.validate_runtime_configs()
    try:
        github_token = os.environ["GITHUB_TOKEN"].strip()
    except KeyError as exc:
        raise RuntimeError("GITHUB_TOKEN environment variable is required") from exc
    github = GitHubClient(
        GitHubConfig(owner=runtime.github_owner, repo=runtime.github_repo, token=github_token)
    )
    router_token = read_secret(runtime.router_token_file)
    router = RouterControlClient(runtime.router_base_url, router_token, inventory=runtime.inventory)
    runner = CommandRunner(
        inventory=runtime.inventory,
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
    storage = ReleaseStorage(runtime.repository, state, runtime.current_root,
                             units=runner.allowed_units)
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
        return DeploymentTransaction(state, runner, router, paths, inventory=runtime.inventory,
                                     release_storage=storage)

    return BranchPoller(
        github,
        runner,
        transaction_for,
        PollCursorStore(runtime.state_dir / "poll-cursors.json"),
        inventory=runtime.inventory,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Poll GitHub branches and deploy verified updates")
    parser.add_argument("--config", default="/home/admin/xl_deploy/config.toml")
    parser.add_argument('--cleanup-releases', action='store_true', help='report release retention as JSON')
    parser.add_argument('--apply', action='store_true', help='apply cleanup (requires --cleanup-releases)')
    parser.add_argument('--include-legacy', action='store_true', help='include verified unrecorded worktrees')
    parser.add_argument(
        "--dry-run-inventory",
        action="store_true",
        help="validate configured instance/runtime ownership and print a redacted summary",
    )
    args = parser.parse_args(argv)
    if (args.apply or args.include_legacy) and not args.cleanup_releases:
        parser.error('--apply/--include-legacy require --cleanup-releases')
    if args.cleanup_releases and args.dry_run_inventory:
        parser.error('choose one read/maintenance operation')
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        if args.cleanup_releases:
            runtime = load_runtime_config(args.config)
            state = DeploymentState(runtime.state_dir, releases_root=runtime.releases_root,
                                    current_path=runtime.current_root)
            storage = ReleaseStorage(runtime.repository, state, runtime.current_root,
                units=runtime.inventory.allowed_units if runtime.inventory else ALLOWED_UNITS)
            report = storage.cleanup(apply=args.apply, include_legacy=args.include_legacy)
            print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
            return 1 if report['blocked'] else 0
        if args.dry_run_inventory:
            runtime = load_runtime_config(args.config)
            if runtime.inventory is None:
                print("No [inventory] configured; legacy fixed test/main mapping is active.")
            else:
                print(render_inventory_dry_run(runtime.inventory))
            return 0
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


def render_inventory_dry_run(inventory: DeploymentInventory) -> str:
    """Validate external runtime mappings and return a summary without secrets."""
    inventory.validate_runtime_configs()
    lines = ["read-only inventory check: valid"]
    lines.append(
        f"gateway unit={inventory.gateway.unit} watch_ref={inventory.gateway.watch_ref} "
        f"port={inventory.gateway.listen_port} groups={len(inventory.group_routes)} "
        f"data_roots={len(inventory.gateway.data_roots)}"
    )
    for instance in inventory.instances:
        if not instance.enabled:
            lines.append(f"disabled instance_id={instance.instance_id}")
            continue
        units = ",".join(f"{role}={unit}" for role, unit in sorted(instance.units.items()))
        ports = ",".join(f"{role}={port}" for role, port in sorted(instance.listen_ports.items()))
        lines.append(
            f"instance_id={instance.instance_id} watch_ref={instance.watch_ref} "
            f"units={units} ports={ports} groups={len(instance.groups)} "
            f"data_roots={len(instance.data_roots)}"
        )
    lines.append("No service, pointer, external config, or data was changed.")
    return "\n".join(lines)


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
