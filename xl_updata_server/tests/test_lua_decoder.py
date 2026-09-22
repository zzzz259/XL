import subprocess
from pathlib import Path

from server_app.lua_decoder import FIXED_HEAD, classify_lua_bytes, decode_chinese, decode_lua_directory


def test_classify_lua_bytes_accepts_plaintext_and_fixed_bytecode():
    assert classify_lua_bytes(b"return {id = 10001}")[0] == "plaintext"
    assert classify_lua_bytes(b"\x1bLua\x54\x00\x19\x93")[0] == "bytecode"


def test_decode_chinese_rewrites_octal_escapes():
    assert decode_chinese("\\229\\165\\189") == "好"


def test_decode_lua_directory_uses_single_jvm_batch_mode(tmp_path, monkeypatch):
    input_dir = tmp_path / "in"
    output_dir = tmp_path / "out"
    input_dir.mkdir()
    (input_dir / "BaseWord_cn.lua").write_text("return {}", encoding="utf-8")
    (input_dir / "BaseCard.lua").write_bytes(FIXED_HEAD + b"payload")

    captured: dict[str, object] = {}

    def fake_run(cmd, *args, **kwargs):
        captured["cmd"] = cmd
        captured["timeout"] = kwargs.get("timeout")
        out_index = cmd.index("--output") + 1
        out_dir = Path(cmd[out_index])
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "BaseCard.lua").write_text("return {id = 10001}", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("server_app.lua_decoder.subprocess.run", fake_run)

    report = decode_lua_directory(
        input_dir, output_dir, "java", tmp_path / "unluac.jar", tmp_path / "opmap"
    )

    assert report.success == 2
    assert report.failed == 0
    assert (output_dir / "BaseWord_cn.lua").is_file()
    assert (output_dir / "BaseCard.lua").is_file()
    assert captured["timeout"] == 600
    assert "-Xmx256m" in captured["cmd"]
    assert str(tmp_path / "unluac.jar") in captured["cmd"]
    assert str(tmp_path / "opmap") in captured["cmd"]
    assert "--output" in captured["cmd"]


def test_decode_lua_directory_counts_batch_failures(tmp_path, monkeypatch):
    input_dir = tmp_path / "in"
    output_dir = tmp_path / "out"
    input_dir.mkdir()
    (input_dir / "Bad.lua").write_bytes(FIXED_HEAD + b"bad")

    def fake_run(_cmd, *args, **kwargs):
        return subprocess.CompletedProcess(_cmd, returncode=1, stdout="", stderr="")

    monkeypatch.setattr("server_app.lua_decoder.subprocess.run", fake_run)

    report = decode_lua_directory(
        input_dir, output_dir, "java", tmp_path / "unluac.jar", tmp_path / "opmap"
    )

    assert report.success == 0
    assert report.failed == 1
