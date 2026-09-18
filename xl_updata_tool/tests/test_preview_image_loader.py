import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from app.features.preview.workers.image_loader import ImageLoadWorker


def test_thumbnail_builder_accepts_thread_safe_qimage(qapp=None):
    app = QApplication.instance() or QApplication([])
    image = QImage(24, 24, QImage.Format.Format_ARGB32)
    image.fill(QColor("#336699"))

    thumbnail = ImageLoadWorker(None)._create_thumbnail(image, 16)

    assert not thumbnail.isNull()
    assert thumbnail.size().width() == 16
    app.processEvents()


def test_explicit_empty_image_paths_do_not_scan_directory(tmp_path):
    (tmp_path / "should-not-load.png").write_bytes(b"not an image")
    worker = ImageLoadWorker(str(tmp_path), 16, image_paths=[])
    loaded = []
    worker.finished_loading.connect(loaded.append)

    worker.run()

    assert loaded == [[]]
