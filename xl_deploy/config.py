"""Validated, secret-safe GitHub API configuration."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlparse


@dataclass(frozen=True)
class GitHubConfig:
    owner: str
    repo: str
    token: str = field(repr=False)
    api_url: str = "https://api.github.com"
    timeout_seconds: float = 10.0
    max_pages: int = 3
    per_page: int = 100

    def __post_init__(self) -> None:
        if not self.owner.strip() or "/" in self.owner:
            raise ValueError("GitHub owner must be a non-empty path component")
        if not self.repo.strip() or "/" in self.repo:
            raise ValueError("GitHub repo must be a non-empty path component")
        if not self.token.strip() or "\n" in self.token or "\r" in self.token:
            raise ValueError("GitHub token must be non-empty and single-line")
        parsed_url = urlparse(self.api_url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise ValueError("GitHub API URL must be an absolute HTTP(S) URL")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if not 1 <= self.max_pages <= 10:
            raise ValueError("max_pages must be between 1 and 10")
        if not 1 <= self.per_page <= 100:
            raise ValueError("per_page must be between 1 and 100")

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> GitHubConfig:
        values = os.environ if environ is None else environ
        repository = values.get("GITHUB_REPOSITORY", "").strip()
        parts = repository.split("/")
        token = values.get("GITHUB_TOKEN", values.get("GH_TOKEN", "")).strip()
        if len(parts) != 2 or not all(parts) or not token:
            raise ValueError("GITHUB_REPOSITORY and GITHUB_TOKEN are required")
        try:
            timeout = float(values.get("XL_DEPLOY_GITHUB_TIMEOUT", "10"))
            max_pages = int(values.get("XL_DEPLOY_GITHUB_MAX_PAGES", "3"))
            per_page = int(values.get("XL_DEPLOY_GITHUB_PER_PAGE", "100"))
        except ValueError as exc:
            raise ValueError("GitHub pagination and timeout settings must be numeric") from exc
        return cls(
            owner=parts[0],
            repo=parts[1],
            token=token,
            api_url=values.get("XL_DEPLOY_GITHUB_API_URL", "https://api.github.com").strip(),
            timeout_seconds=timeout,
            max_pages=max_pages,
            per_page=per_page,
        )
