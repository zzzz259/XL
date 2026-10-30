"""One portable card portrait -> verified full 2x PNG, without importing Qt.

Roots are trusted local executor configuration, never chat-provided paths. Each
attempt retains its own work directory for diagnosis; this entry deletes none.
CPU affinity, process-tree cancellation and memory budgets belong to executor.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import json
import math
from pathlib import Path
import re
import struct
import subprocess
import sys
import tempfile

from .scene_export_adapter import build_scene_command, publish_scene_result

PROFILE = {'format': 'png', 'scale': 2, 'scope': 'full'}


def _inside(path, root, label):
    resolved = Path(path).resolve()
    if resolved == root or root not in resolved.parents:
        raise ValueError(f'{label} escapes declared root')
    return resolved


def validate_request(payload):
    """Validate fixed profile and explicit absolute, nonoverlapping local roots."""
    if not isinstance(payload, dict):
        raise ValueError('request must be an object')
    if type(payload.get('schema_version')) is not int or payload['schema_version'] != 1:
        raise ValueError('unsupported schema_version')
    if payload.get('profile') != PROFILE or type(payload['profile'].get('scale')) is not int:
        raise ValueError('only full 2x PNG profile is supported')
    for key in ('job_id', 'version', 'character_id', 'skin_id'):
        value = payload.get(key)
        if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', value):
            raise ValueError(f'invalid {key} identity')
    roots = {}
    for key in ('input_root', 'output_root', 'work_root'):
        value = payload.get(key)
        if not isinstance(value, str) or not Path(value).is_absolute():
            raise ValueError(f'{key} must be an absolute local path')
        roots[key] = Path(value).resolve()
        if not roots[key].is_dir():
            raise ValueError(f'{key} must be an existing directory')
    for key, root in roots.items():
        for other, other_root in roots.items():
            if key != other and (root == other_root or root in other_root.parents):
                raise ValueError('input/output/work roots must not overlap')
    value = payload.get('package_path')
    if not isinstance(value, str) or not Path(value).is_absolute():
        raise ValueError('package_path must be an absolute local path')
    package = _inside(value, roots['input_root'], 'package_path')
    result = dict(payload)
    result.update({key: str(value) for key, value in roots.items()})
    result['package_path'] = str(package)
    return result


def plan_request(payload, temporary_dir):
    """Load a verified movable engineering package and retain builtin semantics."""
    from .portrait_package import load_portrait_package, build_packaged_portrait_jobs
    from .portrait_scene_export import scene_resource_family, prepare_portrait_scene_export_job
    from .scene_capabilities import portrait_scene_capabilities

    request = validate_request(payload)
    work = _inside(temporary_dir, Path(request['work_root']), 'temporary_dir')
    package = load_portrait_package(request['package_path'])
    scene = package.scene
    if (scene.version_timestamp, scene.character_id, scene.skin_id) != (
            request['version'], request['character_id'], request['skin_id']):
        raise ValueError('package identity does not match request')
    if scene_resource_family(scene) != 'cardspine':
        raise ValueError('only cardspine character scenes are supported')
    caps = portrait_scene_capabilities(scene)
    if not caps.can_export:
        raise ValueError(f'full scene cannot be exported: {caps.state}: {caps.reason}')
    jobs = build_packaged_portrait_jobs(scene, package.manifest_path, work,
        settings=None, options={'include_effects': True, 'include_interactions': False, 'allow_basic': False})
    png = tuple(job for job in jobs if job.settings.format.casefold() == 'png'
        and job.render_scope == 'full' and not job.interaction_id and job.export_mode == 'builtin')
    if len(png) != 1:
        raise ValueError('expected exactly one builtin full PNG job')
    job = replace(png[0], settings=replace(png[0].settings, scale=2))
    return prepare_portrait_scene_export_job(job, work)


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


def run_request(payload):
    """Run one native process; preserve any previous artifact on every failure."""
    result = {'schema_version': 1, 'job_id': payload.get('job_id', '') if isinstance(payload, dict) else '',
              'status': 'error', 'diagnostics': []}
    try:
        request = validate_request(payload)
        work = Path(tempfile.mkdtemp(prefix=request['job_id'] + '-', dir=request['work_root']))
        result['work_directory'] = str(work)
        job = plan_request(request, work)
        from app.platform.tool_locator import ToolLocator
        locator = ToolLocator.create()
        command = build_scene_command(job, locator.spineviewer_cli(), work)
        # Read the descriptor we just created, never a previous staged/final file.
        descriptors = tuple(work.glob('scene-*.json'))
        if len(descriptors) != 1:
            raise ValueError('expected one newly generated scene descriptor')
        descriptor = json.loads(descriptors[0].read_text(encoding='utf-8'))
        staged = _inside(descriptor['output'], work, 'native output')
        if staged.exists():
            raise ValueError('native output must not exist before this attempt')
        with (work / 'stdout.jsonl').open('w', encoding='utf-8') as stdout, (
                work / 'stderr.log').open('w', encoding='utf-8') as stderr:
            process = subprocess.run(command, cwd=work, env=locator.subprocess_env(),
                stdout=stdout, stderr=stderr, check=False)
        if process.returncode != 0:
            raise ValueError(f'native process failed with exit code {process.returncode}; see {work}')
        completion = None
        with (work / 'stdout.jsonl').open(encoding='utf-8', errors='replace') as stream:
            for line in stream:
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if isinstance(record, dict) and record.get('phase') == 'complete' and (
                        Path(record.get('output', '')).resolve() == staged):
                    completion = record
        metadata = validate_png(staged, job, completion)
        target = _inside(Path(request['output_root']) / (request['job_id'] + '.png'),
                         Path(request['output_root']), 'published output')
        publish_scene_result(staged, target)
        result.update(metadata, status='success', staged_path=str(target))
    except Exception as exc:
        result['diagnostics'].append(f'{type(exc).__name__}: {exc}')
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', required=True, type=Path)
    parser.add_argument('--result', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        payload = validate_request(json.loads(args.request.read_text(encoding='utf-8-sig')))
        # Separate metadata from every input and published artifact before rendering.
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
