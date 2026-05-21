from __future__ import annotations

import numpy as np
from PySide6 import QtWidgets

from smart.services.spice_service import BodyState
from smart.ui.i18n import I18nManager
from smart.ui.widgets.common_orbital_tools import (
    HohmannTransferDialog,
    OrbitalConversionDialog,
    SolarLunarPositionDialog,
)


def test_orbital_conversion_dialog_calculates_both_directions() -> None:
    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    dialog = OrbitalConversionDialog(I18nManager())
    try:
        assert dialog._state_output_table.item(0, 0).text() == "位置"
        assert dialog._state_output_table.item(1, 4).text() == "km/s"

        dialog._convert_state_to_elements()

        assert float(dialog._elements_output_table.item(0, 0).text()) > 0.0
        assert 0.0 <= float(dialog._elements_output_table.item(0, 1).text()) < 1.0
        assert dialog._state_status.text() == "计算完成。"
    finally:
        dialog.deleteLater()


def test_hohmann_dialog_calculates_circular_transfer() -> None:
    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    dialog = HohmannTransferDialog(I18nManager())
    try:
        assert float(dialog._result_table.item(0, 5).text()) > 0.0
        assert float(dialog._result_table.item(0, 6).text()) > 0.0
        assert dialog._status.text() == "计算完成。"
    finally:
        dialog.deleteLater()


class _FakeKernelManager:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str, str, str]] = []
        self.transform_calls: list[tuple[str, str, str]] = []

    def state(
        self,
        target: str,
        observer: str,
        utc: str,
        frame: str = "J2000",
        aberration: str = "NONE",
    ) -> BodyState:
        self.calls.append((target, observer, utc, frame, aberration))
        is_sun = target == "SUN"
        return BodyState(
            position_km=np.array([10000.0, 0.0, 0.0]) if is_sun else np.array([0.0, 20000.0, 0.0]),
            velocity_km_s=np.array([0.1, 0.0, 0.0]) if is_sun else np.array([0.0, 0.2, 0.0]),
            light_time_s=4.0 if is_sun else 5.0,
        )

    def transform_state(
        self,
        position_km: np.ndarray,
        velocity_km_s: np.ndarray,
        *,
        from_frame: str,
        to_frame: str,
        utc: str,
    ) -> tuple[np.ndarray, np.ndarray]:
        self.transform_calls.append((from_frame, to_frame, utc))
        return np.asarray(position_km, dtype=np.float64), np.asarray(velocity_km_s, dtype=np.float64)


def test_solar_lunar_dialog_queries_earth_relative_j2000_states() -> None:
    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    manager = _FakeKernelManager()

    dialog = SolarLunarPositionDialog(I18nManager(), manager)  # type: ignore[arg-type]
    try:
        dialog._calculate_positions()

        assert [call[:2] for call in manager.calls] == [("SUN", "EARTH"), ("MOON", "EARTH")]
        assert all(call[3:] == ("J2000", "NONE") for call in manager.calls)
        assert [call[:2] for call in manager.transform_calls] == [("J2000", "ITRF93"), ("J2000", "ITRF93")]
        assert dialog._state_table.item(0, 0).text() == "Sun"
        assert dialog._state_table.item(1, 0).text() == "Moon"
        assert float(dialog._state_table.item(0, 7).text()) == 0.0
        assert float(dialog._state_table.item(0, 8).text()) == 0.0
        assert float(dialog._state_table.item(1, 7).text()) == 90.0
        assert float(dialog._state_table.item(1, 8).text()) == 0.0
        assert dialog._status.text().startswith("计算完成。UTC:")
    finally:
        dialog.deleteLater()
