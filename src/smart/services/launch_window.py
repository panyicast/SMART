from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import math
from pathlib import Path
import re
from typing import Any, Callable

import numpy as np

from smart.services.earth_orientation import format_utc, parse_utc

EARTH_RADIUS_M = 6_378_140.0
EARTH_FLATTENING = 1.0 / 298.257223563
GEO_ALTITUDE_M = 35_786_000.0
CONSTRAINT_TYPE_NO_SHADOW = "no_shadow"
CONSTRAINT_TYPE_GROUND_ELEVATION = "ground_elevation"
CONSTRAINT_TYPE_THETA_S = "theta_s"
CONSTRAINT_TYPE_THETA_ST = "theta_st"
CONSTRAINT_TYPE_RELAY_ALPHA_ABS = "relay_alpha_abs"
CONSTRAINT_TYPE_RELAY_BETA_ABS = "relay_beta_abs"
CONSTRAINT_TYPE_INCLINATION = "inclination"
CONSTRAINT_TYPE_GROUND_VISIBLE = "ground_visible"
CONSTRAINT_TYPE_RELAY_VISIBLE = "relay_visible"
CONSTRAINT_TYPE_GROUND_OR_RELAY_VISIBLE = "ground_or_relay_visible"
_SUPPORTED_TABLE_CONSTRAINT_TYPES = {
    CONSTRAINT_TYPE_NO_SHADOW,
    CONSTRAINT_TYPE_GROUND_VISIBLE,
    CONSTRAINT_TYPE_RELAY_VISIBLE,
    CONSTRAINT_TYPE_GROUND_OR_RELAY_VISIBLE,
    CONSTRAINT_TYPE_THETA_S,
}

CONSTRAINT_SCOPE_ALL = "all"
CONSTRAINT_SCOPE_GROUND = "ground"
CONSTRAINT_SCOPE_RELAY = "relay"
BURN_SUN_AXIS_PLUS_Z = "plus_z"
BURN_SUN_AXIS_MINUS_Z = "minus_z"
_CONSTRAINT_TIME_TOKEN_RE = re.compile(
    r"\s*([+-]?)\s*([Tt]\d+_(?:start|end)|(?:\d+(?:\.\d*)?|\.\d+))\s*",
    flags=re.IGNORECASE,
)
_CONSTRAINT_TIME_VARIABLE_RE = re.compile(r"T(\d+)_(start|end)", flags=re.IGNORECASE)
_TRACKING_ASSET_NAME_ALIASES = {
    "厦门站": "Xiamen Station",
    "渭南站": "Weinan Station",
    "佳木斯站": "Jiamusi Station",
    "喀什站": "Kashi Station",
}


@dataclass(frozen=True, slots=True)
class TrackingAsset:
    name: str
    longitude_deg: float
    latitude_deg: float
    altitude_m: float = 0.0
    asset_type: str = "ground"


@dataclass(frozen=True, slots=True)
class LaunchWindowConfig:
    start_utc: str
    end_utc: str
    rocket_flight_time_s: float = 2134.4121
    sample_step_min: float = 10.0
    min_window_duration_min: float = 60.0
    ground_station_min_elevation_deg: float = 5.0
    ground_station_max_theta_st_deg: float = 70.0
    relay_alpha_abs_max_deg: float = 20.0
    relay_beta_abs_max_deg: float = 40.0
    relay_max_theta_st_deg: float = 80.0
    first_orbit_end_min: float = 500.0
    first_orbit_max_shadow_min: float = 45.0
    no_shadow_start_min: float = 45.0
    no_shadow_end_min: float = 500.0
    tracking_min_duration_min: float = 150.0
    tracking_off_nadir_max_deg: float = 80.0
    remote_track_pre_min: float = 180.0
    remote_track_post_min: float = 60.0
    separation_shadow_max_min: float = 15.0
    burn_sun_angle_max_deg: float = 90.0
    burn_sun_axis: str = BURN_SUN_AXIS_MINUS_Z
    inclination_max_deg: float = 6.0
    require_first_orbit_shadow: bool = True
    require_no_shadow_period: bool = True
    require_tracking_arc: bool = True
    require_remote_tracking: bool = True
    require_separation_shadow: bool = True
    require_burn_sun_angle: bool = True
    require_inclination_limit: bool = False
    constraint_rows: list[dict[str, Any]] = field(default_factory=list)
    ground_station_presets: list[dict[str, Any]] = field(default_factory=list)
    relay_satellite_presets: list[dict[str, Any]] = field(default_factory=list)
    custom_ground_stations: list[dict[str, Any]] = field(default_factory=list)
    custom_relay_satellites: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class LaunchWindowResult:
    window_start_utc: str
    window_end_utc: str
    duration_min: float
    first_failure: str
    first_orbit_shadow_min: float
    no_shadow_period_shadow_min: float
    separation_shadow_min: float
    min_burn_sun_margin_deg: float
    max_tracking_gap_min: float
    inclination_deg: float
    window_start_longest_shadow_min: float = 0.0
    window_end_longest_shadow_min: float = 0.0
    window_start_constraint: str = ""
    window_end_constraint: str = ""


@dataclass(frozen=True, slots=True)
class ShadowInterval:
    start_min: int
    end_min: int
    duration_min: int
    exact_start_min: float
    exact_end_min: float


@dataclass(frozen=True, slots=True)
class ManeuverInterval:
    start_min: float
    end_min: float
    delta_deg: float
    dv_direction: int
    maneuver_index: int = 0


def default_ground_station_presets() -> list[dict[str, Any]]:
    return [
        {"enabled": True, "name": "Xiamen Station", "longitude_deg": 117.97, "latitude_deg": 24.64, "altitude_m": 0.0},
        {"enabled": True, "name": "Weinan Station", "longitude_deg": 109.50, "latitude_deg": 34.47, "altitude_m": 0.0},
        {"enabled": False, "name": "Jiamusi Station", "longitude_deg": 130.30, "latitude_deg": 46.80, "altitude_m": 0.0},
        {"enabled": False, "name": "Kashi Station", "longitude_deg": 75.99, "latitude_deg": 39.47, "altitude_m": 0.0},
    ]


def default_relay_satellite_presets() -> list[dict[str, Any]]:
    return [
        {"enabled": False, "name": "TL2-1", "longitude_deg": 77.0, "latitude_deg": 0.0, "altitude_m": GEO_ALTITUDE_M},
        {"enabled": True, "name": "TL2-2", "longitude_deg": 171.0, "latitude_deg": 0.0, "altitude_m": GEO_ALTITUDE_M},
        {"enabled": False, "name": "TL2-3", "longitude_deg": 10.6, "latitude_deg": 0.0, "altitude_m": GEO_ALTITUDE_M},
        {"enabled": False, "name": "TL2-4", "longitude_deg": 80.0, "latitude_deg": 0.0, "altitude_m": GEO_ALTITUDE_M},
        {"enabled": False, "name": "TL2-5", "longitude_deg": 20.4, "latitude_deg": 0.0, "altitude_m": GEO_ALTITUDE_M},
    ]


def default_launch_window_config() -> dict[str, Any]:
    return {
        "start_utc": "2026-05-15T00:00:00Z",
        "end_utc": "2026-06-15T15:59:59Z",
        "rocket_flight_time_s": 2134.4121,
        "sample_step_min": 10.0,
        "min_window_duration_min": 60.0,
        "ground_station_min_elevation_deg": 5.0,
        "ground_station_max_theta_st_deg": 70.0,
        "relay_alpha_abs_max_deg": 20.0,
        "relay_beta_abs_max_deg": 40.0,
        "relay_max_theta_st_deg": 80.0,
        "first_orbit_end_min": 500.0,
        "first_orbit_max_shadow_min": 45.0,
        "no_shadow_start_min": 45.0,
        "no_shadow_end_min": 500.0,
        "tracking_min_duration_min": 150.0,
        "tracking_off_nadir_max_deg": 80.0,
        "remote_track_pre_min": 180.0,
        "remote_track_post_min": 60.0,
        "separation_shadow_max_min": 15.0,
        "burn_sun_angle_max_deg": 90.0,
        "burn_sun_axis": BURN_SUN_AXIS_MINUS_Z,
        "inclination_max_deg": 6.0,
        "require_first_orbit_shadow": True,
        "require_no_shadow_period": True,
        "require_tracking_arc": True,
        "require_remote_tracking": True,
        "require_separation_shadow": True,
        "require_burn_sun_angle": True,
        "require_inclination_limit": False,
        "constraint_rows": default_constraint_rows(),
        "ground_station_presets": default_ground_station_presets(),
        "relay_satellite_presets": default_relay_satellite_presets(),
        "custom_ground_stations": [],
        "custom_relay_satellites": [],
    }


def default_constraint_rows() -> list[dict[str, Any]]:
    return [
        _structured_constraint_row("转移轨道", 15.0, 500.0, CONSTRAINT_TYPE_NO_SHADOW),
        _structured_constraint_row("一远点测控", 0.0, 150.0, CONSTRAINT_TYPE_RELAY_VISIBLE),
        _structured_constraint_row("第一次点火", 1074.0, 1387.0, CONSTRAINT_TYPE_GROUND_VISIBLE),
        _structured_constraint_row("第一次点火", 1255.0, 1327.0, CONSTRAINT_TYPE_THETA_S),
        _structured_constraint_row("第二次点火", 3758.0, 4053.0, CONSTRAINT_TYPE_GROUND_VISIBLE),
        _structured_constraint_row("第二次点火", 3938.0, 3993.0, CONSTRAINT_TYPE_THETA_S),
        _structured_constraint_row("第三次点火", 6830.0, 7142.0, CONSTRAINT_TYPE_GROUND_VISIBLE),
        _structured_constraint_row("第三次点火", 7011.0, 7082.0, CONSTRAINT_TYPE_THETA_S),
        _structured_constraint_row("第四次点火", 9336.0, 9641.0, CONSTRAINT_TYPE_GROUND_VISIBLE),
        _structured_constraint_row("第四次点火", 9516.0, 9581.0, CONSTRAINT_TYPE_THETA_S),
        _structured_constraint_row("第五次点火", 10383.0, 10409.0, CONSTRAINT_TYPE_THETA_S),
    ]


