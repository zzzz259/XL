from PySide6.QtCore import QAbstractTableModel, Qt, QModelIndex
from app.core import database as db


class BundleTableModel(QAbstractTableModel):
    HEADERS = ["资源包", "哈希值", "大小", "版本", "本地"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows = []
        self._version_ts = None
        self._total = 0
        self._loaded = 0
        self._batch = 200

    def set_version(self, timestamp):
        self.beginResetModel()
        self._version_ts = timestamp
        self._rows = []
        self._total = db.get_bundle_count(timestamp)
        self._loaded = 0
        self.endResetModel()
        self._load_more()

    def _load_more(self):
        if self._version_ts is None:
            return
        new = db.get_bundles_for_version(
            self._version_ts, limit=self._batch, offset=self._loaded
        )
        if not new:
            return
        start = self._loaded
        self.beginInsertRows(QModelIndex(), start, start + len(new) - 1)
        self._rows.extend(new)
        self._loaded = len(self._rows)
        self.endInsertRows()

    def canFetchMore(self, parent=QModelIndex()):
        return self._loaded < self._total

    def fetchMore(self, parent=QModelIndex()):
        self._load_more()

    def rowCount(self, parent=QModelIndex()):
        return len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        return len(self.HEADERS)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        row = self._rows[index.row()]
        col = index.column()
        if role == Qt.DisplayRole:
            if col == 0:
                return row[0]
            if col == 1:
                return row[1][:12] + "..."
            if col == 2:
                return _format_size(row[2])
            if col == 3:
                return str(row[3])
            if col == 4:
                return "是" if row[4] else "否"
        if role == Qt.UserRole:
            if col == 1:
                return row[1]
            if col == 2:
                return row[2]
            if col == 4:
                return row[4]
        if role == Qt.TextAlignmentRole:
            if col >= 2:
                return Qt.AlignCenter
        if role == Qt.ForegroundRole and col == 4:
            from .theme import SUCCESS, TEXT_MUTED
            return SUCCESS if row[4] else TEXT_MUTED
        if role == Qt.ToolTipRole:
            if col == 1:
                return "MD5: " + row[1]
            if col == 4:
                return row[4] if row[4] else "未下载"
        return None

    def headerData(self, section, orientation, role):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return self.HEADERS[section]
        return None

    def get_row(self, row):
        if 0 <= row < len(self._rows):
            return self._rows[row]
        return None

    def search(self, text):
        if not self._version_ts:
            return
        self.beginResetModel()
        if text:
            self._rows = db.get_bundles_for_version(self._version_ts, name_filter=text)
        else:
            self._rows = db.get_bundles_for_version(
                self._version_ts, limit=self._batch, offset=0
            )
        self._loaded = len(self._rows)
        self.endResetModel()


def _format_size(size):
    if size is None:
        return "-"
    size = int(size)
    if size >= 1048576:
        return f"{size / 1048576:.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size} B"
