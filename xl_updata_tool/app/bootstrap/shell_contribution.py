"""Composition Root 提供给桌面 Shell 的通用动作与编排贡献。

这里是唯一允许同时看见多个 Feature controller 的 Shell 编排层。MainWindow
只消费动作、页面和激活入口，不再按业务 key 下转具体 Feature 实现。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable

from app.platform import database as db
from app.platform.diagnostics import logger
from app.platform.paths import get_base_dir, get_tools_dir


@dataclass(frozen=True)
class ShellAction:
    """Shell 可呈现的无领域动作。"""

    text: str
    callback: Callable[[], None]
    icon: str
    primary: bool = False


class ApplicationShellContribution:
    """把跨 Feature 的桌面编排收口到 Composition Root。"""

    def __init__(self, registry, import_workflow, context):
        self.registry = registry
        self.import_workflow = import_workflow
        self.context = context
        self.shell = None
        self.actions: tuple[ShellAction, ...] = ()

    def install(self, shell) -> tuple[ShellAction, ...]:
        self.shell = shell
        versions = self.registry.get("versions")
        preview = self.registry.get("preview")
        audio = self.registry.get("audio")
        character = self.registry.get("character")
        importer = self.registry.get("importer")

        importer.controller.progress_stage.connect(shell._on_import_progress)
        importer.controller.stage_finished.connect(shell._on_import_stage_finished)
        importer.controller.category_finished.connect(shell._on_import_category_finished)
        importer.controller.all_finished.connect(shell._on_import_all_finished)
        versions.controller.progress_changed.connect(shell._on_version_progress)
        preview.controller.progress_changed.connect(
            lambda current, total, stage: shell._on_feature_progress(
                preview.page, current, total, stage
            )
        )
        preview.page.close_requested.connect(lambda: self.activate("versions"))
        audio.page.close_requested.connect(lambda: self.activate("versions"))

        self.actions = (
            ShellAction(
                "检查更新",
                versions.controller.check_update,
                "arrows-rotate",
                primary=True,
            ),
            ShellAction("导入AS", self.import_selected, "file-import", primary=True),
        )
        character.controller.restore_local()
        return self.actions

    def activate(self, key: str) -> None:
        """通用 Shell 激活入口；具体 Feature 行为留在本编排层。"""
        if self.shell is None:
            return
        shell = self.shell
        if key not in shell._nav_buttons:
            return
        self.registry.activate(key)
        shell._set_active_view_btn(shell._nav_buttons[key])
        shell._set_toolbars_visible(key == "versions")
        shell._show_character = key == "character"

        versions = self.registry.get("versions")
        preview = self.registry.get("preview")
        audio = self.registry.get("audio")
        character = self.registry.get("character")
        if key == "versions":
            shell._set_version_content_visible(True)
            versions.controller.load()
            shell.schedule(0, audio.controller.preload_catalog)
        else:
            shell._set_version_content_visible(False)
        if key == "preview":
            preview.controller.load()
        elif key == "audio":
            audio.controller.initialize_player()
            audio.controller.load_catalog()
        elif key == "character":
            character.page.raise_()
            character.page.show()
            if not character.controller.data_loaded:
                character.controller.restore_local()

    def import_selected(self) -> None:
        """执行版本选择、AS 导入和后处理启动的 Shell 编排。"""
        shell = self.shell
        if shell is None:
            return
        versions = self.registry.get("versions")
        importer = self.registry.get("importer")

        self.activate("versions")
        shell._set_toolbars_visible(True)
        ts = versions.controller.selected_version
        if not ts:
            shell.show_warning("未选择", "请先选中一个版本.")
            return
        export_categories = shell._get_export_categories()
        if not export_categories:
            shell.show_warning("提示", "请至少勾选一类要导出的资源")
            return

        versions.controller.service.sync_local(ts)
        sub = db.get_sub_bundles(ts)
        delta_only = (
            shell.debug_mode
            and getattr(shell, "debug_import_scope", None)
            and shell.debug_import_scope.currentData()
        )
        if delta_only:
            delta_hashes = versions.controller.service.delta_hashes(ts)
            fs = [
                row[2]
                for row in sub
                if row[2] and os.path.exists(row[2]) and row[0] in delta_hashes
            ]
            logger.info(
                "[导入AS] 增量导入：%s 个增量 bundle（增量 hash %s 个）",
                len(fs), len(delta_hashes),
            )
        else:
            fs = [row[2] for row in sub if row[2] and os.path.exists(row[2])]
        if not fs:
            shell.show_information("无文件", "此版本没有已下载的 bundle，请先下载.")
            return

        bundle_dir = os.path.dirname(fs[0])
        isolate_bundle_dir = False
        if export_categories in ({"lua"}, {"audio"}):
            category = "lua" if export_categories == {"lua"} else "audio"
            selected_fs, mapped, asset_count, map_path = importer.controller.service.select_bundles(
                category, fs, bundle_dir
            )
            if mapped:
                fs = selected_fs
                isolate_bundle_dir = True
                logger.info(
                    "[导入AS] %s 资源映射命中 %s 个资源，筛选 %s/%s 个 bundle",
                    "Lua" if category == "lua" else "音频",
                    asset_count,
                    len(fs),
                    len(sub),
                )
                if not fs:
                    shell.show_information("没有对应资源", "该版本的资源映射中没有找到所选分类的资源。")
                    return
            else:
                logger.warning(
                    "[导入AS] %s 资源映射不可用，无法提前精准筛选 bundle，将兼容扫描已下载包：%s",
                    "Lua" if category == "lua" else "音频",
                    map_path,
                )
                shell.status_bar.showMessage("未找到资源映射，将扫描已下载 bundle")

        as_cli = os.path.join(get_tools_dir(), "AssetStudio", "AssetStudio.CLI.exe")
        if not os.path.exists(as_cli):
            logger.error("AssetStudio.CLI.exe 不存在: %s", as_cli)
            shell.show_warning("错误", f"AssetStudio CLI 不存在:\n{as_cli}")
            return
        if shell._import_worker is not None:
            shell._import_worker.cancel()
            shell._import_worker.wait(2000)

        shell.btn_browse.setEnabled(False)
        shell.dl_progress.setVisible(True)
        shell.dl_progress.setValue(0)
        shell.dl_progress.setFormat("修复文件头: 0/0")
        shell.status_bar.showMessage("正在导入AS: 修复文件头...")
        if len(fs) > 1000:
            accepted = shell.confirm(
                "耗时提示",
                f"本次将导入 {len(fs)} 个 bundle，预计耗时较长（可能数分钟）。\n\n是否继续？",
            )
            if not accepted:
                shell.btn_browse.setEnabled(True)
                shell.dl_progress.setVisible(False)
                return

        logger.info(
            "开始导入AS: 版本 %s, 共 %s 个 bundle 文件，勾选导出分类 %s",
            ts,
            len(fs),
            sorted(export_categories),
        )
        shell._import_worker = importer.controller.start(
            fs,
            bundle_dir,
            as_cli,
            export_categories=export_categories,
            version_timestamp=ts,
            lua_output_dir=os.path.join(str(self.context.output_dir), "lua"),
            isolate_bundle_dir=isolate_bundle_dir,
        )
        shell._import_progress_dialog = shell.create_progress_dialog("正在导入 AS...", "取消")
        shell._import_progress_dialog.canceled.connect(shell._import_worker.cancel)
        shell._import_progress_dialog.show()

    def append_changelog(self, message: str) -> None:
        try:
            out_log = os.path.join(get_base_dir(), "output", "CHANGELOG.md")
            self.registry.get("versions").controller.service.append_changelog(out_log, message)
            logger.info("已写更新日志: %s", out_log)
        except Exception as error:
            logger.warning("写更新日志失败: %s", error)

    def sync_local(self, timestamp=None):
        return self.registry.get("versions").controller.service.sync_local(timestamp)

    def delta_hashes(self, timestamp):
        return self.registry.get("versions").controller.service.delta_hashes(timestamp)

    def selected_version(self):
        return self.registry.get("versions").controller.selected_version

    def delete_version(self, timestamp):
        self.registry.get("versions").controller.delete_version(timestamp)

    def seed(self) -> None:
        self.registry.get("versions").controller.service.seed()

    def schedule_update_check(self) -> None:
        current = self.registry.get("versions").controller.service.current()
        if not current and self.shell is not None:
            self.shell.status_bar.showMessage("首次启动, 自动检查更新...")
            self.shell.schedule(1500, self.registry.get("versions").controller.check_update)
        elif self.shell is not None:
            self.shell.schedule(500, lambda: self.shell.status_bar.showMessage("就绪"))
