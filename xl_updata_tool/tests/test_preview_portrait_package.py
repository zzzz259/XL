import importlib.util
import json
import shutil
from dataclasses import replace
from pathlib import Path

import pytest
from PIL import Image

from app.features.preview.export_plan import ExportSettings
from app.features.preview.portrait_config.asset_index import AssetIndex, IndexedAsset
from app.features.preview.portrait_config.model import (
    AssetDependency, AssetRef, InteractionEvent, PortraitScene, RendererState,
    SceneDiagnostic, ScenePart, SpineState, SpriteState,
)


def api():
    name = 'app.features.preview.portrait_package'
    assert importlib.util.find_spec(name) is not None, 'portable portrait package API is missing'
    from app.features.preview import portrait_package
    return portrait_package


@pytest.fixture
def package_inputs(tmp_path):
    refs = [AssetRef('bundle', i) for i in range(1, 8)]
    material = tmp_path / 'material'
    folder = material / 'assets'
    folder.mkdir(parents=True)
    (folder / 'body.skel').write_bytes(b'binary-skeleton')
    (folder / 'body.atlas').write_text('body.png\nsize:2,2\nfilter:Linear,Linear\nbody\nbounds:0,0,2,2\n')
    Image.new('RGBA', (2, 2), (100, 50, 20, 128)).save(folder / 'body.png')
    assets = tuple(IndexedAsset(name, 'assets/source.prefab', 'bundle', i, (), 123,
                               tmp_path / 'source.bundle', True, '', kind)
                   for i, name, kind in ((1, 'body.skel', 'TextAsset'),
                                         (2, 'body.atlas', 'TextAsset'),
                                         (3, 'body', 'Texture2D')))
    spine = SpineState(refs[0], default_animation='idle', loop=True,
                       initial_skin='motion_stander', skeleton_json=refs[0],
                       atlases=(refs[1],), texture=refs[2])
    part = ScenePart('Spine:bundle:4', refs[3], 'body', renderer=RendererState('Spine', True), spine=spine)
    scene = PortraitScene('v1', '123', 'game-skin', 'role', 'game-skin', None, ('bundle',),
                          (part,), tuple(refs),
                          interactions=(InteractionEvent('tap', target=refs[3], animation='touch'),),
                          asset_dependencies=(AssetDependency(refs[1], 'texture', refs[2]),),
                          prefab_container='assets/cardspine/source.prefab')
    def provider(skel, atlas):
        return {'internal_skins': ['default', 'motion_stander'],
                'animations': {'idle': 2.5, 'touch': 1.2}, 'query_ok': True}
    return scene, AssetIndex(123, assets), material, tmp_path / 'output', provider


def publish(inputs):
    scene, index, material, output, provider = inputs
    summary = api().publish_portrait_packages((scene,), index, material, output, metadata_provider=provider)
    assert summary.published == 1, summary
    return api().find_portrait_package(output, scene)


