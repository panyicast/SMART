from __future__ import annotations

import json
from pathlib import Path

import pytest

from smart.domain.models import (
    OrbitalElements,
    SatelliteStructureConfig,
)
from smart.services.project_workspace import ProjectWorkspace
from smart.services.design_maneuver_strategy import (
    ContinuousThrustManeuverParameter,
    ContinuousThrustOptimizationResult,
    default_design_maneuver_strategy_payload,
    plan_design_maneuver_strategy,
)


def test_create_project_creates_expected_structure(tmp_path: Path) -> None:
    workspace = ProjectWorkspace()
    info = workspace.create_project("mission_alpha", parent_dir=tmp_path)

    assert info.name == "mission_alpha"
    assert info.root_dir == (tmp_path / "mission_alpha").resolve()
    assert (info.root_dir / "smart_project.json").exists()
    assert (info.root_dir / "data").is_dir()
    assert (info.root_dir / "data" / "kernels").is_dir()
    assert (info.root_dir / "charts").is_dir()
    assert (info.root_dir / "config").is_dir()
    assert not (info.root_dir / "config" / "orbit_initialization.json").exists()
    assert (info.root_dir / "config" / "satellite_3d_model.json").exists()
    assert not (info.root_dir / "config" / "satellite_status.json").exists()
    assert (info.root_dir / "config" / "maneuver_strategy.json").exists()
    assert (info.root_dir / "config" / "design_maneuver_strategy.json").exists()
    assert (info.root_dir / "config" / "launch_window.json").exists()
    assert (info.root_dir / "config" / "tracking_arc.json").exists()

    payload = json.loads((info.root_dir / "config" / "satellite_3d_model.json").read_text(encoding="utf-8"))
    assert payload["body_size_x_m"] == pytest.approx(2.40)
    assert payload["body_size_y_m"] == pytest.approx(2.05)
    assert payload["body_size_z_m"] == pytest.approx(1.65)
    assert payload["east_antenna_count"] == 2
    assert payload["solar_panels_per_wing"] == 5
    assert payload["model_path"] == ""

    strategy_payload = json.loads((info.root_dir / "config" / "maneuver_strategy.json").read_text(encoding="utf-8"))
    assert strategy_payload["launch_mass_kg"] == pytest.approx(5200.0)
    assert strategy_payload["t0_epoch"].endswith("Z")
    assert strategy_payload["t0_orbit"]["semi_major_axis_m"] == pytest.approx(29478137.0)
    assert strategy_payload["t0_orbit"]["eccentricity"] == pytest.approx(0.7768460924)
    assert "true_anomaly_deg" not in strategy_payload["t0_orbit"]
    assert strategy_payload["maneuver_count"] == 1
    assert len(strategy_payload["maneuvers"]) == 1
    first_step = strategy_payload["maneuvers"][0]
    assert first_step["maneuver_index"] == 1
    assert first_step["Tn_start_min"] == pytest.approx(0.0)
    assert first_step["burn_duration_min"] == pytest.approx(0.0)
    assert first_step["control_fuel_%"] == pytest.approx(0.0)
    assert first_step["settle_duration_s"] == pytest.approx(240.0)
    assert first_step["delta_deg"] == pytest.approx(0.0)
    assert first_step["dv_direction"] == 1
    assert first_step["orbit_control_thrust_n"] == pytest.approx(490.0)
    assert first_step["orbit_control_isp_s"] == pytest.approx(314.1)
    assert first_step["settle_thrust_n"] == pytest.approx(20.0)
    assert first_step["settle_isp_s"] == pytest.approx(290.0)

    design_payload = json.loads(
        (info.root_dir / "config" / "design_maneuver_strategy.json").read_text(encoding="utf-8")
    )
    assert design_payload["planner"]["version"] == "V5.1_hard_constrained_phase_search"
    assert design_payload["initial"]["m0_kg"] == pytest.approx(6515.0)
    assert design_payload["initial"]["a_km"] == pytest.approx(29478.137)
    assert design_payload["initial"]["e"] == pytest.approx(0.77684692)
    assert design_payload["initial"]["i_deg"] == pytest.approx(16.5)
    assert design_payload["initial"]["lon_node_deg"] == pytest.approx(8.53237)
    assert design_payload["initial"]["argp_deg"] == pytest.approx(200.0)
    assert design_payload["initial"]["mean_anomaly_deg"] == pytest.approx(1.85437)
    assert design_payload["target"]["a_km"] == pytest.approx(42164.2)
    assert design_payload["maneuver_count"]["user"] == 0
    assert design_payload["maneuver_count"]["total_dv_est_user_mps"] == pytest.approx(1539.0)

    launch_payload = json.loads((info.root_dir / "config" / "launch_window.json").read_text(encoding="utf-8"))
    assert launch_payload["start_utc"] == "2026-05-15T00:00:00Z"
    assert launch_payload["min_window_duration_min"] == pytest.approx(60.0)

    tracking_payload = json.loads((info.root_dir / "config" / "tracking_arc.json").read_text(encoding="utf-8"))
    assert tracking_payload["start_utc"] == launch_payload["start_utc"]
    assert tracking_payload["ground_station_min_elevation_deg"] == launch_payload["ground_station_min_elevation_deg"]


