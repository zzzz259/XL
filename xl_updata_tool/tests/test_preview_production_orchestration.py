import os
import threading
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtWidgets import QApplication

from app.features.preview.controller import PreviewController
from app.features.preview.export_plan import ExportSettings, build_export_plan
from app.features.preview.page import PreviewPage
from app.features.preview.resource_model import SpineSkinRecord
from app.features.preview.service import PreviewService, PreviewPreprocessSummary
from app.features.preview.output_publisher import RawSpinePublishSummary
from app.features.preview.material_catalog import MaterialExportSummary
from app.features.preview.workers.preview_export import PreviewExportWorker
from app.features.preview.workers.preview_postprocess import PreviewPostprocessWorker
from app.features.preview.portrait_config.model import PortraitScene, ScenePart, AssetRef


@pytest.fixture(scope='module')
def qapp():
    return QApplication.instance() or QApplication([])


def record(name):
    return SpineSkinRecord('10080', f'{name}.skel', f'{name}.atlas', name, name, name, 'ready')


def test_job_exception_is_counted_and_remaining_jobs_continue(tmp_path):
    jobs = build_export_plan((record('bad'), record('good')), ExportSettings(), tmp_path)
    calls, results = [], []
    def runner(job):
        calls.append(job)
        if job.record.skin_name == 'bad':
            raise OSError('disk error')
        job.output_path.write_bytes(b'png')
        return True
    worker = PreviewExportWorker(jobs, ExportSettings(), runner)
    worker.export_finished.connect(lambda ok, text: results.append((ok, text)))
    worker.run()
    assert len(calls) == 2
    assert results == [(False, '1 succeeded, 1 failed')]


def test_export_worker_preserves_native_thread_finished_signal(tmp_path):
    worker = PreviewExportWorker(build_export_plan((record('a'),), ExportSettings(), tmp_path), ExportSettings(), lambda job: False)
    assert worker.metaObject().indexOfSignal('summary_ready(QString)') >= 0
    assert worker.metaObject().indexOfSignal('finished(QString)') == -1
    assert worker.metaObject().indexOfSignal('finished()') >= 0


def test_cancel_retains_worker_and_busy_ui_until_actual_thread_exit(tmp_path, qapp):
    page = PreviewPage()
    controller = PreviewController(page, PreviewService(tmp_path / 'data/material', tmp_path / 'output/character'))
    entered, release = threading.Event(), threading.Event()
    def runner(job):
        entered.set()
        release.wait(10)
        return False
    assert controller.start_selected_export((record('a'),), ExportSettings(), runner)
    worker = controller._selected_export_worker
    assert entered.wait(5)
    try:
        controller.cancel_selected_export()
        assert controller._selected_export_worker is worker
        assert worker.isRunning()
        assert page.btn_export_selected.text() == '正在取消…'
        assert not page.btn_export_selected.isEnabled()
    finally:
        release.set()
        assert worker.wait(5000)
        qapp.processEvents()
        page.close()
    assert controller._selected_export_worker is None


def test_selected_export_consumes_current_scene_fallback_and_basic_signal(tmp_path, qapp, monkeypatch):
    page = PreviewPage()
    controller = PreviewController(page, PreviewService(tmp_path / 'data/material', tmp_path / 'output/character'))
    scene = object()
    monkeypatch.setattr(page, 'action_portrait_scenes', lambda: (scene,))
    calls = []
    monkeypatch.setattr(controller, '_start_portrait_scene_export', lambda scenes, **kwargs: calls.append((scenes, kwargs)) or True)
    assert controller.start_selected_export(settings=ExportSettings(), runner=lambda job: True)
    page.export_basic_requested.emit()
    assert [entry[0] for entry in calls] == [(scene,), (scene,)]
    assert calls[1][1]['options']['allow_basic'] is True
    assert calls[1][1]['options']['include_effects'] is False
    page.close()


@pytest.mark.parametrize('spine,materials', [
    (RawSpinePublishSummary(published=1, skipped=1, diagnostics=('failed source',)), MaterialExportSummary()),
    (RawSpinePublishSummary(), MaterialExportSummary(exported=1, failed=1)),
])
def test_postprocess_counts_raw_and_material_failures_as_partial(spine, materials, monkeypatch):
    outcomes = []
    monkeypatch.setattr('app.features.preview.workers.preview_postprocess.set_task_outcome', lambda outcome, **kwargs: outcomes.append(outcome))
    summary = PreviewPreprocessSummary(object(), spine, materials)
    worker = PreviewPostprocessWorker(SimpleNamespace(preprocess_preview_resources=lambda **kwargs: summary))
    worker.run()
    assert outcomes == ['partial']