def test_particle_package_relocates_with_exact_main_texture_and_config(package_inputs, tmp_path):
    from app.features.preview.portrait_config.model import ParticleState
    scene, index, material, output, provider = package_inputs
    ref = AssetRef('bundle', 8)
    Image.new('RGBA', (2, 2), 'orange').save(material / 'assets/fire.png')
    from app.features.preview.portrait_config.particle_metadata import normalize_system, normalize_material
    fixture = json.loads((Path(__file__).parent / 'fixtures/portrait_fire_particle_v1.json').read_text())
    objects = fixture['objects'].values()
    system = next(r['tree'] for r in objects if r['type'] == 'ParticleSystem')
    renderer = next(r['tree'] for r in objects if r['type'] == 'ParticleSystemRenderer')
    mat = next(r for r in fixture['materials'] if 'Additive' in r['shader_name'])
    config = {'version': 1, 'system': normalize_system(system, renderer, 1),
              'material': normalize_material(mat['material'], mat['shader_name'], mat['material']['m_Shader']['m_PathID'])}
    particle = ScenePart('fire', AssetRef('bundle', 9), 'fire',
        renderer=RendererState('ParticleSystemRenderer', True, sorting_order=6),
        particle=ParticleState(AssetRef('bundle', 10), AssetRef('bundle', 11), ref,
                               'additive_v1', json.dumps(config)))
    scene = replace(scene, parts=(*scene.parts, particle),
                    available_assets=(*scene.available_assets, ref, particle.object_ref,
                                      particle.particle.system, particle.particle.material))
    index = replace(index, assets=(*index.assets, IndexedAsset('fire', 'assets/source.prefab',
        'bundle', 8, (), 123, tmp_path / 'source.bundle', True, '', 'Texture2D')))
    path = publish((scene, index, material, output, provider))
    data = json.loads(path.read_text())
    assert data['package_schema_version'] == 2
    fire = next(p for p in data['parts'] if p['part_id'] == 'fire')
    assert fire['kind'] == 'particle' and fire['particle'] == config
    moved = tmp_path / 'moved'
    shutil.move(str(path.parent), moved)
    shutil.rmtree(material)
    jobs = api().build_packaged_portrait_jobs(scene, moved, tmp_path / 'renders')
    assert jobs[0].particle_sources[0].texture_path.is_relative_to(moved)
    assert jobs[0].particle_sources[0].config == config
    data['parts'][-1]['particle']['material']['profile'] = 'other'
    (moved / 'skin.json').write_text(json.dumps(data))
    with pytest.raises(ValueError, match='particle'):
        api().load_portrait_package(moved)


def test_publishes_portable_hierarchy_inputs_metadata_and_scene(package_inputs):
    path = publish(package_inputs)
    scene, _, _, output, _ = package_inputs
    assert path == output / 'portraits/123/characters/role/game-skin/skin.json'
    package = api().load_portrait_package(path)
    assert package.scene == scene
    data = json.loads(path.read_text())
    part = data['parts'][0]
    assert part['directory'].startswith('part_0000_')
    assert part['internal_skins'] == ['default', 'motion_stander']
    assert part['expression_selectors'] == ['motion_stander']
    assert part['animations'] == {'idle': 2.5, 'touch': 1.2}
    assert data['game_skin_id'] == 'game-skin'
    assert data['scene']['interactions'][0]['animation'] == 'touch'
    assert set(data['inputs']) == {'bundle:1', 'bundle:2', 'bundle:3'}
    for resource in data['resources']:
        assert not Path(resource['path']).is_absolute()
        assert len(resource['sha256']) == 64
        assert (path.parent / resource['path']).is_file()
    atlas = path.parent / part['atlas_paths'][0]
    assert 'body.png' in atlas.read_text()


def test_custom_action_only_targets_supporting_parts(package_inputs):
    scene, index, material, output, provider = package_inputs
    first = scene.parts[0]
    effect = replace(first, part_id='effect', object_ref=AssetRef('bundle', 99), spine=replace(first.spine, default_animation='touch'))
    configured = replace(scene, parts=(first, effect), available_assets=(*scene.available_assets, effect.object_ref))
    path = publish((configured, index, material, output, provider))
    data = json.loads(path.read_text())
    data['parts'][1]['animations'] = {'touch': 1.2}
    data['parts'][1]['internal_skins'] = ['motion_stander']
    path.write_text(json.dumps(data))
    jobs = api().build_packaged_portrait_jobs(configured, path, output / 'render', settings=ExportSettings(animation='idle'))
    sources = {s.part_id: s for s in jobs[0].sources}
    assert sources['effect'].animation == 'touch'
    assert sources['effect'].skin == 'motion_stander'
    data['scene']['parts'][1]['spine']['initial_skin'] = ''
    data['parts'][1]['internal_skins'] = ['default']
    path.write_text(json.dumps(data))
    face_job = api().build_packaged_portrait_jobs(configured, path, output / 'render', settings=ExportSettings(skins=('motion_stander',)))[0]
    assert next(s for s in face_job.sources if s.part_id == 'effect').skin == ''
    with pytest.raises(ValueError, match='unavailable'):
        api().build_packaged_portrait_jobs(configured, path, output / 'render', settings=ExportSettings(animation='typo'))
    with pytest.raises(ValueError, match='unavailable'):
        api().build_packaged_portrait_jobs(configured, path, output / 'render', settings=ExportSettings(skins=('typo',)))


