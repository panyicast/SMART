from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PySide6 import QtWidgets

from smart.services.stk_link import (
    StkLinkService,
    _english_stk_label,
    parse_relay_longitude_deg,
    sanitize_stk_object_name,
    write_geo_relay_ephemeris,
    write_stk_attitude_dcm,
)
from smart.services.project_workspace import ProjectWorkspace
from smart.ui.i18n import I18nManager
from smart.ui.widgets.stk_link_page import StkLinkPage


class _RecordingExecutor:
    def __init__(self, *, has_scenario: bool = True) -> None:
        self.root = SimpleNamespace(CurrentScenario=object() if has_scenario else None)
        self.commands: list[str] = []

    def execute(self, command: str, *, ignore_failure: bool = False) -> list[str]:
        self.commands.append(command)
        return []


def test_sanitize_stk_object_name_keeps_connect_safe_names() -> None:
    assert sanitize_stk_object_name("F4 项目-01") == "F4_01"
    assert sanitize_stk_object_name("123 Sat") == "Obj_123_Sat"
    assert sanitize_stk_object_name("  ") == "SMART_Object"


def test_parse_relay_longitude_deg_handles_geo_slot_text() -> None:
    assert parse_relay_longitude_deg("GEO 77E") == 77.0
    assert parse_relay_longitude_deg("171 W") == -171.0
    assert parse_relay_longitude_deg("TL2-5 20.4E") == 20.4
    assert parse_relay_longitude_deg("") is None


def test_english_stk_label_accepts_path_safe_ascii_without_regex_range_error() -> None:
    assert _english_stk_label("F4 mission A/B-01", fallback="Fallback") == "F4 mission A/B-01"
    assert _english_stk_label("中文任务", fallback="Fallback") == "Fallback"


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


def test_sync_current_scenario_analysis_time_updates_existing_stk_scene(tmp_path: Path) -> None:
    workspace = ProjectWorkspace()
    workspace.create_project("stk-sync-time", parent_dir=tmp_path)
    program = workspace.load_flight_program_config() or {}
    program["selected_t0_utc"] = "2026-05-15T00:12:00Z"
    workspace.save_flight_program_config(program)
    (workspace.data_dir() / "full_orbit_history.csv").write_text(
        "elapsed_time_s\n0\n600\n",
        encoding="utf-8",
    )
    executor = _RecordingExecutor(has_scenario=True)

    assert StkLinkService(workspace, executor=executor).sync_current_scenario_analysis_time() is True

    assert executor.commands == [
        'SetAnalysisTimePeriod * "15 May 2026 00:12:00.000000" "15 May 2026 00:22:00.000000"',
        "SetAnimation * StartAndCurrentTime UseAnalysisStartTime",
        "SetAnimation * EndTime UseAnalysisStopTime",
    ]


def test_sync_current_scenario_analysis_time_skips_when_no_stk_scene(tmp_path: Path) -> None:
    workspace = ProjectWorkspace()
    workspace.create_project("stk-no-scene", parent_dir=tmp_path)
    executor = _RecordingExecutor(has_scenario=False)

    assert StkLinkService(workspace, executor=executor).sync_current_scenario_analysis_time() is False
    assert executor.commands == []


def test_sync_current_scenario_time_sets_stk_animation_time(tmp_path: Path) -> None:
    workspace = ProjectWorkspace()
    workspace.create_project("stk-current-time", parent_dir=tmp_path)
    executor = _RecordingExecutor(has_scenario=True)

    assert StkLinkService(workspace, executor=executor).sync_current_scenario_time("2026-05-15T00:24:30Z") is True

    assert executor.commands == ['SetAnimation * CurrentTime "15 May 2026 00:24:30.000000"']


def test_clear_executor_keeps_established_scenario_state(tmp_path: Path) -> None:
    workspace = ProjectWorkspace()
    workspace.create_project("stk-clear-executor", parent_dir=tmp_path)
    service = StkLinkService(workspace, executor=_RecordingExecutor(has_scenario=True))

    assert service.has_current_scenario() is True
    service.clear_executor()

    assert service.executor is None
    assert service._scenario_established is True


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

    assets = StkLinkService(workspace).tracking_assets_for_sync()

    assert [(asset.name, asset.asset_type, asset.longitude_deg) for asset in assets] == [
        ("Ground-A", "ground", 110.0),
        ("Relay-A", "relay", 77.0),
    ]


def test_stk_link_assets_translate_default_tracking_names_to_english(tmp_path: Path) -> None:
    workspace = ProjectWorkspace()
    workspace.create_project("stk-english-assets", parent_dir=tmp_path)
    workspace.save_tracking_arc_config(
        {
            "ground_station_presets": [
                {
                    "enabled": True,
                    "name": "厦门站",
                    "asset_type": "ground",
                    "longitude_deg": 117.97,
                    "latitude_deg": 24.64,
                    "altitude_m": 0.0,
                }
            ],
            "relay_satellite_presets": [
                {
                    "enabled": True,
                    "name": "TL2-2",
                    "asset_type": "relay",
                    "longitude_deg": 171.0,
                    "latitude_deg": 0.0,
                    "altitude_m": 35_786_000.0,
                }
            ],
            "custom_ground_stations": [],
            "custom_relay_satellites": [],
        }
    )

    assets = StkLinkService(workspace).tracking_assets_for_sync()

    assert [(asset.name, asset.asset_type) for asset in assets] == [
        ("Xiamen Station", "ground"),
        ("TL2-2", "relay"),
    ]