def test_reload_only_refreshes_published_catalog(tmp_path, qapp, monkeypatch):
    page = PreviewPage()
    controller = PreviewController(page, PreviewService(tmp_path / 'data/material', tmp_path / 'output/character'))
    monkeypatch.setattr(controller, 'discover_resources', lambda: pytest.fail('reload must not discover source or publish'))
    calls = []
    monkeypatch.setattr(controller, 'load', lambda force=False: calls.append(force))
    controller.reload_requested()
    assert calls == [True]
    page.close()


def test_refresh_missing_exact_version_fails_without_touching_package(tmp_path):
    service = PreviewService(tmp_path / 'data/material', tmp_path / 'output/character')
    old = tmp_path / 'output/portraits/402/old/skin.json'
    old.parent.mkdir(parents=True)
    old.write_bytes(b'old package')
    scene = PortraitScene('402', '402', 'skin', '10080', 'skin', None, (), ())
    result = service.refresh_portrait_scenes((scene,))
    assert result.failed == 1
    assert '402' in result.failures[0]['error']
    assert old.read_bytes() == b'old package'


def test_refresh_rebuilds_only_selected_version_and_root_then_publishes(tmp_path, monkeypatch):
    service = PreviewService(tmp_path / 'data/material', tmp_path / 'output/character')
    bundle = tmp_path / 'data/bundles/402'
    (bundle / '_map').mkdir(parents=True)
    (bundle / '_map/assets_map.json').write_text('[]')
    lua = tmp_path / 'output/lua/402'
    lua.mkdir(parents=True)
    (lua / 'BaseFashion.lua').write_text('BaseFashion = {[1]={id=1,show_spine="selected"}, [2]={id=2,show_spine="other"}}')
    scene = PortraitScene('402', '402', '1', '10080', '1', None, (), ())
    from dataclasses import replace
    rebuilt = replace(scene, parts=(ScenePart('role', AssetRef('ab', 1), 'role'),))
    from app.features.preview import service as module
    calls = []
    monkeypatch.setattr(module, 'build_portrait_scenes', lambda version, roots, index, **kwargs: calls.append((version, roots, kwargs)) or [rebuilt])
    monkeypatch.setattr('app.features.preview.portrait_import.refresh_portrait_packages', lambda scenes, index, bundle_root, material_root, output_root, **kwargs: SimpleNamespace(published=1, failed=0, cancelled=False, manifest_paths=(tmp_path / 'skin.json',), failures=(), metadata_incomplete=0), raising=False)
    result = service.refresh_portrait_scenes((scene,))
    assert result.published == 1
    assert len(calls) == 1
    assert calls[0][0] == 402
    assert [root.skin_id for root in calls[0][1]] == ['1']
    assert calls[0][2].get('cache') is None


@pytest.mark.parametrize('not_applicable', [False, True])
def test_scene_preflight_failure_does_not_hide_valid_scene_result(tmp_path, qapp, monkeypatch, not_applicable):
    from app.features.preview.portrait_scene_export import PortraitSceneExportJob
    page = PreviewPage()
    controller = PreviewController(page, PreviewService(tmp_path / 'data/material', tmp_path / 'output/character'))
    bad = PortraitScene('402', '402', 'bad', '10080', 'bad', None, (), ())
    if not_applicable:
        from dataclasses import replace
        bad = replace(bad, config_metadata=(('export_status', 'not_applicable'),))
    good = PortraitScene('402', '402', 'good', '10080', 'good', None, (), ())
    monkeypatch.setattr('app.features.preview.portrait_package.find_portrait_package', lambda root, scene: tmp_path / 'skin.json' if scene.skin_id == 'good' else None)
    monkeypatch.setattr('app.features.preview.portrait_package.build_packaged_portrait_jobs', lambda scene, *args, **kwargs: (PortraitSceneExportJob(scene, (), tmp_path / 'good.png', ExportSettings()),))
    results = []
    controller.export_finished.connect(lambda ok, text: results.append((ok, text)))
    def runner(job):
        job.output_path.write_bytes(b'png')
        return True
    assert controller._start_portrait_scene_export((bad, good), settings=ExportSettings(), runner=runner)
    assert controller._selected_export_worker.wait(5000)
    qapp.processEvents()
    assert (tmp_path / 'good.png').read_bytes() == b'png'
    assert results[0][0] is not_applicable
    assert ('未适用 1' if not_applicable else 'bad') in results[0][1]
    page.close()


