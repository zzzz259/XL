import os
import sys
import types
import logging

from app.features.audio.processing import AudioDecryptProcessor


def test_existing_audio_does_not_skip_new_bank(tmp_path, monkeypatch):
    material_dir = tmp_path / "material" / "assets" / "fmodassets" / "bgm"
    material_dir.mkdir(parents=True)
    (material_dir / "new_track.bank").write_bytes(b"bank")

    audio_output_dir = tmp_path / "output" / "audio"
    audio_output_dir.mkdir(parents=True)
    (audio_output_dir / "old_track.ogg").write_bytes(b"old")

    debank_dir = tmp_path / "debank"
    debank_dir.mkdir()
    calls = []

    def fake_run(input_dir, output_dir, progress_callback=None, subdir_fn=None,
                 before_copy_callback=None, audio_transform_callback=None,
                 workers=None, temp_dir=None, cancel_check=None, use_cache=None):
        calls.append((input_dir, output_dir, progress_callback, subdir_fn, workers, temp_dir, cancel_check, use_cache))

    monkeypatch.setitem(sys.modules, "epic7_debank", types.SimpleNamespace(run=fake_run))
    monkeypatch.setattr("app.features.audio.processing.build_album_map", lambda _: {})

    worker = AudioDecryptProcessor(
        str(tmp_path / "material"),
        str(audio_output_dir),
        str(debank_dir),
        force=False,
    )
    worker._decrypt_bank_files()

    assert len(calls) == 1
    assert calls[0][-1] is not None


def test_audio_worker_normalizes_duplicate_battle_hit_names(tmp_path):
    result_dir = tmp_path / "result"
    result_dir.mkdir()
    paths = []
    for suffix in (1, 2, 3):
        path = result_dir / f"080_battle_hit_01_{suffix}.wav"
        path.write_bytes(bytes([suffix]))
        paths.append(str(path))

    worker = AudioDecryptProcessor(
        str(tmp_path / "material"),
        str(tmp_path / "output" / "audio"),
        str(tmp_path / "debank"),
    )
    normalized = worker._normalize_voice_audio_files(
        "080",
        "assets/fmodassets/voice_cn/btl/080.bank",
        paths,
    )

    assert [os.path.basename(path) for path in normalized] == [
        "080_battle_hit_01.wav",
        "080_battle_hit_02.wav",
        "080_battle_hit_03.wav",
    ]
    assert sorted(path.name for path in result_dir.iterdir()) == [
        "080_battle_hit_01.wav",
        "080_battle_hit_02.wav",
        "080_battle_hit_03.wav",
    ]


def test_audio_worker_normalizes_uniform_foreign_voice_prefix(tmp_path):
    result_dir = tmp_path / "result"
    result_dir.mkdir()
    paths = []
    for name in ("095_battle_atk_01.wav", "095_battle_skill_01.wav"):
        path = result_dir / name
        path.write_bytes(b"audio")
        paths.append(str(path))

    worker = AudioDecryptProcessor(
        str(tmp_path / "material"),
        str(tmp_path / "output" / "audio"),
        str(tmp_path / "debank"),
    )

    normalized = worker._normalize_voice_audio_files(
        "064",
        "assets/fmodassets/voice_jp/btl/064.bank",
        paths,
    )

    assert [os.path.basename(path) for path in normalized] == [
        "064_battle_atk_01.wav",
        "064_battle_skill_01.wav",
    ]


def test_audio_worker_removes_cross_character_voice_files(tmp_path):
    voice_dir = tmp_path / "output" / "audio" / "voice" / "064" / "jp"
    voice_dir.mkdir(parents=True)
    stale = voice_dir / "095_battle_atk_01.wav"
    stale.write_bytes(b"stale")
    worker = AudioDecryptProcessor(
        str(tmp_path / "material"),
        str(tmp_path / "output" / "audio"),
        str(tmp_path / "debank"),
    )

    worker._before_audio_copy(
        "064",
        "assets/fmodassets/voice_jp/btl/064.bank",
        os.path.join("voice", "064", "jp"),
        ["064_battle_atk_01.wav"],
    )

    assert not stale.exists()


def test_audio_worker_corrects_same_named_file_in_old_album(tmp_path):
    old = tmp_path / "output" / "audio" / "album" / "未分类" / "event.wav"
    old.parent.mkdir(parents=True)
    old.write_bytes(b"old")
    worker = AudioDecryptProcessor(
        str(tmp_path / "material"),
        str(tmp_path / "output" / "audio"),
        str(tmp_path / "debank"),
    )

    worker._before_audio_copy(
        "bgm_system_event_33",
        "bgm/bgm_system/bgm_system_event_33.bank",
        os.path.join("album", "第五专辑"),
        ["event.wav"],
    )

    assert not old.exists()


