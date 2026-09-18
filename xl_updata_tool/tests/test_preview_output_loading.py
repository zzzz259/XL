import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app.features.preview.controller import PreviewController
from app.features.preview.page import PreviewPage
from app.features.preview.service import PreviewService


def test_preview_load_uses_published_index_instead_of_source_discovery(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    page = PreviewPage()
    service = PreviewService(tmp_path / "material", tmp_path / "output" / "character")
    controller = PreviewController(page, service)
    calls = []
    monkeypatch.setattr(service, "discover_preview_resources", lambda: calls.append(True))

    controller.load()
    app.processEvents()
    controller.cancel_export()

    assert calls == []
    assert page.character_output_path.text() == str(tmp_path / "output" / "character")
    page.close()