def test_postprocess_summary_does_not_release_running_thread(tmp_path, qapp, monkeypatch):
    page = PreviewPage()
    service = PreviewService(tmp_path / 'data/material', tmp_path / 'output/character')
    summary = PreviewPreprocessSummary(object(), RawSpinePublishSummary(), MaterialExportSummary())
    monkeypatch.setattr(service, 'preprocess_preview_resources', lambda **kwargs: summary)
    returned, release = threading.Event(), threading.Event()
    class HoldingWorker(PreviewPostprocessWorker):
        def run(self):
            super().run()
            returned.set()
            release.wait(10)
    monkeypatch.setattr('app.features.preview.controller.PreviewPostprocessWorker', HoldingWorker)
    controller = PreviewController(page, service)
    results = []
    controller.processing_finished.connect(results.append)
    assert controller.start_postprocess()
    worker = controller._postprocess_worker
    try:
        assert returned.wait(5)
        qapp.processEvents()
        assert controller._postprocess_worker is worker
        assert results == []
        assert controller.cancel_postprocess()
        assert worker.isRunning()
    finally:
        release.set()
        assert worker.wait(5000)
        qapp.processEvents()
        page.close()
    assert controller._postprocess_worker is None


def test_basic_result_is_distinguished_and_recorded_in_metadata(tmp_path):
    import json
    from app.features.preview.portrait_scene_export import PortraitSceneExportJob
    scene = PortraitScene('402', '402', 's', '10080', 's', None, (), (),
                          prefab_container='assets/art/models/cardspine/prefab/cardspine_10080_1.prefab')
    job = PortraitSceneExportJob(scene, (), tmp_path / 'basic.png', ExportSettings(), render_scope='basic')
    def runner(job):
        job.output_path.write_bytes(b'png')
        return True
    worker = PreviewExportWorker((job,), ExportSettings(), runner)
    results = []
    worker.export_finished.connect(lambda ok, text: results.append((ok, text)))
    worker.run()
    assert '1 basic' in results[0][1]
    assert json.loads((tmp_path / 'basic.png.metadata.json').read_text())['render_scope'] == 'basic'


def test_published_scene_keeps_unrelated_raw_spine_archive_available(tmp_path, monkeypatch):
    import json
    from dataclasses import asdict, replace
    output = tmp_path / 'output'
    output.mkdir()
    raw = replace(record('raw'), resource_family='cardspine')
    (output / 'preview_index.json').write_text(json.dumps({'spine': [{'records': [asdict(raw)]}]}))
    scene = PortraitScene('402', '402', 's', '10080', 's', None, (), (),
                          prefab_container='assets/art/models/cardspine/prefab/cardspine_10080_1.prefab')
    monkeypatch.setattr('app.features.preview.service.load_published_portrait_scenes', lambda root: ((scene,), ()))
    catalog = PreviewService(tmp_path / 'data/material', output / 'character').load_published_preview_resources()
    assert list(catalog.skins.values())[0].source_skel == 'raw.skel'


def test_registry_and_window_defer_close_until_preview_worker_stops(qapp):
    from PySide6.QtWidgets import QMainWindow
    from app.bootstrap.runtime import FeatureRuntimeRegistry
    from app.ui.main_window import MainWindow
    class BusyController:
        busy = True
        calls = 0
        def close(self):
            self.calls += 1
            return not self.busy
    controller = BusyController()
    registry = FeatureRuntimeRegistry((SimpleNamespace(descriptor=SimpleNamespace(key='preview'), controller=controller),))
    class Window(MainWindow):
        def __init__(self):
            QMainWindow.__init__(self)
            self.runtime = SimpleNamespace(registry=registry)
    window = Window()
    window.show()
    qapp.processEvents()
    assert window.close() is False
    assert window.isVisible()
    controller.busy = False
    assert window.close() is True
    assert not window.isVisible()


