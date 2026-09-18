"""One source of truth for unread preview leaves and category aggregation."""

from __future__ import annotations

from dataclasses import dataclass

from .output_browser import OutputBrowserCatalog
from .resource_model import skin_key


@dataclass(frozen=True, slots=True)
class PreviewUnreadSnapshot:
    spine: frozenset[str] = frozenset()
    character_files: frozenset[str] = frozenset()
    material_files: frozenset[str] = frozenset()

    @property
    def character(self) -> frozenset[str]:
        return self.character_files

    @property
    def materials(self) -> frozenset[str]:
        return self.material_files

    def has_unread(self, state) -> bool:
        return any(
            state.is_new(fingerprint)
            for fingerprints in (self.spine, self.character_files, self.material_files)
            for fingerprint in fingerprints
        )

    def category_has_unread(self, category: str, state) -> bool:
        fingerprints = {
            "spine": self.spine,
            "character": self.character_files,
            "materials": self.material_files,
            "game_material": self.material_files,
        }.get(str(category), ())
        return any(state.is_new(fingerprint) for fingerprint in fingerprints)


def build_preview_unread_snapshot(service) -> PreviewUnreadSnapshot:
    catalog = service.load_published_preview_resources()
    character_root = service.preview_dir
    material_root = service.preview_dir.parent / "game_material"
    return PreviewUnreadSnapshot(
        spine=frozenset(skin_key(record) for record in catalog.skins.values()),
        character_files=frozenset(OutputBrowserCatalog(character_root).file_fingerprints()),
        material_files=frozenset(OutputBrowserCatalog(material_root).file_fingerprints()),
    )
