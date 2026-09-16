import os
import hashlib
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.platform import database as db
from app.features.versions.controller import VersionController
from app.features.versions.download_worker import DownloadWorker
from app.features.versions.page import DownloadProgressButton, VersionPage
from app.features.versions.service import VersionService


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _init_version_db(tmp_path):
    db.init_db(str(tmp_path / "versions.db"))
    db.save_version(100, {}, {"data": []})
    db.save_version(200, {}, {"data": []})
    db.save_sub_bundles(100, ["same", "old"])
    db.save_sub_bundles(200, ["same", "new"])


def test_version_page_owns_workspace_table_and_signals(qapp):
    page = VersionPage()

    assert page.objectName() == "viewContainer"
    assert page.table.objectName() == "workspaceTable"
    assert page.table.columnCount() == 8
    assert page.findChild(QObject, "workspaceHeader") is not None

    emitted = []
    page.hover_row_changed.connect(emitted.append)
    page.hover_row_changed.emit(2)
    assert emitted == [2]


def test_version_service_calculates_delta_and_missing_downloads(tmp_path):
    _init_version_db(tmp_path)
    bundles_dir = tmp_path / "bundles"
    bundles_dir.mkdir()
    service = VersionService(bundles_dir)

    assert service.delta_hashes(200) == {"new"}
    sub_bundles, missing = service.missing_downloads(200, delta_only=True)
    assert len(sub_bundles) == 2
    assert missing == ["new"]


def test_version_controller_renders_rows_and_preserves_single_selection(qapp, tmp_path):
    _init_version_db(tmp_path)
    service = VersionService(tmp_path / "bundles")
    page = VersionPage()
    controller = VersionController(page, service)

    versions = service.refresh()
    controller.populate_table(versions, service.delta_map(versions))
    assert page.table.rowCount() == 2
    assert page.table.item(0, 1).data(Qt.UserRole) is not None

    controller._set_version_checked(0, True)
    controller._set_version_checked(1, True)
    assert controller.selected_versions == [versions[1][0]]
    assert "已选择 1" in page.version_summary.text()


def test_version_service_syncs_local_bundle_state(tmp_path):
    _init_version_db(tmp_path)
    bundles_dir = tmp_path / "bundles" / "200"
    bundles_dir.mkdir(parents=True)
    bundle_path = bundles_dir / "new.bundle"
    bundle_path.write_bytes(b"bundle")
    service = VersionService(tmp_path / "bundles")

    service.sync_local(200)
    assert any(row[2] == os.fspath(bundle_path) for row in service.bundles(200))


def test_download_worker_accepts_md5_verified_non_unity_payload(monkeypatch, tmp_path, qapp):
    """Some manifest entries are video payloads rather than UnityFS bundles."""
    payload = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42mp41"
    expected_hash = hashlib.md5(payload).hexdigest()
    monkeypatch.setattr(
        "app.features.versions.download_worker.http_get",
        lambda _url: payload,
    )

    worker = DownloadWorker([expected_hash], str(tmp_path))
    completed = []
    failed = []
    worker.item_done.connect(lambda *args: completed.append(args))
    worker.item_fail.connect(lambda *args: failed.append(args))
    worker.run()

    assert (tmp_path / f"{expected_hash}.bundle").read_bytes() == payload
    assert completed == [(expected_hash, f"{expected_hash}.bundle", str(tmp_path / f"{expected_hash}.bundle"))]
    assert failed == []


def test_download_version_does_not_start_a_second_worker(qapp, tmp_path):
    class ExplodingService:
        bundles_dir = tmp_path

        def missing_downloads(self, *_args):
            raise AssertionError("a second download must not inspect or start another job")

    page = VersionPage()
    controller = VersionController(page, ExplodingService())
    controller._download_worker = SimpleNamespace(isRunning=lambda: True)
    statuses = []
    controller.status_changed.connect(statuses.append)

    controller.download_version(134322036967253022)

    assert statuses[-1] == "已有下载任务正在进行，请等待当前任务完成。"


def test_version_controller_stops_workers_before_window_closes(qapp, tmp_path):
    class Worker:
        def __init__(self):
            self.stopped = False
            self.wait_timeout = None

        def isRunning(self):
            return True

        def stop(self):
            self.stopped = True

        def wait(self, timeout):
            self.wait_timeout = timeout
            return True

    page = VersionPage()
    controller = VersionController(page, SimpleNamespace())
    worker = Worker()
    controller._download_worker = worker

    controller.close()

    assert worker.stopped is True
    assert worker.wait_timeout == 30000
    assert controller._download_worker is None


