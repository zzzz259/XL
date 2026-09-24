"""Non-shell command adapter for Git staging and allowlisted systemd units."""

from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

ALLOWED_UNITS = frozenset(
    {
        "xl-qqbot-debug.service",
        "xl-qqbot-test.service",
        "xl-qqbot-prod.service",
        "xl-qqbot-router.service",
        "xl-updata-server.service",
    }
)
BOT_UNITS = frozenset(
    {
        "xl-qqbot-debug.service",
        "xl-qqbot-test.service",
        "xl-qqbot-prod.service",
        "xl-qqbot-router.service",
    }
)
BACKEND_UNITS = frozenset({"xl-updata-server.service"})
BRANCH_UNITS = {
    "debug": ("xl-qqbot-debug.service",),
    "test": ("xl-qqbot-test.service",),
    "main": (
        "xl-qqbot-prod.service",
        "xl-qqbot-router.service",
        "xl-updata-server.service",
    ),
}
_SHA_PATTERN = frozenset("0123456789abcdefABCDEF")


def _subprocess_executor(args: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, text=True, check=True, capture_output=True, **kwargs)


class CommandRunner:
    """Execute fixed argv vectors; no command string or shell is accepted."""

    def __init__(
        self,
        *,
        repository: Path | str | None = None,
        executor: Callable[..., Any] | None = None,
        health_token: str | None = None,
        health_timeout_seconds: float = 30,
        health_poll_interval_seconds: float = 1,
    ) -> None:
        if health_timeout_seconds <= 0 or health_poll_interval_seconds <= 0:
            raise ValueError("health-check timeouts must be positive")
        self.repository = Path(repository).resolve() if repository is not None else None
        self._executor = executor or _subprocess_executor
        self.health_token = health_token
        self.health_timeout_seconds = health_timeout_seconds
        self.health_poll_interval_seconds = health_poll_interval_seconds

    def systemctl(self, action: str, units: Sequence[str]) -> None:
        if action not in {"start", "stop"}:
            raise ValueError("systemctl action is not allowlisted")
        if not units or any(unit not in ALLOWED_UNITS for unit in units):
            raise ValueError("systemd unit is not in the deployment allowlist")
        if len(set(units)) != len(units):
            raise ValueError("duplicate systemd unit is not allowed")
        self._executor(["systemctl", "--user", action, *units])

    def stage(self, plan: Any, release_path: Path) -> Path:
        sha = _validated_sha(plan.sha)
        if self.repository is None or not self.repository.is_dir():
            raise RuntimeError("deployment source repository is not configured")
        if release_path.exists():
            raise FileExistsError("immutable release target already exists")
        release_path.parent.mkdir(parents=True, exist_ok=True)
        self._executor(["git", "fetch", "--no-tags", "origin", sha], cwd=self.repository)
        self._executor(
            ["git", "worktree", "add", "--detach", str(release_path), sha],
            cwd=self.repository,
        )
        return release_path

    def preflight(
        self,
        plan: Any,
        release_path: Path,
        config_path: Path,
        data_path: Path,
    ) -> None:
        _validated_sha(plan.sha)
        _assert_outside(release_path, config_path, "configuration")
        _assert_outside(release_path, data_path, "runtime data")
        impacted_units = tuple(plan.impacted_units)
        if not impacted_units or len(set(impacted_units)) != len(impacted_units):
            raise ValueError("preflight requires unique impacted systemd units")
        try:
            units = BRANCH_UNITS[plan.branch]
        except KeyError as exc:
            raise ValueError("preflight branch is not supported") from exc
        if any(unit not in units for unit in impacted_units):
            raise ValueError("impacted units do not belong to the deployment branch")
        venv_root = release_path / ".venvs"
        if venv_root.is_symlink() or (venv_root.exists() and not venv_root.is_dir()):
            raise ValueError("candidate .venvs path must be a real directory")
        for unit in units:
            if unit not in ALLOWED_UNITS:
                raise ValueError("systemd unit is not in the deployment allowlist")
            if unit in BOT_UNITS:
                requirements = release_path / "xl_qqbot" / "requirements.txt"
            elif unit in BACKEND_UNITS:
                requirements = release_path / "xl_updata_server" / "requirements.txt"
            else:
                raise ValueError("systemd unit has no dependency manifest mapping")
            if not requirements.is_file():
                raise RuntimeError("candidate is missing a required dependency manifest")
            environment_path = service_venv_path(release_path, unit)
            if environment_path.exists() or environment_path.is_symlink():
                raise RuntimeError("service venv path already exists in candidate release")
            python = environment_path / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            self._executor(
                [sys.executable, "-m", "venv", str(environment_path)]
            )
            self._executor(
                [str(python), "-m", "pip", "install", "--requirement", str(requirements)],
                cwd=release_path,
            )
        source_roots = (
            release_path / "xl_deploy",
            release_path / "xl_qqbot" / "bot_app",
            release_path / "xl_updata_server" / "server_app",
        )
        sources = [
            source
            for source_root in source_roots
            if source_root.is_dir()
            for source in source_root.rglob("*.py")
        ]
        backend_entry = release_path / "xl_updata_server" / "run_server.py"
        if backend_entry.is_file():
            sources.append(backend_entry)
        for source in sources:
            compile(source.read_text(encoding="utf-8"), str(source), "exec")

    def stop(self, units: Sequence[str]) -> None:
        self.systemctl("stop", units)

    def start(self, units: Sequence[str]) -> None:
        self.systemctl("start", units)

    def health_check(self, units: Sequence[str]) -> None:
        for unit in units:
            if unit not in ALLOWED_UNITS:
                raise ValueError("systemd unit is not in the deployment allowlist")
            self._executor(["systemctl", "--user", "is-active", "--quiet", unit])
            port = {
                "xl-qqbot-debug.service": 8781,
                "xl-qqbot-test.service": 8782,
                "xl-qqbot-prod.service": 8783,
                "xl-qqbot-router.service": 8784,
            }.get(unit)
            if port is not None:
                headers = {}
                if unit == "xl-qqbot-router.service":
                    if not self.health_token:
                        raise RuntimeError("router health token is not configured")
                    headers["Authorization"] = f"Bearer {self.health_token}"
                request = urllib.request.Request(
                    f"http://127.0.0.1:{port}/health", headers=headers
                )
                self._wait_for_http_health(request, unit)

    def _wait_for_http_health(self, request: urllib.request.Request, unit: str) -> None:
        deadline = time.monotonic() + self.health_timeout_seconds
        last_error: BaseException | None = None
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError(f"health endpoint timed out for {unit}") from last_error
            try:
                with urllib.request.urlopen(request, timeout=min(5, remaining)) as response:
                    if response.status == 200:
                        return
                    last_error = RuntimeError(f"health endpoint returned {response.status}")
                    if response.status not in {502, 503, 504}:
                        raise last_error
            except urllib.error.HTTPError as exc:
                if exc.code not in {502, 503, 504}:
                    raise RuntimeError(f"health endpoint failed for {unit}") from exc
                last_error = exc
            except (OSError, urllib.error.URLError) as exc:
                last_error = exc
            time.sleep(min(self.health_poll_interval_seconds, max(0, deadline - time.monotonic())))


def _validated_sha(sha: object) -> str:
    if not isinstance(sha, str) or len(sha) != 40 or any(char not in _SHA_PATTERN for char in sha):
        raise ValueError("SHA must be exactly 40 hexadecimal characters")
    return sha.lower()


def service_venv_path(release_path: Path | str, unit: str) -> Path:
    if unit not in ALLOWED_UNITS:
        raise ValueError("systemd unit is not in the deployment allowlist")
    return Path(release_path) / ".venvs" / unit


def _assert_outside(release_path: Path, path: Path, label: str) -> None:
    release = release_path.resolve()
    candidate = path.resolve()
    try:
        candidate.relative_to(release)
    except ValueError:
        return
    raise ValueError(f"{label} path must remain outside immutable releases")
