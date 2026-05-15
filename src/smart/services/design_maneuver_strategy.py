from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import math
from typing import Any

import numpy as np

from smart.domain.models import OrbitalElements
from smart.services.earth_orientation import format_utc, greenwich_angle_at_utc, parse_utc, utc_now_iso_z

BEIJING_OFFSET = timedelta(hours=8)
G0_M_S2 = 9.80665
MU_EARTH_KM3_S2 = 398600.4418
R_EARTH_KM = 6378.137
J2_EARTH = 1.08262668e-3
OMEGA_EARTH_RAD_S = 7.2921158553e-5


@dataclass(frozen=True, slots=True)
class DesignManeuverBurn:
    index: int
    burn_type: str
    apsis: str
    elapsed_min: float
    beijing_time: str
    longitude_deg_e: float
    delta_v_mps: float
    alpha_deg: float
    target_post_a_km: float | None
    total_burn_time_min: float
    propellant_kg: float
    post_a_km: float
    post_e: float
    post_i_deg: float
    duration_ok: bool
    longitude_ok: bool


@dataclass(frozen=True, slots=True)
class DesignManeuverResult:
    config: dict[str, Any]
    summary: dict[str, Any]
    burns: list[DesignManeuverBurn]
    checks: list[dict[str, Any]]
    warnings: list[str]


def design_maneuver_result_to_payload(result: DesignManeuverResult) -> dict[str, Any]:
    return {
        "config": normalize_design_maneuver_strategy_payload(result.config),
        "summary": dict(result.summary),
        "burns": [asdict(burn) for burn in result.burns],
        "checks": [dict(check) for check in result.checks],
        "warnings": list(result.warnings),
    }


def design_maneuver_result_from_payload(payload: dict[str, Any] | None) -> DesignManeuverResult:
    if not isinstance(payload, dict):
        raise ValueError("Invalid design maneuver result payload.")
    burns_payload = payload.get("burns", [])
    checks_payload = payload.get("checks", [])
    warnings_payload = payload.get("warnings", [])
    if not isinstance(burns_payload, list):
        raise ValueError("Invalid design maneuver result payload: 'burns' must be a list.")
    if not isinstance(checks_payload, list):
        raise ValueError("Invalid design maneuver result payload: 'checks' must be a list.")
    if not isinstance(warnings_payload, list):
        raise ValueError("Invalid design maneuver result payload: 'warnings' must be a list.")
    return DesignManeuverResult(
        config=normalize_design_maneuver_strategy_payload(payload.get("config")),
        summary=dict(payload.get("summary", {})) if isinstance(payload.get("summary", {}), dict) else {},
        burns=[DesignManeuverBurn(**dict(item)) for item in burns_payload if isinstance(item, dict)],
        checks=[dict(item) for item in checks_payload if isinstance(item, dict)],
        warnings=[str(item) for item in warnings_payload],
    )


def default_design_maneuver_strategy_payload() -> dict[str, Any]:
    return {
        "planner": {
            "version": "V4.2_simplified_transfer_type",
            "auto_recommend_count": True,
            "maneuver_count_user": 0,
            "force_user_count": True,
        },
        "initial": {
            "t0_epoch": "2026-04-24T13:54:27Z",
            "m0_kg": 6515.0,
            "state_input_type": "keplerian",
            "a_km": 29478.137,
            "e": 0.77684692,
            "i_deg": 16.5,
            "lon_node_deg": 8.53237,
            "argp_deg": 200.0,
            "mean_anomaly_deg": 1.8547,
        },
        "orbit_type": {
            "mode": "auto",
            "supersync_transfer_margin_km": 500.0,
            "standard_transfer_apogee_margin_km": 500.0,
        },
        "target": {
            "a_km": 42164.2,
            "e": 0.0,
            "i_deg": 6.0,
            "lon_degE": 120.0,
            "dv_lon_margin_mps": 50.0,
        },
        "earth": {
            "mu_km3_s2": 398600.4418,
            "Re_km": 6378.137,
            "J2": 1.08262668e-3,
            "omega_e_rad_s": 7.2921158553e-5,
            "use_J2": True,
        },
        "engine": {
            "F_main_N": 490.0,
            "Isp_main_s": 314.1,
            "attitude_control_efficiency": 0.0173,
            "F_set_N": 20.0,
            "Isp_set_s": 290.0,
            "tau_set_s": 240.0,
            "use_settling": True,
        },
        "burn_limit": {
            "include_settling_in_burn_time": True,
            "max_total_burn_time_min": 90.0,
            "preferred_total_burn_time_min": 80.0,
            "burn_utilization": 0.75,
            "design_dv_per_burn_mps": 350.0,
        },
        "longitude": {
            "raw_window_degE": [40.0, 180.0],
            "planning_window_degE": [45.0, 175.0],
            "finite_margin_window_degE": [50.0, 170.0],
            "constraint_mode": "impulse_point",
        },
        "maneuver_count": {
            "min": 1,
            "max": 10,
            "user": 0,
            "engineering_min_count": 5,
            "engineering_min_count_supersync": 5,
            "engineering_min_count_standard": 3,
            "total_dv_est_user_mps": 1539.0,
        },
        "distribution": {
            "mode": "auto",
            "max_uniform_dv_spread_mps": 70.0,
            "dv_min_per_burn_mps": 20.0,
            "front_dv_total_user_mps": 0.0,
            "tail_dv_est_user_mps": 625.0,
            "standard_terminal_reserve_mps": 0.0,
            "allow_small_dv_correction": True,
            "small_dv_correction_bound_mps": 25.0,
            "user_dv_template_mps": [],
            "weights": [],
        },
        "supersynchronous_transfer": {
            "strategy": "n_apogee_plus_1_perigee",
            "tail_fixed_enabled": True,
            "tail_fixed_count": 2,
            "tail_control_mode": "fixed_post_a",
            "a_tail_apogee_plus_fixed_km": 47271.168509,
            "a_tail_perigee_plus_fixed_km": 42164.2,
            "dv_tail_apogee_fixed_mps": None,
            "dv_tail_perigee_fixed_mps": None,
        },
        "standard_transfer": {
            "strategy": "n_apogee",
            "use_fixed_tail": False,
            "terminal_reserve_mps": 0.0,
        },
        "apsis": {
            "pattern_mode": "auto",
            "pattern_user": "",
            "q_AA_default": 3,
            "q_sequence_user": [],
            "search_revolutions_max": 40,
            "search_initial_apogees": 20,
            "max_event_search": 30,
            "q_count_eligible_only": True,
        },
        "alpha": {
            "optimize_alpha": True,
            "alpha_default_deg": 10.0,
            "front_bounds_deg": [-20.0, 40.0],
            "tail_apogee_bounds_deg": [-20.0, 40.0],
            "tail_perigee_bounds_deg": [-180.0, 180.0],
            "standard_bounds_deg": [-30.0, 40.0],
            "smooth_alpha_weight": 0.01,
            "initial_template_deg": [15.0, 10.0, 9.0, 11.37, -176.29],
        },
        "terminal_tolerance": {
            "a_km": 1.0,
            "e": 1.0e-4,
            "i_deg": 0.01,
            "lon_deg": 0.01,
        },
        "optimizer": {
            "enabled": True,
            "method": "Powell",
            "maxiter": 900,
            "maxfev": 25000,
            "terminal_weight": 1.0e6,
            "longitude_weight": 1.0e6,
            "duration_weight": 1.0e6,
            "uniform_weight": 1.0e3,
            "tail_weight": 1.0e9,
            "correction_weight": 1.0e2,
            "random_seed": 7,
        },
    }