def test_f4_design_maneuver_config_keeps_reference_initial_orbit() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    payload = json.loads(
        (repo_root / "projects" / "F4" / "config" / "design_maneuver_strategy.json").read_text(encoding="utf-8")
    )

    assert payload["initial"]["a_km"] == pytest.approx(29478.137)
    assert payload["initial"]["e"] == pytest.approx(0.77684692)
    assert payload["initial"]["i_deg"] == pytest.approx(16.5)
    assert payload["initial"]["lon_node_deg"] == pytest.approx(8.53237)
    assert payload["initial"]["argp_deg"] == pytest.approx(200.0)
    assert payload["initial"]["mean_anomaly_deg"] == pytest.approx(1.85437)


def test_save_project_as_copies_current_project_and_closes(tmp_path: Path) -> None:
    workspace = ProjectWorkspace()
    info = workspace.create_project("mission_alpha", parent_dir=tmp_path)
    marker = info.root_dir / "data" / "marker.txt"
    marker.write_text("payload", encoding="utf-8")

    copied = workspace.save_project_as(tmp_path / "mission_beta")

    assert copied.name == "mission_beta"
    assert copied.root_dir == (tmp_path / "mission_beta").resolve()
    assert workspace.current_project == copied
    assert (copied.root_dir / "data" / "marker.txt").read_text(encoding="utf-8") == "payload"

    workspace.close_project()

    assert workspace.current_project is None


def test_create_project_without_parent_dir_uses_projects_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    workspace = ProjectWorkspace()

    info = workspace.create_project("mission_rooted")

    assert info.root_dir == (tmp_path / "projects" / "mission_rooted").resolve()
    assert (tmp_path / "projects").is_dir()
    assert (info.root_dir / "smart_project.json").exists()


def test_save_and_load_orbit_elements(tmp_path: Path) -> None:
    workspace = ProjectWorkspace()
    workspace.create_project("mission_beta", parent_dir=tmp_path)

    elements = OrbitalElements(
        semi_major_axis_km=9000.0,
        eccentricity=0.11,
        inclination_deg=35.0,
        raan_deg=70.0,
        argument_of_periapsis_deg=25.0,
        true_anomaly_deg=88.0,
    )
    workspace.save_orbit_elements(elements)
    restored = workspace.load_orbit_elements()
    assert restored is not None
    assert restored.semi_major_axis_km == pytest.approx(9000.0)
    assert restored.eccentricity == pytest.approx(0.11)
    assert restored.inclination_deg == pytest.approx(35.0)


