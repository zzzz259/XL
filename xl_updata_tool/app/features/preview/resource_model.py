"""Qt-free identity models for preview resources."""

from __future__ import annotations

import hashlib
import json
import posixpath
from dataclasses import dataclass
from typing import Iterable


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


@dataclass(frozen=True, slots=True)
class PreviewResourceCatalog:
    """Grouped preview resources, including resources without a character match."""

    characters: dict[str, tuple[SpineSkinRecord, ...]]
    skins: dict[str, SpineSkinRecord]
    atlases: tuple[object, ...] = ()
    burst_heads: tuple[object, ...] = ()
    unmatched: tuple[SpineSkinRecord, ...] = ()

    @classmethod
    def from_records(cls, records: Iterable[SpineSkinRecord]) -> "PreviewResourceCatalog":
        characters: dict[str, list[SpineSkinRecord]] = {}
        skins: dict[str, SpineSkinRecord] = {}
        unmatched: list[SpineSkinRecord] = []

        for record in records:
            skins[skin_key(record)] = record
            if record.character_id:
                characters.setdefault(record.character_id, []).append(record)
            else:
                unmatched.append(record)

        return cls(
            characters={key: tuple(value) for key, value in characters.items()},
            skins=skins,
            unmatched=tuple(unmatched),
        )


def _normalise_path(value: str) -> str:
    value = str(value).strip().replace("\\", "/")
    if not value:
        return ""
    return posixpath.normpath(value)


def skin_key(record: SpineSkinRecord) -> str:
    """Return a stable key using attachment identity or its explicit fallback."""
    fingerprint = record.attachment_fingerprint or record.identity_fingerprint
    identity = (
        record.character_id or "",
        _normalise_path(record.source_skel),
        record.skin_name,
        fingerprint,
    )
    payload = json.dumps(identity, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
