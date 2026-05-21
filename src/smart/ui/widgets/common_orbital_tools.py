from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Callable

from PySide6 import QtCore, QtGui, QtWidgets

from smart.domain.models import CircularOrbitMetrics, EARTH_MU_KM3_S2, EARTH_RADIUS_KM, OrbitalElements
from smart.services.earth_orientation import ecef_state_from_eci, format_utc, geodetic_point_from_ecef
from smart.services.orbital_mechanics import (
    apsis_orbit_metrics_from_altitudes,
    circular_orbit_metrics_from_altitude,
    circular_orbit_metrics_from_period,
    hohmann_transfer_between_circular_orbits,
    lambert_transfer,
    orbital_anomalies_from_angle,
    orbital_elements_from_state_vector,
    plane_change_delta_v,
    state_from_true_anomaly,
)
from smart.services.spice_service import BodyState, SpiceKernelManager
from smart.ui.i18n import I18nManager
from smart.ui.widgets.spinboxes import NoWheelComboBox, NoWheelDateTimeEdit, NoWheelDoubleSpinBox


def _beijing_qtimezone() -> QtCore.QTimeZone:
    return QtCore.QTimeZone(b"Asia/Shanghai")


def _utc_to_qdatetime(value: datetime) -> QtCore.QDateTime:
    milliseconds = int(round(value.timestamp() * 1000.0))
    return QtCore.QDateTime.fromMSecsSinceEpoch(milliseconds, _beijing_qtimezone())


def _datetime_edit_to_utc(field: NoWheelDateTimeEdit) -> str:
    py_dt = field.dateTime().toUTC().toPython()
    if py_dt.tzinfo is None:
        py_dt = py_dt.replace(tzinfo=timezone.utc)
    return format_utc(py_dt)


def _number_field(
    value: float,
    minimum: float,
    maximum: float,
    step: float,
    decimals: int,
) -> NoWheelDoubleSpinBox:
    field = NoWheelDoubleSpinBox()
    field.setRange(minimum, maximum)
    field.setSingleStep(step)
    field.setDecimals(decimals)
    field.setValue(value)
    field.setButtonSymbols(QtWidgets.QAbstractSpinBox.ButtonSymbols.NoButtons)
    field.setMinimumHeight(38)
    field.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Fixed)
    return field


