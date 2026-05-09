from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math
from pathlib import Path
from typing import Any

import numpy as np

from smart.services.earth_orientation import format_utc, parse_utc
from smart.services.launch_window import (
    ManeuverInterval,
    _build_timeline,
    _maneuver_intervals,
    _sun_unit_ecef_for_elapsed,
    load_orbit_history_rows,
)


PARAMETER_OPTIONS: tuple[tuple[str, str, str], ...] = (
    ("semi_major_axis_km", "半长轴", "km"),
    ("eccentricity", "偏心率", ""),
    ("inclination_deg", "轨道倾角", "deg"),
    ("raan_deg", "升交点赤经", "deg"),
    ("argument_of_perigee_deg", "近地点幅角", "deg"),
    ("true_anomaly_deg", "真近点角", "deg"),
    ("mean_anomaly_deg", "平近点角", "deg"),
    ("perigee_altitude_km", "近地点高度", "km"),
    ("apogee_altitude_km", "远地点高度", "km"),
    ("subsatellite_longitude_deg", "星下点经度", "deg"),
    ("subsatellite_latitude_deg", "星下点纬度", "deg"),
    ("mass_kg", "卫星质量", "kg"),
    ("beta_angle_deg", "Beta角", "deg"),
    ("earth_sun_angle_deg", "地球矢量-太阳矢量夹角", "deg"),
)


@dataclass(frozen=True, slots=True)
class VisualizationSeries:
    launch_utc: str
    t0_utc: str
    elapsed_min: np.ndarray
    epochs_utc: tuple[str, ...]
    values: dict[str, np.ndarray]
    maneuver_intervals: tuple[ManeuverInterval, ...]


def build_visualization_series(
    *,
    orbit_history_csv: str | Path,
    maneuver_strategy: dict[str, Any],
    launch_utc: str | datetime,
    rocket_flight_time_s: float = 2134.4121,
) -> VisualizationSeries:
    rows = load_orbit_history_rows(orbit_history_csv)
    maneuvers = tuple(_maneuver_intervals(maneuver_strategy))
    launch_epoch = parse_utc(launch_utc)
    t0_epoch = launch_epoch + timedelta(seconds=float(rocket_flight_time_s))
    timeline = _build_timeline(
        rows,
        [],
        maneuvers=list(maneuvers),
        reference_t0_utc=t0_epoch,
    )
    elapsed_min = np.asarray([float(row["elapsed_time_min"]) for row in rows], dtype=np.float64)
    values = _base_values(rows)
    values["mean_anomaly_deg"] = _mean_anomaly_deg(values["true_anomaly_deg"], values["eccentricity"])

    semi_major_axis_km = values["semi_major_axis_km"]
    eccentricity = values["eccentricity"]
    central_radius_km = np.asarray(
        [float(row.get("orbit_height_m", 0.0)) for row in rows],
        dtype=np.float64,
    )
    # Existing CSV stores both subsatellite_altitude_m and orbit_height_m; use the SMART Earth radius
    # implied by position rows for robust apogee/perigee altitude conversion.
    radius_km = np.linalg.norm(timeline["inertial_states"][:, :3], axis=1) / 1000.0
    altitude_km = np.asarray([float(row["subsatellite_altitude_m"]) / 1000.0 for row in rows], dtype=np.float64)
    inferred_earth_radius_km = np.nanmedian(radius_km - altitude_km)
    if not math.isfinite(float(inferred_earth_radius_km)) or inferred_earth_radius_km <= 0.0:
        inferred_earth_radius_km = 6378.1363
    values["perigee_altitude_km"] = semi_major_axis_km * (1.0 - eccentricity) - inferred_earth_radius_km
    values["apogee_altitude_km"] = semi_major_axis_km * (1.0 + eccentricity) - inferred_earth_radius_km
    if "orbit_height_m" in rows[0]:
        values["orbit_height_km"] = central_radius_km / 1000.0

    sun_vectors = _sun_unit_ecef_for_elapsed(t0_epoch, elapsed_min)
    positions_eci = timeline["inertial_states"][:, :3]
    velocities_eci = timeline["inertial_states"][:, 3:]
    orbit_normals = _normalize(np.cross(positions_eci, velocities_eci))
    sun_eci = _sun_unit_eci_for_elapsed(t0_epoch, elapsed_min)
    values["beta_angle_deg"] = np.degrees(np.arcsin(np.clip(np.sum(orbit_normals * sun_eci, axis=1), -1.0, 1.0)))
    earth_vectors = timeline["body_z_unit"]
    values["earth_sun_angle_deg"] = np.degrees(
        np.arccos(np.clip(np.sum(earth_vectors * sun_vectors, axis=1), -1.0, 1.0))
    )

    epochs_utc = tuple(format_utc(t0_epoch + timedelta(minutes=float(minute))) for minute in elapsed_min)
    return VisualizationSeries(
        launch_utc=format_utc(launch_epoch),
        t0_utc=format_utc(t0_epoch),
        elapsed_min=elapsed_min,
        epochs_utc=epochs_utc,
        values=values,
        maneuver_intervals=maneuvers,
    )


