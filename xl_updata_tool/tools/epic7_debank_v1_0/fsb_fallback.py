"""FSB 解码回退：原提取器失败时使用 vgmstream。"""

import os
import subprocess


DEFAULT_FSB_TIMEOUT = 15


def timeout_seconds():
    value = os.environ.get("XL_AUDIO_FSB_TIMEOUT", DEFAULT_FSB_TIMEOUT)
    try:
        timeout = float(value)
        return timeout if timeout > 0 else None
    except (TypeError, ValueError):
        return float(DEFAULT_FSB_TIMEOUT)


def audio_files(folder):
    if not os.path.isdir(folder):
        return []
    return [
        name
        for name in os.listdir(folder)
        if os.path.isfile(os.path.join(folder, name))
        and os.path.splitext(name)[1].lower() in {".wav", ".ogg", ".mp3"}
    ]


def clear_audio_files(folder):
    for name in audio_files(folder):
        try:
            os.remove(os.path.join(folder, name))
        except OSError:
            pass


def vgmstream_path(folder_root):
    configured = os.environ.get("XL_VGMSTREAM_CLI")
    candidates = (
        configured,
        os.path.join(os.path.dirname(folder_root), "vgmstream", "vgmstream-cli.exe"),
        os.path.join(folder_root, "vgmstream", "vgmstream-cli.exe"),
    )
    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            return candidate
    return None


def extract_fsb_with_fallback(file_fsb, folder_cur, folder_root, folder4result):
    """返回 ``(code, method, legacy_code, fallback_code)``。"""
    timeout = timeout_seconds()
    fsb_path = (
        file_fsb
        if os.path.isabs(file_fsb)
        else os.path.abspath(os.path.join(folder_cur, file_fsb))
    )
    legacy_path = os.path.join(folder_root, "_subcontractors", "fsb_aud_extr.exe")
    try:
        legacy = subprocess.run(
            [legacy_path, file_fsb],
            cwd=folder_cur,
            check=False,
            timeout=timeout,
        )
        legacy_code = legacy.returncode
    except subprocess.TimeoutExpired:
        print(f"fsb_aud_extr.exe timeout: {timeout}s")
        legacy_code = "timeout"

    if legacy_code == 0 and audio_files(folder_cur):
        return 0, "fsb_aud_extr", legacy_code, None

    clear_audio_files(folder_cur)
    clear_audio_files(folder4result)
    cli = vgmstream_path(folder_root)
    if not cli:
        return legacy_code or "extractor_failed", None, legacy_code, None

    output_pattern = os.path.join(folder4result, "?02s_?n.wav")
    try:
        fallback = subprocess.run(
            [cli, "-S", "0", "-o", output_pattern, fsb_path],
            cwd=os.path.dirname(cli),
            check=False,
            timeout=timeout,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            encoding="utf-8",
            errors="replace",
        )
        if fallback.stdout:
            print(fallback.stdout, end="")
        fallback_code = fallback.returncode
    except subprocess.TimeoutExpired:
        print(f"vgmstream-cli.exe timeout: {timeout}s")
        fallback_code = "timeout"

    if fallback_code == 0 and audio_files(folder4result):
        print("FSB fallback: vgmstream")
        return 0, "vgmstream", legacy_code, fallback_code
    return fallback_code or legacy_code, None, legacy_code, fallback_code
