import os

import pytest
from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication

from app.features.audio.page import AudioPage
from app.features.audio.service import AudioCatalogIndex, AudioService
from app.features.audio.tree import (
    populate_audio_directory,
    populate_audio_tree as feature_populate_audio_tree,
    populate_audio_tree_roots,
)
from app.ui.features.audio_controller import populate_audio_tree as legacy_populate_audio_tree


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_audio_page_owns_controls_and_exposes_semantic_signals(qapp):
    page = AudioPage()

    assert page.objectName() == "viewContainer"
    assert page.audio_table.objectName() == "audioTree"
    assert page.audio_play_btn.objectName() == "audioPlayButton"
    assert page.findChild(QObject, "pageHeader") is not None
    assert page.findChild(QObject, "pageCommandBar") is not None

    emitted = []
    page.refresh_requested.connect(lambda: emitted.append("refresh"))
    page.refresh_requested.emit()
    assert emitted == ["refresh"]


def test_audio_service_caches_and_invalidates_catalog(tmp_path):
    audio_dir = tmp_path / "audio" / "album-a"
    audio_dir.mkdir(parents=True)
    audio_file = audio_dir / "track.wav"
    audio_file.write_bytes(b"RIFF")

    service = AudioService(tmp_path)
    first = service.load_catalog()
    second = service.load_catalog()
    assert first is second
    assert [item["name"] for item in first] == [os.path.join("album-a", "track.wav")]

    (tmp_path / "audio" / "album-a" / "track-2.wav").write_bytes(b"RIFF2")
    assert len(service.load_catalog()) == 1
    service.invalidate()
    assert len(service.load_catalog()) == 2

    assert service.mark_all_read() is True
    assert all(item["unread"] is False for item in service.load_catalog())


def test_audio_tree_legacy_import_is_compatibility_alias():
    assert legacy_populate_audio_tree is feature_populate_audio_tree


def test_audio_catalog_index_supports_layered_lazy_tree(qapp):
    files = [
        {"name": "album/第五专辑/event.wav", "dir": "album/第五专辑", "ext": "WAV", "size": 1, "unread": True},
        {"name": "album/第六专辑/event.wav", "dir": "album/第六专辑", "ext": "WAV", "size": 1},
        {"name": "voice/001/cn/line.wav", "dir": "voice/001/cn", "ext": "WAV", "size": 1},
    ]
    index = AudioCatalogIndex(files)
    page = AudioPage()
    table = page.audio_table
    roots = populate_audio_tree_roots(table, index, AudioService.format_size)

    assert [root.text(0) for root in roots] == ["album", "voice"]
    assert roots[0].text(5) == "新"
    assert roots[0].childCount() == 1  # 仅保留懒加载占位节点

    populate_audio_directory(roots[0], index, AudioService.format_size)
    assert [roots[0].child(i).text(0) for i in range(roots[0].childCount())] == [
        "第五专辑",
        "第六专辑",
    ]
    assert roots[0].child(0).childCount() == 1  # 专辑曲目仍未构造
    page.close()
