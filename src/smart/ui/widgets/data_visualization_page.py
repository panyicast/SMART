from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtGui, QtWidgets

from smart.domain.models import OrbitTrajectory
from smart.services.data_visualization import (
    PARAMETER_OPTIONS,
    VisualizationSeries,
    build_visualization_series,
    default_launch_utc_from_configs,
    parameter_label,
    parameter_unit,
)
from smart.services.earth_orientation import format_utc, parse_utc
from smart.services.launch_window import default_launch_window_config
from smart.services.project_workspace import ProjectWorkspace
from smart.ui.i18n import I18nManager
from smart.ui.mission_state import MissionState
from smart.ui.widgets.spinboxes import NoWheelComboBox, NoWheelDateTimeEdit


BEIJING_TZ = timezone(timedelta(hours=8))
_PLOT_BACKGROUND = "#071016"
_PLOT_AXIS = "#9fb5bf"
_PLOT_GRID = "#244958"
_COLORS = ("#66d9ea", "#f2b84b", "#7bd88f", "#ff7a90")


def _beijing_qtimezone() -> QtCore.QTimeZone:
    return QtCore.QTimeZone(b"Asia/Shanghai")


def _style_axis(axis: pg.AxisItem) -> None:
    axis.setPen(pg.mkPen(_PLOT_GRID, width=1))
    axis.setTextPen(pg.mkPen(_PLOT_AXIS))
    axis.setStyle(tickFont=QtGui.QFont("Noto Sans SC", 9), tickTextOffset=8)