class _CommonOrbitalToolDialog(QtWidgets.QDialog):
    def __init__(self, i18n: I18nManager, title_key: str, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._i18n = i18n
        self._drag_position: QtCore.QPoint | None = None
        self.setObjectName("commonOrbitalToolDialog")
        self.setWindowFlag(QtCore.Qt.WindowType.FramelessWindowHint, True)
        self.setModal(True)
        self.setMinimumSize(760, 540)
        self._apply_style()

        self.root_layout = QtWidgets.QVBoxLayout(self)
        self.root_layout.setContentsMargins(22, 18, 22, 22)
        self.root_layout.setSpacing(14)
        self._title_bar = self._build_title_bar(i18n.t(title_key))
        self.root_layout.addWidget(self._title_bar)

    def _build_title_bar(self, title: str) -> QtWidgets.QWidget:
        title_bar = QtWidgets.QWidget()
        title_bar.setObjectName("dialogTitleBar")
        title_bar.setCursor(QtCore.Qt.CursorShape.SizeAllCursor)
        row = QtWidgets.QHBoxLayout(title_bar)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)

        icon = QtWidgets.QLabel("⌬")
        icon.setObjectName("dialogTitleIcon")
        icon.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        icon.setFixedSize(28, 28)
        row.addWidget(icon)

        label = QtWidgets.QLabel(title)
        label.setProperty("role", "pageTitle")
        row.addWidget(label)
        row.addStretch(1)

        close_button = QtWidgets.QToolButton()
        close_button.setObjectName("dialogCloseButton")
        close_button.setText("X")
        close_button.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        close_button.clicked.connect(self.reject)
        row.addWidget(close_button)

        for widget in (title_bar, icon, label):
            widget.installEventFilter(self)
        return title_bar

    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:
        if watched is self._title_bar or watched in self._title_bar.findChildren(QtWidgets.QLabel):
            if self._handle_drag_event(event):
                return True
        return super().eventFilter(watched, event)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if not self._handle_drag_event(event):
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if not self._handle_drag_event(event):
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if not self._handle_drag_event(event):
            super().mouseReleaseEvent(event)

    def _handle_drag_event(self, event: QtCore.QEvent) -> bool:
        if not isinstance(event, QtGui.QMouseEvent):
            return False
        if event.type() == QtCore.QEvent.Type.MouseButtonPress:
            if event.button() != QtCore.Qt.MouseButton.LeftButton:
                return False
            self._drag_position = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return True
        if event.type() == QtCore.QEvent.Type.MouseMove and self._drag_position is not None:
            if not event.buttons() & QtCore.Qt.MouseButton.LeftButton:
                return False
            self.move(event.globalPosition().toPoint() - self._drag_position)
            event.accept()
            return True
        if event.type() == QtCore.QEvent.Type.MouseButtonRelease and self._drag_position is not None:
            self._drag_position = None
            event.accept()
            return True
        return False

    @staticmethod
    def _card() -> QtWidgets.QFrame:
        frame = QtWidgets.QFrame()
        frame.setProperty("role", "card")
        return frame

    @staticmethod
    def _panel() -> QtWidgets.QFrame:
        frame = QtWidgets.QFrame()
        frame.setProperty("role", "sectionPanel")
        return frame

    @staticmethod
    def _title(text: str) -> QtWidgets.QLabel:
        label = QtWidgets.QLabel(text)
        label.setProperty("role", "cardTitle")
        return label

    @staticmethod
    def _caption(text: str) -> QtWidgets.QLabel:
        label = QtWidgets.QLabel(text)
        label.setProperty("role", "cardCaption")
        label.setWordWrap(True)
        return label

    @staticmethod
    def _primary_button(text: str, slot: Callable[[], None]) -> QtWidgets.QPushButton:
        button = QtWidgets.QPushButton(text)
        button.setProperty("variant", "primaryAction")
        button.setMinimumHeight(44)
        button.clicked.connect(slot)
        return button

    @staticmethod
    def _output_table(headers: list[str], row_count: int) -> QtWidgets.QTableWidget:
        table = QtWidgets.QTableWidget(row_count, len(headers))
        table.setProperty("role", "toolOutput")
        table.setHorizontalHeaderLabels(headers)
        table.verticalHeader().setVisible(False)
        table.setAlternatingRowColors(True)
        table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.NoSelection)
        table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Stretch)
        return table

    @staticmethod
    def _set_table_row(table: QtWidgets.QTableWidget, row: int, values: list[str]) -> None:
        for column, value in enumerate(values):
            item = QtWidgets.QTableWidgetItem(value)
            item.setTextAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            table.setItem(row, column, item)

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QDialog#commonOrbitalToolDialog {
                background: qradialgradient(cx:0.50, cy:0.10, radius:1.15, fx:0.50, fy:0.10, stop:0 #0c2230, stop:0.52 #07131c, stop:1 #03090f);
                border: 1px solid #1c7d9a;
                border-radius: 18px;
            }
            QDialog#commonOrbitalToolDialog QWidget { background: transparent; }
            QDialog#commonOrbitalToolDialog QLabel[role="pageTitle"] {
                color: #f4fbff;
                font-size: 16pt;
                font-weight: 800;
            }
            QDialog#commonOrbitalToolDialog QLabel#dialogTitleIcon {
                background: rgba(19, 48, 63, 0.9);
                border: 1px solid #27677d;
                border-radius: 14px;
                color: #3bdcff;
                font-size: 13pt;
                font-weight: 700;
            }
            QDialog#commonOrbitalToolDialog QToolButton#dialogCloseButton {
                background: transparent;
                color: #c4d4dc;
                border: none;
                font-size: 18pt;
                padding: 2px 8px;
            }
            QDialog#commonOrbitalToolDialog QToolButton#dialogCloseButton:hover {
                color: #ffffff;
                background: rgba(59, 169, 198, 0.18);
                border-radius: 8px;
            }
            QDialog#commonOrbitalToolDialog QFrame[role="card"] {
                background: rgba(5, 17, 25, 0.72);
                border: 1px solid #1e7892;
                border-radius: 10px;
            }
            QDialog#commonOrbitalToolDialog QFrame[role="sectionPanel"] {
                background: rgba(8, 26, 36, 0.72);
                border: 1px solid #1e7892;
                border-radius: 8px;
            }
            QDialog#commonOrbitalToolDialog QLabel[role="cardTitle"] {
                color: #f2fbff;
                font-size: 13pt;
                font-weight: 800;
            }
            QDialog#commonOrbitalToolDialog QLabel[role="cardCaption"] { color: #91afba; }
            QDialog#commonOrbitalToolDialog QDoubleSpinBox,
            QDialog#commonOrbitalToolDialog QDateTimeEdit {
                background: rgba(7, 19, 28, 0.98);
                border: 1px solid #2b6075;
                border-radius: 6px;
                padding: 7px 9px;
                color: #e6f6fb;
            }
            QDialog#commonOrbitalToolDialog QDoubleSpinBox:focus,
            QDialog#commonOrbitalToolDialog QDateTimeEdit:focus { border: 1px solid #62d8ea; }
            QDialog#commonOrbitalToolDialog QTabWidget::pane {
                background: rgba(5, 17, 25, 0.45);
                border: 1px solid #1e7892;
                border-radius: 8px;
            }
            QDialog#commonOrbitalToolDialog QTabBar::tab {
                background: #102734;
                border: 1px solid #1e5266;
                color: #cfe9f0;
                min-width: 170px;
                padding: 9px 14px;
            }
            QDialog#commonOrbitalToolDialog QTabBar::tab:selected {
                background: #155160;
                color: #ffffff;
                border-color: #54d5e9;
            }
            QDialog#commonOrbitalToolDialog QTableWidget[role="toolOutput"] {
                border: 1px solid #1d6f86;
                border-radius: 8px;
                gridline-color: #1d6f86;
            }
            QDialog#commonOrbitalToolDialog QTableWidget[role="toolOutput"]::item { padding: 6px 4px; }
            QDialog#commonOrbitalToolDialog QHeaderView::section {
                background: #0a2b3b;
                color: #f1fbff;
                border: none;
                border-right: 1px solid #1d6f86;
                padding: 8px 4px;
                font-weight: 700;
            }
            QDialog#commonOrbitalToolDialog QPushButton[variant="primaryAction"] {
                min-width: 148px;
                border-radius: 7px;
                padding-left: 20px;
                padding-right: 20px;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #ff9b35, stop:1 #ff5a22);
                border: 1px solid #ffbd6a;
                color: #ffffff;
                font-size: 11pt;
                font-weight: 800;
            }
            QDialog#commonOrbitalToolDialog QPushButton[variant="primaryAction"]:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #ffae53, stop:1 #ff6d35);
            }
            """
        )


class OrbitalConversionDialog(_CommonOrbitalToolDialog):
    def __init__(self, i18n: I18nManager, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(i18n, "common_tools.orbit_conversion.title", parent)
        self.resize(1040, 660)
        default_elements = OrbitalElements()
        position, velocity = state_from_true_anomaly(default_elements, math.radians(default_elements.true_anomaly_deg))

        tabs = QtWidgets.QTabWidget()
        self.root_layout.addWidget(tabs, 1)
        tabs.addTab(self._build_elements_tab(default_elements), "六根数 -> 状态矢量")
        tabs.addTab(self._build_state_tab(position, velocity), "状态矢量 -> 六根数")

    def _build_elements_tab(self, default_elements: OrbitalElements) -> QtWidgets.QWidget:
        tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        input_card = self._card()
        input_layout = QtWidgets.QVBoxLayout(input_card)
        input_layout.setContentsMargins(16, 16, 16, 16)
        input_layout.setSpacing(10)
        input_layout.addWidget(self._title("输入轨道六根数"))
        input_layout.addWidget(self._caption("地心 J2000，距离 km，角度 deg。"))

        self._element_fields = {
            "a": _number_field(default_elements.semi_major_axis_km, EARTH_RADIUS_KM + 1.0, 2.0e7, 10.0, 6),
            "e": _number_field(default_elements.eccentricity, 0.0, 0.999999, 0.001, 8),
            "i": _number_field(default_elements.inclination_deg, 0.0, 180.0, 0.1, 6),
            "raan": _number_field(default_elements.raan_deg, 0.0, 360.0, 0.1, 6),
            "argp": _number_field(default_elements.argument_of_periapsis_deg, 0.0, 360.0, 0.1, 6),
            "ta": _number_field(default_elements.true_anomaly_deg, 0.0, 360.0, 0.1, 6),
        }
        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(10)
        labels = [
            ("半长轴 a (km)", "a"),
            ("偏心率 e", "e"),
            ("轨道倾角 i (deg)", "i"),
            ("升交点赤经 RAAN (deg)", "raan"),
            ("近地点幅角 (deg)", "argp"),
            ("真近点角 (deg)", "ta"),
        ]
        for index, (label, key) in enumerate(labels):
            row = index // 2
            column = (index % 2) * 2
            grid.addWidget(QtWidgets.QLabel(label), row, column)
            grid.addWidget(self._element_fields[key], row, column + 1)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        input_layout.addLayout(grid)

        convert_button = self._primary_button("计算状态矢量", self._convert_elements_to_state)
        input_layout.addWidget(convert_button, 0, QtCore.Qt.AlignmentFlag.AlignRight)
        layout.addWidget(input_card)

        output_panel = self._panel()
        output_layout = QtWidgets.QVBoxLayout(output_panel)
        output_layout.setContentsMargins(14, 14, 14, 14)
        output_layout.setSpacing(8)
        output_layout.addWidget(self._title("输出"))
        self._elements_status = self._caption("")
        output_layout.addWidget(self._elements_status)
        self._state_output_table = self._output_table(["量", "X", "Y", "Z", "单位"], 2)
        output_layout.addWidget(self._state_output_table)
        layout.addWidget(output_panel, 1)
        self._convert_elements_to_state()
        return tab

    def _build_state_tab(self, position: object, velocity: object) -> QtWidgets.QWidget:
        tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        input_card = self._card()
        input_layout = QtWidgets.QVBoxLayout(input_card)
        input_layout.setContentsMargins(16, 16, 16, 16)
        input_layout.setSpacing(10)
        input_layout.addWidget(self._title("输入位置速度矢量"))
        input_layout.addWidget(self._caption("地心 J2000 位置 km，速度 km/s。"))

        position_values = list(position)
        velocity_values = list(velocity)
        self._state_fields = {
            "x": _number_field(float(position_values[0]), -2.0e7, 2.0e7, 10.0, 8),
            "y": _number_field(float(position_values[1]), -2.0e7, 2.0e7, 10.0, 8),
            "z": _number_field(float(position_values[2]), -2.0e7, 2.0e7, 10.0, 8),
            "vx": _number_field(float(velocity_values[0]), -100.0, 100.0, 0.01, 10),
            "vy": _number_field(float(velocity_values[1]), -100.0, 100.0, 0.01, 10),
            "vz": _number_field(float(velocity_values[2]), -100.0, 100.0, 0.01, 10),
        }
        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(10)
        labels = [
            ("X (km)", "x"),
            ("Y (km)", "y"),
            ("Z (km)", "z"),
            ("Vx (km/s)", "vx"),
            ("Vy (km/s)", "vy"),
            ("Vz (km/s)", "vz"),
        ]
        for index, (label, key) in enumerate(labels):
            row = index // 3
            column = (index % 3) * 2
            grid.addWidget(QtWidgets.QLabel(label), row, column)
            grid.addWidget(self._state_fields[key], row, column + 1)
        for column in (1, 3, 5):
            grid.setColumnStretch(column, 1)
        input_layout.addLayout(grid)
        input_layout.addWidget(
            self._primary_button("计算轨道六根数", self._convert_state_to_elements),
            0,
            QtCore.Qt.AlignmentFlag.AlignRight,
        )
        layout.addWidget(input_card)

        output_panel = self._panel()
        output_layout = QtWidgets.QVBoxLayout(output_panel)
        output_layout.setContentsMargins(14, 14, 14, 14)
        output_layout.setSpacing(8)
        output_layout.addWidget(self._title("输出"))
        self._state_status = self._caption("")
        output_layout.addWidget(self._state_status)
        self._elements_output_table = self._output_table(["a (km)", "e", "i", "RAAN", "近地点幅角", "真近点角"], 1)
        output_layout.addWidget(self._elements_output_table)
        layout.addWidget(output_panel, 1)
        self._convert_state_to_elements()
        return tab

    def _convert_elements_to_state(self) -> None:
        try:
            elements = OrbitalElements(
                semi_major_axis_km=self._element_fields["a"].value(),
                eccentricity=self._element_fields["e"].value(),
                inclination_deg=self._element_fields["i"].value(),
                raan_deg=self._element_fields["raan"].value(),
                argument_of_periapsis_deg=self._element_fields["argp"].value(),
                true_anomaly_deg=self._element_fields["ta"].value(),
            ).validate()
            position, velocity = state_from_true_anomaly(elements, math.radians(elements.true_anomaly_deg))
        except Exception as exc:
            self._elements_status.setText(f"计算失败：{exc}")
            return

        self._elements_status.setText("计算完成。")
        self._set_table_row(
            self._state_output_table,
            0,
            ["位置", *(f"{float(value):.8f}" for value in position), "km"],
        )
        self._set_table_row(
            self._state_output_table,
            1,
            ["速度", *(f"{float(value):.10f}" for value in velocity), "km/s"],
        )

    def _convert_state_to_elements(self) -> None:
        position = [self._state_fields[key].value() for key in ("x", "y", "z")]
        velocity = [self._state_fields[key].value() for key in ("vx", "vy", "vz")]
        try:
            elements = orbital_elements_from_state_vector(position, velocity)
        except Exception as exc:
            self._state_status.setText(f"计算失败：{exc}")
            return

        self._state_status.setText("计算完成。")
        self._set_table_row(
            self._elements_output_table,
            0,
            [
                f"{elements.semi_major_axis_km:.8f}",
                f"{elements.eccentricity:.10f}",
                f"{elements.inclination_deg:.8f}",
                f"{elements.raan_deg:.8f}",
                f"{elements.argument_of_periapsis_deg:.8f}",
                f"{elements.true_anomaly_deg:.8f}",
            ],
        )


class ApsisParametersDialog(_CommonOrbitalToolDialog):
    def __init__(self, i18n: I18nManager, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(i18n, "common_tools.apsis_parameters.title", parent)
        self.resize(920, 560)

        input_card = self._card()
        input_layout = QtWidgets.QVBoxLayout(input_card)
        input_layout.setContentsMargins(16, 16, 16, 16)
        input_layout.setSpacing(12)
        input_layout.addWidget(self._title("输入近远地点高度"))
        input_layout.addWidget(self._caption("地球椭圆轨道，输入近地点和远地点相对地表高度。"))

        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(16)
        self._perigee_altitude_field = _number_field(400.0, 0.0, 2.0e6, 10.0, 6)
        self._apogee_altitude_field = _number_field(1200.0, 0.0, 2.0e6, 10.0, 6)
        grid.addWidget(QtWidgets.QLabel("近地点高度 hp (km)"), 0, 0)
        grid.addWidget(self._perigee_altitude_field, 0, 1)
        grid.addWidget(QtWidgets.QLabel("远地点高度 ha (km)"), 0, 2)
        grid.addWidget(self._apogee_altitude_field, 0, 3)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        input_layout.addLayout(grid)
        input_layout.addWidget(
            self._primary_button("计算参数", self._calculate_apsis_metrics),
            0,
            QtCore.Qt.AlignmentFlag.AlignRight,
        )
        self.root_layout.addWidget(input_card)

        output = self._panel()
        output_layout = QtWidgets.QVBoxLayout(output)
        output_layout.setContentsMargins(14, 14, 14, 14)
        output_layout.setSpacing(8)
        output_layout.addWidget(self._title("输出"))
        self._status = self._caption("")
        output_layout.addWidget(self._status)
        self._metrics_table = self._output_table(
            ["rp (km)", "ra (km)", "a (km)", "e", "周期 (min)"],
            1,
        )
        output_layout.addWidget(self._metrics_table)
        self.root_layout.addWidget(output, 1)
        self._calculate_apsis_metrics()

    def _calculate_apsis_metrics(self) -> None:
        try:
            metrics = apsis_orbit_metrics_from_altitudes(
                self._perigee_altitude_field.value(),
                self._apogee_altitude_field.value(),
            )
        except Exception as exc:
            self._status.setText(f"计算失败：{exc}")
            return

        self._status.setText("计算完成。")
        self._set_table_row(
            self._metrics_table,
            0,
            [
                f"{metrics.perigee_radius_km:.8f}",
                f"{metrics.apogee_radius_km:.8f}",
                f"{metrics.semi_major_axis_km:.8f}",
                f"{metrics.eccentricity:.10f}",
                f"{metrics.period_s / 60.0:.8f}",
            ],
        )


class CircularOrbitPeriodDialog(_CommonOrbitalToolDialog):
    def __init__(self, i18n: I18nManager, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(i18n, "common_tools.circular_period.title", parent)
        self.resize(980, 620)

        tabs = QtWidgets.QTabWidget()
        tabs.addTab(self._build_altitude_tab(), "高度 -> 周期")
        tabs.addTab(self._build_period_tab(), "周期 -> 高度")
        self.root_layout.addWidget(tabs, 1)

    def _build_altitude_tab(self) -> QtWidgets.QWidget:
        tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        layout.addWidget(self._circular_input_card("输入圆轨道高度", "高度 (km)", "_altitude_field", 550.0))
        panel, self._altitude_status, self._altitude_metrics_table = self._circular_output_panel()
        layout.addWidget(panel, 1)
        self._calculate_from_altitude()
        return tab

    def _build_period_tab(self) -> QtWidgets.QWidget:
        tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        layout.addWidget(self._circular_input_card("输入圆轨道周期", "周期 (min)", "_period_field", 95.0))
        panel, self._period_status, self._period_metrics_table = self._circular_output_panel()
        layout.addWidget(panel, 1)
        self._calculate_from_period()
        return tab

    def _circular_input_card(self, title: str, label: str, field_name: str, value: float) -> QtWidgets.QFrame:
        card = self._card()
        card_layout = QtWidgets.QVBoxLayout(card)
        card_layout.setContentsMargins(16, 16, 16, 16)
        card_layout.setSpacing(12)
        card_layout.addWidget(self._title(title))
        card_layout.addWidget(self._caption("地球圆轨道，两体快速换算。"))
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(12)
        row.addWidget(QtWidgets.QLabel(label))
        field = _number_field(value, 0.0 if field_name == "_altitude_field" else 1.0, 2.0e6, 1.0, 8)
        setattr(self, field_name, field)
        row.addWidget(field, 1)
        slot = self._calculate_from_altitude if field_name == "_altitude_field" else self._calculate_from_period
        row.addWidget(self._primary_button("计算", slot))
        card_layout.addLayout(row)
        return card

    def _circular_output_panel(self) -> tuple[QtWidgets.QFrame, QtWidgets.QLabel, QtWidgets.QTableWidget]:
        panel = self._panel()
        panel_layout = QtWidgets.QVBoxLayout(panel)
        panel_layout.setContentsMargins(14, 14, 14, 14)
        panel_layout.setSpacing(8)
        panel_layout.addWidget(self._title("输出"))
        status = self._caption("")
        panel_layout.addWidget(status)
        table = self._output_table(
            ["高度 (km)", "半径 (km)", "周期 (min)", "圆轨道速度 (km/s)", "逃逸速度 (km/s)", "平运动 (rad/s)"],
            1,
        )
        panel_layout.addWidget(table)
        return panel, status, table

    def _calculate_from_altitude(self) -> None:
        try:
            metrics = circular_orbit_metrics_from_altitude(self._altitude_field.value())
        except Exception as exc:
            self._altitude_status.setText(f"计算失败：{exc}")
            return
        self._set_circular_metrics(self._altitude_status, self._altitude_metrics_table, metrics)

    def _calculate_from_period(self) -> None:
        try:
            metrics = circular_orbit_metrics_from_period(self._period_field.value() * 60.0)
        except Exception as exc:
            self._period_status.setText(f"计算失败：{exc}")
            return
        self._set_circular_metrics(self._period_status, self._period_metrics_table, metrics)

    def _set_circular_metrics(
        self,
        status: QtWidgets.QLabel,
        table: QtWidgets.QTableWidget,
        metrics: CircularOrbitMetrics,
    ) -> None:
        status.setText("计算完成。")
        self._set_table_row(
            table,
            0,
            [
                f"{metrics.altitude_km:.8f}",
                f"{metrics.radius_km:.8f}",
                f"{metrics.period_s / 60.0:.8f}",
                f"{metrics.circular_speed_km_s:.10f}",
                f"{metrics.escape_speed_km_s:.10f}",
                f"{metrics.mean_motion_rad_s:.12f}",
            ],
        )


class AnomalyConversionDialog(_CommonOrbitalToolDialog):
    def __init__(self, i18n: I18nManager, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(i18n, "common_tools.anomaly_conversion.title", parent)
        self.resize(920, 560)

        input_card = self._card()
        input_layout = QtWidgets.QVBoxLayout(input_card)
        input_layout.setContentsMargins(16, 16, 16, 16)
        input_layout.setSpacing(12)
        input_layout.addWidget(self._title("输入椭圆轨道近点角"))
        input_layout.addWidget(self._caption("给定偏心率和一个近点角，换算真近点角、偏近点角、平近点角。"))

        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(10)
        self._eccentricity_field = _number_field(0.2, 0.0, 0.999999, 0.001, 8)
        self._anomaly_angle_field = _number_field(120.0, -360000.0, 360000.0, 1.0, 8)
        self._anomaly_source_combo = NoWheelComboBox()
        self._anomaly_source_combo.addItem("真近点角", "true")
        self._anomaly_source_combo.addItem("偏近点角", "eccentric")
        self._anomaly_source_combo.addItem("平近点角", "mean")
        self._anomaly_source_combo.setMinimumHeight(38)
        grid.addWidget(QtWidgets.QLabel("偏心率 e"), 0, 0)
        grid.addWidget(self._eccentricity_field, 0, 1)
        grid.addWidget(QtWidgets.QLabel("输入角类型"), 0, 2)
        grid.addWidget(self._anomaly_source_combo, 0, 3)
        grid.addWidget(QtWidgets.QLabel("输入角 (deg)"), 1, 0)
        grid.addWidget(self._anomaly_angle_field, 1, 1)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        input_layout.addLayout(grid)
        input_layout.addWidget(
            self._primary_button("换算近点角", self._calculate_anomalies),
            0,
            QtCore.Qt.AlignmentFlag.AlignRight,
        )
        self.root_layout.addWidget(input_card)

        output = self._panel()
        output_layout = QtWidgets.QVBoxLayout(output)
        output_layout.setContentsMargins(14, 14, 14, 14)
        output_layout.setSpacing(8)
        output_layout.addWidget(self._title("输出"))
        self._status = self._caption("")
        output_layout.addWidget(self._status)
        self._anomaly_table = self._output_table(["真近点角 (deg)", "偏近点角 (deg)", "平近点角 (deg)"], 1)
        output_layout.addWidget(self._anomaly_table)
        self.root_layout.addWidget(output, 1)
        self._calculate_anomalies()

    def _calculate_anomalies(self) -> None:
        try:
            anomalies = orbital_anomalies_from_angle(
                self._anomaly_angle_field.value(),
                self._eccentricity_field.value(),
                str(self._anomaly_source_combo.currentData()),
            )
        except Exception as exc:
            self._status.setText(f"计算失败：{exc}")
            return
        self._status.setText("计算完成。")
        self._set_table_row(
            self._anomaly_table,
            0,
            [
                f"{anomalies.true_anomaly_deg:.10f}",
                f"{anomalies.eccentric_anomaly_deg:.10f}",
                f"{anomalies.mean_anomaly_deg:.10f}",
            ],
        )


class SolarLunarPositionDialog(_CommonOrbitalToolDialog):
    def __init__(
        self,
        i18n: I18nManager,
        kernel_manager: SpiceKernelManager,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(i18n, "common_tools.sun_moon.title", parent)
        self._kernel_manager = kernel_manager
        self.resize(1080, 610)

        card = self._card()
        card_layout = QtWidgets.QVBoxLayout(card)
        card_layout.setContentsMargins(16, 16, 16, 16)
        card_layout.setSpacing(12)
        card_layout.addWidget(self._title("输入历元"))
        card_layout.addWidget(
            self._caption("北京时间编辑，计算时转 UTC。输出 Earth 相对 J2000 位置速度和日下点/月下点地理经纬度。")
        )

        row = QtWidgets.QHBoxLayout()
        row.setSpacing(12)
        row.addWidget(QtWidgets.QLabel("历元 (北京时间)"))
        self._epoch_field = NoWheelDateTimeEdit()
        self._epoch_field.setCalendarPopup(True)
        self._epoch_field.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        self._epoch_field.setTimeZone(_beijing_qtimezone())
        self._epoch_field.setDateTime(_utc_to_qdatetime(datetime.now(tz=timezone.utc).replace(microsecond=0)))
        self._epoch_field.setMinimumHeight(38)
        row.addWidget(self._epoch_field, 1)
        row.addWidget(self._primary_button("计算位置", self._calculate_positions))
        card_layout.addLayout(row)
        self.root_layout.addWidget(card)

        output = self._panel()
        output_layout = QtWidgets.QVBoxLayout(output)
        output_layout.setContentsMargins(14, 14, 14, 14)
        output_layout.setSpacing(8)
        output_layout.addWidget(self._title("输出"))
        self._status = self._caption("依赖本地 SPICE LSK 与行星 SPK 内核。")
        output_layout.addWidget(self._status)
        self._state_table = self._output_table(
            [
                "天体",
                "X (km)",
                "Y (km)",
                "Z (km)",
                "Vx (km/s)",
                "Vy (km/s)",
                "Vz (km/s)",
                "地理经度 (deg)",
                "地理纬度 (deg)",
                "光行时 (s)",
            ],
            2,
        )
        output_layout.addWidget(self._state_table, 1)
        self.root_layout.addWidget(output, 1)

    def _calculate_positions(self) -> None:
        utc = _datetime_edit_to_utc(self._epoch_field)
        try:
            states = [
                ("Sun", self._kernel_manager.state("SUN", "EARTH", utc, frame="J2000", aberration="NONE")),
                ("Moon", self._kernel_manager.state("MOON", "EARTH", utc, frame="J2000", aberration="NONE")),
            ]
        except Exception as exc:
            self._status.setText(f"计算失败：{exc}")
            return

        self._status.setText(f"计算完成。UTC: {utc}")
        for row, (label, state) in enumerate(states):
            self._set_body_state_row(row, label, state)

    def _set_body_state_row(self, row: int, label: str, state: BodyState) -> None:
        self._set_table_row(
            self._state_table,
            row,
            [
                label,
                *(f"{float(value):.6f}" for value in state.position_km),
                *(f"{float(value):.10f}" for value in state.velocity_km_s),
                *self._geographic_longitude_latitude_text(state),
                f"{state.light_time_s:.6f}",
            ],
        )

    def _geographic_longitude_latitude_text(self, state: BodyState) -> tuple[str, str]:
        utc = _datetime_edit_to_utc(self._epoch_field)
        position_ecef_m, _velocity_ecef_m_s = ecef_state_from_eci(
            state.position_km * 1000.0,
            state.velocity_km_s * 1000.0,
            epoch_utc=utc,
            manager=self._kernel_manager,
        )
        subpoint = geodetic_point_from_ecef(position_ecef_m)
        return f"{subpoint.longitude_deg:.8f}", f"{subpoint.latitude_deg:.8f}"


class HohmannTransferDialog(_CommonOrbitalToolDialog):
    def __init__(self, i18n: I18nManager, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(i18n, "common_tools.hohmann.title", parent)
        self.resize(900, 560)

        card = self._card()
        card_layout = QtWidgets.QVBoxLayout(card)
        card_layout.setContentsMargins(16, 16, 16, 16)
        card_layout.setSpacing(12)
        card_layout.addWidget(self._title("圆轨道输入"))
        card_layout.addWidget(self._caption("输入地球圆轨道高度。计算使用 Earth 标准引力参数。"))

        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(10)
        self._initial_altitude = _number_field(400.0, 0.0, 2.0e6, 10.0, 6)
        self._target_altitude = _number_field(35786.0, 0.0, 2.0e6, 10.0, 6)
        grid.addWidget(QtWidgets.QLabel("初始轨道高度 (km)"), 0, 0)
        grid.addWidget(self._initial_altitude, 0, 1)
        grid.addWidget(QtWidgets.QLabel("目标轨道高度 (km)"), 0, 2)
        grid.addWidget(self._target_altitude, 0, 3)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        card_layout.addLayout(grid)
        card_layout.addWidget(
            self._primary_button("计算转移", self._calculate_transfer),
            0,
            QtCore.Qt.AlignmentFlag.AlignRight,
        )
        self.root_layout.addWidget(card)

        output = self._panel()
        output_layout = QtWidgets.QVBoxLayout(output)
        output_layout.setContentsMargins(14, 14, 14, 14)
        output_layout.setSpacing(8)
        output_layout.addWidget(self._title("输出"))
        self._status = self._caption("")
        output_layout.addWidget(self._status)
        self._result_table = self._output_table(
            ["r1 (km)", "r2 (km)", "转移半长轴 (km)", "Δv1 (km/s)", "Δv2 (km/s)", "总 Δv (km/s)", "转移时间 (min)"],
            1,
        )
        output_layout.addWidget(self._result_table)
        self.root_layout.addWidget(output, 1)
        self._calculate_transfer()

    def _calculate_transfer(self) -> None:
        initial_radius = EARTH_RADIUS_KM + self._initial_altitude.value()
        target_radius = EARTH_RADIUS_KM + self._target_altitude.value()
        try:
            result = hohmann_transfer_between_circular_orbits(initial_radius, target_radius, EARTH_MU_KM3_S2)
        except Exception as exc:
            self._status.setText(f"计算失败：{exc}")
            return

        self._status.setText("计算完成。")
        self._set_table_row(
            self._result_table,
            0,
            [
                f"{result.initial_radius_km:.6f}",
                f"{result.target_radius_km:.6f}",
                f"{result.transfer_semi_major_axis_km:.6f}",
                f"{result.delta_v1_km_s:.10f}",
                f"{result.delta_v2_km_s:.10f}",
                f"{result.total_delta_v_km_s:.10f}",
                f"{result.transfer_time_s / 60.0:.6f}",
            ],
        )


class PlaneChangeDialog(_CommonOrbitalToolDialog):
    def __init__(self, i18n: I18nManager, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(i18n, "common_tools.plane_change.title", parent)
        self.resize(920, 560)

        input_card = self._card()
        input_layout = QtWidgets.QVBoxLayout(input_card)
        input_layout.setContentsMargins(16, 16, 16, 16)
        input_layout.setSpacing(12)
        input_layout.addWidget(self._title("输入速度和转角"))
        input_layout.addWidget(self._caption("同速时得到纯平面变轨；速度不同同时估算速度改变量与平面转角合并点火。"))

        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(10)
        self._initial_speed_field = _number_field(7.5, 0.000001, 1000.0, 0.1, 8)
        self._target_speed_field = _number_field(7.5, 0.000001, 1000.0, 0.1, 8)
        self._plane_angle_field = _number_field(5.0, 0.0, 180.0, 0.1, 8)
        fields = [
            ("点火前速度 v1 (km/s)", self._initial_speed_field),
            ("点火后速度 v2 (km/s)", self._target_speed_field),
            ("平面转角 (deg)", self._plane_angle_field),
        ]
        for index, (label, field) in enumerate(fields):
            row = index // 2
            column = (index % 2) * 2
            grid.addWidget(QtWidgets.QLabel(label), row, column)
            grid.addWidget(field, row, column + 1)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        input_layout.addLayout(grid)
        input_layout.addWidget(
            self._primary_button("计算变轨", self._calculate_plane_change),
            0,
            QtCore.Qt.AlignmentFlag.AlignRight,
        )
        self.root_layout.addWidget(input_card)

        output = self._panel()
        output_layout = QtWidgets.QVBoxLayout(output)
        output_layout.setContentsMargins(14, 14, 14, 14)
        output_layout.setSpacing(8)
        output_layout.addWidget(self._title("输出"))
        self._status = self._caption("")
        output_layout.addWidget(self._status)
        self._plane_change_table = self._output_table(
            ["纯平面变轨 Δv@v1 (km/s)", "合并点火 Δv (km/s)"],
            1,
        )
        output_layout.addWidget(self._plane_change_table)
        self.root_layout.addWidget(output, 1)
        self._calculate_plane_change()

    def _calculate_plane_change(self) -> None:
        try:
            result = plane_change_delta_v(
                self._initial_speed_field.value(),
                self._target_speed_field.value(),
                self._plane_angle_field.value(),
            )
        except Exception as exc:
            self._status.setText(f"计算失败：{exc}")
            return
        self._status.setText("计算完成。")
        self._set_table_row(
            self._plane_change_table,
            0,
            [
                f"{result.pure_plane_change_delta_v_km_s:.10f}",
                f"{result.combined_delta_v_km_s:.10f}",
            ],
        )


class LambertTransferDialog(_CommonOrbitalToolDialog):
    def __init__(self, i18n: I18nManager, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(i18n, "common_tools.lambert.title", parent)
        self.resize(1120, 680)

        input_card = self._card()
        input_layout = QtWidgets.QVBoxLayout(input_card)
        input_layout.setContentsMargins(16, 16, 16, 16)
        input_layout.setSpacing(12)
        input_layout.addWidget(self._title("输入 Lambert 几何"))
        input_layout.addWidget(self._caption("地心 J2000 位置 km，飞行时间 min。当前求零圈单圈两体 Lambert 解。"))

        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(10)
        self._lambert_fields: dict[str, NoWheelDoubleSpinBox] = {}
        vector_defaults = {
            "r1x": 7000.0,
            "r1y": 0.0,
            "r1z": 0.0,
            "r2x": 0.0,
            "r2y": 7000.0,
            "r2z": 0.0,
        }
        for row, (title, prefix) in enumerate((("出发位置 r1", "r1"), ("到达位置 r2", "r2"))):
            grid.addWidget(QtWidgets.QLabel(title), row, 0)
            for offset, axis in enumerate(("x", "y", "z"), start=1):
                field = _number_field(vector_defaults[f"{prefix}{axis}"], -2.0e7, 2.0e7, 10.0, 8)
                self._lambert_fields[f"{prefix}{axis}"] = field
                grid.addWidget(QtWidgets.QLabel(axis.upper()), row, offset * 2 - 1)
                grid.addWidget(field, row, offset * 2)
        for column in (2, 4, 6):
            grid.setColumnStretch(column, 1)
        input_layout.addLayout(grid)

        option_row = QtWidgets.QHBoxLayout()
        option_row.setSpacing(12)
        self._lambert_tof_field = _number_field(24.285366, 0.000001, 1.0e9, 1.0, 8)
        option_row.addWidget(QtWidgets.QLabel("飞行时间 (min)"))
        option_row.addWidget(self._lambert_tof_field, 1)
        self._lambert_path_combo = NoWheelComboBox()
        self._lambert_path_combo.addItem("短路径", False)
        self._lambert_path_combo.addItem("长路径", True)
        self._lambert_path_combo.setMinimumHeight(38)
        option_row.addWidget(QtWidgets.QLabel("路径"))
        option_row.addWidget(self._lambert_path_combo)
        option_row.addWidget(self._primary_button("求解 Lambert", self._calculate_lambert))
        input_layout.addLayout(option_row)
        self.root_layout.addWidget(input_card)

        output = self._panel()
        output_layout = QtWidgets.QVBoxLayout(output)
        output_layout.setContentsMargins(14, 14, 14, 14)
        output_layout.setSpacing(8)
        output_layout.addWidget(self._title("输出"))
        self._status = self._caption("")
        output_layout.addWidget(self._status)
        self._lambert_velocity_table = self._output_table(["量", "X (km/s)", "Y (km/s)", "Z (km/s)"], 2)
        output_layout.addWidget(self._lambert_velocity_table)
        self._lambert_summary_table = self._output_table(["转移角 (deg)", "飞行时间 (min)", "路径"], 1)
        output_layout.addWidget(self._lambert_summary_table)
        self.root_layout.addWidget(output, 1)
        self._calculate_lambert()

    def _calculate_lambert(self) -> None:
        try:
            result = lambert_transfer(
                [self._lambert_fields[key].value() for key in ("r1x", "r1y", "r1z")],
                [self._lambert_fields[key].value() for key in ("r2x", "r2y", "r2z")],
                self._lambert_tof_field.value() * 60.0,
                long_path=bool(self._lambert_path_combo.currentData()),
            )
        except Exception as exc:
            self._status.setText(f"计算失败：{exc}")
            return
        self._status.setText("计算完成。")
        self._set_table_row(
            self._lambert_velocity_table,
            0,
            ["出发速度", *(f"{float(value):.10f}" for value in result.departure_velocity_km_s)],
        )
        self._set_table_row(
            self._lambert_velocity_table,
            1,
            ["到达速度", *(f"{float(value):.10f}" for value in result.arrival_velocity_km_s)],
        )
        self._set_table_row(
            self._lambert_summary_table,
            0,
            [
                f"{result.transfer_angle_deg:.8f}",
                f"{result.time_of_flight_s / 60.0:.8f}",
                "长路径" if result.path == "long" else "短路径",
            ],
        )
