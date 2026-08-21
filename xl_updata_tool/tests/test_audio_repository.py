from app.core.audio_repository import mark_all_read, mark_read, sync_audio_snapshot, unread_files


def test_audio_snapshot_marks_new_and_preserves_read_state(tmp_path):
    audio = tmp_path / "audio"
    source = audio / "album" / "第五专辑" / "event.wav"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"audio")
    info = {"name": "album\\第五专辑\\event.wav", "path": str(source), "size": 5}

    sync_audio_snapshot(str(audio), [info])
    assert unread_files(str(audio)) == {"album/第五专辑/event.wav"}
    assert mark_read(str(audio), info["name"]) is True
    assert unread_files(str(audio)) == set()

    info["unread"] = False
    sync_audio_snapshot(str(audio), [info])
    assert unread_files(str(audio)) == set()

    source.write_bytes(b"new audio")
    sync_audio_snapshot(str(audio), [info])
    assert unread_files(str(audio)) == {"album/第五专辑/event.wav"}
    assert mark_all_read(str(audio)) is True
    assert unread_files(str(audio)) == set()
