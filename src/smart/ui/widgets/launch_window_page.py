from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from datetime import timedelta, timezone
import hashlib
import json
from pathlib import Path
import time
from typing import Any

from PySide6 import QtCore, QtGui, QtWidgets

from smart.services.earth_orientation import format_utc, parse_utc
from smart.services.launch_window import (
    BURN_SUN_AXIS_MINUS_Z,
    BURN_SUN_AXIS_PLUS_Z,
    CONSTRAINT_TYPE_GROUND_OR_RELAY_VISIBLE,
    CONSTRAINT_TYPE_GROUND_VISIBLE,
    CONSTRAINT_TYPE_NO_SHADOW,
    CONSTRAINT_TYPE_RELAY_VISIBLE,
    CONSTRAINT_TYPE_THETA_S,
    compute_launch_windows,
    config_from_payload,
    default_constraint_rows,
    default_ground_station_presets,
    default_launch_window_config,
    default_relay_satellite_presets,
    merge_launch_window_samples,
    tracking_assets_from_config,
)
from smart.services.project_workspace import ProjectWorkspace
from smart.ui.i18n import I18nManager
from smart.ui.widgets.launch_window_gantt import (
    LaunchWindowGanttWidget,
    _GanttScrollArea,
    _GanttSegment,
)
from smart.ui.widgets.spinboxes import NoWheelComboBox, NoWheelDateTimeEdit, NoWheelDoubleSpinBox
from smart.ui.widgets.table_editing import install_table_edit_delegate


BEIJING_TZ = timezone(timedelta(hours=8))
BEIJING_QT_TIMEZONE_ID = b"Asia/Shanghai"


def _beijing_qtimezone() -> QtCore.QTimeZone:
    return QtCore.QTimeZone(BEIJING_QT_TIMEZONE_ID)


class _StateComboBox(NoWheelComboBox):
    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        super().paintEvent(event)
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(QtGui.QColor("#58dff4"))
        center_x = self.width() - 13
        center_y = self.height() // 2 + 1
        painter.drawPolygon(
            QtGui.QPolygon(
                [
                    QtCore.QPoint(center_x - 5, center_y - 3),
                    QtCore.QPoint(center_x + 5, center_y - 3),
                    QtCore.QPoint(center_x, center_y + 3),
                ]
            )
        )


class _LaunchWindowStateDialog(QtWidgets.QDialog):
    def __init__(self, page: "LaunchWindowPage") -> None:
        super().__init__(page)
        self._page = page
        self._drag_position: QtCore.QPoint | None = None
        self.setObjectName("launchWindowStateDialog")
        self.setWindowTitle("状态设置")
        self.setWindowFlag(QtCore.Qt.WindowType.FramelessWindowHint, True)
        self.resize(1160, 880)
        self.setMinimumSize(1040, 700)
        self._apply_dialog_style()

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 24)
        root.setSpacing(14)

        self._title_bar = QtWidgets.QWidget()
        self._title_bar.setCursor(QtCore.Qt.CursorShape.SizeAllCursor)
        title_row = QtWidgets.QHBoxLayout(self._title_bar)
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(12)
        title_icon = QtWidgets.QLabel("◎")
        title_icon.setObjectName("dialogTitleIcon")
        title_icon.setFixedSize(28, 28)
        title_icon.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        title_row.addWidget(title_icon)
        title_label = QtWidgets.QLabel("发射窗口状态设置")
        title_label.setProperty("role", "pageTitle")
        title_row.addWidget(title_label)
        title_row.addStretch(1)
        close_button = QtWidgets.QToolButton()
        close_button.setObjectName("dialogCloseButton")
        close_button.setText("X")
        close_button.clicked.connect(self.reject)
        title_row.addWidget(close_button)
        for drag_widget in (self._title_bar, title_icon, title_label):
            drag_widget.installEventFilter(self)
        root.addWidget(self._title_bar)

        caption = QtWidgets.QLabel("设置测控资源、可见性阈值、点火姿态约束及分阶段限制条件。应用后仍需保存参数或重新计算。")
        caption.setProperty("role", "cardCaption")
        caption.setWordWrap(True)
        root.addWidget(caption)

        scroll = QtWidgets.QScrollArea()
        scroll.setObjectName("stateDialogScrollArea")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        canvas = QtWidgets.QWidget()
        contents = QtWidgets.QVBoxLayout(canvas)
        contents.setContentsMargins(0, 0, 18, 0)
        contents.setSpacing(16)
        contents.addWidget(page._build_tracking_asset_card())
        contents.addWidget(page._build_constraint_card())
        contents.addStretch(1)
        scroll.setWidget(canvas)
        root.addWidget(scroll, 1)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Save | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        apply_button = buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Save)
        cancel_button = buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        apply_button.setText("应用状态设置")
        cancel_button.setText("取消")
        apply_button.setProperty("variant", "primaryAction")
        cancel_button.setProperty("variant", "secondary")
        apply_button.setMinimumHeight(48)
        cancel_button.setMinimumHeight(48)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons, 0, QtCore.Qt.AlignmentFlag.AlignRight)

    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:
        if watched in {self._title_bar, *self._title_bar.findChildren(QtWidgets.QLabel)}:
            if self._handle_drag_event(event):
                return True
        return super().eventFilter(watched, event)

    def _handle_drag_event(self, event: QtCore.QEvent) -> bool:
        if not isinstance(event, QtGui.QMouseEvent):
            return False
        if event.type() == QtCore.QEvent.Type.MouseButtonPress and event.button() == QtCore.Qt.MouseButton.LeftButton:
            self._drag_position = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return True
        if event.type() == QtCore.QEvent.Type.MouseMove and self._drag_position is not None:
            if event.buttons() & QtCore.Qt.MouseButton.LeftButton:
                self.move(event.globalPosition().toPoint() - self._drag_position)
                event.accept()
                return True
        if event.type() == QtCore.QEvent.Type.MouseButtonRelease and self._drag_position is not None:
            self._drag_position = None
            event.accept()
            return True
        return False

    def _apply_dialog_style(self) -> None:
        self.setStyleSheet(
            """
            QDialog#launchWindowStateDialog {
                background: qradialgradient(cx:0.50, cy:0.10, radius:1.15, fx:0.50, fy:0.10, stop:0 #0c2230, stop:0.50 #07131c, stop:1 #03090f);
                border: 1px solid #1c7d9a;
                border-radius: 22px;
            }
            QDialog#launchWindowStateDialog QWidget { background: transparent; }
            QDialog#launchWindowStateDialog QFrame[role="card"] {
                background: rgba(5, 17, 25, 0.62);
                border: 1px solid #1e7892;
                border-radius: 14px;
            }
            QDialog#launchWindowStateDialog QLabel { color: #d7edf5; }
            QDialog#launchWindowStateDialog QLabel[role="pageTitle"] {
                color: #f4fbff;
                font-size: 17pt;
                font-weight: 800;
            }
            QDialog#launchWindowStateDialog QLabel#dialogTitleIcon {
                background: rgba(19, 48, 63, 0.9);
                border: 1px solid #27677d;
                border-radius: 14px;
                color: #3bdcff;
                font-size: 13pt;
                font-weight: 700;
            }
            QDialog#launchWindowStateDialog QToolButton#dialogCloseButton {
                background: transparent;
                color: #c4d4dc;
                border: none;
                font-size: 18pt;
                padding: 2px 8px;
            }
            QDialog#launchWindowStateDialog QToolButton#dialogCloseButton:hover {
                color: #ffffff;
                background: rgba(59, 169, 198, 0.18);
                border-radius: 8px;
            }
            QDialog#launchWindowStateDialog QLabel[role="cardTitle"] {
                color: #f2fbff;
                font-size: 13pt;
                font-weight: 800;
            }
            QDialog#launchWindowStateDialog QLabel[role="cardCaption"] { color: #8fb0bb; }
            QDialog#launchWindowStateDialog QLabel[role="stateFieldLabel"] {
                color: #d7edf5;
                font-size: 10.5pt;
                font-weight: 600;
            }
            QDialog#launchWindowStateDialog QScrollArea#stateDialogScrollArea {
                border: none;
                background: transparent;
            }
            QDialog#launchWindowStateDialog QScrollBar:vertical {
                background: rgba(7, 19, 28, 0.52);
                border: 1px solid rgba(45, 112, 129, 0.58);
                width: 12px;
                margin: 0px;
                border-radius: 6px;
            }
            QDialog#launchWindowStateDialog QScrollBar::handle:vertical {
                background: #2c7c93;
                border-radius: 5px;
                min-height: 56px;
            }
            QDialog#launchWindowStateDialog QScrollBar::add-line:vertical,
            QDialog#launchWindowStateDialog QScrollBar::sub-line:vertical {
                height: 0px;
                border: none;
                background: transparent;
            }
            QDialog#launchWindowStateDialog QTableWidget {
                background: rgba(7, 20, 29, 0.72);
                alternate-background-color: rgba(14, 43, 55, 0.72);
                border: 1px solid #1d6f86;
                border-radius: 8px;
                gridline-color: #1d6f86;
                selection-background-color: rgba(26, 130, 156, 0.55);
                color: #e6f6fb;
            }
            QDialog#launchWindowStateDialog QTableWidget::item {
                color: #e6f6fb;
                padding: 4px 7px;
            }
            QDialog#launchWindowStateDialog QTableWidget::indicator {
                width: 17px;
                height: 17px;
                border-radius: 3px;
                border: 1px solid #36a8bd;
                background: rgba(11, 35, 45, 0.90);
            }
            QDialog#launchWindowStateDialog QTableWidget::indicator:checked {
                background: #2fc3d6;
                image: none;
            }
            QDialog#launchWindowStateDialog QTableWidget::indicator:unchecked:hover {
                border: 1px solid #7eeaff;
                background: rgba(33, 101, 118, 0.65);
            }
            QDialog#launchWindowStateDialog QHeaderView::section {
                background: #0a2b3b;
                color: #f1fbff;
                padding: 8px;
                border: none;
                border-right: 1px solid #1d6f86;
                border-bottom: 1px solid #1d6f86;
                font-weight: 700;
            }
            QDialog#launchWindowStateDialog QDoubleSpinBox,
            QDialog#launchWindowStateDialog QComboBox {
                background: rgba(7, 19, 28, 0.98);
                border: 1px solid #2b6075;
                border-radius: 6px;
                padding: 7px 10px;
                color: #e6f6fb;
            }
            QDialog#launchWindowStateDialog QTableWidget QComboBox {
                min-height: 30px;
                padding: 3px 28px 3px 10px;
            }
            QDialog#launchWindowStateDialog QComboBox QAbstractItemView {
                background: #07141d;
                color: #f3fbff;
                border: 1px solid #1e7892;
                selection-background-color: #153e4d;
                outline: none;
            }
            QDialog#launchWindowStateDialog QComboBox::drop-down {
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 26px;
                border-left: 1px solid #25586a;
            }
            QDialog#launchWindowStateDialog QComboBox::down-arrow {
                image: none;
                width: 0px;
                height: 0px;
            }
            QDialog#launchWindowStateDialog QPushButton {
                color: #d7edf5;
                border: 1px solid #2b6075;
                border-radius: 7px;
                padding: 8px 16px;
                background: rgba(8, 26, 36, 0.82);
            }
            QDialog#launchWindowStateDialog QPushButton:hover {
                border: 1px solid #62d8ea;
                color: #ffffff;
            }
            QDialog#launchWindowStateDialog QPushButton[variant="secondary"] {
                min-width: 108px;
                border-radius: 7px;
                padding: 10px 18px;
                color: #d7edf5;
                border: 1px solid #2b6075;
                background: rgba(8, 26, 36, 0.82);
            }
            QDialog#launchWindowStateDialog QPushButton[variant="secondary"]:hover {
                border: 1px solid #62d8ea;
                color: #ffffff;
            }
            QDialog#launchWindowStateDialog QPushButton[variant="primaryAction"] {
                min-width: 166px;
                border-radius: 7px;
                padding: 10px 22px;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #ff9b35, stop:1 #ff5a22);
                border: 1px solid #ffbd6a;
                color: #ffffff;
                font-size: 11pt;
                font-weight: 800;
            }
            QDialog#launchWindowStateDialog QPushButton[variant="primaryAction"]:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #ffae53, stop:1 #ff6d35);
            }
            """
        )