def test_save_and_load_maneuver_strategy(tmp_path: Path) -> None:
    workspace = ProjectWorkspace()
    workspace.create_project("mission_maneuver_strategy", parent_dir=tmp_path)

    strategy = {
        "launch_mass_kg": 6100.0,
        "to_epoch": "2024-01-01T00:00:00Z",
        "t0_orbit": {
            "semi_major_axis_m": 30500000.0,
            "eccentricity": 0.7,
            "inclination_deg": 12.0,
            "argument_of_perigee_deg": 180.0,
            "raan_deg": 10.0,
            "mean_anomaly_deg": 2.0,
        },
        "maneuver_count": 2,
        "maneuvers": [
            {
                "maneuver_index": 7,
                "Tn_start_min": 60.0,
                "burn_duration_min": 9.1666666667,
                "control_fuel_%": 1.75,
                "settle_duration_s": 260.0,
                "delta_deg": 2.5,
                "dv_direction": -1,
                "orbit_control_thrust_n": 500.0,
                "orbit_control_isp_s": 315.0,
                "settle_thrust_n": 22.0,
                "settle_isp_s": 291.0,
            },
            {
                "maneuver_index": 9,
                "Tn_start_s": 19800.0,
                "burn_duration_s": 420.0,
                "delta_deg": -1.2,
            },
        ],
    }

    file_path = workspace.save_maneuver_strategy(strategy)
    assert file_path == (workspace.root_dir / "config" / "maneuver_strategy.json")

    restored = workspace.load_maneuver_strategy()
    assert restored is not None
    assert restored["launch_mass_kg"] == pytest.approx(6100.0)
    assert restored["t0_epoch"] == "2024-01-01T00:00:00Z"
    assert "to_epoch" not in restored
    assert restored["t0_orbit"]["semi_major_axis_m"] == pytest.approx(30500000.0)
    assert "true_anomaly_deg" not in restored["t0_orbit"]
    assert restored["maneuver_count"] == 2
    assert len(restored["maneuvers"]) == 2

    first = restored["maneuvers"][0]
    assert first["maneuver_index"] == 7
    assert first["Tn_start_min"] == pytest.approx(60.0)
    assert first["burn_duration_min"] == pytest.approx(9.1666666667)
    assert first["control_fuel_%"] == pytest.approx(1.75)
    assert first["settle_duration_s"] == pytest.approx(260.0)
    assert first["delta_deg"] == pytest.approx(2.5)
    assert first["dv_direction"] == -1
    assert first["orbit_control_thrust_n"] == pytest.approx(500.0)
    assert first["orbit_control_isp_s"] == pytest.approx(315.0)
    assert first["settle_thrust_n"] == pytest.approx(22.0)
    assert first["settle_isp_s"] == pytest.approx(291.0)

    second = restored["maneuvers"][1]
    assert second["maneuver_index"] == 9
    assert second["Tn_start_min"] == pytest.approx(330.0)
    assert second["burn_duration_min"] == pytest.approx(7.0)
    assert second["control_fuel_%"] == pytest.approx(0.0)
    assert second["settle_duration_s"] == pytest.approx(240.0)
    assert second["delta_deg"] == pytest.approx(-1.2)
    assert second["dv_direction"] == 1
    assert second["orbit_control_thrust_n"] == pytest.approx(490.0)
    assert second["orbit_control_isp_s"] == pytest.approx(314.1)
    assert second["settle_thrust_n"] == pytest.approx(20.0)
    assert second["settle_isp_s"] == pytest.approx(290.0)


