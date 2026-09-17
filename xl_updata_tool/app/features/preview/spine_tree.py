"""Qt tree for selecting Spine skins by stable resource identity."""

from __future__ import annotations

import os
from collections import OrderedDict
from collections.abc import Iterable, Mapping

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem

from .resource_model import PreviewResourceCatalog, SpineSkinRecord, skin_key
from .resource_state import PreviewResourceState


class PreviewSpineTree(QTreeWidget):
    """Display role → skin → Spine file nodes with recursive tri-state checks."""

    selection_changed = Signal(object)

    _ROLE = "role"
    _SKIN = "skin"
    _FILE = "file"

    def __init__(self, state: PreviewResourceState | None = None, parent=None):
        super().__init__(parent)
        self.setObjectName("previewSpineTree")
        self.setAccessibleName("角色 Spine 资源树")
        self.setColumnCount(2)
        self.setHeaderLabels(["角色 / 皮肤 / 文件", "状态"])
        self.setSelectionMode(QTreeWidget.SingleSelection)
        self.setUniformRowHeights(True)
        self._state = state
        self._catalog = PreviewResourceCatalog.from_records(())
        self._updating = False
        self.itemChanged.connect(self._on_item_changed)

    def set_resource_state(self, state: PreviewResourceState | None) -> None:
        self._state = state
        self._refresh_statuses()

    def set_catalog(self, catalog: PreviewResourceCatalog | None) -> None:
        """Replace displayed resources while retaining checks for stable skin keys."""
        previous = {skin_key(record) for record in self.selected_records()}
        self._catalog = catalog or PreviewResourceCatalog.from_records(())
        records_by_role: OrderedDict[str | None, list[SpineSkinRecord]] = OrderedDict()
        records = list(self._catalog.skins.values())
        for record in self._catalog.unmatched:
            if skin_key(record) not in {skin_key(item) for item in records}:
                records.append(record)
        for record in records:
            records_by_role.setdefault(record.character_id, []).append(record)

        self._updating = True
        try:
            self.clear()
            for role_id, role_records in sorted(
                records_by_role.items(), key=lambda entry: (entry[0] is None, str(entry[0] or "").casefold())
            ):
                role_item = QTreeWidgetItem([role_id or "未匹配资源", ""])
                role_item.setData(0, Qt.UserRole, {"kind": self._ROLE, "role_id": role_id})
                self._make_checkable(role_item, tristate=True)
                self.addTopLevelItem(role_item)

                skin_groups: OrderedDict[str, list[SpineSkinRecord]] = OrderedDict()
                for record in sorted(role_records, key=lambda item: (item.display_name or item.skin_name).casefold()):
                    skin_groups.setdefault(skin_key(record), []).append(record)
                for record_key, grouped_records in skin_groups.items():
                    primary = grouped_records[0]
                    skin_item = QTreeWidgetItem([primary.display_name or primary.skin_name or "未命名皮肤", ""])
                    skin_item.setData(
                        0,
                        Qt.UserRole,
                        {
                            "kind": self._SKIN,
                            "role_id": primary.character_id,
                            "skin_key": record_key,
                            "records": tuple(grouped_records),
                        },
                    )
                    self._make_checkable(
                        skin_item,
                        tristate=True,
                        enabled=all(self._is_eligible(item) for item in grouped_records),
                    )
                    role_item.addChild(skin_item)
                    for record in grouped_records:
                        file_item = QTreeWidgetItem([os.path.basename(record.source_skel), ""])
                        file_item.setData(
                            0,
                            Qt.UserRole,
                            {
                                "kind": self._FILE,
                                "role_id": record.character_id,
                                "skin_key": record_key,
                                "skel_path": record.source_skel,
                                "atlas_path": record.atlas_path,
                                "record": record,
                            },
                        )
                        self._make_checkable(file_item, enabled=self._is_eligible(record))
                        if record.diagnostic:
                            file_item.setToolTip(0, record.diagnostic)
                        skin_item.addChild(file_item)

            self._refresh_statuses()
            self._restore_selection(previous)
            self.expandAll()
        finally:
            self._updating = False
        self._emit_selection()

    def selected_records(self) -> tuple[SpineSkinRecord, ...]:
        """Return checked skins, de-duplicated by their stable skin key."""
        result = []
        seen = set()
        for file_item in self._file_items():
            if file_item.checkState(0) != Qt.Checked:
                continue
            record = file_item.data(0, Qt.UserRole).get("record")
            if record is not None and self._is_eligible(record) and skin_key(record) not in seen:
                result.append(record)
                seen.add(skin_key(record))
        return tuple(result)

    def set_selected_records(self, records: Iterable[SpineSkinRecord | Mapping | str]) -> None:
        """Set checks from records, stable keys, or identity mappings."""
        wanted = set()
        for record in records:
            if isinstance(record, str):
                wanted.add(record)
            elif isinstance(record, Mapping):
                value = record.get("skin_key")
                if value:
                    wanted.add(str(value))
            else:
                wanted.add(skin_key(record))

        self._updating = True
        try:
            for file_item in self._file_items():
                record = file_item.data(0, Qt.UserRole).get("record")
                file_item.setCheckState(0, Qt.Checked if record and skin_key(record) in wanted else Qt.Unchecked)
            self._refresh_parent_checks()
        finally:
            self._updating = False
        self._emit_selection()

    def mark_selected_read(self) -> None:
        """Mark checked resource fingerprints read and refresh every ancestor marker."""
        if self._state is None:
            return
        changed = False
        for record in self.selected_records():
            fingerprint = self._record_fingerprint(record)
            changed = self._state.mark_read(fingerprint) or changed
        if changed:
            self._refresh_statuses()

    def _record_fingerprint(self, record: SpineSkinRecord) -> str:
        return record.attachment_fingerprint or record.identity_fingerprint or skin_key(record)

    def _make_checkable(
        self,
        item: QTreeWidgetItem,
        tristate: bool = False,
        enabled: bool = True,
    ) -> None:
        flags = item.flags() | Qt.ItemIsSelectable
        if enabled:
            flags |= Qt.ItemIsUserCheckable | Qt.ItemIsEnabled
        else:
            flags &= ~Qt.ItemIsUserCheckable
            flags &= ~Qt.ItemIsEnabled
        if tristate:
            flags |= Qt.ItemIsAutoTristate
        item.setFlags(flags)
        item.setCheckState(0, Qt.Unchecked)

    @staticmethod
    def _is_eligible(record: SpineSkinRecord | None) -> bool:
        return bool(record and record.status == "ready" and record.atlas_path)

    def _file_items(self):
        for role_index in range(self.topLevelItemCount()):
            role = self.topLevelItem(role_index)
            for skin_index in range(role.childCount()):
                skin = role.child(skin_index)
                for file_index in range(skin.childCount()):
                    yield skin.child(file_index)

    def _restore_selection(self, wanted: set[str]) -> None:
        for file_item in self._file_items():
            record = file_item.data(0, Qt.UserRole).get("record")
            if record is not None and skin_key(record) in wanted:
                file_item.setCheckState(0, Qt.Checked)
        self._refresh_parent_checks()

    def _refresh_parent_checks(self) -> None:
        for role_index in range(self.topLevelItemCount()):
            role = self.topLevelItem(role_index)
            for skin_index in range(role.childCount()):
                self._set_parent_state(role.child(skin_index))
            self._set_parent_state(role)

    def _set_parent_state(self, item: QTreeWidgetItem) -> None:
        states = [item.child(index).checkState(0) for index in range(item.childCount())]
        if not states:
            return
        if all(state == Qt.Checked for state in states):
            state = Qt.Checked
        elif all(state == Qt.Unchecked for state in states):
            state = Qt.Unchecked
        else:
            state = Qt.PartiallyChecked
        item.setCheckState(0, state)

    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._updating or column != 0:
            return
        self._updating = True
        try:
            if item.childCount():
                state = item.checkState(0)
                child_state = Qt.Checked if state == Qt.Checked else Qt.Unchecked
                for index in range(item.childCount()):
                    item.child(index).setCheckState(0, child_state)
            self._refresh_parent_checks()
        finally:
            self._updating = False
        self._emit_selection()
        if self._state is not None:
            self.mark_selected_read()

    def _refresh_statuses(self) -> None:
        previous_update_state = self._updating
        self._updating = True
        try:
            for role_index in range(self.topLevelItemCount()):
                role = self.topLevelItem(role_index)
                for skin_index in range(role.childCount()):
                    skin = role.child(skin_index)
                    self._refresh_item_status(skin)
                self._refresh_item_status(role)
        finally:
            self._updating = previous_update_state

    def _refresh_item_status(self, item: QTreeWidgetItem) -> bool:
        if item.childCount():
            is_new = any(self._refresh_item_status(item.child(index)) for index in range(item.childCount()))
        else:
            record = item.data(0, Qt.UserRole).get("record")
            is_new = self._is_new(record)
        item.setText(1, "新" if is_new else "")
        return is_new

    def _is_new(self, record: SpineSkinRecord | None) -> bool:
        if record is None:
            return False
        if self._state is not None:
            return self._state.is_new(self._record_fingerprint(record))
        value = getattr(record, "is_new", None)
        return True if value is None else bool(value)

    def _emit_selection(self) -> None:
        self.selection_changed.emit(self.selected_records())
