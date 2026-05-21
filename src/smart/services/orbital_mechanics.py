from __future__ import annotations

from collections.abc import Callable
import math

import numpy as np
from numpy.typing import NDArray
from scipy.optimize import brentq

from smart.domain.models import (
    ApsisOrbitMetrics,
    CircularOrbitMetrics,
    EARTH_MU_KM3_S2,
    EARTH_RADIUS_KM,
    HohmannTransferResult,
    LambertTransferResult,
    OrbitTrajectory,
    OrbitalAnomalySet,
    OrbitalElements,
    PlaneChangeResult,
)
from smart.services import spice_service

_VECTOR_TOLERANCE = 1e-10


def _rotation_matrix_raan_inc_argp(elements: OrbitalElements) -> NDArray[np.float64]:
    raan = math.radians(elements.raan_deg)
    inc = math.radians(elements.inclination_deg)
    argp = math.radians(elements.argument_of_periapsis_deg)

    cos_o = math.cos(raan)
    sin_o = math.sin(raan)
    cos_i = math.cos(inc)
    sin_i = math.sin(inc)
    cos_w = math.cos(argp)
    sin_w = math.sin(argp)

    return np.array(
        [
            [
                cos_o * cos_w - sin_o * sin_w * cos_i,
                -cos_o * sin_w - sin_o * cos_w * cos_i,
                sin_o * sin_i,
            ],
            [
                sin_o * cos_w + cos_o * sin_w * cos_i,
                -sin_o * sin_w + cos_o * cos_w * cos_i,
                -cos_o * sin_i,
            ],
            [
                sin_w * sin_i,
                cos_w * sin_i,
                cos_i,
            ],
        ],
        dtype=np.float64,
    )


def _mean_anomaly_from_true_anomaly(true_anomaly_rad: float | NDArray[np.float64], eccentricity: float) -> NDArray[np.float64]:
    true_anomaly = np.asarray(true_anomaly_rad, dtype=np.float64)
    if eccentricity == 0.0:
        return np.mod(true_anomaly, 2.0 * np.pi)

    eccentric_anomaly = 2.0 * np.arctan2(
        np.sqrt(1.0 - eccentricity) * np.sin(true_anomaly / 2.0),
        np.sqrt(1.0 + eccentricity) * np.cos(true_anomaly / 2.0),
    )
    eccentric_anomaly = np.mod(eccentric_anomaly, 2.0 * np.pi)
    return np.mod(eccentric_anomaly - eccentricity * np.sin(eccentric_anomaly), 2.0 * np.pi)


def _spice_conic_elements(
    elements: OrbitalElements,
    *,
    mean_anomaly_rad: float,
    epoch_et: float,
) -> NDArray[np.float64]:
    return np.array(
        [
            elements.perigee_radius_km,
            elements.eccentricity,
            math.radians(elements.inclination_deg),
            math.radians(elements.raan_deg),
            math.radians(elements.argument_of_periapsis_deg),
            mean_anomaly_rad,
            epoch_et,
            elements.mu_km3_s2,
        ],
        dtype=np.float64,
    )