def test_custom_package_skips_disabled_spine_metadata(package_inputs):
    scene, index, material, output, provider = package_inputs
    disabled = replace(scene.parts[0], part_id='disabled', object_ref=AssetRef('bundle', 99),
                       renderer=replace(scene.parts[0].renderer, enabled=False))
    scene = replace(scene, parts=(*scene.parts, disabled), available_assets=(*scene.available_assets, disabled.object_ref))
    path = publish((scene, index, material, output, provider))
    job = api().build_packaged_portrait_jobs(scene, path, output / 'render', settings=ExportSettings())[0]
    assert len(job.sources) == 1


def test_package_selectors_preserve_names_without_invalid_duration(package_inputs):
    path = publish(package_inputs)
    data = json.loads(path.read_text())
    data['parts'][0]['animations'] = {'idle': None, 'touch': 1.2}
    path.write_text(json.dumps(data))
    names, durations, skins = api().package_selectors(api().load_portrait_package(path))
    assert names == ('idle', 'touch')
    assert durations == {'touch': 1.2}
    assert 'motion_stander' in skins


def test_part_files_support_viewer_automatic_atlas_and_texture_discovery(package_inputs):
    path = publish(package_inputs)
    data = json.loads(path.read_text())
    part = data['parts'][0]
    skeleton = path.parent / part['skeleton_path']
    atlas = path.parent / part['atlas_paths'][0]
    texture = path.parent / part['texture_paths'][0]
    assert skeleton.with_suffix('.atlas') == atlas
    assert skeleton.parent == atlas.parent == texture.parent == path.parent / part['directory']
    assert texture.name in atlas.read_text().splitlines()
    assert '../png/' not in atlas.read_text()


def test_package_cannot_silently_omit_an_enabled_configured_part(package_inputs):
    path = publish(package_inputs)
    data = json.loads(path.read_text())
    data['parts'] = []
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='part'):
        api().load_portrait_package(path)


def test_failed_index_publication_rolls_back_previous_package(package_inputs, monkeypatch):
    path = publish(package_inputs)
    before = path.read_bytes()
    (package_inputs[2] / 'assets/body.skel').write_bytes(b'changed')
    original = api().os.replace
    def replace_index(source, destination):
        if Path(destination).name == 'index.json':
            raise OSError('index publication failed')
        return original(source, destination)
    monkeypatch.setattr(api().os, 'replace', replace_index)
    scene, index, material, output, provider = package_inputs
    summary = api().publish_portrait_packages((scene,), index, material, output, metadata_provider=provider)
    assert summary.failed == 1 and summary.published == 0
    assert path.read_bytes() == before
    api().load_portrait_package(path)


def test_long_part_name_keeps_full_metadata_and_a_short_identity_folder(package_inputs):
    scene, index, material, output, provider = package_inputs
    part = replace(scene.parts[0], game_object_name='background_' * 12)
    scene = replace(scene, parts=(part,))
    path = publish((scene, index, material, output, provider))
    package = api().load_portrait_package(path)
    assert len(package.manifest['parts'][0]['directory']) <= 23
    assert package.scene.parts[0].game_object_name == part.game_object_name


def test_relocated_package_builds_existing_jobs_without_source_material(package_inputs, tmp_path):
    path = publish(package_inputs)
    relocated = tmp_path / 'relocated'
    shutil.move(str(path.parent), relocated)
    shutil.rmtree(package_inputs[2])
    scene = package_inputs[0]
    jobs = api().build_packaged_portrait_jobs(scene, relocated, tmp_path / 'renders')
    assert [job.settings.format.casefold() for job in jobs] == ['png', 'mp4']
    assert jobs[0].sources[0].skin == 'motion_stander'
    assert jobs[0].sources[0].skeleton_path.is_relative_to(relocated)
    assert jobs[0].sources[0].atlas_path.is_file()


