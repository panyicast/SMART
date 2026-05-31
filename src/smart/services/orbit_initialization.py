from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from smart.domain.models import (
    EARTH_MU_KM3_S2,
    EARTH_RADIUS_KM,
    OrbitInitializationSettings,
    OrbitalElements,
)
from smart.logging import get_logger
from smart.services.orbital_mechanics import orbital_elements_from_state_vector
from smart.services.spice_service import SpiceKernelManager, default_local_kernel_roots

_log = get_logger(__name__)

_STK_EPOCH_FORMATS = (
    "%d %b %Y %H:%M:%S.%f",
    "%d %b %Y %H:%M:%S",
)
_STK_DISTANCE_TO_KM = {
    "METERS": 0.001,
    "KILOMETERS": 1.0,
    "FEET": 0.0003048,
    "MILES": 1.609344,
    "NAUTICALMILES": 1.852,
}
_STK_INERTIAL_COORDINATE_SYSTEMS = {
    "J2000",
    "ICRF",
    "INERTIAL",
}
_STK_FIXED_FRAME_ALIASES = {
    "FIXED": "ITRF93",
    "ITRF93": "ITRF93",
    "IAU_EARTH": "IAU_EARTH",
}


class OrbitInitializationError(ValueError):
    pass


def normalize_utc_epoch(value: str) -> str:
    text = value.strip()
    if not text:
        raise OrbitInitializationError("Orbit epoch is required.")

    try:
        manager = SpiceKernelManager(local_kernel_roots=default_local_kernel_roots())
        normalized_utc = manager.et_to_utc(manager.utc_to_et(text), precision=3).replace(" ", "T")
        if normalized_utc.endswith(".000"):
            normalized_utc = normalized_utc[:-4]
        return normalized_utc + "Z"
    except Exception as exc:
        _log.debug("SPICE UTC/ET conversion unavailable, falling back to ISO parsing: %s", exc)

    normalized = text.replace("Z", "+00:00") if text.endswith("Z") else text
    try:
        epoch = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise OrbitInitializationError("Orbit epoch must be an ISO-8601 UTC time.") from exc
    if epoch.tzinfo is None:
        epoch = epoch.replace(tzinfo=timezone.utc)
    epoch = epoch.astimezone(timezone.utc).replace(microsecond=0)
    return epoch.isoformat().replace("+00:00", "Z")


def build_classical_initialization(epoch_utc: str, elements: OrbitalElements) -> OrbitInitializationSettings:
    return OrbitInitializationSettings(
        mode="classical",
        epoch_utc=normalize_utc_epoch(epoch_utc),
        elements=elements.validate(),
    ).validate()


def parse_tle_text(text: str) -> OrbitInitializationSettings:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    line1, line2 = _extract_tle_lines(lines)
    return parse_tle_lines(line1, line2)


def parse_tle_lines(line1: str, line2: str) -> OrbitInitializationSettings:
    line1 = line1.strip()
    line2 = line2.strip()
    if not line1.startswith("1 "):
        raise OrbitInitializationError("TLE line 1 must start with '1 '.")
    if not line2.startswith("2 "):
        raise OrbitInitializationError("TLE line 2 must start with '2 '.")
    if len(line1) < 32 or len(line2) < 63:
        raise OrbitInitializationError("TLE records are malformed.")

    try:
        inclination_deg = float(line2[8:16])
        raan_deg = float(line2[17:25])
        eccentricity = float(f"0.{line2[26:33].strip()}")
        argument_of_periapsis_deg = float(line2[34:42])
        mean_anomaly_deg = float(line2[43:51])
        mean_motion_rev_day = float(line2[52:63])
    except ValueError as exc:
        raise OrbitInitializationError("TLE records are malformed.") from exc

    epoch_utc = _parse_tle_epoch(line1)
    mean_motion_rad_s = mean_motion_rev_day * 2.0 * math.pi / 86400.0
    semi_major_axis_km = (EARTH_MU_KM3_S2 / (mean_motion_rad_s**2)) ** (1.0 / 3.0)
    true_anomaly_deg = _mean_anomaly_to_true_anomaly_deg(mean_anomaly_deg, eccentricity)
    elements = OrbitalElements(
        semi_major_axis_km=semi_major_axis_km,
        eccentricity=eccentricity,
        inclination_deg=inclination_deg,
        raan_deg=raan_deg,
        argument_of_periapsis_deg=argument_of_periapsis_deg,
        true_anomaly_deg=true_anomaly_deg,
        mu_km3_s2=EARTH_MU_KM3_S2,
        central_body_radius_km=EARTH_RADIUS_KM,
        central_body_name="Earth",
    ).validate()
    return OrbitInitializationSettings(
        mode="tle",
        epoch_utc=epoch_utc,
        elements=elements,
        tle_line1=line1,
        tle_line2=line2,
    ).validate()


