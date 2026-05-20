from __future__ import annotations

import math
from typing import Any

import numpy as np

from smart.services.design_maneuver_strategy import (
    ContinuousThrustManeuverParameter,
    ContinuousThrustOptimizationResult,
    DesignManeuverResult,
    _append_continuous_orbit_history_row,
    _evaluate_first_continuous_thrust_candidate,
    _integrate_low_thrust_to_target_metric,
    _longitude_deg,
    _next_apsis,
    _propagate_state_to_elapsed,
    _rv_to_coe,
    _initial_state_km,
    _wrap180,
    normalize_design_maneuver_strategy_payload,
)

FALLBACK_APOGEE_Q_SEQUENCE = (3, 3, 3)


def optimize_continuous_thrust_chain_parameters(
    result: DesignManeuverResult,
) -> ContinuousThrustOptimizationResult:
    config = normalize_design_maneuver_strategy_payload(result.config)
    burns = sorted(result.burns, key=lambda item: item.index)
    if len(burns) < 5:
        raise ValueError("连续推力链路优化需要至少 5 次脉冲规划点火。")

    continuous_cfg = config["continuous_thrust_optimizer"]
    time_step_s = max(1.0, float(continuous_cfg["time_step_s"]))
    yaw_step_deg = max(0.001, float(continuous_cfg["yaw_step_deg"]))
    final_integration_step_s = max(0.5, float(continuous_cfg["final_integration_step_s"]))
    sync_a_km = float(config["target"]["a_km"])
    target_lon_deg = float(config["target"]["lon_degE"])
    target_i_deg = float(config["target"]["i_deg"])
    apogee_q_sequence = _apogee_q_sequence(result)

    r, v = _initial_state_km(config)
    elapsed_s = 0.0
    mass_kg = float(config["initial"]["m0_kg"])
    parameters: list[ContinuousThrustManeuverParameter] = []
    history_rows: list[dict[str, Any]] = []

    _append_history(config, history_rows, 0.0, r, v, mass_kg, "coast", True)

    for burn_position in range(4):
        burn = burns[burn_position]
        if burn_position == 0:
            event_s = float(burn.elapsed_min) * 60.0
        else:
            event_s, _event_r, _event_v = _next_apsis(
                config,
                r,
                v,
                elapsed_s,
                "A",
                apogee_q_sequence[burn_position - 1],
            )
        nominal_start_s = event_s - 0.5 * float(burn.total_burn_time_min) * 60.0
        target_a_km = sync_a_km if burn_position == 3 else float(
            burn.target_post_a_km if burn.target_post_a_km is not None else burn.post_a_km
        )
        if burn_position == 3:
            candidate = _optimize_apogee_burn_for_inclination(
                config,
                r,
                v,
                elapsed_s,
                mass_kg,
                burn,
                target_a_km,
                nominal_start_s,
                float(burn.alpha_deg),
                target_i_deg,
                integration_step_s=final_integration_step_s,
            )
        else:
            candidate = _evaluate_apogee_candidate(
                config,
                r,
                v,
                elapsed_s,
                mass_kg,
                burn,
                target_a_km,
                nominal_start_s,
                float(burn.alpha_deg),
                integration_step_s=final_integration_step_s,
            )
        if candidate is None:
            raise ValueError(f"MV{burn_position + 1} 连续推力积分未到达熄火目标。")
        parameters.append(
            _parameter_from_candidate(
                config,
                burn,
                candidate,
                target_a_km,
                nominal_start_s,
                float(burn.alpha_deg),
                mode="固定链路优化",
                objective_formula="m",
            )
        )
        _append_history(
            config,
            history_rows,
            float(candidate["burn_start_s"]),
            candidate["r_cutoff"],
            candidate["v_cutoff"],
            candidate["post_mass_kg"],
            "orbit_control",
            True,
        )
        r = np.asarray(candidate["r_cutoff"], dtype=float)
        v = np.asarray(candidate["v_cutoff"], dtype=float)
        elapsed_s = float(candidate["cutoff_s"])
        mass_kg = float(candidate["post_mass_kg"])

    perigee_s, _perigee_r, _perigee_v = _next_apsis(config, r, v, elapsed_s, "P", 1)
    final_burn = burns[-1]
    nominal_final_start_s = perigee_s - 0.5 * float(final_burn.total_burn_time_min) * 60.0
    final_candidate = _optimize_final_perigee_burn_for_longitude(
        config,
        r,
        v,
        elapsed_s,
        mass_kg,
        final_burn,
        nominal_final_start_s,
        target_lon_deg,
        integration_step_s=final_integration_step_s,
    )
    if final_candidate is None:
        raise ValueError("MV5 连续推力积分未到达半长轴目标。")
    parameters.append(
        _parameter_from_candidate(
            config,
            final_burn,
            final_candidate,
            sync_a_km,
            nominal_final_start_s,
            float(final_burn.alpha_deg),
            mode="近地点面内减速",
            objective_formula="m",
        )
    )
    _append_history(
        config,
        history_rows,
        float(final_candidate["burn_start_s"]),
        final_candidate["r_cutoff"],
        final_candidate["v_cutoff"],
        final_candidate["post_mass_kg"],
        "orbit_control",
        True,
    )

    failed = _failed_constraints(config, parameters)
    total_propellant = sum(item.propellant_kg for item in parameters)
    return ContinuousThrustOptimizationResult(
        parameters=parameters,
        total_propellant_kg=total_propellant,
        objective_delta_g_kg=total_propellant,
        time_step_s=time_step_s,
        yaw_step_deg=yaw_step_deg,
        hard_constraint_passed=not failed,
        failed_constraints=failed,
        orbit_history_rows=history_rows,
    )


