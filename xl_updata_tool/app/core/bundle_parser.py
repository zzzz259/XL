"""AssetBundle manifest 解析 —— 通过 AssetStudio CLI 提取 xasset bundle 清单"""
import os, sys, json, shutil, subprocess, tempfile, re, time
from app.core.logger import logger
from app.core.path_utils import get_base_dir, get_tools_dir

PROJECT_ROOT = get_base_dir()
AS_CLI = os.path.join(get_tools_dir(), "AssetStudio", "AssetStudio.CLI.exe")


def _check_as_cli():
    """检查 AssetStudio.CLI.exe 是否存在，不存在则重试一次"""
    logger.debug(f"检查 AssetStudio.CLI 路径: {AS_CLI}")
    if os.path.exists(AS_CLI):
        return True
    logger.warning(f"AssetStudio.CLI.exe 不存在，1秒后重试: {AS_CLI}")
    time.sleep(1)
    if os.path.exists(AS_CLI):
        logger.info(f"AssetStudio.CLI.exe 重试后可用: {AS_CLI}")
        return True
    logger.error(f"AssetStudio.CLI.exe 不存在: {AS_CLI}")
    return False

MAGIC_UNITYFS = b"UnityFS"


def needs_fix(filepath):
    """检查文件是否需要修复：文件头不是 UnityFS 魔数"""
    try:
        with open(filepath, "rb") as f:
            header = f.read(len(MAGIC_UNITYFS))
            return header != MAGIC_UNITYFS
    except Exception as e:
        logger.warning(f"读取文件头失败 {filepath}: {e}")
        return False


def fix_bundle_inplace(filepath):
    """
    修复 Bundle 文件：搜索 UnityFS 魔数，从该位置开始截取有效数据。
    - 如果文件头已经是 UnityFS（偏移量为 0），则不做任何修改
    - 如果找不到 UnityFS，跳过并记录 WARNING 日志
    - 单个文件失败不中断流程
    """
    try:
        with open(filepath, "rb") as f:
            data = f.read()

        if len(data) < len(MAGIC_UNITYFS):
            logger.warning(f"文件过小，跳过: {os.path.basename(filepath)} ({len(data)} bytes)")
            return False

        if data[:len(MAGIC_UNITYFS)] == MAGIC_UNITYFS:
            logger.debug(f"文件已是有效 Bundle，无需修复: {os.path.basename(filepath)}")
            return True

        idx = data.find(MAGIC_UNITYFS)
        if idx < 0:
            logger.warning(f"未找到 UnityFS 魔数，跳过: {os.path.basename(filepath)}")
            return False

        if idx > 0:
            logger.info(f"修复 Bundle 文件: {os.path.basename(filepath)}, 偏移量: {idx}")
            with open(filepath, "wb") as f:
                f.write(data[idx:])
        return True
    except Exception as e:
        logger.error(f"修复 Bundle 文件失败 {os.path.basename(filepath)}: {e}", exc_info=True)
        return False


