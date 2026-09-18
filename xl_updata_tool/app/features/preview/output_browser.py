"""Qt-free directory model for output-backed large-icon browsers."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path


_IMAGE_SUFFIXES = frozenset({".bmp", ".gif", ".jpeg", ".jpg", ".png", ".tga", ".webp"})


@dataclass(frozen=True, slots=True)
class OutputBrowserEntry:
    name: str
    path: str
    kind: str
    child_count: int = 0
    fingerprint: str = ""
    is_new: bool | None = None


def path_fingerprint(path) -> str:
    value = Path(path)
    try:
        stat = value.stat()
        payload = f"file:{os.path.normcase(os.path.abspath(os.fspath(value)))}:{stat.st_size}:{stat.st_mtime_ns}"
    except OSError:
        payload = f"missing:{os.path.normcase(os.path.abspath(os.fspath(value)))}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def folder_fingerprint(folder) -> str:
    root = Path(folder)
    children = sorted(
        path_fingerprint(path)
        for path in root.rglob("*")
        if path.is_file() and path.suffix.casefold() in _IMAGE_SUFFIXES
    )
    payload = f"folder:{os.path.normcase(os.path.abspath(os.fspath(root)))}:{':'.join(children)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class OutputBrowserCatalog:
    """Browse only final output folders, never staging/source folders."""

    def __init__(self, root, entries=()):
        self.root = Path(root)
        self.entries = tuple(entries)

    @classmethod
    def from_root(cls, root, *, include_files=True, state=None):
        root_path = Path(root)
        if not root_path.is_dir():
            return cls(root_path)
        entries = []
        for path in sorted(root_path.iterdir(), key=lambda item: item.name.casefold()):
            if path.is_dir():
                count = sum(1 for child in path.rglob("*.png") if child.is_file())
                fingerprint = folder_fingerprint(path)
                is_new = (
                    state.is_new_for(_image_file_fingerprints(path))
                    if state
                    else None
                )
                entries.append(OutputBrowserEntry(path.name, str(path), "folder", count, fingerprint, is_new))
            elif include_files and path.suffix.casefold() in _IMAGE_SUFFIXES:
                fingerprint = path_fingerprint(path)
                entries.append(
                    OutputBrowserEntry(path.name, str(path), "file", 0, fingerprint, state.is_new(fingerprint) if state else None)
                )
        return cls(root_path, entries)

    def children(self, folder) -> tuple[OutputBrowserEntry, ...]:
        return self.from_root(folder).entries

    def all_fingerprints(self) -> tuple[str, ...]:
        """Return leaf image fingerprints; directory fingerprints are derived state."""
        return self.file_fingerprints()

    def file_fingerprints(self, recursive: bool = True) -> tuple[str, ...]:
        return _image_file_fingerprints(self.root, recursive=recursive)


def _image_file_fingerprints(root, *, recursive: bool = True) -> tuple[str, ...]:
    folder = Path(root)
    if not folder.is_dir():
        return ()
    paths = folder.rglob("*") if recursive else folder.iterdir()
    return tuple(
        path_fingerprint(path)
        for path in sorted(paths, key=lambda item: str(item).casefold())
        if path.is_file() and path.suffix.casefold() in _IMAGE_SUFFIXES
    )
