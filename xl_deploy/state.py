"""Crash-safe deployment journal, release pointer, and cross-process lock."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path

_THREAD_LOCKS: dict[str, threading.Lock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()


class SymlinkCurrentPointer:
    """Atomic directory symlink used directly by systemd WorkingDirectory."""

    def read(self, pointer: Path) -> Path | None:
        if not pointer.exists() and not pointer.is_symlink():
            return None
        if not pointer.is_symlink():
            raise RuntimeError("current release pointer must be a directory symlink")
        try:
            target = pointer.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise RuntimeError("current release symlink target is unavailable") from exc
        if not target.is_dir():
            raise RuntimeError("current release symlink target is not a directory")
        return target

    def replace(self, pointer: Path, target: Path) -> None:
        if pointer.exists() and not pointer.is_symlink():
            raise RuntimeError("refusing to replace a non-symlink current path")
        temporary = pointer.parent / f".{pointer.name}.{uuid.uuid4().hex}.tmp"
        try:
            temporary.symlink_to(target, target_is_directory=True)
            os.replace(temporary, pointer)
        finally:
            if temporary.is_symlink():
                temporary.unlink()


@dataclass(frozen=True)
class Journal:
    transaction_id: str
    branch: str
    sha: str
    phase: str
    previous_release: str | None
    candidate_release: str
    impacted_units: tuple[str, ...]
    impacted_tiers: tuple[str, ...]
    announcement_scope: tuple[str, ...]
    maintenance_enabled: bool
    evidence: dict[str, str] = field(default_factory=dict)
    error: str | None = None
    release_note: str | None = None
    completion_notice_sent: bool = False
    release_note_sent: bool = False
    recovery_notice_sent: bool = False

    @classmethod
    def from_dict(cls, payload: object) -> Journal:
        required = {
            "transaction_id", "branch", "sha", "phase", "previous_release",
            "candidate_release", "impacted_units", "impacted_tiers",
            "announcement_scope", "maintenance_enabled", "evidence", "error",
        }
        delivery_fields = {"release_note", "completion_notice_sent", "release_note_sent"}
        all_delivery_fields = delivery_fields | {"recovery_notice_sent"}
        payload_fields = frozenset(payload) if isinstance(payload, dict) else frozenset()
        if payload_fields not in {
            frozenset(required),
            frozenset(required | delivery_fields),
            frozenset(required | all_delivery_fields),
        }:
            raise ValueError("journal fields are incomplete or unknown")
        strings = ("transaction_id", "branch", "sha", "phase", "candidate_release")
        if any(not isinstance(payload[key], str) or not payload[key] for key in strings):
            raise ValueError("journal contains invalid required strings")
        for key in ("previous_release", "error"):
            if payload[key] is not None and not isinstance(payload[key], str):
                raise ValueError(f"journal contains invalid {key}")
        for key in ("impacted_units", "impacted_tiers", "announcement_scope"):
            if not isinstance(payload[key], list) or any(
                not isinstance(value, str) for value in payload[key]
            ):
                raise ValueError(f"journal contains invalid {key}")
        if not isinstance(payload["maintenance_enabled"], bool):
            raise TypeError("journal contains invalid maintenance state")
        release_note = payload.get("release_note")
        if release_note is not None and not isinstance(release_note, str):
            raise TypeError("journal contains invalid release note")
        completion_notice_sent = payload.get(
            "completion_notice_sent", payload["phase"] in {"completed", "rolled_back", "aborted"}
        )
        release_note_sent = payload.get("release_note_sent", True)
        recovery_notice_sent = payload.get("recovery_notice_sent", payload["phase"] == "rolled_back")
        if not all(isinstance(value, bool) for value in (
            completion_notice_sent, release_note_sent, recovery_notice_sent
        )):
            raise TypeError("journal contains invalid announcement delivery state")
        evidence = payload["evidence"]
        if not isinstance(evidence, dict) or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in evidence.items()
        ):
            raise ValueError("journal contains invalid evidence")
        return cls(
            **{key: payload[key] for key in strings},
            previous_release=payload["previous_release"],
            impacted_units=tuple(payload["impacted_units"]),
            impacted_tiers=tuple(payload["impacted_tiers"]),
            announcement_scope=tuple(payload["announcement_scope"]),
            maintenance_enabled=payload["maintenance_enabled"],
            evidence=dict(evidence),
            error=payload["error"],
            release_note=release_note,
            completion_notice_sent=completion_notice_sent,
            release_note_sent=release_note_sent,
            recovery_notice_sent=recovery_notice_sent,
        )


class DeploymentState:
    def __init__(
        self,
        state_dir: Path | str,
        *,
        releases_root: Path | str | None = None,
        current_path: Path | str | None = None,
        pointer_adapter: object | None = None,
    ) -> None:
        self.state_dir = Path(state_dir).absolute()
        self.journal_path = self.state_dir / "journal.json"
        self.lock_path = self.state_dir / "deployment.lock"
        self.releases_root = (
            Path(releases_root).resolve() if releases_root is not None else None
        )
        self.current_path = (
            Path(current_path).absolute()
            if current_path is not None
            else self.state_dir / "current"
        )
        self.pointer_adapter = pointer_adapter or SymlinkCurrentPointer()
        self._lock_key = str(self.lock_path)

    def save(self, journal: Journal) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        payload = asdict(journal)
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix="journal.", suffix=".tmp", dir=self.state_dir
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(encoded)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_name, self.journal_path)
            self._fsync_directory(self.state_dir)
        except BaseException:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            raise

    def load(self) -> Journal | None:
        try:
            payload = json.loads(self.journal_path.read_text(encoding="utf-8"))
            return Journal.from_dict(payload)
        except FileNotFoundError:
            return None
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
            raise RuntimeError("deployment journal is unreadable or invalid") from exc

    def get_current(self, *, current_path: Path | str | None = None) -> Path | None:
        pointer = Path(current_path).absolute() if current_path is not None else self.current_path
        target = self.pointer_adapter.read(pointer)
        if target is None:
            return None
        target = Path(target).resolve()
        self._validate_release_path(target)
        if not target.is_dir():
            raise RuntimeError("current release target does not exist")
        return target

    def set_current(
        self,
        release_path: Path | str,
        *,
        current_path: Path | str | None = None,
    ) -> None:
        target = Path(release_path).resolve()
        self._validate_release_path(target)
        if not target.is_dir():
            raise ValueError("release target must be an existing directory")
        pointer = Path(current_path).absolute() if current_path is not None else self.current_path
        pointer.parent.mkdir(parents=True, exist_ok=True)
        self.pointer_adapter.replace(pointer, target)
        self._fsync_directory(pointer.parent)

    def _validate_release_path(self, target: Path) -> None:
        if self.releases_root is None:
            raise ValueError("release root is required to validate a current target")
        try:
            target.relative_to(self.releases_root)
        except ValueError as exc:
            raise ValueError("current target must be inside the immutable releases root") from exc

    @contextmanager
    def lock(self, *, blocking: bool = True) -> Iterator[None]:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        with _THREAD_LOCKS_GUARD:
            local_lock = _THREAD_LOCKS.setdefault(self._lock_key, threading.Lock())
        if not local_lock.acquire(blocking=blocking):
            raise RuntimeError("deployment process lock is already held")
        descriptor: int | None = None
        os_locked = False
        try:
            descriptor = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
            self._os_lock(descriptor, blocking=blocking)
            os_locked = True
            yield
        finally:
            if descriptor is not None:
                try:
                    if os_locked:
                        self._os_unlock(descriptor)
                finally:
                    os.close(descriptor)
            local_lock.release()

    @staticmethod
    def _os_lock(descriptor: int, *, blocking: bool) -> None:
        if os.name == "nt":
            import msvcrt

            os.lseek(descriptor, 0, os.SEEK_SET)
            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"\0")
                os.lseek(descriptor, 0, os.SEEK_SET)
            mode = msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK
            try:
                msvcrt.locking(descriptor, mode, 1)
            except OSError as exc:
                raise RuntimeError("deployment process lock is already held") from exc
        else:
            import fcntl

            mode = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
            try:
                fcntl.flock(descriptor, mode)
            except BlockingIOError as exc:
                raise RuntimeError("deployment process lock is already held") from exc

    @staticmethod
    def _os_unlock(descriptor: int) -> None:
        if os.name == "nt":
            import msvcrt

            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_UN)

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        if os.name == "nt":
            return
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
