from __future__ import annotations

import json
import math

import numpy as np
import pytest

from smart.services.earth_orientation import inertial_raan_deg_from_ascending_node_longitude_deg

from scripts.satellite_dynamics_equation import (
    ManeuverStrategyStep,
    PropagationSegment,
    _command_for_segment,
    build_orbit_history_rows,
    build_maneuver_result_rows,
    build_propagation_segments_from_strategy,
    simulate_with_maneuver_strategy_config,
    solve_alpha_from_delta,
    thrust_direction,
    true_anomaly_from_mean_anomaly,
)


def test_strategy_segments_start_settle_at_tn_then_orbit_control_with_effective_isp() -> None:
    step = ManeuverStrategyStep(
        maneuver_index=1,
        Tn_start_min=10.0,
        burn_duration_min=5.0,
        control_fuel_percent=5.0,
        settle_duration_s=240.0,
        delta_deg=-2.0,
        orbit_control_thrust_n=490.0,
        orbit_control_isp_s=315.0,
        settle_thrust_n=20.0,
        settle_isp_s=290.0,
    )

    segments = build_propagation_segments_from_strategy([step], extra_free_flight_s=0.0)

    assert [segment.phase_name for segment in segments] == ["coast", "settle", "orbit_control"]
    assert segments[1].start_s == pytest.approx(600.0)
    assert segments[1].end_s == pytest.approx(840.0)
    assert segments[1].thrust_n == pytest.approx(20.0)
    assert segments[1].isp_s == pytest.approx(290.0)
    assert segments[1].dv_direction == 1
    assert segments[2].start_s == pytest.approx(840.0)
    assert segments[2].end_s == pytest.approx(900.0)
    assert segments[2].thrust_n == pytest.approx(490.0)
    assert segments[2].isp_s == pytest.approx(315.0 / 1.05)
    assert segments[2].dv_direction == 1


@pytest.mark.parametrize("phase_name", ["settle", "orbit_control"])
def test_deceleration_direction_keeps_delta_sign_and_selects_retrograde_tangent(
    phase_name: str,
) -> None:
    state = np.array(
        [7_000_000.0, 1_000_000.0, 0.0, -1_000.0, 7_500.0, 0.0, 5_000.0],
        dtype=float,
    )
    segment = PropagationSegment(
        phase_name=phase_name,
        start_s=0.0,
        end_s=60.0,
        thrust_n=20.0 if phase_name == "settle" else 490.0,
        isp_s=290.0 if phase_name == "settle" else 315.0,
        delta_deg=1.15,
        maneuver_index=1,
        dv_direction=-1,
    )

    command = _command_for_segment(segment, state)

    acceleration_delta = np.deg2rad(segment.delta_deg)
    acceleration_alpha = solve_alpha_from_delta(state, acceleration_delta, dv_direction=1)
    acceleration_direction = thrust_direction(acceleration_alpha, acceleration_delta)
    deceleration_direction = thrust_direction(command.alpha_rad, command.delta_rad)
    assert command.delta_rad == pytest.approx(acceleration_delta)
    assert deceleration_direction[2] == pytest.approx(acceleration_direction[2])
    assert deceleration_direction[2] > 0.0
    assert float(np.dot(deceleration_direction, state[:3])) == pytest.approx(0.0, abs=1e-8)
    assert float(np.dot(deceleration_direction, state[3:6])) < 0.0


def test_deceleration_direction_is_applied_to_settle_and_orbit_control_segments() -> None:
    step = ManeuverStrategyStep(
        maneuver_index=1,
        Tn_start_min=10.0,
        burn_duration_min=5.0,
        control_fuel_percent=0.0,
        settle_duration_s=240.0,
        delta_deg=0.0,
        orbit_control_thrust_n=490.0,
        orbit_control_isp_s=315.0,
        settle_thrust_n=20.0,
        settle_isp_s=290.0,
        dv_direction=-1,
    )

    segments = build_propagation_segments_from_strategy([step], extra_free_flight_s=0.0)

    assert [segment.phase_name for segment in segments] == ["coast", "settle", "orbit_control"]
    assert segments[1].dv_direction == -1
    assert segments[2].dv_direction == -1


