"""Qt 下载线程包装。

网络请求、校验和 delta 计算位于 ``app.platform.downloader``；本模块只负责
QThread 生命周期、进度信号和 UI 可消费的结果。
"""

import hashlib
import os
import time

from PySide6.QtCore import QThread, Signal

from app.platform.bundle_parser import compute_delta, extract_manifest_hashes, fix_bundle_inplace
from app.platform.files import atomic_write_bytes
from app.platform.downloader import BUNDLES_URL, check_update, http_get
from app.platform.diagnostics import logger, set_task_outcome, task_operation


class CheckUpdateThread(QThread):
    result_ready = Signal(object, object, object, object)
    error = Signal(str)

    def __init__(self, output_dir, old_hashes=None):
        super().__init__()
        self.output_dir = output_dir
        self.old_hashes = old_hashes

    @task_operation("CHECK_UPDATE", "versions", lambda self: {"known_bundle_hashes": len(self.old_hashes or [])})
    def run(self):
        try:
            logger.info(
                "检查更新线程开始：已有 %s 个旧 hash",
                len(self.old_hashes) if self.old_hashes else 0,
            )
            info, versions = check_update()
            if self.isInterruptionRequested():
                set_task_outcome("cancelled", error_code="UPDATE_CHECK_CANCELLED", message="检查更新已取消")
                return
            os.makedirs(self.output_dir, exist_ok=True)

            categories = {}
            for item in versions["data"]:
                if self.isInterruptionRequested():
                    set_task_outcome("cancelled", error_code="UPDATE_CHECK_CANCELLED", message="检查更新已取消")
                    return
                name = item["name"].lower()
                fname = f"{name}_{item['hash']}.json"
                url = f"{BUNDLES_URL}/{fname}"
                out = os.path.join(self.output_dir, fname)
                if not os.path.exists(out):
                    logger.info("分类索引下载开始 name=%s path=%s", name, out, extra={"event": "catalog.download.start"})
                    data = http_get(url)
                    if self.isInterruptionRequested():
                        set_task_outcome("cancelled", error_code="UPDATE_CHECK_CANCELLED", message="检查更新已取消")
                        return
                    with open(out, "wb") as f:
                        f.write(data)
                    logger.info("分类索引下载完成 name=%s bytes=%s", name, len(data), extra={"event": "catalog.download.complete", "details": {"name": name, "bytes": len(data)}})
                else:
                    logger.info("分类索引命中缓存 name=%s path=%s", name, out, extra={"event": "catalog.cache_hit"})
                categories[name] = out

            new_hashes = set()
            for cat_path in categories.values():
                if self.isInterruptionRequested():
                    set_task_outcome("cancelled", error_code="UPDATE_CHECK_CANCELLED", message="检查更新已取消")
                    return
                logger.info("分类索引解析开始 name=%s path=%s", os.path.basename(cat_path), cat_path, extra={"event": "manifest.parse.start"})
                new_hashes |= extract_manifest_hashes(cat_path)
            if self.isInterruptionRequested():
                set_task_outcome("cancelled", error_code="UPDATE_CHECK_CANCELLED", message="检查更新已取消")
                return
            logger.info("提取到 %s 个 bundle hash", len(new_hashes))

            delta = compute_delta(self.old_hashes or [], new_hashes)
            logger.info(
                "检查更新完成：新增 %s，移除 %s，未变 %s",
                len(delta["added"]), len(delta["removed"]), delta["common"],
            )
            set_task_outcome(
                "success",
                message="更新检查完成",
                details={
                    "catalog_count": len(categories),
                    "bundle_hash_count": len(new_hashes),
                    "added": len(delta["added"]),
                    "removed": len(delta["removed"]),
                    "common": delta["common"],
                },
            )
            self.result_ready.emit(info, versions, sorted(new_hashes), delta)
        except Exception as e:
            logger.error("检查更新异常: %s", e, exc_info=True)
            set_task_outcome("failed", error_code="UPDATE_CHECK_FAILED", message=str(e))
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
        self.outcome = None

    def stop(self):
        self._stop = True

    @task_operation("BUNDLE_DOWNLOAD", "versions", lambda self: {"total": len(self.hashes), "output_dir": self.output_dir})
    def run(self):
        done = 0
        skipped = 0
        failed = 0
        try:
            os.makedirs(self.output_dir, exist_ok=True)
            logger.info("下载线程开始：%s 个文件 → %s", len(self.hashes), self.output_dir)
            for h in self.hashes:
                if self._stop:
                    break
                fname = f"{h}.bundle"
                url = f"{BUNDLES_URL}/{fname}"
                out = os.path.join(self.output_dir, fname)
                self.progress.emit(h, done, len(self.hashes))

                if os.path.exists(out) and os.path.getsize(out) > 100:
                    logger.info("Bundle 下载跳过（已有有效文件） hash=%s size=%s", h, os.path.getsize(out), extra={"event": "bundle.download.skip", "details": {"hash": h, "path": out, "size": os.path.getsize(out)}})
                    done += 1
                    skipped += 1
                    self.progress.emit(h, done, len(self.hashes))
                    self.item_skip.emit(h, fname)
                    continue

                ok = False
                last_error = None
                for attempt in range(3):
                    if self._stop:
                        break
                    try:
                        logger.info("Bundle 下载尝试 hash=%s attempt=%s/3", h, attempt + 1, extra={"event": "bundle.download.attempt", "details": {"hash": h, "attempt": attempt + 1, "max_attempts": 3}})
                        data = http_get(url)
                        if self._stop:
                            break
                        actual_md5 = hashlib.md5(data).hexdigest()
                        if actual_md5.lower() != h.lower():
                            last_error = f"MD5 mismatch (actual {actual_md5})"
                            logger.warning("Bundle 校验失败 hash=%s actual_md5=%s bytes=%s attempt=%s/3", h, actual_md5, len(data), attempt + 1, extra={"event": "bundle.validation.failed", "error_code": "BUNDLE_MD5_MISMATCH", "details": {"hash": h, "actual_md5": actual_md5, "bytes": len(data), "attempt": attempt + 1}})
                            if attempt < 2:
                                time.sleep(1)
                                self.error.emit(
                                    f"{h[:16]}...: MD5 mismatch, retry {attempt + 2}/3"
                                )
                                continue
                            self.error.emit(
                                f"{h[:16]}...: {last_error}, failed after 3 attempts"
                            )
                            break
                        # Manifest entries may be raw video/audio payloads.  Only
                        # run the UnityFS prefix repair when the verified payload
                        # actually contains a UnityFS header; a correct MD5 is
                        # sufficient validation for other asset types.
                        transform = fix_bundle_inplace if b"UnityFS" in data else None
                        if self._stop:
                            break
                        atomic_write_bytes(out, data, transform=transform)
                        logger.info("Bundle 下载并校验完成 hash=%s bytes=%s path=%s header_repaired=%s", h, len(data), out, transform is not None, extra={"event": "bundle.download.complete", "details": {"hash": h, "bytes": len(data), "path": out, "md5": actual_md5, "header_repaired": transform is not None}})
                        ok = True
                        break
                    except Exception as e:
                        last_error = str(e)
                        logger.warning("Bundle 下载失败 hash=%s attempt=%s/3 error=%s", h, attempt + 1, e, extra={"event": "bundle.download.attempt_failed", "error_code": "BUNDLE_DOWNLOAD_FAILED", "details": {"hash": h, "attempt": attempt + 1}})
                        if attempt < 2:
                            time.sleep(1)
                        else:
                            self.error.emit(f"{h[:16]}...: {e}")

                if self._stop:
                    break
                if ok:
                    done += 1
                    self.progress.emit(h, done, len(self.hashes))
                    self.item_done.emit(h, fname, out)
                else:
                    failed += 1
                    self.item_fail.emit(
                        h,
                        f"Failed after 3 attempts: {last_error or 'unknown error'}",
                    )

            if self._stop:
                self.outcome = "cancelled"
                set_task_outcome("cancelled", error_code="BUNDLE_DOWNLOAD_CANCELLED", message="下载已取消", details={"total": len(self.hashes), "downloaded": done - skipped, "skipped": skipped, "failed": failed})
            elif failed:
                self.outcome = "failed"
                set_task_outcome("partial" if done else "failed", error_code="BUNDLE_DOWNLOAD_PARTIAL" if done else "BUNDLE_DOWNLOAD_FAILED", message="部分 Bundle 下载失败" if done else "Bundle 下载失败", details={"total": len(self.hashes), "downloaded": done - skipped, "skipped": skipped, "failed": failed})
            else:
                self.outcome = "success"
                set_task_outcome("success", message="Bundle 下载完成", details={"total": len(self.hashes), "downloaded": done - skipped, "skipped": skipped, "failed": failed})
        except Exception as error:
            self.outcome = "aborted"
            logger.error("下载线程异常: %s", error, exc_info=True)
            set_task_outcome("failed", error_code="BUNDLE_DOWNLOAD_ABORTED", message=str(error), details={"total": len(self.hashes), "downloaded": done - skipped, "skipped": skipped, "failed": failed})
            self.error.emit(str(error))
        finally:
            logger.info(
                "下载线程结束：共 %s 个，成功 %s，跳过 %s，失败 %s，终态 %s",
                len(self.hashes), done - skipped, skipped, failed, self.outcome,
            )
            if self.outcome == "success":
                self.all_done.emit()
