from pathlib import Path

from app.bootstrap.app_factory import FeatureDefinition, create_features
from app.bootstrap.context import build_app_context
from app.bootstrap.runtime import FeatureRuntimeRegistry
from app.platform.files import atomic_write_bytes, replace_directory
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


def test_production_entrypoint_builds_runtime_before_shell():
    main_text = (Path(__file__).parents[1] / "main.py").read_text(encoding="utf-8")
    shell_text = (APP_DIR / "ui" / "main_window.py").read_text(encoding="utf-8")

    assert "build_app_context" in main_text
    assert "create_application_runtime" in main_text
    assert main_text.index("configure_logging") < main_text.index("QApplication")
    assert main_text.index("create_application_runtime") < main_text.index("MainWindow(")
    assert "from app.features." not in shell_text


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


def test_audio_processing_is_qt_free_and_feature_worker_does_not_use_legacy_worker():
    processing_text = (AUDIO_FEATURE_DIR / "processing.py").read_text(encoding="utf-8")
    worker_text = (AUDIO_FEATURE_DIR / "worker.py").read_text(encoding="utf-8")
    legacy_text = (APP_DIR / "ui" / "workers" / "audio_decrypt.py").read_text(encoding="utf-8")

    assert "PySide6" not in processing_text
    assert "app.ui.workers.audio_decrypt" not in worker_text
    assert "AudioDecryptProcessor" in worker_text
    assert "app.features.audio.worker" in legacy_text


def test_audio_and_preview_domain_implementations_live_in_feature_directories():
    audio_files = ["audio_library.py", "audio_repository.py", "album_map.py"]
    preview_files = ["catalog.py", "prefab_parser.py"]
    for filename in audio_files:
        text = (AUDIO_FEATURE_DIR / filename).read_text(encoding="utf-8")
        assert "PySide6" not in text
        assert len(text) > 100
    for filename in preview_files:
        text = (PREVIEW_FEATURE_DIR / filename).read_text(encoding="utf-8")
        assert "PySide6" not in text
        assert len(text) > 100

    assert "app.features.audio.audio_library" in (CORE_DIR / "audio_library.py").read_text(encoding="utf-8")
    assert "app.features.preview.catalog" in (CORE_DIR / "preview_catalog.py").read_text(encoding="utf-8")


def test_versions_domain_implementations_live_in_feature_directory():
    service_text = (VERSIONS_FEATURE_DIR / "service.py").read_text(encoding="utf-8")
    implementation_names = (
        "version_cleanup.py",
        "version_data.py",
        "version_download.py",
        "version_manager.py",
        "version_update.py",
        "local_bundle_sync.py",
        "seed_versions.py",
    )
    assert "app.core.version_" not in service_text
    assert "app.core.local_bundle_sync" not in service_text
    assert "app.core.seed_versions" not in service_text
    for filename in implementation_names:
        text = (VERSIONS_FEATURE_DIR / filename).read_text(encoding="utf-8")
        assert "PySide6" not in text
        assert len(text) > 100
    assert "app.features.versions.version_manager" in (CORE_DIR / "version_manager.py").read_text(encoding="utf-8")


def test_bundle_boundaries_match_consuming_domains():
    importer_service = (IMPORTER_FEATURE_DIR / "service.py").read_text(encoding="utf-8")
    importer_processing = (IMPORTER_FEATURE_DIR / "processing.py").read_text(encoding="utf-8")
    versions_data = (VERSIONS_FEATURE_DIR / "version_data.py").read_text(encoding="utf-8")
    versions_seed = (VERSIONS_FEATURE_DIR / "seed_versions.py").read_text(encoding="utf-8")
    platform_parser = (APP_DIR / "platform" / "bundle_parser.py").read_text(encoding="utf-8")

    assert "app.core.bundle_selector" not in importer_service
    assert "app.core.bundle_parser" not in importer_processing
    assert "app.core.bundle_parser" not in versions_data
    assert "app.core.bundle_parser" not in versions_seed
    assert "PySide6" not in platform_parser
    assert "app.platform.bundle_parser" in (CORE_DIR / "bundle_parser.py").read_text(encoding="utf-8")
    assert "app.features.importer.bundle_selector" in (CORE_DIR / "bundle_selector.py").read_text(encoding="utf-8")
    assert "app.features.versions.bundle_manager" in (CORE_DIR / "bundle_manager.py").read_text(encoding="utf-8")


def test_character_domain_implementations_live_in_feature_directory():
    service_text = (CHARACTERS_FEATURE_DIR / "service.py").read_text(encoding="utf-8")
    implementation_names = ("cache.py", "presenter.py", "profile.py", "repository.py")
    assert "app.core.character_cache" not in service_text
    assert "app.core.character_presenter" not in service_text
    assert "app.core.character_profile" not in service_text
    assert "app.core.character_repository" not in service_text
    for filename in implementation_names:
        text = (CHARACTERS_FEATURE_DIR / filename).read_text(encoding="utf-8")
        assert "PySide6" not in text
        assert len(text) > 100
    assert "app.features.characters.repository" in (CORE_DIR / "character_repository.py").read_text(encoding="utf-8")


def test_platform_implementations_do_not_depend_on_core_compatibility_modules():
    platform_dir = APP_DIR / "platform"
    implementation_names = (
        "database.py", "files.py", "paths.py", "processes.py", "downloader.py",
        "logger.py", "logging_context.py", "task_context.py", "environment.py",
        "crash_reporter.py", "runtime_config.py",
    )
    forbidden = (
        "app.core.database", "app.core.file_utils", "app.core.path_utils",
        "app.core.process_runner", "app.core.downloader", "app.core.logger",
        "app.core.logging_context", "app.core.task_context", "app.core.environment",
        "app.core.crash_reporter", "app.core.runtime_config",
    )
    for filename in implementation_names:
        text = (platform_dir / filename).read_text(encoding="utf-8")
        assert not any(module in text for module in forbidden), filename


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


