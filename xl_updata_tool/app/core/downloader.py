import json
import os
import hashlib
import ssl
import time
import urllib.request
from urllib.error import URLError, HTTPError

from PySide6.QtCore import QThread, Signal

from .bundle_parser import extract_manifest_hashes, fix_bundle_inplace, compute_delta
from .logger import logger

CDN_BASE = "https://elpis.17995cdn.com"
UPDATE_INFO_URL = f"{CDN_BASE}/Android/UpdateInfo/updateinfo.json"
BUNDLES_URL = f"{CDN_BASE}/Android/Bundles"

HEADERS = {
    "User-Agent": "UnityPlayer/2021.3.45f2c1 (UnityWebRequest/1.0, libcurl/8.5.0-DEV)",
    "X-Unity-Version": "2021.3.45f2c1",
}
SSL_CTX = ssl.create_default_context()


def http_get(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, context=SSL_CTX, timeout=15) as resp:
        return resp.read()


def http_head(url):
    req = urllib.request.Request(url, headers=HEADERS)
    req.method = "HEAD"
    try:
        with urllib.request.urlopen(req, context=SSL_CTX, timeout=5) as resp:
            return True, int(resp.headers.get("Content-Length", 0))
    except HTTPError as e:
        return False, e.code
    except Exception as e:
        return False, str(e)


def check_update():
    raw = http_get(UPDATE_INFO_URL).decode("utf-8")
    update_info = json.loads(raw)
    versions_url = f"{BUNDLES_URL}/{update_info['file']}"
    raw2 = http_get(versions_url).decode("utf-8")
    version_data = json.loads(raw2)
    return update_info, version_data


class CheckUpdateThread(QThread):
    finished = Signal(object, object, object, object)
    error = Signal(str)

    def __init__(self, output_dir, old_hashes=None):
        super().__init__()
        self.output_dir = output_dir
        self.old_hashes = old_hashes

    def run(self):
        try:
            logger.info(f"检查更新线程开始：已有 {len(self.old_hashes) if self.old_hashes else 0} 个旧 hash")
            info, versions = check_update()
            ts = versions["timestamp"]
            os.makedirs(self.output_dir, exist_ok=True)

            # 下载 4 个 category 包
            categories = {}
            for item in versions["data"]:
                name = item["name"].lower()
                fname = f"{name}_{item['hash']}.json"
                url = f"{BUNDLES_URL}/{fname}"
                out = os.path.join(self.output_dir, fname)
                if not os.path.exists(out):
                    data = http_get(url)
                    with open(out, "wb") as f:
                        f.write(data)
                    logger.debug(f"下载分类包: {fname} ({len(data)} 字节)")
                else:
                    logger.debug(f"分类包已缓存: {fname}")
                categories[name] = out

            # 从 category 包提取完整的 bundle hash 清单
            new_hashes = set()
            for cat_name, cat_path in categories.items():
                hashes = extract_manifest_hashes(cat_path)
                new_hashes |= hashes
            logger.info(f"提取到 {len(new_hashes)} 个 bundle hash")

            # 计算 delta
            delta = compute_delta(
                self.old_hashes if self.old_hashes else [], new_hashes
            )
            logger.info(f"检查更新完成：新增 {len(delta['added'])}，移除 {len(delta['removed'])}，未变 {delta['common']}")

            self.finished.emit(info, versions, sorted(new_hashes), delta)

        except Exception as e:
            logger.error(f"检查更新异常: {e}", exc_info=True)
            self.error.emit(str(e))


class DownloadWorker(QThread):
    progress = Signal(str, int, int)
    item_done = Signal(str, str, str)
    item_skip = Signal(str, str)
    item_fail = Signal(str, str)
    all_done = Signal()
    error = Signal(str)

    def __init__(self, hashes, output_dir):
        super().__init__()
        self.hashes = hashes
        self.output_dir = output_dir
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        os.makedirs(self.output_dir, exist_ok=True)
        logger.info(f"下载线程开始：{len(self.hashes)} 个文件 → {self.output_dir}")
        done = 0
        skipped = 0
        failed = 0
        for h in self.hashes:
            if self._stop:
                break
            fname = f"{h}.bundle"
            url = f"{BUNDLES_URL}/{fname}"
            out = os.path.join(self.output_dir, fname)

            # check if already downloaded (skip if file exists and has reasonable size)
            if os.path.exists(out) and os.path.getsize(out) > 100:
                done += 1
                skipped += 1
                self.progress.emit(h, done, len(self.hashes))
                self.item_skip.emit(h, fname)
                continue

            # download and verify (check MD5 BEFORE fixing header)
            ok = False
            for attempt in range(3):
                if self._stop:
                    break
                try:
                    data = http_get(url)
                    # verify against raw downloaded data
                    actual_md5 = hashlib.md5(data).hexdigest()
                    if actual_md5.lower() != h.lower():
                        if attempt < 2:
                            time.sleep(1)
                            self.error.emit(f"{h[:16]}...: MD5 mismatch, retry {attempt+2}/3")
                            continue
                        else:
                            self.error.emit(f"{h[:16]}...: MD5 failed after 3 attempts")
                            break
                    # save and fix
                    with open(out, "wb") as f:
                        f.write(data)
                    fix_bundle_inplace(out)
                    ok = True
                    break
                except Exception as e:
                    if attempt < 2:
                        time.sleep(1)
                    else:
                        self.error.emit(f"{h[:16]}...: {e}")

            if ok:
                done += 1
                self.progress.emit(h, done, len(self.hashes))
                self.item_done.emit(h, fname, out)
            else:
                failed += 1
                self.item_fail.emit(h, f"Failed after 3 attempts")

        logger.info(f"下载线程结束：共 {len(self.hashes)} 个，成功 {done - skipped}，跳过 {skipped}，失败 {failed}")
        self.all_done.emit()


def verify_bundle(filepath, expected_hash):
    """验证下载的文件 MD5 是否匹配"""
    if not os.path.exists(filepath):
        logger.warning(f"MD5 校验：文件不存在 {filepath}")
        return False
    actual = hashlib.md5(open(filepath, "rb").read()).hexdigest()
    match = actual.lower() == expected_hash.lower()
    logger.debug(f"MD5 校验 {'通过' if match else '失败'}: {os.path.basename(filepath)}")
    return match
