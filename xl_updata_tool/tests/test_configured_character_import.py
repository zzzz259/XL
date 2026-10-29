from pathlib import Path
from types import SimpleNamespace
import importlib
import importlib.util

import pytest
from PIL import Image

from app.features.importer.processing import ImportProcessor


def _module():
    name = 'app.features.preview.portrait_import'
    assert importlib.util.find_spec(name), 'configured object exporter is missing'
    return importlib.import_module(name)


def _setup(tmp_path, monkeypatch):
    module = _module()
    from app.features.preview.portrait_config.asset_index import AssetIndex, IndexedAsset
    from app.features.preview.portrait_config.model import AssetRef
    bundle = tmp_path / 'bundles' / '123'
    bundle.mkdir(parents=True)
    source = bundle / 'a.bundle'
    source.write_bytes(b'bundle')
    catalog = bundle / '_map' / 'assets_map.json'
    catalog.parent.mkdir()
    catalog.write_text('[]')
    lua = tmp_path / 'lua'
    (lua / '123').mkdir(parents=True)
    for name in ('BaseFashion.lua', 'BaseActivityStageConfig.lua'):
        (lua / '123' / name).write_text('return {}')
    assets = tuple(IndexedAsset(name, container, 'a', number, (), 123, source, True, '', kind)
                   for number, name, container, kind in (
                       (1, 'hero.skel', 'assets/unusual/hero.prefab', 'TextAsset'),
                       (2, 'hero', 'assets/unusual/hero.prefab', 'Texture2D'),
                       (3, 'unselected', 'assets/art/models/cardspine/other.prefab', 'Texture2D')))
    index = AssetIndex(123, assets)
    reads = []
    def reader(number, kind, payload):
        def read():
            reads.append(number)
            return payload
        return SimpleNamespace(path_id=number, type=SimpleNamespace(name=kind), read=read)
    env = SimpleNamespace(objects=[
        reader(1, 'TextAsset', SimpleNamespace(m_Script=b'\x00\xffspine')),
        reader(2, 'Texture2D', SimpleNamespace(image=Image.new('RGBA', (2, 3), '#ff0000'))),
        reader(3, 'Texture2D', None),
    ])
    monkeypatch.setattr(module, '_load_bundle', lambda *_: env)
    monkeypatch.setattr(module.AssetIndex, 'from_catalog', lambda *_, **_kwargs: index)
    root = SimpleNamespace(skin_id='skin')
    scene = SimpleNamespace(skin_id='skin', parts=(1,), diagnostics=(), to_dict=lambda: {'skin_id': 'skin'})
    monkeypatch.setattr(module, 'load_portrait_roots', lambda *_: [root])
    monkeypatch.setattr(module, 'load_eventcover_roots', lambda *_: [])
    monkeypatch.setattr(module, 'load_battle_roots', lambda *_: [])
    monkeypatch.setattr(module, 'build_portrait_scenes', lambda *_args, **_kwargs: [scene])
    monkeypatch.setattr(module, 'required_scene_assets', lambda *_: (AssetRef('a', 1), AssetRef('a', 2)))
    kwargs = dict(version_timestamp=123, catalog_path=catalog, lua_root=lua,
                  bundle_root=bundle, material_root=tmp_path / 'material')
    return module, kwargs, reads


def test_exact_export_reads_only_required_objects_and_preserves_binary(tmp_path, monkeypatch, caplog):
    module, kwargs, reads = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(module.logger, 'propagate', True)
    caplog.set_level(20, logger=module.logger.name)
    result = module.export_configured_character_assets(**kwargs)
    assert reads == [1, 2]
    assert (kwargs['material_root'] / 'assets/unusual/hero.skel').read_bytes() == b'\x00\xffspine'
    with Image.open(kwargs['material_root'] / 'assets/unusual/hero.png') as image:
        assert image.size == (2, 3)
    assert result.asset_count == 2 and result.scene_count == 1
    assert set(result.files) == {'assets/unusual/hero.skel', 'assets/unusual/hero.png'}
    assert Path(result.blueprint_path).is_file()
    assert any(getattr(record, 'event', '') == 'portrait_import.asset.extracted' for record in caplog.records)


