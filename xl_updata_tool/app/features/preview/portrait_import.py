"""Decode one configured portrait scene's exact asset closure into a package.

Only static full-scene portrait objects are handled here. Battle objects and
event covers are intentionally out of scope for this module.
"""
from __future__ import annotations

from pathlib import Path
import tempfile

from app.platform.diagnostics import logger

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
                              cancel_check=None, progress_callback=None, metadata_provider=None):
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
                    cancel_check=cancel_check, progress_callback=progress_callback,
                    metadata_provider=metadata_provider)
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
