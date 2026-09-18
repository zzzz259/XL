import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QListWidgetItem

from app.features.preview.page import PreviewPage


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_thumbnail_page_controls_slice_without_clearing_selection(qapp):
    page = PreviewPage()
    page._thumbnail_page_size = 2
    for index in range(5):
        item = QListWidgetItem(f"image-{index}")
        item.setData(256, {"png": f"output/10080/{index}.png", "_filter_visible": True})
        page.image_list.addItem(item)
    page.refresh_thumbnail_pagination()
    page.image_list.item(0).setSelected(True)

    page.set_thumbnail_page(1)

    assert page.thumbnail_page == 1
    assert page.thumbnail_page_count == 3
    assert page.image_list.item(0).isSelected()
    assert page.image_list.item(0).isHidden()
    assert not page.image_list.item(2).isHidden()
    assert page.image_list.count() == 5
    page.close()