def test_default_raw_runner_failure_keeps_prior_final_and_uses_data_scratch(tmp_path, qapp, monkeypatch):
    page = PreviewPage()
    controller = PreviewController(page, PreviewService(tmp_path / 'data/material', tmp_path / 'output/character'))
    job = build_export_plan((record('raw'),), ExportSettings(), tmp_path / 'output/character')[0]
    job.output_path.parent.mkdir(parents=True)
    job.output_path.write_bytes(b'previous final')
    outputs = []
    def build(execution, cli, temporary):
        outputs.append(execution.output_path)
        return ['fake-cli', str(execution.output_path)]
    def run(command, **kwargs):
        from pathlib import Path
        Path(command[-1]).write_bytes(b'broken new output')
        return SimpleNamespace(returncode=1, stderr='failed', stdout='')
    monkeypatch.setattr('app.features.preview.controller.build_spine_export_command', build)
    monkeypatch.setattr('app.features.preview.controller.run_external_process', run)
    monkeypatch.setattr('app.features.preview.controller.update_process_manifest', lambda *args, **kwargs: None)
    assert not controller._default_skin_runner('fake-cli')(job)
    assert job.output_path.read_bytes() == b'previous final'
    assert tmp_path / 'data/cache/portrait-export' in outputs[0].parents
    assert not outputs[0].exists()
    page.close()


def test_native_frame_progress_is_reported_without_claiming_total_task_percentage(tmp_path, qapp):
    page = PreviewPage()
    controller = PreviewController(page, PreviewService(tmp_path / 'data/material', tmp_path / 'output/character'))
    worker = PreviewExportWorker(build_export_plan((record('a'),), ExportSettings(), tmp_path), ExportSettings(), lambda job: True)
    progress = []
    worker.render_progress.connect(lambda current, total, label: progress.append((current, total, label)))
    controller._on_native_render_line(worker, 'skin', 'stdout', '{"phase":"export","total":180,"current":30}')
    controller._on_native_render_line(worker, 'skin', 'stderr', 'unstructured diagnostic')
    assert progress == [(30, 180, 'skin · 渲染帧')]
    page.close()


def test_cooperative_cancel_is_not_counted_as_failed_job(tmp_path):
    jobs = build_export_plan((record('a'),), ExportSettings(), tmp_path)
    def runner(job):
        worker.cancel()
        return False
    worker = PreviewExportWorker(jobs, ExportSettings(), runner)
    results = []
    worker.summary_ready.connect(results.append)
    worker.run()
    assert results == ['0 succeeded, 0 failed, cancelled']


@pytest.mark.parametrize('browser_name', ['character_browser', 'material_browser'])
def test_close_waits_for_each_browser_thumbnail_without_blocking_or_restart(tmp_path, qapp, monkeypatch, browser_name):
    from PySide6.QtCore import QThread
    from app.features.preview.workers.image_loader import ImageLoadWorker
    entered, release = threading.Event(), threading.Event()
    created = []
    class BlockingLoader(ImageLoadWorker):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            created.append(self)
        def run(self):
            entered.set()
            release.wait(10)
        def wait(self, *args):
            raise AssertionError('GUI cancellation must not wait for thumbnail decode')
    monkeypatch.setattr('app.features.preview.page.ImageLoadWorker', BlockingLoader)
    page = PreviewPage()
    controller = PreviewController(page, PreviewService(tmp_path / 'data/material', tmp_path / 'output/character'))
    browser = getattr(page, browser_name)
    browser.set_entries([{'name': 'image', 'path': str(tmp_path / 'image.png')}])
    browser._load_visible_thumbnails()
    worker = browser._thumbnail_worker
    try:
        assert entered.wait(5)
        assert controller.close() is False
        assert worker._cancelled
        assert controller.has_running_tasks
        browser._load_visible_thumbnails()
        assert len(created) == 1
    finally:
        release.set()
        assert QThread.wait(worker, 5000)
        qapp.processEvents()
        page.close()
    assert controller.close() is True
    browser._load_visible_thumbnails()
    assert len(created) == 1


@pytest.mark.parametrize('staged_state', ['missing', 'empty', 'fresh'])
def test_cli_zero_exit_requires_fresh_nonempty_staged_output(tmp_path, qapp, monkeypatch, staged_state):
    from pathlib import Path
    page = PreviewPage()
    controller = PreviewController(page, PreviewService(tmp_path / 'data/material', tmp_path / 'output/character'))
    job = build_export_plan((record('raw'),), ExportSettings(), tmp_path / 'output/character')[0]
    job.output_path.parent.mkdir(parents=True)
    job.output_path.write_bytes(b'previous final')
    monkeypatch.setattr('app.features.preview.controller.build_spine_export_command', lambda execution, cli, temporary: ['fake-cli', str(execution.output_path)])
    def run(command, **kwargs):
        if staged_state != 'missing':
            Path(command[-1]).write_bytes(b'new final' if staged_state == 'fresh' else b'')
        return SimpleNamespace(returncode=0, stderr='', stdout='')
    monkeypatch.setattr('app.features.preview.controller.run_external_process', run)
    monkeypatch.setattr('app.features.preview.controller.update_process_manifest', lambda *args, **kwargs: None)
    assert controller._default_skin_runner('fake-cli')(job) is (staged_state == 'fresh')
    assert job.output_path.read_bytes() == (b'new final' if staged_state == 'fresh' else b'previous final')
    page.close()


