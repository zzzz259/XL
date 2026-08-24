"""Lua 最终产物仓库：按版本保存反编译后的 Lua 文件。"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from .files import replace_directory
from .diagnostics import logger


# 角色自动解析的最小前置文件。其他 Base 文件缺失时，解析器会按已有能力降级。
REQUIRED_CHARACTER_LUA_FILES = ("BaseCard.lua", "BaseWord_cn.lua")


def version_key(version_timestamp: int | str) -> str:
    """将版本时间戳转换为安全、稳定的目录名。"""
    try:
        return str(int(version_timestamp))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"非法 Lua 版本标识: {version_timestamp!r}") from exc


def version_directory(lua_root: str | os.PathLike[str], version_timestamp: int | str) -> str:
    """返回指定版本的 Lua 目录，不创建目录。"""
    return os.path.join(os.fspath(lua_root), version_key(version_timestamp))


def list_lua_versions(lua_root: str | os.PathLike[str]) -> list[int]:
    """列出 output/lua 下已有的版本目录，按时间戳升序返回。"""
    root = Path(lua_root)
    if not root.is_dir():
        return []
    versions = []
    for child in root.iterdir():
        if not child.is_dir() or child.name.startswith("."):
            continue
        try:
            versions.append(int(child.name))
        except ValueError:
            continue
    return sorted(set(versions))


def latest_lua_version(lua_root: str | os.PathLike[str]) -> int | None:
    """返回已留存 Lua 中最新的版本时间戳。"""
    versions = list_lua_versions(lua_root)
    return versions[-1] if versions else None


def has_character_sources(
    lua_dir: str | os.PathLike[str],
    required_files: tuple[str, ...] = REQUIRED_CHARACTER_LUA_FILES,
) -> bool:
    """判断目录是否具备自动解析角色所需的最小 Base 文件集合。"""
    root = Path(lua_dir)
    return root.is_dir() and all((root / filename).is_file() for filename in required_files)


def should_auto_parse(
    version_timestamp: int | str,
    latest_timestamp: int | str | None,
    lua_dir: str | os.PathLike[str],
) -> bool:
    """判断一次 Lua 导出是否满足自动角色解析条件。"""
    if latest_timestamp is None:
        return False
    try:
        return int(version_timestamp) == int(latest_timestamp) and has_character_sources(lua_dir)
    except (TypeError, ValueError):
        return False


def _iter_lua_files(source_dir: Path):
    for path in source_dir.rglob("*.lua"):
        if path.is_file():
            yield path


def publish_lua_version(
    source_dir: str | os.PathLike[str],
    lua_root: str | os.PathLike[str],
    version_timestamp: int | str,
) -> tuple[str, int, bool]:
    """将 staging/material 中的 Lua 成品原子发布到 output/lua/<版本>。

    只复制反编译后的 ``*.lua``，不会把 ``*.lua.bytes`` 等中间文件带入最终目录。
    同一版本重复导出时替换该版本目录；其他版本目录保持不变。
    返回 (版本目录, 文件数, 是否具备角色解析 Base 文件)。
    """
    source = Path(source_dir)
    root = Path(lua_root)
    if not source.is_dir():
        raise FileNotFoundError(f"Lua staging 目录不存在: {source}")

    lua_files = list(_iter_lua_files(source))
    root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".lua-version-", dir=root))
    try:
        for path in lua_files:
            relative = path.relative_to(source)
            destination = staging / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
        target = Path(version_directory(root, version_timestamp))
        if lua_files:
            replace_directory(staging, target)
            staging = None
        else:
            logger.warning("Lua 导出没有生成反编译后的 .lua 文件: %s", source)
        count = len(lua_files)
        ready = bool(lua_files) and has_character_sources(target)
        logger.info(
            "Lua 版本已发布: version=%s files=%s character_sources=%s path=%s",
            version_key(version_timestamp), count, ready, target,
        )
        return str(target), count, ready
    finally:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)


def cleanup_lua_staging(material_dir: str | os.PathLike[str]) -> bool:
    """清理 data/material/assets/lua 临时目录，并保留空目录结构。"""
    lua_dir = Path(material_dir) / "assets" / "lua"
    if not lua_dir.exists():
        return False
    shutil.rmtree(lua_dir, ignore_errors=True)
    lua_dir.mkdir(parents=True, exist_ok=True)
    logger.info("已清理 Lua 临时目录: %s", lua_dir)
    return True
