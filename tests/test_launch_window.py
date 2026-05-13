from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np
import pytest

from smart.services.earth_orientation import parse_utc
from smart.services import launch_window as launch_window_module
from smart.services.launch_window import (
    BURN_SUN_AXIS_MINUS_Z,
    BURN_SUN_AXIS_PLUS_Z,
    CONSTRAINT_SCOPE_ALL,
    CONSTRAINT_SCOPE_GROUND,
    CONSTRAINT_SCOPE_RELAY,
    CONSTRAINT_TYPE_GROUND_ELEVATION,
    CONSTRAINT_TYPE_GROUND_OR_RELAY_VISIBLE,
    CONSTRAINT_TYPE_GROUND_VISIBLE,
    CONSTRAINT_TYPE_NO_SHADOW,
    CONSTRAINT_TYPE_RELAY_ALPHA_ABS,
    CONSTRAINT_TYPE_RELAY_BETA_ABS,
    CONSTRAINT_TYPE_RELAY_VISIBLE,
    CONSTRAINT_TYPE_THETA_S,
    TrackingAsset,
    ManeuverInterval,
    _body_plus_z_ecef_for_attitude,
    _build_timeline,
    _constraint_time_parameters,
    _ecef_from_geodetic,
    _evaluate_candidate,
    _ground_elevation_matrix,
    _maneuver_intervals,
    _relay_target_angles_matrix,
    _resolve_constraint_time_value,
    _theta_s_deg_from_body_plus_z,
    _sun_unit_ecef_for_elapsed,
    compute_shadow_intervals_for_launch,
    compute_launch_windows,
    config_from_payload,
    default_ground_station_presets,
    default_relay_satellite_presets,
    default_launch_window_config,
    normalize_launch_window_config,
    tracking_assets_from_config,
)


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
        for minute in range(0, 181, 10):
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
                    "position_x_m": 0.0,
                    "position_y_m": 0.0,
                    "position_z_m": 0.0,
                    "velocity_x_m_s": 0.0,
                    "velocity_y_m_s": 0.0,
                    "velocity_z_m_s": 0.0,
                    "subsatellite_longitude_deg": 110.0,
                    "subsatellite_latitude_deg": 30.0,
                    "subsatellite_altitude_m": 500_000.0,
                    "orbit_height_m": 500_000.0,
                    "mass_kg": 1000.0,
                }
            )


def test_normalize_launch_window_config_formats_epochs() -> None:
    payload = normalize_launch_window_config(
        {
            "start_utc": "2026-05-15T08:00:00+08:00",
            "end_utc": "2026-05-16T08:00:00+08:00",
            "sample_step_min": 30,
            "burn_sun_axis": "卫星+Z轴",
            "require_burn_sun_angle": False,
        }
    )

    assert payload["start_utc"] == "2026-05-15T00:00:00Z"
    assert payload["end_utc"] == "2026-05-16T00:00:00Z"
    assert payload["sample_step_min"] == 30.0
    assert payload["rocket_flight_time_s"] == 2134.4121
    assert payload["ground_station_min_elevation_deg"] == 5.0
    assert payload["ground_station_max_theta_st_deg"] == 70.0
    assert payload["relay_alpha_abs_max_deg"] == 20.0
    assert payload["relay_beta_abs_max_deg"] == 40.0
    assert payload["relay_max_theta_st_deg"] == 80.0
    assert payload["burn_sun_angle_max_deg"] == 90.0
    assert payload["burn_sun_axis"] == BURN_SUN_AXIS_PLUS_Z
    assert payload["require_burn_sun_angle"] is False
    assert payload["constraint_rows"]


def test_normalize_launch_window_config_accepts_ground_station_visibility_settings() -> None:
    payload = normalize_launch_window_config(
        {
            "ground_station_min_elevation_deg": 8.5,
            "ground_station_max_theta_st_deg": 72.0,
        }
    )

    assert payload["ground_station_min_elevation_deg"] == 8.5
    assert payload["ground_station_max_theta_st_deg"] == 72.0


def test_normalize_launch_window_config_accepts_relay_visibility_settings() -> None:
    payload = normalize_launch_window_config(
        {
            "relay_alpha_abs_max_deg": 18.0,
            "relay_beta_abs_max_deg": 35.0,
            "relay_max_theta_st_deg": 75.0,
        }
    )

    assert payload["relay_alpha_abs_max_deg"] == 18.0
    assert payload["relay_beta_abs_max_deg"] == 35.0
    assert payload["relay_max_theta_st_deg"] == 75.0


