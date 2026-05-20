from __future__ import annotations

import csv
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from itertools import product
import math
from pathlib import Path
from time import perf_counter
from typing import Any

import numpy as np

try:
    from scipy.optimize import minimize, minimize_scalar
except Exception:  # pragma: no cover - optional runtime fallback
    minimize = None
    minimize_scalar = None

from smart.domain.models import OrbitalElements
from smart.services.earth_orientation import format_utc, greenwich_angle_at_utc, parse_utc, utc_now_iso_z
from smart.services.thrust_direction import local_horizontal_yaw_direction

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
    flight_revolution: int = 0
    position_label: str = ""
    orbit_period_min: float = 0.0
    post_mass_kg: float = 0.0
    semi_major_axis_control_km: float = 0.0


@dataclass(frozen=True, slots=True)
class DesignManeuverResult:
    config: dict[str, Any]
    summary: dict[str, Any]
    burns: list[DesignManeuverBurn]
    checks: list[dict[str, Any]]
    warnings: list[str]


@dataclass(frozen=True, slots=True)
class ContinuousThrustManeuverParameter:
    maneuver_index: int
    flight_revolution: int
    position_label: str
    initial_burn_start_min: float
    initial_yaw_angle_deg: float
    burn_start_min: float
    settle_end_min: float
    cutoff_min: float
    yaw_angle_deg: float
    ignition_longitude_deg_e: float
    cutoff_longitude_deg_e: float
    delta_v_mps: float
    target_post_a_km: float
    total_burn_time_min: float
    settle_duration_min: float
    orbit_control_duration_min: float
    propellant_kg: float
    future_apogee_raise_propellant_kg: float
    future_perigee_lower_propellant_kg: float
    trim_propellant_kg: float
    objective_delta_g_kg: float
    objective_formula: str
    post_a_km: float
    post_e: float
    post_i_deg: float
    post_mass_kg: float
    duration_ok: bool
    longitude_ok: bool
    search_evaluations: int
    optimization_mode: str = ""


@dataclass(frozen=True, slots=True)
class ContinuousThrustOptimizationResult:
    parameters: list[ContinuousThrustManeuverParameter]
    total_propellant_kg: float
    objective_delta_g_kg: float
    time_step_s: float
    yaw_step_deg: float
    hard_constraint_passed: bool
    failed_constraints: list[str]
    orbit_history_rows: list[dict[str, Any]]


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
            "version": "V5.1_hard_constrained_phase_search",
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
            "mean_anomaly_deg": 1.85437,
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
            "first_post_a_control_km": None,
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
            "method": "SLSQP",
            "maxiter": 900,
            "maxfev": 25000,
            "q_fast_optimize_top_k": 10,
            "slsqp_top_k": 6,
            "slsqp_multistart_top_k": 2,
            "slsqp_maxiter": 120,
            "time_budget_sec": 30.0,
            "terminal_weight": 1.0e6,
            "longitude_weight": 1.0e8,
            "inclination_weight": 1.0e7,
            "eccentricity_weight": 1.0e8,
            "semi_major_axis_weight": 1.0e5,
            "duration_weight": 1.0e7,
            "uniform_weight": 1.0e3,
            "tail_weight": 1.0e9,
            "correction_weight": 1.0e2,
            "random_seed": 7,
        },
        "continuous_thrust_optimizer": {
            "time_step_s": 10.0,
            "yaw_step_deg": 0.05,
            "time_window_min": 40.0,
            "yaw_window_deg": 10.0,
            "coarse_time_step_s": 600.0,
            "coarse_yaw_step_deg": 5.0,
            "fine_time_window_min": 1.0,
            "fine_yaw_window_deg": 0.15,
            "search_integration_step_s": 300.0,
            "final_integration_step_s": 10.0,
            "history_sample_interval_s": 60.0,
        },
        "hard_constraint_planner": {
            "enabled": True,
            "q_AA_user": [],
            "q_AP_user": None,
            "q_AP_candidates": [0, 1, 2],
            "fixed_hp_targets_km": {},
            "hard_raw_window": True,
            "hard_planning_window": True,
            "prefilter_top_k": 10,
            "max_local_starts_per_sequence": 5,
            "local_maxiter": 45,
        },
    }


def normalize_design_maneuver_strategy_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    defaults = default_design_maneuver_strategy_payload()
    source = payload if isinstance(payload, dict) else {}
    source = _reference_config_to_internal(source)
    explicit_fixed_hp_targets = None
    source_hard_cfg = source.get("hard_constraint_planner")
    if isinstance(source_hard_cfg, dict) and "fixed_hp_targets_km" in source_hard_cfg:
        explicit_fixed_hp_targets = _parse_index_float_map(source_hard_cfg.get("fixed_hp_targets_km"))
    source = _coerce_hard_constraint_source(source)
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
            "inclination_weight",
            "eccentricity_weight",
            "semi_major_axis_weight",
            "duration_weight",
            "uniform_weight",
            "tail_weight",
            "correction_weight",
            "time_budget_sec",
        ),
        "continuous_thrust_optimizer": (
            "time_step_s",
            "yaw_step_deg",
            "time_window_min",
            "yaw_window_deg",
            "coarse_time_step_s",
            "coarse_yaw_step_deg",
            "fine_time_window_min",
            "fine_yaw_window_deg",
            "search_integration_step_s",
            "final_integration_step_s",
            "history_sample_interval_s",
        ),
    }.items():
        for key in keys:
            result[section][key] = float(result[section].get(key, defaults[section][key]))

    hard_cfg = result["hard_constraint_planner"]
    hard_defaults = defaults["hard_constraint_planner"]
    hard_cfg["enabled"] = bool(hard_cfg.get("enabled", hard_defaults["enabled"]))
    hard_cfg["hard_raw_window"] = bool(hard_cfg.get("hard_raw_window", hard_defaults["hard_raw_window"]))
    hard_cfg["hard_planning_window"] = bool(
        hard_cfg.get("hard_planning_window", hard_defaults["hard_planning_window"])
    )
    hard_cfg["prefilter_top_k"] = max(1, int(hard_cfg.get("prefilter_top_k", hard_defaults["prefilter_top_k"])))
    hard_cfg["max_local_starts_per_sequence"] = max(
        1,
        int(hard_cfg.get("max_local_starts_per_sequence", hard_defaults["max_local_starts_per_sequence"])),
    )
    hard_cfg["local_maxiter"] = max(1, int(hard_cfg.get("local_maxiter", hard_defaults["local_maxiter"])))
    hard_cfg["q_AA_user"] = _parse_int_list(hard_cfg.get("q_AA_user", []), minimum=1)
    q_ap_user = hard_cfg.get("q_AP_user")
    hard_cfg["q_AP_user"] = None if q_ap_user in (None, "") else max(0, int(float(str(q_ap_user).strip())))
    q_ap_candidates = hard_cfg.get("q_AP_candidates", hard_defaults["q_AP_candidates"])
    parsed_q_ap = _parse_int_list(q_ap_candidates, minimum=0)
    hard_cfg["q_AP_candidates"] = parsed_q_ap or [0, 1, 2]
    if explicit_fixed_hp_targets is not None:
        hard_cfg["fixed_hp_targets_km"] = explicit_fixed_hp_targets
    hard_cfg["fixed_hp_targets_km"] = _parse_index_float_map(hard_cfg.get("fixed_hp_targets_km", {}))

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
    result["optimizer"]["method"] = str(result["optimizer"].get("method", defaults["optimizer"]["method"])).upper()
    result["optimizer"]["maxiter"] = max(1, int(result["optimizer"].get("maxiter", 900)))
    result["optimizer"]["maxfev"] = max(1, int(result["optimizer"].get("maxfev", 25000)))
    result["optimizer"]["q_fast_optimize_top_k"] = max(1, int(result["optimizer"].get("q_fast_optimize_top_k", 10)))
    result["optimizer"]["slsqp_top_k"] = max(0, int(result["optimizer"].get("slsqp_top_k", 6)))
    result["optimizer"]["slsqp_multistart_top_k"] = max(0, int(result["optimizer"].get("slsqp_multistart_top_k", 2)))
    result["optimizer"]["slsqp_maxiter"] = max(1, int(result["optimizer"].get("slsqp_maxiter", 120)))
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

    result["apsis"]["q_sequence_user"] = _parse_int_list(result["apsis"].get("q_sequence_user", []), minimum=1)
    alpha_template = result["alpha"].get("initial_template_deg", [])
    result["alpha"]["initial_template_deg"] = [float(value) for value in alpha_template] if isinstance(alpha_template, list) else []
    for key in ("user_dv_template_mps", "weights"):
        values = result["distribution"].get(key, [])
        result["distribution"][key] = [float(value) for value in values] if isinstance(values, list) else []
    result["distribution"]["first_post_a_control_km"] = _optional_float(
        result["distribution"].get("first_post_a_control_km")
    )
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

    if orbit_type == "supersynchronous_transfer" and bool(config["hard_constraint_planner"]["enabled"]):
        return _plan_v51_hard_constrained(
            config,
            orbit_type=orbit_type,
            dv_total_est=dv_total_est,
            design_dv=design_dv,
            recommended_count=recommended_count,
            actual_count=actual_count,
            user_count=user_count,
            warnings=warnings,
        )

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
    alpha_values = phase_plan["alpha_values"]
    if phase_plan["burns"]:
        burns = phase_plan["burns"]
    else:
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
        "phase_alpha_optimized": bool(phase_plan["alpha_optimized"]),
        "optimized_propellant_kg": sum(burn.propellant_kg for burn in burns),
        "phase_lon_error_before_deg": phase_plan["initial_error_deg"],
        "duration_ok": duration_ok,
        "longitude_ok": longitude_ok,
        "uniform_spread_mps": uniform_spread,
        "uniform_ok": uniform_ok,
        "terminal_errors": terminal_errors,
        "phase_diagnostics": dict(phase_plan.get("diagnostics", {})),
    }
    return DesignManeuverResult(config=config, summary=summary, burns=burns, checks=checks, warnings=warnings)


def initial_design_maneuver_subsatellite_longitude_deg_e(payload: dict[str, Any] | None) -> float:
    config = normalize_design_maneuver_strategy_payload(payload)
    r, _v = _initial_state_km(config)
    return _longitude_deg(config, r, 0.0)