class _DualAxisPlot(QtWidgets.QWidget):
    cursor_changed = QtCore.Signal(float)
    reset_requested = QtCore.Signal()

    def __init__(self, title: str, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._title = title
        self._right_view = pg.ViewBox(enableMenu=False)
        self._regions: list[pg.LinearRegionItem] = []
        self._syncing_cursor = False
        self._elapsed_min = np.asarray([], dtype=np.float64)
        self._left_values: np.ndarray | None = None
        self._right_values: np.ndarray | None = None
        self._left_key = ""
        self._right_key = ""
        self._left_color = _COLORS[0]
        self._right_color = _COLORS[1]
        self._syncing_right_range = False

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        controls = QtWidgets.QHBoxLayout()
        controls.setSpacing(8)
        self._title_label = QtWidgets.QLabel(title)
        self._title_label.setProperty("role", "cardCaption")
        controls.addWidget(self._title_label)
        controls.addStretch(1)
        controls.addWidget(QtWidgets.QLabel("左轴"))
        self.left_combo = self._parameter_combo()
        controls.addWidget(self.left_combo)
        controls.addWidget(QtWidgets.QLabel("右轴"))
        self.right_combo = self._parameter_combo()
        controls.addWidget(self.right_combo)
        self.autoscale_button = QtWidgets.QPushButton("自适应纵轴")
        self.autoscale_button.setProperty("variant", "secondary")
        self.autoscale_button.clicked.connect(self.autoscale_y_axes)
        controls.addWidget(self.autoscale_button)
        layout.addLayout(controls)

        self.plot = pg.PlotWidget()
        self.plot.setBackground(_PLOT_BACKGROUND)
        self.plot.showGrid(x=True, y=True, alpha=0.18)
        self.plot.setMenuEnabled(False)
        self.plot.plotItem.hideButtons()
        self.plot.plotItem.getViewBox().setBackgroundColor(_PLOT_BACKGROUND)
        self.plot.plotItem.getViewBox().setBorder(pg.mkPen("#1e3b49", width=1))
        self.plot.plotItem.showAxis("right")
        self.plot.plotItem.scene().addItem(self._right_view)
        self.plot.plotItem.getAxis("right").linkToView(self._right_view)
        self._right_view.setZValue(100)
        self._right_view.setBackgroundColor(None)
        self._right_view.setMouseEnabled(x=False, y=False)
        for axis_name in ("left", "right", "bottom"):
            _style_axis(self.plot.getAxis(axis_name))
        self.plot.plotItem.vb.sigResized.connect(self._update_right_view_geometry)
        self.plot.plotItem.vb.sigXRangeChanged.connect(lambda *_args: self._sync_right_x_range())
        self.plot.scene().sigMouseClicked.connect(self._on_scene_mouse_clicked)
        self._update_right_view_geometry()
        layout.addWidget(self.plot, 1)

        self.left_curve = self.plot.plot(pen=pg.mkPen(_COLORS[0], width=2.4))
        self.right_curve = pg.PlotDataItem(pen=pg.mkPen(_COLORS[1], width=2.4))
        self.right_curve.setZValue(20)
        self._right_view.addItem(self.right_curve)
        self.cursor = pg.InfiniteLine(pos=0.0, angle=90, movable=True, pen=pg.mkPen("#f8f0d8", width=1.4))
        self.cursor.sigPositionChanged.connect(self._on_cursor_moved)
        self.plot.addItem(self.cursor)
        self._readout = pg.TextItem(
            html="",
            anchor=(0.0, 0.0),
            border=pg.mkPen("#3d7a8e", width=1),
            fill=pg.mkBrush(QtGui.QColor(7, 16, 22, 218)),
        )
        self._readout.setZValue(50)
        self.plot.addItem(self._readout)

    def set_color_pair(self, left: str, right: str) -> None:
        self._left_color = left
        self._right_color = right
        self.left_curve.setPen(pg.mkPen(left, width=2.4))
        self.right_curve.setPen(pg.mkPen(right, width=2.4))

    def set_data(
        self,
        elapsed_min: np.ndarray,
        left_values: np.ndarray | None,
        right_values: np.ndarray | None,
        *,
        left_key: str,
        right_key: str,
    ) -> None:
        self._elapsed_min = np.asarray(elapsed_min, dtype=np.float64)
        self._left_values = None if left_values is None else np.asarray(left_values, dtype=np.float64)
        self._right_values = None if right_values is None else np.asarray(right_values, dtype=np.float64)
        self._left_key = left_key
        self._right_key = right_key
        self.left_curve.setData(elapsed_min, [] if left_values is None else left_values)
        self.right_curve.setData(elapsed_min, [] if right_values is None else right_values)
        left_unit = parameter_unit(left_key)
        right_unit = parameter_unit(right_key)
        self.plot.setLabel("left", parameter_label(left_key), units=left_unit, color=_PLOT_AXIS, **{"font-size": "10pt"})
        self.plot.setLabel("right", parameter_label(right_key), units=right_unit, color=_PLOT_AXIS, **{"font-size": "10pt"})
        self.plot.setLabel("bottom", "T0 后时间", units="min", color=_PLOT_AXIS, **{"font-size": "10pt"})
        self._update_right_view_geometry()
        self.autoscale_y_axes()

    def set_maneuver_regions(self, intervals: tuple[object, ...]) -> None:
        for region in self._regions:
            self.plot.removeItem(region)
        self._regions.clear()
        for interval in intervals:
            start = float(getattr(interval, "start_min"))
            end = float(getattr(interval, "end_min"))
            region = pg.LinearRegionItem(
                values=(start, end),
                movable=False,
                brush=QtGui.QColor(211, 34, 42, 32),
                pen=pg.mkPen(QtGui.QColor(211, 34, 42, 95)),
            )
            region.setZValue(-10)
            self.plot.addItem(region)
            self._regions.append(region)

    def set_cursor(self, elapsed_min: float) -> None:
        self._syncing_cursor = True
        try:
            self.cursor.setValue(float(elapsed_min))
            self._update_readout(float(elapsed_min))
        finally:
            self._syncing_cursor = False

    def autoscale_y_axes(self) -> None:
        self._set_view_y_range(self.plot.plotItem.vb, self._left_values)
        self._set_view_y_range(self._right_view, self._right_values)
        self._update_readout(float(self.cursor.value()))

    def reset_view(self) -> None:
        if self._elapsed_min.size:
            self.plot.plotItem.vb.setXRange(
                float(np.nanmin(self._elapsed_min)),
                float(np.nanmax(self._elapsed_min)),
                padding=0.02,
            )
        self.autoscale_y_axes()

    def _on_cursor_moved(self) -> None:
        if self._syncing_cursor:
            return
        self.cursor_changed.emit(float(self.cursor.value()))

    def _update_right_view_geometry(self) -> None:
        self._right_view.setGeometry(self.plot.plotItem.vb.sceneBoundingRect())
        self._right_view.linkedViewChanged(self.plot.plotItem.vb, self._right_view.XAxis)
        self._sync_right_x_range()

    def _sync_right_x_range(self) -> None:
        if self._syncing_right_range:
            return
        x_range = self.plot.plotItem.vb.viewRange()[0]
        current = self._right_view.viewRange()[0]
        if abs(float(current[0]) - float(x_range[0])) < 1e-9 and abs(float(current[1]) - float(x_range[1])) < 1e-9:
            return
        self._syncing_right_range = True
        try:
            self._right_view.setXRange(float(x_range[0]), float(x_range[1]), padding=0.0)
        finally:
            self._syncing_right_range = False

    def _on_scene_mouse_clicked(self, event: object) -> None:
        if not hasattr(event, "double") or not event.double():
            return
        position = event.scenePos()
        if self.plot.plotItem.vb.sceneBoundingRect().contains(position):
            self.reset_requested.emit()

    def _update_readout(self, elapsed_min: float) -> None:
        if self._elapsed_min.size == 0:
            self._readout.setHtml("")
            return
        index = int(np.argmin(np.abs(self._elapsed_min - elapsed_min)))
        left_value = self._value_at(self._left_values, index)
        right_value = self._value_at(self._right_values, index)
        left_text = self._format_readout_value(self._left_key, left_value)
        right_text = self._format_readout_value(self._right_key, right_value)
        self._readout.setHtml(
            "<div style='color:#d8f4ff; font-size:10pt; padding:6px;'>"
            f"T0+{self._elapsed_min[index]:.3f} min<br>"
            f"<span style='color:{self._left_color};'>{parameter_label(self._left_key)}: {left_text}</span><br>"
            f"<span style='color:{self._right_color};'>{parameter_label(self._right_key)}: {right_text}</span>"
            "</div>"
        )
        x_range, y_range = self.plot.plotItem.vb.viewRange()
        x_span = max(float(x_range[1] - x_range[0]), 1e-9)
        y_span = max(float(y_range[1] - y_range[0]), 1e-9)
        x = min(max(self._elapsed_min[index] + 0.018 * x_span, x_range[0] + 0.01 * x_span), x_range[1] - 0.24 * x_span)
        y = y_range[1] - 0.18 * y_span
        self._readout.setPos(float(x), float(y))

    @staticmethod
    def _set_view_y_range(view: pg.ViewBox, values: np.ndarray | None) -> None:
        if values is None or values.size == 0:
            return
        finite = values[np.isfinite(values)]
        if finite.size == 0:
            return
        low = float(np.nanmin(finite))
        high = float(np.nanmax(finite))
        span = high - low
        padding = max(abs(high), abs(low), 1.0) * 0.03 if span <= 0.0 else span * 0.08
        view.setYRange(low - padding, high + padding, padding=0.0)

    @staticmethod
    def _value_at(values: np.ndarray | None, index: int) -> float:
        if values is None or values.size == 0:
            return float("nan")
        return float(values[index])

    @staticmethod
    def _format_readout_value(key: str, value: float) -> str:
        if not np.isfinite(value):
            return "--"
        decimals = 6 if key == "eccentricity" else 3
        unit = parameter_unit(key)
        suffix = f" {unit}" if unit else ""
        return f"{value:.{decimals}f}{suffix}"

    @staticmethod
    def _parameter_combo() -> NoWheelComboBox:
        combo = NoWheelComboBox()
        combo.setMinimumWidth(158)
        for key, label, unit in PARAMETER_OPTIONS:
            suffix = f" ({unit})" if unit else ""
            combo.addItem(f"{label}{suffix}", key)
        return combo


class DataVisualizationPage(QtWidgets.QWidget):
    def __init__(
        self,
        mission_state: MissionState,
        i18n: I18nManager,
        workspace: ProjectWorkspace | None = None,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._mission_state = mission_state
        self._i18n = i18n
        self._workspace = workspace
        self._series: VisualizationSeries | None = None
        self._cursor_min = 0.0
        self._cursor_syncing = False
        self._syncing_x_range = False
        self._summary_value_labels: dict[str, QtWidgets.QLabel] = {}
        self._summary_caption_labels: dict[str, QtWidgets.QLabel] = {}

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(14)

        self._title_label = QtWidgets.QLabel()
        self._title_label.setProperty("role", "pageTitle")
        root.addWidget(self._title_label)

        self._subtitle_label = QtWidgets.QLabel()
        self._subtitle_label.setProperty("role", "pageBody")
        self._subtitle_label.setWordWrap(True)
        root.addWidget(self._subtitle_label)

        toolbar = QtWidgets.QHBoxLayout()
        toolbar.setSpacing(10)
        toolbar.addWidget(QtWidgets.QLabel("发射时间"))
        self._launch_edit = self._launch_datetime_edit()
        self._launch_edit.setMinimumWidth(230)
        toolbar.addWidget(self._launch_edit)
        self._calculate_button = QtWidgets.QPushButton("计算轨道参数")
        self._calculate_button.clicked.connect(self.calculate)
        toolbar.addWidget(self._calculate_button)
        self._status_label = QtWidgets.QLabel("")
        self._status_label.setProperty("role", "cardCaption")
        toolbar.addWidget(self._status_label, 1)
        root.addLayout(toolbar)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_summary_card())
        splitter.addWidget(self._build_plot_card())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([360, 960])
        root.addWidget(splitter, 1)

        self._mission_state.trajectory_changed.connect(self._refresh_fallback)
        self._i18n.language_changed.connect(self.retranslate)
        self.retranslate()
        self._set_default_launch_time()
        self._refresh_fallback(self._mission_state.trajectory)

    def refresh_from_workspace(self) -> None:
        self._set_default_launch_time()
        self.calculate()

    def calculate(self) -> None:
        if self._workspace is None or self._workspace.current_project is None:
            self._set_status("没有活动项目，当前显示轨道设计预览。")
            self._refresh_fallback(self._mission_state.trajectory)
            return
        try:
            strategy = self._workspace.load_maneuver_strategy()
            if strategy is None:
                raise FileNotFoundError(self._workspace.maneuver_strategy_path())
            config = self._workspace.load_launch_window_config() or default_launch_window_config()
            rocket_flight_time_s = float(config.get("rocket_flight_time_s", 2134.4121))
            launch_utc = self._datetime_edit_to_utc(self._launch_edit)
            self._series = build_visualization_series(
                orbit_history_csv=self._workspace.data_dir() / "full_orbit_history.csv",
                maneuver_strategy=strategy,
                launch_utc=launch_utc,
                rocket_flight_time_s=rocket_flight_time_s,
            )
        except Exception as exc:
            self._set_status(f"计算失败：{exc}")
            return
        self._cursor_min = float(self._series.elapsed_min[0]) if self._series.elapsed_min.size else 0.0
        self._refresh_plots()
        self._refresh_cursor_values()
        self._set_status(
            f"已计算：发射 {self._format_beijing(self._series.launch_utc)}，T0 {self._format_beijing(self._series.t0_utc)}。"
        )

    def _build_summary_card(self) -> QtWidgets.QWidget:
        card = QtWidgets.QFrame()
        card.setProperty("role", "card")
        card.setMinimumWidth(330)
        layout = QtWidgets.QVBoxLayout(card)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        self._summary_title_label = QtWidgets.QLabel()
        self._summary_title_label.setProperty("role", "cardTitle")
        layout.addWidget(self._summary_title_label)

        form = QtWidgets.QFormLayout()
        keys = (
            "epoch",
            "semi_major_axis_km",
            "eccentricity",
            "inclination_deg",
            "raan_deg",
            "argument_of_perigee_deg",
            "mean_anomaly_deg",
            "true_anomaly_deg",
            "subsatellite_longitude_deg",
            "subsatellite_latitude_deg",
        )
        for key in keys:
            caption = QtWidgets.QLabel()
            value = QtWidgets.QLabel("--")
            value.setProperty("role", "metricValue")
            value.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
            form.addRow(caption, value)
            self._summary_caption_labels[key] = caption
            self._summary_value_labels[key] = value
        layout.addLayout(form)
        layout.addStretch(1)
        return card

    def _build_plot_card(self) -> QtWidgets.QWidget:
        card = QtWidgets.QFrame()
        card.setProperty("role", "card")
        layout = QtWidgets.QVBoxLayout(card)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        self._plot_title_label = QtWidgets.QLabel()
        self._plot_title_label.setProperty("role", "cardTitle")
        layout.addWidget(self._plot_title_label)

        self._top_plot = _DualAxisPlot("上曲线视图")
        self._bottom_plot = _DualAxisPlot("下曲线视图")
        self._top_plot.set_color_pair(_COLORS[0], _COLORS[1])
        self._bottom_plot.set_color_pair(_COLORS[2], _COLORS[3])
        self._set_combo_value(self._top_plot.left_combo, "semi_major_axis_km")
        self._set_combo_value(self._top_plot.right_combo, "eccentricity")
        self._set_combo_value(self._bottom_plot.left_combo, "perigee_altitude_km")
        self._set_combo_value(self._bottom_plot.right_combo, "mass_kg")
        for plot in (self._top_plot, self._bottom_plot):
            plot.left_combo.currentIndexChanged.connect(lambda _index: self._refresh_plots())
            plot.right_combo.currentIndexChanged.connect(lambda _index: self._refresh_plots())
            plot.cursor_changed.connect(self._set_cursor)
            plot.reset_requested.connect(self._reset_plot_views)
            layout.addWidget(plot, 1)
        self._top_plot.plot.plotItem.vb.sigXRangeChanged.connect(
            lambda _view, value: self._sync_plot_x_range(self._top_plot, self._bottom_plot, value)
        )
        self._bottom_plot.plot.plotItem.vb.sigXRangeChanged.connect(
            lambda _view, value: self._sync_plot_x_range(self._bottom_plot, self._top_plot, value)
        )
        return card

    def _refresh_plots(self) -> None:
        if self._series is None:
            return
        for plot in (self._top_plot, self._bottom_plot):
            left_key = str(plot.left_combo.currentData())
            right_key = str(plot.right_combo.currentData())
            plot.set_data(
                self._series.elapsed_min,
                self._series.values.get(left_key),
                self._series.values.get(right_key),
                left_key=left_key,
                right_key=right_key,
            )
            plot.set_maneuver_regions(self._series.maneuver_intervals)
            plot.set_cursor(self._cursor_min)
        self._sync_plot_x_ranges()

    def _refresh_fallback(self, trajectory: OrbitTrajectory) -> None:
        if self._series is not None:
            return
        elements = self._mission_state.elements
        elapsed_min = trajectory.elapsed_seconds / 60.0
        altitude = trajectory.radii_km - elements.central_body_radius_km
        values = {
            "semi_major_axis_km": np.full_like(elapsed_min, elements.semi_major_axis_km),
            "eccentricity": np.full_like(elapsed_min, elements.eccentricity),
            "inclination_deg": np.full_like(elapsed_min, elements.inclination_deg),
            "raan_deg": np.full_like(elapsed_min, elements.raan_deg),
            "argument_of_perigee_deg": np.full_like(elapsed_min, elements.argument_of_periapsis_deg),
            "true_anomaly_deg": np.linspace(0.0, 360.0, len(elapsed_min), endpoint=False),
            "mean_anomaly_deg": np.linspace(0.0, 360.0, len(elapsed_min), endpoint=False),
            "perigee_altitude_km": np.full_like(elapsed_min, elements.perigee_radius_km - elements.central_body_radius_km),
            "apogee_altitude_km": np.full_like(elapsed_min, elements.apogee_radius_km - elements.central_body_radius_km),
            "subsatellite_longitude_deg": np.full_like(elapsed_min, np.nan),
            "subsatellite_latitude_deg": np.full_like(elapsed_min, np.nan),
            "mass_kg": np.full_like(elapsed_min, np.nan),
            "beta_angle_deg": np.full_like(elapsed_min, np.nan),
            "earth_sun_angle_deg": altitude,
        }
        now = format_utc(datetime.now(tz=timezone.utc))
        self._series = VisualizationSeries(
            launch_utc=now,
            t0_utc=now,
            elapsed_min=elapsed_min,
            epochs_utc=tuple(now for _ in elapsed_min),
            values=values,
            maneuver_intervals=(),
        )
        self._refresh_plots()
        self._refresh_cursor_values()
        self._series = None

    def _set_cursor(self, elapsed_min: float) -> None:
        if self._cursor_syncing:
            return
        self._cursor_syncing = True
        try:
            self._cursor_min = float(elapsed_min)
            self._top_plot.set_cursor(self._cursor_min)
            self._bottom_plot.set_cursor(self._cursor_min)
            self._refresh_cursor_values()
        finally:
            self._cursor_syncing = False

    def _reset_plot_views(self) -> None:
        if self._series is None or self._series.elapsed_min.size == 0:
            return
        start = float(np.nanmin(self._series.elapsed_min))
        end = float(np.nanmax(self._series.elapsed_min))
        self._syncing_x_range = True
        try:
            self._top_plot.plot.plotItem.vb.setXRange(start, end, padding=0.02)
            self._bottom_plot.plot.plotItem.vb.setXRange(start, end, padding=0.02)
        finally:
            self._syncing_x_range = False
        self._top_plot.autoscale_y_axes()
        self._bottom_plot.autoscale_y_axes()

    def _sync_plot_x_ranges(self) -> None:
        if self._series is None or self._series.elapsed_min.size == 0:
            return
        start = float(np.nanmin(self._series.elapsed_min))
        end = float(np.nanmax(self._series.elapsed_min))
        self._syncing_x_range = True
        try:
            self._top_plot.plot.plotItem.vb.setXRange(start, end, padding=0.02)
            self._bottom_plot.plot.plotItem.vb.setXRange(start, end, padding=0.02)
        finally:
            self._syncing_x_range = False

    def _sync_plot_x_range(self, source: _DualAxisPlot, target: _DualAxisPlot, value: object) -> None:
        if self._syncing_x_range:
            return
        if not isinstance(value, (list, tuple)) or len(value) != 2:
            value = source.plot.plotItem.vb.viewRange()[0]
        self._syncing_x_range = True
        try:
            target.plot.plotItem.vb.setXRange(float(value[0]), float(value[1]), padding=0.0)
        finally:
            self._syncing_x_range = False

    def _refresh_cursor_values(self) -> None:
        if self._series is None or self._series.elapsed_min.size == 0:
            return
        index = int(np.argmin(np.abs(self._series.elapsed_min - self._cursor_min)))
        self._summary_value_labels["epoch"].setText(self._format_beijing(self._series.epochs_utc[index]))
        for key in (
            "semi_major_axis_km",
            "eccentricity",
            "inclination_deg",
            "raan_deg",
            "argument_of_perigee_deg",
            "mean_anomaly_deg",
            "true_anomaly_deg",
            "subsatellite_longitude_deg",
            "subsatellite_latitude_deg",
        ):
            value = float(self._series.values[key][index])
            self._summary_value_labels[key].setText(self._format_parameter_value(key, value))

    def _set_default_launch_time(self) -> None:
        if self._workspace is None or self._workspace.current_project is None:
            self._launch_edit.setDateTime(self._utc_to_qdatetime(format_utc(datetime.now(tz=timezone.utc))))
            return
        try:
            flight_program = self._workspace.load_flight_program_config()
            strategy = self._workspace.load_maneuver_strategy()
            config = self._workspace.load_launch_window_config() or default_launch_window_config()
            launch_utc = default_launch_utc_from_configs(
                flight_program=flight_program,
                maneuver_strategy=strategy,
                rocket_flight_time_s=float(config.get("rocket_flight_time_s", 2134.4121)),
            )
            self._launch_edit.setDateTime(self._utc_to_qdatetime(launch_utc))
        except Exception:
            self._launch_edit.setDateTime(self._utc_to_qdatetime(format_utc(datetime.now(tz=timezone.utc))))

    def retranslate(self, _language: str | None = None) -> None:
        t = self._i18n.t
        self._title_label.setText(t("viz.title"))
        self._subtitle_label.setText(
            "根据项目变轨结果和指定发射时间计算轨道参数；拖动曲线时间线查看当前时刻六根数与星下点。"
        )
        self._summary_title_label.setText("时间线当前参数")
        self._plot_title_label.setText("轨道参数曲线")
        labels = {
            "epoch": "当前时刻",
            "semi_major_axis_km": "半长轴",
            "eccentricity": "偏心率",
            "inclination_deg": "轨道倾角",
            "raan_deg": "升交点赤经",
            "argument_of_perigee_deg": "近地点幅角",
            "mean_anomaly_deg": "平近点角",
            "true_anomaly_deg": "真近点角",
            "subsatellite_longitude_deg": "星下点经度",
            "subsatellite_latitude_deg": "星下点纬度",
        }
        for key, label in labels.items():
            self._summary_caption_labels[key].setText(label)

    def export_charts(self, output_dir: Path) -> list[Path]:
        output_dir.mkdir(parents=True, exist_ok=True)
        top_path = output_dir / "altitude_trend.png"
        bottom_path = output_dir / "velocity_trend.png"
        self._top_plot.plot.grab().save(str(top_path))
        self._bottom_plot.plot.grab().save(str(bottom_path))
        return [top_path, bottom_path]

    def _set_status(self, text: str) -> None:
        self._status_label.setText(text)

    @staticmethod
    def _format_parameter_value(key: str, value: float) -> str:
        if not np.isfinite(value):
            return "--"
        unit = parameter_unit(key)
        decimals = 6 if key == "eccentricity" else 3
        suffix = f" {unit}" if unit else ""
        return f"{value:.{decimals}f}{suffix}"

    @staticmethod
    def _set_combo_value(combo: NoWheelComboBox, value: str) -> None:
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    @staticmethod
    def _launch_datetime_edit() -> NoWheelDateTimeEdit:
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
        milliseconds = field.dateTime().toMSecsSinceEpoch()
        epoch = datetime.fromtimestamp(milliseconds / 1000.0, tz=timezone.utc)
        return format_utc(epoch)

    @staticmethod
    def _format_beijing(value: str) -> str:
        return parse_utc(value).astimezone(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")
