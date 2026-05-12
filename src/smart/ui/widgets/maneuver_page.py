from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import timedelta, timezone
import importlib.util
import math
from pathlib import Path
import sys
from typing import Any

import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtGui, QtWidgets

from smart.domain.models import OrbitTrajectory
from smart.services.earth_orientation import (
    format_utc,
    inertial_raan_deg_from_ascending_node_longitude_deg,
    parse_utc,
    subsatellite_point_from_eci,
    utc_now_iso_z,
)
from smart.services.project_workspace import ProjectWorkspace, default_maneuver_strategy_payload
from smart.ui.i18n import I18nManager
from smart.ui.widgets.orbit_views import OrbitPlot3D
from smart.ui.widgets.spinboxes import NoWheelComboBox, NoWheelDateTimeEdit, NoWheelDoubleSpinBox, NoWheelSpinBox

_REPO_ROOT = Path(__file__).resolve().parents[4]
_DYNAMICS_SCRIPT_PATH = _REPO_ROOT / "scripts" / "satellite_dynamics_equation.py"
_EARTH_RADIUS_KM = 6378.14
_MANEUVER_PHASES = {"settle", "orbit_control"}
_GROUND_TRACK_MANEUVER_LABEL_LAT_OFFSET_DEG = 5.0
BEIJING_TZ = timezone(timedelta(hours=8))
BEIJING_QT_TIMEZONE_ID = b"Asia/Shanghai"
_EARTH_TEXTURE_PATHS = (
    _REPO_ROOT
    / "src"
    / "smart"
    / "assets"
    / "textures"
    / "earth_basic_stk.bmp",
    _REPO_ROOT
    / "src"
    / "smart"
    / "assets"
    / "textures"
    / "earth_day_2048.png",
)


def _beijing_qtimezone() -> QtCore.QTimeZone:
    return QtCore.QTimeZone(BEIJING_QT_TIMEZONE_ID)


def _qimage_to_rgba_array(image: QtGui.QImage) -> np.ndarray:
    image = image.convertToFormat(QtGui.QImage.Format.Format_RGBA8888)
    raw = np.frombuffer(image.bits(), dtype=np.uint8, count=image.sizeInBytes()).copy()
    height = image.height()
    width = image.width()
    bytes_per_line = image.bytesPerLine()
    return raw.reshape((height, bytes_per_line))[:, : width * 4].reshape((height, width, 4))


def _load_world_map_rgba() -> np.ndarray | None:
    image = QtGui.QImage()
    for path in _EARTH_TEXTURE_PATHS:
        if path.exists():
            image = QtGui.QImage(str(path))
            if not image.isNull():
                break
    if image.isNull():
        return None
    return np.flipud(_qimage_to_rgba_array(image))


def _shift_mask(mask: np.ndarray, dy: int, dx: int) -> np.ndarray:
    shifted = np.zeros_like(mask, dtype=bool)
    height, width = mask.shape
    src_y0 = max(0, -dy)
    src_y1 = min(height, height - dy)
    dst_y0 = max(0, dy)
    dst_y1 = min(height, height + dy)
    src_x0 = max(0, -dx)
    src_x1 = min(width, width - dx)
    dst_x0 = max(0, dx)
    dst_x1 = min(width, width + dx)
    if src_y0 < src_y1 and src_x0 < src_x1:
        shifted[dst_y0:dst_y1, dst_x0:dst_x1] = mask[src_y0:src_y1, src_x0:src_x1]
    return shifted


def _dilate_mask(mask: np.ndarray) -> np.ndarray:
    result = mask.copy()
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dy == 0 and dx == 0:
                continue
            result |= _shift_mask(mask, dy, dx)
    return result


def _erode_mask(mask: np.ndarray) -> np.ndarray:
    result = mask.copy()
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dy == 0 and dx == 0:
                continue
            result &= _shift_mask(mask, dy, dx)
    return result


def _remove_small_components(mask: np.ndarray, min_area: int) -> np.ndarray:
    visited = np.zeros(mask.shape, dtype=bool)
    keep = np.zeros(mask.shape, dtype=bool)
    height, width = mask.shape
    ys, xs = np.nonzero(mask)
    for start_y, start_x in zip(ys, xs, strict=False):
        if visited[start_y, start_x]:
            continue
        stack = [(int(start_y), int(start_x))]
        visited[start_y, start_x] = True
        component: list[tuple[int, int]] = []
        while stack:
            y, x = stack.pop()
            component.append((y, x))
            for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                if 0 <= ny < height and 0 <= nx < width and mask[ny, nx] and not visited[ny, nx]:
                    visited[ny, nx] = True
                    stack.append((ny, nx))
        if len(component) >= min_area:
            for y, x in component:
                keep[y, x] = True
    return keep


def _fill_small_water_holes(land: np.ndarray, max_area: int) -> np.ndarray:
    water = ~land
    visited = np.zeros(water.shape, dtype=bool)
    filled = land.copy()
    height, width = water.shape
    ys, xs = np.nonzero(water)
    for start_y, start_x in zip(ys, xs, strict=False):
        if visited[start_y, start_x]:
            continue
        stack = [(int(start_y), int(start_x))]
        visited[start_y, start_x] = True
        touches_edge = False
        component: list[tuple[int, int]] = []
        while stack:
            y, x = stack.pop()
            component.append((y, x))
            if y in (0, height - 1) or x in (0, width - 1):
                touches_edge = True
            for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                if 0 <= ny < height and 0 <= nx < width and water[ny, nx] and not visited[ny, nx]:
                    visited[ny, nx] = True
                    stack.append((ny, nx))
        if not touches_edge and len(component) <= max_area:
            for y, x in component:
                filled[y, x] = True
    return filled


