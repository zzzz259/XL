# -*- coding: utf-8 -*-
"""Spine 骨骼动画适配器模块

提供 SpineViewerCLI 导出、Spine 二进制文件解析、图片合成、视频导出等
一系列独立函数，供 PreviewExportWorker / CompositeExportWorker 等线程调用。
"""

import os
import re
import subprocess
import sys
import time
import gc
import shutil
from dataclasses import dataclass, field
from collections import defaultdict
import hashlib
import json

try:
    from PIL import Image
    PILLOW_AVAILABLE = True
except ImportError:
    PILLOW_AVAILABLE = False

from app.platform.diagnostics import logger
from app.platform.tool_locator import ToolLocator


@dataclass(frozen=True, slots=True)
class SkinQueryResult:
    """Result of an authoritative Spine skin metadata query."""

    skin_names: tuple[str, ...] = ()
    stdout: str = ""
    stderr: str = ""
    returncode: int = 0
    error: str = ""
    timed_out: bool = False
    attachment_fingerprints: dict[str, str] = field(default_factory=dict)
    identity_fingerprints: dict[str, str] = field(default_factory=dict)
    attachment_fingerprint_kind: str = "unavailable"
    diagnostic: str = ""

    @property
    def skins(self) -> tuple[str, ...]:
        """Compatibility alias for callers that refer to queried skins."""
        return self.skin_names

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.error and not self.timed_out