def load_stk_ephemeris_file(path: str | Path) -> OrbitInitializationSettings:
    path_text = str(path).strip()
    if not path_text:
        raise OrbitInitializationError("STK ephemeris file path is required.")
    source_path = Path(path_text).expanduser()
    text = source_path.read_text(encoding="utf-8", errors="ignore")
    return parse_stk_ephemeris_text(text, source_path=str(source_path.resolve()))


def parse_stk_ephemeris_text(text: str, source_path: str = "") -> OrbitInitializationSettings:
    scenario_epoch_text = ""
    distance_unit = "Meters"
    coordinate_system = ""
    central_body = "Earth"
    data_format = ""
    first_sample: list[float] | None = None
    in_ephemeris = False

    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue

        upper = stripped.upper()
        if upper == "BEGIN EPHEMERIS":
            in_ephemeris = True
            continue
        if upper.startswith("END EPHEMERIS"):
            break
        if not in_ephemeris:
            continue

        if upper.startswith("SCENARIOEPOCH"):
            scenario_epoch_text = stripped.split(None, 1)[1].strip()
            continue
        if upper.startswith("DISTANCEUNIT"):
            distance_unit = stripped.split()[-1]
            continue
        if upper.startswith("CENTRALBODY"):
            central_body = stripped.split(None, 1)[1].strip()
            continue
        if upper.startswith("COORDINATESYSTEM"):
            coordinate_system = stripped.split(None, 1)[1].strip()
            continue
        if upper in {"EPHEMERISTIMEPOSVEL", "TIMEPOSVEL"}:
            data_format = upper
            continue

        if data_format:
            maybe_sample = _try_parse_float_row(stripped)
            if maybe_sample is not None:
                first_sample = maybe_sample
                break

    if not scenario_epoch_text:
        raise OrbitInitializationError("STK ephemeris must define ScenarioEpoch.")
    if first_sample is None or data_format not in {"EPHEMERISTIMEPOSVEL", "TIMEPOSVEL"}:
        raise OrbitInitializationError(
            "STK ephemeris must contain EphemerisTimePosVel or numeric TimePosVel samples."
        )
    if len(first_sample) < 7:
        raise OrbitInitializationError("STK ephemeris must provide position and velocity columns.")
    if central_body.strip().upper() != "EARTH":
        raise OrbitInitializationError("Only Earth-centered STK ephemeris files are currently supported.")

    unit_key = distance_unit.strip().upper().replace(" ", "")
    scale_to_km = _STK_DISTANCE_TO_KM.get(unit_key)
    if scale_to_km is None:
        raise OrbitInitializationError(f"STK ephemeris distance unit '{distance_unit}' is not supported.")

    scenario_epoch = _parse_stk_epoch(scenario_epoch_text)
    epoch = scenario_epoch + timedelta(seconds=float(first_sample[0]))
    epoch_utc = _format_utc(epoch)
    position_km = np.asarray(first_sample[1:4], dtype=float) * scale_to_km
    velocity_km_s = np.asarray(first_sample[4:7], dtype=float) * scale_to_km
    position_j2000_km, velocity_j2000_km_s = _convert_stk_state_to_j2000(
        position_km,
        velocity_km_s,
        coordinate_system=coordinate_system,
        epoch_utc=epoch_utc,
    )
    elements = orbital_elements_from_state_vector(position_j2000_km, velocity_j2000_km_s)

    return OrbitInitializationSettings(
        mode="stk_ephemeris",
        epoch_utc=epoch_utc,
        elements=elements,
        ephemeris_file_path=source_path,
    ).validate()


