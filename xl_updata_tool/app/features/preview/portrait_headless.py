"""One-call, no-Qt, resource-limited full 2x PNG render for one portrait skin.

Input is only a same-version workspace (decoded Lua + bundles + asset
catalog), the character/skin identity, an output directory and
administrator-configured renderer tool paths. One call performs:
read config -> exact resource closure -> package/scene preparation ->
native full 2x PNG -> return dimensions, hash and resource evidence.

Each skin starts one native render subprocess, serially, with one-CPU affinity
and a finite timeout. Linux systemd units enforce the 512 MiB memory cap; the
development fallback provides affinity and process-tree cleanup but no memory
evidence. Publication is atomic and only happens after pixel verification, so
a failed attempt never replaces a previous artifact.

Automatic updates pass ``workspace_root``; an explicit manual backfill of an
already published package passes ``input_root`` + ``package_path`` instead.
Both share this single entry; neither sends group messages by itself.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import json
import math
import os
from pathlib import Path
import re
import struct
import sys
import tempfile
import time

from .scene_export_adapter import publish_scene_result, write_scene_descriptor
from app.platform.portrait_render_process import (
    DEFAULT_MEMORY_HIGH_BYTES, DEFAULT_MEMORY_MAX_BYTES, DEFAULT_TIMEOUT_SECONDS, RenderProcessLimits)

PROFILE = {'format': 'png', 'scale': 2, 'scope': 'full'}

_IDENTITY = re.compile(r'[A-Za-z0-9_-]+')


def _inside(path, root, label):
    resolved = Path(path).resolve()
    if resolved == root or root not in resolved.parents:
        raise ValueError(f'{label} escapes declared root')
    return resolved


def _absolute_file(payload, key, *, required=True):
    value = payload.get(key)
    if value is None and not required:
        return None
    if not isinstance(value, str) or not Path(value).is_absolute():
        raise ValueError(f'{key} must be an absolute local path')
    path = Path(value).resolve()
    if not path.is_file():
        raise ValueError(f'{key} does not name an existing file')
    return path


def _limits(value):
    if value is None:
        return RenderProcessLimits()
    if isinstance(value, RenderProcessLimits):
        return value
    if not isinstance(value, dict):
        raise ValueError('limits must be an object')
    unknown = set(value) - {'memory_max_bytes', 'memory_high_bytes', 'cpu_affinity', 'timeout_seconds'}
    if unknown:
        raise ValueError(f'unknown limits fields: {sorted(unknown)}')
    memory_max = value.get('memory_max_bytes', DEFAULT_MEMORY_MAX_BYTES)
    if type(memory_max) is not int:
        raise ValueError('limits.memory_max_bytes must be an integer')
    memory_high = value.get('memory_high_bytes', min(DEFAULT_MEMORY_HIGH_BYTES, memory_max))
    if type(memory_high) is not int:
        raise ValueError('limits.memory_high_bytes must be an integer')
    affinity = value.get('cpu_affinity', [0])
    if (not isinstance(affinity, (list, tuple)) or not affinity
            or any(type(core) is not int or core < 0 for core in affinity)):
        raise ValueError('limits.cpu_affinity must be a list of non-negative integers')
    timeout = value.get('timeout_seconds', DEFAULT_TIMEOUT_SECONDS)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise ValueError('limits.timeout_seconds must be a number')
    timeout = float(timeout)
    return RenderProcessLimits(
        memory_max_bytes=memory_max, memory_high_bytes=memory_high,
        cpu_affinity=tuple(affinity), timeout_seconds=timeout)


def validate_request(payload):
    """Validate the fixed 2x PNG profile, identities and absolute local roots."""
    if not isinstance(payload, dict):
        raise ValueError('request must be an object')
    if type(payload.get('schema_version')) is not int or payload['schema_version'] != 2:
        raise ValueError('unsupported schema_version')
    if payload.get('profile') != PROFILE or type(payload['profile'].get('scale')) is not int:
        raise ValueError('only full 2x PNG profile is supported')
    rows = payload.get('png_working_rows', 128)
    if type(rows) is not int or not 0 <= rows <= 1024:
        raise ValueError('png_working_rows must be an integer in 0..1024')
    for key in ('job_id', 'version', 'character_id', 'skin_id'):
        value = payload.get(key)
        if not isinstance(value, str) or not _IDENTITY.fullmatch(value):
            raise ValueError(f'invalid {key} identity')
    if not payload['version'].isdigit():
        raise ValueError('version must be a numeric version timestamp')
    roots = {}
    for key in ('output_root', 'work_root'):
        value = payload.get(key)
        if not isinstance(value, str) or not Path(value).is_absolute():
            raise ValueError(f'{key} must be an absolute local path')
        roots[key] = Path(value).resolve()
        if not roots[key].is_dir():
            raise ValueError(f'{key} must be an existing directory')
    workspace = payload.get('workspace_root')
    package = payload.get('package_path')
    if workspace is not None and package is not None:
        raise ValueError('workspace_root and package_path are mutually exclusive')
    if workspace is None and package is None:
        raise ValueError('one of workspace_root or package_path is required')
    if workspace is not None:
        if not isinstance(workspace, str) or not Path(workspace).is_absolute():
            raise ValueError('workspace_root must be an absolute local path')
        roots['workspace_root'] = Path(workspace).resolve()
        if not roots['workspace_root'].is_dir():
            raise ValueError('workspace_root must be an existing directory')
    else:
        input_root = payload.get('input_root')
        if not isinstance(input_root, str) or not Path(input_root).is_absolute():
            raise ValueError('input_root must be an absolute local path')
        roots['input_root'] = Path(input_root).resolve()
        if not roots['input_root'].is_dir():
            raise ValueError('input_root must be an existing directory')
        if not isinstance(package, str) or not Path(package).is_absolute():
            raise ValueError('package_path must be an absolute local path')
        roots['package_path'] = _inside(package, roots['input_root'], 'package_path')
    for key, root in roots.items():
        for other, other_root in roots.items():
            if key != other and key != 'package_path' and other != 'package_path':
                if root == other_root or root in other_root.parents or other_root in root.parents:
                    raise ValueError(f'{key} and {other} must not overlap')
    tools = {
        'dotnet_path': _absolute_file(payload, 'dotnet_path'),
        'scene_cli_path': _absolute_file(payload, 'scene_cli_path'),
        'query_cli_path': _absolute_file(payload, 'query_cli_path', required=False),
        'xvfb_run_path': _absolute_file(payload, 'xvfb_run_path', required=False),
        'catalog_path': _absolute_file(payload, 'catalog_path', required=False),
    }
    result = dict(payload)
    result['png_working_rows'] = rows
    result['limits'] = _limits(payload.get('limits'))
    result.update({key: str(value) for key, value in roots.items() if value is not None})
    result.update({key: str(value) for key, value in tools.items() if value is not None})
    return result


def _workspace_inputs(request):
    workspace = Path(request['workspace_root'])
    version = request['version']
    lua_dir = workspace / 'lua' / version
    if not (lua_dir / 'BaseFashion.lua').is_file():
        raise ValueError(f'missing same-version Lua BaseFashion.lua under {lua_dir}')
    bundle_root = workspace / 'bundles' / version
    if not bundle_root.is_dir():
        raise ValueError(f'missing same-version bundle directory: {bundle_root}')
    catalog = Path(request.get('catalog_path') or workspace / 'assets_map.json')
    if not catalog.is_file():
        raise ValueError(f'missing asset catalog: {catalog}')
    return catalog, lua_dir.parent, bundle_root


def _select_portrait_root(lua_root, version, skin_id):
    from .portrait_config.lua_config import load_portrait_roots
    roots = [root for root in load_portrait_roots(Path(lua_root), int(version))
             if getattr(root, 'unresolved_reason', None) != 'missing_show_spine']
    for root in roots:
        if root.skin_id == skin_id:
            return root
    raise ValueError(
        f'skin {skin_id} not found in BaseFashion.lua for version {version} ({len(roots)} configured skins)')


def _check_scene_identity(scene, request):
    if (str(scene.version_timestamp), scene.character_id, scene.skin_id) != (
            request['version'], request['character_id'], request['skin_id']):
        raise ValueError('configured scene identity does not match request')


def _build_workspace_scene(request):
    from .portrait_config.asset_index import AssetIndex
    from .portrait_config.builder import build_portrait_scenes
    from .scene_capabilities import portrait_scene_capabilities

    catalog, lua_root, bundle_root = _workspace_inputs(request)
    index = AssetIndex.from_catalog(
        catalog, bundle_root, int(request['version']),
        cab_cache_path=Path(request['work_root']) / 'cache' / 'portrait-cab' / f"{request['version']}.json")
    root = _select_portrait_root(lua_root, request['version'], request['skin_id'])
    scenes = build_portrait_scenes(int(request['version']), (root,), index)
    if len(scenes) != 1:
        raise ValueError(f'expected exactly one configured scene for skin {request["skin_id"]}, got {len(scenes)}')
    scene = scenes[0]
    _check_scene_identity(scene, request)
    capabilities = portrait_scene_capabilities(scene)
    if not capabilities.can_export:
        raise ValueError(f'full scene cannot be exported: {capabilities.state}: {capabilities.reason}')
    return scene, index, bundle_root


def _plan_package_job(scene, manifest_path, work):
    from .portrait_package import build_packaged_portrait_jobs
    from .portrait_scene_export import prepare_portrait_scene_export_job, scene_resource_family

    if scene_resource_family(scene) != 'cardspine':
        raise ValueError('only cardspine character scenes are supported')
    jobs = build_packaged_portrait_jobs(scene, manifest_path, work / 'render-output',
        settings=None, options={'include_effects': True, 'include_interactions': False, 'allow_basic': False})
    png = tuple(job for job in jobs if job.settings.format.casefold() == 'png'
        and job.render_scope == 'full' and not job.interaction_id and job.export_mode == 'builtin')
    if len(png) != 1:
        raise ValueError('expected exactly one builtin full PNG job')
    job = replace(png[0], settings=replace(png[0].settings, scale=2))
    return prepare_portrait_scene_export_job(job, work / 'prepared')


def build_scene_command(job, request, temporary_dir):
    """Write the scene descriptor and return the native render command.

    This is the seam tests replace with a fake renderer; the default
    implementation uses the administrator-configured dotnet + scene CLI.
    """
    descriptor = write_scene_descriptor(job, temporary_dir)
    return [request['dotnet_path'], request['scene_cli_path'], str(descriptor)]


class _QueryLaunch:
    """Minimal launch adapter for the administrator-configured metadata query CLI."""

    def __init__(self, prefix, env):
        self.query_prefix = tuple(prefix)
        self.env = dict(env)

    def run(self, command, *, capture_output, text, timeout, check=False):
        from app.platform.processes import run_external_process
        return run_external_process(
            list(command), tool='PortraitQueryCLI', env=dict(self.env), timeout=timeout,
            max_output_bytes=65536, cancel_tree=True, check=check,
            capture_output=True, text=text)


def _metadata_provider(request, env):
    query = request.get('query_cli_path')
    if not query:
        return None
    launch = _QueryLaunch([request['dotnet_path'], query], env)
    from .portrait_package import query_portrait_metadata
    return lambda skeleton, atlas: query_portrait_metadata(skeleton, atlas, launch=launch)


def _plan_request(request, work):
    from .portrait_package import load_portrait_package
    from .scene_capabilities import portrait_scene_capabilities

    work = _inside(work, Path(request['work_root']), 'temporary_dir')
    work.mkdir(parents=True, exist_ok=True)
    if 'package_path' in request:
        package = load_portrait_package(request['package_path'])
        scene = package.scene
        _check_scene_identity(scene, request)
        capabilities = portrait_scene_capabilities(scene)
        if not capabilities.can_export:
            raise ValueError(f'full scene cannot be exported: {capabilities.state}: {capabilities.reason}')
        return _plan_package_job(scene, package.manifest_path, work), 0
    scene, index, bundle_root = _build_workspace_scene(request)
    from .portrait_import import refresh_portrait_packages
    material_root = work / 'materials'
    packages_root = work / 'packages'
    summary = refresh_portrait_packages((scene,), index, bundle_root, material_root, packages_root,
                                        metadata_provider=_metadata_provider(request, _native_env(request)))
    if summary.cancelled:
        raise ValueError('portrait package preparation was cancelled')
    if summary.published != 1 or summary.failures:
        detail = '; '.join(f"{failure.get('skin_id')}: {failure.get('error')}" for failure in summary.failures)
        raise ValueError(f'portrait package preparation failed: {detail or "no package published"}')
    return _plan_package_job(scene, summary.manifest_paths[0], work), summary.metadata_incomplete


def plan_request(payload, temporary_dir):
    """Load config, exact resource closure and package, then prepare the PNG job.

    Returns ``(prepared_job, metadata_incomplete)``.
    """
    return _plan_request(validate_request(payload), Path(temporary_dir))


def _float32(value):
    return struct.unpack('f', struct.pack('f', value))[0]


def validate_png(path, job, completion):
    """Decode pixels and verify native bounds prove the requested unscaled fit."""
    from PIL import Image
    if completion is None:
        raise ValueError('native process emitted no matching complete record')
    bounds = completion.get('bounds')
    if not isinstance(bounds, list) or len(bounds) != 4:
        raise ValueError('native process emitted no valid bounds evidence')
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in bounds):
        raise ValueError('native bounds must be finite numbers')
    width = _float32(bounds[2] - bounds[0])
    height = _float32(bounds[3] - bounds[1])
    if width <= 0 or height <= 0:
        raise ValueError('native bounds are empty')
    margin = job.settings.margin
    if _float32(max(width, height) * 2) > job.settings.max_resolution - margin * 2:
        raise ValueError('native max_resolution would reduce requested 2x scale')
    expected = (max(1, math.ceil(_float32(width * 2)) + 2 * margin),
                max(1, math.ceil(_float32(height * 2)) + 2 * margin))
    if expected[0] * expected[1] > 64 * 1024 * 1024:
        raise ValueError('PNG exceeds supported canvas pixel limit')
    if (completion.get('width'), completion.get('height')) != expected:
        raise ValueError('native dimensions do not match 2x scene bounds')
    with Image.open(path) as image:
        if image.format != 'PNG' or image.size != expected:
            raise ValueError('PNG format/dimensions do not match native 2x evidence')
        image.verify()
    with Image.open(path) as image:
        image.load()
        rgba = image.convert('RGBA')
        if rgba.getchannel('A').getbbox() is None:
            raise ValueError('PNG contains no visible pixels')
    return {'width': expected[0], 'height': expected[1],
            'sha256': hashlib.sha256(Path(path).read_bytes()).hexdigest()}


def _native_library_entries(cli_dir, current):
    entries = [entry for entry in current.split(':') if entry]
    native_dir = cli_dir / 'runtimes' / 'linux-x64' / 'native'
    if native_dir.is_dir():
        entries.append(str(native_dir))
    entries.append(str(cli_dir))
    return list(dict.fromkeys(entries))


def _native_env(request):
    env = dict(os.environ)
    for key in ('DOTNET_ADDITIONAL_DEPS', 'DOTNET_SHARED_STORE',
                'DOTNET_STARTUP_HOOKS', 'LD_PRELOAD', 'LD_AUDIT'):
        env.pop(key, None)
    dotnet = Path(request['dotnet_path'])
    cli_dir = Path(request['scene_cli_path']).parent
    env.update(DOTNET_ROOT=str(dotnet.parent), DOTNET_MULTILEVEL_LOOKUP='0',
               DOTNET_PROCESSOR_COUNT='1', DOTNET_EnableDiagnostics='0',
               DOTNET_CLI_TELEMETRY_OPTOUT='1', DOTNET_SYSTEM_GLOBALIZATION_INVARIANT='1')
    if os.name == 'posix':
        env.update(LIBGL_ALWAYS_SOFTWARE='1', GALLIUM_DRIVER='llvmpipe', LP_NUM_THREADS='1')
        env['LD_LIBRARY_PATH'] = ':'.join(_native_library_entries(
            cli_dir, env.get('LD_LIBRARY_PATH', '')))
    return env


def _wrap_display(command, env, request):
    if os.name != 'posix' or env.get('DISPLAY'):
        return command, env
    xvfb_run = request.get('xvfb_run_path')
    if xvfb_run is None:
        import shutil
        xvfb_run = shutil.which('xvfb-run')
    if not xvfb_run:
        raise ValueError('no DISPLAY and no xvfb-run available for the native renderer')
    return [str(xvfb_run), '-a', '-s', '-screen 0 128x128x24', '--', *map(str, command)], env


def _run_native(command, env, work, request):
    from app.platform.portrait_render_process import run_render_process
    wrapped, env = _wrap_display(command, env, request)
    process = run_render_process(wrapped, work_dir=work / 'native', env=env, limits=request['limits'])
    return process


def run_request(payload):
    """Run one native render process; preserve any previous artifact on failure."""
    result = {'schema_version': 2, 'job_id': payload.get('job_id', '') if isinstance(payload, dict) else '',
              'status': 'error', 'diagnostics': []}
    started = time.monotonic()
    try:
        request = validate_request(payload)
        work = Path(tempfile.mkdtemp(prefix=request['job_id'] + '-', dir=request['work_root']))
        result['work_directory'] = str(work)
        job, metadata_incomplete = _plan_request(request, work)
        result['metadata_incomplete'] = bool(metadata_incomplete)

        command = build_scene_command(job, request, work)
        descriptors = tuple(work.glob('scene-*.json'))
        if len(descriptors) != 1:
            raise ValueError('expected one newly generated scene descriptor')
        descriptor_path = descriptors[0]
        data = json.loads(descriptor_path.read_text(encoding='utf-8'))
        staged = _inside(data['output'], work, 'native output')
        if staged.exists():
            raise ValueError('native output must not exist before this attempt')
        rows = request['png_working_rows']
        actual_rows = rows if data['scene_schema_version'] == 2 else 0
        env = _native_env(request)
        if actual_rows:
            data['settings']['png_working_rows'] = actual_rows
            descriptor_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        result.update(png_working_rows=actual_rows, resource_mode=(
            'disk_rows' if actual_rows else 'memory_default'))

        process = _run_native(command, env, work, request)
        result['resource'] = {
            'timed_out': process.timed_out,
            'elapsed_seconds': round(process.elapsed_seconds, 3),
            'peak_memory_bytes': process.peak_memory_bytes,
            'unit': process.unit,
            'limits': {
                'memory_max_bytes': request['limits'].memory_max_bytes,
                'memory_high_bytes': request['limits'].memory_high_bytes,
                'cpu_affinity': list(request['limits'].cpu_affinity),
                'timeout_seconds': request['limits'].timeout_seconds,
            },
        }
        if process.timed_out:
            raise ValueError(f'native render timed out after {request["limits"].timeout_seconds}s; see {work}')
        if process.returncode != 0:
            raise ValueError(f'native process failed with exit code {process.returncode}; see {work}')
        completion = None
        with process.stdout_path.open(encoding='utf-8', errors='replace') as stream:
            for line in stream:
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if isinstance(record, dict) and record.get('phase') == 'complete' and (
                        Path(record.get('output', '')).resolve() == staged):
                    completion = record
        metadata = validate_png(staged, job, completion)
        target = Path(request['output_root']) / f"{request['character_id']}_{request['skin_id']}.png"
        target = _inside(target, Path(request['output_root']), 'published output')
        publish_scene_result(staged, target)
        result.update(metadata, status='success', artifact_path=str(target))
    except Exception as exc:
        result['diagnostics'].append(f'{type(exc).__name__}: {exc}')
        if 'resource' not in result:
            result['resource'] = {'timed_out': False, 'elapsed_seconds': round(time.monotonic() - started, 3),
                                  'peak_memory_bytes': None, 'unit': None, 'limits': None}
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', required=True, type=Path)
    parser.add_argument('--result', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        payload = validate_request(json.loads(args.request.read_text(encoding='utf-8-sig')))
        result_path = _inside(args.result, Path(payload['work_root']), 'result path')
        if result_path == args.request.resolve():
            raise ValueError('result path aliases request path')
    except Exception as exc:
        print(f'{type(exc).__name__}: {exc}', file=sys.stderr)
        return 1
    result = run_request(payload)
    from app.platform.files import atomic_write_bytes
    atomic_write_bytes(result_path, (json.dumps(result, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))
    return 0 if result['status'] == 'success' else 1


if __name__ == '__main__':
    raise SystemExit(main())