def _constraint_row(
    name: str,
    start_min: object,
    end_min: object,
    angles: str,
    rule: str,
    *,
    enabled: bool = True,
) -> dict[str, Any]:
    return {
        "enabled": bool(enabled),
        "name": name,
        "start_min": _normalize_constraint_time_value(start_min),
        "end_min": _normalize_constraint_time_value(end_min),
        "angles": angles,
        "rule": rule,
    }


def _structured_constraint_row(
    name: str,
    start_min: object,
    end_min: object,
    condition_type: str,
    *,
    enabled: bool = True,
    operator: str = "",
    threshold: float | None = None,
    asset_scope: str = CONSTRAINT_SCOPE_ALL,
) -> dict[str, Any]:
    return {
        "enabled": bool(enabled),
        "name": str(name),
        "start_min": _normalize_constraint_time_value(start_min),
        "end_min": _normalize_constraint_time_value(end_min),
        "condition_type": str(condition_type),
        "operator": str(operator),
        "threshold": None if threshold is None else float(threshold),
        "asset_scope": str(asset_scope or CONSTRAINT_SCOPE_ALL),
    }


def _normalize_constraint_time_value(value: object) -> float | str:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, int | float):
        return float(value)
    text = str(value).strip()
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return text


def _constraint_time_parameters(maneuvers: list["ManeuverInterval"]) -> dict[str, float]:
    parameters: dict[str, float] = {}
    for fallback_index, maneuver in enumerate(maneuvers, start=1):
        maneuver_index = maneuver.maneuver_index if maneuver.maneuver_index > 0 else fallback_index
        parameters[f"T{maneuver_index}_start"] = float(maneuver.start_min)
        parameters[f"T{maneuver_index}_end"] = float(maneuver.end_min)
    return parameters


def _resolve_constraint_time_value(value: object, parameters: dict[str, float]) -> float:
    normalized = _normalize_constraint_time_value(value)
    if isinstance(normalized, float):
        return normalized

    text = normalized.strip()
    total = 0.0
    position = 0
    saw_token = False
    while position < len(text):
        match = _CONSTRAINT_TIME_TOKEN_RE.match(text, position)
        if match is None:
            raise ValueError(f"unsupported expression {text!r}")
        sign_text, token = match.groups()
        sign = -1.0 if sign_text == "-" else 1.0
        variable_match = _CONSTRAINT_TIME_VARIABLE_RE.fullmatch(token)
        if variable_match is not None:
            key = f"T{int(variable_match.group(1))}_{variable_match.group(2).lower()}"
            if key not in parameters:
                raise ValueError(f"unknown parameter {key}")
            amount = parameters[key]
        else:
            amount = float(token)
        total += sign * amount
        position = match.end()
        saw_token = True
    if not saw_token:
        return 0.0
    return total


def normalize_launch_window_config(payload: dict[str, Any] | None) -> dict[str, Any]:
    defaults = default_launch_window_config()
    source = payload if isinstance(payload, dict) else {}
    result = dict(defaults)
    for key in defaults:
        if key not in source:
            continue
        if key in {"start_utc", "end_utc"}:
            result[key] = format_utc(str(source[key]))
        elif key == "constraint_rows":
            result[key] = _normalize_constraint_rows(source.get(key))
        elif key in {"ground_station_presets", "custom_ground_stations"}:
            result[key] = _normalize_tracking_asset_rows(source.get(key), default_ground_station_presets(), asset_type="ground")
        elif key in {"relay_satellite_presets", "custom_relay_satellites"}:
            result[key] = _normalize_tracking_asset_rows(
                source.get(key),
                default_relay_satellite_presets(),
                asset_type="relay",
            )
        elif key == "burn_sun_axis":
            result[key] = _normalize_burn_sun_axis(source[key])
        elif key.startswith("require_"):
            result[key] = bool(source[key])
        else:
            result[key] = float(source[key])
    return result


def _normalize_burn_sun_axis(value: object) -> str:
    text = str(value).strip().lower().replace(" ", "").replace("-", "_")
    if text in {"plus_z", "+z", "z+", "satellite+z", "卫星+z轴", "卫星+z"}:
        return BURN_SUN_AXIS_PLUS_Z
    if text in {"minus_z", "_z", "-z", "z_", "satellite_z", "satellite-z", "卫星_z轴", "卫星-z轴", "卫星_z", "卫星-z"}:
        return BURN_SUN_AXIS_MINUS_Z
    return BURN_SUN_AXIS_MINUS_Z


def _normalize_constraint_rows(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return default_constraint_rows()
    rows: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        if "condition_type" in item:
            rows.append(
                _migrate_structured_constraint_row(
                    _structured_constraint_row(
                        str(item.get("name", "")),
                        item.get("start_min", 0.0),
                        item.get("end_min", 0.0),
                        str(item.get("condition_type", CONSTRAINT_TYPE_THETA_S)),
                        enabled=bool(item.get("enabled", True)),
                        operator=str(item.get("operator", "")),
                        threshold=None if item.get("threshold") in {None, ""} else float(item.get("threshold", 0.0)),
                        asset_scope=str(item.get("asset_scope", CONSTRAINT_SCOPE_ALL)),
                    )
                )
            )
            continue
        rows.extend(
            _legacy_constraint_row_to_structured_rows(
                _constraint_row(
                    str(item.get("name", "")),
                    item.get("start_min", 0.0),
                    item.get("end_min", 0.0),
                    str(item.get("angles", "")),
                    str(item.get("rule", "")),
                    enabled=bool(item.get("enabled", True)),
                )
            )
        )
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[object, ...]] = set()
    for row in rows:
        key = (
            bool(row.get("enabled", True)),
            str(row.get("name", "")),
            _normalize_constraint_time_value(row.get("start_min", 0.0)),
            _normalize_constraint_time_value(row.get("end_min", 0.0)),
            str(row.get("condition_type", "")),
            str(row.get("operator", "")),
            row.get("threshold"),
            str(row.get("asset_scope", "")),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def _migrate_structured_constraint_row(row: dict[str, Any]) -> dict[str, Any]:
    condition_type = str(row.get("condition_type", ""))
    asset_scope = str(row.get("asset_scope", CONSTRAINT_SCOPE_ALL))
    if condition_type in {CONSTRAINT_TYPE_GROUND_ELEVATION}:
        row["condition_type"] = CONSTRAINT_TYPE_GROUND_VISIBLE
        row["asset_scope"] = CONSTRAINT_SCOPE_GROUND
    elif condition_type in {CONSTRAINT_TYPE_RELAY_ALPHA_ABS, CONSTRAINT_TYPE_RELAY_BETA_ABS}:
        row["condition_type"] = CONSTRAINT_TYPE_RELAY_VISIBLE
        row["asset_scope"] = CONSTRAINT_SCOPE_RELAY
    elif condition_type == CONSTRAINT_TYPE_THETA_ST:
        if asset_scope == CONSTRAINT_SCOPE_RELAY:
            row["condition_type"] = CONSTRAINT_TYPE_RELAY_VISIBLE
        elif asset_scope == CONSTRAINT_SCOPE_GROUND:
            row["condition_type"] = CONSTRAINT_TYPE_GROUND_VISIBLE
        else:
            row["condition_type"] = CONSTRAINT_TYPE_GROUND_OR_RELAY_VISIBLE
    condition_type = str(row.get("condition_type", ""))
    if condition_type == CONSTRAINT_TYPE_GROUND_VISIBLE:
        row["asset_scope"] = CONSTRAINT_SCOPE_GROUND
    elif condition_type == CONSTRAINT_TYPE_RELAY_VISIBLE:
        row["asset_scope"] = CONSTRAINT_SCOPE_RELAY
    elif condition_type in {CONSTRAINT_TYPE_NO_SHADOW, CONSTRAINT_TYPE_THETA_S, CONSTRAINT_TYPE_GROUND_OR_RELAY_VISIBLE}:
        row["asset_scope"] = CONSTRAINT_SCOPE_ALL
    row["operator"] = ""
    row["threshold"] = None
    return row


def _legacy_constraint_row_to_structured_rows(item: dict[str, Any]) -> list[dict[str, Any]]:
    name = str(item.get("name", ""))
    start_min = _normalize_constraint_time_value(item.get("start_min", 0.0))
    end_min = _normalize_constraint_time_value(item.get("end_min", 0.0))
    enabled = bool(item.get("enabled", True))
    angles = str(item.get("angles", ""))
    rule = str(item.get("rule", ""))
    compact = rule.replace(" ", "").replace("≤", "<=").replace("≥", ">=")
    rows: list[dict[str, Any]] = []
    if "无地影" in compact:
        rows.append(_structured_constraint_row(name, start_min, end_min, CONSTRAINT_TYPE_NO_SHADOW, enabled=enabled))
    if _extract_angle_lower_limit(compact, ("E", "elevation", "仰角")) is not None:
        rows.append(_structured_constraint_row(name, start_min, end_min, CONSTRAINT_TYPE_GROUND_VISIBLE, enabled=enabled))
    theta_s_limit = _extract_angle_upper_limit(compact, ("θs", "theta_s", "thetas"))
    if theta_s_limit is not None:
        rows.append(
            _structured_constraint_row(
                name,
                start_min,
                end_min,
                CONSTRAINT_TYPE_THETA_S,
                enabled=enabled,
            )
        )
    has_theta_st = _extract_angle_upper_limit(compact, ("θst", "theta_st", "thetast")) is not None
    has_alpha = _extract_abs_angle_upper_limit(compact, ("α", "alpha")) is not None
    has_beta = _extract_abs_angle_upper_limit(compact, ("β", "beta")) is not None
    if has_alpha or has_beta or (has_theta_st and any(token in f"{angles} {rule}" for token in ("α", "β", "alpha", "beta"))):
        rows.append(_structured_constraint_row(name, start_min, end_min, CONSTRAINT_TYPE_RELAY_VISIBLE, enabled=enabled))
    elif has_theta_st:
        rows.append(_structured_constraint_row(name, start_min, end_min, CONSTRAINT_TYPE_GROUND_OR_RELAY_VISIBLE, enabled=enabled))
    inclination_limit = _extract_angle_upper_limit(compact, ("i", "I", "倾角"))
    if inclination_limit is not None:
        rows.append(
            _structured_constraint_row(
                name,
                start_min,
                end_min,
                CONSTRAINT_TYPE_INCLINATION,
                enabled=enabled,
                operator="<=",
                threshold=inclination_limit,
            )
        )
    return rows


def _normalize_tracking_asset_rows(
    value: object,
    defaults: list[dict[str, Any]],
    *,
    asset_type: str,
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return [dict(item) for item in defaults]
    rows: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "enabled": bool(item.get("enabled", True)),
                "name": _normalize_tracking_asset_name(str(item.get("name", "")).strip()),
                "longitude_deg": float(item.get("longitude_deg", 0.0)),
                "latitude_deg": float(item.get("latitude_deg", 0.0)),
                "altitude_m": float(item.get("altitude_m", 0.0 if asset_type == "ground" else GEO_ALTITUDE_M)),
                "asset_type": asset_type,
            }
        )
    return rows


