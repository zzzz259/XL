from app.features.preview.fgui_atlas import UIPackage, UIPackageTool


def test_uipackage_tool_writes_to_an_explicit_package_directory(tmp_path, monkeypatch):
    source = tmp_path / "Card_fui.bytes"
    source.write_bytes(b"package")
    destination = tmp_path / "output" / "Card"

    def load_empty_package(self, buffer, asset_name_prefix):
        return True

    monkeypatch.setattr(UIPackage, "load_package", load_empty_package)
    UIPackageTool.split_atlas(str(source), str(destination), is_override_exists=False)

    assert (destination / "Card_fui_cut_info.json").is_file()
    assert not (destination / "Card_fui" / "Card_fui_cut_info.json").exists()


def test_uipackage_tool_keeps_legacy_subdirectory_for_an_ordinary_output_dir(tmp_path, monkeypatch):
    source = tmp_path / "Card_fui.bytes"
    source.write_bytes(b"package")
    destination = tmp_path / "output"

    def load_empty_package(self, buffer, asset_name_prefix):
        return True

    monkeypatch.setattr(UIPackage, "load_package", load_empty_package)
    UIPackageTool.split_atlas(str(source), str(destination))

    assert (destination / "Card_fui" / "Card_fui_cut_info.json").is_file()
