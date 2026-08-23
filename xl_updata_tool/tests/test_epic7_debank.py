import sys
import time
from pathlib import Path
from types import SimpleNamespace

from tools.epic7_debank_v1_0 import epic7_debank, fsb_fallback


def test_debank_isolates_same_named_banks_and_reports_empty_output(tmp_path, monkeypatch):
    input_root = tmp_path / "material" / "assets" / "fmodassets"
    cn = input_root / "voice_cn" / "btl" / "064.bank"
    jp = input_root / "voice_jp" / "btl" / "064.bank"
    empty = input_root / "bgm" / "bgm_system_event_20.bank"
    for bank in (cn, jp, empty):
        bank.parent.mkdir(parents=True, exist_ok=True)
        bank.write_bytes(b"bank")

    def fake_execute(
        bank_path,
        _subcontractors,
        extract_dir,
        _folder_cur,
        _python,
        result_dir=None,
        timeout=None,
    ):
        assert timeout == 180.0
        Path(extract_dir).mkdir(parents=True, exist_ok=True)
        if Path(bank_path).name == "bgm_system_event_20.bank":
            return True, 0
        result = Path(result_dir)
        result.mkdir(parents=True, exist_ok=True)
        language = "cn" if "voice_cn" in str(bank_path) else "jp"
        (result / f"064_{language}.wav").write_bytes(language.encode())
        return True, 0

    monkeypatch.setattr(epic7_debank, "execute_quickbms_single", fake_execute)
    output_root = tmp_path / "output"
    summary = epic7_debank.run(
        str(tmp_path / "material"),
        str(output_root),
        folder_cur=str(tmp_path / "debank"),
        subdir_fn=lambda rel_path, _stem: (
            "voice/064/cn" if "voice_cn" in rel_path else
            "voice/064/jp" if "voice_jp" in rel_path else "album/第五专辑"
        ),
        workers=2,
    )

    assert summary["success"] == 2
    assert summary["empty"] == 1
    assert summary["failed"] == 0
    assert (output_root / "voice" / "064" / "cn" / "064_cn.wav").read_bytes() == b"cn"
    assert (output_root / "voice" / "064" / "jp" / "064_jp.wav").read_bytes() == b"jp"
    assert not (tmp_path / "debank" / "_tempo").exists()


def test_debank_reports_bank_timeout(tmp_path, monkeypatch):
    bank = tmp_path / "material" / "assets" / "fmodassets" / "voice_cn" / "btl" / "064.bank"
    bank.parent.mkdir(parents=True, exist_ok=True)
    bank.write_bytes(b"bank")

    def fake_execute(*_args, **_kwargs):
        return False, "timeout"

    monkeypatch.setattr(epic7_debank, "execute_quickbms_single", fake_execute)
    summary = epic7_debank.run(
        str(tmp_path / "material"),
        str(tmp_path / "output"),
        folder_cur=str(tmp_path / "debank"),
        workers=1,
        bank_timeout=1,
    )

    assert summary["success"] == 0
    assert summary["empty"] == 0
    assert summary["failed"] == 1


def test_debank_cancellation_stops_copy_and_queued_banks(tmp_path, monkeypatch):
    input_root = tmp_path / "material" / "assets" / "fmodassets" / "voice_cn" / "btl"
    first = input_root / "064.bank"
    second = input_root / "065.bank"
    input_root.mkdir(parents=True)
    first.write_bytes(b"bank")
    second.write_bytes(b"bank")
    cancelled = False

    def fake_execute(
        bank_path,
        _subcontractors,
        extract_dir,
        _folder_cur,
        _python,
        result_dir=None,
        timeout=None,
        cancel_check=None,
    ):
        nonlocal cancelled
        if Path(bank_path).name == "064.bank":
            cancelled = True
            result = Path(result_dir)
            result.mkdir(parents=True, exist_ok=True)
            (result / "064_in_01.wav").write_bytes(b"audio")
            return True, 0
        assert cancel_check is not None and cancel_check()
        return False, "cancelled"

    monkeypatch.setattr(epic7_debank, "execute_quickbms_single", fake_execute)
    output_root = tmp_path / "output"
    summary = epic7_debank.run(
        str(tmp_path / "material"),
        str(output_root),
        folder_cur=str(tmp_path / "debank"),
        subdir_fn=lambda _rel_path, stem: f"voice/{stem}/cn",
        workers=1,
        cancel_check=lambda: cancelled,
    )

    assert summary["cancelled"] is True
    assert summary["copied"] == 0
    assert not list(output_root.rglob("*.wav"))