def test_default_relay_satellite_presets_use_five_slots_and_expected_longitudes() -> None:
    presets = default_relay_satellite_presets()

    assert [item["name"] for item in presets] == ["TL2-1", "TL2-2", "TL2-3", "TL2-4", "TL2-5"]
    assert [item["longitude_deg"] for item in presets] == [77.0, 171.0, 10.6, 80.0, 20.4]


def test_default_ground_station_presets_use_english_names() -> None:
    presets = default_ground_station_presets()

    assert [item["name"] for item in presets] == [
        "Xiamen Station",
        "Weinan Station",
        "Jiamusi Station",
        "Kashi Station",
    ]


def test_normalize_launch_window_config_preserves_editable_constraint_rows() -> None:
    payload = normalize_launch_window_config(
        {
            "constraint_rows": [
                {
                    "enabled": False,
                    "name": "自定义",
                    "start_min": "10",
                    "end_min": "20",
                    "angles": "θs",
                    "rule": "θs<=90°",
                }
            ]
        }
    )

    assert payload["constraint_rows"] == [
        {
            "enabled": False,
            "name": "自定义",
            "start_min": 10.0,
            "end_min": 20.0,
            "condition_type": CONSTRAINT_TYPE_THETA_S,
            "operator": "",
            "threshold": None,
            "asset_scope": CONSTRAINT_SCOPE_ALL,
        }
    ]


def test_normalize_launch_window_config_preserves_structured_constraint_rows() -> None:
    payload = normalize_launch_window_config(
        {
            "constraint_rows": [
                {
                    "enabled": True,
                    "name": "仰角",
                    "start_min": 0,
                    "end_min": 150,
                    "condition_type": CONSTRAINT_TYPE_GROUND_VISIBLE,
                }
            ]
        }
    )

    assert payload["constraint_rows"] == [
        {
            "enabled": True,
            "name": "仰角",
            "start_min": 0.0,
            "end_min": 150.0,
            "condition_type": CONSTRAINT_TYPE_GROUND_VISIBLE,
            "operator": "",
            "threshold": None,
            "asset_scope": CONSTRAINT_SCOPE_GROUND,
        }
    ]


def test_normalize_launch_window_config_preserves_constraint_time_expressions() -> None:
    payload = normalize_launch_window_config(
        {
            "constraint_rows": [
                {
                    "enabled": True,
                    "name": "第一次变轨前测控",
                    "start_min": "T1_start-180",
                    "end_min": "T1_end+60",
                    "condition_type": CONSTRAINT_TYPE_GROUND_VISIBLE,
                }
            ]
        }
    )

    row = payload["constraint_rows"][0]
    assert row["start_min"] == "T1_start-180"
    assert row["end_min"] == "T1_end+60"


def test_constraint_time_expression_uses_maneuver_start_and_end_parameters() -> None:
    maneuvers = [
        ManeuverInterval(start_min=1254.667, end_min=1326.865, delta_deg=0.0, dv_direction=1, maneuver_index=1),
        ManeuverInterval(start_min=3938.667, end_min=3992.984, delta_deg=0.0, dv_direction=1, maneuver_index=2),
    ]
    parameters = _constraint_time_parameters(maneuvers)

    assert _resolve_constraint_time_value("T1_start-180", parameters) == pytest.approx(1074.667)
    assert _resolve_constraint_time_value("T1_end+60", parameters) == pytest.approx(1386.865)
    assert _resolve_constraint_time_value("T2_start", parameters) == pytest.approx(3938.667)


def test_maneuver_intervals_define_tn_end_from_total_burn_duration() -> None:
    intervals = _maneuver_intervals(
        {
            "maneuvers": [
                {
                    "maneuver_index": 1,
                    "Tn_start_min": 1254.667,
                    "burn_duration_min": 72.198,
                    "delta_deg": -17.85,
                }
            ]
        }
    )

    assert intervals[0].start_min == pytest.approx(1254.667)
    assert intervals[0].end_min == pytest.approx(1326.865)
    assert intervals[0].maneuver_index == 1


