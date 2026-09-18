"""Qt-free grouping and pagination model for exported character thumbnails."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True, slots=True)
class ThumbnailEntry:
    path: str
    role_id: str = ""
    skin_key: str = ""
    fingerprint: str = ""
    is_new: bool | None = None


class ThumbnailCatalog:
    """Stable role grouping and page slicing without filename identity guesses."""

    def __init__(self, entries: Iterable[ThumbnailEntry] = (), page_size: int = 60):
        self.entries = tuple(sorted(entries, key=lambda item: (item.role_id.casefold(), item.path.casefold())))
        self.page_size = max(1, int(page_size))

    @classmethod
    def from_paths(cls, paths: Iterable[str], root, page_size: int = 60) -> "ThumbnailCatalog":
        root_path = Path(root)
        entries = []
        for path_value in paths:
            path = Path(path_value)
            try:
                relative = path.relative_to(root_path)
            except ValueError:
                relative = Path(path.name)
            parts = relative.parts
            role_id = parts[0] if len(parts) > 1 else ""
            entries.append(ThumbnailEntry(str(path), role_id=role_id))
        return cls(entries, page_size)

    @property
    def grouped(self) -> dict[str, tuple[ThumbnailEntry, ...]]:
        grouped: dict[str, list[ThumbnailEntry]] = {}
        for entry in self.entries:
            grouped.setdefault(entry.role_id, []).append(entry)
        return {key: tuple(value) for key, value in grouped.items()}

    def filtered(self, role_id: str = "") -> "ThumbnailCatalog":
        return ThumbnailCatalog(
            (entry for entry in self.entries if not role_id or entry.role_id == role_id),
            self.page_size,
        )

    @property
    def page_count(self) -> int:
        return max(1, (len(self.entries) + self.page_size - 1) // self.page_size)

    def page(self, index: int) -> tuple[ThumbnailEntry, ...]:
        index = min(max(0, int(index)), self.page_count - 1)
        start = index * self.page_size
        return self.entries[start : start + self.page_size]
