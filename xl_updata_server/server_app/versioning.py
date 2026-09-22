"""版本级 workspace 和当前版本指针。"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class VersionWorkspace:
    version_timestamp: int | None
    root: Path

    @classmethod
    def create(cls, versions_dir: Path, version_timestamp: int | None = None) -> VersionWorkspace:
        versions_dir.mkdir(parents=True, exist_ok=True)
        prefix = f".{version_timestamp}-" if version_timestamp is not None else ".pending-"
        root = Path(tempfile.mkdtemp(prefix=prefix, dir=versions_dir))
        (root / "bundles" / "Data").mkdir(parents=True)
        (root / "character_data").mkdir()
        return cls(version_timestamp=version_timestamp, root=root)

    @property
    def bundles_dir(self) -> Path:
        return self.root / "bundles"

    @property
    def output_dir(self) -> Path:
        return self.root


def publish_version(
    workspace: VersionWorkspace,
    versions_dir: Path,
    version_timestamp: int | None = None,
) -> Path:
    version = version_timestamp if version_timestamp is not None else workspace.version_timestamp
    if version is None:
        raise ValueError("发布版本必须提供 version_timestamp")
    destination = versions_dir / str(version)
    if destination.exists():
        raise FileExistsError(f"版本目录已存在: {destination}")
    workspace.root.replace(destination)
    return destination


def write_current_pointer(data_dir: Path, version_timestamp: int) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    pointer = data_dir / "current_version.json"
    temporary = pointer.with_name(pointer.name + ".part")
    temporary.write_text(
        json.dumps({"version": version_timestamp}, ensure_ascii=False),
        encoding="utf-8",
    )
    temporary.replace(pointer)


def read_current_pointer(data_dir: Path) -> int | None:
    pointer = data_dir / "current_version.json"
    try:
        payload = json.loads(pointer.read_text(encoding="utf-8"))
        return int(payload["version"])
    except (OSError, TypeError, ValueError, KeyError):
        return None
