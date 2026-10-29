"""Plan configured portrait and battle objects before decoding selected assets."""
from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile

from app.platform.diagnostics import logger
from app.shared.character_import import CharacterAssetImportSummary

from .portrait_config.asset_index import AssetIndex
from .portrait_config.builder import build_portrait_scenes
from .portrait_config.lua_config import (
    PortraitRoot, _ENTRY_START, _mask_lua_comments, _matching_table_end,
    load_eventcover_roots, load_portrait_roots,
)
from .portrait_config.scope_policy import required_scene_assets
from .portrait_config.extracted_paths import identity_output_path


def _load_bundle(path):
    import UnityPy
    UnityPy.set_assetbundle_decrypt_key(b"yunguihaowan1234")
    return UnityPy.load(str(path))


def _check_cancel(cancel_check):
    if cancel_check and cancel_check():
        raise InterruptedError("已取消")


def refresh_portrait_packages(scenes, index, bundle_root, material_root, output_root, *,
                              cancel_check=None, progress_callback=None):
    """Decode only selected exact closures into scratch, then replace validated packages.

    Existing material exports are not modified. A failed/cancelled decode never
    reaches package publication, so the previous portable project survives.
    """
    from .portrait_package import PortraitPackageSummary, publish_portrait_packages

    scenes = tuple(scenes)
    bundles = Path(bundle_root).resolve()
    version = str(index.version_timestamp)
    if bundles.name != version or any(str(s.version_timestamp) != version for s in scenes):
        raise ValueError('refresh source version does not match selected scenes')
    scratch_parent = Path(material_root).parent
    scratch_parent.mkdir(parents=True, exist_ok=True)
    paths, failures, metadata_incomplete, cancelled = [], [], 0, False
    for scene in scenes:
        try:
            _check_cancel(cancel_check)
            with tempfile.TemporaryDirectory(prefix='.portrait-refresh-', dir=scratch_parent) as temporary:
                staging = Path(temporary)
                grouped = {}
                for ref in required_scene_assets(scene, index):
                    matches = index.find_exact(ref.bundle_hash, ref.path_id)
                    if len(matches) != 1:
                        raise ValueError(f'Exact refresh identity missing or ambiguous: {ref}')
                    asset = matches[0]
                    if (asset.asset_type not in {'TextAsset', 'Texture2D'} or not asset.bundle_present
                            or bundles not in asset.bundle_path.resolve().parents):
                        raise ValueError(f'Invalid same-version refresh source: {ref}')
                    grouped.setdefault(asset.bundle_path, []).append((ref, asset))
                for bundle, assets in grouped.items():
                    _check_cancel(cancel_check)
                    env = _load_bundle(bundle)
                    wanted = {ref.path_id for ref, _ in assets}
                    readers = {}
                    for reader in env.objects:
                        if reader.path_id in wanted:
                            readers.setdefault(reader.path_id, []).append(reader)
                    for ref, asset in assets:
                        _check_cancel(cancel_check)
                        found = readers.get(ref.path_id, ())
                        if len(found) != 1 or found[0].type.name != asset.asset_type:
                            raise ValueError(f'Exact refresh object missing/ambiguous: {ref}')
                        value = found[0].read()
                        target = staging / Path(*identity_output_path(asset).parts)
                        target.parent.mkdir(parents=True, exist_ok=True)
                        if asset.asset_type == 'TextAsset':
                            script = value.m_Script
                            target.write_bytes(script.encode('utf-8', errors='surrogateescape')
                                               if isinstance(script, str) else bytes(script))
                        else:
                            value.image.save(target, format='PNG')
                        if not target.stat().st_size:
                            raise ValueError(f'Empty refresh object: {ref}')
                        event = {'phase': 'portrait_refresh_extract', 'status': 'extracted',
                                 'skin_id': scene.skin_id, 'bundle_hash': ref.bundle_hash,
                                 'path_id': ref.path_id, 'asset_type': asset.asset_type,
                                 'bytes': target.stat().st_size}
                        logger.info('更新工程资源 %s:%s', ref.bundle_hash, ref.path_id,
                                    extra={'event': 'portrait_refresh.asset.extracted', 'details': event})
                        if progress_callback:
                            progress_callback(event)
                    del readers, env
                _check_cancel(cancel_check)
                result = publish_portrait_packages((scene,), index, staging, output_root,
                    cancel_check=cancel_check, progress_callback=progress_callback)
                paths.extend(result.manifest_paths)
                failures.extend(result.failures)
                metadata_incomplete += result.metadata_incomplete
                if result.cancelled:
                    cancelled = True
                    break
        except InterruptedError:
            cancelled = True
            break
        except Exception as error:
            failure = {'skin_id': scene.skin_id, 'entry_config_id': scene.entry_config_id, 'error': str(error)}
            failures.append(failure)
            if progress_callback:
                progress_callback({'phase': 'portrait_refresh', 'status': 'failed', **failure})
    return PortraitPackageSummary(len(paths), len(failures), cancelled, tuple(paths),
                                  tuple(failures), metadata_incomplete)


