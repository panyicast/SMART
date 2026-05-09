from __future__ import annotations

import csv
from dataclasses import dataclass
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
    inertial_raan_deg_from_ascending_node_longitude_deg,
    subsatellite_point_from_eci,
    utc_now_iso_z,
)
from smart.services.project_workspace import ProjectWorkspace, default_maneuver_strategy_payload
from smart.ui.i18n import I18nManager
from smart.ui.widgets.orbit_views import OrbitPlot3D
from smart.ui.widgets.spinboxes import NoWheelComboBox, NoWheelDoubleSpinBox, NoWheelSpinBox

_REPO_ROOT = Path(__file__).resolve().parents[4]
_DYNAMICS_SCRIPT_PATH = _REPO_ROOT / "scripts" / "satellite_dynamics_equation.py"
_EARTH_RADIUS_KM = 6378.14
_MANEUVER_PHASES = {"settle", "orbit_control"}
_EARTH_TEXTURE_PATH = (
    _REPO_ROOT
    / "src"
    / "smart"
    / "assets"
    / "textures"
    / "earth_day_2048.png"
)


def _qimage_to_rgba_array(image: QtGui.QImage) -> np.ndarray:
    image = image.convertToFormat(QtGui.QImage.Format.Format_RGBA8888)
    raw = np.frombuffer(image.bits(), dtype=np.uint8, count=image.sizeInBytes()).copy()
    height = image.height()
    width = image.width()
    bytes_per_line = image.bytesPerLine()
    return raw.reshape((height, bytes_per_line))[:, : width * 4].reshape((height, width, 4))


def _load_world_map_rgba() -> np.ndarray | None:
    image = QtGui.QImage(str(_EARTH_TEXTURE_PATH))
    if image.isNull():
        return None
    return np.flipud(_qimage_to_rgba_array(image))


