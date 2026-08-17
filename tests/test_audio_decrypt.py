import sys
import types

from app.ui.workers.audio_decrypt import AudioDecryptWorker


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

    def fake_run(input_dir, output_dir, progress_callback=None, subdir_fn=None):
        calls.append((input_dir, output_dir, progress_callback, subdir_fn))

    monkeypatch.setitem(sys.modules, "epic7_debank", types.SimpleNamespace(run=fake_run))
    monkeypatch.setattr("app.ui.workers.audio_decrypt.build_album_map", lambda _: {})

    worker = AudioDecryptWorker(
        str(tmp_path / "material"),
        str(audio_output_dir),
        str(debank_dir),
        force=False,
    )
    worker._decrypt_bank_files()

    assert len(calls) == 1
