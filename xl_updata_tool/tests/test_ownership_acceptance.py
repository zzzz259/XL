"""P6-P8 架构入口和 Feature ownership 验收。"""

from pathlib import Path


APP_DIR = Path(__file__).parents[1] / "app"
DOCS_DIR = Path(__file__).parents[1] / "docs"


def test_platform_and_shared_qt_stable_entrypoints_import():
    from app.platform.database import init_db
    from app.platform.diagnostics import logger, timed
    from app.platform.files import atomic_write_bytes, replace_directory
    from app.platform.paths import get_base_dir, get_data_dir
    from app.platform.processes import run_external_process
    from app.shared.qt.chrome import create_page_header
    from app.shared.qt.tokens import ACCENT, get_color

    assert callable(init_db)
    assert logger is not None
    assert callable(timed)
    assert callable(atomic_write_bytes)
    assert callable(replace_directory)
    assert callable(get_base_dir)
    assert callable(get_data_dir)
    assert callable(run_external_process)
    assert callable(create_page_header)
    assert ACCENT
    assert callable(get_color)


def test_shell_uses_platform_entrypoints_for_infrastructure():
    text = (APP_DIR / "ui" / "main_window.py").read_text(encoding="utf-8")

    assert "from app.platform import database as db" in text
    assert "from app.platform.diagnostics import logger" in text
    assert "from app.platform.paths import" in text
    assert "from app.core.database" not in text
    assert "from app.core.logger" not in text
    assert "from app.core.path_utils" not in text


def test_feature_runtime_files_are_qt_free_where_they_are_services():
    feature_dirs = (
        APP_DIR / "features" / "audio",
        APP_DIR / "features" / "characters",
        APP_DIR / "features" / "versions",
        APP_DIR / "features" / "importer",
        APP_DIR / "features" / "preview",
    )

    offenders = []
    for feature_dir in feature_dirs:
        for path in feature_dir.glob("service.py"):
            if "PySide6" in path.read_text(encoding="utf-8"):
                offenders.append(path.relative_to(APP_DIR).as_posix())

    assert offenders == []


def test_legacy_modules_are_explicit_compatibility_entries():
    export_text = (APP_DIR / "ui" / "features" / "export_controller.py").read_text(
        encoding="utf-8"
    )
    worker_text = (APP_DIR / "ui" / "workers" / "preview_export.py").read_text(
        encoding="utf-8"
    )

    assert "兼容" in export_text or "legacy" in export_text.lower()
    assert "app.features.preview" in worker_text


def test_architecture_docs_define_platform_and_ownership_boundaries():
    ownership = (DOCS_DIR / "代码所有权与边界.md").read_text(encoding="utf-8")
    guide = (DOCS_DIR / "开发文档指南.md").read_text(encoding="utf-8")

    assert "Platform" in ownership
    assert "Shared" in ownership
    assert "P6" in guide
    assert "P8" in guide
