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

    def state(
        self,
        target: str,
        observer: str,
        utc: str,
        frame: str = "J2000",
        aberration: str = "NONE",
    ) -> BodyState:
        self.calls.append((target, observer, utc, frame, aberration))
        base = 1.0 if target == "SUN" else 2.0
        return BodyState(
            position_km=np.array([base, base + 1.0, base + 2.0]),
            velocity_km_s=np.array([base / 10.0, base / 20.0, base / 40.0]),
            light_time_s=base + 3.0,
        )


def test_solar_lunar_dialog_queries_earth_relative_j2000_states() -> None:
    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    manager = _FakeKernelManager()

    dialog = SolarLunarPositionDialog(I18nManager(), manager)  # type: ignore[arg-type]
    try:
        dialog._calculate_positions()

        assert [call[:2] for call in manager.calls] == [("SUN", "EARTH"), ("MOON", "EARTH")]
        assert all(call[3:] == ("J2000", "NONE") for call in manager.calls)
        assert dialog._state_table.item(0, 0).text() == "Sun"
        assert dialog._state_table.item(1, 0).text() == "Moon"
        assert dialog._status.text().startswith("计算完成。UTC:")
    finally:
        dialog.deleteLater()
