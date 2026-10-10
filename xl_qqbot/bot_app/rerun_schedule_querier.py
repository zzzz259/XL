"""Read the published rerun schedule from the active immutable game version."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


@dataclass(frozen=True)
class RerunScheduleResult:
    status: str
    image_path: Path | None = None
    source_version: str = ""
    stale: bool = False
    reason: str = ""


class RerunScheduleQuerier:
    def __init__(self, character_data: str | Path, versions_dir: str | Path):
        self.character_data = Path(character_data)
        self.versions_dir = Path(versions_dir)
        self.data_dir = self.character_data.parent.parent

    def find(self) -> RerunScheduleResult:
        try:
            pointer = json.loads((self.data_dir / "current_version.json").read_text(encoding="utf-8"))
            active_version = str(pointer["version"])
            stable_dir = self.data_dir / "rerun_schedule"
            version_dir = self.versions_dir / active_version / "rerun_schedule"
            stable = self._try_candidate(stable_dir)
            versioned = self._try_candidate(version_dir)
            selected = stable if stable and stable[0].get("source_version") == active_version else versioned or stable
            if selected is None:
                raise FileNotFoundError("active and stable schedule snapshots are missing")
            payload, image = selected
            source_version = str(payload["source_version"])
            return RerunScheduleResult(
                status="found",
                image_path=image,
                source_version=source_version,
                stale=source_version != active_version,
            )
        except (OSError, ValueError, KeyError, TypeError) as error:
            logger.warning("stage=rerun_query status=unavailable reason=%s", error)
            return RerunScheduleResult(status="unavailable", reason=str(error))

    @staticmethod
    def _read_candidate(schedule_dir: Path) -> tuple[dict, Path]:
        payload = json.loads((schedule_dir / "current.json").read_text(encoding="utf-8"))
        relative = Path(str(payload["artifact_path"]))
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("unsafe artifact path")
        root = schedule_dir.resolve()
        image = (schedule_dir / relative).resolve()
        if not image.is_relative_to(root) or not image.is_file():
            raise ValueError("artifact missing or outside schedule directory")
        image_bytes = image.read_bytes()
        if not image_bytes.startswith(PNG_SIGNATURE):
            raise ValueError("artifact is not PNG")
        digest = hashlib.sha256(image_bytes).hexdigest()
        if digest != payload.get("render_sha256"):
            raise ValueError("artifact digest mismatch")
        if not str(payload.get("source_version", "")):
            raise ValueError("source version missing")
        return payload, image

    @classmethod
    def _try_candidate(cls, schedule_dir: Path) -> tuple[dict, Path] | None:
        if not (schedule_dir / "current.json").is_file():
            return None
        try:
            return cls._read_candidate(schedule_dir)
        except (OSError, ValueError, KeyError, TypeError) as error:
            logger.warning("排期候选快照无效 path=%s reason=%s", schedule_dir.name, error)
            return None
