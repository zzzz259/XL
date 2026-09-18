import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app.features.preview.controller import PreviewController
from app.features.preview.material_catalog import GameMaterialCatalog, GameMaterialRecord
from app.features.preview.page import PreviewPage
from app.features.preview.resource_model import PreviewResourceCatalog, SpineSkinRecord, skin_key
from app.features.preview.resource_state import PreviewResourceState
from app.features.preview.service import PreviewService


def test_mark_all_read_refreshes_spine_and_material_views_immediately(tmp_path):
    _app = QApplication.instance() or QApplication([])
    output = tmp_path / "output"
    service = PreviewService(tmp_path / "material", output / "character")
    page = PreviewPage()
    controller = PreviewController(page, service)
    record = SpineSkinRecord("10080", "hero.skel", "hero.atlas", "default", "fp", "default", "ready")
    catalog = PreviewResourceCatalog.from_records([record])
    service._resource_state = PreviewResourceState(output / "preview_state.json")
    page.set_spine_catalog(catalog, service.resource_state)
    material_dir = output / "game_material" / "burst-head"
    material_dir.mkdir(parents=True)
    head = material_dir / "10080.png"
    head.write_bytes(b"head")
    material_catalog = GameMaterialCatalog(
        burst_heads=(GameMaterialRecord("burst-head", str(head), "10080", "head-fingerprint"),),
        atlases=(),
        unmatched=(),
    )
    page.set_game_material_catalog(material_catalog, service.resource_state)
    service.load_published_preview_resources = lambda: catalog
    service.discover_processed_game_materials = lambda: material_catalog

    controller.mark_all_read()

    assert not service.resource_state.is_new(skin_key(record))
    assert page.spine_tree.topLevelItem(0).text(1) == ""
    assert page.material_tree.topLevelItem(0).text(1) == ""
    page.close()
