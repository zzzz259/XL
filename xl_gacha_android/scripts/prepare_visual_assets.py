#!/usr/bin/env python3
"""可复现素材生成器：从星落素材交付包重建 xl_gacha_android/web/assets。

用法：
    python scripts/prepare_visual_assets.py [素材包根目录] [--preview-dir DIR] [--no-preview]

素材包默认路径可用环境变量 STARFALL_ASSET_PACK 覆盖。生成器只读取素材包，
不修改其中任何文件；所有产物写入 web/assets 并刷新 manifest.json。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import statistics
import struct
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageSequence

Image.MAX_IMAGE_PIXELS = None

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

DEFAULT_PACK = "C:/Users/49432/Downloads/Starfall-App-Assets-20261029/Starfall-App-Assets"

PORTRAIT_SIZE = 256          # 角色肖像输出边长
SQUARE_PAD_SIZE = 270        # 140x270 HeadSquare 左右补透明后的方画布边长
ALPHA_THRESHOLD = 0          # alpha bbox 阈值（与素材包 catalog/assets.json 约定一致，alpha_min=0）

MASCOT_CANVAS = 864          # gugu 统一画布边长
MASCOT_BASELINE_Y = 848      # 落地帧底行所在基线
MASCOT_CENTER_X = MASCOT_CANVAS // 2
LANDING_TOLERANCE_PX = 2     # 落地帧判定：底行距离地面行不超过该像素数

RESOURCE_VERSION = "134359843258232660"

BRAND_LOGO_SRC = "images/brand/starfall-logo.png"
UI_PERLIN_SRC = "images/masks/perlinNoise.png"
# style-components 下为中文文件名，用转义写法避免编码问题：大按钮字底22绿.png
UI_PRIMARY_BUTTON_SRC = "images/style-components/CommonResource/\u5927\u6309\u94ae\u5b57\u53f722\u7eff.png"

MASCOT_SETS = ("idle", "jump_all", "relive", "die")
MASCOT_NAME = "minigame017_gugu"

# 每套动画的期望时长总和（毫秒），与源 ANMF 帧时长相加结果一致
MASCOT_EXPECTED_TOTAL_MS = {
    "idle": 1366,
    "jump_all": 1699,
    "relive": 1366,
    "die": 1333,
}

MANIFEST_NAMES = ("characters", "fashions", "account_items", "brand", "mascot", "files")


# ---------------------------------------------------------------------------
# WebP RIFF 解析（生成器与测试共用）
# ---------------------------------------------------------------------------

def parse_webp_riff(path):
    """解析 WebP RIFF 容器。

    返回 {
        "canvas": (w, h),
        "loop": ANIM.loop 计数（无 ANIM 时为 None），
        "frames": [{"x","y","w","h","duration"}...]（按 ANMF 顺序），
        "chunks": [fourcc...]（顶层块顺序，含 VP8X/ANIM/ANMF/VP8/VP8L），
        "lossless": 是否存在 VP8L 编码帧，
    }
    """
    data = Path(path).read_bytes()
    if data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        raise ValueError(f"不是 WebP RIFF 文件: {path}")
    off = 12
    canvas = None
    loop = None
    frames = []
    chunks = []
    lossless = False
    while off + 8 <= len(data):
        fourcc = data[off:off + 4]
        size = struct.unpack_from("<I", data, off + 4)[0]
        payload = data[off + 8:off + 8 + size]
        chunks.append(fourcc.decode("ascii", "replace"))
        if fourcc == b"VP8X":
            canvas = (
                int.from_bytes(payload[4:7], "little") + 1,
                int.from_bytes(payload[7:10], "little") + 1,
            )
        elif fourcc == b"VP8L":
            lossless = True
            if canvas is None and payload[0] == 0x2F:
                # 无损单帧：0x2f 签名后 14bit 宽/高（仍图无 VP8X 时从这里取画布）
                width = (payload[1] | ((payload[2] & 0x3F) << 8)) + 1
                height = ((payload[2] >> 6) | (payload[3] << 2) | ((payload[4] & 0x0F) << 10)) + 1
                canvas = (width, height)
        elif fourcc == b"VP8 " and canvas is None:
            # 有损单帧：offset 3 处 9d 012a 起始码，之后 14bit 宽/高
            if payload[3:6] == b"\x9d\x01\x2a":
                width = struct.unpack_from("<H", payload, 6)[0] & 0x3FFF
                height = struct.unpack_from("<H", payload, 8)[0] & 0x3FFF
                canvas = (width, height)
        elif fourcc == b"ANIM":
            loop = struct.unpack_from("<H", payload, 4)[0]
        elif fourcc == b"ANMF":
            frames.append({
                "x": int.from_bytes(payload[0:3], "little"),
                "y": int.from_bytes(payload[3:6], "little"),
                "w": int.from_bytes(payload[6:9], "little"),
                "h": int.from_bytes(payload[9:12], "little"),
                "duration": int.from_bytes(payload[12:15], "little"),
            })
            # ANMF 帧体内部是子块（VP8/VP8L/ALPH），扫描无损标志
            sub = 16
            while sub + 8 <= len(payload):
                sub_fourcc = payload[sub:sub + 4]
                sub_size = struct.unpack_from("<I", payload, sub + 4)[0]
                if sub_fourcc == b"VP8L":
                    lossless = True
                sub += 8 + sub_size + (sub_size & 1)
        off += 8 + size + (size & 1)
    return {
        "canvas": canvas,
        "loop": loop,
        "frames": frames,
        "chunks": chunks,
        "lossless": lossless,
    }


# ---------------------------------------------------------------------------
# 图像辅助
# ---------------------------------------------------------------------------

def alpha_bbox(im):
    """返回 alpha>ALPHA_THRESHOLD 的包围盒 (x0, y0, x1, y1)，x1/y1 为开区间。"""
    rgba = im if im.mode == "RGBA" else im.convert("RGBA")
    alpha = rgba.getchannel("A")
    mask = alpha.point(lambda a: 255 if a > ALPHA_THRESHOLD else 0)
    return mask.getbbox()


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def save_lossless_webp(im, path, duration=None, loop=None, append_images=None):
    # exact=True 保证透明像素下的 RGB 也被逐位保留，解码端可与源帧完全一致
    kwargs = {"lossless": True, "quality": 100, "method": 4, "exact": True}
    if duration is not None:
        kwargs["duration"] = duration
    if loop is not None:
        kwargs["loop"] = loop
    if append_images is not None:
        kwargs["save_all"] = True
        kwargs["append_images"] = append_images
    im.save(path, "WEBP", **kwargs)


# ---------------------------------------------------------------------------
# 角色肖像
# ---------------------------------------------------------------------------

def normalize_portrait(im, square_size):
    """把头像放入 square_size 方画布：按 alpha bbox 水平平移居中，不裁切，纵向不变。"""
    canvas = Image.new("RGBA", (square_size, square_size), (0, 0, 0, 0))
    bbox = alpha_bbox(im)
    if bbox is None:
        return canvas
    x0, _y0, x1, _y1 = bbox
    dx = round((square_size - (x1 - x0)) / 2) - x0
    canvas.paste(im.convert("RGBA"), (dx, 0))
    return canvas


def build_portrait(item_id, character, pack_root, out_dir):
    """生成单个角色肖像，返回输出路径。bursthead 为 null 时回退到 140x270 方图。"""
    burst = character.get("bursthead")
    if burst:
        src = Image.open(pack_root / burst)
        canvas = normalize_portrait(src, PORTRAIT_SIZE)
    else:
        square_rel = character["square"]
        src = Image.open(pack_root / square_rel)
        if src.size != (140, 270):
            raise ValueError(f"{item_id} 方图尺寸异常: {src.size}")
        padded = normalize_portrait(src, SQUARE_PAD_SIZE)
        canvas = padded.resize((PORTRAIT_SIZE, PORTRAIT_SIZE), Image.Resampling.LANCZOS)
    out_path = out_dir / f"{item_id}.webp"
    save_lossless_webp(canvas, out_path)
    return out_path


# ---------------------------------------------------------------------------
# gugu 魔物动画
# ---------------------------------------------------------------------------

def decode_frames(path):
    """逐帧解码 WebP 动画为 RGBA 全画布帧列表。"""
    im = Image.open(path)
    frames = []
    for index in range(im.n_frames):
        im.seek(index)
        frames.append(im.convert("RGBA"))
    return frames


def compute_mascot_offsets(frames):
    """按每套落地帧底行中位数与帧中心 x 中位数计算统一偏移。"""
    bboxes = [alpha_bbox(frame) for frame in frames]
    bottoms = [box[3] for box in bboxes]
    centers_x = [(box[0] + box[2]) / 2 for box in bboxes]
    ground = max(bottoms)
    landing = [index for index, bottom in enumerate(bottoms) if bottom >= ground - LANDING_TOLERANCE_PX]
    base_bottom = statistics.median([bottoms[index] for index in landing])
    base_center_x = statistics.median([centers_x[index] for index in landing])
    dx = round(MASCOT_CENTER_X - base_center_x)
    dy = round(MASCOT_BASELINE_Y - base_bottom)
    return dx, dy, landing


def build_mascot_set(set_name, pack_root, out_dir):
    """归一化一套 gugu 动画并输出无损 WebP，返回 (webp 路径, 时长和, 归一化帧列表)。"""
    src_path = pack_root / "previews" / "mascot" / f"{MASCOT_NAME}-{set_name}.webp"
    riff = parse_webp_riff(src_path)
    durations = [frame["duration"] for frame in riff["frames"]]
    total_ms = sum(durations)
    expected = MASCOT_EXPECTED_TOTAL_MS[set_name]
    if total_ms != expected:
        raise ValueError(f"{set_name} 时长总和 {total_ms} != 期望 {expected}")
    if riff["loop"] != 0:
        raise ValueError(f"{set_name} 源动画 loop 标志应为 0（无限循环），实际 {riff['loop']}")
    frames = decode_frames(src_path)
    if len(frames) != len(durations):
        raise ValueError(f"{set_name} 解码帧数 {len(frames)} 与 ANMF 帧数 {len(durations)} 不一致")
    dx, dy, _landing = compute_mascot_offsets(frames)
    normalized = []
    for frame in frames:
        canvas = Image.new("RGBA", (MASCOT_CANVAS, MASCOT_CANVAS), (0, 0, 0, 0))
        canvas.paste(frame, (dx, dy))
        normalized.append(canvas)
    out_path = out_dir / f"{MASCOT_NAME}-{set_name}.webp"
    save_lossless_webp(normalized[0], out_path, duration=durations, loop=0,
                       append_images=normalized[1:])
    return out_path, total_ms, normalized


# ---------------------------------------------------------------------------
# 目录与清单
# ---------------------------------------------------------------------------

def load_catalog(pack_root, name):
    with open(pack_root / "catalog" / name, encoding="utf-8") as handle:
        return json.load(handle)


def write_manifest(path, manifest):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=1)
        handle.write("\n")


# ---------------------------------------------------------------------------
# 预览 contact sheet（供 server-exports/android-04-visual-preview 检查）
# ---------------------------------------------------------------------------

CHECKER = (238, 238, 238, 255)
CHECKER_ALT = (200, 200, 200, 255)


def checker_cell(draw_target, box):
    x0, y0, x1, y1 = box
    size = 16
    for y in range(y0, y1, size):
        for x in range(x0, x1, size):
            color = CHECKER if ((x // size) + (y // size)) % 2 == 0 else CHECKER_ALT
            draw_target.rectangle(
                [x, y, min(x + size - 1, x1 - 1), min(y + size - 1, y1 - 1)], fill=color)


def fit_thumb(im, cell):
    thumb = im.copy()
    thumb.thumbnail((cell, cell), Image.Resampling.LANCZOS)
    return thumb


def labeled_cell(base, im, label, cell, at):
    tile = Image.new("RGBA", (cell, cell), (0, 0, 0, 0))
    checker = Image.new("RGBA", (cell, cell), (0, 0, 0, 0))
    checker_cell(ImageDraw.Draw(checker), (0, 0, cell, cell))
    thumb = fit_thumb(im, cell)
    checker.paste(thumb, ((cell - thumb.width) // 2, (cell - thumb.height) // 2), thumb)
    tile = checker
    draw = ImageDraw.Draw(tile)
    draw.rectangle([0, 0, cell - 1, cell - 1], outline=(120, 120, 120, 255))
    if label:
        draw.rectangle([0, cell - 16, max(len(label) * 7 + 4, 30), cell], fill=(0, 0, 0, 160))
        draw.text((3, cell - 14), label, fill=(255, 255, 255, 255))
    base.paste(tile, at)


def contact_sheet(cells, cols, cell, out_path):
    rows = (len(cells) + cols - 1) // cols
    sheet = Image.new("RGBA", (cols * cell, rows * cell), (255, 255, 255, 255))
    for index, (im, label) in enumerate(cells):
        labeled_cell(sheet, im, label, cell, ((index % cols) * cell, (index // cols) * cell))
    sheet.convert("RGB").save(out_path)


def frame_with_baseline(im, cell=260):
    """缩放帧并在基线位置画红线，便于肉眼核对落地对齐。"""
    scale = cell / MASCOT_CANVAS
    small = im.resize((cell, cell), Image.Resampling.LANCZOS)
    out = Image.new("RGBA", (cell, cell), (0, 0, 0, 0))
    checker_cell(ImageDraw.Draw(out), (0, 0, cell, cell))
    out.paste(small, (0, 0), small)
    draw = ImageDraw.Draw(out)
    y = round(MASCOT_BASELINE_Y * scale)
    draw.line([0, y, cell - 1, y], fill=(220, 30, 30, 255), width=1)
    return out


def build_previews(preview_dir, pack_root, web_root, manifest):
    preview_dir.mkdir(parents=True, exist_ok=True)
    characters = manifest["characters"]

    # 1) 全部 108 张角色肖像缩略图
    cells = []
    for item_id in sorted(characters):
        im = Image.open(web_root / characters[item_id]["path"])
        cells.append((im, item_id))
    contact_sheet(cells, cols=12, cell=128, out_path=preview_dir / "portraits_all.png")

    # 2) 代表性对比：源图 vs 输出（含两张 140x270 特殊方图）
    catalog = load_catalog(pack_root, "characters.json")
    sorted_ids = sorted(catalog)
    compare_ids = sorted_ids[:2] + [sorted_ids[len(sorted_ids) // 2]] + ["10000199", "10000200"]
    compare_ids = list(dict.fromkeys(compare_ids))
    cells = []
    for item_id in compare_ids:
        entry = catalog[item_id]
        if entry.get("bursthead"):
            src = Image.open(pack_root / entry["bursthead"])
            cells.append((src, f"src {Path(entry['bursthead']).stem}"))
        else:
            src = Image.open(pack_root / entry["square"])
            cells.append((src, f"src {Path(entry['square']).stem}"))
        cells.append((Image.open(web_root / characters[item_id]["path"]), f"out {item_id}"))
    contact_sheet(cells, cols=2, cell=220, out_path=preview_dir / "portraits_compare.png")

    # 3) 每套 gugu 动画全部帧
    for set_name in MASCOT_SETS:
        riff = parse_webp_riff(web_root / "assets" / "mascot" / f"{MASCOT_NAME}-{set_name}.webp")
        frames = decode_frames(web_root / "assets" / "mascot" / f"{MASCOT_NAME}-{set_name}.webp")
        cells = [(frame, str(index)) for index, frame in enumerate(frames)]
        contact_sheet(cells, cols=7, cell=150, out_path=preview_dir / f"gugu_{set_name}_frames.png")

    # 4) 基线核对图：每套取落地帧与最高帧，叠加 y=848 基线
    cells = []
    for set_name in MASCOT_SETS:
        frames = decode_frames(web_root / "assets" / "mascot" / f"{MASCOT_NAME}-{set_name}.webp")
        bboxes = [alpha_bbox(frame) for frame in frames]
        bottoms = [box[3] for box in bboxes]
        tops = [box[1] for box in bboxes]
        landing_index = bottoms.index(max(bottoms))
        apex_index = tops.index(min(tops))
        cells.append((frame_with_baseline(frames[landing_index]), f"{set_name} land f{landing_index}"))
        cells.append((frame_with_baseline(frames[apex_index]), f"{set_name} apex f{apex_index}"))
    contact_sheet(cells, cols=2, cell=260, out_path=preview_dir / "gugu_baseline_check.png")

    # 5) poster 与 idle 首帧
    poster = Image.open(web_root / "assets" / "mascot" / f"{MASCOT_NAME}-idle.png")
    idle0 = decode_frames(web_root / "assets" / "mascot" / f"{MASCOT_NAME}-idle.webp")[0]
    cells = [(poster, "poster idle.png"), (idle0, "idle webp f0")]
    contact_sheet(cells, cols=2, cell=260, out_path=preview_dir / "gugu_poster_check.png")


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def generate(web_root, pack_root, preview_dir=None):
    assets_dir = web_root / "assets"
    portraits_dir = assets_dir / "portraits"
    account_dir = assets_dir / "account"
    brand_dir = assets_dir / "brand"
    ui_dir = assets_dir / "ui"
    mascot_dir = assets_dir / "mascot"

    for directory in (portraits_dir, account_dir, brand_dir, ui_dir, mascot_dir):
        if directory.exists():
            shutil.rmtree(directory)
        directory.mkdir(parents=True)
    fashions_dir = assets_dir / "fashions"
    if fashions_dir.exists():
        shutil.rmtree(fashions_dir)

    characters = load_catalog(pack_root, "characters.json")
    fashions = load_catalog(pack_root, "fashions.json")
    account_items = load_catalog(pack_root, "account-items.json")

    outputs = {}  # relpath (assets/...) -> absolute path

    # 1) 角色肖像
    manifest_characters = {}
    for item_id in sorted(characters):
        entry = characters[item_id]
        out_path = build_portrait(item_id, entry, pack_root, portraits_dir)
        rel = f"assets/portraits/{out_path.name}"
        outputs[rel] = out_path
        manifest_characters[item_id] = {
            "name": entry["name"],
            "rarity": entry["star"],
            "path": rel,
        }

    # 2) 时装 -> 所属 card_id 的默认角色肖像（始终初始_1，即角色默认肖像）
    manifest_fashions = {}
    for fashion_id in sorted(fashions):
        card_id = str(fashions[fashion_id]["card_id"])
        if card_id not in manifest_characters:
            raise ValueError(f"时装 {fashion_id} 的 card_id {card_id} 不在角色表中")
        manifest_fashions[fashion_id] = {"path": f"assets/portraits/{card_id}.webp"}

    # 3) 账户装扮道具（99 项映射、98 个文件：Frame_4002 被两个道具共用）
    manifest_account = {}
    account_sources = {}
    for item_id in sorted(account_items):
        entry = account_items[item_id]
        src_rel = entry["path"]
        out_name = Path(src_rel).name
        rel = f"assets/account/{out_name}"
        if rel in account_sources and account_sources[rel] != src_rel:
            raise ValueError(f"账户素材重名冲突: {rel}")
        account_sources[rel] = src_rel
        out_path = account_dir / out_name
        if rel not in outputs:
            shutil.copyfile(pack_root / src_rel, out_path)
            outputs[rel] = out_path
        manifest_account[item_id] = {
            "path": rel,
            "type": entry["type"],
            "effect_value": entry["effect_value"],
        }

    # 4) brand 与 ui
    static_files = {
        f"assets/brand/{Path(BRAND_LOGO_SRC).name}": BRAND_LOGO_SRC,
        f"assets/ui/{Path(UI_PERLIN_SRC).name}": UI_PERLIN_SRC,
        f"assets/ui/primary-button.png": UI_PRIMARY_BUTTON_SRC,
    }
    manifest_brand = {
        "logo": f"assets/brand/{Path(BRAND_LOGO_SRC).name}",
        "noise": f"assets/ui/{Path(UI_PERLIN_SRC).name}",
        "primary_button": "assets/ui/primary-button.png",
    }
    for rel, src_rel in static_files.items():
        out_path = web_root / rel
        out_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(pack_root / src_rel, out_path)
        outputs[rel] = out_path

    # 5) gugu 魔物
    mascot_outputs = {}
    normalized_frames = {}
    durations = {}
    for set_name in MASCOT_SETS:
        out_path, total_ms, frames = build_mascot_set(set_name, pack_root, mascot_dir)
        rel = f"assets/mascot/{out_path.name}"
        outputs[rel] = out_path
        mascot_outputs[set_name] = rel
        normalized_frames[set_name] = frames
        durations[set_name] = total_ms
    poster_rel = f"assets/mascot/{MASCOT_NAME}-idle.png"
    poster_path = web_root / poster_rel
    normalized_frames["idle"][0].save(poster_path, "PNG")
    outputs[poster_rel] = poster_path

    manifest_mascot = {
        "idle": {
            "path": mascot_outputs["idle"],
            "poster": poster_rel,
            "durationMs": durations["idle"],
        },
        "reactions": [
            {"name": name, "path": mascot_outputs[name], "durationMs": durations[name]}
            for name in ("jump_all", "relive", "die")
        ],
    }

    # 6) manifest
    files = {rel: sha256_file(path) for rel, path in sorted(outputs.items())}
    manifest = {
        "resource_version": RESOURCE_VERSION,
        "characters": manifest_characters,
        "fashions": manifest_fashions,
        "account_items": manifest_account,
        "brand": manifest_brand,
        "mascot": manifest_mascot,
        "files": files,
    }
    write_manifest(assets_dir / "manifest.json", manifest)

    if preview_dir is not None:
        build_previews(preview_dir, pack_root, web_root, manifest)

    return manifest


def main(argv):
    args = list(argv)
    pack_root = Path(os.environ.get("STARFALL_ASSET_PACK", DEFAULT_PACK))
    if args and not args[0].startswith("-"):
        pack_root = Path(args.pop(0))
    preview_dir = None
    no_preview = "--no-preview" in args
    if "--preview-dir" in args:
        index = args.index("--preview-dir")
        preview_dir = Path(args[index + 1])
    if preview_dir is None and not no_preview:
        repo_root = Path(__file__).resolve().parents[2]
        preview_dir = repo_root.parent / "server-exports" / "android-04-visual-preview"
    if not pack_root.is_dir():
        raise SystemExit(f"素材包不存在: {pack_root}")
    web_root = Path(__file__).resolve().parents[1] / "web"
    manifest = generate(web_root, pack_root, preview_dir)
    counts = {
        "characters": len(manifest["characters"]),
        "fashions": len(manifest["fashions"]),
        "account_items": len(manifest["account_items"]),
        "files": len(manifest["files"]),
    }
    print(json.dumps(counts, ensure_ascii=False))
    print(f"manifest -> {web_root / 'assets' / 'manifest.json'}")
    if preview_dir is not None:
        print(f"previews -> {preview_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
