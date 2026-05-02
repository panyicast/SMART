from __future__ import annotations

import csv
from dataclasses import dataclass, replace
import json
import math
from pathlib import Path
import sys
from typing import Any, Callable, Literal

import numpy as np
from numpy.typing import NDArray

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC_ROOT = _REPO_ROOT / "src"
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from smart.services.earth_orientation import (
    format_utc,
    greenwich_angle_at_utc,
    inertial_raan_deg_from_ascending_node_longitude_deg,
    utc_now_iso_z,
)

Vector3 = NDArray[np.float64]
StateVector = NDArray[np.float64]  # [x, y, z, vx, vy, vz, m] in SI


@dataclass(frozen=True)
class DynamicsConstants:
    """Physical constants in SI units."""

    mu: float = 3.986005e14   # m^3 / s^2
    r0: float = 6378.14e3     # m
    j2: float = 1.08263e-3    # dimensionless
    g0: float = 9.8066        # m / s^2
    earth_rotation_rate_rad_s: float = 7.2921150e-5
    earth_flattening: float = 1.0 / 298.257223563


@dataclass(frozen=True)
class ThrustCommand:
    """Thrust command at an instant."""

    thrust_n: float      # N
    alpha_rad: float     # rad
    delta_rad: float     # rad
    isp_s: float         # s


@dataclass(frozen=True)
class OrbitalElements:
    """Classical orbital elements for elliptical orbits in ECI."""

    semi_major_axis_m: float
    eccentricity: float
    inclination_deg: float
    raan_deg: float
    argument_of_perigee_deg: float
    mean_anomaly_deg: float | None = None
    true_anomaly_deg: float | None = None

    def validate(self) -> OrbitalElements:
        if self.semi_major_axis_m <= 0.0:
            raise ValueError("semi_major_axis_m must be positive.")
        if not (0.0 <= self.eccentricity < 1.0):
            raise ValueError("eccentricity must satisfy 0 <= e < 1 for elliptical orbit.")
        if self.mean_anomaly_deg is None and self.true_anomaly_deg is None:
            raise ValueError("At least one anomaly must be provided (mean or true anomaly).")
        return self


@dataclass(frozen=True)
class SubsatellitePoint:
    """Subsatellite point in geodetic coordinates."""

    longitude_deg: float
    latitude_deg: float
    altitude_m: float


@dataclass(frozen=True)
class ManeuverStrategyStep:
    """One maneuver definition loaded from maneuver strategy config."""

    maneuver_index: int
    Tn_start_min: float
    burn_duration_min: float
    control_fuel_percent: float
    settle_duration_s: float
    delta_deg: float
    orbit_control_thrust_n: float
    orbit_control_isp_s: float
    settle_thrust_n: float
    settle_isp_s: float
    dv_direction: int = 1


@dataclass(frozen=True)
class PropagationSegment:
    """One integration segment with fixed thrust mode."""

    phase_name: str
    start_s: float
    end_s: float
    thrust_n: float
    isp_s: float
    delta_deg: float
    maneuver_index: int | None = None
    dv_direction: int = 1


# T0 default data from the figure / configuration template.
DEFAULT_T0_ORBIT_PAYLOAD: dict[str, float] = {
    "semi_major_axis_m": 29_478_137.0,
    "eccentricity": 0.7768460924,
    "inclination_deg": 16.50000,
    "argument_of_perigee_deg": 200.00000,
    # In maneuver_strategy.json this field means the geographic longitude of the
    # ascending node at T0, not inertial RAAN.
    "raan_deg": 8.53237,
    "mean_anomaly_deg": 1.85437,
}
DEFAULT_T0_PERIGEE_ALTITUDE_M = 200_000.0
DEFAULT_T0_APOGEE_ALTITUDE_M = 46_000_000.0
DEFAULT_T0_GEODETIC_LONGITUDE_DEG = -129.76435
DEFAULT_T0_GEODETIC_LATITUDE_DEG = -11.22126
DEFAULT_MANEUVER_STRATEGY_PATH = Path("projects/F4/config/maneuver_strategy.json")
DEFAULT_SATELLITE_STATUS_PATH = Path("projects/F4/config/satellite_status.json")


def default_t0_epoch_utc() -> str:
    return utc_now_iso_z()


def default_t0_orbit(epoch_utc: str | None = None) -> OrbitalElements:
    return _orbital_elements_from_t0_payload(
        DEFAULT_T0_ORBIT_PAYLOAD,
        t0_epoch_utc=epoch_utc or default_t0_epoch_utc(),
    )


def _normalize_angle_rad(angle: float) -> float:
    return angle % (2.0 * math.pi)


def _wrap_to_pi(angle_rad: float) -> float:
    return (angle_rad + math.pi) % (2.0 * math.pi) - math.pi


def _clamp(value: float) -> float:
    return max(-1.0, min(1.0, value))


def _time_in_minutes_from_payload(
    payload: dict[str, Any],
    *,
    minute_keys: tuple[str, ...],
    second_keys: tuple[str, ...],
    default_minutes: float,
) -> float:
    for key in minute_keys:
        if key in payload:
            return float(payload[key])
    for key in second_keys:
        if key in payload:
            return float(payload[key]) / 60.0
    return float(default_minutes)


def _strategy_step_from_payload(payload: dict[str, Any], fallback_index: int) -> ManeuverStrategyStep:
    maneuver_index = int(payload.get("maneuver_index", fallback_index))
    if maneuver_index <= 0:
        maneuver_index = int(fallback_index)
    dv_direction = int(float(payload.get("dv_direction", 1)))
    if dv_direction not in {-1, 1}:
        raise ValueError(f"Maneuver {maneuver_index} dv_direction must be 1 or -1.")
    return ManeuverStrategyStep(
        maneuver_index=maneuver_index,
        Tn_start_min=_time_in_minutes_from_payload(
            payload,
            minute_keys=("Tn_start_min", "Tn_start"),
            second_keys=("Tn_start_s", "t_start_s"),
            default_minutes=0.0,
        ),
        burn_duration_min=_time_in_minutes_from_payload(
            payload,
            minute_keys=("burn_duration_min", "burn_duration"),
            second_keys=("burn_duration_s",),
            default_minutes=0.0,
        ),
        control_fuel_percent=float(
            payload.get("control_fuel_%", payload.get("control_fuel_percent", 0.0))
        ),
        settle_duration_s=float(payload.get("settle_duration_s", 240.0)),
        delta_deg=float(payload.get("delta_deg", payload.get("delta", 0.0))),
        orbit_control_thrust_n=float(payload.get("orbit_control_thrust_n", 490.0)),
        orbit_control_isp_s=float(payload.get("orbit_control_isp_s", 314.1)),
        settle_thrust_n=float(payload.get("settle_thrust_n", 20.0)),
        settle_isp_s=float(payload.get("settle_isp_s", 290.0)),
        dv_direction=dv_direction,
    )


def _orbital_elements_from_payload(payload: object, default: dict[str, float]) -> OrbitalElements:
    payload_map = payload if isinstance(payload, dict) else {}
    has_mean_anomaly = payload_map.get("mean_anomaly_deg") is not None
    return OrbitalElements(
        semi_major_axis_m=float(payload_map.get("semi_major_axis_m", default["semi_major_axis_m"])),
        eccentricity=float(payload_map.get("eccentricity", default["eccentricity"])),
        inclination_deg=float(payload_map.get("inclination_deg", default["inclination_deg"])),
        raan_deg=float(payload_map.get("raan_deg", default["raan_deg"])),
        argument_of_perigee_deg=float(
            payload_map.get("argument_of_perigee_deg", default["argument_of_perigee_deg"])
        ),
        mean_anomaly_deg=(
            float(payload_map["mean_anomaly_deg"])
            if payload_map.get("mean_anomaly_deg") is not None
            else float(default["mean_anomaly_deg"])
        ),
        true_anomaly_deg=(
            None
            if has_mean_anomaly
            else float(payload_map["true_anomaly_deg"])
            if payload_map.get("true_anomaly_deg") is not None
            else None
        ),
    ).validate()


