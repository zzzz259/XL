import os
import json

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app.features.preview.controller import PreviewController
from app.features.preview.page import PreviewPage
from app.features.preview.service import PreviewService
from app.features.preview.portrait_config.cache import PortraitSceneCache
from app.features.preview.portrait_config.model import PortraitScene, SceneDiagnostic


class _Signal:
    def connect(self, _handler):
        pass


class _ImageWorker:
    created = 0

    def __init__(self, *_args, **_kwargs):
        type(self).created += 1
        self.progress = _Signal()
        self.image_loaded = _Signal()
        self.finished_loading = _Signal()
        self.finished = _Signal()

    def start(self):
        pass

    def cancel(self):
        pass

    def wait(self, _timeout):
        return True


def test_preview_load_uses_published_index_instead_of_source_discovery(monkeypatch, tmp_path):
    _app = QApplication.instance() or QApplication([])
    page = PreviewPage()
    service = PreviewService(tmp_path / "material", tmp_path / "output" / "character")
    controller = PreviewController(page, service)
    calls = []
    monkeypatch.setattr(service, "discover_preview_resources", lambda: calls.append(True))

    controller.load()
    _app.processEvents()
    controller.close()

    assert calls == []
    assert page.character_output_path.text() == str(tmp_path / "output" / "character")
    page.close()


def test_preview_load_normalises_legacy_internal_skin_display_names(tmp_path):
    output_root = tmp_path / "output"
    output_root.mkdir(parents=True)
    (output_root / "preview_index.json").write_text(
        json.dumps(
            {
                "version": 1,
                "spine": [
                    {
                        "character_id": "10080",
                        "resource_family": "battlespine",
                        "records": [
                            {
                                "character_id": "10080",
                                "source_skel": "E:/material/battlespine_10080_2.skel.bytes",
                                "atlas_path": "E:/material/battlespine_10080_2.atlas.txt",
                                "skin_name": "default",
                                "attachment_fingerprint": "",
                                "display_name": "default",
                                "status": "ready",
                                "identity_fingerprint": "identity",
                                "fingerprint_kind": "source_skin_identity",
                                "diagnostic": "",
                                "resource_family": "battlespine",
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    catalog = PreviewService(tmp_path / "material", output_root / "character").load_published_preview_resources()

    assert next(iter(catalog.skins.values())).display_name == "皮肤 2"


def test_preview_load_is_cached_until_forced_reload(monkeypatch, tmp_path):
    _app = QApplication.instance() or QApplication([])
    monkeypatch.setattr("app.features.preview.controller.ImageLoadWorker", _ImageWorker)
    _ImageWorker.created = 0
    page = PreviewPage()
    controller = PreviewController(
        page,
        PreviewService(tmp_path / "material", tmp_path / "output" / "character"),
    )

    assert controller.load() is True
    assert controller.load() is False
    assert _ImageWorker.created == 1
    assert controller.load(force=True) is True
    assert _ImageWorker.created == 2
    controller.close()
    page.close()


def test_preview_preload_prepares_views_without_starting_image_worker(monkeypatch, tmp_path):
    _app = QApplication.instance() or QApplication([])
    monkeypatch.setattr("app.features.preview.controller.ImageLoadWorker", _ImageWorker)
    _ImageWorker.created = 0
    page = PreviewPage()
    controller = PreviewController(
        page,
        PreviewService(tmp_path / "material", tmp_path / "output" / "character"),
    )

    assert controller.preload_index() is True
    assert controller.preload_index() is False
    assert _ImageWorker.created == 0
    controller.close()
    page.close()


def test_preview_service_loads_versioned_scene_manifests_without_source_discovery(tmp_path, monkeypatch):
    data_root = tmp_path / "data"
    scene = PortraitScene(
        game_version="test", version_timestamp="20261012", entry_config_id="12002183",
        character_id="10118", skin_id="12002183", prefab=None,
        source_bundles=("bundle-a",), parts=(),
        diagnostics=(SceneDiagnostic("missing_asset", "atlas unavailable"),),
        prefab_container="assets/art/models/cardspine/prefab/cardspine_10118_3.prefab",
    )
    PortraitSceneCache(
        data_root / "cache" / "portrait-scenes", parser_version="3",
    ).store_snapshot(scene)
    service = PreviewService(data_root / "material", tmp_path / "output" / "character")
    monkeypatch.setattr(
        service, "discover_preview_resources",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not scan raw assets")),
    )

    catalog = service.load_published_preview_resources()

    assert len(catalog.portrait_scenes) == 1
    assert catalog.portrait_scenes[0].skin_id == "12002183"
    assert catalog.portrait_scenes[0].diagnostics[0].message == "atlas unavailable"
