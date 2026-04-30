from __future__ import annotations

import pytest

from smart.services.orbit_initialization import (
    OrbitInitializationError,
    parse_stk_ephemeris_text,
    parse_tle_text,
)


def test_parse_tle_text_builds_orbit_initialization() -> None:
    settings = parse_tle_text(
        "\n".join(
            [
                "ISS (ZARYA)",
                "1 25544U 98067A   24108.30656250  .00026428  00000+0  47638-3 0  9998",
                "2 25544  51.6390 164.2480 0006064  22.8152  79.1310 15.50316081449833",
            ]
        )
    )

    assert settings.mode == "tle"
    assert settings.epoch_utc == "2024-04-17T07:21:27Z"
    assert settings.elements.inclination_deg == pytest.approx(51.6390)
    assert settings.elements.raan_deg == pytest.approx(164.2480)
    assert settings.elements.eccentricity == pytest.approx(0.0006064)
    assert settings.elements.argument_of_periapsis_deg == pytest.approx(22.8152)
    assert settings.elements.semi_major_axis_km == pytest.approx(6793.94, abs=0.2)
    assert settings.elements.true_anomaly_deg == pytest.approx(79.2, abs=0.2)


def test_parse_stk_ephemeris_time_pos_vel_converts_meters_to_km() -> None:
    settings = parse_stk_ephemeris_text(
        """
stk.v.4.3

BEGIN Ephemeris

NumberOfEphemerisPoints 2
ScenarioEpoch           1 Jun 2002 12:00:00.000000000
InterpolationMethod     Lagrange
InterpolationOrder      5
CentralBody             Earth
CoordinateSystem        J2000

EphemerisTimePosVel

0.00000000000000e+000 -1.55230948627154e+006 -2.65992202008332e+006 -6.15011534011162e+006 8.67526434970980e+003 -5.06281576839082e+003 -1.68509650677606e-012
6.00000000000000e+001 -1.02879462082021e+006 -2.95759020566045e+006 -6.13657236880605e+006 8.76999864818081e+003 -4.85667538663510e+003 4.50927038498204e+002

END Ephemeris
""".strip(),
        source_path=r"D:\sample\leo.e",
    )

    assert settings.mode == "stk_ephemeris"
    assert settings.epoch_utc == "2002-06-01T12:00:00Z"
    assert settings.ephemeris_file_path == r"D:\sample\leo.e"
    assert settings.elements.central_body_name == "Earth"
    assert 20000.0 < settings.elements.semi_major_axis_km < 30000.0
    assert settings.elements.eccentricity == pytest.approx(0.740969, abs=1e-4)
    assert 0.0 <= settings.elements.inclination_deg <= 180.0


def test_parse_stk_ephemeris_rejects_unsupported_frames() -> None:
    with pytest.raises(OrbitInitializationError, match="not supported by SPICE conversion"):
        parse_stk_ephemeris_text(
            """
stk.v.5.0

BEGIN Ephemeris

NumberOfEphemerisPoints 1
ScenarioEpoch           1 Jun 2002 12:00:00.000000000
DistanceUnit            Kilometers
CentralBody             Earth
CoordinateSystem        Custom TopoCentric Facility/MyLaunchSite

EphemerisTimePosVel

0.0000 1000 2000 3000 1 2 3

END Ephemeris
""".strip()
        )
