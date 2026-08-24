"""角色功能域控制器。"""

from __future__ import annotations

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox, QTableWidgetItem

from app.core.character_profile import build_character_profile
from app.core.logger import logger
from app.features.characters.page import CharacterPage
from app.features.characters.service import CharacterService
from app.ui.theme import DANGER


class CharacterController(QObject):
    """协调角色页面、Lua 解析服务、仓库和未读状态。"""

    status_changed = Signal(str)
    unread_changed = Signal()
    parse_progress = Signal(int, str)

    def __init__(self, page: CharacterPage, service: CharacterService, parent=None):
        super().__init__(parent)
        self.page = page
        self.service = service
        self.characters_full: dict = {}
        self.characters: list[dict] = []
        self.unread: dict[str, str] = {}
        self.word_map: dict = {}
        self.source_version = None
        self.data_loaded = False
        self.loading = False
        self._connect_page()

    @property
    def has_unread(self) -> bool:
        return bool(self.unread)

    def _connect_page(self) -> None:
        self.page.parse_requested.connect(self.manual_load)
        self.page.search_changed.connect(self.filter_characters)
        self.page.refresh_requested.connect(self.refresh)
        self.page.mark_all_read_requested.connect(self.mark_all_read)
        self.page.export_csv_requested.connect(self.export_csv)
        self.page.selection_changed.connect(self.on_selection_changed)

    def _set_status(self, message: str) -> None:
        self.status_changed.emit(message)

    def _apply_state(self, characters_full: dict, unread: dict, version=None) -> None:
        self.characters_full = characters_full or {}
        self.unread = {str(key): value for key, value in (unread or {}).items()}
        self.source_version = version
        self.characters = self.service.derive_index(self.characters_full, self.unread)
        self.data_loaded = bool(self.characters)
        self.populate_table()
        self.unread_changed.emit()

    def restore_local(self) -> bool:
        """恢复角色仓库或旧缓存；绝不因为切换页面触发 Lua 解析。"""
        state = self.service.load_local()
        if not state:
            return False
        self._apply_state(state["characters_full"], state["unread"], state.get("version"))
        self._set_status(
            f"角色数据从{('版本仓库' if state['source'] == 'repository' else '本地缓存')}加载: "
            f"{len(self.characters)} 个角色"
        )
        return self.data_loaded

    def load_data(
        self,
        lua_dir: str | None = None,
        version_timestamp: int | str | None = None,
        force: bool = False,
        automatic: bool = False,
        progress_dialog=None,
    ) -> bool:
        """解析指定 Lua 并增量写入角色仓库。"""
        if self.loading:
            return False
        if lua_dir is None and version_timestamp is None and not force:
            if self.restore_local():
                return True

        if lua_dir is None:
            version_timestamp, lua_dir = self.service.latest_source()
        if not lua_dir or not self.service.has_source(lua_dir):
            self._set_status("未找到完整角色 Lua 数据，请先导出包含 BaseCard/BaseWord 的最新版本")
            return False

        repository = self.service.load_repository()
        if (
            not force
            and version_timestamp is not None
            and repository.get("current_version") == int(version_timestamp)
            and repository.get("current_characters")
        ):
            state = self.service.load_local()
            if state:
                self._apply_state(
                    state["characters_full"],
                    state["unread"],
                    version_timestamp,
                )
            self._set_status(f"角色数据从版本仓库加载: {len(self.characters)} 个角色")
            return self.data_loaded

        self.loading = True
        source_label = f"版本 {version_timestamp}" if version_timestamp is not None else "当前 Lua"
        self._set_status(f"正在解析角色数据（{source_label}）...")
        self._update_progress(0, "准备读取 Lua...", progress_dialog)
        QApplication.processEvents()
        logger.info("开始加载角色数据，version=%s, lua_dir: %s", version_timestamp, lua_dir)

        def on_progress(progress, message):
            self._update_progress(progress, message, progress_dialog)
            QApplication.processEvents()

        try:
            characters, characters_full, word_map = self.service.parse(lua_dir, on_progress)
        except Exception:
            self.loading = False
            raise
        if not characters_full:
            self.loading = False
            self._set_status("角色 Lua 未解析出有效数据，保留已有角色数据")
            return False

        self.word_map = word_map
        if version_timestamp is not None:
            baseline = None
            if not repository.get("current_characters"):
                baseline = self.service.load_cache(validate_source=False)
            merged = self.service.merge_version(
                version_timestamp,
                characters_full,
                source_dir=lua_dir,
                baseline_characters=baseline,
            )
            self._apply_state(
                merged["characters_full"],
                merged["unread"],
                int(version_timestamp),
            )
        else:
            self._apply_state(characters_full, {}, None)

        self._update_progress(100, "角色数据解析完成", progress_dialog)
        self.loading = False
        action = "自动解析完成" if automatic else "角色数据加载完成"
        self._set_status(f"{action}: {len(self.characters)} 个角色，{len(self.unread)} 个新/变更")
        try:
            self.service.save_legacy_cache(self.characters_full, lua_dir)
            logger.info("已留存完整角色数据到 %s", self.service.cache_file)
        except Exception as error:
            logger.warning("留存角色数据失败: %s", error)
        return True

    def _update_progress(self, progress: int, message: str, progress_dialog=None) -> None:
        self.parse_progress.emit(progress, message)
        if progress_dialog is not None:
            progress_dialog.setLabelText(f"自动解析角色数据\n{message}")
            progress_dialog.setRange(0, 100)
            progress_dialog.setValue(progress)
        self._set_status(message)

    def populate_table(self) -> None:
        table = self.page.character_table
        table.setSortingEnabled(False)
        table.setRowCount(0)
        self.page.character_profile_view.clear_profile()
        count = len(self.characters)
        self.page.character_empty.setVisible(count == 0)
        if count == 0:
            self.page.character_status.setText("暂无角色数据")
            return

        table.setRowCount(count)
        for row, character in enumerate(self.characters):
            index_item = QTableWidgetItem(str(character.get("display_index", row + 1)))
            index_item.setTextAlignment(Qt.AlignCenter)
            table.setItem(row, 0, index_item)
            table.setItem(row, 1, QTableWidgetItem(character.get("name", "未知")))
            badge_item = QTableWidgetItem("新" if character.get("change_status") else "")
            badge_item.setTextAlignment(Qt.AlignCenter)
            if character.get("change_status"):
                badge_item.setForeground(QBrush(QColor(DANGER)))
                badge_item.setToolTip("新版本新增或数据发生变化，打开详情后清除")
            table.setItem(row, 2, badge_item)
        self.page.character_status.setText(f"共 {count} 个角色")

    def filter_characters(self, text: str) -> None:
        normalized = text.lower()
        table = self.page.character_table
        for row in range(table.rowCount()):
            item = table.item(row, 1)
            table.setRowHidden(row, not item or normalized not in item.text().lower())

    def refresh(self) -> None:
        self.load_data(force=True)

    def on_selection_changed(self) -> None:
        rows = self.page.character_table.selectionModel().selectedRows()
        if not rows:
            self.page.character_profile_view.clear_profile()
            return
        row = rows[0].row()
        if row < 0 or row >= len(self.characters):
            return
        character_index = self.characters[row]
        char_id = character_index.get("char_id", 0)
        character = self.characters_full.get(char_id, {})
        if not character:
            for candidate in self.characters_full.values():
                if candidate.get("raw_id") == character_index.get("raw_id"):
                    character = candidate
                    break
        if not character:
            return
        if self.service.clear_unread(char_id):
            self.unread.pop(str(char_id), None)
            character_index["change_status"] = None
            badge = self.page.character_table.item(row, 2)
            if badge:
                badge.setText("")
            self.unread_changed.emit()
        self.page.character_profile_view.set_profile(build_character_profile(character))

    def manual_load(self) -> None:
        if self.loading:
            return
        version, lua_dir = self.service.latest_source()
        if not lua_dir or not self.service.has_source(lua_dir):
            QMessageBox.information(
                self.page,
                "提示",
                "未找到完整角色 Lua 数据。\n\n请先导出包含 BaseCard/BaseWord 的版本后再解析。",
            )
            self._set_status("请先导入AS")
            return
        self.load_data(lua_dir=lua_dir, version_timestamp=version, force=True)

    def mark_all_read(self) -> None:
        cleared = self.service.clear_all_unread()
        self.unread.clear()
        for character in self.characters:
            character["change_status"] = None
        self.populate_table()
        self.unread_changed.emit()
        self._set_status("已将全部角色数据标记为已读" if cleared else "当前没有未读角色数据")

    def export_csv(self) -> None:
        if not self.characters_full:
            QMessageBox.information(self.page, "提示", "没有角色数据可导出，请先加载角色视图。")
            return
        file_path, _ = QFileDialog.getSaveFileName(
            self.page, "保存 CSV 文件", "characters.csv", "CSV 文件 (*.csv)"
        )
        if not file_path:
            return
        self._set_status("正在生成角色数据...")
        QApplication.processEvents()
        count = self.service.export_csv(file_path, self.characters_full)
        if not count:
            QMessageBox.information(self.page, "提示", "未找到匹配的角色数据。")
            self._set_status("CSV 导出失败：无匹配角色")
            return
        logger.info("CSV 导出完成: %s (%s 个角色)", file_path, count)
        self._set_status(f"CSV 导出完成: {count} 个角色")

    def auto_parse_after_lua_export(self, result: dict | None, progress_dialog=None) -> bool:
        """仅在最新已发布 Lua 且 Base 文件齐全时自动解析。"""
        if not result:
            return False
        version = result.get("version")
        lua_dir = result.get("directory")
        latest_version, _ = self.service.latest_source()
        if not self.service.should_auto_parse(version, latest_version, lua_dir):
            if not result.get("character_sources"):
                self._set_status("Lua 已按版本留存，但缺少角色 Base 文件，未自动解析")
            else:
                self._set_status(f"历史 Lua 已留存（版本 {version}），非最新版本，不自动解析角色")
            return False
        return self.load_data(
            lua_dir=lua_dir,
            version_timestamp=version,
            force=True,
            automatic=True,
            progress_dialog=progress_dialog,
        )