def _apogee_q_sequence(result: DesignManeuverResult) -> tuple[int, int, int]:
    raw = str(result.summary.get("q_sequence", "") or "")
    values: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            values.append(int(part))
        except ValueError:
            continue
    if len(values) >= 3:
        return (max(1, values[0]), max(1, values[1]), max(1, values[2]))
    return FALLBACK_APOGEE_Q_SEQUENCE


def _evaluate_apogee_candidate(
    config: dict[str, Any],
    r: np.ndarray,
    v: np.ndarray,
    elapsed_s: float,
    mass_kg: float,
    burn: Any,
    target_a_km: float,
    burn_start_s: float,
    yaw_angle_deg: float,
    *,
    integration_step_s: float,
) -> dict[str, Any] | None:
    r_start, v_start = _propagate_state_to_elapsed(config, r, v, elapsed_s, burn_start_s)
    return _evaluate_first_continuous_thrust_candidate(
        config,
        r_start,
        v_start,
        burn_start_s,
        mass_kg,
        burn,
        target_a_km,
        yaw_angle_deg,
        "m",
        integration_step_s=integration_step_s,
    )


def _optimize_apogee_burn_for_inclination(
    config: dict[str, Any],
    r: np.ndarray,
    v: np.ndarray,
    elapsed_s: float,
    mass_kg: float,
    burn: Any,
    target_a_km: float,
    burn_start_s: float,
    center_yaw_deg: float,
    target_i_deg: float,
    *,
    integration_step_s: float,
) -> dict[str, Any] | None:
    def evaluate(yaw_deg: float) -> dict[str, Any] | None:
        candidate = _evaluate_apogee_candidate(
            config,
            r,
            v,
            elapsed_s,
            mass_kg,
            burn,
            target_a_km,
            burn_start_s,
            yaw_deg,
            integration_step_s=integration_step_s,
        )
        if candidate is not None:
            candidate["search_evaluations"] = int(candidate.get("search_evaluations", 0)) + 1
        return candidate

    best = _best_from_grid(
        _grid_values(float(center_yaw_deg), span=4.0, step=0.2),
        evaluate,
        lambda candidate: _mv4_score(candidate, target_i_deg),
    )
    if best is None:
        return None
    best = _best_from_grid(
        _grid_values(float(best["yaw_angle_deg"]), span=0.3, step=0.05),
        evaluate,
        lambda candidate: _mv4_score(candidate, target_i_deg),
        initial=best,
    )
    return best


def _mv4_score(candidate: dict[str, Any], target_i_deg: float) -> tuple[float, float]:
    return (
        abs(float(candidate["post_i_deg"]) - float(target_i_deg)),
        float(candidate["total_burn_time_s"]),
    )


