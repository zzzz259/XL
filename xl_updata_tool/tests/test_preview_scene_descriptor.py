import json
from pathlib import Path

from app.features.preview.export_plan import ExportSettings
from app.features.preview.portrait_config.model import AnimationStep, PortraitScene
from app.features.preview.portrait_scene_export import PortraitSceneExportJob, SpineSource


def test_particle_and_overlay_track_descriptor_use_one_ordered_scene(tmp_path):
    from app.features.preview.portrait_scene_export import ParticleSource, AnimationTrack
    from app.features.preview.scene_export_adapter import write_scene_descriptor
    scene = PortraitScene('v', '123', 'skin', 'role', 'skin', None, (), ())
    body = SpineSource('body', Path('body.skel'), Path('body.atlas'), 'idle', True, '', 1,
                       (0, 2, 0, 0), tracks=(AnimationTrack(1, (AnimationStep('touch', False),)),))
    fire = ParticleSource('fire', Path('fire.png'), 'additive_v1', {'version': 1},
                          (1, 0, 0, 1, 0, 0), (0, 6, 0, 0))
    job = PortraitSceneExportJob(scene, (body,), tmp_path / 'out.png', ExportSettings(), particle_sources=(fire,))
    data = json.loads(write_scene_descriptor(job, tmp_path).read_text())
    assert data['scene_schema_version'] == 2
    assert [p['id'] for p in data['parts']] == ['fire', 'body']
    assert data['parts'][0]['particle'] == {'version': 1}
    assert data['parts'][1]['tracks'] == [{'track': 1, 'sequence': [{'animation': 'touch', 'loop': False}]}]


def test_descriptor_keeps_per_part_clocks_affines_skins_and_queue(tmp_path):
    from app.features.preview.scene_export_adapter import write_scene_descriptor
    scene = PortraitScene('v', '123', 'event', '', 'eventcovers_0035', None, (), (),
                          config_metadata=(('runtime_purpose', 'entrance_only'),))
    source = SpineSource('p', Path('p.skel'), Path('p.atlas'), 'in', False, 'dress', 0.35,
                         (0, 0, 0, 0), affine=(-1, 0, 0, 1, 12, 3),
                         sequence=(AnimationStep('in', False), AnimationStep('idle', True)), fade_in=0.8)
    job = PortraitSceneExportJob(scene, (source,), tmp_path / 'out.png', ExportSettings())
    path = write_scene_descriptor(job, tmp_path)
    value = json.loads(path.read_text())
    assert value['snapshot_policy'] == 'entrance_complete'
    assert value['parts'][0]['affine'] == [-1, 0, 0, 1, 12, 3]
    assert value['parts'][0]['time_scale'] == 0.35
    assert value['parts'][0]['skin'] == 'dress'
    assert value['parts'][0]['sequence'] == [{'animation': 'in', 'loop': False}, {'animation': 'idle', 'loop': True}]
    assert value['parts'][0]['fade_in'] == 0.8


def test_custom_snapshot_uses_explicit_offset(tmp_path):
    from app.features.preview.scene_export_adapter import write_scene_descriptor
    scene = PortraitScene('v', '123', 'event', '', 'eventcovers_0035', None, (), (),
                          config_metadata=(('runtime_purpose', 'entrance_only'),))
    source = SpineSource('p', Path('p.skel'), Path('p.atlas'), 'idle', True, '', 1, (0, 0, 0, 0))
    job = PortraitSceneExportJob(scene, (source,), tmp_path / 'out.png', ExportSettings(time_offset=2), 'custom')
    value = json.loads(write_scene_descriptor(job, tmp_path).read_text())
    assert value['snapshot_policy'] == 'time_offset'
    assert value['settings']['time_offset'] == 2


def test_scene_render_stays_in_job_temp_until_successful_publication(tmp_path):
    from app.features.preview.scene_export_adapter import write_scene_descriptor
    scene = PortraitScene('v', '123', 'skin', 'role', 'skin', None, (), ())
    source = SpineSource('p', Path('p.skel'), Path('p.atlas'), 'idle', True, '', 1, (0, 0, 0, 0))
    job = PortraitSceneExportJob(scene, (source,), tmp_path / 'published' / 'out.mp4', ExportSettings(format='Mp4'))
    scratch = tmp_path / 'scratch'
    value = json.loads(write_scene_descriptor(job, scratch).read_text())
    assert Path(value['output']).parent == scratch
    assert Path(value['output']) != job.output_path
