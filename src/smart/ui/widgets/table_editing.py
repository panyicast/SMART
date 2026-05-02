from __future__ import annotations

from PySide6 import QtCore, QtWidgets


class TableEditDelegate(QtWidgets.QStyledItemDelegate):
    """Make in-cell text editing readable in compact dark tables."""

    def createEditor(
        self,
        parent: QtWidgets.QWidget,
        option: QtWidgets.QStyleOptionViewItem,
        index: QtCore.QModelIndex,
    ) -> QtWidgets.QWidget | None:
        editor = super().createEditor(parent, option, index)
        if isinstance(editor, QtWidgets.QLineEdit):
            editor.setFrame(False)
            editor.setMinimumHeight(max(28, option.rect.height()))
            editor.setContentsMargins(0, 0, 0, 0)
            editor.setStyleSheet(
                """
                QLineEdit {
                    background: #071016;
                    color: #f4fcff;
                    border: 1px solid #66d9ea;
                    border-radius: 6px;
                    padding: 2px 8px;
                    selection-background-color: #1a6675;
                    selection-color: #ffffff;
                }
                """
            )
            QtCore.QTimer.singleShot(0, editor.selectAll)
        return editor

    def updateEditorGeometry(
        self,
        editor: QtWidgets.QWidget,
        option: QtWidgets.QStyleOptionViewItem,
        index: QtCore.QModelIndex,
    ) -> None:
        rect = option.rect.adjusted(2, 2, -2, -2)
        editor.setGeometry(rect)


def install_table_edit_delegate(table: QtWidgets.QTableWidget) -> None:
    table.setItemDelegate(TableEditDelegate(table))