def _optimize_final_perigee_burn_for_longitude(
    config: dict[str, Any],
    r: np.ndarray,
    v: np.ndarray,
    elapsed_s: float,
    mass_kg: float,
    burn: Any,
    center_start_s: float,
    target_lon_deg: float,
    *,
    integration_step_s: float,
) -> dict[str, Any] | None:
    target_a_km = float(config["target"]["a_km"])
    yaw_angle_deg = float(burn.alpha_deg)

    def evaluate(offset_min: float) -> dict[str, Any] | None:
        burn_start_s = max(float(elapsed_s), float(center_start_s) + offset_min * 60.0)
        r_start, v_start = _propagate_state_to_elapsed(config, r, v, elapsed_s, burn_start_s)
        burn_result = _integrate_low_thrust_to_target_metric(
            config,
            r_start,
            v_start,
            mass_kg,
            burn_start_s,
            "a",
            target_a_km,
            yaw_angle_deg,
            integration_step_s=integration_step_s,
        )
        if burn_result is None:
            return None
        candidate = _final_candidate(config, burn_result, r_start, v_start, burn_start_s, mass_kg, yaw_angle_deg)
        candidate["search_evaluations"] = int(candidate.get("search_evaluations", 0)) + 1
        candidate["seed_time_offset_s"] = abs(offset_min) * 60.0
        return candidate

    best = _best_from_grid(
        _grid_values(0.0, span=150.0, step=5.0),
        evaluate,
        lambda candidate: _final_score(candidate, config),
    )
    if best is None:
        return None
    best_offset_min = (float(best["burn_start_s"]) - float(center_start_s)) / 60.0
    best = _best_from_grid(
        _grid_values(best_offset_min, span=7.5, step=0.25),
        evaluate,
        lambda candidate: _final_score(candidate, config),
        initial=best,
    )
    return best


def _final_score(candidate: dict[str, Any], config: dict[str, Any]) -> tuple[float, ...]:
    target = config["target"]
    tolerance = config["terminal_tolerance"]
    lon_error = abs(_wrap180(float(candidate["cutoff_longitude_deg_e"]) - float(target["lon_degE"])))
    return (
        max(0.0, lon_error - float(tolerance["lon_deg"])),
        abs(float(candidate["post_a_km"]) - float(target["a_km"])),
        abs(float(candidate["post_i_deg"]) - float(target["i_deg"])),
        float(candidate["post_e"]),
        lon_error,
    )


