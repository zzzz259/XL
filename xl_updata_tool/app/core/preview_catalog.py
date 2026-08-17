"""预览图与 Spine 资源路径匹配，不依赖 Qt。"""

import os


def build_skel_map(material_dir: str) -> dict[str, tuple[str, str]]:
    """扫描 material 目录，建立 skel 基名到 skel/atlas 路径的映射。"""
    result = {}
    if not os.path.isdir(material_dir):
        return result
    for root, _dirs, files in os.walk(material_dir):
        for filename in files:
            if not filename.endswith(".skel"):
                continue
            base = os.path.splitext(filename)[0]
            skel_path = os.path.join(root, filename)
            result[base] = (skel_path, os.path.join(root, f"{base}.atlas"))
    return result


def find_skel_paths(png_path: str, skel_map: dict[str, tuple[str, str]]) -> tuple[str | None, str | None]:
    """按预览图命名规则查找对应的 Spine 文件。"""
    filename = os.path.splitext(os.path.basename(png_path))[0]
    if filename.endswith("_composite"):
        base = filename[:-len("_composite")]
    elif filename.endswith("_bg"):
        base = filename
    elif filename in skel_map:
        base = filename
    else:
        base = next(
            (known_base for known_base in skel_map if filename.startswith(known_base + "_")),
            filename,
        )
    return skel_map.get(base, (None, None))
