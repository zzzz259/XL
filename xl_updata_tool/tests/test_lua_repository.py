from app.platform.lua_repository import (
    cleanup_lua_staging,
    has_character_sources,
    latest_lua_version,
    list_lua_versions,
    publish_lua_version,
    should_auto_parse,
    version_directory,
)


def test_publish_lua_version_isolates_versions_and_filters_intermediate_files(tmp_path):
    source = tmp_path / "material" / "assets" / "lua"
    source.mkdir(parents=True)
    (source / "BaseCard.lua").write_text("card-v1", encoding="utf-8")
    (source / "BaseWord_cn.lua").write_text("word-v1", encoding="utf-8")
    (source / "intermediate.lua.bytes").write_bytes(b"not-final")
    output = tmp_path / "output" / "lua"

    path, count, ready = publish_lua_version(source, output, 100)

    assert path == version_directory(output, 100)
    assert count == 2
    assert ready is True
    assert (output / "100" / "BaseCard.lua").read_text(encoding="utf-8") == "card-v1"
    assert not (output / "100" / "intermediate.lua.bytes").exists()

    (source / "BaseCard.lua").write_text("card-v2", encoding="utf-8")
    publish_lua_version(source, output, 200)

    assert (output / "100" / "BaseCard.lua").read_text(encoding="utf-8") == "card-v1"
    assert (output / "200" / "BaseCard.lua").read_text(encoding="utf-8") == "card-v2"
    assert list_lua_versions(output) == [100, 200]
    assert latest_lua_version(output) == 200


def test_cleanup_lua_staging_keeps_empty_directory(tmp_path):
    material = tmp_path / "material"
    lua_dir = material / "assets" / "lua"
    lua_dir.mkdir(parents=True)
    (lua_dir / "temporary.lua").write_text("temp", encoding="utf-8")

    assert cleanup_lua_staging(material) is True
    assert lua_dir.is_dir()
    assert list(lua_dir.iterdir()) == []
    assert has_character_sources(lua_dir) is False


def test_auto_parse_requires_latest_version_and_character_base_files(tmp_path):
    lua_dir = tmp_path / "lua"
    lua_dir.mkdir()
    (lua_dir / "BaseCard.lua").write_text("card", encoding="utf-8")
    (lua_dir / "BaseWord_cn.lua").write_text("word", encoding="utf-8")

    assert should_auto_parse(200, 200, lua_dir) is True
    assert should_auto_parse(100, 200, lua_dir) is False
    (lua_dir / "BaseCard.lua").unlink()
    assert should_auto_parse(200, 200, lua_dir) is False
