from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import math
from typing import Any

from smart.domain.models import OrbitalElements
from smart.services.earth_orientation import format_utc, greenwich_angle_at_utc, parse_utc, utc_now_iso_z

BEIJING_OFFSET = timedelta(hours=8)
G0_M_S2 = 9.80665


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


def default_design_maneuver_strategy_payload() -> dict[str, Any]:
    return {
        "planner": {
            "version": "V4.2_simplified_transfer_type",
            "auto_recommend_count": True,
            "maneuver_count_user": 0,
            "force_user_count": True,
        },
        "initial": {
            "t0_epoch": utc_now_iso_z(),
            "m0_kg": 5200.0,
            "state_input_type": "keplerian",
            "a_km": 29478.137,
            "e": 0.7768460924,
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
            "engineering_min_count": 1,
            "total_dv_est_user_mps": 0.0,
        },
        "distribution": {
            "mode": "auto",
            "max_uniform_dv_spread_mps": 70.0,
            "dv_min_per_burn_mps": 20.0,
            "front_dv_total_user_mps": 0.0,
            "standard_terminal_reserve_mps": 0.0,
            "allow_small_dv_correction": True,
            "small_dv_correction_bound_mps": 25.0,
        },
        "supersynchronous_transfer": {
            "strategy": "n_apogee_plus_1_perigee",
            "tail_fixed_enabled": True,
            "tail_fixed_count": 2,
            "tail_control_mode": "fixed_post_a",
            "a_tail_apogee_plus_fixed_km": 47271.168509,
            "a_tail_perigee_plus_fixed_km": 42164.2,
            "dv_tail_apogee_fixed_mps": 0.0,
            "dv_tail_perigee_fixed_mps": 0.0,
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
        },
        "alpha": {
            "optimize_alpha": False,
            "alpha_default_deg": 10.0,
            "front_bounds_deg": [-20.0, 40.0],
            "tail_apogee_bounds_deg": [-20.0, 40.0],
            "tail_perigee_bounds_deg": [-180.0, 180.0],
            "smooth_alpha_weight": 0.01,
        },
        "terminal_tolerance": {
            "a_km": 1.0,
            "e": 1.0e-4,
            "i_deg": 0.01,
            "lon_deg": 0.05,
        },
    }


def normalize_design_maneuver_strategy_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    defaults = default_design_maneuver_strategy_payload()
    source = payload if isinstance(payload, dict) else {}
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
            "standard_terminal_reserve_mps",
            "small_dv_correction_bound_mps",
        ),
        "supersynchronous_transfer": (
            "a_tail_apogee_plus_fixed_km",
            "a_tail_perigee_plus_fixed_km",
            "dv_tail_apogee_fixed_mps",
            "dv_tail_perigee_fixed_mps",
        ),
        "standard_transfer": ("terminal_reserve_mps",),
        "alpha": ("alpha_default_deg", "smooth_alpha_weight"),
        "terminal_tolerance": ("a_km", "e", "i_deg", "lon_deg"),
    }.items():
        for key in keys:
            result[section][key] = float(result[section].get(key, defaults[section][key]))

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
    result["maneuver_count"]["min"] = max(1, int(result["maneuver_count"].get("min", 1)))
    result["maneuver_count"]["max"] = max(result["maneuver_count"]["min"], int(result["maneuver_count"].get("max", 10)))
    result["maneuver_count"]["user"] = max(0, int(result["maneuver_count"].get("user", 0)))
    result["maneuver_count"]["engineering_min_count"] = max(
        1,
        int(result["maneuver_count"].get("engineering_min_count", 1)),
    )
    if result["planner"]["maneuver_count_user"] > 0:
        result["maneuver_count"]["user"] = result["planner"]["maneuver_count_user"]
    else:
        result["planner"]["maneuver_count_user"] = result["maneuver_count"]["user"]
    result["apsis"]["q_AA_default"] = max(1, int(result["apsis"].get("q_AA_default", 3)))
    result["apsis"]["search_revolutions_max"] = max(1, int(result["apsis"].get("search_revolutions_max", 40)))

    for section, key in (
        ("longitude", "raw_window_degE"),
        ("longitude", "planning_window_degE"),
        ("longitude", "finite_margin_window_degE"),
        ("alpha", "front_bounds_deg"),
        ("alpha", "tail_apogee_bounds_deg"),
        ("alpha", "tail_perigee_bounds_deg"),
    ):
        result[section][key] = _number_pair(result[section].get(key), defaults[section][key])

    q_user = result["apsis"].get("q_sequence_user", [])
    result["apsis"]["q_sequence_user"] = [max(1, int(value)) for value in q_user] if isinstance(q_user, list) else []
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
    burns = _build_burns(
        config,
        apsis_pattern=apsis_pattern,
        delta_vs=delta_vs,
        alpha_values=alpha_values,
        warnings=warnings,
    )
    checks = _build_checks(config, burns)
    duration_limit = float(burn_limit["max_total_burn_time_min"])
    duration_ok = all(burn.total_burn_time_min <= duration_limit + 1.0e-9 for burn in burns)
    longitude_ok = all(burn.longitude_ok for burn in burns)
    uniform_spread = _uniform_spread(delta_vs)
    uniform_ok = uniform_spread <= float(distribution["max_uniform_dv_spread_mps"]) + 1.0e-9
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
    if float(supersync.get("dv_tail_apogee_fixed_mps", 0.0)) > 0.0 or float(
        supersync.get("dv_tail_perigee_fixed_mps", 0.0)
    ) > 0.0:
        return (
            max(0.0, float(supersync.get("dv_tail_apogee_fixed_mps", 0.0))),
            max(0.0, float(supersync.get("dv_tail_perigee_fixed_mps", 0.0))),
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
    else:
        n_geom_min = 1
    value = max(n_raw, n_geom_min, int(count_cfg["engineering_min_count"]))
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
) -> list[float]:
    distribution = config["distribution"]
    supersync = config["supersynchronous_transfer"]
    if orbit_type == "supersynchronous_transfer" and bool(supersync["tail_fixed_enabled"]) and count >= 2:
        front_count = max(0, count - 2)
        front_total = float(distribution["front_dv_total_user_mps"])
        if front_total <= 0.0:
            front_total = max(0.0, total_dv - tail_apogee - tail_perigee)
        front = [front_total / front_count] * front_count if front_count else []
        return front + [tail_apogee, tail_perigee]
    reserve = float(distribution["standard_terminal_reserve_mps"])
    if orbit_type == "standard_transfer":
        reserve = max(reserve, float(config["standard_transfer"]["terminal_reserve_mps"]))
    if reserve > 0.0 and count > 1:
        return [(max(0.0, total_dv - reserve) / (count - 1))] * (count - 1) + [reserve]
    return [total_dv / count] * count