def test_orbit_history_rows_save_thrust_direction_angles() -> None:
    state = np.array([7_000_000.0, 0.0, 0.0, 0.0, 7_500.0, 0.0, 5_000.0], dtype=float)
    segment = PropagationSegment(
        phase_name="orbit_control",
        start_s=0.0,
        end_s=60.0,
        thrust_n=490.0,
        isp_s=315.0,
        delta_deg=0.0,
        maneuver_index=1,
        dv_direction=1,
    )

    rows = build_orbit_history_rows(
        times_s=np.array([0.0, 120.0], dtype=float),
        states=np.vstack([state, state]),
        phases=["orbit_control", "coast"],
        theta_g0_rad=0.0,
        event_times_s=[0.0, 60.0],
        segments=[segment],
    )

    assert rows[0]["thrust_alpha_deg"] == pytest.approx(90.0)
    assert rows[0]["thrust_beta_deg"] == pytest.approx(0.0)
    assert rows[0]["thrust_longitude_deg"] == pytest.approx(90.0)
    assert rows[0]["thrust_latitude_deg"] == pytest.approx(0.0)
    assert math.isnan(rows[1]["thrust_alpha_deg"])
    assert math.isnan(rows[1]["thrust_beta_deg"])
    assert math.isnan(rows[1]["thrust_longitude_deg"])
    assert math.isnan(rows[1]["thrust_latitude_deg"])


def test_local_horizontal_yaw_direction_is_applied_during_ignition() -> None:
    state = np.array([7_000_000.0, 0.0, 0.0, 0.0, 7_500.0, 0.0, 5_000.0], dtype=float)
    segment = PropagationSegment(
        phase_name="orbit_control",
        start_s=0.0,
        end_s=60.0,
        thrust_n=490.0,
        isp_s=315.0,
        delta_deg=0.0,
        maneuver_index=1,
        dv_direction=-1,
        direction_mode="local_horizontal_yaw",
        yaw_angle_deg=0.0,
    )

    command = _command_for_segment(segment, state)

    assert command.direction_eci is not None
    assert command.direction_eci[0] == pytest.approx(0.0)
    assert command.direction_eci[1] == pytest.approx(1.0)
    assert command.direction_eci[2] == pytest.approx(0.0)
    assert command.alpha_rad == pytest.approx(0.0)
    assert command.delta_rad == pytest.approx(0.0)


def test_strategy_segments_reject_total_burn_shorter_than_settle_duration() -> None:
    step = ManeuverStrategyStep(
        maneuver_index=1,
        Tn_start_min=10.0,
        burn_duration_min=1.0,
        control_fuel_percent=0.0,
        settle_duration_s=240.0,
        delta_deg=0.0,
        orbit_control_thrust_n=490.0,
        orbit_control_isp_s=315.0,
        settle_thrust_n=20.0,
        settle_isp_s=290.0,
    )

    with pytest.raises(ValueError, match="burn_duration_min"):
        build_propagation_segments_from_strategy([step], extra_free_flight_s=0.0)


def test_strategy_segments_reject_invalid_control_fuel_factor() -> None:
    step = ManeuverStrategyStep(
        maneuver_index=1,
        Tn_start_min=0.0,
        burn_duration_min=1.0,
        control_fuel_percent=-100.0,
        settle_duration_s=0.0,
        delta_deg=0.0,
        orbit_control_thrust_n=490.0,
        orbit_control_isp_s=315.0,
        settle_thrust_n=20.0,
        settle_isp_s=290.0,
    )

    with pytest.raises(ValueError, match="control_fuel_%"):
        build_propagation_segments_from_strategy([step], extra_free_flight_s=0.0)


