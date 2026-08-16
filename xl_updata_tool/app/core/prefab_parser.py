# -*- coding: utf-8 -*-
"""Unity prefab 解析：提取立绘背景相对人物的 Transform 偏移

立绘由「人物 Spine 工程」+「背景」组合，背景相对人物的偏移藏在 Unity prefab
的 Transform.m_LocalPosition 里（AssetStudio 的 Convert 导出不包含 Transform），
需用 UnityPy 从原始 bundle 读取。

链路：bundle → GameObject 树 → Transform.m_LocalPosition → 识别 bg/character/mesh_part
→ 背景像素偏移（PPU 换算）。
"""

import os

from app.core.logger import logger

try:
    import UnityPy
    UnityPy.set_assetbundle_decrypt_key(b'yunguihaowan1234')
    UNITYPY_AVAILABLE = True
except ImportError:
    UNITYPY_AVAILABLE = False


def parse_prefab(bundle_path):
    """解析一个 bundle，提取所有 GameObject 及其组件信息。

    返回 {gameobject_name: {'type': ..., 'pos': (x, y), 'sprite_size': (w, h)}}
      type: background / character / mesh_part / unknown
    """
    if not UNITYPY_AVAILABLE:
        logger.warning("UnityPy 未安装，无法解析 prefab")
        return {}
    try:
        env = UnityPy.load(bundle_path)
    except Exception as e:
        logger.warning(f"UnityPy 加载 bundle 失败: {bundle_path}: {e}")
        return {}

    result = {}
    for obj in env.objects:
        if obj.type.name != 'GameObject':
            continue
        try:
            go = obj.read()
        except Exception:
            continue
        info = {
            'name': go.m_Name,
            'type': 'unknown',
            'pos': (0.0, 0.0),
            'sprite_size': None,
        }
        for comp in go.m_Component:
            if comp.component is None:
                continue
            try:
                cd = comp.component.read()
            except Exception:
                continue
            type_name = type(cd).__name__
            if 'Transform' in type_name:
                pos = cd.m_LocalPosition
                info['pos'] = (pos.x, pos.y)
            elif 'SpriteRenderer' in type_name:
                info['type'] = 'background'
                sz = cd.m_Size
                info['sprite_size'] = (sz.x, sz.y)
            elif 'SkeletonAnimation' in type_name:
                info['type'] = 'character'
            elif 'MeshRenderer' in type_name:
                info['type'] = 'mesh_part'
        # 名字以 _bg 结尾的视为背景（SpriteRenderer 或 Spine 背景都算）
        if go.m_Name.endswith('_bg') and info['type'] in ('unknown', 'character'):
            info['type'] = 'background'
        result[go.m_Name] = info
    return result


def compute_pixel_offset(parse_result, ppu=100.0):
    """根据 parse_prefab 返回，计算背景相对人物的像素偏移。

    返回 {'bg_name': ..., 'pixel_offset': (dx, dy), 'ppu': ...} 或 None（无背景）。
    """
    bg = char = None
    for _name, info in parse_result.items():
        if info['type'] == 'background':
            bg = info
        elif info['type'] == 'character':
            char = info
    if bg is None:
        return None
    char_pos = char['pos'] if char else (0.0, 0.0)
    dx = (bg['pos'][0] - char_pos[0]) * ppu
    dy = (bg['pos'][1] - char_pos[1]) * ppu
    return {
        'bg_name': bg['name'],
        'pixel_offset': (dx, dy),
        'ppu': ppu,
    }


def build_cardspine_bundle_map(assets_map_path):
    """从 assets_map.json 建立 {角色名(cardspine_XXX_X): bundle路径} 映射

    assets_map.json 每条 asset 的 Source 字段记录其来源 bundle 路径，
    据此把「角色名」映射到「bundle 文件」，供 UnityPy 提取背景偏移。
    """
    import json
    import re
    if not assets_map_path or not os.path.isfile(assets_map_path):
        logger.info(f"assets_map.json 不存在，无法建立角色→bundle 映射: {assets_map_path}")
        return {}
    try:
        with open(assets_map_path, encoding='utf-8') as f:
            assets = json.load(f)
    except Exception as e:
        logger.warning(f"读取 assets_map.json 失败: {e}")
        return {}
    mapping = {}
    for a in assets:
        name = a.get('Name', '') or ''
        src = a.get('Source', '') or ''
        if not src:
            continue
        m = re.match(r'cardspine_(\d+_\d+)', name)
        if m:
            role = f'cardspine_{m.group(1)}'
            if role not in mapping:
                mapping[role] = src
    logger.info(f"建立角色→bundle 映射: {len(mapping)} 个角色")
    return mapping
