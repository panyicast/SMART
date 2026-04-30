from __future__ import annotations

from datetime import timezone

from PySide6 import QtWidgets

from smart.services.launch_window import BURN_SUN_AXIS_MINUS_Z, CONSTRAINT_TYPE_GROUND_VISIBLE
from smart.ui.widgets.launch_window_page import LaunchWindowPage
from smart.ui.widgets.spinboxes import NoWheelComboBox


def test_launch_window_datetime_fields_display_beijing_time() -> None:
    qdt = LaunchWindowPage._utc_to_qdatetime("2026-05-15T07:00:00Z")

    assert qdt.offsetFromUtc() == 8 * 3600
    assert qdt.toString("yyyy-MM-dd HH:mm:ss") == "2026-05-15 15:00:00"

    utc = qdt.toUTC().toPython()
    if utc.tzinfo is None:
        utc = utc.replace(tzinfo=timezone.utc)
    assert utc.isoformat().replace("+00:00", "Z") == "2026-05-15T07:00:00Z"


def test_launch_window_constraint_type_combo_ignores_wheel_changes() -> None:
    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    combo = LaunchWindowPage._constraint_type_combo(None, CONSTRAINT_TYPE_GROUND_VISIBLE)

    assert isinstance(combo, NoWheelComboBox)


def test_launch_window_constraint_time_cells_preserve_expressions() -> None:
    assert LaunchWindowPage._constraint_time_payload("1074") == 1074.0
    assert LaunchWindowPage._constraint_time_payload("T1_start-180") == "T1_start-180"
    assert LaunchWindowPage._format_constraint_time_cell(1074) == "1074.000"
    assert LaunchWindowPage._format_constraint_time_cell("T1_end+60") == "T1_end+60"


def test_burn_sun_axis_combo_uses_no_wheel_combo() -> None:
    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    combo = LaunchWindowPage._burn_sun_axis_combo(BURN_SUN_AXIS_MINUS_Z)

    assert isinstance(combo, NoWheelComboBox)
    assert combo.currentData() == BURN_SUN_AXIS_MINUS_Z
