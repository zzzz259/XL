"""预览图与 Spine 资源路径匹配，不依赖 Qt。"""

import os


def build_skel_map(material_dir: str) -> dict[str, tuple[str, str]]:
    """扫描 material 目录，建立 skel 基名到 skel/atlas 路径的映射。"""
    result = {}
    if not os.path.isdir(material_dir):
        return result
    for root, dirs, files in os.walk(material_dir):
        dirs.sort()
        for filename in sorted(files):
            if filename.endswith(".skel.bytes"):
                base = filename[:-len(".skel.bytes")]
                atlas_candidates = (f"{base}.atlas", f"{base}.atlas.txt")
            elif filename.endswith(".skel"):
                base = os.path.splitext(filename)[0]
                atlas_candidates = (f"{base}.atlas", f"{base}.atlas.txt")
            else:
                continue
            skel_path = os.path.join(root, filename)
            atlas_path = next(
                (os.path.join(root, candidate) for candidate in atlas_candidates
                 if os.path.isfile(os.path.join(root, candidate))),
                os.path.join(root, atlas_candidates[0]),
            )
            result[base] = (skel_path, atlas_path)
    return result


def scan_cardspine_roles(material_dir: str) -> list[str]:
    """扫描 cardspine 目录中的角色 skel 名称，排除背景资源。"""
    roles = set()
    if not os.path.isdir(material_dir):
        return []
    for root, _dirs, files in os.walk(material_dir):
        for filename in files:
            if filename.endswith(".skel.bytes"):
                role = filename[:-len(".skel.bytes")]
            elif filename.endswith(".skel"):
                role = os.path.splitext(filename)[0]
            else:
                continue
            if role:
                if not role.endswith("_bg"):
                    roles.add(role)
    return sorted(roles)


def scan_preview_roles(preview_dir: str) -> list[str]:
    """扫描预览输出目录的一级角色目录。"""
    if not os.path.isdir(preview_dir):
        return []
    return sorted(
        name for name in os.listdir(preview_dir)
        if os.path.isdir(os.path.join(preview_dir, name))
    )


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