def test_normalize_launch_window_config_preserves_tracking_assets() -> None:
    payload = normalize_launch_window_config(
        {
            "ground_station_presets": [
                {
                    "enabled": True,
                    "name": "佳木斯站",
                    "longitude_deg": "130.3",
                    "latitude_deg": "46.8",
                    "altitude_m": "0",
                }
            ],
            "relay_satellite_presets": [
                {
                    "enabled": True,
                    "name": "TL2-3",
                    "longitude_deg": "87.5",
                    "latitude_deg": "0",
                    "altitude_m": "35786000",
                }
            ],
            "custom_ground_stations": [
                {
                    "enabled": True,
                    "name": "自定义站",
                    "longitude_deg": "100",
                    "latitude_deg": "20",
                    "altitude_m": "10",
                }
            ],
            "custom_relay_satellites": [
                {
                    "enabled": False,
                    "name": "自定义星",
                    "longitude_deg": "120",
                    "latitude_deg": "0",
                    "altitude_m": "35786001",
                }
            ],
        }
    )

    assert payload["ground_station_presets"][0]["asset_type"] == "ground"
    assert payload["ground_station_presets"][0]["name"] == "Jiamusi Station"
    assert payload["ground_station_presets"][0]["longitude_deg"] == 130.3
    assert payload["relay_satellite_presets"][0]["asset_type"] == "relay"
    assert payload["relay_satellite_presets"][0]["altitude_m"] == 35_786_000.0
    assert payload["custom_ground_stations"][0]["name"] == "自定义站"
    assert payload["custom_relay_satellites"][0]["enabled"] is False


def test_tracking_assets_from_config_uses_enabled_ground_and_relay_assets() -> None:
    config_payload = default_launch_window_config()
    config_payload["ground_station_presets"] = [
        {"enabled": True, "name": "Xiamen Station", "longitude_deg": 117.97, "latitude_deg": 24.64, "altitude_m": 0.0},
        {"enabled": False, "name": "Weinan Station", "longitude_deg": 109.5, "latitude_deg": 34.47, "altitude_m": 0.0},
    ]
    config_payload["relay_satellite_presets"] = [
        {"enabled": True, "name": "TL2-2", "longitude_deg": 171.0, "latitude_deg": 0.0, "altitude_m": 35_786_000.0},
    ]
    config_payload["custom_ground_stations"] = [
        {"enabled": True, "name": "测试站", "longitude_deg": 80.0, "latitude_deg": 40.0, "altitude_m": 123.0},
    ]
    config_payload["custom_relay_satellites"] = [
        {"enabled": False, "name": "禁用星", "longitude_deg": 90.0, "latitude_deg": 0.0, "altitude_m": 35_786_000.0},
    ]

    assets = tracking_assets_from_config(config_from_payload(config_payload))

    assert [asset.name for asset in assets] == ["Xiamen Station", "测试站", "TL2-2"]
    assert [asset.asset_type for asset in assets] == ["ground", "ground", "relay"]


def test_relay_position_is_fixed_by_configured_longitude_latitude_and_altitude() -> None:
    rows = [
        {
            "elapsed_time_min": 0.0,
            "phase": "coast",
            "inclination_deg": 5.0,
            "position_x_m": 1.0,
            "position_y_m": 2.0,
            "position_z_m": 3.0,
            "velocity_x_m_s": 4.0,
            "velocity_y_m_s": 5.0,
            "velocity_z_m_s": 6.0,
            "subsatellite_longitude_deg": 0.0,
            "subsatellite_latitude_deg": 0.0,
            "subsatellite_altitude_m": 500_000.0,
        },
        {
            "elapsed_time_min": 10.0,
            "phase": "coast",
            "inclination_deg": 5.0,
            "position_x_m": 7.0,
            "position_y_m": 8.0,
            "position_z_m": 9.0,
            "velocity_x_m_s": 10.0,
            "velocity_y_m_s": 11.0,
            "velocity_z_m_s": 12.0,
            "subsatellite_longitude_deg": 10.0,
            "subsatellite_latitude_deg": 5.0,
            "subsatellite_altitude_m": 500_000.0,
        },
    ]
    relay = TrackingAsset(
        name="TL2-fixed",
        longitude_deg=171.0,
        latitude_deg=0.0,
        altitude_m=35_786_000.0,
        asset_type="relay",
    )

    timeline = _build_timeline(rows, [relay])
    expected = _ecef_from_geodetic(171.0, 0.0, 35_786_000.0)

    assert timeline["asset_positions"].shape == (1, 3)
    np.testing.assert_allclose(timeline["asset_positions"][0], expected, atol=1e-6)
    assert timeline["relay_indices"].tolist() == [0]


def test_ground_station_elevation_matches_local_zenith_case() -> None:
    satellite_position = np.asarray([[6_878_140.0, 0.0, 0.0]], dtype=np.float64)
    ground_position = np.asarray([[6_378_140.0, 0.0, 0.0]], dtype=np.float64)

    elevation = _ground_elevation_matrix(satellite_position, ground_position)

    assert elevation.shape == (1, 1)
    assert abs(float(elevation[0, 0]) - 90.0) < 1e-6