def default_launch_utc_from_configs(
    *,
    flight_program: dict[str, Any] | None,
    maneuver_strategy: dict[str, Any] | None,
    rocket_flight_time_s: float = 2134.4121,
) -> str:
    if isinstance(flight_program, dict):
        selected_launch = str(flight_program.get("selected_launch_utc", "") or "").strip()
        if selected_launch:
            return format_utc(parse_utc(selected_launch))
        selected_t0 = str(flight_program.get("selected_t0_utc", "") or "").strip()
        if selected_t0:
            return format_utc(parse_utc(selected_t0) - timedelta(seconds=float(rocket_flight_time_s)))
    if isinstance(maneuver_strategy, dict):
        t0_epoch = str(maneuver_strategy.get("t0_epoch", "") or maneuver_strategy.get("to_epoch", "") or "").strip()
        if t0_epoch:
            return format_utc(parse_utc(t0_epoch) - timedelta(seconds=float(rocket_flight_time_s)))
    return format_utc(datetime.now(tz=timezone.utc))


def parameter_label(key: str) -> str:
    for item_key, label, _unit in PARAMETER_OPTIONS:
        if item_key == key:
            return label
    return key


def parameter_unit(key: str) -> str:
    for item_key, _label, unit in PARAMETER_OPTIONS:
        if item_key == key:
            return unit
    return ""


def _base_values(rows: list[dict[str, float | str]]) -> dict[str, np.ndarray]:
    mapping = {
        "semi_major_axis_km": "semi_major_axis_m",
        "eccentricity": "eccentricity",
        "inclination_deg": "inclination_deg",
        "raan_deg": "raan_deg",
        "argument_of_perigee_deg": "argument_of_perigee_deg",
        "true_anomaly_deg": "true_anomaly_deg",
        "subsatellite_longitude_deg": "subsatellite_longitude_deg",
        "subsatellite_latitude_deg": "subsatellite_latitude_deg",
        "mass_kg": "mass_kg",
    }
    values: dict[str, np.ndarray] = {}
    for target_key, source_key in mapping.items():
        scale = 0.001 if source_key.endswith("_m") and source_key != "mass_kg" else 1.0
        values[target_key] = np.asarray([float(row[source_key]) * scale for row in rows], dtype=np.float64)
    return values


def _mean_anomaly_deg(true_anomaly_deg: np.ndarray, eccentricity: np.ndarray) -> np.ndarray:
    true_anomaly = np.deg2rad(true_anomaly_deg)
    eccentricity = np.clip(eccentricity, 0.0, 0.999999999)
    eccentric_anomaly = 2.0 * np.arctan2(
        np.sqrt(1.0 - eccentricity) * np.sin(true_anomaly / 2.0),
        np.sqrt(1.0 + eccentricity) * np.cos(true_anomaly / 2.0),
    )
    mean_anomaly = eccentric_anomaly - eccentricity * np.sin(eccentric_anomaly)
    return np.mod(np.degrees(mean_anomaly), 360.0)


def _sun_unit_eci_for_elapsed(t0_utc: datetime, elapsed_min: np.ndarray) -> np.ndarray:
    jd = _julian_date(t0_utc) + elapsed_min / 1440.0
    n = jd - 2451545.0
    mean_longitude = np.deg2rad((280.460 + 0.9856474 * n) % 360.0)
    mean_anomaly = np.deg2rad((357.528 + 0.9856003 * n) % 360.0)
    ecliptic_longitude = (
        mean_longitude
        + math.radians(1.915) * np.sin(mean_anomaly)
        + math.radians(0.020) * np.sin(2.0 * mean_anomaly)
    )
    obliquity = np.deg2rad(23.439 - 0.0000004 * n)
    return _normalize(
        np.column_stack(
            (
                np.cos(ecliptic_longitude),
                np.cos(obliquity) * np.sin(ecliptic_longitude),
                np.sin(obliquity) * np.sin(ecliptic_longitude),
            )
        )
    )


def _julian_date(epoch_utc: datetime) -> float:
    utc = parse_utc(epoch_utc)
    year = utc.year
    month = utc.month
    day = utc.day
    hour = utc.hour + utc.minute / 60.0 + (utc.second + utc.microsecond / 1_000_000.0) / 3600.0
    if month <= 2:
        year -= 1
        month += 12
    a = year // 100
    b = 2 - a + a // 4
    return (
        math.floor(365.25 * (year + 4716))
        + math.floor(30.6001 * (month + 1))
        + day
        + b
        - 1524.5
        + hour / 24.0
    )


def _normalize(vectors: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vectors, axis=-1, keepdims=True)
    return vectors / np.maximum(norms, 1e-12)
