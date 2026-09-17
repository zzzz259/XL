from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from pathlib import Path
from uuid import uuid4

from app.features.preview.item import build_preview_item


def test_preview_item_preserves_resource_identity_and_new_state_metadata():
    QApplication.instance() or QApplication([])
    image = Path.cwd() / f".task5-preview-item-{uuid4().hex}.png"
    image.write_bytes(b"png")
    thumbnail = ""
    resource = {
        "role_id": "10080",
        "skin_key": "stable-skin-key",
        "fingerprint": "resource-fingerprint",
        "is_new": True,
    }

    try:
        item = build_preview_item(str(image), thumbnail, {}, resource=resource)

        data = item.data(Qt.UserRole)
        assert data["role_id"] == "10080"
        assert data["skin_key"] == "stable-skin-key"
        assert data["fingerprint"] == "resource-fingerprint"
        assert data["is_new"] is True
    finally:
        image.unlink(missing_ok=True)