def test_maneuver_result_rows_report_end_orbit_and_propellant_use() -> None:
    step = ManeuverStrategyStep(
        maneuver_index=3,
        Tn_start_min=10.0,
        burn_duration_min=5.0,
        control_fuel_percent=0.0,
        settle_duration_s=240.0,
        delta_deg=0.0,
        orbit_control_thrust_n=490.0,
        orbit_control_isp_s=315.0,
        settle_thrust_n=20.0,
        settle_isp_s=290.0,
    )
    rows = [
        {
            "elapsed_time_s": 600.0,
            "elapsed_time_min": 10.0,
            "mass_kg": 1000.0,
            "semi_major_axis_m": 1.0,
            "inclination_deg": 2.0,
            "subsatellite_longitude_deg": 3.0,
            "subsatellite_latitude_deg": 4.0,
        },
        {
            "elapsed_time_s": 900.0,
            "elapsed_time_min": 15.0,
            "mass_kg": 992.5,
            "semi_major_axis_m": 30500000.0,
            "inclination_deg": 12.25,
            "subsatellite_longitude_deg": 111.5,
            "subsatellite_latitude_deg": -8.25,
        },
    ]

    summaries = build_maneuver_result_rows([step], rows)

    assert len(summaries) == 1
    assert summaries[0]["maneuver_index"] == 3
    assert summaries[0]["elapsed_time_min"] == pytest.approx(15.0)
    assert summaries[0]["semi_major_axis_m"] == pytest.approx(30500000.0)
    assert summaries[0]["inclination_deg"] == pytest.approx(12.25)
    assert summaries[0]["subsatellite_longitude_deg"] == pytest.approx(111.5)
    assert summaries[0]["subsatellite_latitude_deg"] == pytest.approx(-8.25)
    assert summaries[0]["propellant_consumed_kg"] == pytest.approx(7.5)


def test_simulation_uses_strategy_top_level_mass_and_t0_orbit(tmp_path) -> None:
    config_path = tmp_path / "maneuver_strategy.json"
    config_path.write_text(
        json.dumps(
            {
                "launch_mass_kg": 6100.0,
                "t0_epoch": "2024-01-01T00:00:00Z",
                "t0_orbit": {
                    "semi_major_axis_m": 30500000.0,
                    "eccentricity": 0.65,
                    "inclination_deg": 12.0,
                    "argument_of_perigee_deg": 180.0,
                    "raan_deg": 10.0,
                    "mean_anomaly_deg": 2.0,
                },
                "maneuver_count": 0,
                "maneuvers": [],
            }
        ),
        encoding="utf-8",
    )

    _csv_path, rows = simulate_with_maneuver_strategy_config(
        strategy_config_path=config_path,
        output_csv_path=tmp_path / "history.csv",
        extra_free_flight_s=0.0,
    )

    assert rows[0]["mass_kg"] == pytest.approx(6100.0)
    assert rows[0]["semi_major_axis_m"] == pytest.approx(30500000.0)
    assert rows[0]["eccentricity"] == pytest.approx(0.65)
    expected_true_anomaly = np.degrees(true_anomaly_from_mean_anomaly(np.radians(2.0), 0.65)) % 360.0
    assert rows[0]["true_anomaly_deg"] == pytest.approx(expected_true_anomaly)


def test_simulation_interprets_raan_as_t0_ascending_node_geographic_longitude(tmp_path) -> None:
    t0_epoch = "2024-01-01T00:00:00Z"
    ascending_node_longitude_deg = 30.0
    config_path = tmp_path / "maneuver_strategy.json"
    config_path.write_text(
        json.dumps(
            {
                "launch_mass_kg": 5000.0,
                "t0_epoch": t0_epoch,
                "t0_orbit": {
                    "semi_major_axis_m": 10000000.0,
                    "eccentricity": 0.1,
                    "inclination_deg": 20.0,
                    "argument_of_perigee_deg": 0.0,
                    "raan_deg": ascending_node_longitude_deg,
                    "mean_anomaly_deg": 0.0,
                },
                "maneuver_count": 0,
                "maneuvers": [],
            }
        ),
        encoding="utf-8",
    )

    _csv_path, rows = simulate_with_maneuver_strategy_config(
        strategy_config_path=config_path,
        output_csv_path=tmp_path / "history.csv",
        extra_free_flight_s=0.0,
    )

    expected_raan_deg = inertial_raan_deg_from_ascending_node_longitude_deg(
        ascending_node_longitude_deg,
        t0_epoch,
    )
    assert rows[0]["raan_deg"] == pytest.approx(expected_raan_deg)
    assert rows[0]["subsatellite_longitude_deg"] == pytest.approx(ascending_node_longitude_deg, abs=1e-6)
    assert rows[0]["subsatellite_latitude_deg"] == pytest.approx(0.0, abs=1e-6)