def _stylized_world_map_rgba(source: np.ndarray) -> np.ndarray:
    rgb = source[:, :, :3].astype(np.float32)
    red = rgb[:, :, 0]
    green = rgb[:, :, 1]
    blue = rgb[:, :, 2]
    brightness = rgb.mean(axis=2)
    blue_dominant = (blue > red * 1.08) & (blue > green * 0.92)
    bright_neutral = (brightness > 165.0) & (np.abs(red - green) < 55.0) & (np.abs(green - blue) < 70.0)
    height, width = brightness.shape
    latitudes = 90.0 - (np.arange(height, dtype=np.float32) / max(1, height - 1)) * 180.0
    land = ((~blue_dominant) & (brightness > 28.0)) | bright_neutral
    land[latitudes > 82.0, :] = False
    land[latitudes < -65.0, :] = False
    land = _dilate_mask(_dilate_mask(land))
    land = _erode_mask(_erode_mask(land))
    land = _fill_small_water_holes(land, max_area=max(900, (height * width) // 1300))
    land = _remove_small_components(land, min_area=max(1600, (height * width) // 900))
    interior = _erode_mask(land)
    coastline = land & ~interior
    coastline = _dilate_mask(coastline)
    coastline[latitudes > 80.0, :] = False
    coastline[latitudes < -62.0, :] = False

    y = np.linspace(0.0, 1.0, height, dtype=np.float32)[:, None]
    x = np.linspace(0.0, 1.0, width, dtype=np.float32)[None, :]
    ocean = np.zeros((height, width, 4), dtype=np.uint8)
    ocean[:, :, 0] = np.clip(4 + 4 * (1.0 - y) + 6 * np.sin(x * math.pi), 0, 255).astype(np.uint8)
    ocean[:, :, 1] = np.clip(23 + 18 * (1.0 - np.abs(y - 0.45)) + 7 * np.sin(x * math.pi), 0, 255).astype(np.uint8)
    ocean[:, :, 2] = np.clip(45 + 45 * (1.0 - np.abs(y - 0.5)) + 12 * np.cos(x * math.pi), 0, 255).astype(np.uint8)
    ocean[:, :, 3] = 255

    ocean[interior, :3] = np.asarray([8, 58, 79], dtype=np.uint8)
    ocean[land, :3] = np.asarray([11, 75, 100], dtype=np.uint8)
    ocean[coastline, :3] = np.asarray([32, 213, 255], dtype=np.uint8)
    return ocean


@dataclass(frozen=True, slots=True)
class _StrategyColumn:
    key: str
    label_key: str
    decimals: int


class _GroundTrackViewBox(pg.ViewBox):
    def __init__(self) -> None:
        super().__init__(enableMenu=False)
        self.setMouseEnabled(x=True, y=False)
        self.setLimits(yMin=-90.0, yMax=90.0, minYRange=180.0, maxYRange=180.0)

    def mouseDragEvent(self, ev: object, axis: int | None = None) -> None:
        super().mouseDragEvent(ev, axis=0)
        self.setYRange(-90.0, 90.0, padding=0.0)

    def wheelEvent(self, ev: object, axis: int | None = None) -> None:
        if hasattr(ev, "ignore"):
            ev.ignore()


class _DirectionComboBox(NoWheelComboBox):
    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        super().paintEvent(event)
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(QtGui.QColor("#9fd7e5"))
        center_x = self.width() - 14
        center_y = self.height() // 2 + 1
        points = [
            QtCore.QPoint(center_x - 5, center_y - 3),
            QtCore.QPoint(center_x + 5, center_y - 3),
            QtCore.QPoint(center_x, center_y + 3),
        ]
        painter.drawPolygon(QtGui.QPolygon(points))


class _ManeuverConfigDialog(QtWidgets.QDialog):
    def __init__(
        self,
        i18n: I18nManager,
        strategy: dict[str, Any],
        columns: tuple[_StrategyColumn, ...],
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._i18n = i18n
        self._columns = columns
        self.setObjectName("maneuverConfigDialog")
        self.setWindowTitle(i18n.t("maneuver.config_dialog.title"))
        self.setWindowFlag(QtCore.Qt.WindowType.FramelessWindowHint, True)
        self.resize(1280, 780)
        self.setMinimumSize(1080, 700)
        self._drag_position: QtCore.QPoint | None = None
        self._summary_chips: list[QtWidgets.QLabel] = []
        self._table_title_label: QtWidgets.QLabel | None = None
        self._apply_dialog_style()

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 24)
        root.setSpacing(14)

        self._title_bar = QtWidgets.QWidget()
        self._title_bar.setObjectName("dialogTitleBar")
        self._title_bar.setCursor(QtCore.Qt.CursorShape.SizeAllCursor)
        title_row = QtWidgets.QHBoxLayout(self._title_bar)
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(12)
        title_icon = self._title_icon_label()
        title_row.addWidget(title_icon, 0, QtCore.Qt.AlignmentFlag.AlignVCenter)
        title_label = QtWidgets.QLabel(i18n.t("maneuver.config_dialog.title"))
        title_label.setProperty("role", "pageTitle")
        title_row.addWidget(title_label, 0, QtCore.Qt.AlignmentFlag.AlignVCenter)
        title_row.addStretch(1)
        close_button = QtWidgets.QToolButton()
        close_button.setObjectName("dialogCloseButton")
        close_button.setText("X")
        close_button.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        close_button.clicked.connect(self.reject)
        title_row.addWidget(close_button, 0, QtCore.Qt.AlignmentFlag.AlignVCenter)
        for drag_widget in (self._title_bar, title_icon, title_label):
            drag_widget.installEventFilter(self)
        root.addWidget(self._title_bar)

        initial_card = self._card()
        initial_card.setObjectName("configInitialCard")
        initial_card_layout = QtWidgets.QVBoxLayout(initial_card)
        initial_card_layout.setContentsMargins(18, 18, 18, 18)
        initial_card_layout.setSpacing(16)

        initial_card_layout.addWidget(
            self._section_header(
                i18n.t("maneuver.initial_state_header"),
                QtWidgets.QStyle.StandardPixmap.SP_FileDialogContentsView,
            )
        )

        form_grid = QtWidgets.QGridLayout()
        form_grid.setHorizontalSpacing(24)
        form_grid.setVerticalSpacing(16)
        self._launch_mass_field = self._spinbox(100.0, 30000.0, 10.0, 3)
        self._t0_epoch_field = self._date_edit()
        self._fit_form_field(self._launch_mass_field)
        self._fit_form_field(self._t0_epoch_field)
        form_grid.addWidget(
            self._field_prompt(
                i18n.t("maneuver.field.launch_mass_kg"),
                QtWidgets.QStyle.StandardPixmap.SP_DriveHDIcon,
            ),
            0,
            0,
        )
        form_grid.addWidget(self._launch_mass_field, 0, 1)
        form_grid.addWidget(
            self._field_prompt(
                i18n.t("maneuver.field.t0_epoch"),
                QtWidgets.QStyle.StandardPixmap.SP_FileDialogDetailedView,
            ),
            0,
            2,
        )
        form_grid.addWidget(self._t0_epoch_field, 0, 3)

        self._orbit_fields: dict[str, NoWheelDoubleSpinBox] = {}
        orbit_icon_map = {
            "semi_major_axis_m": QtWidgets.QStyle.StandardPixmap.SP_BrowserReload,
            "eccentricity": QtWidgets.QStyle.StandardPixmap.SP_MediaSeekForward,
            "inclination_deg": QtWidgets.QStyle.StandardPixmap.SP_ArrowUp,
            "argument_of_perigee_deg": QtWidgets.QStyle.StandardPixmap.SP_DialogResetButton,
            "raan_deg": QtWidgets.QStyle.StandardPixmap.SP_DriveNetIcon,
            "mean_anomaly_deg": QtWidgets.QStyle.StandardPixmap.SP_ArrowRight,
        }
        field_order = list(self._orbit_ranges().items())
        for index, (key, (minimum, maximum, step, decimals)) in enumerate(field_order, start=1):
            field = self._spinbox(minimum, maximum, step, decimals)
            self._fit_form_field(field)
            self._orbit_fields[key] = field
            row = ((index - 1) // 2) + 1
            col = ((index - 1) % 2) * 2
            form_grid.addWidget(
                self._field_prompt(i18n.t(f"maneuver.field.{key}"), orbit_icon_map.get(key)),
                row,
                col,
            )
            form_grid.addWidget(field, row, col + 1)
        form_grid.setColumnStretch(1, 1)
        form_grid.setColumnStretch(3, 1)
        form_grid.setColumnMinimumWidth(0, 210)
        form_grid.setColumnMinimumWidth(1, 360)
        form_grid.setColumnMinimumWidth(2, 230)
        form_grid.setColumnMinimumWidth(3, 360)
        initial_card_layout.addLayout(form_grid)
        root.addWidget(initial_card)

        maneuver_card = self._card()
        maneuver_card.setObjectName("maneuverTableCard")
        maneuver_layout = QtWidgets.QVBoxLayout(maneuver_card)
        maneuver_layout.setContentsMargins(18, 18, 18, 18)
        maneuver_layout.setSpacing(14)

        header_row = QtWidgets.QHBoxLayout()
        header_row.setSpacing(10)

        self._table_title_label = self._section_header(
            i18n.t("maneuver.strategy_header"),
            QtWidgets.QStyle.StandardPixmap.SP_ComputerIcon,
        )
        header_row.addWidget(self._table_title_label, 0, QtCore.Qt.AlignmentFlag.AlignVCenter)
        header_row.addSpacing(8)

        count_chip = QtWidgets.QLabel()
        count_chip.setProperty("role", "tagChip")
        self._summary_chips.append(count_chip)
        header_row.addWidget(count_chip, 0, QtCore.Qt.AlignmentFlag.AlignVCenter)
        header_row.addStretch(1)
        maneuver_layout.addLayout(header_row)

        table_frame = self._section_card()
        table_frame.setObjectName("maneuverTableFrame")
        table_frame_layout = QtWidgets.QVBoxLayout(table_frame)
        table_frame_layout.setContentsMargins(0, 0, 0, 0)
        table_frame_layout.setSpacing(0)

        self._table = QtWidgets.QTableWidget(0, len(columns))
        self._table.setObjectName("maneuverConfigTable")
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setShowGrid(True)
        self._table.setGridStyle(QtCore.Qt.PenStyle.SolidLine)
        self._table.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        row_height = self._table.fontMetrics().height() + 14
        self._table.verticalHeader().setDefaultSectionSize(row_height)
        self._table.horizontalHeader().setStretchLastSection(False)
        self._table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Interactive)
        self._table.horizontalHeader().setMinimumHeight(46)
        self._table.setHorizontalScrollMode(QtWidgets.QAbstractItemView.ScrollMode.ScrollPerPixel)
        self._table.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._table.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Expanding)
        self._table.setHorizontalHeaderLabels([i18n.t(column.label_key) for column in columns])
        table_frame_layout.addWidget(self._table)
        maneuver_layout.addWidget(table_frame)

        button_row = QtWidgets.QHBoxLayout()
        button_row.setSpacing(10)
        add_button = QtWidgets.QPushButton(i18n.t("maneuver.add_button"))
        remove_button = QtWidgets.QPushButton(i18n.t("maneuver.remove_button"))
        add_button.setProperty("variant", "secondary")
        remove_button.setProperty("variant", "secondary")
        add_button.setText(f"+  {i18n.t('maneuver.add_button')}")
        remove_button.setText(f"-  {i18n.t('maneuver.remove_button')}")
        add_button.clicked.connect(self._append_default_row)
        remove_button.clicked.connect(self._remove_current_row)
        button_row.addWidget(add_button)
        button_row.addWidget(remove_button)
        button_row.addStretch(1)
        maneuver_layout.addLayout(button_row)
        root.addWidget(maneuver_card, 1)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Save | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        buttons.setCenterButtons(False)
        save_button = buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Save)
        cancel_button = buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        save_button.setText(i18n.t("dialog.save"))
        cancel_button.setText(i18n.t("dialog.cancel"))
        save_button.setProperty("variant", "primaryAction")
        cancel_button.setProperty("variant", "secondary")
        save_button.setText(f"▣  {i18n.t('dialog.save')}")
        save_button.setMinimumHeight(52)
        cancel_button.setMinimumHeight(52)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons, 0, QtCore.Qt.AlignmentFlag.AlignRight)
        self._set_strategy(strategy)
        QtCore.QTimer.singleShot(0, self._refresh_layout_metrics)

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        super().showEvent(event)
        self._refresh_layout_metrics()

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        if hasattr(self, "_table"):
            QtCore.QTimer.singleShot(0, self._refresh_layout_metrics)

    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:
        if watched in {self._title_bar, *self._title_bar.findChildren(QtWidgets.QLabel)}:
            if self._handle_drag_event(event):
                return True
        return super().eventFilter(watched, event)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._handle_drag_event(event):
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._handle_drag_event(event):
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._handle_drag_event(event):
            return
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

    def strategy(self) -> dict[str, Any]:
        maneuvers: list[dict[str, Any]] = []
        for row in range(self._table.rowCount()):
            step: dict[str, Any] = {}
            for column_index, column in enumerate(self._columns):
                widget = self._table.cellWidget(row, column_index)
                if column.key in {"maneuver_index", "dv_direction"}:
                    step[column.key] = self._field_int(widget, row + 1)
                else:
                    step[column.key] = self._field_float(widget)
            maneuvers.append(step)
        return {
            "launch_mass_kg": self._launch_mass_field.value(),
            "t0_epoch": self._datetime_edit_to_utc(self._t0_epoch_field),
            "t0_orbit": {key: field.value() for key, field in self._orbit_fields.items()},
            "maneuver_count": len(maneuvers),
            "maneuvers": maneuvers,
        }

    def _set_strategy(self, strategy: dict[str, Any]) -> None:
        defaults = default_maneuver_strategy_payload(0)
        orbit = strategy.get("t0_orbit", {})
        if not isinstance(orbit, dict):
            orbit = {}
        self._launch_mass_field.setValue(float(strategy.get("launch_mass_kg", defaults["launch_mass_kg"])))
        self._t0_epoch_field.setDateTime(self._utc_to_qdatetime(str(strategy.get("t0_epoch", defaults["t0_epoch"]))))
        for key, field in self._orbit_fields.items():
            field.setValue(float(orbit.get(key, defaults["t0_orbit"][key])))
        self._table.setRowCount(0)
        rows = strategy.get("maneuvers", [])
        if isinstance(rows, list):
            for row in rows:
                if isinstance(row, dict):
                    self._append_row(row)
        self._refresh_layout_metrics()

    def _append_default_row(self) -> None:
        row = self._table.rowCount()
        step = default_maneuver_strategy_payload(1)["maneuvers"][0]
        step["maneuver_index"] = row + 1
        self._append_row(step)

    def _append_row(self, payload: dict[str, Any]) -> None:
        row = self._table.rowCount()
        self._table.insertRow(row)
        for column_index, column in enumerate(self._columns):
            value = payload.get(column.key, row + 1 if column.key == "maneuver_index" else 1 if column.key == "dv_direction" else 0.0)
            self._table.setCellWidget(row, column_index, self._make_field(column, value))
        self._refresh_layout_metrics()

    def _remove_current_row(self) -> None:
        row = self._table.currentRow()
        if row < 0:
            row = self._table.rowCount() - 1
        if row >= 0:
            self._table.removeRow(row)
            for row_index in range(self._table.rowCount()):
                widget = self._table.cellWidget(row_index, 0)
                if isinstance(widget, QtWidgets.QSpinBox):
                    widget.setValue(row_index + 1)
            self._refresh_layout_metrics()

    def _refresh_layout_metrics(self) -> None:
        self._sync_summary_chip()
        self._sync_table_column_widths()
        self._adjust_table_height_to_rows()

    def _sync_summary_chip(self) -> None:
        count_text = f"{self._table.rowCount()} 次机动"
        for chip in self._summary_chips:
            chip.setText(count_text)

    def _sync_table_column_widths(self) -> None:
        base_widths = {
            "maneuver_index": 66,
            "Tn_start_min": 124,
            "burn_duration_min": 120,
            "control_fuel_%": 106,
            "settle_duration_s": 110,
            "delta_deg": 98,
            "dv_direction": 90,
            "orbit_control_thrust_n": 112,
            "orbit_control_isp_s": 112,
            "settle_thrust_n": 112,
            "settle_isp_s": 110,
        }
        header = self._table.horizontalHeader()
        if header.count() <= 0:
            return
        base_total = sum(base_widths.get(column.key, 110) for column in self._columns)
        available_width = self._table.viewport().width()
        if available_width <= 0:
            available_width = self._table.width() - self._table.frameWidth() * 2
        extra_width = max(0, available_width - base_total)
        stretch_columns = max(1, len(self._columns) - 1)
        for column_index, column in enumerate(self._columns):
            resize_mode = QtWidgets.QHeaderView.ResizeMode.Fixed
            header.setSectionResizeMode(column_index, resize_mode)
            extra = 0 if column.key == "maneuver_index" else extra_width // stretch_columns
            self._table.setColumnWidth(column_index, base_widths.get(column.key, 110) + extra)

    def _adjust_table_height_to_rows(self) -> None:
        font_height = self._table.fontMetrics().height()
        row_height = max(font_height + 30, 48)
        for row in range(self._table.rowCount()):
            for column in range(self._table.columnCount()):
                widget = self._table.cellWidget(row, column)
                if widget is not None:
                    row_height = max(row_height, widget.sizeHint().height() + 8)
        for row in range(self._table.rowCount()):
            self._table.setRowHeight(row, row_height)
        header_height = self._table.horizontalHeader().height()
        frame_height = self._table.frameWidth() * 2
        rows = max(self._table.rowCount(), 1)
        visible_rows = min(rows, 6)
        scrollbar_height = self._table.horizontalScrollBar().sizeHint().height()
        height = header_height + row_height * visible_rows + scrollbar_height + frame_height + 8
        self._table.verticalHeader().setDefaultSectionSize(row_height)
        self._table.setMinimumHeight(height)
        self._table.setMaximumHeight(height)

    def _make_field(self, column: _StrategyColumn, value: object) -> QtWidgets.QWidget:
        if column.key == "maneuver_index":
            field = NoWheelSpinBox()
            field.setRange(1, 999)
            field.setValue(self._to_int(value, 1))
            field.setButtonSymbols(QtWidgets.QAbstractSpinBox.ButtonSymbols.NoButtons)
            return field
        if column.key == "dv_direction":
            field = _DirectionComboBox()
            field.addItem("1", 1)
            field.addItem("-1", -1)
            field.setCurrentIndex(1 if self._to_int(value, 1) == -1 else 0)
            self._prepare_direction_combo(field)
            return field
        minimum, maximum, step = ManeuverPage._maneuver_field_range(column.key)
        field = self._spinbox(minimum, maximum, step, column.decimals)
        field.setValue(self._to_float(value))
        return field

    @staticmethod
    def _orbit_ranges() -> dict[str, tuple[float, float, float, int]]:
        return {
            "semi_major_axis_m": (1.0, 1.0e9, 1000.0, 3),
            "eccentricity": (0.0, 0.9999999999, 0.0001, 10),
            "inclination_deg": (0.0, 180.0, 0.1, 6),
            "argument_of_perigee_deg": (0.0, 360.0, 0.1, 6),
            "raan_deg": (0.0, 360.0, 0.1, 6),
            "mean_anomaly_deg": (0.0, 360.0, 0.1, 6),
        }

    @staticmethod
    def _spinbox(minimum: float, maximum: float, step: float, decimals: int) -> NoWheelDoubleSpinBox:
        field = NoWheelDoubleSpinBox()
        field.setRange(minimum, maximum)
        field.setSingleStep(step)
        field.setDecimals(decimals)
        field.setButtonSymbols(QtWidgets.QAbstractSpinBox.ButtonSymbols.NoButtons)
        field.setMinimumHeight(42)
        return field

    @staticmethod
    def _date_edit() -> NoWheelDateTimeEdit:
        field = NoWheelDateTimeEdit()
        field.setCalendarPopup(True)
        field.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        field.setTimeZone(_beijing_qtimezone())
        field.setMinimumHeight(42)
        return field

    @staticmethod
    def _prepare_direction_combo(field: NoWheelComboBox) -> None:
        field.setMaxVisibleItems(2)
        field.setSizeAdjustPolicy(QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        view = QtWidgets.QListView(field)
        view.setMinimumHeight(72)
        view.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        field.setView(view)

    @staticmethod
    def _fit_form_field(field: QtWidgets.QWidget) -> None:
        field.setMinimumWidth(260)
        field.setMaximumWidth(420)
        field.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Fixed)

    @staticmethod
    def _card() -> QtWidgets.QFrame:
        card = QtWidgets.QFrame()
        card.setProperty("role", "card")
        return card

    @staticmethod
    def _section_card() -> QtWidgets.QFrame:
        card = QtWidgets.QFrame()
        card.setProperty("role", "sectionPanel")
        return card

    @staticmethod
    def _field_label(text: str) -> QtWidgets.QLabel:
        label = QtWidgets.QLabel(text)
        label.setProperty("role", "cardCaption")
        label.setWordWrap(True)
        return label

    def _field_prompt(
        self,
        text: str,
        standard_pixmap: QtWidgets.QStyle.StandardPixmap | None,
    ) -> QtWidgets.QWidget:
        container = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        icon_label = QtWidgets.QLabel()
        icon_label.setFixedSize(24, 24)
        if standard_pixmap is not None:
            icon_label.setText("◇")
            icon_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            icon_label.setProperty("kind", "fieldIcon")
        layout.addWidget(icon_label, 0, QtCore.Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self._field_title(text), 1)
        return container

    @staticmethod
    def _field_title(text: str) -> QtWidgets.QLabel:
        label = QtWidgets.QLabel(text)
        label.setProperty("role", "sectionTitle")
        label.setWordWrap(True)
        return label

    def _section_header(
        self,
        text: str,
        standard_pixmap: QtWidgets.QStyle.StandardPixmap,
    ) -> QtWidgets.QWidget:
        container = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        icon_label = QtWidgets.QLabel()
        icon_label.setObjectName("sectionHeaderIcon")
        icon_label.setFixedSize(28, 28)
        icon_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        icon_label.setText("◎")
        layout.addWidget(icon_label, 0, QtCore.Qt.AlignmentFlag.AlignVCenter)
        title = QtWidgets.QLabel(text)
        title.setProperty("role", "cardTitle")
        layout.addWidget(title, 0, QtCore.Qt.AlignmentFlag.AlignVCenter)
        layout.addStretch(1)
        return container

    def _title_icon_label(self) -> QtWidgets.QLabel:
        label = QtWidgets.QLabel()
        label.setObjectName("dialogTitleIcon")
        label.setFixedSize(28, 28)
        label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        label.setText("⌬")
        return label

    def _apply_dialog_style(self) -> None:
        self.setStyleSheet(
            """
            QDialog#maneuverConfigDialog {
                background: qradialgradient(cx:0.50, cy:0.10, radius:1.15, fx:0.50, fy:0.10, stop:0 #0c2230, stop:0.50 #07131c, stop:1 #03090f);
                border: 1px solid #1c7d9a;
                border-radius: 22px;
            }
            QDialog#maneuverConfigDialog QWidget {
                background: transparent;
            }
            QDialog#maneuverConfigDialog QFrame[role="card"] {
                background: rgba(5, 17, 25, 0.62);
                border: 1px solid #1e7892;
                border-radius: 14px;
            }
            QDialog#maneuverConfigDialog QLabel[role="pageTitle"] {
                color: #f4fbff;
                font-size: 17pt;
                font-weight: 800;
            }
            QDialog#maneuverConfigDialog QLabel#dialogTitleIcon,
            QDialog#maneuverConfigDialog QLabel#sectionHeaderIcon {
                background: rgba(19, 48, 63, 0.9);
                border: 1px solid #27677d;
                border-radius: 14px;
                color: #3bdcff;
                font-size: 13pt;
                font-weight: 700;
            }
            QDialog#maneuverConfigDialog QToolButton#dialogCloseButton {
                background: transparent;
                color: #c4d4dc;
                border: none;
                font-size: 18pt;
                font-weight: 300;
                padding: 2px 8px;
            }
            QDialog#maneuverConfigDialog QToolButton#dialogCloseButton:hover {
                color: #ffffff;
                background: rgba(59, 169, 198, 0.18);
                border-radius: 8px;
            }
            QDialog#maneuverConfigDialog QLabel[kind="fieldIcon"] {
                color: #5bdfff;
                font-size: 13pt;
                font-weight: 700;
            }
            QDialog#maneuverConfigDialog QFrame[role="sectionPanel"] {
                background: rgba(8, 26, 36, 0.72);
                border: 1px solid #1e7892;
                border-radius: 8px;
            }
            QDialog#maneuverConfigDialog QLabel[role="cardTitle"] {
                color: #f2fbff;
                font-size: 14pt;
                font-weight: 800;
            }
            QDialog#maneuverConfigDialog QLabel[role="sectionTitle"] {
                color: #d7edf5;
                font-size: 10.5pt;
                font-weight: 600;
            }
            QDialog#maneuverConfigDialog QLabel[role="cardCaption"] {
                color: #8fb0bb;
            }
            QDialog#maneuverConfigDialog QLabel[role="tagChip"] {
                background: rgba(76, 178, 198, 0.12);
                color: #51e0ff;
                border: 1px solid #2d7081;
                border-radius: 10px;
                padding: 3px 12px;
                font-size: 9.5pt;
                font-weight: 700;
            }
            QDialog#maneuverConfigDialog QTableWidget#maneuverConfigTable {
                background: rgba(7, 20, 29, 0.72);
                alternate-background-color: rgba(14, 43, 55, 0.72);
                border: none;
                border-radius: 8px;
                gridline-color: #1d6f86;
                selection-background-color: rgba(26, 130, 156, 0.55);
            }
            QDialog#maneuverConfigDialog QTableWidget#maneuverConfigTable::item {
                background: rgba(7, 20, 29, 0.72);
                border-bottom: 1px solid #1d6f86;
                padding: 6px 8px;
            }
            QDialog#maneuverConfigDialog QTableWidget#maneuverConfigTable::item:alternate {
                background: rgba(14, 43, 55, 0.72);
            }
            QDialog#maneuverConfigDialog QHeaderView::section {
                background: #0a2b3b;
                color: #f1fbff;
                padding: 10px 8px;
                border: none;
                border-right: 1px solid #1d6f86;
                border-bottom: 1px solid #1d6f86;
                font-size: 10.5pt;
                font-weight: 800;
            }
            QDialog#maneuverConfigDialog QHeaderView::section:first {
                border-top-left-radius: 10px;
            }
            QDialog#maneuverConfigDialog QHeaderView::section:last {
                border-right: none;
                border-top-right-radius: 10px;
            }
            QDialog#maneuverConfigDialog QTableWidget#maneuverConfigTable QDoubleSpinBox,
            QDialog#maneuverConfigDialog QTableWidget#maneuverConfigTable QSpinBox,
            QDialog#maneuverConfigDialog QTableWidget#maneuverConfigTable QComboBox {
                min-height: 32px;
                padding: 4px 8px;
                border-radius: 6px;
            }
            QDialog#maneuverConfigDialog QTableWidget#maneuverConfigTable QComboBox {
                padding-right: 22px;
            }
            QDialog#maneuverConfigDialog QTableWidget#maneuverConfigTable QComboBox::drop-down {
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 22px;
                border-left: 1px solid #25586a;
            }
            QDialog#maneuverConfigDialog QTableWidget#maneuverConfigTable QComboBox::down-arrow {
                image: none;
                width: 0px;
                height: 0px;
            }
            QDialog#maneuverConfigDialog QTableWidget#maneuverConfigTable QComboBox QAbstractItemView {
                background: #07141d;
                color: #f3fbff;
                border: 1px solid #1e7892;
                selection-background-color: #153e4d;
                outline: none;
            }
            QDialog#maneuverConfigDialog QDoubleSpinBox,
            QDialog#maneuverConfigDialog QSpinBox,
            QDialog#maneuverConfigDialog QDateTimeEdit,
            QDialog#maneuverConfigDialog QComboBox {
                background: rgba(7, 19, 28, 0.98);
                border: 1px solid #2b6075;
                border-radius: 6px;
                padding: 8px 10px;
                color: #e6f6fb;
            }
            QDialog#maneuverConfigDialog QDoubleSpinBox:focus,
            QDialog#maneuverConfigDialog QSpinBox:focus,
            QDialog#maneuverConfigDialog QDateTimeEdit:focus,
            QDialog#maneuverConfigDialog QComboBox:focus {
                border: 1px solid #62d8ea;
            }
            QDialog#maneuverConfigDialog QPushButton[variant="secondary"] {
                min-width: 116px;
                border-radius: 7px;
                padding: 11px 18px;
            }
            QDialog#maneuverConfigDialog QPushButton[variant="primaryAction"] {
                min-width: 152px;
                border-radius: 7px;
                padding-left: 24px;
                padding-right: 24px;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #ff9b35, stop:1 #ff5a22);
                border: 1px solid #ffbd6a;
                color: #ffffff;
                font-size: 12pt;
                font-weight: 800;
            }
            QDialog#maneuverConfigDialog QPushButton[variant="primaryAction"]:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #ffae53, stop:1 #ff6d35);
                border: 1px solid #ffd196;
            }
            QDialog#maneuverConfigDialog QPushButton[variant="primaryAction"]:pressed {
                background: #df4b1f;
            }
            QDialog#maneuverConfigDialog QDialogButtonBox {
                button-layout: 0;
            }
            """
        )

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
    def _to_float(value: object) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _to_int(value: object, default: int) -> int:
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return int(default)

    @classmethod
    def _field_float(cls, field: QtWidgets.QWidget | None) -> float:
        if isinstance(field, QtWidgets.QDoubleSpinBox):
            return float(field.value())
        if isinstance(field, QtWidgets.QSpinBox):
            return float(field.value())
        if isinstance(field, QtWidgets.QComboBox):
            return float(field.currentData())
        return 0.0

    @classmethod
    def _field_int(cls, field: QtWidgets.QWidget | None, default: int) -> int:
        if isinstance(field, QtWidgets.QSpinBox):
            return int(field.value())
        if isinstance(field, QtWidgets.QComboBox):
            return cls._to_int(field.currentData(), default)
        if isinstance(field, QtWidgets.QDoubleSpinBox):
            return int(field.value())
        return int(default)


def _mean_anomaly_to_true_anomaly_deg(mean_anomaly_deg: float, eccentricity: float) -> float:
    mean_anomaly_rad = math.radians(mean_anomaly_deg % 360.0)
    eccentric_anomaly = mean_anomaly_rad
    for _ in range(30):
        residual = eccentric_anomaly - eccentricity * math.sin(eccentric_anomaly) - mean_anomaly_rad
        derivative = 1.0 - eccentricity * math.cos(eccentric_anomaly)
        if abs(derivative) < 1e-14:
            break
        step = residual / derivative
        eccentric_anomaly -= step
        if abs(step) < 1e-13:
            break

    true_anomaly = 2.0 * math.atan2(
        math.sqrt(1.0 + eccentricity) * math.sin(0.5 * eccentric_anomaly),
        math.sqrt(1.0 - eccentricity) * math.cos(0.5 * eccentric_anomaly),
    )
    return math.degrees(true_anomaly) % 360.0


def _entry_position_eci_m(orbit: dict[str, float], true_anomaly_deg: float) -> np.ndarray:
    semi_major_axis_m = float(orbit["semi_major_axis_m"])
    eccentricity = float(orbit["eccentricity"])
    inclination_rad = math.radians(float(orbit["inclination_deg"]))
    raan_rad = math.radians(float(orbit["raan_deg"]))
    argp_rad = math.radians(float(orbit["argument_of_perigee_deg"]))
    true_anomaly_rad = math.radians(true_anomaly_deg)

    p = semi_major_axis_m * (1.0 - eccentricity * eccentricity)
    radius_m = p / (1.0 + eccentricity * math.cos(true_anomaly_rad))
    argument_of_latitude = argp_rad + true_anomaly_rad

    cos_raan = math.cos(raan_rad)
    sin_raan = math.sin(raan_rad)
    cos_i = math.cos(inclination_rad)
    sin_i = math.sin(inclination_rad)
    cos_u = math.cos(argument_of_latitude)
    sin_u = math.sin(argument_of_latitude)

    return np.asarray(
        [
            radius_m * (cos_raan * cos_u - sin_raan * sin_u * cos_i),
            radius_m * (sin_raan * cos_u + cos_raan * sin_u * cos_i),
            radius_m * sin_u * sin_i,
        ],
        dtype=np.float64,
    )


class ManeuverPage(QtWidgets.QWidget):
    strategy_changed = QtCore.Signal(object)

    _COLUMNS = (
        _StrategyColumn("maneuver_index", "maneuver.table.maneuver_index", 0),
        _StrategyColumn("Tn_start_min", "maneuver.table.Tn_start_min", 3),
        _StrategyColumn("burn_duration_min", "maneuver.table.burn_duration_min", 3),
        _StrategyColumn("control_fuel_%", "maneuver.table.control_fuel_percent", 3),
        _StrategyColumn("settle_duration_s", "maneuver.table.settle_duration_s", 3),
        _StrategyColumn("delta_deg", "maneuver.table.delta_deg", 3),
        _StrategyColumn("dv_direction", "maneuver.table.dv_direction", 0),
        _StrategyColumn("orbit_control_thrust_n", "maneuver.table.orbit_control_thrust_n", 3),
        _StrategyColumn("orbit_control_isp_s", "maneuver.table.orbit_control_isp_s", 3),
        _StrategyColumn("settle_thrust_n", "maneuver.table.settle_thrust_n", 3),
        _StrategyColumn("settle_isp_s", "maneuver.table.settle_isp_s", 3),
    )

    def __init__(
        self,
        i18n: I18nManager,
        workspace: ProjectWorkspace,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._i18n = i18n
        self._workspace = workspace
        self._current_strategy: dict[str, Any] = default_maneuver_strategy_payload()
        self._suppress_emit = False
        self._status_role = "statusDisconnected"
        self._last_result_path: Path | None = None
        self._initial_value_labels: dict[str, QtWidgets.QLabel] = {}
        self._top_level_labels: dict[str, QtWidgets.QLabel] = {}
        self._top_level_fields: dict[str, QtWidgets.QDoubleSpinBox] = {}
        self._top_level_text_labels: dict[str, QtWidgets.QLabel] = {}
        self._top_level_text_fields: dict[str, QtWidgets.QLineEdit] = {}
        self._t0_orbit_labels: dict[str, QtWidgets.QLabel] = {}
        self._t0_orbit_fields: dict[str, QtWidgets.QDoubleSpinBox] = {}
        self._entry_aux_labels: dict[str, QtWidgets.QLabel] = {}
        self._entry_aux_values: dict[str, QtWidgets.QLabel] = {}
        self._config_metric_labels: dict[str, QtWidgets.QLabel] = {}
        self._config_metric_values: dict[str, QtWidgets.QLabel] = {}
        self._maneuver_field_labels: list[dict[str, QtWidgets.QLabel]] = []
        self._maneuver_fields: list[dict[str, QtWidgets.QWidget]] = []
        self._result_value_labels: dict[str, QtWidgets.QLabel] = {}
        self._orbit_3d_view: OrbitPlot3D | None = None
        self._ground_track_plot: pg.PlotWidget | None = None
        self._ground_track_curves: list[pg.PlotDataItem] = []
        self._ground_track_markers: list[pg.PlotDataItem] = []
        self._ground_track_start_marker: pg.PlotDataItem | None = None
        self._ground_track_start_label: pg.TextItem | None = None
        self._ground_track_start_row: dict[str, Any] | None = None
        self._maneuver_number_labels: list[pg.TextItem] = []
        self._maneuver_number_label_outlines: list[tuple[pg.TextItem, float, float]] = []
        self._ground_track_axis_labels: list[pg.TextItem] = []
        self._ground_track_grid_lines: list[pg.PlotDataItem] = []
        self._ground_track_maneuver_summaries: list[dict[str, Any]] = []
        self._ground_track_map_items: list[pg.ImageItem] = []
        self._strategy_table: QtWidgets.QTableWidget | None = None

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(18)

        eyebrow = QtWidgets.QLabel("SMART · MANEUVER STRATEGY")
        eyebrow.setProperty("role", "pageEyebrow")
        root.addWidget(eyebrow)

        self._title_label = QtWidgets.QLabel()
        self._title_label.setProperty("role", "pageTitle")
        root.addWidget(self._title_label)

        accent_rule = QtWidgets.QFrame()
        accent_rule.setProperty("role", "accentRule")
        accent_rule.setFixedHeight(2)
        accent_rule.setMaximumWidth(220)
        root.addWidget(accent_rule)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        root.addWidget(splitter, 1)

        splitter.addWidget(self._build_left_panel())
        splitter.addWidget(self._build_visualization_panel())
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 4)
        splitter.setSizes([720, 820])

        self._status_label = QtWidgets.QLabel()
        self._status_label.setWordWrap(True)
        root.addWidget(self._status_label)

        self._i18n.language_changed.connect(self.retranslate)
        self.retranslate()
        self.refresh_from_workspace()

    def _build_strategy_card(self) -> QtWidgets.QWidget:
        card = QtWidgets.QFrame()
        card.setProperty("role", "card")
        layout = QtWidgets.QVBoxLayout(card)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        self._strategy_header_label = QtWidgets.QLabel()
        self._strategy_header_label.setProperty("role", "cardTitle")
        layout.addWidget(self._strategy_header_label)

        self._strategy_path_label = QtWidgets.QLabel()
        self._strategy_path_label.setProperty("role", "cardCaption")
        self._strategy_path_label.setWordWrap(True)
        layout.addWidget(self._strategy_path_label)

        self._strategy_count_label = QtWidgets.QLabel()
        self._strategy_count_label.setProperty("role", "pageBody")
        layout.addWidget(self._strategy_count_label)

        self._strategy_table = QtWidgets.QTableWidget(0, len(self._COLUMNS))
        self._strategy_table.setAlternatingRowColors(True)
        self._strategy_table.verticalHeader().setVisible(False)
        self._strategy_table.horizontalHeader().setStretchLastSection(False)
        self._strategy_table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self._strategy_table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self._strategy_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self._strategy_table.setMinimumHeight(180)
        layout.addWidget(self._strategy_table)

        self._strategy_tabs = QtWidgets.QTabWidget()
        self._strategy_tabs.setVisible(False)

        button_row = QtWidgets.QHBoxLayout()
        button_row.setSpacing(10)
        self._reload_button = QtWidgets.QPushButton()
        self._reload_button.clicked.connect(self.refresh_from_workspace)
        button_row.addWidget(self._reload_button)

        self._edit_config_button = QtWidgets.QPushButton()
        self._edit_config_button.clicked.connect(self._open_config_dialog)
        button_row.addWidget(self._edit_config_button)
        button_row.addStretch(1)
        layout.addLayout(button_row)
        return card

    def _build_left_panel(self) -> QtWidgets.QWidget:
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)

        canvas = QtWidgets.QWidget()
        scroll.setWidget(canvas)

        layout = QtWidgets.QVBoxLayout(canvas)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        layout.addWidget(self._build_initial_state_card())
        layout.addWidget(self._build_calculation_card())
        layout.addStretch(1)
        return scroll

    def _build_visualization_panel(self) -> QtWidgets.QWidget:
        panel = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        layout.addWidget(self._build_ground_track_card(), 3)
        layout.addWidget(self._build_orbit_3d_card(), 3)
        return panel

    def _build_ground_track_card(self) -> QtWidgets.QWidget:
        card = QtWidgets.QFrame()
        card.setProperty("role", "card")
        layout = QtWidgets.QVBoxLayout(card)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        self._ground_track_title_label = QtWidgets.QLabel()
        self._ground_track_title_label.setProperty("role", "cardTitle")
        layout.addWidget(self._ground_track_title_label)

        view_box = _GroundTrackViewBox()
        self._ground_track_plot = pg.PlotWidget(viewBox=view_box)
        self._ground_track_plot.setMinimumHeight(300)
        self._ground_track_plot.setBackground("#03101a")
        self._ground_track_plot.showGrid(x=False, y=False)
        self._ground_track_plot.setMenuEnabled(False)
        self._ground_track_plot.plotItem.hideButtons()
        self._ground_track_plot.plotItem.setMouseEnabled(x=True, y=False)
        self._ground_track_plot.plotItem.setClipToView(True)
        self._ground_track_plot.plotItem.setContentsMargins(0, 0, 0, 0)
        self._ground_track_plot.plotItem.setLabel("bottom", "")
        self._ground_track_plot.plotItem.setLabel("left", "")
        self._ground_track_plot.plotItem.getAxis("bottom").setStyle(showValues=False)
        self._ground_track_plot.plotItem.getAxis("left").setStyle(showValues=False)
        self._ground_track_plot.plotItem.getAxis("top").setStyle(showValues=False)
        self._ground_track_plot.plotItem.getAxis("right").setStyle(showValues=False)
        self._ground_track_plot.plotItem.getAxis("bottom").setPen(pg.mkPen("#1ca8c7", width=1.2))
        self._ground_track_plot.plotItem.getAxis("left").setPen(pg.mkPen("#1ca8c7", width=1.2))
        view_box.sigRangeChanged.connect(self._refresh_ground_track_annotations)
        self._ground_track_plot.setXRange(-180.0, 180.0, padding=0.0)
        self._ground_track_plot.setYRange(-90.0, 90.0, padding=0.0)
        world_map = _load_world_map_rgba()
        if world_map is not None:
            for offset in range(-2, 3):
                map_item = pg.ImageItem(axisOrder="row-major")
                map_item.setImage(world_map)
                map_item.setRect(QtCore.QRectF(-180.0 + 360.0 * offset, -90.0, 360.0, 180.0))
                map_item.setOpacity(0.92)
                map_item.setZValue(-20)
                self._ground_track_plot.addItem(map_item)
                self._ground_track_map_items.append(map_item)
        self._add_ground_track_grid_lines()
        self._add_ground_track_axis_labels()
        self._ground_track_curves = [
            self._ground_track_plot.plot(pen=pg.mkPen("#f6fbff", width=2.2))
            for _ in range(5)
        ]
        self._ground_track_markers = [
            self._ground_track_plot.plot(
                pen=None,
                symbol="o",
                symbolSize=9,
                symbolBrush="#ff6a2f",
                symbolPen=pg.mkPen("#fff4dc", width=1.4),
            )
            for _ in range(5)
        ]
        self._ground_track_start_marker = self._ground_track_plot.plot(
            pen=None,
            symbol="star",
            symbolSize=17,
            symbolBrush="#a8ff44",
            symbolPen=pg.mkPen("#d9ffc0", width=1.5),
        )
        self._ground_track_start_label = pg.TextItem(
            "",
            color="#f6fdff",
            anchor=(-0.15, 1.15),
            border=pg.mkPen("#27d8f6", width=1.2),
            fill=pg.mkBrush(7, 28, 41, 235),
        )
        self._ground_track_start_label.setZValue(20)
        self._ground_track_plot.addItem(self._ground_track_start_label)
        layout.addWidget(self._ground_track_plot, 1)
        return card

    def _build_orbit_3d_card(self) -> QtWidgets.QWidget:
        card = QtWidgets.QFrame()
        card.setProperty("role", "card")
        layout = QtWidgets.QVBoxLayout(card)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        self._orbit_3d_title_label = QtWidgets.QLabel()
        self._orbit_3d_title_label.setProperty("role", "cardTitle")
        layout.addWidget(self._orbit_3d_title_label)

        try:
            self._orbit_3d_view = OrbitPlot3D()
            self._orbit_3d_view.set_visual_style(
                background_color="#07131f",
                orbit_color=(0.0, 0.82, 1.0, 1.0),
                marker_color=(1.0, 0.58, 0.12, 1.0),
                orbit_width=3.2,
            )
            layout.addWidget(self._orbit_3d_view, 1)
        except Exception as exc:  # pragma: no cover - depends on local OpenGL runtime
            self._orbit_3d_view = None
            message = QtWidgets.QLabel(str(exc))
            message.setProperty("role", "pageBody")
            message.setWordWrap(True)
            message.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(message, 1)
        return card

    def _build_initial_state_card(self) -> QtWidgets.QWidget:
        card = QtWidgets.QFrame()
        card.setProperty("role", "card")
        card.setObjectName("maneuverConfigSummaryCard")
        layout = QtWidgets.QVBoxLayout(card)
        layout.setContentsMargins(22, 22, 22, 22)
        layout.setSpacing(16)
        card.setStyleSheet(
            """
            QFrame#maneuverConfigSummaryCard {
                background: qradialgradient(cx:0.50, cy:0.0, radius:1.10, fx:0.50, fy:0.0, stop:0 #10293a, stop:0.48 #071821, stop:1 #040b11);
                border: 1px solid #1eb8d2;
                border-radius: 18px;
            }
            QFrame#maneuverConfigSummaryCard QFrame#maneuverMetricTile {
                background: rgba(8, 24, 35, 0.86);
                border: 1px solid #1789a3;
                border-radius: 8px;
            }
            QFrame#maneuverConfigSummaryCard QLabel[kind="metricIcon"] {
                color: #35e5ff;
                font-size: 16pt;
                font-weight: 800;
            }
            QFrame#maneuverConfigSummaryCard QLabel[role="cardCaption"] {
                color: #a9cce0;
                font-size: 10pt;
                font-weight: 700;
            }
            QFrame#maneuverConfigSummaryCard QLabel[kind="metricValue"] {
                color: #f7fbff;
                font-size: 12pt;
                font-weight: 800;
            }
            QFrame#maneuverConfigSummaryCard QLabel[kind="sectionIcon"] {
                color: #35e5ff;
                font-size: 16pt;
                font-weight: 800;
            }
            QFrame#maneuverConfigSummaryCard QTableWidget#maneuverSummaryTable {
                background: rgba(6, 17, 25, 0.85);
                alternate-background-color: rgba(13, 37, 49, 0.82);
                border: 1px solid #16b7d0;
                border-radius: 8px;
                gridline-color: #146a80;
                selection-background-color: rgba(26, 130, 156, 0.55);
                color: #f3fbff;
            }
            QFrame#maneuverConfigSummaryCard QTableWidget#maneuverSummaryTable::item {
                padding: 8px;
            }
            QFrame#maneuverConfigSummaryCard QHeaderView::section {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #0a6072, stop:1 #0a3342);
                color: #f6fdff;
                border: none;
                border-right: 1px solid #1bb4ce;
                border-bottom: 1px solid #1bb4ce;
                padding: 10px;
                font-size: 11pt;
                font-weight: 800;
            }
            """
        )

        header_row = QtWidgets.QHBoxLayout()
        header_row.setSpacing(10)
        header_icon = QtWidgets.QLabel("◎")
        header_icon.setFixedSize(32, 32)
        header_icon.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        header_icon.setProperty("kind", "sectionIcon")
        header_row.addWidget(header_icon, 0, QtCore.Qt.AlignmentFlag.AlignVCenter)
        self._initial_state_header_label = QtWidgets.QLabel()
        self._initial_state_header_label.setProperty("role", "cardTitle")
        header_row.addWidget(self._initial_state_header_label, 0, QtCore.Qt.AlignmentFlag.AlignVCenter)
        header_row.addStretch(1)
        layout.addLayout(header_row)

        self._initial_state_caption_label = QtWidgets.QLabel()
        self._initial_state_caption_label.setProperty("role", "cardCaption")
        self._initial_state_caption_label.setWordWrap(True)
        self._initial_state_caption_label.setVisible(False)

        metrics_grid = QtWidgets.QGridLayout()
        metrics_grid.setHorizontalSpacing(12)
        metrics_grid.setVerticalSpacing(12)

        metric_specs = (
            ("perigee_altitude_m", "△", "entry_aux"),
            ("apogee_altitude_m", "△", "entry_aux"),
            ("inclination_deg", "∠", "orbit"),
            ("launch_mass_kg", "▣", "top"),
            ("maneuver_count", "⟳", "count"),
        )
        for index, (key, icon, kind) in enumerate(metric_specs):
            label = QtWidgets.QLabel()
            label.setProperty("role", "cardCaption")
            value_label = QtWidgets.QLabel("--")
            value_label.setProperty("kind", "metricValue")
            value_label.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
            metrics_grid.addWidget(self._metric_tile(icon, label, value_label), index // 2, index % 2)
            if kind == "entry_aux":
                self._entry_aux_labels[key] = label
                self._entry_aux_values[key] = value_label
            elif kind == "orbit":
                self._t0_orbit_labels[key] = label
                self._initial_value_labels[key] = value_label
            elif kind == "top":
                self._top_level_text_labels[key] = label
                self._config_metric_values[key] = value_label
            else:
                self._config_metric_labels[key] = label
                self._config_metric_values[key] = value_label

        metrics_grid.setColumnStretch(0, 1)
        metrics_grid.setColumnStretch(1, 1)
        layout.addLayout(metrics_grid)

        section_row = QtWidgets.QHBoxLayout()
        section_row.setSpacing(8)
        section_icon = QtWidgets.QLabel("⊙")
        section_icon.setProperty("kind", "sectionIcon")
        section_row.addWidget(section_icon, 0, QtCore.Qt.AlignmentFlag.AlignVCenter)
        self._entry_aux_title_label = QtWidgets.QLabel()
        self._entry_aux_title_label.setProperty("role", "cardTitle")
        section_row.addWidget(self._entry_aux_title_label, 0, QtCore.Qt.AlignmentFlag.AlignVCenter)
        section_row.addStretch(1)
        layout.addLayout(section_row)

        self._strategy_header_label = QtWidgets.QLabel()
        self._strategy_header_label.setVisible(False)

        self._strategy_path_label = QtWidgets.QLabel()
        self._strategy_path_label.setVisible(False)

        self._strategy_count_label = QtWidgets.QLabel()
        self._strategy_count_label.setVisible(False)

        self._strategy_table = QtWidgets.QTableWidget(0, 2)
        self._strategy_table.setObjectName("maneuverSummaryTable")
        self._strategy_table.setAlternatingRowColors(True)
        self._strategy_table.verticalHeader().setVisible(False)
        self._strategy_table.horizontalHeader().setStretchLastSection(True)
        self._strategy_table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Stretch)
        self._strategy_table.horizontalHeader().setMinimumHeight(42)
        self._strategy_table.setShowGrid(True)
        self._strategy_table.setGridStyle(QtCore.Qt.PenStyle.SolidLine)
        self._strategy_table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self._strategy_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self._strategy_table.setMinimumHeight(220)
        layout.addWidget(self._strategy_table)

        self._strategy_tabs = QtWidgets.QTabWidget()
        self._strategy_tabs.setVisible(False)

        button_row = QtWidgets.QHBoxLayout()
        button_row.setSpacing(10)
        self._reload_button = QtWidgets.QPushButton()
        self._reload_button.setProperty("variant", "secondary")
        self._reload_button.clicked.connect(self.refresh_from_workspace)
        button_row.addWidget(self._reload_button)

        self._edit_config_button = QtWidgets.QPushButton()
        self._edit_config_button.setProperty("variant", "secondary")
        self._edit_config_button.clicked.connect(self._open_config_dialog)
        button_row.addWidget(self._edit_config_button)
        button_row.addStretch(1)
        layout.addLayout(button_row)
        return card

    def _build_calculation_card(self) -> QtWidgets.QWidget:
        card = QtWidgets.QFrame()
        card.setProperty("role", "card")
        layout = QtWidgets.QVBoxLayout(card)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(14)

        self._calculation_header_label = QtWidgets.QLabel()
        self._calculation_header_label.setProperty("role", "cardTitle")
        layout.addWidget(self._calculation_header_label)

        self._calculate_button = QtWidgets.QPushButton()
        self._calculate_button.setProperty("variant", "primaryAction")
        self._calculate_button.clicked.connect(self.calculate_strategy)
        layout.addWidget(self._calculate_button)

        self._open_result_button = QtWidgets.QPushButton()
        self._open_result_button.clicked.connect(self._open_result_csv)
        self._open_result_button.setEnabled(False)
        layout.addWidget(self._open_result_button)

        self._maneuver_results_label = QtWidgets.QLabel()
        self._maneuver_results_label.setProperty("role", "cardCaption")
        layout.addWidget(self._maneuver_results_label)

        self._maneuver_result_table = QtWidgets.QTableWidget(0, 7)
        self._maneuver_result_table.setAlternatingRowColors(True)
        self._maneuver_result_table.verticalHeader().setVisible(False)
        self._maneuver_result_table.horizontalHeader().setStretchLastSection(False)
        self._maneuver_result_table.horizontalHeader().setSectionResizeMode(
            QtWidgets.QHeaderView.ResizeMode.ResizeToContents
        )
        self._maneuver_result_table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self._maneuver_result_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self._maneuver_result_table.setMinimumHeight(220)
        layout.addWidget(self._maneuver_result_table, 1)

        result_grid = QtWidgets.QGridLayout()
        result_grid.setHorizontalSpacing(14)
        result_grid.setVerticalSpacing(10)
        for row, key in enumerate(("csv_path", "samples", "final_time", "final_mass", "final_position")):
            caption = QtWidgets.QLabel()
            caption.setProperty("role", "cardCaption")
            caption.setWordWrap(True)
            value = QtWidgets.QLabel("--")
            value.setProperty("role", "pageBody")
            value.setWordWrap(True)
            result_grid.addWidget(caption, row, 0)
            result_grid.addWidget(value, row, 1)
            self._result_value_labels[f"{key}_caption"] = caption
            self._result_value_labels[key] = value
        layout.addLayout(result_grid)
        layout.addStretch(1)
        return card

    def refresh_from_workspace(self) -> None:
        if self._workspace.current_project is None:
            self._current_strategy = default_maneuver_strategy_payload(0)
            self._set_initial_state_fields(self._current_strategy)
            self._set_strategy_rows([])
            self._set_controls_enabled(False)
            self._last_result_path = None
            self._open_result_button.setEnabled(False)
            self._refresh_strategy_path_label()
            self._update_strategy_count_label()
            self._clear_result_summary()
            self._set_status("statusDisconnected", self._i18n.t("maneuver.status.no_project"))
            return

        try:
            strategy = self._workspace.load_maneuver_strategy()
        except Exception as exc:
            self._set_controls_enabled(False)
            self._set_status("statusDisconnected", self._i18n.t("maneuver.status.load_failed", error=str(exc)))
            return

        self._current_strategy = strategy if strategy is not None else default_maneuver_strategy_payload()
        self._set_initial_state_fields(self._current_strategy)
        self._set_strategy_rows(self._current_strategy.get("maneuvers", []))
        self._set_controls_enabled(True)
        self._refresh_strategy_path_label()
        self._update_strategy_count_label()
        self._clear_result_summary()
        result_loaded = self._load_existing_result_summary()
        if result_loaded is True:
            self._set_status("statusReady", self._i18n.t("maneuver.status.loaded_with_result"))
        elif result_loaded is False:
            self._set_status("statusReady", self._i18n.t("maneuver.status.loaded"))

    def save_strategy(self) -> Path | None:
        if self._workspace.current_project is None:
            self._set_status("statusDisconnected", self._i18n.t("maneuver.status.no_project"))
            return None

        try:
            saved_path = self._workspace.save_maneuver_strategy(self.strategy())
            loaded = self._workspace.load_maneuver_strategy()
        except Exception as exc:
            self._set_status("statusDisconnected", self._i18n.t("maneuver.status.save_failed", error=str(exc)))
            return None

        if loaded is not None:
            self._current_strategy = loaded
            self._set_initial_state_fields(loaded)
            self._set_strategy_rows(loaded.get("maneuvers", []))
        self._refresh_strategy_path_label()
        self._update_strategy_count_label()
        self._set_status("statusReady", self._i18n.t("maneuver.status.saved", path=str(saved_path)))
        return saved_path

    def calculate_strategy(self) -> None:
        if self._workspace.current_project is None:
            self._set_status("statusDisconnected", self._i18n.t("maneuver.status.no_project"))
            return

        strategy_path = self.save_strategy()
        if strategy_path is None:
            return

        output_path = self._workspace.data_dir() / "full_orbit_history.csv"
        app = QtWidgets.QApplication.instance()
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.CursorShape.WaitCursor)
        self._set_status("statusLoading", self._i18n.t("maneuver.status.calculating"))
        if app is not None:
            app.processEvents()

        try:
            module = _load_dynamics_module()
            csv_path, rows = module.simulate_with_maneuver_strategy_config(
                strategy_config_path=strategy_path,
                output_csv_path=output_path,
                sample_interval_s=60.0,
                max_step_s=10.0,
                coast_max_step_s=30.0,
                extra_free_flight_s=24.0 * 3600.0,
            )
            strategy_steps = module.load_maneuver_strategy_steps(strategy_path)
            maneuver_summaries = module.build_maneuver_result_rows(strategy_steps, rows)
        except Exception as exc:
            self._set_status("statusDisconnected", self._i18n.t("maneuver.status.calc_failed", error=str(exc)))
            return
        finally:
            if QtWidgets.QApplication.overrideCursor() is not None:
                QtWidgets.QApplication.restoreOverrideCursor()

        self._last_result_path = Path(csv_path)
        self._open_result_button.setEnabled(self._last_result_path.exists())
        self._update_result_summary(self._last_result_path, rows, maneuver_summaries)
        self._set_status(
            "statusReady",
            self._i18n.t("maneuver.status.calc_done", path=str(self._last_result_path)),
        )

    def strategy(self) -> dict[str, Any]:
        return dict(self._current_strategy)

    def _set_initial_state_fields(self, strategy: dict[str, Any]) -> None:
        defaults = default_maneuver_strategy_payload(0)
        orbit_defaults = defaults["t0_orbit"]
        orbit_payload = strategy.get("t0_orbit", {})
        if not isinstance(orbit_payload, dict):
            orbit_payload = {}

        for key, label in self._initial_value_labels.items():
            label.setText(f"{float(orbit_payload.get(key, orbit_defaults[key])):.6f}")
        if "launch_mass_kg" in self._config_metric_values:
            mass = float(strategy.get("launch_mass_kg", defaults["launch_mass_kg"]))
            self._config_metric_values["launch_mass_kg"].setText(f"{mass:.3f} kg")
        self._update_entry_auxiliary_values()

    def _set_strategy_rows(self, rows: object) -> None:
        self._maneuver_field_labels.clear()
        self._maneuver_fields.clear()
        self._strategy_tabs.clear()
        if self._strategy_table is None:
            return
        self._strategy_table.setRowCount(0)
        if not isinstance(rows, list):
            return
        for row_payload in rows:
            if not isinstance(row_payload, dict):
                continue
            row = self._strategy_table.rowCount()
            self._strategy_table.insertRow(row)
            for column_index, (key, decimals) in enumerate((("Tn_start_min", 3), ("burn_duration_min", 3))):
                value = row_payload.get(key, 0.0)
                item = QtWidgets.QTableWidgetItem(f"{self._to_float(str(value)):.{decimals}f}")
                item.setTextAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
                self._strategy_table.setItem(row, column_index, item)
        row_height = 42
        for row in range(self._strategy_table.rowCount()):
            self._strategy_table.setRowHeight(row, row_height)
        self._strategy_table.setMinimumHeight(
            self._strategy_table.horizontalHeader().height()
            + row_height * max(1, self._strategy_table.rowCount())
            + self._strategy_table.frameWidth() * 2
            + 8
        )
        self._update_maneuver_tab_labels()

    def _append_strategy_tab(self, row_payload: dict[str, Any]) -> None:
        row = self._strategy_tabs.count()
        tab = QtWidgets.QWidget()
        grid = QtWidgets.QGridLayout(tab)
        grid.setContentsMargins(14, 14, 14, 14)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(10)

        labels: dict[str, QtWidgets.QLabel] = {}
        fields: dict[str, QtWidgets.QWidget] = {}
        for column_index, column in enumerate(self._COLUMNS):
            default_value = self._default_maneuver_value(row, column)
            value = row_payload.get(column.key, default_value)
            label = QtWidgets.QLabel(self._i18n.t(column.label_key))
            label.setProperty("role", "cardCaption")
            field = self._make_maneuver_field(column, value)
            field.setMinimumWidth(150)
            field.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Fixed)

            grid_row = column_index // 2
            grid_col = (column_index % 2) * 2
            grid.addWidget(label, grid_row, grid_col)
            grid.addWidget(field, grid_row, grid_col + 1)
            labels[column.key] = label
            fields[column.key] = field

        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        self._maneuver_field_labels.append(labels)
        self._maneuver_fields.append(fields)
        self._strategy_tabs.addTab(tab, "")
        self._update_maneuver_tab_labels()

    def _append_maneuver(self) -> None:
        row = self._strategy_tabs.count()
        step = default_maneuver_strategy_payload(1)["maneuvers"][0]
        step["maneuver_index"] = row + 1
        self._suppress_emit = True
        self._strategy_tabs.blockSignals(True)
        try:
            self._append_strategy_tab(step)
        finally:
            self._strategy_tabs.blockSignals(False)
            self._suppress_emit = False
        self._strategy_tabs.setCurrentIndex(row)
        self._emit_strategy_changed()

    def _remove_selected_maneuver(self) -> None:
        row = self._strategy_tabs.currentIndex()
        if row < 0:
            row = self._strategy_tabs.count() - 1
        if row < 0:
            return
        self._strategy_tabs.removeTab(row)
        del self._maneuver_field_labels[row]
        del self._maneuver_fields[row]
        self._update_maneuver_tab_labels()
        self._emit_strategy_changed()

    def _on_entry_parameter_changed(self) -> None:
        self._update_entry_auxiliary_values()
        self._emit_strategy_changed()

    def _emit_strategy_changed(self) -> None:
        if self._suppress_emit:
            return
        self._update_maneuver_tab_labels()
        self._update_strategy_count_label()
        self.strategy_changed.emit(self.strategy())

    def _update_entry_auxiliary_values(self) -> None:
        if not self._entry_aux_values:
            return

        orbit_payload = self._current_strategy.get("t0_orbit", {})
        orbit = orbit_payload if isinstance(orbit_payload, dict) else {}
        try:
            t0_epoch = self._resolved_t0_epoch_text()
            eccentricity = float(orbit["eccentricity"])
            semi_major_axis_m = float(orbit["semi_major_axis_m"])
            true_anomaly_deg = _mean_anomaly_to_true_anomaly_deg(
                float(orbit["mean_anomaly_deg"]),
                eccentricity,
            )
            orbit_state = dict(orbit)
            orbit_state["raan_deg"] = inertial_raan_deg_from_ascending_node_longitude_deg(
                float(orbit["raan_deg"]),
                t0_epoch,
            )
            position_eci_m = _entry_position_eci_m(orbit_state, true_anomaly_deg)
            subpoint = subsatellite_point_from_eci(position_eci_m, epoch_utc=t0_epoch)
            perigee_altitude_m = semi_major_axis_m * (1.0 - eccentricity) - _EARTH_RADIUS_KM * 1000.0
            apogee_altitude_m = semi_major_axis_m * (1.0 + eccentricity) - _EARTH_RADIUS_KM * 1000.0
        except (KeyError, ValueError, ZeroDivisionError):
            for label in self._entry_aux_values.values():
                label.setText("--")
            return

        if "apogee_altitude_m" in self._entry_aux_values:
            self._entry_aux_values["apogee_altitude_m"].setText(f"{apogee_altitude_m:.3f} m")
        if "perigee_altitude_m" in self._entry_aux_values:
            self._entry_aux_values["perigee_altitude_m"].setText(f"{perigee_altitude_m:.3f} m")

    def _open_config_dialog(self) -> None:
        dialog = _ManeuverConfigDialog(self._i18n, self.strategy(), self._COLUMNS, self)
        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        self._current_strategy = dialog.strategy()
        self._set_initial_state_fields(self._current_strategy)
        self._set_strategy_rows(self._current_strategy.get("maneuvers", []))
        self._update_strategy_count_label()
        self.save_strategy()
        self.strategy_changed.emit(self.strategy())

    def _make_maneuver_field(self, column: _StrategyColumn, value: object) -> QtWidgets.QWidget:
        if column.key == "maneuver_index":
            field = NoWheelSpinBox()
            field.setRange(1, 999)
            field.setSingleStep(1)
            field.setButtonSymbols(QtWidgets.QAbstractSpinBox.ButtonSymbols.NoButtons)
            field.setValue(self._to_int(str(value), 1))
            field.valueChanged.connect(lambda _value: self._emit_strategy_changed())
            return field

        if column.key == "dv_direction":
            field = _DirectionComboBox()
            field.addItem("1", 1)
            field.addItem("-1", -1)
            field.setCurrentIndex(1 if self._to_int(str(value), 1) == -1 else 0)
            _ManeuverConfigDialog._prepare_direction_combo(field)
            field.currentIndexChanged.connect(lambda _value: self._emit_strategy_changed())
            return field

        minimum, maximum, step = self._maneuver_field_range(column.key)
        field = NoWheelDoubleSpinBox()
        field.setRange(minimum, maximum)
        field.setSingleStep(step)
        field.setDecimals(column.decimals)
        field.setButtonSymbols(QtWidgets.QAbstractSpinBox.ButtonSymbols.NoButtons)
        field.setValue(self._to_float(str(value)))
        field.valueChanged.connect(lambda _value: self._emit_strategy_changed())
        return field

    def _set_controls_enabled(self, enabled: bool) -> None:
        self._strategy_tabs.setEnabled(enabled)
        self._reload_button.setEnabled(enabled)
        self._edit_config_button.setEnabled(enabled)
        self._calculate_button.setEnabled(enabled)

    def _refresh_strategy_path_label(self) -> None:
        if self._workspace.current_project is None:
            text = self._i18n.t("maneuver.config_path.none")
        else:
            text = self._i18n.t("maneuver.config_path", path=str(self._workspace.maneuver_strategy_path()))
        self._strategy_path_label.setText(text)

    def _update_strategy_count_label(self) -> None:
        rows = self._current_strategy.get("maneuvers", [])
        count = len(rows) if isinstance(rows, list) else 0
        self._strategy_count_label.setText(
            self._i18n.t("maneuver.count", count=count)
        )
        if "maneuver_count" in self._config_metric_values:
            self._config_metric_values["maneuver_count"].setText(f"{count} 次")

    def _clear_result_summary(self) -> None:
        for key in ("csv_path", "samples", "final_time", "final_mass", "final_position"):
            self._result_value_labels[key].setText("--")
        self._maneuver_result_table.setRowCount(0)
        self._clear_visualizations()

    def _update_result_summary(
        self,
        csv_path: Path,
        rows: list[dict[str, Any]],
        maneuver_summaries: list[dict[str, Any]],
    ) -> None:
        self._set_maneuver_result_rows(maneuver_summaries)
        self._update_visualizations(rows, maneuver_summaries)
        self._result_value_labels["csv_path"].setText(str(csv_path))
        self._result_value_labels["samples"].setText(str(len(rows)))
        if not rows:
            self._result_value_labels["final_time"].setText("--")
            self._result_value_labels["final_mass"].setText("--")
            self._result_value_labels["final_position"].setText("--")
            return

        final = rows[-1]
        self._result_value_labels["final_time"].setText(
            self._i18n.t("maneuver.result.final_time_value", value=float(final["elapsed_time_min"]))
        )
        self._result_value_labels["final_mass"].setText(
            self._i18n.t("maneuver.result.final_mass_value", value=float(final["mass_kg"]))
        )
        self._result_value_labels["final_position"].setText(
            self._i18n.t(
                "maneuver.result.final_position_value",
                lon=float(final["subsatellite_longitude_deg"]),
                lat=float(final["subsatellite_latitude_deg"]),
            )
        )

    def _set_maneuver_result_rows(self, summaries: list[dict[str, Any]]) -> None:
        self._maneuver_result_table.setRowCount(0)
        for summary in summaries:
            row = self._maneuver_result_table.rowCount()
            self._maneuver_result_table.insertRow(row)
            values = (
                str(int(summary["maneuver_index"])),
                f"{float(summary['elapsed_time_min']):.3f}",
                f"{float(summary['semi_major_axis_m']):.3f}",
                f"{float(summary['inclination_deg']):.6f}",
                f"{float(summary['subsatellite_longitude_deg']):.6f}",
                f"{float(summary['subsatellite_latitude_deg']):.6f}",
                f"{float(summary['propellant_consumed_kg']):.6f}",
            )
            for column, value in enumerate(values):
                item = QtWidgets.QTableWidgetItem(value)
                alignment = (
                    QtCore.Qt.AlignmentFlag.AlignCenter
                    if column == 0
                    else QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter
                )
                item.setTextAlignment(alignment)
                self._maneuver_result_table.setItem(row, column, item)

    def _clear_visualizations(self) -> None:
        for curve in self._ground_track_curves:
            curve.clear()
        for markers in self._ground_track_markers:
            markers.clear()
        if self._ground_track_start_marker is not None:
            self._ground_track_start_marker.clear()
        if self._ground_track_start_label is not None:
            self._ground_track_start_label.setText("")
        self._ground_track_start_row = None
        self._ground_track_maneuver_summaries = []
        self._clear_maneuver_number_labels()
        if self._orbit_3d_view is not None:
            self._orbit_3d_view.clear_trajectory()

    def _update_visualizations(
        self,
        rows: list[dict[str, Any]],
        maneuver_summaries: list[dict[str, Any]],
    ) -> None:
        if not rows:
            self._clear_visualizations()
            return
        trajectory = self._trajectory_from_result_rows(rows)
        longitudes = np.asarray([float(row["subsatellite_longitude_deg"]) for row in rows], dtype=np.float64)
        latitudes = np.asarray([float(row["subsatellite_latitude_deg"]) for row in rows], dtype=np.float64)
        plot_lon = self._unwrap_longitudes(longitudes)
        plot_lat = latitudes

        for offset, curve in zip(range(-2, 3), self._ground_track_curves, strict=False):
            curve.setData(plot_lon + 360.0 * offset, plot_lat)
        marker_lons = np.asarray([float(summary["subsatellite_longitude_deg"]) for summary in maneuver_summaries], dtype=np.float64)
        marker_lats = np.asarray([float(summary["subsatellite_latitude_deg"]) for summary in maneuver_summaries], dtype=np.float64)
        for offset, markers in zip(range(-2, 3), self._ground_track_markers, strict=False):
            markers.setData(marker_lons + 360.0 * offset, marker_lats)
        self._ground_track_start_row = rows[0]
        self._ground_track_maneuver_summaries = maneuver_summaries
        self._set_ground_track_start_marker(rows[0])
        self._set_maneuver_number_labels(maneuver_summaries)

        if self._orbit_3d_view is not None:
            positions_km = trajectory.positions_km
            self._orbit_3d_view.set_trajectory_overlays(
                trajectory,
                _EARTH_RADIUS_KM,
                maneuver_segments_km=self._maneuver_segments_km(rows, positions_km),
                start_label=self._i18n.t("maneuver.plot.start_label"),
            )

    def _set_ground_track_start_marker(self, row: dict[str, Any]) -> None:
        if self._ground_track_start_marker is None:
            return
        lon = float(row["subsatellite_longitude_deg"])
        lat = float(row["subsatellite_latitude_deg"])
        lon = self._nearest_visible_longitude(lon)
        self._ground_track_start_marker.setData([lon], [lat])
        if self._ground_track_start_label is not None:
            self._ground_track_start_label.setText(self._i18n.t("maneuver.plot.start_label"))
            self._ground_track_start_label.setPos(lon, lat)

    def _set_maneuver_number_labels(self, maneuver_summaries: list[dict[str, Any]]) -> None:
        if self._ground_track_plot is None:
            return
        self._clear_maneuver_number_labels()
        font = QtGui.QFont()
        font.setBold(True)
        font.setPointSize(9)
        for summary in maneuver_summaries:
            text = str(int(summary["maneuver_index"]))
            x = self._nearest_visible_longitude(float(summary["subsatellite_longitude_deg"]))
            y = float(summary["subsatellite_latitude_deg"]) + _GROUND_TRACK_MANEUVER_LABEL_LAT_OFFSET_DEG
            for dx, dy in ((-0.55, 0.0), (0.55, 0.0), (0.0, -0.55), (0.0, 0.55)):
                outline = pg.TextItem(text, color="#02070a", anchor=(0.5, 1.45))
                outline.setFont(font)
                outline.setZValue(24)
                outline.setPos(x + dx, y + dy)
                self._ground_track_plot.addItem(outline)
                self._maneuver_number_label_outlines.append((outline, dx, dy))
            label = pg.TextItem(
                text,
                color="#ffd85a",
                anchor=(0.5, 1.45),
            )
            label.setFont(font)
            label.setZValue(25)
            label.setPos(x, y)
            self._ground_track_plot.addItem(label)
            self._maneuver_number_labels.append(label)

    def _refresh_ground_track_annotations(self) -> None:
        self._update_ground_track_axis_labels()
        if self._ground_track_start_row is not None:
            self._set_ground_track_start_marker(self._ground_track_start_row)
        if self._ground_track_maneuver_summaries:
            self._position_maneuver_number_labels()

    def _position_maneuver_number_labels(self) -> None:
        outline_index = 0
        for label, summary in zip(self._maneuver_number_labels, self._ground_track_maneuver_summaries, strict=False):
            x = self._nearest_visible_longitude(float(summary["subsatellite_longitude_deg"]))
            y = float(summary["subsatellite_latitude_deg"]) + _GROUND_TRACK_MANEUVER_LABEL_LAT_OFFSET_DEG
            label.setPos(x, y)
            for outline, dx, dy in self._maneuver_number_label_outlines[outline_index : outline_index + 4]:
                outline.setPos(x + dx, y + dy)
            outline_index += 4

    def _nearest_visible_longitude(self, longitude_deg: float) -> float:
        if self._ground_track_plot is None:
            return longitude_deg
        view_range = self._ground_track_plot.viewRange()
        center_lon = 0.5 * (float(view_range[0][0]) + float(view_range[0][1]))
        offset = round((center_lon - longitude_deg) / 360.0)
        return longitude_deg + 360.0 * offset

    def _clear_maneuver_number_labels(self) -> None:
        if self._ground_track_plot is None:
            self._maneuver_number_labels.clear()
            self._maneuver_number_label_outlines.clear()
            return
        for outline, _, _ in self._maneuver_number_label_outlines:
            self._ground_track_plot.removeItem(outline)
        self._maneuver_number_label_outlines.clear()
        for label in self._maneuver_number_labels:
            self._ground_track_plot.removeItem(label)
        self._maneuver_number_labels.clear()

    def _add_ground_track_axis_labels(self) -> None:
        self._update_ground_track_axis_labels()

    def _add_ground_track_grid_lines(self) -> None:
        if self._ground_track_plot is None:
            return
        grid_pen = pg.mkPen(QtGui.QColor(22, 128, 160, 145), width=0.8, style=QtCore.Qt.PenStyle.DashLine)
        for longitude in range(-180, 181, 30):
            line = self._ground_track_plot.plot(
                [float(longitude), float(longitude)],
                [-90.0, 90.0],
                pen=grid_pen,
            )
            line.setZValue(-12)
            self._ground_track_grid_lines.append(line)
        for latitude in (-60, -30, 0, 30, 60, 90):
            line = self._ground_track_plot.plot(
                [-180.0, 180.0],
                [float(latitude), float(latitude)],
                pen=grid_pen,
            )
            line.setZValue(-12)
            self._ground_track_grid_lines.append(line)

    def _update_ground_track_axis_labels(self) -> None:
        if self._ground_track_plot is None:
            self._ground_track_axis_labels.clear()
            return
        for label in self._ground_track_axis_labels:
            self._ground_track_plot.removeItem(label)
        self._ground_track_axis_labels.clear()

        view_range = self._ground_track_plot.viewRange()
        x_min, x_max = float(view_range[0][0]), float(view_range[0][1])
        y_min, y_max = float(view_range[1][0]), float(view_range[1][1])
        label_color = "#ffd85a"
        x_label_y = max(y_min + 14.0, -76.0)
        y_label_x = x_min + max(6.0, (x_max - x_min) * 0.02)

        first_x = int(math.ceil(x_min / 30.0) * 30)
        last_x = int(math.floor(x_max / 30.0) * 30)
        for longitude in range(first_x, last_x + 1, 30):
            wrapped = ((longitude + 180) % 360) - 180
            text = str(180 if wrapped == -180 and longitude > 0 else wrapped)
            label_x = min(max(float(longitude), x_min + 8.0), x_max - 8.0)
            self._add_outlined_axis_label(text, label_x, x_label_y, (0.5, 0.0), label_color)

        for latitude in (-60, -30, 0, 30, 60, 90):
            if latitude < y_min or latitude > y_max:
                continue
            label_y = min(max(float(latitude), y_min + 10.0), y_max - 10.0)
            self._add_outlined_axis_label(str(latitude), y_label_x, label_y, (0.0, 0.5), label_color)

    def _add_outlined_axis_label(
        self,
        text: str,
        x: float,
        y: float,
        anchor: tuple[float, float],
        color: str,
    ) -> None:
        if self._ground_track_plot is None:
            return
        font = QtGui.QFont()
        font.setBold(True)
        font.setPointSize(9)
        for dx, dy in ((-0.55, 0.0), (0.55, 0.0), (0.0, -0.55), (0.0, 0.55)):
            shadow = pg.TextItem(text, color="#02070a", anchor=anchor)
            shadow.setFont(font)
            shadow.setZValue(29)
            shadow.setPos(x + dx, y + dy)
            self._ground_track_plot.addItem(shadow)
            self._ground_track_axis_labels.append(shadow)
        label = pg.TextItem(text, color=color, anchor=anchor)
        label.setFont(font)
        label.setZValue(30)
        label.setPos(x, y)
        self._ground_track_plot.addItem(label)
        self._ground_track_axis_labels.append(label)

    def _load_existing_result_summary(self) -> bool | None:
        if self._workspace.current_project is None:
            return False

        result_path = self._workspace.data_dir() / "full_orbit_history.csv"
        if not result_path.exists():
            self._last_result_path = None
            self._open_result_button.setEnabled(False)
            return False

        try:
            rows = self._load_orbit_history_csv(result_path)
            maneuver_summaries = self._build_maneuver_summaries(rows)
        except Exception as exc:
            self._last_result_path = None
            self._open_result_button.setEnabled(False)
            self._clear_result_summary()
            self._set_status(
                "statusDisconnected",
                self._i18n.t("maneuver.status.result_load_failed", error=str(exc)),
            )
            return None

        self._last_result_path = result_path
        self._open_result_button.setEnabled(True)
        self._update_result_summary(result_path, rows, maneuver_summaries)
        return True

    def _build_maneuver_summaries(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if self._workspace.current_project is None:
            return []
        strategy_path = self._workspace.maneuver_strategy_path()
        if not strategy_path.exists():
            return []

        module = _load_dynamics_module()
        strategy_steps = module.load_maneuver_strategy_steps(strategy_path)
        return module.build_maneuver_result_rows(strategy_steps, rows)

    @staticmethod
    def _load_orbit_history_csv(csv_path: Path) -> list[dict[str, Any]]:
        with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            rows = [dict(row) for row in reader]
        if not rows:
            return []
        required_columns = {
            "elapsed_time_s",
            "elapsed_time_min",
            "phase",
            "is_event_point",
            "semi_major_axis_m",
            "inclination_deg",
            "position_x_m",
            "position_y_m",
            "position_z_m",
            "velocity_x_m_s",
            "velocity_y_m_s",
            "velocity_z_m_s",
            "subsatellite_longitude_deg",
            "subsatellite_latitude_deg",
            "mass_kg",
        }
        missing = required_columns.difference(rows[0])
        if missing:
            raise ValueError(f"Orbit history CSV is missing columns: {', '.join(sorted(missing))}")
        return rows

    @staticmethod
    def _trajectory_from_result_rows(rows: list[dict[str, Any]]) -> OrbitTrajectory:
        positions_km = np.asarray(
            [
                [
                    float(row["position_x_m"]) / 1000.0,
                    float(row["position_y_m"]) / 1000.0,
                    float(row["position_z_m"]) / 1000.0,
                ]
                for row in rows
            ],
            dtype=np.float64,
        )
        velocities_km_s = np.asarray(
            [
                [
                    float(row["velocity_x_m_s"]) / 1000.0,
                    float(row["velocity_y_m_s"]) / 1000.0,
                    float(row["velocity_z_m_s"]) / 1000.0,
                ]
                for row in rows
            ],
            dtype=np.float64,
        )
        elapsed_seconds = np.asarray([float(row["elapsed_time_s"]) for row in rows], dtype=np.float64)
        radii_km = np.linalg.norm(positions_km, axis=1)
        speeds_km_s = np.linalg.norm(velocities_km_s, axis=1)
        return OrbitTrajectory(
            positions_km=positions_km,
            velocities_km_s=velocities_km_s,
            radii_km=radii_km,
            speeds_km_s=speeds_km_s,
            elapsed_seconds=elapsed_seconds,
            current_position_km=positions_km[-1],
            current_velocity_km_s=velocities_km_s[-1],
        )

    @staticmethod
    def _maneuver_segments_km(rows: list[dict[str, Any]], positions_km: np.ndarray) -> list[np.ndarray]:
        segments: list[np.ndarray] = []
        current: list[np.ndarray] = []
        for index, row in enumerate(rows):
            phase = str(row.get("phase", ""))
            in_maneuver = phase in _MANEUVER_PHASES
            if in_maneuver and not current:
                if index > 0 and int(rows[index - 1].get("is_event_point", 0)):
                    current.append(positions_km[index - 1])
                current.append(positions_km[index])
            elif in_maneuver:
                current.append(positions_km[index])
            elif current:
                if len(current) >= 2:
                    segments.append(np.asarray(current, dtype=np.float64))
                current = []

        if len(current) >= 2:
            segments.append(np.asarray(current, dtype=np.float64))
        return segments

    @staticmethod
    def _unwrap_longitudes(longitudes: np.ndarray) -> np.ndarray:
        if longitudes.size <= 1:
            return longitudes

        unwrapped = np.asarray(longitudes, dtype=np.float64).copy()
        for index in range(1, unwrapped.size):
            delta = unwrapped[index] - unwrapped[index - 1]
            if delta > 180.0:
                unwrapped[index:] -= 360.0
            elif delta < -180.0:
                unwrapped[index:] += 360.0
        return unwrapped

    def _open_result_csv(self) -> None:
        if self._last_result_path is None or not self._last_result_path.exists():
            return
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(self._last_result_path)))

    def _set_status(self, role: str, text: str) -> None:
        self._status_role = role
        self._status_label.setProperty("role", role)
        self._status_label.style().unpolish(self._status_label)
        self._status_label.style().polish(self._status_label)
        self._status_label.setText(text)

    def retranslate(self, _language: str | None = None) -> None:
        t = self._i18n.t
        self._title_label.setText(t("maneuver.title"))
        self._strategy_header_label.setText(t("maneuver.strategy_header"))
        self._initial_state_header_label.setText(t("maneuver.initial_state_header"))
        self._initial_state_caption_label.setText(t("maneuver.initial_state_caption"))
        self._entry_aux_title_label.setText(t("maneuver.strategy_header"))
        self._ground_track_title_label.setText(t("maneuver.ground_track_title"))
        if self._ground_track_plot is not None:
            self._ground_track_plot.plotItem.setLabel("bottom", "")
            self._ground_track_plot.plotItem.setLabel("left", "")
        self._orbit_3d_title_label.setText(t("maneuver.orbit_3d_title"))
        for key, label in self._top_level_text_labels.items():
            label.setText(t(f"maneuver.field.{key}"))
        for key, label in self._t0_orbit_labels.items():
            label.setText(t(f"maneuver.field.{key}"))
        for key, label in self._entry_aux_labels.items():
            label.setText(t(f"maneuver.entry_aux.{key}"))
        if "maneuver_count" in self._config_metric_labels:
            self._config_metric_labels["maneuver_count"].setText("机动次数")
        self._reload_button.setText(f"+  {t('maneuver.reload_button')}")
        self._edit_config_button.setText(f"▱  {t('maneuver.edit_config_button')}")
        self._calculation_header_label.setText(t("maneuver.calculation_header"))
        self._calculate_button.setText(t("maneuver.calculate_button"))
        self._open_result_button.setText(t("maneuver.open_result_button"))
        self._maneuver_results_label.setText(t("maneuver.result.maneuver_results"))
        self._maneuver_result_table.setHorizontalHeaderLabels(
            [
                t("maneuver.result.column.index"),
                t("maneuver.result.column.end_time"),
                t("maneuver.result.column.semi_major_axis"),
                t("maneuver.result.column.inclination"),
                t("maneuver.result.column.longitude"),
                t("maneuver.result.column.latitude"),
                t("maneuver.result.column.propellant"),
            ]
        )
        if self._strategy_table is not None:
            self._strategy_table.setHorizontalHeaderLabels(
                [
                    t("maneuver.table.Tn_start_min"),
                    t("maneuver.table.burn_duration_min"),
                ]
            )
        for labels in self._maneuver_field_labels:
            for key, label in labels.items():
                label.setText(t(f"maneuver.table.{key}"))
        self._update_maneuver_tab_labels()
        self._result_value_labels["csv_path_caption"].setText(t("maneuver.result.csv_path"))
        self._result_value_labels["samples_caption"].setText(t("maneuver.result.samples"))
        self._result_value_labels["final_time_caption"].setText(t("maneuver.result.final_time"))
        self._result_value_labels["final_mass_caption"].setText(t("maneuver.result.final_mass"))
        self._result_value_labels["final_position_caption"].setText(t("maneuver.result.final_position"))
        self._refresh_strategy_path_label()
        self._update_strategy_count_label()
        if not self._status_label.text():
            self._set_status("statusDisconnected", t("maneuver.status.no_project"))

    def _spinbox(
        self,
        value: float,
        minimum: float,
        maximum: float,
        step: float,
        decimals: int,
    ) -> QtWidgets.QDoubleSpinBox:
        box = NoWheelDoubleSpinBox()
        box.setRange(minimum, maximum)
        box.setValue(value)
        box.setSingleStep(step)
        box.setDecimals(decimals)
        box.setButtonSymbols(QtWidgets.QAbstractSpinBox.ButtonSymbols.NoButtons)
        return box

    @staticmethod
    def _readonly_value_label() -> QtWidgets.QLabel:
        label = QtWidgets.QLabel("--")
        label.setProperty("role", "pageBody")
        label.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        return label

    @staticmethod
    def _metric_tile(icon: str, label: QtWidgets.QLabel, value: QtWidgets.QLabel) -> QtWidgets.QFrame:
        tile = QtWidgets.QFrame()
        tile.setObjectName("maneuverMetricTile")
        tile.setMinimumWidth(0)
        tile.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Fixed)
        tile_layout = QtWidgets.QHBoxLayout(tile)
        tile_layout.setContentsMargins(12, 11, 12, 11)
        tile_layout.setSpacing(8)
        icon_label = QtWidgets.QLabel(icon)
        icon_label.setProperty("kind", "metricIcon")
        icon_label.setFixedWidth(24)
        icon_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        tile_layout.addWidget(icon_label, 0, QtCore.Qt.AlignmentFlag.AlignVCenter)
        tile_layout.addWidget(label, 1, QtCore.Qt.AlignmentFlag.AlignVCenter)
        value.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
        tile_layout.addWidget(value, 0, QtCore.Qt.AlignmentFlag.AlignVCenter)
        return tile

    def _format_maneuver_value(self, column: _StrategyColumn, value: object) -> str:
        if column.key in {"maneuver_index", "dv_direction"}:
            return str(self._to_int(str(value), 1))
        return f"{self._to_float(str(value)):.{column.decimals}f}"

    @staticmethod
    def _to_float(value: str) -> float:
        try:
            return float(value)
        except ValueError:
            return 0.0

    @staticmethod
    def _to_int(value: str, default: int) -> int:
        try:
            return int(float(value))
        except ValueError:
            return int(default)

    @staticmethod
    def _default_maneuver_value(row: int, column: _StrategyColumn) -> float | int:
        if column.key == "maneuver_index":
            return row + 1
        if column.key == "dv_direction":
            return 1
        return 0.0

    @staticmethod
    def _maneuver_field_range(key: str) -> tuple[float, float, float]:
        ranges = {
            "Tn_start_min": (0.0, 1.0e7, 1.0),
            "burn_duration_min": (0.0, 1.0e6, 0.1),
            "control_fuel_%": (-99.0, 100.0, 0.01),
            "settle_duration_s": (0.0, 1.0e7, 1.0),
            "delta_deg": (-180.0, 180.0, 0.01),
            "orbit_control_thrust_n": (0.0, 1.0e7, 1.0),
            "orbit_control_isp_s": (0.0, 1.0e5, 1.0),
            "settle_thrust_n": (0.0, 1.0e7, 1.0),
            "settle_isp_s": (0.0, 1.0e5, 1.0),
        }
        return ranges.get(key, (-1.0e9, 1.0e9, 1.0))

    @staticmethod
    def _field_float(field: QtWidgets.QWidget) -> float:
        if isinstance(field, QtWidgets.QDoubleSpinBox):
            return float(field.value())
        if isinstance(field, QtWidgets.QSpinBox):
            return float(field.value())
        if isinstance(field, QtWidgets.QComboBox):
            data = field.currentData()
            return float(data) if data is not None else 0.0
        return 0.0

    def _field_int(self, field: QtWidgets.QWidget, default: int) -> int:
        if isinstance(field, QtWidgets.QSpinBox):
            return int(field.value())
        if isinstance(field, QtWidgets.QComboBox):
            data = field.currentData()
            return self._to_int(str(data), default)
        if isinstance(field, QtWidgets.QDoubleSpinBox):
            return int(field.value())
        return default

    def _resolved_t0_epoch_text(self) -> str:
        current = str(self._current_strategy.get("t0_epoch", "")).strip()
        if current:
            return current
        return utc_now_iso_z()

    def _update_maneuver_tab_labels(self) -> None:
        if not hasattr(self, "_strategy_tabs"):
            return
        for index, fields in enumerate(self._maneuver_fields):
            field = fields.get("maneuver_index")
            maneuver_index = self._field_int(field, index + 1) if field is not None else index + 1
            self._strategy_tabs.setTabText(index, self._i18n.t("maneuver.tab_label", index=maneuver_index))


def _load_dynamics_module() -> object:
    if not _DYNAMICS_SCRIPT_PATH.exists():
        raise FileNotFoundError(f"Dynamics script not found: {_DYNAMICS_SCRIPT_PATH}")

    module_name = "smart_satellite_dynamics_equation"
    spec = importlib.util.spec_from_file_location(module_name, _DYNAMICS_SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load dynamics script: {_DYNAMICS_SCRIPT_PATH}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module