def _alpha_values(config: dict[str, Any], orbit_type: str, apsis_pattern: list[str]) -> list[float]:
    alpha_default = float(config["alpha"]["alpha_default_deg"])
    values: list[float] = []
    for index, apsis in enumerate(apsis_pattern):
        if orbit_type == "supersynchronous_transfer" and index == len(apsis_pattern) - 1 and apsis == "P":
            values.append(180.0)
        else:
            values.append(alpha_default)
    return values


def _build_burns(
    config: dict[str, Any],
    *,
    apsis_pattern: list[str],
    delta_vs: list[float],
    alpha_values: list[float],
    warnings: list[str],
) -> list[DesignManeuverBurn]:
    initial = config["initial"]
    earth = config["earth"]
    longitude_cfg = config["longitude"]
    target = config["target"]
    apsis_cfg = config["apsis"]
    supersync = config["supersynchronous_transfer"]
    mu = float(earth["mu_km3_s2"])
    current_a = float(initial["a_km"])
    current_e = float(initial["e"])
    current_i = float(initial["i_deg"])
    t0 = parse_utc(str(initial["t0_epoch"]))
    current_elapsed_s = 0.0
    mass = float(initial["m0_kg"])
    planning_window = longitude_cfg["planning_window_degE"]
    raw_window = longitude_cfg["raw_window_degE"]
    q_default = int(apsis_cfg["q_AA_default"])
    q_user = apsis_cfg["q_sequence_user"]
    search_limit = int(apsis_cfg["search_revolutions_max"])
    burns: list[DesignManeuverBurn] = []

    for index, apsis in enumerate(apsis_pattern):
        q = int(q_user[index - 1]) if index > 0 and index - 1 < len(q_user) else q_default
        if index == 0:
            q = 1
        elapsed_s, longitude = _find_burn_time_and_longitude(
            config,
            apsis,
            current_a,
            current_e,
            start_elapsed_s=current_elapsed_s,
            q=q,
            planning_window=planning_window,
            search_limit=search_limit,
        )
        longitude_ok = _in_window(longitude, planning_window)
        if not longitude_ok and _in_window(longitude, raw_window):
            warnings.append(f"第 {index + 1} 次点火经度只满足原始窗口，未满足规划收缩窗口。")
        elif not longitude_ok:
            warnings.append(f"第 {index + 1} 次点火经度未满足规划窗口。")

        dv_mps = max(0.0, float(delta_vs[index]))
        alpha_deg = float(alpha_values[index])
        burn_time = _burn_time_for_delta_v(config, mass, dv_mps)
        mass_after = max(1.0, mass - burn_time["propellant_kg"])
        target_post_a = None
        burn_type = "normal"
        if (
            str(config["orbit_type"].get("mode", "auto")) in {"auto", "supersynchronous_transfer", "general_transfer"}
            and bool(supersync["tail_fixed_enabled"])
            and len(apsis_pattern) >= 2
            and index >= len(apsis_pattern) - 2
            and apsis_pattern[-1] == "P"
        ):
            burn_type = "tail_fixed"
            target_post_a = (
                float(supersync["a_tail_apogee_plus_fixed_km"])
                if index == len(apsis_pattern) - 2
                else float(supersync["a_tail_perigee_plus_fixed_km"])
            )
        elif index < max(0, len(apsis_pattern) - 2):
            burn_type = "front"

        current_a, current_e, current_i = _post_burn_elements(
            config,
            apsis,
            current_a,
            current_e,
            current_i,
            dv_mps,
            alpha_deg,
            target_post_a=target_post_a,
        )
        timestamp = t0 + timedelta(seconds=elapsed_s)
        beijing_time = (timestamp + BEIJING_OFFSET).strftime("%Y-%m-%d %H:%M:%S")
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
        current_elapsed_s = elapsed_s
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


def _build_checks(config: dict[str, Any], burns: list[DesignManeuverBurn]) -> list[dict[str, Any]]:
    tolerance = config["terminal_tolerance"]
    target = config["target"]
    max_duration = float(config["burn_limit"]["max_total_burn_time_min"])
    max_spread = float(config["distribution"]["max_uniform_dv_spread_mps"])
    spread = _uniform_spread([burn.delta_v_mps for burn in burns])
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
            "requirement": f"<= {max_spread:.1f} m/s",
            "result": f"{spread:.3f} m/s",
            "passed": spread <= max_spread + 1.0e-9,
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
