import os, json, re, shutil, subprocess, sys, time, urllib.request, ssl, struct
from enum import Enum
from pathlib import Path
from datetime import datetime, timedelta

try:
    from PIL import Image
    PILLOW_AVAILABLE = True
except ImportError:
    PILLOW_AVAILABLE = False

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QProgressBar, QMessageBox, QToolBar, QStatusBar, QApplication,
    QTableWidget, QTableWidgetItem, QAbstractItemView, QToolButton, QHeaderView,
    QScrollArea, QGridLayout, QFrame, QDialog, QSizePolicy,
    QListWidget, QListWidgetItem, QFileDialog, QCheckBox, QMenu,
    QSpinBox, QComboBox, QFormLayout, QSlider,
    QGraphicsScene, QGraphicsView,
    QLineEdit, QTextEdit,
)
from PySide6.QtCore import Qt, QTimer, QThread, Signal, QSize, QEvent, QMimeData, QUrl
from PySide6.QtGui import QColor, QPixmap, QPalette, QKeySequence, QShortcut, QClipboard, QPainter

try:
    from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
    QT_MULTIMEDIA_AVAILABLE = True
except ImportError:
    QT_MULTIMEDIA_AVAILABLE = False

from .theme import (
    BASE_STYLESHEET, ACCENT, BG_SURFACE, BG_DARK, BG_ELEVATED, BORDER,
    TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED, SUCCESS, WARNING, DANGER, INFO,
)
from .panels import ticks_to_date
from app.core.version_manager import VersionManager
from app.core.downloader import CheckUpdateThread, DownloadWorker
from app.core.bundle_parser import extract_manifest_hashes, fix_bundle_inplace
from app.core import database as db
from app.core.logger import logger
from app.core.path_utils import get_data_dir, get_base_dir, get_tools_dir

DATA_DIR = get_data_dir()
BUNDLES_DIR = os.path.join(DATA_DIR, "bundles")


# ========== Image Preview Worker & Dialog ==========

class ImageLoadWorker(QThread):
    """异步加载图片缩略图的工作线程"""
    progress = Signal(int, int)           # current, total
    image_loaded = Signal(str, object)    # path, QPixmap
    finished_loading = Signal(list)       # list of paths

    def __init__(self, image_dir, thumb_size=150):
        super().__init__()
        self.image_dir = image_dir
        self.thumb_size = thumb_size
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        if not os.path.isdir(self.image_dir):
            self.finished_loading.emit([])
            return

        png_files = sorted([f for f in os.listdir(self.image_dir) if f.lower().endswith(".png")])
        total = len(png_files)
        if total == 0:
            self.finished_loading.emit([])
            return

        loaded_paths = []
        for i, fname in enumerate(png_files):
            if self._cancelled:
                break
            fpath = os.path.join(self.image_dir, fname)
            try:
                pixmap = QPixmap(fpath)
                if not pixmap.isNull():
                    thumb = self._create_thumbnail(pixmap, self.thumb_size)
                    self.image_loaded.emit(fpath, thumb)
                    loaded_paths.append(fpath)
            except Exception as e:
                logger.error(f"加载图片失败 {fname}: {e}")
            self.progress.emit(i + 1, total)

        self.finished_loading.emit(loaded_paths)

    def _create_thumbnail(self, pixmap, size):
        """缩放并居中裁剪为正方形缩略图"""
        scaled = pixmap.scaled(size, size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        x = (scaled.width() - size) // 2
        y = (scaled.height() - size) // 2
        return scaled.copy(x, y, size, size)


class ImageViewerDialog(QDialog):
    """大图预览窗口，使用 QGraphicsView 实现无滚动条的缩放/拖拽预览"""

    def __init__(self, image_paths, current_index=0, parent=None):
        super().__init__(parent)
        self.image_paths = image_paths
        self.current_index = current_index
        self.scale_factor = 1.0
        self.original_pixmap = QPixmap()

        self.setWindowTitle("图片预览")
        self.resize(900, 700)
        self.setStyleSheet(f"background-color:{BG_DARK};")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 顶部栏：文件名 + 关闭按钮
        top_bar = QFrame()
        top_bar.setFixedHeight(44)
        top_bar.setStyleSheet(f"QFrame {{ background-color:{BG_SURFACE}; border-bottom:1px solid {BORDER}; }}")
        top_layout = QHBoxLayout(top_bar)
        top_layout.setContentsMargins(12, 0, 12, 0)

        self.fname_label = QLabel()
        self.fname_label.setStyleSheet(f"color:{TEXT_PRIMARY}; font-size:13px; font-weight:600; background:transparent;")
        top_layout.addWidget(self.fname_label)
        top_layout.addStretch()

        btn_close = QPushButton("✕")
        btn_close.setFixedSize(32, 28)
        btn_close.setStyleSheet(f"""
            QPushButton {{ background-color:transparent; border:none; border-radius:4px;
                          color:{TEXT_SECONDARY}; font-size:16px; font-weight:600; }}
            QPushButton:hover {{ background-color:{DANGER}; color:#fff; }}
        """)
        btn_close.clicked.connect(self.close)
        top_layout.addWidget(btn_close)
        layout.addWidget(top_bar)

        # 图片区域：QGraphicsView + QGraphicsScene
        self.scene = QGraphicsScene(self)
        self.pixmap_item = self.scene.addPixmap(QPixmap())

        self.view = QGraphicsView(self.scene)
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.view.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.view.setDragMode(QGraphicsView.ScrollHandDrag)
        self.view.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.view.setResizeAnchor(QGraphicsView.AnchorUnderMouse)
        self.view.setStyleSheet(f"QGraphicsView {{ border:none; background-color:{BG_DARK}; }}")
        self.view.setRenderHints(QPainter.SmoothPixmapTransform | QPainter.Antialiasing)
        layout.addWidget(self.view, 1)

        # 底部栏：上一张 / 信息 / 下一张
        bottom_bar = QFrame()
        bottom_bar.setFixedHeight(50)
        bottom_bar.setStyleSheet(f"QFrame {{ background-color:{BG_SURFACE}; border-top:1px solid {BORDER}; }}")
        bottom_layout = QHBoxLayout(bottom_bar)
        bottom_layout.setContentsMargins(12, 0, 12, 0)

        self.btn_prev = QPushButton("◀ 上一张")
        self.btn_prev.setFixedSize(100, 32)
        self.btn_prev.setStyleSheet(f"""
            QPushButton {{ background-color:{BG_ELEVATED}; border:1px solid {BORDER}; border-radius:6px;
                          color:{TEXT_PRIMARY}; font-size:12px; font-weight:600; }}
            QPushButton:hover {{ border-color:{ACCENT}; }}
            QPushButton:disabled {{ color:{TEXT_MUTED}; opacity:0.5; }}
        """)
        self.btn_prev.clicked.connect(self._prev_image)
        bottom_layout.addWidget(self.btn_prev)

        self.info_label = QLabel()
        self.info_label.setAlignment(Qt.AlignCenter)
        self.info_label.setStyleSheet(f"color:{TEXT_SECONDARY}; font-size:12px; background:transparent;")
        bottom_layout.addWidget(self.info_label, 1)

        self.btn_next = QPushButton("下一张 ▶")
        self.btn_next.setFixedSize(100, 32)
        self.btn_next.setStyleSheet(f"""
            QPushButton {{ background-color:{BG_ELEVATED}; border:1px solid {BORDER}; border-radius:6px;
                          color:{TEXT_PRIMARY}; font-size:12px; font-weight:600; }}
            QPushButton:hover {{ border-color:{ACCENT}; }}
            QPushButton:disabled {{ color:{TEXT_MUTED}; opacity:0.5; }}
        """)
        self.btn_next.clicked.connect(self._next_image)
        bottom_layout.addWidget(self.btn_next)
        layout.addWidget(bottom_bar)

        # 快捷键
        self._esc_shortcut = QShortcut(QKeySequence(Qt.Key_Escape), self)
        self._esc_shortcut.activated.connect(self.close)

        self._load_current_image()

    def _load_current_image(self):
        if not self.image_paths:
            self.info_label.setText("0 / 0")
            return

        path = self.image_paths[self.current_index]
        self.original_pixmap = QPixmap(path)

        fname = os.path.basename(path)
        self.fname_label.setText(fname)
        self.setWindowTitle(f"图片预览 - {fname}")

        self.pixmap_item.setPixmap(self.original_pixmap)
        self.scene.setSceneRect(self.original_pixmap.rect())

        self._fit_to_view()
        self._update_nav_info()

    def _fit_to_view(self):
        """自适应缩放适应视口"""
        self.view.fitInView(self.pixmap_item, Qt.KeepAspectRatio)
        self.scale_factor = self.view.transform().m11()

    def _update_nav_info(self):
        total = len(self.image_paths)
        idx = self.current_index + 1
        if not self.original_pixmap.isNull():
            w = self.original_pixmap.width()
            h = self.original_pixmap.height()
            self.info_label.setText(f"{idx} / {total}  |  尺寸: {w} × {h}")
        else:
            self.info_label.setText(f"{idx} / {total}")

        self.btn_prev.setEnabled(self.current_index > 0)
        self.btn_next.setEnabled(self.current_index < total - 1)

    def _prev_image(self):
        if self.current_index > 0:
            self.current_index -= 1
            self._load_current_image()

    def _next_image(self):
        if self.current_index < len(self.image_paths) - 1:
            self.current_index += 1
            self._load_current_image()

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        factor = 1.15 if delta > 0 else 1.0 / 1.15
        self.view.scale(factor, factor)
        self.scale_factor = self.view.transform().m11()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.close()
        elif event.key() == Qt.Key_Left:
            self._prev_image()
        elif event.key() == Qt.Key_Right:
            self._next_image()
        super().keyPressEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        if not self.original_pixmap.isNull():
            self._fit_to_view()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self.original_pixmap.isNull():
            self._fit_to_view()


class ExportSettingsDialog(QDialog):
    """导出参数设置对话框"""

    def __init__(self, skel_path, atlas_path, default_format="MP4", parent=None):
        super().__init__(parent)
        self.setWindowTitle("导出设置")
        self.setMinimumWidth(420)
        self.setStyleSheet(f"""
            QDialog {{ background-color:{BG_SURFACE}; color:{TEXT_PRIMARY}; }}
            QLabel {{ color:{TEXT_PRIMARY}; font-size:13px; background:transparent; border:none; }}
            QLabel#fileLabel {{ color:{TEXT_SECONDARY}; font-size:11px; padding:6px;
                               background-color:{BG_DARK}; border:1px solid {BORDER}; border-radius:4px; }}
            QComboBox, QSpinBox {{
                background-color:{BG_DARK}; border:1px solid {BORDER}; border-radius:4px;
                padding:5px 8px; color:{TEXT_PRIMARY}; font-size:13px; min-height:24px;
            }}
            QComboBox::drop-down {{ border:none; width:20px; }}
            QCheckBox {{ color:{TEXT_PRIMARY}; font-size:13px; }}
            QPushButton {{ padding:8px 20px; border-radius:6px; border:none; color:#fff; font-size:13px; font-weight:600; }}
            QPushButton#okBtn {{ background-color:{ACCENT}; }}
            QPushButton#okBtn:hover {{ opacity:0.85; }}
            QPushButton#cancelBtn {{ background-color:{BG_ELEVATED}; border:1px solid {BORDER}; color:{TEXT_PRIMARY}; }}
            QPushButton#cancelBtn:hover {{ border-color:{ACCENT}; }}
        """)

        self.skel_path = skel_path
        self.atlas_path = atlas_path
        self._build_ui(default_format)

    def _build_ui(self, default_format):
        skel_base = os.path.splitext(os.path.basename(self.skel_path))[0]
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        # 标题
        title = QLabel("导出 Spine 动画")
        title.setStyleSheet(f"font-size:16px; font-weight:bold; color:{ACCENT};")
        layout.addWidget(title)

        # 表单
        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignRight)

        # 输出格式
        self.format_combo = QComboBox()
        self.format_combo.addItems(["MP4", "GIF"])
        self.format_combo.setCurrentText(default_format)
        self.format_combo.currentIndexChanged.connect(self._update_file_label)
        form.addRow("输出格式:", self.format_combo)

        # 动画名称
        self.anim_combo = QComboBox()
        self.anim_combo.addItems(["idle"])
        self.anim_combo.setCurrentText("idle")
        form.addRow("动画名称:", self.anim_combo)

        # 时长
        self.duration_spin = QSpinBox()
        self.duration_spin.setRange(1, 10)
        self.duration_spin.setValue(2)
        self.duration_spin.setSuffix(" 秒")
        form.addRow("时长:", self.duration_spin)

        # 帧率
        self.fps_spin = QSpinBox()
        self.fps_spin.setRange(5, 60)
        self.fps_spin.setValue(15)
        self.fps_spin.setSuffix(" fps")
        form.addRow("帧率:", self.fps_spin)

        # 缩放
        self.scale_spin = QSpinBox()
        self.scale_spin.setRange(1, 8)
        self.scale_spin.setValue(2)
        self.scale_spin.setSuffix("x")
        form.addRow("缩放:", self.scale_spin)

        layout.addLayout(form)

        # 输出文件路径
        file_header = QLabel("输出文件:")
        file_header.setStyleSheet(f"font-weight:bold; color:{TEXT_SECONDARY};")
        layout.addWidget(file_header)

        self.file_label = QLabel()
        self.file_label.setObjectName("fileLabel")
        self.file_label.setWordWrap(True)
        layout.addWidget(self.file_label)

        # 预乘 Alpha
        self.pma_checkbox = QCheckBox("启用预乘 Alpha (--pma)")
        self.pma_checkbox.setChecked(True)
        layout.addWidget(self.pma_checkbox)

        # 自动打开
        self.auto_open_cb = QCheckBox("导出完成后自动打开文件")
        self.auto_open_cb.setChecked(True)
        layout.addWidget(self.auto_open_cb)

        # 按钮
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        cancel_btn = QPushButton("取消")
        cancel_btn.setObjectName("cancelBtn")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

        ok_btn = QPushButton("导出")
        ok_btn.setObjectName("okBtn")
        ok_btn.clicked.connect(self.accept)
        btn_layout.addWidget(ok_btn)

        layout.addLayout(btn_layout)

        self._skel_base = skel_base
        self._timestamp = timestamp
        self._update_file_label()

    def _update_file_label(self):
        fmt = self.format_combo.currentText().lower()
        ext = ".mp4" if fmt == "mp4" else ".gif"
        fname = f"{self._skel_base}_{self._timestamp}{ext}"
        self.file_label.setText(fname)

    def get_settings(self):
        return {
            "format": self.format_combo.currentText().lower(),
            "animation": self.anim_combo.currentText(),
            "duration": self.duration_spin.value(),
            "fps": self.fps_spin.value(),
            "scale": self.scale_spin.value(),
            "pma": self.pma_checkbox.isChecked(),
            "auto_open": self.auto_open_cb.isChecked(),
            "file_label": self.file_label.text(),
        }


class DragListWidget(QListWidget):
    """支持拖拽文件到桌面的 QListWidget 子类"""

    def __init__(self, parent=None):
        super().__init__(parent)

    def mimeData(self, items):
        """重写 mimeData，将选中的文件路径作为 urls 传递"""
        urls = []
        for item in items:
            data = item.data(Qt.UserRole)
            if data and data.get("png"):
                file_path = data["png"]
                if os.path.exists(file_path):
                    urls.append(QUrl.fromLocalFile(os.path.abspath(file_path)))
        mime = QMimeData()
        mime.setUrls(urls)
        return mime


