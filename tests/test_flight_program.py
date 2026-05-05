from __future__ import annotations

import csv
from pathlib import Path

import pytest

from smart.services.flight_program import (
    ATTITUDE_KIND,
    DEPLOYMENT_KIND,
    build_flight_program_sampling_context,
    MODE_AFM,
    MODE_EPM,
    MODE_SPM,
    MODE_TRANSITION,
    generate_flight_program_draft,
    normalize_flight_event,
    normalize_flight_program_payload,
    sample_flight_program_state,
    validate_flight_program,
)
from smart.services.tracking_arc import TrackingArcOrbitResult, TrackingArcSegment


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
        "thrust_longitude_deg",
        "thrust_latitude_deg",
        "subsatellite_longitude_deg",
        "subsatellite_latitude_deg",
        "subsatellite_altitude_m",
        "orbit_height_m",
        "mass_kg",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for minute in range(0, 121, 10):
            writer.writerow(
                {
                    "elapsed_time_s": minute * 60,
                    "elapsed_time_min": minute,
                    "phase": "orbit_control" if 50 <= minute <= 70 else "coast",
                    "is_event_point": 0,
                    "semi_major_axis_m": 7_000_000.0,
                    "eccentricity": 0.0,
                    "inclination_deg": 0.0,
                    "raan_deg": 0.0,
                    "argument_of_perigee_deg": 0.0,
                    "true_anomaly_deg": 0.0,
                    "position_x_m": 7_000_000.0,
                    "position_y_m": 0.0,
                    "position_z_m": 0.0,
                    "velocity_x_m_s": 0.0,
                    "velocity_y_m_s": 7_500.0,
                    "velocity_z_m_s": 0.0,
                    "thrust_longitude_deg": 90.0 if 50 <= minute <= 70 else "",
                    "thrust_latitude_deg": 0.0 if 50 <= minute <= 70 else "",
                    "subsatellite_longitude_deg": 0.0,
                    "subsatellite_latitude_deg": 0.0,
                    "subsatellite_altitude_m": 621_860.0,
                    "orbit_height_m": 621_860.0,
                    "mass_kg": 1000.0,
                }
            )


def _strategy() -> dict[str, object]:
    return {
        "t0_epoch": "2026-05-15T00:00:00Z",
        "maneuvers": [
            {
                "maneuver_index": 1,
                "Tn_start_min": 50.0,
                "burn_duration_min": 20.0,
                "delta_deg": 0.0,
                "dv_direction": 1,
            }
        ],
    }


def _tracking_result() -> TrackingArcOrbitResult:
    return TrackingArcOrbitResult(
        point_key="leading",
        point_label="窗口前沿轨道",
        launch_utc="2026-05-15T00:00:00Z",
        t0_utc="2026-05-15T00:00:00Z",
        timeline_start_utc="2026-05-15T00:00:00Z",
        timeline_end_utc="2026-05-15T02:00:00Z",
        row_labels=["地面站 测试站", "卫星地影时段"],
        segments=[
            TrackingArcSegment("地面站 测试站", "2026-05-15T00:20:00Z", "2026-05-15T00:40:00Z", "ground", ""),
            TrackingArcSegment("卫星地影时段", "2026-05-15T00:30:00Z", "2026-05-15T00:35:00Z", "shadow", ""),
        ],
        asset_summaries=[],
        shadow_total_min=5.0,
        maneuver_count=1,
    )


def test_normalize_event_keeps_schema_and_instant_end() -> None:
    event = normalize_flight_event(
        {
            "name": "太阳翼展开",
            "kind": DEPLOYMENT_KIND,
            "mode": "SolarArrayDeploy",
            "start_min": 12,
            "end_min": 20,
            "instant": True,
        }
    )

    assert set(event) == {
        "id",
        "name",
        "kind",
        "mode",
        "start_min",
        "end_min",
        "instant",
        "source",
        "locked",
        "notes",
        "properties",
    }
    assert event["end_min"] == event["start_min"]


def test_generate_draft_builds_padded_burn_attitude_and_main_events(tmp_path: Path) -> None:
    history = tmp_path / "full_orbit_history.csv"
    _write_history(history)
    strategy = {
        "t0_epoch": "2026-05-15T00:00:00Z",
        "maneuvers": [
            {
                "maneuver_index": 1,
                "Tn_start_min": 90.0,
                "burn_duration_min": 1.0,
                "delta_deg": 0.0,
                "dv_direction": 1,
            }
        ],
    }

    program = generate_flight_program_draft(
        orbit_history_csv=history,
        maneuver_strategy=strategy,
        tracking_result=_tracking_result(),
        selected_orbit_point="leading",
    )

    attitude_events = [item for item in program["events"] if item["kind"] == ATTITUDE_KIND]
    modes = [item["mode"] for item in attitude_events]
    kinds = [item["kind"] for item in program["events"]]
    afm = next(item for item in attitude_events if item["mode"] == MODE_AFM)

    assert MODE_AFM in modes
    assert MODE_SPM in modes
    assert MODE_TRANSITION in modes
    assert MODE_EPM not in modes
    assert afm["start_min"] == 30.0
    assert afm["end_min"] == 120.0
    assert DEPLOYMENT_KIND in kinds
    assert program["selected_t0_utc"] == "2026-05-15T00:00:00Z"
    assert not [warning for warning in validate_flight_program(program, maneuver_strategy=strategy) if warning.severity == "warning"]


def test_sample_epm_points_plus_z_to_earth(tmp_path: Path) -> None:
    history = tmp_path / "full_orbit_history.csv"
    _write_history(history)
    program = normalize_flight_program_payload(
        {
            "selected_t0_utc": "2026-05-15T00:00:00Z",
            "events": [
                {
                    "name": "测控姿态",
                    "kind": ATTITUDE_KIND,
                    "mode": MODE_EPM,
                    "start_min": 0.0,
                    "end_min": 120.0,
                }
            ],
        }
    )

    sample = sample_flight_program_state(
        orbit_history_csv=history,
        maneuver_strategy=_strategy(),
        payload=program,
        elapsed_min=20.0,
    )

    assert sample.mode == MODE_EPM
    assert sample.plus_z_ecef == pytest.approx((-1.0, 0.0, 0.0))


def test_sample_context_reuses_precomputed_sampling_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    history = tmp_path / "full_orbit_history.csv"
    _write_history(history)
    program = normalize_flight_program_payload(
        {
            "selected_t0_utc": "2026-05-15T00:00:00Z",
            "events": [
                {
                    "name": "点火姿态",
                    "kind": ATTITUDE_KIND,
                    "mode": MODE_AFM,
                    "start_min": 45.0,
                    "end_min": 75.0,
                }
            ],
        }
    )
    context = build_flight_program_sampling_context(
        orbit_history_csv=history,
        maneuver_strategy=_strategy(),
        payload=program,
    )

    monkeypatch.setattr("smart.services.flight_program.load_orbit_history_rows", lambda _path: (_ for _ in ()).throw(AssertionError("unexpected reload")))
    monkeypatch.setattr(
        "smart.services.flight_program._body_plus_z_ecef_for_attitude",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("unexpected AFM recompute")),
    )

    sample = sample_flight_program_state(
        orbit_history_csv=history,
        maneuver_strategy=_strategy(),
        payload=program,
        elapsed_min=50.0,
        context=context,
    )

    assert sample.mode == MODE_AFM