def test_relay_alpha_beta_are_zero_on_relay_plus_z_axis() -> None:
    satellite_position = np.asarray([[6_878_140.0, 0.0, 0.0]], dtype=np.float64)
    relay_position = np.asarray([[42_164_140.0, 0.0, 0.0]], dtype=np.float64)

    alpha_deg, beta_deg = _relay_target_angles_matrix(satellite_position, relay_position)

    assert alpha_deg.shape == (1, 1)
    assert beta_deg.shape == (1, 1)
    assert abs(float(alpha_deg[0, 0])) < 1e-6
    assert abs(float(beta_deg[0, 0])) < 1e-6


def test_theta_s_uses_configured_satellite_axis() -> None:
    body_plus_z = np.asarray(
        [
            [0.0, 0.0, -1.0],
            [0.0, 0.0, 1.0],
        ],
        dtype=np.float64,
    )
    sun_vectors = np.asarray(
        [
            [0.0, 0.0, 1.0],
            [0.0, 0.0, 1.0],
        ],
        dtype=np.float64,
    )

    theta_s_deg = _theta_s_deg_from_body_plus_z(body_plus_z, sun_vectors)
    theta_s_plus_z_deg = _theta_s_deg_from_body_plus_z(body_plus_z, sun_vectors, BURN_SUN_AXIS_PLUS_Z)

    np.testing.assert_allclose(theta_s_deg, np.asarray([0.0, 180.0]), atol=1e-12)
    np.testing.assert_allclose(theta_s_plus_z_deg, np.asarray([180.0, 0.0]), atol=1e-12)


def test_compute_launch_windows_keeps_relative_ground_track_fixed(tmp_path: Path) -> None:
    history_path = tmp_path / "full_orbit_history.csv"
    _write_history(history_path)
    strategy = {"maneuver_count": 0, "maneuvers": []}
    config_payload = default_launch_window_config()
    config_payload.update(
        {
            "start_utc": "2026-05-15T00:00:00Z",
            "end_utc": "2026-05-15T03:00:00Z",
            "sample_step_min": 30.0,
            "min_window_duration_min": 30.0,
            "first_orbit_end_min": 180.0,
            "no_shadow_start_min": 200.0,
            "no_shadow_end_min": 200.0,
            "tracking_min_duration_min": 120.0,
            "require_first_orbit_shadow": False,
            "require_no_shadow_period": False,
            "require_remote_tracking": False,
            "require_separation_shadow": False,
            "require_burn_sun_angle": False,
            "require_inclination_limit": True,
            "constraint_rows": [],
        }
    )

    windows, samples = compute_launch_windows(
        orbit_history_csv=history_path,
        maneuver_strategy=strategy,
        config=config_from_payload(config_payload),
        assets=[TrackingAsset(name="near", longitude_deg=110.0, latitude_deg=30.0)],
    )

    assert samples
    assert windows
    assert all(sample["ok"] for sample in samples)
    assert samples[0]["launch_utc"] == "2026-05-15T00:00:00Z"
    assert samples[0]["t0_utc"] == "2026-05-15T00:35:34Z"


def test_compute_launch_windows_reports_progress(tmp_path: Path) -> None:
    history_path = tmp_path / "full_orbit_history.csv"
    _write_history(history_path)
    config_payload = default_launch_window_config()
    config_payload.update(
        {
            "start_utc": "2026-05-15T00:00:00Z",
            "end_utc": "2026-05-15T01:00:00Z",
            "sample_step_min": 30.0,
            "require_first_orbit_shadow": False,
            "require_no_shadow_period": False,
            "require_tracking_arc": False,
            "require_remote_tracking": False,
            "require_separation_shadow": False,
            "require_burn_sun_angle": False,
            "constraint_rows": [],
        }
    )
    updates: list[tuple[int, int]] = []

    compute_launch_windows(
        orbit_history_csv=history_path,
        maneuver_strategy={"maneuver_count": 0, "maneuvers": []},
        config=config_from_payload(config_payload),
        assets=[],
        progress_callback=lambda completed, total: updates.append((completed, total)),
    )

    assert updates == [(1, 3), (2, 3), (3, 3)]