def test_cancel_during_extraction_preserves_existing_material(tmp_path, monkeypatch):
    module, kwargs, reads = _setup(tmp_path, monkeypatch)
    old = kwargs['material_root'] / 'assets/unusual/hero.skel'
    old.parent.mkdir(parents=True)
    old.write_bytes(b'old-good')
    with pytest.raises(InterruptedError):
        module.export_configured_character_assets(**kwargs, cancel_check=lambda: bool(reads))
    assert old.read_bytes() == b'old-good'
    assert not old.with_name('hero.png').exists()


def test_failed_texture_decode_preserves_existing_material(tmp_path, monkeypatch):
    module, kwargs, _ = _setup(tmp_path, monkeypatch)
    old = kwargs['material_root'] / 'assets/unusual/hero.skel'
    old.parent.mkdir(parents=True)
    old.write_bytes(b'old-good')
    class Broken:
        @property
        def image(self):
            raise ValueError('bad texture')
    env = module._load_bundle(None)
    env.objects[1].read = lambda: Broken()
    with pytest.raises(ValueError, match='bad texture'):
        module.export_configured_character_assets(**kwargs)
    assert old.read_bytes() == b'old-good'


def test_missing_same_version_lua_never_falls_back_to_all_assets(tmp_path, monkeypatch):
    module, kwargs, reads = _setup(tmp_path, monkeypatch)
    (kwargs['lua_root'] / '123' / 'BaseFashion.lua').unlink()
    with pytest.raises(ValueError, match='BaseFashion.lua'):
        module.export_configured_character_assets(**kwargs)
    assert reads == []