def normalize_design_maneuver_strategy_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    defaults = default_design_maneuver_strategy_payload()
    source = payload if isinstance(payload, dict) else {}
    source = _reference_config_to_internal(source)
    result = _merge_dict(defaults, source)

    result["planner"]["auto_recommend_count"] = bool(result["planner"].get("auto_recommend_count", True))
    result["planner"]["force_user_count"] = bool(result["planner"].get("force_user_count", True))
    result["planner"]["maneuver_count_user"] = max(0, int(result["planner"].get("maneuver_count_user", 0)))
    result["initial"]["t0_epoch"] = format_utc(str(result["initial"].get("t0_epoch") or utc_now_iso_z()))
    result["initial"]["state_input_type"] = str(result["initial"].get("state_input_type", "keplerian"))

    for section, keys in {
        "initial": ("m0_kg", "a_km", "e", "i_deg", "lon_node_deg", "argp_deg", "mean_anomaly_deg"),
        "orbit_type": ("supersync_transfer_margin_km", "standard_transfer_apogee_margin_km"),
        "target": ("a_km", "e", "i_deg", "lon_degE", "dv_lon_margin_mps"),
        "earth": ("mu_km3_s2", "Re_km", "J2", "omega_e_rad_s"),
        "engine": ("F_main_N", "Isp_main_s", "attitude_control_efficiency", "F_set_N", "Isp_set_s", "tau_set_s"),
        "burn_limit": (
            "max_total_burn_time_min",
            "preferred_total_burn_time_min",
            "burn_utilization",
            "design_dv_per_burn_mps",
        ),
        "distribution": (
            "max_uniform_dv_spread_mps",
            "dv_min_per_burn_mps",
            "front_dv_total_user_mps",
            "tail_dv_est_user_mps",
            "standard_terminal_reserve_mps",
            "small_dv_correction_bound_mps",
        ),
        "supersynchronous_transfer": (
            "a_tail_apogee_plus_fixed_km",
            "a_tail_perigee_plus_fixed_km",
        ),
        "standard_transfer": ("terminal_reserve_mps",),
        "alpha": ("alpha_default_deg", "smooth_alpha_weight"),
        "terminal_tolerance": ("a_km", "e", "i_deg", "lon_deg"),
        "optimizer": (
            "terminal_weight",
            "longitude_weight",
            "duration_weight",
            "uniform_weight",
            "tail_weight",
            "correction_weight",
        ),
    }.items():
        for key in keys:
            result[section][key] = float(result[section].get(key, defaults[section][key]))

    for key in ("dv_tail_apogee_fixed_mps", "dv_tail_perigee_fixed_mps"):
        result["supersynchronous_transfer"][key] = _optional_float(result["supersynchronous_transfer"].get(key))

    result["earth"]["use_J2"] = bool(result["earth"].get("use_J2", True))
    result["engine"]["use_settling"] = bool(result["engine"].get("use_settling", True))
    result["burn_limit"]["include_settling_in_burn_time"] = bool(
        result["burn_limit"].get("include_settling_in_burn_time", True)
    )
    result["supersynchronous_transfer"]["tail_fixed_enabled"] = bool(
        result["supersynchronous_transfer"].get("tail_fixed_enabled", True)
    )
    result["supersynchronous_transfer"]["tail_fixed_count"] = max(
        0,
        int(result["supersynchronous_transfer"].get("tail_fixed_count", 2)),
    )
    result["distribution"]["allow_small_dv_correction"] = bool(
        result["distribution"].get("allow_small_dv_correction", True)
    )
    result["alpha"]["optimize_alpha"] = bool(result["alpha"].get("optimize_alpha", False))
    result["optimizer"]["enabled"] = bool(result["optimizer"].get("enabled", True))
    result["optimizer"]["maxiter"] = max(1, int(result["optimizer"].get("maxiter", 900)))
    result["optimizer"]["maxfev"] = max(1, int(result["optimizer"].get("maxfev", 25000)))
    result["optimizer"]["random_seed"] = int(result["optimizer"].get("random_seed", 7))
    result["maneuver_count"]["min"] = max(1, int(result["maneuver_count"].get("min", 1)))
    result["maneuver_count"]["max"] = max(result["maneuver_count"]["min"], int(result["maneuver_count"].get("max", 10)))
    result["maneuver_count"]["user"] = max(0, int(result["maneuver_count"].get("user", 0)))
    result["maneuver_count"]["engineering_min_count"] = max(
        1,
        int(result["maneuver_count"].get("engineering_min_count", 1)),
    )
    result["maneuver_count"]["engineering_min_count_supersync"] = max(
        1,
        int(result["maneuver_count"].get("engineering_min_count_supersync", result["maneuver_count"]["engineering_min_count"])),
    )
    result["maneuver_count"]["engineering_min_count_standard"] = max(
        1,
        int(result["maneuver_count"].get("engineering_min_count_standard", result["maneuver_count"]["engineering_min_count"])),
    )
    if result["planner"]["maneuver_count_user"] > 0:
        result["maneuver_count"]["user"] = result["planner"]["maneuver_count_user"]
    else:
        result["planner"]["maneuver_count_user"] = result["maneuver_count"]["user"]
    result["apsis"]["q_AA_default"] = max(1, int(result["apsis"].get("q_AA_default", 3)))
    result["apsis"]["search_revolutions_max"] = max(1, int(result["apsis"].get("search_revolutions_max", 40)))
    result["apsis"]["search_initial_apogees"] = max(1, int(result["apsis"].get("search_initial_apogees", 20)))
    result["apsis"]["max_event_search"] = max(1, int(result["apsis"].get("max_event_search", 30)))
    result["apsis"]["q_count_eligible_only"] = bool(result["apsis"].get("q_count_eligible_only", True))

    for section, key in (
        ("longitude", "raw_window_degE"),
        ("longitude", "planning_window_degE"),
        ("longitude", "finite_margin_window_degE"),
        ("alpha", "front_bounds_deg"),
        ("alpha", "tail_apogee_bounds_deg"),
        ("alpha", "tail_perigee_bounds_deg"),
        ("alpha", "standard_bounds_deg"),
    ):
        result[section][key] = _number_pair(result[section].get(key), defaults[section][key])

    q_user = result["apsis"].get("q_sequence_user", [])
    result["apsis"]["q_sequence_user"] = [max(1, int(value)) for value in q_user] if isinstance(q_user, list) else []
    alpha_template = result["alpha"].get("initial_template_deg", [])
    result["alpha"]["initial_template_deg"] = [float(value) for value in alpha_template] if isinstance(alpha_template, list) else []
    for key in ("user_dv_template_mps", "weights"):
        values = result["distribution"].get(key, [])
        result["distribution"][key] = [float(value) for value in values] if isinstance(values, list) else []
    return result


def plan_design_maneuver_strategy(payload: dict[str, Any] | None) -> DesignManeuverResult:
    config = normalize_design_maneuver_strategy_payload(payload)
    warnings: list[str] = []

    initial = config["initial"]
    earth = config["earth"]
    target = config["target"]
    engine = config["engine"]
    burn_limit = config["burn_limit"]
    count_cfg = config["maneuver_count"]
    distribution = config["distribution"]
    supersync = config["supersynchronous_transfer"]
    alpha_cfg = config["alpha"]

    mu = float(earth["mu_km3_s2"])
    a0 = float(initial["a_km"])
    e0 = float(initial["e"])
    i0 = float(initial["i_deg"])
    a_target = float(target["a_km"])
    e_target = float(target["e"])
    i_target = float(target["i_deg"])
    r_a0 = a0 * (1.0 + e0)
    h_a0 = r_a0 - float(earth["Re_km"])
    h_sync = a_target - float(earth["Re_km"])

    orbit_type = _classify_orbit(config, r_a0, a_target)
    dv_tail_apogee_est, dv_tail_perigee_est = _estimate_tail_delta_v(config, orbit_type)
    dv_total_est = _estimate_total_delta_v(
        config,
        orbit_type,
        dv_tail_apogee_est=dv_tail_apogee_est,
        dv_tail_perigee_est=dv_tail_perigee_est,
    )
    design_dv = _estimate_design_single_burn_dv(config, float(initial["m0_kg"]))
    recommended_count = _recommend_count(config, orbit_type, dv_total_est, design_dv)
    user_count = int(count_cfg["user"])
    actual_count = user_count if user_count > 0 else recommended_count
    actual_count = max(1, actual_count)
    if orbit_type == "supersynchronous_transfer" and actual_count < 2:
        warnings.append("超同步转移至少需要 2 次变轨，已按 2 次生成。")
        actual_count = 2
    if bool(supersync["tail_fixed_enabled"]):
        tail_count = int(supersync["tail_fixed_count"])
        if orbit_type == "supersynchronous_transfer" and actual_count < tail_count:
            warnings.append(f"固定尾段要求变轨次数不小于 {tail_count}，已自动抬高。")
            actual_count = tail_count
    if user_count > 0 and user_count < recommended_count:
        warnings.append("用户指定次数小于自动推荐次数，可能导致点火时长超限。")

    apsis_pattern = _apsis_pattern(config, orbit_type, actual_count)
    delta_vs = _distribute_delta_v(
        config,
        orbit_type,
        actual_count,
        dv_total_est,
        dv_tail_apogee_est,
        dv_tail_perigee_est,
    )
    alpha_values = _alpha_values(config, orbit_type, apsis_pattern)
    phase_plan = _select_phase_plan(
        config,
        orbit_type=orbit_type,
        apsis_pattern=apsis_pattern,
        delta_vs=delta_vs,
        alpha_values=alpha_values,
    )
    delta_vs = phase_plan["delta_vs"]
    burns = _build_burns(
        config,
        apsis_pattern=apsis_pattern,
        delta_vs=delta_vs,
        alpha_values=alpha_values,
        warnings=warnings,
        q_sequence_override=phase_plan["q_sequence"],
    )
    ignore_uniform = bool(phase_plan["delta_v_optimized"])
    checks = _build_checks(config, burns, ignore_uniform=ignore_uniform)
    duration_limit = float(burn_limit["max_total_burn_time_min"])
    duration_ok = all(burn.total_burn_time_min <= duration_limit + 1.0e-9 for burn in burns)
    longitude_ok = all(burn.longitude_ok for burn in burns)
    uniform_spread = _uniform_spread([burn.delta_v_mps for burn in burns if burn.burn_type != "tail_fixed"])
    uniform_ok = ignore_uniform or uniform_spread <= float(distribution["max_uniform_dv_spread_mps"]) + 1.0e-9
    if not duration_ok:
        warnings.append("至少一次点火总时长超过上限。")
    if not longitude_ok:
        warnings.append("至少一次点火经度未落入规划窗口。")
    if not uniform_ok:
        warnings.append("参与分配的点火 Δv 离散度超过均匀性约束。")

    terminal_a = burns[-1].post_a_km if burns else a0
    terminal_e = burns[-1].post_e if burns else e0
    terminal_i = burns[-1].post_i_deg if burns else i0
    terminal_errors = {
        "a_km": terminal_a - a_target,
        "e": terminal_e - e_target,
        "i_deg": terminal_i - i_target,
        "lon_deg": _wrap180((burns[-1].longitude_deg_e if burns else 0.0) - float(target["lon_degE"])),
    }
    summary = {
        "initial_apogee_altitude_km": h_a0,
        "sync_altitude_km": h_sync,
        "orbit_type": orbit_type,
        "estimated_total_delta_v_mps": dv_total_est,
        "design_single_burn_delta_v_mps": design_dv,
        "recommended_count": recommended_count,
        "user_count": user_count,
        "actual_count": actual_count,
        "apsis_pattern": ",".join(apsis_pattern),
        "q_sequence": ",".join(str(value) for value in phase_plan["q_sequence"]),
        "phase_optimized": bool(phase_plan["optimized"]),
        "phase_delta_v_optimized": bool(phase_plan["delta_v_optimized"]),
        "phase_lon_error_before_deg": phase_plan["initial_error_deg"],
        "duration_ok": duration_ok,
        "longitude_ok": longitude_ok,
        "uniform_spread_mps": uniform_spread,
        "uniform_ok": uniform_ok,
        "terminal_errors": terminal_errors,
    }
    return DesignManeuverResult(config=config, summary=summary, burns=burns, checks=checks, warnings=warnings)


