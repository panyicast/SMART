from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
from typing import SupportsFloat

import numpy as np

from smart.services.spice_service import SpiceKernelManager, default_local_kernel_roots

_EARTH_RADIUS_M = 6_378_140.0
_EARTH_FLATTENING = 1.0 / 298.257223563


@dataclass(frozen=True, slots=True)
class GeodeticPoint:
    longitude_deg: float
    latitude_deg: float
    altitude_m: float


def utc_now_iso_z() -> str:
    return format_utc(datetime.now(timezone.utc))


def parse_utc(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    text = str(value).strip()
    if not text:
        raise ValueError("UTC text cannot be empty.")
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"

    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def format_utc(value: str | datetime, *, timespec: str = "seconds") -> str:
    return parse_utc(value).isoformat(timespec=timespec).replace("+00:00", "Z")


def greenwich_angle_at_utc(epoch_utc: str | datetime) -> float:
    epoch = parse_utc(epoch_utc)
    manager = _build_spice_manager()
    return _greenwich_angle_at(epoch, manager=manager)


def inertial_raan_deg_from_ascending_node_longitude_deg(
    ascending_node_longitude_deg: SupportsFloat,
    epoch_utc: str | datetime,
) -> float:
    return (
        float(ascending_node_longitude_deg)
        + math.degrees(greenwich_angle_at_utc(epoch_utc))
    ) % 360.0


def subsatellite_point_from_eci(
    position_eci_m: np.ndarray,
    *,
    epoch_utc: str | datetime,
) -> GeodeticPoint:
    position_ecef_m = ecef_position_from_eci(position_eci_m, epoch_utc=epoch_utc)
    return geodetic_point_from_ecef(position_ecef_m)


def ecef_position_from_eci(
    position_eci_m: np.ndarray,
    *,
    epoch_utc: str | datetime,
) -> np.ndarray:
    theta = greenwich_angle_at_utc(epoch_utc)
    cos_theta = math.cos(theta)
    sin_theta = math.sin(theta)
    x_eci, y_eci, z_eci = (float(value) for value in position_eci_m)
    return np.asarray(
        [
            cos_theta * x_eci + sin_theta * y_eci,
            -sin_theta * x_eci + cos_theta * y_eci,
            z_eci,
        ],
        dtype=np.float64,
    )


def ecef_state_from_eci(
    position_eci_m: np.ndarray,
    velocity_eci_m_s: np.ndarray,
    *,
    epoch_utc: str | datetime,
    manager: SpiceKernelManager | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    epoch = parse_utc(epoch_utc)
    manager = manager if manager is not None else _build_spice_manager()

    if manager is not None:
        try:
            position, velocity = manager.transform_state(
                position_eci_m,
                velocity_eci_m_s,
                from_frame="J2000",
                to_frame="ITRF93",
                utc=format_utc(epoch),
            )
            return (
                np.asarray(position, dtype=np.float64),
                np.asarray(velocity, dtype=np.float64),
            )
        except Exception:
            pass

    theta = greenwich_angle_at_utc(epoch)
    cos_theta = math.cos(theta)
    sin_theta = math.sin(theta)
    rotation = np.asarray(
        [
            [cos_theta, sin_theta, 0.0],
            [-sin_theta, cos_theta, 0.0],
            [0.0, 0.0, 1.0],
        ],
        dtype=np.float64,
    )
    omega_vec = np.asarray([0.0, 0.0, 7.2921150e-5], dtype=np.float64)
    position_eci = np.asarray(position_eci_m, dtype=np.float64)
    velocity_eci = np.asarray(velocity_eci_m_s, dtype=np.float64)
    position_ecef = rotation @ position_eci
    velocity_ecef = rotation @ (velocity_eci - np.cross(omega_vec, position_eci))
    return position_ecef, velocity_ecef


def build_spice_manager_for_earth_orientation() -> SpiceKernelManager | None:
    return _build_spice_manager()


def geodetic_point_from_ecef(position_ecef_m: np.ndarray) -> GeodeticPoint:
    x_ecef, y_ecef, z_ecef = (float(value) for value in position_ecef_m)
    longitude_deg = math.degrees(math.atan2(y_ecef, x_ecef))
    p = math.hypot(x_ecef, y_ecef)
    e2 = _EARTH_FLATTENING * (2.0 - _EARTH_FLATTENING)

    if p <= 1e-12:
        latitude_deg = math.copysign(90.0, z_ecef) if abs(z_ecef) > 1e-12 else 0.0
        altitude_m = abs(z_ecef) - _EARTH_RADIUS_M * (1.0 - _EARTH_FLATTENING)
        return GeodeticPoint(longitude_deg=0.0, latitude_deg=latitude_deg, altitude_m=altitude_m)

    latitude = math.atan2(z_ecef, p * (1.0 - e2))
    altitude_m = 0.0
    for _ in range(20):
        sin_lat = math.sin(latitude)
        cos_lat = math.cos(latitude)
        n = _EARTH_RADIUS_M / math.sqrt(1.0 - e2 * sin_lat * sin_lat)
        altitude_m = p / max(abs(cos_lat), 1e-12) - n
        next_latitude = math.atan2(z_ecef, p * (1.0 - e2 * n / (n + altitude_m)))
        if abs(next_latitude - latitude) < 1e-12:
            latitude = next_latitude
            break
        latitude = next_latitude

    return GeodeticPoint(
        longitude_deg=((longitude_deg + 180.0) % 360.0) - 180.0,
        latitude_deg=math.degrees(latitude),
        altitude_m=altitude_m,
    )


def _greenwich_angle_at(epoch_utc: datetime, *, manager: SpiceKernelManager | None) -> float:
    if manager is not None:
        try:
            position = manager.transform_position(
                [1.0, 0.0, 0.0],
                from_frame="ITRF93",
                to_frame="J2000",
                utc=format_utc(epoch_utc),
            )
            return math.atan2(float(position[1]), float(position[0]))
        except Exception:
            pass
    return _greenwich_angle_gmst(epoch_utc)


def _greenwich_angle_gmst(epoch_utc: datetime) -> float:
    utc = parse_utc(epoch_utc)
    year = utc.year
    month = utc.month
    day = utc.day
    hour = utc.hour
    minute = utc.minute
    second = utc.second + utc.microsecond / 1_000_000.0

    if month <= 2:
        year -= 1
        month += 12

    a = year // 100
    b = 2 - a + a // 4
    jd = (
        math.floor(365.25 * (year + 4716))
        + math.floor(30.6001 * (month + 1))
        + day
        + b
        - 1524.5
        + (hour + minute / 60.0 + second / 3600.0) / 24.0
    )
    t = (jd - 2451545.0) / 36525.0
    gmst_deg = (
        280.46061837
        + 360.98564736629 * (jd - 2451545.0)
        + 0.000387933 * t * t
        - (t * t * t) / 38710000.0
    )
    return math.radians(gmst_deg % 360.0)


def _build_spice_manager() -> SpiceKernelManager | None:
    try:
        manager = SpiceKernelManager(local_kernel_roots=default_local_kernel_roots())
        manager.ensure_local_kernels_loaded()
        return manager
    except Exception:
        return None
