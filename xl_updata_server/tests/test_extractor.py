import pytest

from server_app.extractor import (
    _script_bytes,
    is_lua_asset_name,
    repair_bundle_header,
    safe_role_name,
)


def test_safe_role_name_rejects_path_traversal():
    assert safe_role_name("../../cardspine_10001") == "cardspine_10001"
    assert safe_role_name("cardspine_10001_1") == "cardspine_10001_1"


def test_repair_bundle_header_strips_custom_prefix(tmp_path):
    bundle = tmp_path / "sample.bundle"
    bundle.write_bytes(b"custom-prefix" + b"UnityFS" + b"payload")

    repair_bundle_header(bundle)

    assert bundle.read_bytes() == b"UnityFSpayload"


def test_repair_bundle_header_rejects_non_bundle(tmp_path):
    bundle = tmp_path / "not-a-bundle"
    bundle.write_bytes(b"invalid")

    with pytest.raises(ValueError, match="UnityFS"):
        repair_bundle_header(bundle)


def test_is_lua_asset_name_accepts_unity_object_name_without_payload_suffix():
    assert is_lua_asset_name("BaseCard.lua") is True
    assert is_lua_asset_name("BaseCard.lua.bytes") is True
    assert is_lua_asset_name("BaseMonsterPart1120.lua") is False


def test_script_bytes_preserves_surrogateescaped_binary_content():
    class Asset:
        m_Script = "Lua\udc93"

    assert _script_bytes(Asset()) == b"Lua\x93"
