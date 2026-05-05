from __future__ import annotations

from pathlib import Path

from PySide6 import QtGui, QtWidgets

_BUNDLED_FONT_DIR = Path(__file__).resolve().parents[1] / "assets" / "fonts" / "Noto_Sans_SC"
_BUNDLED_FONT_FILES = (
    _BUNDLED_FONT_DIR / "NotoSansSC-VariableFont_wght.ttf",
)

APP_STYLESHEET = """
QWidget {
    font-family: "Noto Sans SC", "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI";
    background: #071016;
    color: #d8e7ef;
    font-size: 11pt;
}
QLabel {
    background: transparent;
}
QMainWindow {
    background: #071016;
}
QMenuBar {
    background: #091720;
    color: #b8c9d2;
    border-bottom: 1px solid #153241;
}
QMenuBar::item {
    background: transparent;
    padding: 6px 10px;
}
QMenuBar::item:selected {
    background: #102734;
    color: #f2fbff;
}
QMenu {
    background: #0d1c25;
    color: #d8e7ef;
    border: 1px solid #1e3b49;
}
QMenu::item {
    padding: 7px 24px;
}
QMenu::item:selected {
    background: #123445;
    color: #ffffff;
}
QToolBar {
    background: #091720;
    border: none;
    border-bottom: 1px solid #153241;
    spacing: 8px;
    padding: 4px 8px;
}
QToolBar QLabel {
    background: transparent;
    color: #b8c9d2;
    font-weight: 700;
}
QStatusBar {
    background: #071016;
    color: #8fa8b4;
    border-top: 1px solid #153241;
}
QFrame[role="sidebar"] {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #07131b, stop:1 #0f2430);
    border-right: 1px solid #1e3b49;
}
QFrame[role="sidebar"] QWidget {
    background: transparent;
}
QLabel[role="brandTitle"] {
    color: #f7fbff;
    font-size: 28pt;
    font-weight: 700;
    letter-spacing: 3px;
}
QLabel[role="brandSubtitle"] {
    color: #8fa8b4;
    font-size: 10pt;
    line-height: 140%;
}
QListWidget[role="nav"] {
    background: transparent;
    color: #9fb5bf;
    border: none;
    outline: none;
}
QListWidget[role="nav"]::item {
    padding: 13px 14px;
    border-radius: 12px;
    margin: 4px 0px;
}
QListWidget[role="nav"]::item:hover {
    background: rgba(69, 146, 168, 0.13);
    color: #e8f7fb;
}
QListWidget[role="nav"]::item:selected {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #133847, stop:1 #0c5961);
    color: #ffffff;
}
QLabel[role="pageTitle"] {
    color: #edf8fb;
    font-size: 24pt;
    font-weight: 700;
}
QLabel[role="pageBody"] {
    color: #9fb5bf;
    font-size: 10.5pt;
}
QLabel[role="sectionTitle"] {
    color: #e5f4f8;
    font-size: 13pt;
    font-weight: 700;
}
QLabel[role="eyebrow"] {
    color: #6cd4e6;
    font-size: 9pt;
    font-weight: 700;
    letter-spacing: 1px;
}
QFrame[role="card"] {
    background: #0c1b24;
    border: 1px solid #1e3b49;
    border-radius: 18px;
}
QFrame[role="card"]:hover {
    border: 1px solid #2e6374;
}
QFrame[role="sceneToolbar"] {
    background: #071016;
    border: 1px solid #18313f;
    border-radius: 10px;
}
QFrame[role="sceneToolbar"] QPushButton {
    padding: 6px 12px;
    border-radius: 8px;
}
QFrame[role="sceneToolbar"] QPushButton:checked {
    background: #1c6d7b;
    color: #ffffff;
    border: 1px solid #4bb6c8;
}
QFrame[role="dashboardHero"] {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #0d2430, stop:0.52 #0b1b24, stop:1 #112f35);
    border: 1px solid #255160;
    border-radius: 24px;
}
QFrame[role="metricTile"] {
    background: #0b1a22;
    border: 1px solid #1b3a47;
    border-radius: 18px;
}
QFrame[role="glassPanel"] {
    background: rgba(12, 27, 36, 0.88);
    border: 1px solid #1d3d4a;
    border-radius: 20px;
}
QLabel[role="cardTitle"] {
    color: #eaf7fb;
    font-size: 14pt;
    font-weight: 700;
}
QLabel[role="cardCaption"] {
    color: #88a3ae;
    font-size: 10pt;
}
QLabel[role="metricValue"] {
    color: #66d9ea;
    font-size: 20pt;
    font-weight: 700;
}
QLabel[role="metricCaption"] {
    color: #78939e;
    font-size: 9.5pt;
}
QPushButton {
    background: #1c6d7b;
    color: #f4fcff;
    border: none;
    border-radius: 11px;
    padding: 10px 18px;
    font-weight: 700;
}
QPushButton:hover {
    background: #248799;
}
QPushButton:pressed {
    background: #155a66;
}
QPushButton[variant="secondary"] {
    background: #132733;
    color: #cde3ea;
    border: 1px solid #244958;
}
QPushButton[variant="secondary"]:hover {
    background: #173343;
    border: 1px solid #347084;
}
QDoubleSpinBox,
QSpinBox,
QLineEdit,
QDateTimeEdit,
QTextEdit,
QPlainTextEdit {
    background: #0d1c25;
    color: #d8e7ef;
    border: 1px solid #244958;
    border-radius: 8px;
    padding: 7px;
    selection-background-color: #1f6c7a;
}
QLabel[role="statusOperational"] {
    color: #55d18f;
    font-weight: 700;
}
QLabel[role="statusPlanned"] {
    color: #f2b84b;
    font-weight: 700;
}
QLabel[role="statusReady"] {
    color: #55d18f;
    font-weight: 700;
}
QLabel[role="statusLoading"] {
    color: #66d9ea;
    font-weight: 700;
}
QLabel[role="statusDisconnected"] {
    color: #ff7a66;
    font-weight: 700;
}
QComboBox {
    background: #0d1c25;
    border: 1px solid #244958;
    color: #d8e7ef;
    border-radius: 8px;
    padding: 5px 8px;
}
QComboBox::drop-down {
    border: none;
    width: 20px;
}
QComboBox QAbstractItemView {
    background: #0d1c25;
    color: #d8e7ef;
    selection-background-color: #1f6c7a;
    selection-color: #ffffff;
}
QFrame[role="sidebar"] QComboBox {
    background: #0d1c25;
    color: #d8e7ef;
    border: 1px solid #244958;
    font-weight: 600;
}
QFrame[role="sidebar"] QComboBox:focus {
    border: 1px solid #4aaec2;
}
QTableView,
QTableWidget {
    background: #0d1c25;
    alternate-background-color: #102531;
    color: #d8e7ef;
    border: 1px solid #244958;
    border-radius: 12px;
    gridline-color: #1b3440;
    selection-background-color: #164b58;
    selection-color: #ffffff;
    outline: none;
}
QTableView::item,
QTableWidget::item {
    background: #0d1c25;
    color: #d8e7ef;
    padding: 5px;
}
QTableView::item:alternate,
QTableWidget::item:alternate {
    background: #102531;
    color: #d8e7ef;
}
QTableView::item:selected,
QTableWidget::item:selected {
    background: #1a6675;
    color: #ffffff;
}
QTableView::item:disabled,
QTableWidget::item:disabled {
    color: #607b86;
}
QTableView QLineEdit,
QTableWidget QLineEdit {
    background: #071016;
    color: #f4fcff;
    border: 1px solid #66d9ea;
    border-radius: 6px;
    padding: 2px 8px;
    selection-background-color: #1a6675;
    selection-color: #ffffff;
}
QTableView::indicator,
QTableWidget::indicator {
    width: 16px;
    height: 16px;
}
QTableView::indicator:unchecked,
QTableWidget::indicator:unchecked {
    background: #071016;
    border: 1px solid #315566;
    border-radius: 2px;
}
QTableView::indicator:checked,
QTableWidget::indicator:checked {
    background: #1a6675;
    border: 1px solid #66d9ea;
    border-radius: 2px;
}
QTableCornerButton::section {
    background: #102734;
    border: none;
}
QHeaderView::section {
    background: #102734;
    color: #d8e7ef;
    padding: 8px;
    border: none;
    font-weight: 700;
}
QScrollArea {
    border: none;
    background: transparent;
}
QScrollBar:vertical {
    background: #071016;
    width: 12px;
    margin: 2px;
}
QScrollBar::handle:vertical {
    background: #1d3d4a;
    border-radius: 6px;
    min-height: 32px;
}
QScrollBar::handle:vertical:hover {
    background: #2f6575;
}
QScrollBar::add-line:vertical,
QScrollBar::sub-line:vertical {
    height: 0px;
}
QListWidget[role="recentList"] {
    background: #0b1a22;
    color: #bdd0d8;
    border: 1px solid #1d3d4a;
    border-radius: 16px;
    outline: none;
    padding: 6px;
}
QListWidget[role="recentList"]::item {
    padding: 10px;
    border-radius: 10px;
    margin: 3px;
}
QListWidget[role="recentList"]::item:hover {
    background: #102b38;
    color: #ffffff;
}
QListWidget[role="recentList"]::item:selected {
    background: #164b58;
    color: #ffffff;
}
"""


