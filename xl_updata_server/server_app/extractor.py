"""从选中的 Unity Bundle 提取角色 Lua 源资源。"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path

import UnityPy

from .selector import LUA_NAMES

UNITYFS_MAGIC = b"UnityFS"
LUA_OBJECT_NAMES = frozenset(re.sub(r"\.(bytes|bank)$", "", name, flags=re.IGNORECASE) for name in LUA_NAMES)


def safe_role_name(value: str) -> str:
    value = re.split(r"[/\\]", str(value))[-1]
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip(".")
    return value or "unnamed"


def repair_bundle_header(bundle_path: Path) -> Path:
    """Remove the game's custom prefix so UnityPy sees the UnityFS header."""
    data = bundle_path.read_bytes()
    offset = data.find(UNITYFS_MAGIC)
    if offset < 0:
        raise ValueError(f"Bundle 缺少 UnityFS 文件头: {bundle_path}")
    if offset:
        bundle_path.write_bytes(data[offset:])
    return bundle_path


def is_lua_asset_name(name: str) -> bool:
    normalized = re.sub(r"\.(bytes|bank)$", "", str(name), flags=re.IGNORECASE)
    return normalized.lower() in {value.lower() for value in LUA_OBJECT_NAMES}


def _object_name(asset_object, asset) -> str:
    return str(getattr(asset, "m_Name", "") or getattr(asset_object, "name", "") or "")


def _script_bytes(asset) -> bytes:
    value = getattr(asset, "m_Script", b"")
    if isinstance(value, str):
        return value.encode("utf-8", errors="surrogateescape")
    return bytes(value or b"")


def extract_lua_files(bundle_paths: Sequence[Path], output_dir: Path) -> tuple[Path, ...]:
    output_dir.mkdir(parents=True, exist_ok=True)
    result: list[Path] = []
    for bundle_path in bundle_paths:
        repair_bundle_header(bundle_path)
        environment = UnityPy.load(str(bundle_path))
        for asset_object in environment.objects:
            if getattr(getattr(asset_object, "type", None), "name", "") != "TextAsset":
                continue
            asset = asset_object.read()
            name = _object_name(asset_object, asset)
            if not is_lua_asset_name(name):
                continue
            target = output_dir / re.sub(r"\.(bytes|bank)$", "", name, flags=re.IGNORECASE)
            target.write_bytes(_script_bytes(asset))
            result.append(target)
    return tuple(sorted(set(result)))