def test_tracking_arc_config_is_saved_independently_from_launch_window(tmp_path: Path) -> None:
    workspace = ProjectWorkspace()
    workspace.create_project("mission_tracking_arc", parent_dir=tmp_path)

    workspace.save_launch_window_config({"ground_station_min_elevation_deg": 5.0})
    file_path = workspace.save_tracking_arc_config({"ground_station_min_elevation_deg": 12.5})

    assert file_path == (workspace.root_dir / "config" / "tracking_arc.json")
    launch_payload = workspace.load_launch_window_config()
    tracking_payload = workspace.load_tracking_arc_config()
    assert launch_payload is not None
    assert tracking_payload is not None
    assert launch_payload["ground_station_min_elevation_deg"] == pytest.approx(5.0)
    assert tracking_payload["ground_station_min_elevation_deg"] == pytest.approx(12.5)


def test_save_and_load_flight_program_reference_results(tmp_path: Path) -> None:
    workspace = ProjectWorkspace()
    workspace.create_project("mission_flight_program_refs", parent_dir=tmp_path)
    payload = {
        "version": 1,
        "selected_t0_utc": "2026-05-15T00:00:00Z",
        "results": [{"point_key": "leading", "segments": []}],
    }

    file_path = workspace.save_flight_program_reference_results(payload)

    assert file_path == workspace.root_dir / "data" / "flight_program_reference_results.json"
    assert workspace.load_flight_program_reference_results() == payload


def test_save_and_load_design_maneuver_results(tmp_path: Path) -> None:
    workspace = ProjectWorkspace()
    workspace.create_project("design-maneuver-results", tmp_path)
    result = plan_design_maneuver_strategy(default_design_maneuver_strategy_payload())

    file_path = workspace.save_design_maneuver_results(result)
    loaded = workspace.load_design_maneuver_results()

    assert file_path == workspace.root_dir / "data" / "design_maneuver_results.json"
    assert loaded is not None
    assert loaded.summary["actual_count"] == result.summary["actual_count"]
    assert len(loaded.burns) == len(result.burns)
    assert loaded.burns[0].delta_v_mps == pytest.approx(result.burns[0].delta_v_mps)


def test_save_and_load_design_continuous_thrust_results(tmp_path: Path) -> None:
    workspace = ProjectWorkspace()
    workspace.create_project("continuous-thrust-results", tmp_path)
    result = ContinuousThrustOptimizationResult(
        parameters=[
            ContinuousThrustManeuverParameter(
                maneuver_index=1,
                flight_revolution=3,
                position_label="远地点",
                initial_burn_start_min=100.0,
                initial_yaw_angle_deg=10.0,
                burn_start_min=101.0,
                settle_end_min=105.0,
                cutoff_min=150.0,
                yaw_angle_deg=12.0,
                ignition_longitude_deg_e=80.0,
                cutoff_longitude_deg_e=120.0,
                delta_v_mps=300.0,
                target_post_a_km=32100.0,
                total_burn_time_min=49.0,
                settle_duration_min=4.0,
                orbit_control_duration_min=45.0,
                propellant_kg=600.0,
                future_apogee_raise_propellant_kg=10.0,
                future_perigee_lower_propellant_kg=20.0,
                trim_propellant_kg=1.0,
                objective_delta_g_kg=631.0,
                objective_formula="m",
                post_a_km=32100.0,
                post_e=0.5,
                post_i_deg=10.0,
                post_mass_kg=5915.0,
                duration_ok=True,
                longitude_ok=True,
                search_evaluations=12,
                optimization_mode="固定链路优化",
            )
        ],
        total_propellant_kg=600.0,
        objective_delta_g_kg=631.0,
        time_step_s=10.0,
        yaw_step_deg=0.05,
        hard_constraint_passed=True,
        failed_constraints=[],
        orbit_history_rows=[{"elapsed_time_min": 101.0}],
    )

    file_path = workspace.save_design_continuous_thrust_results(result)
    loaded = workspace.load_design_continuous_thrust_results()

    assert file_path == workspace.root_dir / "data" / "design_continuous_thrust_results.json"
    assert loaded is not None
    assert loaded.hard_constraint_passed is True
    assert loaded.parameters[0].burn_start_min == pytest.approx(101.0)
    assert loaded.parameters[0].optimization_mode == "固定链路优化"
    assert loaded.orbit_history_rows == []


