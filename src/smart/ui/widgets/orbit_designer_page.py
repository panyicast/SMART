from __future__ import annotations

from pathlib import Path

from PySide6 import QtWidgets

from smart.domain.models import OrbitTrajectory, OrbitalElements
from smart.ui.i18n import I18nManager
from smart.ui.mission_state import MissionState
from smart.ui.widgets.orbit_views import OrbitPlot2D, OrbitPlot3D
from smart.ui.widgets.spinboxes import NoWheelDoubleSpinBox

_VALIDATION_ERROR_KEYS = {
    "Semi-major axis must be larger than the central-body radius.": "orbit.error.semi_major_axis",
    "Eccentricity must satisfy 0 <= e < 1 for an elliptical orbit.": "orbit.error.eccentricity",
    "Periapsis must remain above the central-body surface.": "orbit.error.periapsis",
}


class OrbitDesignerPage(QtWidgets.QWidget):
    def __init__(
        self,
        mission_state: MissionState,
        i18n: I18nManager,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._mission_state = mission_state
        self._i18n = i18n
        self._metric_value_labels: dict[str, QtWidgets.QLabel] = {}
        self._metric_caption_labels: dict[str, QtWidgets.QLabel] = {}
        self._field_labels: dict[str, QtWidgets.QLabel] = {}
        self._last_error_message: str | None = None

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

        splitter = QtWidgets.QSplitter()
        splitter.setChildrenCollapsible(False)
        root.addWidget(splitter, 1)

        controls = self._build_controls()
        splitter.addWidget(controls)

        visual_panel = QtWidgets.QWidget()
        visual_layout = QtWidgets.QVBoxLayout(visual_panel)
        visual_layout.setContentsMargins(0, 0, 0, 0)
        visual_layout.setSpacing(14)

        plot_row = QtWidgets.QHBoxLayout()
        plot_row.setSpacing(14)
        visual_layout.addLayout(plot_row, 1)

        self._plot_2d = OrbitPlot2D()
        self._plot_3d = OrbitPlot3D()
        card_2d, self._card_2d_title_label = self._wrap_card(self._plot_2d)
        card_3d, self._card_3d_title_label = self._wrap_card(self._plot_3d)
        plot_row.addWidget(card_2d, 1)
        plot_row.addWidget(card_3d, 1)

        splitter.addWidget(visual_panel)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([320, 980])

        self._mission_state.trajectory_changed.connect(self._refresh)
        self._i18n.language_changed.connect(self.retranslate)
        self.retranslate()
        self._refresh(self._mission_state.trajectory)

    def _build_controls(self) -> QtWidgets.QWidget:
        card = QtWidgets.QFrame()
        card.setProperty("role", "card")
        layout = QtWidgets.QVBoxLayout(card)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        self._inputs_header_label = QtWidgets.QLabel()
        self._inputs_header_label.setProperty("role", "cardTitle")
        layout.addWidget(self._inputs_header_label)

        self._semi_major_axis = self._spinbox(7000.0, 6600.0, 120000.0, 10.0, 1)
        self._eccentricity = self._spinbox(0.05, 0.0, 0.95, 0.01, 3)
        self._inclination = self._spinbox(28.5, 0.0, 180.0, 0.1, 2)
        self._raan = self._spinbox(40.0, 0.0, 360.0, 0.1, 2)
        self._argp = self._spinbox(10.0, 0.0, 360.0, 0.1, 2)
        self._true_anomaly = self._spinbox(0.0, 0.0, 360.0, 0.1, 2)

        form = QtWidgets.QFormLayout()
        self._field_labels["semi_major_axis"] = QtWidgets.QLabel()
        self._field_labels["eccentricity"] = QtWidgets.QLabel()
        self._field_labels["inclination"] = QtWidgets.QLabel()
        self._field_labels["raan"] = QtWidgets.QLabel()
        self._field_labels["argp"] = QtWidgets.QLabel()
        self._field_labels["true_anomaly"] = QtWidgets.QLabel()
        form.addRow(self._field_labels["semi_major_axis"], self._semi_major_axis)
        form.addRow(self._field_labels["eccentricity"], self._eccentricity)
        form.addRow(self._field_labels["inclination"], self._inclination)
        form.addRow(self._field_labels["raan"], self._raan)
        form.addRow(self._field_labels["argp"], self._argp)
        form.addRow(self._field_labels["true_anomaly"], self._true_anomaly)
        layout.addLayout(form)

        self._error_label = QtWidgets.QLabel()
        self._error_label.setProperty("role", "pageBody")
        self._error_label.setWordWrap(True)
        self._error_label.setStyleSheet("color: #a13f22;")
        layout.addWidget(self._error_label)

        self._update_button = QtWidgets.QPushButton()
        self._update_button.clicked.connect(self._apply_inputs)
        layout.addWidget(self._update_button)

        metrics_card = QtWidgets.QFrame()
        metrics_card.setProperty("role", "card")
        metrics_layout = QtWidgets.QVBoxLayout(metrics_card)
        metrics_layout.setContentsMargins(16, 16, 16, 16)
        metrics_layout.setSpacing(10)

        self._metrics_title_label = QtWidgets.QLabel()
        self._metrics_title_label.setProperty("role", "cardTitle")
        metrics_layout.addWidget(self._metrics_title_label)

        for key in ("period", "perigee_altitude", "apogee_altitude", "current_speed"):
            value_label = QtWidgets.QLabel("--")
            value_label.setProperty("role", "metricValue")
            caption_label = QtWidgets.QLabel()
            caption_label.setProperty("role", "cardCaption")
            metric_row = QtWidgets.QVBoxLayout()
            metric_row.addWidget(value_label)
            metric_row.addWidget(caption_label)
            metrics_layout.addLayout(metric_row)
            self._metric_value_labels[key] = value_label
            self._metric_caption_labels[key] = caption_label

        layout.addWidget(metrics_card)
        layout.addStretch(1)
        return card

    def _apply_inputs(self) -> None:
        elements = OrbitalElements(
            semi_major_axis_km=self._semi_major_axis.value(),
            eccentricity=self._eccentricity.value(),
            inclination_deg=self._inclination.value(),
            raan_deg=self._raan.value(),
            argument_of_periapsis_deg=self._argp.value(),
            true_anomaly_deg=self._true_anomaly.value(),
        )
        try:
            self._mission_state.update_elements(elements)
        except ValueError as exc:
            self._last_error_message = str(exc)
            self._error_label.setText(self._translate_validation_error(self._last_error_message))
            return
        self._last_error_message = None
        self._error_label.setText("")

    def _refresh(self, trajectory: OrbitTrajectory) -> None:
        t = self._i18n.t
        elements = self._mission_state.elements
        self._plot_2d.set_trajectory(trajectory, elements.central_body_radius_km)
        self._plot_3d.set_trajectory(trajectory, elements.central_body_radius_km)

        self._metric_value_labels["period"].setText(t("orbit.time_min", value=elements.period_seconds / 60.0))
        self._metric_value_labels["perigee_altitude"].setText(
            f"{elements.perigee_radius_km - elements.central_body_radius_km:.1f} km"
        )
        self._metric_value_labels["apogee_altitude"].setText(
            f"{elements.apogee_radius_km - elements.central_body_radius_km:.1f} km"
        )
        self._metric_value_labels["current_speed"].setText(
            f"{float((trajectory.current_velocity_km_s**2).sum() ** 0.5):.3f} km/s"
        )

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
        return box

    def _wrap_card(self, widget: QtWidgets.QWidget) -> tuple[QtWidgets.QFrame, QtWidgets.QLabel]:
        card = QtWidgets.QFrame()
        card.setProperty("role", "card")
        layout = QtWidgets.QVBoxLayout(card)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        header = QtWidgets.QLabel()
        header.setProperty("role", "cardTitle")
        layout.addWidget(header)
        layout.addWidget(widget, 1)
        return card, header

    def _translate_validation_error(self, message: str) -> str:
        key = _VALIDATION_ERROR_KEYS.get(message)
        if key is None:
            return message
        return self._i18n.t(key)

    def retranslate(self, _language: str | None = None) -> None:
        t = self._i18n.t
        self._title_label.setText(t("orbit.title"))
        self._subtitle_label.setText(t("orbit.subtitle"))
        self._card_2d_title_label.setText(t("orbit.view_2d"))
        self._card_3d_title_label.setText(t("orbit.view_3d"))
        self._inputs_header_label.setText(t("orbit.inputs_header"))
        self._field_labels["semi_major_axis"].setText(t("orbit.field.semi_major_axis"))
        self._field_labels["eccentricity"].setText(t("orbit.field.eccentricity"))
        self._field_labels["inclination"].setText(t("orbit.field.inclination"))
        self._field_labels["raan"].setText(t("orbit.field.raan"))
        self._field_labels["argp"].setText(t("orbit.field.argp"))
        self._field_labels["true_anomaly"].setText(t("orbit.field.true_anomaly"))
        self._update_button.setText(t("orbit.update_button"))
        self._metrics_title_label.setText(t("orbit.metrics_title"))
        self._metric_caption_labels["period"].setText(t("orbit.metric.period"))
        self._metric_caption_labels["perigee_altitude"].setText(t("orbit.metric.perigee_altitude"))
        self._metric_caption_labels["apogee_altitude"].setText(t("orbit.metric.apogee_altitude"))
        self._metric_caption_labels["current_speed"].setText(t("orbit.metric.current_speed"))
        if self._last_error_message:
            self._error_label.setText(self._translate_validation_error(self._last_error_message))
        self._refresh(self._mission_state.trajectory)

    def export_charts(self, output_dir: Path) -> list[Path]:
        output_dir.mkdir(parents=True, exist_ok=True)
        orbit_2d_path = output_dir / "orbit_2d.png"
        orbit_3d_path = output_dir / "orbit_3d.png"
        self._plot_2d.grab().save(str(orbit_2d_path))
        self._plot_3d.grab().save(str(orbit_3d_path))
        return [orbit_2d_path, orbit_3d_path]
