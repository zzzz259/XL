"""Preview 页面：只构造控件并发布语义信号。"""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, QSize, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidgetItem,
    QListWidget,
    QProgressBar,
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
    create_page_header,
    create_status_label,
)
from .drag_list import DragListWidget
from .spine_tree import PreviewSpineTree


class PreviewTabs(QTabWidget):
    """Named tab container used by the preview page and controller tests."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("previewTabs")
        self.setAccessibleName("预览资源分页")

    def add_named_tab(self, widget: QWidget, title: str, accessible_name: str) -> None:
        widget.setObjectName(accessible_name)
        widget.setAccessibleName(accessible_name)
        self.addTab(widget, title)


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

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("previewPage")
        self.setAccessibleName("图片预览工作台")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        top_bar, self.preview_title, self.btn_close_preview = create_page_header(
            "角色预览器 · 共 0 张图片", "关闭预览", None, self
        )
        layout.addWidget(top_bar)

        command_bar, command_layout = create_command_bar(self)
        self.btn_reload = create_action_button("重新加载图片", "secondary", None, self)
        command_layout.addWidget(self.btn_reload)
        filter_label = QLabel("角色")
        filter_label.setAccessibleName("角色筛选")
        command_layout.addWidget(filter_label)
        self.character_filter = QComboBox()
        self.character_filter.setMinimumWidth(140)
        self.character_filter.setToolTip("按角色筛选图片")
        self.character_filter.setAccessibleName("角色筛选")
        command_layout.addWidget(self.character_filter)
        self.preview_progress = QProgressBar()
        self.preview_progress.setObjectName("previewProgress")
        self.preview_progress.setFixedHeight(24)
        self.preview_progress.setFixedWidth(250)
        self.preview_progress.setVisible(False)
        command_layout.addWidget(self.preview_progress)
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

        self.btn_close_preview.clicked.connect(self.close_requested)
        self.btn_reload.clicked.connect(self.reload_requested)
        self.character_filter.currentIndexChanged.connect(self.filter_changed)
        self.image_list.customContextMenuRequested.connect(self.context_menu_requested)
        self.image_list.itemClicked.connect(self.item_clicked)
        self.image_list.itemDoubleClicked.connect(self.item_double_clicked)
        self.image_list.itemSelectionChanged.connect(self.selection_changed)
        self.spine_tree.selection_changed.connect(self._on_spine_selection_changed)
        self._material_state = None

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
        character_layout = QGridLayout(self.tabs.character_tab)
        character_layout.setContentsMargins(0, 0, 0, 0)
        character_layout.setSpacing(0)
        self.image_list = PreviewImageList()
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
        character_layout.addWidget(self.image_list, 0, 0)
        self.tabs.add_named_tab(self.tabs.character_tab, "角色导出立绘", "角色导出立绘分页")

    def _build_material_tab(self) -> None:
        self.tabs.material_tab = QWidget(self.tabs)
        material_layout = QGridLayout(self.tabs.material_tab)
        material_layout.setContentsMargins(12, 12, 12, 12)
        self.material_tree = QTreeWidget(self.tabs.material_tab)
        self.material_tree.setObjectName("previewMaterialGroups")
        self.material_tree.setAccessibleName("游戏素材分组")
        self.material_tree.setColumnCount(2)
        self.material_tree.setHeaderLabels(["素材组", "状态"])
        self.material_tree.setSelectionMode(QTreeWidget.SingleSelection)
        material_layout.addWidget(self.material_tree, 0, 0)
        self.material_empty_label = create_empty_state("暂无游戏素材", self.tabs.material_tab)
        material_layout.addWidget(self.material_empty_label, 1, 0)
        self.tabs.add_named_tab(self.tabs.material_tab, "游戏素材", "游戏素材分页")

    def set_spine_catalog(self, catalog, state=None) -> None:
        """Populate the Spine tab without affecting character thumbnails."""
        if state is not None:
            self.spine_tree.set_resource_state(state)
        self.spine_tree.set_catalog(catalog)
        self.spine_empty_label.setVisible(self.spine_tree.topLevelItemCount() == 0)

    def set_game_material_catalog(self, catalog, state=None) -> None:
        """Render game-material groups supplied by the Qt-free material catalog."""
        if state is not None:
            self._material_state = state
        self.material_tree.clear()
        burst_heads = tuple(getattr(catalog, "burst_heads", ()) or ()) if catalog is not None else ()
        atlases = tuple(getattr(catalog, "atlases", ()) or ()) if catalog is not None else ()
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
            group_path = f"fgui/{package_name}"
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
        self.material_tree.expandAll()
        self.material_empty_label.setVisible(self.material_tree.topLevelItemCount() == 0)

    set_material_catalog = set_game_material_catalog
    set_game_materials = set_game_material_catalog

    def selected_spine_records(self):
        return self.spine_tree.selected_records()

    def set_selected_spine_records(self, records) -> None:
        self.spine_tree.set_selected_records(records)

    def mark_selected_spine_read(self) -> None:
        self.spine_tree.mark_selected_read()

    def set_resource_state(self, state) -> None:
        self.spine_tree.set_resource_state(state)

    def refresh_spine_catalog(self, catalog, state=None) -> None:
        self.set_spine_catalog(catalog, state)

    def refresh_game_material_catalog(self, catalog, state=None) -> None:
        self.set_game_material_catalog(catalog, state)

    def _on_spine_selection_changed(self, records) -> None:
        self.spine_selection_changed.emit(records)
        self.export_requested.emit(records)

    def _sync_character_empty_state(self) -> None:
        self.empty_label.sync_visibility()

    def _sync_character_empty_from_items(self, *_args) -> None:
        self.empty_label.setVisible(self.image_list.count() == 0)

    def _material_status(self, record) -> str:
        fingerprint = str(getattr(record, "fingerprint", "") or "")
        if getattr(record, "is_new", None) is not None:
            return "新" if record.is_new else ""
        if self._material_state is not None and fingerprint:
            return "新" if self._material_state.is_new(fingerprint) else ""
        return ""
