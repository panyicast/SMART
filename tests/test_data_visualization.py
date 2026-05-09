from __future__ import annotations

import csv
from pathlib import Path

import numpy as np

from smart.services.data_visualization import build_visualization_series, default_launch_utc_from_configs


def _write_history(path: Path) -> None:
    fields = [
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
        "thrust_alpha_deg",
        "thrust_beta_deg",
        "thrust_longitude_deg",
        "thrust_latitude_deg",
        "subsatellite_longitude_deg",
        "subsatellite_latitude_deg",
        "subsatellite_altitude_m",
        "orbit_height_m",
        "mass_kg",
    ]
    rows = [
        {
            "elapsed_time_s": 0.0,
            "elapsed_time_min": 0.0,
            "phase": "coast",
            "is_event_point": 0,
            "semi_major_axis_m": 7000000.0,
            "eccentricity": 0.01,
            "inclination_deg": 20.0,
            "raan_deg": 30.0,
            "argument_of_perigee_deg": 40.0,
            "true_anomaly_deg": 0.0,
            "position_x_m": 7000000.0,
            "position_y_m": 0.0,
            "position_z_m": 0.0,
            "velocity_x_m_s": 0.0,
            "velocity_y_m_s": 7500.0,
            "velocity_z_m_s": 2500.0,
            "thrust_alpha_deg": np.nan,
            "thrust_beta_deg": np.nan,
            "thrust_longitude_deg": np.nan,
            "thrust_latitude_deg": np.nan,
            "subsatellite_longitude_deg": 0.0,
            "subsatellite_latitude_deg": 0.0,
            "subsatellite_altitude_m": 621864.0,
            "orbit_height_m": 621864.0,
            "mass_kg": 5000.0,
        },
        {
            "elapsed_time_s": 60.0,
            "elapsed_time_min": 1.0,
            "phase": "orbit_control",
            "is_event_point": 0,
            "semi_major_axis_m": 7010000.0,
            "eccentricity": 0.02,
            "inclination_deg": 21.0,
            "raan_deg": 31.0,
            "argument_of_perigee_deg": 41.0,
            "true_anomaly_deg": 90.0,
            "position_x_m": 0.0,
            "position_y_m": 7000000.0,
            "position_z_m": 0.0,
            "velocity_x_m_s": -7500.0,
            "velocity_y_m_s": 0.0,
            "velocity_z_m_s": 2500.0,
            "thrust_alpha_deg": np.nan,
            "thrust_beta_deg": np.nan,
            "thrust_longitude_deg": np.nan,
            "thrust_latitude_deg": np.nan,
            "subsatellite_longitude_deg": 90.0,
            "subsatellite_latitude_deg": 0.0,
            "subsatellite_altitude_m": 621864.0,
            "orbit_height_m": 621864.0,
            "mass_kg": 4990.0,
        },
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def test_default_launch_uses_flight_program_selected_launch() -> None:
    launch = default_launch_utc_from_configs(
        flight_program={"selected_launch_utc": "2026-05-15T07:28:00Z"},
        maneuver_strategy={"t0_epoch": "2026-05-15T08:03:34Z"},
        rocket_flight_time_s=2134.0,
    )
    assert launch == "2026-05-15T07:28:00Z"


def test_build_visualization_series_derives_parameters(tmp_path: Path) -> None:
    history_path = tmp_path / "full_orbit_history.csv"
    _write_history(history_path)
    strategy = {
        "maneuvers": [
            {
                "maneuver_index": 1,
                "Tn_start_min": 1.0,
                "burn_duration_min": 2.0,
                "delta_deg": 0.0,
                "dv_direction": 1,
            }
        ]
    }

    series = build_visualization_series(
        orbit_history_csv=history_path,
        maneuver_strategy=strategy,
        launch_utc="2026-05-15T00:00:00Z",
        rocket_flight_time_s=120.0,
    )

    assert series.t0_utc == "2026-05-15T00:02:00Z"
    assert series.elapsed_min.tolist() == [0.0, 1.0]
    assert series.values["semi_major_axis_km"].tolist() == [7000.0, 7010.0]
    assert series.values["mean_anomaly_deg"][0] == 0.0
    assert series.values["mass_kg"].tolist() == [5000.0, 4990.0]
    assert np.isfinite(series.values["beta_angle_deg"]).all()
    assert len(series.maneuver_intervals) == 1