class BatchExportWorker(QThread):
    """批量导出工作线程"""
    progress = Signal(int, int, str)   # current, total, filename
    one_finished = Signal(str, bool)   # filepath, success
    all_finished = Signal(int, int)   # success_count, fail_count

    def __init__(self, skel_atlas_list, settings, spine_cli, project_root, parent=None):
        super().__init__(parent)
        self.skel_atlas_list = skel_atlas_list  # [(skel, atlas), ...]
        self.settings = settings
        self.spine_cli = spine_cli
        self.project_root = project_root
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        total = len(self.skel_atlas_list)
        success = 0
        fail = 0

        fmt = self.settings["format"]
        animation = self.settings["animation"]
        duration = self.settings["duration"]
        fps = self.settings["fps"]
        scale = self.settings["scale"]
        pma = self.settings.get("pma", False)

        ext = ".mp4" if fmt == "mp4" else ".gif"
        output_dir = os.path.join(
            self.project_root,
            "output",
            "video" if fmt == "mp4" else "character"
        )
        os.makedirs(output_dir, exist_ok=True)

        for i, entry in enumerate(self.skel_atlas_list):
            if self._cancelled:
                break

            skel_path = entry[0]
            atlas_path = entry[1]
            skin_name = entry[2] if len(entry) > 2 else None

            skel_base = os.path.splitext(os.path.basename(skel_path))[0]
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = os.path.join(output_dir, f"{skel_base}_{timestamp}{ext}")

            self.progress.emit(i + 1, total, skel_base)
            QApplication.processEvents()

            try:
                cmd = [
                    self.spine_cli, "export", skel_path,
                    "-f", "Mp4" if fmt == "mp4" else "Gif",
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

                proc = subprocess.run(
                    cmd,
                    cwd=os.path.dirname(self.spine_cli),
                    capture_output=True,
                    text=True,
                    timeout=60,
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                )

                if proc.returncode == 0 and os.path.exists(output_path):
                    success += 1
                    self.one_finished.emit(output_path, True)
                else:
                    fail += 1
                    logger.error(f"批量导出失败 [{skel_base}]: {proc.stderr[:200]}")
                    self.one_finished.emit(skel_path, False)

            except subprocess.TimeoutExpired:
                fail += 1
                logger.error(f"批量导出超时 [{skel_base}]")
                self.one_finished.emit(skel_path, False)
            except Exception as e:
                fail += 1
                logger.error(f"批量导出异常 [{skel_base}]: {e}")
                self.one_finished.emit(skel_path, False)

        self.all_finished.emit(success, fail)


# ========== FGUI 图集切割核心类（移植自 根据二进制fgui文件分割图集.py） ==========

class PackageItemType(Enum):
    Image = 0
    MovieClip = 1
    Sound = 2
    Component = 3
    Atlas = 4
    Font = 5
    Swf = 6
    Misc = 7
    Unknown = 8
    Spine = 9
    DragoneBones = 10


class Rect:
    def __init__(self, x=0.0, y=0.0, width=0.0, height=0.0):
        self.x = x
        self.y = y
        self.width = width
        self.height = height


class Vector2:
    def __init__(self, x=0.0, y=0.0):
        self.x = x
        self.y = y


class AtlasSprite:
    def __init__(self):
        self.atlas = None
        self.rect = Rect()
        self.offset = Vector2()
        self.originalSize = Vector2()
        self.rotated = False


class PackageItem:
    def __init__(self):
        self.owner = None
        self.type = PackageItemType.Unknown
        self.id = ""
        self.name = ""
        self.width = 0
        self.height = 0
        self.path = ""
        self.file = ""
        self.exported = False
        self.rawData = None
        self.branches = None
        self.highResolution = None
        self.scale9Grid = None
        self.scaleByTile = False
        self.tileGridIndice = 0
        self.pixelHitTestData = None


class ByteBuffer:
    def __init__(self, data: bytes, offset: int = 0, length: int = -1):
        self._data = data
        self._pointer = 0
        self._offset = offset
        if length < 0:
            self._length = len(data) - offset
        else:
            self._length = length
        self.little_endian = False
        self.string_table = []
        self.version = 0

    @property
    def position(self) -> int:
        return self._pointer

    @position.setter
    def position(self, value: int):
        self._pointer = value

    @property
    def length(self) -> int:
        return self._length

    @property
    def bytes_available(self) -> bool:
        return self._pointer < self._length

    def skip(self, count: int) -> int:
        self._pointer += count
        return self._pointer

    def read_byte(self) -> int:
        if self._pointer >= self._length:
            raise IndexError("Buffer out of range")
        result = self._data[self._offset + self._pointer]
        self._pointer += 1
        return result

    def read_bytes(self, count: int) -> bytes:
        if self._pointer + count > self._length:
            raise IndexError("Buffer out of range")
        result = self._data[self._offset + self._pointer:self._offset + self._pointer + count]
        self._pointer += count
        return result

    def read_bool(self) -> bool:
        return self.read_byte() == 1

    def read_short(self) -> int:
        start_index = self._offset + self._pointer
        self._pointer += 2
        if self.little_endian:
            return self._data[start_index] | (self._data[start_index + 1] << 8)
        else:
            return (self._data[start_index] << 8) | self._data[start_index + 1]

    def read_ushort(self) -> int:
        return self.read_short() & 0xFFFF

    def read_int(self) -> int:
        start_index = self._offset + self._pointer
        self._pointer += 4
        if self.little_endian:
            return (self._data[start_index] |
                    (self._data[start_index + 1] << 8) |
                    (self._data[start_index + 2] << 16) |
                    (self._data[start_index + 3] << 24))
        else:
            return ((self._data[start_index] << 24) |
                    (self._data[start_index + 1] << 16) |
                    (self._data[start_index + 2] << 8) |
                    self._data[start_index + 3])

    def read_uint(self) -> int:
        return self.read_int() & 0xFFFFFFFF

    def read_float(self) -> float:
        int_val = self.read_int()
        return struct.unpack('f', struct.pack('I', int_val))[0]

    def read_string(self) -> str:
        length = self.read_ushort()
        if length == 0:
            return ""
        result = self._data[self._offset + self._pointer:self._offset + self._pointer + length].decode('utf-8')
        self._pointer += length
        return result

    def read_string_with_length(self, length: int) -> str:
        if length == 0:
            return ""
        result = self._data[self._offset + self._pointer:self._offset + self._pointer + length].decode('utf-8')
        self._pointer += length
        return result

    def read_s(self) -> str:
        index = self.read_ushort()
        if index == 65534:  # null
            return None
        elif index == 65533:  # empty
            return ""
        elif index < len(self.string_table):
            return self.string_table[index]
        else:
            return ""

    def read_s_array(self, count: int) -> list:
        result = []
        for i in range(count):
            result.append(self.read_s())
        return result

    def read_color(self) -> tuple:
        r = self.read_byte()
        g = self.read_byte()
        b = self.read_byte()
        a = self.read_byte()
        return (r, g, b, a)

    def seek(self, index_table_pos: int, block_index: int) -> bool:
        tmp = self.position
        self.position = index_table_pos
        seg_count = self.read_byte()
        if block_index < seg_count:
            use_short = self.read_byte() == 1
            if use_short:
                self.position += 2 * block_index
                new_pos = self.read_short()
            else:
                self.position += 4 * block_index
                new_pos = self.read_int()
            if new_pos > 0:
                self.position = index_table_pos + new_pos
                return True
            else:
                self.position = tmp
                return False
        else:
            self.position = tmp
            return False

    def read_buffer(self) -> 'ByteBuffer':
        count = self.read_int()
        ba = ByteBuffer(self._data, self.position, count)
        ba.string_table = self.string_table
        ba.version = self.version
        self.position += count
        return ba


class UIPackage:
    URL_PREFIX = "ui://"

    def __init__(self):
        self.id = ""
        self.name = ""
        self._items = []
        self._items_by_id = {}
        self._items_by_name = {}
        self._sprites = {}
        self._dependencies = []
        self._asset_path = ""
        self._branches = []
        self._branch_index = -1
        self.string_table = []

    def load_package(self, buffer: ByteBuffer, asset_name_prefix: str) -> bool:
        if buffer.read_uint() != 0x46475549:  # 'FGUI'
            raise Exception(f"Invalid package format in '{asset_name_prefix}'")
        buffer.version = buffer.read_int()
        ver2 = buffer.version >= 2
        compressed = buffer.read_bool()
        self.id = buffer.read_string()
        self.name = buffer.read_string()
        buffer.skip(20)
        index_table_pos = buffer.position
        if buffer.seek(index_table_pos, 4):
            count = buffer.read_int()
            self.string_table = []
            for i in range(count):
                self.string_table.append(buffer.read_string())
            buffer.string_table = self.string_table
        if buffer.seek(index_table_pos, 0):
            count = buffer.read_short()
            self._dependencies = []
            for i in range(count):
                dep_id = buffer.read_s()
                dep_name = buffer.read_s()
                self._dependencies.append({"id": dep_id, "name": dep_name})
        branch_included = False
        if ver2 and buffer.seek(index_table_pos, 5):
            count = buffer.read_short()
            if count > 0:
                self._branches = buffer.read_s_array(count)
                branch_included = count > 0
        if buffer.seek(index_table_pos, 1):
            count = buffer.read_short()
            asset_path = os.path.dirname(asset_name_prefix)
            if asset_path:
                asset_path += "/"
            for i in range(count):
                next_pos = buffer.read_int() + buffer.position
                item = PackageItem()
                item.owner = self
                item.type = PackageItemType(buffer.read_byte())
                item.id = buffer.read_s()
                item.name = buffer.read_s()
                item.path = buffer.read_s()
                item.file = buffer.read_s()
                item.exported = buffer.read_bool()
                item.width = buffer.read_int()
                item.height = buffer.read_int()
                if item.type == PackageItemType.Image:
                    scale_option = buffer.read_byte()
                    if scale_option == 1:
                        rect = Rect()
                        rect.x = buffer.read_int()
                        rect.y = buffer.read_int()
                        rect.width = buffer.read_int()
                        rect.height = buffer.read_int()
                        item.scale9Grid = rect
                        item.tileGridIndice = buffer.read_int()
                    elif scale_option == 2:
                        item.scaleByTile = True
                    buffer.read_bool()
                elif item.type == PackageItemType.MovieClip:
                    buffer.read_bool()
                    item.rawData = buffer.read_buffer()
                elif item.type == PackageItemType.Font:
                    item.rawData = buffer.read_buffer()
                elif item.type in [PackageItemType.Atlas, PackageItemType.Sound, PackageItemType.Misc]:
                    item.file = asset_name_prefix + "_" + item.file
                if ver2:
                    branch_str = buffer.read_s()
                    if branch_str:
                        item.name = branch_str + "/" + item.name
                    branch_count = buffer.read_byte()
                    if branch_count > 0:
                        if branch_included:
                            item.branches = buffer.read_s_array(branch_count)
                    high_res_count = buffer.read_byte()
                    if high_res_count > 0:
                        item.highResolution = buffer.read_s_array(high_res_count)
                self._items.append(item)
                self._items_by_id[item.id] = item
                if item.name:
                    self._items_by_name[item.name] = item
                buffer.position = next_pos
        if buffer.seek(index_table_pos, 2):
            count = buffer.read_short()
            for i in range(count):
                next_pos = buffer.read_ushort() + buffer.position
                item_id = buffer.read_s()
                atlas_item_id = buffer.read_s()
                atlas_item = self._items_by_id.get(atlas_item_id)
                if atlas_item:
                    sprite = AtlasSprite()
                    sprite.atlas = atlas_item
                    sprite.rect.x = buffer.read_int()
                    sprite.rect.y = buffer.read_int()
                    sprite.rect.width = buffer.read_int()
                    sprite.rect.height = buffer.read_int()
                    sprite.rotated = buffer.read_bool()
                    if ver2 and buffer.read_bool():
                        sprite.offset.x = buffer.read_int()
                        sprite.offset.y = buffer.read_int()
                        sprite.originalSize.x = buffer.read_int()
                        sprite.originalSize.y = buffer.read_int()
                    elif sprite.rotated:
                        sprite.originalSize.x = sprite.rect.height
                        sprite.originalSize.y = sprite.rect.width
                    else:
                        sprite.originalSize.x = sprite.rect.width
                        sprite.originalSize.y = sprite.rect.height
                    self._sprites[item_id] = sprite
                buffer.position = next_pos
        return True

    def get_items(self) -> list:
        return self._items

    def get_item(self, item_id: str):
        return self._items_by_id.get(item_id)

    def get_item_by_name(self, item_name: str):
        return self._items_by_name.get(item_name)

    @property
    def sprites(self) -> dict:
        return self._sprites


class UIPackageTool:
    @staticmethod
    def split_atlas(byte_file: str, export_dir: str, is_override_exists: bool = True):
        base_name = os.path.splitext(os.path.basename(byte_file))[0]
        out_path = os.path.join(export_dir, base_name)
        os.makedirs(out_path, exist_ok=True)
        info_output_file = os.path.join(out_path, f"{base_name}_cut_info.json")
        cut_info = []
        with open(byte_file, 'rb') as f:
            source_data = f.read()
        buffer = ByteBuffer(source_data)
        file_dir = os.path.dirname(byte_file)
        pkg = UIPackage()
        main_asset_name = base_name
        pkg.load_package(buffer, main_asset_name)
        sprites = pkg.sprites
        atlas_map = {}
        for item in pkg.get_items():
            if item.type == PackageItemType.Atlas:
                atlas_file = item.file.replace("_fui", "")
                atlas_path = os.path.join(file_dir, atlas_file)
                if os.path.exists(atlas_path):
                    atlas_map[item.file] = Image.open(atlas_path).convert("RGBA")
        sprite_name_count = {}
        for sprite_id, sprite in sprites.items():
            item = pkg.get_item(sprite_id)
            if not item:
                continue
            name = item.name
            rect = sprite.rect
            rotated = sprite.rotated
            atlas_file = sprite.atlas.file if sprite.atlas else "unknown_atlas"
            output_file_name = f"{name}_{atlas_file}.png"
            if output_file_name in sprite_name_count:
                sprite_name_count[output_file_name] += 1
                output_file_name = f"{name}_{atlas_file}_{sprite_name_count[output_file_name]}.png"
            else:
                sprite_name_count[output_file_name] = 0
            output_path = os.path.join(out_path, output_file_name)
            if not is_override_exists and os.path.exists(output_path):
                continue
            atlas_key = sprite.atlas.file if sprite.atlas else None
            if not atlas_key or atlas_key not in atlas_map:
                continue
            atlas_img = atlas_map[atlas_key]
            x = int(rect.x)
            y = int(rect.y)
            width = int(rect.width)
            height = int(rect.height)
            atlas_width, atlas_height = atlas_img.size
            if x < 0 or y < 0 or x + width > atlas_width or y + height > atlas_height:
                print(f"Warning: Sprite {name} out of atlas bounds")
                continue
            sub_image = atlas_img.crop((x, y, x + width, y + height))
            if rotated:
                sub_image = sub_image.transpose(Image.ROTATE_90)
                sub_image = sub_image.transpose(Image.ROTATE_180)
            sub_image.save(output_path, "PNG")
            cut_info.append({
                "sprite_name": name,
                "atlas_file": atlas_key,
                "x": x, "y": y,
                "width": width, "height": height,
                "rotated": rotated,
                "output_file": os.path.basename(output_path)
            })
        with open(info_output_file, 'w', encoding='utf-8') as f:
            json.dump(cut_info, f, indent=4, ensure_ascii=False)


class PreviewExportWorker(QThread):
    """图片预览导出工作线程（.skel → PNG，含配对合成 + 皮肤导出）

    在后台线程执行 SpineViewerCLI 导出，避免阻塞 UI。
    支持去重：force=False 时跳过已存在的 PNG。
    """
    progress = Signal(int, int)            # current, total
    export_finished = Signal(bool, str)    # success, summary
    error = Signal(str)

    def __init__(self, material_dir, output_dir, spine_cli, force=False, parent=None):
        super().__init__(parent)
        self.material_dir = material_dir
        self.output_dir = output_dir
        self.spine_cli = spine_cli
        self.force = force
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            success = self._do_export()
        except Exception as e:
            logger.error(f"预览导出线程异常: {e}", exc_info=True)
            self.error.emit(str(e))

    def _do_export(self):
        """执行完整的 .skel 导出流程，返回 (success, summary)"""
        # 扫描 .skel 文件
        skel_files = []
        for root, dirs, files in os.walk(self.material_dir):
            for f in files:
                if f.endswith(".skel"):
                    skel_files.append(os.path.join(root, f))

        if not skel_files:
            logger.warning("未找到 .skel 文件")
            self.error.emit("未找到 .skel 文件")
            return False

        os.makedirs(self.output_dir, exist_ok=True)

        # 识别配对
        pairs, unpaired = MainWindow._find_paired_files(skel_files)
        logger.info(f"找到 {len(skel_files)} 个 .skel 文件，其中配对 {len(pairs)} 组，未配对 {len(unpaired)} 个")

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

            processed += 1
            self.progress.emit(processed, total)

            if not os.path.exists(atlas_path):
                logger.warning(f"跳过 {skel_name}: 缺少对应的 .atlas 文件")
                skipped_count += 1
                continue

            # 去重检查：force=False 时跳过已存在且非空的 PNG
            main_output = os.path.join(self.output_dir, f"{base_name}.png")
            if not self.force and os.path.exists(main_output) and os.path.getsize(main_output) > 0:
                logger.info(f"跳过已存在的 PNG: {base_name}.png")
                skipped_count += 1
                continue

            logger.info(f"查找 atlas: {skel_path} -> {atlas_path}")

            # 获取动画列表
            animations = MainWindow._get_animation_names(skel_path, atlas_path, self.spine_cli)
            if not animations:
                animations = ["idle"]

            # 导出 idle 动画作为主图
            export_ok = MainWindow._export_animation_frames(
                skel_path, atlas_path, self.spine_cli, self.output_dir, base_name, animations
            )
            if export_ok:
                success_count += 1
            else:
                fail_count += 1

            # 导出皮肤图片（各表情独立图片）
            skin_names = MainWindow._extract_motion_names(skel_path)
            if skin_names:
                logger.info(f"开始导出皮肤图片: {base_name} ({len(skin_names)} 个皮肤)")
                skin_count = MainWindow._export_skel_skins(
                    skel_path, atlas_path, self.spine_cli, self.output_dir, base_name, skin_names
                )
                logger.info(f"皮肤导出完成: {base_name} (成功 {skin_count}/{len(skin_names)})")

        # 处理配对合成
        for role_skel, bg_skel in pairs:
            if self._cancelled:
                break

            role_name = os.path.splitext(os.path.basename(role_skel))[0]
            bg_name = os.path.splitext(os.path.basename(bg_skel))[0]

            role_png = os.path.join(self.output_dir, f"{role_name}.png")
            bg_png = os.path.join(self.output_dir, f"{bg_name}.png")

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
            composite_path = os.path.join(self.output_dir, f"{role_name}_composite.png")
            if not self.force and os.path.exists(composite_path) and os.path.getsize(composite_path) > 0:
                logger.info(f"跳过已存在的合成图: {role_name}_composite.png")
                continue

            if MainWindow._composite_images(role_png, bg_png, composite_path):
                composite_count += 1
                logger.info(f"合成完成: {composite_path}")
            else:
                fail_count += 1

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
        self._export_fgui_atlas()

        self.export_finished.emit(success_count > 0, summary)
        return success_count > 0

    def _export_fgui_atlas(self):
        """
        检查并处理 FGUI 图集：将 .bank 重命名为 .bytes，调用 UIPackageTool 切割图集，
        并将切出的 PNG 移动到 output/character/ 根目录。
        """
        # 1. 构建目标文件路径
        fgui_dir = os.path.join(DATA_DIR, "material", "assets", "fairygui", "ui")
        bank_path = os.path.join(fgui_dir, "CardHeadBanner_fui.bank")
        bytes_path = os.path.join(fgui_dir, "CardHeadBanner_fui.bytes")
        target_bytes = None

        # 2. 检查 .bank 是否存在，存在则重命名为 .bytes
        if os.path.exists(bank_path):
            try:
                os.rename(bank_path, bytes_path)
                logger.info(f"已重命名 .bank 为 .bytes: {bytes_path}")
                target_bytes = bytes_path
            except Exception as e:
                logger.error(f"重命名 .bank 失败: {e}")
                return
        elif os.path.exists(bytes_path):
            target_bytes = bytes_path
            logger.info(f"找到已存在的 .bytes 文件: {bytes_path}")
        else:
            logger.info("未找到 CardHeadBanner_fui.bank 或 .bytes，跳过 FGUI 切割")
            return

        # 3. 检查配套的图集图片是否存在（png，模糊匹配）
        try:
            atlas_pattern = list(Path(fgui_dir).glob("CardHeadBanner*.png"))
        except Exception as e:
            logger.error(f"扫描图集图片失败: {e}")
            return
        if not atlas_pattern:
            logger.warning(f"未找到 CardHeadBanner*.png 图集图片，跳过切割")
            return

        # 4. 调用 UIPackageTool 切割图集，输出到 output/character/
        try:
            UIPackageTool.split_atlas(target_bytes, self.output_dir, is_override_exists=False)
            logger.info(f"FGUI 图集切割完成，输出目录: {self.output_dir}")

            # 5. 移动子文件夹中的 PNG 到 output/character/ 根目录，并清理临时子文件夹和 JSON
            base_name = os.path.splitext(os.path.basename(target_bytes))[0]
            sub_dir = os.path.join(self.output_dir, base_name)
            if os.path.isdir(sub_dir):
                for fname in os.listdir(sub_dir):
                    if fname.lower().endswith(".png"):
                        src = os.path.join(sub_dir, fname)
                        dst = os.path.join(self.output_dir, fname)
                        shutil.move(src, dst)
                        logger.debug(f"移动文件: {fname} -> {dst}")
                shutil.rmtree(sub_dir)
                logger.info(f"已清理临时子目录: {sub_dir}")

            # 6. 删除生成的 JSON 信息文件
            json_file = os.path.join(self.output_dir, f"{base_name}_cut_info.json")
            if os.path.exists(json_file):
                os.unlink(json_file)
                logger.debug(f"已删除 JSON 信息文件: {json_file}")

        except Exception as e:
            logger.error(f"FGUI 图集切割或文件整理失败: {e}")


class AudioDecryptWorker(QThread):
    """音频解密工作线程（.bytes → .bank → 解密 → output/audio/）

    在后台线程执行 epic7_debank.py 解密，避免阻塞 UI。
    支持去重：force=False 时若 output/audio/ 已有音频文件则跳过解密。
    """
    progress = Signal(str)
    finished_decrypt = Signal()
    error = Signal(str)

    def __init__(self, material_dir, audio_output_dir, debank_dir, force=False, parent=None):
        super().__init__(parent)
        self.material_dir = material_dir
        self.audio_output_dir = audio_output_dir
        self.debank_dir = debank_dir
        self.force = force
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            # 步骤 1：转换 .bytes → .bank
            self.progress.emit("正在转换 .bytes 文件...")
            self._convert_bytes_to_bank()

            if self._cancelled:
                self.finished_decrypt.emit()
                return

            # 步骤 2：解密 .bank 文件
            self._decrypt_bank_files()

            self.finished_decrypt.emit()
        except Exception as e:
            logger.error(f"音频解密线程异常: {e}", exc_info=True)
            self.error.emit(str(e))

    def _convert_bytes_to_bank(self):
        """扫描 data/material/ 目录，将符合条件的 .bytes 文件重命名为 .bank，并复制到解密工具的 input 目录

        筛选规则（两者需同时满足）：
        1. 文件名（不含扩展名）完全由数字组成
        2. 文件所在路径包含 fmodassets/ 子目录
        """
        debank_input = os.path.join(self.debank_dir, "input")

        if not os.path.isdir(self.material_dir):
            return 0

        os.makedirs(debank_input, exist_ok=True)
        count = 0
        skipped = 0

        for root, dirs, files in os.walk(self.material_dir):
            for f in files:
                if not f.endswith(".bytes"):
                    continue

                bytes_path = os.path.join(root, f)

                # 筛选规则 1：文件名（不含扩展名）必须为纯数字
                base = os.path.splitext(f)[0]
                if not base.isdigit():
                    logger.debug(f"跳过非音频 .bytes 文件（文件名非纯数字）: {bytes_path}")
                    skipped += 1
                    continue

                # 筛选规则 2：路径必须包含 fmodassets
                if "fmodassets" not in root.split(os.sep):
                    logger.debug(f"跳过非音频 .bytes 文件（不在 fmodassets 目录下）: {bytes_path}")
                    skipped += 1
                    continue

                bank_name = f[:-len(".bytes")] + ".bank"
                bank_path = os.path.join(root, bank_name)

                try:
                    os.rename(bytes_path, bank_path)
                    count += 1
                    logger.info(f"转换 .bytes → .bank: {f} → {bank_name}")
                except (OSError, PermissionError) as e:
                    logger.error(f"重命名失败 {f}: {e}")
                    continue

                # 复制到解密工具 input 目录（去重：大小一致则跳过）
                dest = os.path.join(debank_input, bank_name)
                try:
                    if os.path.exists(dest) and os.path.getsize(dest) == os.path.getsize(bank_path):
                        logger.debug(f"跳过已存在且大小一致的文件: {bank_name}")
                    else:
                        shutil.copy2(bank_path, dest)
                except (OSError, PermissionError) as e:
                    logger.error(f"复制到解密目录失败 {bank_name}: {e}")

        logger.info(f"扫描到音频 .bytes 文件: {count} 个（跳过 {skipped} 个非音频文件）")
        if count > 0:
            self.progress.emit(f"已转换 {count} 个 .bytes → .bank")
        return count

    def _decrypt_bank_files(self):
        """通过导入 epic7_debank 模块解密 .bank 文件（递归扫描 data/material/，输出到 output/audio/）"""
        if not os.path.isdir(self.debank_dir):
            logger.warning(f"epic7_debank 目录不存在: {self.debank_dir}")
            return

        # 检查 data/material/ 中是否有符合条件的 .bank 文件
        bank_count = 0
        if os.path.isdir(self.material_dir):
            for root, dirs, files in os.walk(self.material_dir):
                for f in files:
                    if f.lower().endswith(".bank"):
                        filepath = os.path.join(root, f)
                        # 与 _convert_bytes_to_bank 使用相同的筛选规则
                        base = os.path.splitext(f)[0]
                        if base.isdigit() and "fmodassets" in root.split(os.sep):
                            bank_count += 1
        if bank_count == 0:
            logger.info("素材目录无 .bank 文件，跳过解密")
            return

        # 去重检查：force=False 时若 output/audio/ 已有音频文件则跳过解密（递归扫描子目录）
        if not self.force and os.path.isdir(self.audio_output_dir):
            existing_audio = 0
            for root, dirs, files in os.walk(self.audio_output_dir):
                for f in files:
                    ext = os.path.splitext(f)[1].lower()
                    if ext in (".wav", ".ogg", ".mp3") and os.path.getsize(os.path.join(root, f)) > 0:
                        existing_audio += 1
            if existing_audio > 0:
                logger.info(f"output/audio/ 已有 {existing_audio} 个音频文件，跳过解密")
                self.progress.emit(f"已有 {existing_audio} 个音频文件，跳过解密")
                return

        self.progress.emit(f"正在解密 {bank_count} 个 .bank 文件...")

        try:
            os.makedirs(self.audio_output_dir, exist_ok=True)
            # 将 epic7_debank 目录加入 sys.path 后导入调用，避免打包后 subprocess 递归启动 EXE
            if self.debank_dir not in sys.path:
                sys.path.insert(0, self.debank_dir)
            import epic7_debank
            epic7_debank.run(self.material_dir, self.audio_output_dir)
            # 统计输出目录中的音频文件数（递归扫描子目录）
            audio_count = 0
            for root, dirs, files in os.walk(self.audio_output_dir):
                for f in files:
                    if os.path.splitext(f)[1].lower() in (".wav", ".ogg", ".mp3"):
                        audio_count += 1
            self.progress.emit(f"解密完成: 提取 {audio_count} 个音频文件")
            logger.info(f"解密完成: 输出 {audio_count} 个音频文件到 {self.audio_output_dir}")

        except Exception as e:
            logger.error(f"解密异常: {e}", exc_info=True)
            self.error.emit(f"解密异常: {e}")


# ========== Lua Decrypt Worker ==========

FIXED_HEAD = (b'\x1B\x4C\x75\x61\x54\x00\x19\x93\x0D\x0A\x1A\x0A\x04\x08\x08\x78'
              b'\x56\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x28\x77\x40\x01')


class LuaDecryptWorker(QThread):
    """Lua 字节码批量解密工作线程

    递归扫描 data/material/assets/lua/ 目录，处理所有 .lua 和 .lua.bank 文件：
    1. 头部修复（替换为 FIXED_HEAD）
    2. 反编译（调用 unluac.jar）
    3. 中文转义解码（\\xxx 序列 → UTF-8）
    4. 原地覆盖原文件
    """
    progress = Signal(str)          # 进度消息
    finished = Signal(int, int)     # 成功数, 失败数
    error = Signal(str)             # 错误信息
    file_done = Signal(str)         # 单个文件解密完成，传递文件名

    def __init__(self, lua_dir, unluac_path, opmap_path, parent=None):
        super().__init__(parent)
        self.lua_dir = lua_dir
        self.unluac_path = unluac_path
        self.opmap_path = opmap_path
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            self._decrypt_all()
        except Exception as e:
            logger.error(f"Lua 解密线程异常: {e}", exc_info=True)
            self.error.emit(str(e))

    def _decrypt_all(self):
        """遍历目录，处理所有符合条件的 Lua 文件"""
        # 收集所有待处理文件，将 BaseWord_cn.lua 和 BaseCard.lua 优先
        priority_files = []
        other_files = []
        if os.path.isdir(self.lua_dir):
            for root, dirs, files in os.walk(self.lua_dir):
                for f in files:
                    # 排除已处理过的 .lua.bank.lua 文件
                    if f.endswith('.lua.bank.lua'):
                        continue
                    if f.endswith('.lua') or f.endswith('.lua.bank'):
                        path = os.path.join(root, f)
                        if f in ('BaseWord_cn.lua', 'BaseCard.lua'):
                            priority_files.append(path)
                        else:
                            other_files.append(path)
        all_files = priority_files + other_files

        total = len(all_files)
        if total == 0:
            logger.info("Lua 目录无待处理文件，跳过解密")
            self.finished.emit(0, 0)
            return

        logger.info(f"Lua 解密开始: 共 {total} 个文件")
        self.progress.emit(f"正在解密 Lua: 0/{total}")

        success_count = 0
        fail_count = 0

        for i, filepath in enumerate(all_files):
            if self._cancelled:
                break

            self.progress.emit(f"正在解密 Lua: {i+1}/{total}")
            fname = os.path.basename(filepath)
            is_bank = fname.endswith('.lua.bank')

            # 检查是否已经是可读文本 Lua（已解密），跳过处理
            if not is_bank:
                try:
                    with open(filepath, 'r', encoding='utf-8') as f:
                        first_line = f.readline()
                        if first_line and (first_line.startswith('local') or first_line.startswith('--') or first_line.startswith('function')):
                            logger.debug(f"已解密，跳过: {fname}")
                            success_count += 1
                            continue
                except Exception:
                    pass

            # 重试 1 次
            ok = False
            for attempt in range(2):
                try:
                    ok = self._process_single_file(filepath)
                    if ok:
                        break
                except Exception as e:
                    logger.error(f"Lua 处理异常 (第{attempt+1}次) {fname}: {e}")

            if ok:
                success_count += 1
                logger.info(f"Lua 解密成功: {fname}")
            else:
                fail_count += 1
                logger.warning(f"Lua 解密失败（已跳过）: {fname}")

            self.progress.emit(f"正在解密 Lua: {i+1}/{total}（成功: {success_count}）")

        self.progress.emit(f"Lua 解密完成: 成功 {success_count} 个, 失败 {fail_count} 个")
        logger.info(f"Lua 解密完成: 成功 {success_count}, 失败 {fail_count}, 共 {total}")
        self.finished.emit(success_count, fail_count)

    def _process_single_file(self, filepath):
        """处理单个文件：头部修复 → 反编译 → 中文转义解码"""
        fname = os.path.basename(filepath)
        is_bank = fname.endswith('.lua.bank')
        out_dir = os.path.dirname(filepath)

        # 输出文件名：去除 .bank 后缀
        if is_bank:
            out_name = fname[:-len('.bank')]  # .lua.bank → .lua
        else:
            out_name = fname

        # 临时文件路径
        temp_fixed = os.path.join(out_dir, f'__tmp_fixed_{out_name}')
        temp_decomp = os.path.join(out_dir, f'__tmp_decomp_{out_name}')

        try:
            # Step 1: 头部修复
            with open(filepath, 'rb') as f:
                data = f.read()
            end = data.find(b'\x28\x77\x40\x01')
            if end == -1:
                logger.error(f"无效的 luac 文件（未找到头部特征标记）: {filepath}")
                return False
            head_len = end + 4
            fixed_data = FIXED_HEAD + data[head_len:]
            with open(temp_fixed, 'wb') as f:
                f.write(fixed_data)

            # Step 2: 反编译
            java_cmd = [
                'java', '-jar', self.unluac_path,
                temp_fixed,
                '-o', temp_decomp,
                '--opmap', self.opmap_path
            ]
            result = subprocess.run(java_cmd, capture_output=True, timeout=120)
            if result.returncode != 0:
                stderr = result.stderr.decode('utf-8', errors='replace')
                logger.error(f"反编译失败: {fname}, 返回码: {result.returncode}, stderr: {stderr[:200]}")
                return False

            # Step 3: 中文转义解码
            # 将 \xxx 连续转义序列解码为 UTF-8 中文
            pattern = re.compile(r'(\\(\d{3}))+')
            with open(temp_decomp, 'r', encoding='utf-8') as f:
                content = f.read()

            def replace_long_match(match):
                codes = match.group().split('\\')[1:]
                byte_values = [int(code) for code in codes]
                byte_sequence = bytes(byte_values)
                try:
                    return byte_sequence.decode('utf-8')
                except UnicodeDecodeError:
                    return match.group()

            decoded_content = pattern.sub(replace_long_match, content)

            # Step 4: 原地覆盖
            out_path = os.path.join(out_dir, out_name)
            with open(out_path, 'w', encoding='utf-8') as f:
                f.write(decoded_content)

            # 如果原文件是 .lua.bank，删除原文件
            if is_bank:
                os.remove(filepath)

            # 通知主线程关键文件已完成
            if fname in ('BaseWord_cn.lua', 'BaseCard.lua'):
                self.file_done.emit(fname)

            return True

        except subprocess.TimeoutExpired:
            logger.error(f"反编译超时: {fname}")
            return False
        except FileNotFoundError as e:
            logger.error(f"未找到 Java 运行时: {e}")
            self.error.emit("未找到 Java 运行时，请确保 Java 已安装并加入 PATH")
            return False
        except Exception as e:
            logger.error(f"处理文件失败 {fname}: {e}")
            return False
        finally:
            # 清理临时文件
            for tmp in [temp_fixed, temp_decomp]:
                if os.path.isfile(tmp):
                    try:
                        os.remove(tmp)
                    except Exception:
                        pass


class CompositeExportWorker(QThread):
    """批量合成图视频导出工作线程

    在后台线程串行处理合成图导出（角色MP4 + 背景MP4 + FFmpeg叠加），
    避免阻塞 UI。
    """
    progress = Signal(int, int, str)       # current, total, filename
    one_finished = Signal(str, bool)       # filepath, success
    all_finished = Signal(int, int)        # success_count, fail_count

    def __init__(self, composite_pngs, settings, spine_cli, skel_map, project_root, parent=None):
        super().__init__(parent)
        self.composite_pngs = composite_pngs
        self.settings = settings
        self.spine_cli = spine_cli
        self.skel_map = skel_map
        self.project_root = project_root
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        total = len(self.composite_pngs)
        success = 0
        fail = 0

        for i, png_path in enumerate(self.composite_pngs):
            if self._cancelled:
                break

            base_name = os.path.basename(png_path)
            self.progress.emit(i + 1, total, base_name)

            skin_name = MainWindow._extract_skin_name_from_png(png_path)
            if self._export_one(png_path, skin_name):
                success += 1
                self.one_finished.emit(png_path, True)
            else:
                fail += 1
                self.one_finished.emit(png_path, False)

        self.all_finished.emit(success, fail)

    def _export_one(self, png_path, skin_name=None):
        """导出单个合成图视频，返回 bool 表示成功与否"""
        role_skel, role_atlas, bg_skel, bg_atlas = MainWindow._find_composite_sources(png_path, self.skel_map)
        if not role_skel or not bg_skel:
            logger.warning(f"批量合成导出: 缺少角色或背景骨骼数据: {png_path}")
            return False

        if not os.path.exists(self.spine_cli):
            logger.error(f"SpineViewerCLI 不存在: {self.spine_cli}")
            return False

        fmt = self.settings["format"]
        animation = self.settings["animation"]
        duration = self.settings["duration"]
        fps = self.settings["fps"]
        scale = self.settings["scale"]
        pma = self.settings.get("pma", False)

        ext = ".mp4" if fmt == "mp4" else ".gif"
        base_name = os.path.splitext(os.path.basename(png_path))[0]
        if base_name.endswith("_composite"):
            base_name = base_name[:-len("_composite")]
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = os.path.join(self.project_root, "output",
                                   "video" if fmt == "mp4" else "character")
        os.makedirs(output_dir, exist_ok=True)

        # 唯一临时目录
        temp_dir = os.path.join(self.project_root, "output", "temp",
                                f"composite_{base_name}_{datetime.now().strftime('%H%M%S_%f')}")
        os.makedirs(temp_dir, exist_ok=True)

        role_temp_path = os.path.join(temp_dir, f"role_temp{ext}")
        bg_temp_path = os.path.join(temp_dir, f"bg_temp{ext}")
        output_path = os.path.join(output_dir, f"{base_name}_composite_{timestamp}{ext}")

        logger.info(f"批量合成视频导出: {base_name}")
        logger.info(f"参数: 格式={fmt}, 时长={duration}s, 帧率={fps}fps, 缩放={scale}x, 预乘={pma}, 皮肤={skin_name or '无'}")

        try:
            # 步骤 1: 导出角色视频（应用皮肤）
            if not MainWindow._export_spine_media_file(
                self.spine_cli, role_skel, role_atlas, role_temp_path,
                animation, duration, fps, scale, fmt,
                label="角色", pma=pma, skin_name=skin_name
            ):
                logger.error(f"批量合成: 角色视频导出失败: {base_name}")
                return False

            # 步骤 2: 导出背景视频
            if not MainWindow._export_spine_media_file(
                self.spine_cli, bg_skel, bg_atlas, bg_temp_path,
                animation, duration, fps, scale, fmt,
                label="背景", pma=pma
            ):
                logger.error(f"批量合成: 背景视频导出失败: {base_name}")
                return False

            # 步骤 3: FFmpeg 叠加合成
            if not MainWindow._ffmpeg_composite_videos(
                bg_temp_path, role_temp_path, output_path,
                fps, fmt
            ):
                logger.error(f"批量合成: FFmpeg 叠加失败: {base_name}")
                return False

            if os.path.exists(output_path):
                size = os.path.getsize(output_path)
                logger.info(f"批量合成视频导出完成: {output_path} (大小: {size} bytes)")
                return True
            else:
                logger.error(f"批量合成: 输出文件未生成: {base_name}")
                return False

        except Exception as e:
            logger.error(f"批量合成视频导出异常 [{base_name}]: {e}")
            return False
        finally:
            time.sleep(0.5)
            MainWindow._cleanup_temp(temp_dir)


class ImportASWorker(QThread):
    """导入AS后台工作线程：整合 修复→解析→导出分类 三阶段

    直接修复原始 .bundle 文件（不复制到临时目录），
    然后调用 AssetStudio CLI 生成资源映射（assets_map.json），
    最后按类型导出所有资源到 data/material/。
    """
    progress_stage = Signal(str, int, int)  # stage_name, current, total
    stage_finished = Signal(str)            # stage_name
    all_finished = Signal(bool, str)        # success, message

    def __init__(self, bundle_paths, bundle_dir, material_dir, as_cli, parent=None):
        super().__init__(parent)
        self.bundle_paths = bundle_paths
        self.bundle_dir = bundle_dir
        self.material_dir = material_dir
        self.as_cli = as_cli
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            # 阶段 1: 修复文件头（直接修复原始文件）
            success, fail = self._stage_fix()
            if self._cancelled:
                self.all_finished.emit(False, "已取消")
                return
            if success == 0:
                self.all_finished.emit(False, "所有文件修复失败，请删除该版本并重新下载")
                return
            self.stage_finished.emit("修复文件头")
            if fail > 0:
                logger.warning(f"[导入AS] 修复阶段：{fail} 个文件失败，继续处理 {success} 个成功文件")

            # 阶段 2: 解析资源（生成 assets_map.json）
            assets, msg = self._stage_map()
            if self._cancelled:
                self.all_finished.emit(False, "已取消")
                return
            if assets is None:
                self.all_finished.emit(False, f"资源解析失败: {msg}")
                return
            self.stage_finished.emit("解析资源")

            # 阶段 3: 导出分类到 data/material/
            total_files, msg = self._stage_export(assets)
            if self._cancelled:
                self.all_finished.emit(False, "已取消")
                return
            self.stage_finished.emit("导出分类")

            if total_files > 0:
                self.all_finished.emit(
                    True,
                    f"导入完成！文件已分类到 data/material/\n共导出 {total_files} 个文件"
                )
            else:
                self.all_finished.emit(False, "导出完成但文件数为 0，请检查资源")
        except Exception as e:
            logger.error(f"[导入AS] 工作线程异常: {e}", exc_info=True)
            self.all_finished.emit(False, f"导入失败: {e}")

    def _stage_fix(self):
        """阶段 1: 直接修复原始 .bundle 文件头"""
        total = len(self.bundle_paths)
        success = 0
        fail = 0
        logger.info(f"[导入AS] 阶段1: 修复文件头，共 {total} 个文件")
        for i, f in enumerate(self.bundle_paths):
            if self._cancelled:
                break
            h = os.path.basename(f).replace(".bundle", "")
            try:
                fix_bundle_inplace(f)
                success += 1
                logger.debug(f"[导入AS] 修复完成: {h[:16]}...")
            except Exception as e:
                logger.error(f"[导入AS] 修复失败: {h[:16]}... - {e}")
                fail += 1
            self.progress_stage.emit("修复文件头", i + 1, total)
        logger.info(f"[导入AS] 阶段1完成: 成功 {success}, 失败 {fail}")
        return success, fail

    def _stage_map(self):
        """阶段 2: 调用 AssetStudio CLI 生成资源映射"""
        if not os.path.exists(self.as_cli):
            logger.error(f"[导入AS] AssetStudio.CLI.exe 不存在: {self.as_cli}")
            return None, f"AssetStudio CLI 不存在: {self.as_cli}"
        map_dir = os.path.join(self.bundle_dir, "_map")
        os.makedirs(map_dir, exist_ok=True)
        logger.info(f"[导入AS] 阶段2: 解析资源，输出映射到 {map_dir}")
        self.progress_stage.emit("解析资源", 0, 0)
        try:
            proc = subprocess.Popen(
                [self.as_cli, self.bundle_dir, map_dir, "--game", "UnityCN", "--key_index", "23",
                 "--map_op", "Both", "--map_type", "JSON", "--silent"],
                cwd=os.path.dirname(self.as_cli), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, bufsize=1,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
            for line in proc.stdout:
                if self._cancelled:
                    proc.terminate()
                    break
                if "Processed" in line:
                    try:
                        p = line.split()
                        c = int(p[0].split("/")[0].lstrip("["))
                        t = int(p[0].split("/")[1])
                        self.progress_stage.emit("解析资源", c, t)
                    except Exception:
                        pass
            proc.wait()
            map_file = os.path.join(map_dir, "assets_map.json")
            if os.path.exists(map_file) and os.path.getsize(map_file) > 100:
                with open(map_file, "r", encoding="utf-8") as f:
                    assets = json.load(f)
                logger.info(f"[导入AS] 阶段2完成: 解析到 {len(assets)} 个资源")
                return assets, ""
            else:
                return None, "assets_map.json 为空或不存在"
        except Exception as e:
            return None, str(e)

    def _stage_export(self, assets):
        """阶段 3: 按类型导出所有资源到 data/material/"""
        if not os.path.exists(self.as_cli):
            logger.error(f"[导入AS] AssetStudio.CLI.exe 不存在: {self.as_cli}")
            return
        logger.info(f"[导入AS] 阶段3: 导出分类到 {self.material_dir}")
        # 清空旧目录
        if os.path.exists(self.material_dir):
            shutil.rmtree(self.material_dir, ignore_errors=True)
        os.makedirs(self.material_dir, exist_ok=True)

        all_types = sorted(set(a.get("Type", "") for a in assets if a.get("Type")))
        total_types = len(all_types)
        logger.info(f"[导入AS] 待导出类型（共 {total_types} 种）: {all_types}")

        for i, tp in enumerate(all_types):
            if self._cancelled:
                break
            self.progress_stage.emit("导出分类", i + 1, total_types)
            try:
                cmd = [self.as_cli, self.bundle_dir, self.material_dir,
                       "--game", "UnityCN", "--key_index", "23",
                       "--types", tp, "--group_assets", "ByContainer",
                       "--export_type", "Convert"]
                logger.debug(f"[导入AS] CLI 命令: {' '.join(cmd)}")
                proc = subprocess.run(
                    cmd, cwd=os.path.dirname(self.as_cli),
                    capture_output=True, text=True, timeout=300,
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
                if proc.returncode != 0:
                    logger.warning(
                        f"[导入AS] 类型 {tp} 导出失败 (退出码 {proc.returncode}): {proc.stderr[:300]}"
                    )
            except subprocess.TimeoutExpired:
                logger.error(f"[导入AS] 类型 {tp} 导出超时")
            except Exception as e:
                logger.error(f"[导入AS] 类型 {tp} 导出异常: {e}")

        # 统计导出文件数
        total_files = self._count_files(self.material_dir)
        # 清理 .prefab 后缀（AssetStudio CLI 可能错误添加）
        self._cleanup_prefab_suffix(self.material_dir)
        logger.info(f"[导入AS] 阶段3完成: 共导出 {total_files} 个文件")
        return total_files, ""

    @staticmethod
    def _count_files(directory):
        count = 0
        if not os.path.isdir(directory):
            return 0
        for root, dirs, files in os.walk(directory):
            count += len(files)
        return count

    @staticmethod
    def _cleanup_prefab_suffix(out_dir):
        """清理文件名末尾多余的 .prefab 后缀（AssetStudio CLI 错误添加）"""
        try:
            renamed = 0
            for root, dirs, files in os.walk(out_dir):
                for f in files:
                    if not f.endswith(".prefab"):
                        continue
                    src = os.path.join(root, f)
                    new_name = f[:-len(".prefab")]
                    dst = os.path.join(root, new_name)
                    if os.path.exists(dst):
                        continue
                    try:
                        os.rename(src, dst)
                        renamed += 1
                    except Exception:
                        pass
            if renamed > 0:
                logger.info(f"[导入AS] 清理 {renamed} 个 .prefab 后缀文件")
        except Exception as e:
            logger.error(f"[导入AS] 清理 .prefab 后缀失败: {e}")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("XL 更新管理工具")
        self.resize(1200, 700)
        self.setMinimumSize(900, 500)
        self.showMaximized()
        self.setStyleSheet(BASE_STYLESHEET)
        self.version_mgr = VersionManager()
        # 后台工作线程实例（避免 AttributeError）
        self._preview_worker = None
        self._audio_worker = None
        self._batch_worker = None
        self._composite_worker = None
        self._image_worker = None
        self._import_worker = None
        self._lua_worker = None
        self._show_character = False
        self._character_data_loaded = False
        self._character_loading = False
        self._pending_refresh = False
        self.character_base = []      # 角色基础信息：{raw_id, name, display_index}
        self.characters = []           # 角色完整数据：{name, element_type, max_hp, atk, def, skills, raw_text}
        self.word_map = {}            # BaseWord_cn.lua 中的文本映射：id->文本
        self._skel_map = {}
        self._init_db()
        logger.info(f"当前 DATA_DIR: {DATA_DIR}")
        self._seed_bundled_version()
        self._setup_ui()
        self._load_data()
        self._check_auto()

    def _init_db(self):
        os.makedirs(DATA_DIR, exist_ok=True)
        db.init_db(os.path.join(DATA_DIR, "xl_updata.db"))

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._toolbar())

        self.table = QTableWidget()
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels(["版本", "状态", "Bundle数", "备注", "", "", ""])
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.Interactive)
        hdr.setSectionResizeMode(1, QHeaderView.Interactive)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.Stretch)
        hdr.setSectionResizeMode(4, QHeaderView.Fixed)
        hdr.setSectionResizeMode(5, QHeaderView.Fixed)
        hdr.setSectionResizeMode(6, QHeaderView.Fixed)
        self.table.setColumnWidth(0, 180)
        self.table.setColumnWidth(1, 140)
        self.table.setColumnWidth(4, 90)
        self.table.setColumnWidth(5, 90)
        self.table.setColumnWidth(6, 90)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setDefaultSectionSize(52)
        self.table.setStyleSheet(f"""
            QTableWidget {{ background-color:{BG_DARK}; border:none; gridline-color:transparent; }}
            QTableWidget::item {{ padding:14px 12px; font-size:14px; }}
            QTableWidget::item:selected {{ background-color:{ACCENT}; color:#fff; }}
            QHeaderView::section {{ background-color:{BG_SURFACE}; padding:12px 14px;
                border:none; border-bottom:2px solid {BORDER}; font-size:13px;
                font-weight:600; color:{TEXT_SECONDARY}; }}
        """)
        self.table.currentItemChanged.connect(self._on_row_select)
        root.addWidget(self.table, 1)

        # 预览视图容器（默认隐藏）
        self.preview_container = self._create_preview_view()
        self.preview_container.setVisible(False)
        root.addWidget(self.preview_container, 1)

        # 音频管理器视图容器（默认隐藏）
        self.audio_container = self._create_audio_view()
        self.audio_container.setVisible(False)
        root.addWidget(self.audio_container, 1)

        # 角色视图容器（默认隐藏）
        self.character_container = self._create_character_view()
        self.character_container.setVisible(False)
        root.addWidget(self.character_container, 1)

        self.status_bar = QStatusBar()
        self.status_bar.setStyleSheet(f"""
            QStatusBar {{ background-color:{BG_SURFACE}; border-top:1px solid {BORDER};
                         padding:4px 12px; color:{TEXT_SECONDARY}; font-size:12px; }}
        """)
        self.setStatusBar(self.status_bar)
        self._anim_timer = QTimer()
        self._anim_dots = 0

    def _toolbar(self):
        bar = QToolBar(); bar.setMovable(False)
        bar.addWidget(self._lbl("XL 1.0.1", 16, ACCENT, True))
        bar.addSeparator()
        self.btn_check = self._tbtn("检查更新", True)
        self.btn_check.clicked.connect(self._check_update)
        bar.addWidget(self.btn_check)
        self.btn_browse = self._tbtn("导入AS")
        self.btn_browse.clicked.connect(self._import_selected)
        bar.addWidget(self.btn_browse)
        bar.addSeparator()
        self.btn_image_preview = self._tbtn("图片预览")
        self.btn_image_preview.clicked.connect(lambda: self._toggle_preview_mode(True))
        bar.addWidget(self.btn_image_preview)
        self.btn_audio = self._tbtn("音频")
        self.btn_audio.clicked.connect(lambda: self._toggle_audio_mode(True))
        bar.addWidget(self.btn_audio)
        self.btn_lua = self._tbtn("角色")
        self.btn_lua.clicked.connect(self._start_lua_decrypt)
        bar.addWidget(self.btn_lua)
        r = self._tbtn("刷新"); r.clicked.connect(self._load_data); bar.addWidget(r)
        self.btn_author = self._tbtn("作者")
        self.btn_author.clicked.connect(self._show_author_info)
        bar.addWidget(self.btn_author)
        bar.addSeparator()
        self.dl_progress = QProgressBar()
        self.dl_progress.setFixedHeight(28); self.dl_progress.setFixedWidth(260)
        self.dl_progress.setVisible(False)
        self.dl_progress.setStyleSheet(f"""
            QProgressBar {{ background-color:{BG_ELEVATED}; border:none; border-radius:4px;
                           text-align:center; color:{TEXT_PRIMARY}; font-size:12px; }}
            QProgressBar::chunk {{ background-color:{SUCCESS}; border-radius:4px; }}
        """)
        bar.addWidget(self.dl_progress)
        return bar

    def _lbl(self, t, s=12, c=TEXT_PRIMARY, b=False):
        l = QLabel(t); l.setStyleSheet(f"color:{c};font-size:{s}px;font-weight:{'bold' if b else 'normal'};padding:4px 8px;background:transparent;border:none;"); return l

    def _tbtn(self, t, accent=False):
        b = QToolButton(); b.setText(t)
        if accent: b.setProperty("accent","true"); b.setStyleSheet(b.styleSheet())
        return b

    def _row_btn(self, text, color, tooltip=""):
        b = QPushButton(text)
        b.setFixedHeight(32)
        if "增量" in text or "全量" in text: b.setFixedWidth(82)
        elif "删除" in text: b.setFixedWidth(82)
        else: b.setFixedWidth(64)
        b.setStyleSheet(f"""
            QPushButton {{ background-color:{color}; border:none; border-radius:6px;
                          padding:4px 8px; color:#fff; font-size:12px; font-weight:600; }}
            QPushButton:hover {{ opacity:0.85; }}
        """)
        return b

    # ========== DATA ==========

    def _load_data(self):
        # 确保版本列表可见（隐藏图片预览、音频和角色视图）
        self.table.setVisible(True)
        self.preview_container.setVisible(False)
        self.audio_container.setVisible(False)
        self.character_container.setVisible(False)
        self._show_character = False
        prev = getattr(self, '_sel_ts', None)
        versions = self.version_mgr.refresh()
        self._populate_table(versions)
        if prev:
            self._sel_ts = prev
            for i in range(self.table.rowCount()):
                if self.table.item(i, 0) and self.table.item(i, 0).data(Qt.UserRole) == prev:
                    self.table.selectRow(i); break
        self.status_bar.showMessage(f"已追踪 {len(versions)} 个版本")

    def _populate_table(self, versions):
        self.table.setSortingEnabled(False)
        # fully clear old widgets and rows
        self.table.clearContents()
        self.table.setRowCount(0)
        self.table.setRowCount(len(versions))
        for i, v in enumerate(versions):
            ts, arts, data, other, video, apk, manifest, is_cur, dl, created, notes = v
            dt = ticks_to_date(ts); date_str = dt.strftime("%Y-%m-%d")
            label = date_str
            if is_cur: label += "  [最新]"
            vi = QTableWidgetItem(label); vi.setData(Qt.UserRole, ts)
            if is_cur: vi.setForeground(QColor("#f0a040")); f = vi.font(); f.setBold(True); vi.setFont(f)
            self.table.setItem(i, 0, vi)
            sub = db.get_sub_bundles(ts); total = len(sub) if sub else 0
            down = sum(1 for r in sub if r[2]) if sub else 0
            if total == 0: status = "无Bundle"
            elif down >= total: status = "已下载"
            elif down > 0: status = f"部分 ({down}/{total})"
            else: status = "未下载"
            si = QTableWidgetItem(status)
            if status == "已下载": si.setForeground(QColor(SUCCESS))
            elif "部分" in status: si.setForeground(QColor(WARNING))
            else: si.setForeground(QColor(TEXT_MUTED))
            self.table.setItem(i, 1, si)
            self.table.setItem(i, 2, QTableWidgetItem(f"{total:,}" if total else "-"))
            self.table.setItem(i, 3, QTableWidgetItem(notes or ""))
            # per-row buttons: delta, full, delete
            for col, (txt, clr, cb) in enumerate([
                ("增量下载", SUCCESS, lambda c, t=ts: self._download_version(t, True)),
                ("全量下载", INFO, lambda c, t=ts: self._download_version(t, False)),
                ("删除已下载", DANGER, lambda c, t=ts: self._delete_version(t)),
            ], start=4):
                btn = self._row_btn(txt, clr)
                btn.clicked.connect(cb)
                self.table.setCellWidget(i, col, btn)
        self.table.setSortingEnabled(True)

    def _get_selected_ts(self):
        rows = set(idx.row() for idx in self.table.selectedIndexes())
        if rows:
            r = list(rows)[0]
            it = self.table.item(r, 0)
            if it: return it.data(Qt.UserRole)
        return None

    def _on_row_select(self, current, prev):
        if current:
            it = self.table.item(current.row(), 0)
            if it and it.data(Qt.UserRole):
                self._sel_ts = it.data(Qt.UserRole)

    # ========== CHECK UPDATE ==========

    def _check_update(self):
        # 确保版本列表可见（隐藏图片预览和音频视图）
        self.table.setVisible(True)
        self.preview_container.setVisible(False)
        self.audio_container.setVisible(False)
        self.btn_check.setEnabled(False)
        self.table.insertRow(0)
        self.table.setItem(0, 0, QTableWidgetItem("正在检查更新"))
        anim_item = self.table.item(0, 0)
        for c in range(1, 7):
            self.table.setItem(0, c, QTableWidgetItem(""))
        def animate():
            self._anim_dots = (self._anim_dots + 1) % 4
            try: anim_item.setText("正在检查更新" + "." * (self._anim_dots + 1))
            except: pass
            self._anim_timer.start(400)
        self._anim_timer.timeout.connect(animate)
        self._anim_timer.start(400)
        self.status_bar.showMessage("正在检查更新...")
        pv = self.version_mgr.get_current(); oh = []
        if pv:
            or_ = db.get_sub_bundles(pv[0]); oh = [r[0] for r in or_] if or_ else []
        out_dir = os.path.join(BUNDLES_DIR, "current")
        self.thread = CheckUpdateThread(out_dir, oh)
        self.thread.finished.connect(self._on_update_checked)
        self.thread.error.connect(self._on_check_error)
        self.thread.start()

    def _on_update_checked(self, info, versions, new_hashes, delta):
        self._anim_timer.stop()
        self.btn_check.setEnabled(True)
        ts = versions["timestamp"]
        existing = {r[0] for r in db.get_all_versions()}
        if ts not in existing:
            self.version_mgr.register_version(ts, info, versions)
            db.save_sub_bundles(ts, new_hashes)
            notes = f"新增 {len(delta['added'])} | 移除 {len(delta['removed'])} | 未变 {delta['common']}"
            db.add_notes(ts, notes)
            cnt = len(delta['added'])
            self.status_bar.showMessage(f"发现新版本! 新增 {cnt} 个 bundle.")
            QMessageBox.information(self, "更新完成", f"发现新版本!\n\n{notes}")
        else:
            self.status_bar.showMessage("已是最新版本.")
            QMessageBox.information(self, "已是最新", "当前已是最新版本，无需更新。")
        self._load_data()

    def _on_check_error(self, err):
        self._anim_timer.stop(); self.btn_check.setEnabled(True)
        self._load_data()
        self.status_bar.showMessage(f"错误: {err}")
        QMessageBox.warning(self, "错误", f"检查更新失败:\n{err}")

    # ========== DOWNLOAD ==========

    def _download_version(self, ts, delta_only=True):
        if not ts: return
        ah = [r[0] for r in db.get_sub_bundles(ts)]
        if not ah: QMessageBox.information(self, "无Bundle", "此版本无 bundle."); return
        sub = db.get_sub_bundles(ts); ds = {r[0] for r in sub if r[2]}
        label = "增量下载"
        if delta_only:
            vs = self.version_mgr.get_versions(); ph = set()
            for vv in vs:
                if vv[0] < ts and "(delta" not in (vv[9] or ""):
                    ph = set(r[0] for r in db.get_sub_bundles(vv[0])); break
            target = set(ah) - ph
            if not target: target = set(ah)
        else:
            rp = QMessageBox.question(self, "全量下载确认",
                f"全量下载将下载此版本的全部 {len(ah)} 个 bundle 文件.\n\n"
                "通常只需「增量下载」即可获取本版本新增/修改的文件.\n"
                "全量下载耗时较长且占用大量磁盘空间.\n\n"
                "建议: 先尝试增量下载.\n\n"
                "是否仍然进行全量下载?",
                QMessageBox.Yes|QMessageBox.No, QMessageBox.No)
            if rp != QMessageBox.Yes: return
            target = set(ah); label = "全量下载"
        missing = sorted(target - ds)
        if not missing: QMessageBox.information(self, "已下载", "全部已下载."); return

        self._dl_ts = ts; self._dl_total = len(missing); self._dl_size = 0
        self._dl_count = 0; self._dl_label = label

        # update status cell for this version
        self._update_row_status(ts, f"下载中 0/{len(missing)}")

        self.dl_progress.setVisible(True)
        self.dl_progress.setMaximum(len(missing)); self.dl_progress.setValue(0)
        self.dl_progress.setFormat(f"{label}: 0/{len(missing)}")
        self.status_bar.showMessage(f"{label}: 准备下载 {len(missing)} 个文件...")
        logger.info(f"开始下载版本 {ts}，共 {len(missing)} 个文件")

        out_dir = os.path.join(BUNDLES_DIR, str(ts))
        self.dl_worker = DownloadWorker(missing, out_dir)
        self.dl_worker.progress.connect(lambda n, d, t: (
            setattr(self, '_dl_count', d),
            self.dl_progress.setValue(d),
            self.dl_progress.setFormat(f"{label}: {d}/{len(missing)}"),
            self._update_row_status(ts, f"下载中 {d}/{len(missing)} | {self._dl_size/1048576:.1f}MB"),
            self.status_bar.showMessage(f"{label}: {d}/{len(missing)} | {self._dl_size/1048576:.1f}MB | {n[:30]}")))
        self.dl_worker.item_done.connect(lambda n, f, p: (
            self._dl_done(n, f, p, ts),
            setattr(self, '_dl_size', self._dl_size + (os.path.getsize(p) if os.path.exists(p) else 0))))
        self.dl_worker.item_fail.connect(lambda h, msg: logger.error(f"文件下载失败: {h[:16]}... - {msg}"))
        self.dl_worker.all_done.connect(self._dl_complete)
        self.dl_worker.error.connect(lambda e: (
            logger.error(f"下载错误: {e}"),
            self.status_bar.showMessage(f"下载出错: {e}")))
        self.dl_worker.start()

    def _update_row_status(self, ts, text):
        self.table.blockSignals(True)
        for i in range(self.table.rowCount()):
            it = self.table.item(i, 0)
            if it and it.data(Qt.UserRole) == ts:
                si = QTableWidgetItem(text)
                si.setForeground(QColor(SUCCESS))
                self.table.setItem(i, 1, si)
                break
        self.table.blockSignals(False)

    def _dl_done(self, n, f, p, ts):
        try:
            c = db.get_conn()
            c.execute("UPDATE sub_bundles SET local_path=?, downloadable=1 WHERE hash=? AND version_timestamp=?",(p,n,ts))
            c.commit(); c.close()
            logger.debug(f"文件下载完成并更新数据库: {n[:16]}...")
        except Exception as e:
            logger.error(f"更新数据库失败: {n[:16]}... - {e}", exc_info=True)

    def _dl_complete(self):
        self.dl_progress.setVisible(False)
        self._load_data()
        self.status_bar.showMessage("下载完成!")
        logger.info("下载完成")
        QMessageBox.information(self, "完成", "下载完毕!")

    # ========== BROWSE ==========

    def _import_selected(self):
        # 确保版本列表可见（隐藏图片预览和音频视图）
        self.table.setVisible(True)
        self.preview_container.setVisible(False)
        self.audio_container.setVisible(False)
        ts = self._get_selected_ts()
        if not ts: QMessageBox.warning(self, "未选择", "请先选中一个版本."); return

        # 获取该版本所有 Bundle 的本地路径
        sub = db.get_sub_bundles(ts)
        fs = [r[2] for r in sub if r[2] and os.path.exists(r[2])] if sub else []
        if not fs:
            QMessageBox.information(self, "无文件", "此版本没有已下载的 bundle，请先下载.")
            return

        # 计算路径
        bundle_dir = os.path.dirname(fs[0])
        as_cli = os.path.join(get_tools_dir(), "AssetStudio", "AssetStudio.CLI.exe")
        material_dir = os.path.join(DATA_DIR, "material")

        if not os.path.exists(as_cli):
            logger.error(f"AssetStudio.CLI.exe 不存在: {as_cli}")
            QMessageBox.warning(self, "错误", f"AssetStudio CLI 不存在:\n{as_cli}")
            return
        logger.debug(f"AssetStudio.CLI 路径确认: {as_cli}")

        # 取消已有导入线程
        if self._import_worker is not None:
            self._import_worker.cancel()
            self._import_worker.wait(2000)

        # 禁用导入按钮，显示进度条
        self.btn_browse.setEnabled(False)
        self.dl_progress.setVisible(True)
        self.dl_progress.setValue(0)
        self.dl_progress.setFormat("修复文件头: 0/0")
        self.status_bar.showMessage("正在导入AS: 修复文件头...")
        logger.info(f"开始导入AS: 版本 {ts}, 共 {len(fs)} 个 bundle 文件")

        # 启动后台工作线程
        self._import_worker = ImportASWorker(fs, bundle_dir, material_dir, as_cli, self)
        self._import_worker.progress_stage.connect(self._on_import_progress)
        self._import_worker.stage_finished.connect(self._on_import_stage_finished)
        self._import_worker.all_finished.connect(self._on_import_all_finished)
        self._import_worker.start()

    def _on_import_progress(self, stage_name, current, total):
        """导入AS进度更新"""
        if total > 0:
            self.dl_progress.setMaximum(total)
            self.dl_progress.setValue(current)
            self.dl_progress.setFormat(f"{stage_name}: {current}/{total}")
        else:
            self.dl_progress.setFormat(f"{stage_name}...")
        self.status_bar.showMessage(f"导入AS: {stage_name} {current}/{total}")

    def _on_import_stage_finished(self, stage_name):
        """导入AS阶段完成"""
        logger.info(f"导入AS阶段完成: {stage_name}")
        self.status_bar.showMessage(f"导入AS: {stage_name} 完成")

    def _on_import_all_finished(self, success, message):
        """导入AS全部完成"""
        self.btn_browse.setEnabled(True)
        self.dl_progress.setVisible(False)
        if success:
            self.status_bar.showMessage("导入AS: 文件已分类完成")
            QMessageBox.information(self, "完成", message)
        else:
            self.status_bar.showMessage("导入AS: 失败")
            QMessageBox.warning(
                self, "失败",
                f"{message}\n\n文件可能损坏，请点击【删除已下载】并重新下载。"
            )

    def _browse_version(self, ts):
        """备用方法：打开资源浏览器窗口（当前未使用，保留以备后用）"""
        sub = db.get_sub_bundles(ts)
        fs = [r[2] for r in sub if r[2] and os.path.exists(r[2])] if sub else []
        if not fs: QMessageBox.information(self, "无文件", "此版本没有已下载的 bundle，请先下载."); return
        from .asset_browser import AssetBrowser
        browser = AssetBrowser(self, fs, ts)
        browser.exec()
        self.status_bar.showMessage("资源浏览器已关闭.")

    # ========== AUTHOR INFO ==========

    def _show_author_info(self):
        """显示作者信息"""
        QMessageBox.information(
            self,
            "关于",
            "作者：患得患失和Lucky_King共同创作"
        )

    # ========== OPEN SPINEVIEWER ==========

    def _open_spineviewer(self):
        """启动 SpineViewer 独立程序"""
        sv_exe = os.path.join(get_tools_dir(), "SpineViewer", "SpineViewer.exe")

        if not os.path.exists(sv_exe):
            logger.error(f"SpineViewer.exe 未找到: {sv_exe}")
            QMessageBox.warning(
                self, "错误",
                "SpineViewer.exe 未找到，请确认 tools/SpineViewer/ 目录完整"
            )
            return

        try:
            logger.info(f"启动 SpineViewer: {sv_exe}")
            if sys.platform == "win32":
                os.startfile(sv_exe)
            else:
                subprocess.Popen([sv_exe], shell=True)
            self.status_bar.showMessage("SpineViewer 已启动")
        except Exception as e:
            logger.error(f"启动 SpineViewer 失败: {e}")
            QMessageBox.warning(self, "错误", f"启动 SpineViewer 失败:\n{e}")

    # ========== PREVIEW ==========

    def _preview_images(self):
        """预览图片：检查缓存，有则直接加载，无则导出后加载"""
        output_dir = os.path.join(get_base_dir(), "output", "character")

        os.makedirs(output_dir, exist_ok=True)

        # 检查已有图片
        existing_pngs = [f for f in os.listdir(output_dir) if f.lower().endswith(".png")]

        if existing_pngs:
            # 情况 A：已有图片，直接加载
            logger.info(f"预览目录已存在 {len(existing_pngs)} 张图片，直接加载")
            self._preview_source = "缓存"
            self._toggle_preview_mode(True)
            return

        # 情况 B：没有图片，启动后台线程导出
        logger.info("预览目录为空，开始导出 ...")
        self._start_preview_export(force=False)

    def _start_preview_export(self, force=False):
        """启动后台线程执行 .skel → PNG 导出（含配对合成 + 皮肤导出）

        force=True 时强制重新导出（跳过去重检查）。
        """
        material_dir = os.path.join(DATA_DIR, "material")
        output_dir = os.path.join(get_base_dir(), "output", "character")
        spine_cli = os.path.join(get_tools_dir(), "SpineViewer", "SpineViewerCLI.exe")

        # 前置校验（UI 线程中执行，可弹窗）
        if not os.path.isdir(material_dir):
            logger.warning(f"素材目录不存在: {material_dir}")
            QMessageBox.warning(self, "错误", f"素材目录不存在:\n{material_dir}\n\n请先点击【导入AS】导出资源.")
            return

        if not os.path.exists(spine_cli):
            logger.warning(f"SpineViewerCLI 不存在: {spine_cli}")
            QMessageBox.warning(self, "错误", f"SpineViewerCLI 不存在:\n{spine_cli}")
            return

        # 取消已有的导出线程
        self._cancel_preview_worker()

        # 切换到预览视图（显示进度条）
        self._preview_source = "刚导出"
        self._toggle_preview_mode(True)

        self.status_bar.showMessage("正在处理..." if not force else "强制重新导出中...")
        if hasattr(self, "preview_progress"):
            self.preview_progress.setVisible(True)
            self.preview_progress.setValue(0)

        # 启动后台线程
        self._preview_worker = PreviewExportWorker(
            material_dir, output_dir, spine_cli, force=force, parent=self
        )
        self._preview_worker.progress.connect(self._on_preview_export_progress)
        self._preview_worker.export_finished.connect(self._on_preview_export_finished)
        self._preview_worker.error.connect(self._on_preview_export_error)
        self._preview_worker.start()

    def _on_preview_export_progress(self, current, total):
        """预览导出进度更新"""
        if hasattr(self, "preview_progress") and self.preview_progress:
            self.preview_progress.setMaximum(total)
            self.preview_progress.setValue(current)
            self.preview_progress.setFormat(f"导出中... {current}/{total}")
        self.status_bar.showMessage(f"正在导出预览图片: {current}/{total}")

    def _on_preview_export_finished(self, success, summary):
        """预览导出完成回调"""
        if hasattr(self, "preview_progress") and self.preview_progress:
            self.preview_progress.setVisible(False)
        self.status_bar.showMessage("导出完成" if success else "导出失败")
        QMessageBox.information(self, "导出完成", summary)
        # 重新加载预览图片
        self._load_preview_images()

    def _on_preview_export_error(self, err_msg):
        """预览导出错误回调"""
        if hasattr(self, "preview_progress") and self.preview_progress:
            self.preview_progress.setVisible(False)
        self.status_bar.showMessage("导出失败")
        QMessageBox.warning(self, "错误", f"预览导出失败:\n{err_msg}")

    # --- 配对识别 ---

    @staticmethod
    def _find_paired_files(skel_files):
        """识别 xxx.skel + xxx_bg.skel 配对
        返回: (pairs, unpaired)
          pairs: [(role_skel_path, bg_skel_path), ...]
          unpaired: [skel_path, ...]
        """
        # 分离出 _bg 结尾的 skel
        bg_skels = {}  # base_without_bg -> path
        for s in skel_files:
            name = os.path.splitext(os.path.basename(s))[0]
            if name.endswith("_bg"):
                base = name[:-3]  # 去掉 _bg 后缀
                bg_skels[base] = s

        # 查找配对
        pairs = []
        unpaired = []
        used_bg = set()

        for s in skel_files:
            name = os.path.splitext(os.path.basename(s))[0]
            if name.endswith("_bg"):
                continue  # bg 文件单独处理

            if name in bg_skels:
                logger.info(f"发现配对: {name} + {name}_bg")
                pairs.append((s, bg_skels[name]))
                used_bg.add(name)
            else:
                unpaired.append(s)

        # 未配对的 bg 文件也加入 unpaired
        for base, bg_path in bg_skels.items():
            if base not in used_bg:
                unpaired.append(bg_path)

        return pairs, unpaired

    # --- 图片合成 ---

    @staticmethod
    def _composite_images(role_path, bg_path, output_path):
        """将角色图叠加在背景图上，生成合成图"""
        if not PILLOW_AVAILABLE:
            logger.warning("Pillow 未安装，跳过图片合成")
            return False

        try:
            bg_img = Image.open(bg_path).convert("RGBA")
            role_img = Image.open(role_path).convert("RGBA")

            # 将角色图缩放至背景图尺寸（如果需要）
            if role_img.size != bg_img.size:
                role_img = role_img.resize(bg_img.size, Image.LANCZOS)

            # alpha_composite 或 paste
            composite = Image.alpha_composite(bg_img, role_img)
            composite.save(output_path, "PNG")
            logger.info(f"图片合成成功: {output_path}")
            return True
        except Exception as e:
            logger.error(f"图片合成失败: {e}", exc_info=True)
            return False

    # --- 获取动画列表 ---

    @staticmethod
    def _get_animation_names(skel_path, atlas_path, spine_cli):
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

            # 解析输出
            output = proc.stdout.strip()
            if proc.returncode == 0 and output:
                # 尝试按行解析
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

        # CLI 失败时，从 .skel 二进制文件提取动画名称
        if not animations:
            animations = MainWindow._extract_motion_names(skel_path)
            if animations:
                logger.info(f"从 .skel 文件提取到 {len(animations)} 个动画名称: {animations}")

        if not animations:
            animations = ["idle"]

        return animations

    @staticmethod
    def _extract_motion_names(skel_path):
        """从 .skel 二进制文件提取 motion_* 名称列表（用于动画名和皮肤名）"""
        try:
            with open(skel_path, 'rb') as f:
                data = f.read()
            pattern = re.compile(rb'motion_[a-zA-Z0-9_]+')
            matches = set(match.decode('utf-8') for match in pattern.findall(data))
            # 过滤掉不需要的名称（组、眼睛/嘴部独立控制，非完整表情）
            exclude = {"motion_group", "motion_dizzy_eye_l", "motion_dizzy_eye_r", "motion_dizzy_mouth"}
            matches = matches - exclude
            # 过滤掉以数字结尾的名称（如 motion_angry2，其纯净版 motion_angry 已存在）
            matches = {name for name in matches if not re.search(r'\d$', name)}
            if matches:
                return sorted(matches)
        except Exception as e:
            logger.error(f"从 .skel 文件提取名称失败: {e}")
        return []

    # --- 导出单个动画帧 ---

    @staticmethod
    def _export_animation_frames(skel_path, atlas_path, spine_cli, output_dir, base_name, animations):
        """导出一个 .skel 文件的所有动画帧
        文件名格式: {base_name}_{animation}.png (idle 动画命名为 {base_name}.png)
        """
        scale = 4
        max_resolution = 8192
        overall_success = False

        for anim_name in animations:
            # idle 动画作为主文件名，其他动画加后缀
            if anim_name.lower() == "idle":
                output_name = f"{base_name}.png"
            else:
                safe_anim = re.sub(r'[\\/:*?"<>|]', '_', anim_name)
                output_name = f"{base_name}_{safe_anim}.png"

            output_path = os.path.join(output_dir, output_name)
            logger.info(f"导出动画: {anim_name} -> {output_path}")

            export_ok = MainWindow._run_spine_export(
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

    @staticmethod
    def _export_skel_skins(skel_path, atlas_path, spine_cli, output_dir, base_name, skin_names):
        """导出每个皮肤的独立图片（动画固定为 idle，带 --pma 尝试 + fallback）
        文件名格式: {base_name}_{skin_name}.png
        """
        scale = 4
        max_resolution = 8192
        skin_success = 0

        for skin_name in skin_names:
            safe_skin = re.sub(r'[\\/:*?"<>|]', '_', skin_name)
            output_path = os.path.join(output_dir, f"{base_name}_{safe_skin}.png")

            # 第一阶段：带 --pma
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

    @staticmethod
    def _run_spine_export(spine_cli, skel_path, atlas_path, output_path, scale, max_resolution, animation):
        """执行 SpineViewerCLI export 命令（带 --pma 尝试 + fallback）"""
        # 第一阶段：带 --pma
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

    def _force_reload_preview(self):
        """重新加载预览图片（不清空目录，只补充缺失的图片）"""
        logger.info("重新加载预览图片，只补充缺失的 ...")
        self.status_bar.showMessage("重新加载预览图片，只补充缺失的 ...")

        # 启动后台线程，force=False 时利用去重逻辑跳过已存在的文件
        self._start_preview_export(force=False)

    # ========== IMAGE GALLERY PREVIEW ==========

    def _create_preview_view(self):
        """创建图片预览视图容器"""
        container = QWidget()
        container.setStyleSheet(f"background-color:{BG_DARK};")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 顶部栏：标题 + 关闭按钮
        top_bar = QFrame()
        top_bar.setFixedHeight(50)
        top_bar.setStyleSheet(f"QFrame {{ background-color:{BG_SURFACE}; border-bottom:1px solid {BORDER}; }}")
        top_layout = QHBoxLayout(top_bar)
        top_layout.setContentsMargins(16, 0, 16, 0)

        self.preview_title = QLabel("🖼️ 角色预览器  共 0 张图片")
        self.preview_title.setStyleSheet(f"color:{TEXT_PRIMARY}; font-size:16px; font-weight:bold; background:transparent; border:none;")
        top_layout.addWidget(self.preview_title)
        top_layout.addStretch()

        btn_close_preview = QPushButton("✕ 关闭预览")
        btn_close_preview.setFixedSize(100, 32)
        btn_close_preview.setStyleSheet(f"""
            QPushButton {{ background-color:{DANGER}; border:none; border-radius:6px;
                          color:#fff; font-size:12px; font-weight:600; }}
            QPushButton:hover {{ opacity:0.85; }}
        """)
        btn_close_preview.clicked.connect(lambda: self._toggle_preview_mode(False))
        top_layout.addWidget(btn_close_preview)
        layout.addWidget(top_bar)

        # 工具栏：重新加载 + 进度条
        ctrl_bar = QFrame()
        ctrl_bar.setFixedHeight(50)
        ctrl_bar.setStyleSheet(f"QFrame {{ background-color:{BG_ELEVATED}; border-bottom:1px solid {BORDER}; }}")
        ctrl_layout = QHBoxLayout(ctrl_bar)
        ctrl_layout.setContentsMargins(16, 0, 16, 0)

        self.btn_reload = QPushButton("🔄 重新加载图片")
        self.btn_reload.setFixedSize(140, 32)
        self.btn_reload.setStyleSheet(f"""
            QPushButton {{ background-color:{INFO}; border:none; border-radius:6px;
                          color:#fff; font-size:12px; font-weight:600; }}
            QPushButton:hover {{ opacity:0.85; }}
        """)
        self.btn_reload.clicked.connect(self._force_reload_preview)
        ctrl_layout.addWidget(self.btn_reload)

        self.preview_progress = QProgressBar()
        self.preview_progress.setFixedHeight(24)
        self.preview_progress.setFixedWidth(250)
        self.preview_progress.setVisible(False)
        self.preview_progress.setStyleSheet(f"""
            QProgressBar {{ background-color:{BG_DARK}; border:none; border-radius:4px;
                           text-align:center; color:{TEXT_PRIMARY}; font-size:12px; }}
            QProgressBar::chunk {{ background-color:{SUCCESS}; border-radius:4px; }}
        """)
        ctrl_layout.addWidget(self.preview_progress)
        ctrl_layout.addStretch()
        layout.addWidget(ctrl_bar)

        # 图片列表（IconMode，自适应列数，支持多选和拖放）
        self.image_list = DragListWidget()
        self.image_list.setViewMode(QListWidget.IconMode)
        self.image_list.setIconSize(QSize(150, 150))
        self.image_list.setGridSize(QSize(180, 210))
        self.image_list.setResizeMode(QListWidget.Adjust)
        self.image_list.setMovement(QListWidget.Static)
        self.image_list.setSpacing(10)
        self.image_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.image_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.image_list.setDragEnabled(True)
        self.image_list.setStyleSheet(f"""
            QListWidget {{ border:none; background-color:{BG_DARK}; padding:10px; }}
            QListWidget::item {{ border-radius:6px; }}
            QListWidget::item:hover {{ background-color:{BG_ELEVATED}; }}
            QListWidget::item:selected {{ background-color:{BG_ELEVATED}; }}
        """)
        self.image_list.customContextMenuRequested.connect(self._show_context_menu)
        self.image_list.itemClicked.connect(self._on_item_clicked)
        self.image_list.itemDoubleClicked.connect(self._on_item_double_clicked)
        layout.addWidget(self.image_list, 1)

        # 底部状态
        self.preview_status = QLabel("共 0 张图片")
        self.preview_status.setFixedHeight(28)
        self.preview_status.setStyleSheet(f"color:{TEXT_SECONDARY}; font-size:12px; padding:4px 16px; background-color:{BG_SURFACE}; border-top:1px solid {BORDER};")
        layout.addWidget(self.preview_status)

        # 空状态提示
        self.empty_label = QLabel("暂无图片，请先导出角色立绘")
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setStyleSheet(f"color:{TEXT_MUTED}; font-size:18px; background:transparent; border:none;")
        self.empty_label.setVisible(False)
        layout.addWidget(self.empty_label)

        self._image_paths = []
        self._thumb_cache = {}
        return container

    def _toggle_preview_mode(self, show_preview):
        """切换预览视图/版本列表"""
        self.table.setVisible(not show_preview)
        self.preview_container.setVisible(show_preview)
        self.character_container.setVisible(False)
        self._show_character = False
        if show_preview:
            self.audio_container.setVisible(False)
            # 切换到预览时取消音频解密线程
            self._cancel_audio_worker()
            self._load_preview_images()
        else:
            # 离开预览时取消导出线程
            self._cancel_preview_worker()

    # ==================== 音频管理器 ====================

    def _toggle_audio_mode(self, show_audio):
        """切换音频管理器视图"""
        self.table.setVisible(not show_audio)
        self.preview_container.setVisible(False)
        self.character_container.setVisible(False)
        self._show_character = False
        self.audio_container.setVisible(show_audio)
        if show_audio:
            # 切换到音频时取消预览导出线程
            self._cancel_preview_worker()
            self._init_audio_player()
            # 启动后台线程：转换 .bytes → 解密 .bank → 完成后加载列表
            self._start_audio_decrypt(force=False)
        else:
            # 离开音频时取消解密线程
            self._cancel_audio_worker()

    def _cancel_preview_worker(self):
        """取消预览导出线程"""
        if self._preview_worker is not None:
            self._preview_worker.cancel()
            self._preview_worker.wait(2000)
            self._preview_worker = None

    def _cancel_audio_worker(self):
        """取消音频解密线程"""
        if self._audio_worker is not None:
            self._audio_worker.cancel()
            self._audio_worker.wait(2000)
            self._audio_worker = None

    def _start_audio_decrypt(self, force=False):
        """启动后台线程执行音频解密（.bytes → .bank → 解密）"""
        material_dir = os.path.join(DATA_DIR, "material")
        debank_dir = os.path.join(get_tools_dir(), "epic7_debank_v1_0")
        audio_output_dir = os.path.join(get_base_dir(), "output", "audio")

        # 取消已有的解密线程
        self._cancel_audio_worker()

        self.status_bar.showMessage("正在处理音频文件...")

        self._audio_worker = AudioDecryptWorker(
            material_dir, audio_output_dir, debank_dir,
            force=force, parent=self
        )
        self._audio_worker.progress.connect(self._on_audio_decrypt_progress)
        self._audio_worker.finished_decrypt.connect(self._on_audio_decrypt_finished)
        self._audio_worker.error.connect(self._on_audio_decrypt_error)
        self._audio_worker.start()

    def _on_audio_decrypt_progress(self, msg):
        """音频解密进度更新"""
        self.status_bar.showMessage(msg)

    def _on_audio_decrypt_finished(self):
        """音频解密完成回调，自动加载音频列表"""
        self.status_bar.showMessage("音频处理完成")
        self._load_audio_list()

    def _on_audio_decrypt_error(self, err_msg):
        """音频解密错误回调"""
        self.status_bar.showMessage("音频处理失败")
        logger.error(f"音频解密失败: {err_msg}")
        # 即使解密失败也尝试加载已有文件
        self._load_audio_list()

    def _create_audio_view(self):
        """创建音频管理器视图容器"""
        container = QWidget()
        container.setStyleSheet(f"background-color:{BG_DARK};")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 顶部栏：标题 + 关闭按钮
        top_bar = QFrame()
        top_bar.setFixedHeight(50)
        top_bar.setStyleSheet(f"QFrame {{ background-color:{BG_SURFACE}; border-bottom:1px solid {BORDER}; }}")
        top_layout = QHBoxLayout(top_bar)
        top_layout.setContentsMargins(16, 0, 16, 0)

        self.audio_title = QLabel("🎵 音频管理器  共 0 个音频文件")
        self.audio_title.setStyleSheet(f"color:{TEXT_PRIMARY}; font-size:16px; font-weight:bold; background:transparent; border:none;")
        top_layout.addWidget(self.audio_title)
        top_layout.addStretch()

        btn_close_audio = QPushButton("✕ 关闭音频")
        btn_close_audio.setFixedSize(100, 32)
        btn_close_audio.setStyleSheet(f"""
            QPushButton {{ background-color:{DANGER}; border:none; border-radius:6px;
                          color:#fff; font-size:12px; font-weight:600; }}
            QPushButton:hover {{ opacity:0.85; }}
        """)
        btn_close_audio.clicked.connect(lambda: self._toggle_audio_mode(False))
        top_layout.addWidget(btn_close_audio)
        layout.addWidget(top_bar)

        # 工具栏
        ctrl_bar = QFrame()
        ctrl_bar.setFixedHeight(50)
        ctrl_bar.setStyleSheet(f"QFrame {{ background-color:{BG_ELEVATED}; border-bottom:1px solid {BORDER}; }}")
        ctrl_layout = QHBoxLayout(ctrl_bar)
        ctrl_layout.setContentsMargins(16, 0, 16, 0)

        btn_style = f"""
            QPushButton {{ background-color:{INFO}; border:none; border-radius:6px;
                          color:#fff; font-size:12px; font-weight:600; padding:6px 12px; }}
            QPushButton:hover {{ opacity:0.85; }}
        """
        btn_refresh = QPushButton("🔄 刷新列表")
        btn_refresh.setStyleSheet(btn_style)
        btn_refresh.clicked.connect(lambda: self._load_audio_list())
        ctrl_layout.addWidget(btn_refresh)

        btn_export = QPushButton("📤 导出选中")
        btn_export.setStyleSheet(btn_style)
        btn_export.clicked.connect(self._export_selected_audio)
        ctrl_layout.addWidget(btn_export)

        btn_play = QPushButton("▶ 播放选中")
        btn_play.setStyleSheet(btn_style)
        btn_play.clicked.connect(self._play_selected_audio)
        ctrl_layout.addWidget(btn_play)

        ctrl_layout.addStretch()
        layout.addWidget(ctrl_bar)

        # 音频表格
        self.audio_table = QTableWidget()
        self.audio_table.setColumnCount(5)
        self.audio_table.setHorizontalHeaderLabels(["", "文件名", "时长", "格式", "大小"])
        hdr = self.audio_table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.Fixed)
        hdr.setSectionResizeMode(1, QHeaderView.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.audio_table.setColumnWidth(0, 36)
        self.audio_table.setAlternatingRowColors(True)
        self.audio_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.audio_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.audio_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.audio_table.verticalHeader().setVisible(False)
        self.audio_table.setShowGrid(False)
        self.audio_table.verticalHeader().setDefaultSectionSize(36)
        self.audio_table.setStyleSheet(f"""
            QTableWidget {{ background-color:{BG_DARK}; border:none; gridline-color:transparent; }}
            QTableWidget::item {{ padding:6px 8px; font-size:13px; }}
            QTableWidget::item:selected {{ background-color:{ACCENT}; color:#fff; }}
            QHeaderView::section {{ background-color:{BG_SURFACE}; padding:8px 10px;
                border:none; border-bottom:1px solid {BORDER}; font-size:12px;
                font-weight:600; color:{TEXT_SECONDARY}; }}
        """)
        self.audio_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.audio_table.customContextMenuRequested.connect(self._show_audio_context_menu)
        self.audio_table.doubleClicked.connect(self._on_audio_double_click)
        layout.addWidget(self.audio_table, 1)

        # 底部播放控制区
        player_bar = QFrame()
        player_bar.setFixedHeight(56)
        player_bar.setStyleSheet(f"QFrame {{ background-color:{BG_SURFACE}; border-top:1px solid {BORDER}; }}")
        player_layout = QHBoxLayout(player_bar)
        player_layout.setContentsMargins(16, 0, 16, 0)

        self.audio_play_btn = QPushButton("▶")
        self.audio_play_btn.setFixedSize(36, 36)
        self.audio_play_btn.setStyleSheet(f"""
            QPushButton {{ background-color:{ACCENT}; border:none; border-radius:18px;
                          color:#fff; font-size:16px; font-weight:bold; }}
            QPushButton:hover {{ opacity:0.85; }}
            QPushButton:disabled {{ background-color:{BG_ELEVATED}; color:{TEXT_MUTED}; }}
        """)
        self.audio_play_btn.setEnabled(False)
        self.audio_play_btn.clicked.connect(self._toggle_play)
        player_layout.addWidget(self.audio_play_btn)

        self.audio_now_playing = QLabel("未播放")
        self.audio_now_playing.setStyleSheet(f"color:{TEXT_SECONDARY}; font-size:12px; background:transparent; border:none; min-width:160px;")
        player_layout.addWidget(self.audio_now_playing)

        self.audio_position_label = QLabel("00:00 / 00:00")
        self.audio_position_label.setStyleSheet(f"color:{TEXT_MUTED}; font-size:11px; background:transparent; border:none;")
        player_layout.addWidget(self.audio_position_label)

        self.audio_slider = QSlider(Qt.Horizontal)
        self.audio_slider.setFixedHeight(20)
        self.audio_slider.setStyleSheet(f"""
            QSlider::groove:horizontal {{ background:{BG_ELEVATED}; height:4px; border-radius:2px; }}
            QSlider::handle:horizontal {{ background:{ACCENT}; width:14px; margin:-5px 0; border-radius:7px; }}
            QSlider::sub-page:horizontal {{ background:{ACCENT}; border-radius:2px; }}
        """)
        self.audio_slider.setEnabled(False)
        self.audio_slider.sliderMoved.connect(self._on_audio_slider_moved)
        player_layout.addWidget(self.audio_slider, 1)

        vol_label = QLabel("🔊")
        vol_label.setStyleSheet("background:transparent; border:none;")
        player_layout.addWidget(vol_label)

        self.audio_volume = QSlider(Qt.Horizontal)
        self.audio_volume.setFixedWidth(80)
        self.audio_volume.setRange(0, 100)
        self.audio_volume.setValue(80)
        self.audio_volume.setStyleSheet(f"""
            QSlider::groove:horizontal {{ background:{BG_ELEVATED}; height:4px; border-radius:2px; }}
            QSlider::handle:horizontal {{ background:{ACCENT}; width:10px; margin:-3px 0; border-radius:5px; }}
            QSlider::sub-page:horizontal {{ background:{ACCENT}; border-radius:2px; }}
        """)
        self.audio_volume.valueChanged.connect(self._set_audio_volume)
        player_layout.addWidget(self.audio_volume)

        layout.addWidget(player_bar)

        # 状态栏
        self.audio_status = QLabel("已选: 0 个 | 共 0 个音频文件")
        self.audio_status.setFixedHeight(28)
        self.audio_status.setStyleSheet(f"color:{TEXT_SECONDARY}; font-size:12px; padding:4px 16px; background-color:{BG_SURFACE}; border-top:1px solid {BORDER};")
        layout.addWidget(self.audio_status)

        self._audio_files = []
        self._audio_player = None
        self._audio_output = None
        self._audio_current_path = None
        return container

    def _init_audio_player(self):
        """初始化音频播放器"""
        if self._audio_player is not None:
            return
        if not QT_MULTIMEDIA_AVAILABLE:
            logger.warning("QtMultimedia 不可用，音频播放功能受限")
            return
        self._audio_player = QMediaPlayer()
        self._audio_output = QAudioOutput()
        self._audio_player.setAudioOutput(self._audio_output)
        self._audio_output.setVolume(0.8)
        self._audio_player.positionChanged.connect(self._update_audio_position)
        self._audio_player.durationChanged.connect(self._update_audio_duration)
        self._audio_player.playbackStateChanged.connect(self._on_audio_state_changed)

    def _load_audio_list(self, force_reload=False):
        """扫描 output/audio/ 目录，加载已解密的音频文件列表"""
        audio_output_dir = os.path.join(get_base_dir(), "output", "audio")
        audio_exts = {".wav", ".ogg", ".mp3"}

        self._audio_files = []

        if not os.path.isdir(audio_output_dir):
            self.audio_title.setText("🎵 音频管理器  共 0 个音频文件")
            self.audio_status.setText("已选: 0 个 | 共 0 个音频文件")
            self.audio_table.setRowCount(0)
            logger.info(f"音频输出目录不存在: {audio_output_dir}")
            return

        # 递归扫描 output/audio/ 目录中的音频文件（支持子目录结构）
        for root, dirs, files in os.walk(audio_output_dir):
            for f in files:
                ext = os.path.splitext(f)[1].lower()
                filepath = os.path.join(root, f)
                if ext in audio_exts:
                    size = os.path.getsize(filepath)
                    # 使用相对于 output/audio/ 的路径作为显示名，以区分同名文件
                    rel_name = os.path.relpath(filepath, audio_output_dir)
                    self._audio_files.append({
                        "path": filepath,
                        "name": rel_name,
                        "ext": ext.lstrip(".").upper(),
                        "size": size,
                        "duration": None,
                    })

        # 按文件名排序
        self._audio_files.sort(key=lambda x: x["name"])

        # 填充表格
        self.audio_table.setRowCount(len(self._audio_files))
        for i, info in enumerate(self._audio_files):
            # 复选框
            cb = QCheckBox()
            cb.setStyleSheet("background:transparent; border:none;")
            cb_widget = QWidget()
            cb_layout = QHBoxLayout(cb_widget)
            cb_layout.addWidget(cb)
            cb_layout.setAlignment(Qt.AlignCenter)
            cb_layout.setContentsMargins(0, 0, 0, 0)
            self.audio_table.setCellWidget(i, 0, cb_widget)

            self.audio_table.setItem(i, 1, QTableWidgetItem(info["name"]))
            self.audio_table.setItem(i, 2, QTableWidgetItem("-"))
            self.audio_table.setItem(i, 3, QTableWidgetItem(info["ext"]))
            self.audio_table.setItem(i, 4, QTableWidgetItem(self._format_size(info["size"])))

        total = len(self._audio_files)
        self.audio_title.setText(f"🎵 音频管理器  共 {total} 个音频文件")
        self.audio_status.setText(f"已选: 0 个 | 共 {total} 个音频文件")
        logger.info(f"音频列表加载完成: 共 {total} 个文件")
        self.status_bar.showMessage(f"音频列表加载完成: {total} 个文件")

    # ========== 角色视图 ==========

    ELEMENT_MAP = {
        1: "火焰", 2: "水", 3: "木", 4: "光", 5: "暗",
    }

    def _create_character_view(self):
        """创建角色视图容器"""
        container = QWidget()
        container.setStyleSheet(f"background-color:{BG_DARK};")
        main_layout = QVBoxLayout(container)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # 顶部栏：标题
        top_bar = QFrame()
        top_bar.setFixedHeight(50)
        top_bar.setStyleSheet(f"QFrame {{ background-color:{BG_SURFACE}; border-bottom:1px solid {BORDER}; }}")
        top_layout = QHBoxLayout(top_bar)
        top_layout.setContentsMargins(16, 0, 16, 0)

        self.character_title = QLabel("角色数据")
        self.character_title.setStyleSheet(
            f"color:{TEXT_PRIMARY}; font-size:16px; font-weight:bold; background:transparent; border:none;"
        )
        top_layout.addWidget(self.character_title)
        top_layout.addStretch()
        main_layout.addWidget(top_bar)

        # 搜索与控制栏
        ctrl_bar = QFrame()
        ctrl_bar.setFixedHeight(50)
        ctrl_bar.setStyleSheet(f"QFrame {{ background-color:{BG_ELEVATED}; border-bottom:1px solid {BORDER}; }}")
        ctrl_layout = QHBoxLayout(ctrl_bar)
        ctrl_layout.setContentsMargins(16, 0, 16, 0)

        self.character_search = QLineEdit()
        self.character_search.setPlaceholderText("搜索角色名称...")
        self.character_search.setFixedWidth(250)
        self.character_search.setFixedHeight(32)
        self.character_search.setStyleSheet(f"""
            QLineEdit {{ background-color:{BG_DARK}; border:1px solid {BORDER}; border-radius:6px;
                        padding:4px 12px; color:{TEXT_PRIMARY}; font-size:13px; }}
            QLineEdit:focus {{ border-color:{ACCENT}; }}
        """)
        self.character_search.textChanged.connect(self._filter_character_table)
        ctrl_layout.addWidget(self.character_search)

        btn_refresh = QPushButton("刷新列表")
        btn_refresh.setFixedSize(100, 32)
        btn_refresh.setStyleSheet(f"""
            QPushButton {{ background-color:{INFO}; border:none; border-radius:6px;
                          color:#fff; font-size:12px; font-weight:600; }}
            QPushButton:hover {{ opacity:0.85; }}
        """)
        btn_refresh.clicked.connect(self._refresh_character_list)
        ctrl_layout.addWidget(btn_refresh)

        btn_csv = QPushButton("下载CSV")
        btn_csv.setFixedSize(100, 32)
        btn_csv.setStyleSheet(f"""
            QPushButton {{ background-color:{INFO}; border:none; border-radius:6px;
                          color:#fff; font-size:12px; font-weight:600; }}
            QPushButton:hover {{ opacity:0.85; }}
        """)
        btn_csv.clicked.connect(self._export_characters_csv)
        ctrl_layout.addWidget(btn_csv)

        self.character_status = QLabel("")
        self.character_status.setStyleSheet(f"color:{TEXT_SECONDARY}; font-size:12px; background:transparent; padding-left:16px;")
        ctrl_layout.addWidget(self.character_status)
        ctrl_layout.addStretch()
        main_layout.addWidget(ctrl_bar)

        # 主体内容：左右布局
        body = QWidget()
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(8, 8, 8, 8)
        body_layout.setSpacing(8)

        # 左侧：角色表格
        self.character_table = QTableWidget()
        self.character_table.setColumnCount(2)
        self.character_table.setHorizontalHeaderLabels(["序号", "名称"])
        hdr = self.character_table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.Stretch)
        self.character_table.setAlternatingRowColors(True)
        self.character_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.character_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.character_table.verticalHeader().setVisible(False)
        self.character_table.setShowGrid(False)
        self.character_table.verticalHeader().setDefaultSectionSize(36)
        self.character_table.setStyleSheet(f"""
            QTableWidget {{ background-color:{BG_DARK}; border:1px solid {BORDER}; border-radius:6px; }}
            QTableWidget::item {{ padding:6px 12px; font-size:13px; }}
            QTableWidget::item:selected {{ background-color:{ACCENT}; color:#fff; }}
            QHeaderView::section {{ background-color:{BG_SURFACE}; padding:8px 12px;
                border:none; border-bottom:2px solid {BORDER}; font-size:12px;
                font-weight:600; color:{TEXT_SECONDARY}; }}
        """)
        self.character_table.itemSelectionChanged.connect(self._on_character_select)
        body_layout.addWidget(self.character_table, 3)

        # 右侧：详情卡片（放入 QScrollArea）
        detail_container = QWidget()
        detail_container.setStyleSheet(f"background-color:{BG_ELEVATED}; border:1px solid {BORDER}; border-radius:8px;")
        self.character_detail = detail_container  # 保持引用名不变
        detail_layout = QVBoxLayout(detail_container)
        detail_layout.setContentsMargins(16, 16, 16, 16)
        detail_layout.setSpacing(8)

        self.character_detail_name = QLabel("请选择一个角色")
        self.character_detail_name.setStyleSheet(f"color:{TEXT_PRIMARY}; font-size:18px; font-weight:bold; background:transparent;")
        detail_layout.addWidget(self.character_detail_name)

        self.character_detail_info = QLabel("")
        self.character_detail_info.setWordWrap(True)
        self.character_detail_info.setStyleSheet(f"color:{TEXT_SECONDARY}; font-size:13px; background:transparent;")
        detail_layout.addWidget(self.character_detail_info)

        detail_layout.addStretch()

        # 滚动区域包裹
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setWidget(detail_container)
        scroll_area.setStyleSheet("QScrollArea { border: none; background-color: transparent; }")
        body_layout.addWidget(scroll_area, 7)

        main_layout.addWidget(body, 1)

        # 空状态提示
        self.character_empty = QLabel("暂无角色数据，请先导入资源并解密 Lua")
        self.character_empty.setAlignment(Qt.AlignCenter)
        self.character_empty.setStyleSheet(f"color:{TEXT_MUTED}; font-size:16px; background:transparent;")
        self.character_empty.setVisible(False)
        main_layout.addWidget(self.character_empty)

        return container

    def _toggle_character_mode(self, show_character):
        """切换角色视图显示/隐藏"""
        self.table.setVisible(not show_character)
        self.preview_container.setVisible(False)
        self.audio_container.setVisible(False)
        self.character_container.setVisible(show_character)
        self._show_character = show_character
        if show_character:
            # 隐藏其他视图时取消相关线程
            self._cancel_audio_worker()
            self._cancel_preview_worker()
            # 检查是否有待刷新的数据
            if self._pending_refresh:
                self._pending_refresh = False
                self._load_character_data()
            self.character_container.raise_()
            self.character_container.show()

    @staticmethod
    def _extract_all_card_blocks(card_content):
        """解析 BaseCard.lua 中所有 `[数字] = { ... }` 块，返回 (raw_id, block_text) 列表。
        使用栈计数法处理嵌套大括号，确保正确提取完整块内容。
        """
        blocks = []
        pattern = re.compile(r'\[\s*(\d+)\s*\]\s*=\s*\{')
        for m in pattern.finditer(card_content):
            raw_id = int(m.group(1))
            start = m.end() - 1  # 指向 {
            depth = 1
            pos = start + 1
            while pos < len(card_content) and depth > 0:
                ch = card_content[pos]
                if ch == '{':
                    depth += 1
                elif ch == '}':
                    depth -= 1
                pos += 1
            if depth == 0:
                block_text = card_content[m.start():pos]
                blocks.append((raw_id, block_text))
        return blocks

    @staticmethod
    def _extract_t_references(block_text):
        """从块文本中提取所有 T(数字) 引用的 raw_id 集合"""
        return set(int(x) for x in re.findall(r'T\((\d+)\)', block_text))

    @staticmethod
    def _parse_skill_up_args(args_str):
        """解析 BaseSkillLevelUp.lua 中 des 的 T() 参数列表。
        输入: "80512141, T(80520017, 70), T(80520012, 1), T(80520018, 7)"
        输出: [80512141, 70, 1, 7]  (第一个是模板 ID，后续是数值)
        """
        result = []
        # 先处理嵌套 T(id, value) 提取 value
        # 替换 T(...) 为其中的第二个数字
        def replace_t(m):
            parts = [int(x) for x in re.findall(r'\d+', m.group(1))]
            if len(parts) >= 2:
                return str(parts[1])
            return "0"
        processed = re.sub(r'T\(([^)]*)\)', replace_t, args_str)
        # 现在提取所有数字
        for num_str in re.findall(r'\d+', processed):
            result.append(int(num_str))
        return result

    def _parse_t_args(self, s):
        """解析 T() 内部的参数列表，处理嵌套 T() 中的逗号。"""
        args = []
        depth = 0
        current = []
        for ch in s:
            if ch == '(':
                depth += 1
                current.append(ch)
            elif ch == ')':
                depth -= 1
                current.append(ch)
            elif ch == ',' and depth == 0:
                args.append(''.join(current).strip())
                current = []
            else:
                current.append(ch)
        if current:
            args.append(''.join(current).strip())
        return args

    def _resolve_t_call(self, call_str):
        """递归解析 T(id, arg1, arg2, ...) 格式的调用，返回最终字符串。
        从 self.word_map 获取模板文本，用参数替换 %s/%d 占位符。
        嵌套的 T() 会递归解析。
        """
        call_str = call_str.strip()
        # 如果是纯数字，直接返回
        try:
            int(call_str)
            return call_str
        except ValueError:
            pass
        # 解析参数列表
        args = self._parse_t_args(call_str)
        if not args:
            return call_str
        # 第一个参数是模板 ID
        try:
            template_id = int(args[0].strip())
        except ValueError:
            return call_str
        # 获取模板文本
        template = self.word_map.get(template_id, "")
        if not template:
            logger.debug(f"_resolve_t_call: 模板缺失 template_id={template_id}")
            return str(template_id)
        # 处理剩余参数
        values = []
        for arg in args[1:]:
            arg = arg.strip()
            if arg.startswith('T(') and arg.endswith(')'):
                inner = arg[2:-1]  # 去掉 T( 和 )
                resolved = self._resolve_t_call(inner)
                values.append(resolved)
            else:
                values.append(arg)
        # 格式化模板：尝试 % 格式化，失败则回退到简单替换
        typed_values = []
        for v in values:
            try:
                if '.' in v:
                    typed_values.append(float(v))
                else:
                    typed_values.append(int(v))
            except (ValueError, TypeError):
                typed_values.append(v)
        try:
            result = template % tuple(typed_values)
        except (TypeError, ValueError, IndexError):
            result = template
            for v in values:
                result = result.replace('%s', str(v), 1)
                result = result.replace('%d', str(v), 1)
        return result

    def _load_character_data(self):
        """从解密后的 Lua 文件中加载角色数据（参考星落角色图鉴提取.py 的完整解析逻辑）"""
        self._character_loading = True
        self.status_bar.showMessage("正在加载角色数据...")
        QApplication.processEvents()
        lua_dir = os.path.join(DATA_DIR, "material", "assets", "lua")
        logger.info(f"开始加载角色数据，lua_dir: {lua_dir}")

        # 清空旧数据
        self.character_base = []
        self.characters = []
        self.characters_full = {}
        self.word_map = {}

        # 1. 读取 BaseWord_cn.lua → word_dict
        self.dl_progress.setValue(10)
        self.status_bar.showMessage("正在解析 BaseWord_cn.lua...")
        QApplication.processEvents()
        bw_path = os.path.join(lua_dir, "BaseWord_cn.lua")
        if not os.path.isfile(bw_path):
            logger.warning("BaseWord_cn.lua 不存在, 角色数据无法加载")
            self._character_loading = False
            return
        word_dict = self._parse_word_file(bw_path)
        self.word_map = word_dict
        logger.info(f"BaseWord 文本映射提取: {len(word_dict)} 条")

        # 2. 读取 BaseCvNameCn.lua → cv_dict
        self.dl_progress.setValue(20)
        self.status_bar.showMessage("正在解析 BaseCvNameCn.lua...")
        QApplication.processEvents()
        cv_path = os.path.join(lua_dir, "BaseCvNameCn.lua")
        cv_dict = {}
        if os.path.isfile(cv_path):
            cv_dict = self._parse_cv_file(cv_path)
        logger.info(f"BaseCvNameCn 解析完成: {len(cv_dict)} 条")

        # 3. 读取 BaseCardLevelUp.lua → level_up_dict
        self.dl_progress.setValue(30)
        self.status_bar.showMessage("正在解析 BaseCardLevelUp.lua...")
        QApplication.processEvents()
        lv_path = os.path.join(lua_dir, "BaseCardLevelUp.lua")
        level_up_dict = {}
        if os.path.isfile(lv_path):
            level_up_dict = self._parse_level_up_file(lv_path)
        logger.info(f"BaseCardLevelUp 解析完成: {len(level_up_dict)} 条")

        # 4. 读取 BaseCardQualityUp.lua → quality_up_dict（带消耗）
        self.dl_progress.setValue(40)
        self.status_bar.showMessage("正在解析 BaseCardQualityUp.lua...")
        QApplication.processEvents()
        qu_path = os.path.join(lua_dir, "BaseCardQualityUp.lua")
        quality_up_dict = {}
        if os.path.isfile(qu_path):
            quality_up_dict = self._parse_quality_up_file_with_cost(qu_path)
        logger.info(f"BaseCardQualityUp 解析完成: {len(quality_up_dict)} 条")

        # 5. 读取 BaseSkill.lua + BaseSkillLevelUp.lua → skill_name_map, skill_desc_map, skill_to_upgrade
        self.dl_progress.setValue(50)
        self.status_bar.showMessage("正在解析 BaseSkill + BaseSkillLevelUp...")
        QApplication.processEvents()
        sk_path = os.path.join(lua_dir, "BaseSkill.lua")
        slu_path = os.path.join(lua_dir, "BaseSkillLevelUp.lua")
        skill_name_map = {}
        skill_desc_map = {}
        skill_to_upgrade = {}
        if os.path.isfile(sk_path) and os.path.isfile(slu_path):
            with open(sk_path, 'r', encoding='utf-8') as f:
                sk_content = f.read()
            for m in re.finditer(r'\[\s*(\d+)\s*\]\s*=\s*\{[^}]*?name\s*=\s*function\(\)\s*return\s*T\((\d+)\)\s*end', sk_content):
                sid = int(m.group(1))
                tid = int(m.group(2))
                skill_name_map[sid] = word_dict.get(tid, str(sid))

            with open(slu_path, 'r', encoding='utf-8') as f:
                slu_content = f.read()
            lu_pat = re.compile(r'\[\s*(\d+)\s*\]\s*=\s*\{[^}]*?des\s*=\s*function\(\)\s*return\s*T\(')
            for m in lu_pat.finditer(slu_content):
                upgrade_id = int(m.group(1))
                start = m.end()
                depth = 1
                pos = start
                while pos < len(slu_content) and depth > 0:
                    if slu_content[pos] == '(':
                        depth += 1
                    elif slu_content[pos] == ')':
                        depth -= 1
                    pos += 1
                if depth != 0:
                    continue
                des_args_str = slu_content[start:pos - 1].strip()
                params = self._parse_t_function_params(des_args_str)
                processed_params = self._process_t_function_params(params, word_dict)
                if processed_params:
                    try:
                        main_id = int(processed_params[0])
                        main_text = word_dict.get(main_id, f"未知({main_id})")
                        for i, param in enumerate(processed_params[1:]):
                            if "%s" in main_text:
                                main_text = main_text.replace("%s", str(param), 1)
                            elif "%d" in main_text:
                                main_text = main_text.replace("%d", str(param), 1)
                        main_text = main_text.replace("%%", "%")
                        main_text = main_text.replace("\\n", "\n")
                        main_text = re.sub(r'\[color=#[0-9a-fA-F]+\]', '', main_text)
                        main_text = re.sub(r'\[/color\]', '', main_text)
                        skill_desc_map[upgrade_id] = main_text
                    except (ValueError, IndexError):
                        logger.debug(f"技能描述解析失败: upgrade_id={upgrade_id}")

            # 建立 skill_id -> first_upgrade_id 映射
            for uid in sorted(skill_desc_map.keys()):
                skill_part = uid // 1000
                if skill_part not in skill_to_upgrade:
                    skill_to_upgrade[skill_part] = uid

        logger.info(f"BaseSkill 解析: {len(skill_name_map)} 个技能名称, {len(skill_desc_map)} 条描述")

        # 6. 读取 BaseBadgeSuitGroup.lua → badge_suit_dict
        self.dl_progress.setValue(60)
        self.status_bar.showMessage("正在解析 BaseBadgeSuitGroup.lua...")
        QApplication.processEvents()
        bg_path = os.path.join(lua_dir, "BaseBadgeSuitGroup.lua")
        badge_suit_dict = {}
        if os.path.isfile(bg_path):
            badge_suit_dict = self._parse_badge_suit_file(bg_path, word_dict)
        logger.info(f"BaseBadgeSuitGroup 解析完成: {len(badge_suit_dict)} 套")

        # 7. 读取 BaseItem.lua → item_dict
        self.dl_progress.setValue(70)
        self.status_bar.showMessage("正在解析 BaseItem.lua...")
        QApplication.processEvents()
        it_path = os.path.join(lua_dir, "BaseItem.lua")
        item_dict = {}
        if os.path.isfile(it_path):
            item_dict = self._parse_item_file(it_path, word_dict)
        logger.info(f"BaseItem 解析完成: {len(item_dict)} 个物品")

        # 8. 读取 BaseSkillLevelUp.lua → skill_level_up_dict（带消耗）
        self.dl_progress.setValue(80)
        self.status_bar.showMessage("正在解析技能升级消耗...")
        QApplication.processEvents()
        skill_level_up_dict = {}
        if os.path.isfile(slu_path):
            skill_level_up_dict = self._parse_skill_level_up_file_with_cost(slu_path)
        logger.info(f"BaseSkillLevelUp 消耗解析完成: {len(skill_level_up_dict)} 条")

        # 9. 使用 _parse_basecard_file 解析 BaseCard.lua 得到完整角色数据
        self.dl_progress.setValue(90)
        self.status_bar.showMessage("正在解析 BaseCard.lua...")
        QApplication.processEvents()
        bc_path = os.path.join(lua_dir, "BaseCard.lua")
        if not os.path.isfile(bc_path):
            logger.warning("BaseCard.lua 不存在")
            self._character_loading = False
            return

        self.characters_full = self._parse_basecard_file(
            bc_path, word_dict, cv_dict, level_up_dict,
            quality_up_dict, sk_path, slu_path, badge_suit_dict
        )

        # 10. 添加突破消耗和技能升级消耗
        for char_id, char_data in self.characters_full.items():
            breakthrough_costs = self._get_breakthrough_cost(char_id, quality_up_dict, item_dict)
            char_data['breakthrough_costs'] = breakthrough_costs

            normal_skill_id = char_data.get('normal_skill_id', 0)
            if normal_skill_id:
                normal_upgrade_costs = self._get_normal_skill_upgrade_cost(normal_skill_id, skill_level_up_dict, item_dict)
                char_data['normal_skill_upgrade_costs'] = normal_upgrade_costs
            else:
                char_data['normal_skill_upgrade_costs'] = ["", "", ""]

            first_passive_skill_id = char_data.get('first_passive_skill_id', 0)
            if first_passive_skill_id:
                passive_upgrade_costs = self._get_passive_skill_upgrade_cost(first_passive_skill_id, skill_level_up_dict, item_dict)
                char_data['passive_skill_upgrade_costs'] = passive_upgrade_costs
            else:
                char_data['passive_skill_upgrade_costs'] = ["", "", ""]

        # 过滤 ID 范围 80100001~80101999
        filtered = {}
        for char_id, char_info in self.characters_full.items():
            raw_id = char_info.get("raw_id", 0)
            if 80100001 <= raw_id <= 80101999:
                filtered[char_id] = char_info

        # 填充 self.characters 列表（用于表格显示）
        self.characters = []
        for char_id, char_info in sorted(filtered.items(), key=lambda x: x[1].get("raw_id", 0)):
            self.characters.append({
                "name": char_info.get("name", "未知").split('/')[0],
                "char_id": char_id,
                "raw_id": char_info.get("raw_id", 0),
                "display_index": char_info.get("raw_id", 0)
            })

        self.dl_progress.setValue(100)
        self._populate_character_table()
        self._character_data_loaded = len(self.characters) > 0
        self._character_loading = False

        if len(self.characters) > 0:
            self.status_bar.showMessage(f"角色数据加载完成: {len(self.characters)} 个角色")
        else:
            self.status_bar.showMessage("角色数据加载完成: 无匹配角色")

    @staticmethod
    def _parse_word_file(file_path):
        """解析 BaseWord_cn.lua，返回 word_dict (id->文本)"""
        d = {}
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        # 使用完整模式匹配
        pattern = r'\[(\d+)\] = \{(.*?)\}(?=,|\n\s*\]|\n\s*\})'
        matches = re.findall(pattern, content, re.DOTALL)
        for key_id, item_content in matches:
            key_id = int(key_id)
            name_pattern = r'(?:name|sub_name) = "([^"]+)"'
            name_match = re.search(name_pattern, item_content)
            if name_match:
                d[key_id] = name_match.group(1)
        # 也匹配直接赋值的文本 [id] = "文本"
        for m in re.finditer(r'\[\s*(\d+)\s*\]\s*=\s*"([^"]*)"', content):
            d[int(m.group(1))] = m.group(2)
        return d

    @staticmethod
    def _parse_cv_file(file_path):
        """解析 BaseCvNameCn.lua，返回 cv_dict (id->"中文名/日文名")"""
        d = {}
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        pattern = r'\[(\d+)\] = \{(.*?)\}(?=,|\n\s*\]|\n\s*\})'
        matches = re.findall(pattern, content, re.DOTALL)
        for match in matches:
            cv_id, cv_data = match
            cv_id = int(cv_id)
            cn_pattern = r'name_cn = "([^"]+)"'
            cn_match = re.search(cn_pattern, cv_data)
            jp_pattern = r'name_jp = "([^"]+)"'
            jp_match = re.search(jp_pattern, cv_data)
            if cn_match and jp_match:
                cn_name = cn_match.group(1)
                jp_name = jp_match.group(1)
                d[cv_id] = f"{cn_name}/{jp_name}"
        return d

    @staticmethod
    def _parse_level_up_file(file_path):
        """解析 BaseCardLevelUp.lua，返回 level_up_dict (id->{属性名:增加值})"""
        d = {}
        attr_map = {"40000102": "生命", "40000103": "攻击", "40000104": "防御"}
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        pattern = r'\[(\d+)\] = (\{(?:[^{}]|(?:\{(?:[^{}]|(?:\{[^{}]*\}))*\}))*\})'
        matches = re.findall(pattern, content, re.DOTALL)
        for match in matches:
            level_id, level_data = match
            level_id = int(level_id)
            attr_pattern = r'add_attr = \{(.*?)\}'
            attr_match = re.search(attr_pattern, level_data, re.DOTALL)
            if attr_match:
                attr_content = attr_match.group(1)
                attr_items = re.findall(r'"([^]"]+)"', attr_content)
                attr_dict = {}
                for item in attr_items:
                    parts = item.split(':')
                    if len(parts) == 3:
                        atype = parts[1]
                        aval = int(parts[2])
                        aname = attr_map.get(atype, atype)
                        attr_dict[aname] = attr_dict.get(aname, 0) + aval
                if attr_dict:
                    d[level_id] = attr_dict
        return d

    @staticmethod
    def _parse_badge_suit_file(file_path, word_dict):
        """解析 BaseBadgeSuitGroup.lua，返回 badge_suit_dict (id->名称)"""
        d = {}
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        pattern = r'\[(\d+)\] = \{(.*?)\}(?=,|\n\s*\]|\n\s*\})'
        matches = re.findall(pattern, content, re.DOTALL)
        for match in matches:
            suit_id, suit_data = match
            suit_id = int(suit_id)
            name_pattern = r'name = function\(\)\s*return T\((\d+)\)\s*end'
            name_match = re.search(name_pattern, suit_data)
            if name_match:
                name_key = int(name_match.group(1))
                suit_name = word_dict.get(name_key, str(suit_id))
                d[suit_id] = suit_name
        return d

    @staticmethod
    def _parse_item_file(file_path, word_dict):
        """解析 BaseItem.lua，返回 item_dict (id->名称)"""
        d = {}
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        pattern = r'\[(\d+)\] = \{(.*?)\}(?=,|\n\s*\]|\n\s*\})'
        matches = re.findall(pattern, content, re.DOTALL)
        for match in matches:
            item_id, item_data = match
            item_id = int(item_id)
            name_pattern = r'name = function\(\)\s*return T\((\d+)\)\s*end'
            name_match = re.search(name_pattern, item_data)
            if name_match:
                name_key = int(name_match.group(1))
                item_name = word_dict.get(name_key, str(item_id))
                d[item_id] = item_name
        return d

    @staticmethod
    def _parse_quality_up_file_with_cost(file_path):
        """解析 BaseCardQualityUp.lua，返回带消耗的字典"""
        quality_up_dict = {}
        attr_map = {"40000102": "生命", "40000103": "攻击", "40000104": "防御"}
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        pattern = r'\[(\d+)\] = (\{(?:[^{}]|(?:\{(?:[^{}]|(?:\{[^{}]*\}))*\}))*\})'
        matches = re.findall(pattern, content, re.DOTALL)
        for match in matches:
            quality_id, quality_data = match
            quality_id = int(quality_id)
            attr_dict = {}
            am = re.search(r'add_attr = \{(.*?)\}', quality_data, re.DOTALL)
            if am:
                for item in re.findall(r'"([^]"]+)"', am.group(1)):
                    parts = item.split(':')
                    if len(parts) == 3:
                        atype = parts[1]
                        aval = int(parts[2])
                        aname = attr_map.get(atype, atype)
                        attr_dict[aname] = attr_dict.get(aname, 0) + aval
            cost_list = []
            cm = re.search(r'cost\s*=\s*\{([^}]*)\}', quality_data, re.DOTALL)
            if cm:
                for item in re.findall(r'"([^]"]+)"', cm.group(1)):
                    parts = item.split(':')
                    if len(parts) == 3:
                        item_id = int(parts[1])
                        item_count = int(parts[2])
                        cost_list.append((item_id, item_count))
            quality_up_dict[quality_id] = {"add_attr": attr_dict, "cost": cost_list}
        return quality_up_dict

    @staticmethod
    def _parse_t_function_params(content):
        """解析T函数参数"""
        params = []
        current_param = ""
        bracket_count = 0
        in_quotes = False
        for char in content:
            if char == '"' and not in_quotes:
                in_quotes = True
            elif char == '"' and in_quotes:
                in_quotes = False
            elif char == '(' and not in_quotes:
                bracket_count += 1
            elif char == ')' and not in_quotes:
                bracket_count -= 1
            elif char == ',' and bracket_count == 0 and not in_quotes:
                params.append(current_param.strip())
                current_param = ""
                continue
            current_param += char
        if current_param:
            params.append(current_param.strip())
        return params

    @staticmethod
    def _process_t_function_params(params, word_dict):
        """处理T函数参数（嵌套处理）"""
        processed_params = []
        for param in params:
            if isinstance(param, str) and param.startswith('T(') and param.endswith(')'):
                nested_content = param[2:-1]
                nested_parts = MainWindow._parse_t_function_params(nested_content)
                nested_processed = MainWindow._process_t_function_params(nested_parts, word_dict)
                if nested_processed:
                    try:
                        nested_id = int(nested_processed[0])
                        nested_text = word_dict.get(nested_id, f"未知({nested_id})")
                        for i, nested_param in enumerate(nested_processed[1:]):
                            if "%s" in nested_text:
                                nested_text = nested_text.replace("%s", str(nested_param), 1)
                            elif "%d" in nested_text:
                                nested_text = nested_text.replace("%d", str(nested_param), 1)
                        nested_text = nested_text.replace("%%", "%")
                        processed_params.append(nested_text)
                    except ValueError:
                        processed_params.append(f"参数错误: {nested_processed[0]}")
                else:
                    processed_params.append(param)
            else:
                processed_params.append(param)
        return processed_params

    @staticmethod
    def _merge_t_function_params(all_params):
        """合并多级T函数参数"""
        if not all_params:
            return []
        if not all(len(params) == len(all_params[0]) for params in all_params):
            return all_params[0]
        merged_params = []
        for i in range(len(all_params[0])):
            values = [params[i] for params in all_params]
            all_have_percent = True
            for v in values:
                if '%' not in str(v):
                    all_have_percent = False
                    break
            if all_have_percent:
                numeric_parts = []
                for v in values:
                    v_str = str(v)
                    if '%' in v_str:
                        percent_index = v_str.index('%')
                        numeric_part = v_str[:percent_index]
                        numeric_parts.append(numeric_part)
                    else:
                        numeric_parts.append(v_str)
                if all(n == numeric_parts[0] for n in numeric_parts):
                    merged_params.append(f"{numeric_parts[0]}%")
                else:
                    merged_params.append("/".join(numeric_parts) + "%")
            else:
                all_numeric = True
                for v in values:
                    if not re.match(r'^-?\d+(\.\d+)?$', str(v)):
                        all_numeric = False
                        break
                if all_numeric:
                    if all(v == values[0] for v in values):
                        merged_params.append(values[0])
                    else:
                        merged_params.append("/".join(map(str, values)))
                else:
                    if all(v == values[0] for v in values):
                        merged_params.append(values[0])
                    else:
                        merged_params.append("/".join(map(str, values)))
        return merged_params

    @staticmethod
    def _extract_skill_info(skill_id, skill_file_path, skill_level_up_file_path, word_dict):
        """提取单等级技能信息"""
        skill_name = "未知"
        skill_description = "未知"
        try:
            with open(skill_file_path, 'r', encoding='utf-8') as f:
                skill_content = f.read()
            skill_pattern = rf'\[{skill_id}\] = \{{(.*?)\}}'
            skill_match = re.search(skill_pattern, skill_content, re.DOTALL)
            if skill_match:
                skill_data = skill_match.group(1)
                name_pattern = r'name = function\(\)\s*return T\((\d+)\)\s*end'
                name_match = re.search(name_pattern, skill_data)
                if name_match:
                    name_key = int(name_match.group(1))
                    skill_name = word_dict.get(name_key, f"未知({name_key})")
                with open(skill_level_up_file_path, 'r', encoding='utf-8') as f:
                    skill_level_up_content = f.read()
                skill_level_id = skill_id * 1000 + 1
                skill_level_pattern = rf'\[{skill_level_id}\] = \{{(.*?)\}}'
                skill_level_match = re.search(skill_level_pattern, skill_level_up_content, re.DOTALL)
                if skill_level_match:
                    skill_level_data = skill_level_match.group(1)
                    des_pattern = r'des\s*=\s*function\(\)\s*return\s*T\((.*?)\)\s*end'
                    des_match = re.search(des_pattern, skill_level_data, re.DOTALL)
                    if des_match:
                        des_content = des_match.group(1)
                        params = MainWindow._parse_t_function_params(des_content)
                        processed_params = MainWindow._process_t_function_params(params, word_dict)
                        if processed_params:
                            try:
                                main_id = int(processed_params[0])
                                main_text = word_dict.get(main_id, f"未知({main_id})")
                                for i, param in enumerate(processed_params[1:]):
                                    if "%s" in main_text:
                                        main_text = main_text.replace("%s", str(param), 1)
                                main_text = main_text.replace("%%", "%")
                                main_text = main_text.replace("\\n", "\n")
                                main_text = re.sub(r'\[color=#[0-9a-fA-F]+\]', '', main_text)
                                main_text = re.sub(r'\[/color\]', '', main_text)
                                main_text = re.sub(r'\[[^\]]+\]', '', main_text)
                                skill_description = main_text
                            except ValueError:
                                skill_description = f"参数错误: {processed_params[0]}"
        except Exception:
            logger.debug(f"提取技能信息出错: skill_id={skill_id}")
        return f"{skill_name}\n{skill_description}"

    @staticmethod
    def _extract_awakening_skill_info(skill_id, skill_file_path, skill_level_up_file_path, word_dict):
        """提取觉醒技能信息"""
        skill_name = "未知"
        skill_description = "未知"
        try:
            with open(skill_file_path, 'r', encoding='utf-8') as f:
                skill_content = f.read()
            skill_pattern = rf'\[{skill_id}\] = \{{(.*?)\}}'
            skill_match = re.search(skill_pattern, skill_content, re.DOTALL)
            if skill_match:
                skill_data = skill_match.group(1)
                name_pattern = r'name = function\(\)\s*return T\((\d+)\)\s*end'
                name_match = re.search(name_pattern, skill_data)
                if name_match:
                    name_key = int(name_match.group(1))
                    skill_name = word_dict.get(name_key, f"未知({name_key})")
                with open(skill_level_up_file_path, 'r', encoding='utf-8') as f:
                    skill_level_up_content = f.read()
                skill_level_id = skill_id * 1000 + 1
                skill_level_pattern = rf'\[{skill_level_id}\] = \{{(.*?)\}}'
                skill_level_match = re.search(skill_level_pattern, skill_level_up_content, re.DOTALL)
                if skill_level_match:
                    skill_level_data = skill_level_match.group(1)
                    des_pattern = r'des\s*=\s*function\(\)\s*return\s*T\((.*?)\)\s*end'
                    des_match = re.search(des_pattern, skill_level_data, re.DOTALL)
                    if des_match:
                        des_content = des_match.group(1)
                        params = MainWindow._parse_t_function_params(des_content)
                        processed_params = MainWindow._process_t_function_params(params, word_dict)
                        if processed_params:
                            try:
                                main_id = int(processed_params[0])
                                main_text = word_dict.get(main_id, f"未知({main_id})")
                                for i, param in enumerate(processed_params[1:]):
                                    if "%s" in main_text:
                                        main_text = main_text.replace("%s", str(param), 1)
                                main_text = main_text.replace("%%", "%")
                                main_text = main_text.replace("\\n", "\n")
                                main_text = re.sub(r'\[color=#[0-9a-fA-F]+\]', '', main_text)
                                main_text = re.sub(r'\[/color\]', '', main_text)
                                main_text = re.sub(r'\[[^\]]+\]', '', main_text)
                                skill_description = main_text
                            except ValueError:
                                skill_description = f"参数错误: {processed_params[0]}"
        except Exception:
            logger.debug(f"提取觉醒技能信息出错: skill_id={skill_id}")
        return f"{skill_name}\n{skill_description}"

    @staticmethod
    def _extract_awakening_info(association_skill_id, skill_level_up_file_path, word_dict):
        """提取觉醒描述信息"""
        awakening_info = ""
        AWAKENING_MAPPING = {14: "觉醒1", 15: "觉醒2", 16: "觉醒3", 17: "觉醒4", 18: "觉醒5"}
        try:
            with open(skill_level_up_file_path, 'r', encoding='utf-8') as f:
                skill_level_up_content = f.read()
            skill_level_pattern = rf'(\[{association_skill_id}\]\s*=\s*\{{.*?association_des.*?\n\s*\}})'
            skill_level_match = re.search(skill_level_pattern, skill_level_up_content, re.DOTALL)
            if skill_level_match:
                skill_level_data = skill_level_match.group(1)
                association_des_pattern = r'association_des\s*=\s*function\(\)\s*return\s*T\((.*?)\)\s*end'
                association_des_match = re.search(association_des_pattern, skill_level_data, re.DOTALL)
                if association_des_match:
                    association_des_content = association_des_match.group(1)
                    params = MainWindow._parse_t_function_params(association_des_content)
                    processed_params = MainWindow._process_t_function_params(params, word_dict)
                    if processed_params:
                        try:
                            main_id = int(processed_params[0])
                            main_text = word_dict.get(main_id, f"未知({main_id})")
                            for i, param in enumerate(processed_params[1:]):
                                if "%s" in main_text:
                                    main_text = main_text.replace("%s", str(param), 1)
                            main_text = main_text.replace("%%", "%")
                            main_text = main_text.replace("\\n", "\n")
                            main_text = re.sub(r'\[color=#[0-9a-fA-F]+\]', '', main_text)
                            main_text = re.sub(r'\[/color\]', '', main_text)
                            main_text = re.sub(r'\[[^\]]+\]', '', main_text)
                            awakening_level = int(str(association_skill_id)[3:5])
                            awakening_name = AWAKENING_MAPPING.get(awakening_level, f"觉醒{awakening_level - 13}")
                            awakening_info = f"\n\n{awakening_name}\n{main_text}"
                        except ValueError:
                            awakening_info = f"\n\n觉醒描述参数错误"
        except Exception:
            logger.debug(f"提取觉醒信息出错: association_skill_id={association_skill_id}")
        return awakening_info

    @staticmethod
    def _extract_multi_level_skill_info_new(skill_id, skill_file_path, skill_level_up_file_path, word_dict,
                                            max_level_override=None, is_burst_skill=False):
        """提取多等级技能信息（在参数阶段合并）"""
        skill_name = "未知"
        skill_type = 0
        skill_cd = ""
        all_params = []
        try:
            with open(skill_file_path, 'r', encoding='utf-8') as f:
                skill_content = f.read()
            skill_pattern = rf'\[{skill_id}\] = \{{(.*?)\}}'
            skill_match = re.search(skill_pattern, skill_content, re.DOTALL)
            if skill_match:
                skill_data = skill_match.group(1)
                name_pattern = r'name = function\(\)\s*return T\((\d+)\)\s*end'
                name_match = re.search(name_pattern, skill_data)
                if name_match:
                    name_key = int(name_match.group(1))
                    skill_name = word_dict.get(name_key, f"未知({name_key})")
                if is_burst_skill:
                    cd_pattern = r'cd\s*=\s*(\d+)'
                    cd_match = re.search(cd_pattern, skill_data)
                    if cd_match:
                        skill_cd = cd_match.group(1) + "秒"
                        skill_name = f"{skill_name} {skill_cd}"
                type_pattern = r'type\s*=\s*(\d+)'
                type_match = re.search(type_pattern, skill_data)
                if type_match:
                    skill_type = int(type_match.group(1))
                max_level = 1
                if max_level_override is not None:
                    max_level = max_level_override
                elif skill_type == 1:
                    max_level = 4
                elif skill_type in [2, 7]:
                    max_level = 6
                else:
                    max_level_pattern = r'max_level\s*=\s*(\d+)'
                    max_level_match = re.search(max_level_pattern, skill_data)
                    if max_level_match:
                        max_level = int(max_level_match.group(1))
                with open(skill_level_up_file_path, 'r', encoding='utf-8') as f:
                    skill_level_up_content = f.read()
                for level in range(1, max_level + 1):
                    skill_level_id = skill_id * 1000 + level
                    skill_level_pattern = rf'\[{skill_level_id}\] = \{{(.*?)\}}'
                    skill_level_match = re.search(skill_level_pattern, skill_level_up_content, re.DOTALL)
                    if skill_level_match:
                        skill_level_data = skill_level_match.group(1)
                        des_pattern = r'des\s*=\s*function\(\)\s*return\s*T\((.*?)\)\s*end'
                        des_match = re.search(des_pattern, skill_level_data, re.DOTALL)
                        if des_match:
                            des_content = des_match.group(1)
                            params = MainWindow._parse_t_function_params(des_content)
                            processed_params = MainWindow._process_t_function_params(params, word_dict)
                            all_params.append(processed_params)
        except Exception:
            logger.debug(f"提取多等级技能信息出错: skill_id={skill_id}")
            return f"{skill_name}\n未知"

        # 检查觉醒技能
        awakening_info = ""
        try:
            if skill_match:
                skill_data = skill_match.group(1)
                association_pattern = r'association_skills\s*=\s*\{\s*"([^"]+)"'
                association_match = re.search(association_pattern, skill_data, re.DOTALL)
                if association_match:
                    association_content = association_match.group(1)
                    parts = association_content.split(':')
                    if len(parts) == 3:
                        association_skill_id = int(parts[1])
                        awakening_info = MainWindow._extract_awakening_info(association_skill_id, skill_level_up_file_path, word_dict)
        except Exception:
            logger.debug(f"提取觉醒技能信息出错: skill_id={skill_id}")

        if all_params:
            merged_params = MainWindow._merge_t_function_params(all_params)
            try:
                main_id = int(merged_params[0])
                main_text = word_dict.get(main_id, f"未知({main_id})")
                processed_params = []
                for param in merged_params[1:]:
                    if isinstance(param, str) and param.startswith('T(') and param.endswith(')'):
                        nested_content = param[2:-1]
                        nested_parts = MainWindow._parse_t_function_params(nested_content)
                        nested_processed = MainWindow._process_t_function_params(nested_parts, word_dict)
                        if nested_processed:
                            try:
                                nested_id = int(nested_processed[0])
                                nested_text = word_dict.get(nested_id, f"未知({nested_id})")
                                for i, nested_param in enumerate(nested_processed[1:]):
                                    if "%s" in nested_text:
                                        nested_text = nested_text.replace("%s", str(nested_param), 1)
                                    elif "%d" in nested_text:
                                        nested_text = nested_text.replace("%d", str(nested_param), 1)
                                nested_text = nested_text.replace("%%", "%")
                                processed_params.append(nested_text)
                            except ValueError:
                                processed_params.append(f"参数错误: {nested_processed[0]}")
                    else:
                        processed_params.append(param)
                for param in processed_params:
                    if "%s" in main_text:
                        main_text = main_text.replace("%s", str(param), 1)
                    elif "%d" in main_text:
                        main_text = main_text.replace("%d", str(param), 1)
                main_text = main_text.replace("%%", "%")
                main_text = re.sub(r'\[color=#[0-9a-fA-F]+\]', '', main_text)
                main_text = re.sub(r'\[/color\]', '', main_text)
                main_text = main_text.replace("\\n", "\n")
                return f"{skill_name}\n{main_text}{awakening_info}"
            except Exception:
                logger.debug(f"处理合并参数出错: skill_id={skill_id}")
        return f"{skill_name}\n未知{awakening_info}"

    @staticmethod
    def _parse_skill_level_up_file_with_cost(file_path):
        """解析BaseSkillLevelUp.lua，返回技能升级消耗字典"""
        skill_level_up_dict = {}
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        pattern = r'\[(\d+)\] = (\{(?:[^{}]|(?:\{(?:[^{}]|(?:\{[^{}]*\}))*\}))*\})'
        matches = re.findall(pattern, content, re.DOTALL)
        for match in matches:
            skill_level_id, skill_level_data = match
            skill_level_id = int(skill_level_id)
            cost_pattern = r'cost\s*=\s*\{([^}]*)\}'
            cost_match = re.search(cost_pattern, skill_level_data, re.DOTALL)
            cost_list = []
            if cost_match:
                cost_content = cost_match.group(1)
                cost_items = re.findall(r'"([^]"]+)"', cost_content)
                for item in cost_items:
                    parts = item.split(':')
                    if len(parts) == 3:
                        item_id = int(parts[1])
                        item_count = int(parts[2])
                        cost_list.append((item_id, item_count))
            skill_level_up_dict[skill_level_id] = cost_list
        return skill_level_up_dict

    @staticmethod
    def _get_breakthrough_cost(char_id, quality_up_dict, item_dict):
        """获取突破消耗"""
        breakthrough_costs = ["", "", "", ""]
        for i in range(4):
            quality_id = char_id * 1000 + i
            if quality_id in quality_up_dict:
                cost_items = quality_up_dict[quality_id].get("cost", [])
                cost_strings = []
                for item_id, item_count in cost_items:
                    item_name = item_dict.get(item_id, f"未知物品({item_id})")
                    cost_strings.append(f"{item_name} * {item_count}")
                if cost_strings:
                    breakthrough_costs[i] = " | ".join(cost_strings)
        return breakthrough_costs

    @staticmethod
    def _get_normal_skill_upgrade_cost(normal_skill_id, skill_level_up_dict, item_dict):
        """获取普通技能升级消耗"""
        upgrade_costs = ["", "", ""]
        for i in range(2, 5):
            skill_level_id = normal_skill_id * 1000 + i
            if skill_level_id in skill_level_up_dict:
                cost_items = skill_level_up_dict[skill_level_id]
                cost_strings = []
                for item_id, item_count in cost_items:
                    item_name = item_dict.get(item_id, f"未知物品({item_id})")
                    cost_strings.append(f"{item_name} * {item_count}")
                if cost_strings:
                    upgrade_costs[i - 2] = " | ".join(cost_strings)
        return upgrade_costs

    @staticmethod
    def _get_passive_skill_upgrade_cost(passive_skill_id, skill_level_up_dict, item_dict):
        """获取被动技能升级消耗"""
        upgrade_costs = ["", "", ""]
        for i in range(1, 4):
            skill_level_id = passive_skill_id * 1000 + i
            if skill_level_id in skill_level_up_dict:
                cost_items = skill_level_up_dict[skill_level_id]
                cost_strings = []
                for item_id, item_count in cost_items:
                    item_name = item_dict.get(item_id, f"未知物品({item_id})")
                    cost_strings.append(f"{item_name} * {item_count}")
                if cost_strings:
                    upgrade_costs[i - 1] = " | ".join(cost_strings)
        return upgrade_costs

    def _parse_basecard_file(self, file_path, word_dict, cv_dict, level_up_dict,
                             quality_up_dict, skill_file_path, skill_level_up_file_path,
                             badge_suit_dict):
        """完整解析 BaseCard.lua，返回角色数据字典（与参考脚本完全一致）"""
        TYPE_MAPPING = {1: "坚甲", 2: "异刃", 4: "言灵", 5: "猎影"}
        ELEMENT_MAPPING = {1: "水属性", 2: "火属性", 3: "木属性", 4: "暗属性", 5: "光属性"}
        # 语音类型映射（按顺序34条）
        VOICE_TYPE_MAPPING = [
            "成员报道", "问候", "闲谈1", "闲谈2", "闲谈3", "突破感悟1", "突破感悟2", "突破感悟3",
            "觉醒感悟1", "觉醒感悟2", "觉醒感悟3", "觉醒感悟4", "觉醒感悟5", "出战", "攻击1", "攻击2",
            "攻击3", "战技1", "战技2", "总攻技1", "总攻技2", "总攻技3", "受击1", "受击2", "受击3",
            "重伤", "退场", "作战胜利", "作战失败", "生日祝福", "新年祝福", "情人节祝福", "万圣节祝福", "圣诞节祝福"
        ]

        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()

        characters = {}
        pattern = r'\[(\d+)\] = (\{(?:[^{}]|(?:\{(?:[^{}]|(?:\{[^{}]*\}))*\}))*\})'
        matches = re.findall(pattern, content, re.DOTALL)

        for match in matches:
            char_id, char_data = match
            char_id = int(char_id)

            # 提取普通技能ID
            normal_skill_id = 0
            normal_skill_pattern = r'normal_skill\s*=\s*(\d+)'
            normal_skill_match = re.search(normal_skill_pattern, char_data)
            if normal_skill_match:
                normal_skill_id = int(normal_skill_match.group(1))

            # 提取第一个被动技能ID
            first_passive_skill_id = 0
            grow_skills_pattern = r'grow_skill_ids\s*=\s*\{([^}]*)\}'
            grow_skills_match = re.search(grow_skills_pattern, char_data)
            if grow_skills_match:
                grow_skills_content = grow_skills_match.group(1)
                grow_skill_ids = re.findall(r'(\d+)', grow_skills_content)
                if len(grow_skill_ids) > 0:
                    first_passive_skill_id = int(grow_skill_ids[0])

            # 提取名称
            name_pattern = r'name = function\(\)\s*return T\((\d+)\)\s*end'
            name_match = re.search(name_pattern, char_data)
            eng_name_pattern = r'name_english = function\(\)\s*return T\((\d+)\)\s*end'
            eng_name_match = re.search(eng_name_pattern, char_data)

            if not (name_match and eng_name_match):
                continue

            name_key = int(name_match.group(1))
            eng_name_key = int(eng_name_match.group(1))
            chinese_name = word_dict.get(name_key, f"未知({name_key})")
            english_name = word_dict.get(eng_name_key, f"Unknown({eng_name_key})")

            # 提取星级
            star = "未知"
            star_match = re.search(r'star\s*=\s*(\d+)', char_data)
            if star_match:
                star = star_match.group(1)

            # 提取初始属性
            init_hp = 0
            init_atk = 0
            init_def = 0
            hp_match = re.search(r'max_hp\s*=\s*(\d+)', char_data)
            atk_match = re.search(r'atk\s*=\s*(\d+)', char_data)
            def_match = re.search(r'def\s*=\s*(\d+)', char_data)
            if hp_match:
                init_hp = int(hp_match.group(1))
            if atk_match:
                init_atk = int(atk_match.group(1))
            if def_match:
                init_def = int(def_match.group(1))

            # 成长模型ID
            grow_model_id = 0
            grow_model_match = re.search(r'grow_model_id\s*=\s*(\d+)', char_data)
            if grow_model_match:
                grow_model_id = int(grow_model_match.group(1))

            quality_max = 0
            quality_max_match = re.search(r'quality_max\s*=\s*(\d+)', char_data)
            if quality_max_match:
                quality_max = int(quality_max_match.group(1))

            # 计算满级满破属性
            max_hp, max_atk, max_def = init_hp, init_atk, init_def
            if grow_model_id:
                level_up_id = grow_model_id * 1000 + 340
                level_up_attr = level_up_dict.get(level_up_id, {})
                max_hp += level_up_attr.get("生命", 0)
                max_atk += level_up_attr.get("攻击", 0)
                max_def += level_up_attr.get("防御", 0)
                quality_up_id = char_id * 1000 + quality_max
                quality_up_attr = quality_up_dict.get(quality_up_id, {})
                quality_up_attr = quality_up_attr.get('add_attr', {}) if isinstance(quality_up_attr, dict) else {}
                max_hp += quality_up_attr.get("生命", 0)
                max_atk += quality_up_attr.get("攻击", 0)
                max_def += quality_up_attr.get("防御", 0)

            # 提取职业
            profession = "未知"
            type_match = re.search(r'type\s*=\s*(\d+)', char_data)
            if type_match:
                type_id = int(type_match.group(1))
                profession = TYPE_MAPPING.get(type_id, f"未知({type_id})")

            # 提取属性
            element = "未知"
            element_match = re.search(r'element_type\s*=\s*\{(\d+)\}', char_data)
            if element_match:
                element_id = int(element_match.group(1))
                element = ELEMENT_MAPPING.get(element_id, f"未知({element_id})")

            # 提取生日
            birthday = "未知"
            info1_match = re.search(r'information1\s*=\s*function\(\)\s*return\s*T\(\d+,\s*(\d+),\s*(\d+)\)\s*end', char_data)
            if info1_match:
                month = info1_match.group(1)
                day = info1_match.group(2)
                birthday = f"{month}/{day}"

            # 提取身高
            height = "未知"
            info2_match = re.search(r'information2\s*=\s*function\(\)\s*return\s*T\(\d+,\s*(\d+)\)\s*end', char_data)
            if info2_match:
                height_value = info2_match.group(1)
                height = f"{height_value}cm"

            # 提取阵营
            faction = "未知"
            info3_match = re.search(r'information3\s*=\s*function\(\)\s*return\s*T\((\d+)\)\s*end', char_data)
            if info3_match:
                faction_key = int(info3_match.group(1))
                faction = word_dict.get(faction_key, f"未知({faction_key})")

            # 提取声优
            cv_info = "未知"
            cv_match = re.search(r'cv_name\s*=\s*(\d+)', char_data)
            if cv_match:
                cv_id = int(cv_match.group(1))
                cv_info = cv_dict.get(cv_id, f"未知({cv_id})")

            # 提取描述
            description = "未知"
            des_match = re.search(r'des\s*=\s*function\(\)\s*return\s*T\((\d+)\)\s*end', char_data)
            des1_match = re.search(r'des1\s*=\s*function\(\)\s*return\s*T\((\d+)\)\s*end', char_data)
            if des_match and des1_match:
                des_key = int(des_match.group(1))
                des1_key = int(des1_match.group(1))
                des_text = word_dict.get(des_key, f"未知({des_key})")
                des1_text = word_dict.get(des1_key, f"未知({des1_key})")
                description = f"{des_text}\n——{des1_text}"

            # 提取队长技能
            leader_skill_info = "未知"
            leader_skill_match = re.search(r'leader_skill\s*=\s*(\d+)', char_data)
            if leader_skill_match:
                leader_skill_id = int(leader_skill_match.group(1))
                leader_skill_info = MainWindow._extract_skill_info(leader_skill_id, skill_file_path, skill_level_up_file_path, word_dict)

            # 提取普通技能
            normal_skill_info = "未知"
            if normal_skill_match:
                normal_skill_id = int(normal_skill_match.group(1))
                normal_skill_info = MainWindow._extract_multi_level_skill_info_new(normal_skill_id, skill_file_path, skill_level_up_file_path, word_dict)

            # 提取特殊技能
            special_skill_info = "未知"
            special_skill_match = re.search(r'special_skill\s*=\s*(\d+)', char_data)
            if special_skill_match:
                special_skill_id = int(special_skill_match.group(1))
                special_skill_info = MainWindow._extract_multi_level_skill_info_new(special_skill_id, skill_file_path, skill_level_up_file_path, word_dict)

            # 提取爆发技能
            burst_skill_info = "未知"
            burst_skill_match = re.search(r'burst_skill\s*=\s*(\d+)', char_data)
            if burst_skill_match:
                burst_skill_id = int(burst_skill_match.group(1))
                burst_skill_info = MainWindow._extract_multi_level_skill_info_new(burst_skill_id, skill_file_path, skill_level_up_file_path, word_dict, is_burst_skill=True)

            # 提取被动技能
            passive_skill_1_info = "未知"
            passive_skill_2_info = "未知"
            passive_skill_3_info = "未知"
            if grow_skills_match:
                grow_skills_content = grow_skills_match.group(1)
                grow_skill_ids = re.findall(r'(\d+)', grow_skills_content)
                if len(grow_skill_ids) > 0:
                    passive_skill_1_id = int(grow_skill_ids[0])
                    passive_skill_1_info = MainWindow._extract_multi_level_skill_info_new(passive_skill_1_id, skill_file_path, skill_level_up_file_path, word_dict, 3)
                if len(grow_skill_ids) > 1:
                    passive_skill_2_id = int(grow_skill_ids[1])
                    passive_skill_2_info = MainWindow._extract_multi_level_skill_info_new(passive_skill_2_id, skill_file_path, skill_level_up_file_path, word_dict, 3)
                if len(grow_skill_ids) > 2:
                    passive_skill_3_id = int(grow_skill_ids[2])
                    passive_skill_3_info = MainWindow._extract_multi_level_skill_info_new(passive_skill_3_id, skill_file_path, skill_level_up_file_path, word_dict, 3)

            # 提取觉醒技能
            awakening_skill_1_info = "未知"
            awakening_skill_2_info = "未知"
            awakening_skill_3_info = "未知"
            awakening_skill_4_info = "未知"
            awakening_skill_5_info = "未知"
            unlock_skills_match = re.search(r'unlock_skill_ids\s*=\s*\{([^}]*)\}', char_data, re.DOTALL)
            if unlock_skills_match:
                unlock_skills_content = unlock_skills_match.group(1)
                unlock_skill_ids = re.findall(r'(\d+)', unlock_skills_content)
                for i, skill_id in enumerate(unlock_skill_ids):
                    if i < 5:
                        skill_id_int = int(skill_id)
                        skill_info = MainWindow._extract_awakening_skill_info(skill_id_int, skill_file_path, skill_level_up_file_path, word_dict)
                        if i == 0:
                            awakening_skill_1_info = skill_info
                        elif i == 1:
                            awakening_skill_2_info = skill_info
                        elif i == 2:
                            awakening_skill_3_info = skill_info
                        elif i == 3:
                            awakening_skill_4_info = skill_info
                        elif i == 4:
                            awakening_skill_5_info = skill_info

            # 提取语音
            voice_lines = [''] * 34
            sound_match = re.search(r'sound_ids\s*=\s*\{([^}]*)\}', char_data, re.DOTALL)
            if sound_match:
                sound_content = sound_match.group(1)
                sound_ids = re.findall(r'(\d+)', sound_content)
                if len(sound_ids) == 34:
                    for i, sound_id in enumerate(sound_ids):
                        if i < 34:
                            voice_text = word_dict.get(int(sound_id), "未知")
                            voice_lines[i] = f'"{voice_text}"'
                elif len(sound_ids) == 32:
                    for i, sound_id in enumerate(sound_ids):
                        if i < 32:
                            if i < 2:
                                pos = i
                            elif i < 4:
                                pos = i
                            elif i < 7:
                                pos = i + 1
                            elif i < 12:
                                pos = i + 1
                            elif i < 13:
                                pos = i + 1
                            elif i < 16:
                                pos = i + 1
                            elif i < 18:
                                pos = i + 1
                            elif i < 20:
                                pos = i + 1
                            elif i < 23:
                                pos = i + 2
                            else:
                                pos = i + 2
                            voice_text = word_dict.get(int(sound_id), "未知")
                            voice_lines[pos] = f'"{voice_text}"'
                else:
                    for i, sound_id in enumerate(sound_ids):
                        if i < 34:
                            voice_text = word_dict.get(int(sound_id), "未知")
                            voice_lines[i] = f'"{voice_text}"'

            # 提取角色故事
            personal_info = "未知"
            anecdote = "未知"
            record = "未知"
            anecdote2 = "未知"
            story_match = re.search(r'story_ids\s*=\s*\{([^}]*)\}', char_data, re.DOTALL)
            if story_match:
                story_content = story_match.group(1)
                story_items = re.findall(r'"([^"]+)"', story_content)
                for i, story_item in enumerate(story_items):
                    if ':' in story_item:
                        story_id_str, story_type = story_item.split(':')
                        story_id = int(story_id_str)
                        story_text = word_dict.get(story_id, f"未知({story_id})")
                        story_text = re.sub(r'<[^>]+>', '', story_text)
                        story_text = re.sub(r'&[a-z]+;', '', story_text)
                        story_text = re.sub(r'<style[^>]*>.*?</style>', '', story_text, flags=re.DOTALL)
                        story_text = re.sub(r'<script[^>]*>.*?</script>', '', story_text, flags=re.DOTALL)
                        story_text = re.sub(r'<!--.*?-->', '', story_text, flags=re.DOTALL)
                        story_text = re.sub(r'<p style=\'text-align: right;\'>.*?</p>', '', story_text)
                        story_text = re.sub(r'<span.*?>.*?</span>', '', story_text)
                        story_text = re.sub(r'<.*?>', '', story_text)
                        story_text = re.sub(r'\s+', ' ', story_text)
                        story_text = story_text.strip()
                        story_text = story_text.replace("\\n", "\n")
                        if i == 0:
                            personal_info = story_text
                        elif i == 1:
                            anecdote = story_text
                        elif i == 2:
                            record = story_text
                        elif i == 3:
                            anecdote2 = story_text

            # 提取徽章信息
            badge_info = ""
            badge_suit_match = re.search(r'badge_suit_ids\s*=\s*\{([^}]*)\}', char_data, re.DOTALL)
            badge_main_match = re.search(r'badge_main_attribute\s*=\s*\{([^}]*)\}', char_data, re.DOTALL)
            badge_vice_match = re.search(r'badge_vice_attribute\s*=\s*\{([^}]*)\}', char_data, re.DOTALL)
            if badge_suit_match or badge_main_match or badge_vice_match:
                badge_suit_names = []
                if badge_suit_match:
                    badge_suit_content = badge_suit_match.group(1)
                    badge_suit_ids = re.findall(r'(\d+)', badge_suit_content)
                    for suit_id in badge_suit_ids:
                        suit_name = badge_suit_dict.get(int(suit_id), f"未知({suit_id})")
                        badge_suit_names.append(suit_name)
                main_attrs = []
                if badge_main_match:
                    badge_main_content = badge_main_match.group(1)
                    badge_main_items = re.findall(r'"([^"]+)"', badge_main_content)
                    for main_item in badge_main_items:
                        if ':' in main_item:
                            attr_ids = main_item.split(':')
                            attr_names = []
                            for attr_id in attr_ids:
                                text_id = int('8' + attr_id[1:])
                                attr_name = word_dict.get(text_id, f"未知({text_id})")
                                attr_names.append(attr_name)
                            main_attrs.append(' '.join(attr_names))
                        else:
                            text_id = int('8' + main_item[1:])
                            attr_name = word_dict.get(text_id, f"未知({text_id})")
                            main_attrs.append(attr_name)
                vice_attrs = []
                if badge_vice_match:
                    badge_vice_content = badge_vice_match.group(1)
                    badge_vice_ids = re.findall(r'(\d+)', badge_vice_content)
                    for vice_id in badge_vice_ids:
                        text_id = int('8' + vice_id[1:])
                        attr_name = word_dict.get(text_id, f"未知({text_id})")
                        vice_attrs.append(attr_name)
                badge_info_parts = []
                if badge_suit_names:
                    badge_info_parts.append("推荐徽章")
                    badge_info_parts.append("/".join(badge_suit_names))
                    badge_info_parts.append("")
                if main_attrs:
                    badge_info_parts.append("推荐主属性")
                    badge_info_parts.append("//".join(main_attrs))
                    badge_info_parts.append("")
                if vice_attrs:
                    badge_info_parts.append("推荐副属性")
                    badge_info_parts.append(" ".join(vice_attrs))
                badge_info = "\n".join(badge_info_parts)

            # 提取其他属性
            crt_value = "未知"
            crt_match = re.search(r'crt\s*=\s*(\d+)', char_data)
            if crt_match:
                crt_value = crt_match.group(1)
            blk_value = "未知"
            blk_match = re.search(r'blk\s*=\s*(\d+)', char_data)
            if blk_match:
                blk_value = blk_match.group(1)
            crt_int_value = "未知"
            crt_int_match = re.search(r'crt_int\s*=\s*(\d+)', char_data)
            if crt_int_match:
                crt_int_value = crt_int_match.group(1)
            blk_int_value = "未知"
            blk_int_match = re.search(r'blk_int\s*=\s*(\d+)', char_data)
            if blk_int_match:
                blk_int_value = blk_int_match.group(1)
            spd_move_value = "未知"
            spd_move_match = re.search(r'spd_move\s*=\s*(\d+)', char_data)
            if spd_move_match:
                spd_move_value = spd_move_match.group(1)
            spd_atk_value = "未知"
            spd_atk_match = re.search(r'spd_atk\s*=\s*(\d+)', char_data)
            if spd_atk_match:
                spd_atk_value = spd_atk_match.group(1)
            range_atk_value = "未知"
            range_atk_match = re.search(r'range_atk\s*=\s*(\d+)', char_data)
            if range_atk_match:
                range_atk_value = range_atk_match.group(1)
            weight_value = "未知"
            weight_match = re.search(r'weight\s*=\s*(\d+)', char_data)
            if weight_match:
                weight_value = weight_match.group(1)

            characters[char_id] = {
                "raw_id": name_key,
                "name": f"{chinese_name}/{english_name}",
                "star": star,
                "profession": profession,
                "element": element,
                "birthday": birthday,
                "height": height,
                "faction": faction,
                "cv": cv_info,
                "description": description,
                "init_atk": init_atk, "init_def": init_def, "init_hp": init_hp,
                "max_atk": max_atk, "max_def": max_def, "max_hp": max_hp,
                "crt": crt_value, "blk": blk_value,
                "crt_int": crt_int_value, "blk_int": blk_int_value,
                "spd_move": spd_move_value, "spd_atk": spd_atk_value, "range_atk": range_atk_value,
                "weight": weight_value,
                "leader_skill": leader_skill_info,
                "normal_skill": normal_skill_info,
                "special_skill": special_skill_info,
                "burst_skill": burst_skill_info,
                "passive_skill_1": passive_skill_1_info,
                "passive_skill_2": passive_skill_2_info,
                "passive_skill_3": passive_skill_3_info,
                "awakening_skill_1": awakening_skill_1_info,
                "awakening_skill_2": awakening_skill_2_info,
                "awakening_skill_3": awakening_skill_3_info,
                "awakening_skill_4": awakening_skill_4_info,
                "awakening_skill_5": awakening_skill_5_info,
                "voice_1": voice_lines[0], "voice_2": voice_lines[1], "voice_3": voice_lines[2],
                "voice_4": voice_lines[3], "voice_5": voice_lines[4], "voice_6": voice_lines[5],
                "voice_7": voice_lines[6], "voice_8": voice_lines[7], "voice_9": voice_lines[8],
                "voice_10": voice_lines[9], "voice_11": voice_lines[10], "voice_12": voice_lines[11],
                "voice_13": voice_lines[12], "voice_14": voice_lines[13], "voice_15": voice_lines[14],
                "voice_16": voice_lines[15], "voice_17": voice_lines[16], "voice_18": voice_lines[17],
                "voice_19": voice_lines[18], "voice_20": voice_lines[19], "voice_21": voice_lines[20],
                "voice_22": voice_lines[21], "voice_23": voice_lines[22], "voice_24": voice_lines[23],
                "voice_25": voice_lines[24], "voice_26": voice_lines[25], "voice_27": voice_lines[26],
                "voice_28": voice_lines[27], "voice_29": voice_lines[28], "voice_30": voice_lines[29],
                "voice_31": voice_lines[30], "voice_32": voice_lines[31], "voice_33": voice_lines[32],
                "voice_34": voice_lines[33],
                "personal_info": personal_info,
                "anecdote": anecdote,
                "record": record,
                "anecdote2": anecdote2,
                "badge_info": badge_info,
                "normal_skill_id": normal_skill_id,
                "first_passive_skill_id": first_passive_skill_id
            }

        logger.debug(f"BaseCard.lua 解析完成: {len(characters)} 个角色")
        return characters

    def _populate_character_table(self):
        """填充角色表格"""
        self.character_table.setSortingEnabled(False)
        self.character_table.setRowCount(0)
        count = len(self.characters)
        if count == 0:
            self.character_empty.setVisible(True)
            self.character_status.setText("暂无角色数据")
            return
        self.character_empty.setVisible(False)
        self.character_table.setRowCount(count)
        for i, char in enumerate(self.characters):
            idx_item = QTableWidgetItem(str(char.get("display_index", i + 1)))
            idx_item.setTextAlignment(Qt.AlignCenter)
            self.character_table.setItem(i, 0, idx_item)
            name_item = QTableWidgetItem(char.get("name", "未知"))
            self.character_table.setItem(i, 1, name_item)
        self.character_status.setText(f"共 {count} 个角色")

    def _filter_character_table(self, text):
        """根据搜索文本过滤角色表格"""
        for i in range(self.character_table.rowCount()):
            name_item = self.character_table.item(i, 1)
            if name_item:
                match = text.lower() in name_item.text().lower()
                self.character_table.setRowHidden(i, not match)

    def _refresh_character_list(self):
        """刷新角色列表（重新加载 Lua 文件，不重新解密）"""
        self._load_character_data()

    def _on_character_select(self):
        """角色表格选中行时更新详情卡片"""
        rows = self.character_table.selectionModel().selectedRows()
        if not rows:
            return
        row = rows[0].row()
        if row < 0 or row >= len(self.characters):
            return
        # 从 self.characters 获取角色信息
        char_item = self.characters[row]
        # 优先使用 char_id 查找
        char_id = char_item.get("char_id", 0)
        char_data = self.characters_full.get(char_id, {})
        # 如果 char_id 找不到，尝试用 raw_id 查找
        if not char_data:
            raw_id = char_item.get("raw_id", 0)
            for cid, cinfo in self.characters_full.items():
                if cinfo.get("raw_id") == raw_id:
                    char_data = cinfo
                    break
        if char_data:
            self._update_character_detail(char_data)

    def _update_character_detail(self, char):
        """更新右侧详情卡片（展示文图效果的全部内容，放入 QScrollArea）"""
        # 辅助函数
        def _nl(text):
            return text.replace("\n", "<br/>") if text else ""

        def _pct(v):
            if v and v != "未知":
                try:
                    n = int(v)
                    return f"{n // 100}%"
                except (ValueError, TypeError):
                    return str(v)
            return "-"

        name = char.get("name", "未知").split('/')[0] if '/' in str(char.get("name", "")) else char.get("name", "未知")
        self.character_detail_name.setText(name)

        # ---- 基础信息 ----
        star = char.get("star", "未知")
        profession = char.get("profession", "未知")
        element = char.get("element", "未知")
        birthday = char.get("birthday", "未知")
        height = char.get("height", "未知")
        faction = char.get("faction", "未知")
        cv = char.get("cv", "未知")
        description = _nl(char.get("description", "未知"))

        # ---- 战斗属性 ----
        init_hp = char.get("init_hp", 0)
        init_atk = char.get("init_atk", 0)
        init_def = char.get("init_def", 0)
        max_hp = char.get("max_hp", 0)
        max_atk = char.get("max_atk", 0)
        max_def = char.get("max_def", 0)

        crt = _pct(char.get("crt", "0"))
        blk = _pct(char.get("blk", "0"))
        crt_int = _pct(char.get("crt_int", "0"))
        blk_int = _pct(char.get("blk_int", "0"))
        spd_move = char.get("spd_move", "未知")
        spd_atk = char.get("spd_atk", "未知")
        range_atk = char.get("range_atk", "未知")

        # ---- 构建 HTML 内容 ----
        html_parts = []
        html_parts.append(f"<h2>基础信息</h2>")
        html_parts.append(f"<p><b>名称：</b>{name}</p>")
        html_parts.append(f"<p><b>星级：</b>{star}</p>")
        html_parts.append(f"<p><b>职业：</b>{profession}</p>")
        html_parts.append(f"<p><b>属性：</b>{element}</p>")
        html_parts.append(f"<p><b>生日：</b>{birthday}</p>")
        html_parts.append(f"<p><b>身高：</b>{height}</p>")
        html_parts.append(f"<p><b>阵营：</b>{faction}</p>")
        html_parts.append(f"<p><b>CV：</b>{cv}</p>")
        html_parts.append(f"<p><b>简介：</b><br/>{description}</p>")

        html_parts.append(f"<h2>战斗属性</h2>")
        html_parts.append(f"<p><b>初始生命：</b>{init_hp} → <b>满级生命：</b>{max_hp}</p>")
        html_parts.append(f"<p><b>初始攻击：</b>{init_atk} → <b>满级攻击：</b>{max_atk}</p>")
        html_parts.append(f"<p><b>初始防御：</b>{init_def} → <b>满级防御：</b>{max_def}</p>")
        html_parts.append(f"<p><b>暴击：</b>{crt} &nbsp; <b>格挡：</b>{blk}</p>")
        html_parts.append(f"<p><b>暴击伤害：</b>{crt_int} &nbsp; <b>格挡效果：</b>{blk_int}</p>")
        html_parts.append(f"<p><b>移动速度：</b>{spd_move} &nbsp; <b>攻击速度：</b>{spd_atk} &nbsp; <b>攻击范围：</b>{range_atk}</p>")

        # ---- 技能 ----
        html_parts.append(f"<h2>技能</h2>")
        skill_fields = [
            ("队长技能", "leader_skill"),
            ("普通技能", "normal_skill"),
            ("特殊技能", "special_skill"),
            ("爆发技能", "burst_skill"),
        ]
        for label, field in skill_fields:
            val = char.get(field, "未知")
            if val and val != "未知":
                html_parts.append(f"<p><b>{label}：</b><br/>{_nl(val)}</p>")

        # 被动技能
        for i in range(1, 4):
            val = char.get(f"passive_skill_{i}", "未知")
            if val and val != "未知":
                html_parts.append(f"<p><b>被动技能{i}：</b><br/>{_nl(val)}</p>")

        # 觉醒技能
        for i in range(1, 6):
            val = char.get(f"awakening_skill_{i}", "未知")
            if val and val != "未知":
                html_parts.append(f"<p><b>觉醒技能{i}：</b><br/>{_nl(val)}</p>")

        # ---- 徽章推荐 ----
        badge_info = char.get("badge_info", "")
        if badge_info:
            html_parts.append(f"<h2>徽章推荐</h2>")
            html_parts.append(f"<p>{_nl(badge_info)}</p>")

        # ---- 突破消耗 ----
        breakthrough_costs = char.get("breakthrough_costs", ["", "", "", ""])
        if any(breakthrough_costs):
            html_parts.append(f"<h2>突破消耗</h2>")
            labels = ["突破一", "突破二", "突破三", "突破四"]
            for i, cost in enumerate(breakthrough_costs):
                if cost:
                    html_parts.append(f"<p><b>{labels[i]}：</b>{cost}</p>")

        # ---- 技能升级消耗 ----
        normal_upgrade_costs = char.get("normal_skill_upgrade_costs", ["", "", ""])
        if any(normal_upgrade_costs):
            html_parts.append(f"<h2>普通技能升级消耗</h2>")
            for i, cost in enumerate(normal_upgrade_costs):
                if cost:
                    html_parts.append(f"<p><b>升级{i+1}：</b>{cost}</p>")

        passive_upgrade_costs = char.get("passive_skill_upgrade_costs", ["", "", ""])
        if any(passive_upgrade_costs):
            html_parts.append(f"<h2>被动技能升级消耗</h2>")
            for i, cost in enumerate(passive_upgrade_costs):
                if cost:
                    html_parts.append(f"<p><b>升级{i+1}：</b>{cost}</p>")

        # ---- 语音 ----
        html_parts.append(f"<h2>语音</h2>")
        voice_labels = [
            "成员报道", "问候", "闲谈1", "闲谈2", "闲谈3",
            "突破感悟1", "突破感悟2", "突破感悟3",
            "觉醒感悟1", "觉醒感悟2", "觉醒感悟3", "觉醒感悟4", "觉醒感悟5",
            "出战", "攻击1", "攻击2", "攻击3", "战技1", "战技2",
            "总攻技1", "总攻技2", "总攻技3", "受击1", "受击2", "受击3",
            "重伤", "退场", "作战胜利", "作战失败",
            "生日祝福", "新年祝福", "情人节祝福", "万圣节祝福", "圣诞节祝福"
        ]
        for i in range(34):
            voice_key = f"voice_{i+1}"
            voice_text = char.get(voice_key, "")
            if voice_text:
                html_parts.append(f"<p><b>{voice_labels[i]}：</b>{voice_text}</p>")

        # ---- 故事 ----
        html_parts.append(f"<h2>故事</h2>")
        story_fields = [
            ("个人情报", "personal_info"),
            ("风闻", "anecdote"),
            ("记录", "record"),
            ("逸事", "anecdote2"),
        ]
        for label, field in story_fields:
            val = char.get(field, "未知")
            if val and val != "未知":
                html_parts.append(f"<p><b>{label}：</b><br/>{_nl(val)}</p>")

        full_html = "<html><body>" + "<br/>".join(html_parts) + "</body></html>"
        self.character_detail_info.setText(full_html)

    def _export_characters_csv(self):
        """导出角色数据为 CSV 文件（直接从 self.characters_full 缓存写入）"""
        if not self.characters_full:
            QMessageBox.information(self, "提示", "没有角色数据可导出，请先加载角色视图。")
            return

        file_path, _ = QFileDialog.getSaveFileName(
            self, "保存 CSV 文件", "characters.csv", "CSV 文件 (*.csv)"
        )
        if not file_path:
            return

        self.status_bar.showMessage("正在生成角色数据...")
        QApplication.processEvents()

        headers = [
            "ID", "Name", "Star", "Profession", "Element", "Birthday", "Height", "Faction", "CV", "Description",
            "Init_ATK", "Init_DEF", "Init_HP", "Max_ATK", "Max_DEF", "Max_HP",
            "CRT", "BLK", "CRT_INT", "BLK_INT", "SPD_MOVE", "SPD_ATK", "RANGE_ATK", "WEIGHT",
            "Leader_Skill", "Normal_Skill", "Special_Skill", "Burst_Skill",
            "Passive_Skill_1", "Passive_Skill_2", "Passive_Skill_3",
            "觉醒1", "觉醒2", "觉醒3", "觉醒4", "觉醒5",
            "成员报道", "问候", "闲谈1", "闲谈2", "闲谈3",
            "突破感悟1", "突破感悟2", "突破感悟3",
            "觉醒感悟1", "觉醒感悟2", "觉醒感悟3", "觉醒感悟4", "觉醒感悟5",
            "出战", "攻击1", "攻击2", "攻击3", "战技1", "战技2",
            "总攻技1", "总攻技2", "总攻技3", "受击1", "受击2", "受击3",
            "重伤", "退场", "作战胜利", "作战失败",
            "生日祝福", "新年祝福", "情人节祝福", "万圣节祝福", "圣诞节祝福",
            "个人情报", "风闻", "记录", "逸事",
            "推荐徽章",
            "突破一", "突破二", "突破三", "突破四",
            "普通技能升级一", "普通技能升级二", "普通技能升级三",
            "被动技能升级一", "被动技能升级二", "被动技能升级三"
        ]

        # 过滤 ID 范围 80100001~80101999
        filtered_full = {}
        for char_id, char_info in self.characters_full.items():
            raw_id = char_info.get("raw_id", 0)
            if 80100001 <= raw_id <= 80101999:
                filtered_full[char_id] = char_info

        if not filtered_full:
            QMessageBox.information(self, "提示", "未找到匹配的角色数据。")
            self.status_bar.showMessage("CSV 导出失败：无匹配角色")
            return

        import csv
        with open(file_path, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f)
            writer.writerow(headers)

            for char_id, char_info in sorted(filtered_full.items(), key=lambda x: x[1].get("raw_id", 0)):
                def _g(key, default=""):
                    v = char_info.get(key, default)
                    if v is None:
                        return default
                    return str(v)

                def _skill(field, default=""):
                    val = char_info.get(field, default)
                    if val and val != "未知":
                        return val.replace("\n", " | ").replace("\"", "'")
                    return default

                def _voice(idx):
                    v = char_info.get(f"voice_{idx}", "")
                    if v:
                        return v.strip('"')
                    return ""

                row = [
                    _g("raw_id"),
                    _g("name", "").split('/')[0] if '/' in str(_g("name", "")) else _g("name", ""),
                    _g("star"),
                    _g("profession"),
                    _g("element"),
                    _g("birthday"),
                    _g("height"),
                    _g("faction"),
                    _g("cv"),
                    _g("description", "").replace("\n", " "),
                    _g("init_atk"), _g("init_def"), _g("init_hp"),
                    _g("max_atk"), _g("max_def"), _g("max_hp"),
                    _g("crt"), _g("blk"), _g("crt_int"), _g("blk_int"),
                    _g("spd_move"), _g("spd_atk"), _g("range_atk"), _g("weight"),
                    _skill("leader_skill"),
                    _skill("normal_skill"),
                    _skill("special_skill"),
                    _skill("burst_skill"),
                    _skill("passive_skill_1"),
                    _skill("passive_skill_2"),
                    _skill("passive_skill_3"),
                    _skill("awakening_skill_1"),
                    _skill("awakening_skill_2"),
                    _skill("awakening_skill_3"),
                    _skill("awakening_skill_4"),
                    _skill("awakening_skill_5"),
                ]
                # 34条语音
                for i in range(1, 35):
                    row.append(_voice(i))
                # 故事
                row.append(_g("personal_info", "").replace("\n", " "))
                row.append(_g("anecdote", "").replace("\n", " "))
                row.append(_g("record", "").replace("\n", " "))
                row.append(_g("anecdote2", "").replace("\n", " "))
                # 徽章推荐
                row.append(_g("badge_info", "").replace("\n", " | "))
                # 突破消耗
                breakthrough_costs = char_info.get("breakthrough_costs", ["", "", "", ""])
                for i in range(4):
                    row.append(breakthrough_costs[i] if i < len(breakthrough_costs) else "")
                # 普通技能升级消耗
                normal_upgrade_costs = char_info.get("normal_skill_upgrade_costs", ["", "", ""])
                for i in range(3):
                    row.append(normal_upgrade_costs[i] if i < len(normal_upgrade_costs) else "")
                # 被动技能升级消耗
                passive_upgrade_costs = char_info.get("passive_skill_upgrade_costs", ["", "", ""])
                for i in range(3):
                    row.append(passive_upgrade_costs[i] if i < len(passive_upgrade_costs) else "")

                writer.writerow(row)

        logger.info(f"CSV 导出完成: {file_path} ({len(filtered_full)} 个角色)")
        self.status_bar.showMessage(f"CSV 导出完成: {len(filtered_full)} 个角色")

    # ========== Lua Decrypt ==========

    def _start_lua_decrypt(self):
        """点击【角色】按钮，切换角色视图显示/隐藏"""
        if self._show_character:
            # 当前已显示角色视图，切换到版本列表
            self._toggle_character_mode(False)
            return

        # 切换到角色视图
        self._toggle_character_mode(True)

        # 如果已加载过数据，直接显示
        if self._character_data_loaded:
            self._populate_character_table()
            return

        # 如果正在加载中，不重复启动
        if self._character_loading:
            return

        # 计算路径
        lua_dir = os.path.join(DATA_DIR, "material", "assets", "lua")
        tools_dir = get_tools_dir()
        unluac_path = os.path.join(tools_dir, "lua", "unluac.jar")
        opmap_path = os.path.join(tools_dir, "lua", "opmap")

        # 依赖检查
        if not os.path.isfile(unluac_path):
            logger.error(f"unluac.jar 不存在: {unluac_path}")
            QMessageBox.warning(self, "错误", f"unluac.jar 不存在:\n{unluac_path}")
            return
        if not os.path.isfile(opmap_path):
            logger.error(f"opmap 文件不存在: {opmap_path}")
            QMessageBox.warning(self, "错误", f"opmap 文件不存在:\n{opmap_path}")
            return

        if not os.path.isdir(lua_dir):
            logger.info(f"Lua 目录不存在: {lua_dir}")
            self.status_bar.showMessage("Lua 目录不存在，请先导入资源")
            return

        # 取消已有线程
        if self._lua_worker is not None:
            self._lua_worker.cancel()
            self._lua_worker.wait(2000)

        # 标记加载中
        self._character_loading = True
        self._character_data_loaded = False

        # 禁用按钮，显示进度
        self.btn_lua.setEnabled(False)
        self.dl_progress.setVisible(True)
        self.dl_progress.setValue(0)
        self.dl_progress.setFormat("Lua 解密准备中...")
        self.status_bar.showMessage("正在解密中...")
        logger.info(f"开始 Lua 解密: 目录 {lua_dir}")

        # 启动后台线程
        self._lua_worker = LuaDecryptWorker(lua_dir, unluac_path, opmap_path, self)
        self._lua_worker.progress.connect(self._on_lua_decrypt_progress)
        self._lua_worker.finished.connect(self._on_lua_decrypt_finished)
        self._lua_worker.error.connect(self._on_lua_decrypt_error)
        self._lua_worker.file_done.connect(self._on_lua_file_done)
        self._lua_worker.start()

    def _on_lua_decrypt_progress(self, msg):
        """Lua 解密进度更新"""
        self.status_bar.showMessage(msg)
        # 尝试从消息中解析进度数值
        m = re.search(r'(\d+)/(\d+)', msg)
        if m:
            self.dl_progress.setMaximum(int(m.group(2)))
            self.dl_progress.setValue(int(m.group(1)))
        self.dl_progress.setFormat(msg[:50])

    def _on_lua_decrypt_finished(self, success_count, fail_count):
        """Lua 解密完成 → 加载角色数据"""
        self.btn_lua.setEnabled(True)
        self.dl_progress.setVisible(False)
        self._character_loading = False
        total = success_count + fail_count
        if total == 0:
            self.status_bar.showMessage("Lua 解密: 无待处理文件")
            self._character_data_loaded = False
            return
        msg = f"Lua 解密完成: 成功 {success_count} 个, 失败 {fail_count} 个"
        self.status_bar.showMessage(msg)
        logger.info(msg)
        # 自动加载角色数据
        self._load_character_data()

    def _on_lua_decrypt_error(self, err_msg):
        """Lua 解密错误"""
        self.btn_lua.setEnabled(True)
        self.dl_progress.setVisible(False)
        self._character_loading = False
        logger.error(f"Lua 解密错误: {err_msg}")
        self.status_bar.showMessage(f"Lua 解密错误: {err_msg}")
        QMessageBox.warning(self, "Lua 解密错误", err_msg)

    def _on_lua_file_done(self, filename):
        """当 BaseWord_cn.lua 或 BaseCard.lua 解密完成时，立即加载角色数据"""
        logger.info(f"角色数据文件解密完成: {filename}，立即加载")
        # 如果当前角色视图可见且未在加载中，则刷新数据
        if self.character_container.isVisible() and not self._character_loading:
            self._load_character_data()
            # 强制刷新表格和状态
            self._populate_character_table()
            if len(self.characters) > 0:
                self.character_status.setText(f"共 {len(self.characters)} 个角色")
            else:
                self.character_status.setText("暂无角色数据")
        else:
            # 若视图不可见，设置待刷新标志，下次打开时自动加载
            self._pending_refresh = True

    def _export_selected_audio(self):
        """导出选中的音频文件"""
        selected = []
        for i in range(self.audio_table.rowCount()):
            cb_widget = self.audio_table.cellWidget(i, 0)
            if cb_widget:
                cb = cb_widget.findChild(QCheckBox)
                if cb and cb.isChecked():
                    selected.append(self._audio_files[i])

        if not selected:
            QMessageBox.information(self, "提示", "请先勾选要导出的音频文件")
            return

        dst_dir = QFileDialog.getExistingDirectory(self, "选择导出目录")
        if not dst_dir:
            return

        success = 0
        for info in selected:
            try:
                dst = os.path.join(dst_dir, info["name"])
                shutil.copy2(info["path"], dst)
                success += 1
            except (OSError, PermissionError) as e:
                logger.error(f"导出失败 {info['name']}: {e}")

        QMessageBox.information(self, "导出完成", f"成功导出 {success} 个音频文件到:\n{dst_dir}")
        logger.info(f"导出 {success} 个音频文件到 {dst_dir}")

    def _play_selected_audio(self):
        """播放选中的音频文件（取第一个）"""
        for i in range(self.audio_table.rowCount()):
            cb_widget = self.audio_table.cellWidget(i, 0)
            if cb_widget:
                cb = cb_widget.findChild(QCheckBox)
                if cb and cb.isChecked():
                    info = self._audio_files[i]
                    self._play_audio_file(info["path"], info["name"])
                    return

        # 如果没有勾选，尝试播放当前选中行
        row = self.audio_table.currentRow()
        if row >= 0 and row < len(self._audio_files):
            info = self._audio_files[row]
            self._play_audio_file(info["path"], info["name"])

    def _on_audio_double_click(self, index):
        """双击播放音频"""
        row = index.row()
        if row < 0 or row >= len(self._audio_files):
            return
        info = self._audio_files[row]
        self._play_audio_file(info["path"], info["name"])

    def _play_audio_file(self, filepath, filename):
        """播放指定音频文件"""
        if not QT_MULTIMEDIA_AVAILABLE or self._audio_player is None:
            QMessageBox.warning(self, "错误", "音频播放器不可用（QtMultimedia 未安装）")
            return

        if not os.path.exists(filepath):
            QMessageBox.warning(self, "错误", f"文件不存在: {filepath}")
            return

        self._audio_current_path = filepath
        self._audio_player.setSource(QUrl.fromLocalFile(filepath))
        self._audio_player.play()
        self.audio_now_playing.setText(f"▶ {filename}")
        self.audio_play_btn.setText("⏸")
        self.audio_play_btn.setEnabled(True)
        self.audio_slider.setEnabled(True)
        logger.info(f"播放音频: {filename}")

    def _toggle_play(self):
        """播放/暂停切换"""
        if not self._audio_player:
            return
        if self._audio_player.playbackState() == QMediaPlayer.PlayingState:
            self._audio_player.pause()
        else:
            self._audio_player.play()

    def _on_audio_state_changed(self, state):
        """播放状态变化"""
        if state == QMediaPlayer.PlayingState:
            self.audio_play_btn.setText("⏸")
        elif state in (QMediaPlayer.PausedState, QMediaPlayer.StoppedState):
            self.audio_play_btn.setText("▶")

    def _update_audio_position(self, position):
        """更新播放进度"""
        duration = self._audio_player.duration() if self._audio_player else 0
        if duration > 0:
            self.audio_slider.setRange(0, duration)
            self.audio_slider.setValue(position)
        pos_str = self._format_duration(position)
        dur_str = self._format_duration(duration)
        self.audio_position_label.setText(f"{pos_str} / {dur_str}")

    def _update_audio_duration(self, duration):
        """总时长变化"""
        if duration > 0:
            self.audio_slider.setRange(0, duration)

    def _on_audio_slider_moved(self, position):
        """拖动进度条跳转"""
        if self._audio_player:
            self._audio_player.setPosition(position)

    def _set_audio_volume(self, volume):
        """设置音量"""
        if self._audio_output:
            self._audio_output.setVolume(volume / 100.0)

    def _show_audio_context_menu(self, position):
        """音频列表右键菜单"""
        item = self.audio_table.itemAt(position)
        if not item:
            return
        row = item.row()
        if row < 0 or row >= len(self._audio_files):
            return
        info = self._audio_files[row]
        filepath = info["path"]

        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{ background-color:{BG_SURFACE}; border:1px solid {BORDER}; border-radius:6px; padding:4px 0px; }}
            QMenu::item {{ padding:8px 24px; color:{TEXT_PRIMARY}; font-size:13px; border-radius:4px; }}
            QMenu::item:selected {{ background-color:{ACCENT}; color:#fff; }}
        """)

        act_open = menu.addAction("📂 打开文件所在目录")
        act_copy = menu.addAction("📋 复制文件")
        act_play = menu.addAction("🎵 播放")

        action = menu.exec(self.audio_table.mapToGlobal(position))
        if action == act_open:
            self._open_audio_file_location(filepath)
        elif action == act_copy:
            self._copy_audio_file(filepath)
        elif action == act_play:
            self._play_audio_file(filepath, info["name"])

    def _open_audio_file_location(self, file_path):
        """打开文件所在目录"""
        logger.info(f"打开文件所在目录: {file_path}")
        try:
            if sys.platform == "win32":
                subprocess.Popen(['explorer', '/select,', file_path],
                                 creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                folder = os.path.dirname(file_path)
                subprocess.Popen(['xdg-open', folder] if sys.platform.startswith('linux') else ['open', folder])
        except Exception as e:
            logger.error(f"打开目录失败: {e}")

    def _copy_audio_file(self, file_path):
        """复制文件到剪贴板"""
        if not os.path.exists(file_path):
            return
        try:
            from PySide6.QtGui import QGuiApplication
            clipboard = QGuiApplication.clipboard()
            url = QUrl.fromLocalFile(file_path)
            data = QMimeData()
            data.setUrls([url])
            clipboard.setMimeData(data)
            logger.info(f"已复制文件到剪贴板: {file_path}")
        except Exception as e:
            logger.error(f"复制文件失败: {e}")

    def _get_audio_duration(self, filepath):
        """获取音频时长（毫秒），失败返回 None"""
        try:
            if not QT_MULTIMEDIA_AVAILABLE:
                return None
            # 使用 QMediaPlayer 临时探测（非阻塞方式不可行，这里用文件大小估算）
            # 对于 WAV: duration ≈ filesize / (sample_rate * channels * bits_per_sample/8) * 1000
            # 简化：返回 None，让用户播放时显示实际时长
            return None
        except Exception:
            return None

    @staticmethod
    def _format_duration(ms):
        """毫秒转 mm:ss"""
        if not ms or ms <= 0:
            return "00:00"
        seconds = int(ms / 1000)
        m, s = divmod(seconds, 60)
        return f"{m:02d}:{s:02d}"

    @staticmethod
    def _format_size(size):
        """字节转可读大小"""
        if size < 1024:
            return f"{size} B"
        elif size < 1024 * 1024:
            return f"{size / 1024:.1f} KB"
        else:
            return f"{size / (1024 * 1024):.1f} MB"

    def _load_preview_images(self):
        """异步加载预览图片"""
        preview_dir = os.path.join(get_base_dir(), "output", "character")
        material_dir = os.path.join(DATA_DIR, "material")

        logger.info(f"开始加载预览图片: {preview_dir}")

        if not os.path.isdir(preview_dir):
            os.makedirs(preview_dir, exist_ok=True)
            logger.warning(f"预览目录不存在，已创建: {preview_dir}")

        # 预扫描 material 目录，建立 文件名→skel路径 映射
        self._skel_map = {}  # base_name -> (skel_path, atlas_path)
        if os.path.isdir(material_dir):
            for root, dirs, files in os.walk(material_dir):
                for f in files:
                    if f.endswith(".skel"):
                        base = os.path.splitext(f)[0]
                        skel_p = os.path.join(root, f)
                        atlas_p = os.path.join(root, f"{base}.atlas")
                        self._skel_map[base] = (skel_p, atlas_p)
            logger.debug(f"扫描到 {len(self._skel_map)} 个 .skel 文件用于路径匹配")

        self._clear_list()
        self._thumb_cache = {}

        if hasattr(self, "_image_worker") and self._image_worker is not None:
            self._image_worker.cancel()
            self._image_worker.wait(2000)

        self.preview_progress.setVisible(True)
        self.preview_progress.setValue(0)
        self.empty_label.setVisible(False)

        self._image_worker = ImageLoadWorker(preview_dir, 150)
        self._image_worker.progress.connect(self._on_load_progress)
        self._image_worker.image_loaded.connect(self._on_thumbnail_loaded)
        self._image_worker.finished_loading.connect(self._on_load_finished)
        self._image_worker.start()

    def _clear_list(self):
        """清空 QListWidget"""
        self.image_list.clear()
        self._image_paths = []

    def _on_load_progress(self, current, total):
        """更新加载进度"""
        self.preview_progress.setMaximum(total)
        self.preview_progress.setValue(current)
        self.preview_progress.setFormat(f"加载中... {current}/{total}")
        self.preview_title.setText(f"🖼️ 角色预览器  共 {total} 张图片  (加载中 {current}/{total})")

    def _on_thumbnail_loaded(self, image_path, thumbnail):
        """添加缩略图到 QListWidget，同时存储 skel/atlas 路径"""
        self._thumb_cache[image_path] = thumbnail
        self._image_paths.append(image_path)

        # 从 PNG 文件名反查 skel/atlas 路径
        skel_path, atlas_path = self._find_skel_paths(image_path)

        item = QListWidgetItem(QPixmap(thumbnail), "")
        item.setData(Qt.UserRole, {
            "png": image_path,
            "skel": skel_path,
            "atlas": atlas_path,
        })

        fname = os.path.basename(image_path)
        display_name = fname if len(fname) <= 22 else fname[:19] + "..."
        item.setText(display_name)
        item.setToolTip(fname)

        self.image_list.addItem(item)

    def _find_skel_paths(self, png_path):
        """从 PNG 文件名反查对应的 .skel 和 .atlas 路径
        PNG 命名规则:
          - {base}.png              -> skel: {base}.skel
          - {base}_{anim}.png       -> skel: {base}.skel
          - {base}_bg.png           -> skel: {base}_bg.skel
          - {base}_composite.png    -> skel: {base}.skel (角色)
        """
        fname = os.path.splitext(os.path.basename(png_path))[0]

        # 处理 composite（合成图对应角色的 skel）
        if fname.endswith("_composite"):
            base = fname[:-len("_composite")]
        # 处理 _bg 后缀
        elif fname.endswith("_bg"):
            base = fname
        else:
            # 尝试去掉可能的动画后缀
            # 先直接匹配
            if fname in self._skel_map:
                base = fname
            else:
                # 尝试查找 {base}_{anim} 模式
                found_base = None
                for known_base in self._skel_map:
                    if fname.startswith(known_base + "_"):
                        found_base = known_base
                        break
                base = found_base if found_base else fname

        if base in self._skel_map:
            return self._skel_map[base]

        return None, None

    def _on_load_finished(self, loaded_paths):
        """加载完成回调"""
        self.preview_progress.setVisible(False)
        count = len(loaded_paths)
        self._image_paths = loaded_paths
        self.preview_title.setText(f"🖼️ 角色预览器  共 {count} 张图片")

        if count == 0:
            self.empty_label.setVisible(True)
            self.preview_status.setText("共 0 张图片")
            logger.warning("预览目录为空")
        else:
            self.empty_label.setVisible(False)
            self.preview_status.setText(f"共 {count} 张图片")

        logger.info(f"加载完成，共 {count} 张图片")
        self.status_bar.showMessage(f"图片预览: 共 {count} 张图片")

    @staticmethod
    def _extract_skin_name_from_png(png_path):
        """从 PNG 文件名提取皮肤名（如 motion_angry），无匹配时返回 None"""
        fname = os.path.splitext(os.path.basename(png_path))[0]
        match = re.search(r'(motion_[a-zA-Z0-9_]+)', fname)
        return match.group(1) if match else None

    def _show_context_menu(self, position):
        """显示右键菜单（单选/多选自适应），支持仅有 PNG 无 skel/atlas 的项"""
        # 如果右键的项未选中，先清空选择并选中该项
        item_at_pos = self.image_list.itemAt(position)
        if item_at_pos and not item_at_pos.isSelected():
            self.image_list.clearSelection()
            item_at_pos.setSelected(True)

        selected_items = self.image_list.selectedItems()
        if not selected_items:
            return

        # 收集所有选中项的 skel/atlas/png 数据
        entries = []
        png_only_entries = []
        for item in selected_items:
            data = item.data(Qt.UserRole)
            if not data:
                continue
            png_path = data.get("png", "")
            if not png_path or not os.path.exists(png_path):
                continue
            if data.get("skel") and data.get("atlas"):
                entries.append((data["skel"], data["atlas"], png_path))
            else:
                png_only_entries.append(png_path)

        has_skel = len(entries) > 0
        has_png = len(png_only_entries) > 0

        if not has_skel and not has_png:
            return

        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{ background-color:{BG_SURFACE}; border:1px solid {BORDER}; border-radius:6px;
                     padding:4px 0px; }}
            QMenu::item {{ padding:8px 24px; color:{TEXT_PRIMARY}; font-size:13px; border-radius:4px; }}
            QMenu::item:selected {{ background-color:{ACCENT}; color:#fff; }}
            QMenu::separator {{ height:1px; background-color:{BORDER}; margin:4px 8px; }}
        """)

        is_multi = len(selected_items) > 1

        if is_multi:
            # 多选
            if has_skel:
                act_batch_gif = menu.addAction(f"🎞️ 批量导出 GIF ({len(entries)} 个)")
                act_batch_video = menu.addAction(f"🎬 批量导出视频 ({len(entries)} 个)")
                if has_png:
                    menu.addSeparator()
            if has_png:
                act_open = menu.addAction("📂 打开文件所在目录")
                act_copy = menu.addAction("📋 复制文件")

            action = menu.exec(self.image_list.mapToGlobal(position))
            if has_skel and (action == act_batch_gif or action == act_batch_video):
                pass  # 需要在下面判断
            # 判断具体点击的 action
            if has_skel and action == act_batch_gif:
                self._batch_export_with_dialog(entries, "GIF")
            elif has_skel and action == act_batch_video:
                self._batch_export_with_dialog(entries, "MP4")
            elif has_png and action == act_open:
                png_path = png_only_entries[0] if png_only_entries else entries[0][2]
                self._open_file_location(png_path)
            elif has_png and action == act_copy:
                png_path = png_only_entries[0] if png_only_entries else entries[0][2]
                self._copy_file_to_clipboard(png_path)
        else:
            # 单选
            data = selected_items[0].data(Qt.UserRole)
            png_path = data.get("png", "")

            act_open = menu.addAction("📂 打开文件所在目录")
            act_copy = menu.addAction("📋 复制文件")

            if has_skel:
                menu.addSeparator()
                skel_path, atlas_path, _ = entries[0]
                is_composite = self._is_composite_png(png_path)
                if is_composite:
                    act_export_gif = menu.addAction("🎞️ 合成 GIF 预览")
                    act_export_video = menu.addAction("🎬 合成视频")
                else:
                    act_export_gif = menu.addAction("🎞️ 导出 GIF 预览")
                    act_export_video = menu.addAction("🎬 导出为视频")

            action = menu.exec(self.image_list.mapToGlobal(position))
            if action == act_open:
                self._open_file_location(png_path)
            elif action == act_copy:
                self._copy_file_to_clipboard(png_path)
            elif has_skel and action == act_export_gif:
                skin_name = self._extract_skin_name_from_png(png_path)
                if is_composite:
                    self._export_composite_video(png_path, "GIF", skin_name=skin_name)
                else:
                    self._export_with_dialog(skel_path, atlas_path, "GIF", skin_name=skin_name)
            elif has_skel and action == act_export_video:
                skin_name = self._extract_skin_name_from_png(png_path)
                if is_composite:
                    self._export_composite_video(png_path, "MP4", skin_name=skin_name)
                else:
                    self._export_with_dialog(skel_path, atlas_path, "MP4", skin_name=skin_name)

    def _on_item_clicked(self, item):
        """左键点击缩略图 → 不做任何操作（仅选中）"""
        pass

    def _on_item_double_clicked(self, item):
        """双击缩略图 → 打开独立预览窗口"""
        data = item.data(Qt.UserRole)
        if not data:
            return
        png_path = data.get("png", "")
        if not png_path or not os.path.exists(png_path):
            return

        # 获取 output/character/ 目录下所有 PNG 文件列表，用于上下导航
        output_dir = os.path.dirname(png_path)
        all_pngs = []
        current_index = 0
        if os.path.isdir(output_dir):
            for fname in sorted(os.listdir(output_dir)):
                if fname.lower().endswith(".png"):
                    full_path = os.path.join(output_dir, fname)
                    all_pngs.append(full_path)
                    if os.path.normpath(full_path) == os.path.normpath(png_path):
                        current_index = len(all_pngs) - 1

        if not all_pngs:
            all_pngs = [png_path]
            current_index = 0

        logger.debug(f"双击预览: {png_path}, 索引 {current_index}/{len(all_pngs)}")
        dialog = ImageViewerDialog(all_pngs, current_index, self)
        dialog.exec()

    def _open_file_location(self, file_path):
        """打开文件所在目录并选中该文件"""
        logger.info(f"打开文件所在目录: {file_path}")
        try:
            subprocess.Popen(
                ['explorer', '/select,', os.path.normpath(file_path)],
                shell=True
            )
        except Exception as e:
            logger.error(f"打开文件位置失败: {e}")
            QMessageBox.warning(self, "错误", f"打开文件位置失败:\n{e}")

    def _copy_file_to_clipboard(self, file_path):
        """复制文件到系统剪贴板"""
        logger.info(f"复制文件: {file_path}")
        try:
            mime_data = QMimeData()
            mime_data.setUrls([QUrl.fromLocalFile(os.path.abspath(file_path))])
            QApplication.clipboard().setMimeData(mime_data)
            self.status_bar.showMessage(f"已复制文件: {os.path.basename(file_path)}")
        except Exception as e:
            logger.error(f"复制文件失败: {e}")
            QMessageBox.warning(self, "错误", f"复制文件失败:\n{e}")

    @staticmethod
    def _is_composite_png(png_path):
        """判断是否为合成图（文件名含 _composite）"""
        fname = os.path.splitext(os.path.basename(png_path))[0]
        return fname.endswith("_composite")

    @staticmethod
    def _find_composite_sources(png_path, skel_map):
        """从合成图路径解析角色和背景的 .skel/.atlas 路径
        返回 (role_skel, role_atlas, bg_skel, bg_atlas) 或 (None, None, None, None)"""
        fname = os.path.splitext(os.path.basename(png_path))[0]
        if not fname.endswith("_composite"):
            return None, None, None, None

        base = fname[:-len("_composite")]
        # 查找角色
        role_entry = skel_map.get(base)
        if not role_entry:
            logger.warning(f"合成图解析: 未找到角色 skel: {base}.skel")
            return None, None, None, None

        # 查找背景
        bg_entry = skel_map.get(f"{base}_bg")
        if not bg_entry:
            logger.warning(f"合成图解析: 未找到背景 skel: {base}_bg.skel")
            return None, None, None, None

        return role_entry[0], role_entry[1], bg_entry[0], bg_entry[1]

    def _export_composite_video(self, png_path, default_format="MP4", skin_name=None):
        """导出合成图视频（单文件，弹窗选择参数）"""
        role_skel, role_atlas, bg_skel, bg_atlas = MainWindow._find_composite_sources(png_path, self._skel_map)

        if not role_skel or not bg_skel:
            QMessageBox.warning(self, "错误",
                "缺少角色或背景骨骼数据，无法合成视频\n"
                f"文件: {os.path.basename(png_path)}")
            return

        if not os.path.exists(role_skel):
            QMessageBox.warning(self, "错误", f"角色 .skel 不存在: {role_skel}")
            return
        if not os.path.exists(bg_skel):
            QMessageBox.warning(self, "错误", f"背景 .skel 不存在: {bg_skel}")
            return

        spine_cli = os.path.join(get_tools_dir(), "SpineViewer", "SpineViewerCLI.exe")

        if not os.path.exists(spine_cli):
            QMessageBox.warning(self, "错误",
                "SpineViewerCLI.exe 未找到，请确认 tools/SpineViewer/ 目录完整")
            return

        # 获取对话框设置（合成图默认 MP4）
        dialog = ExportSettingsDialog(role_skel, role_atlas, default_format, self)
        if dialog.exec() != QDialog.Accepted:
            return

        settings = dialog.get_settings()
        base_name = os.path.splitext(os.path.basename(png_path))[0]
        if base_name.endswith("_composite"):
            base_name = base_name[:-len("_composite")]

        self.status_bar.showMessage(f"正在导出合成视频... {base_name}")
        QApplication.processEvents()

        success = self._export_composite_video_with_params(png_path, settings, skin_name=skin_name)

        if success:
            if settings["auto_open"]:
                # 找到输出文件并打开
                ext = ".mp4" if settings["format"] == "mp4" else ".gif"
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                output_dir = os.path.join(get_base_dir(), "output",
                                           "video" if settings["format"] == "mp4" else "character")
                output_path = os.path.join(output_dir, f"{base_name}_composite_{timestamp}{ext}")
                if os.path.exists(output_path):
                    if sys.platform == "win32":
                        os.startfile(output_path)
                    else:
                        subprocess.Popen(
                            ['xdg-open', output_path]
                            if sys.platform.startswith('linux')
                            else ['open', output_path]
                        )
        else:
            self.status_bar.showMessage("导出失败")
            QMessageBox.warning(self, "导出失败", "合成视频导出失败")

    def _export_composite_video_with_params(self, png_path, settings, skin_name=None):
        """使用预设参数导出合成图视频（不弹窗，用于批量导出）。返回 bool 表示成功与否。"""
        role_skel, role_atlas, bg_skel, bg_atlas = MainWindow._find_composite_sources(png_path, self._skel_map)
        if not role_skel or not bg_skel:
            logger.warning(f"批量合成导出: 缺少角色或背景骨骼数据: {png_path}")
            return False

        spine_cli = os.path.join(get_tools_dir(), "SpineViewer", "SpineViewerCLI.exe")
        if not os.path.exists(spine_cli):
            logger.error(f"SpineViewerCLI 不存在: {spine_cli}")
            return False

        fmt = settings["format"]
        animation = settings["animation"]
        duration = settings["duration"]
        fps = settings["fps"]
        scale = settings["scale"]
        pma = settings.get("pma", False)

        ext = ".mp4" if fmt == "mp4" else ".gif"
        base_name = os.path.splitext(os.path.basename(png_path))[0]
        if base_name.endswith("_composite"):
            base_name = base_name[:-len("_composite")]
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = os.path.join(get_base_dir(), "output",
                                   "video" if fmt == "mp4" else "character")
        os.makedirs(output_dir, exist_ok=True)

        # 唯一临时目录
        temp_dir = os.path.join(get_base_dir(), "output", "temp",
                                f"composite_{base_name}_{datetime.now().strftime('%H%M%S_%f')}")
        os.makedirs(temp_dir, exist_ok=True)

        role_temp_path = os.path.join(temp_dir, f"role_temp{ext}")
        bg_temp_path = os.path.join(temp_dir, f"bg_temp{ext}")
        output_path = os.path.join(output_dir, f"{base_name}_composite_{timestamp}{ext}")

        logger.info(f"批量合成视频导出: {base_name}")
        logger.info(f"参数: 格式={fmt}, 时长={duration}s, 帧率={fps}fps, 缩放={scale}x, 预乘={pma}, 皮肤={skin_name or '无'}")

        try:
            # 步骤 1: 导出角色视频（应用皮肤）
            if not MainWindow._export_spine_media_file(
                spine_cli, role_skel, role_atlas, role_temp_path,
                animation, duration, fps, scale, fmt,
                label="角色", pma=pma, skin_name=skin_name
            ):
                logger.error(f"批量合成: 角色视频导出失败: {base_name}")
                return False

            # 步骤 2: 导出背景视频
            if not MainWindow._export_spine_media_file(
                spine_cli, bg_skel, bg_atlas, bg_temp_path,
                animation, duration, fps, scale, fmt,
                label="背景", pma=pma
            ):
                logger.error(f"批量合成: 背景视频导出失败: {base_name}")
                return False

            # 步骤 3: FFmpeg 叠加合成
            if not MainWindow._ffmpeg_composite_videos(
                bg_temp_path, role_temp_path, output_path,
                fps, fmt
            ):
                logger.error(f"批量合成: FFmpeg 叠加失败: {base_name}")
                return False

            if os.path.exists(output_path):
                size = os.path.getsize(output_path)
                logger.info(f"批量合成视频导出完成: {output_path} (大小: {size} bytes)")
                return True
            else:
                logger.error(f"批量合成: 输出文件未生成: {base_name}")
                return False

        except Exception as e:
            logger.error(f"批量合成视频导出异常 [{base_name}]: {e}")
            return False
        finally:
            time.sleep(0.5)
            MainWindow._cleanup_temp(temp_dir)

    @staticmethod
    def _export_spine_media_file(spine_cli, skel_path, atlas_path,
                                  output_path, animation, duration, fps, scale,
                                  fmt="mp4", label="", pma=False, skin_name=None):
        """使用 SpineViewerCLI 直接导出 MP4 或 GIF 文件（带重试）"""
        media_fmt = "Mp4" if fmt == "mp4" else "Gif"
        max_attempts = 2
        last_error = ""

        for attempt in range(1, max_attempts + 1):
            logger.info(f"{label}视频导出尝试 {attempt}/{max_attempts}: {os.path.basename(skel_path)}")

            # 确保输出目录存在
            out_dir = os.path.dirname(output_path)
            if out_dir:
                os.makedirs(out_dir, exist_ok=True)

            # 清理可能残留的同名输出文件
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

                # 收集错误
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

    @staticmethod
    def _get_ffmpeg_path():
        """获取 FFmpeg 可执行文件路径

        优先使用 tools/SpineViewer/ffmpeg.exe，若不存在则回退到系统 PATH。
        """
        local_ffmpeg = os.path.join(get_tools_dir(), "SpineViewer", "ffmpeg.exe")
        if os.path.exists(local_ffmpeg):
            logger.debug(f"使用本地 FFmpeg: {local_ffmpeg}")
            return local_ffmpeg
        logger.debug("使用系统 PATH 中的 FFmpeg")
        return "ffmpeg"

    @staticmethod
    def _ffmpeg_composite_videos(bg_path, role_path, output_path, fps, fmt="mp4"):
        """使用 FFmpeg filter_complex 将角色视频叠加到背景视频上"""
        out_dir = os.path.dirname(output_path)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)

        # 清理可能残留的同名输出文件
        if os.path.exists(output_path):
            try:
                os.remove(output_path)
            except (PermissionError, OSError) as e:
                logger.warning(f"清理旧合成文件失败 {output_path}: {e}")

        ffmpeg_path = MainWindow._get_ffmpeg_path()

        if fmt == "mp4":
            # MP4 叠加：colorkey 去除角色黑色背景，叠加到背景视频
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
            # GIF 叠加：colorkey 去黑底 + palettegen/paletteuse 保持透明度
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

    @staticmethod
    def _cleanup_temp(temp_dir):
        """清理临时目录（带重试 + 分阶段删除）

        删除策略：
          1) 先尝试清空目录中的所有文件（递归），留给子目录删除更干净的状态；
          2) 使用重试循环删除目录本身，应对 Windows 文件句柄延迟释放；
          3) 最终回退使用 ignore_errors，保证资源尽可能被回收。
        """
        if not os.path.exists(temp_dir):
            return

        # 阶段 1：先尝试删除目录内的所有文件，仅保留空目录结构
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

        # 阶段 2：重试删除目录
        for attempt in range(3):
            if not os.path.exists(temp_dir):
                break
            try:
                import gc
                gc.collect()
                shutil.rmtree(temp_dir, ignore_errors=False)
                logger.debug(f"已清理临时目录: {temp_dir}")
                return
            except PermissionError as e:
                logger.warning(f"清理临时目录失败 (尝试 {attempt+1}/3): {e}")
                time.sleep(0.5)
            except FileNotFoundError:
                # 其他线程/进程已清理
                return
            except Exception as e:
                logger.warning(f"清理临时目录异常: {e}")
                break

        # 阶段 3：回退，尽可能删除剩余内容
        if os.path.exists(temp_dir):
            try:
                shutil.rmtree(temp_dir, ignore_errors=True)
                logger.debug(f"已清理临时目录（回退）: {temp_dir}")
            except Exception as e:
                logger.error(f"清理临时目录最终失败: {e}")

    def _export_with_dialog(self, skel_path, atlas_path, default_format="MP4", skin_name=None):
        """弹出导出设置对话框，按用户选择的参数导出"""
        # 验证文件
        if not skel_path or not os.path.exists(skel_path):
            logger.warning(f"无法导出，缺少 .skel 文件: {skel_path}")
            QMessageBox.warning(self, "错误", "无法导出，.skel 文件不存在")
            return

        if not atlas_path or not os.path.exists(atlas_path):
            logger.warning(f"无法导出，缺少 .atlas 文件: {atlas_path}")
            QMessageBox.warning(self, "错误", "无法导出，缺少对应的 .atlas 文件")
            return

        spine_cli = os.path.join(get_tools_dir(), "SpineViewer", "SpineViewerCLI.exe")

        if not os.path.exists(spine_cli):
            logger.error(f"SpineViewerCLI 不存在: {spine_cli}")
            QMessageBox.warning(self, "错误",
                "SpineViewerCLI.exe 未找到，请确认 tools/SpineViewer/ 目录完整")
            return

        # 弹出设置对话框
        dialog = ExportSettingsDialog(skel_path, atlas_path, default_format, self)
        if dialog.exec() != QDialog.Accepted:
            return

        settings = dialog.get_settings()
        fmt = settings["format"]
        animation = settings["animation"]
        duration = settings["duration"]
        fps = settings["fps"]
        scale = settings["scale"]
        pma = settings["pma"]
        auto_open = settings["auto_open"]

        # 确定输出路径
        ext = ".mp4" if fmt == "mp4" else ".gif"
        skel_base = os.path.splitext(os.path.basename(skel_path))[0]
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = os.path.join(get_base_dir(), "output", "video" if fmt == "mp4" else "character")
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, f"{skel_base}_{timestamp}{ext}")

        logger.info(f"导出设置: 格式={fmt}, 时长={duration}s, 帧率={fps}fps, 缩放={scale}x, 预乘={pma}, 皮肤={skin_name or '无'}")
        self.status_bar.showMessage(f"正在导出 {fmt.upper()}... {skel_base}")
        QApplication.processEvents()

        try:
            cmd = [
                spine_cli, "export", skel_path,
                "-f", "Mp4" if fmt == "mp4" else "Gif",
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

            logger.debug(f"执行命令: {' '.join(cmd)}")
            proc = subprocess.run(
                cmd,
                cwd=os.path.dirname(spine_cli),
                capture_output=True,
                text=True,
                timeout=60,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )

            if proc.stderr:
                logger.debug(f"SpineViewerCLI stderr: {proc.stderr[:300]}")

            if proc.returncode == 0 and os.path.exists(output_path):
                size = os.path.getsize(output_path)
                logger.info(f"导出完成: {output_path} (大小: {size} bytes)")
                self.status_bar.showMessage(f"已导出: {os.path.basename(output_path)}")

                if auto_open:
                    if sys.platform == "win32":
                        os.startfile(output_path)
                    else:
                        subprocess.Popen(['xdg-open', output_path] if sys.platform.startswith('linux') else ['open', output_path])
            else:
                error_msg = proc.stderr[:300] if proc.stderr else f"退出码: {proc.returncode}"
                logger.error(f"导出失败: {error_msg}")
                self.status_bar.showMessage("导出失败")
                QMessageBox.warning(self, "导出失败",
                    f"{fmt.upper()} 导出失败:\n{error_msg}")
        except subprocess.TimeoutExpired:
            logger.error(f"导出超时: {skel_path}")
            self.status_bar.showMessage("导出失败")
            QMessageBox.warning(self, "错误", "导出超时（超过60秒）")
        except Exception as e:
            logger.error(f"导出异常: {e}")
            self.status_bar.showMessage("导出失败")
            QMessageBox.warning(self, "错误", f"导出异常:\n{e}")

    def _batch_export_with_dialog(self, entries_with_png, default_format="MP4"):
        """批量导出：单次弹窗，合成图后台线程 + 普通文件后台线程"""
        # 分类：合成图 vs 普通文件
        regular_entries = []   # [(skel, atlas, skin_name), ...]
        composite_pngs = []    # [png_path, ...]

        for entry in entries_with_png:
            skel_path = entry[0]
            atlas_path = entry[1]
            png_path = entry[2] if len(entry) > 2 else ""

            if MainWindow._is_composite_png(png_path):
                composite_pngs.append(png_path)
                logger.info(f"批量导出合成图: {png_path}")
            else:
                if os.path.exists(skel_path) and atlas_path and os.path.exists(atlas_path):
                    skin = MainWindow._extract_skin_name_from_png(png_path)
                    regular_entries.append((skel_path, atlas_path, skin))
                    logger.info(f"批量导出普通文件: {skel_path} (皮肤: {skin or '无'})")
                else:
                    logger.warning(f"批量导出: 跳过无效文件: {skel_path}")

        total_all = len(regular_entries) + len(composite_pngs)
        if total_all == 0:
            QMessageBox.warning(self, "错误", "没有可导出的有效文件")
            return

        spine_cli = os.path.join(get_tools_dir(), "SpineViewer", "SpineViewerCLI.exe")
        if not os.path.exists(spine_cli):
            logger.error(f"SpineViewerCLI 不存在: {spine_cli}")
            QMessageBox.warning(self, "错误",
                "SpineViewerCLI.exe 未找到，请确认 tools/SpineViewer/ 目录完整")
            return

        # 单次弹出设置对话框（用第一个有效条目初始化）
        if regular_entries:
            first_skel, first_atlas = regular_entries[0][0], regular_entries[0][1]
        else:
            # 全是合成图：用第一个合成图的角色 skel 初始化
            role_skel, role_atlas, _, _ = MainWindow._find_composite_sources(composite_pngs[0], self._skel_map)
            first_skel = role_skel or ""
            first_atlas = role_atlas or ""

        dialog = ExportSettingsDialog(first_skel, first_atlas, default_format, self)
        if dialog.exec() != QDialog.Accepted:
            return

        settings = dialog.get_settings()
        auto_open = settings["auto_open"]

        # 确认对话框
        fmt_label = "MP4 视频" if settings["format"] == "mp4" else "GIF 动画"
        ret = QMessageBox.question(
            self, "批量导出确认",
            f"即将批量导出 {total_all} 个文件为 {fmt_label}\n"
            f"（普通: {len(regular_entries)}，合成图: {len(composite_pngs)}）\n"
            f"参数: {settings['duration']}秒 / {settings['fps']}fps / {settings['scale']}x\n"
            f"\n是否继续?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes
        )
        if ret != QMessageBox.Yes:
            return

        # 禁用相关按钮，防止重复操作
        self.btn_reload.setEnabled(False)

        # 保存批量导出上下文（用于合成图 + 普通文件结果合并）
        self._batch_settings = settings
        self._batch_auto_open = auto_open
        self._batch_regular_entries = regular_entries
        self._batch_spine_cli = spine_cli
        self._batch_project_root = get_base_dir()
        self._batch_comp_success = 0
        self._batch_comp_fail = 0

        # 1. 启动合成图导出线程（如有）
        if composite_pngs:
            self._composite_worker = CompositeExportWorker(
                composite_pngs, settings, spine_cli, self._skel_map, get_base_dir(), self
            )
            self._composite_worker.progress.connect(self._on_composite_progress)
            self._composite_worker.one_finished.connect(self._on_batch_one_finished)
            self._composite_worker.all_finished.connect(self._on_composite_all_finished)
            self._composite_worker.start()
        else:
            # 无合成图，直接处理普通文件
            self._start_regular_batch_export()

    def _on_composite_progress(self, current, total, filename):
        """合成图批量导出进度"""
        self.status_bar.showMessage(f"合成图导出中 [{current}/{total}]: {filename}")

    def _on_composite_all_finished(self, success_count, fail_count):
        """合成图批量导出全部完成"""
        self._batch_comp_success = success_count
        self._batch_comp_fail = fail_count
        logger.info(f"合成图批量导出完成: 成功 {success_count}, 失败 {fail_count}")
        # 继续处理普通文件（如有）
        self._start_regular_batch_export()

    def _start_regular_batch_export(self):
        """启动普通文件批量导出线程"""
        regular_entries = getattr(self, "_batch_regular_entries", [])
        if regular_entries:
            spine_cli = self._batch_spine_cli
            settings = self._batch_settings
            self._batch_worker = BatchExportWorker(
                regular_entries, settings, spine_cli, get_base_dir(), self
            )
            self._batch_worker.progress.connect(self._on_batch_progress)
            self._batch_worker.one_finished.connect(self._on_batch_one_finished)
            self._batch_worker.all_finished.connect(self._on_regular_all_finished)
            self._batch_worker.start()
        else:
            # 无普通文件，直接显示合成图结果
            self.btn_reload.setEnabled(True)
            self._on_batch_all_finished(
                self._batch_comp_success, self._batch_comp_fail, self._batch_auto_open
            )

    def _on_regular_all_finished(self, success_count, fail_count):
        """普通文件批量导出全部完成，合并结果"""
        total_success = success_count + self._batch_comp_success
        total_fail = fail_count + self._batch_comp_fail
        self.btn_reload.setEnabled(True)
        self._on_batch_all_finished(total_success, total_fail, self._batch_auto_open)

    def _on_batch_progress(self, current, total, filename):
        """批量导出进度"""
        self.status_bar.showMessage(
            f"批量导出中 [{current}/{total}]: {filename}"
        )
        QApplication.processEvents()

    def _on_batch_one_finished(self, path, success):
        """单个文件导出完成"""
        if success:
            logger.info(f"批量导出成功: {os.path.basename(path)}")
        else:
            logger.warning(f"批量导出失败: {os.path.basename(path)}")

    def _on_batch_all_finished(self, success_count, fail_count, auto_open):
        """批量导出全部完成"""
        self.btn_reload.setEnabled(True)
        total = success_count + fail_count
        settings = getattr(self, "_batch_settings", {})
        fmt = "视频" if settings.get("format") == "mp4" else "GIF"
        self.status_bar.showMessage(
            f"批量导出完成: 成功 {success_count} 个，失败 {fail_count} 个"
        )

        if success_count > 0 and auto_open:
            # 打开输出目录
            output_dir = os.path.join(get_base_dir(), "output",
                                      "video" if fmt == "视频" else "character")
            if os.path.exists(output_dir):
                if sys.platform == "win32":
                    os.startfile(output_dir)

        QMessageBox.information(
            self, "批量导出完成",
            f"共处理 {total} 个文件\n"
            f"✅ 成功: {success_count} 个\n"
            f"❌ 失败: {fail_count} 个"
        )

    # ========== DELETE ==========

    def _delete_version(self, ts):
        sub = db.get_sub_bundles(ts); down = sum(1 for r in sub if r[2]) if sub else 0
        if down == 0: QMessageBox.information(self, "无文件", "没有已下载的 bundle."); return
        rp = QMessageBox.question(self, "确认删除", f"删除此版本 {down} 个文件?", QMessageBox.Yes|QMessageBox.No, QMessageBox.No)
        if rp != QMessageBox.Yes: return
        c = db.get_conn()
        for r in sub:
            if r[2]:
                try: os.remove(r[2])
                except: pass
                c.execute("UPDATE sub_bundles SET local_path=NULL, downloadable=0 WHERE hash=? AND version_timestamp=?",(r[0],ts))
        c.commit(); c.close()
        self._load_data()
        self.status_bar.showMessage(f"已删除 {down} 个文件.")
        QMessageBox.information(self, "完成", f"已删除 {down} 个文件.")

    # ========== SEED ==========

    def _seed_bundled_version(self):
        existing = {r[0] for r in db.get_all_versions()}
        ctx = ssl.create_default_context()
        CDN_DIR = os.path.join(BUNDLES_DIR, "seeds")
        HD = {"User-Agent": "UnityPlayer/2021.3.45f2c1 (UnityWebRequest/1.0, libcurl/8.5.0-DEV)","X-Unity-Version": "2021.3.45f2c1"}
        def dcat(n,h):
            fp=os.path.join(CDN_DIR,f"SEED_{n}_{h[:12]}.json")
            if not os.path.exists(fp):
                os.makedirs(CDN_DIR,exist_ok=True)
                d=urllib.request.urlopen(urllib.request.Request(f"https://elpis.17995cdn.com/Android/Bundles/{n.lower()}_{h}.json",headers=HD),context=ctx,timeout=15).read()
                with open(fp,"wb")as f:f.write(d)
            return fp
        def seed(ts,info,vd,notes,cats,ic=False):
            if ts in existing:return
            self.version_mgr.register_version(ts,info,vd,is_current=ic)
            db.add_notes(ts,notes)
            ah=set()
            for n,h in cats:
                try: ah|=extract_manifest_hashes(dcat(n,h))
                except: pass
            if ah: db.save_sub_bundles(ts,list(ah))
        seed(134091181056097516,{"timestamp":134091181056097516,"file":"versions_vA137.D342.O142.V6_134091181056097516.json","hash":"","size":0,"downloadURL":"https://elpis.17995cdn.com/Android/Bundles","playerURL":"https://xl.haoplay.com.cn/dl","latestVersion":"1.0","minVersion":"1.0"},{"timestamp":134091181056097516,"data":[{"name":"Arts","hash":"80d80e718768bdd7b35f5b9406d624ed","size":781708,"ver":137},{"name":"Data","hash":"48002d6e908b3fce2c4b61d8c34b9393","size":158685,"ver":342},{"name":"Other","hash":"fdf0f3b9cad9498a25f542c4c9ce0f8b","size":129635,"ver":142},{"name":"Video","hash":"a7fd12e3f78b95bcfb6c63be49247b5f","size":2738,"ver":6}]},"APK内置版本, 2025年12月",[("Arts","80d80e718768bdd7b35f5b9406d624ed"),("Data","48002d6e908b3fce2c4b61d8c34b9393"),("Other","fdf0f3b9cad9498a25f542c4c9ce0f8b")])
        seed(134239138473475084,{"timestamp":134239138473475084,"file":"versions_vA145.D378.O152.V6_134239138473475084.json","hash":"","size":0,"downloadURL":"https://elpis.17995cdn.com/Android/Bundles","playerURL":"https://xl.haoplay.com.cn/dl","latestVersion":"1.1","minVersion":"1.1"},{"timestamp":134239138473475084,"data":[{"name":"Arts","hash":"a1c72aae7f79b72bc763e63803362d01","size":836280,"ver":145},{"name":"Data","hash":"7bbfa67db42e024cb7124dddb8b91d2b","size":180385,"ver":378},{"name":"Other","hash":"d3632702f53de4ba0457b5e518aaf0f3","size":139791,"ver":152},{"name":"Video","hash":"01fe1717b1979689c37f26f6312ea23b","size":2738,"ver":6}]},"完整版本, 2026年5月26日",[("Arts","a1c72aae7f79b72bc763e63803362d01"),("Data","7bbfa67db42e024cb7124dddb8b91d2b"),("Other","d3632702f53de4ba0457b5e518aaf0f3")])
        seed(134272123703055311,{"timestamp":134272123703055311,"file":"versions_vA152.D386.O156.V6_134272123703055311.json","hash":"bb7d22dab4acab771f336ce53f6af261","size":367,"downloadURL":"https://elpis.17995cdn.com/Android/Bundles","playerURL":"https://xl.haoplay.com.cn/dl","latestVersion":"1.2","minVersion":"1.2"},{"timestamp":134272123703055311,"data":[{"name":"Arts","hash":"30ed8244781b44cacc3c5d1ea10a976e","size":844382,"ver":152},{"name":"Data","hash":"5d2c794364dfe22100547764f60689fe","size":185983,"ver":386},{"name":"Other","hash":"dd50845a464e1eda86b027103d2cce43","size":146765,"ver":156},{"name":"Video","hash":"ac0fb4b1842c88408eee708a5f394a8b","size":2744,"ver":6}]},"在线版本, 2026年6月29日",[("Arts","30ed8244781b44cacc3c5d1ea10a976e"),("Data","5d2c794364dfe22100547764f60689fe"),("Other","dd50845a464e1eda86b027103d2cce43")])

    def _check_auto(self):
        cur = self.version_mgr.get_current()
        if not cur:
            self.status_bar.showMessage("首次启动, 自动检查更新...")
            QTimer.singleShot(1500, self._check_update)
        else:
            QTimer.singleShot(500, lambda: self.status_bar.showMessage("就绪"))