def _merge_dict(defaults: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, default_value in defaults.items():
        source_value = source.get(key)
        if isinstance(default_value, dict):
            result[key] = _merge_dict(default_value, source_value if isinstance(source_value, dict) else {})
        else:
            result[key] = source_value if source_value is not None else default_value
    for key, value in source.items():
        if key not in result:
            result[key] = value
    return result


def _reference_config_to_internal(source: dict[str, Any]) -> dict[str, Any]:
    if "initial_orbit" not in source and "initial_mass_kg" not in source and "t0_bj" not in source:
        return source
    result = dict(source)
    initial_orbit = source.get("initial_orbit", {})
    if isinstance(initial_orbit, dict):
        initial = dict(result.get("initial", {})) if isinstance(result.get("initial"), dict) else {}
        initial["m0_kg"] = source.get("initial_mass_kg", initial.get("m0_kg", 6515.0))
        if source.get("t0_bj"):
            t0_bj = datetime.strptime(str(source["t0_bj"]), "%Y-%m-%d %H:%M:%S")
            initial["t0_epoch"] = format_utc(t0_bj.replace(tzinfo=timezone(timedelta(hours=8))).astimezone(timezone.utc))
        initial["a_km"] = initial_orbit.get("a_km", initial.get("a_km"))
        initial["e"] = initial_orbit.get("e", initial.get("e"))
        initial["i_deg"] = initial_orbit.get("i_deg", initial.get("i_deg"))
        initial["lon_node_deg"] = initial_orbit.get(
            "ascending_node_longitude_deg",
            initial_orbit.get("raan_deg", initial.get("lon_node_deg")),
        )
        initial["argp_deg"] = initial_orbit.get("argp_deg", initial.get("argp_deg"))
        initial["mean_anomaly_deg"] = initial_orbit.get("M_deg", initial.get("mean_anomaly_deg"))
        result["initial"] = initial
    planner = dict(result.get("planner", {})) if isinstance(result.get("planner"), dict) else {}
    planner["version"] = source.get("version", planner.get("version", "V4.2_simplified_transfer_type"))
    if isinstance(source.get("maneuver_count"), dict):
        planner["auto_recommend_count"] = source["maneuver_count"].get("auto_recommend_count", planner.get("auto_recommend_count", True))
        planner["force_user_count"] = source["maneuver_count"].get("force_user_count", planner.get("force_user_count", True))
        planner["maneuver_count_user"] = source["maneuver_count"].get("user", planner.get("maneuver_count_user", 0))
    result["planner"] = planner
    result.pop("initial_orbit", None)
    result.pop("initial_mass_kg", None)
    result.pop("t0_bj", None)
    result.pop("version", None)
    return result


def _optional_float(value: object) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def _number_pair(value: object, default: list[float]) -> list[float]:
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        return [float(value[0]), float(value[1])]
    return [float(default[0]), float(default[1])]


def _classify_orbit(config: dict[str, Any], r_a0: float, a_target: float) -> str:
    orbit_cfg = config["orbit_type"]
    mode = str(orbit_cfg.get("mode", "auto"))
    if mode in {"supersynchronous_transfer", "standard_transfer", "general_transfer"}:
        return mode
    if r_a0 > a_target + float(orbit_cfg["supersync_transfer_margin_km"]):
        return "supersynchronous_transfer"
    if abs(r_a0 - a_target) <= float(orbit_cfg["standard_transfer_apogee_margin_km"]):
        return "standard_transfer"
    return "general_transfer"


def _estimate_design_single_burn_dv(config: dict[str, Any], mass_kg: float) -> float:
    engine = config["engine"]
    limit = config["burn_limit"]
    use_settling = bool(engine["use_settling"])
    tau_set = float(engine["tau_set_s"]) if use_settling else 0.0
    isp_set = max(1.0, float(engine["Isp_set_s"]))
    f_set = max(0.0, float(engine["F_set_N"]))
    isp_main_eff = max(1.0, float(engine["Isp_main_s"]) / (1.0 + float(engine["attitude_control_efficiency"])))
    c_main_eff = isp_main_eff * G0_M_S2
    mdot_main = max(1.0e-12, float(engine["F_main_N"]) / c_main_eff)
    max_total_s = max(0.0, float(limit["max_total_burn_time_min"]) * 60.0)
    tau_main_max = max_total_s - tau_set if bool(limit["include_settling_in_burn_time"]) else max_total_s
    tau_main_max = max(0.0, tau_main_max)
    mdot_set = f_set / (isp_set * G0_M_S2) if use_settling else 0.0
    mp_set = min(max(0.0, mass_kg * 0.95), mdot_set * tau_set)
    m_after_set = max(1.0, mass_kg - mp_set)
    dv_set = isp_set * G0_M_S2 * math.log(mass_kg / m_after_set) if mp_set > 0.0 else 0.0
    mp_main = min(max(0.0, m_after_set * 0.95), mdot_main * tau_main_max)
    dv_main = c_main_eff * math.log(m_after_set / (m_after_set - mp_main)) if mp_main > 0.0 else 0.0
    return max(1.0, min(float(limit["design_dv_per_burn_mps"]), float(limit["burn_utilization"]) * (dv_set + dv_main)))


def _estimate_total_delta_v(
    config: dict[str, Any],
    orbit_type: str,
    *,
    dv_tail_apogee_est: float,
    dv_tail_perigee_est: float,
) -> float:
    count_cfg = config["maneuver_count"]
    if float(count_cfg["total_dv_est_user_mps"]) > 0.0:
        return float(count_cfg["total_dv_est_user_mps"])
    initial = config["initial"]
    target = config["target"]
    earth = config["earth"]
    mu = float(earth["mu_km3_s2"])
    a0 = float(initial["a_km"])
    e0 = float(initial["e"])
    r_a0 = a0 * (1.0 + e0)
    v_a0 = math.sqrt(max(0.0, mu * (2.0 / r_a0 - 1.0 / a0)))
    v_sync = math.sqrt(mu / float(target["a_km"]))
    di = math.radians(abs(float(initial["i_deg"]) - float(target["i_deg"])))
    dv_standard = math.sqrt(max(0.0, v_a0 * v_a0 + v_sync * v_sync - 2.0 * v_a0 * v_sync * math.cos(di)))
    dv_standard_mps = dv_standard * 1000.0 + float(target["dv_lon_margin_mps"])
    if orbit_type == "supersynchronous_transfer":
        plane_margin = max(0.0, abs(float(initial["i_deg"]) - float(target["i_deg"])) * 15.0)
        return max(dv_standard_mps, dv_tail_apogee_est + dv_tail_perigee_est + plane_margin + float(target["dv_lon_margin_mps"]))
    return dv_standard_mps


def _estimate_tail_delta_v(config: dict[str, Any], orbit_type: str) -> tuple[float, float]:
    if orbit_type != "supersynchronous_transfer":
        return 0.0, 0.0
    supersync = config["supersynchronous_transfer"]
    tail_apogee_user = _optional_float(supersync.get("dv_tail_apogee_fixed_mps"))
    tail_perigee_user = _optional_float(supersync.get("dv_tail_perigee_fixed_mps"))
    if (tail_apogee_user or 0.0) > 0.0 or (tail_perigee_user or 0.0) > 0.0:
        return (
            max(0.0, float(tail_apogee_user or 0.0)),
            max(0.0, float(tail_perigee_user or 0.0)),
        )
    initial = config["initial"]
    earth = config["earth"]
    mu = float(earth["mu_km3_s2"])
    a0 = float(initial["a_km"])
    e0 = float(initial["e"])
    r_a = a0 * (1.0 + e0)
    a_tail = float(supersync["a_tail_apogee_plus_fixed_km"])
    a_final = float(supersync["a_tail_perigee_plus_fixed_km"])
    v_before_a = math.sqrt(max(0.0, mu * (2.0 / r_a - 1.0 / a0)))
    v_after_a = math.sqrt(max(0.0, mu * (2.0 / r_a - 1.0 / a_tail)))
    r_p_tail = max(float(earth["Re_km"]) + 1.0, 2.0 * a_tail - r_a)
    v_before_p = math.sqrt(max(0.0, mu * (2.0 / r_p_tail - 1.0 / a_tail)))
    v_after_p = math.sqrt(max(0.0, mu * (2.0 / r_p_tail - 1.0 / a_final)))
    return abs(v_after_a - v_before_a) * 1000.0, abs(v_after_p - v_before_p) * 1000.0


def _recommend_count(config: dict[str, Any], orbit_type: str, total_dv: float, design_dv: float) -> int:
    count_cfg = config["maneuver_count"]
    supersync = config["supersynchronous_transfer"]
    n_raw = math.ceil(max(0.0, total_dv) / max(1.0, design_dv))
    if orbit_type == "supersynchronous_transfer":
        n_geom_min = 2
        if bool(supersync["tail_fixed_enabled"]):
            n_geom_min = max(n_geom_min, int(supersync["tail_fixed_count"]))
        engineering_min = int(count_cfg["engineering_min_count_supersync"])
    elif orbit_type == "standard_transfer":
        n_geom_min = 1
        engineering_min = int(count_cfg["engineering_min_count_standard"])
    else:
        n_geom_min = 1
        engineering_min = int(count_cfg["engineering_min_count_standard"])
    value = max(n_raw, n_geom_min, engineering_min)
    return min(max(value, int(count_cfg["min"])), int(count_cfg["max"]))


def _apsis_pattern(config: dict[str, Any], orbit_type: str, count: int) -> list[str]:
    apsis_cfg = config["apsis"]
    if str(apsis_cfg.get("pattern_mode", "auto")) == "user":
        raw = str(apsis_cfg.get("pattern_user", "")).replace("，", ",")
        parsed = [item.strip().upper() for item in raw.split(",") if item.strip().upper() in {"A", "P"}]
        if len(parsed) >= count:
            return parsed[:count]
    if orbit_type == "supersynchronous_transfer":
        return ["A"] * max(0, count - 1) + ["P"]
    return ["A"] * count


def _distribute_delta_v(
    config: dict[str, Any],
    orbit_type: str,
    count: int,
    total_dv: float,
    tail_apogee: float,
    tail_perigee: float,
) -> list[float | None]:
    distribution = config["distribution"]
    supersync = config["supersynchronous_transfer"]
    if str(distribution.get("mode", "")) == "user_template":
        template = distribution.get("user_dv_template_mps", [])
        if isinstance(template, list) and len(template) == count:
            return [float(value) for value in template]
    if orbit_type == "supersynchronous_transfer" and bool(supersync["tail_fixed_enabled"]) and count >= 2:
        if str(supersync["tail_control_mode"]) == "fixed_delta_v":
            tail_a = supersync.get("dv_tail_apogee_fixed_mps")
            tail_p = supersync.get("dv_tail_perigee_fixed_mps")
            if tail_a is None or tail_p is None:
                raise ValueError("fixed_delta_v tail requires dv_tail_apogee_fixed_mps and dv_tail_perigee_fixed_mps.")
            tail_values: list[float | None] = [float(tail_a), float(tail_p)]
        else:
            tail_values = [None, None]
        front_count = max(0, count - 2)
        front_total = float(distribution["front_dv_total_user_mps"])
        if front_total <= 0.0:
            front_total = max(0.0, total_dv - float(distribution["tail_dv_est_user_mps"]))
        weights = distribution.get("weights", [])
        if not isinstance(weights, list) or len(weights) != front_count:
            weights = [1.0] * front_count
        weight_sum = sum(float(value) for value in weights) or 1.0
        front = [front_total * float(weight) / weight_sum for weight in weights] if front_count else []
        return front + tail_values
    reserve = float(distribution["standard_terminal_reserve_mps"])
    if orbit_type == "standard_transfer":
        reserve = max(reserve, float(config["standard_transfer"]["terminal_reserve_mps"]))
    if reserve > 0.0 and count > 1:
        return [(max(0.0, total_dv - reserve) / (count - 1))] * (count - 1) + [reserve]
    return [total_dv / count] * count


def _alpha_values(config: dict[str, Any], orbit_type: str, apsis_pattern: list[str]) -> list[float]:
    alpha_default = float(config["alpha"]["alpha_default_deg"])
    template = config["alpha"].get("initial_template_deg", [])
    if isinstance(template, list) and template:
        values = [float(value) for value in template[: len(apsis_pattern)]]
        if len(values) < len(apsis_pattern):
            values.extend([alpha_default] * (len(apsis_pattern) - len(values)))
        return values
    values: list[float] = []
    for index, apsis in enumerate(apsis_pattern):
        if orbit_type == "supersynchronous_transfer" and index == len(apsis_pattern) - 1 and apsis == "P":
            values.append(180.0)
        else:
            values.append(alpha_default)
    return values


def _q_sequence(config: dict[str, Any], count: int, orbit_type: str) -> list[int]:
    q_limit = _q_limit(config)
    q_user = config["apsis"].get("q_sequence_user", [])
    if isinstance(q_user, list) and q_user:
        if len(q_user) != count - 1:
            raise ValueError("apsis.q_sequence_user length must be N-1.")
        return [min(q_limit, max(1, int(value))) for value in q_user]
    if orbit_type == "supersynchronous_transfer" and count >= 3:
        return [q_limit] * max(0, count - 3) + [min(2, q_limit), 1]
    return [q_limit] * max(0, count - 1)


def _q_limit(config: dict[str, Any]) -> int:
    return max(1, int(config["apsis"]["q_AA_default"]))


def _select_phase_plan(
    config: dict[str, Any],
    *,
    orbit_type: str,
    apsis_pattern: list[str],
    delta_vs: list[float | None],
    alpha_values: list[float],
) -> dict[str, Any]:
    base_sequence = _q_sequence(config, len(apsis_pattern), orbit_type)
    if not _can_phase_optimize(config, orbit_type, apsis_pattern):
        return {
            "q_sequence": list(base_sequence),
            "delta_vs": list(delta_vs),
            "optimized": False,
            "delta_v_optimized": False,
            "initial_error_deg": None,
        }

    base_error = _phase_terminal_longitude_error(
        config,
        apsis_pattern=apsis_pattern,
        delta_vs=delta_vs,
        alpha_values=alpha_values,
        q_sequence=base_sequence,
    )
    best_sequence = list(base_sequence)
    best_error = base_error
    best_delta_vs = list(delta_vs)
    best_score = _phase_score(config, apsis_pattern, best_delta_vs, alpha_values, best_sequence)
    tolerance = float(config["terminal_tolerance"]["lon_deg"])
    for candidate in _phase_q_candidates(config, apsis_pattern, base_sequence):
        optimized = _optimize_phase_delta_vs(
            config,
            apsis_pattern=apsis_pattern,
            delta_vs=delta_vs,
            alpha_values=alpha_values,
            q_sequence=candidate,
        )
        score = optimized["score"]
        error = float(optimized["error_deg"])
        if score < best_score:
            best_sequence = list(candidate)
            best_error = error
            best_delta_vs = list(optimized["delta_vs"])
            best_score = score
            if score[0] == 0 and abs(best_error) <= tolerance:
                break
    return {
        "q_sequence": best_sequence,
        "delta_vs": best_delta_vs,
        "optimized": best_sequence != base_sequence or best_delta_vs != list(delta_vs),
        "delta_v_optimized": best_delta_vs != list(delta_vs),
        "initial_error_deg": base_error,
    }


def _can_phase_optimize(config: dict[str, Any], orbit_type: str, apsis_pattern: list[str]) -> bool:
    if not bool(config["optimizer"]["enabled"]):
        return False
    if orbit_type != "supersynchronous_transfer":
        return False
    if len(apsis_pattern) < 4 or apsis_pattern[-1] != "P":
        return False
    if not bool(config["supersynchronous_transfer"]["tail_fixed_enabled"]):
        return False
    return True


def _phase_q_candidates(
    config: dict[str, Any],
    apsis_pattern: list[str],
    base_sequence: list[int],
) -> list[list[int]]:
    if len(base_sequence) != len(apsis_pattern) - 1:
        return [base_sequence]
    q_user = config["apsis"].get("q_sequence_user", [])
    if isinstance(q_user, list) and q_user:
        return [base_sequence]
    q_default = _q_limit(config)
    tail_q = 1 if apsis_pattern[-2:] == ["A", "P"] else base_sequence[-1]
    raw = [
        base_sequence,
        [max(1, q_default - 1), max(1, q_default - 1), 1, tail_q],
        [max(1, q_default - 1), 1, 1, tail_q],
        [q_default, q_default, 1, tail_q],
        [1, q_default, min(2, q_default), tail_q],
        [1, 1, 1, tail_q],
    ]
    candidates: list[list[int]] = []
    seen: set[tuple[int, ...]] = set()
    for candidate in raw:
        if len(candidate) != len(base_sequence):
            continue
        normalized = [max(1, min(q_default, int(value))) for value in candidate]
        key = tuple(normalized)
        if key not in seen:
            candidates.append(normalized)
            seen.add(key)
    return candidates


def _optimize_phase_delta_vs(
    config: dict[str, Any],
    *,
    apsis_pattern: list[str],
    delta_vs: list[float | None],
    alpha_values: list[float],
    q_sequence: list[int],
) -> dict[str, Any]:
    front_count = max(0, len(delta_vs) - 2)
    if front_count < 3:
        return {
            "delta_vs": list(delta_vs),
            "error_deg": _phase_terminal_longitude_error(
                config,
                apsis_pattern=apsis_pattern,
                delta_vs=delta_vs,
                alpha_values=alpha_values,
                q_sequence=q_sequence,
            ),
            "score": _phase_score(config, apsis_pattern, delta_vs, alpha_values, q_sequence),
        }
    front_base = [float(value or 0.0) for value in delta_vs[:front_count]]
    seeds = [
        front_base,
        [front_base[0] * 0.55, front_base[1] * 0.55, front_base[2] * 1.18],
    ]
    best_delta_vs = list(delta_vs)
    best_error = _phase_terminal_longitude_error(
        config,
        apsis_pattern=apsis_pattern,
        delta_vs=best_delta_vs,
        alpha_values=alpha_values,
        q_sequence=q_sequence,
    )
    best_score = _phase_score(config, apsis_pattern, best_delta_vs, alpha_values, q_sequence)
    for seed in seeds:
        optimized_delta_vs, error, score = _coordinate_search_front_delta_vs(
            config,
            apsis_pattern=apsis_pattern,
            delta_vs=delta_vs,
            alpha_values=alpha_values,
            q_sequence=q_sequence,
            seed_front=seed,
        )
        if score < best_score:
            best_delta_vs = optimized_delta_vs
            best_error = error
            best_score = score
    return {"delta_vs": best_delta_vs, "error_deg": best_error, "score": best_score}


def _coordinate_search_front_delta_vs(
    config: dict[str, Any],
    *,
    apsis_pattern: list[str],
    delta_vs: list[float | None],
    alpha_values: list[float],
    q_sequence: list[int],
    seed_front: list[float],
) -> tuple[list[float | None], float, tuple[int, float, float, float]]:
    front_count = len(seed_front)
    current = [float(value) for value in seed_front]
    candidate_delta_vs: list[float | None] = current + list(delta_vs[front_count:])
    best_error = _phase_terminal_longitude_error(
        config,
        apsis_pattern=apsis_pattern,
        delta_vs=candidate_delta_vs,
        alpha_values=alpha_values,
        q_sequence=q_sequence,
    )
    best_score = _phase_score(config, apsis_pattern, candidate_delta_vs, alpha_values, q_sequence)
    min_dv = float(config["distribution"]["dv_min_per_burn_mps"])
    max_dv = max(700.0, max(current, default=0.0) + 100.0)
    eval_count = 1
    eval_limit = min(80, max(1, int(config["optimizer"]["maxfev"])))
    for step in (80.0, 40.0, 20.0, 10.0, 5.0, 2.0, 1.0, 0.5, 0.2, 0.1, 0.05):
        improved = True
        while improved and eval_count < eval_limit:
            improved = False
            for index in range(front_count):
                for sign in (1.0, -1.0):
                    trial = list(current)
                    trial[index] += sign * step
                    if trial[index] < min_dv or trial[index] > max_dv:
                        continue
                    trial_delta_vs: list[float | None] = trial + list(delta_vs[front_count:])
                    score = _phase_score(config, apsis_pattern, trial_delta_vs, alpha_values, q_sequence)
                    eval_count += 1
                    if score < best_score:
                        current = trial
                        best_score = score
                        best_error = _phase_terminal_longitude_error(
                            config,
                            apsis_pattern=apsis_pattern,
                            delta_vs=trial_delta_vs,
                            alpha_values=alpha_values,
                            q_sequence=q_sequence,
                        )
                        candidate_delta_vs = trial_delta_vs
                        improved = True
                        break
                if improved:
                    break
    return candidate_delta_vs, best_error, best_score


def _phase_score(
    config: dict[str, Any],
    apsis_pattern: list[str],
    delta_vs: list[float | None],
    alpha_values: list[float],
    q_sequence: list[int],
) -> tuple[int, float, float, float]:
    warnings: list[str] = []
    burns = _build_burns(
        config,
        apsis_pattern=apsis_pattern,
        delta_vs=delta_vs,
        alpha_values=alpha_values,
        warnings=warnings,
        q_sequence_override=q_sequence,
    )
    if not burns:
        return (1, float("inf"), float("inf"), float("inf"))
    error = abs(_wrap180(burns[-1].longitude_deg_e - float(config["target"]["lon_degE"])))
    max_duration = max(burn.total_burn_time_min for burn in burns)
    duration_limit = float(config["burn_limit"]["max_total_burn_time_min"])
    duration_penalty = max(0.0, max_duration - duration_limit) * 1000.0
    invalid = 1 if warnings or duration_penalty > 0.0 else 0
    spread = _uniform_spread([burn.delta_v_mps for burn in burns if burn.burn_type != "tail_fixed"])
    return (invalid, error + duration_penalty + (1000.0 if warnings else 0.0), max_duration, spread)


def _phase_terminal_longitude_error(
    config: dict[str, Any],
    *,
    apsis_pattern: list[str],
    delta_vs: list[float | None],
    alpha_values: list[float],
    q_sequence: list[int],
) -> float:
    burns = _build_burns(
        config,
        apsis_pattern=apsis_pattern,
        delta_vs=delta_vs,
        alpha_values=alpha_values,
        warnings=[],
        q_sequence_override=q_sequence,
    )
    if not burns:
        return float("inf")
    return _wrap180(burns[-1].longitude_deg_e - float(config["target"]["lon_degE"]))


def _initial_state_km(config: dict[str, Any]) -> tuple[np.ndarray, np.ndarray]:
    initial = config["initial"]
    t0 = parse_utc(str(initial["t0_epoch"]))
    lon_node = float(initial["lon_node_deg"])
    raan = math.radians((lon_node + math.degrees(greenwich_angle_at_utc(t0))) % 360.0)
    return _coe_to_rv(
        float(initial["a_km"]),
        float(initial["e"]),
        math.radians(float(initial["i_deg"])),
        raan,
        math.radians(float(initial["argp_deg"])),
        math.radians(float(initial["mean_anomaly_deg"])),
        mu=float(config["earth"]["mu_km3_s2"]),
    )


def _coe_to_rv(
    a: float,
    e: float,
    inc: float,
    raan: float,
    argp: float,
    mean_anomaly: float,
    *,
    mu: float = MU_EARTH_KM3_S2,
) -> tuple[np.ndarray, np.ndarray]:
    mean_anomaly = mean_anomaly % (2.0 * math.pi)
    eccentric_anomaly = mean_anomaly
    for _ in range(80):
        denominator = 1.0 - e * math.cos(eccentric_anomaly)
        delta = (eccentric_anomaly - e * math.sin(eccentric_anomaly) - mean_anomaly) / denominator
        eccentric_anomaly -= delta
        if abs(delta) < 1.0e-13:
            break
    true_anomaly = 2.0 * math.atan2(
        math.sqrt(1.0 + e) * math.sin(eccentric_anomaly / 2.0),
        math.sqrt(1.0 - e) * math.cos(eccentric_anomaly / 2.0),
    )
    p = a * (1.0 - e * e)
    radius = p / (1.0 + e * math.cos(true_anomaly))
    r_pf = np.asarray([radius * math.cos(true_anomaly), radius * math.sin(true_anomaly), 0.0], dtype=float)
    v_pf = np.asarray(
        [
            -math.sqrt(mu / p) * math.sin(true_anomaly),
            math.sqrt(mu / p) * (e + math.cos(true_anomaly)),
            0.0,
        ],
        dtype=float,
    )
    cos_o, sin_o = math.cos(raan), math.sin(raan)
    cos_i, sin_i = math.cos(inc), math.sin(inc)
    cos_w, sin_w = math.cos(argp), math.sin(argp)
    rotation = np.asarray(
        [
            [cos_o * cos_w - sin_o * sin_w * cos_i, -cos_o * sin_w - sin_o * cos_w * cos_i, sin_o * sin_i],
            [sin_o * cos_w + cos_o * sin_w * cos_i, -sin_o * sin_w + cos_o * cos_w * cos_i, -cos_o * sin_i],
            [sin_w * sin_i, cos_w * sin_i, cos_i],
        ],
        dtype=float,
    )
    return rotation @ r_pf, rotation @ v_pf


def _rv_to_coe(
    r: np.ndarray,
    v: np.ndarray,
    *,
    mu: float = MU_EARTH_KM3_S2,
) -> tuple[float, float, float, float, float, float, float]:
    radius = float(np.linalg.norm(r))
    speed = float(np.linalg.norm(v))
    h_vec = np.cross(r, v)
    h_norm = float(np.linalg.norm(h_vec))
    k_hat = np.asarray([0.0, 0.0, 1.0], dtype=float)
    n_vec = np.cross(k_hat, h_vec)
    n_norm = float(np.linalg.norm(n_vec))
    e_vec = np.cross(v, h_vec) / mu - r / radius
    e = float(np.linalg.norm(e_vec))
    energy = speed * speed / 2.0 - mu / radius
    a = -mu / (2.0 * energy)
    inc = math.acos(float(np.clip(h_vec[2] / h_norm, -1.0, 1.0)))
    raan = math.atan2(float(n_vec[1]), float(n_vec[0])) if n_norm > 1.0e-12 else 0.0
    if e > 1.0e-10 and n_norm > 1.0e-12:
        argp = math.atan2(
            float(np.dot(np.cross(n_vec, e_vec), h_vec)) / (h_norm * n_norm * e),
            float(np.dot(n_vec, e_vec)) / (n_norm * e),
        )
        true_anomaly = math.atan2(
            float(np.dot(np.cross(e_vec, r), h_vec)) / (h_norm * e * radius),
            float(np.dot(e_vec, r)) / (e * radius),
        )
    else:
        argp = 0.0
        if n_norm > 1.0e-12:
            n_hat = n_vec / n_norm
            q_hat = np.cross(h_vec / h_norm, n_hat)
            true_anomaly = math.atan2(float(np.dot(r, q_hat)), float(np.dot(r, n_hat)))
        else:
            true_anomaly = math.atan2(float(r[1]), float(r[0]))
    if e < 1.0:
        eccentric_anomaly = 2.0 * math.atan2(
            math.sqrt(max(0.0, 1.0 - e)) * math.sin(true_anomaly / 2.0),
            math.sqrt(1.0 + e) * math.cos(true_anomaly / 2.0),
        )
        mean_anomaly = eccentric_anomaly - e * math.sin(eccentric_anomaly)
    else:
        mean_anomaly = 0.0
    return (
        float(a),
        float(e),
        float(inc),
        raan % (2.0 * math.pi),
        argp % (2.0 * math.pi),
        mean_anomaly % (2.0 * math.pi),
        true_anomaly % (2.0 * math.pi),
    )


def _j2_rates(a: float, e: float, inc: float, *, mu: float, radius: float, j2: float) -> tuple[float, float, float]:
    p = a * (1.0 - e * e)
    mean_motion = math.sqrt(mu / a**3)
    factor = j2 * (radius / p) ** 2
    raan_dot = -1.5 * mean_motion * factor * math.cos(inc)
    argp_dot = 0.75 * mean_motion * factor * (5.0 * math.cos(inc) ** 2 - 1.0)
    mean_dot = mean_motion + 0.75 * mean_motion * factor * math.sqrt(max(0.0, 1.0 - e * e)) * (
        3.0 * math.cos(inc) ** 2 - 1.0
    )
    return raan_dot, argp_dot, mean_dot


def _next_apsis(
    config: dict[str, Any],
    r: np.ndarray,
    v: np.ndarray,
    elapsed_s: float,
    apsis: str,
    index: int,
) -> tuple[float, np.ndarray, np.ndarray]:
    earth = config["earth"]
    mu = float(earth["mu_km3_s2"])
    a, e, inc, raan, argp, mean_anomaly, _true_anomaly = _rv_to_coe(r, v, mu=mu)
    if bool(earth["use_J2"]):
        raan_dot, argp_dot, mean_dot = _j2_rates(
            a,
            e,
            inc,
            mu=mu,
            radius=float(earth["Re_km"]),
            j2=float(earth["J2"]),
        )
    else:
        raan_dot = 0.0
        argp_dot = 0.0
        mean_dot = math.sqrt(mu / a**3)
    target_m = math.pi if apsis.upper() == "A" else 0.0
    delta = (target_m - mean_anomaly) % (2.0 * math.pi)
    if delta < 1.0e-9:
        delta = 2.0 * math.pi
    delta += (max(1, int(index)) - 1) * 2.0 * math.pi
    dt = delta / mean_dot
    next_elapsed = elapsed_s + dt
    next_r, next_v = _coe_to_rv(
        a,
        e,
        inc,
        (raan + raan_dot * dt) % (2.0 * math.pi),
        (argp + argp_dot * dt) % (2.0 * math.pi),
        target_m,
        mu=mu,
    )
    return next_elapsed, next_r, next_v


def _longitude_deg(config: dict[str, Any], r: np.ndarray, elapsed_s: float) -> float:
    t0 = parse_utc(str(config["initial"]["t0_epoch"]))
    theta0 = greenwich_angle_at_utc(t0)
    theta = theta0 + float(config["earth"]["omega_e_rad_s"]) * elapsed_s
    x, y, _z = (float(value) for value in r)
    x_ecef = math.cos(theta) * x + math.sin(theta) * y
    y_ecef = -math.sin(theta) * x + math.cos(theta) * y
    return math.degrees(math.atan2(y_ecef, x_ecef)) % 360.0


def _local_horizontal_direction(r: np.ndarray, alpha_deg: float) -> np.ndarray:
    r_hat = r / np.linalg.norm(r)
    k_hat = np.asarray([0.0, 0.0, 1.0], dtype=float)
    east = np.cross(k_hat, r_hat)
    east = east / np.linalg.norm(east)
    north = k_hat - np.dot(k_hat, r_hat) * r_hat
    north = north / np.linalg.norm(north)
    south = -north
    alpha = math.radians(alpha_deg)
    return math.cos(alpha) * east + math.sin(alpha) * south


def _solve_dv_for_target_a(r: np.ndarray, v: np.ndarray, alpha_deg: float, target_a_km: float) -> float | None:
    direction = _local_horizontal_direction(r, alpha_deg)
    radius = float(np.linalg.norm(r))
    b = float(np.dot(v, direction))
    c = float(np.dot(v, v)) - 2.0 * MU_EARTH_KM3_S2 / radius + MU_EARTH_KM3_S2 / target_a_km
    discriminant = b * b - c
    if discriminant < -1.0e-12:
        return None
    discriminant = max(0.0, discriminant)
    roots = [-b + math.sqrt(discriminant), -b - math.sqrt(discriminant)]
    positive_mps = [root * 1000.0 for root in roots if root > 1.0e-11 and math.isfinite(root)]
    return min(positive_mps) if positive_mps else None


def _find_initial_burn_event(
    config: dict[str, Any],
    r: np.ndarray,
    v: np.ndarray,
    apsis: str,
) -> tuple[float, np.ndarray, np.ndarray, float]:
    raw_window = config["longitude"]["raw_window_degE"]
    planning_window = config["longitude"]["planning_window_degE"]
    candidates: list[tuple[float, np.ndarray, np.ndarray, float]] = []
    for event_index in range(1, int(config["apsis"]["search_initial_apogees"]) + 1):
        elapsed_s, event_r, event_v = _next_apsis(config, r, v, 0.0, apsis, event_index)
        lon = _longitude_deg(config, event_r, elapsed_s)
        candidate = (elapsed_s, event_r, event_v, lon)
        candidates.append(candidate)
        if _in_window(lon, planning_window):
            return candidate
    for candidate in candidates:
        if _in_window(candidate[3], raw_window):
            return candidate
    return candidates[0]


def _find_next_burn_event(
    config: dict[str, Any],
    r: np.ndarray,
    v: np.ndarray,
    elapsed_s: float,
    apsis: str,
    q: int,
    *,
    target_longitude_deg_e: float | None = None,
) -> tuple[float, np.ndarray, np.ndarray, float]:
    raw_window = config["longitude"]["raw_window_degE"]
    planning_window = config["longitude"]["planning_window_degE"]
    raw_events: list[tuple[float, np.ndarray, np.ndarray, float]] = []
    eligible: list[tuple[float, np.ndarray, np.ndarray, float]] = []
    for event_index in range(1, int(config["apsis"]["max_event_search"]) + 1):
        next_elapsed, next_r, next_v = _next_apsis(config, r, v, elapsed_s, apsis, event_index)
        lon = _longitude_deg(config, next_r, next_elapsed)
        event = (next_elapsed, next_r, next_v, lon)
        raw_events.append(event)
        if _in_window(lon, planning_window):
            eligible.append(event)
            if target_longitude_deg_e is None and len(eligible) >= q:
                return eligible[q - 1]
    if target_longitude_deg_e is not None:
        if eligible:
            return min(eligible, key=lambda event: abs(_wrap180(event[3] - target_longitude_deg_e)))
        eligible_raw = [event for event in raw_events if _in_window(event[3], raw_window)]
        if eligible_raw:
            return min(eligible_raw, key=lambda event: abs(_wrap180(event[3] - target_longitude_deg_e)))
        return min(raw_events, key=lambda event: abs(_wrap180(event[3] - target_longitude_deg_e)))
    eligible_raw = [event for event in raw_events if _in_window(event[3], raw_window)]
    if len(eligible_raw) >= q:
        return eligible_raw[q - 1]
    if eligible_raw:
        return eligible_raw[-1]
    return raw_events[min(max(q, 1), len(raw_events)) - 1]


def _build_burns(
    config: dict[str, Any],
    *,
    apsis_pattern: list[str],
    delta_vs: list[float | None],
    alpha_values: list[float],
    warnings: list[str],
    q_sequence_override: list[int] | None = None,
) -> list[DesignManeuverBurn]:
    initial = config["initial"]
    longitude_cfg = config["longitude"]
    apsis_cfg = config["apsis"]
    supersync = config["supersynchronous_transfer"]
    t0 = parse_utc(str(initial["t0_epoch"]))
    mass = float(initial["m0_kg"])
    planning_window = longitude_cfg["planning_window_degE"]
    raw_window = longitude_cfg["raw_window_degE"]
    burns: list[DesignManeuverBurn] = []
    r, v = _initial_state_km(config)
    elapsed_s, r, v, longitude = _find_initial_burn_event(config, r, v, apsis_pattern[0])
    q_sequence = q_sequence_override or _q_sequence(
        config,
        len(apsis_pattern),
        _classify_orbit(
            config,
            float(initial["a_km"]) * (1.0 + float(initial["e"])),
            float(config["target"]["a_km"]),
        ),
    )

    for index, apsis in enumerate(apsis_pattern):
        longitude_ok = _in_window(longitude, planning_window)
        if not longitude_ok and _in_window(longitude, raw_window):
            warnings.append(f"第 {index + 1} 次点火经度只满足原始窗口，未满足规划收缩窗口。")
        elif not longitude_ok:
            warnings.append(f"第 {index + 1} 次点火经度未满足规划窗口。")

        target_post_a = None
        burn_type = "normal"
        fixed_tail = (
            bool(supersync["tail_fixed_enabled"])
            and len(apsis_pattern) >= 2
            and index >= len(apsis_pattern) - 2
            and apsis_pattern[-1] == "P"
        )
        if fixed_tail:
            burn_type = "tail_fixed"
            target_post_a = (
                float(supersync["a_tail_apogee_plus_fixed_km"])
                if index == len(apsis_pattern) - 2
                else float(supersync["a_tail_perigee_plus_fixed_km"])
            )
        elif index < max(0, len(apsis_pattern) - 2):
            burn_type = "front"

        alpha_deg = float(alpha_values[index])
        if target_post_a is not None and delta_vs[index] is None:
            solved_dv = _solve_dv_for_target_a(r, v, alpha_deg, target_post_a)
            if solved_dv is None:
                warnings.append(f"第 {index + 1} 次固定尾段半长轴反解 Δv 失败，已置为 0。")
                dv_mps = 0.0
            else:
                dv_mps = solved_dv
        else:
            raw_dv = delta_vs[index]
            dv_mps = 0.0 if raw_dv is None else max(0.0, float(raw_dv))
        burn_time = _burn_time_for_delta_v(config, mass, dv_mps)
        mass_after = max(1.0, mass - burn_time["propellant_kg"])
        pre_a, pre_e, pre_i, *_ = _rv_to_coe(r, v)
        v = v + (dv_mps / 1000.0) * _local_horizontal_direction(r, alpha_deg)
        current_a, current_e, current_i_rad, *_ = _rv_to_coe(r, v)
        current_i = math.degrees(current_i_rad)
        timestamp = t0 + timedelta(seconds=elapsed_s)
        beijing_time = (timestamp + BEIJING_OFFSET).strftime("%Y-%m-%d %H:%M:%S.%f")
        duration_ok = burn_time["total_burn_time_min"] <= float(config["burn_limit"]["max_total_burn_time_min"]) + 1.0e-9
        burns.append(
            DesignManeuverBurn(
                index=index + 1,
                burn_type=burn_type,
                apsis=apsis,
                elapsed_min=elapsed_s / 60.0,
                beijing_time=beijing_time,
                longitude_deg_e=longitude,
                delta_v_mps=dv_mps,
                alpha_deg=alpha_deg,
                target_post_a_km=target_post_a,
                total_burn_time_min=burn_time["total_burn_time_min"],
                propellant_kg=burn_time["propellant_kg"],
                post_a_km=current_a,
                post_e=current_e,
                post_i_deg=current_i,
                duration_ok=duration_ok,
                longitude_ok=longitude_ok,
            )
        )
        mass = mass_after
        if index < len(apsis_pattern) - 1:
            next_apsis_name = apsis_pattern[index + 1]
            q = q_sequence[index] if index < len(q_sequence) else int(apsis_cfg["q_AA_default"])
            target_longitude = None
            if apsis == "A" and next_apsis_name == "P":
                q = 1
            if index == len(apsis_pattern) - 2:
                target_longitude = float(config["target"]["lon_degE"])
            elapsed_s, r, v, longitude = _find_next_burn_event(
                config,
                r,
                v,
                elapsed_s,
                next_apsis_name,
                q,
                target_longitude_deg_e=target_longitude,
            )
    return burns


def _find_burn_time_and_longitude(
    config: dict[str, Any],
    apsis: str,
    a_km: float,
    e: float,
    *,
    start_elapsed_s: float,
    q: int,
    planning_window: list[float],
    search_limit: int,
) -> tuple[float, float]:
    mu = float(config["earth"]["mu_km3_s2"])
    period = 2.0 * math.pi * math.sqrt(max(1.0, a_km**3 / mu))
    mean_anomaly_rad = math.radians(float(config["initial"]["mean_anomaly_deg"]) % 360.0)
    target_m = math.pi if apsis == "A" else 0.0
    if start_elapsed_s <= 1.0e-9:
        delta_m = (target_m - mean_anomaly_rad) % (2.0 * math.pi)
        base_elapsed = delta_m / (2.0 * math.pi) * period
    else:
        base_elapsed = start_elapsed_s + (0.5 * period if apsis == "P" else period)
    matches = 0
    best_elapsed = base_elapsed
    best_lon = _subsatellite_longitude_for_apsis(config, apsis, best_elapsed)
    for offset in range(search_limit):
        elapsed = base_elapsed + offset * period
        lon = _subsatellite_longitude_for_apsis(config, apsis, elapsed)
        if _in_window(lon, planning_window):
            matches += 1
            if matches >= q:
                return elapsed, lon
        if offset == 0 or _window_distance(lon, planning_window) < _window_distance(best_lon, planning_window):
            best_elapsed = elapsed
            best_lon = lon
    return best_elapsed, best_lon


def _subsatellite_longitude_for_apsis(config: dict[str, Any], apsis: str, elapsed_s: float) -> float:
    initial = config["initial"]
    t0 = parse_utc(str(initial["t0_epoch"]))
    epoch = t0 + timedelta(seconds=elapsed_s)
    lon_node = float(initial["lon_node_deg"])
    inertial_raan = (lon_node + math.degrees(greenwich_angle_at_utc(t0))) % 360.0
    true_anomaly = 180.0 if apsis == "A" else 0.0
    inertial_longitude = (inertial_raan + float(initial["argp_deg"]) + true_anomaly) % 360.0
    east = (inertial_longitude - math.degrees(greenwich_angle_at_utc(epoch))) % 360.0
    return east


def _burn_time_for_delta_v(config: dict[str, Any], mass_kg: float, dv_mps: float) -> dict[str, float]:
    engine = config["engine"]
    limit = config["burn_limit"]
    use_settling = bool(engine["use_settling"])
    tau_set = float(engine["tau_set_s"]) if use_settling else 0.0
    isp_set = max(1.0, float(engine["Isp_set_s"]))
    f_set = max(0.0, float(engine["F_set_N"]))
    mdot_set = f_set / (isp_set * G0_M_S2) if use_settling else 0.0
    mp_set = min(max(0.0, mass_kg * 0.95), mdot_set * tau_set)
    m_after_set = max(1.0, mass_kg - mp_set)
    dv_set = isp_set * G0_M_S2 * math.log(mass_kg / m_after_set) if mp_set > 0.0 else 0.0
    dv_main = max(0.0, dv_mps - dv_set)
    isp_main_eff = max(1.0, float(engine["Isp_main_s"]) / (1.0 + float(engine["attitude_control_efficiency"])))
    c_main_eff = isp_main_eff * G0_M_S2
    mdot_main = max(1.0e-12, float(engine["F_main_N"]) / c_main_eff)
    mp_main = m_after_set * (1.0 - math.exp(-dv_main / c_main_eff)) if dv_main > 0.0 else 0.0
    tau_main = mp_main / mdot_main if mp_main > 0.0 else 0.0
    total_s = tau_set + tau_main if bool(limit["include_settling_in_burn_time"]) else tau_main
    return {
        "total_burn_time_min": total_s / 60.0,
        "propellant_kg": mp_set + mp_main,
    }


def _post_burn_elements(
    config: dict[str, Any],
    apsis: str,
    a_km: float,
    e: float,
    i_deg: float,
    dv_mps: float,
    alpha_deg: float,
    *,
    target_post_a: float | None,
) -> tuple[float, float, float]:
    if target_post_a is not None:
        post_a = target_post_a
    else:
        mu = float(config["earth"]["mu_km3_s2"])
        radius = a_km * (1.0 + e) if apsis == "A" else a_km * (1.0 - e)
        v = math.sqrt(max(0.0, mu * (2.0 / radius - 1.0 / a_km)))
        dv = dv_mps / 1000.0
        v_plus_sq = max(0.0, v * v + dv * dv + 2.0 * v * dv * math.cos(math.radians(alpha_deg)))
        denom = 2.0 / radius - v_plus_sq / mu
        post_a = 1.0 / denom if denom > 1.0e-12 else a_km
    radius = a_km * (1.0 + e) if apsis == "A" else a_km * (1.0 - e)
    if apsis == "A":
        post_e = max(0.0, min(0.98, radius / max(post_a, 1.0) - 1.0))
    else:
        post_e = max(0.0, min(0.98, 1.0 - radius / max(post_a, 1.0)))
    inclination_change = math.degrees(abs((dv_mps / 1000.0) * math.sin(math.radians(alpha_deg))) / 3.0)
    target_i = float(config["target"]["i_deg"])
    if i_deg >= target_i:
        post_i = max(target_i, i_deg - inclination_change)
    else:
        post_i = min(target_i, i_deg + inclination_change)
    return post_a, post_e, post_i


def _build_checks(
    config: dict[str, Any],
    burns: list[DesignManeuverBurn],
    *,
    ignore_uniform: bool = False,
) -> list[dict[str, Any]]:
    tolerance = config["terminal_tolerance"]
    target = config["target"]
    max_duration = float(config["burn_limit"]["max_total_burn_time_min"])
    max_spread = float(config["distribution"]["max_uniform_dv_spread_mps"])
    spread = _uniform_spread([burn.delta_v_mps for burn in burns if burn.burn_type != "tail_fixed"])
    checks = [
        {
            "item": "点火经度",
            "requirement": f"{config['longitude']['planning_window_degE'][0]:.1f}~{config['longitude']['planning_window_degE'][1]:.1f} degE",
            "result": "全部通过" if all(burn.longitude_ok for burn in burns) else "存在越界",
            "passed": all(burn.longitude_ok for burn in burns),
        },
        {
            "item": "总点火时长",
            "requirement": f"<= {max_duration:.1f} min",
            "result": f"最大 {max((burn.total_burn_time_min for burn in burns), default=0.0):.3f} min",
            "passed": all(burn.total_burn_time_min <= max_duration + 1.0e-9 for burn in burns),
        },
        {
            "item": "均匀性",
            "requirement": "不限制" if ignore_uniform else f"<= {max_spread:.1f} m/s",
            "result": f"{spread:.3f} m/s",
            "passed": ignore_uniform or spread <= max_spread + 1.0e-9,
        },
    ]
    if burns:
        final = burns[-1]
        checks.extend(
            [
                {
                    "item": "终端半长轴误差",
                    "requirement": f"<= {float(tolerance['a_km']):.3f} km",
                    "result": f"{final.post_a_km - float(target['a_km']):.3f} km",
                    "passed": abs(final.post_a_km - float(target["a_km"])) <= float(tolerance["a_km"]),
                },
                {
                    "item": "终端偏心率误差",
                    "requirement": f"<= {float(tolerance['e']):.6g}",
                    "result": f"{final.post_e - float(target['e']):.6g}",
                    "passed": abs(final.post_e - float(target["e"])) <= float(tolerance["e"]),
                },
                {
                    "item": "终端倾角误差",
                    "requirement": f"<= {float(tolerance['i_deg']):.3f} deg",
                    "result": f"{final.post_i_deg - float(target['i_deg']):.6f} deg",
                    "passed": abs(final.post_i_deg - float(target["i_deg"])) <= float(tolerance["i_deg"]),
                },
                {
                    "item": "终端经度误差",
                    "requirement": f"<= {float(tolerance['lon_deg']):.3f} deg",
                    "result": f"{_wrap180(final.longitude_deg_e - float(target['lon_degE'])):.6f} deg",
                    "passed": abs(_wrap180(final.longitude_deg_e - float(target["lon_degE"]))) <= float(
                        tolerance["lon_deg"]
                    ),
                },
            ]
        )
    return checks


def _uniform_spread(values: list[float]) -> float:
    if not values:
        return 0.0
    mean = sum(values) / len(values)
    return max(abs(value - mean) for value in values)


def _in_window(longitude_deg_e: float, window: list[float]) -> bool:
    value = longitude_deg_e % 360.0
    start = window[0] % 360.0
    end = window[1] % 360.0
    if start <= end:
        return start <= value <= end
    return value >= start or value <= end


def _window_distance(longitude_deg_e: float, window: list[float]) -> float:
    if _in_window(longitude_deg_e, window):
        return 0.0
    value = longitude_deg_e % 360.0
    return min(abs(_wrap180(value - window[0])), abs(_wrap180(value - window[1])))


def _wrap180(value: float) -> float:
    return ((value + 180.0) % 360.0) - 180.0


def config_from_orbital_elements(
    payload: dict[str, Any],
    elements: OrbitalElements,
    *,
    epoch_utc: str,
    mass_kg: float | None = None,
) -> dict[str, Any]:
    config = normalize_design_maneuver_strategy_payload(payload)
    config["initial"]["t0_epoch"] = format_utc(epoch_utc)
    config["initial"]["a_km"] = float(elements.semi_major_axis_km)
    config["initial"]["e"] = float(elements.eccentricity)
    config["initial"]["i_deg"] = float(elements.inclination_deg)
    config["initial"]["lon_node_deg"] = float(elements.raan_deg)
    config["initial"]["argp_deg"] = float(elements.argument_of_periapsis_deg)
    config["initial"]["mean_anomaly_deg"] = float(elements.true_anomaly_deg)
    if mass_kg is not None:
        config["initial"]["m0_kg"] = float(mass_kg)
    return normalize_design_maneuver_strategy_payload(config)
