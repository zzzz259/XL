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
import math
import json
import struct
import gc
import shutil

try:
    from PIL import Image
    PILLOW_AVAILABLE = True
except ImportError:
    PILLOW_AVAILABLE = False

from app.core.logger import logger
from app.core.path_utils import get_tools_dir, get_base_dir


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
        logger.debug(f"--pma 导出失败，尝试不带 --pma")
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

    优先使用 tools/SpineViewer/ffmpeg.exe，若不存在则回退到系统 PATH。
    """
    local_ffmpeg = os.path.join(get_tools_dir(), "SpineViewer", "ffmpeg.exe")
    if os.path.exists(local_ffmpeg):
        logger.debug(f"使用本地 FFmpeg: {local_ffmpeg}")
        return local_ffmpeg
    logger.debug("使用系统 PATH 中的 FFmpeg")
    return "ffmpeg"


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