def test_pass_windows_are_not_filtered_by_minimum_window_duration(tmp_path: Path) -> None:
    history_path = tmp_path / "full_orbit_history.csv"
    _write_history(history_path)
    config_payload = default_launch_window_config()
    config_payload.update(
        {
            "start_utc": "2026-05-15T00:00:00Z",
            "end_utc": "2026-05-15T00:01:00Z",
            "sample_step_min": 10.0,
            "min_window_duration_min": 60.0,
            "require_first_orbit_shadow": False,
            "require_no_shadow_period": False,
            "require_tracking_arc": False,
            "require_remote_tracking": False,
            "require_separation_shadow": False,
            "require_burn_sun_angle": False,
            "require_inclination_limit": False,
            "constraint_rows": [],
        }
    )

    windows, samples = compute_launch_windows(
        orbit_history_csv=history_path,
        maneuver_strategy={"maneuver_count": 0, "maneuvers": []},
        config=config_from_payload(config_payload),
        assets=[],
    )

    assert len(samples) == 1
    assert len(windows) == 1
    assert windows[0].duration_min == 10.0


def test_pass_window_records_edge_constraints() -> None:
    config_payload = default_launch_window_config()
    config_payload.update({"sample_step_min": 10.0})
    metric_values = {
        "first_orbit_shadow_min": 0.0,
        "no_shadow_period_shadow_min": 0.0,
        "separation_shadow_min": 0.0,
        "min_burn_sun_margin_deg": 1.0,
        "max_tracking_gap_min": 0.0,
        "inclination_deg": 0.0,
        "longest_shadow_min": 0.0,
    }
    samples = [
        {
            "launch_utc": "2026-05-15T00:00:00Z",
            "ok": False,
            "failure": "front constraint",
            **metric_values,
        },
        {
            "launch_utc": "2026-05-15T00:10:00Z",
            "ok": True,
            "failure": "",
            **metric_values,
            "longest_shadow_min": 11.0,
        },
        {
            "launch_utc": "2026-05-15T00:20:00Z",
            "ok": True,
            "failure": "",
            **metric_values,
            "longest_shadow_min": 22.0,
        },
        {
            "launch_utc": "2026-05-15T00:30:00Z",
            "ok": False,
            "failure": "rear constraint",
            **metric_values,
            "longest_shadow_min": 33.0,
        },
    ]

    windows = launch_window_module._merge_pass_samples(samples, config_from_payload(config_payload))

    assert len(windows) == 1
    assert windows[0].window_start_constraint == "front constraint"
    assert windows[0].window_end_constraint == "rear constraint"
    assert windows[0].window_start_longest_shadow_min == pytest.approx(11.0)
    assert windows[0].window_end_longest_shadow_min == pytest.approx(33.0)


def test_table_constraint_row_can_fail_candidate(tmp_path: Path) -> None:
    history_path = tmp_path / "full_orbit_history.csv"
    _write_history(history_path)
    config_payload = default_launch_window_config()
    config_payload.update(
        {
            "start_utc": "2026-05-15T00:00:00Z",
            "end_utc": "2026-05-15T00:30:00Z",
            "sample_step_min": 30.0,
            "burn_sun_angle_max_deg": -1.0,
            "constraint_rows": [
                {
                    "enabled": True,
                    "name": "impossible panel angle",
                    "start_min": 0.0,
                    "end_min": 180.0,
                    "condition_type": CONSTRAINT_TYPE_THETA_S,
                    "operator": "",
                    "threshold": None,
                    "asset_scope": CONSTRAINT_SCOPE_ALL,
                }
            ],
        }
    )

    _windows, samples = compute_launch_windows(
        orbit_history_csv=history_path,
        maneuver_strategy={"maneuver_count": 0, "maneuvers": []},
        config=config_from_payload(config_payload),
        assets=[],
    )

    assert samples[0]["ok"] is False
    assert samples[0]["failure"] == "impossible panel angle"
    assert samples[0]["constraint_results"] == [
        {
            "name": "impossible panel angle - 太阳角",
            "passed": False,
            "enabled": True,
        }
    ]


