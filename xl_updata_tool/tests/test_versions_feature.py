import os

import pytest
from PySide6.QtCore import QObject, Qt
from PySide6.QtWidgets import QApplication

from app.platform import database as db
from app.features.versions.controller import VersionController
from app.features.versions.page import VersionPage
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