def test_download_progress_button_shows_percent_and_emits_cancel(qapp):
    button = DownloadProgressButton()
    clicked = []
    button.clicked.connect(lambda: clicked.append(True))
    button.set_progress(5, 10)

    QTest.mouseClick(button, Qt.LeftButton)

    assert button.value() == 50
    assert button.format() == "取消下载 50%"
    assert clicked == [True]

    button.set_progress(150, 200)
    assert button.value() == 75
    assert button.format() == "取消下载 75%"

    button.set_progress(5, 0)
    assert button.value() == 0
    assert button.format() == "取消下载 0%"


def test_version_page_checking_animation_resets_header(qapp):
    page = VersionPage()
    messages = []
    page.checking_message_changed.connect(messages.append)

    page.set_checking(True)
    assert page.workspace_title.text() == "检查更新中"
    page._advance_check_animation()
    assert page.workspace_title.text() == "检查更新中。"

    page.set_checking(False)
    assert page.workspace_title.text() == "版本工作区"
    assert page.workspace_description.text() == "管理版本、下载状态与增量关系"
    assert messages == ["检查更新中", "检查更新中。"]


def test_download_ui_tracks_progress_filename_and_cancel(monkeypatch, qapp, tmp_path):
    _init_version_db(tmp_path)

    class FakeDownloadWorker(QObject):
        progress = Signal(str, int, int)
        item_done = Signal(str, str, str)
        item_fail = Signal(str, str)
        all_done = Signal()
        error = Signal(str)

        def __init__(self, hashes, output_dir):
            super().__init__()
            self.hashes = hashes
            self.output_dir = output_dir
            self.running = False
            self.stopped = False

        def start(self):
            self.running = True

        def isRunning(self):
            return self.running

        def stop(self):
            self.stopped = True
            self.running = False

    monkeypatch.setattr(
        "app.features.versions.controller.DownloadWorker", FakeDownloadWorker
    )
    service = VersionService(tmp_path / "bundles")
    page = VersionPage()
    controller = VersionController(page, service)
    controller.populate_table(service.refresh(), service.delta_map(service.refresh()))
    statuses = []
    controller.status_changed.connect(statuses.append)

    controller.download_version(200, delta_only=True)
    worker = controller._download_worker
    worker.progress.emit("new", 3, 10)

    progress_button = controller._download_controls[200][True]
    assert isinstance(progress_button, DownloadProgressButton)
    assert progress_button.format() == "取消下载 30%"
    assert controller._download_controls[200][False].isEnabled() is False
    assert controller._delete_buttons[200].isEnabled() is False
    assert controller._status_items[200].text() == "下载中 (3/10)"
    assert statuses[-1] == "增量下载 3/10 · new.bundle"

    QTest.mouseClick(progress_button, Qt.LeftButton)

    assert worker.stopped is True


def test_check_update_locks_action_and_animates_page_title(monkeypatch, qapp, tmp_path):
    _init_version_db(tmp_path)

    class FakeCheckThread(QObject):
        finished = Signal(object, object, object, object)
        error = Signal(str)

        def __init__(self, *_args):
            super().__init__()
            self.running = False

        def start(self):
            self.running = True

        def isRunning(self):
            return self.running

        def wait(self, _timeout):
            return True

        def requestInterruption(self):
            self.running = False

    monkeypatch.setattr(
        "app.features.versions.controller.CheckUpdateThread", FakeCheckThread
    )
    service = VersionService(tmp_path / "bundles")
    page = VersionPage()
    controller = VersionController(page, service)
    statuses = []
    checking_states = []
    controller.status_changed.connect(statuses.append)
    controller.check_state_changed.connect(checking_states.append)

    controller.check_update(notify_errors=False)
    first_title = page.workspace_title.text()
    page._advance_check_animation()
    second_title = page.workspace_title.text()

    assert first_title == "检查更新中"
    assert second_title == "检查更新中。"
    assert checking_states == [True]
    assert statuses[-1] == "检查更新中。"

    controller._check_thread.error.emit("network")

    assert checking_states == [True, False]
    assert page.workspace_title.text() == "版本工作区"