def test_stk_link_page_preview_uses_tracking_arc_assets(tmp_path: Path) -> None:
    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    workspace = ProjectWorkspace()
    workspace.create_project("stk-page-assets", parent_dir=tmp_path)
    workspace.save_tracking_arc_config(
        {
            "ground_station_presets": [
                {
                    "enabled": True,
                    "name": "Xiamen Station",
                    "asset_type": "ground",
                    "longitude_deg": 117.97,
                    "latitude_deg": 24.64,
                    "altitude_m": 0.0,
                }
            ],
            "relay_satellite_presets": [
                {
                    "enabled": True,
                    "name": "TL2-2",
                    "asset_type": "relay",
                    "longitude_deg": 171.0,
                    "latitude_deg": 0.0,
                    "altitude_m": 35_786_000.0,
                }
            ],
            "custom_ground_stations": [],
            "custom_relay_satellites": [],
        }
    )

    page = StkLinkPage(I18nManager(), workspace)

    names = [page._asset_table.item(row, 1).text() for row in range(page._asset_table.rowCount())]
    assert names == ["Xiamen Station", "TL2-2"]


def test_flight_event_annotations_show_attitude_modes_as_large_top_left_pixel_text(tmp_path: Path) -> None:
    workspace = ProjectWorkspace()
    workspace.create_project("stk-event-annotations", parent_dir=tmp_path)
    program = workspace.load_flight_program_config() or {}
    program["events"] = [
        {
            "id": "att-0",
            "name": "太阳指向巡航",
            "kind": "attitude",
            "mode": "SPM",
            "start_min": 0.0,
            "end_min": 10.0,
            "instant": False,
            "source": "auto",
            "locked": False,
            "notes": "",
            "properties": {},
        },
        {
            "id": "att-1",
            "name": "T1 点火前过渡",
            "kind": "attitude",
            "mode": "Transition",
            "start_min": 10.0,
            "end_min": 20.0,
            "instant": False,
            "source": "auto",
            "locked": False,
            "notes": "",
            "properties": {"from": "SPM", "to": "AFM", "maneuver_index": 1},
        },
        {
            "id": "att-epm",
            "name": "地球指向巡航",
            "kind": "attitude",
            "mode": "EPM",
            "start_min": 20.0,
            "end_min": 25.0,
            "instant": False,
            "source": "auto",
            "locked": False,
            "notes": "",
            "properties": {},
        },
        {
            "id": "att-2",
            "name": "T1 点火模式",
            "kind": "attitude",
            "mode": "AFM",
            "start_min": 25.0,
            "end_min": 30.0,
            "instant": False,
            "source": "auto",
            "locked": False,
            "notes": "",
            "properties": {"maneuver_index": 1},
        },
        {
            "id": "dep-1",
            "name": "太阳翼展开",
            "kind": "deployment",
            "mode": "SolarArrayDeploy",
            "start_min": 30.0,
            "end_min": 30.0,
            "instant": True,
            "source": "auto",
            "locked": False,
            "notes": "",
            "properties": {"subsystem": "solar_array"},
        },
    ]
    workspace.save_flight_program_config(program)
    executor = _RecordingExecutor(has_scenario=True)

    count = StkLinkService(workspace, executor=executor)._create_flight_event_annotations(
        [],
        scenario_epoch_utc="2026-05-15T00:00:00Z",
    )

    annotation_commands = [command for command in executor.commands if command.startswith("VO * Annotation Add")]
    assert count == 4
    assert all(command.isascii() for command in annotation_commands)
    assert all("Coord Pixel" in command for command in annotation_commands)
    assert all("Position 24 32 0" in command for command in annotation_commands)
    assert all("HorizPixelOrigin Left" in command for command in annotation_commands)
    assert all("VertPixelOrigin Top" in command for command in annotation_commands)
    assert all("FontStyle Large" in command for command in annotation_commands)
    assert 'String "SPM"' in annotation_commands[0]
    assert 'Interval Add 1 "15 May 2026 00:00:00.000000" "15 May 2026 00:10:00.000000"' in annotation_commands[0]
    assert 'String "TRM"' in annotation_commands[1]
    assert 'Interval Add 1 "15 May 2026 00:10:00.000000" "15 May 2026 00:20:00.000000"' in annotation_commands[1]
    assert 'String "EPM"' in annotation_commands[2]
    assert 'Interval Add 1 "15 May 2026 00:20:00.000000" "15 May 2026 00:25:00.000000"' in annotation_commands[2]
    assert 'String "AFM"' in annotation_commands[3]
    assert 'Interval Add 1 "15 May 2026 00:25:00.000000" "15 May 2026 00:30:00.000000"' in annotation_commands[3]
    assert all("Solar Array Deployment" not in command for command in annotation_commands)
