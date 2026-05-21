from __future__ import annotations

import numpy as np
import pytest
from PySide6 import QtCore
from PySide6 import QtWidgets

from smart.services.project_workspace import ProjectWorkspace
from smart.ui.i18n import I18nManager
from smart.ui.widgets.maneuver_page import ManeuverPage, _GroundTrackViewBox, _ManeuverConfigDialog


def test_maneuver_page_uses_readonly_summary_and_edit_dialog(tmp_path) -> None:
    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    workspace = ProjectWorkspace()
    workspace.create_project("maneuver-page", tmp_path)

    page = ManeuverPage(I18nManager("zh"), workspace)
    strategy = workspace.load_maneuver_strategy()
    assert strategy is not None

    assert page._title_label.text() == "导入变轨策略"
    assert not hasattr(page, "_subtitle_label")
    assert page._ground_track_title_label.text() == "星下点轨迹"
    assert page._initial_state_header_label.text() == "配置参数"
    assert page._edit_config_button.text().endswith("修改配置")
    assert page._import_design_strategy_button.text().endswith("引入变轨策略")
    assert page._calculate_button.text() == "计算变轨策略"
    assert page._calculate_button.property("variant") == "primaryAction"
    assert isinstance(page._ground_track_plot.getViewBox(), _GroundTrackViewBox)
    assert len(page._ground_track_map_items) in {0, 5}
    assert len(page._ground_track_curves) == 5
    assert len(page._ground_track_markers) == 5
    assert page._ground_track_plot.plotItem.getAxis("bottom").labelText == ""
    assert page._ground_track_plot.plotItem.getAxis("left").labelText == ""
    unwrapped_lons = page._unwrap_longitudes(np.asarray([170.0, 179.0, -178.0, -170.0]))
    assert not np.isnan(unwrapped_lons).any()
    assert np.all(np.abs(np.diff(unwrapped_lons)) < 180.0)
    page._set_ground_track_start_marker({"subsatellite_longitude_deg": 10.0, "subsatellite_latitude_deg": 0.0})
    page._ground_track_start_row = {"subsatellite_longitude_deg": 10.0, "subsatellite_latitude_deg": 0.0}
    page._ground_track_maneuver_summaries = [
        {"maneuver_index": 1, "subsatellite_longitude_deg": 20.0, "subsatellite_latitude_deg": 5.0}
    ]
    page._set_maneuver_number_labels(page._ground_track_maneuver_summaries)
    page._ground_track_plot.setXRange(180.0, 540.0, padding=0.0)
    page._refresh_ground_track_annotations()
    assert page._ground_track_start_marker.xData[0] == 370.0
    assert page._maneuver_number_labels[0].pos().x() == 380.0
    assert page._maneuver_number_labels[0].pos().y() == 10.0
    assert page._maneuver_number_labels[0].border.style() == QtCore.Qt.PenStyle.NoPen
    assert page._maneuver_number_labels[0].fill.style() == QtCore.Qt.BrushStyle.NoBrush
    assert page._maneuver_number_labels[0].textItem.font().bold()
    assert page._maneuver_number_labels[0].textItem.font().pointSize() == 9
    assert len(page._maneuver_number_label_outlines) == 4
    assert page._strategy_table.editTriggers() == QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers
    assert page._strategy_table.columnCount() == 2
    assert page._strategy_table.rowCount() == len(strategy["maneuvers"])
    assert set(page._initial_value_labels) == {"inclination_deg"}
    assert set(page._entry_aux_values) == {"perigee_altitude_m", "apogee_altitude_m"}
    assert not hasattr(page, "_save_button")
    assert not hasattr(page, "_add_button")
    assert not hasattr(page, "_remove_button")

    design_strategy = {
        "launch_mass_kg": 6400.0,
        "t0_epoch": "2026-04-24T13:54:27Z",
        "t0_orbit": {
            "semi_major_axis_m": 29478137.0,
            "eccentricity": 0.77684692,
            "inclination_deg": 16.5,
            "argument_of_perigee_deg": 200.0,
            "raan_deg": 8.53237,
            "mean_anomaly_deg": 1.85437,
        },
        "maneuver_count": 2,
        "maneuvers": [
            {
                "maneuver_index": 1,
                "Tn_start_min": 10.0,
                "burn_duration_min": 2.5,
                "control_fuel_%": 1.73,
                "settle_duration_s": 60.0,
                "direction_mode": "local_horizontal_yaw",
                "yaw_angle_deg": 12.5,
                "delta_deg": 2.5,
                "dv_direction": 1,
                "orbit_control_thrust_n": 490.0,
                "orbit_control_isp_s": 314.1,
                "settle_thrust_n": 20.0,
                "settle_isp_s": 290.0,
            },
            {
                "maneuver_index": 2,
                "Tn_start_min": 120.0,
                "burn_duration_min": 3.0,
                "control_fuel_%": 1.73,
                "settle_duration_s": 60.0,
                "direction_mode": "local_horizontal_yaw",
                "yaw_angle_deg": -178.5,
                "delta_deg": -1.0,
                "dv_direction": -1,
                "orbit_control_thrust_n": 490.0,
                "orbit_control_isp_s": 314.1,
                "settle_thrust_n": 20.0,
                "settle_isp_s": 290.0,
            },
        ],
    }
    workspace.save_design_import_maneuver_strategy(design_strategy)
    page._import_design_strategy_button.click()
    imported = workspace.load_maneuver_strategy()
    assert imported is not None
    assert imported["launch_mass_kg"] == pytest.approx(6400.0)
    assert imported["maneuver_count"] == 2
    assert imported["maneuvers"][1]["direction_mode"] == "local_horizontal_yaw"
    assert imported["maneuvers"][1]["yaw_angle_deg"] == pytest.approx(-178.5)
    assert imported["maneuvers"][1]["dv_direction"] == -1
    assert page._strategy_table.rowCount() == 2
    assert "已从设计变轨策略导入配置" in page._status_label.text()

    dialog = _ManeuverConfigDialog(page._i18n, page.strategy(), page._COLUMNS, page)
    assert not dialog.findChildren(QtWidgets.QTabWidget)
    assert dialog._table.minimumHeight() == dialog._table.maximumHeight()
    assert dialog._t0_epoch_field.displayFormat() == "yyyy-MM-dd HH:mm:ss"
    assert dialog._t0_epoch_field.dateTime().timeZone().id().data().decode() == "Asia/Shanghai"
    for column in range(dialog._table.columnCount()):
        widget = dialog._table.cellWidget(0, column)
        assert widget is not None
        assert dialog._table.rowHeight(0) >= widget.sizeHint().height()
    direction_widget = dialog._table.cellWidget(0, 7)
    assert isinstance(direction_widget, QtWidgets.QComboBox)
    assert direction_widget.maxVisibleItems() == 2
    beijing_tz = QtCore.QTimeZone(b"Asia/Shanghai")
    dialog._t0_epoch_field.setDateTime(
        QtCore.QDateTime(QtCore.QDate(2024, 1, 1), QtCore.QTime(8, 0, 0), beijing_tz)
    )
    dialog._launch_mass_field.setValue(7000.0)
    edited = dialog.strategy()

    assert edited["launch_mass_kg"] == 7000.0
    assert edited["t0_epoch"] == "2024-01-01T00:00:00Z"
    assert len(edited["maneuvers"]) == len(imported["maneuvers"])
    assert edited["maneuvers"][0]["direction_mode"] == "local_horizontal_yaw"
    assert edited["maneuvers"][0]["yaw_angle_deg"] == pytest.approx(12.5)
    dialog.close()
    dialog.deleteLater()
