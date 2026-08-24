"""音频文件目录扫描，不依赖 Qt。"""

import os


DEFAULT_AUDIO_EXTENSIONS = frozenset({".wav", ".ogg", ".mp3"})


def format_duration(milliseconds: int | None) -> str:
    """毫秒转为 mm:ss。"""
    if not milliseconds or milliseconds <= 0:
        return "00:00"
    minutes, seconds = divmod(int(milliseconds / 1000), 60)
    return f"{minutes:02d}:{seconds:02d}"


def format_size(size: int) -> str:
    """字节数转为适合列表展示的可读文本。"""
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def scan_audio_files(audio_dir: str, extensions=DEFAULT_AUDIO_EXTENSIONS) -> list[dict]:
    """递归扫描音频目录，返回供 UI 展示的稳定排序元数据。"""
    if not os.path.isdir(audio_dir):
        return []

    files = []
    for root, _dirs, names in os.walk(audio_dir):
        for filename in names:
            extension = os.path.splitext(filename)[1].lower()
            if extension not in extensions:
                continue
            filepath = os.path.join(root, filename)
            rel_name = os.path.relpath(filepath, audio_dir)
            files.append({
                "path": filepath,
                "name": rel_name,
                "dir": os.path.dirname(rel_name),
                "ext": extension.lstrip(".").upper(),
                "size": os.path.getsize(filepath),
                "duration": None,
            })
    files.sort(key=lambda item: item["name"])
    return files


def export_audio_files(audio_files: list[dict], destination_dir: str) -> tuple[int, list[str]]:
    """复制音频文件到目标目录，返回成功数量和失败文件名。"""
    success = 0
    failures = []
    for info in audio_files:
        try:
            destination = os.path.join(destination_dir, info["name"])
            os.makedirs(os.path.dirname(destination), exist_ok=True)
            import shutil
            shutil.copy2(info["path"], destination)
            success += 1
        except (OSError, PermissionError):
            failures.append(info.get("name", info.get("path", "")))
    return success, failures
