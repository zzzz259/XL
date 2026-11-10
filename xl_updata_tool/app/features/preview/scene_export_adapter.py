"""Describe per-part scene playback for the bundled Spine rendering extension."""
from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import shutil
import uuid

from app.platform.files import atomic_write_bytes
from app.platform.tool_locator import ToolLocator


def write_scene_descriptor(job, temporary_dir):
    directory = Path(temporary_dir)
    directory.mkdir(parents=True, exist_ok=True)
    purpose = dict(job.scene.config_metadata).get('runtime_purpose', '')
    entrance = purpose in {'entrance_only', 'entrance_stage'}
    payload = {
        'scene_schema_version': 2 if job.particle_sources or any(s.tracks for s in job.sources) else 1,
        'projection': 'unity_ui_orthographic_xy',
        'scene_id': job.scene.skin_id,
        'output': str((directory / ('render-' + uuid.uuid4().hex + job.output_path.suffix)).resolve()),
        'settings': asdict(job.settings),
        'snapshot_policy': 'entrance_complete' if entrance and job.export_mode == 'builtin' else 'time_offset',
        'parts': [{
            'id': source.part_id,
            'kind': 'spine', 'unity_order': list(source.unity_order),
            'skeleton': str(source.skeleton_path.resolve()),
            'atlas': str(source.atlas_path.resolve()),
            'animation': source.animation, 'loop': source.loop, 'skin': source.skin,
            'time_scale': source.time_scale, 'affine': list(source.affine),
            'color': list(source.color), 'fade_in': source.fade_in,
            'sequence': [{'animation': step.animation, 'loop': step.loop} for step in source.sequence],
            'tracks': [{'track': track.track, 'sequence': [
                {'animation': step.animation, 'loop': step.loop} for step in track.sequence
            ]} for track in source.tracks],
        } for source in job.sources],
    }
    payload['parts'].extend({
        'id': source.part_id, 'kind': 'particle',
        'texture': str(source.texture_path.resolve()),
        'affine': list(source.affine), 'color': list(source.color),
        'unity_order': list(source.unity_order), 'particle': source.config,
    } for source in job.particle_sources)
    payload['parts'].sort(key=lambda p: tuple(p['unity_order']), reverse=True)
    path = directory / f'scene-{uuid.uuid4().hex}.json'
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return path


def build_scene_command(job, spine_cli, temporary_dir):
    if temporary_dir is None:
        raise ValueError('scene export requires a per-job temporary directory')
    path = write_scene_descriptor(job, temporary_dir)
    helper = Path(spine_cli).parent / 'PortraitSceneCLI.dll'
    return [ToolLocator.create().dotnet(), str(helper), str(path)]


def publish_scene_result(staged, target):
    staged = Path(staged)
    if not staged.is_file() or staged.stat().st_size <= 0:
        raise ValueError(f'scene exporter produced no valid file: {staged}')
    def copy_to_temporary(path):
        shutil.copyfile(staged, path)
        return True
    atomic_write_bytes(target, b'', transform=copy_to_temporary)
