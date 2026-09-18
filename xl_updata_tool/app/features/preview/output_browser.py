"""Qt-free directory model for output-backed large-icon browsers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


_IMAGE_SUFFIXES = frozenset({".bmp", ".gif", ".jpeg", ".jpg", ".png", ".tga", ".webp"})


@dataclass(frozen=True, slots=True)
class OutputBrowserEntry:
    name: str
    path: str
    kind: str
    child_count: int = 0


class OutputBrowserCatalog:
    """Browse only final output folders, never staging/source folders."""

    def __init__(self, root, entries=()):
        self.root = Path(root)
        self.entries = tuple(entries)

    @classmethod
    def from_root(cls, root, *, include_files=True):
        root_path = Path(root)
        if not root_path.is_dir():
            return cls(root_path)
        entries = []
        for path in sorted(root_path.iterdir(), key=lambda item: item.name.casefold()):
            if path.is_dir():
                count = sum(1 for child in path.rglob("*.png") if child.is_file())
                entries.append(OutputBrowserEntry(path.name, str(path), "folder", count))
            elif include_files and path.suffix.casefold() in _IMAGE_SUFFIXES:
                entries.append(OutputBrowserEntry(path.name, str(path), "file"))
        return cls(root_path, entries)

    def children(self, folder) -> tuple[OutputBrowserEntry, ...]:
        return self.from_root(folder).entries