def parse_skin_query_output(stdout) -> tuple[str, ...]:
    """Parse skin entries from the recognized sections of CLI query output."""
    names = []
    seen = set()
    in_skin_section = False

    def add_name(value):
        value = value.strip()
        if value.startswith(("- ", "* ")):
            value = value[2:].strip()
        if value and value not in seen:
            names.append(value)
            seen.add(value)

    def header_name(value):
        value = value.strip()
        if value.startswith("[") and value.endswith("]"):
            return value[1:-1].strip().casefold()
        if value.endswith(":"):
            return value[:-1].strip().casefold()
        return value.casefold() if value.casefold() in {
            "skin", "skins", "skin name", "skin names",
        } else None

    for raw_line in str(stdout or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        lowered = line.casefold()
        if lowered.startswith("skin:"):
            value = line.split(":", 1)[1].strip()
            if value:
                add_name(value)
                in_skin_section = False
            else:
                in_skin_section = True
            continue
        if lowered.startswith("attachment:"):
            continue
        header = header_name(line)
        if header in {"skin", "skins", "skin name", "skin names"}:
            in_skin_section = True
            continue
        if header is not None:
            in_skin_section = False
            continue
        if not in_skin_section or re.match(
            r"^(?:\[[a-z]+\]|(?:trace|debug|info|warn|warning|error)\b|spineviewercli\b)",
            lowered,
        ):
            continue
        add_name(line)
    return tuple(names)


def _parse_attachment_query_data(stdout):
    """Parse ``Attachment: skin=...;slot=...;name=...`` identity rows."""
    attachment_rows = defaultdict(list)
    for raw_line in str(stdout or "").splitlines():
        line = raw_line.strip()
        if not line.casefold().startswith("attachment:"):
            continue
        values = {}
        for component in line.split(":", 1)[1].split(";"):
            if "=" not in component:
                continue
            key, value = component.split("=", 1)
            key = key.strip().casefold()
            value = " ".join(value.strip().replace("\\", "/").split())
            if key and value:
                values[key] = value
        skin_name = values.pop("skin", None)
        if skin_name and values:
            attachment_rows[skin_name].append(tuple(sorted(values.items())))

    fingerprints = {}
    for skin_name, rows in attachment_rows.items():
        payload = json.dumps(sorted(rows), ensure_ascii=False, separators=(",", ":"))
        fingerprints[skin_name] = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return fingerprints


def _source_skin_identity(skel_path, atlas_path, skin_name):
    identity = {
        "source_skel": os.path.abspath(os.fspath(skel_path)).replace("\\", "/"),
        "atlas_path": os.path.abspath(os.fspath(atlas_path)).replace("\\", "/"),
        "skin_name": skin_name,
    }
    payload = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class SpineQueryRunner:
    """Run SpineViewerCLI metadata queries without hiding process failures."""

    def __init__(self, spine_cli=None, timeout=15):
        self.spine_cli = os.fspath(spine_cli or ToolLocator.create().spineviewer_cli())
        self.timeout = timeout

    def query_skins(self, skel_path, atlas_path) -> SkinQueryResult:
        command = [
            self.spine_cli,
            "query",
            str(skel_path),
            "--atlas",
            str(atlas_path),
            "--skin",
        ]
        try:
            proc = subprocess.run(
                command,
                cwd=os.path.dirname(self.spine_cli) or None,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                env=ToolLocator.create().subprocess_env(),
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
            skin_names = parse_skin_query_output(stdout) if proc.returncode == 0 else ()
            attachment_fingerprints = _parse_attachment_query_data(stdout) if proc.returncode == 0 else {}
            identity_fingerprints = {
                name: _source_skin_identity(skel_path, atlas_path, name) for name in skin_names
            }
            attachment_kind = "attachment_set" if attachment_fingerprints else (
                "source_skin_identity" if skin_names else "unavailable"
            )
            diagnostic = ""
            if skin_names and not attachment_fingerprints:
                diagnostic = (
                    "SpineViewerCLI skin output did not expose attachment sets; "
                    "attachment fingerprint unavailable; using source/skin identity fallback"
                )
            elif attachment_fingerprints.keys() != set(skin_names):
                diagnostic = "SpineViewerCLI returned attachment data for only some skins"
            return SkinQueryResult(
                skin_names=skin_names,
                stdout=stdout,
                stderr=stderr,
                returncode=proc.returncode,
                error=("SpineViewerCLI query failed" if proc.returncode else ""),
                attachment_fingerprints=attachment_fingerprints,
                identity_fingerprints=identity_fingerprints,
                attachment_fingerprint_kind=attachment_kind,
                diagnostic=diagnostic,
            )
        except subprocess.TimeoutExpired as exc:
            return SkinQueryResult(
                stdout=str(getattr(exc, "stdout", "") or ""),
                stderr=str(getattr(exc, "stderr", "") or ""),
                returncode=-1,
                error=f"SpineViewerCLI query timed out after {self.timeout}s",
                timed_out=True,
                diagnostic="SpineViewerCLI skin query timed out",
            )
        except Exception as exc:
            return SkinQueryResult(returncode=-1, error=str(exc))


def extract_character_id(base_name):
    """从 skel base 名提取角色编号（如 cardspine_10080_4 → 10080）；找不到则返回原 base 名"""
    m = re.search(r'(\d{5})', base_name)
    return m.group(1) if m else base_name


# ---------------------------------------------------------------------------
# 配对识别
# ---------------------------------------------------------------------------

def find_paired_files(skel_files):
    """识别 xxx.skel + xxx_bg.skel 配对

    返回: (pairs, unpaired)
      pairs: [(role_skel_path, bg_skel_path), ...]
      unpaired: [skel_path, ...]
    """
    bg_skels = {}
    for s in skel_files:
        name = os.path.splitext(os.path.basename(s))[0]
        if name.endswith("_bg"):
            base = name[:-3]
            bg_skels[base] = s

    pairs = []
    unpaired = []
    used_bg = set()

    for s in skel_files:
        name = os.path.splitext(os.path.basename(s))[0]
        if name.endswith("_bg"):
            continue
        if name in bg_skels:
            logger.info(f"发现配对: {name} + {name}_bg")
            pairs.append((s, bg_skels[name]))
            used_bg.add(name)
        else:
            unpaired.append(s)

    for base, bg_path in bg_skels.items():
        if base not in used_bg:
            unpaired.append(bg_path)

    logger.info(f"配对识别完成：{len(pairs)} 组配对，{len(unpaired)} 个未配对")
    return pairs, unpaired


# ---------------------------------------------------------------------------
# 图片合成
# ---------------------------------------------------------------------------

def composite_images(role_path, bg_path, output_path):
    """将角色图叠加在背景图上，生成合成图"""
    if not PILLOW_AVAILABLE:
        logger.warning("Pillow 未安装，跳过图片合成")
        return False

    try:
        bg_img = Image.open(bg_path).convert("RGBA")
        role_img = Image.open(role_path).convert("RGBA")

        if role_img.size != bg_img.size:
            role_img = role_img.resize(bg_img.size, Image.LANCZOS)

        composite = Image.alpha_composite(bg_img, role_img)
        composite.save(output_path, "PNG")
        logger.info(f"图片合成成功: {output_path}")
        return True
    except Exception as e:
        logger.error(f"图片合成失败: {e}", exc_info=True)
        return False


def composite_with_offset(char_path, bg_path, offset_xy, output_path):
    """按背景偏移合成立绘（不拉伸变形）。

    offset_xy = 背景相对人物的像素偏移（Pillow 坐标，Y 向下已翻转）。
    背景按偏移贴，人物 (0,0) 对齐，画布自动扩展到覆盖两者。
    """
    if not PILLOW_AVAILABLE:
        logger.warning("Pillow 未安装，跳过图片合成")
        return False
    try:
        char_img = Image.open(char_path).convert("RGBA")
        bg_img = Image.open(bg_path).convert("RGBA")
        dx, dy = int(round(offset_xy[0])), int(round(offset_xy[1]))
        # 画布范围：负偏移需要扩展左上角
        min_x = min(0, dx)
        min_y = min(0, dy)
        w = max(char_img.width, bg_img.width + dx) - min_x
        h = max(char_img.height, bg_img.height + dy) - min_y
        canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        canvas.paste(bg_img, (dx - min_x, dy - min_y), bg_img)
        canvas.paste(char_img, (-min_x, -min_y), char_img)
        canvas.save(output_path, "PNG")
        logger.info(f"偏移合成成功: {output_path} (offset={offset_xy})")
        return True
    except Exception as e:
        logger.error(f"偏移合成失败: {e}", exc_info=True)
        return False


# ---------------------------------------------------------------------------
# 动画名称获取
# ---------------------------------------------------------------------------

def get_animation_names(skel_path, atlas_path, spine_cli):
    """使用 SpineViewerCLI query 获取模型的动画名称列表"""
    animations = []
    try:
        cmd = [
            spine_cli, "query", skel_path,
            "--atlas", atlas_path,
            "--animations",
        ]
        logger.debug(f"查询动画列表: {' '.join(cmd)}")
        proc = subprocess.run(
            cmd,
            cwd=os.path.dirname(spine_cli),
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )

        output = proc.stdout.strip()
        if proc.returncode == 0 and output:
            for line in output.split('\n'):
                line = line.strip()
                if line and not line.startswith('#') and not line.startswith('Animation'):
                    animations.append(line)

        if not animations:
            logger.debug(f"CLI 未解析到动画列表，尝试从 .skel 文件提取。stdout: {output[:200]}")
    except subprocess.TimeoutExpired:
        logger.warning(f"查询动画列表超时: {skel_path}")
    except Exception as e:
        logger.warning(f"查询动画列表失败: {e}")

    if not animations:
        animations = extract_motion_names(skel_path)
        if animations:
            logger.info(f"从 .skel 文件提取到 {len(animations)} 个动画名称: {animations}")

    if not animations:
        animations = ["idle"]

    return animations


def extract_motion_names(skel_path):
    """从 .skel 二进制文件提取 motion_* 名称列表（用于动画名和皮肤名）"""
    try:
        with open(skel_path, 'rb') as f:
            data = f.read()
        pattern = re.compile(rb'motion_[a-zA-Z0-9_]+')
        matches = set(match.decode('utf-8') for match in pattern.findall(data))
        exclude = {"motion_group", "motion_dizzy_eye_l", "motion_dizzy_eye_r", "motion_dizzy_mouth"}
        matches = matches - exclude
        matches = {name for name in matches if not re.search(r'\d$', name)}
        if matches:
            return sorted(matches)
    except Exception as e:
        logger.error(f"从 .skel 文件提取名称失败: {e}")
    return []


# ---------------------------------------------------------------------------
# 动画帧导出
# ---------------------------------------------------------------------------

def export_animation_frames(skel_path, atlas_path, spine_cli, output_dir, base_name, animations):
    """导出一个 .skel 文件的所有动画帧

    文件名格式: {base_name}_{animation}.png (idle 动画命名为 {base_name}.png)
    """
    scale = 4
    max_resolution = 8192
    overall_success = False

    for anim_name in animations:
        if anim_name.lower() == "idle":
            output_name = f"{base_name}.png"
        else:
            safe_anim = re.sub(r'[\\/:*?"<>|]', '_', anim_name)
            output_name = f"{base_name}_{safe_anim}.png"

        output_path = os.path.join(output_dir, output_name)
        logger.info(f"导出动画: {anim_name} -> {output_path}")

        export_ok = run_spine_export(
            spine_cli, skel_path, atlas_path, output_path,
            scale, max_resolution, anim_name
        )

        if export_ok:
            overall_success = True
            file_size = os.path.getsize(output_path) if os.path.exists(output_path) else 0
            logger.info(f"导出完成: {output_path} (大小: {file_size} bytes)")
        else:
            logger.warning(f"动画 {anim_name} 导出失败: {skel_path}")

    return overall_success


def export_skel_skins(skel_path, atlas_path, spine_cli, output_dir, base_name, skin_names):
    """导出每个皮肤的独立图片（动画固定为 idle，带 --pma 尝试 + fallback）

    文件名格式: {base_name}_{skin_name}.png
    """
    scale = 4
    max_resolution = 8192
    skin_success = 0

    for skin_name in skin_names:
        safe_skin = re.sub(r'[\\/:*?"<>|]', '_', skin_name)
        output_path = os.path.join(output_dir, f"{base_name}_{safe_skin}.png")

        cmd = [
            spine_cli, "export", skel_path,
            "-f", "Png",
            "-o", output_path,
            "-a", "idle",
            "--atlas", atlas_path,
            "--skins", skin_name,
            "--scale", str(scale),
            "--max-resolution", str(max_resolution),
            "--time", "0",
            "--duration", "1",
            "--fps", "1",
            "--pma",
        ]

        try:
            logger.debug(f"导出皮肤: {skin_name} -> {output_path}")
            proc = subprocess.run(
                cmd,
                cwd=os.path.dirname(spine_cli),
                capture_output=True,
                text=True,
                timeout=60,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            if proc.stderr:
                logger.debug(f"SpineViewerCLI stderr: {proc.stderr[:200]}")

            if proc.returncode == 0 and os.path.exists(output_path):
                file_size = os.path.getsize(output_path)
                logger.info(f"皮肤导出完成: {output_path} (大小: {file_size} bytes)")
                skin_success += 1
                continue

            # fallback: 不带 --pma
            logger.debug(f"--pma 皮肤导出失败，尝试不带 --pma: {skin_name}")
            cmd.remove("--pma")
            proc = subprocess.run(
                cmd,
                cwd=os.path.dirname(spine_cli),
                capture_output=True,
                text=True,
                timeout=60,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            if proc.returncode == 0 and os.path.exists(output_path):
                file_size = os.path.getsize(output_path)
                logger.info(f"皮肤导出完成 (无--pma): {output_path} (大小: {file_size} bytes)")
                skin_success += 1
            else:
                logger.warning(f"皮肤 {skin_name} 导出失败: {skel_path}")
        except subprocess.TimeoutExpired:
            logger.warning(f"皮肤 {skin_name} 导出超时: {skel_path}")
        except Exception as e:
            logger.error(f"皮肤 {skin_name} 导出异常: {e}")

    return skin_success


def run_spine_export(spine_cli, skel_path, atlas_path, output_path, scale, max_resolution, animation):
    """执行 SpineViewerCLI export 命令（带 --pma 尝试 + fallback）"""
    cmd_pma = [
        spine_cli, "export", skel_path,
        "-f", "Png",
        "-o", output_path,
        "-a", animation,
        "--atlas", atlas_path,
        "--scale", str(scale),
        "--max-resolution", str(max_resolution),
        "--time", "0",
        "--duration", "1",
        "--fps", "1",
        "--pma",
    ]

    try:
        logger.debug(f"执行命令: {' '.join(cmd_pma)}")
        proc = subprocess.run(
            cmd_pma,
            cwd=os.path.dirname(spine_cli),
            capture_output=True,
            text=True,
            timeout=60,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        if proc.stderr:
            logger.debug(f"SpineViewerCLI stderr: {proc.stderr[:200]}")

        if proc.returncode == 0 and os.path.exists(output_path):
            return True

        # fallback: 不带 --pma
        logger.debug("--pma 导出失败，尝试不带 --pma")
        cmd_no_pma = [
            spine_cli, "export", skel_path,
            "-f", "Png",
            "-o", output_path,
            "-a", animation,
            "--atlas", atlas_path,
            "--scale", str(scale),
            "--max-resolution", str(max_resolution),
            "--time", "0",
            "--duration", "1",
            "--fps", "1",
        ]
        logger.debug(f"执行命令 (无--pma): {' '.join(cmd_no_pma)}")
        proc = subprocess.run(
            cmd_no_pma,
            cwd=os.path.dirname(spine_cli),
            capture_output=True,
            text=True,
            timeout=60,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        if proc.stderr:
            logger.debug(f"SpineViewerCLI stderr: {proc.stderr[:200]}")

        return proc.returncode == 0 and os.path.exists(output_path)

    except subprocess.TimeoutExpired:
        logger.error(f"导出超时: {skel_path} (动画: {animation})")
        return False
    except Exception as e:
        logger.error(f"导出异常: {e}")
        return False


# ---------------------------------------------------------------------------
# PNG 文件名解析
# ---------------------------------------------------------------------------

def extract_skin_name_from_png(png_path):
    """从 PNG 文件名提取皮肤名（如 motion_angry），无匹配时返回 None"""
    fname = os.path.splitext(os.path.basename(png_path))[0]
    match = re.search(r'(motion_[a-zA-Z0-9_]+)', fname)
    return match.group(1) if match else None


def is_composite_png(png_path):
    """判断是否为合成图（文件名含 _composite）"""
    fname = os.path.splitext(os.path.basename(png_path))[0]
    return fname.endswith("_composite")


def find_composite_sources(png_path, skel_map):
    """从合成图路径解析角色和背景的 .skel/.atlas 路径

    返回 (role_skel, role_atlas, bg_skel, bg_atlas) 或 (None, None, None, None)
    """
    fname = os.path.splitext(os.path.basename(png_path))[0]
    if not fname.endswith("_composite"):
        return None, None, None, None

    base = fname[:-len("_composite")]
    role_entry = skel_map.get(base)
    if not role_entry:
        logger.warning(f"合成图解析: 未找到角色 skel: {base}.skel")
        return None, None, None, None

    bg_entry = skel_map.get(f"{base}_bg")
    if not bg_entry:
        logger.warning(f"合成图解析: 未找到背景 skel: {base}_bg.skel")
        return None, None, None, None

    return role_entry[0], role_entry[1], bg_entry[0], bg_entry[1]


# ---------------------------------------------------------------------------
# 媒体文件导出（MP4 / GIF）
# ---------------------------------------------------------------------------

def export_spine_media_file(spine_cli, skel_path, atlas_path,
                             output_path, animation, duration, fps, scale,
                             fmt="mp4", label="", pma=False, skin_name=None):
    """使用 SpineViewerCLI 直接导出 MP4 或 GIF 文件（带重试）"""
    media_fmt = "Mp4" if fmt == "mp4" else "Gif"
    max_attempts = 2
    last_error = ""

    for attempt in range(1, max_attempts + 1):
        logger.info(f"{label}视频导出尝试 {attempt}/{max_attempts}: {os.path.basename(skel_path)}")

        out_dir = os.path.dirname(output_path)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)

        if os.path.exists(output_path):
            try:
                os.remove(output_path)
            except (PermissionError, OSError) as e:
                logger.warning(f"清理旧输出文件失败 {output_path}: {e}")

        cmd = [
            spine_cli, "export", skel_path,
            "-f", media_fmt,
            "-o", output_path,
            "-a", animation,
            "--atlas", atlas_path,
            "--duration", str(duration),
            "--fps", str(fps),
            "--scale", str(scale),
            "--color", "#00000000",
        ]
        if pma:
            cmd.append("--pma")
        if skin_name:
            cmd.extend(["--skins", skin_name])
        if fmt == "gif":
            cmd.append("--loop")

        logger.debug(f"导出{label}视频: {' '.join(cmd)}")

        try:
            proc = subprocess.run(
                cmd,
                cwd=os.path.dirname(spine_cli),
                capture_output=True,
                text=True,
                timeout=120,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )

            if proc.stderr:
                logger.debug(f"SpineViewerCLI stderr: {proc.stderr[:300]}")

            if proc.returncode == 0 and os.path.exists(output_path):
                file_size = os.path.getsize(output_path)
                logger.info(f"{label}视频导出成功: {output_path} (大小: {file_size} bytes)")
                return True

            err_lines = (proc.stderr or "").strip().splitlines()
            last_error = f"退出码 {proc.returncode}"
            if err_lines:
                last_error += f": {' | '.join(err_lines[:3])}"

        except subprocess.TimeoutExpired:
            last_error = f"导出超时 ({duration}s x {fps}fps)"
            logger.error(f"{label}视频导出超时: {skel_path}")
        except Exception as e:
            last_error = str(e)
            logger.error(f"{label}视频导出异常: {e}")

        if attempt < max_attempts:
            logger.warning(f"{label}视频导出第 {attempt} 次失败，1s 后重试: {last_error}")
            time.sleep(1.0)

    logger.error(f"{label}视频导出最终失败（已重试 {max_attempts} 次）: {last_error[:300]}")
    return False


# ---------------------------------------------------------------------------
# FFmpeg 工具
# ---------------------------------------------------------------------------

def get_ffmpeg_path():
    """获取 FFmpeg 可执行文件路径

    路径解析统一收口到 ToolLocator：优先 tools/SpineViewer/ffmpeg.exe，
    开发模式缺失时回退系统 PATH，冻结模式不回退。
    """
    return ToolLocator.create().ffmpeg()


def ffmpeg_composite_videos(bg_path, role_path, output_path, fps, fmt="mp4"):
    """使用 FFmpeg filter_complex 将角色视频叠加到背景视频上"""
    out_dir = os.path.dirname(output_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    if os.path.exists(output_path):
        try:
            os.remove(output_path)
        except (PermissionError, OSError) as e:
            logger.warning(f"清理旧合成文件失败 {output_path}: {e}")

    ffmpeg_path = get_ffmpeg_path()

    if fmt == "mp4":
        cmd = [
            ffmpeg_path, "-y",
            "-i", bg_path,
            "-i", role_path,
            "-filter_complex",
            "[1:v]colorkey=0x000000:0.1:0.2[role];"
            "[0:v][role]overlay=0:0",
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-crf", "23",
            "-r", str(fps),
            output_path,
        ]
    else:
        cmd = [
            ffmpeg_path, "-y",
            "-i", bg_path,
            "-i", role_path,
            "-filter_complex",
            "[1:v]colorkey=0x000000:0.1:0.2[role];"
            "[0:v][role]overlay=0:0,split[s0][s1];"
            "[s0]palettegen=max_colors=256[p];"
            "[s1][p]paletteuse=alpha_threshold=128",
            "-loop", "0",
            "-r", str(fps),
            output_path,
        ]

    logger.debug(f"FFmpeg合成视频: {' '.join(cmd)}")

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )

        if proc.returncode == 0 and os.path.exists(output_path):
            file_size = os.path.getsize(output_path)
            logger.info(f"FFmpeg合成完成: {output_path} (大小: {file_size} bytes)")
            return True
        else:
            err_msg = (proc.stderr or "").strip()
            logger.error(f"FFmpeg合成失败: {err_msg[-500:]}")
            return False

    except FileNotFoundError:
        logger.error(f"FFmpeg 未找到: {ffmpeg_path}")
        return False
    except subprocess.TimeoutExpired:
        logger.error("FFmpeg 合成超时")
        return False
    except Exception as e:
        logger.error(f"FFmpeg 合成异常: {e}")
        return False


# ---------------------------------------------------------------------------
# 临时目录清理
# ---------------------------------------------------------------------------

def cleanup_temp(temp_dir):
    """清理临时目录（带重试 + 分阶段删除）

    删除策略：
      1) 先尝试清空目录中的所有文件（递归），留给子目录删除更干净的状态；
      2) 使用重试循环删除目录本身，应对 Windows 文件句柄延迟释放；
      3) 最终回退使用 ignore_errors，保证资源尽可能被回收。
    """
    if not os.path.exists(temp_dir):
        return

    if os.path.isdir(temp_dir):
        try:
            for root, dirs, files in os.walk(temp_dir, topdown=False):
                for name in files:
                    try:
                        fp = os.path.join(root, name)
                        if os.path.isfile(fp) or os.path.islink(fp):
                            os.remove(fp)
                    except (PermissionError, OSError) as e:
                        logger.debug(f"删除临时文件失败 {fp}: {e}")
        except Exception as e:
            logger.debug(f"清理临时文件阶段跳过: {e}")

    for attempt in range(3):
        if not os.path.exists(temp_dir):
            break
        try:
            gc.collect()
            shutil.rmtree(temp_dir, ignore_errors=False)
            logger.debug(f"已清理临时目录: {temp_dir}")
            return
        except PermissionError as e:
            logger.warning(f"清理临时目录失败 (尝试 {attempt+1}/3): {e}")
            time.sleep(0.5)
        except FileNotFoundError:
            return
        except Exception as e:
            logger.warning(f"清理临时目录异常: {e}")
            break

    if os.path.exists(temp_dir):
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
            logger.debug(f"已清理临时目录（回退）: {temp_dir}")
        except Exception as e:
            logger.error(f"清理临时目录最终失败: {e}")
