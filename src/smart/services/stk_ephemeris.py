from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math
from pathlib import Path
from typing import Any

import numpy as np

from smart.logging import get_logger
from smart.services.earth_orientation import (
    build_spice_manager_for_earth_orientation,
    ecef_state_from_eci,
    format_utc,
    parse_utc,
)
from smart.services.spice_service import SpiceKernelManager, default_local_kernel_roots

_log = get_logger(__name__)

_EARTH_ROTATION_RATE_RAD_S = 7.2921150e-5
_DEFAULT_SCENARIO_EPOCH_UTC = "2024-01-01T00:00:00Z"


@dataclass(frozen=True, slots=True)
class StkEphemerisMetadata:
    scenario_epoch_utc: str
    sample_count: int
    output_path: Path


def write_stk_ephemeris(
    rows: Sequence[Mapping[str, Any]],
    output_path: str | Path,
    *,
    scenario_epoch_utc: str | None = None,
) -> StkEphemerisMetadata:
    if not rows:
        raise ValueError("STK ephemeris export requires at least one trajectory sample.")

    target_path = Path(output_path).expanduser().resolve()
    target_path.parent.mkdir(parents=True, exist_ok=True)

    normalized_rows = [_normalize_ephemeris_row(row) for row in rows]
    epoch_utc = scenario_epoch_utc or derive_scenario_epoch_utc(normalized_rows)
    scenario_epoch = _parse_utc(epoch_utc)
    scenario_epoch_text = _format_stk_epoch(scenario_epoch)
    spice_manager = build_spice_manager_for_earth_orientation()

    lines = [
        "stk.v.11.0",
        "",
        "BEGIN Ephemeris",
        "",
        f"NumberOfEphemerisPoints {len(normalized_rows)}",
        f"ScenarioEpoch           {scenario_epoch_text}",
        "InterpolationMethod     Lagrange",
        "InterpolationOrder      5",
        "CentralBody             Earth",
        "CoordinateSystem        Fixed",
        "DistanceUnit            Meters",
        "",
        "EphemerisTimePosVel",
        "",
    ]

    for row in normalized_rows:
        sample_epoch_utc = format_utc(
            scenario_epoch + timedelta(seconds=row["elapsed_time_s"]),
            timespec="microseconds",
        )
        position_ecef_m, velocity_ecef_m_s = ecef_state_from_eci(
            np.asarray(
                [
                    row["position_x_m"],
                    row["position_y_m"],
                    row["position_z_m"],
                ],
                dtype=np.float64,
            ),
            np.asarray(
                [
                    row["velocity_x_m_s"],
                    row["velocity_y_m_s"],
                    row["velocity_z_m_s"],
                ],
                dtype=np.float64,
            ),
            epoch_utc=sample_epoch_utc,
            manager=spice_manager,
        )
        lines.append(
            " ".join(
                [
                    f"{row['elapsed_time_s']:.14e}",
                    f"{position_ecef_m[0]:.14e}",
                    f"{position_ecef_m[1]:.14e}",
                    f"{position_ecef_m[2]:.14e}",
                    f"{velocity_ecef_m_s[0]:.14e}",
                    f"{velocity_ecef_m_s[1]:.14e}",
                    f"{velocity_ecef_m_s[2]:.14e}",
                ]
            )
        )

    lines.extend(["", "END Ephemeris", ""])
    target_path.write_text("\n".join(lines), encoding="utf-8")
    return StkEphemerisMetadata(
        scenario_epoch_utc=_format_utc(scenario_epoch),
        sample_count=len(normalized_rows),
        output_path=target_path,
    )


def derive_scenario_epoch_utc(rows: Sequence[Mapping[str, Any]]) -> str:
    if not rows:
        return _DEFAULT_SCENARIO_EPOCH_UTC

    first = _normalize_ephemeris_row(rows[0])
    longitude_deg = float(first.get("subsatellite_longitude_deg", 0.0))
    theta_g0 = _solve_theta_g0_from_t0_longitude(
        np.asarray(
            [
                first["position_x_m"],
                first["position_y_m"],
                first["position_z_m"],
            ],
            dtype=np.float64,
        ),
        longitude_deg,
    )
    epoch = _solve_epoch_for_greenwich_angle(theta_g0)
    return _format_utc(epoch)