def _normalize_tracking_asset_name(name: str) -> str:
    return _TRACKING_ASSET_NAME_ALIASES.get(name, name)


def config_from_payload(payload: dict[str, Any]) -> LaunchWindowConfig:
    normalized = normalize_launch_window_config(payload)
    return LaunchWindowConfig(**normalized)


def tracking_assets_from_config(config: LaunchWindowConfig) -> list[TrackingAsset]:
    assets: list[TrackingAsset] = []
    seen: set[tuple[str, float, float, float, str]] = set()
    for row in (
        *config.ground_station_presets,
        *config.custom_ground_stations,
        *config.relay_satellite_presets,
        *config.custom_relay_satellites,
    ):
        if not bool(row.get("enabled", True)):
            continue
        name = str(row.get("name", "")).strip()
        if not name:
            continue
        asset_type = str(row.get("asset_type", "ground")).strip().lower() or "ground"
        longitude_deg = float(row.get("longitude_deg", 0.0))
        latitude_deg = float(row.get("latitude_deg", 0.0))
        altitude_m = float(row.get("altitude_m", 0.0 if asset_type == "ground" else GEO_ALTITUDE_M))
        key = (name, round(longitude_deg, 6), round(latitude_deg, 6), round(altitude_m, 3), asset_type)
        if key in seen:
            continue
        seen.add(key)
        assets.append(
            TrackingAsset(
                name=name,
                longitude_deg=longitude_deg,
                latitude_deg=latitude_deg,
                altitude_m=altitude_m,
                asset_type=asset_type,
            )
        )
    return assets


def load_orbit_history_rows(path: str | Path) -> list[dict[str, float | str]]:
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"Maneuver orbit history not found: {file_path}")

    rows: list[dict[str, float | str]] = []
    with file_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for raw in reader:
            row: dict[str, float | str] = {}
            for key, value in raw.items():
                if key == "phase":
                    row[key] = str(value)
                elif value in (None, ""):
                    row[key] = float("nan")
                else:
                    row[key] = float(value)
            rows.append(row)
    if not rows:
        raise ValueError(f"Maneuver orbit history is empty: {file_path}")
    return rows


def compute_launch_windows(
    *,
    orbit_history_csv: str | Path,
    maneuver_strategy: dict[str, Any],
    config: LaunchWindowConfig,
    assets: list[TrackingAsset] | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
) -> tuple[list[LaunchWindowResult], list[dict[str, Any]]]:
    rows = load_orbit_history_rows(orbit_history_csv)
    tracking_assets = assets if assets is not None else tracking_assets_from_config(config)
    if not tracking_assets:
        tracking_assets = default_tracking_assets()
    maneuvers = _maneuver_intervals(maneuver_strategy)
    reference_t0_utc = _reference_t0_utc_from_strategy(maneuver_strategy)
    timeline = _build_timeline(
        rows,
        tracking_assets,
        maneuvers=maneuvers,
        reference_t0_utc=reference_t0_utc,
    )

    start_utc = parse_utc(config.start_utc)
    end_utc = parse_utc(config.end_utc)
    step = timedelta(minutes=max(config.sample_step_min, 1.0))
    if end_utc <= start_utc:
        raise ValueError("Launch-window end time must be after start time.")

    candidate_count = int(math.floor((end_utc - start_utc).total_seconds() / step.total_seconds())) + 1
    samples: list[dict[str, Any]] = []
    launch_utc = start_utc
    sample_index = 0
    while launch_utc <= end_utc:
        sample_index += 1
        t0_utc = launch_utc + timedelta(seconds=config.rocket_flight_time_s)
        ok, metrics, failure = _evaluate_candidate(
            t0_utc,
            timeline=timeline,
            maneuvers=maneuvers,
            config=config,
        )
        samples.append(
            {
                "launch_utc": format_utc(launch_utc),
                "t0_utc": format_utc(t0_utc),
                "ok": ok,
                "failure": failure,
                **metrics,
            }
        )
        if progress_callback is not None:
            progress_callback(sample_index, candidate_count)
        launch_utc += step

    windows = merge_launch_window_samples(samples, config)
    return windows, samples


def compute_shadow_intervals_for_launch(
    *,
    orbit_history_csv: str | Path,
    launch_utc: str | datetime,
    rocket_flight_time_s: float = 2134.4121,
) -> list[ShadowInterval]:
    rows = load_orbit_history_rows(orbit_history_csv)
    timeline = _build_timeline(rows, [])
    t0_utc = parse_utc(launch_utc) + timedelta(seconds=float(rocket_flight_time_s))
    elapsed_min: np.ndarray = timeline["elapsed_min"]
    shadow, shadow_margin_m = _shadow_flags_and_margin(t0_utc, timeline)

    intervals: list[ShadowInterval] = []
    start_index: int | None = None
    for index, flag in enumerate(shadow):
        if flag and start_index is None:
            start_index = index
        if start_index is not None and (not flag or index == len(shadow) - 1):
            end_index = index - 1 if not flag else index
            exact_start = _interpolated_shadow_boundary(
                elapsed_min,
                shadow_margin_m,
                start_index - 1,
                start_index,
                fallback=float(elapsed_min[start_index]),
            )
            exact_end = _interpolated_shadow_boundary(
                elapsed_min,
                shadow_margin_m,
                end_index,
                end_index + 1,
                fallback=float(elapsed_min[end_index]),
            )
            start_min = int(round(float(elapsed_min[start_index])))
            end_sample_index = min(end_index + 1, len(elapsed_min) - 1)
            end_min = int(round(float(elapsed_min[end_sample_index])))
            intervals.append(
                ShadowInterval(
                    start_min=start_min,
                    end_min=end_min,
                    duration_min=end_min - start_min,
                    exact_start_min=exact_start,
                    exact_end_min=exact_end,
                )
            )
            start_index = None
    return intervals


def default_tracking_assets() -> list[TrackingAsset]:
    config = config_from_payload(default_launch_window_config())
    return tracking_assets_from_config(config)


