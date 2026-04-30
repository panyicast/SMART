from __future__ import annotations

from PySide6 import QtGui, QtWidgets


class NoWheelDoubleSpinBox(QtWidgets.QDoubleSpinBox):
    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:
        event.ignore()


class NoWheelSpinBox(QtWidgets.QSpinBox):
    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:
        event.ignore()


class NoWheelDateTimeEdit(QtWidgets.QDateTimeEdit):
    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:
        event.ignore()


class NoWheelComboBox(QtWidgets.QComboBox):
    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:
        event.ignore()
