from __future__ import annotations

import math

import numpy as np
import pytest

from smart.domain.models import EARTH_MU_KM3_S2, EARTH_RADIUS_KM, OrbitalElements
from smart.services.orbital_mechanics import (
    apsis_orbit_metrics_from_altitudes,
    circular_orbit_metrics_from_altitude,
    circular_orbit_metrics_from_period,
    hohmann_transfer_between_circular_orbits,
    lambert_transfer,
    orbital_anomalies_from_angle,
    orbital_elements_from_state_vector,
    plane_change_delta_v,
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


def test_circular_altitude_period_conversion_round_trips() -> None:
    metrics = circular_orbit_metrics_from_altitude(550.0)
    restored = circular_orbit_metrics_from_period(metrics.period_s)

    assert metrics.circular_speed_km_s > 0.0
    assert metrics.escape_speed_km_s > metrics.circular_speed_km_s
    assert restored.altitude_km == pytest.approx(metrics.altitude_km, rel=1e-12)


def test_apsis_metrics_and_plane_change_match_expected_limits() -> None:
    apsis = apsis_orbit_metrics_from_altitudes(400.0, 1200.0)
    plane_change = plane_change_delta_v(7.5, 7.5, 30.0)

    assert apsis.apogee_radius_km > apsis.perigee_radius_km
    assert apsis.semi_major_axis_km == pytest.approx(0.5 * (apsis.perigee_radius_km + apsis.apogee_radius_km))
    assert plane_change.combined_delta_v_km_s == pytest.approx(plane_change.pure_plane_change_delta_v_km_s)
    assert plane_change.pure_plane_change_delta_v_km_s == pytest.approx(2.0 * 7.5 * math.sin(math.radians(15.0)))


def test_anomaly_conversion_round_trips_from_true_to_mean() -> None:
    from_true = orbital_anomalies_from_angle(120.0, 0.4, "true")
    from_mean = orbital_anomalies_from_angle(from_true.mean_anomaly_deg, 0.4, "mean")

    assert from_mean.true_anomaly_deg == pytest.approx(from_true.true_anomaly_deg, abs=1e-9)
    assert from_mean.eccentric_anomaly_deg == pytest.approx(from_true.eccentric_anomaly_deg, abs=1e-9)


def test_lambert_transfer_recovers_quarter_circular_arc_velocity() -> None:
    radius_km = 7000.0
    circular_speed = math.sqrt(EARTH_MU_KM3_S2 / radius_km)
    quarter_period = 0.25 * 2.0 * math.pi * math.sqrt(radius_km**3 / EARTH_MU_KM3_S2)

    result = lambert_transfer(
        [radius_km, 0.0, 0.0],
        [0.0, radius_km, 0.0],
        quarter_period,
    )

    np.testing.assert_allclose(result.departure_velocity_km_s, [0.0, circular_speed, 0.0], atol=1e-8)
    np.testing.assert_allclose(result.arrival_velocity_km_s, [-circular_speed, 0.0, 0.0], atol=1e-8)
    assert result.transfer_angle_deg == pytest.approx(90.0)
