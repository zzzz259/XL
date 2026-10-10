# -*- coding: utf-8 -*-
"""图片预览导出工作线程（.skel → PNG，含配对合成 + 皮肤导出 + FGUI 图集切割）"""

import os
import json
import threading
from dataclasses import asdict

from PySide6.QtCore import QThread, Signal

from app.platform.diagnostics import logger, set_task_outcome, task_operation, timed
from app.platform.paths import DATA_DIR

from app.features.preview.fgui import UIPackageTool
from app.features.preview.material_catalog import discover_game_materials, export_game_materials
from app.features.preview.adapter import (
    find_paired_files,
    composite_images,
    composite_with_offset,
    get_animation_names,
    extract_motion_names,
    export_animation_frames,
    export_skel_skins,
    extract_character_id,
)
from app.features.preview.prefab_parser import parse_prefab, compute_pixel_offset, build_cardspine_bundle_map
from app.features.preview.export_plan import ExportSettings


class PreviewExportWorker(QThread):
    """图片预览导出工作线程（.skel → PNG，含配对合成 + 皮肤导出）

    在后台线程执行 SpineViewerCLI 导出，避免阻塞 UI。
    支持去重：force=False 时跳过已存在的 PNG。
    """
    progress = Signal(int, int)            # legacy current, total
    skin_progress = Signal(int, int, str)  # skin-job current, total, label
    finished = Signal(str)                 # summary for identity-based jobs
    export_finished = Signal(bool, str)    # success, summary
    error = Signal(str)

    def __init__(self, jobs, settings=None, runner=None, force=False, selected_roles=None, parent=None):
        super().__init__(parent)
        self._job_mode = callable(runner) and (
            isinstance(settings, ExportSettings)
            or (
                bool(jobs)
                and all(
                    hasattr(job, "record") and hasattr(job, "settings")
                    for job in jobs
                )
            )
        )
        if self._job_mode:
            self.jobs = tuple(jobs)
            self.settings = settings
            self.runner = runner
        else:
            self.material_dir = jobs
            self.output_dir = settings
            self.spine_cli = runner
            self.force = force
            self.selected_roles = selected_roles
        self._cancelled = False
        self._active = False
        self._active_lock = threading.Lock()

    def start(self, priority=None):
        """Start once while active, returning whether a task was started."""
        with self._active_lock:
            if self._active:
                return False
            self._active = True
        try:
            if priority is None:
                super().start()
            else:
                super().start(priority)
        except Exception:
            with self._active_lock:
                self._active = False
            raise
        return True

    def cancel(self):
        self._cancelled = True

    @task_operation("SPINE_EXPORT", "preview", lambda self: {"mode": "jobs" if self._job_mode else "legacy", "job_count": len(self.jobs) if self._job_mode else None})
    def run(self):
        try:
            if self._job_mode:
                self._run_jobs()
            else:
                self._do_export()
        except Exception as e:
            logger.error(f"预览导出线程异常: {e}", exc_info=True)
            set_task_outcome("failed", error_code="SPINE_EXPORT_WORKER_FAILED", message=str(e))
            self.error.emit(str(e))
        finally:
            with self._active_lock:
                self._active = False

    def _run_jobs(self):
        total = len(self.jobs)
        success_count = 0
        failure_count = 0
        cancelled = False

        for current, job in enumerate(self.jobs, start=1):
            if self._cancelled:
                cancelled = True
                break

            label = (
                f"{job.record.resource_family}/{job.settings.format}: "
                f"{job.record.display_name or job.record.skin_name}"
            )
            input_records = getattr(job, "records", (job.record,))
            job_details = {
                "record": asdict(job.record),
                "records": [asdict(record) for record in input_records],
                "settings": asdict(job.settings),
                "export_mode": getattr(job, "export_mode", "custom"),
                "output": str(job.output_path),
                "index": current,
                "total": total,
            }
            logger.info(
                "Spine 导出项开始 [%s/%s] label=%s output=%s",
                current, total, label, job.output_path,
                extra={"event": "spine.export.start", "details": job_details},
            )
            self.skin_progress.emit(current, total, label)
            try:
                job.output_path.parent.mkdir(parents=True, exist_ok=True)
                runner_ok = bool(self.runner(job))
                output_exists = job.output_path.is_file()
                output_bytes = job.output_path.stat().st_size if output_exists else 0
                output_verified = runner_ok and output_exists and output_bytes > 0
                if not output_verified:
                    failure_count += 1
                    logger.error(
                        "Spine 导出项未通过产物校验 label=%s runner_ok=%s output_exists=%s output_bytes=%s output=%s",
                        label, runner_ok, output_exists, output_bytes, job.output_path,
                        extra={"event": "spine.export.failed", "error_code": "SPINE_OUTPUT_NOT_VERIFIED", "details": {**job_details, "runner_ok": runner_ok, "output_exists": output_exists, "output_bytes": output_bytes}},
                    )
                    continue
                self._write_metadata(job)
                success_count += 1
                logger.info(
                    "Spine 导出项完成 label=%s output=%s bytes=%s metadata=%s",
                    label, job.output_path, output_bytes,
                    job.output_path.with_name(job.output_path.name + ".metadata.json"),
                    extra={"event": "spine.export.complete", "details": {**job_details, "output_bytes": output_bytes, "metadata_path": str(job.output_path.with_name(job.output_path.name + ".metadata.json"))}},
                )
            except Exception:
                logger.exception(
                    "Spine 导出项发生异常 label=%s output=%s",
                    label, job.output_path,
                    extra={"event": "spine.export.failed", "error_code": "SPINE_EXPORT_ITEM_FAILED", "details": job_details},
                )
                raise

        if self._cancelled:
            cancelled = True
        summary = f"{success_count} succeeded, {failure_count} failed"
        if cancelled:
            summary += ", cancelled"
        if cancelled:
            outcome = "cancelled"
            error_code = "SPINE_EXPORT_CANCELLED"
        elif failure_count and success_count:
            outcome = "partial"
            error_code = "SPINE_EXPORT_PARTIAL"
        elif failure_count:
            outcome = "failed"
            error_code = "SPINE_EXPORT_FAILED"
        else:
            outcome = "success"
            error_code = None
        set_task_outcome(
            outcome,
            error_code=error_code,
            message=summary,
            details={"total": total, "succeeded": success_count, "failed": failure_count, "cancelled": cancelled},
        )
        logger.info("Spine 导出批次结束 outcome=%s total=%s succeeded=%s failed=%s cancelled=%s", outcome, total, success_count, failure_count, cancelled, extra={"event": "spine.export.summary", "outcome": outcome, "error_code": error_code, "details": {"total": total, "succeeded": success_count, "failed": failure_count, "cancelled": cancelled}})
        self.finished.emit(summary)
        self.export_finished.emit(success_count > 0 and failure_count == 0 and not cancelled, summary)

    @staticmethod
    def _write_metadata(job):
        metadata = {
            "record": asdict(job.record),
            "records": [asdict(record) for record in getattr(job, "records", (job.record,))],
            "settings": asdict(job.settings),
            "export_mode": getattr(job, "export_mode", "custom"),
            "output_path": str(job.output_path),
        }
        metadata_path = job.output_path.with_name(job.output_path.name + ".metadata.json")
        metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

    def _find_assets_map(self):
        """扫描 data/bundles/*/_map/assets_map.json，返回最新一个的路径（用于角色→bundle 映射）"""
        bundles_dir = os.path.join(DATA_DIR, "bundles")
        if not os.path.isdir(bundles_dir):
            return None
        for ts in sorted(os.listdir(bundles_dir), reverse=True):
            map_path = os.path.join(bundles_dir, ts, "_map", "assets_map.json")
            if os.path.isfile(map_path):
                return map_path
        return None

    @timed("图片导出")
    def _do_export(self):
        """执行完整的 .skel 导出流程，返回 (success, summary)"""
        logger.info(f"图片预览导出开始：material={self.material_dir}, output={self.output_dir}, force={self.force}")
        # 扫描 .skel 文件
        skel_files = []
        for root, dirs, files in os.walk(self.material_dir):
            for f in files:
                if f.endswith(".skel"):
                    skel_files.append(os.path.join(root, f))

        # 选中角色过滤：只导出勾选角色（含对应的 _bg 背景）
        if self.selected_roles:
            def _kept(p):
                base = os.path.splitext(os.path.basename(p))[0]
                role = base[:-3] if base.endswith("_bg") else base
                return role in self.selected_roles
            skel_files = [p for p in skel_files if _kept(p)]

        logger.info("Spine 输入扫描完成 material=%s selected_roles=%s skel_count=%s", self.material_dir, sorted(self.selected_roles or ()), len(skel_files), extra={"event": "spine.scan.complete", "details": {"material_dir": self.material_dir, "selected_roles": sorted(self.selected_roles or ()), "skel_count": len(skel_files)}})

        if not skel_files:
            logger.warning("未找到 .skel 文件")
            set_task_outcome("failed", error_code="SPINE_INPUT_NOT_FOUND", message="未找到 .skel 文件", details={"material_dir": self.material_dir})
            self.error.emit("未找到 .skel 文件")
            return False

        os.makedirs(self.output_dir, exist_ok=True)

        # 识别配对
        pairs, unpaired = find_paired_files(skel_files)
        logger.info(f"找到 {len(skel_files)} 个 .skel 文件，其中配对 {len(pairs)} 组，未配对 {len(unpaired)} 个", extra={"event": "spine.pairing.complete", "details": {"skel_count": len(skel_files), "pairs": [[role, bg] for role, bg in pairs], "unpaired": unpaired}})

        success_count = 0
        fail_count = 0
        skipped_count = 0
        composite_count = 0
        total = len(skel_files)
        processed = 0

        # 处理每个 .skel 文件
        for skel_path in skel_files:
            if self._cancelled:
                logger.info("预览导出已取消")
                break

            skel_name = os.path.basename(skel_path)
            base_name = os.path.splitext(skel_name)[0]
            skel_dir = os.path.dirname(skel_path)
            atlas_path = os.path.join(skel_dir, f"{base_name}.atlas")
            # 按角色编号分目录
            char_id = extract_character_id(base_name)
            char_subdir = os.path.join(self.output_dir, char_id)

            processed += 1
            self.progress.emit(processed, total)
            input_details = {"skel": skel_path, "skel_bytes": os.path.getsize(skel_path), "atlas": atlas_path, "character_id": char_id, "resource_name": base_name, "index": processed, "total": total}
            logger.info("Spine 资源处理开始 [%s/%s] name=%s skel=%s atlas=%s", processed, total, base_name, skel_path, atlas_path, extra={"event": "spine.resource.start", "details": input_details})

            if not os.path.exists(atlas_path):
                logger.warning(f"跳过 {skel_name}: 缺少对应的 .atlas 文件")
                logger.error("Spine 输入校验失败：缺少 atlas skel=%s expected_atlas=%s", skel_path, atlas_path, extra={"event": "spine.resource.failed", "error_code": "SPINE_ATLAS_MISSING", "details": input_details})
                skipped_count += 1
                continue

            # 去重检查：force=False 时跳过已存在且非空的 PNG
            main_output = os.path.join(char_subdir, f"{base_name}.png")
            is_battlespine = "battlespine" in os.path.normcase(skel_path)
            if (
                not self.force
                and not is_battlespine
                and os.path.exists(main_output)
                and os.path.getsize(main_output) > 0
            ):
                logger.info(f"跳过已存在的 PNG: {base_name}.png")
                logger.info("Spine 资源跳过（非强制模式且输出已存在） skel=%s output=%s output_bytes=%s", skel_path, main_output, os.path.getsize(main_output), extra={"event": "spine.resource.skipped", "details": {**input_details, "output": main_output, "output_bytes": os.path.getsize(main_output), "reason": "output_exists"}})
                skipped_count += 1
                continue

            os.makedirs(char_subdir, exist_ok=True)
            logger.info(f"查找 atlas: {skel_path} -> {atlas_path}")

            # 获取动画列表
            animations = get_animation_names(skel_path, atlas_path, self.spine_cli)
            if not animations:
                animations = ["idle"]
            logger.info("Spine 动画发现 name=%s animations=%s", base_name, animations, extra={"event": "spine.animations.discovered", "details": {**input_details, "animations": animations}})

            # 导出 idle 动画作为主图
            export_ok = export_animation_frames(
                skel_path,
                atlas_path,
                self.spine_cli,
                char_subdir,
                base_name,
                animations,
                "motion_stander" if is_battlespine else None,
            )
            if export_ok:
                success_count += 1
                expected = os.path.join(char_subdir, f"{base_name}.png")
                logger.info("Spine 静态图导出成功 name=%s output=%s exists=%s bytes=%s", base_name, expected, os.path.isfile(expected), os.path.getsize(expected) if os.path.isfile(expected) else 0, extra={"event": "spine.resource.complete", "details": {**input_details, "output": expected, "output_exists": os.path.isfile(expected), "output_bytes": os.path.getsize(expected) if os.path.isfile(expected) else 0, "animations": animations, "selected_skin": "motion_stander" if is_battlespine else None}})
            else:
                fail_count += 1
                logger.error("Spine 静态图导出失败 name=%s skel=%s atlas=%s", base_name, skel_path, atlas_path, extra={"event": "spine.resource.failed", "error_code": "SPINE_STATIC_EXPORT_FAILED", "details": {**input_details, "animations": animations, "selected_skin": "motion_stander" if is_battlespine else None}})

            # 导出皮肤图片（各表情独立图片）
            skin_names = extract_motion_names(skel_path)
            if skin_names:
                logger.info(f"开始导出皮肤图片: {base_name} ({len(skin_names)} 个皮肤)")
                skin_count = export_skel_skins(
                    skel_path, atlas_path, self.spine_cli, char_subdir, base_name, skin_names
                )
                logger.info(f"皮肤导出完成: {base_name} (成功 {skin_count}/{len(skin_names)})")

        # 处理配对合成（UnityPy 提取背景偏移 → 按偏移合成，无偏移则回退旧合成）
        bundle_map = build_cardspine_bundle_map(self._find_assets_map())
        _offset_cache = {}  # bundle 路径 -> 像素偏移（空间换时间）

        for role_skel, bg_skel in pairs:
            if self._cancelled:
                break

            role_name = os.path.splitext(os.path.basename(role_skel))[0]
            bg_name = os.path.splitext(os.path.basename(bg_skel))[0]
            role_subdir = os.path.join(self.output_dir, extract_character_id(role_name))

            role_png = os.path.join(role_subdir, f"{role_name}.png")
            bg_png = os.path.join(role_subdir, f"{bg_name}.png")

            # 检查两张图片是否存在
            if not os.path.exists(role_png):
                logger.warning(f"跳过合成 {role_name}: 角色图不存在")
                skipped_count += 1
                continue
            if not os.path.exists(bg_png):
                logger.warning(f"跳过合成 {role_name}: 背景图不存在")
                skipped_count += 1
                continue

            # 合成（去重：force=False 时跳过已存在的合成图）
            composite_path = os.path.join(role_subdir, f"{role_name}_composite.png")
            if not self.force and os.path.exists(composite_path) and os.path.getsize(composite_path) > 0:
                logger.info(f"跳过已存在的合成图: {role_name}_composite.png")
                continue

            # 提取背景偏移（有 bundle 映射时才做，结果缓存）
            offset = None
            bundle_path = bundle_map.get(role_name)
            if bundle_path and os.path.isfile(bundle_path):
                if bundle_path not in _offset_cache:
                    _offset_cache[bundle_path] = compute_pixel_offset(parse_prefab(bundle_path))
                off = _offset_cache[bundle_path]
                if off:
                    # Unity (x,y) → Pillow (px,py)，Y 轴翻转
                    offset = (off['pixel_offset'][0], -off['pixel_offset'][1])

            if offset is not None:
                ok = composite_with_offset(role_png, bg_png, offset, composite_path)
            else:
                ok = composite_images(role_png, bg_png, composite_path)

            if ok:
                composite_count += 1
                logger.info(f"合成完成: {composite_path}", extra={"event": "spine.composite.complete", "details": {"role_png": role_png, "background_png": bg_png, "output": composite_path, "offset": offset, "output_bytes": os.path.getsize(composite_path) if os.path.isfile(composite_path) else 0}})
            else:
                fail_count += 1
                logger.error("Spine 部件合成失败 role=%s background=%s output=%s offset=%s", role_png, bg_png, composite_path, offset, extra={"event": "spine.composite.failed", "error_code": "SPINE_COMPOSITE_FAILED", "details": {"role_png": role_png, "background_png": bg_png, "output": composite_path, "offset": offset}})

        summary = (
            f"共找到 {len(skel_files)} 个 .skel 文件\n"
            f"成功导出: {success_count} 个\n"
            f"合成完成: {composite_count} 张\n"
            f"跳过: {skipped_count} 个\n"
            f"失败: {fail_count} 个\n\n"
            f"输出目录:\n{self.output_dir}"
        )
        logger.info(f"预览图片完成: 成功 {success_count}, 合成 {composite_count}, 跳过 {skipped_count}, 失败 {fail_count}")
        # 处理 FGUI 图集切割
        material_summary = self._export_fgui_atlas()
        material_failures = material_summary.failed if material_summary else 0
        cancelled = self._cancelled
        outcome = "cancelled" if cancelled else "partial" if fail_count + material_failures and success_count else "failed" if fail_count + material_failures or success_count == 0 else "success"
        set_task_outcome(outcome, error_code="SPINE_EXPORT_CANCELLED" if cancelled else "SPINE_EXPORT_PARTIAL" if outcome == "partial" else "SPINE_EXPORT_FAILED" if outcome == "failed" else None, message="预览图片导出结束", details={"skel_count": len(skel_files), "exported": success_count, "composites": composite_count, "skipped": skipped_count, "spine_failed": fail_count, "material_failed": material_failures, "cancelled": cancelled, "output_dir": self.output_dir})
        logger.info("Spine 导出汇总 outcome=%s skel_count=%s exported=%s composites=%s skipped=%s spine_failed=%s material_failed=%s", outcome, len(skel_files), success_count, composite_count, skipped_count, fail_count, material_failures, extra={"event": "spine.export.summary", "outcome": outcome, "details": {"skel_count": len(skel_files), "exported": success_count, "composites": composite_count, "skipped": skipped_count, "spine_failed": fail_count, "material_failed": material_failures, "cancelled": cancelled, "output_dir": self.output_dir}})

        self.export_finished.emit(success_count > 0, summary)
        return success_count > 0

    def _export_fgui_atlas(self):
        """Cut every FGUI package from staging into the final output tree.

        ``.bank`` is a container suffix used by the exporter; the parser only
        needs the bytes, so the source is passed through without renaming or
        mutating anything under ``data/material``.
        """
        if not os.path.isdir(self.material_dir):
            logger.info("素材目录不存在，跳过游戏素材导出")
            return None
        catalog = discover_game_materials(self.material_dir)
        output_root = os.path.dirname(os.path.abspath(self.output_dir))
        summary = export_game_materials(
            catalog,
            output_root,
            UIPackageTool.split_atlas_to_package_dir,
        )
        for diagnostic in summary.diagnostics:
            logger.warning("游戏素材导出: %s", diagnostic)
        logger.info(
            "游戏素材导出完成: 成功 %s，失败 %s，输出=%s",
            summary.exported,
            summary.failed,
            output_root,
        )
        return summary
