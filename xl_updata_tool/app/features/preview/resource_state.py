"""Persistent unread state for preview resources."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


class PreviewResourceState:
    """Track read resource fingerprints and persist them atomically as JSON."""

    def __init__(self, path: str | os.PathLike[str]):
        self.path = Path(path)
        self._read_fingerprints: set[str] = set()
        self._load()

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            with self.path.open(encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, TypeError, ValueError):
            return

        if not isinstance(data, dict):
            return
        fingerprints = data.get("read_fingerprints", data.get("read", ()))
        if isinstance(fingerprints, (list, tuple, set)):
            self._read_fingerprints = {str(value) for value in fingerprints}

    def is_new(self, fingerprint: str) -> bool:
        return str(fingerprint) not in self._read_fingerprints

    def mark_read(self, fingerprint: str) -> bool:
        value = str(fingerprint)
        if value in self._read_fingerprints:
            return False
        self._read_fingerprints.add(value)
        return True

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            dir=self.path.parent,
        )
        try:
            with os.fdopen(file_descriptor, "w", encoding="utf-8") as handle:
                json.dump(
                    {"read_fingerprints": sorted(self._read_fingerprints)},
                    handle,
                    ensure_ascii=False,
                    indent=2,
                )
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, self.path)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)
