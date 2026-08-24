import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from app.bootstrap.app_factory import create_features, default_feature_definitions
from app.bootstrap.context import build_app_context


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app
    app.quit()


def test_default_feature_factories_create_isolated_runtime(qapp, tmp_path):
    context = build_app_context(
        base_dir=tmp_path,
        data_dir=tmp_path / "data",
        output_dir=tmp_path / "output",
        tools_dir=tmp_path / "tools",
    )

    features = create_features(context, default_feature_definitions())

    assert [feature.descriptor.key for feature in features] == [
        "versions", "preview", "audio", "character", "importer"
    ]
    assert [feature.descriptor.title for feature in features] == [
        "版本列表", "图片预览", "音频", "角色", "导入AS"
    ]
    assert all(feature.page is not None for feature in features)
    assert all(feature.controller is not None for feature in features)
    assert features[0].status_signal is not None
    assert features[1].progress_signal is not None
    assert features[2].badge_signal is not None
    assert features[3].badge_signal is not None

    for feature in features:
        close = getattr(feature.page, "close", None)
        if close is not None:
            close()