def derive_stk_time_bounds(
    rows: Sequence[Mapping[str, Any]],
    *,
    scenario_epoch_utc: str,
) -> tuple[str, str]:
    if not rows:
        raise ValueError("STK time bounds require at least one trajectory sample.")

    normalized_rows = [_normalize_ephemeris_row(row) for row in rows]
    start_offset_s = min(float(row["elapsed_time_s"]) for row in normalized_rows)
    stop_offset_s = max(float(row["elapsed_time_s"]) for row in normalized_rows)

    start_utc = _parse_utc(scenario_epoch_utc) + timedelta(seconds=start_offset_s)
    stop_utc = _parse_utc(scenario_epoch_utc) + timedelta(seconds=stop_offset_s)
    if stop_utc <= start_utc:
        stop_utc = start_utc + timedelta(seconds=1.0)
    return _format_stk_epoch(start_utc), _format_stk_epoch(stop_utc)


def _normalize_ephemeris_row(row: Mapping[str, Any]) -> dict[str, float]:
    payload = {
        "elapsed_time_s": _coerce_float(row, "elapsed_time_s"),
        "position_x_m": _coerce_float(row, "position_x_m"),
        "position_y_m": _coerce_float(row, "position_y_m"),
        "position_z_m": _coerce_float(row, "position_z_m"),
        "velocity_x_m_s": _coerce_float(row, "velocity_x_m_s"),
        "velocity_y_m_s": _coerce_float(row, "velocity_y_m_s"),
        "velocity_z_m_s": _coerce_float(row, "velocity_z_m_s"),
    }
    if "subsatellite_longitude_deg" in row:
        payload["subsatellite_longitude_deg"] = float(row["subsatellite_longitude_deg"])
    return payload


def _coerce_float(row: Mapping[str, Any], key: str) -> float:
    try:
        return float(row[key])
    except KeyError as exc:
        raise ValueError(f"Trajectory sample is missing required field '{key}'.") from exc
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Trajectory sample field '{key}' must be numeric.") from exc


def _solve_theta_g0_from_t0_longitude(position_eci_t0_m: np.ndarray, t0_longitude_deg: float) -> float:
    x_eci = float(position_eci_t0_m[0])
    y_eci = float(position_eci_t0_m[1])
    lon_eci = math.atan2(y_eci, x_eci)
    lon_target = math.radians(t0_longitude_deg)
    return _wrap_to_pi(lon_eci - lon_target)


def _solve_epoch_for_greenwich_angle(target_angle_rad: float) -> datetime:
    base_epoch = _parse_utc(_DEFAULT_SCENARIO_EPOCH_UTC)
    manager: SpiceKernelManager | None = None
    try:
        candidate = base_epoch
        manager = SpiceKernelManager(local_kernel_roots=default_local_kernel_roots())
        manager.ensure_local_kernels_loaded()
    except Exception as exc:
        _log.debug("SPICE kernel manager unavailable for epoch solving, using GMST fallback: %s", exc)
        candidate = base_epoch
        manager = None

    for _ in range(4):
        angle = _greenwich_angle_at(candidate, manager=manager)
        delta_angle = _wrap_to_pi(target_angle_rad - angle)
        candidate += timedelta(seconds=delta_angle / _EARTH_ROTATION_RATE_RAD_S)

    return candidate.astimezone(timezone.utc)


def _greenwich_angle_at(epoch_utc: datetime, *, manager: SpiceKernelManager | None) -> float:
    if manager is not None:
        try:
            position = manager.transform_position(
                [1.0, 0.0, 0.0],
                from_frame="ITRF93",
                to_frame="J2000",
                utc=_format_utc(epoch_utc),
            )
            return math.atan2(float(position[1]), float(position[0]))
        except Exception as exc:
            _log.debug("SPICE ITRF93->J2000 position transform failed, falling back to GMST: %s", exc)
    return _greenwich_angle_gmst(epoch_utc)


def _greenwich_angle_gmst(epoch_utc: datetime) -> float:
    utc = epoch_utc.astimezone(timezone.utc)
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


def _wrap_to_pi(angle_rad: float) -> float:
    return (angle_rad + math.pi) % (2.0 * math.pi) - math.pi


def _parse_utc(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    epoch = datetime.fromisoformat(normalized)
    if epoch.tzinfo is None:
        epoch = epoch.replace(tzinfo=timezone.utc)
    return epoch.astimezone(timezone.utc)


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _format_stk_epoch(value: datetime) -> str:
    text = value.astimezone(timezone.utc).strftime("%d %b %Y %H:%M:%S.%f")
    return text[1:] if text.startswith("0") else text
