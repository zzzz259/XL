import json
import threading

import pytest

from app.features.preview.export_plan import ExportSettings, build_default_export_plan, build_export_plan
from app.features.preview.resource_model import SpineSkinRecord
from app.features.preview.workers import preview_export
from app.features.preview.workers.preview_export import PreviewExportWorker


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
    runner = RecordingRunner([True, False])
    worker = PreviewExportWorker(make_jobs(tmp_path, "base", "holiday"), ExportSettings(), runner)
    progress = []
    summaries = []
    errors = []
    worker.skin_progress.connect(lambda current, total, label: progress.append((current, total, label)))
    worker.finished.connect(summaries.append)
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
    worker.finished.connect(summaries.append)

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


def test_worker_cancellation_stops_subsequent_jobs_and_preserves_completed_output(tmp_path):
    runner = RecordingRunner()

    def run_and_cancel(job):
        result = runner(job)
        worker.cancel()
        return result

    worker_runner = run_and_cancel
    worker = PreviewExportWorker(make_jobs(tmp_path, "base", "holiday"), ExportSettings(), worker_runner)
    summaries = []
    worker.finished.connect(summaries.append)

    worker.run()

    assert len(runner.calls) == 1
    assert runner.calls[0].output_path.is_file()
    assert not (tmp_path / "10080" / "10080_Spine_holiday.png").exists()
    assert summaries and "cancelled" in summaries[0].lower()


def test_worker_emits_error_for_runner_exception_without_starting_cli(tmp_path):
    def runner(_job):
        raise RuntimeError("runner unavailable")

    worker = PreviewExportWorker(make_jobs(tmp_path, "base"), ExportSettings(), runner)
    errors = []
    summaries = []
    worker.error.connect(errors.append)
    worker.finished.connect(summaries.append)

    worker.run()

    assert errors == ["runner unavailable"]
    assert summaries == []


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
