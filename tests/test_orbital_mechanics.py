from __future__ import annotations

import math

import numpy as np
import pytest

from smart.domain.models import EARTH_MU_KM3_S2, EARTH_RADIUS_KM, OrbitalElements
from smart.services.orbital_mechanics import (
    hohmann_transfer_between_circular_orbits,
    orbital_elements_from_state_vector,
    sample_orbit,
    state_from_true_anomaly,
)


def test_sample_orbit_radius_bounds_match_elements() -> None:
    elements = OrbitalElements(
        semi_major_axis_km=12000.0,
        eccentricity=0.2,
        inclination_deg=45.0,
        raan_deg=25.0,
        argument_of_periapsis_deg=15.0,
    )
    trajectory = sample_orbit(elements, sample_count=720)
    assert math.isclose(float(np.min(trajectory.radii_km)), elements.perigee_radius_km, rel_tol=1e-3)
    assert math.isclose(float(np.max(trajectory.radii_km)), elements.apogee_radius_km, rel_tol=1e-3)


def test_hohmann_transfer_is_positive_for_orbit_raise() -> None:
    initial_radius = EARTH_RADIUS_KM + 400.0
    target_radius = EARTH_RADIUS_KM + 1200.0
    result = hohmann_transfer_between_circular_orbits(initial_radius, target_radius, EARTH_MU_KM3_S2)
    assert result.delta_v1_km_s > 0.0
    assert result.delta_v2_km_s > 0.0
    assert result.total_delta_v_km_s > result.delta_v1_km_s


def test_hohmann_transfer_is_negative_then_negative_for_orbit_lower() -> None:
    initial_radius = EARTH_RADIUS_KM + 1500.0
    target_radius = EARTH_RADIUS_KM + 400.0
    result = hohmann_transfer_between_circular_orbits(initial_radius, target_radius, EARTH_MU_KM3_S2)
    assert result.delta_v1_km_s < 0.0
    assert result.delta_v2_km_s < 0.0
    assert result.total_delta_v_km_s > 0.0


def test_state_vector_round_trip_recovers_orbital_elements() -> None:
    elements = OrbitalElements(
        semi_major_axis_km=26600.0,
        eccentricity=0.72,
        inclination_deg=63.4,
        raan_deg=45.0,
        argument_of_periapsis_deg=270.0,
        true_anomaly_deg=120.0,
    )

    position_km, velocity_km_s = state_from_true_anomaly(elements, math.radians(elements.true_anomaly_deg))
    restored = orbital_elements_from_state_vector(position_km, velocity_km_s)

    assert restored.semi_major_axis_km == pytest.approx(elements.semi_major_axis_km, rel=1e-4)
    assert restored.eccentricity == pytest.approx(elements.eccentricity, rel=1e-5)
    assert restored.inclination_deg == pytest.approx(elements.inclination_deg, abs=1e-3)
