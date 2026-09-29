"""Small GitHub REST client with an injectable transport for deterministic tests."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from xl_deploy.config import GitHubConfig


@dataclass(frozen=True)
class _Response:
    status_code: int
    payload: Any

    def json(self) -> Any:
        return self.payload


def _urlopen_transport(method: str, url: str, headers: dict[str, str], timeout: float) -> _Response:
    request = Request(url, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
            return _Response(response.status, payload)
    except HTTPError as exc:
        return _Response(exc.code, None)


Transport = Callable[[str, str, dict[str, str], float], Any]


class GitHubClient:
    """Read GitHub state; callers inject transport and tests never use the network."""

    def __init__(self, config: GitHubConfig, *, transport: Transport | None = None):
        self.config = config
        self._transport = transport or _urlopen_transport

    @property
    def _repository_url(self) -> str:
        owner = quote(self.config.owner, safe="")
        repo = quote(self.config.repo, safe="")
        return f"{self.config.api_url.rstrip('/')}/repos/{owner}/{repo}"

    def _get_json(self, url: str) -> Any:
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.config.token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        try:
            response = self._transport("GET", url, headers, self.config.timeout_seconds)
        except Exception:  # noqa: BLE001 - sanitize arbitrary injected transport errors at this boundary
            # Transport exception strings may include request details; never echo them.
            raise RuntimeError("GitHub API transport failed") from None
        status = getattr(response, "status_code", getattr(response, "status", None))
        if status != 200:
            raise RuntimeError(f"GitHub API returned HTTP {status}")
        try:
            payload = response.json()
        except (ValueError, TypeError, AttributeError):
            raise RuntimeError("GitHub API returned invalid JSON") from None
        return payload

    def latest_sha(self, branch: str) -> str:
        if not branch.strip():
            raise ValueError("branch must be non-empty")
        branch_path = quote(branch, safe="")
        payload = self._get_json(f"{self._repository_url}/branches/{branch_path}")
        try:
            sha = payload["commit"]["sha"]
        except (KeyError, TypeError):
            raise RuntimeError("GitHub branch response did not include a commit SHA") from None
        if not isinstance(sha, str) or not sha.strip():
            raise RuntimeError("GitHub branch response did not include a commit SHA")
        return sha

    def ci_result(self, branch: str, sha: str) -> str:
        """Return the latest exact-branch/SHA XL CI workflow result."""
        if not branch.strip() or not sha.strip():
            raise ValueError("branch and SHA must be non-empty")
        all_runs: list[dict[str, Any]] = []
        total_count: int | None = None
        for page in range(1, self.config.max_pages + 1):
            query = urlencode({
                "head_sha": sha,
                "branch": branch,
                "per_page": self.config.per_page,
                "page": page,
            })
            payload = self._get_json(
                f"{self._repository_url}/actions/runs?{query}"
            )
            if not isinstance(payload, dict):
                raise TypeError("GitHub workflow-runs response has an invalid shape")
            count = payload.get("total_count")
            runs = payload.get("workflow_runs")
            if not isinstance(count, int) or count < 0 or not isinstance(runs, list):
                raise RuntimeError("GitHub workflow-runs response has an invalid shape")
            total_count = count if total_count is None else total_count
            if count != total_count or any(not isinstance(run, dict) for run in runs):
                raise RuntimeError("GitHub workflow-runs pagination changed during inspection")
            all_runs.extend(runs)
            if len(all_runs) >= total_count:
                break

        xl_ci_runs = [
            run for run in all_runs
            if run.get("name") == "XL CI"
            and run.get("head_sha") == sha
            and run.get("head_branch") == branch
        ]
        if not xl_ci_runs:
            return "pending"

        # GitHub lists workflow runs newest first; use IDs as a defensive ordering
        # fallback when test transports or compatible API implementations reorder.
        latest = max(xl_ci_runs, key=lambda run: run.get("id", 0) if isinstance(run.get("id", 0), int) else 0)
        if latest.get("status") != "completed":
            return "pending"
        return "success" if latest.get("conclusion") == "success" else "failure"

    def release_notes(self) -> tuple[dict[str, Any], ...]:
        """Return a bounded set of published GitHub releases, newest first."""
        entries: list[dict[str, Any]] = []
        for page in range(1, self.config.max_pages + 1):
            query = urlencode({"per_page": self.config.per_page, "page": page})
            payload = self._get_json(f"{self._repository_url}/releases?{query}")
            if not isinstance(payload, list) or any(not isinstance(item, dict) for item in payload):
                raise RuntimeError("GitHub releases response has an invalid shape")
            entries.extend(
                item for item in payload
                if item.get("draft") is not True
                and item.get("prerelease") is not True
                and isinstance(item.get("published_at"), str)
                and bool(item["published_at"].strip())
            )
            if len(payload) < self.config.per_page:
                break
        return tuple(entries)
