# -*- coding: utf-8 -*-
"""表格数据模型：视图行数据 + 可编辑列控制 + 脏标记。

每行是一个 dict：展示字段用中文列名做键，隐藏主键以 `_` 开头。
setData 只更新内存并记录脏单元格，真正落库由主窗口在「保存编辑」时完成。
"""

from __future__ import annotations

from typing import Callable

from PyQt5.QtCore import QAbstractTableModel, QModelIndex, Qt


class TableModel(QAbstractTableModel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._headers: list[str] = []
        self._rows: list[dict] = []
        self._editable: set[int] = set()
        self._checker: Callable[[dict, int], bool] | None = None
        self._dirty: set[tuple[int, int]] = set()

    def load(
        self,
        headers: list[str],
        rows: list[dict],
        editable_cols: set[int],
        checker: Callable[[dict, int], bool] | None = None,
    ) -> None:
        """载入一批行；checker 可选，用于逐行覆盖列的可编辑性。"""
        self.beginResetModel()
        self._headers = list(headers)
        self._rows = list(rows)
        self._editable = set(editable_cols)
        self._checker = checker
        self._dirty.clear()
        self.endResetModel()

    # -- Qt 模型接口 ---------------------------------------------
    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._headers)

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid():
            return None
        header = self._headers[index.column()]
        value = self._rows[index.row()].get(header, "")
        if role in (Qt.DisplayRole, Qt.EditRole):
            return value
        if role == Qt.ToolTipRole:
            text = str(value)
            return text if len(text) > 60 else None
        return None

    def headerData(
        self, section: int, orientation: int, role: int = Qt.DisplayRole
    ):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            if 0 <= section < len(self._headers):
                return self._headers[section]
        return None

    def flags(self, index: QModelIndex):
        if not index.isValid():
            return Qt.NoItemFlags
        flags = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        if self._is_editable(index.row(), index.column()):
            flags |= Qt.ItemIsEditable
        return flags

    def setData(self, index: QModelIndex, value, role: int = Qt.EditRole) -> bool:
        if not index.isValid() or not self._is_editable(index.row(), index.column()):
            return False
        header = self._headers[index.column()]
        self._rows[index.row()][header] = str(value)
        self._dirty.add((index.row(), index.column()))
        self.dataChanged.emit(index, index, [Qt.DisplayRole, Qt.EditRole])
        return True

    # -- 供界面调用 ---------------------------------------------
    def is_dirty(self) -> bool:
        return bool(self._dirty)

    def mark_clean(self) -> None:
        self._dirty.clear()

    def changed_cells(self) -> list[tuple[int, int]]:
        return sorted(self._dirty)

    def row_dict(self, row: int) -> dict:
        return self._rows[row]

    def header(self, col: int) -> str:
        return self._headers[col]

    def _is_editable(self, row: int, col: int) -> bool:
        if self._checker is not None:
            return self._checker(self._rows[row], col)
        return col in self._editable