def _build_timeline(
    rows: list[dict[str, float | str]],
    assets: list[TrackingAsset],
    *,
    maneuvers: list[ManeuverInterval] | None = None,
    reference_t0_utc: datetime | None = None,
) -> dict[str, Any]:
    elapsed_min = np.asarray([float(row["elapsed_time_min"]) for row in rows], dtype=np.float64)
    positions = np.asarray(
        [
            _ecef_from_geodetic(
                float(row["subsatellite_longitude_deg"]),
                float(row["subsatellite_latitude_deg"]),
                float(row["subsatellite_altitude_m"]),
            )
            for row in rows
        ],
        dtype=np.float64,
    )
    radial_unit = _normalize(positions)
    body_z_unit = -radial_unit
    phases = [str(row.get("phase", "")) for row in rows]
    inclinations = np.asarray([float(row["inclination_deg"]) for row in rows], dtype=np.float64)
    inertial_states = np.asarray(
        [
            [
                float(row["position_x_m"]),
                float(row["position_y_m"]),
                float(row["position_z_m"]),
                float(row["velocity_x_m_s"]),
                float(row["velocity_y_m_s"]),
                float(row["velocity_z_m_s"]),
            ]
            for row in rows
        ],
        dtype=np.float64,
    )

    # Tracking assets are modeled as fixed ECEF points during launch-window analysis.
    # For relay satellites, the configured longitude/latitude/altitude are treated as
    # fixed geodetic coordinates instead of propagating a time-varying orbit.
    asset_positions = np.asarray(
        [_ecef_from_geodetic(asset.longitude_deg, asset.latitude_deg, asset.altitude_m) for asset in assets],
        dtype=np.float64,
    )
    asset_types = [str(asset.asset_type).strip().lower() or "ground" for asset in assets]
    ground_indices = np.asarray([index for index, asset_type in enumerate(asset_types) if asset_type == "ground"], dtype=np.int64)
    relay_indices = np.asarray([index for index, asset_type in enumerate(asset_types) if asset_type == "relay"], dtype=np.int64)
    asset_los_unit = _los_unit_matrix(positions, asset_positions)
    ground_positions = asset_positions[ground_indices] if ground_indices.size else np.empty((0, 3), dtype=np.float64)
    relay_positions = asset_positions[relay_indices] if relay_indices.size else np.empty((0, 3), dtype=np.float64)
    ground_los_unit = asset_los_unit[:, ground_indices, :] if ground_indices.size else _empty_los_matrix(len(positions))
    relay_los_unit = asset_los_unit[:, relay_indices, :] if relay_indices.size else _empty_los_matrix(len(positions))
    ground_elevation_deg = _ground_elevation_matrix(positions, ground_positions)
    relay_alpha_deg, relay_beta_deg = _relay_target_angles_matrix(positions, relay_positions)
    saved_thrust_plus_z_ecef, saved_thrust_attitude_mask = _saved_thrust_attitude(rows)
    reference_thrust_plus_z_ecef, reference_thrust_attitude_mask = _reference_thrust_attitude(
        elapsed_min,
        phases,
        inertial_states,
        maneuvers or [],
        reference_t0_utc,
    )
    thrust_plus_z_ecef, thrust_attitude_mask = _merge_thrust_attitudes(
        saved_thrust_plus_z_ecef,
        saved_thrust_attitude_mask,
        reference_thrust_plus_z_ecef,
        reference_thrust_attitude_mask,
    )
    attitude_active_mask, attitude_thrust_eci = _precompute_attitude_thrust(
        elapsed_min, phases, inertial_states, maneuvers or []
    )

    return {
        "elapsed_min": elapsed_min,
        "positions": positions,
        "body_z_unit": body_z_unit,
        "phases": phases,
        "inclinations": inclinations,
        "inertial_states": inertial_states,
        "asset_positions": asset_positions,
        "asset_types": asset_types,
        "ground_indices": ground_indices,
        "relay_indices": relay_indices,
        "asset_los_unit": asset_los_unit,
        "ground_los_unit": ground_los_unit,
        "relay_los_unit": relay_los_unit,
        "ground_elevation_deg": ground_elevation_deg,
        "relay_alpha_deg": relay_alpha_deg,
        "relay_beta_deg": relay_beta_deg,
        "thrust_plus_z_ecef": thrust_plus_z_ecef,
        "thrust_attitude_mask": thrust_attitude_mask,
        "attitude_active_mask": attitude_active_mask,
        "attitude_thrust_eci": attitude_thrust_eci,
    }


def _candidate_constraint_plan(config: LaunchWindowConfig) -> tuple[set[str], bool]:
    enabled_types: set[str] = set()
    has_table_checks = False
    for row in config.constraint_rows:
        if not bool(row.get("enabled", True)):
            has_table_checks = True
            continue
        condition_type = str(row.get("condition_type", "")).strip()
        if condition_type in _SUPPORTED_TABLE_CONSTRAINT_TYPES:
            has_table_checks = True
            enabled_types.add(condition_type)
    return enabled_types, not has_table_checks


def _evaluate_candidate(
    t0_utc: datetime,
    *,
    timeline: dict[str, Any],
    maneuvers: list[ManeuverInterval],
    config: LaunchWindowConfig,
) -> tuple[bool, dict[str, Any], str]:
    elapsed_min: np.ndarray = timeline["elapsed_min"]
    inclinations: np.ndarray = timeline["inclinations"]

    sun_vectors = _sun_unit_ecef_for_elapsed(t0_utc, elapsed_min)
    shadow, _shadow_margin_m = _shadow_flags_and_margin(t0_utc, timeline, sun_vectors=sun_vectors)
    enabled_table_types, uses_legacy_checks = _candidate_constraint_plan(config)
    needs_burn_sun_angle = CONSTRAINT_TYPE_THETA_S in enabled_table_types or (
        uses_legacy_checks and config.require_burn_sun_angle
    )
    needs_tracking_coverage = uses_legacy_checks and (config.require_tracking_arc or config.require_remote_tracking)
    needs_ground_coverage = (
        CONSTRAINT_TYPE_GROUND_VISIBLE in enabled_table_types
        or CONSTRAINT_TYPE_GROUND_OR_RELAY_VISIBLE in enabled_table_types
        or needs_tracking_coverage
    )
    needs_relay_coverage = (
        CONSTRAINT_TYPE_RELAY_VISIBLE in enabled_table_types
        or CONSTRAINT_TYPE_GROUND_OR_RELAY_VISIBLE in enabled_table_types
        or needs_tracking_coverage
    )

    theta_s_deg = np.full(elapsed_min.shape, np.nan, dtype=np.float64)
    if needs_burn_sun_angle:
        body_plus_z_ecef = _body_plus_z_ecef_for_attitude(t0_utc, timeline, maneuvers, sun_vectors)
        theta_s_deg = _theta_s_deg_from_body_plus_z(body_plus_z_ecef, sun_vectors, config.burn_sun_axis)

    theta_st_reference_ecef = -sun_vectors
    all_theta_st_deg = np.empty((len(elapsed_min), 0), dtype=np.float64)
    ground_elevation_deg = timeline["ground_elevation_deg"]
    ground_theta_st_deg = np.empty((len(elapsed_min), 0), dtype=np.float64)
    if needs_ground_coverage:
        ground_theta_st_deg = _theta_st_matrix_from_los(timeline["ground_los_unit"], theta_st_reference_ecef)
    relay_theta_st_deg = np.empty((len(elapsed_min), 0), dtype=np.float64)
    if needs_relay_coverage:
        relay_theta_st_deg = _theta_st_matrix_from_los(timeline["relay_los_unit"], theta_st_reference_ecef)
    relay_alpha_deg = timeline["relay_alpha_deg"]
    relay_beta_deg = timeline["relay_beta_deg"]
    ground_covered = np.zeros(len(elapsed_min), dtype=bool)
    if needs_ground_coverage:
        ground_covered = _asset_matrix_any(
            (ground_elevation_deg >= config.ground_station_min_elevation_deg)
            & (ground_theta_st_deg <= config.ground_station_max_theta_st_deg)
        )
    relay_covered = np.zeros(len(elapsed_min), dtype=bool)
    if needs_relay_coverage:
        relay_covered = _asset_matrix_any(
            (np.abs(relay_alpha_deg) <= config.relay_alpha_abs_max_deg)
            & (np.abs(relay_beta_deg) <= config.relay_beta_abs_max_deg)
            & (relay_theta_st_deg <= config.relay_max_theta_st_deg)
        )
    covered = ground_covered | relay_covered

    first_orbit_shadow_min = _duration_where(elapsed_min, shadow, 0.0, config.first_orbit_end_min)
    longest_shadow_min = _longest_true_duration(elapsed_min, shadow)
    no_shadow_period_shadow_min = _duration_where(
        elapsed_min,
        shadow,
        config.no_shadow_start_min,
        config.no_shadow_end_min,
    )
    constraint_time_parameters = _constraint_time_parameters(maneuvers)
    table_no_shadow_duration_min = _no_shadow_rows_shadow_duration(
        config.constraint_rows,
        elapsed_min,
        shadow,
        constraint_time_parameters=constraint_time_parameters,
    )
    if table_no_shadow_duration_min is not None:
        no_shadow_period_shadow_min = table_no_shadow_duration_min
    separation_shadow_min = _leading_duration_where(elapsed_min, shadow, 0.0)
    min_burn_sun_margin_deg = float("inf")
    if needs_burn_sun_angle:
        min_burn_sun_margin_deg = _min_burn_sun_margin(
            elapsed_min,
            theta_s_deg,
            maneuvers,
            config.burn_sun_angle_max_deg,
        )
    max_tracking_gap_min = 0.0
    if needs_ground_coverage or needs_relay_coverage:
        max_tracking_gap_min = _max_gap_without_coverage(elapsed_min, covered)
    inclination_deg = float(np.nanmax(inclinations)) if inclinations.size else 0.0

    checks = _constraint_checks_from_rows(
        config.constraint_rows,
        elapsed_min=elapsed_min,
        shadow=shadow,
        panel_sun_angles_deg=theta_s_deg,
        all_theta_st_deg=all_theta_st_deg,
        ground_elevation_deg=ground_elevation_deg,
        ground_theta_st_deg=ground_theta_st_deg,
        relay_theta_st_deg=relay_theta_st_deg,
        relay_alpha_deg=relay_alpha_deg,
        relay_beta_deg=relay_beta_deg,
        ground_covered=ground_covered,
        relay_covered=relay_covered,
        covered=covered,
        inclination_deg=inclination_deg,
        burn_sun_angle_max_deg=config.burn_sun_angle_max_deg,
        constraint_time_parameters=constraint_time_parameters,
    )
    if not checks:
        checks = [
            _constraint_check(
                config.require_first_orbit_shadow,
                first_orbit_shadow_min <= config.first_orbit_max_shadow_min,
                "first_orbit_shadow",
                "第一圈地影",
            ),
            _constraint_check(
                config.require_no_shadow_period,
                no_shadow_period_shadow_min <= 1e-9,
                "no_shadow_period",
                "无地影时段",
            ),
            _constraint_check(
                config.require_tracking_arc,
                _has_contiguous_coverage(
                    elapsed_min,
                    covered,
                    config.tracking_min_duration_min,
                ),
                "tracking_arc",
                "测控连续弧段",
            ),
            _constraint_check(
                config.require_remote_tracking,
                _remote_tracking_ok(
                    elapsed_min,
                    covered,
                    maneuvers,
                    config,
                ),
                "remote_tracking",
                "远距离测控",
            ),
            _constraint_check(
                config.require_separation_shadow,
                separation_shadow_min <= config.separation_shadow_max_min,
                "separation_shadow",
                "分离地影",
            ),
            _constraint_check(
                config.require_burn_sun_angle,
                min_burn_sun_margin_deg >= 0.0,
                "burn_sun_angle",
                "点火太阳角",
            ),
            _constraint_check(
                config.require_inclination_limit,
                inclination_deg <= config.inclination_max_deg,
                "inclination",
                "倾角限制",
            ),
        ]

    metrics = {
        "first_orbit_shadow_min": first_orbit_shadow_min,
        "no_shadow_period_shadow_min": no_shadow_period_shadow_min,
        "separation_shadow_min": separation_shadow_min,
        "min_burn_sun_margin_deg": min_burn_sun_margin_deg,
        "max_tracking_gap_min": max_tracking_gap_min,
        "inclination_deg": inclination_deg,
        "longest_shadow_min": longest_shadow_min,
        "constraint_results": [
            {
                "name": str(check["label"]),
                "passed": bool(check["passed"]),
                "enabled": bool(check["enabled"]),
            }
            for check in checks
        ],
    }

    for check in checks:
        if bool(check["enabled"]) and not bool(check["passed"]):
            return False, metrics, str(check["name"])

    return True, metrics, ""