def test_service_catalog_reads_packages_without_material_or_cache(package_inputs, monkeypatch):
    path = publish(package_inputs)
    scene, _, material, output, _ = package_inputs
    shutil.rmtree(material)
    from app.features.preview.service import PreviewService
    # Listing metadata must not hash gigabytes of textures on the UI thread.
    monkeypatch.setattr(api(), '_sha', lambda _: pytest.fail('catalog hashed a resource'))
    catalog = PreviewService(material, output / 'character').load_published_preview_resources()
    assert catalog.portrait_scenes == (scene,)
    assert not catalog.portrait_diagnostics
    assert path.is_file()


def test_portable_catalog_keeps_configured_skin_and_separate_raw_archive(package_inputs):
    path = publish(package_inputs)
    output = package_inputs[3]
    from dataclasses import asdict
    from app.features.preview.resource_model import SpineSkinRecord
    from app.features.preview.service import PreviewService
    records = [SpineSkinRecord('role', f'assets/{family}.skel', f'assets/{family}.atlas', 'default',
                               family, 'raw', 'ready', resource_family=family)
               for family in ('cardspine', 'battlespine')]
    (output / 'preview_index.json').write_text(json.dumps({'spine': [{'records': [asdict(r) for r in records]}]}))
    catalog = PreviewService(package_inputs[2], output / 'character').load_published_preview_resources()
    assert len(catalog.portrait_scenes) == 1
    assert [r.resource_family for r in catalog.skins.values()] == ['cardspine', 'battlespine']
    assert path.is_file()


def test_package_catalog_quarantines_unsafe_index_entry(package_inputs):
    publish(package_inputs)
    output = package_inputs[3]
    (output / 'portraits/index.json').write_text(json.dumps({
        'package_schema_version': 1, 'packages': ['../outside/skin.json'],
    }))
    scenes, diagnostics = api().load_published_portrait_scenes(output)
    assert not scenes
    assert len(diagnostics) == 1


def test_controller_exports_published_skin_without_assetstudio_index(package_inputs, monkeypatch):
    import os
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication
    from app.features.preview.controller import PreviewController
    from app.features.preview.page import PreviewPage
    from app.features.preview.service import PreviewService
    app = QApplication.instance() or QApplication([])
    publish(package_inputs)
    scene, _, material, output, _ = package_inputs
    shutil.rmtree(material)
    monkeypatch.setattr(AssetIndex, 'from_catalog', lambda *_: pytest.fail('package export read AB map'))
    page = PreviewPage()
    controller = PreviewController(page, PreviewService(material, output / 'character'))
    calls = []
    def runner(job):
        calls.append(job)
        job.output_path.parent.mkdir(parents=True, exist_ok=True)
        job.output_path.write_bytes(b'rendered')
        return True
    try:
        assert controller._start_portrait_scene_export((scene,), settings=ExportSettings(format='Png'), runner=runner)
        assert controller._selected_export_worker.wait(5000)
        app.processEvents()
        assert len(calls) == 1
        assert calls[0].sources[0].skeleton_path.is_relative_to(output / 'portraits')
    finally:
        controller.close()
        page.close()


def test_postprocess_publishes_configured_scenes_as_portable_packages(package_inputs, monkeypatch):
    from app.features.preview import service as module
    from app.features.preview.material_catalog import GameMaterialCatalog, MaterialExportSummary
    from app.features.preview.output_publisher import RawSpinePublishSummary
    from app.features.preview.portrait_config.import_service import PortraitImportSummary
    from app.shared.contracts import ImportResult
    scene, index, material, output, provider = package_inputs
    monkeypatch.setattr(module, 'generate_portrait_manifests', lambda *_a, **_kw: PortraitImportSummary('complete'))
    monkeypatch.setattr(module, 'load_portrait_scene_catalog', lambda *_: ((scene,), ()))
    monkeypatch.setattr(AssetIndex, 'from_catalog', lambda *_, **_kwargs: index)
    monkeypatch.setattr(api(), 'query_portrait_metadata', provider)
    service = module.PreviewService(material, output / 'character')
    service.discover_preview_resources = lambda: object()
    service.publish_raw_spine_resources = lambda _: RawSpinePublishSummary()
    service.discover_game_materials = lambda: GameMaterialCatalog((), (), ())
    service.export_game_materials = lambda *_a, **_kw: MaterialExportSummary()
    result = service.preprocess_preview_resources(import_result=ImportResult(
        version_timestamp=123, completed_categories=frozenset({'character'}),
    ))
    assert result.packages.published == 1
    assert api().find_portrait_package(output, scene).is_file()


