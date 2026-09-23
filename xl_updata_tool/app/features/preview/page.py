"""Preview 页面：只构造控件并发布语义信号。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPoint, Qt, QSize, Signal, QTimer
from PySide6.QtGui import QIcon, QPixmap, QPainter, QColor, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidgetItem,
    QListWidget,
    QProgressBar,
    QTabBar,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.shared.qt.chrome import (
    create_action_button,
    create_command_bar,
    create_empty_state,
    create_status_label,
)
from .drag_list import DragListWidget
from .output_browser import OutputBrowserCatalog, OutputBrowserEntry, folder_fingerprint
from .spine_tree import PreviewSpineTree
from .workers.image_loader import ImageLoadWorker
from app.ui.theme import get_color


class PreviewTabs(QTabWidget):
    """Named tab container used by the preview page and controller tests."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("previewTabs")
        self.setAccessibleName("预览资源分页")
        self._tab_badges = {}

    def add_named_tab(self, widget: QWidget, title: str, accessible_name: str) -> None:
        widget.setObjectName(accessible_name)
        widget.setAccessibleName(accessible_name)
        index = self.addTab(widget, title)
        # QTabWidget already paints the title supplied to addTab().  The old
        # custom button repeated that title, which produced two labels per tab.
        # Keep only the unread dot in the custom button.
        dot = QLabel("●", self.tabBar())
        dot.setObjectName(f"{accessible_name}UnreadBadge")
        dot.setAccessibleName(f"{title}有未读资源")
        dot.setStyleSheet(f"color: {get_color('DANGER')};")
        dot.setVisible(False)
        self.tabBar().setTabButton(index, QTabBar.RightSide, dot)
        self._tab_badges[title] = dot

    def set_tab_unread(self, title: str, value: bool) -> None:
        badge = self._tab_badges.get(str(title))
        if badge is not None:
            badge.setProperty("unread", bool(value))
            badge.setVisible(bool(value))


class PreviewEmptyState(QLabel):
    """Legacy empty-label facade that only renders on the character tab."""

    def __init__(self, text: str, route, parent=None):
        super().__init__(text, parent)
        self.setObjectName("emptyState")
        self.setAlignment(Qt.AlignCenter)
        self.setAccessibleName(text)
        self._route = route
        self._requested_visible = False

    def setVisible(self, visible: bool) -> None:
        self._requested_visible = bool(visible)
        super().setVisible(self._requested_visible and self._route())

    def sync_visibility(self) -> None:
        super().setVisible(self._requested_visible and self._route())


class PreviewImageList(DragListWidget):
    """Legacy image list with a safe content-change signal for empty routing."""

    content_changed = Signal()

    def addItem(self, *args):
        result = super().addItem(*args)
        self.content_changed.emit()
        return result

    def addItems(self, *args):
        result = super().addItems(*args)
        self.content_changed.emit()
        return result

    def insertItem(self, *args):
        result = super().insertItem(*args)
        self.content_changed.emit()
        return result

    def insertItems(self, *args):
        result = super().insertItems(*args)
        self.content_changed.emit()
        return result

    def clear(self) -> None:
        super().clear()
        self.content_changed.emit()

    def takeItem(self, *args):
        result = super().takeItem(*args)
        self.content_changed.emit()
        return result


