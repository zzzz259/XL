from app.core.album_map import build_album_map


def test_album_map_keeps_full_bank_path_and_filename_aliases(tmp_path, monkeypatch):
    (tmp_path / "BaseWord_cn.lua").write_text("words", encoding="utf-8")
    (tmp_path / "BaseSound.lua").write_text(
        'BaseSound = {\n'
        '[70160068] = {\n'
        ' bank = "bank:/bgm/bgm_system/bgm_system_event_33"\n'
        '}\n'
        '}',
        encoding="utf-8",
    )
    (tmp_path / "BaseSoundChapter.lua").write_text(
        'BaseSoundChapter = {\n'
        '[70150005] = { name = function() return T(80880005) end, '
        'child_ids = { 70160068 }, sort = 5 }\n'
        '}',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "app.core.album_map.parse_word_file",
        lambda _path: {80880005: "第五专辑"},
    )

    result = build_album_map(str(tmp_path))

    assert result["bgm/bgm_system/bgm_system_event_33"] == "第五专辑"
    assert result["bgm_system_event_33"] == "第五专辑"
