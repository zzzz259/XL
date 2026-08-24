from app.features.audio.audio_library import export_audio_files, format_duration, format_size, scan_audio_files


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


def test_audio_formatters_and_export_create_nested_directories(tmp_path):
    source = tmp_path / "source.wav"
    source.write_bytes(b"audio")
    destination = tmp_path / "export"

    success, failures = export_audio_files([{"path": str(source), "name": "voice/001/source.wav"}], str(destination))

    assert (success, failures) == (1, [])
    assert (destination / "voice" / "001" / "source.wav").read_bytes() == b"audio"
    assert format_duration(65000) == "01:05"
    assert format_size(1536) == "1.5 KB"
