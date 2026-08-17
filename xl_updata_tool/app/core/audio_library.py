"""音频文件目录扫描，不依赖 Qt。"""

import os


DEFAULT_AUDIO_EXTENSIONS = frozenset({".wav", ".ogg", ".mp3"})


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