def _t0_epoch_from_strategy_payload(payload: dict[str, Any]) -> str:
    raw_epoch = payload.get("t0_epoch")
    if raw_epoch in (None, ""):
        raw_epoch = payload.get("to_epoch")
    if raw_epoch in (None, ""):
        raw_epoch = default_t0_epoch_utc()
    return format_utc(raw_epoch)


def _orbital_elements_from_t0_payload(payload: object, *, t0_epoch_utc: str) -> OrbitalElements:
    elements = _orbital_elements_from_payload(payload, DEFAULT_T0_ORBIT_PAYLOAD)
    input_payload = payload if isinstance(payload, dict) else {}
    ascending_node_longitude_deg = float(
        input_payload.get("raan_deg", DEFAULT_T0_ORBIT_PAYLOAD["raan_deg"])
    )
    true_anomaly_deg = elements.true_anomaly_deg
    if true_anomaly_deg is None and elements.mean_anomaly_deg is not None:
        true_anomaly_deg = math.degrees(
            true_anomaly_from_mean_anomaly(
                math.radians(elements.mean_anomaly_deg),
                elements.eccentricity,
            )
        ) % 360.0
    return replace(
        elements,
        raan_deg=inertial_raan_deg_from_ascending_node_longitude_deg(
            ascending_node_longitude_deg,
            t0_epoch_utc,
        ),
        true_anomaly_deg=true_anomaly_deg,
    ).validate()


