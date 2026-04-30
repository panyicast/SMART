from __future__ import annotations

from pathlib import Path

from smart.domain.models import OrbitalElements, RelaySatelliteConfig, SatelliteStatusSettings
from smart.services.orbital_mechanics import sample_orbit
from smart.ui.widgets.cesium_mission_view import build_scene_payload


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