def test_failure_and_cancellation_preserve_last_good_package(package_inputs):
    path = publish(package_inputs)
    before = path.read_bytes()
    scene, index, material, output, provider = package_inputs
    (material / 'assets/body.png').unlink()
    failed = api().publish_portrait_packages((scene,), index, material, output, metadata_provider=provider)
    assert failed.failed == 1
    assert path.read_bytes() == before
    cancelled = api().publish_portrait_packages((scene,), index, material, output,
                                               metadata_provider=provider, cancel_check=lambda: True)
    assert cancelled.cancelled
    assert path.read_bytes() == before


@pytest.mark.parametrize('unsafe', ['../escape.png', '/escape.png', 'C:/escape.png', r'..\escape.png'])
def test_loader_rejects_unsafe_resource_paths(package_inputs, unsafe):
    path = publish(package_inputs)
    data = json.loads(path.read_text())
    data['resources'][0]['path'] = unsafe
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='unsafe|escape'):
        api().load_portrait_package(path)


def test_render_diagnostic_keeps_source_package(package_inputs):
    scene, index, material, output, provider = package_inputs
    scene = replace(scene, diagnostics=(SceneDiagnostic('unsupported_spine_transform', 'clock constraint'),))
    summary = api().publish_portrait_packages((scene,), index, material, output, metadata_provider=provider)
    assert summary.published == 1
    package = api().load_portrait_package(api().find_portrait_package(output, scene))
    assert package.scene.diagnostics[0].code == 'unsupported_spine_transform'


def test_static_sprite_has_generated_project_and_straight_rgba_source(package_inputs):
    scene, index, material, output, provider = package_inputs
    sprite = ScenePart('Sprite:bundle:7', AssetRef('bundle', 7), 'background',
                       renderer=RendererState('Sprite', True, sorting_order=-1),
                       sprite=SpriteState(AssetRef('bundle', 6), (0, 0, 2, 2), (0.5, 0.5), AssetRef('bundle', 3)))
    scene = replace(scene, parts=(*scene.parts, sprite))
    path = publish((scene, index, material, output, provider))
    data = json.loads(path.read_text())
    part = data['parts'][1]
    assert part['kind'] == 'static_sprite'
    assert (path.parent / part['skeleton_path']).is_file()
    source = path.parent / part['original_texture_path']
    with Image.open(source) as image:
        assert image.getpixel((0, 0)) == (100, 50, 20, 128)
    jobs = api().build_packaged_portrait_jobs(scene, path, output / 'renders', settings=ExportSettings(skins=('default',), pma=True))
    assert jobs[0].sprite_sources[0].texture_path == source.resolve()
    from app.features.preview.portrait_scene_export import prepare_portrait_scene_export_job
    prepared = prepare_portrait_scene_export_job(jobs[0], output / 'scratch')
    generated = next(item for item in prepared.sources if item.source_kind == 'static_sprite')
    with Image.open(generated.atlas_path.parent / next(line for line in generated.atlas_path.read_text().splitlines() if line.endswith('.png'))) as image:
        assert image.getpixel((0, 0)) == (50, 25, 10, 128)


def test_static_project_is_available_when_spine_parts_use_different_frames(package_inputs):
    from app.features.preview.portrait_config.model import TransformState
    scene, index, material, output, provider = package_inputs
    shifted = replace(scene.parts[0], part_id='shifted', object_ref=AssetRef('bundle', 5),
                      transform_chain=(TransformState(AssetRef('bundle', 5), (1, 2, 0), (0, 0, 0, 1), (1, 1, 1), 0),))
    sprite = ScenePart('Sprite:bundle:7', AssetRef('bundle', 7), 'background',
                       renderer=RendererState('Sprite', True, sorting_order=-1),
                       sprite=SpriteState(AssetRef('bundle', 6), (0, 0, 2, 2), (0.5, 0.5), AssetRef('bundle', 3)))
    scene = replace(scene, parts=(*scene.parts, shifted, sprite))
    path = publish((scene, index, material, output, provider))
    data = json.loads(path.read_text())
    part = next(p for p in data['parts'] if p['kind'] == 'static_sprite')
    assert part['generation']['coordinate_space'] == 'part_local'
    assert (path.parent / part['skeleton_path']).is_file()
    assert api().load_portrait_package(path).scene == scene