def load_maneuver_strategy_config(config_path: str | Path) -> dict[str, Any]:
    path = Path(config_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"Maneuver strategy config not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("Maneuver strategy config must be a JSON object.")
    return payload


def load_maneuver_strategy_steps(config_path: str | Path) -> list[ManeuverStrategyStep]:
    payload = load_maneuver_strategy_config(config_path)

    maneuvers_raw = payload.get("maneuvers", [])
    if maneuvers_raw is None:
        maneuvers_raw = []
    if not isinstance(maneuvers_raw, list):
        raise ValueError("Maneuver strategy config field 'maneuvers' must be a list.")

    maneuver_count = int(payload.get("maneuver_count", len(maneuvers_raw)))
    if maneuver_count < 0:
        raise ValueError("Maneuver strategy field 'maneuver_count' must be >= 0.")

    parsed: list[ManeuverStrategyStep] = []
    for index in range(maneuver_count):
        step_payload = maneuvers_raw[index] if index < len(maneuvers_raw) else {}
        if not isinstance(step_payload, dict):
            raise ValueError(f"Maneuver step {index + 1} must be a JSON object.")
        parsed.append(_strategy_step_from_payload(step_payload, fallback_index=index + 1))

    return sorted(parsed, key=lambda step: (step.maneuver_index, step.Tn_start_min))


def load_initial_mass_kg(
    status_config_path: str | Path = DEFAULT_SATELLITE_STATUS_PATH,
    default_mass_kg: float = 5200.0,
) -> float:
    path = Path(status_config_path).expanduser().resolve()
    if not path.exists():
        return float(default_mass_kg)
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if isinstance(payload, dict) and "launch_mass_kg" in payload:
            mass_kg = float(payload["launch_mass_kg"])
            if mass_kg > 0.0:
                return mass_kg
    except Exception:
        pass
    return float(default_mass_kg)


def solve_kepler_equation(mean_anomaly_rad: float, eccentricity: float, tol: float = 1e-13, max_iter: int = 50) -> float:
    """Solve E - e*sin(E) = M for elliptical orbit."""

    if not (0.0 <= eccentricity < 1.0):
        raise ValueError("eccentricity must satisfy 0 <= e < 1.")

    mean_anomaly = _normalize_angle_rad(mean_anomaly_rad)
    if eccentricity == 0.0:
        return mean_anomaly

    eccentric_anomaly = mean_anomaly if eccentricity < 0.8 else math.pi
    for _ in range(max_iter):
        f = eccentric_anomaly - eccentricity * math.sin(eccentric_anomaly) - mean_anomaly
        fp = 1.0 - eccentricity * math.cos(eccentric_anomaly)
        step = f / fp
        eccentric_anomaly -= step
        if abs(step) < tol:
            return eccentric_anomaly
    raise RuntimeError("Kepler equation solver did not converge.")


def true_anomaly_from_mean_anomaly(mean_anomaly_rad: float, eccentricity: float) -> float:
    eccentric_anomaly = solve_kepler_equation(mean_anomaly_rad, eccentricity)
    true_anomaly = 2.0 * math.atan2(
        math.sqrt(1.0 + eccentricity) * math.sin(0.5 * eccentric_anomaly),
        math.sqrt(1.0 - eccentricity) * math.cos(0.5 * eccentric_anomaly),
    )
    return _normalize_angle_rad(true_anomaly)


def mean_anomaly_from_true_anomaly(true_anomaly_rad: float, eccentricity: float) -> float:
    eccentric_anomaly = 2.0 * math.atan2(
        math.sqrt(1.0 - eccentricity) * math.sin(0.5 * true_anomaly_rad),
        math.sqrt(1.0 + eccentricity) * math.cos(0.5 * true_anomaly_rad),
    )
    return _normalize_angle_rad(eccentric_anomaly - eccentricity * math.sin(eccentric_anomaly))


def _rotation_matrix_raan_inc_argp(elements: OrbitalElements) -> NDArray[np.float64]:
    raan = math.radians(elements.raan_deg)
    inc = math.radians(elements.inclination_deg)
    argp = math.radians(elements.argument_of_perigee_deg)

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


def orbital_elements_to_state_vector(
    elements: OrbitalElements,
    *,
    mu_m3_s2: float = DynamicsConstants().mu,
    anomaly_source: Literal["true", "mean"] = "true",
) -> tuple[Vector3, Vector3]:
    """
    Convert orbital elements -> (position, velocity) in ECI, SI units.
    anomaly_source:
      - "true": prefer true anomaly, fall back to mean anomaly.
      - "mean": force using mean anomaly.
    """

    elements.validate()
    e = elements.eccentricity
    a = elements.semi_major_axis_m

    if anomaly_source == "mean":
        if elements.mean_anomaly_deg is None:
            raise ValueError("mean_anomaly_deg is required when anomaly_source='mean'.")
        nu = true_anomaly_from_mean_anomaly(math.radians(elements.mean_anomaly_deg), e)
    else:
        if elements.true_anomaly_deg is not None:
            nu = _normalize_angle_rad(math.radians(elements.true_anomaly_deg))
        elif elements.mean_anomaly_deg is not None:
            nu = true_anomaly_from_mean_anomaly(math.radians(elements.mean_anomaly_deg), e)
        else:
            raise ValueError("No anomaly data available.")

    p = a * (1.0 - e * e)
    radius = p / (1.0 + e * math.cos(nu))

    position_pf = np.array([radius * math.cos(nu), radius * math.sin(nu), 0.0], dtype=np.float64)
    velocity_pf = math.sqrt(mu_m3_s2 / p) * np.array(
        [-math.sin(nu), e + math.cos(nu), 0.0],
        dtype=np.float64,
    )

    rotation = _rotation_matrix_raan_inc_argp(elements)
    return rotation @ position_pf, rotation @ velocity_pf


def state_vector_to_orbital_elements(
    position_m: Vector3,
    velocity_m_s: Vector3,
    *,
    mu_m3_s2: float = DynamicsConstants().mu,
) -> OrbitalElements:
    """Convert (position, velocity) -> orbital elements, SI units."""

    r_vec = np.asarray(position_m, dtype=np.float64)
    v_vec = np.asarray(velocity_m_s, dtype=np.float64)
    if r_vec.shape != (3,) or v_vec.shape != (3,):
        raise ValueError("position_m and velocity_m_s must each contain exactly 3 values.")

    tol = 1e-12
    radius = float(np.linalg.norm(r_vec))
    speed = float(np.linalg.norm(v_vec))
    if radius <= tol:
        raise ValueError("Position norm must be positive.")

    h_vec = np.cross(r_vec, v_vec)
    h_norm = float(np.linalg.norm(h_vec))
    if h_norm <= tol:
        raise ValueError("Angular momentum norm is zero.")

    k_hat = np.array([0.0, 0.0, 1.0], dtype=np.float64)
    n_vec = np.cross(k_hat, h_vec)
    n_norm = float(np.linalg.norm(n_vec))

    e_vec = np.cross(v_vec, h_vec) / mu_m3_s2 - r_vec / radius
    eccentricity = float(np.linalg.norm(e_vec))

    specific_energy = 0.5 * speed * speed - mu_m3_s2 / radius
    if abs(specific_energy) <= tol:
        raise ValueError("Parabolic orbits are not supported in this helper.")
    semi_major_axis_m = -mu_m3_s2 / (2.0 * specific_energy)
    if semi_major_axis_m <= 0.0:
        raise ValueError("Only bound elliptical orbits are supported in this helper.")

    inclination_deg = math.degrees(math.acos(_clamp(h_vec[2] / h_norm)))
    raan_deg = math.degrees(math.atan2(float(n_vec[1]), float(n_vec[0]))) % 360.0 if n_norm > tol else 0.0

    if eccentricity > tol and n_norm > tol:
        argp = math.acos(_clamp(float(np.dot(n_vec, e_vec)) / (n_norm * eccentricity)))
        if float(e_vec[2]) < 0.0:
            argp = 2.0 * math.pi - argp
    elif eccentricity > tol:
        argp = math.atan2(float(e_vec[1]), float(e_vec[0])) % (2.0 * math.pi)
    else:
        argp = 0.0
    argument_of_perigee_deg = math.degrees(argp) % 360.0

    if eccentricity > tol:
        nu = math.acos(_clamp(float(np.dot(e_vec, r_vec)) / (eccentricity * radius)))
        if float(np.dot(r_vec, v_vec)) < 0.0:
            nu = 2.0 * math.pi - nu
    elif n_norm > tol:
        nu = math.acos(_clamp(float(np.dot(n_vec, r_vec)) / (n_norm * radius)))
        if float(r_vec[2]) < 0.0:
            nu = 2.0 * math.pi - nu
    else:
        nu = math.atan2(float(r_vec[1]), float(r_vec[0])) % (2.0 * math.pi)

    mean_anomaly_deg = math.degrees(mean_anomaly_from_true_anomaly(nu, eccentricity)) % 360.0
    true_anomaly_deg = math.degrees(nu) % 360.0

    return OrbitalElements(
        semi_major_axis_m=semi_major_axis_m,
        eccentricity=eccentricity,
        inclination_deg=inclination_deg,
        raan_deg=raan_deg,
        argument_of_perigee_deg=argument_of_perigee_deg,
        mean_anomaly_deg=mean_anomaly_deg,
        true_anomaly_deg=true_anomaly_deg,
    )


def perigee_apogee_altitudes(elements: OrbitalElements, earth_radius_m: float) -> tuple[float, float]:
    elements.validate()
    rp = elements.semi_major_axis_m * (1.0 - elements.eccentricity)
    ra = elements.semi_major_axis_m * (1.0 + elements.eccentricity)
    return rp - earth_radius_m, ra - earth_radius_m


def eci_to_ecef(
    position_eci_m: Vector3,
    elapsed_time_s: float,
    *,
    earth_rotation_rate_rad_s: float = DynamicsConstants().earth_rotation_rate_rad_s,
    theta_g0_rad: float = 0.0,
) -> Vector3:
    """
    Convert ECI position to ECEF by Earth rotation around Z-axis.
    theta_g(t) = theta_g0 + omega_earth * t
    r_ecef = R3(-theta_g) * r_eci
    """

    r_eci = np.asarray(position_eci_m, dtype=np.float64)
    if r_eci.shape != (3,):
        raise ValueError("position_eci_m must contain exactly 3 values.")

    theta = theta_g0_rad + earth_rotation_rate_rad_s * float(elapsed_time_s)
    cos_t = math.cos(theta)
    sin_t = math.sin(theta)

    x_eci, y_eci, z_eci = (float(v) for v in r_eci)
    x_ecef = cos_t * x_eci + sin_t * y_eci
    y_ecef = -sin_t * x_eci + cos_t * y_eci
    return np.array([x_ecef, y_ecef, z_eci], dtype=np.float64)


def solve_theta_g0_from_t0_longitude(position_eci_t0_m: Vector3, t0_longitude_deg: float) -> float:
    """
    Solve theta_g0 (rad) from T0 ECI position and known T0 subsatellite longitude.
    Using lon(t0) = atan2(y_eci, x_eci) - theta_g0.
    """

    x_eci, y_eci, _ = (float(v) for v in np.asarray(position_eci_t0_m, dtype=np.float64))
    lon_eci = math.atan2(y_eci, x_eci)
    lon_target = math.radians(t0_longitude_deg)
    return _wrap_to_pi(lon_eci - lon_target)


def ecef_to_geodetic(
    position_ecef_m: Vector3,
    *,
    equatorial_radius_m: float = DynamicsConstants().r0,
    flattening: float = DynamicsConstants().earth_flattening,
    tol: float = 1e-12,
    max_iter: int = 20,
) -> SubsatellitePoint:
    """Convert ECEF Cartesian coordinates to geodetic longitude/latitude/altitude."""

    x, y, z = (float(v) for v in np.asarray(position_ecef_m, dtype=np.float64))
    if equatorial_radius_m <= 0.0:
        raise ValueError("equatorial_radius_m must be positive.")
    if not (0.0 <= flattening < 1.0):
        raise ValueError("flattening must satisfy 0 <= f < 1.")

    a = equatorial_radius_m
    e2 = flattening * (2.0 - flattening)
    lon = math.atan2(y, x)
    p = math.hypot(x, y)

    if p <= tol:
        lat = math.copysign(math.pi / 2.0, z) if abs(z) > tol else 0.0
        b = a * (1.0 - flattening)
        alt = abs(z) - b
        return SubsatellitePoint(
            longitude_deg=math.degrees(lon),
            latitude_deg=math.degrees(lat),
            altitude_m=alt,
        )

    lat = math.atan2(z, p * (1.0 - e2))
    alt = 0.0

    for _ in range(max_iter):
        sin_lat = math.sin(lat)
        cos_lat = math.cos(lat)
        n = a / math.sqrt(1.0 - e2 * sin_lat * sin_lat)
        alt = p / max(cos_lat, tol) - n
        lat_next = math.atan2(z, p * (1.0 - e2 * n / (n + alt)))
        if abs(lat_next - lat) < tol:
            lat = lat_next
            break
        lat = lat_next

    return SubsatellitePoint(
        longitude_deg=((math.degrees(lon) + 180.0) % 360.0) - 180.0,
        latitude_deg=math.degrees(lat),
        altitude_m=alt,
    )


def subsatellite_point_from_eci(
    elapsed_time_s: float,
    position_eci_m: Vector3,
    *,
    theta_g0_rad: float = 0.0,
    constants: DynamicsConstants = DynamicsConstants(),
) -> SubsatellitePoint:
    """
    Compute subsatellite geodetic longitude/latitude/altitude from:
    - elapsed time from T0
    - ECI position at that time
    Earth rotation is included via omega_earth.
    """

    position_ecef = eci_to_ecef(
        position_eci_m,
        elapsed_time_s,
        earth_rotation_rate_rad_s=constants.earth_rotation_rate_rad_s,
        theta_g0_rad=theta_g0_rad,
    )
    return ecef_to_geodetic(
        position_ecef,
        equatorial_radius_m=constants.r0,
        flattening=constants.earth_flattening,
    )


def thrust_direction(alpha_rad: float, delta_rad: float) -> Vector3:
    """u = [cos(alpha)cos(delta), sin(alpha)cos(delta), sin(delta)]."""

    cos_a = math.cos(alpha_rad)
    sin_a = math.sin(alpha_rad)
    cos_d = math.cos(delta_rad)
    sin_d = math.sin(delta_rad)
    return np.array([cos_a * cos_d, sin_a * cos_d, sin_d], dtype=np.float64)


def _alpha_candidates_from_delta(state: StateVector, delta_rad: float, tol: float = 1e-10) -> list[float]:
    """
    Return alpha candidates from tangency constraint:
    x*cos(alpha)*cos(delta) + y*sin(alpha)*cos(delta) + z*sin(delta) = 0.
    """

    x, y, z = (float(v) for v in state[:3])
    cos_d = math.cos(delta_rad)
    sin_d = math.sin(delta_rad)

    if abs(cos_d) <= tol:
        if abs(z * sin_d) > tol:
            raise ValueError("No alpha solution for this delta at current position.")
        return [math.atan2(y, x)]

    rhs = -z * math.tan(delta_rad)
    rho = math.hypot(x, y)
    if rho <= tol:
        if abs(rhs) > tol:
            raise ValueError("No alpha solution near the pole for this delta.")
        return [0.0]

    cos_term = rhs / rho
    if cos_term > 1.0 + tol or cos_term < -1.0 - tol:
        raise ValueError("No real alpha solution because |rhs/rho| > 1.")
    cos_term = max(-1.0, min(1.0, cos_term))

    phase = math.atan2(y, x)
    offset = math.acos(cos_term)
    return [phase + offset, phase - offset]


def solve_alpha_from_delta(
    state: StateVector,
    delta_rad: float,
    tol: float = 1e-10,
    dv_direction: int = 1,
) -> float:
    """
    Solve alpha from tangency constraint.

    For one delta there are generally two tangent thrust directions. dv_direction=1
    chooses the one with the largest velocity projection; dv_direction=-1 chooses
    the one with the smallest velocity projection while preserving delta's sign.
    """

    if dv_direction not in {-1, 1}:
        raise ValueError("dv_direction must be 1 or -1.")

    alpha_candidates = _alpha_candidates_from_delta(state, delta_rad, tol=tol)

    v_vec = np.asarray(state[3:6], dtype=np.float64)
    if float(np.linalg.norm(v_vec)) <= tol:
        return alpha_candidates[0]

    best_alpha = alpha_candidates[0]
    best_score = -float("inf") if dv_direction == 1 else float("inf")
    for alpha in alpha_candidates:
        u = thrust_direction(alpha, delta_rad)
        score = float(np.dot(u, v_vec))
        is_better = score > best_score if dv_direction == 1 else score < best_score
        if is_better:
            best_score = score
            best_alpha = alpha
    return best_alpha


def thrust_tangency_constraint(state: StateVector, delta_rad: float) -> float:
    alpha_rad = solve_alpha_from_delta(state, delta_rad)
    r_vec = np.asarray(state[:3], dtype=np.float64)
    u_vec = thrust_direction(alpha_rad, delta_rad)
    return float(np.dot(r_vec, u_vec))


def satellite_dynamics(
    t: float,
    state: StateVector,
    command: ThrustCommand,
    constants: DynamicsConstants = DynamicsConstants(),
) -> StateVector:
    """Dynamics from the provided formula (Eq. 4.1), SI units."""

    del t

    x, y, z, vx, vy, vz, mass = (float(v) for v in state)
    if mass <= 0.0:
        raise ValueError("Spacecraft mass must stay positive.")
    if command.isp_s <= 0.0:
        raise ValueError("Specific impulse must be positive.")

    r2 = x * x + y * y + z * z
    if r2 <= 0.0:
        raise ValueError("Position norm must be positive.")
    r = math.sqrt(r2)

    mu = constants.mu
    r3 = r2 * r
    r5 = r3 * r2
    zr = z / r
    zr2 = zr * zr

    ax = -mu * x / r3
    ay = -mu * y / r3
    az = -mu * z / r3

    j2_factor = constants.j2 * mu * (constants.r0 ** 2) / r5
    ax += j2_factor * x * (-1.5 + 7.5 * zr2)
    ay += j2_factor * y * (-1.5 + 7.5 * zr2)
    az += j2_factor * z * (-4.5 + 7.5 * zr2)

    u = thrust_direction(command.alpha_rad, command.delta_rad)
    thrust_over_mass = command.thrust_n / mass
    ax += thrust_over_mass * float(u[0])
    ay += thrust_over_mass * float(u[1])
    az += thrust_over_mass * float(u[2])

    mdot = -command.thrust_n / (command.isp_s * constants.g0)
    return np.array([vx, vy, vz, ax, ay, az, mdot], dtype=np.float64)


DynamicsFn = Callable[[float, StateVector], StateVector]
CommandLaw = Callable[[float, StateVector], ThrustCommand]


def rk4_step(dynamics: DynamicsFn, t: float, state: StateVector, dt: float) -> StateVector:
    """Classic fixed-step RK4."""

    k1 = dynamics(t, state)
    k2 = dynamics(t + 0.5 * dt, state + 0.5 * dt * k1)
    k3 = dynamics(t + 0.5 * dt, state + 0.5 * dt * k2)
    k4 = dynamics(t + dt, state + dt * k3)
    return state + (dt / 6.0) * (k1 + 2.0 * k2 + 2.0 * k3 + k4)


def propagate(
    initial_state: StateVector,
    t0: float,
    tf: float,
    dt: float,
    command_law: CommandLaw,
    constants: DynamicsConstants = DynamicsConstants(),
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Propagate [x,y,z,vx,vy,vz,m] with fixed-step RK4."""

    if dt <= 0.0:
        raise ValueError("dt must be positive.")
    if tf <= t0:
        raise ValueError("tf must be greater than t0.")

    steps = int(math.ceil((tf - t0) / dt))
    times = np.empty(steps + 1, dtype=np.float64)
    states = np.empty((steps + 1, 7), dtype=np.float64)

    time_now = float(t0)
    state_now = np.asarray(initial_state, dtype=np.float64).copy()
    times[0] = time_now
    states[0] = state_now

    for i in range(1, steps + 1):
        h = min(dt, tf - time_now)

        def dyn(t_local: float, s_local: StateVector) -> StateVector:
            cmd = command_law(t_local, s_local)
            return satellite_dynamics(t_local, s_local, cmd, constants)

        state_now = rk4_step(dyn, time_now, state_now, h)
        time_now += h
        times[i] = time_now
        states[i] = state_now

    return times, states


ORBIT_HISTORY_COLUMNS = [
    "elapsed_time_s",
    "elapsed_time_min",
    "phase",
    "is_event_point",
    "semi_major_axis_m",
    "eccentricity",
    "inclination_deg",
    "raan_deg",
    "argument_of_perigee_deg",
    "true_anomaly_deg",
    "position_x_m",
    "position_y_m",
    "position_z_m",
    "velocity_x_m_s",
    "velocity_y_m_s",
    "velocity_z_m_s",
    "thrust_alpha_deg",
    "thrust_beta_deg",
    "thrust_longitude_deg",
    "thrust_latitude_deg",
    "subsatellite_longitude_deg",
    "subsatellite_latitude_deg",
    "subsatellite_altitude_m",
    "orbit_height_m",
    "mass_kg",
]


def build_propagation_segments_from_strategy(
    strategy_steps: list[ManeuverStrategyStep],
    *,
    extra_free_flight_s: float = 24.0 * 3600.0,
) -> list[PropagationSegment]:
    """
    Build piecewise propagation segments from maneuver strategy.

    Rule per maneuver:
      - settle thruster: [Tn_start_min, Tn_start_min + settle_duration_s)
      - burn_duration_min is the total burn window including settle and orbit-control phases
      - orbit-control thruster: starts after settle_duration_s and ends at Tn_start_min + burn_duration_min
      - orbit-control Isp: orbit_control_isp_s / (1 + control_fuel_% / 100)
      - dv_direction: 1 selects the tangent solution along velocity, -1 selects the one against velocity.
      - between burns: coast
      - after last burn: append extra_free_flight_s coast.
    """

    if extra_free_flight_s < 0.0:
        raise ValueError("extra_free_flight_s must be >= 0.")

    tol = 1e-9
    thrust_segments: list[PropagationSegment] = []
    for step in strategy_steps:
        if step.settle_duration_s < 0.0:
            raise ValueError(f"Maneuver {step.maneuver_index} settle_duration_s must be >= 0.")
        if step.burn_duration_min < 0.0:
            raise ValueError(f"Maneuver {step.maneuver_index} burn_duration_min must be >= 0.")
        if step.dv_direction not in {-1, 1}:
            raise ValueError(f"Maneuver {step.maneuver_index} dv_direction must be 1 or -1.")

        control_fuel_factor = 1.0 + step.control_fuel_percent / 100.0
        if control_fuel_factor <= 0.0:
            raise ValueError(f"Maneuver {step.maneuver_index} control_fuel_% must be greater than -100.")

        total_burn_start_s = step.Tn_start_min * 60.0
        total_burn_duration_s = step.burn_duration_min * 60.0
        if total_burn_duration_s > tol and total_burn_duration_s + tol < step.settle_duration_s:
            raise ValueError(
                f"Maneuver {step.maneuver_index} burn_duration_min must include settle_duration_s."
            )

        settle_start_s = total_burn_start_s
        settle_end_s = settle_start_s + min(step.settle_duration_s, total_burn_duration_s)
        burn_start_s = settle_end_s
        burn_end_s = total_burn_start_s + total_burn_duration_s

        settle_start_s = max(0.0, settle_start_s)
        settle_end_s = max(0.0, settle_end_s)
        burn_start_s = max(0.0, burn_start_s)
        burn_end_s = max(0.0, burn_end_s)

        if total_burn_duration_s > 0.0 and step.settle_thrust_n > 0.0 and settle_end_s > settle_start_s + tol:
            thrust_segments.append(
                PropagationSegment(
                    phase_name="settle",
                    start_s=settle_start_s,
                    end_s=settle_end_s,
                    thrust_n=step.settle_thrust_n,
                    isp_s=step.settle_isp_s,
                    delta_deg=step.delta_deg,
                    maneuver_index=step.maneuver_index,
                    dv_direction=step.dv_direction,
                )
            )

        if step.orbit_control_thrust_n > 0.0 and burn_end_s > burn_start_s + tol:
            thrust_segments.append(
                PropagationSegment(
                    phase_name="orbit_control",
                    start_s=burn_start_s,
                    end_s=burn_end_s,
                    thrust_n=step.orbit_control_thrust_n,
                    isp_s=step.orbit_control_isp_s / control_fuel_factor,
                    delta_deg=step.delta_deg,
                    maneuver_index=step.maneuver_index,
                    dv_direction=step.dv_direction,
                )
            )

    thrust_segments.sort(key=lambda seg: seg.start_s)
    for i in range(1, len(thrust_segments)):
        previous = thrust_segments[i - 1]
        current = thrust_segments[i]
        if current.start_s < previous.end_s - tol:
            raise ValueError(
                "Maneuver strategy intervals overlap: "
                f"{previous.phase_name}[{previous.start_s:.3f},{previous.end_s:.3f}] and "
                f"{current.phase_name}[{current.start_s:.3f},{current.end_s:.3f}]"
            )

    last_burn_end_s = max((seg.end_s for seg in thrust_segments), default=0.0)
    final_time_s = max(0.0, last_burn_end_s) + extra_free_flight_s

    segments: list[PropagationSegment] = []
    cursor_s = 0.0
    for thrust_segment in thrust_segments:
        if thrust_segment.start_s > cursor_s + tol:
            segments.append(
                PropagationSegment(
                    phase_name="coast",
                    start_s=cursor_s,
                    end_s=thrust_segment.start_s,
                    thrust_n=0.0,
                    isp_s=300.0,
                    delta_deg=0.0,
                    maneuver_index=None,
                    dv_direction=1,
                )
            )
        if thrust_segment.end_s > cursor_s + tol:
            segment_start_s = max(cursor_s, thrust_segment.start_s)
            segments.append(
                PropagationSegment(
                    phase_name=thrust_segment.phase_name,
                    start_s=segment_start_s,
                    end_s=thrust_segment.end_s,
                    thrust_n=thrust_segment.thrust_n,
                    isp_s=thrust_segment.isp_s,
                    delta_deg=thrust_segment.delta_deg,
                    maneuver_index=thrust_segment.maneuver_index,
                    dv_direction=thrust_segment.dv_direction,
                )
            )
            cursor_s = thrust_segment.end_s

    if final_time_s > cursor_s + tol or not segments:
        segments.append(
            PropagationSegment(
                phase_name="coast",
                start_s=cursor_s,
                end_s=final_time_s,
                thrust_n=0.0,
                isp_s=300.0,
                delta_deg=0.0,
                maneuver_index=None,
                dv_direction=1,
            )
        )

    return segments


def _targets_in_segment(start_s: float, end_s: float, sample_interval_s: float) -> list[float]:
    tol = 1e-9
    if end_s <= start_s + tol:
        return []

    targets: list[float] = []
    first_mark = math.ceil(start_s / sample_interval_s) * sample_interval_s
    if first_mark <= start_s + tol:
        first_mark += sample_interval_s

    t_mark = first_mark
    while t_mark < end_s - tol:
        targets.append(float(t_mark))
        t_mark += sample_interval_s

    targets.append(float(end_s))
    return targets


def _command_for_segment(segment: PropagationSegment, state: StateVector) -> ThrustCommand:
    if segment.thrust_n <= 0.0:
        return ThrustCommand(
            thrust_n=0.0,
            alpha_rad=0.0,
            delta_rad=0.0,
            isp_s=300.0,
        )
    if segment.isp_s <= 0.0:
        raise ValueError(f"Segment ISP must be positive for thrusting phase '{segment.phase_name}'.")
    if segment.dv_direction not in {-1, 1}:
        raise ValueError(f"Segment dv_direction must be 1 or -1 for thrusting phase '{segment.phase_name}'.")
    delta_rad = math.radians(segment.delta_deg)
    alpha_rad = solve_alpha_from_delta(state, delta_rad, dv_direction=segment.dv_direction)
    return ThrustCommand(
        thrust_n=segment.thrust_n,
        alpha_rad=alpha_rad,
        delta_rad=delta_rad,
        isp_s=segment.isp_s,
    )


def _wrap_degrees_180(angle_deg: float) -> float:
    return (float(angle_deg) + 180.0) % 360.0 - 180.0


def _direction_longitude_latitude_deg(direction_ecef: Vector3) -> tuple[float, float]:
    unit = np.asarray(direction_ecef, dtype=np.float64)
    norm = float(np.linalg.norm(unit))
    if norm <= 0.0:
        return float("nan"), float("nan")
    unit = unit / norm
    x, y, z = (float(value) for value in unit)
    longitude_deg = math.degrees(math.atan2(y, x))
    latitude_deg = math.degrees(math.atan2(z, math.hypot(x, y)))
    return _wrap_degrees_180(longitude_deg), latitude_deg


def _active_thrust_segment_at_time(
    segments: list[PropagationSegment] | None,
    elapsed_time_s: float,
    phase: str,
    *,
    tol: float = 1e-6,
) -> PropagationSegment | None:
    if not segments:
        return None
    t_s = float(elapsed_time_s)
    candidates = [
        segment
        for segment in segments
        if segment.thrust_n > 0.0 and segment.start_s - tol <= t_s <= segment.end_s + tol
    ]
    if not candidates:
        return None
    for segment in candidates:
        if segment.phase_name == phase:
            return segment
    for segment in candidates:
        if abs(t_s - segment.start_s) <= tol:
            return segment
    return candidates[-1]


def _thrust_history_values(
    *,
    state: StateVector,
    segment: PropagationSegment | None,
    elapsed_time_s: float,
    theta_g0_rad: float,
    constants: DynamicsConstants,
) -> dict[str, float]:
    empty = {
        "thrust_alpha_deg": float("nan"),
        "thrust_beta_deg": float("nan"),
        "thrust_longitude_deg": float("nan"),
        "thrust_latitude_deg": float("nan"),
    }
    if segment is None:
        return empty

    command = _command_for_segment(segment, state)
    if command.thrust_n <= 0.0:
        return empty

    direction_eci = thrust_direction(command.alpha_rad, command.delta_rad)
    direction_ecef = eci_to_ecef(
        direction_eci,
        elapsed_time_s=elapsed_time_s,
        earth_rotation_rate_rad_s=constants.earth_rotation_rate_rad_s,
        theta_g0_rad=theta_g0_rad,
    )
    longitude_deg, latitude_deg = _direction_longitude_latitude_deg(direction_ecef)
    return {
        "thrust_alpha_deg": _wrap_degrees_180(math.degrees(command.alpha_rad)),
        "thrust_beta_deg": math.degrees(command.delta_rad),
        "thrust_longitude_deg": longitude_deg,
        "thrust_latitude_deg": latitude_deg,
    }


def propagate_by_segments(
    initial_state: StateVector,
    segments: list[PropagationSegment],
    *,
    sample_interval_s: float = 60.0,
    max_step_s: float = 10.0,
    coast_max_step_s: float | None = None,
    constants: DynamicsConstants = DynamicsConstants(),
) -> tuple[NDArray[np.float64], NDArray[np.float64], list[str]]:
    """
    Propagate state segment-by-segment.
    Output points:
      - every sample_interval_s (default each minute)
      - each segment end (thus maneuver ignition on/off events are included).
    """

    if not segments:
        raise ValueError("At least one propagation segment is required.")
    if sample_interval_s <= 0.0:
        raise ValueError("sample_interval_s must be positive.")
    if max_step_s <= 0.0:
        raise ValueError("max_step_s must be positive.")
    if coast_max_step_s is not None and coast_max_step_s <= 0.0:
        raise ValueError("coast_max_step_s must be positive when provided.")

    tol = 1e-7
    first_start_s = segments[0].start_s
    if abs(first_start_s) > tol:
        raise ValueError("First segment must start at t=0.")

    current_time_s = 0.0
    current_state = np.asarray(initial_state, dtype=np.float64).copy()

    output_times: list[float] = [current_time_s]
    output_states: list[StateVector] = [current_state.copy()]
    output_phases: list[str] = [segments[0].phase_name]

    for segment in segments:
        if abs(segment.start_s - current_time_s) > tol:
            raise ValueError(
                f"Non-continuous segment timeline: expected start {current_time_s:.6f}, got {segment.start_s:.6f}."
            )

        def dynamics_for_segment(t_local: float, s_local: StateVector) -> StateVector:
            command = _command_for_segment(segment, s_local)
            return satellite_dynamics(t_local, s_local, command, constants)

        segment_max_step_s = (
            coast_max_step_s
            if coast_max_step_s is not None and segment.phase_name == "coast"
            else max_step_s
        )
        targets = _targets_in_segment(segment.start_s, segment.end_s, sample_interval_s)
        for target_time_s in targets:
            while current_time_s < target_time_s - 1e-12:
                step_s = min(segment_max_step_s, target_time_s - current_time_s)
                current_state = rk4_step(dynamics_for_segment, current_time_s, current_state, step_s)
                current_time_s += step_s
            output_times.append(current_time_s)
            output_states.append(current_state.copy())
            output_phases.append(segment.phase_name)

    return (
        np.asarray(output_times, dtype=np.float64),
        np.asarray(output_states, dtype=np.float64),
        output_phases,
    )


def _orbital_elements_or_nan(position_m: Vector3, velocity_m_s: Vector3, mu_m3_s2: float) -> OrbitalElements | None:
    try:
        return state_vector_to_orbital_elements(position_m, velocity_m_s, mu_m3_s2=mu_m3_s2)
    except Exception:
        return None


def build_orbit_history_rows(
    times_s: NDArray[np.float64],
    states: NDArray[np.float64],
    phases: list[str],
    *,
    theta_g0_rad: float,
    event_times_s: list[float],
    segments: list[PropagationSegment] | None = None,
    constants: DynamicsConstants = DynamicsConstants(),
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    tol = 1e-6

    for idx, t_s in enumerate(times_s):
        state = states[idx]
        phase = phases[idx] if idx < len(phases) else "unknown"
        pos = state[:3]
        vel = state[3:6]
        mass_kg = float(state[6])
        orbit_height_m = float(np.linalg.norm(pos) - constants.r0)

        elements = _orbital_elements_or_nan(pos, vel, mu_m3_s2=constants.mu)
        if elements is None:
            a_m = float("nan")
            e_val = float("nan")
            inc_deg = float("nan")
            raan_deg = float("nan")
            argp_deg = float("nan")
            ta_deg = float("nan")
        else:
            a_m = elements.semi_major_axis_m
            e_val = elements.eccentricity
            inc_deg = elements.inclination_deg
            raan_deg = elements.raan_deg
            argp_deg = elements.argument_of_perigee_deg
            ta_deg = elements.true_anomaly_deg if elements.true_anomaly_deg is not None else float("nan")

        subpoint = subsatellite_point_from_eci(
            elapsed_time_s=float(t_s),
            position_eci_m=pos,
            theta_g0_rad=theta_g0_rad,
            constants=constants,
        )
        is_event_point = any(abs(float(t_s) - event_time_s) <= tol for event_time_s in event_times_s)
        thrust_values = _thrust_history_values(
            state=state,
            segment=_active_thrust_segment_at_time(segments, float(t_s), phase, tol=tol),
            elapsed_time_s=float(t_s),
            theta_g0_rad=theta_g0_rad,
            constants=constants,
        )

        rows.append(
            {
                "elapsed_time_s": float(t_s),
                "elapsed_time_min": float(t_s) / 60.0,
                "phase": phase,
                "is_event_point": int(is_event_point),
                "semi_major_axis_m": a_m,
                "eccentricity": e_val,
                "inclination_deg": inc_deg,
                "raan_deg": raan_deg,
                "argument_of_perigee_deg": argp_deg,
                "true_anomaly_deg": ta_deg,
                "position_x_m": float(pos[0]),
                "position_y_m": float(pos[1]),
                "position_z_m": float(pos[2]),
                "velocity_x_m_s": float(vel[0]),
                "velocity_y_m_s": float(vel[1]),
                "velocity_z_m_s": float(vel[2]),
                **thrust_values,
                "subsatellite_longitude_deg": subpoint.longitude_deg,
                "subsatellite_latitude_deg": subpoint.latitude_deg,
                "subsatellite_altitude_m": subpoint.altitude_m,
                "orbit_height_m": orbit_height_m,
                "mass_kg": mass_kg,
            }
        )

    return rows


def _row_at_elapsed_time(rows: list[dict[str, Any]], elapsed_time_s: float, *, tol: float = 1e-3) -> dict[str, Any]:
    if not rows:
        raise ValueError("No orbit history rows are available.")
    target = float(elapsed_time_s)
    best = min(rows, key=lambda row: abs(float(row["elapsed_time_s"]) - target))
    if abs(float(best["elapsed_time_s"]) - target) > tol:
        raise ValueError(f"No orbit history row found at elapsed time {target:.6f} s.")
    return best


def build_maneuver_result_rows(
    strategy_steps: list[ManeuverStrategyStep],
    orbit_history_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Summarize each maneuver at its total burn end.

    burn_duration_min is the total burn window from Tn_start_min, so propellant
    use is the mass difference between Tn_start_min and Tn_start_min + burn_duration_min.
    """

    summaries: list[dict[str, Any]] = []
    tol = 1e-9
    for step in strategy_steps:
        total_burn_duration_s = step.burn_duration_min * 60.0
        if total_burn_duration_s <= tol:
            continue

        maneuver_start_s = max(0.0, step.Tn_start_min * 60.0)
        maneuver_end_s = max(0.0, step.Tn_start_min * 60.0 + total_burn_duration_s)
        start_row = _row_at_elapsed_time(orbit_history_rows, maneuver_start_s)
        end_row = _row_at_elapsed_time(orbit_history_rows, maneuver_end_s)
        propellant_consumed_kg = float(start_row["mass_kg"]) - float(end_row["mass_kg"])

        summaries.append(
            {
                "maneuver_index": step.maneuver_index,
                "elapsed_time_s": float(end_row["elapsed_time_s"]),
                "elapsed_time_min": float(end_row["elapsed_time_min"]),
                "semi_major_axis_m": float(end_row["semi_major_axis_m"]),
                "inclination_deg": float(end_row["inclination_deg"]),
                "subsatellite_longitude_deg": float(end_row["subsatellite_longitude_deg"]),
                "subsatellite_latitude_deg": float(end_row["subsatellite_latitude_deg"]),
                "propellant_consumed_kg": propellant_consumed_kg,
            }
        )
    return summaries


def export_orbit_history_csv(rows: list[dict[str, Any]], output_path: str | Path) -> Path:
    path = Path(output_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=ORBIT_HISTORY_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return path


def simulate_with_maneuver_strategy_config(
    *,
    strategy_config_path: str | Path = DEFAULT_MANEUVER_STRATEGY_PATH,
    output_csv_path: str | Path = "scripts/full_orbit_history.csv",
    initial_mass_kg: float | None = None,
    t0_orbit: OrbitalElements | None = None,
    sample_interval_s: float = 60.0,
    max_step_s: float = 10.0,
    coast_max_step_s: float | None = None,
    extra_free_flight_s: float = 24.0 * 3600.0,
    constants: DynamicsConstants = DynamicsConstants(),
) -> tuple[Path, list[dict[str, Any]]]:
    """
    Read maneuver strategy config and run full-process propagation.

    Output rows include:
      elapsed time, six orbital elements, position/velocity, subsatellite lon/lat,
      thrust direction, orbit height, mass, one point per minute plus ignition on/off event times.
    """

    strategy_config = load_maneuver_strategy_config(strategy_config_path)
    if initial_mass_kg is None:
        initial_mass_kg = float(strategy_config.get("launch_mass_kg", 5200.0))
    if initial_mass_kg <= 0.0:
        raise ValueError("initial_mass_kg must be positive.")
    t0_epoch_utc = _t0_epoch_from_strategy_payload(strategy_config)
    if t0_orbit is None:
        t0_orbit = _orbital_elements_from_t0_payload(
            strategy_config.get("t0_orbit"),
            t0_epoch_utc=t0_epoch_utc,
        )

    strategy_steps = load_maneuver_strategy_steps(strategy_config_path)
    segments = build_propagation_segments_from_strategy(strategy_steps, extra_free_flight_s=extra_free_flight_s)

    t0_elements = t0_orbit.validate()
    anomaly_source: Literal["mean", "true"] = "mean" if t0_elements.mean_anomaly_deg is not None else "true"
    pos0, vel0 = orbital_elements_to_state_vector(
        t0_elements,
        mu_m3_s2=constants.mu,
        anomaly_source=anomaly_source,
    )
    theta_g0 = greenwich_angle_at_utc(t0_epoch_utc)
    initial_state = np.array(
        [pos0[0], pos0[1], pos0[2], vel0[0], vel0[1], vel0[2], initial_mass_kg],
        dtype=np.float64,
    )

    times_s, states, phases = propagate_by_segments(
        initial_state=initial_state,
        segments=segments,
        sample_interval_s=sample_interval_s,
        max_step_s=max_step_s,
        coast_max_step_s=coast_max_step_s,
        constants=constants,
    )

    event_times_s = sorted(
        {
            float(seg.start_s)
            for seg in segments
            if seg.phase_name != "coast"
        }
        | {
            float(seg.end_s)
            for seg in segments
            if seg.phase_name != "coast"
        }
    )

    rows = build_orbit_history_rows(
        times_s=times_s,
        states=states,
        phases=phases,
        theta_g0_rad=theta_g0,
        event_times_s=event_times_s,
        segments=segments,
        constants=constants,
    )
    csv_path = export_orbit_history_csv(rows, output_csv_path)
    return csv_path, rows


def test_zero_thrust_ground_track_24h(
    *,
    output_path: str | Path = "scripts/groundtrack_24h_f0.png",
    dt_s: float = 60.0,
    initial_mass_kg: float = 500.0,
    constants: DynamicsConstants = DynamicsConstants(),
) -> Path:
    """
    Test case:
    - F = 0 N
    - Integrate for 24 hours
    - Plot subsatellite longitude/latitude ground track.
    """

    if dt_s <= 0.0:
        raise ValueError("dt_s must be positive.")
    if initial_mass_kg <= 0.0:
        raise ValueError("initial_mass_kg must be positive.")

    try:
        import matplotlib.pyplot as plt
    except ImportError as exc:  # pragma: no cover - runtime dependency check
        raise RuntimeError("matplotlib is required to output the ground-track plot.") from exc

    t0_epoch_utc = default_t0_epoch_utc()
    t0_elements = default_t0_orbit(t0_epoch_utc).validate()
    pos0, vel0 = orbital_elements_to_state_vector(t0_elements, mu_m3_s2=constants.mu, anomaly_source="mean")
    theta_g0 = greenwich_angle_at_utc(t0_epoch_utc)
    initial_state = np.array(
        [pos0[0], pos0[1], pos0[2], vel0[0], vel0[1], vel0[2], initial_mass_kg],
        dtype=np.float64,
    )

    zero_thrust_command = ThrustCommand(
        thrust_n=0.0,
        alpha_rad=0.0,
        delta_rad=0.0,
        isp_s=2200.0,
    )
    times, states = propagate(
        initial_state=initial_state,
        t0=0.0,
        tf=24.0 * 3600.0,
        dt=dt_s,
        command_law=lambda _t, _s: zero_thrust_command,
        constants=constants,
    )

    longitudes_deg = np.empty_like(times)
    latitudes_deg = np.empty_like(times)
    for i, t in enumerate(times):
        subpoint = subsatellite_point_from_eci(
            elapsed_time_s=float(t),
            position_eci_m=states[i, :3],
            theta_g0_rad=theta_g0,
            constants=constants,
        )
        longitudes_deg[i] = subpoint.longitude_deg
        latitudes_deg[i] = subpoint.latitude_deg

    fig, ax = plt.subplots(figsize=(11, 6), dpi=120, facecolor="#071016")
    ax.set_facecolor("#0B1A22")
    ax.set_title("Ground Track (F=0, 24 h)", color="#D8E7EF", pad=14)
    ax.set_xlabel("Longitude [deg]")
    ax.set_ylabel("Latitude [deg]")
    ax.set_xlim(-180.0, 180.0)
    ax.set_ylim(-90.0, 90.0)
    ax.grid(True, linestyle="--", linewidth=0.6, alpha=0.35, color="#244958")
    ax.tick_params(colors="#9FB5BF")
    ax.xaxis.label.set_color("#9FB5BF")
    ax.yaxis.label.set_color("#9FB5BF")
    for spine in ax.spines.values():
        spine.set_color("#1E3B49")

    # Split the line at the dateline to avoid wrap-around artifacts.
    split_indices = np.where(np.abs(np.diff(longitudes_deg)) > 180.0)[0]
    start = 0
    for idx in split_indices:
        ax.plot(longitudes_deg[start : idx + 1], latitudes_deg[start : idx + 1], color="#66D9EA", linewidth=1.5)
        start = idx + 1
    ax.plot(longitudes_deg[start:], latitudes_deg[start:], color="#66D9EA", linewidth=1.5, label="Ground track")

    ax.scatter([longitudes_deg[0]], [latitudes_deg[0]], color="#F2B84B", s=36, zorder=3, label="T0")
    legend = ax.legend(loc="upper right", facecolor="#0D1C25", edgecolor="#244958", framealpha=0.96)
    for text in legend.get_texts():
        text.set_color("#D8E7EF")

    output = Path(output_path).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(output, facecolor=fig.get_facecolor(), edgecolor="none")
    plt.close(fig)
    return output


def main() -> None:
    constants = DynamicsConstants()
    t0_epoch_utc = default_t0_epoch_utc()
    t0_elements = default_t0_orbit(t0_epoch_utc).validate()

    pos0, vel0 = orbital_elements_to_state_vector(t0_elements, mu_m3_s2=constants.mu, anomaly_source="mean")
    roundtrip = state_vector_to_orbital_elements(pos0, vel0, mu_m3_s2=constants.mu)

    hp_calc, ha_calc = perigee_apogee_altitudes(t0_elements, constants.r0)

    print("T0 default orbital elements (from image):")
    print(
        f"  a={t0_elements.semi_major_axis_m:.3f} m, e={t0_elements.eccentricity:.10f}, "
        f"i={t0_elements.inclination_deg:.5f} deg"
    )
    print(
        f"  omega={t0_elements.argument_of_perigee_deg:.5f} deg, "
        f"Omega={t0_elements.raan_deg:.5f} deg, M={t0_elements.mean_anomaly_deg:.5f} deg, "
        f"f={t0_elements.true_anomaly_deg:.5f} deg"
    )
    print(
        f"  Hp(input)={DEFAULT_T0_PERIGEE_ALTITUDE_M:.1f} m, Ha(input)={DEFAULT_T0_APOGEE_ALTITUDE_M:.1f} m, "
        f"T0 epoch={t0_epoch_utc}, "
        f"node lon={DEFAULT_T0_ORBIT_PAYLOAD['raan_deg']:.5f} deg, "
        f"B={DEFAULT_T0_GEODETIC_LATITUDE_DEG:.5f} deg"
    )
    print(f"  Hp(calc)={hp_calc:.1f} m, Ha(calc)={ha_calc:.1f} m")

    print("T0 converted state vector:")
    print(f"  r0 [m]   = [{pos0[0]:.3f}, {pos0[1]:.3f}, {pos0[2]:.3f}]")
    print(f"  v0 [m/s] = [{vel0[0]:.6f}, {vel0[1]:.6f}, {vel0[2]:.6f}]")

    print("Round-trip (state -> elements):")
    print(
        f"  a={roundtrip.semi_major_axis_m:.3f} m, e={roundtrip.eccentricity:.10f}, "
        f"i={roundtrip.inclination_deg:.5f} deg, Omega={roundtrip.raan_deg:.5f} deg"
    )
    print(
        f"  omega={roundtrip.argument_of_perigee_deg:.5f} deg, "
        f"M={roundtrip.mean_anomaly_deg:.5f} deg, f={roundtrip.true_anomaly_deg:.5f} deg"
    )

    theta_g0 = greenwich_angle_at_utc(t0_epoch_utc)
    t0_subpoint = subsatellite_point_from_eci(
        0.0,
        pos0,
        theta_g0_rad=theta_g0,
        constants=constants,
    )
    print("T0 subsatellite point:")
    print(
        f"  Lon={t0_subpoint.longitude_deg:.5f} deg, "
        f"Lat={t0_subpoint.latitude_deg:.5f} deg, "
        f"Alt={t0_subpoint.altitude_m:.1f} m"
    )

    mass0 = 500.0
    initial_state = np.array([pos0[0], pos0[1], pos0[2], vel0[0], vel0[1], vel0[2], mass0], dtype=np.float64)

    delta_rad = math.radians(0.0)
    alpha_rad = solve_alpha_from_delta(initial_state, delta_rad)
    constraint0 = thrust_tangency_constraint(initial_state, delta_rad)
    print(f"Solved alpha from delta: {math.degrees(alpha_rad):.6f} deg")
    print(f"Initial tangency constraint value: {constraint0:.6e} m")

    def command_law(_t: float, s: StateVector) -> ThrustCommand:
        return ThrustCommand(
            thrust_n=0.2,
            alpha_rad=solve_alpha_from_delta(s, delta_rad),
            delta_rad=delta_rad,
            isp_s=2200.0,
        )

    _, states = propagate(
        initial_state=initial_state,
        t0=0.0,
        tf=600.0,
        dt=1.0,
        command_law=command_law,
        constants=constants,
    )

    final = states[-1]
    print("Final state after 600 s (SI units):")
    print(f"  Position [m]:  [{final[0]:.3f}, {final[1]:.3f}, {final[2]:.3f}]")
    print(f"  Velocity [m/s]:[{final[3]:.6f}, {final[4]:.6f}, {final[5]:.6f}]")
    print(f"  Mass [kg]:     {final[6]:.6f}")

    final_subpoint = subsatellite_point_from_eci(
        600.0,
        final[:3],
        theta_g0_rad=theta_g0,
        constants=constants,
    )
    print("Subsatellite point at T0+600 s:")
    print(
        f"  Lon={final_subpoint.longitude_deg:.5f} deg, "
        f"Lat={final_subpoint.latitude_deg:.5f} deg, "
        f"Alt={final_subpoint.altitude_m:.1f} m"
    )

    figure_path = test_zero_thrust_ground_track_24h(constants=constants)
    print(f"Zero-thrust 24h ground-track figure saved to: {figure_path}")

    strategy_config = load_maneuver_strategy_config(DEFAULT_MANEUVER_STRATEGY_PATH)
    strategy_initial_mass_kg = float(
        strategy_config.get(
            "launch_mass_kg",
            load_initial_mass_kg(DEFAULT_SATELLITE_STATUS_PATH, default_mass_kg=5200.0),
        )
    )
    history_csv, rows = simulate_with_maneuver_strategy_config(
        strategy_config_path=DEFAULT_MANEUVER_STRATEGY_PATH,
        output_csv_path="scripts/full_orbit_history.csv",
        sample_interval_s=60.0,
        max_step_s=10.0,
        extra_free_flight_s=24.0 * 3600.0,
        constants=constants,
    )
    print(f"Maneuver-strategy full orbit history saved to: {history_csv}")
    print(f"Initial mass used for maneuver strategy propagation: {strategy_initial_mass_kg:.3f} kg")
    print(f"Output samples: {len(rows)}")
    if rows:
        print(
            "Final sample: "
            f"t={rows[-1]['elapsed_time_min']:.3f} min, "
            f"m={rows[-1]['mass_kg']:.3f} kg, "
            f"lon={rows[-1]['subsatellite_longitude_deg']:.5f} deg, "
            f"lat={rows[-1]['subsatellite_latitude_deg']:.5f} deg"
        )


if __name__ == "__main__":
    main()