def extract_manifest_hashes(category_path):
    """
    用 AssetStudio CLI 将 category AssetBundle 导出为 JSON，
    从中提取所有 sub-bundle 的 hash 列表。
    返回 set of hash strings
    """
    if not _check_as_cli():
        return set()

    logger.debug(f"提取 manifest hash: {os.path.basename(category_path)}")
    out_dir = tempfile.mkdtemp()
    try:
        proc = subprocess.run([
            AS_CLI, category_path, out_dir,
            "--game", "UnityCN", "--key_index", "23",
            "--group_assets", "ByType",
            "--export_type", "Convert",
            "--silent",
        ], cwd=os.path.dirname(AS_CLI),
            capture_output=True, text=True,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            timeout=300,
        )
        if proc.returncode != 0:
            logger.error(f"AssetStudio CLI 失败 (退出码 {proc.returncode}): {proc.stderr[:500]}")
        elif proc.stderr:
            logger.debug(f"AssetStudio CLI stderr: {proc.stderr[:500]}")
        for root, dirs, files in os.walk(out_dir):
            for fname in files:
                if fname.endswith(".json"):
                    fpath = os.path.join(root, fname)
                    try:
                        with open(fpath, "r", encoding="utf-8") as fp:
                            data = json.load(fp)
                        bundles = data.get("bundles", [])
                        if bundles:
                            hashes = set(b["hash"] for b in bundles if b.get("hash"))
                            logger.debug(f"{os.path.basename(category_path)} 提取 {len(hashes)} 个 hash")
                            return hashes
                    except (json.JSONDecodeError, KeyError):
                        continue
        logger.warning(f"{os.path.basename(category_path)} 未提取到 hash")
        return set()
    except subprocess.TimeoutExpired:
        logger.error(f"AssetStudio CLI 执行超时 (300s): {category_path}")
        return set()
    except Exception as e:
        logger.error(f"AssetStudio CLI 执行失败: {e}", exc_info=True)
        return set()
    finally:
        try:
            shutil.rmtree(out_dir, ignore_errors=True)
        except Exception:
            pass


def extract_manifest_from_dir(category_dir, log_cb=None):
    """
    从一个目录下的所有 category 文件提取完整的 bundle hash 列表。
    同时展开每个 bundle 的依赖信息。
    返回 {hash: {"deps": [...], "name": "..."}}
    """
    if not _check_as_cli():
        return {}

    logger.debug(f"提取 manifest（目录）: {category_dir}")
    out_dir = tempfile.mkdtemp()
    result = {}
    try:
        proc = subprocess.run([
            AS_CLI, category_dir, out_dir,
            "--game", "UnityCN", "--key_index", "23",
            "--group_assets", "ByType",
            "--export_type", "Convert",
            "--silent",
        ], cwd=os.path.dirname(AS_CLI),
            capture_output=True, text=True,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            timeout=300,
        )
        if proc.returncode != 0:
            logger.error(f"AssetStudio CLI 失败 (退出码 {proc.returncode}): {proc.stderr[:500]}")
        elif proc.stderr:
            logger.debug(f"AssetStudio CLI stderr: {proc.stderr[:500]}")
        for root, dirs, files in os.walk(out_dir):
            for fname in files:
                if fname.endswith(".json"):
                    fpath = os.path.join(root, fname)
                    try:
                        with open(fpath, "r", encoding="utf-8") as fp:
                            data = json.load(fp)
                        bundles = data.get("bundles", [])
                        for b in bundles:
                            h = b.get("hash")
                            if h:
                                result[h] = {
                                    "name": b.get("name", ""),
                                    "deps": b.get("deps", []),
                                }
                    except (json.JSONDecodeError, KeyError):
                        continue
        logger.debug(f"提取 manifest（目录）: {len(result)} 个 bundle")
        return result
    except subprocess.TimeoutExpired:
        logger.error(f"AssetStudio CLI 执行超时 (300s): {category_dir}")
        return {}
    except Exception as e:
        logger.error(f"AssetStudio CLI 执行失败: {e}", exc_info=True)
        return {}
    finally:
        try:
            shutil.rmtree(out_dir, ignore_errors=True)
        except Exception:
            pass


def compute_delta(old_hashes, new_hashes):
    """计算两个版本之间的 bundle 差异"""
    old_s = set(old_hashes)
    new_s = set(new_hashes)
    return {
        "added": sorted(new_s - old_s),
        "removed": sorted(old_s - new_s),
        "common": len(old_s & new_s),
        "old_total": len(old_s),
        "new_total": len(new_s),
    }


def format_hash_list(hashes, prefix=""):
    """格式化 hash 列表为可读文本"""
    lines = []
    for i, h in enumerate(hashes):
        lines.append(f"{prefix}{i+1:4d}. {h}")
        if i >= 200:
            lines.append(f"{prefix}... 共 {len(hashes)} 个")
            break
    return "\n".join(lines)
