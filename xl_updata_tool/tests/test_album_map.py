import json

from app.features.audio.album_map import audit_bgm_exports, build_album_bank_map, build_album_map


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
        "app.features.audio.album_map.parse_word_file",
        lambda _path: {80880005: "第五专辑"},
    )

    result = build_album_map(str(tmp_path))

    assert result["bgm/bgm_system/bgm_system_event_33"] == "第五专辑"
    assert result["bgm_system_event_33"] == "第五专辑"


def test_audit_bgm_exports_reports_missing_lua_bank(tmp_path, monkeypatch):
    (tmp_path / "BaseWord_cn.lua").write_text("words", encoding="utf-8")
    (tmp_path / "BaseSound.lua").write_text(
        'BaseSound = {\n'
        '[70160068] = { bank = "bank:/bgm/bgm_system/event_33" }\n'
        '[70160069] = { bank = "bank:/bgm/bgm_system/event_34" }\n'
        '} ',
        encoding="utf-8",
    )
    (tmp_path / "BaseSoundChapter.lua").write_text(
        'BaseSoundChapter = {\n'
        '[70150005] = { name = function() return T(80880005) end, '
        'child_ids = { 70160068, 70160069 }, sort = 5 }\n'
        '} ',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "app.features.audio.album_map.parse_word_file",
        lambda _path: {80880005: "第五专辑"},
    )
    audio_dir = tmp_path / "audio"
    output = audio_dir / "album" / "第五专辑" / "event_33.wav"
    output.parent.mkdir(parents=True)
    output.write_bytes(b"audio")
    (audio_dir / ".bank_state.json").write_text(
        json.dumps({
            "banks": {
                "assets/fmodassets/bgm/bgm_system/event_33.bank": {
                    "out_rel": "album\\第五专辑",
                    "files": [{"path": "album/第五专辑/event_33.wav", "size": 5}],
                }
            }
        }),
        encoding="utf-8",
    )

    assert build_album_bank_map(str(tmp_path)) == {
        "第五专辑": {"bgm/bgm_system/event_33", "bgm/bgm_system/event_34"}
    }
    report = audit_bgm_exports(str(tmp_path), str(audio_dir))

    assert report["missing_by_album"] == {"第五专辑": ["bgm/bgm_system/event_34"]}
