"""Durable and recoverable stop/switch/start deployment transaction."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from xl_deploy.runner import ALLOWED_UNITS
from xl_deploy.state import DeploymentState, Journal

START_NOTICE = "检测到更新，正在更新bot，期间将暂停服务"
COMPLETION_NOTICE = "更新完毕"
RECOVERY_NOTICE = "部署失败，已回滚到上一版本。"
_TERMINAL_PHASES = frozenset(
    {"completed", "rolled_back", "aborted", "no_op", "preflight_failed", "notice_failed"}
)
_SHA_CHARS = frozenset("0123456789abcdefABCDEF")


@dataclass(frozen=True)
class DeploymentPaths:
    releases_root: Path
    current_path: Path
    config_path: Path
    data_path: Path

    def __post_init__(self) -> None:
        for name in ("releases_root", "current_path", "config_path", "data_path"):
            object.__setattr__(self, name, Path(getattr(self, name)).absolute())
        releases = self.releases_root.resolve()
        for name in ("config_path", "data_path", "current_path"):
            path = Path(getattr(self, name))
            # Validate the pointer's filesystem location without following its
            # final symlink, which intentionally targets a release directory.
            path = path.parent.resolve() / path.name if name == "current_path" else path.resolve()
            try:
                path.relative_to(releases)
            except ValueError:
                continue
            raise ValueError(f"{name} must remain outside immutable releases")


@dataclass(frozen=True)
class DeploymentResult:
    status: str
    sha: str | None = None
    message: str = ""


class DeploymentTransaction:
    def __init__(
        self,
        state: DeploymentState,
        runner: Any,
        router: Any,
        paths: DeploymentPaths,
        *,
        drain_timeout_seconds: float = 30,
    ) -> None:
        if drain_timeout_seconds <= 0:
            raise ValueError("drain timeout must be positive")
        self.state = state
        self.runner = runner
        self.router = router
        self.paths = paths
        self.drain_timeout_seconds = drain_timeout_seconds
        self.events: list[tuple[object, ...]] = []

    def execute(self, plan: Any) -> DeploymentResult:
        with self.state.lock():
            return self._execute_locked(plan)

    def _execute_locked(self, plan: Any) -> DeploymentResult:
        sha = _validate_sha(getattr(plan, "sha", None))
        branch = getattr(plan, "branch", None)
        if branch not in {"debug", "test", "main"}:
            raise ValueError("unsupported deployment branch")
        units = tuple(getattr(plan, "impacted_units", ()))
        tiers = tuple(getattr(plan, "impacted_tiers", ()))
        scope = tuple(getattr(plan, "announcement_scope", ()))
        if not units:
            return DeploymentResult("no_op", sha, "No impacted services")
        existing = self.state.load()
        if existing is not None and existing.phase not in _TERMINAL_PHASES:
            raise RuntimeError("a pending deployment must be recovered before a new update")
        _validate_units(units)
        self._validate_paths()
        branch_pointer = self._branch_pointer(branch)
        previous = self.state.get_current(current_path=branch_pointer)
        if previous is None:
            raise RuntimeError("a verified current release is required before transactional deployment")
        transaction_id = str(uuid.uuid4())
        candidate = self.paths.releases_root / f"{sha}-{transaction_id}"
        release_note_value = getattr(plan, "release_note", None)
        release_note = (
            release_note_value.strip()
            if isinstance(release_note_value, str) and release_note_value.strip() and scope
            else None
        )
        journal = Journal(
            transaction_id=transaction_id,
            branch=branch,
            sha=sha,
            phase="staging",
            previous_release=str(previous),
            candidate_release=str(candidate),
            impacted_units=units,
            impacted_tiers=tiers,
            announcement_scope=scope,
            maintenance_enabled=False,
            release_note=release_note,
            completion_notice_sent=not bool(scope),
            release_note_sent=not bool(release_note),
        )
        self._save(journal)
        try:
            self.events.append(("stage", sha))
            staged_path = self.runner.stage(plan, candidate)
            if Path(staged_path).resolve() != candidate.resolve() or not candidate.is_dir():
                raise RuntimeError("staging did not produce the exact immutable candidate directory")
            journal = self._update(journal, phase="staged")
            self.events.append(("preflight", sha))
            self.runner.preflight(
                plan, candidate, self.paths.config_path, self.paths.data_path
            )
            journal = self._update(journal, phase="preflight_passed")
        except Exception as exc:
            self._update(
                journal,
                phase="preflight_failed",
                error=_safe_error(exc),
                evidence={"preflight": "failed"},
            )
            raise

        try:
            if scope:
                journal = self._update(journal, phase="announcing_start")
                self.events.append(("announce", START_NOTICE, *scope))
                if not _all_succeeded(self.router.announce(scope, START_NOTICE)):
                    journal = self._update(journal, phase="notice_failed", error="start notice delivery failed")
                    raise RuntimeError("start notice delivery failed; services were not stopped")
            if tiers:
                journal = self._update(journal, phase="pausing", maintenance_enabled=True)
                self.events.append(("pause", *tiers))
                self.router.pause(tiers)
                journal = self._update(journal, phase="draining", maintenance_enabled=True)
                self.events.append(("drain", *tiers))
                if not self.router.drain(tiers, self.drain_timeout_seconds):
                    raise TimeoutError("router drain timed out; services were not stopped")
            journal = self._update(
                journal,
                phase="stopping",
                maintenance_enabled=bool(tiers),
                evidence={"stop_attempted": "true"},
            )
            self.events.append(("stop", *units))
            self.runner.stop(units)
            journal = self._update(journal, phase="stopped", evidence={"services_stopped": "true"})

            self.events.append(("switch", sha))
            journal = self._update(journal, phase="switching")
            self.state.set_current(candidate, current_path=branch_pointer)
            journal = self._update(journal, phase="switched")

            journal = self._update(journal, phase="starting")
            self.events.append(("start", *units))
            self.runner.start(units)
            journal = self._update(journal, phase="checking")
            self.events.append(("health", *units))
            self.runner.health_check(units)

            if tiers:
                journal = self._update(journal, phase="resuming", maintenance_enabled=True)
                self.events.append(("resume", *tiers))
                self.router.resume(tiers)
            journal = self._update(journal, phase="announcing_completion", maintenance_enabled=False)
            return self._deliver_announcements(journal)
        except Exception as exc:
            latest = self.state.load() or journal
            stop_attempted = latest.evidence.get("stop_attempted") == "true"
            if stop_attempted:
                return self._rollback(latest, exc)
            if latest.maintenance_enabled and tiers:
                try:
                    self.events.append(("resume", *tiers))
                    self.router.resume(tiers)
                except Exception as resume_error:  # noqa: BLE001 - preserve maintenance and journal.
                    return self._keep_maintenance(latest, exc, resume_error)
            phase = "notice_failed" if latest.phase == "notice_failed" else "aborted"
            self._update(latest, phase=phase, maintenance_enabled=False, error=_safe_error(exc))
            raise

    def rollback(self, journal: Journal | None = None) -> DeploymentResult:
        with self.state.lock():
            current = journal or self.state.load()
            if current is None:
                return DeploymentResult("nothing_to_recover")
            return self._rollback(current, RuntimeError("explicit rollback requested"))

    def recover(self) -> DeploymentResult:
        with self.state.lock():
            journal = self.state.load()
            if journal is None:
                return DeploymentResult("nothing_to_recover")
            if journal.phase in _TERMINAL_PHASES:
                return DeploymentResult("already_recovered", journal.sha)
            if journal.phase == "recovery_notice_pending":
                try:
                    current = self.state.get_current(
                        current_path=self._branch_pointer(journal.branch)
                    )
                    if current != Path(journal.previous_release or "").resolve():
                        raise RuntimeError("pending recovery notice does not match the active rollback release")
                    self.runner.health_check(journal.impacted_units)
                except Exception as exc:  # noqa: BLE001 - do not claim recovery until the old release is healthy.
                    return self._keep_maintenance(journal, exc, exc)
                return self._deliver_recovery_notice(journal)
            if journal.phase in {
                "resuming",
                "announcing_completion",
                "completion_notice_pending",
                "release_note_pending",
                "completion_notice_failed",
            }:
                try:
                    current = self.state.get_current(
                        current_path=self._branch_pointer(journal.branch)
                    )
                    if current != Path(journal.candidate_release).resolve():
                        raise RuntimeError("pending announcement does not match the active release")
                    self.runner.health_check(journal.impacted_units)
                    if journal.phase == "resuming" and journal.impacted_tiers:
                        self.events.append(("resume", *journal.impacted_tiers))
                        self.router.resume(journal.impacted_tiers)
                    journal = self._update(
                        journal,
                        phase="announcing_completion",
                        maintenance_enabled=False,
                    )
                except Exception as exc:  # noqa: BLE001 - a failed readiness check requires rollback.
                    return self._rollback(journal, exc)
                return self._deliver_announcements(journal)
            if journal.evidence.get("stop_attempted") == "true":
                return self._rollback(journal, RuntimeError("recovering interrupted deployment"))
            if journal.maintenance_enabled and journal.impacted_tiers:
                self.events.append(("resume", *journal.impacted_tiers))
                self.router.resume(journal.impacted_tiers)
            self._update(
                journal,
                phase="aborted",
                maintenance_enabled=False,
                error="recovered before service stop",
            )
            return DeploymentResult("aborted", journal.sha, "Candidate was not activated")

    def _deliver_announcements(self, journal: Journal) -> DeploymentResult:
        scope = journal.announcement_scope
        if scope and not journal.completion_notice_sent:
            journal = self._update(
                journal,
                phase="completion_notice_pending",
                maintenance_enabled=False,
                error=None,
            )
            try:
                self.events.append(("announce", COMPLETION_NOTICE, *scope))
                delivered = self.router.announce(scope, COMPLETION_NOTICE)
                if not _all_succeeded(delivered):
                    journal = self._update(
                        journal,
                        phase="completion_notice_pending",
                        error="completion notice delivery failed",
                        evidence={"completion_notice_error": "delivery failed"},
                    )
                    return DeploymentResult("announcement_pending", journal.sha, "Deployment is healthy")
            except Exception as exc:  # noqa: BLE001 - delivery must be retried, not rolled back.
                journal = self._update(
                    journal,
                    phase="completion_notice_pending",
                    error=_safe_error(exc),
                    evidence={"completion_notice_error": _safe_error(exc)},
                )
                return DeploymentResult("announcement_pending", journal.sha, "Deployment is healthy")
            journal = self._update(journal, completion_notice_sent=True, error=None)

        if scope and journal.release_note and not journal.release_note_sent:
            journal = self._update(
                journal,
                phase="release_note_pending",
                maintenance_enabled=False,
                error=None,
            )
            try:
                self.events.append(("release_note", journal.release_note, *scope))
                delivered = self.router.announce_release_note(scope, journal.release_note)
                if not _all_succeeded(delivered):
                    journal = self._update(
                        journal,
                        phase="release_note_pending",
                        error="release note delivery failed",
                        evidence={"release_note_error": "delivery failed"},
                    )
                    return DeploymentResult("announcement_pending", journal.sha, "Deployment is healthy")
            except Exception as exc:  # noqa: BLE001 - delivery must be retried, not rolled back.
                journal = self._update(
                    journal,
                    phase="release_note_pending",
                    error=_safe_error(exc),
                    evidence={"release_note_error": _safe_error(exc)},
                )
                return DeploymentResult("announcement_pending", journal.sha, "Deployment is healthy")
            journal = self._update(journal, release_note_sent=True, error=None)

        journal = self._update(
            journal,
            phase="completed",
            maintenance_enabled=False,
            error=None,
            evidence={"deployment_health": "passed"},
        )
        return DeploymentResult("success", journal.sha)

    def _rollback(self, journal: Journal, cause: BaseException) -> DeploymentResult:
        self._update(
            journal,
            phase="rolling_back",
            maintenance_enabled=bool(journal.impacted_tiers),
            error=_safe_error(cause),
        )
        try:
            if journal.impacted_tiers:
                self.events.append(("pause", *journal.impacted_tiers))
                self.router.pause(journal.impacted_tiers)
            self.events.append(("stop", *journal.impacted_units))
            self.runner.stop(journal.impacted_units)
            if journal.previous_release is None:
                raise RuntimeError("previous release is missing; rollback cannot proceed")
            previous = Path(journal.previous_release).resolve()
            self.state.set_current(
                previous, current_path=self._branch_pointer(journal.branch)
            )
            self.events.append(("start", *journal.impacted_units))
            self.runner.start(journal.impacted_units)
            self.events.append(("health", *journal.impacted_units))
            self.runner.health_check(journal.impacted_units)
            if journal.impacted_tiers:
                self.events.append(("resume", *journal.impacted_tiers))
                self.router.resume(journal.impacted_tiers)
            journal = self._update(
                journal,
                phase="recovery_notice_pending",
                maintenance_enabled=False,
                recovery_notice_sent=not bool(journal.announcement_scope),
                evidence={"rollback": "passed"},
            )
            return self._deliver_recovery_notice(journal, cause=cause)
        except Exception as rollback_error:  # noqa: BLE001 - convert every rollback failure to durable state.
            return self._keep_maintenance(journal, cause, rollback_error)

    def _keep_maintenance(
        self, journal: Journal, cause: BaseException, rollback_error: BaseException
    ) -> DeploymentResult:
        maintenance_active = False
        evidence = {
            "rollback_error": _safe_error(rollback_error),
            "recovery_required": "true",
        }
        if journal.impacted_tiers:
            try:
                self.events.append(("pause", *journal.impacted_tiers))
                self.router.pause(journal.impacted_tiers)
                maintenance_active = True
            except Exception as maintenance_error:  # noqa: BLE001 - keep failure evidence without masking rollback.
                evidence["maintenance_error"] = _safe_error(maintenance_error)
        latest = self.state.load() or journal
        self._update(
            latest,
            phase="rollback_failed",
            maintenance_enabled=maintenance_active,
            error=_safe_error(cause),
            evidence=evidence,
        )
        return DeploymentResult("rollback_failed", journal.sha, _safe_error(rollback_error))

    def _deliver_recovery_notice(
        self, journal: Journal, *, cause: BaseException | None = None
    ) -> DeploymentResult:
        if journal.announcement_scope and not journal.recovery_notice_sent:
            try:
                self.events.append(("announce", RECOVERY_NOTICE, *journal.announcement_scope))
                delivered = self.router.announce(
                    journal.announcement_scope, RECOVERY_NOTICE
                )
                if not _all_succeeded(delivered):
                    journal = self._update(
                        journal,
                        phase="recovery_notice_pending",
                        error="recovery notice delivery failed",
                        evidence={"recovery_notice_error": "delivery failed"},
                    )
                    return DeploymentResult(
                        "announcement_pending", journal.sha, "Previous release is healthy"
                    )
            except Exception as exc:  # noqa: BLE001 - persist and retry operator notice.
                journal = self._update(
                    journal,
                    phase="recovery_notice_pending",
                    error=_safe_error(exc),
                    evidence={"recovery_notice_error": _safe_error(exc)},
                )
                return DeploymentResult(
                    "announcement_pending", journal.sha, "Previous release is healthy"
                )
            journal = self._update(journal, recovery_notice_sent=True, error=None)
        journal = self._update(
            journal,
            phase="rolled_back",
            maintenance_enabled=False,
            recovery_notice_sent=True,
        )
        return DeploymentResult("rolled_back", journal.sha, _safe_error(cause) if cause else "")

    def _validate_paths(self) -> None:
        releases = self.paths.releases_root.resolve()
        for label, path in (
            ("config", self.paths.config_path),
            ("data", self.paths.data_path),
        ):
            try:
                path.resolve().relative_to(releases)
            except ValueError:
                continue
            raise ValueError(f"{label} path must remain outside immutable releases")

    def _branch_pointer(self, branch: str) -> Path:
        if branch not in {"debug", "test", "main"}:
            raise ValueError("unsupported deployment branch")
        return self.paths.current_path / branch

    def _save(self, journal: Journal) -> None:
        self.state.save(journal)

    def _update(self, journal: Journal, **changes: Any) -> Journal:
        evidence = changes.pop("evidence", None)
        if evidence is not None:
            changes["evidence"] = {**journal.evidence, **evidence}
        updated = replace(journal, **changes)
        self.state.save(updated)
        return updated


def _validate_sha(sha: object) -> str:
    if not isinstance(sha, str) or len(sha) != 40 or any(char not in _SHA_CHARS for char in sha):
        raise ValueError("SHA must be exactly 40 hexadecimal characters")
    return sha.lower()


def _validate_units(units: tuple[str, ...]) -> None:
    if len(set(units)) != len(units) or any(unit not in ALLOWED_UNITS for unit in units):
        raise ValueError("impacted systemd unit is not in the deployment allowlist")


def _all_succeeded(result: Any) -> bool:
    if result is True:
        return True
    if isinstance(result, dict) and result:
        for value in result.values():
            if value is not True and not (isinstance(value, dict) and value.get("success") is True):
                return False
        return True
    return False


def _safe_error(exc: BaseException) -> str:
    message = str(exc).strip() or type(exc).__name__
    return f"{type(exc).__name__}: {message}"[:1000]
