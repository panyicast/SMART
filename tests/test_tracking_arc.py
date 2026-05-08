from __future__ import annotations

import csv
from pathlib import Path

import pytest
from PySide6 import QtWidgets

from smart.services.earth_orientation import parse_utc
from smart.services.launch_window import LaunchWindowResult, TrackingAsset, config_from_payload, default_launch_window_config
from smart.services.tracking_arc import (
    TRACKING_ARC_POINT_LEADING,
    TRACKING_ARC_POINT_MIDPOINT,
    TRACKING_ARC_POINT_TRAILING,
    TrackingArcOrbitResult,
    TrackingArcSegment,
    compute_tracking_arcs_for_window,
    tracking_arc_launch_points,
)
from smart.ui.widgets.tracking_arc_page import TrackingArcGanttWidget, _GanttScrollArea


def _write_history(path: Path) -> None:
    columns = [
        "elapsed_time_s",
        "elapsed_time_min",
        "phase",
        "is_event_point",
        "semi_major_axis_m",
        "eccentricity",
        "inclination_deg",
        "raan_deg",
        "argument_of_perigee_deg",
        "true_anomaly_deg",
        "position_x_m",
        "position_y_m",
        "position_z_m",
        "velocity_x_m_s",
        "velocity_y_m_s",
        "velocity_z_m_s",
        "subsatellite_longitude_deg",
        "subsatellite_latitude_deg",
        "subsatellite_altitude_m",
        "orbit_height_m",
        "mass_kg",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for minute in (0, 10, 20, 30):
            writer.writerow(
                {
                    "elapsed_time_s": minute * 60,
                    "elapsed_time_min": minute,
                    "phase": "coast",
                    "is_event_point": 0,
                    "semi_major_axis_m": 7_000_000.0,
                    "eccentricity": 0.0,
                    "inclination_deg": 5.0,
                    "raan_deg": 0.0,
                    "argument_of_perigee_deg": 0.0,
                    "true_anomaly_deg": 0.0,
                    "position_x_m": 7_000_000.0,
                    "position_y_m": 0.0,
                    "position_z_m": 0.0,
                    "velocity_x_m_s": 0.0,
                    "velocity_y_m_s": 7_500.0,
                    "velocity_z_m_s": 0.0,
                    "subsatellite_longitude_deg": 0.0,
                    "subsatellite_latitude_deg": 0.0,
                    "subsatellite_altitude_m": 500_000.0,
                    "orbit_height_m": 500_000.0,
                    "mass_kg": 1000.0,
                }
            )


def _window() -> LaunchWindowResult:
    return LaunchWindowResult(
        window_start_utc="2026-05-15T00:00:00Z",
        window_end_utc="2026-05-15T00:20:00Z",
        duration_min=20.0,
        first_failure="",
        first_orbit_shadow_min=0.0,
        no_shadow_period_shadow_min=0.0,
        separation_shadow_min=0.0,
        min_burn_sun_margin_deg=0.0,
        max_tracking_gap_min=0.0,
        inclination_deg=0.0,
    )


def test_tracking_arc_launch_points_use_window_edges_and_midpoint() -> None:
    points = tracking_arc_launch_points(_window())

    assert [point[0] for point in points] == [
        TRACKING_ARC_POINT_LEADING,
        TRACKING_ARC_POINT_MIDPOINT,
        TRACKING_ARC_POINT_TRAILING,
    ]
    assert [point[2].isoformat().replace("+00:00", "Z") for point in points] == [
        "2026-05-15T00:00:00Z",
        "2026-05-15T00:10:00Z",
        "2026-05-15T00:20:00Z",
    ]


def test_compute_tracking_arcs_for_window_builds_three_orbit_gantts(tmp_path: Path) -> None:
    history_path = tmp_path / "full_orbit_history.csv"
    _write_history(history_path)
    payload = default_launch_window_config()
    payload.update(
        {
            "rocket_flight_time_s": 0.0,
            "ground_station_min_elevation_deg": -90.0,
            "ground_station_max_theta_st_deg": 180.0,
            "relay_satellite_presets": [],
            "custom_relay_satellites": [],
        }
    )
    config = config_from_payload(payload)
    assets = [TrackingAsset("测试站", 0.0, 0.0, 0.0, "ground")]
    strategy = {"maneuvers": [{"maneuver_index": 1, "Tn_start_min": 10.0, "burn_duration_min": 5.0}]}

    results = compute_tracking_arcs_for_window(
        orbit_history_csv=history_path,
        maneuver_strategy=strategy,
        config=config,
        window=_window(),
        assets=assets,
    )

    assert [result.point_key for result in results] == [
        TRACKING_ARC_POINT_LEADING,
        TRACKING_ARC_POINT_MIDPOINT,
        TRACKING_ARC_POINT_TRAILING,
    ]
    leading = results[0]
    assert leading.launch_utc == "2026-05-15T00:00:00Z"
    assert leading.t0_utc == "2026-05-15T00:00:00Z"
    assert "变轨点火时段" in leading.row_labels
    assert "地面站 测试站" in leading.row_labels
    assert "卫星地影时段" in leading.row_labels

    burn = next(segment for segment in leading.segments if segment.kind == "burn")
    assert burn.start_utc == "2026-05-15T00:10:00Z"
    assert burn.end_utc == "2026-05-15T00:15:00Z"

    summary = leading.asset_summaries[0]
    assert summary.name == "测试站"
    assert summary.interval_count == 1
    assert summary.total_duration_min == pytest.approx(40.0)


def test_compute_tracking_arcs_respects_explicit_empty_assets(tmp_path: Path) -> None:
    history_path = tmp_path / "full_orbit_history.csv"
    _write_history(history_path)
    payload = default_launch_window_config()
    payload["rocket_flight_time_s"] = 0.0
    config = config_from_payload(payload)

    results = compute_tracking_arcs_for_window(
        orbit_history_csv=history_path,
        maneuver_strategy={"maneuvers": []},
        config=config,
        window=_window(),
        assets=[],
    )

    assert results[0].asset_summaries == []
    assert results[0].row_labels == ["变轨点火时段", "卫星地影时段"]


def test_tracking_arc_gantt_supports_local_zoom_and_reset() -> None:
    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    widget = TrackingArcGanttWidget()
    widget.resize(1200, 360)
    result = TrackingArcOrbitResult(
        point_key="leading",
        point_label="窗口前沿轨道",
        launch_utc="2026-05-15T00:00:00Z",
        t0_utc="2026-05-15T00:00:00Z",
        timeline_start_utc="2026-05-15T00:00:00Z",
        timeline_end_utc="2026-05-15T01:00:00Z",
        row_labels=["变轨点火时段", "地面站 测试站"],
        segments=[
            TrackingArcSegment("变轨点火时段", "2026-05-15T00:10:00Z", "2026-05-15T00:20:00Z", "burn", ""),
            TrackingArcSegment("地面站 测试站", "2026-05-15T00:30:00Z", "2026-05-15T00:45:00Z", "ground", ""),
        ],
        asset_summaries=[],
        shadow_total_min=0.0,
        maneuver_count=1,
    )
    widget.set_result(result)

    original_start, original_end = widget._visible_range()
    assert original_start == parse_utc("2026-05-15T00:00:00Z")
    assert original_end == parse_utc("2026-05-15T01:00:00Z")

    changed = widget._zoom_view(widget._plot_rect().center().x(), 0.8)

    zoomed_start, zoomed_end = widget._visible_range()
    assert changed is True
    assert (zoomed_end - zoomed_start) < (original_end - original_start)

    widget._reset_view_range()

    reset_start, reset_end = widget._visible_range()
    assert reset_start == original_start
    assert reset_end == original_end


def test_tracking_arc_gantt_scroll_area_forwards_wheel_to_zoom() -> None:
    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    chart = TrackingArcGanttWidget()
    chart.resize(1200, 360)
    result = TrackingArcOrbitResult(
        point_key="leading",
        point_label="窗口前沿轨道",
        launch_utc="2026-05-15T00:00:00Z",
        t0_utc="2026-05-15T00:00:00Z",
        timeline_start_utc="2026-05-15T00:00:00Z",
        timeline_end_utc="2026-05-15T01:00:00Z",
        row_labels=["变轨点火时段"],
        segments=[TrackingArcSegment("变轨点火时段", "2026-05-15T00:10:00Z", "2026-05-15T00:20:00Z", "burn", "")],
        asset_summaries=[],
        shadow_total_min=0.0,
        maneuver_count=1,
    )
    chart.set_result(result)
    scroll = _GanttScrollArea()
    scroll.resize(1200, 360)
    scroll.setWidget(chart)

    original_start, original_end = chart._visible_range()
    forwarded = scroll._forward_wheel_to_chart_x(chart._plot_rect().center().x(), 120)

    zoomed_start, zoomed_end = chart._visible_range()
    assert forwarded is True
    assert (zoomed_end - zoomed_start) < (original_end - original_start)