CONTINUOUS_THRUST_ORBIT_HISTORY_COLUMNS = [
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


def export_continuous_thrust_orbit_history_csv(
    result: ContinuousThrustOptimizationResult,
    output_path: str | Path,
) -> Path:
    path = Path(output_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CONTINUOUS_THRUST_ORBIT_HISTORY_COLUMNS)
        writer.writeheader()
        for row in result.orbit_history_rows:
            writer.writerow({key: row.get(key, "") for key in CONTINUOUS_THRUST_ORBIT_HISTORY_COLUMNS})
    return path


def continuous_thrust_result_to_maneuver_strategy_payload(
    result: ContinuousThrustOptimizationResult,
    config: dict[str, Any],
) -> dict[str, Any]:
    normalized_config = normalize_design_maneuver_strategy_payload(config)
    parameters = sorted(result.parameters, key=lambda item: item.maneuver_index)
    if not parameters:
        raise ValueError("Continuous thrust result has no maneuver parameters.")

    initial = normalized_config["initial"]
    engine = normalized_config["engine"]
    r_km, v_km_s = _initial_state_km(normalized_config)
    elapsed_s = 0.0
    mass_kg = float(initial["m0_kg"])
    maneuvers: list[dict[str, Any]] = []

    for parameter in parameters:
        burn_start_s = float(parameter.burn_start_min) * 60.0
        r_start, v_start = _propagate_state_to_elapsed(normalized_config, r_km, v_km_s, elapsed_s, burn_start_s)
        delta_deg, dv_direction = _maneuver_strategy_direction_fields(
            r_start,
            v_start,
            float(parameter.yaw_angle_deg),
        )
        total_burn_s = max(0.0, (float(parameter.cutoff_min) - float(parameter.burn_start_min)) * 60.0)
        settle_duration_s = max(0.0, min(float(parameter.settle_duration_min) * 60.0, total_burn_s))
        maneuvers.append(
            {
                "maneuver_index": int(parameter.maneuver_index),
                "Tn_start_min": float(parameter.burn_start_min),
                "burn_duration_min": total_burn_s / 60.0,
                "control_fuel_%": float(engine["attitude_control_efficiency"]) * 100.0,
                "settle_duration_s": settle_duration_s,
                "direction_mode": "local_horizontal_yaw",
                "yaw_angle_deg": float(parameter.yaw_angle_deg),
                "delta_deg": delta_deg,
                "dv_direction": dv_direction,
                "orbit_control_thrust_n": float(engine["F_main_N"]),
                "orbit_control_isp_s": float(engine["Isp_main_s"]),
                "settle_thrust_n": float(engine["F_set_N"]),
                "settle_isp_s": float(engine["Isp_set_s"]),
            }
        )

        r_km, v_km_s, mass_kg = _propagate_continuous_parameter_to_cutoff(
            normalized_config,
            r_start,
            v_start,
            mass_kg,
            parameter,
        )
        elapsed_s = float(parameter.cutoff_min) * 60.0

    return {
        "source": {
            "type": "design_continuous_thrust",
            "generated_utc": utc_now_iso_z(),
            "hard_constraint_passed": bool(result.hard_constraint_passed),
        },
        "launch_mass_kg": float(initial["m0_kg"]),
        "t0_epoch": format_utc(str(initial["t0_epoch"])),
        "t0_orbit": {
            "semi_major_axis_m": float(initial["a_km"]) * 1000.0,
            "eccentricity": float(initial["e"]),
            "inclination_deg": float(initial["i_deg"]),
            "argument_of_perigee_deg": float(initial["argp_deg"]),
            "raan_deg": float(initial["lon_node_deg"]),
            "mean_anomaly_deg": float(initial["mean_anomaly_deg"]),
        },
        "maneuver_count": len(maneuvers),
        "maneuvers": maneuvers,
    }


def _propagate_continuous_parameter_to_cutoff(
    config: dict[str, Any],
    r_start_km: np.ndarray,
    v_start_km_s: np.ndarray,
    mass_kg: float,
    parameter: ContinuousThrustManeuverParameter,
) -> tuple[np.ndarray, np.ndarray, float]:
    engine = config["engine"]
    state = np.asarray(
        [
            float(r_start_km[0]),
            float(r_start_km[1]),
            float(r_start_km[2]),
            float(v_start_km_s[0]),
            float(v_start_km_s[1]),
            float(v_start_km_s[2]),
            max(1.0, float(mass_kg)),
        ],
        dtype=float,
    )
    current_s = float(parameter.burn_start_min) * 60.0
    cutoff_s = float(parameter.cutoff_min) * 60.0
    if cutoff_s <= current_s:
        return state[:3].copy(), state[3:6].copy(), float(state[6])
    main_isp_s = max(1.0, float(engine["Isp_main_s"]) / (1.0 + float(engine["attitude_control_efficiency"])))
    phases = (
        (
            max(0.0, float(parameter.settle_duration_min) * 60.0),
            max(0.0, float(engine["F_set_N"])),
            max(1.0, float(engine["Isp_set_s"])),
        ),
        (
            max(0.0, cutoff_s - current_s - max(0.0, float(parameter.settle_duration_min) * 60.0)),
            max(0.0, float(engine["F_main_N"])),
            main_isp_s,
        ),
    )
    step_limit_s = max(0.5, float(config["continuous_thrust_optimizer"]["final_integration_step_s"]))
    for duration_s, thrust_n, isp_s in phases:
        phase_end_s = min(cutoff_s, current_s + duration_s)
        while current_s < phase_end_s - 1.0e-12:
            step_s = min(step_limit_s, phase_end_s - current_s)
            state = _rk4_low_thrust_step(config, current_s, state, step_s, float(parameter.yaw_angle_deg), thrust_n, isp_s)
            current_s += step_s
    return state[:3].copy(), state[3:6].copy(), float(state[6])


def _maneuver_strategy_direction_fields(r_km: np.ndarray, v_km_s: np.ndarray, yaw_angle_deg: float) -> tuple[float, int]:
    desired = _local_horizontal_direction(np.asarray(r_km, dtype=float), float(yaw_angle_deg))
    desired_norm = float(np.linalg.norm(desired))
    if desired_norm <= 1.0e-12:
        raise ValueError("Continuous thrust yaw direction produced a zero vector.")
    desired = desired / desired_norm
    z_component = max(-1.0, min(1.0, float(desired[2])))
    delta_rad = math.asin(z_component)
    delta_deg = math.degrees(delta_rad)
    candidates = _tangent_direction_candidates_from_delta(np.asarray(r_km, dtype=float), delta_rad)
    desired_index = max(
        range(len(candidates)),
        key=lambda index: float(np.dot(candidates[index], desired)),
    )
    velocity_scores = [float(np.dot(candidate, np.asarray(v_km_s, dtype=float))) for candidate in candidates]
    prograde_index = max(range(len(candidates)), key=lambda index: velocity_scores[index])
    dv_direction = 1 if desired_index == prograde_index else -1
    return delta_deg, dv_direction


def _tangent_direction_candidates_from_delta(r_km: np.ndarray, delta_rad: float) -> list[np.ndarray]:
    x, y, z = (float(value) for value in r_km)
    cos_delta = math.cos(delta_rad)
    if abs(cos_delta) <= 1.0e-12:
        return [np.asarray([0.0, 0.0, math.copysign(1.0, math.sin(delta_rad))], dtype=float)]
    rho = math.hypot(x, y)
    if rho <= 1.0e-12:
        return [np.asarray([math.cos(0.0) * cos_delta, math.sin(0.0) * cos_delta, math.sin(delta_rad)], dtype=float)]
    cos_term = max(-1.0, min(1.0, -z * math.tan(delta_rad) / rho))
    phase = math.atan2(y, x)
    offset = math.acos(cos_term)
    candidates: list[np.ndarray] = []
    for alpha in (phase + offset, phase - offset):
        candidates.append(
            np.asarray(
                [
                    math.cos(alpha) * cos_delta,
                    math.sin(alpha) * cos_delta,
                    math.sin(delta_rad),
                ],
                dtype=float,
            )
        )
    return candidates


def optimize_continuous_thrust_model_parameters(
    result: DesignManeuverResult,
) -> ContinuousThrustOptimizationResult:
    from smart.services.design_continuous_thrust_optimizer import optimize_continuous_thrust_chain_parameters

    return optimize_continuous_thrust_chain_parameters(result)

    config = normalize_design_maneuver_strategy_payload(result.config)
    continuous_cfg = config["continuous_thrust_optimizer"]
    time_step_s = max(1.0, float(continuous_cfg["time_step_s"]))
    yaw_step_deg = max(0.001, float(continuous_cfg["yaw_step_deg"]))
    time_window_s = max(time_step_s, float(continuous_cfg["time_window_min"]) * 60.0)
    yaw_window_deg = max(yaw_step_deg, float(continuous_cfg["yaw_window_deg"]))
    coarse_time_step_s = max(time_step_s, float(continuous_cfg["coarse_time_step_s"]))
    coarse_yaw_step_deg = max(yaw_step_deg, float(continuous_cfg["coarse_yaw_step_deg"]))
    fine_time_window_s = max(time_step_s, float(continuous_cfg["fine_time_window_min"]) * 60.0)
    fine_yaw_window_deg = max(yaw_step_deg, float(continuous_cfg["fine_yaw_window_deg"]))
    search_integration_step_s = max(1.0, float(continuous_cfg["search_integration_step_s"]))
    final_integration_step_s = max(0.5, float(continuous_cfg["final_integration_step_s"]))
    history_sample_interval_s = max(1.0, float(continuous_cfg["history_sample_interval_s"]))
    parameters: list[ContinuousThrustManeuverParameter] = []
    sorted_burns = sorted(result.burns, key=lambda item: item.index)
    if not sorted_burns:
        return ContinuousThrustOptimizationResult(
            parameters=[],
            total_propellant_kg=0.0,
            objective_delta_g_kg=0.0,
            time_step_s=time_step_s,
            yaw_step_deg=yaw_step_deg,
            hard_constraint_passed=False,
            failed_constraints=["连续推力参数"],
            orbit_history_rows=[],
        )
    burn = sorted_burns[0]
    r, v = _initial_state_km(config)
    state_elapsed_s = 0.0
    mass = float(config["initial"]["m0_kg"])
    orbit_history_rows: list[dict[str, Any]] = []
    _append_continuous_orbit_history_row(
        config,
        orbit_history_rows,
        0.0,
        r,
        v,
        mass,
        phase="coast",
        is_event_point=True,
    )
    initial_start_s = max(
        state_elapsed_s,
        float(burn.elapsed_min) * 60.0 - 0.5 * float(burn.total_burn_time_min) * 60.0,
    )
    initial_yaw_deg = float(burn.alpha_deg)
    target_post_a_km = float(burn.target_post_a_km if burn.target_post_a_km is not None else burn.post_a_km)
    target_control_metric, target_control_value_km = _continuous_control_target(config, burn, target_post_a_km)
    formula = "m + mA + mP"
    yaw_low, yaw_high = _continuous_yaw_bounds_for_burn(config, sorted_burns, 0, burn)
    mode_specs = (
        ("fixed_start_yaw", "固定t优化δ", False),
        ("start_yaw", "优化t和δ", True),
    )
    final_history_candidate: dict[str, Any] | None = None
    for mode_key, mode_label, optimize_time in mode_specs:
        coarse = _search_first_continuous_thrust_burn(
            config,
            r,
            v,
            state_elapsed_s,
            mass,
            burn,
            target_post_a_km,
            initial_start_s,
            initial_yaw_deg,
            formula,
            time_window_s=time_window_s if optimize_time else 0.0,
            yaw_window_deg=yaw_window_deg,
            time_step_s=coarse_time_step_s,
            yaw_step_deg=coarse_yaw_step_deg,
            yaw_low=yaw_low,
            yaw_high=yaw_high,
            nominal_start_s=initial_start_s,
            nominal_yaw_deg=initial_yaw_deg,
            integration_step_s=search_integration_step_s,
        )
        fine_center_start = coarse["burn_start_s"] if coarse is not None else initial_start_s
        fine_center_yaw = coarse["yaw_angle_deg"] if coarse is not None else initial_yaw_deg
        fine = _search_first_continuous_thrust_burn(
            config,
            r,
            v,
            state_elapsed_s,
            mass,
            burn,
            target_post_a_km,
            fine_center_start,
            fine_center_yaw,
            formula,
            time_window_s=fine_time_window_s if optimize_time else 0.0,
            yaw_window_deg=fine_yaw_window_deg,
            time_step_s=time_step_s,
            yaw_step_deg=yaw_step_deg,
            yaw_low=yaw_low,
            yaw_high=yaw_high,
            nominal_start_s=initial_start_s,
            nominal_yaw_deg=initial_yaw_deg,
            integration_step_s=search_integration_step_s,
        )
        best = fine or coarse
        if best is None:
            best = _continuous_first_thrust_fallback_candidate(
                config,
                r,
                v,
                state_elapsed_s,
                mass,
                burn,
                target_post_a_km,
                initial_start_s,
                initial_yaw_deg,
                formula,
            )
        best_search_evaluations = int(best.get("search_evaluations", 0))
        final_best = _evaluate_first_continuous_thrust_candidate(
            config,
            *(_propagate_state_to_elapsed(config, r, v, state_elapsed_s, float(best["burn_start_s"]))),
            float(best["burn_start_s"]),
            mass,
            burn,
            target_post_a_km,
            float(best["yaw_angle_deg"]),
            formula,
            integration_step_s=final_integration_step_s,
        )
        if final_best is not None:
            final_best["search_evaluations"] = best_search_evaluations
            best = final_best
        search_evaluations = int(best.get("search_evaluations", 0))
        if coarse is not None:
            search_evaluations += int(coarse.get("search_evaluations", 0))
        displayed_delta_v_mps = float(best["delta_v_mps"])
        displayed_propellant_kg = float(best["propellant_kg"])
        displayed_post_i_deg = float(best["post_i_deg"])
        displayed_post_mass_kg = float(best["post_mass_kg"])
        m1 = float(best["future_apogee_raise_propellant_kg"])
        m2 = float(best["future_perigee_lower_propellant_kg"])
        parameters.append(
            ContinuousThrustManeuverParameter(
                maneuver_index=int(burn.index),
                flight_revolution=int(burn.flight_revolution),
                position_label=burn.position_label or ("远地点" if burn.apsis == "A" else "近地点"),
                initial_burn_start_min=initial_start_s / 60.0,
                initial_yaw_angle_deg=initial_yaw_deg,
                burn_start_min=float(best["burn_start_s"]) / 60.0,
                settle_end_min=float(best["settle_end_s"]) / 60.0,
                cutoff_min=float(best["cutoff_s"]) / 60.0,
                yaw_angle_deg=float(best["yaw_angle_deg"]),
                ignition_longitude_deg_e=float(best["ignition_longitude_deg_e"]),
                cutoff_longitude_deg_e=float(best["cutoff_longitude_deg_e"]),
                delta_v_mps=displayed_delta_v_mps,
                target_post_a_km=target_post_a_km,
                total_burn_time_min=float(best["total_burn_time_s"]) / 60.0,
                settle_duration_min=float(best["settle_duration_s"]) / 60.0,
                orbit_control_duration_min=float(best["orbit_control_duration_s"]) / 60.0,
                propellant_kg=displayed_propellant_kg,
                future_apogee_raise_propellant_kg=m1,
                future_perigee_lower_propellant_kg=m2,
                trim_propellant_kg=0.0,
                objective_delta_g_kg=float(best["objective_delta_g_kg"]),
                objective_formula=formula,
                post_a_km=float(best["post_a_km"]),
                post_e=float(best["post_e"]),
                post_i_deg=displayed_post_i_deg,
                post_mass_kg=displayed_post_mass_kg,
                duration_ok=bool(best["duration_ok"]),
                longitude_ok=bool(best["longitude_ok"]),
                search_evaluations=search_evaluations,
                optimization_mode=mode_label,
            )
        )
        final_history_candidate = best
    if final_history_candidate is not None:
        _append_continuous_coast_history(
            config,
            orbit_history_rows,
            r,
            v,
            mass,
            state_elapsed_s,
            float(final_history_candidate["burn_start_s"]),
            sample_interval_s=history_sample_interval_s,
        )
        _append_continuous_burn_history(
            config,
            orbit_history_rows,
            r,
            v,
            mass,
            state_elapsed_s,
            float(final_history_candidate["burn_start_s"]),
            float(final_history_candidate["yaw_angle_deg"]),
            target_control_metric,
            target_control_value_km,
            sample_interval_s=history_sample_interval_s,
            integration_step_s=final_integration_step_s,
        )
    failed_constraints = _continuous_first_burn_failed_constraints(parameters)
    return ContinuousThrustOptimizationResult(
        parameters=parameters,
        total_propellant_kg=sum(item.propellant_kg for item in parameters),
        objective_delta_g_kg=sum(item.objective_delta_g_kg for item in parameters),
        time_step_s=time_step_s,
        yaw_step_deg=yaw_step_deg,
        hard_constraint_passed=not failed_constraints,
        failed_constraints=failed_constraints,
        orbit_history_rows=orbit_history_rows,
    )


def _search_first_continuous_thrust_burn(
    config: dict[str, Any],
    r: np.ndarray,
    v: np.ndarray,
    state_elapsed_s: float,
    mass_kg: float,
    burn: DesignManeuverBurn,
    target_post_a_km: float,
    center_start_s: float,
    center_yaw_deg: float,
    objective_formula: str,
    *,
    time_window_s: float,
    yaw_window_deg: float,
    time_step_s: float,
    yaw_step_deg: float,
    yaw_low: float,
    yaw_high: float,
    nominal_start_s: float,
    nominal_yaw_deg: float,
    integration_step_s: float,
) -> dict[str, Any] | None:
    start_low = max(float(state_elapsed_s), float(center_start_s) - float(time_window_s))
    start_high = max(start_low, float(center_start_s) + float(time_window_s))
    starts = _grid_values_around(float(center_start_s), start_low, start_high, float(time_step_s))
    yaw_values = _grid_values_around(
        float(center_yaw_deg),
        max(float(yaw_low), float(center_yaw_deg) - float(yaw_window_deg)),
        min(float(yaw_high), float(center_yaw_deg) + float(yaw_window_deg)),
        float(yaw_step_deg),
    )
    best: dict[str, Any] | None = None
    evaluations = 0
    state_cache: dict[float, tuple[np.ndarray, np.ndarray]] = {}
    for start_s in starts:
        state_key = round(float(start_s), 6)
        if state_key not in state_cache:
            state_cache[state_key] = _propagate_state_to_elapsed(config, r, v, state_elapsed_s, float(start_s))
        r_start, v_start = state_cache[state_key]
        for yaw_deg in yaw_values:
            evaluations += 1
            candidate = _evaluate_first_continuous_thrust_candidate(
                config,
                r_start,
                v_start,
                float(start_s),
                mass_kg,
                burn,
                target_post_a_km,
                float(yaw_deg),
                objective_formula,
                integration_step_s=integration_step_s,
            )
            if candidate is None:
                continue
            candidate["seed_time_offset_s"] = abs(float(start_s) - float(nominal_start_s))
            candidate["seed_yaw_offset_deg"] = abs(float(yaw_deg) - float(nominal_yaw_deg))
            candidate["search_evaluations"] = evaluations
            if best is None or _continuous_candidate_score(candidate) < _continuous_candidate_score(best):
                best = candidate
    if best is not None:
        best["search_evaluations"] = evaluations
    return best


def _evaluate_first_continuous_thrust_candidate(
    config: dict[str, Any],
    r_start: np.ndarray,
    v_start: np.ndarray,
    burn_start_s: float,
    mass_kg: float,
    burn: DesignManeuverBurn,
    target_post_a_km: float,
    yaw_angle_deg: float,
    objective_formula: str,
    *,
    integration_step_s: float,
) -> dict[str, Any] | None:
    target_control_metric, target_control_value_km = _continuous_control_target(config, burn, target_post_a_km)
    burn_result = _integrate_low_thrust_to_target_metric(
        config,
        r_start,
        v_start,
        mass_kg,
        burn_start_s,
        target_control_metric,
        target_control_value_km,
        yaw_angle_deg,
        integration_step_s=integration_step_s,
    )
    if burn_result is None:
        return None
    r_cutoff = np.asarray(burn_result["r_cutoff"], dtype=float)
    v_cutoff = np.asarray(burn_result["v_cutoff"], dtype=float)
    cutoff_s = float(burn_result["cutoff_s"])
    total_burn_s = float(burn_result["total_burn_time_s"])
    propellant_kg = max(0.0, float(mass_kg) - float(burn_result["post_mass_kg"]))
    post_a, post_e, post_i_rad, *_ = _rv_to_coe(r_cutoff, v_cutoff, mu=float(config["earth"]["mu_km3_s2"]))
    post_i_deg = math.degrees(post_i_rad)
    future = _first_burn_future_impulse_propellant(config, post_a, post_e, post_i_deg, float(burn_result["post_mass_kg"]))
    future_apogee_propellant_kg = float(future["apogee_propellant_kg"])
    future_perigee_propellant_kg = float(future["perigee_propellant_kg"])
    objective_delta_g_kg = propellant_kg + future_apogee_propellant_kg + future_perigee_propellant_kg
    ignition_longitude = _longitude_deg(config, r_start, burn_start_s)
    cutoff_longitude = _longitude_deg(config, r_cutoff, cutoff_s)
    target_ra_km = float(burn.post_a_km) * (1.0 + float(burn.post_e))
    return {
        "burn_start_s": float(burn_start_s),
        "settle_end_s": float(burn_start_s) + float(burn_result["settle_duration_s"]),
        "cutoff_s": cutoff_s,
        "yaw_angle_deg": float(yaw_angle_deg),
        "ignition_longitude_deg_e": ignition_longitude,
        "cutoff_longitude_deg_e": cutoff_longitude,
        "delta_v_mps": float(burn_result["delta_v_mps"]),
        "total_burn_time_s": total_burn_s,
        "settle_duration_s": float(burn_result["settle_duration_s"]),
        "orbit_control_duration_s": float(burn_result["orbit_control_duration_s"]),
        "propellant_kg": propellant_kg,
        "trim_delta_v_mps": 0.0,
        "trim_propellant_kg": 0.0,
        "future_apogee_raise_propellant_kg": future_apogee_propellant_kg,
        "future_perigee_lower_propellant_kg": future_perigee_propellant_kg,
        "future_apogee_delta_v_mps": float(future["apogee_delta_v_mps"]),
        "future_perigee_delta_v_mps": float(future["perigee_delta_v_mps"]),
        "objective_delta_g_kg": objective_delta_g_kg,
        "terminal_lon_excess_deg": 0.0,
        "terminal_i_excess_deg": 0.0,
        "terminal_i_error_abs_deg": 0.0,
        "terminal_e_excess": 0.0,
        "terminal_e_error_abs": 0.0,
        "apsis_timing_error_s": 0.0,
        "apogee_radius_error_km": abs(float(post_a) * (1.0 + float(post_e)) - target_ra_km),
        "inclination_error_deg": 0.0,
        "inclination_excess_deg": 0.0,
        "post_a_km": float(post_a),
        "post_e": float(post_e),
        "post_i_deg": post_i_deg,
        "post_mass_kg": max(1.0, float(burn_result["post_mass_kg"])),
        "duration_ok": total_burn_s <= float(config["burn_limit"]["max_total_burn_time_min"]) * 60.0 + 1.0e-9,
        "longitude_ok": _in_window(ignition_longitude, config["longitude"]["planning_window_degE"]),
        "r_cutoff": r_cutoff,
        "v_cutoff": v_cutoff,
        "pulse_time_offset_s": abs(float(burn_start_s) - float(burn.elapsed_min) * 60.0),
        "pulse_yaw_offset_deg": abs(float(yaw_angle_deg) - float(burn.alpha_deg)),
        "objective_formula": objective_formula,
    }


def _first_burn_future_impulse_propellant(
    config: dict[str, Any],
    post_a_km: float,
    post_e: float,
    post_i_deg: float,
    post_mass_kg: float,
) -> dict[str, float]:
    mu = float(config["earth"]["mu_km3_s2"])
    sync_radius_km = float(config["target"]["a_km"])
    ra_km = float(post_a_km) * (1.0 + float(post_e))
    if sync_radius_km <= 0.0 or ra_km <= sync_radius_km:
        return {
            "apogee_delta_v_mps": float("inf"),
            "perigee_delta_v_mps": float("inf"),
            "apogee_propellant_kg": float("inf"),
            "perigee_propellant_kg": float("inf"),
        }
    transfer_a_km = 0.5 * (ra_km + sync_radius_km)
    current_apogee_speed_km_s = math.sqrt(max(0.0, mu * (2.0 / ra_km - 1.0 / float(post_a_km))))
    transfer_apogee_speed_km_s = math.sqrt(max(0.0, mu * (2.0 / ra_km - 1.0 / transfer_a_km)))
    di_rad = math.radians(abs(float(post_i_deg) - float(config["target"]["i_deg"])))
    apogee_delta_v_km_s = math.sqrt(
        max(
            0.0,
            current_apogee_speed_km_s**2
            + transfer_apogee_speed_km_s**2
            - 2.0 * current_apogee_speed_km_s * transfer_apogee_speed_km_s * math.cos(di_rad),
        )
    )
    apogee_delta_v_mps = apogee_delta_v_km_s * 1000.0
    apogee_propellant_kg = _burn_time_for_delta_v(config, post_mass_kg, apogee_delta_v_mps)["propellant_kg"]
    mass_after_apogee = max(1.0, float(post_mass_kg) - apogee_propellant_kg)
    transfer_perigee_speed_km_s = math.sqrt(max(0.0, mu * (2.0 / sync_radius_km - 1.0 / transfer_a_km)))
    sync_speed_km_s = math.sqrt(max(0.0, mu / sync_radius_km))
    perigee_delta_v_mps = abs(transfer_perigee_speed_km_s - sync_speed_km_s) * 1000.0
    perigee_propellant_kg = _burn_time_for_delta_v(config, mass_after_apogee, perigee_delta_v_mps)["propellant_kg"]
    return {
        "apogee_delta_v_mps": apogee_delta_v_mps,
        "perigee_delta_v_mps": perigee_delta_v_mps,
        "apogee_propellant_kg": apogee_propellant_kg,
        "perigee_propellant_kg": perigee_propellant_kg,
    }


def _continuous_first_thrust_fallback_candidate(
    config: dict[str, Any],
    r: np.ndarray,
    v: np.ndarray,
    state_elapsed_s: float,
    mass_kg: float,
    burn: DesignManeuverBurn,
    target_post_a_km: float,
    initial_start_s: float,
    initial_yaw_deg: float,
    objective_formula: str,
) -> dict[str, Any]:
    start_s = max(float(state_elapsed_s), float(initial_start_s))
    r_start, v_start = _propagate_state_to_elapsed(config, r, v, state_elapsed_s, start_s)
    candidate = _evaluate_first_continuous_thrust_candidate(
        config,
        r_start,
        v_start,
        start_s,
        mass_kg,
        burn,
        target_post_a_km,
        initial_yaw_deg,
        objective_formula,
        integration_step_s=max(1.0, float(config["continuous_thrust_optimizer"]["search_integration_step_s"])),
    )
    if candidate is None:
        raise ValueError("第一次连续点火未能在最大点火时长内达到目标近地点高度。")
    candidate["search_evaluations"] = 0
    return candidate


def _search_continuous_thrust_burn(
    config: dict[str, Any],
    r: np.ndarray,
    v: np.ndarray,
    state_elapsed_s: float,
    mass_kg: float,
    burn: DesignManeuverBurn,
    target_post_a_km: float,
    center_start_s: float,
    center_yaw_deg: float,
    future_m1_kg: float,
    future_m2_kg: float,
    objective_formula: str,
    *,
    time_window_s: float,
    yaw_window_deg: float,
    time_step_s: float,
    yaw_step_deg: float,
    yaw_low: float,
    yaw_high: float,
    nominal_start_s: float,
    nominal_yaw_deg: float,
    integration_step_s: float,
) -> dict[str, Any] | None:
    start_low = max(float(state_elapsed_s), float(center_start_s) - float(time_window_s))
    start_high = max(start_low, float(center_start_s) + float(time_window_s))
    if str(burn.apsis).upper() == "P":
        start_high = max(start_low, min(start_high, float(burn.elapsed_min) * 60.0))
    starts = _grid_values_around(float(center_start_s), start_low, start_high, float(time_step_s))
    yaw_values = _grid_values_around(
        float(center_yaw_deg),
        max(float(yaw_low), float(center_yaw_deg) - float(yaw_window_deg)),
        min(float(yaw_high), float(center_yaw_deg) + float(yaw_window_deg)),
        float(yaw_step_deg),
    )
    best: dict[str, Any] | None = None
    evaluations = 0
    state_cache: dict[float, tuple[np.ndarray, np.ndarray]] = {}
    for start_s in starts:
        state_key = round(float(start_s), 6)
        if state_key not in state_cache:
            state_cache[state_key] = _propagate_state_to_elapsed(config, r, v, state_elapsed_s, float(start_s))
        r_start, v_start = state_cache[state_key]
        for yaw_deg in yaw_values:
            evaluations += 1
            candidate = _evaluate_continuous_thrust_candidate(
                config,
                r_start,
                v_start,
                float(start_s),
                mass_kg,
                burn,
                target_post_a_km,
                float(yaw_deg),
                future_m1_kg,
                future_m2_kg,
                objective_formula,
                integration_step_s=integration_step_s,
            )
            if candidate is None:
                continue
            candidate["seed_time_offset_s"] = abs(float(start_s) - float(nominal_start_s))
            candidate["seed_yaw_offset_deg"] = abs(float(yaw_deg) - float(nominal_yaw_deg))
            candidate["search_evaluations"] = evaluations
            if best is None or _continuous_candidate_score(candidate) < _continuous_candidate_score(best):
                best = candidate
    if best is not None:
        best["search_evaluations"] = evaluations
    return best


def _evaluate_continuous_thrust_candidate(
    config: dict[str, Any],
    r_start: np.ndarray,
    v_start: np.ndarray,
    burn_start_s: float,
    mass_kg: float,
    burn: DesignManeuverBurn,
    target_post_a_km: float,
    yaw_angle_deg: float,
    future_m1_kg: float,
    future_m2_kg: float,
    objective_formula: str,
    *,
    integration_step_s: float,
) -> dict[str, Any] | None:
    target_control_metric, target_control_value_km = _continuous_control_target(config, burn, target_post_a_km)
    burn_result = _integrate_low_thrust_to_target_metric(
        config,
        r_start,
        v_start,
        mass_kg,
        burn_start_s,
        target_control_metric,
        target_control_value_km,
        yaw_angle_deg,
        integration_step_s=integration_step_s,
    )
    if burn_result is None:
        return None
    r_cutoff = np.asarray(burn_result["r_cutoff"], dtype=float)
    v_cutoff = np.asarray(burn_result["v_cutoff"], dtype=float)
    cutoff_s = float(burn_result["cutoff_s"])
    total_burn_s = float(burn_result["total_burn_time_s"])
    propellant_kg = max(0.0, float(mass_kg) - float(burn_result["post_mass_kg"]))
    post_a, post_e, post_i_rad, *_ = _rv_to_coe(r_cutoff, v_cutoff, mu=float(config["earth"]["mu_km3_s2"]))
    post_i_deg = math.degrees(post_i_rad)
    target_i_deg = float(burn.post_i_deg)
    trim_dv_mps = (
        _inclination_trim_delta_v_mps(
            v_cutoff,
            post_i_deg,
            target_i_deg,
            float(config["terminal_tolerance"]["i_deg"]),
        )
        if str(burn.apsis).upper() == "A"
        else 0.0
    )
    trim_propellant_kg = _propellant_for_delta_v(
        max(1.0, float(burn_result["post_mass_kg"])),
        trim_dv_mps,
        float(config["engine"]["Isp_set_s"]),
    )
    objective_delta_g_kg = propellant_kg + trim_propellant_kg
    if objective_formula != "m + m3":
        objective_delta_g_kg += float(future_m1_kg) + float(future_m2_kg)
    ignition_longitude = _longitude_deg(config, r_start, burn_start_s)
    cutoff_longitude = _longitude_deg(config, r_cutoff, cutoff_s)
    terminal_lon_excess_deg = 0.0
    if objective_formula == "m + m3":
        terminal_lon_excess_deg = abs(_wrap180(cutoff_longitude - float(config["target"]["lon_degE"])))
    terminal_i_excess_deg = 0.0
    terminal_i_error_abs_deg = 0.0
    terminal_e_excess = 0.0
    terminal_e_error_abs = 0.0
    apsis_timing_error_s = 0.0
    if str(burn.apsis).upper() == "P":
        pulse_time_s = float(burn.elapsed_min) * 60.0
        if pulse_time_s < float(burn_start_s):
            apsis_timing_error_s = float(burn_start_s) - pulse_time_s
        elif pulse_time_s > cutoff_s:
            apsis_timing_error_s = pulse_time_s - cutoff_s
    apogee_radius_error_km = 0.0
    inclination_error_deg = abs(post_i_deg - target_i_deg)
    inclination_excess_deg = max(0.0, inclination_error_deg - float(config["terminal_tolerance"]["i_deg"]))
    if str(burn.apsis).upper() == "A":
        target_ra_km = float(burn.post_a_km) * (1.0 + float(burn.post_e))
        apogee_radius_error_km = abs(float(post_a) * (1.0 + float(post_e)) - target_ra_km)
    if objective_formula == "m + m3":
        terminal_i_error_abs_deg = abs(post_i_deg - target_i_deg)
        terminal_i_excess_deg = max(0.0, terminal_i_error_abs_deg - float(config["terminal_tolerance"]["i_deg"]))
        terminal_e_error_abs = abs(float(post_e) - float(config["target"]["e"]))
        terminal_e_excess = max(0.0, terminal_e_error_abs - float(config["terminal_tolerance"]["e"]))
    return {
        "burn_start_s": float(burn_start_s),
        "settle_end_s": float(burn_start_s) + float(burn_result["settle_duration_s"]),
        "cutoff_s": cutoff_s,
        "yaw_angle_deg": float(yaw_angle_deg),
        "ignition_longitude_deg_e": ignition_longitude,
        "cutoff_longitude_deg_e": cutoff_longitude,
        "delta_v_mps": float(burn_result["delta_v_mps"]),
        "total_burn_time_s": total_burn_s,
        "settle_duration_s": float(burn_result["settle_duration_s"]),
        "orbit_control_duration_s": float(burn_result["orbit_control_duration_s"]),
        "propellant_kg": propellant_kg,
        "trim_delta_v_mps": trim_dv_mps,
        "trim_propellant_kg": trim_propellant_kg,
        "objective_delta_g_kg": objective_delta_g_kg,
        "terminal_lon_excess_deg": terminal_lon_excess_deg,
        "terminal_i_excess_deg": terminal_i_excess_deg,
        "terminal_i_error_abs_deg": terminal_i_error_abs_deg,
        "terminal_e_excess": terminal_e_excess,
        "terminal_e_error_abs": terminal_e_error_abs,
        "apsis_timing_error_s": apsis_timing_error_s,
        "apogee_radius_error_km": apogee_radius_error_km,
        "inclination_error_deg": inclination_error_deg,
        "inclination_excess_deg": inclination_excess_deg,
        "post_a_km": float(post_a),
        "post_e": float(post_e),
        "post_i_deg": post_i_deg,
        "post_mass_kg": max(1.0, float(burn_result["post_mass_kg"])),
        "duration_ok": total_burn_s <= float(config["burn_limit"]["max_total_burn_time_min"]) * 60.0 + 1.0e-9,
        "longitude_ok": _in_window(ignition_longitude, config["longitude"]["planning_window_degE"]),
        "r_cutoff": r_cutoff,
        "v_cutoff": v_cutoff,
        "pulse_time_offset_s": abs(float(burn_start_s) - float(burn.elapsed_min) * 60.0),
        "pulse_yaw_offset_deg": abs(float(yaw_angle_deg) - float(burn.alpha_deg)),
    }


def _integrate_low_thrust_to_target_metric(
    config: dict[str, Any],
    r_start: np.ndarray,
    v_start: np.ndarray,
    mass_kg: float,
    burn_start_s: float,
    target_metric: str,
    target_value_km: float,
    yaw_angle_deg: float,
    *,
    integration_step_s: float,
) -> dict[str, Any] | None:
    engine = config["engine"]
    burn_limit = config["burn_limit"]
    max_burn_s = max(0.0, float(burn_limit["max_total_burn_time_min"]) * 60.0)
    if max_burn_s <= 0.0 or mass_kg <= 1.0:
        return None
    use_settling = bool(engine.get("use_settling", True))
    include_settling = bool(burn_limit.get("include_settling_in_burn_time", True))
    settle_limit_s = float(engine.get("tau_set_s", 0.0)) if use_settling else 0.0
    if not include_settling:
        settle_limit_s = 0.0
    settle_limit_s = max(0.0, min(settle_limit_s, max_burn_s))
    main_isp_s = max(1.0, float(engine["Isp_main_s"]) / (1.0 + float(engine["attitude_control_efficiency"])))
    phases = (
        ("settle", settle_limit_s, max(0.0, float(engine["F_set_N"])), max(1.0, float(engine["Isp_set_s"]))),
        (
            "orbit_control",
            max_burn_s - settle_limit_s,
            max(0.0, float(engine["F_main_N"])),
            main_isp_s,
        ),
    )
    state = np.asarray([*np.asarray(r_start, dtype=float), *np.asarray(v_start, dtype=float), float(mass_kg)], dtype=float)
    current_s = float(burn_start_s)
    elapsed_burn_s = 0.0
    start_value = _continuous_target_metric_value(config, state[:3], state[3:6], target_metric)
    target_sign = 1.0 if float(target_value_km) >= start_value else -1.0
    previous_state = state.copy()
    previous_s = current_s
    previous_error = target_sign * (start_value - float(target_value_km))
    reached = abs(previous_error) <= 1.0e-6
    active_phase = "settle" if settle_limit_s > 0.0 else "orbit_control"
    ideal_delta_v_mps = 0.0
    if reached:
        return _low_thrust_result_from_state(
            config,
            state,
            burn_start_s,
            current_s,
            yaw_angle_deg,
            settle_duration_s=0.0,
            phase=active_phase,
        )
    for phase_name, phase_duration_s, thrust_n, isp_s in phases:
        phase_elapsed_s = 0.0
        while phase_elapsed_s < phase_duration_s - 1.0e-9 and elapsed_burn_s < max_burn_s - 1.0e-9:
            step_s = min(float(integration_step_s), phase_duration_s - phase_elapsed_s, max_burn_s - elapsed_burn_s)
            previous_state = state.copy()
            previous_s = current_s
            state = _rk4_low_thrust_step(config, current_s, state, step_s, yaw_angle_deg, thrust_n, isp_s)
            current_s += step_s
            phase_elapsed_s += step_s
            elapsed_burn_s += step_s
            try:
                current_value = _continuous_target_metric_value(config, state[:3], state[3:6], target_metric)
            except Exception:
                return None
            current_error = target_sign * (current_value - float(target_value_km))
            if current_error >= 0.0 and previous_error <= 0.0:
                state, current_s = _refine_low_thrust_metric_crossing(
                    config,
                    previous_state,
                    previous_s,
                    step_s,
                    yaw_angle_deg,
                    thrust_n,
                    isp_s,
                    target_metric,
                    target_value_km,
                    target_sign,
                )
                elapsed_burn_s = current_s - float(burn_start_s)
                ideal_delta_v_mps += _ideal_low_thrust_delta_v_mps(thrust_n, float(previous_state[6]), current_s - previous_s)
                reached = True
                break
            ideal_delta_v_mps += _ideal_low_thrust_delta_v_mps(thrust_n, float(previous_state[6]), step_s)
            previous_error = current_error
        if reached:
            active_phase = phase_name
            break
    if not reached:
        return None
    settle_duration_s = min(settle_limit_s, elapsed_burn_s)
    return _low_thrust_result_from_state(
        config,
        state,
        burn_start_s,
        current_s,
        yaw_angle_deg,
        settle_duration_s=settle_duration_s,
        phase=active_phase,
        delta_v_mps=ideal_delta_v_mps,
    )


def _continuous_control_target(
    _config: dict[str, Any],
    burn: DesignManeuverBurn,
    target_post_a_km: float,
) -> tuple[str, float]:
    if str(burn.apsis).upper() == "A":
        return "rp", float(burn.post_a_km) * (1.0 - float(burn.post_e))
    if str(burn.apsis).upper() == "P":
        return "ra", float(burn.post_a_km) * (1.0 + float(burn.post_e))
    return "a", float(target_post_a_km)


def _continuous_target_metric_value(
    config: dict[str, Any],
    r: np.ndarray,
    v: np.ndarray,
    metric: str,
) -> float:
    if metric == "a":
        return _semi_major_axis_from_rv(r, v, float(config["earth"]["mu_km3_s2"]))
    a_km, e_val, *_ = _rv_to_coe(r, v, mu=float(config["earth"]["mu_km3_s2"]))
    if metric == "rp":
        return float(a_km) * (1.0 - float(e_val))
    if metric == "ra":
        return float(a_km) * (1.0 + float(e_val))
    raise ValueError(f"Unsupported continuous target metric: {metric}")


def _low_thrust_result_from_state(
    config: dict[str, Any],
    state: np.ndarray,
    burn_start_s: float,
    cutoff_s: float,
    yaw_angle_deg: float,
    *,
    settle_duration_s: float,
    phase: str,
    delta_v_mps: float = 0.0,
) -> dict[str, Any]:
    del config, yaw_angle_deg, phase
    total_burn_s = max(0.0, float(cutoff_s) - float(burn_start_s))
    return {
        "cutoff_s": float(cutoff_s),
        "total_burn_time_s": total_burn_s,
        "settle_duration_s": max(0.0, min(float(settle_duration_s), total_burn_s)),
        "orbit_control_duration_s": max(0.0, total_burn_s - max(0.0, min(float(settle_duration_s), total_burn_s))),
        "post_mass_kg": float(state[6]),
        "r_cutoff": np.asarray(state[:3], dtype=float).copy(),
        "v_cutoff": np.asarray(state[3:6], dtype=float).copy(),
        "delta_v_mps": float(delta_v_mps),
    }


def _ideal_low_thrust_delta_v_mps(thrust_n: float, mass_kg: float, duration_s: float) -> float:
    if thrust_n <= 0.0 or duration_s <= 0.0 or mass_kg <= 0.0:
        return 0.0
    return float(thrust_n) / float(mass_kg) * float(duration_s)


def _rk4_low_thrust_step(
    config: dict[str, Any],
    time_s: float,
    state: np.ndarray,
    step_s: float,
    yaw_angle_deg: float,
    thrust_n: float,
    isp_s: float,
) -> np.ndarray:
    def dynamics(t_local: float, state_local: np.ndarray) -> np.ndarray:
        return _low_thrust_state_derivative(config, t_local, state_local, yaw_angle_deg, thrust_n, isp_s)

    h = float(step_s)
    k1 = dynamics(time_s, state)
    k2 = dynamics(time_s + 0.5 * h, state + 0.5 * h * k1)
    k3 = dynamics(time_s + 0.5 * h, state + 0.5 * h * k2)
    k4 = dynamics(time_s + h, state + h * k3)
    next_state = state + (h / 6.0) * (k1 + 2.0 * k2 + 2.0 * k3 + k4)
    next_state[6] = max(1.0, float(next_state[6]))
    return next_state


def _low_thrust_state_derivative(
    config: dict[str, Any],
    _time_s: float,
    state: np.ndarray,
    yaw_angle_deg: float,
    thrust_n: float,
    isp_s: float,
) -> np.ndarray:
    r = np.asarray(state[:3], dtype=float)
    v = np.asarray(state[3:6], dtype=float)
    mass_kg = max(1.0, float(state[6]))
    radius = float(np.linalg.norm(r))
    if radius <= 0.0:
        raise ValueError("Position norm must be positive.")
    earth = config["earth"]
    mu = float(earth["mu_km3_s2"])
    accel = -mu * r / (radius**3)
    if bool(earth.get("use_J2", True)):
        z = float(r[2])
        zr2 = (z / radius) ** 2
        factor = float(earth["J2"]) * mu * float(earth["Re_km"]) ** 2 / (radius**5)
        accel += factor * np.asarray(
            [
                float(r[0]) * (-1.5 + 7.5 * zr2),
                float(r[1]) * (-1.5 + 7.5 * zr2),
                z * (-4.5 + 7.5 * zr2),
            ],
            dtype=float,
        )
    mdot = 0.0
    if thrust_n > 0.0:
        accel += (float(thrust_n) / mass_kg / 1000.0) * _local_horizontal_direction_fast(r, yaw_angle_deg)
        mdot = -float(thrust_n) / (max(1.0, float(isp_s)) * G0_M_S2)
    return np.asarray([v[0], v[1], v[2], accel[0], accel[1], accel[2], mdot], dtype=float)


def _semi_major_axis_from_rv(r: np.ndarray, v: np.ndarray, mu: float) -> float:
    rx, ry, rz = float(r[0]), float(r[1]), float(r[2])
    vx, vy, vz = float(v[0]), float(v[1]), float(v[2])
    radius = math.sqrt(rx * rx + ry * ry + rz * rz)
    speed2 = vx * vx + vy * vy + vz * vz
    energy = 0.5 * speed2 - float(mu) / radius
    return -float(mu) / (2.0 * energy)


def _local_horizontal_direction_fast(r: np.ndarray, alpha_deg: float) -> np.ndarray:
    return local_horizontal_yaw_direction(r, alpha_deg)


def _refine_low_thrust_metric_crossing(
    config: dict[str, Any],
    state_before: np.ndarray,
    time_before_s: float,
    step_s: float,
    yaw_angle_deg: float,
    thrust_n: float,
    isp_s: float,
    target_metric: str,
    target_value_km: float,
    target_sign: float,
) -> tuple[np.ndarray, float]:
    low_s = 0.0
    high_s = float(step_s)
    best_state = state_before.copy()
    best_time_s = float(time_before_s)
    for _ in range(18):
        mid_s = 0.5 * (low_s + high_s)
        state_mid = _rk4_low_thrust_step(config, time_before_s, state_before, mid_s, yaw_angle_deg, thrust_n, isp_s)
        value_mid = _continuous_target_metric_value(config, state_mid[:3], state_mid[3:6], target_metric)
        if target_sign * (value_mid - float(target_value_km)) >= 0.0:
            best_state = state_mid
            best_time_s = float(time_before_s) + mid_s
            high_s = mid_s
        else:
            low_s = mid_s
    return best_state, best_time_s


def _continuous_thrust_fallback_candidate(
    config: dict[str, Any],
    r: np.ndarray,
    v: np.ndarray,
    state_elapsed_s: float,
    mass_kg: float,
    burn: DesignManeuverBurn,
    target_post_a_km: float,
    initial_start_s: float,
    initial_yaw_deg: float,
    future_m1_kg: float,
    future_m2_kg: float,
    objective_formula: str,
) -> dict[str, Any]:
    start_s = max(float(state_elapsed_s), float(initial_start_s))
    r_start, v_start = _propagate_state_to_elapsed(config, r, v, state_elapsed_s, start_s)
    candidate = _evaluate_continuous_thrust_candidate(
        config,
        r_start,
        v_start,
        start_s,
        mass_kg,
        burn,
        target_post_a_km,
        initial_yaw_deg,
        future_m1_kg,
        future_m2_kg,
        objective_formula,
        integration_step_s=max(1.0, float(config["continuous_thrust_optimizer"]["search_integration_step_s"])),
    )
    if candidate is not None:
        candidate["search_evaluations"] = 0
        return candidate
    total_burn_s = max(0.0, float(burn.total_burn_time_min) * 60.0)
    cutoff_s = start_s + total_burn_s
    r_cutoff, v_cutoff = _propagate_state_to_elapsed(config, r_start, v_start, start_s, cutoff_s)
    terminal_i_error_abs_deg = (
        abs(float(burn.post_i_deg) - float(config["target"]["i_deg"])) if objective_formula == "m + m3" else 0.0
    )
    terminal_i_excess_deg = max(
        0.0,
        terminal_i_error_abs_deg - float(config["terminal_tolerance"]["i_deg"]),
    )
    terminal_e_error_abs = abs(float(burn.post_e) - float(config["target"]["e"])) if objective_formula == "m + m3" else 0.0
    terminal_e_excess = max(0.0, terminal_e_error_abs - float(config["terminal_tolerance"]["e"]))
    apsis_timing_error_s = 0.0
    if str(burn.apsis).upper() == "P":
        pulse_time_s = float(burn.elapsed_min) * 60.0
        if pulse_time_s < start_s:
            apsis_timing_error_s = start_s - pulse_time_s
        elif pulse_time_s > cutoff_s:
            apsis_timing_error_s = pulse_time_s - cutoff_s
    apogee_radius_error_km = 0.0
    if str(burn.apsis).upper() == "A":
        target_ra_km = float(burn.post_a_km) * (1.0 + float(burn.post_e))
        apogee_radius_error_km = abs(target_ra_km - float(burn.post_a_km) * (1.0 + float(burn.post_e)))
    inclination_error_deg = 0.0
    inclination_excess_deg = 0.0
    return {
        "burn_start_s": start_s,
        "settle_end_s": start_s,
        "cutoff_s": cutoff_s,
        "yaw_angle_deg": float(initial_yaw_deg),
        "ignition_longitude_deg_e": _longitude_deg(config, r_start, start_s),
        "cutoff_longitude_deg_e": _longitude_deg(config, r_cutoff, cutoff_s),
        "delta_v_mps": float(burn.delta_v_mps),
        "total_burn_time_s": total_burn_s,
        "settle_duration_s": 0.0,
        "orbit_control_duration_s": total_burn_s,
        "propellant_kg": float(burn.propellant_kg),
        "trim_delta_v_mps": 0.0,
        "trim_propellant_kg": 0.0,
        "objective_delta_g_kg": float(burn.propellant_kg) + future_m1_kg + future_m2_kg,
        "terminal_lon_excess_deg": abs(_wrap180(_longitude_deg(config, r_cutoff, cutoff_s) - float(config["target"]["lon_degE"])))
        if objective_formula == "m + m3"
        else 0.0,
        "terminal_i_excess_deg": terminal_i_excess_deg,
        "terminal_i_error_abs_deg": terminal_i_error_abs_deg,
        "terminal_e_excess": terminal_e_excess,
        "terminal_e_error_abs": terminal_e_error_abs,
        "apsis_timing_error_s": apsis_timing_error_s,
        "apogee_radius_error_km": apogee_radius_error_km,
        "inclination_error_deg": inclination_error_deg,
        "inclination_excess_deg": inclination_excess_deg,
        "post_a_km": float(burn.post_a_km),
        "post_e": float(burn.post_e),
        "post_i_deg": float(burn.post_i_deg),
        "post_mass_kg": max(1.0, mass_kg - float(burn.propellant_kg)),
        "duration_ok": bool(burn.duration_ok),
        "longitude_ok": bool(burn.longitude_ok),
        "r_cutoff": r_cutoff,
        "v_cutoff": v_cutoff,
        "pulse_time_offset_s": 0.0,
        "pulse_yaw_offset_deg": 0.0,
        "search_evaluations": 0,
    }


def _append_continuous_coast_history(
    config: dict[str, Any],
    rows: list[dict[str, Any]],
    r: np.ndarray,
    v: np.ndarray,
    mass_kg: float,
    start_s: float,
    end_s: float,
    *,
    sample_interval_s: float,
) -> None:
    if end_s <= start_s + 1.0e-9:
        return
    targets = _history_target_times(start_s, end_s, sample_interval_s)
    for time_s in targets:
        r_time, v_time = _propagate_state_to_elapsed(config, r, v, start_s, time_s)
        _append_continuous_orbit_history_row(
            config,
            rows,
            time_s,
            r_time,
            v_time,
            mass_kg,
            phase="coast",
            is_event_point=abs(time_s - end_s) <= 1.0e-6,
        )


def _append_continuous_burn_history(
    config: dict[str, Any],
    rows: list[dict[str, Any]],
    r: np.ndarray,
    v: np.ndarray,
    mass_kg: float,
    state_elapsed_s: float,
    burn_start_s: float,
    yaw_angle_deg: float,
    target_metric: str,
    target_value_km: float,
    *,
    sample_interval_s: float,
    integration_step_s: float,
) -> None:
    r_start, v_start = _propagate_state_to_elapsed(config, r, v, state_elapsed_s, burn_start_s)
    engine = config["engine"]
    burn_limit = config["burn_limit"]
    max_burn_s = max(0.0, float(burn_limit["max_total_burn_time_min"]) * 60.0)
    settle_limit_s = float(engine.get("tau_set_s", 0.0)) if bool(engine.get("use_settling", True)) else 0.0
    if not bool(burn_limit.get("include_settling_in_burn_time", True)):
        settle_limit_s = 0.0
    settle_limit_s = max(0.0, min(settle_limit_s, max_burn_s))
    main_isp_s = max(1.0, float(engine["Isp_main_s"]) / (1.0 + float(engine["attitude_control_efficiency"])))
    phases = (
        ("settle", settle_limit_s, max(0.0, float(engine["F_set_N"])), max(1.0, float(engine["Isp_set_s"]))),
        ("orbit_control", max_burn_s - settle_limit_s, max(0.0, float(engine["F_main_N"])), main_isp_s),
    )
    state = np.asarray([*r_start, *v_start, float(mass_kg)], dtype=float)
    current_s = float(burn_start_s)
    start_value = _continuous_target_metric_value(config, state[:3], state[3:6], target_metric)
    target_sign = 1.0 if target_value_km >= start_value else -1.0
    previous_error = target_sign * (start_value - target_value_km)
    _append_continuous_orbit_history_row(
        config,
        rows,
        current_s,
        state[:3],
        state[3:6],
        float(state[6]),
        phase="settle" if settle_limit_s > 0.0 else "orbit_control",
        is_event_point=True,
        yaw_angle_deg=yaw_angle_deg,
    )
    for phase_name, phase_duration_s, thrust_n, isp_s in phases:
        phase_elapsed_s = 0.0
        next_sample_s = math.ceil(current_s / sample_interval_s) * sample_interval_s
        if next_sample_s <= current_s + 1.0e-9:
            next_sample_s += sample_interval_s
        while phase_elapsed_s < phase_duration_s - 1.0e-9:
            step_s = min(float(integration_step_s), phase_duration_s - phase_elapsed_s)
            previous_state = state.copy()
            previous_s = current_s
            state = _rk4_low_thrust_step(config, current_s, state, step_s, yaw_angle_deg, thrust_n, isp_s)
            current_s += step_s
            phase_elapsed_s += step_s
            while next_sample_s <= current_s + 1.0e-9:
                interp = 0.0 if step_s <= 0.0 else max(0.0, min(1.0, (next_sample_s - previous_s) / step_s))
                sample_state = previous_state + interp * (state - previous_state)
                _append_continuous_orbit_history_row(
                    config,
                    rows,
                    next_sample_s,
                    sample_state[:3],
                    sample_state[3:6],
                    float(sample_state[6]),
                    phase=phase_name,
                    is_event_point=False,
                    yaw_angle_deg=yaw_angle_deg,
                )
                next_sample_s += sample_interval_s
            current_value = _continuous_target_metric_value(config, state[:3], state[3:6], target_metric)
            current_error = target_sign * (current_value - target_value_km)
            if current_error >= 0.0 and previous_error <= 0.0:
                state, current_s = _refine_low_thrust_metric_crossing(
                    config,
                    previous_state,
                    previous_s,
                    step_s,
                    yaw_angle_deg,
                    thrust_n,
                    isp_s,
                    target_metric,
                    target_value_km,
                    target_sign,
                )
                _append_continuous_orbit_history_row(
                    config,
                    rows,
                    current_s,
                    state[:3],
                    state[3:6],
                    float(state[6]),
                    phase=phase_name,
                    is_event_point=True,
                    yaw_angle_deg=yaw_angle_deg,
                )
                return
            previous_error = current_error


def _append_continuous_orbit_history_row(
    config: dict[str, Any],
    rows: list[dict[str, Any]],
    elapsed_time_s: float,
    r_km: np.ndarray,
    v_km_s: np.ndarray,
    mass_kg: float,
    *,
    phase: str,
    is_event_point: bool,
    yaw_angle_deg: float | None = None,
) -> None:
    if rows and abs(float(rows[-1]["elapsed_time_s"]) - float(elapsed_time_s)) <= 1.0e-6:
        rows.pop()
    try:
        a_km, e_val, inc_rad, raan_rad, argp_rad, _mean_rad, true_rad = _rv_to_coe(
            np.asarray(r_km, dtype=float),
            np.asarray(v_km_s, dtype=float),
            mu=float(config["earth"]["mu_km3_s2"]),
        )
    except Exception:
        a_km = e_val = inc_rad = raan_rad = argp_rad = true_rad = float("nan")
    lon_deg_e = _longitude_deg(config, np.asarray(r_km, dtype=float), elapsed_time_s)
    lat_deg, alt_km = _spherical_latitude_altitude(config, np.asarray(r_km, dtype=float), elapsed_time_s)
    thrust_lon = thrust_lat = float("nan")
    if yaw_angle_deg is not None:
        thrust_direction = _local_horizontal_direction(np.asarray(r_km, dtype=float), yaw_angle_deg)
        thrust_lon, thrust_lat = _direction_longitude_latitude_deg(config, thrust_direction, elapsed_time_s)
    rows.append(
        {
            "elapsed_time_s": float(elapsed_time_s),
            "elapsed_time_min": float(elapsed_time_s) / 60.0,
            "phase": phase,
            "is_event_point": 1 if is_event_point else 0,
            "semi_major_axis_m": float(a_km) * 1000.0,
            "eccentricity": float(e_val),
            "inclination_deg": math.degrees(float(inc_rad)) if math.isfinite(float(inc_rad)) else float("nan"),
            "raan_deg": math.degrees(float(raan_rad)) % 360.0 if math.isfinite(float(raan_rad)) else float("nan"),
            "argument_of_perigee_deg": math.degrees(float(argp_rad)) % 360.0
            if math.isfinite(float(argp_rad))
            else float("nan"),
            "true_anomaly_deg": math.degrees(float(true_rad)) % 360.0 if math.isfinite(float(true_rad)) else float("nan"),
            "position_x_m": float(r_km[0]) * 1000.0,
            "position_y_m": float(r_km[1]) * 1000.0,
            "position_z_m": float(r_km[2]) * 1000.0,
            "velocity_x_m_s": float(v_km_s[0]) * 1000.0,
            "velocity_y_m_s": float(v_km_s[1]) * 1000.0,
            "velocity_z_m_s": float(v_km_s[2]) * 1000.0,
            "thrust_alpha_deg": float(yaw_angle_deg) if yaw_angle_deg is not None else float("nan"),
            "thrust_beta_deg": 0.0 if yaw_angle_deg is not None else float("nan"),
            "thrust_longitude_deg": thrust_lon,
            "thrust_latitude_deg": thrust_lat,
            "subsatellite_longitude_deg": _wrap180(lon_deg_e),
            "subsatellite_latitude_deg": lat_deg,
            "subsatellite_altitude_m": alt_km * 1000.0,
            "orbit_height_m": (float(np.linalg.norm(r_km)) - float(config["earth"]["Re_km"])) * 1000.0,
            "mass_kg": float(mass_kg),
        }
    )


def _history_target_times(start_s: float, end_s: float, sample_interval_s: float) -> list[float]:
    if end_s <= start_s + 1.0e-9:
        return []
    values: list[float] = []
    current = math.ceil(float(start_s) / float(sample_interval_s)) * float(sample_interval_s)
    if current <= start_s + 1.0e-9:
        current += float(sample_interval_s)
    while current < end_s - 1.0e-9:
        values.append(current)
        current += float(sample_interval_s)
    values.append(float(end_s))
    return values


def _spherical_latitude_altitude(config: dict[str, Any], r_eci_km: np.ndarray, elapsed_time_s: float) -> tuple[float, float]:
    theta = greenwich_angle_at_utc(parse_utc(str(config["initial"]["t0_epoch"]))) + float(config["earth"]["omega_e_rad_s"]) * elapsed_time_s
    x, y, z = (float(value) for value in r_eci_km)
    x_ecef = math.cos(theta) * x + math.sin(theta) * y
    y_ecef = -math.sin(theta) * x + math.cos(theta) * y
    radius = math.sqrt(x_ecef * x_ecef + y_ecef * y_ecef + z * z)
    lat = math.degrees(math.atan2(z, math.hypot(x_ecef, y_ecef)))
    return lat, radius - float(config["earth"]["Re_km"])


def _direction_longitude_latitude_deg(
    config: dict[str, Any],
    direction_eci: np.ndarray,
    elapsed_time_s: float,
) -> tuple[float, float]:
    theta = greenwich_angle_at_utc(parse_utc(str(config["initial"]["t0_epoch"]))) + float(config["earth"]["omega_e_rad_s"]) * elapsed_time_s
    x, y, z = (float(value) for value in direction_eci)
    x_ecef = math.cos(theta) * x + math.sin(theta) * y
    y_ecef = -math.sin(theta) * x + math.cos(theta) * y
    norm = math.sqrt(x_ecef * x_ecef + y_ecef * y_ecef + z * z)
    if norm <= 0.0:
        return float("nan"), float("nan")
    return _wrap180(math.degrees(math.atan2(y_ecef, x_ecef))), math.degrees(math.asin(max(-1.0, min(1.0, z / norm))))


def _continuous_candidate_score(candidate: dict[str, Any]) -> tuple[float, float, float, float, float, float]:
    invalid_penalty = 0.0
    if not bool(candidate.get("duration_ok", False)):
        invalid_penalty += 1.0
    if not bool(candidate.get("longitude_ok", False)):
        invalid_penalty += 1.0
    if float(candidate.get("apsis_timing_error_s", 0.0)) > 0.0:
        invalid_penalty += 1.0
    if float(candidate.get("terminal_e_excess", 0.0)) > 0.0:
        invalid_penalty += 1.0
    if float(candidate.get("inclination_excess_deg", 0.0)) > 0.0:
        invalid_penalty += 1.0
    continuity_penalty = (
        float(candidate["objective_delta_g_kg"])
        + 1.0e6 * float(candidate.get("inclination_excess_deg", 0.0))
        + 1.0e3 * float(candidate.get("inclination_error_deg", 0.0))
        + 1.0e2 * float(candidate.get("apogee_radius_error_km", 0.0))
        + 1.0e6 * float(candidate.get("terminal_e_excess", 0.0))
        + 1.0e3 * float(candidate.get("terminal_e_error_abs", 0.0))
        + 1.0e3 * float(candidate.get("apsis_timing_error_s", 0.0))
        + 200.0 * float(candidate.get("seed_yaw_offset_deg", 0.0))
        + 5.0 * float(candidate.get("seed_time_offset_s", 0.0)) / 60.0
    )
    return (
        invalid_penalty,
        float(candidate.get("terminal_lon_excess_deg", 0.0)),
        float(candidate.get("terminal_e_excess", 0.0)),
        float(candidate.get("apsis_timing_error_s", 0.0)),
        float(candidate.get("inclination_excess_deg", 0.0)),
        float(candidate.get("apogee_radius_error_km", 0.0)),
        continuity_penalty,
    )


def _continuous_yaw_bounds_for_burn(
    config: dict[str, Any],
    burns: list[DesignManeuverBurn],
    burn_position: int,
    burn: DesignManeuverBurn,
) -> tuple[float, float]:
    del burns, burn_position
    if str(burn.apsis).upper() == "P":
        return (float(burn.alpha_deg), float(burn.alpha_deg))
    if burn.burn_type == "tail_fixed" and burn.apsis == "A":
        return _number_pair(config["alpha"]["tail_apogee_bounds_deg"], [-20.0, 40.0])
    if burn.burn_type == "front":
        return _number_pair(config["alpha"]["front_bounds_deg"], [-20.0, 40.0])
    return _number_pair(config["alpha"]["standard_bounds_deg"], [-30.0, 40.0])


def _continuous_thrust_failed_constraints(
    config: dict[str, Any],
    parameters: list[ContinuousThrustManeuverParameter],
) -> list[str]:
    failed: list[str] = []
    if not parameters:
        return ["连续推力参数"]
    tolerance = config["terminal_tolerance"]
    target = config["target"]
    if not all(item.longitude_ok for item in parameters):
        failed.append("点火经度")
    if not all(item.duration_ok for item in parameters):
        failed.append("总点火时长")
    final = parameters[-1]
    if abs(_wrap180(final.cutoff_longitude_deg_e - float(target["lon_degE"]))) > float(tolerance["lon_deg"]):
        failed.append("终端经度误差")
    if abs(final.post_a_km - float(target["a_km"])) > float(tolerance["a_km"]):
        failed.append("终端半长轴误差")
    if abs(final.post_e - float(target["e"])) > float(tolerance["e"]):
        failed.append("终端偏心率误差")
    if abs(final.post_i_deg - float(target["i_deg"])) > float(tolerance["i_deg"]):
        failed.append("终端倾角误差")
    if not all(item.search_evaluations >= 0 and math.isfinite(item.objective_delta_g_kg) for item in parameters):
        failed.append("推进剂最小")
    return failed


def _continuous_first_burn_failed_constraints(parameters: list[ContinuousThrustManeuverParameter]) -> list[str]:
    failed: list[str] = []
    if not parameters:
        return ["连续推力参数"]
    if not all(item.longitude_ok for item in parameters):
        failed.append("点火经度")
    if not all(item.duration_ok for item in parameters):
        failed.append("总点火时长")
    if not all(math.isfinite(item.objective_delta_g_kg) for item in parameters):
        failed.append("推进剂最小")
    return failed


def _grid_values_around(center: float, low: float, high: float, step: float) -> list[float]:
    if high < low:
        return []
    step = max(1.0e-9, float(step))
    start_index = math.ceil((float(low) - float(center)) / step - 1.0e-9)
    end_index = math.floor((float(high) - float(center)) / step + 1.0e-9)
    values = [float(center) + index * step for index in range(start_index, end_index + 1)]
    clipped = [min(float(high), max(float(low), value)) for value in values]
    if not clipped or abs(clipped[0] - float(low)) > 1.0e-9:
        clipped.insert(0, float(low))
    if abs(clipped[-1] - float(high)) > 1.0e-9:
        clipped.append(float(high))
    unique: list[float] = []
    previous: float | None = None
    for value in clipped:
        rounded = round(float(value), 9)
        if previous is None or abs(rounded - previous) > 1.0e-9:
            unique.append(rounded)
            previous = rounded
    return unique


def find_feasible_q_sequences(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    config = normalize_design_maneuver_strategy_payload(payload)
    q_aa_user_for_count = [int(value) for value in config["hard_constraint_planner"].get("q_AA_user", [])]
    if (
        str(config["apsis"].get("pattern_mode", "auto")) == "user"
        and q_aa_user_for_count
        and int(config["maneuver_count"].get("user", 0)) <= 0
    ):
        config["maneuver_count"]["user"] = len(q_aa_user_for_count) + 2
        config["planner"]["maneuver_count_user"] = len(q_aa_user_for_count) + 2
    config["apsis"]["pattern_mode"] = "auto"
    config["hard_constraint_planner"]["q_AA_user"] = []
    config["hard_constraint_planner"]["q_AP_user"] = None
    initial = config["initial"]
    earth = config["earth"]
    target = config["target"]
    hard_cfg = config["hard_constraint_planner"]
    count_cfg = config["maneuver_count"]
    supersync = config["supersynchronous_transfer"]

    a0 = float(initial["a_km"])
    e0 = float(initial["e"])
    i_target = float(target["i_deg"])
    a_target = float(target["a_km"])
    re_km = float(earth["Re_km"])
    r_a0 = a0 * (1.0 + e0)
    orbit_type = _classify_orbit(config, r_a0, a_target)
    if orbit_type != "supersynchronous_transfer" or not bool(hard_cfg["enabled"]):
        raise RuntimeError("独立 q 序列扫描仅支持 V5.1 超同步硬约束规划。")

    _dv_tail_apogee_est, _dv_tail_perigee_est = _estimate_tail_delta_v(config, orbit_type)
    dv_total_est = _estimate_total_delta_v(
        config,
        orbit_type,
        dv_tail_apogee_est=_dv_tail_apogee_est,
        dv_tail_perigee_est=_dv_tail_perigee_est,
    )
    design_dv = _estimate_design_single_burn_dv(config, float(initial["m0_kg"]))
    recommended_count = _recommend_count(config, orbit_type, dv_total_est, design_dv)
    user_count = int(count_cfg["user"])
    actual_count = user_count if user_count > 0 else recommended_count
    actual_count = max(2, actual_count)
    if bool(supersync["tail_fixed_enabled"]):
        actual_count = max(actual_count, int(supersync["tail_fixed_count"]))

    first_elapsed, first_r, first_v, first_lon = _find_initial_burn_event(config, *_initial_state_km(config), "A")
    reference = _v51_apogee_to_rp_i(config, first_r, first_v, a_target, i_target)
    if reference is None:
        raise RuntimeError("首个远地点无法一次完成目标近地点与倾角参考解。")

    raw_design_dv = max(1.0, float(config["burn_limit"]["design_dv_per_burn_mps"]))
    v51_recommended_total = max(2, int(math.ceil(float(reference["dv_mps"]) / raw_design_dv)) + 1)
    q_aa_user = [int(value) for value in hard_cfg.get("q_AA_user", [])]
    sequence_drives_count = str(config["apsis"].get("pattern_mode", "auto")) == "user" and bool(q_aa_user)
    if sequence_drives_count:
        total_count = len(q_aa_user) + 2
    else:
        total_count = actual_count if user_count > 0 else v51_recommended_total
    total_count = max(2, total_count)
    front_count = max(0, total_count - 2)

    fixed_rp: dict[int, float] = {}
    for key, hp in hard_cfg.get("fixed_hp_targets_km", {}).items():
        index = int(key)
        if 1 <= index <= front_count:
            fixed_rp[index] = re_km + float(hp)
    manual_first_control = _first_post_a_control_km(config)
    if manual_first_control is not None and front_count >= 1:
        pre_a_first, *_ = _rv_to_coe(first_r, first_v)
        target_post_a_first = pre_a_first + manual_first_control
        fixed_rp[1] = 2.0 * target_post_a_first - float(np.linalg.norm(first_r))
    _v51_validate_fixed_rp(config, fixed_rp, front_count)

    q_aa_candidates = _v51_q_aa_candidates(config, front_count)
    q_ap_candidates = (
        [int(hard_cfg["q_AP_user"])]
        if hard_cfg.get("q_AP_user") is not None
        else [int(value) for value in hard_cfg.get("q_AP_candidates", [0, 1, 2])]
    )
    starts = _v51_template_points(config, front_count, fixed_rp)
    bounds = _v51_variable_bounds(config, front_count, fixed_rp)
    variable_indices = [index for index in range(1, front_count + 1) if index not in fixed_rp]
    return _v51_scan_feasible_q_sequences(
        config,
        first=(first_elapsed, first_r, first_v, first_lon),
        fixed_rp=fixed_rp,
        variable_indices=variable_indices,
        bounds=bounds,
        starts=starts,
        q_aa_candidates=q_aa_candidates,
        q_ap_candidates=q_ap_candidates,
        front_count=front_count,
        scan_all_q=True,
        ignore_terminal_i=True,
    )


def _plan_v51_hard_constrained(
    config: dict[str, Any],
    *,
    orbit_type: str,
    dv_total_est: float,
    design_dv: float,
    recommended_count: int,
    actual_count: int,
    user_count: int,
    warnings: list[str],
) -> DesignManeuverResult:
    initial = config["initial"]
    target = config["target"]
    earth = config["earth"]
    hard_cfg = config["hard_constraint_planner"]
    a0 = float(initial["a_km"])
    e0 = float(initial["e"])
    i0 = float(initial["i_deg"])
    a_target = float(target["a_km"])
    e_target = float(target["e"])
    i_target = float(target["i_deg"])
    re_km = float(earth["Re_km"])
    r_a0 = a0 * (1.0 + e0)
    h_a0 = r_a0 - re_km
    h_sync = a_target - re_km

    first_elapsed, first_r, first_v, first_lon = _find_initial_burn_event(config, *_initial_state_km(config), "A")
    reference = _v51_apogee_to_rp_i(config, first_r, first_v, a_target, i_target)
    if reference is None:
        raise RuntimeError("首个远地点无法一次完成目标近地点与倾角参考解。")

    raw_design_dv = max(1.0, float(config["burn_limit"]["design_dv_per_burn_mps"]))
    v51_recommended_total = max(2, int(math.ceil(float(reference["dv_mps"]) / raw_design_dv)) + 1)
    q_aa_user = [int(value) for value in hard_cfg.get("q_AA_user", [])]
    sequence_drives_count = str(config["apsis"].get("pattern_mode", "auto")) == "user" and bool(q_aa_user)
    if sequence_drives_count:
        total_count = len(q_aa_user) + 2
        if user_count > 0 and user_count != total_count:
            warnings.append(f"用户 q 序列定义 {total_count} 次点火，已覆盖用户指定次数 {user_count}。")
    else:
        total_count = actual_count if user_count > 0 else v51_recommended_total
    total_count = max(2, total_count)
    n_apogee = total_count - 1
    front_count = max(0, n_apogee - 1)
    apsis_pattern = ["A"] * n_apogee + ["P"]

    fixed_rp: dict[int, float] = {}
    for key, hp in hard_cfg.get("fixed_hp_targets_km", {}).items():
        index = int(key)
        if 1 <= index <= front_count:
            fixed_rp[index] = re_km + float(hp)
    manual_first_control = _first_post_a_control_km(config)
    if manual_first_control is not None and front_count >= 1:
        pre_a_first, *_ = _rv_to_coe(first_r, first_v)
        target_post_a_first = pre_a_first + manual_first_control
        fixed_rp[1] = 2.0 * target_post_a_first - float(np.linalg.norm(first_r))
    _v51_validate_fixed_rp(config, fixed_rp, front_count)

    q_aa_candidates = _v51_q_aa_candidates(config, front_count)
    q_ap_candidates = (
        [int(hard_cfg["q_AP_user"])]
        if hard_cfg.get("q_AP_user") is not None
        else [int(value) for value in hard_cfg.get("q_AP_candidates", [0, 1, 2])]
    )
    starts = _v51_template_points(config, front_count, fixed_rp)
    bounds = _v51_variable_bounds(config, front_count, fixed_rp)
    variable_indices = [index for index in range(1, front_count + 1) if index not in fixed_rp]

    phase_starts_by_q: dict[tuple[int, ...], list[np.ndarray]] = {}
    if str(config["apsis"].get("pattern_mode", "auto")) == "user" and q_aa_candidates:
        q_aa_to_test = q_aa_candidates[: max(1, int(hard_cfg["prefilter_top_k"]))]
    else:
        q_ranked: list[tuple[float, tuple[int, ...]]] = []
        for q_aa in q_aa_candidates:
            q_ranked.append(
                (
                    _v51_phase_chain_q_score(
                        config,
                        first=(first_elapsed, first_r, first_v, first_lon),
                        fixed_rp=fixed_rp,
                        q_aa=q_aa,
                    ),
                    q_aa,
                )
            )
        q_ranked.sort(key=lambda item: item[0])
        q_cap = 10 if fixed_rp else 5
        q_prefilter = min(q_cap, max(1, int(hard_cfg["prefilter_top_k"])))
        q_aa_to_test = [q_aa for _score, q_aa in q_ranked[:q_prefilter]]

    records: list[dict[str, Any]] = []
    for q_aa in q_aa_to_test:
        for q_ap in q_ap_candidates:
            if q_aa in phase_starts_by_q:
                phase_starts = phase_starts_by_q[q_aa]
            else:
                phase_starts = _v51_phase_chain_template_points(
                    config,
                    first=(first_elapsed, first_r, first_v, first_lon),
                    front_count=front_count,
                    fixed_rp=fixed_rp,
                    variable_indices=variable_indices,
                    bounds=bounds,
                    q_aa=q_aa,
                    q_ap=int(q_ap),
                )
                phase_starts_by_q[q_aa] = phase_starts
            records.extend(
                _v51_optimize_sequence(
                    config,
                    first=(first_elapsed, first_r, first_v, first_lon),
                    fixed_rp=fixed_rp,
                    variable_indices=variable_indices,
                    bounds=bounds,
                    starts=phase_starts or starts,
                    q_aa=q_aa,
                    q_ap=q_ap,
                )
            )
    if not records:
        raise RuntimeError("没有生成可传播候选。")
    records.sort(key=lambda rec: _v51_rank_record(config, rec))
    best = records[0]
    if not best.get("success"):
        raise RuntimeError(str(best.get("reason") or "没有成功候选。"))

    best_violations = _v51_feasibility_violations(config, best)
    feasible = _v51_is_feasible(config, best)
    feasible_count = sum(1 for rec in records if rec.get("success") and _v51_is_feasible(config, rec))
    if not feasible:
        raise RuntimeError(f"V5.1 未找到完全满足硬约束的候选；最小违约量: {best_violations}")
    unique_records = _v51_unique_record_summaries(config, records)
    feasible_q_sequences = _v51_scan_feasible_q_sequences(
        config,
        first=(first_elapsed, first_r, first_v, first_lon),
        fixed_rp=fixed_rp,
        variable_indices=variable_indices,
        bounds=bounds,
        starts=starts,
        q_aa_candidates=q_aa_candidates,
        q_ap_candidates=q_ap_candidates,
        front_count=front_count,
    )

    burns = list(best["burns"])
    checks = _build_checks(config, burns, ignore_uniform=True)
    terminal_a = burns[-1].post_a_km if burns else a0
    terminal_e = burns[-1].post_e if burns else e0
    terminal_i = burns[-1].post_i_deg if burns else i0
    terminal_errors = {
        "a_km": terminal_a - a_target,
        "e": terminal_e - e_target,
        "i_deg": terminal_i - i_target,
        "lon_deg": _wrap180((burns[-1].longitude_deg_e if burns else 0.0) - float(target["lon_degE"])),
    }
    max_duration = max((burn.total_burn_time_min for burn in burns), default=0.0)
    summary = {
        "initial_apogee_altitude_km": h_a0,
        "sync_altitude_km": h_sync,
        "orbit_type": orbit_type,
        "estimated_total_delta_v_mps": dv_total_est,
        "design_single_burn_delta_v_mps": design_dv,
        "reference_apogee_delta_v_mps": float(reference["dv_mps"]),
        "recommended_count": v51_recommended_total if user_count <= 0 else recommended_count,
        "user_count": user_count,
        "actual_count": total_count,
        "apsis_pattern": ",".join(apsis_pattern),
        "q_sequence": ",".join([str(value) for value in best["q_AA"]] + [str(best["q_AP"])]),
        "phase_optimized": True,
        "phase_delta_v_optimized": True,
        "phase_alpha_optimized": True,
        "optimized_propellant_kg": sum(burn.propellant_kg for burn in burns),
        "phase_lon_error_before_deg": None,
        "duration_ok": max_duration <= float(config["burn_limit"]["max_total_burn_time_min"]) + 1.0e-9,
        "longitude_ok": all(burn.longitude_ok for burn in burns),
        "uniform_spread_mps": _uniform_spread([burn.delta_v_mps for burn in burns if burn.apsis == "A"]),
        "uniform_ok": True,
        "terminal_errors": terminal_errors,
        "phase_diagnostics": {
            "optimizer_method": "V5.1 hard-constrained",
            "hard_constraint_feasible": feasible,
            "hard_constraint_violations": best_violations,
            "q_total_candidates": len(q_aa_candidates) * len(q_ap_candidates),
            "q_tested_fast": len(q_aa_to_test) * len(q_ap_candidates),
            "q_tested_slsqp": 0,
            "feasible_solutions": feasible_count,
            "best_q_sequence": list(best["q_AA"]) + [int(best["q_AP"])],
            "fixed_hp_targets_km": {
                str(index): fixed_rp[index] - re_km for index in sorted(fixed_rp)
            },
            "optimized_hp_targets_km": [float(value) - re_km for value in best["rp_targets_km"]],
            "top_candidates": unique_records,
            "feasible_q_sequences": feasible_q_sequences,
        },
    }
    return DesignManeuverResult(config=config, summary=summary, burns=burns, checks=checks, warnings=warnings)


def _v51_q_aa_candidates(config: dict[str, Any], front_count: int) -> list[tuple[int, ...]]:
    hard_cfg = config["hard_constraint_planner"]
    q_user = list(hard_cfg.get("q_AA_user", []))
    if str(config["apsis"].get("pattern_mode", "auto")) == "user" and len(q_user) == front_count:
        return [tuple(q_user)]
    if front_count <= 0:
        return [tuple()]
    q_limit = max(1, int(config["apsis"]["q_AA_default"]))
    values = tuple(range(1, q_limit + 1))
    return [tuple(int(item) for item in candidate) for candidate in product(values, repeat=front_count)]


def _v51_validate_fixed_rp(config: dict[str, Any], fixed_rp: dict[int, float], front_count: int) -> None:
    rp0 = float(config["initial"]["a_km"]) * (1.0 - float(config["initial"]["e"]))
    target_rp = float(config["target"]["a_km"]) * (1.0 - float(config["target"]["e"]))
    previous = rp0
    for index in range(1, front_count + 1):
        if index not in fixed_rp:
            continue
        value = fixed_rp[index]
        if not (rp0 + 1.0 < value < target_rp - 1.0):
            raise RuntimeError(f"固定第 {index} 次控后近地点高度超出可行范围。")
        if value <= previous + 1.0e-6:
            raise RuntimeError("固定控后近地点高度必须单调增加。")
        previous = value


def _v51_variable_bounds(
    config: dict[str, Any],
    front_count: int,
    fixed_rp: dict[int, float],
) -> list[tuple[float, float]]:
    rp0 = float(config["initial"]["a_km"]) * (1.0 - float(config["initial"]["e"]))
    target_rp = float(config["target"]["a_km"]) * (1.0 - float(config["target"]["e"]))
    bounds: list[tuple[float, float]] = []
    for index in range(1, front_count + 1):
        if index in fixed_rp:
            continue
        low = rp0 + 50.0
        for previous in range(1, index):
            if previous in fixed_rp:
                low = max(low, fixed_rp[previous] + 50.0)
        high = target_rp - 50.0
        for following in range(index + 1, front_count + 1):
            if following in fixed_rp:
                high = min(high, fixed_rp[following] - 50.0)
                break
        if low >= high:
            raise RuntimeError(f"第 {index} 次控后近地点高度无可行搜索区间。")
        bounds.append((low, high))
    return bounds


def _v51_rp_from_x(
    config: dict[str, Any],
    front_count: int,
    fixed_rp: dict[int, float],
    variable_indices: list[int],
    x_values: list[float],
) -> list[float]:
    if len(variable_indices) != len(x_values):
        raise RuntimeError("V5.1 高度变量维度不匹配。")
    rp0 = float(config["initial"]["a_km"]) * (1.0 - float(config["initial"]["e"]))
    target_rp = float(config["target"]["a_km"]) * (1.0 - float(config["target"]["e"]))
    by_index = dict(fixed_rp)
    for index, value in zip(variable_indices, x_values):
        by_index[index] = float(value)
    result: list[float] = []
    previous = rp0 + 50.0
    for index in range(1, front_count + 1):
        raw = by_index[index]
        low = max(previous + 50.0, rp0 + 50.0)
        high = target_rp - 50.0
        value = raw if index in fixed_rp else min(max(raw, low), high)
        if index in fixed_rp and not (low - 50.0 <= value <= high + 50.0):
            raise RuntimeError("固定控后近地点高度破坏单调约束。")
        result.append(value)
        previous = value
    return result


def _v51_template_points(
    config: dict[str, Any],
    front_count: int,
    fixed_rp: dict[int, float],
) -> list[np.ndarray]:
    variable_indices = [index for index in range(1, front_count + 1) if index not in fixed_rp]
    if not variable_indices:
        return [np.asarray([], dtype=float)]
    rp0 = float(config["initial"]["a_km"]) * (1.0 - float(config["initial"]["e"]))
    target_rp = float(config["target"]["a_km"]) * (1.0 - float(config["target"]["e"]))
    templates: list[list[float]] = []
    if front_count == 3:
        for fractions in ((0.08, 0.22, 0.48), (0.105, 0.229, 0.491), (0.16, 0.33, 0.58)):
            templates.append([rp0 + (target_rp - rp0) * fraction for fraction in fractions])
    for power in (0.85, 1.0, 1.35, 1.5, 2.0):
        templates.append([rp0 + (target_rp - rp0) * ((j + 1) / max(front_count + 1, 1)) ** power for j in range(front_count)])
    starts: list[np.ndarray] = []
    seen: set[tuple[float, ...]] = set()
    limit = int(config["hard_constraint_planner"]["max_local_starts_per_sequence"])
    for template in templates:
        full = list(template)
        for index, value in fixed_rp.items():
            full[index - 1] = value
        try:
            rp_full = _v51_rp_from_x(config, front_count, fixed_rp, variable_indices, [full[index - 1] for index in variable_indices])
        except Exception:
            continue
        x = np.asarray([rp_full[index - 1] for index in variable_indices], dtype=float)
        key = tuple(round(float(value), 1) for value in x)
        if key not in seen:
            starts.append(x)
            seen.add(key)
        if len(starts) >= limit:
            break
    return starts or [np.asarray([(low + high) * 0.5 for low, high in _v51_variable_bounds(config, front_count, fixed_rp)], dtype=float)]


def _v51_phase_chain_template_points(
    config: dict[str, Any],
    *,
    first: tuple[float, np.ndarray, np.ndarray, float],
    front_count: int,
    fixed_rp: dict[int, float],
    variable_indices: list[int],
    bounds: list[tuple[float, float]],
    q_aa: tuple[int, ...],
    q_ap: int,
) -> list[np.ndarray]:
    if front_count != 3 or len(q_aa) != 3 or not variable_indices:
        return []
    _elapsed, first_r, _first_v, first_lon = first
    apogee_radius = float(np.linalg.norm(first_r))
    rp0 = float(config["initial"]["a_km"]) * (1.0 - float(config["initial"]["e"]))
    target_rp = float(config["target"]["a_km"]) * (1.0 - float(config["target"]["e"]))

    planning_low, planning_high = _number_pair(config["longitude"]["planning_window_degE"], [45.0, 175.0])
    finite_low, finite_high = _number_pair(config["longitude"]["finite_margin_window_degE"], [50.0, 170.0])
    grid = [min(max(float(value), planning_low), planning_high) for value in np.linspace(finite_low, finite_high, 25)]

    rp1_by_lon2 = [
        _v51_phase_chain_rp_for_step(config, first_lon, lon2, int(q_aa[0]), apogee_radius)
        for lon2 in grid
    ]
    rp2_by_lons = [
        [
            _v51_phase_chain_rp_for_step(config, lon2, lon3, int(q_aa[1]), apogee_radius)
            for lon3 in grid
        ]
        for lon2 in grid
    ]
    rp3_by_lons = [
        [
            _v51_phase_chain_rp_for_step(config, lon3, lon4, int(q_aa[2]), apogee_radius)
            for lon4 in grid
        ]
        for lon3 in grid
    ]
    span = max(1.0, target_rp - rp0)

    candidates: list[tuple[float, np.ndarray]] = []
    for lon2_index, lon2 in enumerate(grid):
        rp1 = rp1_by_lon2[lon2_index]
        if rp1 is None:
            continue
        for lon3_index, lon3 in enumerate(grid):
            rp2 = rp2_by_lons[lon2_index][lon3_index]
            if rp2 is None:
                continue
            for lon4_index, lon4 in enumerate(grid):
                rp3 = rp3_by_lons[lon3_index][lon4_index]
                if rp3 is None:
                    continue
                full = [rp1, rp2, rp3]
                for index, value in fixed_rp.items():
                    full[index - 1] = value
                rp_full: list[float] = []
                previous = rp0 + 50.0
                rejected = False
                for index, raw in enumerate(full, start=1):
                    low = max(previous + 50.0, rp0 + 50.0)
                    high = target_rp - 50.0
                    if index in fixed_rp:
                        value = float(raw)
                        if not (low - 50.0 <= value <= high + 50.0):
                            rejected = True
                            break
                    else:
                        value = min(max(float(raw), low), high)
                    rp_full.append(value)
                    previous = value
                if rejected:
                    continue
                x = np.asarray([rp_full[index - 1] for index in variable_indices], dtype=float)
                if any(float(value) < low - 1.0e-6 or float(value) > high + 1.0e-6 for value, (low, high) in zip(x, bounds)):
                    continue
                fractions = [(value - rp0) / span for value in rp_full]
                fraction_penalty = sum((value - target) ** 2 for value, target in zip(fractions, (0.105, 0.229, 0.491)))
                window_penalty = sum(_window_distance(lon, [planning_low, planning_high]) ** 2 for lon in (lon2, lon3, lon4)) / 1.0e4
                candidates.append((fraction_penalty + window_penalty, x))
    candidates.sort(key=lambda item: item[0])
    result: list[np.ndarray] = []
    seen: set[tuple[float, ...]] = set()
    limit = min(2, max(1, int(config["hard_constraint_planner"]["max_local_starts_per_sequence"])))
    for _score, x in candidates:
        key = tuple(round(float(value), 1) for value in x)
        if key in seen:
            continue
        seen.add(key)
        result.append(x)
        if len(result) >= limit:
            break
    return result


def _v51_phase_chain_q_score(
    config: dict[str, Any],
    *,
    first: tuple[float, np.ndarray, np.ndarray, float],
    fixed_rp: dict[int, float],
    q_aa: tuple[int, ...],
) -> float:
    if len(q_aa) != 3:
        return 1.0e9
    _elapsed, first_r, _first_v, first_lon = first
    rp0 = float(config["initial"]["a_km"]) * (1.0 - float(config["initial"]["e"]))
    target_rp = float(config["target"]["a_km"]) * (1.0 - float(config["target"]["e"]))
    span = max(1.0, target_rp - rp0)
    apogee_radius = float(np.linalg.norm(first_r))
    mu = float(config["earth"]["mu_km3_s2"])
    earth_rot_deg_per_min = math.degrees(float(config["earth"]["omega_e_rad_s"]) * 60.0)
    planning_window = config["longitude"]["planning_window_degE"]
    lon = float(first_lon)
    previous_rp = rp0
    score = 0.0
    for index, (fraction, q) in enumerate(zip((0.105, 0.229, 0.491), q_aa), start=1):
        rp = float(fixed_rp.get(index, rp0 + span * fraction))
        if rp <= previous_rp + 1.0:
            score += 1.0e6
        previous_rp = rp
        a_km = 0.5 * (apogee_radius + rp)
        period_min = 2.0 * math.pi * math.sqrt(max(1.0, a_km**3 / mu)) / 60.0
        lon = (lon - earth_rot_deg_per_min * float(q) * period_min) % 360.0
        score += _window_distance(lon, planning_window) ** 2
    return score


def _v51_phase_chain_rp_for_step(
    config: dict[str, Any],
    lon_from_deg: float,
    lon_to_deg: float,
    q: int,
    apogee_radius_km: float,
) -> float | None:
    if q <= 0:
        return None
    earth_rot_deg_per_min = math.degrees(float(config["earth"]["omega_e_rad_s"]) * 60.0)
    lon_delta = (float(lon_from_deg) - float(lon_to_deg)) % 360.0
    rp0 = float(config["initial"]["a_km"]) * (1.0 - float(config["initial"]["e"]))
    target_rp = float(config["target"]["a_km"]) * (1.0 - float(config["target"]["e"]))
    best: float | None = None
    for wrap in range(0, 5):
        period_min = (lon_delta + 360.0 * wrap) / (earth_rot_deg_per_min * float(q))
        if period_min <= 0.0:
            continue
        a_km = (float(config["earth"]["mu_km3_s2"]) * (period_min * 60.0 / (2.0 * math.pi)) ** 2) ** (1.0 / 3.0)
        rp_km = 2.0 * a_km - apogee_radius_km
        if rp0 + 1.0 < rp_km < target_rp - 1.0:
            if best is None or rp_km < best:
                best = rp_km
    return best


def _v51_front_alpha_bounds(config: dict[str, Any]) -> tuple[float, float]:
    low, high = _number_pair(config["alpha"]["front_bounds_deg"], [-20.0, 40.0])
    low, high = min(low, high), max(low, high)
    initial_i = float(config["initial"]["i_deg"])
    target_i = float(config["target"]["i_deg"])
    if initial_i > target_i:
        low = max(0.0, low)
    elif initial_i < target_i:
        high = min(0.0, high)
    if low > high:
        low = high = 0.0
    return low, high


def _v51_apogee_to_rp_i(
    config: dict[str, Any],
    r: np.ndarray,
    v: np.ndarray,
    rp_target_km: float,
    i_target_deg: float,
) -> dict[str, Any] | None:
    radius = float(np.linalg.norm(r))
    if rp_target_km >= radius:
        return None
    r_hat = r / radius
    east, _north, south = _local_horizontal_basis(r)
    cos_delta = math.sqrt(max(0.0, 1.0 - float(r_hat[2]) ** 2))
    if cos_delta <= 1.0e-12:
        return None
    cos_beta = math.cos(math.radians(i_target_deg)) / cos_delta
    if abs(cos_beta) > 1.0 + 1.0e-12:
        return None
    cos_beta = float(np.clip(cos_beta, -1.0, 1.0))
    beta_abs = math.acos(cos_beta)
    post_a = 0.5 * (radius + rp_target_km)
    v_required = math.sqrt(float(config["earth"]["mu_km3_s2"]) * (2.0 / radius - 1.0 / post_a))
    low, high = _v51_front_alpha_bounds(config)
    candidates: list[dict[str, Any]] = []
    for beta in (beta_abs, -beta_abs):
        v_plus = v_required * (math.cos(beta) * east + math.sin(beta) * south)
        dv_vec = v_plus - v
        dv_mps = float(np.linalg.norm(dv_vec) * 1000.0)
        alpha_deg = _alpha_from_local_horizontal_vector(r, dv_vec)
        if low - 1.0e-9 <= alpha_deg <= high + 1.0e-9:
            candidates.append({"dv_mps": dv_mps, "alpha_deg": alpha_deg, "v_plus": v_plus, "post_a_km": post_a})
    return min(candidates, key=lambda item: item["dv_mps"]) if candidates else None


def _v51_terminal_perigee_burn(config: dict[str, Any], r: np.ndarray, v: np.ndarray) -> dict[str, Any]:
    radius = float(np.linalg.norm(r))
    speed = float(np.linalg.norm(v))
    v_required = math.sqrt(float(config["earth"]["mu_km3_s2"]) / radius)
    dv_vec = (v_required / speed - 1.0) * v
    return {
        "dv_mps": float(np.linalg.norm(dv_vec) * 1000.0),
        "alpha_deg": _alpha_from_local_horizontal_vector(r, dv_vec),
        "v_plus": v + dv_vec,
    }


def _v51_optimize_front_alpha(
    config: dict[str, Any],
    r: np.ndarray,
    v: np.ndarray,
    elapsed_s: float,
    rp_target_km: float,
    q_next: int,
    cache: dict[tuple[float, float, int], tuple[float, float]],
) -> tuple[float, float]:
    key = (round(float(elapsed_s), 3), round(float(rp_target_km), 3), int(q_next))
    if key in cache:
        return cache[key]
    radius = float(np.linalg.norm(r))
    target_a = 0.5 * (radius + rp_target_km)
    low, high = _v51_front_alpha_bounds(config)
    planning_window = config["longitude"]["planning_window_degE"]
    eval_cache: dict[float, float] = {}

    def objective(alpha_deg: float) -> float:
        alpha_key = round(float(alpha_deg), 6)
        if alpha_key in eval_cache:
            return eval_cache[alpha_key]
        dv_mps = _solve_dv_for_target_a(r, v, alpha_deg, target_a)
        if dv_mps is None:
            value = 1.0e15
        else:
            r_after = r.copy()
            v_after = v + (dv_mps / 1000.0) * _local_horizontal_direction(r, alpha_deg)
            next_elapsed, next_r, next_v = _next_apsis(config, r_after, v_after, elapsed_s, "A", int(q_next))
            rem = _v51_apogee_to_rp_i(config, next_r, next_v, float(config["target"]["a_km"]), float(config["target"]["i_deg"]))
            window_excess = _window_distance(_longitude_deg(config, next_r, next_elapsed), planning_window)
            value = dv_mps + (float(rem["dv_mps"]) if rem else 1.0e7) + 1000.0 * window_excess * window_excess
        eval_cache[alpha_key] = float(value)
        return float(value)

    grid = np.linspace(low, high, 9)
    best_alpha = min(((objective(float(alpha)), float(alpha)) for alpha in grid), key=lambda item: item[0])[1]
    if minimize_scalar is not None and high > low:
        result = minimize_scalar(
            objective,
            bounds=(max(low, best_alpha - 6.0), min(high, best_alpha + 6.0)),
            method="bounded",
            options={"xatol": 0.01, "maxiter": 50},
        )
        if bool(getattr(result, "success", False)):
            best_alpha = float(result.x)
    dv_star = _solve_dv_for_target_a(r, v, best_alpha, target_a)
    if dv_star is None:
        raise RuntimeError("远地点 alpha 优化后无法反解 Δv。")
    cache[key] = (float(dv_star), float(best_alpha))
    return cache[key]


def _v51_simulate_candidate(
    config: dict[str, Any],
    first: tuple[float, np.ndarray, np.ndarray, float],
    rp_targets_km: list[float],
    q_aa: tuple[int, ...],
    q_ap: int,
) -> dict[str, Any]:
    elapsed_s, r, v, _lon = first
    r = np.asarray(r, dtype=float)
    v = np.asarray(v, dtype=float)
    mass = float(config["initial"]["m0_kg"])
    burns: list[DesignManeuverBurn] = []
    raw_window = config["longitude"]["raw_window_degE"]
    planning_window = config["longitude"]["planning_window_degE"]
    cache: dict[tuple[float, float, int], tuple[float, float]] = {}

    def append_burn(
        *,
        index: int,
        burn_type: str,
        apsis: str,
        elapsed: float,
        r_pre: np.ndarray,
        v_pre: np.ndarray,
        dv_mps: float,
        alpha_deg: float,
        v_plus: np.ndarray,
        target_post_a: float | None,
        flight_revolution: int,
    ) -> None:
        nonlocal mass
        pre_a, *_ = _rv_to_coe(r_pre, v_pre)
        post_a, post_e, post_i_rad, *_ = _rv_to_coe(r_pre, v_plus)
        burn_time = _burn_time_for_delta_v(config, mass, dv_mps)
        mass = max(1.0, mass - burn_time["propellant_kg"])
        lon = _longitude_deg(config, r_pre, elapsed)
        timestamp = parse_utc(str(config["initial"]["t0_epoch"])) + timedelta(seconds=elapsed)
        longitude_ok = _in_window(lon, planning_window)
        burns.append(
            DesignManeuverBurn(
                index=index,
                burn_type=burn_type,
                apsis=apsis,
                elapsed_min=elapsed / 60.0,
                beijing_time=(timestamp + BEIJING_OFFSET).strftime("%Y-%m-%d %H:%M:%S.%f"),
                longitude_deg_e=lon,
                delta_v_mps=dv_mps,
                alpha_deg=alpha_deg,
                target_post_a_km=target_post_a,
                total_burn_time_min=burn_time["total_burn_time_min"],
                propellant_kg=burn_time["propellant_kg"],
                post_a_km=post_a,
                post_e=post_e,
                post_i_deg=math.degrees(post_i_rad),
                duration_ok=burn_time["total_burn_time_min"] <= float(config["burn_limit"]["max_total_burn_time_min"]) + 1.0e-9,
                longitude_ok=longitude_ok,
                flight_revolution=flight_revolution,
                position_label="远地点" if apsis == "A" else "近地点",
                orbit_period_min=_orbit_period_min(config, post_a),
                post_mass_kg=mass,
                semi_major_axis_control_km=post_a - pre_a,
            )
        )

    flight_revolution = 2
    for index, rp_target in enumerate(rp_targets_km, start=1):
        q_next = int(q_aa[index - 1])
        dv_mps, alpha_deg = _v51_optimize_front_alpha(config, r, v, elapsed_s, rp_target, q_next, cache)
        v_after = v + (dv_mps / 1000.0) * _local_horizontal_direction(r, alpha_deg)
        append_burn(
            index=index,
            burn_type="front",
            apsis="A",
            elapsed=elapsed_s,
            r_pre=r,
            v_pre=v,
            dv_mps=dv_mps,
            alpha_deg=alpha_deg,
            v_plus=v_after,
            target_post_a=0.5 * (float(np.linalg.norm(r)) + rp_target),
            flight_revolution=flight_revolution,
        )
        elapsed_s, r, v = _next_apsis(config, r, v_after, elapsed_s, "A", q_next)
        flight_revolution += q_next

    terminal = _v51_apogee_to_rp_i(config, r, v, float(config["target"]["a_km"]), float(config["target"]["i_deg"]))
    if terminal is None:
        return {"success": False, "reason": "terminal apogee solve infeasible"}
    terminal_index = len(rp_targets_km) + 1
    append_burn(
        index=terminal_index,
        burn_type="terminal_apogee",
        apsis="A",
        elapsed=elapsed_s,
        r_pre=r,
        v_pre=v,
        dv_mps=float(terminal["dv_mps"]),
        alpha_deg=float(terminal["alpha_deg"]),
        v_plus=np.asarray(terminal["v_plus"], dtype=float),
        target_post_a=float(terminal["post_a_km"]),
        flight_revolution=flight_revolution,
    )
    r_after = r.copy()
    v_after = np.asarray(terminal["v_plus"], dtype=float)
    elapsed_p, r_p, v_p = _next_apsis(config, r_after, v_after, elapsed_s, "P", int(q_ap) + 1)
    circ = _v51_terminal_perigee_burn(config, r_p, v_p)
    append_burn(
        index=terminal_index + 1,
        burn_type="terminal_perigee",
        apsis="P",
        elapsed=elapsed_p,
        r_pre=r_p,
        v_pre=v_p,
        dv_mps=float(circ["dv_mps"]),
        alpha_deg=float(circ["alpha_deg"]),
        v_plus=np.asarray(circ["v_plus"], dtype=float),
        target_post_a=float(config["target"]["a_km"]),
        flight_revolution=flight_revolution + int(q_ap),
    )

    final = burns[-1]
    return {
        "success": True,
        "q_AA": list(q_aa),
        "q_AP": int(q_ap),
        "rp_targets_km": [float(value) for value in rp_targets_km],
        "burns": burns,
        "terminal": {
            "a_error_km": final.post_a_km - float(config["target"]["a_km"]),
            "e_error": final.post_e - float(config["target"]["e"]),
            "i_error_deg": final.post_i_deg - float(config["target"]["i_deg"]),
            "lon_error_deg": _wrap180(final.longitude_deg_e - float(config["target"]["lon_degE"])),
            "total_dv_mps": sum(burn.delta_v_mps for burn in burns),
            "total_propellant_kg": sum(burn.propellant_kg for burn in burns),
            "max_total_burn_min": max(burn.total_burn_time_min for burn in burns),
            "max_raw_window_excess_deg": max(_window_distance(burn.longitude_deg_e, raw_window) for burn in burns),
            "max_planning_window_excess_deg": max(_window_distance(burn.longitude_deg_e, planning_window) for burn in burns),
        },
    }


def _v51_feasibility_violations(config: dict[str, Any], rec: dict[str, Any]) -> dict[str, float]:
    if not rec.get("success"):
        return {"invalid": 1.0}
    hard_cfg = config["hard_constraint_planner"]
    terminal = rec["terminal"]
    tolerance = config["terminal_tolerance"]
    return {
        "invalid": 0.0,
        "raw_window_excess_deg": max(0.0, float(terminal["max_raw_window_excess_deg"])) if hard_cfg["hard_raw_window"] else 0.0,
        "planning_window_excess_deg": max(0.0, float(terminal["max_planning_window_excess_deg"])) if hard_cfg["hard_planning_window"] else 0.0,
        "duration_excess_min": max(0.0, float(terminal["max_total_burn_min"]) - float(config["burn_limit"]["max_total_burn_time_min"])),
        "terminal_lon_excess_deg": max(0.0, abs(float(terminal["lon_error_deg"])) - float(tolerance["lon_deg"])),
        "terminal_a_excess_km": max(0.0, abs(float(terminal["a_error_km"])) - float(tolerance["a_km"])),
        "terminal_e_excess": max(0.0, abs(float(terminal["e_error"])) - float(tolerance["e"])),
        "terminal_i_excess_deg": max(0.0, abs(float(terminal["i_error_deg"])) - float(tolerance["i_deg"])),
    }


def _v51_is_feasible(config: dict[str, Any], rec: dict[str, Any], *, ignore_terminal_i: bool = False) -> bool:
    violations = _v51_feasibility_violations(config, rec)
    if ignore_terminal_i:
        violations = dict(violations)
        violations["terminal_i_excess_deg"] = 0.0
    return all(value <= 1.0e-9 for value in violations.values())


def _v51_rank_record(config: dict[str, Any], rec: dict[str, Any]) -> tuple[float, ...]:
    if not rec.get("success"):
        return (1.0, 1.0e99, 1.0e99, 1.0e99)
    violations = _v51_feasibility_violations(config, rec)
    violation_sum = sum(value * value for value in violations.values())
    terminal = rec["terminal"]
    return (
        0.0 if _v51_is_feasible(config, rec) else 1.0,
        violation_sum,
        float(terminal["total_propellant_kg"]),
        float(terminal["total_dv_mps"]),
        float(terminal["max_total_burn_min"]),
        abs(float(terminal["lon_error_deg"])),
    )


def _v51_hard_objective(
    config: dict[str, Any],
    first: tuple[float, np.ndarray, np.ndarray, float],
    fixed_rp: dict[int, float],
    variable_indices: list[int],
    front_count: int,
    x_values: list[float],
    q_aa: tuple[int, ...],
    q_ap: int,
) -> float:
    try:
        rp_targets = _v51_rp_from_x(config, front_count, fixed_rp, variable_indices, x_values)
        rec = _v51_simulate_candidate(config, first, rp_targets, q_aa, q_ap)
    except Exception:
        return 1.0e18
    violations = _v51_feasibility_violations(config, rec)
    violation_sum = sum(value * value for value in violations.values())
    if violation_sum > 0.0:
        return 1.0e12 * violation_sum + 1.0e9
    terminal = rec["terminal"]
    return float(terminal["total_propellant_kg"]) + 1.0e-4 * float(terminal["total_dv_mps"])


def _v51_use_template_only_fast_path(
    front_count: int,
    fixed_rp: dict[int, float],
    variable_indices: list[int],
) -> bool:
    del fixed_rp
    return front_count == 3 and len(variable_indices) >= 2


def _v51_refine_last_variable_from_best_template(
    config: dict[str, Any],
    *,
    first: tuple[float, np.ndarray, np.ndarray, float],
    fixed_rp: dict[int, float],
    variable_indices: list[int],
    bounds: list[tuple[float, float]],
    front_count: int,
    records: list[dict[str, Any]],
    q_aa: tuple[int, ...],
    q_ap: int,
) -> list[dict[str, Any]]:
    if minimize_scalar is None or not _v51_use_template_only_fast_path(front_count, fixed_rp, variable_indices):
        return []
    successful = [rec for rec in records if rec.get("success")]
    if not successful:
        return []
    best = min(successful, key=lambda rec: _v51_rank_record(config, rec))
    violations = _v51_feasibility_violations(config, best)
    if violations["duration_excess_min"] > 1.0e-9 or violations["raw_window_excess_deg"] > 1.0e-9 or violations["planning_window_excess_deg"] > 1.0e-9:
        return []
    if violations["terminal_lon_excess_deg"] <= 1.0e-9:
        return []
    rp_targets = [float(value) for value in best["rp_targets_km"]]
    x_values = [rp_targets[index - 1] for index in variable_indices]
    variable_offset = len(variable_indices) - 1
    low, high = bounds[variable_offset]

    def objective(value: float) -> float:
        trial = list(x_values)
        trial[variable_offset] = float(value)
        return _v51_hard_objective(
            config,
            first,
            fixed_rp,
            variable_indices,
            front_count,
            trial,
            q_aa,
            q_ap,
        )

    result = minimize_scalar(
        objective,
        bounds=(low, high),
        method="bounded",
        options={"xatol": 1.0e-3, "maxiter": min(24, int(config["hard_constraint_planner"]["local_maxiter"]))},
    )
    try:
        refined_x = list(x_values)
        refined_x[variable_offset] = float(result.x)
        refined_targets = _v51_rp_from_x(config, front_count, fixed_rp, variable_indices, refined_x)
        refined = _v51_simulate_candidate(config, first, refined_targets, q_aa, q_ap)
        refined["optimizer"] = {
            "method": "template_x3_scalar",
            "success": bool(result.success),
            "nfev": int(getattr(result, "nfev", -1)),
        }
        return [refined]
    except Exception as exc:
        return [{"success": False, "reason": str(exc), "optimizer": {"method": "template_x3_scalar", "success": False}}]


def _v51_optimize_sequence(
    config: dict[str, Any],
    *,
    first: tuple[float, np.ndarray, np.ndarray, float],
    fixed_rp: dict[int, float],
    variable_indices: list[int],
    bounds: list[tuple[float, float]],
    starts: list[np.ndarray],
    q_aa: tuple[int, ...],
    q_ap: int,
) -> list[dict[str, Any]]:
    front_count = len(q_aa)
    records: list[dict[str, Any]] = []
    if not variable_indices:
        try:
            rp_targets = _v51_rp_from_x(config, front_count, fixed_rp, variable_indices, [])
            rec = _v51_simulate_candidate(config, first, rp_targets, q_aa, q_ap)
            records.append(rec)
        except Exception as exc:
            records.append({"success": False, "reason": str(exc)})
        return records

    def append_record(x_values: list[float], optimizer: dict[str, Any]) -> None:
        try:
            rp_targets = _v51_rp_from_x(config, front_count, fixed_rp, variable_indices, x_values)
            rec = _v51_simulate_candidate(config, first, rp_targets, q_aa, q_ap)
            rec["optimizer"] = optimizer
            records.append(rec)
        except Exception as exc:
            records.append({"success": False, "reason": str(exc), "optimizer": optimizer})

    for start in starts:
        append_record([float(value) for value in start], {"method": "template", "success": True, "nfev": 1})

    if _v51_use_template_only_fast_path(front_count, fixed_rp, variable_indices):
        records.extend(
            _v51_refine_last_variable_from_best_template(
                config,
                first=first,
                fixed_rp=fixed_rp,
                variable_indices=variable_indices,
                bounds=bounds,
                front_count=front_count,
                records=records,
                q_aa=q_aa,
                q_ap=q_ap,
            )
        )
        return records

    if len(variable_indices) == 1 and minimize_scalar is not None:
        low, high = bounds[0]
        result = minimize_scalar(
            lambda value: _v51_hard_objective(config, first, fixed_rp, variable_indices, front_count, [float(value)], q_aa, q_ap),
            bounds=(low, high),
            method="bounded",
            options={"xatol": 1.0e-3, "maxiter": int(config["hard_constraint_planner"]["local_maxiter"])},
        )
        append_record([float(result.x)], {"method": "bounded_scalar", "success": bool(result.success), "nfev": int(getattr(result, "nfev", -1))})
        return records
    if minimize is not None:
        for start in starts:
            result = minimize(
                lambda values: _v51_hard_objective(
                    config,
                    first,
                    fixed_rp,
                    variable_indices,
                    front_count,
                    [float(value) for value in values],
                    q_aa,
                    q_ap,
                ),
                np.asarray(start, dtype=float),
                method="Powell",
                bounds=bounds,
                options={"maxiter": int(config["hard_constraint_planner"]["local_maxiter"]), "xtol": 1.0e-2, "ftol": 1.0e-3, "disp": False},
            )
            append_record([float(value) for value in result.x], {"method": "Powell_barrier", "success": bool(result.success), "nfev": int(getattr(result, "nfev", -1))})
    return records


def _v51_unique_record_summaries(config: dict[str, Any], records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for rec in records:
        if not rec.get("success"):
            continue
        key = (
            tuple(rec.get("q_AA", [])),
            int(rec.get("q_AP", 0)),
            tuple(round(float(value), 2) for value in rec.get("rp_targets_km", [])),
        )
        if key in seen:
            continue
        seen.add(key)
        terminal = rec["terminal"]
        unique.append(
            {
                "q_sequence": list(rec.get("q_AA", [])) + [int(rec.get("q_AP", 0))],
                "score": list(_v51_rank_record(config, rec)),
                "propellant_kg": float(terminal["total_propellant_kg"]),
                "total_delta_v_mps": float(terminal["total_dv_mps"]),
                "lon_error_deg": float(terminal["lon_error_deg"]),
                "max_burn_duration_min": float(terminal["max_total_burn_min"]),
                "hp_targets_km": [float(value) - float(config["earth"]["Re_km"]) for value in rec.get("rp_targets_km", [])],
                "feasible": _v51_is_feasible(config, rec),
            }
        )
        if len(unique) >= 5:
            break
    return unique


def _v51_scan_feasible_q_sequences(
    config: dict[str, Any],
    *,
    first: tuple[float, np.ndarray, np.ndarray, float],
    fixed_rp: dict[int, float],
    variable_indices: list[int],
    bounds: list[tuple[float, float]],
    starts: list[np.ndarray],
    q_aa_candidates: list[tuple[int, ...]],
    q_ap_candidates: list[int],
    front_count: int,
    scan_all_q: bool = False,
    ignore_terminal_i: bool = False,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    phase_starts_by_q: dict[tuple[int, ...], list[np.ndarray]] = {}
    seen: set[tuple[int, ...]] = set()
    q_ranked = sorted(
        (
            _v51_phase_chain_q_score(config, first=first, fixed_rp=fixed_rp, q_aa=q_aa),
            q_aa,
        )
        for q_aa in q_aa_candidates
    )
    if scan_all_q:
        q_aa_to_scan = [q_aa for _score, q_aa in q_ranked]
    else:
        q_score_limit = 4000.0
        q_scan_limit = max(10, int(config["hard_constraint_planner"]["prefilter_top_k"]))
        q_aa_to_scan = [
            q_aa
            for score, q_aa in q_ranked
            if score <= q_score_limit
        ][:q_scan_limit]
        if not q_aa_to_scan:
            q_aa_to_scan = [q_aa for _score, q_aa in q_ranked[:q_scan_limit]]
    for q_aa in q_aa_to_scan:
        phase_starts = phase_starts_by_q.get(q_aa)
        if phase_starts is None:
            phase_starts = _v51_phase_chain_template_points(
                config,
                first=first,
                front_count=front_count,
                fixed_rp=fixed_rp,
                variable_indices=variable_indices,
                bounds=bounds,
                q_aa=q_aa,
                q_ap=0,
            )
            phase_starts_by_q[q_aa] = phase_starts
        candidate_starts = phase_starts or starts
        for q_ap in q_ap_candidates:
            key = tuple([*q_aa, int(q_ap)])
            if key in seen:
                continue
            records: list[dict[str, Any]] = []
            if not variable_indices:
                try:
                    rp_targets = _v51_rp_from_x(config, front_count, fixed_rp, variable_indices, [])
                    records.append(_v51_simulate_candidate(config, first, rp_targets, q_aa, q_ap))
                except Exception as exc:
                    records.append({"success": False, "reason": str(exc)})
            else:
                for start in candidate_starts:
                    try:
                        x_values = [float(value) for value in start]
                        rp_targets = _v51_rp_from_x(config, front_count, fixed_rp, variable_indices, x_values)
                        records.append(_v51_simulate_candidate(config, first, rp_targets, q_aa, q_ap))
                    except Exception as exc:
                        records.append({"success": False, "reason": str(exc)})
                successful = [rec for rec in records if rec.get("success")]
                if successful:
                    best_template = min(successful, key=lambda rec: _v51_rank_record(config, rec))
                    violations = _v51_feasibility_violations(config, best_template)
                    if (
                        violations["duration_excess_min"] <= 1.0e-9
                        and violations["raw_window_excess_deg"] <= 1.0e-9
                        and violations["planning_window_excess_deg"] <= 1.0e-9
                        and violations["terminal_lon_excess_deg"] <= 30.0
                    ):
                        records.extend(
                            _v51_refine_last_variable_from_best_template(
                                config,
                                first=first,
                                fixed_rp=fixed_rp,
                                variable_indices=variable_indices,
                                bounds=bounds,
                                front_count=front_count,
                                records=records,
                                q_aa=q_aa,
                                q_ap=q_ap,
                            )
                        )
            feasible_records = [
                rec
                for rec in records
                if rec.get("success") and _v51_is_feasible(config, rec, ignore_terminal_i=ignore_terminal_i)
            ]
            if not feasible_records:
                continue
            seen.add(key)
            best = min(feasible_records, key=lambda rec: abs(float(rec["terminal"]["lon_error_deg"])))
            terminal = best["terminal"]
            result.append(
                {
                    "q_sequence": list(best.get("q_AA", [])) + [int(best.get("q_AP", 0))],
                    "lon_error_deg": float(terminal["lon_error_deg"]),
                    "max_burn_duration_min": float(terminal["max_total_burn_min"]),
                    "hp_targets_km": [
                        float(value) - float(config["earth"]["Re_km"])
                        for value in best.get("rp_targets_km", [])
                    ],
                }
            )
    result.sort(key=lambda item: tuple(int(value) for value in item["q_sequence"]))
    return result


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


def _coerce_hard_constraint_source(source: dict[str, Any]) -> dict[str, Any]:
    hard_cfg = source.get("hard_constraint_planner")
    if not isinstance(hard_cfg, dict):
        return source
    fixed_hp = hard_cfg.get("fixed_hp_targets_km")
    if isinstance(fixed_hp, str):
        result = dict(source)
        result["hard_constraint_planner"] = dict(hard_cfg)
        result["hard_constraint_planner"]["fixed_hp_targets_km"] = _parse_index_float_map(fixed_hp)
        return result
    return source


def _optional_float(value: object) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def _parse_int_list(value: object, *, minimum: int) -> list[int]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        raw_items = value.replace(";", ",").split(",")
    elif isinstance(value, (list, tuple)):
        raw_items = list(value)
    else:
        raw_items = [value]
    result: list[int] = []
    for raw in raw_items:
        if raw in (None, ""):
            continue
        text = str(raw).strip()
        if not text:
            continue
        result.append(max(minimum, int(float(text))))
    return result


def _parse_index_float_map(value: object) -> dict[str, float]:
    if value in (None, ""):
        return {}
    if isinstance(value, dict):
        items = value.items()
    elif isinstance(value, str):
        pairs: list[tuple[str, str]] = []
        for chunk in value.replace(";", ",").split(","):
            text = chunk.strip()
            if not text:
                continue
            if ":" in text:
                key, raw_value = text.split(":", 1)
            elif "=" in text:
                key, raw_value = text.split("=", 1)
            else:
                continue
            pairs.append((key, raw_value))
        items = pairs
    else:
        return {}
    result: dict[str, float] = {}
    for key, raw_value in items:
        index = max(1, int(float(str(key).strip())))
        result[str(index)] = float(str(raw_value).strip())
    return dict(sorted(result.items(), key=lambda item: int(item[0])))


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
    empty_diagnostics = {
        "q_total_candidates": 1,
        "q_tested_fast": 0,
        "q_tested_slsqp": 0,
        "feasible_solutions": 0,
        "optimizer_method": "disabled",
        "optimizer_converged": False,
        "fallback_used": False,
    }
    if not _can_phase_optimize(config, orbit_type, apsis_pattern):
        return {
            "q_sequence": list(base_sequence),
            "delta_vs": list(delta_vs),
            "alpha_values": list(alpha_values),
            "optimized": False,
            "delta_v_optimized": False,
            "alpha_optimized": False,
            "initial_error_deg": None,
            "burns": [],
            "diagnostics": empty_diagnostics,
        }

    base_score, base_error, _base_burns = _phase_score(
        config, apsis_pattern, delta_vs, alpha_values, base_sequence
    )
    q_candidates = _phase_q_candidates(config, apsis_pattern, base_sequence)
    screened: list[dict[str, Any]] = []
    for candidate in q_candidates:
        score, error, burns = _phase_score(config, apsis_pattern, delta_vs, alpha_values, candidate)
        screened.append(
            {
                "q_sequence": list(candidate),
                "score": score,
                "error_deg": error,
                "burns": burns,
            }
        )
    screened.sort(key=lambda item: item["score"])
    optimize_top_k = min(len(screened), int(config["optimizer"].get("q_fast_optimize_top_k", 10)))
    selected_keys = {tuple(item["q_sequence"]) for item in screened[:optimize_top_k]}
    selected_keys.add(tuple(base_sequence))
    candidates: list[dict[str, Any]] = []
    for candidate in [item["q_sequence"] for item in screened if tuple(item["q_sequence"]) in selected_keys]:
        optimized = _optimize_phase_controls(
            config,
            orbit_type=orbit_type,
            apsis_pattern=apsis_pattern,
            delta_vs=delta_vs,
            alpha_values=alpha_values,
            q_sequence=candidate,
        )
        candidates.append(
            {
                "q_sequence": list(candidate),
                "delta_vs": list(optimized["delta_vs"]),
                "alpha_values": list(optimized["alpha_values"]),
                "error_deg": float(optimized["error_deg"]),
                "score": optimized["score"],
                "burns": optimized["burns"],
                "method": "coordinate",
                "slsqp_success": False,
                "slsqp_message": "",
                "slsqp_nfev": 0,
            }
        )
    if not candidates:
        candidates.append(
            {
                "q_sequence": list(base_sequence),
                "delta_vs": list(delta_vs),
                "alpha_values": list(alpha_values),
                "error_deg": base_error,
                "score": base_score,
                "burns": _base_burns,
                "method": "base",
                "slsqp_success": False,
                "slsqp_message": "",
                "slsqp_nfev": 0,
            }
        )
    candidates.sort(key=lambda item: item["score"])
    diagnostics = _build_phase_diagnostics(config, candidates, optimizer_method="coordinate")
    diagnostics["q_total_candidates"] = len(q_candidates)
    diagnostics["q_screened_candidates"] = len(screened)
    if _should_use_slsqp(config):
        start = perf_counter()
        refined, slsqp_diag = _refine_top_phase_candidates_slsqp(
            config,
            orbit_type=orbit_type,
            apsis_pattern=apsis_pattern,
            candidates=candidates,
            started_at=start,
        )
        candidates = refined
        diagnostics.update(slsqp_diag)
    else:
        diagnostics.update(
            {
                "q_tested_slsqp": 0,
                "optimizer_method": str(config["optimizer"].get("method", "coordinate")),
                "optimizer_converged": False,
                "fallback_used": False,
            }
        )
    candidates.sort(key=lambda item: item["score"])
    best = candidates[0]
    best_sequence = list(best["q_sequence"])
    best_delta_vs = list(best["delta_vs"])
    best_alpha_values = list(best["alpha_values"])
    best_burns = list(best["burns"])
    best_error = float(best["error_deg"])
    final_diagnostics = _build_phase_diagnostics(config, candidates, optimizer_method=str(best["method"]))
    final_diagnostics["q_total_candidates"] = len(q_candidates)
    final_diagnostics["q_screened_candidates"] = len(screened)
    diagnostics.update(final_diagnostics)
    return {
        "q_sequence": best_sequence,
        "delta_vs": best_delta_vs,
        "alpha_values": best_alpha_values,
        "optimized": best_sequence != base_sequence
        or best_delta_vs != list(delta_vs)
        or best_alpha_values != list(alpha_values),
        "delta_v_optimized": best_delta_vs != list(delta_vs),
        "alpha_optimized": best_alpha_values != list(alpha_values),
        "initial_error_deg": base_error,
        "burns": best_burns,
        "diagnostics": diagnostics,
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
    q_limit = _q_limit(config)
    candidates: list[list[int]] = []
    seen: set[tuple[int, ...]] = set()
    raw: list[list[int]] = [base_sequence]
    for values in product(range(1, q_limit + 1), repeat=len(base_sequence)):
        candidate = list(values)
        if apsis_pattern[-2:] == ["A", "P"]:
            candidate[-1] = 1
        raw.append(candidate)
    for candidate in raw:
        normalized = [max(1, min(q_limit, int(value))) for value in candidate]
        if apsis_pattern[-2:] == ["A", "P"]:
            normalized[-1] = 1
        key = tuple(normalized)
        if key not in seen:
            candidates.append(normalized)
            seen.add(key)
    return candidates


def _optimize_phase_controls(
    config: dict[str, Any],
    *,
    orbit_type: str,
    apsis_pattern: list[str],
    delta_vs: list[float | None],
    alpha_values: list[float],
    q_sequence: list[int],
) -> dict[str, Any]:
    front_count = max(0, len(delta_vs) - 2)
    if front_count < 3:
        score, error, _burns = _phase_score(config, apsis_pattern, delta_vs, alpha_values, q_sequence)
        return {
            "delta_vs": list(delta_vs),
            "alpha_values": list(alpha_values),
            "error_deg": error,
            "score": score,
            "burns": _burns,
        }
    front_base = [float(value or 0.0) for value in delta_vs[:front_count]]
    seeds = [
        front_base,
        [front_base[0] * 0.55, front_base[1] * 0.55, front_base[2] * 1.18],
    ]
    best_delta_vs = list(delta_vs)
    best_alpha_values = list(alpha_values)
    best_score, best_error, best_burns = _phase_score(
        config, apsis_pattern, best_delta_vs, best_alpha_values, q_sequence
    )
    for seed in seeds:
        optimized_delta_vs, optimized_alpha_values, error, score, opt_burns = _coordinate_search_phase_controls(
            config,
            orbit_type=orbit_type,
            apsis_pattern=apsis_pattern,
            delta_vs=delta_vs,
            alpha_values=alpha_values,
            q_sequence=q_sequence,
            seed_front=seed,
            search_alpha=False,
        )
        if score < best_score:
            best_delta_vs = optimized_delta_vs
            best_alpha_values = optimized_alpha_values
            best_error = error
            best_score = score
            best_burns = opt_burns
    if bool(config["alpha"]["optimize_alpha"]):
        target_post_a_values = [burn.post_a_km for burn in best_burns] if best_burns else None
        alpha_seed = (
            _inclination_weighted_alpha_seed(config, apsis_pattern, best_burns, best_alpha_values)
            if target_post_a_values is not None
            else best_alpha_values
        )
        refined_delta_vs, refined_alpha_values, refined_error, refined_score, refined_burns = (
            _coordinate_search_phase_controls(
                config,
                orbit_type=orbit_type,
                apsis_pattern=apsis_pattern,
                delta_vs=[None] * len(delta_vs),
                alpha_values=alpha_seed,
                q_sequence=q_sequence,
                seed_front=[float(value or 0.0) for value in best_delta_vs[:front_count]],
                search_alpha=True,
                target_post_a_values=target_post_a_values,
            )
        )
        if refined_score < best_score:
            best_delta_vs = refined_delta_vs
            best_alpha_values = refined_alpha_values
            best_error = refined_error
            best_score = refined_score
            best_burns = refined_burns
    return {
        "delta_vs": best_delta_vs,
        "alpha_values": best_alpha_values,
        "error_deg": best_error,
        "score": best_score,
        "burns": best_burns,
    }


def _should_use_slsqp(config: dict[str, Any]) -> bool:
    if minimize is None:
        return False
    optimizer = config["optimizer"]
    method = str(optimizer.get("method", "")).upper()
    return (
        bool(optimizer.get("enabled", True))
        and bool(config["alpha"].get("optimize_alpha", True))
        and method == "SLSQP"
        and int(optimizer.get("slsqp_top_k", 0)) > 0
    )


def _refine_top_phase_candidates_slsqp(
    config: dict[str, Any],
    *,
    orbit_type: str,
    apsis_pattern: list[str],
    candidates: list[dict[str, Any]],
    started_at: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    top_k = min(len(candidates), int(config["optimizer"]["slsqp_top_k"]))
    multistart_top_k = min(top_k, int(config["optimizer"]["slsqp_multistart_top_k"]))
    refined = [dict(candidate) for candidate in candidates]
    tested = 0
    converged = 0
    nfev = 0
    fallback_used = False
    time_budget = max(0.0, float(config["optimizer"].get("time_budget_sec", 30.0)))
    for rank, candidate in enumerate(candidates[:top_k]):
        if time_budget > 0.0 and perf_counter() - started_at >= time_budget:
            fallback_used = True
            break
        result = _optimize_phase_continuous_slsqp(
            config,
            orbit_type=orbit_type,
            apsis_pattern=apsis_pattern,
            candidate=candidate,
            seed_count=3 if rank < multistart_top_k else 1,
            started_at=started_at,
        )
        tested += 1
        nfev += int(result.get("slsqp_nfev", 0)) if result else 0
        if not result:
            fallback_used = True
            continue
        converged += 1 if bool(result.get("slsqp_success", False)) else 0
        if result["score"] < refined[rank]["score"]:
            refined[rank] = result
        else:
            fallback_used = True
    refined.sort(key=lambda item: item["score"])
    return refined, {
        "q_tested_slsqp": tested,
        "optimizer_method": "SLSQP" if tested else "coordinate",
        "optimizer_converged": converged > 0,
        "slsqp_converged_candidates": converged,
        "slsqp_nfev": nfev,
        "fallback_used": fallback_used or (refined and refined[0].get("method") != "SLSQP"),
        "elapsed_sec": perf_counter() - started_at,
    }


def _optimize_phase_continuous_slsqp(
    config: dict[str, Any],
    *,
    orbit_type: str,
    apsis_pattern: list[str],
    candidate: dict[str, Any],
    seed_count: int,
    started_at: float,
) -> dict[str, Any] | None:
    if minimize is None or orbit_type != "supersynchronous_transfer" or apsis_pattern[-1] != "P":
        return None
    x0 = _continuous_x_from_burns(config, apsis_pattern, candidate.get("burns", []))
    if x0 is None:
        return None
    bounds = _continuous_bounds(config, orbit_type, apsis_pattern, x0)
    seeds = _continuous_seeds(config, apsis_pattern, x0)[: max(1, seed_count)]
    best: dict[str, Any] | None = None
    total_nfev = 0
    time_budget = max(0.0, float(config["optimizer"].get("time_budget_sec", 30.0)))
    for seed in seeds:
        if time_budget > 0.0 and perf_counter() - started_at >= time_budget:
            break
        cache: dict[tuple[float, ...], tuple[tuple[float, ...], float, dict[str, float | bool], list[DesignManeuverBurn]]] = {}

        def evaluate(x_values: Any) -> tuple[tuple[float, ...], float, dict[str, float | bool], list[DesignManeuverBurn]]:
            key = tuple(round(float(value), 7) for value in x_values)
            cached = cache.get(key)
            if cached is not None:
                return cached
            target_post_a_values, alpha_trial = _continuous_unpack(config, apsis_pattern, [float(v) for v in x_values])
            warnings: list[str] = []
            burns = _build_burns(
                config,
                apsis_pattern=apsis_pattern,
                delta_vs=[None] * len(apsis_pattern),
                alpha_values=alpha_trial,
                warnings=warnings,
                q_sequence_override=list(candidate["q_sequence"]),
                target_post_a_values=target_post_a_values,
            )
            score, signed_error, details = _phase_score_from_burns(config, burns, warnings)
            value = (score, signed_error, details, burns)
            cache[key] = value
            return value

        def objective(x_values: Any) -> float:
            _score, _error, details, _burns = evaluate(x_values)
            return _scalar_phase_cost(config, details)

        def hard_constraints(x_values: Any) -> list[float]:
            _score, _error, details, _burns = evaluate(x_values)
            tolerance = config["terminal_tolerance"]
            duration_limit = float(config["burn_limit"]["max_total_burn_time_min"])
            values = [
                float(tolerance["lon_deg"]) - abs(float(details["terminal_lon_error_deg"])),
                float(tolerance["i_deg"]) - abs(float(details["terminal_i_error_deg"])),
                float(tolerance["a_km"]) - abs(float(details["terminal_a_error_km"])),
                float(tolerance["e"]) - abs(float(details["terminal_e_error"])),
                duration_limit - float(details["max_burn_duration_min"]),
            ]
            values.extend(_post_a_chain_constraints(config, apsis_pattern, [float(v) for v in x_values]))
            return values

        result = minimize(
            objective,
            np.asarray(seed, dtype=float),
            method="SLSQP",
            bounds=bounds,
            constraints=[{"type": "ineq", "fun": hard_constraints}],
            options={
                "maxiter": int(config["optimizer"]["slsqp_maxiter"]),
                "ftol": 1.0e-7,
                "disp": False,
            },
        )
        total_nfev += int(getattr(result, "nfev", 0) or 0)
        score, signed_error, _details, burns = evaluate(result.x)
        if not burns:
            continue
        refined = {
            "q_sequence": list(candidate["q_sequence"]),
            "delta_vs": [burn.delta_v_mps for burn in burns],
            "alpha_values": [burn.alpha_deg for burn in burns],
            "error_deg": signed_error,
            "score": score,
            "burns": burns,
            "method": "SLSQP",
            "slsqp_success": bool(getattr(result, "success", False)),
            "slsqp_message": str(getattr(result, "message", "")),
            "slsqp_nfev": total_nfev,
        }
        if best is None or refined["score"] < best["score"]:
            best = refined
    if best is not None:
        best["slsqp_nfev"] = total_nfev
    return best


def _continuous_x_from_burns(
    config: dict[str, Any],
    apsis_pattern: list[str],
    burns: list[DesignManeuverBurn],
) -> list[float] | None:
    front_count = max(0, len(apsis_pattern) - 2)
    alpha_count = max(0, len(apsis_pattern) - 1)
    if len(burns) < len(apsis_pattern) or front_count <= 0 or alpha_count <= 0:
        return None
    post_a = [float(burns[index].post_a_km) for index in range(front_count)]
    alpha = [float(burns[index].alpha_deg) for index in range(alpha_count)]
    return post_a + alpha


def _continuous_unpack(
    config: dict[str, Any],
    apsis_pattern: list[str],
    x_values: list[float],
) -> tuple[list[float | None], list[float]]:
    front_count = max(0, len(apsis_pattern) - 2)
    post_a_front = [float(value) for value in x_values[:front_count]]
    supersync = config["supersynchronous_transfer"]
    target_post_a_values: list[float | None] = post_a_front + [
        float(supersync["a_tail_apogee_plus_fixed_km"]),
        float(supersync["a_tail_perigee_plus_fixed_km"]),
    ]
    alpha_values = [float(value) for value in x_values[front_count:]] + [0.0]
    return target_post_a_values[: len(apsis_pattern)], alpha_values[: len(apsis_pattern)]


def _continuous_bounds(
    config: dict[str, Any],
    orbit_type: str,
    apsis_pattern: list[str],
    x0: list[float],
) -> list[tuple[float, float]]:
    front_count = max(0, len(apsis_pattern) - 2)
    initial_a = float(config["initial"]["a_km"])
    tail_a = float(config["supersynchronous_transfer"]["a_tail_apogee_plus_fixed_km"])
    manual_first_control = _first_post_a_control_km(config)
    bounds: list[tuple[float, float]] = []
    for index in range(front_count):
        current = float(x0[index])
        if index == 0 and manual_first_control is not None:
            bounds.append((current, current))
            continue
        low = max(initial_a + 1.0, current - 15000.0)
        high = min(tail_a - 1.0, current + 15000.0)
        low = min(low, current)
        high = max(high, current)
        bounds.append((low, high))
    alpha_bounds = _alpha_search_bounds(config, orbit_type, apsis_pattern)
    for index in range(max(0, len(apsis_pattern) - 1)):
        bounds.append(alpha_bounds[index])
    return bounds


def _continuous_seeds(config: dict[str, Any], apsis_pattern: list[str], x0: list[float]) -> list[list[float]]:
    front_count = max(0, len(apsis_pattern) - 2)
    alpha_count = max(0, len(apsis_pattern) - 1)
    seeds = [list(x0)]
    if front_count <= 0:
        return seeds
    initial_a = float(config["initial"]["a_km"])
    tail_a = float(config["supersynchronous_transfer"]["a_tail_apogee_plus_fixed_km"])
    linear = [initial_a + (tail_a - initial_a) * (index + 1) / (front_count + 1) for index in range(front_count)]
    alpha_base = list(x0[front_count : front_count + alpha_count])
    seeds.append(linear + alpha_base)
    small_alpha = [min(20.0, max(-20.0, value * 0.5)) for value in alpha_base]
    seeds.append(linear + small_alpha)
    return seeds


def _post_a_chain_constraints(config: dict[str, Any], apsis_pattern: list[str], x_values: list[float]) -> list[float]:
    front_count = max(0, len(apsis_pattern) - 2)
    if front_count <= 0:
        return []
    values: list[float] = []
    initial_a = float(config["initial"]["a_km"])
    tail_a = float(config["supersynchronous_transfer"]["a_tail_apogee_plus_fixed_km"])
    post_a = [float(value) for value in x_values[:front_count]]
    values.append(post_a[0] - initial_a - 1.0)
    for previous, current in zip(post_a, post_a[1:]):
        values.append(current - previous - 1.0)
    values.append(tail_a - post_a[-1] - 1.0)
    return values


def _scalar_phase_cost(config: dict[str, Any], details: dict[str, float | bool]) -> float:
    optimizer = config["optimizer"]
    invalid_penalty = 1.0e12 if bool(details.get("invalid", False)) else 0.0
    def finite(value: object, fallback: float = 1.0e6) -> float:
        number = float(value)
        return number if math.isfinite(number) else fallback

    return (
        invalid_penalty
        + finite(details["total_propellant_kg"])
        + float(optimizer["longitude_weight"]) * finite(details["terminal_lon_excess"]) ** 2
        + float(optimizer["inclination_weight"]) * finite(details["terminal_i_excess"]) ** 2
        + float(optimizer["semi_major_axis_weight"]) * finite(details["terminal_a_excess"]) ** 2
        + float(optimizer["eccentricity_weight"]) * finite(details["terminal_e_excess"]) ** 2
        + float(optimizer["duration_weight"]) * finite(details["max_burn_duration_excess_min"]) ** 2
    )


def _coordinate_search_phase_controls(
    config: dict[str, Any],
    *,
    orbit_type: str,
    apsis_pattern: list[str],
    delta_vs: list[float | None],
    alpha_values: list[float],
    q_sequence: list[int],
    seed_front: list[float],
    search_alpha: bool,
    target_post_a_values: list[float | None] | None = None,
) -> tuple[list[float | None], list[float], float, tuple[float, ...], list[DesignManeuverBurn]]:
    front_count = len(seed_front)
    current = [float(value) for value in seed_front]
    current_alpha = list(alpha_values)
    candidate_delta_vs: list[float | None] = (
        [None] * len(delta_vs) if target_post_a_values is not None else current + list(delta_vs[front_count:])
    )
    best_score, best_error, best_burns = _phase_score(
        config,
        apsis_pattern,
        candidate_delta_vs,
        current_alpha,
        q_sequence,
        target_post_a_values=target_post_a_values,
    )
    min_dv = float(config["distribution"]["dv_min_per_burn_mps"])
    max_dv = max(700.0, max(current, default=0.0) + 100.0)
    if target_post_a_values is not None:
        search_indices = range(0)
    else:
        search_indices = range(1, front_count) if _first_post_a_control_km(config) is not None else range(front_count)
    alpha_bounds = _alpha_search_bounds(config, orbit_type, apsis_pattern)
    eval_count = 1
    eval_cap = 80 if search_alpha else 25
    eval_limit = min(eval_cap, max(1, int(config["optimizer"]["maxfev"])))
    for step in (80.0, 40.0, 20.0, 10.0, 5.0, 2.0, 1.0, 0.5):
        improved = True
        while improved and eval_count < eval_limit:
            improved = False
            for index in search_indices:
                for sign in (1.0, -1.0):
                    trial = list(current)
                    trial[index] += sign * step
                    if trial[index] < min_dv or trial[index] > max_dv:
                        continue
                    trial_delta_vs: list[float | None] = trial + list(delta_vs[front_count:])
                    score, trial_error, _trial_burns = _phase_score(
                        config, apsis_pattern, trial_delta_vs, current_alpha, q_sequence
                    )
                    eval_count += 1
                    if score < best_score:
                        current = trial
                        best_score = score
                        best_error = trial_error
                        best_burns = _trial_burns
                        candidate_delta_vs = trial_delta_vs
                        improved = True
                        break
                if improved:
                    break
            if improved or not search_alpha:
                continue
            alpha_step = max(0.1, min(10.0, step))
            for index, bounds in enumerate(alpha_bounds):
                for sign in (1.0, -1.0):
                    trial_alpha = list(current_alpha)
                    trial_alpha[index] = max(bounds[0], min(bounds[1], trial_alpha[index] + sign * alpha_step))
                    if trial_alpha[index] == current_alpha[index]:
                        continue
                    score, trial_error, _trial_burns = _phase_score(
                        config,
                        apsis_pattern,
                        candidate_delta_vs,
                        trial_alpha,
                        q_sequence,
                        target_post_a_values=target_post_a_values,
                    )
                    eval_count += 1
                    if score < best_score:
                        current_alpha = trial_alpha
                        best_score = score
                        best_error = trial_error
                        best_burns = _trial_burns
                        improved = True
                        break
                if improved or eval_count >= eval_limit:
                    break
    return candidate_delta_vs, current_alpha, best_error, best_score, best_burns


def _inclination_weighted_alpha_seed(
    config: dict[str, Any],
    apsis_pattern: list[str],
    burns: list[DesignManeuverBurn],
    fallback_alpha_values: list[float],
) -> list[float]:
    values = list(fallback_alpha_values)
    if not burns or len(values) < len(apsis_pattern):
        return values
    initial_i = float(config["initial"]["i_deg"])
    target_i = float(config["target"]["i_deg"])
    direction = 1.0 if initial_i >= target_i else -1.0
    active = [
        index
        for index, apsis in enumerate(apsis_pattern)
        if apsis == "A" and index < len(burns) and not (index == len(apsis_pattern) - 1)
    ]
    if not active:
        return values
    weights = [abs(burns[index].semi_major_axis_control_km) for index in active]
    total = sum(weights)
    if total <= 1.0e-9:
        weights = [1.0] * len(active)
        total = float(len(active))
    for index, weight in zip(active, weights):
        fraction = weight / total
        magnitude = min(40.0, max(2.0, 40.0 * fraction))
        values[index] = direction * magnitude
    return values


def _alpha_search_bounds(
    config: dict[str, Any],
    orbit_type: str,
    apsis_pattern: list[str],
) -> list[tuple[float, float]]:
    alpha_cfg = config["alpha"]
    initial_i = float(config["initial"]["i_deg"])
    target_i = float(config["target"]["i_deg"])
    bounds: list[tuple[float, float]] = []
    for index, apsis in enumerate(apsis_pattern):
        if orbit_type == "standard_transfer":
            raw_bounds = alpha_cfg["standard_bounds_deg"]
        elif index == len(apsis_pattern) - 1 and apsis == "P":
            raw_bounds = alpha_cfg["tail_perigee_bounds_deg"]
        elif index >= max(0, len(apsis_pattern) - 2):
            raw_bounds = alpha_cfg["tail_apogee_bounds_deg"]
        else:
            raw_bounds = alpha_cfg["front_bounds_deg"]
        low, high = float(raw_bounds[0]), float(raw_bounds[1])
        low, high = min(low, high), max(low, high)
        if apsis == "A":
            if initial_i > target_i:
                low = max(0.0, low)
            elif initial_i < target_i:
                high = min(0.0, high)
            if low > high:
                low = high = 0.0
        bounds.append((low, high))
    return bounds


def _first_post_a_control_km(config: dict[str, Any]) -> float | None:
    return _optional_float(config.get("distribution", {}).get("first_post_a_control_km"))


def _build_phase_diagnostics(
    config: dict[str, Any],
    candidates: list[dict[str, Any]],
    *,
    optimizer_method: str,
) -> dict[str, Any]:
    feasible = 0
    top_candidates: list[dict[str, Any]] = []
    for item in candidates:
        burns = item.get("burns", [])
        score, _error, details = _phase_score_from_burns(config, burns, [])
        is_feasible = (
            float(item.get("score", score)[0]) <= 0.0
            and not bool(details["invalid"])
            and float(details["terminal_lon_excess"]) <= 0.0
            and float(details["terminal_i_excess"]) <= 0.0
            and float(details["terminal_a_excess"]) <= 0.0
            and float(details["terminal_e_excess"]) <= 0.0
            and float(details["max_burn_duration_excess_min"]) <= 0.0
        )
        feasible += 1 if is_feasible else 0
        if len(top_candidates) < 5:
            top_candidates.append(
                {
                    "q_sequence": list(item.get("q_sequence", [])),
                    "method": str(item.get("method", "")),
                    "score": list(score),
                    "propellant_kg": float(details["total_propellant_kg"]),
                    "lon_error_deg": float(details["terminal_lon_error_deg"]),
                    "i_error_deg": float(details["terminal_i_error_deg"]),
                    "a_error_km": float(details["terminal_a_error_km"]),
                    "e_error": float(details["terminal_e_error"]),
                    "max_burn_duration_min": float(details["max_burn_duration_min"]),
                }
            )
    best = candidates[0] if candidates else {}
    best_burns = best.get("burns", [])
    _score, _error, best_details = _phase_score_from_burns(config, best_burns, [])
    tolerance = config["terminal_tolerance"]
    duration_limit = float(config["burn_limit"]["max_total_burn_time_min"])
    return {
        "q_total_candidates": len(candidates),
        "q_tested_fast": len(candidates),
        "feasible_solutions": feasible,
        "best_q_sequence": list(best.get("q_sequence", [])),
        "optimizer_method": optimizer_method,
        "optimizer_converged": str(best.get("method", "")) == "SLSQP" and bool(best.get("slsqp_success", False)),
        "active_constraints": _active_phase_constraints(config, best_details),
        "alpha_at_bounds": _detect_alpha_at_bounds(config, best_burns),
        "terminal_error_margins": {
            "lon_deg": float(tolerance["lon_deg"]) - abs(float(best_details["terminal_lon_error_deg"])),
            "i_deg": float(tolerance["i_deg"]) - abs(float(best_details["terminal_i_error_deg"])),
            "a_km": float(tolerance["a_km"]) - abs(float(best_details["terminal_a_error_km"])),
            "e": float(tolerance["e"]) - abs(float(best_details["terminal_e_error"])),
        },
        "max_burn_duration_margin_min": duration_limit - float(best_details["max_burn_duration_min"]),
        "top_candidates": top_candidates,
    }


def _active_phase_constraints(config: dict[str, Any], details: dict[str, float | bool]) -> list[str]:
    tolerance = config["terminal_tolerance"]
    duration_limit = float(config["burn_limit"]["max_total_burn_time_min"])
    active: list[str] = []
    if abs(float(details["terminal_lon_error_deg"])) >= 0.8 * float(tolerance["lon_deg"]):
        active.append("terminal_lon")
    if abs(float(details["terminal_i_error_deg"])) >= 0.8 * float(tolerance["i_deg"]):
        active.append("terminal_i")
    if abs(float(details["terminal_a_error_km"])) >= 0.8 * float(tolerance["a_km"]):
        active.append("terminal_a")
    if abs(float(details["terminal_e_error"])) >= 0.8 * float(tolerance["e"]):
        active.append("terminal_e")
    if duration_limit - float(details["max_burn_duration_min"]) <= 1.0:
        active.append("burn_duration")
    return active


def _detect_alpha_at_bounds(config: dict[str, Any], burns: list[DesignManeuverBurn]) -> list[dict[str, float | int]]:
    if not burns:
        return []
    orbit_type = _classify_orbit(
        config,
        float(config["initial"]["a_km"]) * (1.0 + float(config["initial"]["e"])),
        float(config["target"]["a_km"]),
    )
    bounds = _alpha_search_bounds(config, orbit_type, [burn.apsis for burn in burns])
    hits: list[dict[str, float | int]] = []
    for index, burn in enumerate(burns):
        low, high = bounds[index]
        if abs(burn.alpha_deg - low) <= 1.0e-6 or abs(burn.alpha_deg - high) <= 1.0e-6:
            hits.append({"index": index + 1, "alpha_deg": burn.alpha_deg, "low": low, "high": high})
    return hits


def _phase_score(
    config: dict[str, Any],
    apsis_pattern: list[str],
    delta_vs: list[float | None],
    alpha_values: list[float],
    q_sequence: list[int],
    target_post_a_values: list[float | None] | None = None,
) -> tuple[tuple[float, ...], float, list[DesignManeuverBurn]]:
    warnings: list[str] = []
    burns = _build_burns(
        config,
        apsis_pattern=apsis_pattern,
        delta_vs=delta_vs,
        alpha_values=alpha_values,
        warnings=warnings,
        q_sequence_override=q_sequence,
        target_post_a_values=target_post_a_values,
    )
    score, signed_error, _details = _phase_score_from_burns(config, burns, warnings)
    return score, signed_error, burns


def _phase_score_from_burns(
    config: dict[str, Any],
    burns: list[DesignManeuverBurn],
    warnings: list[str],
) -> tuple[tuple[float, ...], float, dict[str, float | bool]]:
    if not burns:
        details: dict[str, float | bool] = {
            "invalid": True,
            "terminal_lon_error_deg": float("inf"),
            "terminal_i_error_deg": float("inf"),
            "terminal_a_error_km": float("inf"),
            "terminal_e_error": float("inf"),
            "terminal_lon_excess": float("inf"),
            "terminal_i_excess": float("inf"),
            "terminal_a_excess": float("inf"),
            "terminal_e_excess": float("inf"),
            "total_propellant_kg": float("inf"),
            "max_burn_duration_min": float("inf"),
            "max_burn_duration_excess_min": float("inf"),
            "uniform_spread_mps": float("inf"),
            "duration_penalty": float("inf"),
            "warning_penalty": float("inf"),
        }
        return (1.0, float("inf"), float("inf"), float("inf"), float("inf"), float("inf"), float("inf"), float("inf"), float("inf"), float("inf")), float("inf"), details
    signed_lon_error = _wrap180(burns[-1].longitude_deg_e - float(config["target"]["lon_degE"]))
    lon_error = abs(signed_lon_error)
    target = config["target"]
    tolerance = config["terminal_tolerance"]
    terminal_i_signed = burns[-1].post_i_deg - float(target["i_deg"])
    terminal_a_signed = burns[-1].post_a_km - float(target["a_km"])
    terminal_e_signed = burns[-1].post_e - float(target["e"])
    terminal_i_error = abs(terminal_i_signed)
    terminal_a_error = abs(terminal_a_signed)
    terminal_e_error = abs(terminal_e_signed)
    terminal_lon_excess = max(0.0, lon_error - float(tolerance["lon_deg"]))
    terminal_i_excess = max(0.0, terminal_i_error - float(tolerance["i_deg"]))
    terminal_a_excess = max(0.0, terminal_a_error - float(tolerance["a_km"]))
    terminal_e_excess = max(0.0, terminal_e_error - float(tolerance["e"]))
    max_duration = max(burn.total_burn_time_min for burn in burns)
    duration_limit = float(config["burn_limit"]["max_total_burn_time_min"])
    duration_excess = max(0.0, max_duration - duration_limit)
    duration_penalty = duration_excess * 1000.0
    warning_penalty = 1000.0 if warnings else 0.0
    invalid = bool(warnings) or duration_excess > 0.0 or not all(
        math.isfinite(value)
        for burn in burns
        for value in (
            burn.delta_v_mps,
            burn.alpha_deg,
            burn.post_a_km,
            burn.post_e,
            burn.post_i_deg,
            burn.total_burn_time_min,
            burn.propellant_kg,
        )
    )
    propellant = sum(burn.propellant_kg for burn in burns)
    spread = _uniform_spread([burn.delta_v_mps for burn in burns if burn.burn_type != "tail_fixed"])
    details = {
        "invalid": invalid,
        "terminal_lon_error_deg": signed_lon_error,
        "terminal_i_error_deg": terminal_i_signed,
        "terminal_a_error_km": terminal_a_signed,
        "terminal_e_error": terminal_e_signed,
        "terminal_lon_excess": terminal_lon_excess,
        "terminal_i_excess": terminal_i_excess,
        "terminal_a_excess": terminal_a_excess,
        "terminal_e_excess": terminal_e_excess,
        "total_propellant_kg": propellant,
        "max_burn_duration_min": max_duration,
        "max_burn_duration_excess_min": duration_excess,
        "uniform_spread_mps": spread,
        "duration_penalty": duration_penalty,
        "warning_penalty": warning_penalty,
    }
    score = (
        1.0 if invalid else 0.0,
        terminal_lon_excess,
        terminal_i_excess,
        terminal_a_excess,
        terminal_e_excess,
        duration_excess,
        propellant,
        lon_error,
        max_duration,
        spread,
    )
    return score, signed_lon_error, details


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
    rx, ry, rz = float(r[0]), float(r[1]), float(r[2])
    vx, vy, vz = float(v[0]), float(v[1]), float(v[2])
    radius = math.sqrt(rx * rx + ry * ry + rz * rz)
    speed_sq = vx * vx + vy * vy + vz * vz
    speed = math.sqrt(speed_sq)
    hx = ry * vz - rz * vy
    hy = rz * vx - rx * vz
    hz = rx * vy - ry * vx
    h_norm = math.sqrt(hx * hx + hy * hy + hz * hz)
    nx, ny = -hy, hx
    n_norm = math.hypot(nx, ny)
    evx = (vy * hz - vz * hy) / mu - rx / radius
    evy = (vz * hx - vx * hz) / mu - ry / radius
    evz = (vx * hy - vy * hx) / mu - rz / radius
    e = math.sqrt(evx * evx + evy * evy + evz * evz)
    energy = speed * speed / 2.0 - mu / radius
    a = -mu / (2.0 * energy)
    inc = math.acos(max(-1.0, min(1.0, hz / h_norm)))
    raan = math.atan2(ny, nx) if n_norm > 1.0e-12 else 0.0
    if e > 1.0e-10 and n_norm > 1.0e-12:
        n_cross_e_x = ny * evz
        n_cross_e_y = -nx * evz
        n_cross_e_z = nx * evy - ny * evx
        e_cross_r_x = evy * rz - evz * ry
        e_cross_r_y = evz * rx - evx * rz
        e_cross_r_z = evx * ry - evy * rx
        argp = math.atan2(
            (n_cross_e_x * hx + n_cross_e_y * hy + n_cross_e_z * hz) / (h_norm * n_norm * e),
            (nx * evx + ny * evy) / (n_norm * e),
        )
        true_anomaly = math.atan2(
            (e_cross_r_x * hx + e_cross_r_y * hy + e_cross_r_z * hz) / (h_norm * e * radius),
            (evx * rx + evy * ry + evz * rz) / (e * radius),
        )
    else:
        argp = 0.0
        if n_norm > 1.0e-12:
            n_hat_x, n_hat_y = nx / n_norm, ny / n_norm
            hx_hat, hy_hat, hz_hat = hx / h_norm, hy / h_norm, hz / h_norm
            qx = -hz_hat * n_hat_y
            qy = hz_hat * n_hat_x
            qz = hx_hat * n_hat_y - hy_hat * n_hat_x
            true_anomaly = math.atan2(rx * qx + ry * qy + rz * qz, rx * n_hat_x + ry * n_hat_y)
        else:
            true_anomaly = math.atan2(ry, rx)
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


def _propagate_state_to_elapsed(
    config: dict[str, Any],
    r: np.ndarray,
    v: np.ndarray,
    from_elapsed_s: float,
    to_elapsed_s: float,
) -> tuple[np.ndarray, np.ndarray]:
    dt = float(to_elapsed_s) - float(from_elapsed_s)
    if abs(dt) <= 1.0e-9:
        return np.asarray(r, dtype=float).copy(), np.asarray(v, dtype=float).copy()
    earth = config["earth"]
    mu = float(earth["mu_km3_s2"])
    a, e, inc, raan, argp, mean_anomaly, _true_anomaly = _rv_to_coe(np.asarray(r, dtype=float), np.asarray(v, dtype=float), mu=mu)
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
    return _coe_to_rv(
        a,
        e,
        inc,
        (raan + raan_dot * dt) % (2.0 * math.pi),
        (argp + argp_dot * dt) % (2.0 * math.pi),
        (mean_anomaly + mean_dot * dt) % (2.0 * math.pi),
        mu=mu,
    )


def _longitude_deg(config: dict[str, Any], r: np.ndarray, elapsed_s: float) -> float:
    t0 = parse_utc(str(config["initial"]["t0_epoch"]))
    theta0 = greenwich_angle_at_utc(t0)
    theta = theta0 + float(config["earth"]["omega_e_rad_s"]) * elapsed_s
    x, y, _z = (float(value) for value in r)
    x_ecef = math.cos(theta) * x + math.sin(theta) * y
    y_ecef = -math.sin(theta) * x + math.cos(theta) * y
    return math.degrees(math.atan2(y_ecef, x_ecef)) % 360.0


def _local_horizontal_direction(r: np.ndarray, alpha_deg: float) -> np.ndarray:
    return local_horizontal_yaw_direction(r, alpha_deg)


def _local_horizontal_basis(r: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    r_hat = r / np.linalg.norm(r)
    k_hat = np.asarray([0.0, 0.0, 1.0], dtype=float)
    east = np.cross(k_hat, r_hat)
    east_norm = float(np.linalg.norm(east))
    if east_norm <= 1.0e-12:
        east = np.asarray([0.0, 1.0, 0.0], dtype=float)
    else:
        east = east / east_norm
    north = k_hat - np.dot(k_hat, r_hat) * r_hat
    north_norm = float(np.linalg.norm(north))
    if north_norm <= 1.0e-12:
        north = np.cross(r_hat, east)
        north = north / np.linalg.norm(north)
    else:
        north = north / north_norm
    south = -north
    return east, north, south


def _alpha_from_local_horizontal_vector(r: np.ndarray, vector: np.ndarray) -> float:
    r_hat = r / np.linalg.norm(r)
    east, _north, south = _local_horizontal_basis(r)
    horizontal = vector - np.dot(vector, r_hat) * r_hat
    norm = float(np.linalg.norm(horizontal))
    if norm <= 1.0e-12:
        horizontal = vector
        norm = float(np.linalg.norm(horizontal))
    if norm <= 1.0e-12:
        return 0.0
    horizontal = horizontal / norm
    return math.degrees(math.atan2(float(np.dot(horizontal, south)), float(np.dot(horizontal, east))))


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
    target_post_a_values: list[float | None] | None = None,
) -> list[DesignManeuverBurn]:
    initial = config["initial"]
    longitude_cfg = config["longitude"]
    apsis_cfg = config["apsis"]
    supersync = config["supersynchronous_transfer"]
    t0 = parse_utc(str(initial["t0_epoch"]))
    mass = float(initial["m0_kg"])
    planning_window = longitude_cfg["planning_window_degE"]
    raw_window = longitude_cfg["raw_window_degE"]
    manual_first_control_km = _first_post_a_control_km(config)
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
    flight_revolution = 2
    pre_a: float | None = None

    for index, apsis in enumerate(apsis_pattern):
        longitude_ok = _in_window(longitude, planning_window)
        if not longitude_ok and _in_window(longitude, raw_window):
            warnings.append(f"第 {index + 1} 次点火经度只满足原始窗口，未满足规划收缩窗口。")
        elif not longitude_ok:
            warnings.append(f"第 {index + 1} 次点火经度未满足规划窗口。")

        if pre_a is None:
            pre_a, *_ = _rv_to_coe(r, v)
        target_post_a = None
        burn_type = "normal"
        fixed_tail = (
            bool(supersync["tail_fixed_enabled"])
            and len(apsis_pattern) >= 2
            and index >= len(apsis_pattern) - 2
            and apsis_pattern[-1] == "P"
        )
        if target_post_a_values is not None and index < len(target_post_a_values):
            target_post_a = target_post_a_values[index]
            if index < max(0, len(apsis_pattern) - 2):
                burn_type = "front"
            elif fixed_tail:
                burn_type = "tail_fixed"
        elif fixed_tail:
            burn_type = "tail_fixed"
            target_post_a = (
                float(supersync["a_tail_apogee_plus_fixed_km"])
                if index == len(apsis_pattern) - 2
                else float(supersync["a_tail_perigee_plus_fixed_km"])
            )
        elif index == 0 and manual_first_control_km is not None:
            target_post_a = pre_a + manual_first_control_km
        elif index < max(0, len(apsis_pattern) - 2):
            burn_type = "front"

        alpha_deg = float(alpha_values[index])
        if index == len(apsis_pattern) - 1 and apsis == "P":
            alpha_deg = _alpha_from_local_horizontal_vector(r, -v)
        solve_target_a = target_post_a is not None
        if solve_target_a:
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
        v = v + (dv_mps / 1000.0) * _local_horizontal_direction(r, alpha_deg)
        current_a, current_e, current_i_rad, *_ = _rv_to_coe(r, v)
        current_i = math.degrees(current_i_rad)
        if index == len(apsis_pattern) - 1 and apsis != "P":
            inclination_trim = _terminal_inclination_trim_delta_v_mps(config, v, current_i)
            if inclination_trim > 0.0:
                dv_mps += inclination_trim
                burn_time = _burn_time_for_delta_v(config, mass, dv_mps)
                mass_after = max(1.0, mass - burn_time["propellant_kg"])
                current_i = float(config["target"]["i_deg"])
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
                flight_revolution=flight_revolution,
                position_label="远地点" if apsis == "A" else "近地点",
                orbit_period_min=_orbit_period_min(config, current_a),
                post_mass_kg=mass_after,
                semi_major_axis_control_km=current_a - pre_a,
            )
        )
        mass = mass_after
        pre_a = current_a
        if index < len(apsis_pattern) - 1:
            next_apsis_name = apsis_pattern[index + 1]
            q = q_sequence[index] if index < len(q_sequence) else int(apsis_cfg["q_AA_default"])
            target_longitude = None
            if apsis == "A" and next_apsis_name == "P":
                q = 1
            if index == len(apsis_pattern) - 2:
                target_longitude = float(config["target"]["lon_degE"])
            next_flight_revolution = flight_revolution + q
            elapsed_s, r, v, longitude = _find_next_burn_event(
                config,
                r,
                v,
                elapsed_s,
                next_apsis_name,
                q,
                target_longitude_deg_e=target_longitude,
            )
            flight_revolution = next_flight_revolution
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


def _propellant_for_delta_v(mass_kg: float, dv_mps: float, isp_s: float) -> float:
    if dv_mps <= 0.0:
        return 0.0
    c_eff = max(1.0, float(isp_s)) * G0_M_S2
    return max(0.0, float(mass_kg) * (1.0 - math.exp(-float(dv_mps) / c_eff)))


def _orbit_period_min(config: dict[str, Any], a_km: float) -> float:
    mu = float(config["earth"]["mu_km3_s2"])
    return 2.0 * math.pi * math.sqrt(max(1.0, a_km**3 / mu)) / 60.0


def _terminal_inclination_trim_delta_v_mps(config: dict[str, Any], v: np.ndarray, current_i_deg: float) -> float:
    return _inclination_trim_delta_v_mps(
        v,
        current_i_deg,
        float(config["target"]["i_deg"]),
        float(config["terminal_tolerance"]["i_deg"]),
    )


def _inclination_trim_delta_v_mps(v: np.ndarray, current_i_deg: float, target_i_deg: float, tolerance_deg: float) -> float:
    error_deg = float(current_i_deg) - float(target_i_deg)
    if abs(error_deg) <= float(tolerance_deg):
        return 0.0
    speed_mps = float(np.linalg.norm(v)) * 1000.0
    return 2.0 * speed_mps * math.sin(math.radians(abs(error_deg)) / 2.0)


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
