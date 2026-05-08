from __future__ import annotations

from datetime import timezone

from PySide6 import QtWidgets

from smart.services.earth_orientation import parse_utc
from smart.services.launch_window import BURN_SUN_AXIS_MINUS_Z, CONSTRAINT_TYPE_GROUND_VISIBLE
from smart.ui.widgets.launch_window_page import LaunchWindowGanttWidget, LaunchWindowPage, _GanttScrollArea
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


def test_launch_window_gantt_supports_local_zoom_and_reset() -> None:
    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    widget = LaunchWindowGanttWidget()
    widget.resize(1200, 360)
    widget.set_samples(
        [
            {"launch_utc": "2026-05-15T00:00:00Z", "ok": True},
            {"launch_utc": "2026-05-15T00:10:00Z", "ok": True},
            {"launch_utc": "2026-05-15T00:20:00Z", "ok": False},
            {"launch_utc": "2026-05-15T00:30:00Z", "ok": True},
        ]
    )

    original_start, original_end = widget._visible_range()
    assert original_start == parse_utc("2026-05-15T00:00:00Z")
    assert original_end == parse_utc("2026-05-15T00:40:00Z")

    changed = widget._zoom_view(widget._plot_rect().center().x(), 0.8)

    zoomed_start, zoomed_end = widget._visible_range()
    assert changed is True
    assert (zoomed_end - zoomed_start) < (original_end - original_start)

    widget._reset_view_range()

    reset_start, reset_end = widget._visible_range()
    assert reset_start == original_start
    assert reset_end == original_end


def test_launch_window_gantt_scroll_area_forwards_wheel_to_zoom() -> None:
    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    chart = LaunchWindowGanttWidget()
    chart.resize(1200, 360)
    chart.set_samples(
        [
            {"launch_utc": "2026-05-15T00:00:00Z", "ok": True},
            {"launch_utc": "2026-05-15T00:10:00Z", "ok": True},
            {"launch_utc": "2026-05-15T00:20:00Z", "ok": True},
            {"launch_utc": "2026-05-15T00:30:00Z", "ok": True},
        ]
    )
    scroll = _GanttScrollArea()
    scroll.resize(1200, 360)
    scroll.setWidget(chart)

    original_start, original_end = chart._visible_range()
    forwarded = scroll._forward_wheel_to_chart_x(chart._plot_rect().center().x(), 120)

    zoomed_start, zoomed_end = chart._visible_range()
    assert forwarded is True
    assert (zoomed_end - zoomed_start) < (original_end - original_start)
