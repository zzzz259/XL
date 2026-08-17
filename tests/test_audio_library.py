from app.core.audio_library import scan_audio_files


def test_scan_audio_files_filters_and_sorts_relative_names(tmp_path):
    (tmp_path / "voice" / "002").mkdir(parents=True)
    (tmp_path / "voice" / "001").mkdir(parents=True)
    (tmp_path / "voice" / "002" / "b.wav").write_bytes(b"12")
    (tmp_path / "voice" / "001" / "a.ogg").write_bytes(b"123")
    (tmp_path / "ignore.txt").write_text("ignore", encoding="utf-8")

    files = scan_audio_files(str(tmp_path))

    assert [item["name"] for item in files] == ["voice\\001\\a.ogg", "voice\\002\\b.wav"]
    assert files[0]["ext"] == "OGG"
    assert files[1]["size"] == 2
