"""One-shot branch polling with durable per-branch cursors."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from xl_deploy.announcements import ReleaseNoteSelector
from xl_deploy.planner import plan_deployment

_BRANCHES = ("debug", "test", "main")
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


@dataclass(frozen=True)
class PollResult:
    branch: str
    status: str
    sha: str | None = None
    detail: str = ""


class PollCursorStore:
    """Atomic JSON state outside immutable releases; one cursor per branch."""

    def __init__(self, path: str | Path):
        self.path = Path(path).absolute()
        with _LOCKS_GUARD:
            self._lock = _LOCKS.setdefault(str(self.path), threading.Lock())

    def get_cursor(self, branch: str) -> str | None:
        payload = self._load()
        value = payload["cursors"].get(_branch(branch))
        if value is not None and not _valid_sha(value):
            raise RuntimeError("poll cursor state contains an invalid SHA")
        return value

    def set_cursor(self, branch: str, sha: str) -> None:
        branch = _branch(branch)
        if not _valid_sha(sha):
            raise ValueError("poll cursor SHA must be 40 hexadecimal characters")
        with self._lock:
            payload = self._load()
            payload["cursors"][branch] = sha.lower()
            self._save(payload)

    def recorded_releases(self) -> tuple[set[str], set[str]]:
        payload = self._load()
        return set(payload["release_tags"]), set(payload["release_shas"])

    def record_release(self, tag: str, sha: str) -> None:
        with self._lock:
            payload = self._load()
            if tag:
                payload["release_tags"] = sorted(set(payload["release_tags"]) | {tag})
            if sha:
                payload["release_shas"] = sorted(set(payload["release_shas"]) | {sha.lower()})
            self._save(payload)

    def _load(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"cursors": {}, "release_tags": [], "release_shas": []}
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("poll cursor state is unreadable") from exc
        if not isinstance(payload, dict) or set(payload) != {"cursors", "release_tags", "release_shas"}:
            raise RuntimeError("poll cursor state has an invalid schema")
        cursors, tags, shas = payload["cursors"], payload["release_tags"], payload["release_shas"]
        if (
            not isinstance(cursors, dict)
            or any(key not in _BRANCHES or not _valid_sha(value) for key, value in cursors.items())
            or not isinstance(tags, list)
            or any(not isinstance(value, str) for value in tags)
            or not isinstance(shas, list)
            or any(not _valid_sha(value) for value in shas)
        ):
            raise RuntimeError("poll cursor state has invalid values")
        return {"cursors": dict(cursors), "release_tags": list(tags), "release_shas": list(shas)}

    def _save(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix="poll-state.", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(payload, stream, ensure_ascii=False, sort_keys=True, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            if os.name != "nt":
                directory_fd = os.open(self.path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        except BaseException:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise


class BranchPoller:
    """Inspect and deploy one branch without adopting uninitialized live paths."""

    def __init__(
        self,
        github: Any,
        runner: Any,
        transaction_for_branch: Callable[[str], Any],
        cursors: PollCursorStore,
        *,
        release_selector: ReleaseNoteSelector | None = None,
    ):
        self.github = github
        self.runner = runner
        self.transaction_for_branch = transaction_for_branch
        self.cursors = cursors
        self.release_selector = release_selector or ReleaseNoteSelector()

    def poll_all(self) -> tuple[PollResult, ...]:
        results = []
        for branch in _BRANCHES:
            try:
                results.append(self.poll_branch(branch))
            except Exception as exc:  # noqa: BLE001 - isolate branches but expose no secrets.
                results.append(PollResult(branch, "error", detail=_safe_error(exc)))
        return tuple(results)

    def poll_branch(self, branch: str) -> PollResult:
        branch = _branch(branch)
        transaction = self.transaction_for_branch(branch)
        recovery = transaction.recover()
        if recovery.status not in {
            "nothing_to_recover", "already_recovered", "aborted", "rolled_back", "completed",
        }:
            return PollResult(branch, "recovery_pending", recovery.sha, recovery.message)

        pointer = transaction.paths.current_path / branch
        current_release = transaction.state.get_current(current_path=pointer)
        if current_release is None:
            raise RuntimeError(f"{branch} current release is missing; explicit bootstrap is required")
        cursor = self.cursors.get_cursor(branch) or _release_sha(current_release)

        latest_sha = self.github.latest_sha(branch)
        if not _valid_sha(latest_sha):
            raise RuntimeError("GitHub returned an invalid branch SHA")
        latest_sha = latest_sha.lower()
        active_sha = _release_sha(current_release)
        if latest_sha == active_sha and cursor != latest_sha:
            self.cursors.set_cursor(branch, latest_sha)
            return PollResult(branch, "up_to_date", latest_sha)
        if cursor == latest_sha:
            if self.cursors.get_cursor(branch) is None:
                self.cursors.set_cursor(branch, cursor)
            return PollResult(branch, "up_to_date", latest_sha)

        ci_result = self.github.ci_result(branch, latest_sha)
        if ci_result == "pending":
            return PollResult(branch, "ci_pending", latest_sha)
        if ci_result != "success":
            return PollResult(branch, "ci_failed", latest_sha, ci_result)

        changed_paths = self.runner.changed_paths(cursor, latest_sha)
        plan = plan_deployment(branch, latest_sha, changed_paths)
        if not plan.impacted_units:
            self.cursors.set_cursor(branch, latest_sha)
            return PollResult(branch, "no_op", latest_sha)

        release_note = None
        selected_note = None
        if branch == "main" and plan.announcement_scope:
            entries = self.github.release_notes()
            recorded_tags, recorded_shas = self.cursors.recorded_releases()
            selected_note = self.release_selector.select(
                entries, recorded_tags=recorded_tags, recorded_shas=recorded_shas
            )
            release_note = selected_note.content[:4000] if selected_note else None
            plan = plan_deployment(branch, latest_sha, changed_paths, release_note=release_note)

        result = transaction.execute(plan)
        if result.status != "completed":
            return PollResult(branch, f"deployment_{result.status}", latest_sha, result.message)
        self.cursors.set_cursor(branch, latest_sha)
        if selected_note is not None:
            self.cursors.record_release(selected_note.tag, selected_note.sha)
        return PollResult(branch, "deployed", latest_sha)


def _branch(value: str) -> str:
    if value not in _BRANCHES:
        raise ValueError("deployment branch is not allowlisted")
    return value


def _valid_sha(value: object) -> bool:
    return isinstance(value, str) and len(value) == 40 and all(
        char in "0123456789abcdefABCDEF" for char in value
    )


def _release_sha(path: Path) -> str:
    name = Path(path).name
    sha = name.split("-", 1)[0]
    if not _valid_sha(sha):
        raise RuntimeError("active release directory does not start with a verified commit SHA")
    return sha.lower()


def _safe_error(error: Exception) -> str:
    return f"{type(error).__name__}: operation failed"
