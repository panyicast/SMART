from __future__ import annotations

from datetime import timezone
from types import SimpleNamespace

from PySide6 import QtCore, QtWidgets

from smart.services.earth_orientation import parse_utc
from smart.services.launch_window import BURN_SUN_AXIS_MINUS_Z, CONSTRAINT_TYPE_GROUND_VISIBLE
from smart.services.project_workspace import ProjectWorkspace
from smart.ui.i18n import I18nManager
import smart.ui.widgets.launch_window_page as launch_window_page_module
from smart.ui.widgets.launch_window_page import (
    LaunchWindowGanttWidget,
    LaunchWindowPage,
    _GanttScrollArea,
    _LaunchWindowStateDialog,
    _StateComboBox,
)
from smart.ui.widgets.spinboxes import NoWheelComboBox

_TABLE_GEOMETRY_TOLERANCE_PX = 8
_CONTROL_WIDTH_TOLERANCE_PX = 5


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
    assert isinstance(combo, _StateComboBox)
    assert combo.currentData() == BURN_SUN_AXIS_MINUS_Z


def test_launch_window_state_settings_use_dialog_and_cancel_restores_values(tmp_path) -> None:
    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    workspace = ProjectWorkspace()
    workspace.create_project("launch-window-page", tmp_path)

    page = LaunchWindowPage(I18nManager("zh"), workspace)
    dialog = page._state_dialog

    assert isinstance(dialog, _LaunchWindowStateDialog)
    assert page._edit_state_button.text() == "状态设置"
    close_button = dialog.findChild(QtWidgets.QToolButton, "dialogCloseButton")
    assert close_button is not None
    assert close_button.cursor().shape() == QtCore.Qt.CursorShape.PointingHandCursor
    assert page._ground_station_table.window() is dialog
    assert page._relay_satellite_table.window() is dialog
    assert page._constraint_table.window() is dialog
    assert 900 <= dialog.minimumWidth() < 960
    assert page._ground_station_table.columnWidth(1) >= 220
    assert page._ground_station_table.maximumWidth() < dialog.minimumWidth()
    assert abs(page._constraint_table.width() - page._ground_station_table.width()) <= _TABLE_GEOMETRY_TOLERANCE_PX
    assert abs(page._constraint_table.maximumWidth() - page._ground_station_table.maximumWidth()) <= _TABLE_GEOMETRY_TOLERANCE_PX
    assert page._constraint_table.horizontalHeader().sectionResizeMode(1) == QtWidgets.QHeaderView.ResizeMode.Stretch
    assert abs(page._constraint_table.columnWidth(2) - 170) <= _CONTROL_WIDTH_TOLERANCE_PX
    assert abs(page._constraint_table.columnWidth(3) - 170) <= _CONTROL_WIDTH_TOLERANCE_PX
    assert abs(page._constraint_table.columnWidth(4) - 180) <= _CONTROL_WIDTH_TOLERANCE_PX
    assert page._constraint_table.item(0, 2).textAlignment() & QtCore.Qt.AlignmentFlag.AlignRight
    assert page._constraint_table.item(0, 3).textAlignment() & QtCore.Qt.AlignmentFlag.AlignRight
    assert abs(page._number_fields["ground_station_min_elevation_deg"].width() - 132) <= _CONTROL_WIDTH_TOLERANCE_PX
    assert abs(page._number_fields["relay_alpha_abs_max_deg"].width() - 132) <= _CONTROL_WIDTH_TOLERANCE_PX
    assert isinstance(page._combo_fields["burn_sun_axis"], _StateComboBox)
    assert abs(page._combo_fields["burn_sun_axis"].width() - 210) <= _CONTROL_WIDTH_TOLERANCE_PX
    assert "启用条件" in page._state_summary_label.text()
    assert "地面站" in page._state_summary_label.text()
    assert "Xiamen Station" in page._state_assets_label.text()
    assert "Weinan Station" in page._state_assets_label.text()
    assert "TL2-2" in page._state_assets_label.text()

    original_elevation = page._number_fields["ground_station_min_elevation_deg"].value()

    def change_and_reject() -> None:
        page._number_fields["ground_station_min_elevation_deg"].setValue(original_elevation + 10.0)
        dialog.reject()

    QtWidgets.QApplication.instance().processEvents()
    QtCore.QTimer.singleShot(0, change_and_reject)
    page._open_state_settings_dialog()

    assert page._number_fields["ground_station_min_elevation_deg"].value() == original_elevation
    assert f"{original_elevation:.2f} deg" in page._state_details_label.text()


def test_launch_window_calculate_button_is_primary_and_exports_csv(tmp_path, monkeypatch) -> None:
    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    workspace = ProjectWorkspace()
    workspace.create_project("launch-window-export", tmp_path)
    page = LaunchWindowPage(I18nManager("zh"), workspace)

    assert page._calculate_button.property("variant") == "primaryAction"
    assert not hasattr(page, "_reload_button")
    assert not hasattr(page, "_save_button")
    assert page._calculate_button.text() == "计算发射窗口"
    assert page._save_results_button.text() == "导出结果"

    def fake_compute_launch_windows(**_kwargs):
        return [
            SimpleNamespace(
                window_start_utc="2026-05-15T00:00:00Z",
                window_end_utc="2026-05-15T00:10:00Z",
                duration_min=10.0,
                first_orbit_shadow_min=1.0,
                window_start_longest_shadow_min=2.0,
                window_end_longest_shadow_min=3.0,
                window_start_constraint="",
                window_end_constraint="",
            )
        ], [{"launch_utc": "2026-05-15T00:00:00Z", "t0_utc": "2026-05-15T00:35:34Z", "ok": True}]

    monkeypatch.setattr(launch_window_page_module, "compute_launch_windows", fake_compute_launch_windows)
    page.calculate_windows()

    default_csv = workspace.data_dir() / "launch_window_results.csv"
    assert default_csv.exists()
    assert page._save_results_button.isEnabled()
    assert "已自动保存结果 CSV" in page._status_label.text()

    export_csv = tmp_path / "selected_export.csv"
    monkeypatch.setattr(
        QtWidgets.QFileDialog,
        "getSaveFileName",
        lambda *_args, **_kwargs: (str(export_csv), "CSV 文件 (*.csv)"),
    )
    page._save_results_button.click()

    assert export_csv.exists()
    assert "已导出结果 CSV" in page._status_label.text()

    page._set_result_rows([])
    empty_csv = tmp_path / "empty_export.csv"
    assert page._save_result_csv(empty_csv) == empty_csv
    assert empty_csv.exists()


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
