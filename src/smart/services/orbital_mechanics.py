from __future__ import annotations

import math

import numpy as np
from numpy.typing import NDArray

from smart.domain.models import (
    EARTH_MU_KM3_S2,
    EARTH_RADIUS_KM,
    HohmannTransferResult,
    OrbitTrajectory,
    OrbitalElements,
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


def _clamp(value: float) -> float:
    return max(-1.0, min(1.0, value))
