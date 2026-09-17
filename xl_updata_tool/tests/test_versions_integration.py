"""Regression coverage for the final download/refresh integration review."""

import hashlib
from threading import Event
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from app.features.versions import download_worker as workers
from app.features.versions.controller import VersionController
from app.features.versions.page import DownloadProgressButton, VersionPage
from app.features.versions.service import VersionService
from app.platform import database as db


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def download(monkeypatch, tmp_path, qapp):
    db.init_db(str(tmp_path / "versions.db"))
    for ts in (100, 200):
        db.save_version(ts, {}, {"data": []})
        db.save_sub_bundles(ts, [f"file-{ts}"])
    controller = VersionController(VersionPage(), VersionService(tmp_path / "bundles"))
    controller.load()
    # Keep the real worker and signals, controlling only thread scheduling/network.
    monkeypatch.setattr(workers.DownloadWorker, "start", lambda self: None)
    dialogs, statuses, progress = [], [], []
    monkeypatch.setattr(QMessageBox, "information", lambda *args: dialogs.append(args))
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: dialogs.append(args))
    controller.status_changed.connect(statuses.append)
    controller.progress_changed.connect(lambda *args: progress.append(args))
    yield controller, dialogs, statuses, progress
    controller.close()


@pytest.mark.parametrize("cancelled", [False, True])
def test_rebuild_restores_active_download_controls_and_feedback(download, monkeypatch, cancelled):
    controller, _, statuses, _ = download
    controller.download_version(200)
    worker = controller._download_worker
    monkeypatch.setattr(worker, "isRunning", lambda: True)
    monkeypatch.setattr(worker, "wait", lambda *_args: True)
    worker.progress.emit("file-200", 1, 4)
    if cancelled:
        controller.cancel_download()
    controller.load()

    button = controller._download_controls[200][True]
    assert isinstance(button, DownloadProgressButton)
    assert button.value() == 25
    assert "1/4" in controller._status_items[200].text()
    assert all(not b.isEnabled() for b in controller._delete_buttons.values())
    assert all(
        not b.isEnabled()
        for controls in controller._download_controls.values()
        for b in controls.values() if b is not button
    )
    if cancelled:
        assert "取消" in statuses[-1]
    else:
        assert "file-200.bundle" in statuses[-1]
        button.clicked.emit()
        assert worker._stop


def test_sorted_download_replaces_the_live_timestamp_row(download):
    controller, _, _, _ = download
    table = controller.page.table
    # Distinct labels force movement even though these tiny timestamps share a date.
    table.setSortingEnabled(False)
    for row in range(table.rowCount()):
        item = table.item(row, 1)
        item.setText(str(item.data(Qt.UserRole)))
    table.setSortingEnabled(True)
    table.sortItems(1, Qt.AscendingOrder)
    controller.download_version(200)
    for row in range(table.rowCount()):
        ts = table.item(row, 1).data(Qt.UserRole)
        assert isinstance(table.cellWidget(row, 5), DownloadProgressButton) == (ts == 200)


def test_first_transfer_updates_all_feedback_before_http_returns(download, monkeypatch):
    controller, _, statuses, progress = download
    controller.download_version(200)
    worker = controller._download_worker

    def transfer(_url):
        assert "file-200.bundle" in statuses[-1]
        assert progress[-1][:2] == (0, 1)
        assert "file-200.bundle" in progress[-1][2]
        assert controller._status_items[200].text() == "下载中 (0/1)"
        worker.stop()
        return b"cancelled"

    monkeypatch.setattr(workers, "http_get", transfer)
    worker.run()
    assert worker.outcome == "cancelled"
    assert progress, "the first transfer never announced its current file"


@pytest.mark.parametrize("outcome", ["failed", "aborted", "cancelled", "success"])
def test_completion_requires_successful_terminal_outcome(download, outcome):
    controller, dialogs, statuses, _ = download
    controller.download_version(200)
    worker = controller._download_worker
    worker.outcome = outcome
    if outcome == "failed":
        worker.item_fail.emit("file-200", "network exhausted")
    worker.finished.emit()
    assert bool(dialogs) == (outcome == "success")
    assert ("下载完成" in statuses[-1]) == (outcome == "success")
    if outcome == "cancelled":
        assert "取消" in statuses[-1]
    elif outcome == "failed":
        assert "失败" in statuses[-1]
    elif outcome == "aborted":
        assert "中止" in statuses[-1]
    assert controller._download_worker is None
    assert all(b.isEnabled() for b in controller._delete_buttons.values())


