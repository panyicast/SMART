from __future__ import annotations

from PySide6 import QtGui, QtWidgets

APP_STYLESHEET = """
QWidget {
    background: #f2ede3;
    color: #1f2b33;
    font-size: 11pt;
}
QMainWindow {
    background: #f2ede3;
}
QToolBar {
    background: #e6ddcf;
    border: none;
    border-bottom: 1px solid #d0c4b2;
    spacing: 8px;
    padding: 4px 8px;
}
QToolBar QLabel {
    background: transparent;
    color: #10263b;
    font-weight: 700;
}
QFrame[role="sidebar"] {
    background: #123047;
    border-right: 1px solid #2e5168;
}
QFrame[role="sidebar"] QWidget {
    background: transparent;
}
QLabel[role="brandTitle"] {
    color: #f6fbff;
    font-size: 28pt;
    font-weight: 700;
    letter-spacing: 3px;
}
QLabel[role="brandSubtitle"] {
    color: #d4e4f0;
    font-size: 10pt;
    line-height: 140%;
}
QListWidget[role="nav"] {
    background: transparent;
    color: #d8e2e8;
    border: none;
    outline: none;
}
QListWidget[role="nav"]::item {
    padding: 12px 14px;
    border-radius: 12px;
    margin: 4px 0px;
}
QListWidget[role="nav"]::item:selected {
    background: #1f4e6d;
    color: #ffffff;
}
QLabel[role="pageTitle"] {
    color: #10263b;
    font-size: 22pt;
    font-weight: 700;
}
QLabel[role="pageBody"] {
    color: #4f5e68;
    font-size: 10.5pt;
}
QFrame[role="card"] {
    background: #fffaf2;
    border: 1px solid #d9d1c3;
    border-radius: 18px;
}
QLabel[role="cardTitle"] {
    color: #10263b;
    font-size: 14pt;
    font-weight: 700;
}
QLabel[role="cardCaption"] {
    color: #6f7c85;
    font-size: 10pt;
}
QLabel[role="metricValue"] {
    color: #c25c38;
    font-size: 18pt;
    font-weight: 700;
}
QPushButton {
    background: #c25c38;
    color: #ffffff;
    border: none;
    border-radius: 10px;
    padding: 10px 18px;
    font-weight: 700;
}
QPushButton:hover {
    background: #ab4f30;
}
QPushButton:pressed {
    background: #93452a;
}
QDoubleSpinBox,
QSpinBox,
QLineEdit {
    background: #fffdf8;
    border: 1px solid #cbbfaa;
    border-radius: 8px;
    padding: 7px;
}
QLabel[role="statusOperational"] {
    color: #0f7b56;
    font-weight: 700;
}
QLabel[role="statusPlanned"] {
    color: #aa6a00;
    font-weight: 700;
}
QLabel[role="statusReady"] {
    color: #0f7b56;
    font-weight: 700;
}
QLabel[role="statusLoading"] {
    color: #2d5f85;
    font-weight: 700;
}
QLabel[role="statusDisconnected"] {
    color: #a13f22;
    font-weight: 700;
}
QComboBox {
    background: #fffdf8;
    border: 1px solid #cbbfaa;
    color: #1f2b33;
    border-radius: 8px;
    padding: 5px 8px;
}
QComboBox::drop-down {
    border: none;
    width: 20px;
}
QComboBox QAbstractItemView {
    background: #fffdf8;
    color: #1f2b33;
    selection-background-color: #dce8f1;
    selection-color: #10263b;
}
QFrame[role="sidebar"] QComboBox {
    background: #f4efe3;
    color: #10263b;
    border: 1px solid #c8bea8;
    font-weight: 600;
}
QFrame[role="sidebar"] QComboBox:focus {
    border: 1px solid #8ba9bc;
}
QTableWidget {
    background: #fffdf8;
    border: 1px solid #d9d1c3;
    border-radius: 12px;
    gridline-color: #e6ddcf;
}
QHeaderView::section {
    background: #ebe2d2;
    color: #1f2b33;
    padding: 8px;
    border: none;
    font-weight: 700;
}
QScrollArea {
    border: none;
    background: transparent;
}
"""


def apply_theme(app: QtWidgets.QApplication) -> None:
    app.setStyle("Fusion")
    font = QtGui.QFont("Bahnschrift SemiCondensed", 10)
    if font.family() != "Bahnschrift SemiCondensed":
        font = QtGui.QFont("Segoe UI", 10)
    app.setFont(font)
    app.setStyleSheet(APP_STYLESHEET)
