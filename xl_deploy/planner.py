"""Pure branch/path-to-service deployment planning."""

from __future__ import annotations

from dataclasses import dataclass


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
                units.append("xl-qqbot-test.service")
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
                units.extend(("xl-qqbot-prod.service", "xl-qqbot-router.service"))
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
) -> DeploymentPlan:
    if branch not in {"debug", "test", "main"}:
        raise ValueError(f"unsupported deployment branch: {branch}")
    if not sha.strip():
        raise ValueError("sha must be non-empty")
    normalized_paths = tuple(dict.fromkeys(path.replace("\\", "/").lstrip("./") for path in changed_paths))
    target = DeploymentTarget.for_changes(branch, normalized_paths)

    return DeploymentPlan(
        branch=branch,
        sha=sha,
        changed_paths=normalized_paths,
        impacted_units=target.impacted_units,
        impacted_tiers=target.impacted_tiers,
        announcement_scope=target.announcement_scope,
        release_note=release_note,
    )