def test_audio_worker_does_not_delete_current_bgm_staging_file(tmp_path):
    audio_root = tmp_path / "output" / "audio"
    staging = audio_root / ".debank-temp" / "0001_bgm" / "result"
    staging.mkdir(parents=True)
    current = staging / "event.wav"
    current.write_bytes(b"current")

    worker = AudioDecryptProcessor(
        str(tmp_path / "material"),
        str(audio_root),
        str(tmp_path / "debank"),
    )

    worker._before_audio_copy(
        "bgm_system_event_33",
        "assets/fmodassets/bgm/bgm_system/bgm_system_event_33.bank",
        os.path.join("album", "第五专辑"),
        [current.name],
    )

    assert current.exists()


def test_audio_worker_keeps_cn_file_when_processing_same_named_jp_file(tmp_path):
    cn_file = tmp_path / "output" / "audio" / "voice" / "116" / "cn" / "116_in_01.wav"
    jp_file = tmp_path / "output" / "audio" / "voice" / "116" / "jp" / "116_in_01.wav"
    cn_file.parent.mkdir(parents=True)
    jp_file.parent.mkdir(parents=True)
    cn_file.write_bytes(b"cn")
    jp_file.write_bytes(b"jp")

    worker = AudioDecryptProcessor(
        str(tmp_path / "material"),
        str(tmp_path / "output" / "audio"),
        str(tmp_path / "debank"),
    )

    worker._before_audio_copy(
        "116",
        "assets/fmodassets/voice_jp/btl/116.bank",
        os.path.join("voice", "116", "jp"),
        ["116_in_01.wav"],
    )

    assert cn_file.exists()


def test_debank_copy_emits_structured_per_file_output_event(tmp_path, monkeypatch, caplog):
    tools_dir = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "tools", "epic7_debank_v1_0")
    )
    monkeypatch.syspath_prepend(tools_dir)
    import epic7_debank
    test_logger = logging.getLogger("audio-debank-test")
    monkeypatch.setattr(epic7_debank, "logger", test_logger)

    source = tmp_path / "staging" / "voice.wav"
    source.parent.mkdir()
    source.write_bytes(b"wave-data")
    output_root = tmp_path / "output"

    with caplog.at_level(logging.DEBUG, logger="audio-debank-test"):
        copied, skipped, failed = epic7_debank._copy_job_audio(
            {
                "audio_files": [str(source)],
                "bank_stem": "118",
                "rel_path": "assets/fmodassets/voice_cn/btl/118.bank",
                "bank_path": str(tmp_path / "input" / "118.bank"),
                "out_rel": "voice/118/cn",
                "bank_name": "118.bank",
            },
            str(output_root),
            None,
            None,
        )

    event = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "audio.output_file"
    )
    assert (copied, skipped, failed) == (1, 0, 0)
    assert event.details["source"] == str(source)
    normalized_output = event.details["output"].replace("\\", "/")
    assert normalized_output.endswith("voice/118/cn/voice.wav")
    assert event.details["size_bytes"] == len(b"wave-data")
    assert event.details["outcome"] == "written"


def test_bytes_conversion_records_input_and_output_file_event(tmp_path, monkeypatch, caplog):
    from app.features.audio import processing

    test_logger = logging.getLogger("audio-conversion-test")
    monkeypatch.setattr(processing, "logger", test_logger)
    material_root = tmp_path / "material"
    source = material_root / "assets" / "fmodassets" / "voice_cn" / "btl" / "118.bytes"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"bank-data")
    processor = AudioDecryptProcessor(
        str(material_root), str(tmp_path / "output" / "audio"), str(tmp_path / "debank")
    )

    with caplog.at_level(logging.DEBUG, logger="audio-conversion-test"):
        converted = processor._convert_bytes_to_bank()

    event = next(
        record for record in caplog.records
        if getattr(record, "event", None) == "audio.bytes_to_bank.file"
    )
    assert converted == 1
    assert not source.exists()
    assert event.outcome == "success"
    assert event.details["source"].endswith("118.bytes")
    assert event.details["output"].endswith("118.bank")
    assert event.details["size_bytes"] == len(b"bank-data")