class LaunchWindowPage(QtWidgets.QWidget):
    def __init__(
        self,
        i18n: I18nManager,
        workspace: ProjectWorkspace,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._i18n = i18n
        self._workspace = workspace
        self._status_role = "statusDisconnected"
        self._last_result_path: Path | None = None

        self._date_fields: dict[str, NoWheelDateTimeEdit] = {}
        self._number_fields: dict[str, NoWheelDoubleSpinBox] = {}
        self._combo_fields: dict[str, NoWheelComboBox] = {}
        self._check_fields: dict[str, QtWidgets.QCheckBox] = {}
        self._constraint_table: QtWidgets.QTableWidget | None = None
        self._gantt_chart: LaunchWindowGanttWidget | None = None
        self._ground_station_table: QtWidgets.QTableWidget | None = None
        self._relay_satellite_table: QtWidgets.QTableWidget | None = None
        self._state_dialog: _LaunchWindowStateDialog | None = None
        self._progress_last_percent = -1
        self._progress_last_update_monotonic = 0.0
        self._ground_station_preset_names = {str(item["name"]) for item in default_ground_station_presets()}
        self._relay_satellite_preset_names = {str(item["name"]) for item in default_relay_satellite_presets()}

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(18)

        self._title_label = QtWidgets.QLabel()
        self._title_label.setProperty("role", "pageTitle")
        root.addWidget(self._title_label)

        self._subtitle_label = QtWidgets.QLabel()
        self._subtitle_label.setProperty("role", "pageBody")
        self._subtitle_label.setWordWrap(True)
        root.addWidget(self._subtitle_label)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        root.addWidget(splitter, 1)

        splitter.addWidget(self._build_left_panel())
        splitter.addWidget(self._build_result_panel())
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 4)
        splitter.setSizes([600, 900])

        self._status_label = QtWidgets.QLabel()
        self._status_label.setWordWrap(True)
        root.addWidget(self._status_label)

        self._state_dialog = _LaunchWindowStateDialog(self)
        self._i18n.language_changed.connect(self.retranslate)
        self.retranslate()
        self.refresh_from_workspace()

    def refresh_from_workspace(self) -> None:
        if self._workspace.current_project is None:
            self._set_config(default_launch_window_config())
            self._set_controls_enabled(False)
            self._clear_results()
            self._set_status("statusDisconnected", "没有活动项目。")
            return

        try:
            payload = self._workspace.load_launch_window_config() or default_launch_window_config()
        except Exception as exc:
            payload = default_launch_window_config()
            self._set_status("statusDisconnected", f"加载发射窗口配置失败：{exc}")
        self._set_config(payload)
        self._set_controls_enabled(True)
        self._refresh_source_labels()
        cached_result = self._load_cached_results(config_from_payload(payload))
        if cached_result is True:
            return
        self._clear_results()
        if cached_result is False:
            self._set_status("statusReady", "已加载发射窗口参数。")

    def _build_left_panel(self) -> QtWidgets.QWidget:
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        canvas = QtWidgets.QWidget()
        scroll.setWidget(canvas)
        layout = QtWidgets.QVBoxLayout(canvas)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        layout.addWidget(self._build_source_card())
        layout.addWidget(self._build_scan_card())
        layout.addWidget(self._build_state_summary_card())
        layout.addWidget(self._build_action_card())
        layout.addStretch(1)
        return scroll

    def _build_source_card(self) -> QtWidgets.QWidget:
        card = self._card()
        layout = QtWidgets.QVBoxLayout(card)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(10)

        self._source_title_label = self._card_title()
        layout.addWidget(self._source_title_label)

        self._source_body_label = QtWidgets.QLabel()
        self._source_body_label.setProperty("role", "cardCaption")
        self._source_body_label.setWordWrap(True)
        layout.addWidget(self._source_body_label)

        self._strategy_path_label = QtWidgets.QLabel()
        self._strategy_path_label.setProperty("role", "cardCaption")
        self._strategy_path_label.setWordWrap(True)
        self._history_path_label = QtWidgets.QLabel()
        self._history_path_label.setProperty("role", "cardCaption")
        self._history_path_label.setWordWrap(True)
        layout.addWidget(self._strategy_path_label)
        layout.addWidget(self._history_path_label)
        return card

    def _build_scan_card(self) -> QtWidgets.QWidget:
        card = self._card()
        layout = QtWidgets.QVBoxLayout(card)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        self._scan_title_label = self._card_title()
        layout.addWidget(self._scan_title_label)

        form = QtWidgets.QFormLayout()
        form.setSpacing(10)
        self._date_fields["start_utc"] = self._date_edit()
        self._date_fields["end_utc"] = self._date_edit()
        form.addRow("发射开始时刻 (北京时间)", self._date_fields["start_utc"])
        form.addRow("发射结束时刻 (北京时间)", self._date_fields["end_utc"])
        for key, label, value, minimum, maximum, step in (
            ("rocket_flight_time_s", "火箭飞行时间 (s)", 2134.4121, 0.0, 20000.0, 0.1),
            ("sample_step_min", "扫描步长 (min)", 10.0, 1.0, 1440.0, 1.0),
        ):
            field = self._double_spin(value, minimum, maximum, step, 3)
            self._number_fields[key] = field
            form.addRow(label, field)
        layout.addLayout(form)
        return card

    def _build_constraint_card(self) -> QtWidgets.QWidget:
        card = self._card()
        layout = QtWidgets.QVBoxLayout(card)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        self._constraint_title_label = self._card_title()
        layout.addWidget(self._constraint_title_label)

        help_label = QtWidgets.QLabel(
            "每行只定义时间段和条件类型；阈值统一使用上方全局参数。开始/结束可填绝对航时（单位 min），也可填参数表达式。"
            "参数来自变轨策略：T1_start 表示第一次变轨开始时间（沉底开始），T1_end 表示第一次变轨结束时间；"
            "T2_start/T2_end 以此类推。用例：1074、T1_start-180、T1_end+60、T2_start。"
        )
        help_label.setProperty("role", "cardCaption")
        help_label.setWordWrap(True)
        layout.addWidget(help_label)

        self._constraint_table = QtWidgets.QTableWidget(0, 5)
        self._constraint_table.setHorizontalHeaderLabels(
            ["启用", "条件", "开始/min或参数", "结束/min或参数", "条件类型"]
        )
        self._constraint_table.setAlternatingRowColors(True)
        self._constraint_table.verticalHeader().setVisible(False)
        self._constraint_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self._constraint_table.setHorizontalScrollMode(QtWidgets.QAbstractItemView.ScrollMode.ScrollPerPixel)
        self._constraint_table.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._constraint_table.setMinimumHeight(390)
        self._constraint_table.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Fixed)
        install_table_edit_delegate(self._constraint_table)
        self._configure_constraint_table_columns()
        layout.addWidget(self._constraint_table)

        button_row = QtWidgets.QHBoxLayout()
        button_row.setSpacing(10)
        self._add_constraint_button = QtWidgets.QPushButton("新增条件")
        self._add_constraint_button.clicked.connect(self._append_constraint_row)
        self._duplicate_constraint_button = QtWidgets.QPushButton("复制子条件")
        self._duplicate_constraint_button.clicked.connect(self._duplicate_selected_constraint_row)
        self._delete_constraint_button = QtWidgets.QPushButton("删除")
        self._delete_constraint_button.clicked.connect(self._delete_selected_constraint_rows)
        self._reset_constraints_button = QtWidgets.QPushButton("恢复默认表格")
        self._reset_constraints_button.clicked.connect(lambda: self._set_constraint_rows(default_constraint_rows()))
        for button in (
            self._add_constraint_button,
            self._duplicate_constraint_button,
            self._delete_constraint_button,
            self._reset_constraints_button,
        ):
            button_row.addWidget(button)
        button_row.addStretch(1)
        layout.addLayout(button_row)
        return card

    def _build_state_summary_card(self) -> QtWidgets.QWidget:
        card = self._card()
        layout = QtWidgets.QVBoxLayout(card)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(10)

        self._state_title_label = self._card_title()
        layout.addWidget(self._state_title_label)
        self._state_summary_label = QtWidgets.QLabel("--")
        self._state_summary_label.setProperty("role", "pageBody")
        self._state_summary_label.setWordWrap(True)
        layout.addWidget(self._state_summary_label)
        self._state_assets_label = QtWidgets.QLabel("--")
        self._state_assets_label.setProperty("role", "cardCaption")
        self._state_assets_label.setWordWrap(True)
        layout.addWidget(self._state_assets_label)
        self._state_details_label = QtWidgets.QLabel("--")
        self._state_details_label.setProperty("role", "cardCaption")
        self._state_details_label.setWordWrap(True)
        layout.addWidget(self._state_details_label)
        self._edit_state_button = QtWidgets.QPushButton("状态设置")
        self._edit_state_button.setProperty("variant", "secondary")
        self._edit_state_button.clicked.connect(self._open_state_settings_dialog)
        layout.addWidget(self._edit_state_button, 0, QtCore.Qt.AlignmentFlag.AlignLeft)
        return card

    def _build_tracking_asset_card(self) -> QtWidgets.QWidget:
        card = self._card()
        layout = QtWidgets.QVBoxLayout(card)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        title = self._card_title()
        title.setText("测控资源")
        layout.addWidget(title)

        help_label = QtWidgets.QLabel(
            "地面站可从预置站启用，也可人工增添。中继星默认按倾角 0° 的地球同步轨道处理；当前窗口计算使用其经纬高位置，姿态约束说明会随配置一起保存。"
        )
        help_label.setProperty("role", "cardCaption")
        help_label.setWordWrap(True)
        layout.addWidget(help_label)

        self._ground_station_table = self._asset_table(with_enabled=True)
        layout.addWidget(self._ground_station_table)
        custom_ground_buttons = QtWidgets.QHBoxLayout()
        self._add_custom_ground_button = QtWidgets.QPushButton("新增地面站")
        self._add_custom_ground_button.clicked.connect(
            lambda: self._append_asset_row(
                self._ground_station_table,
                {"enabled": True, "name": "自定义地面站", "longitude_deg": 0.0, "latitude_deg": 0.0, "altitude_m": 0.0},
            )
        )
        self._delete_custom_ground_button = QtWidgets.QPushButton("删除地面站")
        self._delete_custom_ground_button.clicked.connect(lambda: self._delete_selected_rows(self._ground_station_table))
        custom_ground_buttons.addWidget(self._add_custom_ground_button)
        custom_ground_buttons.addWidget(self._delete_custom_ground_button)
        custom_ground_buttons.addStretch(1)
        layout.addLayout(custom_ground_buttons)

        ground_visibility_title = self._section_label("地面站测控可见条件")
        layout.addWidget(ground_visibility_title)
        ground_visibility_form = QtWidgets.QGridLayout()
        self._prepare_state_form_grid(ground_visibility_form)
        self._number_fields["ground_station_min_elevation_deg"] = self._double_spin(5.0, -90.0, 90.0, 0.5, 2)
        self._number_fields["ground_station_max_theta_st_deg"] = self._double_spin(70.0, 0.0, 180.0, 0.5, 2)
        for field in (
            self._number_fields["ground_station_min_elevation_deg"],
            self._number_fields["ground_station_max_theta_st_deg"],
        ):
            self._prepare_state_numeric_field(field)
        ground_visibility_form.addWidget(self._state_field_label("仰角最小值 (deg)"), 0, 0)
        ground_visibility_form.addWidget(self._number_fields["ground_station_min_elevation_deg"], 0, 1)
        ground_visibility_form.addWidget(self._state_field_label("天线角最大值 (deg)"), 0, 2)
        ground_visibility_form.addWidget(self._number_fields["ground_station_max_theta_st_deg"], 0, 3)
        layout.addLayout(ground_visibility_form)

        self._relay_satellite_table = self._asset_table(with_enabled=True)
        layout.addWidget(self._relay_satellite_table)

        relay_note = QtWidgets.QLabel("默认 GEO 赤道轨道；中继星姿态说明：+Z 指向地球，+X 指向卫星速度方向。")
        relay_note.setProperty("role", "cardCaption")
        relay_note.setWordWrap(True)
        layout.addWidget(relay_note)
        custom_relay_buttons = QtWidgets.QHBoxLayout()
        self._add_custom_relay_button = QtWidgets.QPushButton("新增中继星")
        self._add_custom_relay_button.clicked.connect(
            lambda: self._append_asset_row(
                self._relay_satellite_table,
                {"enabled": True, "name": "自定义中继星", "longitude_deg": 0.0, "latitude_deg": 0.0, "altitude_m": 35786000.0},
            )
        )
        self._delete_custom_relay_button = QtWidgets.QPushButton("删除中继星")
        self._delete_custom_relay_button.clicked.connect(lambda: self._delete_selected_rows(self._relay_satellite_table))
        custom_relay_buttons.addWidget(self._add_custom_relay_button)
        custom_relay_buttons.addWidget(self._delete_custom_relay_button)
        custom_relay_buttons.addStretch(1)
        layout.addLayout(custom_relay_buttons)

        relay_visibility_title = self._section_label("中继星测控可见条件")
        layout.addWidget(relay_visibility_title)
        relay_visibility_form = QtWidgets.QGridLayout()
        self._prepare_state_form_grid(relay_visibility_form)
        self._number_fields["relay_alpha_abs_max_deg"] = self._double_spin(20.0, 0.0, 180.0, 0.5, 2)
        self._number_fields["relay_beta_abs_max_deg"] = self._double_spin(40.0, 0.0, 180.0, 0.5, 2)
        self._number_fields["relay_max_theta_st_deg"] = self._double_spin(80.0, 0.0, 180.0, 0.5, 2)
        for field in (
            self._number_fields["relay_alpha_abs_max_deg"],
            self._number_fields["relay_beta_abs_max_deg"],
            self._number_fields["relay_max_theta_st_deg"],
        ):
            self._prepare_state_numeric_field(field)
        relay_visibility_form.addWidget(self._state_field_label("alpha 最大值 (deg)"), 0, 0)
        relay_visibility_form.addWidget(self._number_fields["relay_alpha_abs_max_deg"], 0, 1)
        relay_visibility_form.addWidget(self._state_field_label("beta 最大值 (deg)"), 0, 2)
        relay_visibility_form.addWidget(self._number_fields["relay_beta_abs_max_deg"], 0, 3)
        relay_visibility_form.addWidget(self._state_field_label("天线覆盖角最大值 (deg)"), 1, 0)
        relay_visibility_form.addWidget(self._number_fields["relay_max_theta_st_deg"], 1, 1)
        layout.addLayout(relay_visibility_form)

        burn_visibility_title = self._section_label("点火期间约束")
        layout.addWidget(burn_visibility_title)
        burn_visibility_form = QtWidgets.QGridLayout()
        self._prepare_state_form_grid(burn_visibility_form)
        self._number_fields["burn_sun_angle_max_deg"] = self._double_spin(90.0, 0.0, 180.0, 0.5, 2)
        self._prepare_state_numeric_field(self._number_fields["burn_sun_angle_max_deg"])
        self._combo_fields["burn_sun_axis"] = self._burn_sun_axis_combo(BURN_SUN_AXIS_MINUS_Z)
        self._prepare_state_combo_field(self._combo_fields["burn_sun_axis"])
        burn_visibility_form.addWidget(self._state_field_label("点火期间 θs 最大值 (deg)"), 0, 0)
        burn_visibility_form.addWidget(self._number_fields["burn_sun_angle_max_deg"], 0, 1)
        burn_visibility_form.addWidget(self._state_field_label("帆板方向"), 0, 2)
        burn_visibility_form.addWidget(self._combo_fields["burn_sun_axis"], 0, 3)
        layout.addLayout(burn_visibility_form)
        return card

    def _build_action_card(self) -> QtWidgets.QWidget:
        card = self._card()
        layout = QtWidgets.QVBoxLayout(card)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(10)

        self._action_title_label = self._card_title()
        layout.addWidget(self._action_title_label)
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(10)
        self._reload_button = QtWidgets.QPushButton("重新加载")
        self._reload_button.clicked.connect(self.refresh_from_workspace)
        self._save_button = QtWidgets.QPushButton("保存参数")
        self._save_button.clicked.connect(self.save_config)
        self._calculate_button = QtWidgets.QPushButton("计算发射窗口")
        self._calculate_button.setProperty("variant", "primaryAction")
        self._calculate_button.clicked.connect(self.calculate_windows)
        self._save_results_button = QtWidgets.QPushButton("导出结果")
        self._save_results_button.clicked.connect(self._export_result_csv)
        self._save_results_button.setEnabled(False)
        for button in (
            self._reload_button,
            self._save_button,
            self._calculate_button,
            self._save_results_button,
        ):
            row.addWidget(button)
        row.addStretch(1)
        layout.addLayout(row)

        self._progress_bar = QtWidgets.QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setTextVisible(True)
        layout.addWidget(self._progress_bar)
        return card

    def _build_result_panel(self) -> QtWidgets.QWidget:
        panel = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        summary_card = self._card()
        summary_layout = QtWidgets.QGridLayout(summary_card)
        summary_layout.setContentsMargins(18, 18, 18, 18)
        summary_layout.setHorizontalSpacing(16)
        summary_layout.setVerticalSpacing(8)
        self._summary_title_label = self._card_title()
        summary_layout.addWidget(self._summary_title_label, 0, 0, 1, 4)
        self._summary_values: dict[str, QtWidgets.QLabel] = {}
        for index, (key, caption) in enumerate(
            (
                ("window_count", "窗口数"),
                ("sample_count", "扫描样本"),
                ("pass_count", "通过样本"),
                ("sample_path", "样本文件"),
            ),
            start=1,
        ):
            label = QtWidgets.QLabel(caption)
            label.setProperty("role", "cardCaption")
            value = QtWidgets.QLabel("--")
            value.setProperty("role", "pageBody")
            value.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
            row = (index + 1) // 2
            col = 0 if index % 2 else 2
            summary_layout.addWidget(label, row, col)
            summary_layout.addWidget(value, row, col + 1)
            self._summary_values[key] = value
        layout.addWidget(summary_card)

        self._result_table = QtWidgets.QTableWidget(0, 9)
        self._result_table.setProperty("role", "card")
        self._result_table.setAlternatingRowColors(True)
        self._result_table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self._result_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self._result_table.verticalHeader().setVisible(False)
        self._result_table.horizontalHeader().setStretchLastSection(True)
        self._result_table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self._result_table, 1)

        self._gantt_chart = LaunchWindowGanttWidget()
        layout.addWidget(self._gantt_chart, 1)
        return panel

    def save_config(self) -> Path | None:
        if self._workspace.current_project is None:
            self._set_status("statusDisconnected", "没有活动项目。")
            return None
        try:
            path = self._workspace.save_launch_window_config(self.config_payload())
        except Exception as exc:
            self._set_status("statusDisconnected", f"保存发射窗口参数失败：{exc}")
            return None
        self._set_status("statusReady", f"已保存发射窗口参数：{path}")
        return path

    def _open_state_settings_dialog(self) -> None:
        if self._state_dialog is None:
            return
        original_payload = self.config_payload()
        result = self._state_dialog.exec()
        if result != QtWidgets.QDialog.DialogCode.Accepted:
            self._set_config(original_payload)
            return
        self._refresh_state_summary()
        self._set_status("statusReady", "状态设置已应用；保存参数或计算后写入项目。")

    def calculate_windows(self) -> None:
        if self._workspace.current_project is None:
            self._set_status("statusDisconnected", "没有活动项目。")
            return
        if self.save_config() is None:
            return

        strategy_path = self._workspace.maneuver_strategy_path()
        history_path = self._workspace.data_dir() / "full_orbit_history.csv"
        try:
            strategy = self._workspace.load_maneuver_strategy()
            if strategy is None:
                raise FileNotFoundError(strategy_path)
        except Exception as exc:
            self._set_status("statusDisconnected", f"加载变轨策略失败：{exc}")
            return

        app = QtWidgets.QApplication.instance()
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.CursorShape.WaitCursor)
        self._set_status("statusLoading", "正在计算发射窗口。")
        self._progress_bar.setFormat("%p%")
        self._progress_bar.setValue(0)
        self._progress_last_percent = -1
        self._progress_last_update_monotonic = 0.0
        if app is not None:
            app.processEvents()

        try:
            config = config_from_payload(self.config_payload())
            windows, samples = compute_launch_windows(
                orbit_history_csv=history_path,
                maneuver_strategy=strategy,
                config=config,
                assets=tracking_assets_from_config(config),
                progress_callback=self._on_calculation_progress,
            )
            sample_path = self._write_sample_csv(samples)
        except Exception as exc:
            self._set_status("statusDisconnected", f"发射窗口计算失败：{exc}")
            return
        finally:
            if QtWidgets.QApplication.overrideCursor() is not None:
                QtWidgets.QApplication.restoreOverrideCursor()

        self._show_calculation_results(windows, samples, sample_path)
        result_path = self._save_result_csv()
        self._progress_bar.setValue(100)
        if result_path is None:
            self._set_status("statusReady", f"发射窗口计算完成：{len(windows)} 个窗口。")
        else:
            self._set_status("statusReady", f"发射窗口计算完成：{len(windows)} 个窗口，已自动保存结果 CSV：{result_path}")

    def config_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        for key, field in self._date_fields.items():
            payload[key] = self._datetime_edit_to_utc(field)
        for key, field in self._number_fields.items():
            payload[key] = float(field.value())
        for key, field in self._combo_fields.items():
            payload[key] = str(field.currentData())
        for key, checkbox in self._check_fields.items():
            payload[key] = bool(checkbox.isChecked())
        payload["constraint_rows"] = self._constraint_rows_payload()
        ground_rows = self._asset_rows_payload(self._ground_station_table, asset_type="ground")
        payload["ground_station_presets"] = [
            row for row in ground_rows if str(row.get("name", "")) in self._ground_station_preset_names
        ]
        payload["custom_ground_stations"] = [
            row for row in ground_rows if str(row.get("name", "")) not in self._ground_station_preset_names
        ]
        relay_rows = self._asset_rows_payload(self._relay_satellite_table, asset_type="relay")
        payload["relay_satellite_presets"] = [
            row for row in relay_rows if str(row.get("name", "")) in self._relay_satellite_preset_names
        ]
        payload["custom_relay_satellites"] = [
            row for row in relay_rows if str(row.get("name", "")) not in self._relay_satellite_preset_names
        ]
        return payload

    def _set_config(self, payload: dict[str, Any]) -> None:
        config = config_from_payload(payload)
        for key in ("start_utc", "end_utc"):
            self._date_fields[key].blockSignals(True)
            self._date_fields[key].setDateTime(self._utc_to_qdatetime(getattr(config, key)))
            self._date_fields[key].blockSignals(False)
        for key, field in self._number_fields.items():
            field.blockSignals(True)
            field.setValue(float(getattr(config, key)))
            field.blockSignals(False)
        for key, field in self._combo_fields.items():
            field.blockSignals(True)
            index = field.findData(str(getattr(config, key)))
            field.setCurrentIndex(index if index >= 0 else 0)
            field.blockSignals(False)
        for key, checkbox in self._check_fields.items():
            checkbox.blockSignals(True)
            checkbox.setChecked(bool(getattr(config, key)))
            checkbox.blockSignals(False)
        self._set_constraint_rows(config.constraint_rows)
        self._set_asset_rows(
            self._ground_station_table,
            [*config.ground_station_presets, *config.custom_ground_stations],
            with_enabled=True,
        )
        self._set_asset_rows(
            self._relay_satellite_table,
            [*config.relay_satellite_presets, *config.custom_relay_satellites],
            with_enabled=True,
        )
        self._refresh_state_summary()

    def _refresh_state_summary(self) -> None:
        if not hasattr(self, "_state_summary_label"):
            return
        constraint_rows = self._constraint_rows_payload()
        enabled_constraints = sum(1 for row in constraint_rows if bool(row.get("enabled", True)))
        ground_rows = self._asset_rows_payload(self._ground_station_table, asset_type="ground")
        relay_rows = self._asset_rows_payload(self._relay_satellite_table, asset_type="relay")
        enabled_ground = sum(1 for row in ground_rows if bool(row.get("enabled", True)))
        enabled_relay = sum(1 for row in relay_rows if bool(row.get("enabled", True)))
        ground_names = [str(row.get("name", "")).strip() for row in ground_rows if bool(row.get("enabled", True))]
        relay_names = [str(row.get("name", "")).strip() for row in relay_rows if bool(row.get("enabled", True))]
        self._state_summary_label.setText(
            f"启用条件 {enabled_constraints}/{len(constraint_rows)} · 地面站 {enabled_ground}/{len(ground_rows)} · 中继星 {enabled_relay}/{len(relay_rows)}"
        )
        self._state_assets_label.setText(
            f"使用地面站：{', '.join(name for name in ground_names if name) or '无'}\n"
            f"使用中继星：{', '.join(name for name in relay_names if name) or '无'}"
        )
        ground_elevation = self._number_fields.get("ground_station_min_elevation_deg")
        burn_angle = self._number_fields.get("burn_sun_angle_max_deg")
        if ground_elevation is None or burn_angle is None:
            self._state_details_label.setText("--")
            return
        axis = self._combo_fields.get("burn_sun_axis")
        axis_text = axis.currentText() if axis is not None else "--"
        self._state_details_label.setText(
            f"地面站最低仰角 {ground_elevation.value():.2f} deg · 点火 θs ≤ {burn_angle.value():.2f} deg · {axis_text}"
        )

    def _set_constraint_rows(self, rows: list[dict[str, Any]]) -> None:
        if self._constraint_table is None:
            return
        self._constraint_table.setRowCount(0)
        for row_payload in rows:
            self._insert_constraint_row(row_payload)

    def _append_constraint_row(self) -> None:
        self._insert_constraint_row(
            {
                "enabled": True,
                "name": "自定义条件",
                "start_min": 0.0,
                "end_min": 0.0,
                "condition_type": CONSTRAINT_TYPE_GROUND_VISIBLE,
            }
        )

    def _duplicate_selected_constraint_row(self) -> None:
        if self._constraint_table is None:
            return
        row = self._constraint_table.currentRow()
        if row < 0:
            return
        self._insert_constraint_row(self._constraint_row_payload(row), row + 1)

    def _delete_selected_constraint_rows(self) -> None:
        if self._constraint_table is None:
            return
        rows = sorted({index.row() for index in self._constraint_table.selectedIndexes()}, reverse=True)
        if not rows and self._constraint_table.currentRow() >= 0:
            rows = [self._constraint_table.currentRow()]
        for row in rows:
            self._constraint_table.removeRow(row)

    def _insert_constraint_row(self, row_payload: dict[str, Any], row: int | None = None) -> None:
        if self._constraint_table is None:
            return
        if row is None:
            row = self._constraint_table.rowCount()
        self._constraint_table.insertRow(row)
        enabled_item = QtWidgets.QTableWidgetItem()
        enabled_item.setFlags(enabled_item.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable)
        enabled_item.setCheckState(
            QtCore.Qt.CheckState.Checked if bool(row_payload.get("enabled", True)) else QtCore.Qt.CheckState.Unchecked
        )
        enabled_item.setTextAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self._constraint_table.setItem(row, 0, enabled_item)
        values = [
            str(row_payload.get("name", "")),
            self._format_constraint_time_cell(row_payload.get("start_min", 0.0)),
            self._format_constraint_time_cell(row_payload.get("end_min", 0.0)),
        ]
        for offset, value in enumerate(values, start=1):
            item = QtWidgets.QTableWidgetItem(value)
            if offset in {2, 3}:
                item.setTextAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
            self._constraint_table.setItem(row, offset, item)
        type_box = self._constraint_type_combo(str(row_payload.get("condition_type", CONSTRAINT_TYPE_GROUND_VISIBLE)))
        self._constraint_table.setCellWidget(row, 4, type_box)
        self._constraint_table.setRowHeight(row, max(self._constraint_table.rowHeight(row), 42))

    def _constraint_rows_payload(self) -> list[dict[str, Any]]:
        if self._constraint_table is None:
            return []
        return [self._constraint_row_payload(row) for row in range(self._constraint_table.rowCount())]

    def _constraint_row_payload(self, row: int) -> dict[str, Any]:
        assert self._constraint_table is not None
        enabled_item = self._constraint_table.item(row, 0)
        return {
            "enabled": enabled_item is None or enabled_item.checkState() == QtCore.Qt.CheckState.Checked,
            "name": self._table_text(self._constraint_table, row, 1),
            "start_min": self._constraint_time_payload(self._table_text(self._constraint_table, row, 2)),
            "end_min": self._constraint_time_payload(self._table_text(self._constraint_table, row, 3)),
            "condition_type": self._combo_value(self._constraint_table.cellWidget(row, 4)),
            "operator": "",
            "threshold": None,
        }

    def _sample_csv_path(self) -> Path:
        return self._workspace.data_dir() / "launch_window_samples.csv"

    def _sample_meta_path(self) -> Path:
        return self._workspace.data_dir() / "launch_window_samples.meta.json"

    def _result_csv_path(self) -> Path:
        return self._workspace.data_dir() / "launch_window_results.csv"

    def _load_cached_results(self, config: Any) -> bool | None:
        path = self._sample_csv_path()
        if not path.exists():
            return False
        if not self._cached_samples_match_current_inputs(config):
            self._set_status("statusReady", "已有发射窗口缓存已过期，请重新计算。")
            return False
        try:
            samples = self._read_sample_csv(path)
            windows = merge_launch_window_samples(samples, config)
        except Exception as exc:
            self._set_status("statusDisconnected", f"加载已有发射窗口结果失败：{exc}")
            return None
        self._show_calculation_results(windows, samples, path)
        self._progress_bar.setValue(100)
        self._progress_bar.setFormat("已加载")
        self._set_status("statusReady", f"已加载已有发射窗口计算结果：{len(windows)} 个窗口。")
        return True

    def _cached_samples_match_current_inputs(self, config: Any) -> bool:
        meta_path = self._sample_meta_path()
        if not meta_path.exists():
            return False
        try:
            metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            return False
        if not isinstance(metadata, dict):
            return False
        return metadata == self._sample_cache_metadata(config)

    def _sample_cache_metadata(self, config: Any) -> dict[str, str]:
        strategy_path = self._workspace.maneuver_strategy_path()
        history_path = self._workspace.data_dir() / "full_orbit_history.csv"
        return {
            "config_hash": _stable_hash(_hashable_dataclass(config)),
            "maneuver_strategy_hash": _file_hash(strategy_path),
            "orbit_history_hash": _file_hash(history_path),
        }

    def _show_calculation_results(self, windows: list[Any], samples: list[dict[str, Any]], sample_path: Path) -> None:
        self._last_result_path = self._result_csv_path()
        self._set_result_rows(windows)
        self._save_results_button.setEnabled(self._result_table.columnCount() > 0)
        if self._gantt_chart is not None:
            self._gantt_chart.set_samples(samples)
        self._summary_values["window_count"].setText(str(len(windows)))
        self._summary_values["sample_count"].setText(str(len(samples)))
        self._summary_values["pass_count"].setText(str(sum(1 for item in samples if item["ok"])))
        self._summary_values["sample_path"].setText(str(sample_path))

    def _read_sample_csv(self, path: Path) -> list[dict[str, Any]]:
        import csv
        import json

        samples: list[dict[str, Any]] = []
        numeric_columns = {
            "first_orbit_shadow_min",
            "no_shadow_period_shadow_min",
            "separation_shadow_min",
            "longest_shadow_min",
            "min_burn_sun_margin_deg",
            "max_tracking_gap_min",
            "inclination_deg",
        }
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            for raw_row in reader:
                sample: dict[str, Any] = {
                    "launch_utc": str(raw_row.get("launch_utc", "")).strip(),
                    "t0_utc": str(raw_row.get("t0_utc", "")).strip(),
                    "ok": self._parse_bool(raw_row.get("ok")),
                    "failure": str(raw_row.get("failure", "")).strip(),
                }
                for column in numeric_columns:
                    raw_value = raw_row.get(column)
                    if column == "longest_shadow_min" and not str(raw_value or "").strip():
                        continue
                    sample[column] = self._to_float(raw_value)
                constraint_results = str(raw_row.get("constraint_results", "")).strip()
                if constraint_results:
                    parsed_results = json.loads(constraint_results)
                    if isinstance(parsed_results, list):
                        sample["constraint_results"] = parsed_results
                if sample["launch_utc"]:
                    samples.append(sample)
        return samples

    @staticmethod
    def _parse_bool(value: object) -> bool:
        text = str(value).strip().lower()
        return text in {"1", "true", "yes", "y", "通过", "pass", "passed"}

    def _write_sample_csv(self, samples: list[dict[str, Any]]) -> Path:
        path = self._sample_csv_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        columns = [
            "launch_utc",
            "t0_utc",
            "rocket_flight_time_s",
            "ok",
            "failure",
            "first_orbit_shadow_min",
            "no_shadow_period_shadow_min",
            "separation_shadow_min",
            "longest_shadow_min",
            "min_burn_sun_margin_deg",
            "max_tracking_gap_min",
            "inclination_deg",
            "constraint_results",
        ]
        with path.open("w", encoding="utf-8", newline="") as handle:
            import csv

            writer = csv.DictWriter(handle, fieldnames=columns)
            writer.writeheader()
            for sample in samples:
                row = {key: sample.get(key, "") for key in columns}
                row["rocket_flight_time_s"] = self._number_fields["rocket_flight_time_s"].value()
                row["constraint_results"] = json.dumps(
                    sample.get("constraint_results", []),
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                writer.writerow(row)
        try:
            config = config_from_payload(self.config_payload())
            self._sample_meta_path().write_text(
                json.dumps(self._sample_cache_metadata(config), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            self._sample_meta_path().unlink(missing_ok=True)
        return path

    def _save_result_csv(self, path: Path | None = None, *, announce: bool = False) -> Path | None:
        if self._workspace.current_project is None:
            self._set_status("statusDisconnected", "没有活动项目。")
            return None
        if self._result_table.columnCount() <= 0:
            self._set_status("statusDisconnected", "没有可保存的发射窗口计算结果。")
            return None
        if path is None:
            path = self._result_csv_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            import csv

            headers = [
                self._result_table.horizontalHeaderItem(column).text()
                if self._result_table.horizontalHeaderItem(column) is not None
                else f"column_{column + 1}"
                for column in range(self._result_table.columnCount())
            ]
            with path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(headers)
                for row in range(self._result_table.rowCount()):
                    writer.writerow(
                        [
                            self._table_text(self._result_table, row, column)
                            for column in range(self._result_table.columnCount())
                        ]
                    )
        except Exception as exc:
            self._set_status("statusDisconnected", f"保存结果 CSV 失败：{exc}")
            return None
        self._last_result_path = path
        if announce:
            self._set_status("statusReady", f"已导出结果 CSV：{path}")
        return path

    def _export_result_csv(self) -> Path | None:
        if self._workspace.current_project is None:
            self._set_status("statusDisconnected", "没有活动项目。")
            return None
        default_path = self._last_result_path or self._result_csv_path()
        selected, _filter = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "导出发射窗口结果 CSV",
            str(default_path),
            "CSV 文件 (*.csv);;所有文件 (*)",
        )
        if not selected:
            return None
        path = Path(selected)
        if path.suffix.lower() != ".csv":
            path = path.with_suffix(".csv")
        return self._save_result_csv(path, announce=True)

    def _set_result_rows(self, windows: list[Any]) -> None:
        headers = [
            "窗口前沿 (北京时间)",
            "窗口后沿 (北京时间)",
            "长度/min",
            "入轨 T0 前沿 (北京时间)",
            "第一圈地影/min",
            "窗口前沿轨道最长地影/min",
            "窗口后沿轨道最长地影/min",
            "窗口前沿限制条件",
            "窗口后沿限制条件",
        ]
        self._result_table.setColumnCount(len(headers))
        self._result_table.setHorizontalHeaderLabels(headers)
        self._result_table.setRowCount(0)
        for window in windows:
            row = self._result_table.rowCount()
            self._result_table.insertRow(row)
            values = [
                self._format_beijing(window.window_start_utc),
                self._format_beijing(window.window_end_utc),
                f"{window.duration_min:.1f}",
                self._format_beijing_t0(window.window_start_utc),
                f"{window.first_orbit_shadow_min:.1f}",
                f"{window.window_start_longest_shadow_min:.1f}",
                f"{window.window_end_longest_shadow_min:.1f}",
                str(getattr(window, "window_start_constraint", "") or "--"),
                str(getattr(window, "window_end_constraint", "") or "--"),
            ]
            self._set_table_values(self._result_table, row, values)

    def _clear_results(self) -> None:
        self._result_table.setRowCount(0)
        if self._gantt_chart is not None:
            self._gantt_chart.clear()
        for value in self._summary_values.values():
            value.setText("--")
        self._last_result_path = None
        self._save_results_button.setEnabled(False)

    def _set_controls_enabled(self, enabled: bool) -> None:
        for widget in (
            *self._date_fields.values(),
            *self._number_fields.values(),
            *self._combo_fields.values(),
            *self._check_fields.values(),
            self._constraint_table,
            self._add_constraint_button,
            self._duplicate_constraint_button,
            self._delete_constraint_button,
            self._reset_constraints_button,
            self._ground_station_table,
            self._relay_satellite_table,
            self._add_custom_ground_button,
            self._delete_custom_ground_button,
            self._add_custom_relay_button,
            self._delete_custom_relay_button,
            self._edit_state_button,
            self._reload_button,
            self._save_button,
            self._calculate_button,
            self._save_results_button,
            self._progress_bar,
        ):
            if widget is not None:
                widget.setEnabled(enabled)

    def _refresh_source_labels(self) -> None:
        if self._workspace.current_project is None:
            self._strategy_path_label.setText("变轨策略：--")
            self._history_path_label.setText("变轨结果：--")
            return
        self._strategy_path_label.setText(f"变轨策略：{self._workspace.maneuver_strategy_path()}")
        self._history_path_label.setText(f"变轨结果：{self._workspace.data_dir() / 'full_orbit_history.csv'}")

    def _asset_table(self, *, with_enabled: bool) -> QtWidgets.QTableWidget:
        column_count = 5 if with_enabled else 4
        table = QtWidgets.QTableWidget(0, column_count)
        table.setHorizontalHeaderLabels(
            ["启用", "名称", "经度/deg", "纬度/deg", "高度/m"]
            if with_enabled
            else ["名称", "经度/deg", "纬度/deg", "高度/m"]
        )
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(40)
        table.horizontalHeader().setStretchLastSection(False)
        table.horizontalHeader().setMinimumHeight(42)
        table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Fixed)
        widths = (58, 220, 150, 150, 190) if with_enabled else (220, 150, 150, 190)
        for column, width in enumerate(widths):
            table.setColumnWidth(column, width)
        table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        table.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        table.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Fixed)
        table.setMaximumWidth(sum(widths) + table.frameWidth() * 2 + 4)
        table.setMinimumHeight(172)
        install_table_edit_delegate(table)
        return table

    def _section_label(self, text: str) -> QtWidgets.QLabel:
        label = QtWidgets.QLabel(text)
        label.setProperty("role", "cardCaption")
        return label

    @staticmethod
    def _state_field_label(text: str) -> QtWidgets.QLabel:
        label = QtWidgets.QLabel(text)
        label.setProperty("role", "stateFieldLabel")
        label.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
        label.setMinimumWidth(178)
        return label

    @staticmethod
    def _prepare_state_numeric_field(field: QtWidgets.QWidget) -> None:
        field.setMinimumHeight(42)
        field.setFixedWidth(132)
        field.setSizePolicy(QtWidgets.QSizePolicy.Policy.Fixed, QtWidgets.QSizePolicy.Policy.Fixed)

    @staticmethod
    def _prepare_state_combo_field(field: QtWidgets.QWidget) -> None:
        field.setMinimumHeight(42)
        field.setFixedWidth(210)
        field.setSizePolicy(QtWidgets.QSizePolicy.Policy.Fixed, QtWidgets.QSizePolicy.Policy.Fixed)

    @staticmethod
    def _prepare_state_form_grid(grid: QtWidgets.QGridLayout) -> None:
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(14)
        grid.setColumnMinimumWidth(0, 178)
        grid.setColumnMinimumWidth(2, 178)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)

    def _constraint_type_combo(self, current_value: str) -> QtWidgets.QComboBox:
        combo = _StateComboBox()
        labels: list[tuple[str, str]] = []
        for value, label, tooltip in (
            (CONSTRAINT_TYPE_NO_SHADOW, "无地影", "转移轨道 - 无地影"),
            (CONSTRAINT_TYPE_GROUND_VISIBLE, "地面站可见", "点火测控 - 地面站可见"),
            (CONSTRAINT_TYPE_RELAY_VISIBLE, "中继星可见", "远点测控 - 中继星可见"),
            (CONSTRAINT_TYPE_GROUND_OR_RELAY_VISIBLE, "站/星可见", "地面站或中继星可见"),
            (CONSTRAINT_TYPE_THETA_S, "太阳角-帆板", "太阳角与帆板夹角"),
        ):
            combo.addItem(label, value)
            combo.setItemData(combo.count() - 1, tooltip, QtCore.Qt.ItemDataRole.ToolTipRole)
            labels.append((label, tooltip))
        index = combo.findData(current_value)
        combo.setCurrentIndex(index if index >= 0 else 0)
        combo.setSizeAdjustPolicy(QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        combo.setMinimumContentsLength(max(len(label) for label, _tooltip in labels) + 1)
        combo.setMinimumWidth(150)
        combo.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Fixed)
        combo.setToolTip(combo.currentData(QtCore.Qt.ItemDataRole.ToolTipRole) or combo.currentText())
        combo.currentIndexChanged.connect(
            lambda _index, box=combo: box.setToolTip(
                box.currentData(QtCore.Qt.ItemDataRole.ToolTipRole) or box.currentText()
            )
        )
        return combo

    def _configure_constraint_table_columns(self) -> None:
        if self._constraint_table is None:
            return
        header = self._constraint_table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setMinimumSectionSize(64)
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(4, QtWidgets.QHeaderView.ResizeMode.Fixed)
        header.setMinimumHeight(44)
        self._constraint_table.verticalHeader().setDefaultSectionSize(42)
        self._constraint_table.setColumnWidth(0, 68)
        self._constraint_table.setColumnWidth(1, 240)
        self._constraint_table.setColumnWidth(2, 170)
        self._constraint_table.setColumnWidth(3, 170)
        self._constraint_table.setColumnWidth(4, 180)

    @staticmethod
    def _burn_sun_axis_combo(current_value: str) -> NoWheelComboBox:
        combo = _StateComboBox()
        combo.addItem("卫星 -Z 轴", BURN_SUN_AXIS_MINUS_Z)
        combo.addItem("卫星 +Z 轴", BURN_SUN_AXIS_PLUS_Z)
        index = combo.findData(current_value)
        combo.setCurrentIndex(index if index >= 0 else 0)
        return combo

    def _sync_constraint_row_widgets(self, row: int) -> None:
        return

    @staticmethod
    def _combo_value(widget: QtWidgets.QWidget | None) -> str:
        return "" if not isinstance(widget, QtWidgets.QComboBox) else str(widget.currentData())

    def _set_asset_rows(
        self,
        table: QtWidgets.QTableWidget | None,
        rows: list[dict[str, Any]],
        *,
        with_enabled: bool,
    ) -> None:
        if table is None:
            return
        table.setRowCount(0)
        for row in rows:
            self._append_asset_row(table, row, with_enabled=with_enabled)
        self._adjust_asset_table_height(table)

    def _append_asset_row(
        self,
        table: QtWidgets.QTableWidget | None,
        row_payload: dict[str, Any],
        *,
        with_enabled: bool | None = None,
    ) -> None:
        if table is None:
            return
        if with_enabled is None:
            with_enabled = table.columnCount() == 5
        row = table.rowCount()
        table.insertRow(row)
        start_col = 0
        if with_enabled:
            enabled_item = QtWidgets.QTableWidgetItem()
            enabled_item.setFlags(enabled_item.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable)
            enabled_item.setCheckState(
                QtCore.Qt.CheckState.Checked
                if bool(row_payload.get("enabled", True))
                else QtCore.Qt.CheckState.Unchecked
            )
            enabled_item.setTextAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            table.setItem(row, 0, enabled_item)
            start_col = 1
        values = [
            str(row_payload.get("name", "")),
            f"{float(row_payload.get('longitude_deg', 0.0)):.6f}",
            f"{float(row_payload.get('latitude_deg', 0.0)):.6f}",
            f"{float(row_payload.get('altitude_m', 0.0)):.3f}",
        ]
        for offset, value in enumerate(values, start=start_col):
            item = QtWidgets.QTableWidgetItem(value)
            if offset > start_col:
                item.setTextAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
            table.setItem(row, offset, item)
        table.setRowHeight(row, 40)
        self._adjust_asset_table_height(table)

    def _asset_rows_payload(self, table: QtWidgets.QTableWidget | None, *, asset_type: str) -> list[dict[str, Any]]:
        if table is None:
            return []
        rows: list[dict[str, Any]] = []
        with_enabled = table.columnCount() == 5
        for row in range(table.rowCount()):
            rows.append(self._asset_row_payload(table, row, asset_type=asset_type, with_enabled=with_enabled))
        return rows

    def _asset_row_payload(
        self,
        table: QtWidgets.QTableWidget,
        row: int,
        *,
        asset_type: str,
        with_enabled: bool,
    ) -> dict[str, Any]:
        col = 0
        enabled = True
        if with_enabled:
            enabled_item = table.item(row, 0)
            enabled = enabled_item is None or enabled_item.checkState() == QtCore.Qt.CheckState.Checked
            col = 1
        return {
            "enabled": enabled,
            "name": self._table_text(table, row, col),
            "longitude_deg": self._to_float(self._table_text(table, row, col + 1)),
            "latitude_deg": self._to_float(self._table_text(table, row, col + 2)),
            "altitude_m": self._to_float(self._table_text(table, row, col + 3)),
            "asset_type": asset_type,
        }

    def _delete_selected_rows(self, table: QtWidgets.QTableWidget | None) -> None:
        if table is None:
            return
        rows = sorted({index.row() for index in table.selectedIndexes()}, reverse=True)
        if not rows and table.currentRow() >= 0:
            rows = [table.currentRow()]
        for row in rows:
            table.removeRow(row)
        self._adjust_asset_table_height(table)

    @staticmethod
    def _adjust_asset_table_height(table: QtWidgets.QTableWidget | None) -> None:
        if table is None:
            return
        visible_rows = min(max(table.rowCount(), 3), 6)
        row_height = 40
        header_height = max(table.horizontalHeader().height(), 42)
        frame_height = table.frameWidth() * 2
        height = header_height + row_height * visible_rows + frame_height + 8
        table.setMinimumHeight(height)
        table.setMaximumHeight(height)

    def retranslate(self, _language: str | None = None) -> None:
        self._title_label.setText("发射窗口分析")
        self._subtitle_label.setText(
            "基于变轨策略页面的计算结果扫描火箭发射时刻。内部按“发射时刻 + 火箭飞行时间”得到卫星轨道 T0，卫星相对航时的地固位置、速度、星下点轨迹和高度保持不变。"
        )
        self._source_title_label.setText("轨道来源")
        self._source_body_label.setText("请先在“变轨策略”页面完成计算，本页复用 full_orbit_history.csv 作为相对航时轨道；窗口扫描时间按北京时间输入。")
        self._scan_title_label.setText("窗口扫描参数")
        self._state_title_label.setText("状态设置概览")
        self._constraint_title_label.setText("限制条件")
        self._action_title_label.setText("计算")
        self._summary_title_label.setText("计算结果")
        self._refresh_source_labels()

    def _set_status(self, role: str, text: str) -> None:
        self._status_role = role
        self._status_label.setProperty("role", role)
        self._status_label.style().unpolish(self._status_label)
        self._status_label.style().polish(self._status_label)
        self._status_label.setText(text)

    def _on_calculation_progress(self, completed: int, total: int) -> None:
        if total <= 0:
            self._progress_bar.setValue(0)
            return
        percent = int(round(100.0 * completed / total))
        now = time.monotonic()
        should_update = (
            completed >= total
            or percent == 0
            or percent >= self._progress_last_percent + 2
            or now - self._progress_last_update_monotonic >= 0.12
        )
        if not should_update:
            return
        self._progress_last_percent = percent
        self._progress_last_update_monotonic = now
        self._progress_bar.setValue(max(0, min(100, percent)))
        self._progress_bar.setFormat(f"{completed}/{total} ({percent}%)")
        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.processEvents(QtCore.QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)

    @staticmethod
    def _card() -> QtWidgets.QFrame:
        card = QtWidgets.QFrame()
        card.setProperty("role", "card")
        return card

    @staticmethod
    def _card_title() -> QtWidgets.QLabel:
        label = QtWidgets.QLabel()
        label.setProperty("role", "cardTitle")
        return label

    @staticmethod
    def _double_spin(value: float, minimum: float, maximum: float, step: float, decimals: int) -> NoWheelDoubleSpinBox:
        field = NoWheelDoubleSpinBox()
        field.setRange(minimum, maximum)
        field.setValue(value)
        field.setSingleStep(step)
        field.setDecimals(decimals)
        field.setButtonSymbols(QtWidgets.QAbstractSpinBox.ButtonSymbols.NoButtons)
        return field

    @staticmethod
    def _date_edit() -> NoWheelDateTimeEdit:
        field = NoWheelDateTimeEdit()
        field.setCalendarPopup(True)
        field.setDisplayFormat("yyyy-MM-dd HH:mm:ss 'BJT'")
        field.setTimeZone(_beijing_qtimezone())
        return field

    @staticmethod
    def _utc_to_qdatetime(value: str) -> QtCore.QDateTime:
        epoch = parse_utc(value)
        milliseconds = int(round(epoch.timestamp() * 1000.0))
        return QtCore.QDateTime.fromMSecsSinceEpoch(milliseconds, _beijing_qtimezone())

    @staticmethod
    def _datetime_edit_to_utc(field: NoWheelDateTimeEdit) -> str:
        py_dt = field.dateTime().toUTC().toPython()
        if py_dt.tzinfo is None:
            py_dt = py_dt.replace(tzinfo=timezone.utc)
        return format_utc(py_dt)

    @staticmethod
    def _format_beijing(value: str) -> str:
        return parse_utc(value).astimezone(BEIJING_TZ).strftime("%Y-%m-%d %H:%M")

    def _format_beijing_t0(self, launch_utc: str) -> str:
        t0 = parse_utc(launch_utc) + timedelta(seconds=self._number_fields["rocket_flight_time_s"].value())
        return t0.astimezone(BEIJING_TZ).strftime("%Y-%m-%d %H:%M")

    @staticmethod
    def _set_table_values(table: QtWidgets.QTableWidget, row: int, values: list[str]) -> None:
        for column, value in enumerate(values):
            item = QtWidgets.QTableWidgetItem(value)
            item.setTextAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            table.setItem(row, column, item)

    @staticmethod
    def _table_text(table: QtWidgets.QTableWidget, row: int, column: int) -> str:
        item = table.item(row, column)
        return "" if item is None else item.text().strip()

    @staticmethod
    def _format_constraint_time_cell(value: object) -> str:
        if isinstance(value, bool):
            return f"{float(value):.3f}"
        if isinstance(value, int | float):
            return f"{float(value):.3f}"
        text = str(value).strip()
        if not text:
            return "0.000"
        try:
            return f"{float(text):.3f}"
        except ValueError:
            return text

    @staticmethod
    def _constraint_time_payload(value: str) -> float | str:
        text = str(value).strip()
        if not text:
            return 0.0
        try:
            return float(text)
        except ValueError:
            return text

    @staticmethod
    def _is_numeric_text(value: str) -> bool:
        try:
            float(value)
        except ValueError:
            return False
        return True

    @staticmethod
    def _to_float(value: object) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0


def _stable_hash(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _file_hash(path: Path) -> str:
    if not path.exists():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _hashable_dataclass(value: Any) -> Any:
    return asdict(value) if is_dataclass(value) else value
