from pathlib import Path

from app.bootstrap.app_factory import FeatureDefinition, create_features
from app.bootstrap.context import build_app_context
from app.core.file_utils import atomic_write_bytes, replace_directory
from app.shared.contracts import FeatureDescriptor, FeatureRuntime, ImportResult

CORE_DIR = Path(__file__).parents[1] / "app" / "core"
APP_DIR = Path(__file__).parents[1] / "app"
BOOTSTRAP_DIR = APP_DIR / "bootstrap"
SHARED_DIR = APP_DIR / "shared"
AUDIO_FEATURE_DIR = APP_DIR / "features" / "audio"
CHARACTERS_FEATURE_DIR = APP_DIR / "features" / "characters"
VERSIONS_FEATURE_DIR = APP_DIR / "features" / "versions"
IMPORTER_FEATURE_DIR = APP_DIR / "features" / "importer"
PREVIEW_FEATURE_DIR = APP_DIR / "features" / "preview"


def test_core_layer_does_not_import_qt():
    """Keep the core layer usable in CLI/tests without a Qt runtime."""
    offenders = []
    for path in CORE_DIR.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "PySide6" in text or "PyQt" in text:
            offenders.append(path.name)

    assert offenders == []


def test_legacy_asset_browser_has_explicit_compatibility_entrypoint():
    from app.ui.legacy.asset_browser_entry import open_legacy_asset_browser

    assert callable(open_legacy_asset_browser)


def test_ui_feature_boundaries_have_explicit_entrypoints():
    from app.ui.features.audio_controller import populate_audio_tree
    from app.ui.features.preview_controller import build_preview_item
    from app.ui.views.version_view import create_version_table

    assert callable(populate_audio_tree)
    assert callable(build_preview_item)
    assert callable(create_version_table)


def test_p0_contract_layers_are_framework_free():
    """组合根和共享契约不能把 Qt 依赖带进应用边界。"""
    offenders = []
    for root in (BOOTSTRAP_DIR, SHARED_DIR):
        for path in root.glob("*.py"):
            text = path.read_text(encoding="utf-8")
            if "PySide6" in text or "PyQt" in text:
                offenders.append(path.relative_to(APP_DIR).as_posix())

    assert offenders == []


def test_audio_page_and_service_respect_feature_boundaries():
    page_text = (AUDIO_FEATURE_DIR / "page.py").read_text(encoding="utf-8")
    service_text = (AUDIO_FEATURE_DIR / "service.py").read_text(encoding="utf-8")

    assert "parent._" not in page_text
    assert "controls =" not in page_text
    assert "PySide6" not in service_text


def test_characters_page_and_service_respect_feature_boundaries():
    page_text = (CHARACTERS_FEATURE_DIR / "page.py").read_text(encoding="utf-8")
    service_text = (CHARACTERS_FEATURE_DIR / "service.py").read_text(encoding="utf-8")

    assert "parent._" not in page_text
    assert "controls =" not in page_text
    assert "PySide6" not in service_text


def test_versions_page_and_service_respect_feature_boundaries():
    page_text = (VERSIONS_FEATURE_DIR / "page.py").read_text(encoding="utf-8")
    service_text = (VERSIONS_FEATURE_DIR / "service.py").read_text(encoding="utf-8")

    assert "parent._" not in page_text
    assert "controls =" not in page_text
    assert "PySide6" not in service_text


def test_importer_service_and_specs_respect_feature_boundaries():
    service_text = (IMPORTER_FEATURE_DIR / "service.py").read_text(encoding="utf-8")
    spec_text = (IMPORTER_FEATURE_DIR / "spec.py").read_text(encoding="utf-8")

    assert "PySide6" not in service_text
    assert "PySide6" not in spec_text
    assert "ImportResult" in service_text


def test_main_window_delegates_import_runtime_to_feature_controller():
    main_window_text = (APP_DIR / "ui" / "main_window.py").read_text(encoding="utf-8")

    assert "ImportController" in main_window_text
    assert "ImporterService" in main_window_text
    assert "ImportASWorker" not in main_window_text