class PreviewIconBrowser(QListWidget):
    """Large-icon output folder/file browser used by character and material tabs."""

    path_activated = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setViewMode(QListWidget.IconMode)
        self.setIconSize(QSize(128, 128))
        self.setGridSize(QSize(180, 180))
        self.setResizeMode(QListWidget.Adjust)
        self.setMovement(QListWidget.Static)
        self.setSpacing(12)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setObjectName("previewIconBrowser")
        self._root_path = ""
        self._thumbnail_worker = None
        self._thumbnail_pending: set[str] = set()
        self._thumbnail_loaded: set[str] = set()
        self._file_items: dict[str, QListWidgetItem] = {}
        self._base_icons: dict[str, QIcon] = {}
        self._folder_icon = self._make_folder_icon()
        self._file_icon = self._make_file_icon()
        self.itemDoubleClicked.connect(self._activate_item)
        self.verticalScrollBar().valueChanged.connect(lambda _value: self._schedule_visible_thumbnails())

    def set_entries(self, entries) -> None:
        self._stop_thumbnail_worker()
        self.clear()
        self._file_items.clear()
        self._thumbnail_pending.clear()
        self._thumbnail_loaded.clear()
        self._base_icons.clear()
        for entry in entries:
            if isinstance(entry, OutputBrowserEntry):
                name, path, kind, child_count = entry.name, entry.path, entry.kind, entry.child_count
                fingerprint, is_new = entry.fingerprint, entry.is_new
            else:
                name = str(entry.get("name", "资源"))
                path = str(entry.get("path", ""))
                kind = str(entry.get("kind", "file"))
                child_count = int(entry.get("child_count", 0))
                fingerprint = str(entry.get("fingerprint", ""))
                is_new = entry.get("is_new")
            icon = self._folder_icon if kind == "folder" else self._file_icon
            item = QListWidgetItem(self._icon_with_badge(icon, bool(is_new)), name)
            item.setData(
                Qt.UserRole,
                {
                    "path": path,
                    "kind": kind,
                    "child_count": child_count,
                    "fingerprint": fingerprint,
                    "is_new": is_new,
                },
            )
            item.setToolTip(path)
            self.addItem(item)
            if kind == "file" and path.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tga", ".gif")):
                self._file_items[path] = item
                self._base_icons[path] = icon
        self._schedule_visible_thumbnails()

    def refresh_entry_statuses(self, state=None) -> None:
        for index in range(self.count()):
            item = self.item(index)
            data = item.data(Qt.UserRole) or {}
            path = str(data.get("path", ""))
            kind = str(data.get("kind", "file"))
            fingerprint = str(data.get("fingerprint", ""))
            if state is None:
                is_new = data.get("is_new")
            elif kind == "folder":
                is_new = state.is_new_for(OutputBrowserCatalog(path).file_fingerprints())
            else:
                is_new = state.is_new(fingerprint) if fingerprint else False
            data["is_new"] = is_new
            item.setData(Qt.UserRole, data)
            base_icon = self._base_icons.get(
                path,
                self._folder_icon if kind == "folder" else self._file_icon,
            )
            item.setIcon(self._icon_with_badge(base_icon, bool(is_new)))

    @staticmethod
    def _icon_with_badge(icon, is_new: bool):
        if not is_new:
            return icon
        pixmap = icon.pixmap(QSize(128, 128))
        if pixmap.isNull():
            pixmap = QPixmap(128, 128)
            pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(get_color("DANGER")))
        painter.drawEllipse(pixmap.width() - 28, 4, 22, 22)
        painter.end()
        return QIcon(pixmap)

    def set_root(self, root, state=None) -> None:
        self._root_path = str(root)
        self.set_entries(OutputBrowserCatalog.from_root(root, state=state).entries)

    @property
    def root_path(self) -> str:
        return self._root_path

    def _make_folder_icon(self):
        pixmap = QPixmap(128, 128)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor(get_color("ACCENT")), 3))
        painter.setBrush(QColor(get_color("BG_ELEVATED")))
        painter.drawRoundedRect(14, 30, 100, 78, 10, 10)
        painter.setBrush(QColor(get_color("ACCENT")))
        painter.drawRoundedRect(14, 22, 48, 24, 8, 8)
        painter.end()
        return QIcon(pixmap)

    def _make_file_icon(self):
        pixmap = QPixmap(128, 128)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor(get_color("BORDER")), 3))
        painter.setBrush(QColor(get_color("BG_ELEVATED")))
        painter.drawRoundedRect(28, 14, 72, 100, 8, 8)
        painter.setPen(QPen(QColor(get_color("ACCENT")), 4))
        painter.drawLine(44, 52, 84, 52)
        painter.drawLine(44, 68, 84, 68)
        painter.drawLine(44, 84, 72, 84)
        painter.end()
        return QIcon(pixmap)

    def _schedule_visible_thumbnails(self):
        QTimer.singleShot(80, self._load_visible_thumbnails)

    def _load_visible_thumbnails(self):
        if self._thumbnail_worker is not None:
            return
        visible = []
        viewport_rect = self.viewport().rect()
        for index in range(self.count()):
            item = self.item(index)
            path = str((item.data(Qt.UserRole) or {}).get("path", ""))
            if path not in self._file_items or path in self._thumbnail_loaded:
                continue
            if self.visualItemRect(item).intersects(viewport_rect):
                visible.append(path)
        if not visible:
            visible = list(self._file_items)[:24]
        paths = [path for path in visible if path not in self._thumbnail_pending][:24]
        if not paths:
            return
        self._thumbnail_pending.update(paths)
        worker = ImageLoadWorker(None, self.iconSize().width(), image_paths=paths)
        worker.image_loaded.connect(self._on_thumbnail_loaded)
        worker.finished_loading.connect(self._on_thumbnail_finished)
        worker.finished.connect(self._on_thumbnail_worker_finished)
        self._thumbnail_worker = worker
        worker.start()

    def _on_thumbnail_loaded(self, path, image):
        item = self._file_items.get(str(path))
        if item is not None:
            data = item.data(Qt.UserRole) or {}
            base_icon = QIcon(QPixmap.fromImage(image))
            self._base_icons[str(path)] = base_icon
            item.setIcon(self._icon_with_badge(base_icon, bool(data.get("is_new"))))
        self._thumbnail_loaded.add(str(path))

    def _on_thumbnail_finished(self, _paths):
        # ImageLoadWorker emits this signal before QThread.run() has returned.
        # Keep the worker alive until the inherited finished signal arrives.
        return None

    def _on_thumbnail_worker_finished(self):
        """Release the thumbnail QThread after its native run() has returned."""
        worker = self.sender()
        if worker is self._thumbnail_worker:
            self._thumbnail_worker = None
        if worker is not None:
            worker.deleteLater()
        self._load_visible_thumbnails()

    def _stop_thumbnail_worker(self):
        worker = self._thumbnail_worker
        if worker is not None:
            worker.cancel()
            worker.wait(2000)
        self._thumbnail_worker = None

    def stop_thumbnail_loading(self) -> None:
        """Stop background image decoding before the page is destroyed."""
        self._stop_thumbnail_worker()

    def closeEvent(self, event):
        self.stop_thumbnail_loading()
        super().closeEvent(event)

    def _activate_item(self, item) -> None:
        data = item.data(Qt.UserRole) or {}
        if data.get("kind") == "folder":
            self.path_activated.emit(str(data.get("path", "")))