@dataclass(frozen=True, slots=True)
class _StrategyColumn:
    key: str
    label_key: str
    decimals: int


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
        self._top_level_labels: dict[str, QtWidgets.QLabel] = {}
        self._top_level_fields: dict[str, QtWidgets.QDoubleSpinBox] = {}
        self._top_level_text_labels: dict[str, QtWidgets.QLabel] = {}
        self._top_level_text_fields: dict[str, QtWidgets.QLineEdit] = {}
        self._t0_orbit_labels: dict[str, QtWidgets.QLabel] = {}
        self._t0_orbit_fields: dict[str, QtWidgets.QDoubleSpinBox] = {}
        self._entry_aux_labels: dict[str, QtWidgets.QLabel] = {}
        self._entry_aux_values: dict[str, QtWidgets.QLabel] = {}
        self._maneuver_field_labels: list[dict[str, QtWidgets.QLabel]] = []
        self._maneuver_fields: list[dict[str, QtWidgets.QWidget]] = []
        self._result_value_labels: dict[str, QtWidgets.QLabel] = {}
        self._orbit_3d_view: OrbitPlot3D | None = None
        self._ground_track_plot: pg.PlotWidget | None = None
        self._ground_track_curve: pg.PlotDataItem | None = None
        self._ground_track_markers: pg.PlotDataItem | None = None
        self._ground_track_start_marker: pg.PlotDataItem | None = None
        self._ground_track_start_label: pg.TextItem | None = None
        self._maneuver_number_labels: list[pg.TextItem] = []

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

        self._subtitle_label = QtWidgets.QLabel()
        self._subtitle_label.setProperty("role", "pageBody")
        self._subtitle_label.setWordWrap(True)
        root.addWidget(self._subtitle_label)

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

        for field in (*self._top_level_fields.values(), *self._t0_orbit_fields.values()):
            field.valueChanged.connect(lambda _value: self._on_entry_parameter_changed())
        for field in self._top_level_text_fields.values():
            field.textChanged.connect(lambda _text: self._on_entry_parameter_changed())
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

        self._strategy_tabs = QtWidgets.QTabWidget()
        self._strategy_tabs.setDocumentMode(True)
        self._strategy_tabs.setUsesScrollButtons(False)
        self._strategy_tabs.setMinimumHeight(300)
        layout.addWidget(self._strategy_tabs)

        button_row = QtWidgets.QHBoxLayout()
        button_row.setSpacing(10)
        self._reload_button = QtWidgets.QPushButton()
        self._reload_button.clicked.connect(self.refresh_from_workspace)
        button_row.addWidget(self._reload_button)

        self._save_button = QtWidgets.QPushButton()
        self._save_button.clicked.connect(self.save_strategy)
        button_row.addWidget(self._save_button)

        self._add_button = QtWidgets.QPushButton()
        self._add_button.clicked.connect(self._append_maneuver)
        button_row.addWidget(self._add_button)

        self._remove_button = QtWidgets.QPushButton()
        self._remove_button.clicked.connect(self._remove_selected_maneuver)
        button_row.addWidget(self._remove_button)
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
        layout.addWidget(self._build_strategy_card())
        layout.addWidget(self._build_calculation_card())
        layout.addStretch(1)
        return scroll

    def _build_visualization_panel(self) -> QtWidgets.QWidget:
        panel = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        layout.addWidget(self._build_ground_track_card(), 2)
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

        self._ground_track_plot = pg.PlotWidget()
        self._ground_track_plot.setBackground("#dfe9e4")
        self._ground_track_plot.showGrid(x=True, y=True, alpha=0.24)
        self._ground_track_plot.setMenuEnabled(False)
        self._ground_track_plot.plotItem.hideButtons()
        self._ground_track_plot.setXRange(-180.0, 180.0, padding=0.0)
        self._ground_track_plot.setYRange(-90.0, 90.0, padding=0.0)
        self._ground_track_map = pg.ImageItem(axisOrder="row-major")
        world_map = _load_world_map_rgba()
        if world_map is not None:
            self._ground_track_map.setImage(world_map)
            self._ground_track_map.setRect(QtCore.QRectF(-180.0, -90.0, 360.0, 180.0))
            self._ground_track_map.setOpacity(0.86)
            self._ground_track_plot.addItem(self._ground_track_map)
        self._ground_track_curve = self._ground_track_plot.plot(pen=pg.mkPen("#00a6d6", width=2.4))
        self._ground_track_markers = self._ground_track_plot.plot(
            pen=None,
            symbol="o",
            symbolSize=9,
            symbolBrush="#f28c28",
            symbolPen=pg.mkPen("#7d3217", width=1.1),
        )
        self._ground_track_start_marker = self._ground_track_plot.plot(
            pen=None,
            symbol="star",
            symbolSize=15,
            symbolBrush="#7ee049",
            symbolPen=pg.mkPen("#245a19", width=1.4),
        )
        self._ground_track_start_label = pg.TextItem(
            "",
            color="#193d12",
            anchor=(0.0, 1.2),
            border=pg.mkPen("#245a19", width=1.0),
            fill=pg.mkBrush(235, 255, 224, 220),
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
        layout = QtWidgets.QVBoxLayout(card)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        self._initial_state_header_label = QtWidgets.QLabel()
        self._initial_state_header_label.setProperty("role", "cardTitle")
        layout.addWidget(self._initial_state_header_label)

        self._initial_state_caption_label = QtWidgets.QLabel()
        self._initial_state_caption_label.setProperty("role", "cardCaption")
        self._initial_state_caption_label.setWordWrap(True)
        layout.addWidget(self._initial_state_caption_label)

        content = QtWidgets.QHBoxLayout()
        content.setSpacing(22)

        form = QtWidgets.QFormLayout()
        form.setSpacing(10)

        launch_mass_label = QtWidgets.QLabel()
        launch_mass_field = self._spinbox(5200.0, 100.0, 30000.0, 10.0, 3)
        form.addRow(launch_mass_label, launch_mass_field)
        self._top_level_labels["launch_mass_kg"] = launch_mass_label
        self._top_level_fields["launch_mass_kg"] = launch_mass_field

        t0_epoch_label = QtWidgets.QLabel()
        t0_epoch_field = QtWidgets.QLineEdit()
        t0_epoch_field.setClearButtonEnabled(False)
        t0_epoch_field.setPlaceholderText("2024-01-01T00:00:00Z")
        form.addRow(t0_epoch_label, t0_epoch_field)
        self._top_level_text_labels["t0_epoch"] = t0_epoch_label
        self._top_level_text_fields["t0_epoch"] = t0_epoch_field

        orbit_definitions = {
            "semi_major_axis_m": (29_478_137.0, 1.0, 1.0e9, 1000.0, 3),
            "eccentricity": (0.7768460924, 0.0, 0.9999999999, 0.0001, 10),
            "inclination_deg": (16.5, 0.0, 180.0, 0.1, 6),
            "argument_of_perigee_deg": (200.0, 0.0, 360.0, 0.1, 6),
            "raan_deg": (8.53237, 0.0, 360.0, 0.1, 6),
            "mean_anomaly_deg": (1.85437, 0.0, 360.0, 0.1, 6),
        }
        for key, (value, minimum, maximum, step, decimals) in orbit_definitions.items():
            label = QtWidgets.QLabel()
            field = self._spinbox(value, minimum, maximum, step, decimals)
            form.addRow(label, field)
            self._t0_orbit_labels[key] = label
            self._t0_orbit_fields[key] = field

        content.addLayout(form, 3)

        aux_layout = QtWidgets.QGridLayout()
        aux_layout.setHorizontalSpacing(14)
        aux_layout.setVerticalSpacing(10)
        self._entry_aux_title_label = QtWidgets.QLabel()
        self._entry_aux_title_label.setProperty("role", "cardCaption")
        aux_layout.addWidget(self._entry_aux_title_label, 0, 0, 1, 2)
        for row, key in enumerate(
            (
                "true_anomaly_deg",
                "subsatellite_longitude_deg",
                "subsatellite_latitude_deg",
                "apogee_altitude_m",
                "perigee_altitude_m",
            ),
            start=1,
        ):
            label = QtWidgets.QLabel()
            label.setProperty("role", "cardCaption")
            value = QtWidgets.QLabel("--")
            value.setProperty("role", "pageBody")
            value.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
            aux_layout.addWidget(label, row, 0)
            aux_layout.addWidget(value, row, 1)
            self._entry_aux_labels[key] = label
            self._entry_aux_values[key] = value
        aux_layout.setColumnStretch(1, 1)
        content.addLayout(aux_layout, 2)

        layout.addLayout(content)
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
        payload = dict(self._current_strategy)
        maneuvers: list[dict[str, Any]] = []

        for row, fields in enumerate(self._maneuver_fields):
            step: dict[str, Any] = {}
            for column in self._COLUMNS:
                field = fields[column.key]
                if column.key == "maneuver_index":
                    step[column.key] = self._field_int(field, row + 1)
                elif column.key == "dv_direction":
                    step[column.key] = self._field_int(field, 1)
                else:
                    step[column.key] = self._field_float(field)
            maneuvers.append(step)

        payload["launch_mass_kg"] = self._top_level_fields["launch_mass_kg"].value()
        payload["t0_epoch"] = self._resolved_t0_epoch_text()
        payload["t0_orbit"] = {
            key: field.value()
            for key, field in self._t0_orbit_fields.items()
        }
        payload["maneuver_count"] = len(maneuvers)
        payload["maneuvers"] = maneuvers
        return payload

    def _set_initial_state_fields(self, strategy: dict[str, Any]) -> None:
        defaults = default_maneuver_strategy_payload(0)
        orbit_defaults = defaults["t0_orbit"]
        orbit_payload = strategy.get("t0_orbit", {})
        if not isinstance(orbit_payload, dict):
            orbit_payload = {}

        self._suppress_emit = True
        for field in (
            *self._top_level_fields.values(),
            *self._t0_orbit_fields.values(),
            *self._top_level_text_fields.values(),
        ):
            field.blockSignals(True)
        try:
            self._top_level_fields["launch_mass_kg"].setValue(
                float(strategy.get("launch_mass_kg", defaults["launch_mass_kg"]))
            )
            self._top_level_text_fields["t0_epoch"].setText(
                str(strategy.get("t0_epoch", defaults["t0_epoch"]))
            )
            for key, field in self._t0_orbit_fields.items():
                field.setValue(float(orbit_payload.get(key, orbit_defaults[key])))
        finally:
            for field in (
                *self._top_level_fields.values(),
                *self._t0_orbit_fields.values(),
                *self._top_level_text_fields.values(),
            ):
                field.blockSignals(False)
            self._suppress_emit = False
        self._update_entry_auxiliary_values()

    def _set_strategy_rows(self, rows: object) -> None:
        self._suppress_emit = True
        self._strategy_tabs.blockSignals(True)
        try:
            self._strategy_tabs.clear()
            self._maneuver_field_labels.clear()
            self._maneuver_fields.clear()
            if not isinstance(rows, list):
                return
            for row_payload in rows:
                if not isinstance(row_payload, dict):
                    continue
                self._append_strategy_tab(row_payload)
            if self._strategy_tabs.count() > 0:
                self._strategy_tabs.setCurrentIndex(0)
        finally:
            self._strategy_tabs.blockSignals(False)
            self._suppress_emit = False
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

        orbit = {
            key: field.value()
            for key, field in self._t0_orbit_fields.items()
        }
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

        self._entry_aux_values["true_anomaly_deg"].setText(f"{true_anomaly_deg:.6f} deg")
        self._entry_aux_values["subsatellite_longitude_deg"].setText(f"{subpoint.longitude_deg:.5f} deg")
        self._entry_aux_values["subsatellite_latitude_deg"].setText(f"{subpoint.latitude_deg:.5f} deg")
        self._entry_aux_values["apogee_altitude_m"].setText(f"{apogee_altitude_m:.3f} m")
        self._entry_aux_values["perigee_altitude_m"].setText(f"{perigee_altitude_m:.3f} m")

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
            field = NoWheelComboBox()
            field.addItem("1", 1)
            field.addItem("-1", -1)
            field.setCurrentIndex(1 if self._to_int(str(value), 1) == -1 else 0)
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
        for field in (*self._top_level_fields.values(), *self._t0_orbit_fields.values()):
            field.setEnabled(enabled)
        for field in self._top_level_text_fields.values():
            field.setEnabled(enabled)
        self._reload_button.setEnabled(enabled)
        self._save_button.setEnabled(enabled)
        self._add_button.setEnabled(enabled)
        self._remove_button.setEnabled(enabled)
        self._calculate_button.setEnabled(enabled)

    def _refresh_strategy_path_label(self) -> None:
        if self._workspace.current_project is None:
            text = self._i18n.t("maneuver.config_path.none")
        else:
            text = self._i18n.t("maneuver.config_path", path=str(self._workspace.maneuver_strategy_path()))
        self._strategy_path_label.setText(text)

    def _update_strategy_count_label(self) -> None:
        self._strategy_count_label.setText(
            self._i18n.t("maneuver.count", count=self._strategy_tabs.count())
        )

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
        if self._ground_track_curve is not None:
            self._ground_track_curve.clear()
        if self._ground_track_markers is not None:
            self._ground_track_markers.clear()
        if self._ground_track_start_marker is not None:
            self._ground_track_start_marker.clear()
        if self._ground_track_start_label is not None:
            self._ground_track_start_label.setText("")
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
        plot_lon, plot_lat = self._with_dateline_breaks(longitudes, latitudes)

        if self._ground_track_curve is not None:
            self._ground_track_curve.setData(plot_lon, plot_lat)
        if self._ground_track_markers is not None:
            self._ground_track_markers.setData(
                [float(summary["subsatellite_longitude_deg"]) for summary in maneuver_summaries],
                [float(summary["subsatellite_latitude_deg"]) for summary in maneuver_summaries],
            )
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
        self._ground_track_start_marker.setData([lon], [lat])
        if self._ground_track_start_label is not None:
            self._ground_track_start_label.setText(self._i18n.t("maneuver.plot.start_label"))
            self._ground_track_start_label.setPos(lon, lat)

    def _set_maneuver_number_labels(self, maneuver_summaries: list[dict[str, Any]]) -> None:
        if self._ground_track_plot is None:
            return
        self._clear_maneuver_number_labels()
        for summary in maneuver_summaries:
            label = pg.TextItem(
                str(int(summary["maneuver_index"])),
                color="#321700",
                anchor=(0.5, 1.35),
                border=pg.mkPen("#7d3217", width=1.0),
                fill=pg.mkBrush(255, 238, 207, 230),
            )
            label.setZValue(25)
            label.setPos(
                float(summary["subsatellite_longitude_deg"]),
                float(summary["subsatellite_latitude_deg"]),
            )
            self._ground_track_plot.addItem(label)
            self._maneuver_number_labels.append(label)

    def _clear_maneuver_number_labels(self) -> None:
        if self._ground_track_plot is None:
            self._maneuver_number_labels.clear()
            return
        for label in self._maneuver_number_labels:
            self._ground_track_plot.removeItem(label)
        self._maneuver_number_labels.clear()

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
    def _with_dateline_breaks(
        longitudes: np.ndarray,
        latitudes: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray]:
        if longitudes.size <= 1:
            return longitudes, latitudes

        plot_lon: list[float] = []
        plot_lat: list[float] = []
        for index, (lon, lat) in enumerate(zip(longitudes, latitudes, strict=True)):
            if index > 0 and abs(lon - longitudes[index - 1]) > 180.0:
                plot_lon.append(float("nan"))
                plot_lat.append(float("nan"))
            plot_lon.append(float(lon))
            plot_lat.append(float(lat))
        return np.asarray(plot_lon, dtype=np.float64), np.asarray(plot_lat, dtype=np.float64)

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
        self._subtitle_label.setText(t("maneuver.subtitle"))
        self._strategy_header_label.setText(t("maneuver.strategy_header"))
        self._initial_state_header_label.setText(t("maneuver.initial_state_header"))
        self._initial_state_caption_label.setText(t("maneuver.initial_state_caption"))
        self._entry_aux_title_label.setText(t("maneuver.entry_aux.title"))
        self._ground_track_title_label.setText(t("maneuver.ground_track_title"))
        if self._ground_track_plot is not None:
            self._ground_track_plot.plotItem.setLabel("bottom", t("maneuver.ground_track.longitude"), units="deg")
            self._ground_track_plot.plotItem.setLabel("left", t("maneuver.ground_track.latitude"), units="deg")
        self._orbit_3d_title_label.setText(t("maneuver.orbit_3d_title"))
        self._top_level_labels["launch_mass_kg"].setText(t("maneuver.field.launch_mass_kg"))
        for key, label in self._top_level_text_labels.items():
            label.setText(t(f"maneuver.field.{key}"))
        for key, label in self._t0_orbit_labels.items():
            label.setText(t(f"maneuver.field.{key}"))
        for key, label in self._entry_aux_labels.items():
            label.setText(t(f"maneuver.entry_aux.{key}"))
        self._reload_button.setText(t("maneuver.reload_button"))
        self._save_button.setText(t("maneuver.save_button"))
        self._add_button.setText(t("maneuver.add_button"))
        self._remove_button.setText(t("maneuver.remove_button"))
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
        field = self._top_level_text_fields.get("t0_epoch")
        if field is not None:
            candidate = field.text().strip()
            if candidate:
                return candidate
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