def load_battle_roots(lua_root, version_timestamp):
    """Select BaseFashion.spd independently; never merge battle into a portrait."""
    source = Path(lua_root) / str(int(version_timestamp)) / "BaseFashion.lua"
    text = source.read_text(encoding="utf-8-sig")
    searchable = _mask_lua_comments(text)
    roots, seen, cursor = [], set(), 0
    while match := _ENTRY_START.search(searchable, cursor):
        end = _matching_table_end(searchable, match.end() - 1)
        if end is None:
            raise ValueError(f"Invalid BaseFashion table at {source}:{match.start()}")
        body = searchable[match.end():end]
        spd = re.search(r"\bspd\s*=\s*['\"]([^'\"]+)['\"]", body)
        if spd and spd.group(1).strip() and spd.group(1) not in seen:
            name = spd.group(1)
            seen.add(name)
            card = re.search(r"\bcard_id\s*=\s*(\d+)", body)
            roots.append(PortraitRoot(
                skin_id=f"battle_{match.group(1)}", version_timestamp=int(version_timestamp),
                prefab_name=name, position=None,
                metadata=(("entry_id", match.group(1)), ("display_family", "battle"),
                          ("card_id", card.group(1) if card else "")),
                evidence=f"{source}:{text.count(chr(10), 0, match.start()) + 1}",
            ))
        cursor = end + 1
    return roots


def _relative_output(asset):
    container = PurePosixPath(asset.container.replace("\\", "/"))
    name = PurePosixPath(asset.name.replace("\\", "/"))
    for path in (container, name):
        if (not str(path) or str(path) == "." or path.is_absolute()
                or any(part in {"..", "."} or ":" in part for part in path.parts)):
            raise ValueError(f"unsafe configured asset path: {asset.container}/{asset.name}")
    if asset.asset_type == "Texture2D" and name.suffix.casefold() != ".png":
        name = PurePosixPath(str(name) + ".png")
    return (container.parent / name).as_posix()