class PreviewPage(QWidget):
    """图片预览页面，直接拥有控件并只发布页面级语义信号。"""

    close_requested = Signal()
    reload_requested = Signal()
    filter_changed = Signal()
    context_menu_requested = Signal(QPoint)
    item_clicked = Signal(QListWidgetItem)
    item_double_clicked = Signal(QListWidgetItem)
    selection_changed = Signal()
    export_requested = Signal(object)
    spine_selection_changed = Signal(object)
    export_selected_requested = Signal()
    thumbnail_page_changed = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("previewPage")
        self.setAccessibleName("图片预览工作台")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        command_bar, command_layout = create_command_bar(self)
        self.command_layout = command_layout
        self.btn_reload = create_action_button("重新加载图片", "secondary", None, self)
        self.btn_reload.setVisible(False)
        self.btn_mark_all_read = create_action_button("全部标为已读", "secondary", None, self)
        self.btn_mark_all_read.setObjectName("markAllPreviewReadButton")
        command_layout.addWidget(self.btn_mark_all_read)
        self.preview_progress = QProgressBar(self)
        self.preview_progress.setObjectName("previewProgress")
        self.preview_progress.setFixedHeight(24)
        self.preview_progress.setFixedWidth(250)
        self.preview_progress.setVisible(False)
        command_layout.addWidget(self.preview_progress)
        filter_label = QLabel("角色")
        filter_label.setAccessibleName("角色筛选")
        command_layout.addWidget(filter_label)
        self.character_filter = QComboBox(self)
        self.character_filter.setMinimumWidth(140)
        self.character_filter.setToolTip("按角色筛选图片")
        self.character_filter.setAccessibleName("角色筛选")
        self.character_filter.setVisible(False)
        filter_label.setVisible(False)
        self.btn_thumbnail_previous = create_action_button("上一页", "secondary", None, self)
        self.btn_thumbnail_previous.setObjectName("thumbnailPreviousButton")
        self.btn_thumbnail_previous.setAccessibleName("立绘缩略图上一页")
        self.btn_thumbnail_previous.setVisible(False)
        self.thumbnail_page_label = QLabel("第 1/1 页")
        self.thumbnail_page_label.setObjectName("thumbnailPageLabel")
        self.thumbnail_page_label.setAccessibleName("立绘缩略图页码")
        self.thumbnail_page_label.setVisible(False)
        self.btn_thumbnail_next = create_action_button("下一页", "secondary", None, self)
        self.btn_thumbnail_next.setObjectName("thumbnailNextButton")
        self.btn_thumbnail_next.setAccessibleName("立绘缩略图下一页")
        self.btn_thumbnail_next.setVisible(False)
        command_layout.addStretch()
        layout.addWidget(command_bar)

        content = QWidget(self)
        content.setObjectName("viewContent")
        content_layout = QGridLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        self.tabs = PreviewTabs(content)
        self._build_spine_tab()
        self._build_character_tab()
        self._build_material_tab()
        content_layout.addWidget(self.tabs, 0, 0)

        # Keep the legacy empty label directly under viewContent. The controller
        # still owns its visibility, while tab switching keeps it contextual.
        self.empty_label = PreviewEmptyState(
            "暂无图片，请先导出角色立绘",
            lambda: self.tabs.currentWidget() is self.tabs.character_tab,
            content,
        )
        self.empty_label.setVisible(False)
        self.empty_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        content_layout.addWidget(self.empty_label, 0, 0)
        self.image_list.content_changed.connect(self._sync_character_empty_from_items)
        self.tabs.currentChanged.connect(self._sync_character_empty_state)
        self.empty_label.setVisible(self.image_list.count() == 0)
        self._sync_character_empty_state()
        layout.addWidget(content, 1)

        self.preview_status = create_status_label("共 0 张图片", self)
        self.preview_status.setFixedHeight(28)
        layout.addWidget(self.preview_status)

        self.btn_reload.clicked.connect(self.reload_requested)
        self.character_filter.currentIndexChanged.connect(self.filter_changed)
        self.image_list.customContextMenuRequested.connect(self.context_menu_requested)
        self.image_list.itemClicked.connect(self.item_clicked)
        self.image_list.itemDoubleClicked.connect(self.item_double_clicked)
        self.image_list.itemSelectionChanged.connect(self.selection_changed)
        self.btn_thumbnail_previous.clicked.connect(lambda: self.set_thumbnail_page(self._thumbnail_page - 1))
        self.btn_thumbnail_next.clicked.connect(lambda: self.set_thumbnail_page(self._thumbnail_page + 1))
        self.spine_tree.selection_changed.connect(self._on_spine_selection_changed)
        self._material_state = None
        self._character_state = None
        self._material_catalog = None
        self._thumbnail_page = 0
        self._thumbnail_page_size = 60
        self._thumbnail_page_count = 1
        self.image_list.content_changed.connect(self.refresh_thumbnail_pagination)

    def _build_spine_tab(self) -> None:
        self.tabs.spine_tab = QWidget(self.tabs)
        spine_layout = QVBoxLayout(self.tabs.spine_tab)
        spine_layout.setContentsMargins(12, 12, 12, 12)
        spine_command = QHBoxLayout()
        self.btn_export_selected = create_action_button("导出选中 Spine", "primary", None, self.tabs.spine_tab)
        self.btn_export_selected.setObjectName("exportSelectedSpineButton")
        self.btn_export_selected.setAccessibleName("导出选中 Spine 皮肤")
        spine_command.addWidget(self.btn_export_selected)
        spine_command.addStretch()
        spine_layout.addLayout(spine_command)
        self.spine_tree = PreviewSpineTree(parent=self.tabs.spine_tab)
        spine_layout.addWidget(self.spine_tree, 1)
        self.spine_empty_label = create_empty_state("暂无 Spine 资源", self.tabs.spine_tab)
        self.spine_empty_label.setVisible(True)
        spine_layout.addWidget(self.spine_empty_label)
        self.tabs.add_named_tab(self.tabs.spine_tab, "角色 Spine", "角色 Spine 分页")
        self.btn_export_selected.clicked.connect(self.export_selected_requested)

    def _build_character_tab(self) -> None:
        self.tabs.character_tab = QWidget(self.tabs)
        character_layout = QVBoxLayout(self.tabs.character_tab)
        character_layout.setContentsMargins(12, 12, 12, 12)
        character_layout.setSpacing(8)
        character_toolbar = QHBoxLayout()
        self.btn_character_up = create_action_button("返回上级", "secondary", None, self.tabs.character_tab)
        self.btn_character_up.setObjectName("characterOutputUpButton")
        character_toolbar.addWidget(self.btn_character_up)
        self.character_output_path = QLabel("角色导出立绘")
        self.character_output_path.setObjectName("characterOutputPath")
        character_toolbar.addWidget(self.character_output_path)
        self.btn_open_character_folder = create_action_button(
            "打开当前文件夹", "secondary", None, self.tabs.character_tab
        )
        self.btn_open_character_folder.setObjectName("openCharacterOutputFolderButton")
        self.btn_open_character_folder.setAccessibleName("在资源管理器中打开当前立绘文件夹")
        character_toolbar.addWidget(self.btn_open_character_folder)
        character_toolbar.addStretch()
        character_layout.addLayout(character_toolbar)
        self.character_browser = PreviewIconBrowser(self.tabs.character_tab)
        self.character_browser.setAccessibleName("角色导出立绘文件夹浏览")
        character_layout.addWidget(self.character_browser, 1)

        # Legacy thumbnail list remains available to controller/tests during
        # migration, but the visible character surface is output-backed folders.
        self.image_list = PreviewImageList(self.tabs.character_tab)
        self.image_list.setObjectName("previewImageList")
        self.image_list.setAccessibleName("角色导出立绘列表")
        self.image_list.setViewMode(QListWidget.IconMode)
        self.image_list.setIconSize(QSize(150, 150))
        self.image_list.setGridSize(QSize(180, 210))
        self.image_list.setResizeMode(QListWidget.Adjust)
        self.image_list.setMovement(QListWidget.Static)
        self.image_list.setSpacing(10)
        self.image_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.image_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.image_list.setDragEnabled(True)
        self.image_list.setVisible(False)
        self.tabs.add_named_tab(self.tabs.character_tab, "角色导出立绘", "角色导出立绘分页")

    def _build_material_tab(self) -> None:
        self.tabs.material_tab = QWidget(self.tabs)
        material_layout = QVBoxLayout(self.tabs.material_tab)
        material_layout.setContentsMargins(12, 12, 12, 12)
        material_toolbar = QHBoxLayout()
        self.btn_material_up = create_action_button("返回上级", "secondary", None, self.tabs.material_tab)
        self.btn_material_up.setObjectName("materialOutputUpButton")
        material_toolbar.addWidget(self.btn_material_up)
        self.material_output_path = QLabel("游戏素材")
        self.material_output_path.setObjectName("materialOutputPath")
        material_toolbar.addWidget(self.material_output_path)
        self.btn_open_material_folder = create_action_button(
            "打开当前文件夹", "secondary", None, self.tabs.material_tab
        )
        self.btn_open_material_folder.setObjectName("openMaterialOutputFolderButton")
        self.btn_open_material_folder.setAccessibleName("在资源管理器中打开当前游戏素材文件夹")
        material_toolbar.addWidget(self.btn_open_material_folder)
        material_toolbar.addStretch()
        material_layout.addLayout(material_toolbar)
        self.material_tree = QTreeWidget(self.tabs.material_tab)
        self.material_tree.setObjectName("previewMaterialGroups")
        self.material_tree.setAccessibleName("游戏素材分组")
        self.material_tree.setColumnCount(2)
        self.material_tree.setHeaderLabels(["素材组", "状态"])
        self.material_tree.setSelectionMode(QTreeWidget.SingleSelection)
        self.material_tree.setVisible(False)
        self.material_browser = PreviewIconBrowser(self.tabs.material_tab)
        self.material_browser.setAccessibleName("游戏素材文件夹浏览")
        material_layout.addWidget(self.material_browser, 1)
        self.material_empty_label = create_empty_state("暂无游戏素材", self.tabs.material_tab)
        material_layout.addWidget(self.material_empty_label)
        self.tabs.add_named_tab(self.tabs.material_tab, "游戏素材", "游戏素材分页")

    def set_spine_catalog(self, catalog, state=None) -> None:
        """Populate the Spine tab without affecting character thumbnails."""
        if state is not None:
            self.spine_tree.set_resource_state(state)
        if getattr(self, "_character_name_resolver", None) is not None:
            self.spine_tree.set_character_name_resolver(self._character_name_resolver)
        self.spine_tree.set_catalog(catalog)
        self.spine_empty_label.setVisible(self.spine_tree.topLevelItemCount() == 0)

    def set_game_material_catalog(self, catalog, state=None) -> None:
        """Render game-material groups supplied by the Qt-free material catalog."""
        if state is not None:
            self._material_state = state
        self._material_catalog = catalog
        self.material_tree.clear()
        burst_heads = tuple(getattr(catalog, "burst_heads", ()) or ()) if catalog is not None else ()
        atlases = tuple(getattr(catalog, "atlases", ()) or ()) if catalog is not None else ()
        standalone = tuple(getattr(catalog, "standalone", ()) or ()) if catalog is not None else ()
        if burst_heads:
            burst_path = "game_material/burst-head"
            burst = QTreeWidgetItem([burst_path, ""])
            burst.setData(
                0,
                Qt.UserRole,
                {"kind": "burst-head", "source": burst_path, "path": burst_path, "package": ""},
            )
            self.material_tree.addTopLevelItem(burst)
            for record in burst_heads:
                child = QTreeWidgetItem(
                    [str(getattr(record, "display_name", "资源")), self._material_status(record)]
                )
                source_path = str(getattr(record, "source_path", ""))
                child.setData(
                    0,
                    Qt.UserRole,
                    {
                        "kind": "burst-head",
                        "source": source_path,
                        "path": source_path,
                        "package": "",
                        "record": record,
                    },
                )
                burst.addChild(child)
        for atlas in atlases:
            package_name = str(getattr(atlas, "package_name", "未命名图集"))
            group_kind = str(getattr(atlas, "kind", "fgui") or "fgui")
            group_path = f"game_material/{group_kind}/{package_name}"
            group = QTreeWidgetItem([group_path, ""])
            group.setData(
                0,
                Qt.UserRole,
                {
                    "kind": "atlas",
                    "source": str(getattr(atlas, "source_path", "")),
                    "path": group_path,
                    "package": package_name,
                },
            )
            self.material_tree.addTopLevelItem(group)
            for sprite_path in tuple(getattr(atlas, "sprite_paths", ()) or ()):
                sprite_path = str(sprite_path)
                child = QTreeWidgetItem([sprite_path, ""])
                child.setData(
                    0,
                    Qt.UserRole,
                    {
                        "kind": "atlas-sprite",
                        "source": str(getattr(atlas, "source_path", "")),
                        "path": sprite_path,
                        "package": package_name,
                    },
                )
                group.addChild(child)
        standalone_groups = {}
        for record in standalone:
            kind = str(getattr(record, "kind", "unmatched") or "unmatched")
            standalone_groups.setdefault(kind, []).append(record)
        for kind, records in sorted(standalone_groups.items(), key=lambda item: item[0].casefold()):
            group_path = f"game_material/{kind}"
            group = QTreeWidgetItem([group_path, ""])
            group.setData(
                0,
                Qt.UserRole,
                {"kind": kind, "source": group_path, "path": group_path, "package": ""},
            )
            self.material_tree.addTopLevelItem(group)
            for record in records:
                source_path = str(getattr(record, "source_path", ""))
                child = QTreeWidgetItem(
                    [str(getattr(record, "display_name", "资源")), self._material_status(record)]
                )
                child.setData(
                    0,
                    Qt.UserRole,
                    {"kind": kind, "source": source_path, "path": source_path, "package": "", "record": record},
                )
                group.addChild(child)
        self.material_tree.expandAll()
        self.material_empty_label.setVisible(self.material_tree.topLevelItemCount() == 0)
        browser_entries = []
        if burst_heads:
            browser_entries.append(
                OutputBrowserEntry(
                    "burst-head",
                    str(Path(burst_heads[0].source_path).parent),
                    "folder",
                    len(burst_heads),
                    folder_fingerprint(Path(burst_heads[0].source_path).parent),
                    self._folder_has_unread(Path(burst_heads[0].source_path).parent)
                    if self._material_state
                    else None,
                )
            )
        browser_entries.extend(
            OutputBrowserEntry(
                str(getattr(atlas, "package_name", "未命名图集")),
                str(getattr(atlas, "source_path", "")),
                "folder",
                len(tuple(getattr(atlas, "sprite_paths", ()) or ())),
                folder_fingerprint(str(getattr(atlas, "source_path", ""))),
                self._folder_has_unread(str(getattr(atlas, "source_path", "")))
                if self._material_state
                else None,
            )
            for atlas in atlases
        )
        browser_entries.extend(
            OutputBrowserEntry(
                kind,
                str(Path(records[0].source_path).parent),
                "folder",
                len(records),
                folder_fingerprint(str(Path(records[0].source_path).parent)),
                self._folder_has_unread(str(Path(records[0].source_path).parent))
                if self._material_state
                else None,
            )
            for kind, records in sorted(standalone_groups.items(), key=lambda item: item[0].casefold())
            if records
        )
        self.material_browser.set_entries(browser_entries)
        self.material_browser._root_path = ""
        self.material_output_path.setText("游戏素材")
        self.btn_material_up.setEnabled(False)

    set_material_catalog = set_game_material_catalog
    set_game_materials = set_game_material_catalog

    def selected_spine_records(self):
        return self.spine_tree.selected_records()

    def selected_spine_record_groups(self):
        return self.spine_tree.selected_record_groups()

    def selected_spine_skin_names(self):
        return self.spine_tree.selected_skin_names()

    def set_selected_spine_records(self, records) -> None:
        self.spine_tree.set_selected_records(records)

    def mark_selected_spine_read(self) -> None:
        self.spine_tree.mark_selected_read()

    def set_resource_state(self, state) -> None:
        self.spine_tree.set_resource_state(state)

    def set_character_name_resolver(self, resolver) -> None:
        self._character_name_resolver = resolver
        self.spine_tree.set_character_name_resolver(resolver)

    def refresh_unread_badges(self, snapshot) -> None:
        state = self._material_state or self._character_state or self.spine_tree._state
        if state is None:
            return
        self.tabs.set_tab_unread("角色 Spine", snapshot.category_has_unread("spine", state))
        self.tabs.set_tab_unread("角色导出立绘", snapshot.category_has_unread("character", state))
        self.tabs.set_tab_unread("游戏素材", snapshot.category_has_unread("materials", state))
        self.character_browser.refresh_entry_statuses(state)
        self.material_browser.refresh_entry_statuses(state)

    def refresh_spine_catalog(self, catalog, state=None) -> None:
        self.set_spine_catalog(catalog, state)

    def refresh_game_material_catalog(self, catalog, state=None) -> None:
        self.set_game_material_catalog(catalog, state)

    def open_material_output_folder(self, folder, state=None) -> None:
        self.material_browser.set_root(folder, state or self._material_state)
        self.material_output_path.setText(str(folder))
        self.btn_material_up.setEnabled(True)

    def reset_material_output_root(self, catalog=None, state=None) -> None:
        if catalog is not None:
            self.set_game_material_catalog(catalog, state or self._material_state)
        else:
            self.material_browser.set_entries(())
            self.material_output_path.setText("游戏素材")
            self.btn_material_up.setEnabled(False)

    def set_character_output_root(self, root, state=None) -> None:
        root_path = str(root)
        if state is not None:
            self._character_state = state
        self.character_output_path.setText(root_path)
        self.character_browser.set_root(root_path, state)
        self.btn_character_up.setEnabled(False)

    def open_character_output_folder(self, folder, state=None) -> None:
        folder_path = str(folder)
        self.character_output_path.setText(folder_path)
        self.character_browser.set_root(folder_path, state or self._character_state)
        self.btn_character_up.setEnabled(True)

    def reset_character_output_root(self, root, state=None) -> None:
        self.set_character_output_root(root, state)

    def _on_spine_selection_changed(self, records) -> None:
        self.spine_selection_changed.emit(records)
        self.export_requested.emit(records)

    def _sync_character_empty_state(self) -> None:
        self.empty_label.sync_visibility()

    def _sync_character_empty_from_items(self, *_args) -> None:
        self.empty_label.setVisible(self.image_list.count() == 0)

    def set_thumbnail_page(self, page: int) -> None:
        self._thumbnail_page = min(max(0, int(page)), self._thumbnail_page_count - 1)
        self._refresh_thumbnail_visibility()
        self.thumbnail_page_changed.emit(self._thumbnail_page)

    def refresh_thumbnail_pagination(self, *_args) -> None:
        visible_count = sum(
            1
            for index in range(self.image_list.count())
            if self._item_filter_visible(self.image_list.item(index))
        )
        self._thumbnail_page_count = max(1, (visible_count + self._thumbnail_page_size - 1) // self._thumbnail_page_size)
        self._thumbnail_page = min(self._thumbnail_page, self._thumbnail_page_count - 1)
        self._refresh_thumbnail_visibility()

    def _item_filter_visible(self, item) -> bool:
        data = item.data(Qt.UserRole)
        return not isinstance(data, dict) or data.get("_filter_visible", True)

    def _refresh_thumbnail_visibility(self) -> None:
        visible_indices = [
            index
            for index in range(self.image_list.count())
            if self._item_filter_visible(self.image_list.item(index))
        ]
        start = self._thumbnail_page * self._thumbnail_page_size
        allowed = set(visible_indices[start : start + self._thumbnail_page_size])
        for index in range(self.image_list.count()):
            self.image_list.item(index).setHidden(index not in allowed)
        self.thumbnail_page_label.setText(f"第 {self._thumbnail_page + 1}/{self._thumbnail_page_count} 页")
        self.btn_thumbnail_previous.setEnabled(self._thumbnail_page > 0)
        self.btn_thumbnail_next.setEnabled(self._thumbnail_page + 1 < self._thumbnail_page_count)

    @property
    def thumbnail_page(self) -> int:
        return self._thumbnail_page

    @property
    def thumbnail_page_count(self) -> int:
        return self._thumbnail_page_count

    def _material_status(self, record) -> str:
        fingerprint = str(getattr(record, "fingerprint", "") or "")
        if getattr(record, "is_new", None) is not None:
            return "新" if record.is_new else ""
        if self._material_state is not None and fingerprint:
            return "新" if self._material_state.is_new(fingerprint) else ""
        return ""

    def _folder_has_unread(self, folder) -> bool:
        if self._material_state is None:
            return False
        return self._material_state.is_new_for(OutputBrowserCatalog(folder).file_fingerprints())
