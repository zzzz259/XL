"""Atomic, version- and skin-scoped cache for complete portrait scenes."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

from .asset_index import AssetIndex
from .model import SCHEMA_VERSION, PortraitScene

CACHE_FORMAT_VERSION = 1


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class PortraitSceneCache:
    def __init__(self, cache_root: Path, parser_version: str) -> None:
        self.cache_root = Path(cache_root).resolve()
        self.parser_version = str(parser_version)

    @staticmethod
    def key_for(version_timestamp: int | str, skin_id: str) -> str:
        version = str(version_timestamp)
        skin = str(skin_id)
        if not version.isdigit() or not skin.isdigit():
            raise ValueError("cache key requires numeric version timestamp and skin ID")
        return f"{version}/{skin}.json"

    def _path_for_key(self, key: str) -> Path:
        normalized = str(key).replace("\\", "/")
        relative = PurePosixPath(normalized)
        if relative.is_absolute() or len(relative.parts) != 2 or any(
            part in {"", ".", ".."} for part in relative.parts
        ) or ":" in normalized:
            raise ValueError(f"invalid portrait-scene cache key: {key!r}")
        if not relative.parts[0].isdigit() or not relative.parts[1].endswith(".json"):
            raise ValueError(f"invalid portrait-scene cache key: {key!r}")
        path = (self.cache_root / Path(*relative.parts)).resolve()
        if self.cache_root not in path.parents:
            raise ValueError(f"cache key escapes cache root: {key!r}")
        return path

    def load(self, key: str, expected_config_hash: str, index: AssetIndex) -> PortraitScene | None:
        path = self._path_for_key(key)
        try:
            envelope: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(envelope, dict):
                return None
            scene_data = envelope["scene"]
            if not isinstance(scene_data, dict):
                return None
            scene = PortraitScene.from_dict(scene_data)
        except (OSError, ValueError, TypeError, KeyError, AttributeError, IndexError, json.JSONDecodeError):
            return None

        parts = PurePosixPath(str(key).replace("\\", "/")).parts
        expected_version = parts[0] if len(parts) == 2 else ""
        if (
            envelope.get("cache_format_version") != CACHE_FORMAT_VERSION
            or envelope.get("schema_version") != SCHEMA_VERSION
            or envelope.get("parser_version") != self.parser_version
            or envelope.get("config_hash") != expected_config_hash
            or str(envelope.get("version_timestamp")) != expected_version
            or str(scene.version_timestamp) != expected_version
            or (len(parts) == 2 and Path(parts[1]).stem != scene.skin_id)
            or index.version_timestamp != int(expected_version)
        ):
            return None

        recorded_hashes = envelope.get("source_hashes")
        if not isinstance(recorded_hashes, dict) or not recorded_hashes:
            return None
        for bundle_hash, recorded_digest in recorded_hashes.items():
            bundle_path = index.bundle_path_for(str(bundle_hash))
            current_digest = (
                file_sha256(bundle_path)
                if bundle_path is not None and bundle_path.is_file()
                else "missing"
            )
            if current_digest != recorded_digest:
                return None
        if not set(scene.source_bundles).issubset(recorded_hashes):
            return None
        if any(item.severity == "error" for item in scene.validate()):
            return None
        return scene

    def load_manifest(self, key: str) -> PortraitScene | None:
        """Load a display manifest without hashing or opening source bundles.

        This is intentionally separate from ``load``: only ``load`` may reuse
        a complete build cache after source verification. The preview catalog
        may still display an incomplete scene and its diagnostics.
        """
        try:
            envelope = json.loads(self._path_for_key(key).read_text(encoding="utf-8"))
            scene_data = envelope["scene"]
            scene = PortraitScene.from_dict(scene_data)
        except (OSError, ValueError, TypeError, KeyError, AttributeError, IndexError,
                json.JSONDecodeError):
            return None
        parts = PurePosixPath(str(key).replace("\\", "/")).parts
        if (
            len(parts) != 2
            or envelope.get("cache_format_version") != CACHE_FORMAT_VERSION
            or envelope.get("schema_version") != SCHEMA_VERSION
            or envelope.get("parser_version") != self.parser_version
            or str(envelope.get("version_timestamp")) != parts[0]
            or str(scene.version_timestamp) != parts[0]
            or Path(parts[1]).stem != scene.skin_id
        ):
            return None
        return scene

    def store_snapshot(self, scene: PortraitScene) -> str:
        """Atomically persist a scene result for catalog display, even if partial.

        Incomplete snapshots are never reusable by ``load`` because they do not
        contain source hashes and may include validation errors.
        """
        if not any(item.severity == "error" for item in scene.validate()):
            raise ValueError("store_snapshot is reserved for incomplete portrait scenes")
        key = self.key_for(scene.version_timestamp, scene.skin_id)
        snapshot_cache = PortraitSceneCache(
            self.cache_root / "_incomplete", parser_version=self.parser_version,
        )
        path = snapshot_cache._path_for_key(key)
        envelope = {
            "cache_format_version": CACHE_FORMAT_VERSION,
            "schema_version": SCHEMA_VERSION,
            "parser_version": self.parser_version,
            "version_timestamp": int(scene.version_timestamp),
            "skin_id": scene.skin_id,
            "config_hash": "",
            "source_hashes": {},
            "scene": scene.to_dict(),
        }
        encoded = json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if PortraitScene.from_dict(json.loads(encoded)["scene"]) != scene:
            raise ValueError("portrait scene snapshot round-trip changed scene data")
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", newline="\n", dir=path.parent,
                prefix=f".{path.name}.", suffix=".tmp", delete=False,
            ) as temporary:
                temp_name = temporary.name
                temporary.write(encoded)
                temporary.flush()
                os.fsync(temporary.fileno())
            if PortraitScene.from_dict(json.loads(Path(temp_name).read_text(encoding="utf-8"))["scene"]) != scene:
                raise ValueError("staged portrait scene snapshot validation failed")
            os.replace(temp_name, path)
            temp_name = None
        finally:
            if temp_name is not None:
                try:
                    Path(temp_name).unlink()
                except FileNotFoundError:
                    pass
        return key

    def store(
        self,
        scene: PortraitScene,
        config_hash: str,
        source_hashes: dict[str, str],
    ) -> str:
        if (scene.prefab is None and not scene.prefab_container) or any(
            item.severity == "error" for item in scene.validate()
        ):
            raise ValueError("only complete, validated portrait scenes can be cached")
        key = self.key_for(scene.version_timestamp, scene.skin_id)
        path = self._path_for_key(key)
        if not source_hashes or not set(scene.source_bundles).issubset(source_hashes):
            raise ValueError("source bundle fingerprints must cover every scene source bundle")
        if any(not re.fullmatch(r"[0-9a-f]{64}", str(value)) for value in source_hashes.values()):
            raise ValueError("source bundle fingerprints must be SHA-256 hex digests")

        envelope = {
            "cache_format_version": CACHE_FORMAT_VERSION,
            "schema_version": SCHEMA_VERSION,
            "parser_version": self.parser_version,
            "version_timestamp": int(scene.version_timestamp),
            "skin_id": scene.skin_id,
            "config_hash": str(config_hash),
            "source_hashes": dict(sorted(source_hashes.items())),
            "scene": scene.to_dict(),
        }
        encoded = json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        # Validate the exact payload before it can replace a previous record.
        checked = json.loads(encoded)
        if PortraitScene.from_dict(checked["scene"]) != scene:
            raise ValueError("portrait scene cache round-trip changed scene data")
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", newline="\n", dir=path.parent,
                prefix=f".{path.name}.", suffix=".tmp", delete=False,
            ) as temporary:
                temp_name = temporary.name
                temporary.write(encoded)
                temporary.flush()
                os.fsync(temporary.fileno())
            # Re-read and validate the sibling temporary file before publish.
            staged = json.loads(Path(temp_name).read_text(encoding="utf-8"))
            if PortraitScene.from_dict(staged["scene"]) != scene:
                raise ValueError("staged portrait scene cache validation failed")
            os.replace(temp_name, path)
            temp_name = None
            snapshot_path = (
                self.cache_root / "_incomplete" / Path(*PurePosixPath(key).parts)
            )
            try:
                snapshot_path.unlink()
            except FileNotFoundError:
                pass
        finally:
            if temp_name is not None:
                try:
                    Path(temp_name).unlink()
                except FileNotFoundError:
                    pass
        return key
