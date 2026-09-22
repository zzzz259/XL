"""服务器版 Lua 字节码修复和批量反编译。"""

from __future__ import annotations

import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

FIXED_HEAD = (
    b"\x1B\x4C\x75\x61\x54\x00\x19\x93\x0D\x0A\x1A\x0A\x04\x08\x08\x78"
    b"\x56\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x28\x77\x40\x01"
)
MARKER = b"\x28\x77\x40\x01"


@dataclass(frozen=True)
class DecodeReport:
    success: int
    failed: int


def classify_lua_bytes(data: bytes) -> tuple[str, bytes | None]:
    stripped = data.lstrip()
    if stripped.startswith((b"--- ", b"---@", b"function", b"local", b"return")):
        return "plaintext", data
    marker = data.find(MARKER)
    if marker >= 16:
        return "bytecode", FIXED_HEAD + data[marker + len(MARKER):]
    if marker >= 0 or data.startswith(b"\x1bLua"):
        return "bytecode", data
    return "unknown", None


def decode_chinese(content: str) -> str:
    pattern = re.compile(r"(\\(\d{3}))+")

    def replace(match: re.Match[str]) -> str:
        values = [int(item) for item in match.group().split("\\")[1:]]
        try:
            return bytes(values).decode("utf-8")
        except UnicodeDecodeError:
            return match.group()

    return pattern.sub(replace, content)


def decode_lua_directory(
    input_dir: Path,
    output_dir: Path,
    java_bin: str,
    unluac_jar: Path,
    opmap: Path,
) -> DecodeReport:
    """批量反编译目录中的 Lua 文件。

    明文文件直接写出并做中文解码；字节码文件集中到临时目录后启用单个 JVM 批量反编译，
    避免弱服务器上每个文件起一个 JVM 的内存与时间开销。
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    success = 0
    failed = 0
    bytecode: dict[str, tuple[Path, bytes]] = {}

    for source in sorted(input_dir.iterdir()):
        if not source.is_file():
            continue
        kind, processed = classify_lua_bytes(source.read_bytes())
        target_name = re.sub(r"\.(bytes|bank)$", "", source.name, flags=re.IGNORECASE)
        if kind == "plaintext":
            target = output_dir / target_name
            text = decode_chinese((processed or b"").decode("utf-8", errors="replace"))
            target.write_text(text, encoding="utf-8")
            success += 1
        elif kind == "bytecode" and processed is not None:
            bytecode[target_name] = (source, processed)
        else:
            failed += 1

    if not bytecode:
        return DecodeReport(success, failed)

    with tempfile.TemporaryDirectory(prefix="lua_batch_") as tmp:
        binary_dir = Path(tmp) / "binary"
        batch_out = Path(tmp) / "out"
        binary_dir.mkdir()
        batch_out.mkdir()
        for target_name, (_, fixed) in bytecode.items():
            (binary_dir / target_name).write_bytes(fixed)

        subprocess.run(
            [
                java_bin,
                "-Xmx256m",
                "-jar",
                str(unluac_jar),
                str(binary_dir),
                "--output",
                str(batch_out),
                "--opmap",
                str(opmap),
            ],
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )

        for target_name, (source, _) in bytecode.items():
            decomp = batch_out / target_name
            target = output_dir / target_name
            if decomp.is_file() and decomp.stat().st_size > 0:
                target.write_text(
                    decode_chinese(decomp.read_text(encoding="utf-8", errors="replace")),
                    encoding="utf-8",
                )
                success += 1
            else:
                failed += 1

    return DecodeReport(success, failed)