def test_cannot_replace_worker_before_queued_completion_is_handled(download):
    controller, _, _, _ = download
    controller.download_version(200)
    first = controller._download_worker
    controller.download_version(100)
    assert controller._download_worker is first


def test_close_resets_check_animation_and_suppresses_queued_ui(download):
    controller, dialogs, statuses, progress = download
    controller.download_version(200)
    worker = controller._download_worker
    controller.page.set_checking(True)
    states = []
    controller.check_state_changed.connect(states.append)
    controller.close()
    before = (len(statuses), len(progress))
    worker.progress.emit("late", 1, 1)
    worker.finished.emit()
    controller._on_update_checked({}, {}, [], {})
    controller._on_check_error("late error")
    controller.check_update()
    controller.download_version(100)

    assert controller.page.workspace_title.text() == "版本工作区"
    assert not controller.page._check_animation_timer.isActive()
    assert states == [False]
    assert dialogs == []
    assert (len(statuses), len(progress)) == before


def test_close_joins_after_initial_wait_times_out(qapp):
    waits = []
    interruptions = []
    worker = SimpleNamespace(
        isRunning=lambda: True, stop=lambda: None,
        requestInterruption=lambda: interruptions.append(True),
        wait=lambda *args: waits.append(args) or not args,
    )
    controller = VersionController(VersionPage(), SimpleNamespace())
    controller._check_thread = worker
    controller.close()
    assert waits == [(30000,), ()]
    assert interruptions == [True]
    assert controller._check_thread is None


def test_interrupted_check_exits_without_downloading_categories(monkeypatch, tmp_path, qapp):
    entered, release = Event(), Event()
    requests = []

    def check(**_kwargs):
        entered.set()
        assert release.wait(5)
        return {}, {"data": [{"name": "Arts", "hash": "abc"}]}

    monkeypatch.setattr(workers, "check_update", check)
    monkeypatch.setattr(workers, "http_get", lambda url: requests.append(url) or b"data")
    monkeypatch.setattr(workers, "extract_manifest_hashes", lambda _path: set())
    worker = workers.CheckUpdateThread(str(tmp_path))
    worker.start()
    try:
        assert entered.wait(5)
        worker.requestInterruption()
    finally:
        release.set()
        assert worker.wait(5000)
    assert requests == []


def test_check_thread_finished_is_lifecycle_signal_and_clears_controller(monkeypatch, tmp_path, qapp):
    db.init_db(str(tmp_path / "versions.db"))
    monkeypatch.setattr(workers, "check_update", lambda **kw: ({}, {"data": []}))
    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    controller = VersionController(VersionPage(), VersionService(tmp_path))
    monkeypatch.setattr(controller.service, "register_checked", lambda *args: None)
    controller.check_update()
    worker = controller._check_thread
    assert worker.wait(5000)
    qapp.processEvents()
    assert controller._check_thread is None
    assert not controller.page._checking


@pytest.mark.parametrize("mode", ["failure", "cancel", "success", "mkdir_error"])
def test_worker_records_terminal_outcome_and_only_announces_success(monkeypatch, tmp_path, qapp, mode):
    payload = b"non-Unity verified asset"
    file_hash = hashlib.md5(payload).hexdigest()
    worker = workers.DownloadWorker([file_hash], str(tmp_path / "output"))
    completed, failed = [], []
    worker.all_done.connect(lambda: completed.append(True))
    worker.item_fail.connect(lambda *args: failed.append(args))
    monkeypatch.setattr(workers.time, "sleep", lambda _seconds: None)

    def transfer(_url):
        if mode == "cancel":
            worker.stop()
        return b"invalid" if mode == "failure" else payload

    monkeypatch.setattr(workers, "http_get", transfer)
    if mode == "mkdir_error":
        (tmp_path / "output").write_bytes(b"file blocks directory")
    worker.run()
    assert getattr(worker, "outcome", None) == {
        "failure": "failed", "cancel": "cancelled", "success": "success", "mkdir_error": "aborted",
    }[mode]
    assert completed == ([True] if mode == "success" else [])
    assert bool(failed) == (mode == "failure")
    if mode == "cancel":
        assert not (tmp_path / "output" / f"{file_hash}.bundle").exists()
