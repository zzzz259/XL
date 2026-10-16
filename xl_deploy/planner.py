"""Pure branch/path-to-service deployment planning."""

from __future__ import annotations

from dataclasses import dataclass

from xl_deploy.config import DeploymentInventory


@dataclass(frozen=True)
class DeploymentTarget:
    impacted_units: tuple[str, ...]
    impacted_tiers: tuple[str, ...]
    announcement_scope: tuple[str, ...]

    @classmethod
    def for_changes(cls, branch: str, changed_paths: list[str] | tuple[str, ...]) -> DeploymentTarget:
        if branch not in {"debug", "test", "main"}:
            raise ValueError(f"unsupported deployment branch: {branch}")
        normalized_paths = tuple(path.replace("\\", "/").lstrip("./") for path in changed_paths)
        qqbot_changed = any(_is_runtime_path(path, "xl_qqbot") for path in normalized_paths)
        backend_changed = any(_is_runtime_path(path, "xl_updata_server") for path in normalized_paths)

        if branch == "test":
            units: list[str] = []
            tiers: tuple[str, ...] = ()
            notice: tuple[str, ...] = ()
            if qqbot_changed:
                units.extend(
                    (
                        "xl-qqbot-test.service",
                        "xl-qqbot-test-notify.service",
                        "xl-qqbot-test-jobs.service",
                    )
                )
                tiers = ("debug", "test")
                notice = ("debug", "test")
            if backend_changed:
                units.append("xl-updata-server-test.service")
                tiers = tuple(dict.fromkeys((*tiers, "debug", "test")))
                notice = tuple(dict.fromkeys((*notice, "debug", "test")))
            return cls(tuple(units), tiers, notice)
        if branch == "main":
            units: list[str] = []
            tiers: tuple[str, ...] = ()
            notice: tuple[str, ...] = ()
            if qqbot_changed:
                units.extend(
                    (
                        "xl-qqbot-prod.service",
                        "xl-qqbot-router.service",
                        "xl-qqbot-main-notify.service",
                        "xl-qqbot-control.service",
                        "xl-qqbot-main-jobs.service",
                    )
                )
                # The router owns the shared gateway and forwards for every tier.
                tiers = ("debug", "test", "production")
                notice = ("main",)
            if backend_changed:
                units.append("xl-updata-server.service")
                tiers = tuple(dict.fromkeys((*tiers, "production")))
                notice = tuple(dict.fromkeys((*notice, "main")))
            return cls(tuple(units), tiers, notice)
        return cls((), (), ())


@dataclass(frozen=True)
class DeploymentPlan:
    branch: str
    sha: str
    changed_paths: tuple[str, ...]
    impacted_units: tuple[str, ...]
    impacted_tiers: tuple[str, ...]
    announcement_scope: tuple[str, ...]
    release_note: str | None = None


def _is_runtime_path(path: str, root: str) -> bool:
    if not (path == root or path.startswith(f"{root}/")):
        return False
    parts = path.split("/")
    if any(part in {"docs", "tests", "__pycache__"} for part in parts[1:]):
        return False
    return not path.lower().endswith((".md", ".rst", ".txt")) or path.endswith("requirements.txt")


def plan_deployment(
    branch: str,
    sha: str,
    changed_paths: list[str] | tuple[str, ...],
    *,
    release_note: str | None = None,
    inventory: DeploymentInventory | None = None,
) -> DeploymentPlan:
    if branch not in {"debug", "test", "main"}:
        raise ValueError(f"unsupported deployment branch: {branch}")
    if not sha.strip():
        raise ValueError("sha must be non-empty")
    normalized_paths = tuple(dict.fromkeys(path.replace("\\", "/").lstrip("./") for path in changed_paths))
    target = (configured_target(branch, normalized_paths, inventory) if inventory is not None
              else DeploymentTarget.for_changes(branch, normalized_paths))

    return DeploymentPlan(
        branch=branch,
        sha=sha,
        changed_paths=normalized_paths,
        impacted_units=target.impacted_units,
        impacted_tiers=target.impacted_tiers,
        announcement_scope=target.announcement_scope,
        release_note=release_note,
    )


def configured_target(branch, paths, inventory):
    """Select instances by snapshot ref, never by the spelling of their IDs."""
    from xl_deploy.announcements import instance_announcement_scope
    inventory.validate()
    if branch == 'debug':
        return DeploymentTarget((), (), ())
    bot = any(_is_runtime_path(p, 'xl_qqbot') for p in paths)
    backend = any(_is_runtime_path(p, 'xl_updata_server') for p in paths)
    gateway = inventory.gateway.watch_ref == branch and any(
        p in {*(f'xl_qqbot/bot_app/{name}.py' for name in
                ('gateway', 'config', 'deploy_control', 'groups', 'sender', 'tiers', 'updater')),
               'xl_qqbot/requirements.txt'} for p in paths)
    units, ids = [], []
    for instance in inventory.instances:
        if not instance.enabled or instance.watch_ref != branch: continue
        selected = [unit for role, unit in instance.units.items()
                    if (backend if role == 'backend' else bot)]
        if selected:
            units.extend(selected)
            if 'bot' in instance.units:
                ids.append(instance.instance_id)
    if gateway:
        units.append(inventory.gateway.unit)
        ids = list(dict.fromkeys([*ids, *(i.instance_id for i in inventory.instances if i.enabled and 'bot' in i.units)]))
    return DeploymentTarget(tuple(units), tuple(ids), instance_announcement_scope(inventory, ids))