def apply_theme(app: QtWidgets.QApplication) -> None:
    app.setStyle("Fusion")
    bundled_families: list[str] = []
    for font_path in _BUNDLED_FONT_FILES:
        if font_path.exists():
            font_id = QtGui.QFontDatabase.addApplicationFont(str(font_path))
            if font_id >= 0:
                bundled_families.extend(QtGui.QFontDatabase.applicationFontFamilies(font_id))

    if not bundled_families:
        for font_path in (
            Path("C:/Windows/Fonts/NotoSansSC-VF.ttf"),
            Path("C:/Windows/Fonts/msyh.ttc"),
            Path("C:/Windows/Fonts/simhei.ttf"),
        ):
            if font_path.exists():
                QtGui.QFontDatabase.addApplicationFont(str(font_path))

    families = set(QtGui.QFontDatabase.families())
    preferred = tuple(dict.fromkeys(bundled_families)) + (
        "Noto Sans SC",
        "Microsoft YaHei UI",
        "Microsoft YaHei",
        "SimHei",
        "Segoe UI",
    )
    family = next((candidate for candidate in preferred if candidate in families), "Segoe UI")
    font = QtGui.QFont(family, 10)
    app.setFont(font)

    palette = QtGui.QPalette()
    palette.setColor(QtGui.QPalette.ColorRole.Window, QtGui.QColor("#071016"))
    palette.setColor(QtGui.QPalette.ColorRole.WindowText, QtGui.QColor("#d8e7ef"))
    palette.setColor(QtGui.QPalette.ColorRole.Base, QtGui.QColor("#0d1c25"))
    palette.setColor(QtGui.QPalette.ColorRole.AlternateBase, QtGui.QColor("#102531"))
    palette.setColor(QtGui.QPalette.ColorRole.Text, QtGui.QColor("#d8e7ef"))
    palette.setColor(QtGui.QPalette.ColorRole.Button, QtGui.QColor("#132733"))
    palette.setColor(QtGui.QPalette.ColorRole.ButtonText, QtGui.QColor("#f4fcff"))
    palette.setColor(QtGui.QPalette.ColorRole.Highlight, QtGui.QColor("#1a6675"))
    palette.setColor(QtGui.QPalette.ColorRole.HighlightedText, QtGui.QColor("#ffffff"))
    palette.setColor(QtGui.QPalette.ColorRole.ToolTipBase, QtGui.QColor("#102734"))
    palette.setColor(QtGui.QPalette.ColorRole.ToolTipText, QtGui.QColor("#d8e7ef"))
    palette.setColor(QtGui.QPalette.ColorGroup.Disabled, QtGui.QPalette.ColorRole.Text, QtGui.QColor("#607b86"))
    palette.setColor(QtGui.QPalette.ColorGroup.Disabled, QtGui.QPalette.ColorRole.WindowText, QtGui.QColor("#607b86"))
    app.setPalette(palette)
    app.setStyleSheet(APP_STYLESHEET)
