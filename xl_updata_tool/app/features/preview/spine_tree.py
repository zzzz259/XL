"""Qt tree for selecting Spine skins by stable resource identity."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Iterable, Mapping

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import QHeaderView, QTreeWidget, QTreeWidgetItem

from .character_names import CharacterNameResolver, display_role_label, display_skin_label
from .resource_model import PreviewResourceCatalog, SpineSkinRecord, display_skin_key, skin_key
from .resource_state import PreviewResourceState
from app.ui.theme import get_color


class PreviewSpineTree(QTreeWidget):
    """Display a compact role → skin tree with recursive tri-state checks."""

    selection_changed = Signal(object)

    _ROLE = "role"
    _SKIN = "skin"

    def __init__(self, state: PreviewResourceState | None = None, parent=None):
        super().__init__(parent)
        self.setObjectName("previewSpineTree")
        self.setAccessibleName("角色 Spine 资源树")
        self.setColumnCount(2)
        self.setHeaderLabels(["角色 / 皮肤", "状态"])
        header = self.header()
        header.setMinimumSectionSize(96)
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        header.setSectionResizeMode(1, QHeaderView.Fixed)
        header.resizeSection(0, 560)
        header.resizeSection(1, 96)
        self.setSelectionMode(QTreeWidget.SingleSelection)
        self.setUniformRowHeights(True)
        self._state = state
        self._catalog = PreviewResourceCatalog.from_records(())
        self._character_name_resolver = None
        self._updating = False
        self._mouse_multi_select = False
        self.itemChanged.connect(self._on_item_changed)

    def mousePressEvent(self, event) -> None:
        self._mouse_multi_select = bool(event.modifiers() & Qt.ControlModifier)
        super().mousePressEvent(event)

    def set_resource_state(self, state: PreviewResourceState | None) -> None:
        self._state = state
        self._refresh_statuses()

    def set_character_name_resolver(self, resolver: CharacterNameResolver | None) -> None:
        self._character_name_resolver = resolver
        if self._catalog.skins:
            self.set_catalog(self._catalog)

    def set_catalog(self, catalog: PreviewResourceCatalog | None) -> None:
        """Replace displayed resources while retaining checks for stable skin keys."""
        previous = {skin_key(record) for record in self.selected_records()}
        self._catalog = catalog or PreviewResourceCatalog.from_records(())
        records_by_group: OrderedDict[str, tuple[str | None, list[SpineSkinRecord]]] = OrderedDict()
        for record in self._catalog.skins.values():
            if record.resource_family == "eventcovers":
                group_key = "eventcovers"
            elif record.character_id:
                group_key = f"role:{record.character_id}"
            else:
                group_key = f"unmatched:{record.resource_family}"
            role_id, group_records = records_by_group.setdefault(
                group_key, (record.character_id, [])
            )
            group_records.append(record)

        self._updating = True
        try:
            self.clear()
            def group_sort(entry):
                key, (_role_id, _records) = entry
                return (
                    key != "eventcovers" and not key.startswith("unmatched:"),
                    key.casefold(),
                )

            for group_key, (role_id, role_records) in sorted(records_by_group.items(), key=group_sort):
                role_label = (
                    "活动封面" if group_key == "eventcovers"
                    else "未匹配资源" if group_key.startswith("unmatched:")
                    else display_role_label(role_id, None, self._character_name_resolver)
                )
                role_item = QTreeWidgetItem([role_label, ""])
                role_item.setData(
                    0,
                    Qt.UserRole,
                    {
                        "kind": self._ROLE,
                        "role_id": role_id,
                        "group": group_key,
                        "resource_family": (
                            "eventcovers"
                            if group_key == "eventcovers"
                            else "role"
                        ),
                    },
                )
                self._make_checkable(
                    role_item,
                    tristate=True,
                    enabled=any(self._is_eligible(item) for item in role_records),
                )
                self.addTopLevelItem(role_item)

                skin_groups: OrderedDict[tuple[str, str, str], list[SpineSkinRecord]] = OrderedDict()
                family_order = {"cardspine": 0, "battlespine": 1, "eventcovers": 2, "spine": 3}
                for record in sorted(
                    role_records,
                    key=lambda item: (
                        family_order.get(item.resource_family, 9),
                        display_skin_label(item.source_skel, item.character_id, item.resource_family).casefold(),
                        item.source_skel.casefold(),
                    ),
                ):
                    skin_groups.setdefault(display_skin_key(record), []).append(record)
                for grouped_records in skin_groups.values():
                    primary = self._primary_record(grouped_records)
                    skin_item = QTreeWidgetItem([
                        display_skin_label(
                            primary.source_skel,
                            primary.character_id,
                            primary.resource_family,
                        ),
                        "",
                    ])
                    skin_item.setData(
                        0,
                        Qt.UserRole,
                        {
                            "kind": self._SKIN,
                            "role_id": primary.character_id,
                            "skin_key": skin_key(primary),
                            "records": tuple(grouped_records),
                        },
                    )
                    self._make_checkable(skin_item, enabled=any(
                        self._is_eligible(item) for item in grouped_records
                    ))
                    role_item.addChild(skin_item)

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
        for skin_item in self._skin_items():
            if skin_item.checkState(0) != Qt.Checked:
                continue
            data = skin_item.data(0, Qt.UserRole) or {}
            grouped_records = tuple(data.get("records", ()))
            primary = self._primary_record(grouped_records)
            if self._is_eligible(primary) and skin_key(primary) not in seen:
                result.append(primary)
                seen.add(skin_key(primary))
        return tuple(result)

    def selected_record_groups(self) -> tuple[tuple[SpineSkinRecord, ...], ...]:
        """Return every source part belonging to each checked visible skin."""
        result = []
        for skin_item in self._skin_items():
            if skin_item.checkState(0) != Qt.Checked:
                continue
            data = skin_item.data(0, Qt.UserRole) or {}
            grouped_records = tuple(data.get("records", ()))
            if grouped_records and any(self._is_eligible(record) for record in grouped_records):
                parts = OrderedDict()
                for record in grouped_records:
                    source_key = (
                        record.source_skel.casefold(),
                        record.atlas_path.casefold(),
                    )
                    parts.setdefault(source_key, []).append(record)
                result.append(tuple(self._primary_record(records) for records in parts.values()))
        return tuple(result)

    def selected_skin_names(self) -> tuple[str, ...]:
        """Return internal Spine skin names for the first selected visible group."""
        for skin_item in self._skin_items():
            if skin_item.checkState(0) != Qt.Checked:
                continue
            records = (skin_item.data(0, Qt.UserRole) or {}).get("records", ())
            return tuple(dict.fromkeys(record.skin_name for record in records if record.skin_name))
        return ()

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
            for skin_item in self._skin_items():
                records = (skin_item.data(0, Qt.UserRole) or {}).get("records", ())
                checked = any(skin_key(record) in wanted for record in records)
                skin_item.setCheckState(0, Qt.Checked if checked else Qt.Unchecked)
            self._refresh_parent_checks()
        finally:
            self._updating = False
        self._emit_selection()

    def mark_selected_read(self) -> None:
        """Mark checked resource fingerprints read and refresh every ancestor marker."""
        if self._state is None:
            return
        changed = False
        for skin_item in self._skin_items():
            if skin_item.checkState(0) != Qt.Checked:
                continue
            for record in (skin_item.data(0, Qt.UserRole) or {}).get("records", ()):
                changed = self._state.mark_read(skin_key(record)) or changed
        if changed:
            self._refresh_statuses()
            self._state.save()

    def mark_all_read(self) -> None:
        if self._state is None:
            return
        changed = False
        for record in self._catalog.skins.values():
            changed = self._state.mark_read(skin_key(record)) or changed
        for record in self._catalog.unmatched:
            changed = self._state.mark_read(skin_key(record)) or changed
        for record in self._catalog.eventcovers:
            changed = self._state.mark_read(skin_key(record)) or changed
        if changed:
            self._state.save()
        self._refresh_statuses()

    def _record_fingerprint(self, record: SpineSkinRecord) -> str:
        return skin_key(record)

    @staticmethod
    def _primary_record(records: Iterable[SpineSkinRecord]) -> SpineSkinRecord:
        grouped = tuple(records)
        return min(
            grouped,
            key=lambda record: (
                "_bg" in record.source_skel.casefold() or "bg" in record.display_name.casefold(),
                record.skin_name.casefold() != "default",
                not bool(record.atlas_path),
                record.source_skel.casefold(),
            ),
        )

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
            # Parent states are aggregated below so disabled descendants are
            # never auto-checked by QTreeWidget's built-in propagation.
            flags &= ~Qt.ItemIsAutoTristate
        item.setFlags(flags)
        item.setCheckState(0, Qt.Unchecked)

    @staticmethod
    def _is_eligible(record: SpineSkinRecord | None) -> bool:
        return bool(record and record.status == "ready" and record.atlas_path)

    def _skin_items(self):
        for role_index in range(self.topLevelItemCount()):
            role = self.topLevelItem(role_index)
            for skin_index in range(role.childCount()):
                yield role.child(skin_index)

    def _restore_selection(self, wanted: set[str]) -> None:
        for skin_item in self._skin_items():
            records = (skin_item.data(0, Qt.UserRole) or {}).get("records", ())
            if any(self._is_eligible(record) and skin_key(record) in wanted for record in records):
                skin_item.setCheckState(0, Qt.Checked)
        self._refresh_parent_checks()

    def _refresh_parent_checks(self) -> None:
        for role_index in range(self.topLevelItemCount()):
            role = self.topLevelItem(role_index)
            for skin_index in range(role.childCount()):
                skin = role.child(skin_index)
                if skin.childCount():
                    self._set_parent_state(skin)
            self._set_parent_state(role)

    def _set_parent_state(self, item: QTreeWidgetItem) -> None:
        eligible_children = [
            item.child(index)
            for index in range(item.childCount())
            if self._has_eligible_descendant(item.child(index))
        ]
        states = [child.checkState(0) for child in eligible_children]
        if not states:
            item.setCheckState(0, Qt.Unchecked)
            return
        if all(state == Qt.Checked for state in states):
            state = Qt.Checked
        elif all(state == Qt.Unchecked for state in states):
            state = Qt.Unchecked
        else:
            state = Qt.PartiallyChecked
        item.setCheckState(0, state)

    def _has_eligible_descendant(self, item: QTreeWidgetItem) -> bool:
        if not item.childCount():
            records = (item.data(0, Qt.UserRole) or {}).get("records", ())
            return any(self._is_eligible(record) for record in records)
        return any(self._has_eligible_descendant(item.child(index)) for index in range(item.childCount()))

    def _set_subtree_check_state(self, item: QTreeWidgetItem, state) -> None:
        if not self._has_eligible_descendant(item):
            item.setCheckState(0, Qt.Unchecked)
            return
        if not item.childCount():
            item.setCheckState(0, state if self._has_eligible_descendant(item) else Qt.Unchecked)
            return
        for index in range(item.childCount()):
            self._set_subtree_check_state(item.child(index), state)
        self._set_parent_state(item)

    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._updating or column != 0:
            return
        self._updating = True
        try:
            if item.checkState(0) == Qt.Checked and not self._mouse_multi_select:
                self._clear_other_checks(item)
            if item.childCount():
                state = item.checkState(0)
                child_state = Qt.Checked if state == Qt.Checked else Qt.Unchecked
                self._set_subtree_check_state(item, child_state)
            elif not self._has_eligible_descendant(item):
                item.setCheckState(0, Qt.Unchecked)
            self._refresh_parent_checks()
        finally:
            self._updating = False
            self._mouse_multi_select = False
        self._emit_selection()
        if self._state is not None:
            self.mark_selected_read()

    def _clear_other_checks(self, selected_item: QTreeWidgetItem) -> None:
        for skin_item in self._skin_items():
            if skin_item is selected_item or self._is_descendant(selected_item, skin_item):
                continue
            if skin_item.checkState(0) != Qt.Unchecked:
                skin_item.setCheckState(0, Qt.Unchecked)

    @staticmethod
    def _is_descendant(parent: QTreeWidgetItem, item: QTreeWidgetItem) -> bool:
        current = item.parent()
        while current is not None:
            if current is parent:
                return True
            current = current.parent()
        return False

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
            records = (item.data(0, Qt.UserRole) or {}).get("records", ())
            is_new = any(self._is_eligible(record) and self._is_new(record) for record in records)
        item.setText(1, "新" if is_new else "")
        item.setForeground(1, QBrush(QColor(get_color("DANGER"))) if is_new else QBrush())
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