def test_table_constraint_row_can_use_parameterized_maneuver_time(tmp_path: Path) -> None:
    history_path = tmp_path / "full_orbit_history.csv"
    _write_history(history_path)
    config_payload = default_launch_window_config()
    config_payload.update(
        {
            "start_utc": "2026-05-15T00:00:00Z",
            "end_utc": "2026-05-15T00:01:00Z",
            "sample_step_min": 30.0,
            "burn_sun_angle_max_deg": -1.0,
            "constraint_rows": [
                {
                    "enabled": True,
                    "name": "parameterized panel angle",
                    "start_min": "T1_start",
                    "end_min": "T1_end",
                    "condition_type": CONSTRAINT_TYPE_THETA_S,
                    "operator": "",
                    "threshold": None,
                    "asset_scope": CONSTRAINT_SCOPE_ALL,
                }
            ],
        }
    )

    _windows, samples = compute_launch_windows(
        orbit_history_csv=history_path,
        maneuver_strategy={
            "maneuver_count": 1,
            "maneuvers": [
                {
                    "maneuver_index": 1,
                    "Tn_start_min": 60.0,
                    "burn_duration_min": 10.0,
                    "delta_deg": 0.0,
                    "dv_direction": 1,
                }
            ],
        },
        config=config_from_payload(config_payload),
        assets=[],
    )

    assert samples[0]["ok"] is False
    assert samples[0]["failure"] == "parameterized panel angle"


def test_table_constraint_row_supports_ground_elevation_rule(tmp_path: Path) -> None:
    history_path = tmp_path / "full_orbit_history.csv"
    _write_history(history_path)
    config_payload = default_launch_window_config()
    config_payload.update(
        {
            "start_utc": "2026-05-15T00:00:00Z",
            "end_utc": "2026-05-15T00:01:00Z",
            "require_first_orbit_shadow": False,
            "require_no_shadow_period": False,
            "require_tracking_arc": False,
            "require_remote_tracking": False,
            "require_separation_shadow": False,
            "require_burn_sun_angle": False,
            "constraint_rows": [
                {
                    "enabled": True,
                    "name": "station elevation",
                    "start_min": 0.0,
                    "end_min": 180.0,
                    "condition_type": CONSTRAINT_TYPE_GROUND_ELEVATION,
                    "operator": ">=",
                    "threshold": 80.0,
                    "asset_scope": CONSTRAINT_SCOPE_GROUND,
                }
            ],
        }
    )

    _windows, samples = compute_launch_windows(
        orbit_history_csv=history_path,
        maneuver_strategy={"maneuver_count": 0, "maneuvers": []},
        config=config_from_payload(config_payload),
        assets=[TrackingAsset(name="near", longitude_deg=110.0, latitude_deg=30.0, asset_type="ground")],
    )

    assert samples[0]["ok"] is True


def test_table_constraint_row_supports_relay_alpha_beta_rule(tmp_path: Path) -> None:
    history_path = tmp_path / "full_orbit_history.csv"
    with history_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
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
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "elapsed_time_s": 0.0,
                "elapsed_time_min": 0.0,
                "phase": "coast",
                "is_event_point": 0,
                "semi_major_axis_m": 7_000_000.0,
                "eccentricity": 0.0,
                "inclination_deg": 5.0,
                "raan_deg": 0.0,
                "argument_of_perigee_deg": 0.0,
                "true_anomaly_deg": 0.0,
                "position_x_m": 6_878_140.0,
                "position_y_m": 0.0,
                "position_z_m": 0.0,
                "velocity_x_m_s": 0.0,
                "velocity_y_m_s": 7500.0,
                "velocity_z_m_s": 0.0,
                "subsatellite_longitude_deg": 0.0,
                "subsatellite_latitude_deg": 0.0,
                "subsatellite_altitude_m": 500_000.0,
                "orbit_height_m": 500_000.0,
                "mass_kg": 1000.0,
            }
        )

    config_payload = default_launch_window_config()
    config_payload.update(
        {
            "start_utc": "2026-05-15T00:00:00Z",
            "end_utc": "2026-05-15T00:01:00Z",
            "require_first_orbit_shadow": False,
            "require_no_shadow_period": False,
            "require_tracking_arc": False,
            "require_remote_tracking": False,
            "require_separation_shadow": False,
            "require_burn_sun_angle": False,
            "constraint_rows": [
                {
                    "enabled": True,
                    "name": "relay alpha beta",
                    "start_min": 0.0,
                    "end_min": 0.0,
                    "condition_type": CONSTRAINT_TYPE_RELAY_ALPHA_ABS,
                    "operator": "<=",
                    "threshold": 1.0,
                    "asset_scope": CONSTRAINT_SCOPE_RELAY,
                },
                {
                    "enabled": True,
                    "name": "relay alpha beta",
                    "start_min": 0.0,
                    "end_min": 0.0,
                    "condition_type": CONSTRAINT_TYPE_RELAY_BETA_ABS,
                    "operator": "<=",
                    "threshold": 1.0,
                    "asset_scope": CONSTRAINT_SCOPE_RELAY,
                }
            ],
        }
    )

    _windows, samples = compute_launch_windows(
        orbit_history_csv=history_path,
        maneuver_strategy={"maneuver_count": 0, "maneuvers": []},
        config=config_from_payload(config_payload),
        assets=[TrackingAsset(name="TL2-2", longitude_deg=0.0, latitude_deg=0.0, altitude_m=35_786_000.0, asset_type="relay")],
    )

    assert samples[0]["ok"] is True