def test_external_process_is_terminated_when_cancelled(tmp_path):
    started = time.monotonic()
    result = epic7_debank._run_process(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        cwd=str(tmp_path),
        timeout=10,
        cancel_check=lambda: time.monotonic() - started >= 0.2,
    )

    assert result == "cancelled"
    assert time.monotonic() - started < 5


def test_debank_caches_unchanged_bank_and_validates_output(tmp_path, monkeypatch):
    bank = tmp_path / "material" / "assets" / "fmodassets" / "bgm" / "track.bank"
    bank.parent.mkdir(parents=True)
    bank.write_bytes(b"bank")
    calls = 0

    def fake_execute(
        _bank_path,
        _subcontractors,
        extract_dir,
        _folder_cur,
        _python,
        result_dir=None,
        timeout=None,
    ):
        nonlocal calls
        calls += 1
        Path(extract_dir).mkdir(parents=True, exist_ok=True)
        result = Path(result_dir)
        result.mkdir(parents=True, exist_ok=True)
        (result / "track.wav").write_bytes(b"audio")
        return True, 0

    monkeypatch.setattr(epic7_debank, "execute_quickbms_single", fake_execute)
    output_root = tmp_path / "output"
    kwargs = {
        "folder_cur": str(tmp_path / "debank"),
        "subdir_fn": lambda _rel_path, _stem: "album/第五专辑",
        "workers": 1,
    }

    first = epic7_debank.run(str(tmp_path / "material"), str(output_root), **kwargs)
    second = epic7_debank.run(str(tmp_path / "material"), str(output_root), **kwargs)

    assert first["cached"] == 0
    assert second["cached"] == 1
    assert second["copied"] == 0
    assert calls == 1

    (output_root / "album" / "第五专辑" / "track.wav").unlink()
    third = epic7_debank.run(str(tmp_path / "material"), str(output_root), **kwargs)
    assert third["cached"] == 0
    assert calls == 2


def test_execute_quickbms_propagates_fsb_callback_failure(tmp_path, monkeypatch):
    bank = tmp_path / "064.bank"
    bank.write_bytes(b"bank")
    extract_dir = tmp_path / "extract"
    result_dir = tmp_path / "result"
    result_dir.mkdir()
    (result_dir / ".extract_status.json").write_text(
        '{"status": "failed", "returncode": 3221226519}',
        encoding="utf-8",
    )
    class CompletedProcess:
        pid = 0

        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(epic7_debank.subprocess, "Popen", lambda *_args, **_kwargs: CompletedProcess())

    ok, code = epic7_debank.execute_quickbms_single(
        str(bank),
        str(tmp_path / "subcontractors"),
        str(extract_dir),
        str(tmp_path),
        "python.exe",
        result_dir=str(result_dir),
        timeout=1,
    )

    assert ok is False
    assert code == 3221226519


def test_fsb_fallback_uses_vgmstream_after_legacy_failure(tmp_path, monkeypatch):
    extract_dir = tmp_path / "extract"
    result_dir = tmp_path / "result"
    extract_dir.mkdir()
    result_dir.mkdir()
    (extract_dir / "00000000.fsb").write_bytes(b"fsb")
    calls = []

    def fake_run(command, **_kwargs):
        calls.append(command[0])
        if len(calls) == 1:
            return SimpleNamespace(returncode=3221226519)
        (result_dir / "01_e20.wav").write_bytes(b"wav")
        return SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr(fsb_fallback.subprocess, "run", fake_run)
    monkeypatch.setattr(fsb_fallback, "vgmstream_path", lambda _root: "vgmstream-cli.exe")

    code, method, legacy_code, fallback_code = fsb_fallback.extract_fsb_with_fallback(
        "00000000.fsb",
        str(extract_dir),
        str(tmp_path),
        str(result_dir),
    )

    assert (code, method) == (0, "vgmstream")
    assert legacy_code == 3221226519
    assert fallback_code == 0
