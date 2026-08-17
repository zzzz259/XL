"""Qt 下载线程包装。

网络请求、校验和 delta 计算位于 ``app.core.downloader``；本模块只负责
QThread 生命周期、进度信号和 UI 可消费的结果。
"""

import hashlib
import os
import time

from PySide6.QtCore import QThread, Signal

from app.core.bundle_parser import compute_delta, extract_manifest_hashes, fix_bundle_inplace
from app.core.downloader import BUNDLES_URL, check_update, http_get
from app.core.logger import logger


class CheckUpdateThread(QThread):
    finished = Signal(object, object, object, object)
    error = Signal(str)

    def __init__(self, output_dir, old_hashes=None):
        super().__init__()
        self.output_dir = output_dir
        self.old_hashes = old_hashes

    def run(self):
        try:
            logger.info(
                "检查更新线程开始：已有 %s 个旧 hash",
                len(self.old_hashes) if self.old_hashes else 0,
            )
            info, versions = check_update()
            os.makedirs(self.output_dir, exist_ok=True)

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
                    logger.debug("下载分类包: %s (%s 字节)", fname, len(data))
                else:
                    logger.debug("分类包已缓存: %s", fname)
                categories[name] = out

            new_hashes = set()
            for cat_path in categories.values():
                new_hashes |= extract_manifest_hashes(cat_path)
            logger.info("提取到 %s 个 bundle hash", len(new_hashes))

            delta = compute_delta(self.old_hashes or [], new_hashes)
            logger.info(
                "检查更新完成：新增 %s，移除 %s，未变 %s",
                len(delta["added"]), len(delta["removed"]), delta["common"],
            )
            self.finished.emit(info, versions, sorted(new_hashes), delta)
        except Exception as e:
            logger.error("检查更新异常: %s", e, exc_info=True)
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
        logger.info("下载线程开始：%s 个文件 → %s", len(self.hashes), self.output_dir)
        done = 0
        skipped = 0
        failed = 0
        for h in self.hashes:
            if self._stop:
                break
            fname = f"{h}.bundle"
            url = f"{BUNDLES_URL}/{fname}"
            out = os.path.join(self.output_dir, fname)

            if os.path.exists(out) and os.path.getsize(out) > 100:
                done += 1
                skipped += 1
                self.progress.emit(h, done, len(self.hashes))
                self.item_skip.emit(h, fname)
                continue

            ok = False
            for attempt in range(3):
                if self._stop:
                    break
                try:
                    data = http_get(url)
                    actual_md5 = hashlib.md5(data).hexdigest()
                    if actual_md5.lower() != h.lower():
                        if attempt < 2:
                            time.sleep(1)
                            self.error.emit(f"{h[:16]}...: MD5 mismatch, retry {attempt + 2}/3")
                            continue
                        self.error.emit(f"{h[:16]}...: MD5 failed after 3 attempts")
                        break
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
                self.item_fail.emit(h, "Failed after 3 attempts")

        logger.info(
            "下载线程结束：共 %s 个，成功 %s，跳过 %s，失败 %s",
            len(self.hashes), done - skipped, skipped, failed,
        )
        self.all_done.emit()
