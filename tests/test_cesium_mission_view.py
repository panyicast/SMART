from __future__ import annotations

from pathlib import Path
import json

from PySide6 import QtWidgets

from smart.domain.models import OrbitalElements, RelaySatelliteConfig, SatelliteStatusSettings
from smart.services.orbital_mechanics import sample_orbit
from smart.ui.widgets.cesium_mission_view import build_scene_payload
from smart.ui.widgets.cesium_mission_view import CesiumMissionView


def test_build_scene_payload_resolves_glb_model(tmp_path: Path) -> None:
    model_path = tmp_path / "satellite.glb"
    model_path.write_bytes(b"glb")
    settings = SatelliteStatusSettings()
    settings.structure.model_path = str(model_path)

    trajectory = sample_orbit(OrbitalElements())
    payload, note = build_scene_payload(OrbitalElements(), trajectory, settings)

    assert payload["model"] is not None
    assert payload["model"]["url"] == model_path.resolve().as_uri()
    assert note == {"detail_key": "model_loaded", "detail_name": "satellite.glb"}


def test_build_scene_payload_skips_unsupported_model_and_parses_geo_slots(tmp_path: Path) -> None:
    model_path = tmp_path / "satellite.dae"
    model_path.write_text("dae", encoding="utf-8")
    settings = SatelliteStatusSettings(
        relay_satellites=[
            RelaySatelliteConfig(name="Relay-East", orbital_slot_orbit="GEO 77E", band="Ka", note=""),
            RelaySatelliteConfig(name="Relay-West", orbital_slot_orbit="140W", band="Ka", note=""),
            RelaySatelliteConfig(name="Relay-Other", orbital_slot_orbit="IGSO", band="Ka", note=""),
        ]
    )
    settings.structure.model_path = str(model_path)

    trajectory = sample_orbit(OrbitalElements())
    payload, note = build_scene_payload(OrbitalElements(), trajectory, settings)

    assert payload["model"] is None
    assert note == {"detail_key": "model_unsupported", "detail_name": "satellite.dae"}
    assert payload["relaySatellites"] == [
        {
            "name": "Relay-East",
            "band": "Ka",
            "longitudeDeg": 77.0,
            "altitudeM": 35786000.0,
        },
        {
            "name": "Relay-West",
            "band": "Ka",
            "longitudeDeg": -140.0,
            "altitudeM": 35786000.0,
        },
    ]


def test_build_scene_payload_accepts_flight_program_overlays() -> None:
    trajectory = sample_orbit(OrbitalElements())
    overlays = {
        "attitudePlusZ": [0.0, 0.0, 1.0],
        "sunDirection": [1.0, 0.0, 0.0],
        "subsatellitePoint": {"longitudeDeg": 10.0, "latitudeDeg": 20.0},
    }

    payload, _note = build_scene_payload(
        OrbitalElements(),
        trajectory,
        SatelliteStatusSettings(),
        scene_overlays=overlays,
    )

    assert payload["attitudePlusZ"] == [0.0, 0.0, 1.0]
    assert payload["sunDirection"] == [1.0, 0.0, 0.0]
    assert payload["subsatellitePoint"] == {"longitudeDeg": 10.0, "latitudeDeg": 20.0}


def test_cesium_view_defaults_to_earth_centered_camera() -> None:
    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    view = CesiumMissionView()
    view.set_scene_payload({"currentPositionM": [1.0, 0.0, 0.0]})

    assert json.loads(view._scene_payload)["cameraMode"] == "earth"
    assert view._earth_focus_button.isChecked()

    view._set_camera_mode("spacecraft")

    assert json.loads(view._scene_payload)["cameraMode"] == "spacecraft"
    assert view._spacecraft_focus_button.isChecked()