def test_shadow_intervals_use_launch_time_plus_rocket_flight(tmp_path: Path) -> None:
    history_path = tmp_path / "full_orbit_history.csv"
    _write_history(history_path)

    intervals = compute_shadow_intervals_for_launch(
        orbit_history_csv=history_path,
        launch_utc="2026-05-15T07:30:00Z",
        rocket_flight_time_s=2134.4121,
    )

    assert isinstance(intervals, list)


def test_attitude_uses_sun_pointing_outside_burn_and_thrust_pointing_during_burn() -> None:
    rows = [
        {
            "elapsed_time_min": 0.0,
            "phase": "coast",
            "inclination_deg": 5.0,
            "position_x_m": 7_000_000.0,
            "position_y_m": 0.0,
            "position_z_m": 0.0,
            "velocity_x_m_s": 0.0,
            "velocity_y_m_s": 7_500.0,
            "velocity_z_m_s": 0.0,
            "subsatellite_longitude_deg": 0.0,
            "subsatellite_latitude_deg": 0.0,
            "subsatellite_altitude_m": 500_000.0,
        },
        {
            "elapsed_time_min": 10.0,
            "phase": "orbit_control",
            "inclination_deg": 5.0,
            "position_x_m": 7_000_000.0,
            "position_y_m": 0.0,
            "position_z_m": 0.0,
            "velocity_x_m_s": 0.0,
            "velocity_y_m_s": 7_500.0,
            "velocity_z_m_s": 0.0,
            "subsatellite_longitude_deg": 0.0,
            "subsatellite_latitude_deg": 0.0,
            "subsatellite_altitude_m": 500_000.0,
        },
    ]
    timeline = _build_timeline(rows, [])
    t0_utc = parse_utc("2026-05-15T08:05:34Z")
    elapsed = timeline["elapsed_min"]
    sun_vectors = _sun_unit_ecef_for_elapsed(t0_utc, elapsed)

    plus_z = _body_plus_z_ecef_for_attitude(
        t0_utc,
        timeline,
        [ManeuverInterval(start_min=5.0, end_min=15.0, delta_deg=0.0, dv_direction=1)],
        sun_vectors,
    )

    np.testing.assert_allclose(plus_z[0], -sun_vectors[0], atol=1e-12)
    assert abs(float(np.dot(plus_z[1], sun_vectors[1]))) <= 1.0
    assert not np.allclose(plus_z[1], -sun_vectors[1])


def test_reference_thrust_attitude_is_fixed_across_candidate_t0() -> None:
    rows = [
        {
            "elapsed_time_min": 0.0,
            "phase": "coast",
            "inclination_deg": 5.0,
            "position_x_m": 7_000_000.0,
            "position_y_m": 0.0,
            "position_z_m": 0.0,
            "velocity_x_m_s": 0.0,
            "velocity_y_m_s": 7_500.0,
            "velocity_z_m_s": 0.0,
            "subsatellite_longitude_deg": 0.0,
            "subsatellite_latitude_deg": 0.0,
            "subsatellite_altitude_m": 500_000.0,
        },
        {
            "elapsed_time_min": 10.0,
            "phase": "orbit_control",
            "inclination_deg": 5.0,
            "position_x_m": 7_000_000.0,
            "position_y_m": 0.0,
            "position_z_m": 0.0,
            "velocity_x_m_s": 0.0,
            "velocity_y_m_s": 7_500.0,
            "velocity_z_m_s": 0.0,
            "subsatellite_longitude_deg": 0.0,
            "subsatellite_latitude_deg": 0.0,
            "subsatellite_altitude_m": 500_000.0,
        },
    ]
    maneuvers = [ManeuverInterval(start_min=5.0, end_min=15.0, delta_deg=0.0, dv_direction=1)]
    timeline = _build_timeline(
        rows,
        [],
        maneuvers=maneuvers,
        reference_t0_utc=parse_utc("2026-05-15T08:05:34Z"),
    )
    elapsed = timeline["elapsed_min"]
    t0_a = parse_utc("2026-05-16T08:05:34Z")
    t0_b = parse_utc("2026-05-17T08:05:34Z")

    plus_z_a = _body_plus_z_ecef_for_attitude(t0_a, timeline, maneuvers, _sun_unit_ecef_for_elapsed(t0_a, elapsed))
    plus_z_b = _body_plus_z_ecef_for_attitude(t0_b, timeline, maneuvers, _sun_unit_ecef_for_elapsed(t0_b, elapsed))

    assert not np.allclose(plus_z_a[0], plus_z_b[0])
    np.testing.assert_allclose(plus_z_a[1], plus_z_b[1], atol=1e-12)


