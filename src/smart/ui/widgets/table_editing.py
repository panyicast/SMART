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


class ComboBoxTableEditDelegate(TableEditDelegate):
    """Use preset combo-box choices for selected table columns."""

    def __init__(
        self,
        options_by_column: dict[int, list[tuple[str, str]]],
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._options_by_column = options_by_column

    def createEditor(
        self,
        parent: QtWidgets.QWidget,
        option: QtWidgets.QStyleOptionViewItem,
        index: QtCore.QModelIndex,
    ) -> QtWidgets.QWidget | None:
        options = self._options_by_column.get(index.column())
        if not options:
            return super().createEditor(parent, option, index)
        editor = QtWidgets.QComboBox(parent)
        editor.setMinimumHeight(max(28, option.rect.height()))
        editor.setStyleSheet(
            """
            QComboBox {
                background: #071016;
                color: #f4fcff;
                border: 1px solid #66d9ea;
                border-radius: 6px;
                padding: 2px 8px;
            }
            QComboBox::drop-down {
                border: none;
                width: 20px;
            }
            QComboBox QAbstractItemView {
                background: #0d1c25;
                color: #d8e7ef;
                border: 1px solid #244958;
                selection-background-color: #1a6675;
                selection-color: #ffffff;
            }
            """
        )
        for label, value in options:
            editor.addItem(label, value)
        editor.currentIndexChanged.connect(lambda _index, combo=editor: self.commitData.emit(combo))
        return editor

    def setEditorData(self, editor: QtWidgets.QWidget, index: QtCore.QModelIndex) -> None:
        if isinstance(editor, QtWidgets.QComboBox):
            text = str(index.data(QtCore.Qt.ItemDataRole.EditRole) or "")
            combo_index = editor.findData(text)
            if combo_index < 0:
                combo_index = editor.findText(text)
            editor.setCurrentIndex(max(0, combo_index))
            return
        super().setEditorData(editor, index)

    def setModelData(
        self,
        editor: QtWidgets.QWidget,
        model: QtCore.QAbstractItemModel,
        index: QtCore.QModelIndex,
    ) -> None:
        if isinstance(editor, QtWidgets.QComboBox):
            model.setData(index, str(editor.currentData() or editor.currentText()), QtCore.Qt.ItemDataRole.EditRole)
            return
        super().setModelData(editor, model, index)


def install_table_edit_delegate(table: QtWidgets.QTableWidget) -> None:
    table.setItemDelegate(TableEditDelegate(table))


def install_combo_table_edit_delegate(
    table: QtWidgets.QTableWidget,
    options_by_column: dict[int, list[tuple[str, str]]],
) -> None:
    table.setItemDelegate(ComboBoxTableEditDelegate(options_by_column, table))