def test_open_project_requires_metadata_file(tmp_path: Path) -> None:
    workspace = ProjectWorkspace()
    orphan_dir = tmp_path / "orphan"
    orphan_dir.mkdir(parents=True, exist_ok=True)

    with pytest.raises(FileNotFoundError):
        workspace.open_project(orphan_dir)


def test_save_and_load_satellite_3d_model_config(tmp_path: Path) -> None:
    workspace = ProjectWorkspace()
    workspace.create_project("mission_gamma", parent_dir=tmp_path)

    legacy_config = workspace.root_dir / "config" / "satellite_status.json"
    legacy_data = workspace.root_dir / "data" / "satellite_status.json"
    legacy_config.write_text("{}", encoding="utf-8")
    legacy_data.write_text("{}", encoding="utf-8")

    config = SatelliteStructureConfig(
        body_size_x_m=2.8,
        body_size_y_m=2.4,
        body_size_z_m=4.2,
        model_path=r"D:\Program Files\AGI\STK 116\STKData\VO\Models\Space\satellite.dae",
        east_antenna_count=2,
        west_antenna_count=1,
        north_wing_count=1,
        south_wing_count=1,
        solar_panels_per_wing=4,
        solar_panel_span_m=1.6,
        solar_panel_width_m=1.2,
        solar_panel_gap_m=0.1,
    )
    file_path = workspace.save_satellite_3d_model_config(config)
    assert file_path == (workspace.root_dir / "config" / "satellite_3d_model.json")
    assert not legacy_config.exists()
    assert not legacy_data.exists()

    restored = workspace.load_satellite_3d_model_config()
    assert restored is not None
    assert restored.body_size_x_m == pytest.approx(2.8)
    assert restored.body_size_y_m == pytest.approx(2.4)
    assert restored.body_size_z_m == pytest.approx(4.2)
    assert restored.model_path.endswith("satellite.dae")
    assert restored.east_antenna_count == 2
    assert restored.solar_panels_per_wing == 4


def test_load_satellite_3d_model_config_supports_legacy_data_path(tmp_path: Path) -> None:
    workspace = ProjectWorkspace()
    workspace.create_project("mission_delta", parent_dir=tmp_path)

    (workspace.root_dir / "config" / "satellite_3d_model.json").unlink()
    config_path = workspace.root_dir / "config" / "satellite_status.json"
    legacy_path = workspace.root_dir / "data" / "satellite_status.json"
    legacy_path.write_text(
        """
{
  "launch_mass_kg": 6100.0,
  "fuel_load_kg": 2200.0,
  "helium_load_kg": 81.0,
  "orbit_engine_thrust_n": 505.0,
  "orbit_engine_isp_s": 321.0,
  "settle_engine_thrust_n": 29.0,
  "settle_engine_isp_s": 291.0,
  "structure": {
    "body_size_x_m": 2.5,
    "body_size_y_m": 2.2,
    "body_size_z_m": 3.9,
    "dae_model_path": "legacy/sample.dae",
    "east_antenna_count": 1,
    "west_antenna_count": 1,
    "north_wing_count": 1,
    "south_wing_count": 1,
    "solar_panels_per_wing": 3,
    "solar_panel_span_m": 1.4,
    "solar_panel_width_m": 1.0,
    "solar_panel_gap_m": 0.08
  },
  "ttc_antennas": [],
  "relay_antennas": [],
  "ground_assets": [],
  "relay_satellites": []
}
""".strip(),
        encoding="utf-8",
    )

    restored = workspace.load_satellite_3d_model_config()
    assert restored is not None
    assert restored.body_size_x_m == pytest.approx(2.5)
    assert restored.model_path == "legacy/sample.dae"