def test_importer_processing_is_qt_free_and_feature_worker_does_not_use_legacy_worker():
    processing_text = (IMPORTER_FEATURE_DIR / "processing.py").read_text(encoding="utf-8")
    worker_text = (IMPORTER_FEATURE_DIR / "worker.py").read_text(encoding="utf-8")
    legacy_text = (APP_DIR / "ui" / "workers" / "import_as.py").read_text(encoding="utf-8")

    assert "PySide6" not in processing_text
    assert "app.ui.workers.import_as" not in worker_text
    assert "ImportProcessor" in worker_text
    assert "app.features.importer.worker" in legacy_text


def test_main_window_delegates_import_runtime_to_feature_controller():
    main_window_text = (APP_DIR / "ui" / "main_window.py").read_text(encoding="utf-8")

    assert 'self._features["importer"]' in main_window_text
    assert "create_application_runtime" in main_window_text
    assert "ImportASWorker" not in main_window_text


def test_preview_service_and_main_window_respect_feature_boundary():
    service_text = (PREVIEW_FEATURE_DIR / "service.py").read_text(encoding="utf-8")
    main_window_text = (APP_DIR / "ui" / "main_window.py").read_text(encoding="utf-8")

    assert "PySide6" not in service_text
    assert 'self._features["preview"]' in main_window_text
    assert "create_application_runtime" in main_window_text
    assert "PreviewExportWorker" not in main_window_text
    assert "ImageLoadWorker" not in main_window_text


def test_preview_export_and_compatibility_entrypoints_are_feature_owned():
    main_window_text = (APP_DIR / "ui" / "main_window.py").read_text(encoding="utf-8")
    page_text = (PREVIEW_FEATURE_DIR / "page.py").read_text(encoding="utf-8")
    controller_text = (PREVIEW_FEATURE_DIR / "controller.py").read_text(encoding="utf-8")
    legacy_text = (APP_DIR / "ui" / "features" / "export_controller.py").read_text(encoding="utf-8")
    feature_export_text = (PREVIEW_FEATURE_DIR / "export_controller.py").read_text(encoding="utf-8")

    assert 'self._features["preview"]' in main_window_text
    assert "preview_controller" in main_window_text
    assert "create_preview_view" not in page_text
    assert "self.controls" not in page_text
    assert "show_context_menu" in controller_text
    assert "open_item" in controller_text
    assert "preview_controls" not in main_window_text
    assert "app.features.preview.export_controller" in legacy_text
    assert "parent._" not in feature_export_text
    assert "CompositeExportWorker" in feature_export_text
    assert "BatchExportWorker" in feature_export_text


def test_main_window_delegates_audio_runtime_to_feature_controller():
    main_window_text = (APP_DIR / "ui" / "main_window.py").read_text(encoding="utf-8")

    assert 'self._features["audio"]' in main_window_text
    assert "from app.features.audio" not in main_window_text
    assert "AudioService" not in main_window_text
    assert "AudioDecryptWorker" not in main_window_text
    assert "QMediaPlayer" not in main_window_text
    assert "self._audio_worker" not in main_window_text
    assert "self.audio_table" not in main_window_text


def test_main_window_delegates_character_runtime_to_feature_controller():
    main_window_text = (APP_DIR / "ui" / "main_window.py").read_text(encoding="utf-8")

    assert 'self._features["character"]' in main_window_text
    assert "CharacterController" not in main_window_text
    assert "CharacterService" not in main_window_text
    assert "create_character_view" not in main_window_text
    assert "load_character_data" not in main_window_text
    assert "merge_character_snapshot" not in main_window_text
    assert "self.characters" not in main_window_text
    assert "self.character_table" not in main_window_text


def test_main_window_delegates_version_runtime_to_feature_controller():
    main_window_text = (APP_DIR / "ui" / "main_window.py").read_text(encoding="utf-8")

    assert 'self._features["versions"]' in main_window_text
    assert "VersionController" not in main_window_text
    assert "VersionService" not in main_window_text
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


def test_feature_runtime_registry_binds_generic_ports_and_lifecycle():
    class Signal:
        def __init__(self):
            self.handlers = []

        def connect(self, handler):
            self.handlers.append(handler)

        def emit(self, *args):
            for handler in self.handlers:
                handler(*args)

    class Page:
        def __init__(self):
            self.visible = None

        def set_visible(self, visible):
            self.visible = visible

    class Controller:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    first_page, second_page = Page(), Page()
    first_controller, second_controller = Controller(), Controller()
    status, progress, badges = Signal(), Signal(), Signal()
    features = (
        FeatureRuntime(
            FeatureDescriptor("first", "第一项"), first_page, first_controller,
            status_signal=status, progress_signal=progress, badge_signal=badges,
        ),
        FeatureRuntime(
            FeatureDescriptor("second", "第二项"), second_page, second_controller,
        ),
    )
    registry = FeatureRuntimeRegistry(features)

    received = []
    registry.bind_status(lambda value: received.append(("status", value)))
    registry.bind_progress(lambda *value: received.append(("progress", value)))
    registry.bind_badge(lambda: received.append(("badge",)))
    registry.activate("second")
    status.emit("ready")
    progress.emit(1, 2, "loading")
    badges.emit()
    registry.close()

    assert registry.keys() == ("first", "second")
    assert first_page.visible is False and second_page.visible is True
    assert received == [("status", "ready"), ("progress", (1, 2, "loading")), ("badge",)]
    assert first_controller.closed and second_controller.closed
