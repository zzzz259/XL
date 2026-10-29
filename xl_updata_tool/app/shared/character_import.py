"""Cross-feature port for configuration-selected character extraction."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from os import PathLike
from typing import Protocol


@dataclass(frozen=True)
class CharacterAssetImportSummary:
    scene_count: int
    asset_count: int
    files: tuple[str, ...]
    blueprint_path: str = ""
    incomplete_scenes: int = 0
    battle_scene_count: int = 0
    skipped_auxiliary: int = 0


class CharacterAssetExporter(Protocol):
    def __call__(
        self, *, version_timestamp: int, catalog_path: str | PathLike,
        lua_root: str | PathLike, bundle_root: str | PathLike,
        material_root: str | PathLike,
        cancel_check: Callable[[], bool] | None = None,
        progress_callback: Callable[[str, int, int], None] | None = None,
    ) -> CharacterAssetImportSummary: ...