def test_sprite_only_event_builds_a_native_export_job(package_inputs):
    from app.features.preview.portrait_scene_export import prepare_portrait_scene_export_job
    from app.features.preview.spine_adapter import build_spine_export_command
    scene, index, material, output, provider = package_inputs
    sprite = ScenePart('Sprite:bundle:7', AssetRef('bundle', 7), 'background',
                       renderer=RendererState('Sprite', True),
                       sprite=SpriteState(AssetRef('bundle', 6), (0, 0, 2, 2), (0.5, 0.5), AssetRef('bundle', 3)))
    scene = replace(scene, character_id='', skin_id='eventcovers_0028',
                    prefab_container='assets/eventcovers/0028.prefab', parts=(sprite,), interactions=())
    path = publish((scene, index, material, output, provider))
    jobs = api().build_packaged_portrait_jobs(scene, path, output / 'renders')
    prepared = prepare_portrait_scene_export_job(jobs[0], output / 'scratch')
    command = build_spine_export_command(prepared, 'SpineViewerCLI.exe', output / 'descriptor')
    assert len(prepared.sources) == 1
    assert prepared.sources[0].source_kind == 'static_sprite'
    assert Path(command[1]).name == 'PortraitSceneCLI.dll'
    assert len(json.loads(Path(command[-1]).read_text())['parts']) == 1


def test_shared_skeleton_retains_each_part_folder_animation_and_identity(package_inputs):
    scene, index, material, output, provider = package_inputs
    first = replace(scene.parts[0], spine=replace(scene.parts[0].spine, initial_skin='default'))
    second = replace(first, part_id='second', object_ref=AssetRef('bundle', 5),
                     game_object_name='background', stable_order=1,
                     spine=replace(first.spine, default_animation='touch'))
    scene = replace(scene, parts=(first, second))
    path = publish((scene, index, material, output, provider))
    data = json.loads(path.read_text())
    assert len(data['inputs']['bundle:1']['paths']) == 2
    jobs = api().build_packaged_portrait_jobs(scene, path, output / 'render')
    sources = {source.part_id: source for source in jobs[0].sources}
    assert sources[first.part_id].skeleton_ref == sources['second'].skeleton_ref == AssetRef('bundle', 1)
    assert sources[first.part_id].skeleton_path != sources['second'].skeleton_path
    assert sources[first.part_id].animation == 'idle'
    assert sources['second'].animation == 'touch'


def test_different_exact_textures_with_same_filename_are_bound_per_part(package_inputs):
    scene, index, material, output, provider = package_inputs
    (material / 'other').mkdir()
    for name in ('body.skel', 'body.atlas', 'body.png'):
        shutil.copy(material / 'assets' / name, material / 'other' / name)
    assets = tuple(replace(asset, path_id=asset.path_id + 10, container='other/source.prefab') for asset in index.assets)
    index = replace(index, assets=(*index.assets, *assets))
    first = replace(scene.parts[0], spine=replace(scene.parts[0].spine, initial_skin='default'))
    second = replace(first, part_id='second', object_ref=AssetRef('bundle', 5), stable_order=1,
                     spine=replace(first.spine, skeleton=AssetRef('bundle', 11), skeleton_json=AssetRef('bundle', 11),
                                   atlases=(AssetRef('bundle', 12),), texture=AssetRef('bundle', 13)))
    scene = replace(scene, parts=(first, second),
                    available_assets=(*scene.available_assets, *(AssetRef('bundle', i) for i in (11, 12, 13))))
    path = publish((scene, index, material, output, provider))
    data = json.loads(path.read_text())
    assert set(data['inputs']) == {'bundle:1', 'bundle:2', 'bundle:3', 'bundle:11', 'bundle:12', 'bundle:13'}


