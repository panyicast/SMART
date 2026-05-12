from __future__ import annotations

from pathlib import Path

import numpy as np

from smart.services.stk_link import (
    StkLinkService,
    parse_relay_longitude_deg,
    sanitize_stk_object_name,
    write_geo_relay_ephemeris,
    write_stk_attitude_dcm,
)
from smart.services.project_workspace import ProjectWorkspace


def test_sanitize_stk_object_name_keeps_connect_safe_names() -> None:
    assert sanitize_stk_object_name("F4 项目-01") == "F4_01"
    assert sanitize_stk_object_name("123 Sat") == "Obj_123_Sat"
    assert sanitize_stk_object_name("  ") == "SMART_Object"


def test_parse_relay_longitude_deg_handles_geo_slot_text() -> None:
    assert parse_relay_longitude_deg("GEO 77E") == 77.0
    assert parse_relay_longitude_deg("171 W") == -171.0
    assert parse_relay_longitude_deg("TL2-5 20.4E") == 20.4
    assert parse_relay_longitude_deg("") is None


def test_write_geo_relay_ephemeris_uses_fixed_geo_slot(tmp_path: Path) -> None:
    output = write_geo_relay_ephemeris(
        tmp_path / "relay.e",
        longitude_deg=77.0,
        scenario_epoch_utc="2026-05-12T00:00:00Z",
        duration_s=600.0,
    )

    text = output.read_text(encoding="utf-8")
    assert "stk.v.11.0" in text
    assert "CoordinateSystem        Fixed" in text
    assert "NumberOfEphemerisPoints 2" in text
    assert "6.00000000000000e+02" in text


def test_write_stk_attitude_dcm_aligns_body_z_with_project_plus_z(tmp_path: Path) -> None:
    output = write_stk_attitude_dcm(
        [(0.0, (0.0, 0.0, 1.0)), (60.0, (0.0, 1.0, 0.0))],
        tmp_path / "attitude.a",
        scenario_epoch_utc="2026-05-12T00:00:00Z",
    )

    lines = output.read_text(encoding="utf-8").splitlines()
    assert "BEGIN Attitude" in lines
    assert "CoordinateAxes          Fixed" in lines
    data_lines = [line for line in lines if line.startswith("0.000") or line.startswith("6.000")]
    first_values = np.asarray([float(value) for value in data_lines[0].split()[1:]], dtype=float)
    first_dcm = first_values.reshape((3, 3))
    np.testing.assert_allclose(first_dcm[2], [0.0, 0.0, 1.0], atol=1e-12)


def test_scenario_epoch_prefers_flight_program_selected_t0(tmp_path: Path) -> None:
    workspace = ProjectWorkspace()
    workspace.create_project("stk-time", parent_dir=tmp_path)
    strategy = workspace.load_maneuver_strategy() or {}
    strategy["t0_epoch"] = "2026-05-01T00:00:00Z"
    workspace.save_maneuver_strategy(strategy)
    program = workspace.load_flight_program_config() or {}
    program["selected_t0_utc"] = "2026-05-15T00:12:00Z"
    workspace.save_flight_program_config(program)

    service = StkLinkService(workspace)

    assert service._scenario_epoch_utc([]) == "2026-05-15T00:12:00.000000Z"


def test_stk_link_assets_use_tracking_arc_config(tmp_path: Path) -> None:
    workspace = ProjectWorkspace()
    workspace.create_project("stk-assets", parent_dir=tmp_path)
    workspace.save_tracking_arc_config(
        {
            "ground_station_presets": [],
            "relay_satellite_presets": [],
            "custom_ground_stations": [
                {
                    "enabled": True,
                    "name": "Ground-A",
                    "asset_type": "ground",
                    "longitude_deg": 110.0,
                    "latitude_deg": 20.0,
                    "altitude_m": 30.0,
                }
            ],
            "custom_relay_satellites": [
                {
                    "enabled": True,
                    "name": "Relay-A",
                    "asset_type": "relay",
                    "longitude_deg": 77.0,
                    "latitude_deg": 0.0,
                    "altitude_m": 35_786_000.0,
                }
            ],
        }
    )

    assets = StkLinkService(workspace)._flight_program_tracking_assets()

    assert [(asset.name, asset.asset_type, asset.longitude_deg) for asset in assets] == [
        ("Ground-A", "ground", 110.0),
        ("Relay-A", "relay", 77.0),
    ]