def _extract_tle_lines(lines: list[str]) -> tuple[str, str]:
    for index, line in enumerate(lines):
        if line.startswith("1 "):
            for candidate in lines[index + 1 :]:
                if candidate.startswith("2 "):
                    return line, candidate
    raise OrbitInitializationError("TLE text must contain line 1 and line 2 records.")


def _parse_tle_epoch(line1: str) -> str:
    try:
        year_short = int(line1[18:20])
        day_of_year = float(line1[20:32])
    except ValueError as exc:
        raise OrbitInitializationError("TLE records are malformed.") from exc

    year = 1900 + year_short if year_short >= 57 else 2000 + year_short
    epoch = datetime(year, 1, 1, tzinfo=timezone.utc) + timedelta(days=day_of_year - 1.0)
    return _format_utc(epoch)


def _mean_anomaly_to_true_anomaly_deg(mean_anomaly_deg: float, eccentricity: float) -> float:
    mean_anomaly_rad = math.radians(mean_anomaly_deg) % (2.0 * math.pi)
    eccentric_anomaly = mean_anomaly_rad if eccentricity < 0.8 else math.pi
    for _ in range(24):
        numerator = eccentric_anomaly - eccentricity * math.sin(eccentric_anomaly) - mean_anomaly_rad
        denominator = 1.0 - eccentricity * math.cos(eccentric_anomaly)
        eccentric_anomaly -= numerator / denominator

    true_anomaly_rad = 2.0 * math.atan2(
        math.sqrt(1.0 + eccentricity) * math.sin(eccentric_anomaly / 2.0),
        math.sqrt(1.0 - eccentricity) * math.cos(eccentric_anomaly / 2.0),
    )
    return math.degrees(true_anomaly_rad) % 360.0


def _convert_stk_state_to_j2000(
    position_km: np.ndarray,
    velocity_km_s: np.ndarray,
    *,
    coordinate_system: str,
    epoch_utc: str,
) -> tuple[np.ndarray, np.ndarray]:
    coordinate_key = coordinate_system.strip().upper().replace(" ", "")
    if not coordinate_key or coordinate_key in _STK_INERTIAL_COORDINATE_SYSTEMS:
        return position_km, velocity_km_s

    source_frame = _STK_FIXED_FRAME_ALIASES.get(coordinate_key)
    if source_frame is None:
        raise OrbitInitializationError("STK ephemeris coordinate system is not supported by SPICE conversion.")

    manager = SpiceKernelManager(local_kernel_roots=default_local_kernel_roots())
    try:
        return manager.transform_state(
            position_km,
            velocity_km_s,
            from_frame=source_frame,
            to_frame="J2000",
            utc=epoch_utc,
        )
    except Exception as exc:
        raise OrbitInitializationError("STK ephemeris frame conversion requires local SPICE kernels.") from exc


def _parse_stk_epoch(value: str) -> datetime:
    candidate = value.strip()
    if "." in candidate:
        prefix, suffix = candidate.split(".", 1)
        digits = "".join(ch for ch in suffix if ch.isdigit())
        trimmed = digits[:6].ljust(6, "0")
        candidate = f"{prefix}.{trimmed}" if trimmed else prefix
    for fmt in _STK_EPOCH_FORMATS:
        try:
            return datetime.strptime(candidate, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise OrbitInitializationError("STK ephemeris ScenarioEpoch is not recognized.")


def _try_parse_float_row(value: str) -> list[float] | None:
    try:
        return [float(token) for token in value.replace(",", " ").split()]
    except ValueError:
        return None


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
