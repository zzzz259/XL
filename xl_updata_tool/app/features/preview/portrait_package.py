"""Publish movable, version-bound portrait projects and consume their exact inputs.

The manifest is the source of truth after publication. Source AB files, catalogs,
and the material directory are never consulted by the package consumer.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from .output_publisher import _atlas_texture_names
from .portrait_config.asset_index import AssetIndex, IndexedAsset
from .portrait_config.model import AssetRef, PortraitScene
from .portrait_scene_export import _resolve_asset_path, build_portrait_scene_export_jobs


@dataclass(frozen=True, slots=True)
class PortraitPackage:
    scene: PortraitScene
    manifest_path: Path
    resources: tuple[dict[str, Any], ...]
    manifest: dict[str, Any]


@dataclass(frozen=True, slots=True)
class PortraitPackageSummary:
    published: int = 0
    failed: int = 0
    cancelled: bool = False
    manifest_paths: tuple[Path, ...] = ()
    failures: tuple[dict[str, str], ...] = ()
    metadata_incomplete: int = 0


class _Cancelled(Exception):
    pass


def _check_cancel(check):
    if check is not None and check():
        raise _Cancelled()


def _safe_name(value):
    original = str(value)
    if not original or original in {'.', '..'}:
        raise ValueError('unsafe empty portrait path component')
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', original).strip(' .')
    if not cleaned:
        raise ValueError('unsafe portrait path component')
    if cleaned != original:
        cleaned += '_' + hashlib.sha256(original.encode()).hexdigest()[:8]
    if cleaned.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}:
        cleaned = '_' + cleaned
    return cleaned[:140]


def _relative_path(root, relative):
    text = str(relative)
    posix = PurePosixPath(text)
    if (not text or '\\' in text or ':' in text or posix.is_absolute()
            or PureWindowsPath(text).drive or '..' in posix.parts or '.' == text):
        raise ValueError(f'unsafe portrait resource path: {text!r}')
    root = Path(root).resolve()
    target = (root / Path(*posix.parts)).resolve()
    if target == root or root not in target.parents:
        raise ValueError(f'portrait resource escapes package: {text!r}')
    return target


def _package_relative(scene):
    version = _safe_name(scene.version_timestamp)
    metadata = dict(getattr(scene, 'config_metadata', ()))
    if '/eventcovers/' in scene.prefab_container.replace('\\', '/').casefold():
        event_id = metadata.get('eventcover_id')
        stage = metadata.get('stage_index')
        match = re.fullmatch(r'(eventcovers_[^/]+?)_stage_(\d+)', scene.entry_config_id)
        event_name = f'eventcovers_{event_id}' if event_id else (
            match.group(1) if match else scene.entry_config_id
        )
        stage_name = f'stage_{int(stage):02d}' if stage else (
            f'stage_{int(match.group(2)):02d}' if match else 'stage_01'
        )
        return Path('portraits') / version / 'events' / _safe_name(event_name) / stage_name
    return Path('portraits') / version / 'characters' / _safe_name(scene.character_id or 'unknown') / _safe_name(scene.skin_id)


def find_portrait_package(output_root, scene) -> Path | None:
    path = Path(output_root) / _package_relative(scene) / 'skin.json'
    # Resolve the final path as well, so an existing junction cannot escape root.
    path = _relative_path(Path(output_root), path.relative_to(output_root).as_posix())
    return path if path.is_file() else None


def _sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def _identity(ref):
    return f'{ref.bundle_hash}:{ref.path_id}'


def query_portrait_metadata(skeleton_path, atlas_path):
    """Query actual CLI metadata; failures remain explicit instead of guessed names."""
    from .spine_adapter import SpineQueryRunner, _parse_animation_table, _run_spine_cli
    from app.platform.tool_locator import ToolLocator

    locator = ToolLocator.create()
    runner = SpineQueryRunner()
    skins = runner.query_skins(skeleton_path, atlas_path)
    command = [runner.spine_cli, 'query', os.fspath(Path(skeleton_path).resolve()),
               '--atlas', os.fspath(Path(atlas_path).resolve()), '--animation']
    result = _run_spine_cli(
        command, cwd=os.path.dirname(runner.spine_cli) or None,
        capture_output=True, text=True, timeout=15, env=locator.subprocess_env(),
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0,
    )
    return {
        'internal_skins': list(skins.skin_names),
        'animations': _parse_animation_table(result.stdout) if result.returncode == 0 else {},
        'query_ok': skins.ok and result.returncode == 0,
        'skin_query_stdout': skins.stdout, 'skin_query_stderr': skins.stderr,
        'animation_query_stdout': result.stdout, 'animation_query_stderr': result.stderr,
        'diagnostic': skins.error or skins.diagnostic,
    }


def _publish_one(scene, index, material_root, staging, provider, cancel_check):
    from .portrait_config.scope_policy import required_scene_assets
    from .portrait_config.static_layer_renderer import (
        StaticLayerSpec, generate_static_layer,
    )

    if int(scene.version_timestamp) != index.version_timestamp:
        raise ValueError('portrait scene and asset index versions differ')
    required = required_scene_assets(scene, index)
    source_paths = {}
    for ref in required:
        _check_cancel(cancel_check)
        source_paths[ref] = _resolve_asset_path(index, Path(material_root), ref)
    if not source_paths:
        raise ValueError('portrait scene has no required source assets')
    inputs, resources, parts = {}, [], []

    def add_file(source, relative, ref=None):
        _check_cancel(cancel_check)
        target = _relative_path(staging, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        record = {'path': relative, 'sha256': _sha(target), 'size': target.stat().st_size}
        if ref is not None:
            asset = index.find_exact(ref.bundle_hash, ref.path_id)[0]
            trace = {'source': ref.to_dict(), 'source_sha256': _sha(source),
                     'source_name': asset.name, 'source_container': asset.container,
                     'asset_type': asset.asset_type}
            record.update(trace)
            key = _identity(ref)
            if key not in inputs:
                inputs[key] = {**trace, 'path': relative, 'paths': [relative]}
            elif relative not in inputs[key]['paths']:
                inputs[key]['paths'].append(relative)
        resources.append(record)
        return target

    def update_sha(relative):
        record = next(item for item in resources if item['path'] == relative)
        record['sha256'] = _sha(staging / relative)
        record['size'] = (staging / relative).stat().st_size

    def copy_atlas(source, directory, ref, texture_refs):
        atlas_name = _safe_name(source.name)
        relative = f'{directory}/{atlas_name}'
        target = add_file(source, relative, ref)
        replacements, texture_paths = {}, []
        for page in _atlas_texture_names(source):
            # Original pages are source-local. Never resolve a path from a map
            # or atlas outside the already selected exact resource set.
            _relative_path(source.parent, page)
            matches = [item for item in texture_refs if source_paths[item].name.casefold() == PurePosixPath(page).name.casefold()]
            if len(matches) != 1:
                raise ValueError(f'atlas page {page!r} has {len(matches)} exact selected Texture2D inputs')
            texture_ref = matches[0]
            filename = _safe_name(PurePosixPath(page).name)
            texture_relative = f'{directory}/{filename}'
            if texture_relative not in texture_paths:
                add_file(source_paths[texture_ref], texture_relative, texture_ref)
                texture_paths.append(texture_relative)
            replacements[page] = filename
        if not replacements:
            raise ValueError(f'atlas has no texture pages: {source.name}')
        target.write_text('\n'.join(replacements.get(line.strip(), line) for line in source.read_text(encoding='utf-8-sig').splitlines()) + '\n', encoding='utf-8')
        update_sha(relative)
        return relative, texture_paths

    for part in scene.parts:
        if part.renderer is not None and not part.renderer.enabled:
            continue
        if part.spine is None and part.sprite is None and part.particle is None:
            continue
        _check_cancel(cancel_check)
        identity = hashlib.sha256(part.part_id.encode()).hexdigest()[:12]
        directory = f'part_{part.stable_order:04d}_{identity}'
        renderer = part.renderer
        order = (part.sorting_layer_value, renderer.sorting_order if renderer else 0, part.renderer_depth, part.stable_order)
        entry = {'part_id': part.part_id, 'directory': directory, 'unity_order': list(order)}
        if part.spine:
            spine = part.spine
            # Atlas page names are local to this Unity part's exact closure.
            # Two unrelated parts may legitimately use identically named PNGs.
            texture_refs = tuple(ref for ref in required_scene_assets(replace(scene, parts=(part,)), index)
                                 if index.find_exact(ref.bundle_hash, ref.path_id)[0].asset_type.casefold() == 'texture2d')
            if spine.skeleton_json not in source_paths:
                raise ValueError(f'missing exact skeleton input for {part.part_id}')
            skeleton_source = source_paths[spine.skeleton_json]
            skeleton_relative = f'{directory}/{_safe_name(skeleton_source.name)}'
            skeleton = add_file(skeleton_source, skeleton_relative, spine.skeleton_json)
            atlas_paths, pages = [], []
            for ref in spine.atlases or ((spine.atlas,) if spine.atlas else ()):
                atlas, textures = copy_atlas(source_paths[ref], directory, ref, texture_refs)
                atlas_paths.append(atlas)
                pages.extend(textures)
            if not atlas_paths:
                raise ValueError(f'missing exact atlas input for {part.part_id}')
            try:
                metadata = dict(provider(skeleton, staging / atlas_paths[0]))
            except Exception as exc:
                metadata = {'query_ok': False, 'diagnostic': str(exc)}
            skins = list(metadata.get('internal_skins', spine.skins))
            animations = metadata.get('animations', {name: None for name in spine.animations})
            entry.update(kind='spine', skeleton_path=skeleton_relative, atlas_paths=atlas_paths,
                         texture_paths=pages, internal_skins=skins, animations=animations,
                         expression_selectors=[name for name in skins if name.startswith('motion_')],
                         default_animation=spine.default_animation, loop=spine.loop,
                         time_scale=spine.time_scale, initial_internal_skin=spine.initial_skin,
                         animation_sequence=[{'animation': step.animation, 'loop': step.loop,
                                              'evidence': step.evidence} for step in spine.animation_sequence],
                         metadata=metadata)
        elif part.particle:
            particle = part.particle
            if particle.texture is not None and particle.texture in source_paths:
                relative = f'{directory}/{_safe_name(source_paths[particle.texture].name)}'
                add_file(source_paths[particle.texture], relative, particle.texture)
                entry['original_texture_path'] = relative
                entry['texture_paths'] = [relative]
            entry.update(kind='particle', profile=particle.profile,
                         particle=json.loads(particle.config_json),
                         system=particle.system.to_dict(),
                         material=particle.material.to_dict() if particle.material else None)
        else:
            sprite = part.sprite
            if sprite.texture not in source_paths:
                raise ValueError(f'missing exact Sprite texture input for {part.part_id}')
            relative = f'{directory}/source/{_safe_name(source_paths[sprite.texture].name)}'
            original = add_file(source_paths[sprite.texture], relative, sprite.texture)
            entry.update(kind='static_sprite', original_texture_path=relative,
                         internal_skins=['default'], expression_selectors=[], animations={'idle': 0.0},
                         generation={'alpha_mode': 'straight', 'pma': False,
                                     'coordinate_space': 'part_local', 'skeleton_scale': 0.01,
                                     'rect': list(sprite.rect), 'pivot': list(sprite.pivot),
                                     'pixels_per_unit': sprite.pixels_per_unit})
            try:
                # Publish a standalone part project. Placement is recorded in
                # scene.transform_chain and applied by the scene consumer.
                generated = generate_static_layer(StaticLayerSpec(
                    part.part_id, original, sprite.rect, sprite.pivot, sprite.pixels_per_unit,
                    (), order,
                    color=renderer.color if renderer else (1.0, 1.0, 1.0, 1.0),
                    flip_x=renderer.flip_x if renderer else False, flip_y=renderer.flip_y if renderer else False,
                    mask_interaction=renderer.mask_interaction if renderer else 0,
                    blend_mode=renderer.blend_mode if renderer else 'normal',
                    spine_transform_chain=(), skeleton_scale=0.01, pma=False,
                ), staging / directory / '_generate')
                skeleton_relative = f'{directory}/{generated.skeleton_path.name}'
                add_file(generated.skeleton_path, skeleton_relative)
                png_relative = f'{directory}/{generated.texture_path.name}'
                add_file(generated.texture_path, png_relative)
                atlas_relative = f'{directory}/{generated.atlas_path.name}'
                add_file(generated.atlas_path, atlas_relative)
                entry.update(skeleton_path=skeleton_relative, atlas_paths=[atlas_relative], texture_paths=[png_relative])
            except ValueError as exc:
                # Preserve recoverable source material and precise generation
                # parameters even when a native renderer limitation is present.
                entry['generation_diagnostic'] = str(exc)
            finally:
                shutil.rmtree(staging / directory / '_generate', ignore_errors=True)
        parts.append(entry)
    if not parts:
        raise ValueError('portrait scene has no enabled renderable parts')
    manifest = {'package_schema_version': 2, 'game_skin_id': scene.skin_id,
                'scene': scene.to_dict(), 'parts': parts, 'inputs': inputs, 'resources': resources}
    _write_json(staging / 'skin.json', manifest)
    load_portrait_package(staging)
    return manifest


def _replace_package(staging, target, finalize=lambda: None):
    backup = target.with_name(f'.{target.name}.previous-{uuid.uuid4().hex}')
    previous = target.exists()
    if previous:
        os.replace(target, backup)
    installed = False
    try:
        os.replace(staging, target)
        installed = True
        finalize()
    except BaseException:
        if installed:
            os.replace(target, staging)
        if previous:
            os.replace(backup, target)
        raise
    if previous:
        shutil.rmtree(backup, ignore_errors=True)


def _register_package(root, path):
    index_path = root / 'portraits' / 'index.json'
    existing = json.loads(index_path.read_text(encoding='utf-8')) if index_path.is_file() else {'packages': []}
    entries = {str(item) for item in existing.get('packages', ())}
    entries.add(path.relative_to(root).as_posix())
    for entry in entries:
        _relative_path(root, entry)
    temporary = index_path.with_name(f'.index-{uuid.uuid4().hex}.json')
    try:
        _write_json(temporary, {'package_schema_version': 2, 'packages': sorted(entries)})
        os.replace(temporary, index_path)
    finally:
        temporary.unlink(missing_ok=True)


def publish_portrait_packages(scenes, asset_index, material_root, output_root, *,
                              metadata_provider=None, cancel_check=None, progress_callback=None):
    """Stage and validate each skin before replacing its previous complete project."""
    root = Path(output_root).resolve()
    query = metadata_provider or query_portrait_metadata
    metadata_cache = {}
    def provider(skeleton, atlas):
        key = (_sha(skeleton), _sha(atlas))
        if key not in metadata_cache:
            result = dict(query(skeleton, atlas))
            if result.get('query_ok'):
                metadata_cache[key] = result
            return result
        return metadata_cache[key]
    paths, failures = [], []
    metadata_incomplete = 0
    cancelled = False
    for scene in scenes:
        staging = None
        try:
            _check_cancel(cancel_check)
            relative = _package_relative(scene)
            target = _relative_path(root, relative.as_posix())
            target.parent.mkdir(parents=True, exist_ok=True)
            # Keep the staging prefix shallow on Windows. Expanded skin paths
            # plus a UUID beside the target can exceed Win32's path limit even
            # when every final published file has a valid path length.
            staging = root / f'.portrait-stage-{uuid.uuid4().hex[:12]}'
            staging.mkdir()
            manifest = _publish_one(scene, asset_index, material_root, staging, provider, cancel_check)
            _check_cancel(cancel_check)
            _replace_package(staging, target, lambda: _register_package(root, target / 'skin.json'))
            paths.append(target / 'skin.json')
            missing_metadata = [part['part_id'] for part in manifest['parts']
                                if part['kind'] == 'spine' and not part['metadata'].get('query_ok')]
            metadata_incomplete += bool(missing_metadata)
            if progress_callback:
                progress_callback({'phase': 'portrait_package', 'status': 'published',
                                   'skin_id': scene.skin_id, 'manifest_path': str(paths[-1]),
                                   'metadata_missing_parts': missing_metadata})
        except _Cancelled:
            cancelled = True
            break
        except Exception as exc:
            failures.append({'skin_id': scene.skin_id, 'entry_config_id': scene.entry_config_id, 'error': str(exc)})
            if progress_callback:
                progress_callback({'phase': 'portrait_package', 'status': 'failed', **failures[-1]})
        finally:
            if staging is not None and staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
    return PortraitPackageSummary(len(paths), len(failures), cancelled, tuple(paths), tuple(failures), metadata_incomplete)


def load_portrait_package(path) -> PortraitPackage:
    try:
        return _load_portrait_package(path)
    except (KeyError, TypeError, AttributeError, IndexError) as exc:
        raise ValueError(f'invalid portrait package manifest: {exc}') from exc


def _load_portrait_package(path) -> PortraitPackage:
    manifest_path = Path(path).resolve()
    if manifest_path.is_dir():
        manifest_path /= 'skin.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if manifest.get('package_schema_version') not in {1, 2}:
        raise ValueError('unsupported portrait package schema')
    root = manifest_path.parent
    resources = tuple(manifest['resources'])
    registered, records = {}, {}
    for item in resources:
        target = _relative_path(root, item['path'])
        if item['path'] in registered:
            raise ValueError(f'duplicate portrait resource: {item["path"]}')
        if not target.is_file() or _sha(target) != item['sha256']:
            raise ValueError(f'portrait resource missing or SHA256 mismatch: {item["path"]}')
        registered[item['path']] = target
        records[item['path']] = item

    def resource(relative):
        target = _relative_path(root, relative)
        if relative not in registered:
            raise ValueError(f'portrait resource is not fingerprinted: {relative}')
        return target

    def source_identity(relative, expected):
        resource(relative)
        if records[relative].get('source') != expected.to_dict():
            raise ValueError(f'portrait resource identity mismatch: {relative}')

    for key, item in manifest['inputs'].items():
        ref = AssetRef.from_dict(item['source'])
        if key != _identity(ref) or item['path'] not in item['paths']:
            raise ValueError('portrait input identity mismatch')
        resource(item['path'])
        for relative in item['paths']:
            source_identity(relative, ref)
    scene = PortraitScene.from_dict(manifest['scene'])
    scene_parts = {part.part_id: part for part in scene.parts}
    expected_parts = {p.part_id for p in scene.parts
                      if (p.renderer is None or p.renderer.enabled) and (p.spine or p.sprite or p.particle)}
    actual_parts = [p['part_id'] for p in manifest['parts']]
    if len(set(actual_parts)) != len(actual_parts) or set(actual_parts) != expected_parts:
        raise ValueError('portrait package part coverage does not match saved scene')
    for part in manifest['parts']:
        _relative_path(root, part['directory'])
        for field in ('skeleton_path', 'original_texture_path'):
            if field in part:
                resource(part[field])
        for relative in (*part.get('atlas_paths', ()), *part.get('texture_paths', ())):
            resource(relative)
        original = scene_parts.get(part['part_id'])
        if original is None:
            raise ValueError('portrait part identity is absent from saved scene')
        if original.spine:
            source_identity(part['skeleton_path'], original.spine.skeleton_json)
            refs = original.spine.atlases or ((original.spine.atlas,) if original.spine.atlas else ())
            if len(refs) != len(part.get('atlas_paths', ())):
                raise ValueError('portrait atlas identity count mismatch')
            for relative, ref in zip(part['atlas_paths'], refs):
                source_identity(relative, ref)
        elif original.sprite:
            source_identity(part['original_texture_path'], original.sprite.texture)
        elif original.particle:
            particle = original.particle
            if (part.get('kind') != 'particle' or part.get('profile') != particle.profile
                    or part.get('particle') != json.loads(particle.config_json)
                    or part.get('system') != particle.system.to_dict()
                    or part.get('material') != (particle.material.to_dict() if particle.material else None)):
                raise ValueError('portrait particle configuration mismatch')
            if particle.texture is not None and 'original_texture_path' in part:
                source_identity(part['original_texture_path'], particle.texture)
            elif particle.profile != 'unsupported':
                raise ValueError('portrait particle texture missing')
        for relative in part.get('atlas_paths', ()):
            atlas = resource(relative)
            for page in _atlas_texture_names(atlas):
                if '\\' in page or ':' in page or PurePosixPath(page).is_absolute():
                    raise ValueError('unsafe atlas page path')
                page_path = (atlas.parent / page).resolve()
                if root not in page_path.parents or page_path not in registered.values():
                    raise ValueError('atlas page escapes package or lacks fingerprint')
    return PortraitPackage(scene, manifest_path, resources, manifest)


def load_published_portrait_scenes(output_root):
    """List portable scene metadata; resource hashes are checked at export time."""
    root = Path(output_root)
    index_path = root / 'portraits' / 'index.json'
    if not index_path.is_file():
        return (), ()
    scenes, diagnostics = {}, []
    try:
        index = json.loads(index_path.read_text(encoding='utf-8'))
        if index.get('package_schema_version') not in {1, 2} or not isinstance(index.get('packages'), list):
            raise ValueError('unsupported portrait package index')
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        return (), ((str(index_path), str(exc)),)
    for relative in index['packages']:
        try:
            path = _relative_path(root, relative)
            data = json.loads(path.read_text(encoding='utf-8'))
            if data.get('package_schema_version') not in {1, 2}:
                raise ValueError('unsupported portrait package schema')
            scene = PortraitScene.from_dict(data['scene'])
            expected = find_portrait_package(root, scene)
            if expected is None or expected.resolve() != path:
                raise ValueError('portrait index path does not match scene identity')
            scenes[(scene.version_timestamp, scene.skin_id)] = scene
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            diagnostics.append((str(relative), str(exc)))
    return tuple(scenes.values()), tuple(diagnostics)


def package_selectors(package):
    """Return actual Spine capabilities; generated Sprite actions are separate."""
    spine_parts = {p.part_id: p for p in package.scene.parts if p.spine}
    names, durations, skins = [], {}, []
    for entry in package.manifest['parts']:
        if entry['part_id'] not in spine_parts:
            continue
        for name, value in entry.get('animations', {}).items():
            if name not in names:
                names.append(name)
            speed = spine_parts[entry['part_id']].spine.time_scale
            if isinstance(value, (int, float)) and math.isfinite(value) and value >= 0 and speed > 0:
                durations[name] = max(durations.get(name, 0), value / speed)
        for skin in entry.get('internal_skins', ()):
            if skin not in skins:
                skins.append(skin)
    if not spine_parts:
        return ('idle',), {'idle': 0.0}, ('default',)
    return tuple(names), durations, tuple(skins)


def build_packaged_portrait_jobs(scene, package_path, output_root, name_resolver=None, settings=None, options=None):
    """Reuse export planning with a package-local exact index, then bind each part.

    The transient index is built from the manifest (never an AssetStudio map).
    A source reused by two Unity renderers has one canonical index identity and
    separate part paths; remapping after planning keeps those paths unambiguous.
    """
    package = load_portrait_package(package_path)
    saved = package.scene
    if ((scene.version_timestamp, scene.entry_config_id, scene.character_id, scene.skin_id)
            != (saved.version_timestamp, saved.entry_config_id, saved.character_id, saved.skin_id)):
        raise ValueError('portrait package belongs to a different configured scene')
    root = package.manifest_path.parent
    parts = {item['part_id']: item for item in package.manifest['parts']}
    if settings is not None:
        names, _, skins = package_selectors(package)
        if settings.animation not in names:
            raise ValueError(f'custom animation unavailable in package: {settings.animation}')
        if len(settings.skins) != 1 or settings.skins[0] not in skins:
            raise ValueError(f'custom internal skin unavailable in package: {settings.skins}')
        saved = replace(saved, parts=tuple(
            replace(p, spine=replace(p.spine,
                animations=tuple(parts[p.part_id].get('animations', ())) or (p.spine.default_animation,),
                skins=tuple(parts[p.part_id].get('internal_skins', ())) or (p.spine.initial_skin or 'default',),
            )) if p.spine and p.part_id in parts else p for p in saved.parts
        ))
    assets = []
    for item in package.manifest['inputs'].values():
        ref = AssetRef.from_dict(item['source'])
        relative = PurePosixPath(item['path'])
        assets.append(IndexedAsset(
            relative.name, str(relative.parent / '_package.prefab'), ref.bundle_hash,
            ref.path_id, (), int(saved.version_timestamp), root, True,
            'skin.json#inputs/' + _identity(ref), item['asset_type'],
        ))
    index = AssetIndex(int(saved.version_timestamp), tuple(assets))
    jobs = build_portrait_scene_export_jobs(saved, index, root, output_root, name_resolver, settings, options)
    return tuple(replace(
        job,
        sources=tuple(replace(source,
                              skeleton_path=_relative_path(root, parts[source.part_id]['skeleton_path']),
                              atlas_path=_relative_path(root, parts[source.part_id]['atlas_paths'][0]))
                      for source in job.sources),
        sprite_sources=tuple(replace(source, texture_path=_relative_path(root, parts[source.part_id]['original_texture_path']))
                             for source in job.sprite_sources),
        particle_sources=tuple(replace(source, texture_path=_relative_path(root, parts[source.part_id]['original_texture_path']))
                               for source in job.particle_sources),
    ) for job in jobs)