def test_preview_service_and_main_window_respect_feature_boundary():
    service_text = (PREVIEW_FEATURE_DIR / "service.py").read_text(encoding="utf-8")
    main_window_text = (APP_DIR / "ui" / "main_window.py").read_text(encoding="utf-8")

    assert "PySide6" not in service_text
    assert "PreviewPage" in main_window_text
    assert "PreviewController" in main_window_text
    assert "PreviewExportWorker" not in main_window_text
    assert "ImageLoadWorker" not in main_window_text


def test_main_window_delegates_audio_runtime_to_feature_controller():
    main_window_text = (APP_DIR / "ui" / "main_window.py").read_text(encoding="utf-8")

    assert "AudioController" in main_window_text
    assert "AudioService" not in main_window_text
    assert "AudioDecryptWorker" not in main_window_text
    assert "QMediaPlayer" not in main_window_text
    assert "self._audio_worker" not in main_window_text
    assert "self.audio_table" not in main_window_text


def test_main_window_delegates_character_runtime_to_feature_controller():
    main_window_text = (APP_DIR / "ui" / "main_window.py").read_text(encoding="utf-8")

    assert "CharacterController" in main_window_text
    assert "CharacterService" in main_window_text
    assert "create_character_view" not in main_window_text
    assert "load_character_data" not in main_window_text
    assert "merge_character_snapshot" not in main_window_text
    assert "self.characters" not in main_window_text
    assert "self.character_table" not in main_window_text


def test_main_window_delegates_version_runtime_to_feature_controller():
    main_window_text = (APP_DIR / "ui" / "main_window.py").read_text(encoding="utf-8")

    assert "VersionController" in main_window_text
    assert "VersionService" in main_window_text
    assert "create_version_table" not in main_window_text
    assert "CheckUpdateThread" not in main_window_text
    assert "DownloadWorker" not in main_window_text
    assert "self._version_checkboxes" not in main_window_text


def test_feature_factory_preserves_registration_order_and_rejects_duplicates(tmp_path):
    context = build_app_context(
        base_dir=tmp_path,
        data_dir=tmp_path / "data",
        output_dir=tmp_path / "output",
        tools_dir=tmp_path / "tools",
    )

    class Page:
        def set_loading(self, loading):
            self.loading = loading

    class Controller:
        def start(self):
            pass

        def stop(self):
            pass

    def make_feature(descriptor):
        return lambda _context: FeatureRuntime(descriptor, Page(), Controller())

    first = FeatureDescriptor("first", "第一项")
    second = FeatureDescriptor("second", "第二项")
    features = create_features(
        context,
        (
            FeatureDefinition(first, make_feature(first)),
            FeatureDefinition(second, make_feature(second)),
        ),
    )

    assert [feature.descriptor.key for feature in features] == ["first", "second"]

    duplicate = FeatureDefinition(first, make_feature(first))
    try:
        create_features(context, (duplicate, duplicate))
    except ValueError as error:
        assert "Duplicate feature key" in str(error)
    else:
        raise AssertionError("duplicate feature keys must be rejected")


def test_import_result_exposes_category_state():
    result = ImportResult(
        categories=frozenset({"lua", "audio"}),
        completed_categories=frozenset({"lua"}),
        failed_categories=frozenset({"audio"}),
        published_outputs=("output/lua/20260824",),
    )

    assert result.has_category("lua")
    assert not result.has_category("character")
    assert not result.succeeded

    complete = ImportResult(
        categories=frozenset({"lua", "audio"}),
        completed_categories=frozenset({"lua", "audio"}),
    )
    assert complete.succeeded


def test_atomic_write_preserves_existing_file_when_transform_fails(tmp_path):
    destination = tmp_path / "asset.bundle"
    destination.write_bytes(b"known-good")

    try:
        atomic_write_bytes(destination, b"new-data", transform=lambda _: False)
    except OSError:
        pass
    else:
        raise AssertionError("transform failure must abort the atomic write")

    assert destination.read_bytes() == b"known-good"
    assert list(tmp_path.glob("*.tmp")) == []


def test_replace_directory_swaps_staged_output(tmp_path):
    destination = tmp_path / "material"
    destination.mkdir()
    (destination / "old.txt").write_text("old", encoding="utf-8")
    source = tmp_path / "staging" / "assets"
    source.mkdir(parents=True)
    (source / "new.txt").write_text("new", encoding="utf-8")

    replace_directory(source, destination)

    assert (destination / "new.txt").read_text(encoding="utf-8") == "new"
    assert not (destination / "old.txt").exists()