def _constraint_checks_from_rows(
    rows: list[dict[str, Any]],
    *,
    elapsed_min: np.ndarray,
    shadow: np.ndarray,
    panel_sun_angles_deg: np.ndarray,
    all_theta_st_deg: np.ndarray,
    ground_elevation_deg: np.ndarray,
    ground_theta_st_deg: np.ndarray,
    relay_theta_st_deg: np.ndarray,
    relay_alpha_deg: np.ndarray,
    relay_beta_deg: np.ndarray,
    ground_covered: np.ndarray,
    relay_covered: np.ndarray,
    covered: np.ndarray,
    inclination_deg: float,
    burn_sun_angle_max_deg: float,
    constraint_time_parameters: dict[str, float],
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        enabled = bool(row.get("enabled", True))
        name = str(row.get("name", f"constraint_{index + 1}")) or f"constraint_{index + 1}"
        label = _constraint_row_chart_label(row, name)
        if not enabled:
            checks.append(_constraint_check(False, True, name, label))
            continue
        try:
            start_min = _resolve_constraint_time_value(row.get("start_min", 0.0), constraint_time_parameters)
            end_min = _resolve_constraint_time_value(row.get("end_min", start_min), constraint_time_parameters)
        except ValueError as exc:
            raise ValueError(f"Invalid time expression in constraint {name!r}: {exc}") from exc
        if end_min < start_min:
            start_min, end_min = end_min, start_min
        passed, supported = _evaluate_constraint_entry(
            row,
            elapsed_min=elapsed_min,
            shadow=shadow,
            panel_sun_angles_deg=panel_sun_angles_deg,
            all_theta_st_deg=all_theta_st_deg,
            ground_elevation_deg=ground_elevation_deg,
            ground_theta_st_deg=ground_theta_st_deg,
            relay_theta_st_deg=relay_theta_st_deg,
            relay_alpha_deg=relay_alpha_deg,
            relay_beta_deg=relay_beta_deg,
            ground_covered=ground_covered,
            relay_covered=relay_covered,
            covered=covered,
            inclination_deg=inclination_deg,
            start_min=start_min,
            end_min=end_min,
            burn_sun_angle_max_deg=burn_sun_angle_max_deg,
        )
        if supported:
            checks.append(_constraint_check(True, passed, name, label))
    return checks


def _constraint_check(enabled: bool, passed: bool, name: str, label: str | None = None) -> dict[str, Any]:
    return {
        "enabled": bool(enabled),
        "passed": bool(passed),
        "name": str(name),
        "label": str(label or name),
    }


def _constraint_row_chart_label(row: dict[str, Any], name: str) -> str:
    condition_label = _constraint_condition_label(str(row.get("condition_type", "")))
    return f"{name} - {condition_label}" if condition_label else name


def _constraint_condition_label(condition_type: str) -> str:
    return {
        CONSTRAINT_TYPE_NO_SHADOW: "无地影",
        CONSTRAINT_TYPE_GROUND_VISIBLE: "地面站可见",
        CONSTRAINT_TYPE_RELAY_VISIBLE: "中继星可见",
        CONSTRAINT_TYPE_GROUND_OR_RELAY_VISIBLE: "地面站或中继星可见",
        CONSTRAINT_TYPE_THETA_S: "太阳角",
        CONSTRAINT_TYPE_INCLINATION: "倾角",
        CONSTRAINT_TYPE_GROUND_ELEVATION: "地面站可见",
        CONSTRAINT_TYPE_THETA_ST: "天线覆盖角",
        CONSTRAINT_TYPE_RELAY_ALPHA_ABS: "中继 alpha",
        CONSTRAINT_TYPE_RELAY_BETA_ABS: "中继 beta",
    }.get(condition_type, "")


def _evaluate_constraint_entry(
    row: dict[str, Any],
    *,
    elapsed_min: np.ndarray,
    shadow: np.ndarray,
    panel_sun_angles_deg: np.ndarray,
    all_theta_st_deg: np.ndarray,
    ground_elevation_deg: np.ndarray,
    ground_theta_st_deg: np.ndarray,
    relay_theta_st_deg: np.ndarray,
    relay_alpha_deg: np.ndarray,
    relay_beta_deg: np.ndarray,
    ground_covered: np.ndarray,
    relay_covered: np.ndarray,
    covered: np.ndarray,
    inclination_deg: float,
    start_min: float,
    end_min: float,
    burn_sun_angle_max_deg: float,
) -> tuple[bool, bool]:
    condition_type = str(row.get("condition_type", "")).strip()
    mask = _time_mask(elapsed_min, start_min, end_min)
    if condition_type == CONSTRAINT_TYPE_NO_SHADOW:
        return _duration_where(elapsed_min, shadow, start_min, end_min) <= 1e-9, True
    if not mask.any():
        return True, True
    if condition_type == CONSTRAINT_TYPE_THETA_S:
        return float(np.nanmax(panel_sun_angles_deg[mask])) <= burn_sun_angle_max_deg, True
    if condition_type == CONSTRAINT_TYPE_GROUND_VISIBLE:
        return bool(np.all(ground_covered[mask])), True
    if condition_type == CONSTRAINT_TYPE_RELAY_VISIBLE:
        return bool(np.all(relay_covered[mask])), True
    if condition_type == CONSTRAINT_TYPE_GROUND_OR_RELAY_VISIBLE:
        return bool(np.all(covered[mask])), True
    return True, False


def _compare_series(value: float, operator: str, threshold: float) -> bool:
    if operator == "<=":
        return float(value) <= threshold
    if operator == ">=":
        return float(value) >= threshold
    return False


def _time_mask(elapsed_min: np.ndarray, start_min: float, end_min: float) -> np.ndarray:
    return (elapsed_min >= start_min) & (elapsed_min <= end_min)


def _extract_angle_upper_limit(text: str, names: tuple[str, ...]) -> float | None:
    for name in names:
        match = re.search(rf"{re.escape(name)}<=(-?\d+(?:\.\d+)?)", text, flags=re.IGNORECASE)
        if match:
            return float(match.group(1))
    return None


def _extract_angle_lower_limit(text: str, names: tuple[str, ...]) -> float | None:
    for name in names:
        match = re.search(rf"{re.escape(name)}>=(-?\d+(?:\.\d+)?)", text, flags=re.IGNORECASE)
        if match:
            return float(match.group(1))
    return None


def _extract_abs_angle_upper_limit(text: str, names: tuple[str, ...]) -> float | None:
    for name in names:
        patterns = (
            rf"\|{re.escape(name)}\|<=(-?\d+(?:\.\d+)?)",
            rf"{re.escape(name)}<=(-?\d+(?:\.\d+)?)",
        )
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                return float(match.group(1))
    return None


def merge_launch_window_samples(samples: list[dict[str, Any]], config: LaunchWindowConfig) -> list[LaunchWindowResult]:
    return _merge_pass_samples(samples, config)


def _merge_pass_samples(samples: list[dict[str, Any]], config: LaunchWindowConfig) -> list[LaunchWindowResult]:
    windows: list[LaunchWindowResult] = []
    step_min = max(config.sample_step_min, 1.0)

    # 把所有 launch_utc 一次性解析，避免内层循环重复解析造成 O(N²) 行为。
    parsed_launch_utc: list[datetime] = [
        parse_utc(str(sample["launch_utc"])) for sample in samples
    ]

    def failure_label(sample: dict[str, Any] | None) -> str:
        if sample is None or bool(sample.get("ok")):
            return ""
        return str(sample.get("failure") or "")

    def longest_shadow(sample: dict[str, Any] | None) -> float:
        if sample is None:
            return 0.0
        return float(sample.get("longest_shadow_min", sample.get("first_orbit_shadow_min", 0.0)))

    def append_window(
        active_indices: list[int],
        *,
        leading_constraint: str,
        trailing_sample: dict[str, Any] | None,
        trailing_constraint: str,
    ) -> None:
        active = [samples[i] for i in active_indices]
        start = parsed_launch_utc[active_indices[0]]
        end = parsed_launch_utc[active_indices[-1]] + timedelta(minutes=step_min)
        duration = (end - start).total_seconds() / 60.0
        windows.append(
            LaunchWindowResult(
                window_start_utc=format_utc(start),
                window_end_utc=format_utc(end),
                duration_min=duration,
                first_failure=leading_constraint,
                first_orbit_shadow_min=float(max(item["first_orbit_shadow_min"] for item in active)),
                no_shadow_period_shadow_min=float(max(item["no_shadow_period_shadow_min"] for item in active)),
                separation_shadow_min=float(max(item["separation_shadow_min"] for item in active)),
                min_burn_sun_margin_deg=float(min(item["min_burn_sun_margin_deg"] for item in active)),
                max_tracking_gap_min=float(max(item["max_tracking_gap_min"] for item in active)),
                inclination_deg=float(max(item["inclination_deg"] for item in active)),
                window_start_longest_shadow_min=longest_shadow(active[0]),
                window_end_longest_shadow_min=longest_shadow(trailing_sample if trailing_sample is not None else active[-1]),
                window_start_constraint=leading_constraint,
                window_end_constraint=trailing_constraint,
            )
        )

    index = 0
    while index < len(samples):
        if not samples[index]["ok"]:
            index += 1
            continue

        start_index = index
        active_indices = [index]
        index += 1
        while index < len(samples):
            previous_time = parsed_launch_utc[active_indices[-1]]
            sample_time = parsed_launch_utc[index]
            contiguous = abs((sample_time - previous_time).total_seconds() / 60.0 - step_min) < 1e-6
            if not samples[index]["ok"] or not contiguous:
                break
            active_indices.append(index)
            index += 1

        leading_sample = samples[start_index - 1] if start_index > 0 else None
        trailing_sample = samples[index] if index < len(samples) else None
        append_window(
            active_indices,
            leading_constraint=failure_label(leading_sample),
            trailing_sample=trailing_sample,
            trailing_constraint=failure_label(trailing_sample),
        )
    return windows


def _maneuver_intervals(strategy: dict[str, Any]) -> list[ManeuverInterval]:
    intervals: list[ManeuverInterval] = []
    maneuvers = strategy.get("maneuvers", [])
    if not isinstance(maneuvers, list):
        return intervals
    for item in maneuvers:
        if not isinstance(item, dict):
            continue
        start_min = float(item.get("Tn_start_min", 0.0))
        duration_min = float(item.get("burn_duration_min", 0.0))
        if duration_min > 0.0:
            maneuver_index = int(item.get("maneuver_index", len(intervals) + 1))
            intervals.append(
                ManeuverInterval(
                    start_min=start_min,
                    end_min=start_min + duration_min,
                    delta_deg=float(item.get("delta_deg", 0.0)),
                    dv_direction=_coerce_dv_direction(item.get("dv_direction", 1)),
                    maneuver_index=maneuver_index,
                )
            )
    return intervals


def _reference_t0_utc_from_strategy(strategy: dict[str, Any]) -> datetime | None:
    raw_epoch = strategy.get("t0_epoch") if isinstance(strategy, dict) else None
    if raw_epoch in (None, "") and isinstance(strategy, dict):
        raw_epoch = strategy.get("to_epoch")
    if raw_epoch in (None, ""):
        return None
    return parse_utc(str(raw_epoch))


def _coerce_dv_direction(value: object) -> int:
    try:
        parsed = int(float(value))
    except (TypeError, ValueError):
        return 1
    return parsed if parsed in {-1, 1} else 1


def _reference_thrust_attitude(
    elapsed_min: np.ndarray,
    phases: list[str],
    inertial_states: np.ndarray,
    maneuvers: list[ManeuverInterval],
    reference_t0_utc: datetime | None,
) -> tuple[np.ndarray | None, np.ndarray]:
    mask = np.zeros(len(elapsed_min), dtype=bool)
    if reference_t0_utc is None:
        return None, mask

    plus_z = np.zeros((len(elapsed_min), 3), dtype=np.float64)
    for index, (minute, phase) in enumerate(zip(elapsed_min, phases, strict=True)):
        maneuver = _maneuver_for_time(float(minute), maneuvers)
        if phase not in {"settle", "orbit_control"} and maneuver is None:
            continue
        if maneuver is None:
            maneuver = ManeuverInterval(float(minute), float(minute), 0.0, 1)
        direction_eci = _thrust_direction_for_state(
            inertial_states[index],
            maneuver.delta_deg,
            maneuver.dv_direction,
        )
        epoch = reference_t0_utc + timedelta(minutes=float(minute))
        plus_z[index] = _eci_direction_to_ecef(direction_eci, epoch)
        mask[index] = True
    if not bool(mask.any()):
        return None, mask
    plus_z[mask] = _normalize(plus_z[mask])
    return plus_z, mask


def _saved_thrust_attitude(rows: list[dict[str, float | str]]) -> tuple[np.ndarray | None, np.ndarray]:
    mask = np.zeros(len(rows), dtype=bool)
    plus_z = np.zeros((len(rows), 3), dtype=np.float64)
    for index, row in enumerate(rows):
        longitude_deg = _optional_float(row.get("thrust_longitude_deg"))
        latitude_deg = _optional_float(row.get("thrust_latitude_deg"))
        if longitude_deg is None or latitude_deg is None:
            continue
        plus_z[index] = _unit_from_spherical_lon_lat(longitude_deg, latitude_deg)
        mask[index] = True
    if not bool(mask.any()):
        return None, mask
    return plus_z, mask


def _merge_thrust_attitudes(
    saved_plus_z: np.ndarray | None,
    saved_mask: np.ndarray,
    reference_plus_z: np.ndarray | None,
    reference_mask: np.ndarray,
) -> tuple[np.ndarray | None, np.ndarray]:
    if saved_plus_z is None:
        return reference_plus_z, reference_mask
    plus_z = saved_plus_z.copy()
    mask = saved_mask.copy()
    if reference_plus_z is not None and bool(reference_mask.any()):
        missing_reference_mask = reference_mask & ~mask
        plus_z[missing_reference_mask] = reference_plus_z[missing_reference_mask]
        mask |= reference_mask
    return plus_z, mask


def _optional_float(value: object) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _unit_from_spherical_lon_lat(longitude_deg: float, latitude_deg: float) -> np.ndarray:
    longitude = math.radians(float(longitude_deg))
    latitude = math.radians(float(latitude_deg))
    cos_latitude = math.cos(latitude)
    return np.asarray(
        [
            cos_latitude * math.cos(longitude),
            cos_latitude * math.sin(longitude),
            math.sin(latitude),
        ],
        dtype=np.float64,
    )


def _body_plus_z_ecef_for_attitude(
    t0_utc: datetime,
    timeline: dict[str, Any],
    maneuvers: list[ManeuverInterval],
    sun_vectors_ecef: np.ndarray,
) -> np.ndarray:
    plus_z = -sun_vectors_ecef.copy()
    reference_plus_z: np.ndarray | None = timeline.get("thrust_plus_z_ecef")
    reference_mask: np.ndarray | None = timeline.get("thrust_attitude_mask")
    if reference_plus_z is not None and reference_mask is not None and bool(reference_mask.any()):
        plus_z[reference_mask] = reference_plus_z[reference_mask]
        return _normalize(plus_z)

    active_mask: np.ndarray | None = timeline.get("attitude_active_mask")
    if active_mask is None or not bool(active_mask.any()):
        # 无变轨/姿态需求 → 默认即指向反阳方向。
        return _normalize(plus_z)

    elapsed_min: np.ndarray = timeline["elapsed_min"]
    thrust_eci: np.ndarray = timeline["attitude_thrust_eci"]
    eci_active = thrust_eci[active_mask]
    minutes_active = elapsed_min[active_mask]
    gmst_active = _gmst_rad_array(t0_utc, minutes_active)
    cos_g = np.cos(gmst_active)
    sin_g = np.sin(gmst_active)
    x = eci_active[:, 0]
    y = eci_active[:, 1]
    z = eci_active[:, 2]
    plus_z[active_mask] = np.column_stack(
        (cos_g * x + sin_g * y, -sin_g * x + cos_g * y, z)
    )
    return _normalize(plus_z)


def _precompute_attitude_thrust(
    elapsed_min: np.ndarray,
    phases: list[str],
    inertial_states: np.ndarray,
    maneuvers: list[ManeuverInterval],
) -> tuple[np.ndarray, np.ndarray]:
    """在 timeline 构建阶段一次性算出每个采样点的姿态推力方向（ECI）。

    返回 ``(active_mask, thrust_eci)``，其中 ``thrust_eci`` 仅在 ``active_mask``
    位置上有效；非激活位置由 ``_body_plus_z_ecef_for_attitude`` 的反阳默认值
    填充。
    """

    n = int(elapsed_min.size)
    active = np.zeros(n, dtype=bool)
    thrust_eci = np.zeros((n, 3), dtype=np.float64)
    if n == 0:
        return active, thrust_eci

    # 阶段过滤：与原 _maneuver_for_time + 阶段判定保持完全一致语义。
    phase_array = np.asarray(phases, dtype=object)
    needs_attitude_phase = np.isin(phase_array, np.asarray(["settle", "orbit_control"]))

    # 把 maneuver 列表向量化成区间映射；保留首匹配语义。
    maneuver_idx = np.full(n, -1, dtype=np.int64)
    for j, maneuver in enumerate(maneuvers):
        in_range = (elapsed_min >= maneuver.start_min - 1e-9) & (
            elapsed_min <= maneuver.end_min + 1e-9
        )
        new_assignments = in_range & (maneuver_idx == -1)
        maneuver_idx[new_assignments] = j

    has_maneuver = maneuver_idx >= 0
    active = needs_attitude_phase | has_maneuver
    if not bool(active.any()):
        return active, thrust_eci

    for index in np.flatnonzero(active):
        midx = int(maneuver_idx[index])
        if midx >= 0:
            maneuver = maneuvers[midx]
            delta_deg = maneuver.delta_deg
            dv_direction = maneuver.dv_direction
        else:
            delta_deg = 0.0
            dv_direction = 1
        thrust_eci[index] = _thrust_direction_for_state(
            inertial_states[index],
            delta_deg,
            dv_direction,
        )
    return active, thrust_eci


def _gmst_rad_array(t0_utc: datetime, elapsed_min: np.ndarray) -> np.ndarray:
    """向量化版 GMST：与 ``_gmst_rad`` 数学公式严格一致，但接受 elapsed_min 数组。"""

    jd = _julian_date(t0_utc) + elapsed_min / 1440.0
    t = (jd - 2451545.0) / 36525.0
    gmst_deg = (
        280.46061837
        + 360.98564736629 * (jd - 2451545.0)
        + 0.000387933 * t * t
        - (t * t * t) / 38710000.0
    )
    return np.deg2rad(np.mod(gmst_deg, 360.0))


def _maneuver_for_time(minute: float, maneuvers: list[ManeuverInterval]) -> ManeuverInterval | None:
    for maneuver in maneuvers:
        if maneuver.start_min - 1e-9 <= minute <= maneuver.end_min + 1e-9:
            return maneuver
    return None


def _theta_st_matrix(
    positions_ecef_m: np.ndarray,
    plus_z_ecef: np.ndarray,
    asset_positions_ecef_m: np.ndarray,
) -> np.ndarray:
    return _theta_st_matrix_from_los(_los_unit_matrix(positions_ecef_m, asset_positions_ecef_m), plus_z_ecef)


def _los_unit_matrix(positions_ecef_m: np.ndarray, asset_positions_ecef_m: np.ndarray) -> np.ndarray:
    if len(asset_positions_ecef_m) == 0:
        return _empty_los_matrix(len(positions_ecef_m))
    return _normalize(asset_positions_ecef_m[np.newaxis, :, :] - positions_ecef_m[:, np.newaxis, :])


def _empty_los_matrix(row_count: int) -> np.ndarray:
    return np.empty((row_count, 0, 3), dtype=np.float64)


def _theta_st_matrix_from_los(los_unit: np.ndarray, reference_unit_ecef: np.ndarray) -> np.ndarray:
    if los_unit.ndim != 3 or los_unit.shape[1] == 0:
        return np.empty((len(reference_unit_ecef), 0), dtype=np.float64)
    cos_theta = np.sum(los_unit * reference_unit_ecef[:, np.newaxis, :], axis=2)
    return np.degrees(np.arccos(np.clip(cos_theta, -1.0, 1.0)))


def _theta_s_deg_from_body_plus_z(
    body_plus_z_ecef: np.ndarray,
    sun_vectors_ecef: np.ndarray,
    burn_sun_axis: str = BURN_SUN_AXIS_MINUS_Z,
) -> np.ndarray:
    reference_axis = body_plus_z_ecef if burn_sun_axis == BURN_SUN_AXIS_PLUS_Z else -body_plus_z_ecef
    cos_theta = np.clip(np.sum(reference_axis * sun_vectors_ecef, axis=1), -1.0, 1.0)
    return np.degrees(np.arccos(cos_theta))


def _best_theta_st_deg(theta_st_deg: np.ndarray) -> np.ndarray:
    if theta_st_deg.size == 0:
        return np.full(theta_st_deg.shape[0] if theta_st_deg.ndim == 2 else 0, 180.0, dtype=np.float64)
    return np.nanmin(theta_st_deg, axis=1)


def _asset_matrix_any(values: np.ndarray) -> np.ndarray:
    if values.ndim != 2 or values.shape[1] == 0:
        return np.zeros(values.shape[0] if values.ndim >= 1 else 0, dtype=bool)
    return np.any(values, axis=1)


def _ground_elevation_matrix(
    positions_ecef_m: np.ndarray,
    ground_positions_ecef_m: np.ndarray,
) -> np.ndarray:
    if len(ground_positions_ecef_m) == 0:
        return np.empty((len(positions_ecef_m), 0), dtype=np.float64)
    los_unit = _normalize(positions_ecef_m[:, np.newaxis, :] - ground_positions_ecef_m[np.newaxis, :, :])
    up_unit = _normalize(ground_positions_ecef_m)
    return np.degrees(np.arcsin(np.clip(np.sum(los_unit * up_unit[np.newaxis, :, :], axis=2), -1.0, 1.0)))


def _relay_target_angles_matrix(
    positions_ecef_m: np.ndarray,
    relay_positions_ecef_m: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    if len(relay_positions_ecef_m) == 0:
        empty = np.empty((len(positions_ecef_m), 0), dtype=np.float64)
        return empty, empty
    relay_plus_z = _normalize(-relay_positions_ecef_m)
    relay_plus_x = _relay_velocity_direction_ecef(relay_positions_ecef_m)
    relay_plus_y = _normalize(np.cross(relay_plus_z, relay_plus_x))
    los_unit = _normalize(positions_ecef_m[:, np.newaxis, :] - relay_positions_ecef_m[np.newaxis, :, :])
    x = np.sum(los_unit * relay_plus_x[np.newaxis, :, :], axis=2)
    y = np.sum(los_unit * relay_plus_y[np.newaxis, :, :], axis=2)
    z = np.sum(los_unit * relay_plus_z[np.newaxis, :, :], axis=2)
    alpha = np.degrees(np.arctan2(-y, np.sqrt(np.maximum(x * x + z * z, 1e-12))))
    beta = np.degrees(np.arctan2(x, z))
    return alpha, beta


def _relay_velocity_direction_ecef(relay_positions_ecef_m: np.ndarray) -> np.ndarray:
    radial = _normalize(relay_positions_ecef_m)
    earth_axis = np.tile(np.asarray([[0.0, 0.0, 1.0]], dtype=np.float64), (len(relay_positions_ecef_m), 1))
    east = np.cross(earth_axis, radial)
    norms = np.linalg.norm(east, axis=1, keepdims=True)
    fallback = np.cross(np.tile(np.asarray([[1.0, 0.0, 0.0]], dtype=np.float64), (len(relay_positions_ecef_m), 1)), radial)
    east = np.where(norms > 1e-12, east, fallback)
    return _normalize(east)


def _asset_window_rule_ok(
    mask: np.ndarray,
    *,
    theta_st_deg: np.ndarray,
    elevation_deg: np.ndarray | None,
    alpha_deg: np.ndarray | None,
    beta_deg: np.ndarray | None,
    theta_st_limit: float | None,
    elevation_limit: float | None,
    alpha_limit: float | None,
    beta_limit: float | None,
) -> bool:
    if not mask.any():
        return True
    if theta_st_deg.ndim != 2 or theta_st_deg.shape[1] == 0:
        return False
    valid = np.ones(theta_st_deg.shape, dtype=bool)
    if theta_st_limit is not None:
        valid &= theta_st_deg <= theta_st_limit
    if elevation_limit is not None:
        if elevation_deg is None or elevation_deg.shape != theta_st_deg.shape:
            return False
        valid &= elevation_deg >= elevation_limit
    if alpha_limit is not None:
        if alpha_deg is None or alpha_deg.shape != theta_st_deg.shape:
            return False
        valid &= np.abs(alpha_deg) <= alpha_limit
    if beta_limit is not None:
        if beta_deg is None or beta_deg.shape != theta_st_deg.shape:
            return False
        valid &= np.abs(beta_deg) <= beta_limit
    return bool(np.all(np.any(valid[mask], axis=1)))


def _thrust_direction_for_state(state_eci: np.ndarray, delta_deg: float, dv_direction: int) -> np.ndarray:
    position = np.asarray(state_eci[:3], dtype=np.float64)
    velocity = np.asarray(state_eci[3:6], dtype=np.float64)
    delta_rad = math.radians(float(delta_deg))
    try:
        alpha = _solve_alpha_from_delta(position, velocity, delta_rad, dv_direction=dv_direction)
        direction = np.asarray(
            [
                math.cos(alpha) * math.cos(delta_rad),
                math.sin(alpha) * math.cos(delta_rad),
                math.sin(delta_rad),
            ],
            dtype=np.float64,
        )
    except ValueError:
        direction = velocity
    return _normalize(np.asarray(direction, dtype=np.float64).reshape(1, 3))[0]


def _solve_alpha_from_delta(
    position_eci_m: np.ndarray,
    velocity_eci_m_s: np.ndarray,
    delta_rad: float,
    *,
    dv_direction: int,
    tol: float = 1e-10,
) -> float:
    x, y, z = (float(value) for value in position_eci_m)
    cos_d = math.cos(delta_rad)
    if abs(cos_d) <= tol:
        if abs(z * math.sin(delta_rad)) > tol:
            raise ValueError("No alpha solution for this delta.")
        candidates = [math.atan2(y, x)]
    else:
        rho = math.hypot(x, y)
        if rho <= tol:
            raise ValueError("No alpha solution near the pole.")
        cos_term = -z * math.tan(delta_rad) / rho
        if cos_term > 1.0 + tol or cos_term < -1.0 - tol:
            raise ValueError("No real alpha solution.")
        cos_term = max(-1.0, min(1.0, cos_term))
        phase = math.atan2(y, x)
        offset = math.acos(cos_term)
        candidates = [phase + offset, phase - offset]

    best_alpha = candidates[0]
    best_score = -float("inf") if dv_direction == 1 else float("inf")
    for alpha in candidates:
        direction = np.asarray(
            [
                math.cos(alpha) * math.cos(delta_rad),
                math.sin(alpha) * math.cos(delta_rad),
                math.sin(delta_rad),
            ],
            dtype=np.float64,
        )
        score = float(np.dot(direction, velocity_eci_m_s))
        if (dv_direction == 1 and score > best_score) or (dv_direction == -1 and score < best_score):
            best_alpha = alpha
            best_score = score
    return best_alpha


def _eci_direction_to_ecef(direction_eci: np.ndarray, epoch_utc: datetime) -> np.ndarray:
    theta = _gmst_rad(epoch_utc)
    cos_theta = math.cos(theta)
    sin_theta = math.sin(theta)
    x, y, z = (float(value) for value in direction_eci)
    return np.asarray(
        [
            cos_theta * x + sin_theta * y,
            -sin_theta * x + cos_theta * y,
            z,
        ],
        dtype=np.float64,
    )


def _shadow_flags_and_margin(
    t0_utc: datetime,
    timeline: dict[str, Any],
    *,
    sun_vectors: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    elapsed_min: np.ndarray = timeline["elapsed_min"]
    positions: np.ndarray = timeline["positions"]
    if sun_vectors is None:
        sun_vectors = _sun_unit_ecef_for_elapsed(t0_utc, elapsed_min)
    projections = np.sum(positions * sun_vectors, axis=1)
    perpendicular = positions - projections[:, np.newaxis] * sun_vectors
    shadow_margin_m = np.linalg.norm(perpendicular, axis=1) - EARTH_RADIUS_M
    return (projections < 0.0) & (shadow_margin_m <= 0.0), shadow_margin_m


def _interpolated_shadow_boundary(
    times_min: np.ndarray,
    shadow_margin_m: np.ndarray,
    left_index: int,
    right_index: int,
    *,
    fallback: float,
) -> float:
    if left_index < 0 or right_index >= len(times_min):
        return fallback
    left_t = float(times_min[left_index])
    right_t = float(times_min[right_index])
    left_value = float(shadow_margin_m[left_index])
    right_value = float(shadow_margin_m[right_index])
    if abs(right_value - left_value) < 1e-12:
        return fallback
    fraction = (0.0 - left_value) / (right_value - left_value)
    return left_t + fraction * (right_t - left_t)


def _duration_where(elapsed_min: np.ndarray, flags: np.ndarray, start_min: float, end_min: float) -> float:
    mask = (elapsed_min >= start_min) & (elapsed_min <= end_min)
    return _flag_duration(elapsed_min[mask], flags[mask])


def _no_shadow_rows_shadow_duration(
    rows: list[dict[str, Any]],
    elapsed_min: np.ndarray,
    shadow: np.ndarray,
    *,
    constraint_time_parameters: dict[str, float],
) -> float | None:
    intervals: list[tuple[float, float]] = []
    for row in rows:
        if not bool(row.get("enabled", True)):
            continue
        if str(row.get("condition_type", "")).strip() != CONSTRAINT_TYPE_NO_SHADOW:
            continue
        name = str(row.get("name", "no_shadow")) or "no_shadow"
        try:
            start_min = _resolve_constraint_time_value(row.get("start_min", 0.0), constraint_time_parameters)
            end_min = _resolve_constraint_time_value(row.get("end_min", start_min), constraint_time_parameters)
        except ValueError as exc:
            raise ValueError(f"Invalid time expression in constraint {name!r}: {exc}") from exc
        if end_min < start_min:
            start_min, end_min = end_min, start_min
        intervals.append((start_min, end_min))
    if not intervals:
        return None

    intervals.sort()
    merged: list[tuple[float, float]] = []
    for start_min, end_min in intervals:
        if not merged or start_min > merged[-1][1]:
            merged.append((start_min, end_min))
            continue
        merged[-1] = (merged[-1][0], max(merged[-1][1], end_min))
    return sum(_duration_where(elapsed_min, shadow, start_min, end_min) for start_min, end_min in merged)


def _leading_duration_where(elapsed_min: np.ndarray, flags: np.ndarray, start_min: float) -> float:
    mask = elapsed_min >= start_min
    times = elapsed_min[mask]
    values = flags[mask]
    if times.size == 0 or not bool(values[0]):
        return 0.0
    false_indices = np.flatnonzero(~values)
    end_index = int(false_indices[0]) if false_indices.size else times.size - 1
    return float(times[end_index] - times[0])


def _flag_duration(times: np.ndarray, flags: np.ndarray) -> float:
    if times.size <= 1:
        return 0.0
    values = np.asarray(flags, dtype=bool)
    intervals = values[:-1] | values[1:]
    if not bool(intervals.any()):
        return 0.0
    return float(np.sum(np.diff(times)[intervals]))


def _has_contiguous_coverage(times: np.ndarray, covered: np.ndarray, required_min: float) -> bool:
    return _longest_true_duration(times, covered) + 1e-9 >= required_min


def _max_gap_without_coverage(times: np.ndarray, covered: np.ndarray) -> float:
    return _longest_true_duration(times, ~covered)


def _longest_true_duration(times: np.ndarray, flags: np.ndarray) -> float:
    if times.size <= 1:
        return 0.0
    values = np.asarray(flags, dtype=bool)
    intervals = values[:-1] & values[1:]
    if not bool(intervals.any()):
        return 0.0
    padded = np.concatenate(([False], intervals, [False]))
    change_indices = np.flatnonzero(padded[1:] != padded[:-1])
    starts = change_indices[0::2]
    ends = change_indices[1::2]
    cumulative = np.concatenate(([0.0], np.cumsum(np.diff(times))))
    return float(np.max(cumulative[ends] - cumulative[starts]))


def _remote_tracking_ok(
    elapsed_min: np.ndarray,
    covered: np.ndarray,
    maneuvers: list[ManeuverInterval],
    config: LaunchWindowConfig,
) -> bool:
    if not maneuvers:
        return True
    for maneuver in maneuvers:
        mask = (elapsed_min >= maneuver.start_min - config.remote_track_pre_min) & (
            elapsed_min <= maneuver.end_min + config.remote_track_post_min
        )
        if mask.any() and not bool(np.all(covered[mask])):
            return False
    return True


def _min_burn_sun_margin(
    elapsed_min: np.ndarray,
    panel_sun_angles_deg: np.ndarray,
    maneuvers: list[ManeuverInterval],
    max_angle_deg: float,
) -> float:
    margins: list[float] = []
    for maneuver in maneuvers:
        mask = (elapsed_min >= maneuver.start_min) & (elapsed_min <= maneuver.end_min)
        if mask.any():
            margins.append(float(max_angle_deg - np.nanmax(panel_sun_angles_deg[mask])))
    if not margins:
        return float("inf")
    return min(margins)


def _ecef_from_geodetic(longitude_deg: float, latitude_deg: float, altitude_m: float) -> np.ndarray:
    lon = math.radians(longitude_deg)
    lat = math.radians(latitude_deg)
    e2 = EARTH_FLATTENING * (2.0 - EARTH_FLATTENING)
    sin_lat = math.sin(lat)
    cos_lat = math.cos(lat)
    n = EARTH_RADIUS_M / math.sqrt(1.0 - e2 * sin_lat * sin_lat)
    return np.asarray(
        [
            (n + altitude_m) * cos_lat * math.cos(lon),
            (n + altitude_m) * cos_lat * math.sin(lon),
            (n * (1.0 - e2) + altitude_m) * sin_lat,
        ],
        dtype=np.float64,
    )


def _normalize(vectors: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vectors, axis=-1, keepdims=True)
    return vectors / np.maximum(norms, 1e-12)


def _in_earth_shadow(position_ecef_m: np.ndarray, sun_unit_ecef: np.ndarray) -> bool:
    projection = float(np.dot(position_ecef_m, sun_unit_ecef))
    if projection >= 0.0:
        return False
    perpendicular = position_ecef_m - projection * sun_unit_ecef
    return float(np.linalg.norm(perpendicular)) <= EARTH_RADIUS_M


def _sun_unit_ecef(epoch_utc: datetime) -> np.ndarray:
    sun_eci = _sun_unit_eci(epoch_utc)
    theta = _gmst_rad(epoch_utc)
    cos_theta = math.cos(theta)
    sin_theta = math.sin(theta)
    x, y, z = sun_eci
    return np.asarray(
        [
            cos_theta * x + sin_theta * y,
            -sin_theta * x + cos_theta * y,
            z,
        ],
        dtype=np.float64,
    )


def _sun_unit_ecef_for_elapsed(t0_utc: datetime, elapsed_min: np.ndarray) -> np.ndarray:
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
    sun_eci = np.column_stack(
        (
            np.cos(ecliptic_longitude),
            np.cos(obliquity) * np.sin(ecliptic_longitude),
            np.sin(obliquity) * np.sin(ecliptic_longitude),
        )
    )
    sun_eci = sun_eci / np.maximum(np.linalg.norm(sun_eci, axis=1, keepdims=True), 1e-12)

    t = (jd - 2451545.0) / 36525.0
    gmst = np.deg2rad(
        (
            280.46061837
            + 360.98564736629 * (jd - 2451545.0)
            + 0.000387933 * t * t
            - (t * t * t) / 38710000.0
        )
        % 360.0
    )
    cos_theta = np.cos(gmst)
    sin_theta = np.sin(gmst)
    x = sun_eci[:, 0]
    y = sun_eci[:, 1]
    z = sun_eci[:, 2]
    return np.column_stack((cos_theta * x + sin_theta * y, -sin_theta * x + cos_theta * y, z))


def _sun_unit_eci(epoch_utc: datetime) -> np.ndarray:
    jd = _julian_date(epoch_utc)
    n = jd - 2451545.0
    mean_longitude = math.radians((280.460 + 0.9856474 * n) % 360.0)
    mean_anomaly = math.radians((357.528 + 0.9856003 * n) % 360.0)
    ecliptic_longitude = mean_longitude + math.radians(1.915) * math.sin(mean_anomaly) + math.radians(0.020) * math.sin(2.0 * mean_anomaly)
    obliquity = math.radians(23.439 - 0.0000004 * n)
    vector = np.asarray(
        [
            math.cos(ecliptic_longitude),
            math.cos(obliquity) * math.sin(ecliptic_longitude),
            math.sin(obliquity) * math.sin(ecliptic_longitude),
        ],
        dtype=np.float64,
    )
    return vector / np.linalg.norm(vector)


def _gmst_rad(epoch_utc: datetime) -> float:
    jd = _julian_date(epoch_utc)
    t = (jd - 2451545.0) / 36525.0
    gmst_deg = (
        280.46061837
        + 360.98564736629 * (jd - 2451545.0)
        + 0.000387933 * t * t
        - (t * t * t) / 38710000.0
    )
    return math.radians(gmst_deg % 360.0)


def _julian_date(epoch_utc: datetime) -> float:
    utc = parse_utc(epoch_utc).astimezone(timezone.utc)
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
