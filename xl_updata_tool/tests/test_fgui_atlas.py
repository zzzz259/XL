from app.features.preview.fgui_atlas import (
    AtlasSprite,
    PackageItem,
    PackageItemType,
    Rect,
    UIPackage,
    UIPackageTool,
)
from PIL import Image


def _install_test_package(monkeypatch, *, package_name, sprites):
    atlas_item = PackageItem()
    atlas_item.type = PackageItemType.Atlas
    atlas_item.id = "atlas-id"
    atlas_item.file = f"{package_name}_fui_atlas0.png"

    image_item = PackageItem()
    image_item.type = PackageItemType.Image
    image_item.id = "image-id"
    image_item.name = "01"

    def load_package(self, buffer, asset_name_prefix):
        self.name = package_name
        self._items = [atlas_item, image_item]
        self._items_by_id = {image_item.id: image_item, atlas_item.id: atlas_item}
        self._items_by_name = {image_item.name: image_item}
        self._sprites = {}
        for sprite_id, x in sprites:
            sprite = AtlasSprite()
            sprite.atlas = atlas_item
            sprite.rect = Rect(x, 0, 1, 1)
            self._sprites[sprite_id] = sprite
        return True

    monkeypatch.setattr(UIPackage, "load_package", load_package)


def _write_test_atlas(tmp_path, package_name, width):
    Image.new("RGBA", (width, 1), (255, 0, 0, 255)).save(
        tmp_path / f"{package_name}_atlas0.png"
    )


def test_chat_emoji_exports_sprite_ids_including_movieclip_frame_sprites(tmp_path, monkeypatch):
    package_name = "ChatEmoji"
    _install_test_package(
        monkeypatch,
        package_name=package_name,
        sprites=[("image-id", 0), ("dv1q7f_0", 1)],
    )
    source = tmp_path / "ChatEmoji_fui.bytes"
    source.write_bytes(b"package")
    _write_test_atlas(tmp_path, package_name, 2)
    destination = tmp_path / "output"

    UIPackageTool.split_atlas_to_package_dir(
        str(source), str(destination), is_override_exists=False
    )

    assert {path.name for path in destination.glob("*.png")} == {
        "image-id.png",
        "dv1q7f_0.png",
    }


def test_regular_fgui_package_uses_original_sprite_name_without_hash(tmp_path, monkeypatch):
    package_name = "Card"
    _install_test_package(
        monkeypatch,
        package_name=package_name,
        sprites=[("image-id", 0), ("orphan-frame-id_0", 1)],
    )
    source = tmp_path / "Card_fui.bytes"
    source.write_bytes(b"package")
    _write_test_atlas(tmp_path, package_name, 2)
    destination = tmp_path / "output"

    UIPackageTool.split_atlas_to_package_dir(
        str(source), str(destination), is_override_exists=False
    )

    assert {path.name for path in destination.glob("*.png")} == {"01.png"}


def test_uipackage_tool_explicit_package_entry_writes_to_the_given_directory(tmp_path, monkeypatch):
    source = tmp_path / "Card_fui.bytes"
    source.write_bytes(b"package")
    destination = tmp_path / "output" / "Card"

    def load_empty_package(self, buffer, asset_name_prefix):
        return True

    monkeypatch.setattr(UIPackage, "load_package", load_empty_package)
    UIPackageTool.split_atlas_to_package_dir(str(source), str(destination), is_override_exists=False)

    assert (destination / "Card_fui_cut_info.json").is_file()
    assert not (destination / "Card_fui" / "Card_fui_cut_info.json").exists()


def test_uipackage_tool_legacy_call_keeps_subdirectory_when_output_basename_is_package_name(tmp_path, monkeypatch):
    source = tmp_path / "Card_fui.bytes"
    source.write_bytes(b"package")
    destination = tmp_path / "output" / "Card"

    def load_empty_package(self, buffer, asset_name_prefix):
        return True

    monkeypatch.setattr(UIPackage, "load_package", load_empty_package)
    UIPackageTool.split_atlas(str(source), str(destination))

    assert (destination / "Card_fui" / "Card_fui_cut_info.json").is_file()
