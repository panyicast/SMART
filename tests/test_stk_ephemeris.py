from __future__ import annotations

import math

from smart.services.orbit_initialization import parse_stk_ephemeris_text
from smart.services.stk_ephemeris import derive_stk_time_bounds, write_stk_ephemeris


def test_write_stk_ephemeris_emits_valid_stk_file(tmp_path) -> None:
    rows = [
        {
            "elapsed_time_s": 0.0,
            "position_x_m": -1.55230948627154e6,
            "position_y_m": -2.65992202008332e6,
            "position_z_m": -6.15011534011162e6,
            "velocity_x_m_s": 8.67526434970980e3,
            "velocity_y_m_s": -5.06281576839082e3,
            "velocity_z_m_s": -1.68509650677606e-12,
            "subsatellite_longitude_deg": -129.76435,
        },
        {
            "elapsed_time_s": 60.0,
            "position_x_m": -1.02879462082021e6,
            "position_y_m": -2.95759020566045e6,
            "position_z_m": -6.13657236880605e6,
            "velocity_x_m_s": 8.76999864818081e3,
            "velocity_y_m_s": -4.85667538663510e3,
            "velocity_z_m_s": 4.50927038498204e2,
            "subsatellite_longitude_deg": -128.90000,
        },
    ]
    output_path = tmp_path / "preview.e"

    metadata = write_stk_ephemeris(
        rows,
        output_path,
        scenario_epoch_utc="2024-01-01T00:00:00Z",
    )

    text = output_path.read_text(encoding="utf-8")
    assert "NumberOfEphemerisPoints 2" in text
    assert "DistanceUnit            Meters" in text
    assert "CoordinateSystem        Fixed" in text
    assert "ScenarioEpoch           1 Jan 2024 00:00:00.000000" in text
    assert metadata.sample_count == 2
    assert metadata.output_path == output_path.resolve()

    samples = [
        [float(token) for token in line.split()]
        for line in text.splitlines()
        if line.strip() and line.strip()[0] in "-0123456789"
    ]
    assert len(samples) == 2
    assert not math.isclose(samples[0][1], rows[0]["position_x_m"], rel_tol=0.0, abs_tol=1.0)
    assert not math.isclose(samples[0][4], rows[0]["velocity_x_m_s"], rel_tol=0.0, abs_tol=1.0)

    settings = parse_stk_ephemeris_text(text, source_path=str(output_path))
    assert settings.mode == "stk_ephemeris"
    assert settings.epoch_utc == "2024-01-01T00:00:00Z"
    assert settings.ephemeris_file_path == str(output_path)


def test_derive_stk_time_bounds_uses_first_and_last_samples() -> None:
    rows = [
        {"elapsed_time_s": 120.0, "position_x_m": 0, "position_y_m": 0, "position_z_m": 0, "velocity_x_m_s": 0, "velocity_y_m_s": 0, "velocity_z_m_s": 0},
        {"elapsed_time_s": 0.0, "position_x_m": 0, "position_y_m": 0, "position_z_m": 0, "velocity_x_m_s": 0, "velocity_y_m_s": 0, "velocity_z_m_s": 0},
        {"elapsed_time_s": 360.0, "position_x_m": 0, "position_y_m": 0, "position_z_m": 0, "velocity_x_m_s": 0, "velocity_y_m_s": 0, "velocity_z_m_s": 0},
    ]

    start_time, stop_time = derive_stk_time_bounds(
        rows,
        scenario_epoch_utc="2024-01-01T00:00:00Z",
    )

    assert start_time == "1 Jan 2024 00:00:00.000000"
    assert stop_time == "1 Jan 2024 00:06:00.000000"
