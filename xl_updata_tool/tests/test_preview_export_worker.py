import json
import threading

import pytest

from app.features.preview.export_plan import ExportSettings, build_default_export_plan, build_export_plan
from app.features.preview.resource_model import SpineSkinRecord
from app.features.preview.portrait_config.model import AssetRef, PortraitScene
from app.features.preview.portrait_scene_export import (
    PortraitSceneExportJob,
    SpineSource,
    SpriteSource,
)
from app.features.preview.workers import preview_export
from app.features.preview.workers.preview_export import PreviewExportWorker
from app.platform.logger import configure_logging
from app.platform.runtime_config import RuntimeConfig


def ready_record(skin_name, *, character_id="10080", fingerprint=None):
    return SpineSkinRecord(
        character_id=character_id,
        source_skel=f"assets/{skin_name}.skel",
        atlas_path=f"assets/{skin_name}.atlas",
        skin_name=skin_name,
        attachment_fingerprint=fingerprint or skin_name,
        display_name=skin_name,
        status="ready",
    )


class RecordingRunner:
    def __init__(self, results=None):
        self.calls = []
        self.results = iter(results or ())

    def __call__(self, job):
        self.calls.append(job)
        job.output_path.parent.mkdir(parents=True, exist_ok=True)
        result = next(self.results, True)
        if result:
            job.output_path.write_bytes(b"png")
        return result


def make_jobs(tmp_path, *skin_names):
    records = tuple(ready_record(name) for name in skin_names)
    return build_export_plan(records, ExportSettings(), tmp_path)