def _best_from_grid(
    values: list[float],
    evaluate: Any,
    score: Any,
    *,
    initial: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    best = initial
    for value in values:
        candidate = evaluate(float(value))
        if candidate is None:
            continue
        if best is None or score(candidate) < score(best):
            best = candidate
    return best


def _grid_values(center: float, span: float, step: float) -> list[float]:
    if step <= 0.0:
        return [float(center)]
    count = max(0, int(math.ceil(float(span) / float(step))))
    return [float(center) + index * float(step) for index in range(-count, count + 1)]


def _parameter_from_candidate(
    config: dict[str, Any],
    burn: Any,
    candidate: dict[str, Any],
    target_a_km: float,
    initial_start_s: float,
    initial_yaw_deg: float,
    *,
    mode: str,
    objective_formula: str,
) -> ContinuousThrustManeuverParameter:
    total_burn_s = float(candidate["total_burn_time_s"])
    return ContinuousThrustManeuverParameter(
        maneuver_index=int(burn.index),
        flight_revolution=int(burn.flight_revolution),
        position_label=burn.position_label or ("远地点" if burn.apsis == "A" else "近地点"),
        initial_burn_start_min=float(initial_start_s) / 60.0,
        initial_yaw_angle_deg=float(initial_yaw_deg),
        burn_start_min=float(candidate["burn_start_s"]) / 60.0,
        settle_end_min=float(candidate["settle_end_s"]) / 60.0,
        cutoff_min=float(candidate["cutoff_s"]) / 60.0,
        yaw_angle_deg=float(candidate["yaw_angle_deg"]),
        ignition_longitude_deg_e=float(candidate["ignition_longitude_deg_e"]),
        cutoff_longitude_deg_e=float(candidate["cutoff_longitude_deg_e"]),
        delta_v_mps=float(candidate["delta_v_mps"]),
        target_post_a_km=float(target_a_km),
        total_burn_time_min=total_burn_s / 60.0,
        settle_duration_min=float(candidate["settle_duration_s"]) / 60.0,
        orbit_control_duration_min=float(candidate["orbit_control_duration_s"]) / 60.0,
        propellant_kg=float(candidate["propellant_kg"]),
        future_apogee_raise_propellant_kg=0.0,
        future_perigee_lower_propellant_kg=0.0,
        trim_propellant_kg=0.0,
        objective_delta_g_kg=float(candidate["propellant_kg"]),
        objective_formula=objective_formula,
        post_a_km=float(candidate["post_a_km"]),
        post_e=float(candidate["post_e"]),
        post_i_deg=float(candidate["post_i_deg"]),
        post_mass_kg=float(candidate["post_mass_kg"]),
        duration_ok=bool(candidate["duration_ok"]),
        longitude_ok=bool(candidate["longitude_ok"]),
        search_evaluations=1,
        optimization_mode=mode,
    )


def _final_candidate(
    config: dict[str, Any],
    burn_result: dict[str, Any],
    r_start: np.ndarray,
    v_start: np.ndarray,
    burn_start_s: float,
    mass_kg: float,
    yaw_angle_deg: float,
) -> dict[str, Any]:
    r_cutoff = np.asarray(burn_result["r_cutoff"], dtype=float)
    v_cutoff = np.asarray(burn_result["v_cutoff"], dtype=float)
    post_a, post_e, post_i_rad, *_ = _rv_to_coe(r_cutoff, v_cutoff, mu=float(config["earth"]["mu_km3_s2"]))
    cutoff_s = float(burn_result["cutoff_s"])
    return {
        "burn_start_s": float(burn_start_s),
        "settle_end_s": float(burn_start_s) + float(burn_result["settle_duration_s"]),
        "cutoff_s": cutoff_s,
        "yaw_angle_deg": float(yaw_angle_deg),
        "ignition_longitude_deg_e": _longitude_deg(config, r_start, burn_start_s),
        "cutoff_longitude_deg_e": _longitude_deg(config, r_cutoff, cutoff_s),
        "delta_v_mps": float(burn_result["delta_v_mps"]),
        "total_burn_time_s": float(burn_result["total_burn_time_s"]),
        "settle_duration_s": float(burn_result["settle_duration_s"]),
        "orbit_control_duration_s": float(burn_result["orbit_control_duration_s"]),
        "propellant_kg": max(0.0, float(mass_kg) - float(burn_result["post_mass_kg"])),
        "post_a_km": float(post_a),
        "post_e": float(post_e),
        "post_i_deg": math.degrees(post_i_rad),
        "post_mass_kg": max(1.0, float(burn_result["post_mass_kg"])),
        "duration_ok": float(burn_result["total_burn_time_s"]) <= float(config["burn_limit"]["max_total_burn_time_min"]) * 60.0 + 1.0e-9,
        "longitude_ok": True,
        "r_cutoff": r_cutoff,
        "v_cutoff": v_cutoff,
    }


def _append_history(
    config: dict[str, Any],
    rows: list[dict[str, Any]],
    elapsed_s: float,
    r: np.ndarray,
    v: np.ndarray,
    mass_kg: float,
    phase: str,
    is_event_point: bool,
) -> None:
    _append_continuous_orbit_history_row(
        config,
        rows,
        elapsed_s,
        np.asarray(r, dtype=float),
        np.asarray(v, dtype=float),
        float(mass_kg),
        phase=phase,
        is_event_point=is_event_point,
    )


def _failed_constraints(config: dict[str, Any], parameters: list[ContinuousThrustManeuverParameter]) -> list[str]:
    if len(parameters) < 5:
        return ["连续推力参数"]
    failed: list[str] = []
    mv4 = parameters[3]
    mv5 = parameters[4]
    re_km = float(config["earth"]["Re_km"])
    sync_a_km = float(config["target"]["a_km"])
    target_i_deg = float(config["target"]["i_deg"])
    tolerance = config["terminal_tolerance"]
    if abs(mv4.post_a_km * (1.0 - mv4.post_e) - re_km - (sync_a_km - re_km)) > 0.05:
        failed.append("MV4近地点高度")
    if abs(mv4.post_i_deg - target_i_deg) > float(tolerance["i_deg"]):
        failed.append("MV4倾角")
    if abs(mv5.post_a_km - sync_a_km) > float(tolerance["a_km"]):
        failed.append("MV5半长轴")
    if abs(_wrap180(mv5.cutoff_longitude_deg_e - float(config["target"]["lon_degE"]))) > float(tolerance["lon_deg"]):
        failed.append("MV5熄火经度")
    if not all(item.duration_ok for item in parameters):
        failed.append("总点火时长")
    return failed
