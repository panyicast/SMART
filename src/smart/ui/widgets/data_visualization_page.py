from __future__ import annotations

from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PySide6 import QtWidgets

from smart.domain.models import OrbitTrajectory
from smart.ui.i18n import I18nManager
from smart.ui.mission_state import MissionState


class DataVisualizationPage(QtWidgets.QWidget):
    def __init__(
        self,
        mission_state: MissionState,
        i18n: I18nManager,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._mission_state = mission_state
        self._i18n = i18n
        self._summary_value_labels: dict[str, QtWidgets.QLabel] = {}
        self._summary_caption_labels: dict[str, QtWidgets.QLabel] = {}

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

        top_row = QtWidgets.QHBoxLayout()
        top_row.setSpacing(14)
        root.addLayout(top_row, 1)

        top_row.addWidget(self._build_summary_card(), 0)
        top_row.addWidget(self._build_plot_card(), 1)

        self._mission_state.trajectory_changed.connect(self._refresh)
        self._i18n.language_changed.connect(self.retranslate)
        self.retranslate()
        self._refresh(self._mission_state.trajectory)

    def _build_summary_card(self) -> QtWidgets.QWidget:
        card = QtWidgets.QFrame()
        card.setProperty("role", "card")
        layout = QtWidgets.QVBoxLayout(card)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        self._summary_title_label = QtWidgets.QLabel()
        self._summary_title_label.setProperty("role", "cardTitle")
        layout.addWidget(self._summary_title_label)

        form = QtWidgets.QFormLayout()
        for key in ("radius", "speed", "x", "y", "z"):
            caption = QtWidgets.QLabel()
            value = QtWidgets.QLabel("--")
            value.setProperty("role", "metricValue")
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

        container = QtWidgets.QWidget()
        plots = QtWidgets.QVBoxLayout(container)
        plots.setContentsMargins(0, 0, 0, 0)
        plots.setSpacing(12)

        self._altitude_plot = pg.PlotWidget()
        self._speed_plot = pg.PlotWidget()

        for plot in (self._altitude_plot, self._speed_plot):
            plot.setBackground("#fffdf8")
            plot.showGrid(x=True, y=True, alpha=0.16)
            plot.setMenuEnabled(False)
            plot.plotItem.hideButtons()
            plots.addWidget(plot, 1)

        self._altitude_curve = self._altitude_plot.plot(pen=pg.mkPen("#0f7b8c", width=2.2))
        self._speed_curve = self._speed_plot.plot(pen=pg.mkPen("#c25c38", width=2.2))

        layout.addWidget(container, 1)
        return card

    def _refresh(self, trajectory: OrbitTrajectory) -> None:
        elements = self._mission_state.elements
        time_minutes = trajectory.elapsed_seconds / 60.0
        altitude = trajectory.radii_km - elements.central_body_radius_km

        self._altitude_curve.setData(time_minutes, altitude)
        self._speed_curve.setData(time_minutes, trajectory.speeds_km_s)

        current_radius = float(np.linalg.norm(trajectory.current_position_km))
        current_speed = float(np.linalg.norm(trajectory.current_velocity_km_s))
        self._summary_value_labels["radius"].setText(f"{current_radius:.1f} km")
        self._summary_value_labels["speed"].setText(f"{current_speed:.4f} km/s")
        self._summary_value_labels["x"].setText(f"{trajectory.current_position_km[0]:.1f} km")
        self._summary_value_labels["y"].setText(f"{trajectory.current_position_km[1]:.1f} km")
        self._summary_value_labels["z"].setText(f"{trajectory.current_position_km[2]:.1f} km")

    def retranslate(self, _language: str | None = None) -> None:
        t = self._i18n.t
        self._title_label.setText(t("viz.title"))
        self._subtitle_label.setText(t("viz.subtitle"))
        self._summary_title_label.setText(t("viz.summary_title"))
        self._summary_caption_labels["radius"].setText(t("viz.field.radius"))
        self._summary_caption_labels["speed"].setText(t("viz.field.speed"))
        self._summary_caption_labels["x"].setText(t("viz.field.x"))
        self._summary_caption_labels["y"].setText(t("viz.field.y"))
        self._summary_caption_labels["z"].setText(t("viz.field.z"))
        self._plot_title_label.setText(t("viz.plot_title"))
        self._altitude_plot.setLabel("left", t("viz.axis.altitude"), units="km")
        self._altitude_plot.setLabel("bottom", t("viz.axis.time"), units="min")
        self._speed_plot.setLabel("left", t("viz.axis.velocity"), units="km/s")
        self._speed_plot.setLabel("bottom", t("viz.axis.time"), units="min")

    def export_charts(self, output_dir: Path) -> list[Path]:
        output_dir.mkdir(parents=True, exist_ok=True)
        altitude_path = output_dir / "altitude_trend.png"
        velocity_path = output_dir / "velocity_trend.png"
        self._altitude_plot.grab().save(str(altitude_path))
        self._speed_plot.grab().save(str(velocity_path))
        return [altitude_path, velocity_path]