def test_saved_thrust_attitude_overrides_recomputed_strategy_direction() -> None:
    rows = [
        {
            "elapsed_time_min": 10.0,
            "phase": "orbit_control",
            "inclination_deg": 5.0,
            "position_x_m": 7_000_000.0,
            "position_y_m": 0.0,
            "position_z_m": 0.0,
            "velocity_x_m_s": 0.0,
            "velocity_y_m_s": 7_500.0,
            "velocity_z_m_s": 0.0,
            "subsatellite_longitude_deg": 0.0,
            "subsatellite_latitude_deg": 0.0,
            "subsatellite_altitude_m": 500_000.0,
            "thrust_longitude_deg": 0.0,
            "thrust_latitude_deg": 0.0,
        },
    ]
    maneuvers = [ManeuverInterval(start_min=5.0, end_min=15.0, delta_deg=0.0, dv_direction=1)]
    timeline = _build_timeline(
        rows,
        [],
        maneuvers=maneuvers,
        reference_t0_utc=parse_utc("2026-05-15T08:05:34Z"),
    )
    t0_utc = parse_utc("2026-05-16T08:05:34Z")
    sun_vectors = _sun_unit_ecef_for_elapsed(t0_utc, timeline["elapsed_min"])

    plus_z = _body_plus_z_ecef_for_attitude(t0_utc, timeline, maneuvers, sun_vectors)

    assert bool(timeline["thrust_attitude_mask"][0])
    np.testing.assert_allclose(timeline["thrust_plus_z_ecef"][0], [1.0, 0.0, 0.0], atol=1e-12)
    np.testing.assert_allclose(plus_z[0], [1.0, 0.0, 0.0], atol=1e-12)


def test_ground_theta_st_uses_anti_sun_direction_and_any_station(monkeypatch) -> None:
    monkeypatch.setattr(
        launch_window_module,
        "_sun_unit_ecef_for_elapsed",
        lambda _t0_utc, elapsed_min: np.tile(np.asarray([[1.0, 0.0, 0.0]], dtype=np.float64), (len(elapsed_min), 1)),
    )
    timeline = {
        "elapsed_min": np.asarray([0.0], dtype=np.float64),
        "positions": np.asarray([[7_000_000.0, 0.0, 0.0]], dtype=np.float64),
        "inclinations": np.asarray([0.0], dtype=np.float64),
        "phases": ["orbit_control"],
        "inertial_states": np.asarray([[7_000_000.0, 0.0, 0.0, 0.0, 7_500.0, 0.0]], dtype=np.float64),
        "asset_los_unit": np.asarray([[[0.0, 1.0, 0.0], [-1.0, 0.0, 0.0]]], dtype=np.float64),
        "ground_los_unit": np.asarray([[[0.0, 1.0, 0.0], [-1.0, 0.0, 0.0]]], dtype=np.float64),
        "relay_los_unit": np.empty((1, 0, 3), dtype=np.float64),
        "ground_elevation_deg": np.asarray([[90.0, 90.0]], dtype=np.float64),
        "relay_alpha_deg": np.empty((1, 0), dtype=np.float64),
        "relay_beta_deg": np.empty((1, 0), dtype=np.float64),
        "thrust_plus_z_ecef": np.asarray([[1.0, 0.0, 0.0]], dtype=np.float64),
        "thrust_attitude_mask": np.asarray([True]),
    }
    payload = default_launch_window_config()
    payload.update(
        {
            "ground_station_max_theta_st_deg": 70.0,
            "constraint_rows": [
                {
                    "enabled": True,
                    "name": "ground visible",
                    "start_min": 0.0,
                    "end_min": 0.0,
                    "condition_type": CONSTRAINT_TYPE_GROUND_VISIBLE,
                }
            ],
        }
    )

    ok, _metrics, failure = _evaluate_candidate(
        parse_utc("2026-05-15T00:00:00Z"),
        timeline=timeline,
        maneuvers=[],
        config=config_from_payload(payload),
    )

    assert ok is True
    assert failure == ""