def test_worker_reports_progress_and_finished_summary_for_runner_results(tmp_path):
    session = configure_logging(RuntimeConfig(debug=False), logs_dir=tmp_path / "logs")
    runner = RecordingRunner([True, False])
    worker = PreviewExportWorker(make_jobs(tmp_path, "base", "holiday"), ExportSettings(), runner)
    progress = []
    summaries = []
    errors = []
    worker.skin_progress.connect(lambda current, total, label: progress.append((current, total, label)))
    worker.summary_ready.connect(summaries.append)
    worker.error.connect(errors.append)

    worker.run()

    assert progress == [(1, 2, "spine/Png: base"), (2, 2, "spine/Png: holiday")]
    assert len(runner.calls) == 2
    assert summaries and "1 succeeded" in summaries[0] and "1 failed" in summaries[0]
    assert errors == []
    assert (tmp_path / "10080" / "10080_Spine_base.png").is_file()
    metadata = json.loads(
        runner.calls[0].output_path.with_name(
            runner.calls[0].output_path.name + ".metadata.json"
        ).read_text(encoding="utf-8")
    )
    assert metadata["record"]["skin_name"] == "base"
    summary_events = [json.loads(line) for line in session.events_log.read_text(encoding="utf-8").splitlines()]
    task_id = next(event["task_id"] for event in summary_events if event["event"] == "task.start")
    task_events = [
        json.loads(line)
        for line in (session.directory / "tasks" / task_id / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    job_events = [event for event in task_events if event["event"].startswith("spine.export.")]
    assert any(event["event"] == "spine.export.complete" and event["details"]["output"].endswith("Spine_base.png") for event in job_events)
    assert any(event["event"] == "spine.export.failed" and event["details"]["record"]["skin_name"] == "holiday" for event in job_events)
    summaries = list((session.directory / "tasks").glob("*/summary.json"))
    assert len(summaries) == 1
    assert json.loads(summaries[0].read_text(encoding="utf-8"))["outcome"] == "partial"


def test_worker_logs_and_writes_exact_scene_part_refs(tmp_path):
    scene = PortraitScene(
        game_version="vA160", version_timestamp="134351472190013976",
        entry_config_id="12002183", character_id="10118", skin_id="12002183",
        prefab=None, source_bundles=("bundle-a",), parts=(),
    )
    source = SpineSource(
        part_id="role", skeleton_path=tmp_path / "role.skel", atlas_path=tmp_path / "role.atlas",
        animation="idle", loop=False, skin="", time_scale=1.0,
        unity_order=(0, 4, 0.0, 0), skeleton_ref=AssetRef("bundle-a", 10),
        atlas_refs=(AssetRef("bundle-a", 11),),
    )
    job = PortraitSceneExportJob(
        scene, (source,), tmp_path / "character" / "role.png", ExportSettings(),
    )
    runner = RecordingRunner()
    worker = PreviewExportWorker((job,), settings=ExportSettings(), runner=runner)
    summaries = []
    worker.summary_ready.connect(summaries.append)

    worker.run()

    assert summaries == ["1 succeeded, 0 failed"]
    metadata = json.loads(
        job.output_path.with_name(job.output_path.name + ".metadata.json").read_text(encoding="utf-8")
    )
    assert metadata["scene"]["source_bundles"] == ["bundle-a"]
    assert metadata["sources"][0]["skeleton_ref"] == {"bundle_hash": "bundle-a", "path_id": 10}
    assert metadata["sources"][0]["animation"] == "idle"


def test_scene_metadata_records_static_sprite_identity_and_render_transform(tmp_path):
    scene = PortraitScene(
        game_version="vA160", version_timestamp="134351472190013976",
        entry_config_id="12002183", character_id="10118", skin_id="12002183",
        prefab=None, source_bundles=("bundle-a",), parts=(),
    )
    sprite = SpriteSource(
        part_id="SpriteRenderer:bundle-a:40",
        texture_path=tmp_path / "sprite-atlas.png",
        rect=(12.0, 20.0, 64.0, 80.0),
        pivot=(0.25, 0.5),
        pixels_per_unit=100.0,
        transform_chain=(),
        color=(1.0, 1.0, 1.0, 0.75),
        flip_x=True,
        flip_y=False,
        mask_interaction=0,
        blend_mode="normal",
        unity_order=(0, 3, 0.0, 2),
        sprite_ref=AssetRef("bundle-a", 40),
        texture_ref=AssetRef("bundle-a", 41),
    )
    job = PortraitSceneExportJob(
        scene, (), tmp_path / "character" / "sprite.png", ExportSettings(), sprite_sources=(sprite,),
    )
    worker = PreviewExportWorker((job,), settings=ExportSettings(), runner=RecordingRunner())
    worker.run()

    metadata = json.loads(
        job.output_path.with_name(job.output_path.name + ".metadata.json").read_text(encoding="utf-8")
    )
    assert metadata["sprite_sources"] == [{
        "part_id": "SpriteRenderer:bundle-a:40",
        "texture_path": str(tmp_path / "sprite-atlas.png"),
        "sprite_ref": {"bundle_hash": "bundle-a", "path_id": 40},
        "texture_ref": {"bundle_hash": "bundle-a", "path_id": 41},
        "rect": [12.0, 20.0, 64.0, 80.0],
        "pivot": [0.25, 0.5],
        "pixels_per_unit": 100.0,
        "unity_order": [0, 3, 0.0, 2],
        "color": [1.0, 1.0, 1.0, 0.75],
        "flip_x": True,
        "flip_y": False,
        "mask_interaction": 0,
        "blend_mode": "normal",
    }]


def test_skin_jobs_use_labeled_signal_and_preserve_legacy_progress_signature(tmp_path):
    worker = PreviewExportWorker(make_jobs(tmp_path, "base"), ExportSettings(), RecordingRunner())
    assert worker.metaObject().indexOfSignal("progress(int,int)") >= 0
    assert worker.metaObject().indexOfSignal("progress(int,int,QString)") == -1
    assert worker.metaObject().indexOfSignal("skin_progress(int,int,QString)") >= 0


def test_skin_job_worker_does_not_call_composite_functions(tmp_path, monkeypatch):
    def fail_if_called(*_args, **_kwargs):
        pytest.fail("skin-job worker must not call composite functions")

    monkeypatch.setattr(preview_export, "composite_images", fail_if_called)
    monkeypatch.setattr(preview_export, "composite_with_offset", fail_if_called)

    worker = PreviewExportWorker(make_jobs(tmp_path, "base"), ExportSettings(), RecordingRunner())
    summaries = []
    worker.summary_ready.connect(summaries.append)

    worker.run()

    assert summaries == ["1 succeeded, 0 failed"]


def test_mixed_default_jobs_write_per_job_metadata(tmp_path):
    card = SpineSkinRecord(
        character_id="10080",
        source_skel="assets/cardspine_10080_4.skel",
        atlas_path="assets/cardspine_10080_4.atlas",
        skin_name="default",
        attachment_fingerprint="card",
        display_name="card",
        status="ready",
        resource_family="cardspine",
    )
    battle = SpineSkinRecord(
        character_id="10080",
        source_skel="assets/battlespine_10080_2.skel",
        atlas_path="assets/battlespine_10080_2.atlas",
        skin_name="default",
        attachment_fingerprint="battle",
        display_name="battle",
        status="ready",
        resource_family="battlespine",
    )
    jobs = build_default_export_plan(((card,), (battle,)), tmp_path / "character")
    runner = RecordingRunner()
    worker = PreviewExportWorker(jobs, settings=None, runner=runner)

    worker.run()

    assert len(runner.calls) == 3
    metadata_paths = [
        job.output_path.with_name(job.output_path.name + ".metadata.json")
        for job in runner.calls
    ]
    assert len(set(metadata_paths)) == 3
    formats = [
        json.loads(path.read_text(encoding="utf-8"))["settings"]["format"]
        for path in metadata_paths
    ]
    assert formats.count("Png") == 2
    assert formats.count("Mp4") == 1
    assert all(
        json.loads(path.read_text(encoding="utf-8"))["export_mode"] == "builtin"
        for path in metadata_paths
    )


def test_legacy_export_emits_two_argument_progress(tmp_path, monkeypatch):
    material_dir = tmp_path / "material"
    material_dir.mkdir()
    (material_dir / "hero.skel").write_bytes(b"")
    (material_dir / "hero.atlas").write_bytes(b"")

    monkeypatch.setattr(preview_export, "get_animation_names", lambda *_args: ["idle"])
    monkeypatch.setattr(preview_export, "export_animation_frames", lambda *_args: True)
    monkeypatch.setattr(preview_export, "extract_motion_names", lambda *_args: [])

    worker = PreviewExportWorker(str(material_dir), str(tmp_path / "output"), "SpineViewerCLI.exe")
    progress = []
    worker.progress.connect(lambda current, total: progress.append((current, total)))

    worker.run()

    assert progress == [(1, 1)]


def test_legacy_battlespine_export_passes_motion_stander_skin(tmp_path, monkeypatch):
    material_dir = tmp_path / "material" / "assets" / "art" / "models" / "battlespine"
    material_dir.mkdir(parents=True)
    (material_dir / "battlespine_10080_2.skel").write_bytes(b"")
    (material_dir / "battlespine_10080_2.atlas").write_bytes(b"")
    calls = []

    monkeypatch.setattr(preview_export, "get_animation_names", lambda *_args: ["idle"])
    monkeypatch.setattr(
        preview_export,
        "export_animation_frames",
        lambda *args: calls.append(args) or True,
    )
    monkeypatch.setattr(preview_export, "extract_motion_names", lambda *_args: [])
    monkeypatch.setattr(preview_export, "discover_game_materials", lambda *_args: None)
    monkeypatch.setattr(
        preview_export,
        "export_game_materials",
        lambda *_args, **_kwargs: type(
            "Summary", (), {"diagnostics": (), "exported": 0, "failed": 0}
        )(),
    )

    worker = PreviewExportWorker(
        str(tmp_path / "material"),
        str(tmp_path / "output"),
        "SpineViewerCLI.exe",
    )
    worker.run()

    assert len(calls) == 1
    assert calls[0][-1] == "motion_stander"


@pytest.mark.parametrize('mode,spine_failed,material_failed,outcome,complete', [
    ('complete', 0, 0, 'success', True),
    ('materials', 0, 1, 'partial', False),
    ('raw', 1, 0, 'partial', False),
    ('missing_atlas', 1, 0, 'partial', False),
    ('skins', 1, 0, 'partial', False),
    ('cancelled', 0, 0, 'cancelled', False),
])
def test_legacy_whole_image_summary_accounts_for_every_failure_and_cancel(
    tmp_path, monkeypatch, mode, spine_failed, material_failed, outcome, complete,
):
    from app.features.preview.material_catalog import MaterialExportSummary
    material = tmp_path / 'material'
    material.mkdir()
    for name in ('good', 'bad'):
        (material / f'{name}.skel').write_bytes(b'skeleton')
        if name != 'bad' or mode != 'missing_atlas':
            (material / f'{name}.atlas').write_bytes(b'atlas')
    monkeypatch.setattr(preview_export, 'get_animation_names', lambda *args: ['idle'])
    monkeypatch.setattr(preview_export, 'build_cardspine_bundle_map', lambda *args: {})
    monkeypatch.setattr(PreviewExportWorker, '_find_assets_map', lambda self: None)
    monkeypatch.setattr(preview_export, 'extract_motion_names', lambda path: ['expression'] if mode == 'skins' and path.endswith('bad.skel') else [])
    monkeypatch.setattr(preview_export, 'export_skel_skins', lambda *args: 0)
    worker = PreviewExportWorker(str(material), str(tmp_path / 'output'), 'fake-cli')
    def export(*args):
        if mode == 'cancelled':
            worker.cancel()
        return not (mode == 'raw' and args[0].endswith('bad.skel'))
    monkeypatch.setattr(preview_export, 'export_animation_frames', export)
    material_calls, outcomes, results = [], [], []
    monkeypatch.setattr(worker, '_export_fgui_atlas', lambda: material_calls.append(True) or MaterialExportSummary(exported=1, failed=material_failed, diagnostics=('material failed',) if material_failed else ()))
    monkeypatch.setattr(preview_export, 'set_task_outcome', lambda value, **kwargs: outcomes.append((value, kwargs)))
    worker.export_finished.connect(lambda ok, summary: results.append((ok, summary)))

    returned = worker._do_export()

    assert returned is complete
    assert results[0][0] is complete
    assert f'失败: {spine_failed + material_failed} 个' in results[0][1]
    assert outcomes[-1][0] == outcome
    assert outcomes[-1][1]['details']['spine_failed'] == spine_failed
    assert outcomes[-1][1]['details']['material_failed'] == material_failed
    if mode == 'cancelled':
        assert '已取消' in results[0][1]
        assert material_calls == []
    else:
        assert f'游戏素材失败: {material_failed} 个' in results[0][1]


def test_worker_cancellation_stops_subsequent_jobs_and_preserves_completed_output(tmp_path):
    runner = RecordingRunner()

    def run_and_cancel(job):
        result = runner(job)
        worker.cancel()
        return result

    worker_runner = run_and_cancel
    worker = PreviewExportWorker(make_jobs(tmp_path, "base", "holiday"), ExportSettings(), worker_runner)
    summaries = []
    worker.summary_ready.connect(summaries.append)

    worker.run()

    assert len(runner.calls) == 1
    assert runner.calls[0].output_path.is_file()
    assert not (tmp_path / "10080" / "10080_Spine_holiday.png").exists()
    assert summaries and "cancelled" in summaries[0].lower()


def test_worker_reports_item_failure_for_runner_exception_without_starting_cli(tmp_path):
    def runner(_job):
        raise RuntimeError("runner unavailable")

    worker = PreviewExportWorker(make_jobs(tmp_path, "base"), ExportSettings(), runner)
    errors = []
    summaries = []
    worker.error.connect(errors.append)
    worker.summary_ready.connect(summaries.append)

    worker.run()

    assert errors == []
    assert summaries == ['0 succeeded, 1 failed']


def test_worker_rejects_a_second_active_start(tmp_path):
    entered = threading.Event()
    release = threading.Event()

    def runner(job):
        entered.set()
        release.wait(timeout=5)
        job.output_path.parent.mkdir(parents=True, exist_ok=True)
        job.output_path.write_bytes(b"png")
        return True

    worker = PreviewExportWorker(make_jobs(tmp_path, "base"), ExportSettings(), runner)

    assert worker.start()
    assert entered.wait(timeout=5)
    assert worker.start() is False
    release.set()
    assert worker.wait(5000)