def test_loader_rejects_fingerprint_change_and_part_path_escape(package_inputs):
    path = publish(package_inputs)
    data = json.loads(path.read_text())
    first = path.parent / data['resources'][0]['path']
    original = first.read_bytes()
    first.write_bytes(b'corrupted')
    with pytest.raises(ValueError, match='SHA256'):
        api().load_portrait_package(path)
    first.write_bytes(original)
    data['parts'][0]['skeleton_path'] = '../outside.skel'
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='unsafe'):
        api().load_portrait_package(path)


def test_malformed_manifest_has_an_actionable_validation_error(package_inputs):
    path = publish(package_inputs)
    data = json.loads(path.read_text())
    del data['resources']
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='invalid portrait package'):
        api().load_portrait_package(path)


def test_cancellation_during_staging_preserves_package_and_index(package_inputs):
    path = publish(package_inputs)
    scene, index, material, output, provider = package_inputs
    before = path.read_bytes()
    index_path = output / 'portraits/index.json'
    before_index = index_path.read_bytes()
    checks = []
    def cancel():
        checks.append(True)
        return len(checks) >= 6
    summary = api().publish_portrait_packages((scene,), index, material, output,
                                             metadata_provider=provider, cancel_check=cancel)
    assert summary.cancelled
    assert path.read_bytes() == before
    assert index_path.read_bytes() == before_index
    assert not tuple(output.glob('.portrait-stage-*'))


def test_event_stage_uses_event_hierarchy_and_preserves_config_metadata(package_inputs):
    scene, index, material, output, provider = package_inputs
    scene = replace(scene, skin_id='eventcovers_0005_stage_03', entry_config_id='eventcovers_0005_stage_03',
                    character_id='', prefab_container='assets/eventcovers/prefab/eventcovers_0005_3.prefab',
                    config_metadata=(('eventcover_id', '0005'), ('stage_index', '3')),
                    display_position=(0.5, 0.7))
    path = publish((scene, index, material, output, provider))
    assert path == output / 'portraits/123/events/eventcovers_0005/stage_03/skin.json'
    assert api().load_portrait_package(path).scene == scene


def test_failed_directory_swap_rolls_back_last_good_package(package_inputs, monkeypatch):
    path = publish(package_inputs)
    scene, index, material, output, provider = package_inputs
    before = path.read_bytes()
    module = api()
    original = module.os.replace
    def fail_staging(source, destination):
        if Path(source).name.startswith('.portrait-stage-'):
            raise OSError('simulated publication interruption')
        return original(source, destination)
    monkeypatch.setattr(module.os, 'replace', fail_staging)
    summary = module.publish_portrait_packages((scene,), index, material, output, metadata_provider=provider)
    assert summary.failed == 1
    assert path.read_bytes() == before
    assert module.load_portrait_package(path).scene == scene


def test_metadata_query_failure_keeps_configuration_without_invented_names(package_inputs):
    scene, index, material, output, _ = package_inputs
    def broken_provider(skeleton, atlas):
        raise RuntimeError('native metadata query unavailable')
    summary = api().publish_portrait_packages((scene,), index, material, output, metadata_provider=broken_provider)
    assert summary.published == 1
    assert summary.metadata_incomplete == 1
    path = api().find_portrait_package(output, scene)
    data = json.loads(path.read_text())
    assert data['parts'][0]['metadata']['query_ok'] is False
    assert data['parts'][0]['internal_skins'] == []
    assert data['parts'][0]['animations'] == {}
    assert data['scene']['parts'][0]['spine']['default_animation'] == 'idle'


@pytest.mark.parametrize('boundary', ['input', 'part'])
def test_loader_rejects_cross_bound_exact_resource_identity(package_inputs, boundary):
    path = publish(package_inputs)
    data = json.loads(path.read_text())
    atlas_path = data['parts'][0]['atlas_paths'][0]
    if boundary == 'input':
        data['inputs']['bundle:1']['path'] = atlas_path
        data['inputs']['bundle:1']['paths'] = [atlas_path]
    else:
        data['parts'][0]['skeleton_path'] = atlas_path
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='identity'):
        api().load_portrait_package(path)