def _publish_files(staging, material, files, cancel_check):
    """Replace selected files as one rollback-capable transaction."""
    material.mkdir(parents=True, exist_ok=True)
    backup = Path(tempfile.mkdtemp(prefix=".portrait-backup-", dir=material.parent))
    changed, backed_up = [], []
    try:
        for relative in files:
            _check_cancel(cancel_check)
            destination = material / relative
            # resolve() also rejects a pre-existing symlink/junction out of root.
            if material.resolve() not in destination.resolve().parents:
                raise ValueError(f"asset destination escapes material root: {relative}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                if not destination.is_file():
                    raise ValueError(f"asset destination is not a file: {relative}")
                previous = backup / relative
                previous.parent.mkdir(parents=True, exist_ok=True)
                os.replace(destination, previous)
                backed_up.append(relative)
            os.replace(staging / relative, destination)
            changed.append(relative)
        _check_cancel(cancel_check)
    except BaseException:
        for relative in reversed(changed):
            (material / relative).unlink(missing_ok=True)
        for relative in reversed(backed_up):
            os.replace(backup / relative, material / relative)
        raise
    finally:
        shutil.rmtree(backup, ignore_errors=True)


def export_configured_character_assets(
    *, version_timestamp, catalog_path, lua_root, bundle_root, material_root,
    cancel_check=None, progress_callback=None,
):
    """Decode only exact required TextAsset/Texture2D IDs after a complete plan."""
    _check_cancel(cancel_check)
    version = int(version_timestamp)
    catalog, lua, bundles, material = map(Path, (catalog_path, lua_root, bundle_root, material_root))
    if bundles.name != str(version):
        raise ValueError("Configured import bundle directory must match the selected version")
    for filename in ("BaseFashion.lua", "BaseActivityStageConfig.lua"):
        if not (lua / str(version) / filename).is_file():
            raise ValueError(f"Missing same-version Lua {filename}; import Lua for version {version} first")
    if not catalog.is_file():
        raise ValueError("AssetStudio assets_map.json must be generated before configured extraction")
    index = AssetIndex.from_catalog(catalog, bundles, version,
        cab_cache_path=material.parent / 'cache' / 'portrait-cab' / f'{version}.json')
    configured_roots = [*load_portrait_roots(lua, version), *load_eventcover_roots(lua, version)]
    roots = [root for root in configured_roots
             if getattr(root, "unresolved_reason", None) != "missing_show_spine"]
    for root in configured_roots:
        if getattr(root, "unresolved_reason", None) == "missing_show_spine":
            logger.info("无展示配置，跳过 skin=%s", root.skin_id, extra={
                "event": "portrait_import.selection.not_displayed",
                "details": {"skin_id": root.skin_id, "reason": "missing_show_spine"},
            })
    battle_roots = load_battle_roots(lua, version)
    scenes, battle_scenes = [], []
    selected = set()
    incomplete = 0
    for number, root in enumerate([*roots, *battle_roots], 1):
        _check_cancel(cancel_check)
        built = build_portrait_scenes(version, [root], index)
        target = battle_scenes if number > len(roots) else scenes
        for scene in built:
            target.append(scene)
            diagnostics = tuple(getattr(scene, "diagnostics", ()))
            errors = [item for item in diagnostics if item.severity == "error"]
            incomplete += bool(errors)
            logger.info("配置资源计划 skin=%s parts=%s diagnostics=%s", scene.skin_id,
                        len(scene.parts), len(diagnostics), extra={
                "event": "portrait_import.selection.scene", "details": {
                    "skin_id": scene.skin_id, "family": "battle" if target is battle_scenes else "portrait",
                    "diagnostics": [{"code": item.code, "message": item.message,
                                     "severity": item.severity} for item in diagnostics],
                },
            })
            selected.update(required_scene_assets(scene, index))
        if progress_callback:
            progress_callback(f"配置计划 {root.skin_id}", number, len(roots) + len(battle_roots))
    _check_cancel(cancel_check)
    assets, destinations = [], {}
    export_identities = {}
    for item in index.assets:
        if item.asset_type not in {"TextAsset", "Texture2D"}:
            continue
        try:
            relative = _relative_output(item)
        except ValueError:
            continue
        export_identities.setdefault(relative.casefold(), set()).add((item.bundle_hash, item.path_id))
    for reference in sorted(selected):
        matches = index.find_exact(reference.bundle_hash, reference.path_id)
        if len(matches) != 1:
            raise ValueError(f"Required asset is absent or ambiguous: {reference}")
        asset = matches[0]
        if asset.asset_type not in {"TextAsset", "Texture2D"} or not asset.bundle_present:
            raise ValueError(f"Required asset has an invalid type or missing bundle: {reference}")
        relative = _relative_output(asset)
        if len(export_identities[relative.casefold()]) > 1:
            relative = identity_output_path(asset).as_posix()
        collision = destinations.get(relative.casefold())
        if collision is not None and collision != reference:
            raise ValueError(f"Multiple selected object identities share destination: {relative}")
        destinations[relative.casefold()] = reference
        assets.append((reference, asset, relative))
    if not assets:
        raise ValueError("No configured character assets were selected; inspect same-version Lua and scene diagnostics")
    blueprint = catalog.parent / "configured_character_plan.json"
    blueprint_temp = blueprint.with_suffix(".json.tmp")
    payload = {
        "version_timestamp": version, "scene_count": len(scenes), "battle_scene_count": len(battle_scenes),
        "incomplete_scenes": incomplete,
        "scenes": [scene.to_dict() for scene in scenes],
        "battle_scenes": [scene.to_dict() for scene in battle_scenes],
        "assets": [{"bundle_hash": ref.bundle_hash, "path_id": ref.path_id,
                    "bundle_path": str(asset.bundle_path), "asset_type": asset.asset_type,
                    "destination": relative} for ref, asset, relative in assets],
    }
    try:
        blueprint_temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        _check_cancel(cancel_check)
        os.replace(blueprint_temp, blueprint)
    finally:
        blueprint_temp.unlink(missing_ok=True)
    material.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".portrait-selected-", dir=material.parent))
    completed = 0
    try:
        grouped = {}
        for reference, asset, relative in assets:
            grouped.setdefault(asset.bundle_path, []).append((reference, asset, relative))
        for bundle_path, required in grouped.items():
            _check_cancel(cancel_check)
            environment = _load_bundle(bundle_path)
            object_readers = {}
            wanted = {ref.path_id for ref, _asset, _relative in required}
            for reader in environment.objects:
                if reader.path_id in wanted:
                    object_readers.setdefault(reader.path_id, []).append(reader)
            for reference, asset, relative in required:
                _check_cancel(cancel_check)
                readers = object_readers.get(reference.path_id, ())
                if len(readers) != 1 or readers[0].type.name != asset.asset_type:
                    raise ValueError(f"Exact Unity object missing/ambiguous or type mismatch: {reference}")
                value = readers[0].read()
                destination = staging / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                if asset.asset_type == "TextAsset":
                    script = value.m_Script
                    data = script.encode("utf-8", errors="surrogateescape") if isinstance(script, str) else bytes(script)
                    destination.write_bytes(data)
                else:
                    value.image.save(destination, format="PNG")
                if destination.stat().st_size == 0:
                    raise ValueError(f"Required object decoded to an empty file: {reference}")
                completed += 1
                logger.info("选择资源提取 %s:%s -> %s", reference.bundle_hash, reference.path_id,
                            relative, extra={"event": "portrait_import.asset.extracted", "details": {
                    "source_bundle": str(bundle_path), "bundle_hash": reference.bundle_hash,
                    "object_id": reference.path_id, "asset_type": asset.asset_type,
                    "destination": str(material / relative), "bytes": destination.stat().st_size,
                    "index": completed, "total": len(assets),
                }})
                if progress_callback:
                    progress_callback(f"配置提取 {asset.name}", completed, len(assets))
            del object_readers, environment
        files = tuple(relative for _ref, _asset, relative in assets)
        _publish_files(staging, material, files, cancel_check)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    selected_refs = {(ref.bundle_hash, ref.path_id) for ref in selected}
    skipped = len({(asset.bundle_hash, asset.path_id) for asset in index.assets
                   if asset.asset_type == "Texture2D"
                   and re.search(r"(?:[_\-.](?:n|e|normal|normalmap|bump|noise|flow|mask))$", asset.name, re.I)
                   and (asset.bundle_hash, asset.path_id) not in selected_refs})
    result = CharacterAssetImportSummary(len(scenes), len(assets), files, str(blueprint),
                                         incomplete, len(battle_scenes), skipped)
    logger.info("配置提取完成 scenes=%s battle=%s assets=%s auxiliary_skipped=%s incomplete=%s",
                result.scene_count, result.battle_scene_count, result.asset_count, skipped, incomplete,
                extra={"event": "portrait_import.selection.complete", "details": {
                    "version": version, "scene_count": result.scene_count,
                    "battle_scene_count": result.battle_scene_count, "asset_count": result.asset_count,
                    "skipped_auxiliary": skipped, "incomplete_scenes": incomplete,
                    "blueprint_path": str(blueprint),
                }})
    return result