def test_hooked_processor_prepares_lua_and_map_before_single_callback(tmp_path, monkeypatch):
    events = []
    cli = tmp_path / 'as.exe'
    cli.write_bytes(b'as')
    bundle = tmp_path / 'bundles' / '123'
    bundle.mkdir(parents=True)
    lua = tmp_path / 'lua'
    material = tmp_path / 'material'
    def export(**kwargs):
        assert (Path(kwargs['lua_root']) / '123' / 'BaseFashion.lua').is_file()
        assert Path(kwargs['catalog_path']).is_file()
        events.append('callback')
        path = Path(kwargs['material_root']) / 'assets/unusual/hero.skel'
        path.parent.mkdir(parents=True)
        path.write_bytes(b'selected')
        return SimpleNamespace(files=('assets/unusual/hero.skel',), asset_count=1, scene_count=1)
    assert 'character_asset_exporter' in __import__('inspect').signature(ImportProcessor).parameters
    processor = ImportProcessor([], str(bundle), str(material), str(cli), export_categories={'character', 'lua'},
                                version_timestamp=123, lua_output_dir=str(lua), character_asset_exporter=export,
                                category_finished_callback=lambda name: events.append(name))
    original = ImportProcessor._stage_export
    def lua_export(self, assets):
        if self is processor:
            return original(self, assets)
        events.append('lua')
        (lua / '123').mkdir(parents=True)
        for name in ('BaseFashion.lua', 'BaseActivityStageConfig.lua'):
            (lua / '123' / name).write_text('return {}')
        self.lua_export_result = {'directory': str(lua / '123')}
        self._categories_with_output = {'lua'}
        return 2, ''
    monkeypatch.setattr(ImportProcessor, '_stage_export', lua_export)
    def make_map():
        events.append('map')
        catalog = bundle / '_map' / 'assets_map.json'
        catalog.parent.mkdir()
        catalog.write_text('[]')
        return [], ''
    monkeypatch.setattr(processor, '_stage_map', make_map)
    commands = []
    def run(command, **_kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr('app.features.importer.processing.run_external_process', run)
    monkeypatch.setattr('app.features.importer.processing.update_process_manifest', lambda *_args, **_kwargs: None)
    count, error = processor._stage_export(None)
    assert not error and count == 1
    assert [event for event in events if event in {'lua', 'map', 'callback'}] == ['lua', 'map', 'callback']
    assert events.count('callback') == 1
    assert events.count('导出 character') == 1
    assert events.index('导出 lua') < events.index('callback')
    assert len(commands) == 2
    assert all('bursthead' in str(command) or 'lotterybg' in str(command) for command in commands)
    assert (material / 'assets/unusual/hero.skel').read_bytes() == b'selected'
    assert processor._categories_with_output == {'lua', 'character'}


def test_textasset_surrogateescape_roundtrip_is_lossless(tmp_path, monkeypatch):
    module, kwargs, _ = _setup(tmp_path, monkeypatch)
    env = module._load_bundle(None)
    raw = b'\x00\xff\xfe\x80spine\r\n'
    env.objects[0].read = lambda: SimpleNamespace(m_Script=raw.decode('utf-8', errors='surrogateescape'))
    module.export_configured_character_assets(**kwargs)
    assert (kwargs['material_root'] / 'assets/unusual/hero.skel').read_bytes() == raw


def test_same_name_in_distinct_bundles_keeps_exact_object_paths(tmp_path, monkeypatch):
    from dataclasses import replace
    from app.features.preview.portrait_config.model import AssetRef
    from app.features.preview.portrait_scene_export import _resolve_asset_path
    module, kwargs, _ = _setup(tmp_path, monkeypatch)
    index = module.AssetIndex.from_catalog(None)
    other_bundle = kwargs['bundle_root'] / 'b.bundle'
    other_bundle.write_bytes(b'other')
    duplicate = replace(index.assets[0], bundle_hash='b', bundle_path=other_bundle)
    index = replace(index, assets=(*index.assets, duplicate))
    monkeypatch.setattr(module.AssetIndex, 'from_catalog', lambda *_, **_kwargs: index)
    monkeypatch.setattr(module, 'required_scene_assets', lambda *_: (AssetRef('a', 1), AssetRef('b', 1)))
    module.export_configured_character_assets(**kwargs)
    first = _resolve_asset_path(index, kwargs['material_root'], AssetRef('a', 1))
    second = _resolve_asset_path(index, kwargs['material_root'], AssetRef('b', 1))
    assert first != second
    assert first.read_bytes() == second.read_bytes() == b'\x00\xffspine'


def test_battle_roots_are_exact_spd_entries_and_independent_from_portraits(tmp_path):
    module = _module()
    path = tmp_path / '123' / 'BaseFashion.lua'
    path.parent.mkdir()
    path.write_text('''return {
      [1] = {card_id=10, show_spine="portrait.prefab", spd="battlespine_10.prefab"},
      -- [9] = {spd="wrong.prefab"},
      [2] = {card_id=10, spd="battlespine_10.prefab"},
      [3] = {card_id=11, spd="battlespine_11.prefab"},
    }''')
    roots = module.load_battle_roots(tmp_path, 123)
    assert [root.prefab_name for root in roots] == ['battlespine_10.prefab', 'battlespine_11.prefab']
    assert all(dict(root.metadata)['display_family'] == 'battle' for root in roots)


def test_selected_files_rollback_if_second_atomic_replace_fails(tmp_path, monkeypatch):
    module, kwargs, _ = _setup(tmp_path, monkeypatch)
    material = kwargs['material_root']
    old = material / 'assets/unusual/hero.skel'
    old.parent.mkdir(parents=True)
    old.write_bytes(b'old-skel')
    old.with_name('hero.png').write_bytes(b'old-png')
    original = module.os.replace
    def fail_second(source, destination):
        if '.portrait-selected-' in str(source) and str(source).endswith('hero.png'):
            raise OSError('simulated replace failure')
        return original(source, destination)
    monkeypatch.setattr(module.os, 'replace', fail_second)
    with pytest.raises(OSError, match='replace failure'):
        module.export_configured_character_assets(**kwargs)
    assert old.read_bytes() == b'old-skel'
    assert old.with_name('hero.png').read_bytes() == b'old-png'


def test_category_commit_rollback_preserves_untouched_old_categories(tmp_path, monkeypatch):
    material, staging = tmp_path / 'material', tmp_path / 'staging'
    relatives = ('assets/art/models', 'assets/art/texturesingle/bursthead', 'assets/art/texturesingle/lotterybg')
    for relative in relatives:
        for base, data in ((material, 'old'), (staging, 'new')):
            path = base / relative / 'content.txt'
            path.parent.mkdir(parents=True)
            path.write_text(data)
    processor = ImportProcessor([], str(tmp_path), str(material), 'as.exe', export_categories={'character'})
    processor._working_material_dir = str(staging)
    import os
    original = os.replace
    def fail_backup(source, destination):
        if Path(source) == material / relatives[1]:
            raise OSError('backup failure')
        return original(source, destination)
    monkeypatch.setattr('app.features.importer.processing.os.replace', fail_backup)
    with pytest.raises(OSError, match='backup failure'):
        processor._commit_staged_material({'character'})
    assert all((material / relative / 'content.txt').read_text() == 'old' for relative in relatives)


def test_composition_root_wires_shared_exporter_into_importer_service(tmp_path):
    from app.bootstrap.workflows import ImportPostprocessWorkflow
    from app.features.importer.service import ImporterService
    class Signal:
        def connect(self, _handler):
            pass
    signal = Signal()
    importer = SimpleNamespace(service=ImporterService(tmp_path / 'material', tmp_path / 'lua'), result_ready=signal)
    consumer = SimpleNamespace(processing_finished=signal, processing_cancelled=signal, processing_error=signal)
    ImportPostprocessWorkflow(importer, consumer, object(), object(), preview=consumer)
    assert importer.service.character_asset_exporter is _module().export_configured_character_assets


def test_actual_worker_runs_callback_once_after_configuration_and_catalog(tmp_path, monkeypatch):
    from app.features.importer.worker import ImportWorker
    bundle = tmp_path / 'bundles' / '123'
    bundle.mkdir(parents=True)
    catalog = bundle / '_map' / 'assets_map.json'
    catalog.parent.mkdir()
    catalog.write_text('[]')
    lua = tmp_path / 'lua' / '123'
    lua.mkdir(parents=True)
    for name in ('BaseFashion.lua', 'BaseActivityStageConfig.lua'):
        (lua / name).write_text('return {}')
    cli = tmp_path / 'as.exe'
    cli.write_bytes(b'as')
    calls = []
    def exporter(**kwargs):
        calls.append(kwargs)
        path = Path(kwargs['material_root']) / 'assets/art/models/cardspine/selected.skel'
        path.parent.mkdir(parents=True)
        path.write_bytes(b'selected')
        return SimpleNamespace(files=('assets/art/models/cardspine/selected.skel',), scene_count=1, asset_count=1)
    worker = ImportWorker([], str(bundle), str(tmp_path / 'material'), str(cli),
                          export_categories={'character'}, version_timestamp=123,
                          lua_output_dir=str(lua.parent), character_asset_exporter=exporter)
    monkeypatch.setattr(worker.processor, '_stage_fix', lambda: (1, 0))
    monkeypatch.setattr('app.features.importer.processing.run_external_process', lambda *_args, **_kwargs: SimpleNamespace(returncode=0))
    monkeypatch.setattr('app.features.importer.processing.update_process_manifest', lambda *_args, **_kwargs: None)
    finished = []
    worker.all_finished.connect(lambda *args: finished.append(args))
    worker.run()
    assert len(calls) == 1
    assert finished and finished[0][0] is True
    assert worker.processor._categories_with_output == {'character'}


def test_missing_show_spine_config_is_skipped_before_building(tmp_path, monkeypatch):
    module, kwargs, _ = _setup(tmp_path, monkeypatch)
    missing = SimpleNamespace(skin_id='unused', unresolved_reason='missing_show_spine')
    valid = SimpleNamespace(skin_id='skin')
    monkeypatch.setattr(module, 'load_portrait_roots', lambda *_: [missing, valid])
    original = module.build_portrait_scenes
    def build(version, roots, index):
        assert all(root.skin_id != 'unused' for root in roots), 'non-display config must not become an incomplete export'
        return original(version, roots, index)
    monkeypatch.setattr(module, 'build_portrait_scenes', build)
    assert module.export_configured_character_assets(**kwargs).scene_count == 1


def test_unsafe_required_container_never_writes_material(tmp_path, monkeypatch):
    from dataclasses import replace
    module, kwargs, reads = _setup(tmp_path, monkeypatch)
    index = module.AssetIndex.from_catalog(None)
    invalid = module.AssetIndex(123, (replace(index.assets[0], container='../escape/hero.prefab'), *index.assets[1:]))
    monkeypatch.setattr(module.AssetIndex, 'from_catalog', lambda *_, **_kwargs: invalid)
    with pytest.raises(ValueError, match='unsafe configured asset path'):
        module.export_configured_character_assets(**kwargs)
    assert reads == [] and not kwargs['material_root'].exists()


def test_cancellation_during_category_commit_rolls_back_all_material(tmp_path):
    material, staging = tmp_path / 'material', tmp_path / 'staging'
    relatives = ('assets/art/models', 'assets/art/texturesingle/bursthead', 'assets/art/texturesingle/lotterybg')
    for relative in relatives:
        for base, data in ((material, 'old'), (staging, 'new')):
            path = base / relative / 'content.txt'
            path.parent.mkdir(parents=True)
            path.write_text(data)
    checks = []
    def cancel_check():
        checks.append(1)
        return len(checks) >= 3
    processor = ImportProcessor([], str(tmp_path), str(material), 'as.exe', export_categories={'character'},
                                cancel_check=cancel_check)
    processor._working_material_dir = str(staging)
    with pytest.raises(InterruptedError):
        processor._commit_staged_material({'character'})
    assert all((material / relative / 'content.txt').read_text() == 'old' for relative in relatives)
def test_refresh_rejects_version_mismatch_before_reading_bundles(tmp_path):
    from app.features.preview.portrait_import import refresh_portrait_packages
    from app.features.preview.portrait_config.asset_index import AssetIndex
    from app.features.preview.portrait_config.model import PortraitScene
    import pytest
    scene = PortraitScene('v', '123', 'skin', 'role', 'skin', None, (), ())
    with pytest.raises(ValueError, match='version'):
        refresh_portrait_packages((scene,), AssetIndex(124, ()), tmp_path / '123',
                                  tmp_path / 'material', tmp_path / 'output')


def test_refresh_decodes_exact_closure_in_scratch_and_keeps_material_untouched(tmp_path, monkeypatch):
    module, kwargs, reads = _setup(tmp_path, monkeypatch)
    from app.features.preview.portrait_config.model import PortraitScene
    from app.features.preview import portrait_package
    index = module.AssetIndex.from_catalog(None)
    scene = PortraitScene('v', '123', 'skin', 'role', 'skin', None, (), ())
    observed = []
    def publish(scenes, actual_index, scratch, output, **options):
        observed.append((scratch / '_portrait_inputs/a/1/hero.skel').read_bytes())
        assert (scratch / '_portrait_inputs/a/2/hero.png').is_file()
        return portrait_package.PortraitPackageSummary(published=1, manifest_paths=(output / 'skin.json',))
    monkeypatch.setattr(portrait_package, 'publish_portrait_packages', publish)
    result = module.refresh_portrait_packages((scene,), index, kwargs['bundle_root'],
        kwargs['material_root'], tmp_path / 'output')
    assert result.published == 1 and reads == [1, 2] and observed == [b'\x00\xffspine']
    assert not kwargs['material_root'].exists()
    assert not tuple(tmp_path.glob('.portrait-refresh-*'))


def test_refresh_cancel_during_decode_preserves_previous_package(tmp_path, monkeypatch):
    module, kwargs, reads = _setup(tmp_path, monkeypatch)
    from app.features.preview.portrait_config.model import PortraitScene
    output = tmp_path / 'output'
    output.mkdir()
    previous = output / 'skin.json'
    previous.write_bytes(b'previous')
    scene = PortraitScene('v', '123', 'skin', 'role', 'skin', None, (), ())
    result = module.refresh_portrait_packages((scene,), module.AssetIndex.from_catalog(None),
        kwargs['bundle_root'], kwargs['material_root'], output, cancel_check=lambda: bool(reads))
    assert result.cancelled and not result.published
    assert previous.read_bytes() == b'previous'
    assert not tuple(tmp_path.glob('.portrait-refresh-*'))
