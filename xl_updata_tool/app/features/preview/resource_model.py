"""Qt-free identity models for preview resources."""

from __future__ import annotations

import hashlib
import json
import posixpath
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .portrait_config.model import PortraitScene


@dataclass(frozen=True, slots=True)
class SpineSkinRecord:
    """Identity and display metadata for one Spine skin."""

    character_id: str | None
    source_skel: str
    atlas_path: str
    skin_name: str
    attachment_fingerprint: str
    display_name: str
    status: str
    identity_fingerprint: str = ""
    fingerprint_kind: str = "attachment_set"
    diagnostic: str = ""
    resource_family: str = "spine"
    # Retained for compatibility with preview_index.json files written by
    # earlier indexing builds. Configured composition comes from PortraitScene.
    composition_group: str | None = None
    composition_order: int | None = None


@dataclass(frozen=True, slots=True)
class PreviewResourceCatalog:
    """Grouped preview resources, including resources without a character match."""

    characters: dict[str, tuple[SpineSkinRecord, ...]]
    skins: dict[str, SpineSkinRecord]
    atlases: tuple[object, ...] = ()
    burst_heads: tuple[object, ...] = ()
    unmatched: tuple[SpineSkinRecord, ...] = ()
    eventcovers: tuple[SpineSkinRecord, ...] = ()
    portrait_scenes: tuple[PortraitScene, ...] = ()
    portrait_diagnostics: tuple[tuple[str, str], ...] = ()

    @classmethod
    def from_records(
        cls,
        records: Iterable[SpineSkinRecord],
        *,
        portrait_scenes: Iterable[PortraitScene] = (),
        portrait_diagnostics: Iterable[tuple[str, str]] = (),
    ) -> "PreviewResourceCatalog":
        characters: dict[str, list[SpineSkinRecord]] = {}
        skins: dict[str, SpineSkinRecord] = {}
        unmatched: list[SpineSkinRecord] = []
        eventcovers: list[SpineSkinRecord] = []

        for record in records:
            skins[skin_key(record)] = record
            if record.resource_family == "eventcovers":
                eventcovers.append(record)
            elif record.character_id:
                characters.setdefault(record.character_id, []).append(record)
            else:
                unmatched.append(record)

        return cls(
            characters={key: tuple(value) for key, value in characters.items()},
            skins=skins,
            unmatched=tuple(unmatched),
            eventcovers=tuple(eventcovers),
            portrait_scenes=tuple(portrait_scenes),
            portrait_diagnostics=tuple(portrait_diagnostics),
        )


def _normalise_path(value: str) -> str:
    value = str(value).strip().replace("\\", "/")
    if not value:
        return ""
    return posixpath.normpath(value)


def skin_key(record: SpineSkinRecord) -> str:
    """Return a stable key for the source/skin identity, never just attachments."""
    identity_fingerprint = record.identity_fingerprint or ""
    identity = (
        record.character_id or "",
        _normalise_path(record.source_skel),
        _normalise_path(record.atlas_path),
        record.skin_name,
        identity_fingerprint,
        # Records built without a source identity still need to distinguish
        # attachment variants; discovered records always have the stronger
        # source/skin identity above.
        record.attachment_fingerprint if not identity_fingerprint else "",
    )
    payload = json.dumps(identity, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def portrait_scene_key(scene: PortraitScene) -> str:
    """Version-scoped unread identity for one Lua-configured portrait scene."""
    identity = (
        str(scene.game_version), str(scene.version_timestamp),
        str(scene.character_id), str(scene.skin_id),
    )
    payload = json.dumps(identity, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def portrait_scene_status(scene: PortraitScene) -> str:
    """Map scene diagnostics to an explicit catalog status."""
    diagnostics = scene.validate()
    if any(item.severity == "error" and item.code.startswith("unsupported_") for item in diagnostics):
        return "unsupported"
    if any(item.severity == "error" for item in diagnostics):
        return "incomplete"
    container = scene.prefab_container.casefold().replace("\\", "/")
    if not scene.character_id or "/eventcovers/" in container:
        return "standalone"
    return "complete"


def spine_source_stem(path: str) -> str:
    """Return a Spine source stem without Unity transport suffixes."""
    name = Path(str(path).replace("\\", "/")).name
    lowered = name.casefold()
    for suffix in (".skel.prefab", ".skel.bytes", ".skel"):
        if lowered.endswith(suffix):
            return name[: -len(suffix)]
    return Path(name).stem


def display_skin_key(record: SpineSkinRecord) -> tuple[str, str, str]:
    """Identify one user-facing skin source, excluding internal Spine skins."""
    source_stem = spine_source_stem(record.source_skel)
    if source_stem.casefold().endswith("_bg"):
        source_stem = source_stem[:-3]
    return (
        record.resource_family,
        record.character_id or "",
        source_stem.casefold(),
    )