def _manual_state_from_true_anomaly(
    elements: OrbitalElements,
    true_anomaly_rad: float,
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    elements.validate()
    mu = elements.mu_km3_s2
    a = elements.semi_major_axis_km
    e = elements.eccentricity
    p = a * (1.0 - e**2)

    radius = p / (1.0 + e * math.cos(true_anomaly_rad))
    position_pf = np.array(
        [
            radius * math.cos(true_anomaly_rad),
            radius * math.sin(true_anomaly_rad),
            0.0,
        ],
        dtype=np.float64,
    )
    velocity_pf = math.sqrt(mu / p) * np.array(
        [
            -math.sin(true_anomaly_rad),
            e + math.cos(true_anomaly_rad),
            0.0,
        ],
        dtype=np.float64,
    )

    rotation = _rotation_matrix_raan_inc_argp(elements)
    return rotation @ position_pf, rotation @ velocity_pf


def _manual_sample_orbit(elements: OrbitalElements, sample_count: int = 360) -> OrbitTrajectory:
    elements.validate()
    anomalies = np.linspace(0.0, 2.0 * np.pi, sample_count, endpoint=False, dtype=np.float64)
    mu = elements.mu_km3_s2
    a = elements.semi_major_axis_km
    e = elements.eccentricity
    p = a * (1.0 - e**2)

    radii = p / (1.0 + e * np.cos(anomalies))
    perifocal_positions = np.column_stack(
        [
            radii * np.cos(anomalies),
            radii * np.sin(anomalies),
            np.zeros_like(radii),
        ]
    )
    perifocal_velocities = math.sqrt(mu / p) * np.column_stack(
        [
            -np.sin(anomalies),
            e + np.cos(anomalies),
            np.zeros_like(radii),
        ]
    )

    rotation = _rotation_matrix_raan_inc_argp(elements)
    positions = perifocal_positions @ rotation.T
    velocities = perifocal_velocities @ rotation.T
    mean_anomaly = _mean_anomaly_from_true_anomaly(anomalies, e)
    mean_motion = math.sqrt(mu / a**3)
    elapsed_seconds = mean_anomaly / mean_motion
    current_position, current_velocity = _manual_state_from_true_anomaly(
        elements,
        math.radians(elements.true_anomaly_deg),
    )

    return OrbitTrajectory(
        positions_km=positions,
        velocities_km_s=velocities,
        radii_km=np.linalg.norm(positions, axis=1),
        speeds_km_s=np.linalg.norm(velocities, axis=1),
        elapsed_seconds=elapsed_seconds,
        current_position_km=current_position,
        current_velocity_km_s=current_velocity,
    )


def _manual_orbital_elements_from_state_vector(
    position_km: np.ndarray | list[float] | tuple[float, float, float],
    velocity_km_s: np.ndarray | list[float] | tuple[float, float, float],
    *,
    mu_km3_s2: float = EARTH_MU_KM3_S2,
    central_body_radius_km: float = EARTH_RADIUS_KM,
    central_body_name: str = "Earth",
) -> OrbitalElements:
    r_vec = np.asarray(position_km, dtype=float)
    v_vec = np.asarray(velocity_km_s, dtype=float)
    if r_vec.shape != (3,) or v_vec.shape != (3,):
        raise ValueError("State vector must provide three position and velocity components.")

    radius = float(np.linalg.norm(r_vec))
    speed = float(np.linalg.norm(v_vec))
    if radius <= _VECTOR_TOLERANCE:
        raise ValueError("State vector radius must be greater than zero.")

    h_vec = np.cross(r_vec, v_vec)
    h_norm = float(np.linalg.norm(h_vec))
    if h_norm <= _VECTOR_TOLERANCE:
        raise ValueError("State vector cannot produce a valid orbital plane.")

    k_hat = np.array([0.0, 0.0, 1.0], dtype=float)
    n_vec = np.cross(k_hat, h_vec)
    n_norm = float(np.linalg.norm(n_vec))
    e_vec = np.cross(v_vec, h_vec) / mu_km3_s2 - r_vec / radius
    eccentricity = float(np.linalg.norm(e_vec))

    specific_energy = speed**2 / 2.0 - mu_km3_s2 / radius
    if abs(specific_energy) <= _VECTOR_TOLERANCE:
        raise ValueError("Parabolic trajectories are not supported.")

    semi_major_axis_km = -mu_km3_s2 / (2.0 * specific_energy)
    if semi_major_axis_km <= 0.0:
        raise ValueError("Only bound elliptical trajectories are currently supported.")

    inclination_deg = math.degrees(math.acos(_clamp(h_vec[2] / h_norm)))
    if n_norm > _VECTOR_TOLERANCE:
        raan_deg = math.degrees(math.atan2(float(n_vec[1]), float(n_vec[0]))) % 360.0
    else:
        raan_deg = 0.0

    if eccentricity > _VECTOR_TOLERANCE and n_norm > _VECTOR_TOLERANCE:
        argument_of_periapsis_deg = math.degrees(
            math.acos(_clamp(float(np.dot(n_vec, e_vec)) / (n_norm * eccentricity)))
        )
        if float(e_vec[2]) < 0.0:
            argument_of_periapsis_deg = 360.0 - argument_of_periapsis_deg
    elif eccentricity > _VECTOR_TOLERANCE:
        argument_of_periapsis_deg = math.degrees(math.atan2(float(e_vec[1]), float(e_vec[0]))) % 360.0
    else:
        argument_of_periapsis_deg = 0.0

    if eccentricity > _VECTOR_TOLERANCE:
        true_anomaly_deg = math.degrees(
            math.acos(_clamp(float(np.dot(e_vec, r_vec)) / (eccentricity * radius)))
        )
        if float(np.dot(r_vec, v_vec)) < 0.0:
            true_anomaly_deg = 360.0 - true_anomaly_deg
    elif n_norm > _VECTOR_TOLERANCE:
        true_anomaly_deg = math.degrees(math.acos(_clamp(float(np.dot(n_vec, r_vec)) / (n_norm * radius))))
        if float(r_vec[2]) < 0.0:
            true_anomaly_deg = 360.0 - true_anomaly_deg
    else:
        true_anomaly_deg = math.degrees(math.atan2(float(r_vec[1]), float(r_vec[0]))) % 360.0

    return OrbitalElements(
        semi_major_axis_km=semi_major_axis_km,
        eccentricity=eccentricity,
        inclination_deg=inclination_deg,
        raan_deg=raan_deg,
        argument_of_periapsis_deg=argument_of_periapsis_deg,
        true_anomaly_deg=true_anomaly_deg,
        mu_km3_s2=mu_km3_s2,
        central_body_radius_km=central_body_radius_km,
        central_body_name=central_body_name,
    ).validate()


def state_from_true_anomaly(
    elements: OrbitalElements,
    true_anomaly_rad: float,
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    elements.validate()
    if spice_service.spice is None:
        return _manual_state_from_true_anomaly(elements, true_anomaly_rad)

    mean_anomaly_rad = float(_mean_anomaly_from_true_anomaly(true_anomaly_rad, elements.eccentricity))
    try:
        state = np.asarray(
            spice_service.spice.conics(
                _spice_conic_elements(elements, mean_anomaly_rad=mean_anomaly_rad, epoch_et=0.0),
                0.0,
            ),
            dtype=np.float64,
        )
        return state[:3], state[3:]
    except Exception:
        return _manual_state_from_true_anomaly(elements, true_anomaly_rad)


def sample_orbit(elements: OrbitalElements, sample_count: int = 360) -> OrbitTrajectory:
    elements.validate()
    if spice_service.spice is None:
        return _manual_sample_orbit(elements, sample_count=sample_count)

    anomalies = np.linspace(0.0, 2.0 * np.pi, sample_count, endpoint=False, dtype=np.float64)
    mean_anomaly = _mean_anomaly_from_true_anomaly(anomalies, elements.eccentricity)
    mean_motion = math.sqrt(elements.mu_km3_s2 / elements.semi_major_axis_km**3)
    elapsed_seconds = mean_anomaly / mean_motion
    conic_elements = _spice_conic_elements(elements, mean_anomaly_rad=0.0, epoch_et=0.0)

    try:
        states = np.asarray(
            [spice_service.spice.conics(conic_elements, float(epoch_s)) for epoch_s in elapsed_seconds],
            dtype=np.float64,
        )
        current_position, current_velocity = state_from_true_anomaly(
            elements,
            math.radians(elements.true_anomaly_deg),
        )
        positions = states[:, :3]
        velocities = states[:, 3:]
        return OrbitTrajectory(
            positions_km=positions,
            velocities_km_s=velocities,
            radii_km=np.linalg.norm(positions, axis=1),
            speeds_km_s=np.linalg.norm(velocities, axis=1),
            elapsed_seconds=elapsed_seconds,
            current_position_km=current_position,
            current_velocity_km_s=current_velocity,
        )
    except Exception:
        return _manual_sample_orbit(elements, sample_count=sample_count)


def orbital_elements_from_state_vector(
    position_km: np.ndarray | list[float] | tuple[float, float, float],
    velocity_km_s: np.ndarray | list[float] | tuple[float, float, float],
    *,
    mu_km3_s2: float = EARTH_MU_KM3_S2,
    central_body_radius_km: float = EARTH_RADIUS_KM,
    central_body_name: str = "Earth",
    epoch_et: float = 0.0,
) -> OrbitalElements:
    if spice_service.spice is None:
        return _manual_orbital_elements_from_state_vector(
            position_km,
            velocity_km_s,
            mu_km3_s2=mu_km3_s2,
            central_body_radius_km=central_body_radius_km,
            central_body_name=central_body_name,
        )

    state = np.concatenate(
        [
            np.asarray(position_km, dtype=np.float64),
            np.asarray(velocity_km_s, dtype=np.float64),
        ]
    )
    try:
        extended_elements = np.asarray(spice_service.spice.oscltx(state, float(epoch_et), mu_km3_s2), dtype=np.float64)
        return OrbitalElements(
            semi_major_axis_km=float(extended_elements[9]),
            eccentricity=float(extended_elements[1]),
            inclination_deg=math.degrees(float(extended_elements[2])),
            raan_deg=math.degrees(float(extended_elements[3])) % 360.0,
            argument_of_periapsis_deg=math.degrees(float(extended_elements[4])) % 360.0,
            true_anomaly_deg=math.degrees(float(extended_elements[8])) % 360.0,
            mu_km3_s2=mu_km3_s2,
            central_body_radius_km=central_body_radius_km,
            central_body_name=central_body_name,
        ).validate()
    except Exception:
        return _manual_orbital_elements_from_state_vector(
            position_km,
            velocity_km_s,
            mu_km3_s2=mu_km3_s2,
            central_body_radius_km=central_body_radius_km,
            central_body_name=central_body_name,
        )


def hohmann_transfer_between_circular_orbits(
    initial_radius_km: float,
    target_radius_km: float,
    mu_km3_s2: float,
) -> HohmannTransferResult:
    if initial_radius_km <= 0.0 or target_radius_km <= 0.0:
        raise ValueError("Orbit radii must be positive.")

    transfer_semi_major_axis = 0.5 * (initial_radius_km + target_radius_km)
    initial_circular_velocity = math.sqrt(mu_km3_s2 / initial_radius_km)
    target_circular_velocity = math.sqrt(mu_km3_s2 / target_radius_km)

    transfer_velocity_1 = math.sqrt(
        mu_km3_s2 * (2.0 / initial_radius_km - 1.0 / transfer_semi_major_axis)
    )
    transfer_velocity_2 = math.sqrt(
        mu_km3_s2 * (2.0 / target_radius_km - 1.0 / transfer_semi_major_axis)
    )

    delta_v1 = transfer_velocity_1 - initial_circular_velocity
    delta_v2 = target_circular_velocity - transfer_velocity_2
    transfer_time = np.pi * math.sqrt(transfer_semi_major_axis**3 / mu_km3_s2)

    return HohmannTransferResult(
        initial_radius_km=initial_radius_km,
        target_radius_km=target_radius_km,
        delta_v1_km_s=delta_v1,
        delta_v2_km_s=delta_v2,
        total_delta_v_km_s=abs(delta_v1) + abs(delta_v2),
        transfer_time_s=float(transfer_time),
        transfer_semi_major_axis_km=transfer_semi_major_axis,
    )


def circular_orbit_metrics_from_altitude(
    altitude_km: float,
    *,
    mu_km3_s2: float = EARTH_MU_KM3_S2,
    central_body_radius_km: float = EARTH_RADIUS_KM,
) -> CircularOrbitMetrics:
    if altitude_km < 0.0:
        raise ValueError("Circular-orbit altitude must be non-negative.")
    return _circular_orbit_metrics_from_radius(
        central_body_radius_km + altitude_km,
        mu_km3_s2=mu_km3_s2,
        central_body_radius_km=central_body_radius_km,
    )


def circular_orbit_metrics_from_period(
    period_s: float,
    *,
    mu_km3_s2: float = EARTH_MU_KM3_S2,
    central_body_radius_km: float = EARTH_RADIUS_KM,
) -> CircularOrbitMetrics:
    if period_s <= 0.0:
        raise ValueError("Circular-orbit period must be positive.")
    radius_km = (mu_km3_s2 * (period_s / (2.0 * math.pi)) ** 2) ** (1.0 / 3.0)
    if radius_km < central_body_radius_km:
        raise ValueError("Circular-orbit period places the orbit below the central-body surface.")
    return _circular_orbit_metrics_from_radius(
        radius_km,
        mu_km3_s2=mu_km3_s2,
        central_body_radius_km=central_body_radius_km,
    )


def apsis_orbit_metrics_from_altitudes(
    perigee_altitude_km: float,
    apogee_altitude_km: float,
    *,
    mu_km3_s2: float = EARTH_MU_KM3_S2,
    central_body_radius_km: float = EARTH_RADIUS_KM,
) -> ApsisOrbitMetrics:
    if perigee_altitude_km < 0.0 or apogee_altitude_km < 0.0:
        raise ValueError("Apsis altitudes must be non-negative.")
    if apogee_altitude_km < perigee_altitude_km:
        raise ValueError("Apogee altitude must be greater than or equal to perigee altitude.")

    perigee_radius_km = central_body_radius_km + perigee_altitude_km
    apogee_radius_km = central_body_radius_km + apogee_altitude_km
    semi_major_axis_km = 0.5 * (perigee_radius_km + apogee_radius_km)
    eccentricity = (apogee_radius_km - perigee_radius_km) / (apogee_radius_km + perigee_radius_km)
    period_s = 2.0 * math.pi * math.sqrt(semi_major_axis_km**3 / mu_km3_s2)
    return ApsisOrbitMetrics(
        perigee_altitude_km=perigee_altitude_km,
        apogee_altitude_km=apogee_altitude_km,
        perigee_radius_km=perigee_radius_km,
        apogee_radius_km=apogee_radius_km,
        semi_major_axis_km=semi_major_axis_km,
        eccentricity=eccentricity,
        period_s=period_s,
    )


def plane_change_delta_v(
    initial_speed_km_s: float,
    target_speed_km_s: float,
    angle_deg: float,
) -> PlaneChangeResult:
    if initial_speed_km_s <= 0.0 or target_speed_km_s <= 0.0:
        raise ValueError("Plane-change speeds must be positive.")
    if not 0.0 <= angle_deg <= 180.0:
        raise ValueError("Plane-change angle must stay within 0 to 180 degrees.")

    angle_rad = math.radians(angle_deg)
    pure_delta_v = 2.0 * initial_speed_km_s * math.sin(angle_rad / 2.0)
    combined_delta_v = math.sqrt(
        initial_speed_km_s**2
        + target_speed_km_s**2
        - 2.0 * initial_speed_km_s * target_speed_km_s * math.cos(angle_rad)
    )
    return PlaneChangeResult(
        initial_speed_km_s=initial_speed_km_s,
        target_speed_km_s=target_speed_km_s,
        angle_deg=angle_deg,
        pure_plane_change_delta_v_km_s=pure_delta_v,
        combined_delta_v_km_s=combined_delta_v,
    )


def orbital_anomalies_from_angle(
    angle_deg: float,
    eccentricity: float,
    source: str,
) -> OrbitalAnomalySet:
    if not 0.0 <= eccentricity < 1.0:
        raise ValueError("Elliptic anomaly conversion requires 0 <= e < 1.")
    normalized_source = source.strip().lower()
    angle_rad = math.radians(angle_deg % 360.0)

    if normalized_source == "true":
        true_anomaly = angle_rad
        eccentric_anomaly = _eccentric_anomaly_from_true_anomaly(true_anomaly, eccentricity)
        mean_anomaly = eccentric_anomaly - eccentricity * math.sin(eccentric_anomaly)
    elif normalized_source == "eccentric":
        eccentric_anomaly = angle_rad
        true_anomaly = _true_anomaly_from_eccentric_anomaly(eccentric_anomaly, eccentricity)
        mean_anomaly = eccentric_anomaly - eccentricity * math.sin(eccentric_anomaly)
    elif normalized_source == "mean":
        mean_anomaly = angle_rad
        eccentric_anomaly = _eccentric_anomaly_from_mean_anomaly(mean_anomaly, eccentricity)
        true_anomaly = _true_anomaly_from_eccentric_anomaly(eccentric_anomaly, eccentricity)
    else:
        raise ValueError("Anomaly source must be 'true', 'eccentric', or 'mean'.")

    return OrbitalAnomalySet(
        eccentricity=eccentricity,
        true_anomaly_deg=_normalized_angle_deg(true_anomaly),
        eccentric_anomaly_deg=_normalized_angle_deg(eccentric_anomaly),
        mean_anomaly_deg=_normalized_angle_deg(mean_anomaly),
    )


def lambert_transfer(
    departure_position_km: np.ndarray | list[float] | tuple[float, float, float],
    arrival_position_km: np.ndarray | list[float] | tuple[float, float, float],
    time_of_flight_s: float,
    *,
    mu_km3_s2: float = EARTH_MU_KM3_S2,
    long_path: bool = False,
) -> LambertTransferResult:
    """Solve the zero-revolution two-body Lambert transfer with universal variables."""
    r1_vec = _three_vector(departure_position_km, name="Departure position")
    r2_vec = _three_vector(arrival_position_km, name="Arrival position")
    if time_of_flight_s <= 0.0:
        raise ValueError("Lambert time of flight must be positive.")
    if mu_km3_s2 <= 0.0:
        raise ValueError("Lambert gravitational parameter must be positive.")

    r1 = float(np.linalg.norm(r1_vec))
    r2 = float(np.linalg.norm(r2_vec))
    if r1 <= _VECTOR_TOLERANCE or r2 <= _VECTOR_TOLERANCE:
        raise ValueError("Lambert positions must stay away from the central-body center.")

    cos_transfer_angle = _clamp(float(np.dot(r1_vec, r2_vec)) / (r1 * r2))
    short_angle = math.acos(cos_transfer_angle)
    if short_angle <= 1e-8 or abs(math.pi - short_angle) <= 1e-8:
        raise ValueError("Lambert transfer angle must not be collinear.")
    transfer_angle = 2.0 * math.pi - short_angle if long_path else short_angle
    sin_transfer_angle = math.sin(transfer_angle)
    lambert_a = sin_transfer_angle * math.sqrt(r1 * r2 / (1.0 - math.cos(transfer_angle)))
    if abs(lambert_a) <= _VECTOR_TOLERANCE:
        raise ValueError("Lambert geometry cannot form a transfer arc.")

    def residual(z: float) -> float | None:
        y = _lambert_y(z, r1, r2, lambert_a)
        c = _stumpff_c(z)
        if y is None or c <= _VECTOR_TOLERANCE:
            return None
        return (
            (y / c) ** 1.5 * _stumpff_s(z)
            + lambert_a * math.sqrt(y)
            - math.sqrt(mu_km3_s2) * time_of_flight_s
        )

    bracket = _lambert_zero_revolution_bracket(residual)
    if bracket[0] == bracket[1]:
        z_root = bracket[0]
    else:
        z_root = float(brentq(lambda z: _required_residual(residual(z)), *bracket, xtol=1e-12, rtol=1e-12))
    y_root = _lambert_y(z_root, r1, r2, lambert_a)
    if y_root is None:
        raise ValueError("Lambert solution failed to produce a valid transfer arc.")

    f_lagrange = 1.0 - y_root / r1
    g_lagrange = lambert_a * math.sqrt(y_root / mu_km3_s2)
    gdot_lagrange = 1.0 - y_root / r2
    if abs(g_lagrange) <= _VECTOR_TOLERANCE:
        raise ValueError("Lambert solution produced a singular velocity mapping.")

    departure_velocity = (r2_vec - f_lagrange * r1_vec) / g_lagrange
    arrival_velocity = (gdot_lagrange * r2_vec - r1_vec) / g_lagrange
    return LambertTransferResult(
        departure_velocity_km_s=np.asarray(departure_velocity, dtype=np.float64),
        arrival_velocity_km_s=np.asarray(arrival_velocity, dtype=np.float64),
        time_of_flight_s=time_of_flight_s,
        transfer_angle_deg=math.degrees(transfer_angle),
        path="long" if long_path else "short",
    )


def _circular_orbit_metrics_from_radius(
    radius_km: float,
    *,
    mu_km3_s2: float,
    central_body_radius_km: float,
) -> CircularOrbitMetrics:
    if radius_km < central_body_radius_km:
        raise ValueError("Circular-orbit radius must stay above the central-body surface.")
    period_s = 2.0 * math.pi * math.sqrt(radius_km**3 / mu_km3_s2)
    mean_motion_rad_s = math.sqrt(mu_km3_s2 / radius_km**3)
    return CircularOrbitMetrics(
        altitude_km=radius_km - central_body_radius_km,
        radius_km=radius_km,
        period_s=period_s,
        circular_speed_km_s=math.sqrt(mu_km3_s2 / radius_km),
        escape_speed_km_s=math.sqrt(2.0 * mu_km3_s2 / radius_km),
        mean_motion_rad_s=mean_motion_rad_s,
    )


def _eccentric_anomaly_from_true_anomaly(true_anomaly_rad: float, eccentricity: float) -> float:
    return math.atan2(
        math.sqrt(1.0 - eccentricity**2) * math.sin(true_anomaly_rad),
        eccentricity + math.cos(true_anomaly_rad),
    ) % (2.0 * math.pi)


def _true_anomaly_from_eccentric_anomaly(eccentric_anomaly_rad: float, eccentricity: float) -> float:
    return math.atan2(
        math.sqrt(1.0 - eccentricity**2) * math.sin(eccentric_anomaly_rad),
        math.cos(eccentric_anomaly_rad) - eccentricity,
    ) % (2.0 * math.pi)


def _eccentric_anomaly_from_mean_anomaly(mean_anomaly_rad: float, eccentricity: float) -> float:
    eccentric_anomaly = mean_anomaly_rad if eccentricity < 0.8 else math.pi
    for _ in range(50):
        residual = eccentric_anomaly - eccentricity * math.sin(eccentric_anomaly) - mean_anomaly_rad
        slope = 1.0 - eccentricity * math.cos(eccentric_anomaly)
        step = residual / max(slope, 1e-12)
        eccentric_anomaly -= step
        if abs(step) <= 1e-13:
            return eccentric_anomaly % (2.0 * math.pi)
    raise ValueError("Kepler anomaly conversion did not converge.")


def _normalized_angle_deg(angle_rad: float) -> float:
    return math.degrees(angle_rad) % 360.0


def _three_vector(values: np.ndarray | list[float] | tuple[float, float, float], *, name: str) -> NDArray[np.float64]:
    vector = np.asarray(values, dtype=np.float64)
    if vector.shape != (3,):
        raise ValueError(f"{name} must contain three components.")
    return vector


def _lambert_y(z: float, r1: float, r2: float, lambert_a: float) -> float | None:
    c = _stumpff_c(z)
    if c <= _VECTOR_TOLERANCE:
        return None
    y = r1 + r2 + lambert_a * (z * _stumpff_s(z) - 1.0) / math.sqrt(c)
    if y <= _VECTOR_TOLERANCE:
        return None
    return y


def _stumpff_c(z: float) -> float:
    if z > 1e-8:
        root = math.sqrt(z)
        return (1.0 - math.cos(root)) / z
    if z < -1e-8:
        root = math.sqrt(-z)
        return (math.cosh(root) - 1.0) / (-z)
    return 0.5 - z / 24.0 + z * z / 720.0


def _stumpff_s(z: float) -> float:
    if z > 1e-8:
        root = math.sqrt(z)
        return (root - math.sin(root)) / (root**3)
    if z < -1e-8:
        root = math.sqrt(-z)
        return (math.sinh(root) - root) / (root**3)
    return 1.0 / 6.0 - z / 120.0 + z * z / 5040.0


def _lambert_zero_revolution_bracket(residual_fn: Callable[[float], float | None]) -> tuple[float, float]:
    upper = (2.0 * math.pi) ** 2 - 1e-6
    negative_limit = -(2.0 * math.pi) ** 2
    for _ in range(8):
        points = np.linspace(negative_limit, upper, 2401, dtype=np.float64)
        previous_z: float | None = None
        previous_value: float | None = None
        for raw_z in points:
            z = float(raw_z)
            try:
                value = residual_fn(z)
            except OverflowError:
                continue
            if value is None or not math.isfinite(value):
                continue
            if abs(value) <= 1e-10:
                return z, z
            if previous_value is not None and previous_value * value < 0.0:
                return float(previous_z), z
            previous_z = z
            previous_value = float(value)
        negative_limit *= 4.0
    raise ValueError("Lambert time of flight has no zero-revolution solution for this geometry.")


def _required_residual(value: float | None) -> float:
    if value is None or not math.isfinite(value):
        raise ValueError("Lambert residual became invalid during root solving.")
    return value


def _clamp(value: float) -> float:
    return max(-1.0, min(1.0, value))