def test_thumbnail_navigation_retains_old_thread_and_ignores_stale_decodes(tmp_path, qapp, monkeypatch):
    from PySide6.QtCore import QThread
    from PySide6.QtGui import QImage
    from app.features.preview.workers.image_loader import ImageLoadWorker
    created, gates, entered = [], [], []
    class BlockingLoader(ImageLoadWorker):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            created.append(self)
            self.gate = threading.Event()
            self.entered = threading.Event()
            gates.append(self.gate)
            entered.append(self.entered)
        def run(self):
            self.entered.set()
            self.gate.wait(10)
            self.image_loaded.emit(self.image_paths[0], QImage(1, 1, QImage.Format_ARGB32))
        def wait(self, *args):
            raise AssertionError('navigation must not join thumbnail decoding')
    monkeypatch.setattr('app.features.preview.page.ImageLoadWorker', BlockingLoader)
    page = PreviewPage()
    browser = page.character_browser
    path = str(tmp_path / 'same-name.png')
    try:
        browser.set_entries([{'name': 'old', 'path': path}])
        browser._load_visible_thumbnails()
        old = browser._thumbnail_worker
        assert entered[0].wait(5)
        browser.set_entries([{'name': 'new', 'path': path}])
        assert old in browser._retired_thumbnail_workers
        browser._load_visible_thumbnails()
        current = browser._thumbnail_worker
        assert current is not old and entered[1].wait(5)
        gates[0].set()
        assert QThread.wait(old, 5000)
        qapp.processEvents()
        assert browser._thumbnail_worker is current
        assert path not in browser._thumbnail_loaded
        assert old not in browser._retired_thumbnail_workers
        browser.stop_thumbnail_loading()
        assert browser.has_running_thumbnail_tasks
    finally:
        browser.stop_thumbnail_loading()
        for gate in gates:
            gate.set()
        for worker in created:
            # finished slots may already have deleted previous workers.
            try:
                assert QThread.wait(worker, 5000)
            except RuntimeError:
                pass
        qapp.processEvents()
        page.close()
    assert not browser.has_running_thumbnail_tasks
    assert len(created) == 2


def test_legacy_controller_forwards_partial_material_result_after_thread_cleanup(tmp_path, qapp, monkeypatch):
    from app.features.preview.workers import preview_export
    material = tmp_path / 'material'
    material.mkdir()
    (material / 'hero.skel').write_bytes(b'skeleton')
    (material / 'hero.atlas').write_bytes(b'atlas')
    cli = tmp_path / 'fake-cli.exe'
    cli.write_bytes(b'fake')
    monkeypatch.setattr(preview_export, 'get_animation_names', lambda *args: ['idle'])
    monkeypatch.setattr(preview_export, 'export_animation_frames', lambda *args: True)
    monkeypatch.setattr(preview_export, 'extract_motion_names', lambda *args: [])
    monkeypatch.setattr(preview_export, 'build_cardspine_bundle_map', lambda *args: {})
    monkeypatch.setattr(PreviewExportWorker, '_find_assets_map', lambda self: None)
    monkeypatch.setattr(PreviewExportWorker, '_export_fgui_atlas', lambda self: MaterialExportSummary(failed=1, diagnostics=('FGUI failed',)))
    page = PreviewPage()
    controller = PreviewController(page, PreviewService(material, tmp_path / 'output/character'))
    results = []
    controller.export_finished.connect(lambda ok, text: results.append((ok, text)))
    assert controller.start_export(str(cli))
    worker = controller._export_worker
    assert worker.wait(5000)
    qapp.processEvents()
    assert controller._export_worker is None
    assert results[0][0] is False
    assert '部分完成' in results[0][1]
    assert '游戏素材失败: 1 个' in results[0][1]
    assert 'FGUI failed' in results[0][1]
    page.close()
