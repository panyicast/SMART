"""Continuous-thrust expansion for the design maneuver strategy.

Frozen algorithm rules:
- MV1-MV3 follow the pulse planner q sequence, event timing, yaw seeds, and perigee targets.
- MV4 is the only tail phasing lever: optimize its start time and yaw to close target longitude
  while satisfying target inclination.
- MV5 must remain near perigee and may only make a small start-time adjustment. It minimizes
  post-control eccentricity while cutting off at target semi-major axis. Do not use MV5 as a
  large longitude phasing burn.

Update the regression tests and `doc/design_continuous_thrust_parameter_optimization_algorithm.md`
before changing these invariants.
"""

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
    _require_j2_enabled,
    _rv_to_coe,
    _initial_state_km,
    _wrap180,
    normalize_design_maneuver_strategy_payload,
)

FALLBACK_APOGEE_Q_SEQUENCE = (3, 3, 3)
MV5_NEAR_PERIGEE_START_WINDOW_MIN = 3.0
MV5_LOCKED_MAX_ECCENTRICITY = 1.0e-3


def optimize_continuous_thrust_chain_parameters(
    result: DesignManeuverResult,
) -> ContinuousThrustOptimizationResult:
    config = normalize_design_maneuver_strategy_payload(result.config)
    _require_j2_enabled(config)
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

    for burn_position in range(3):
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
        target_a_km = float(
            burn.target_post_a_km if burn.target_post_a_km is not None else burn.post_a_km
        )
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

    mv4_burn = burns[3]
    final_burn = burns[-1]
    mv4_event_s, _mv4_event_r, _mv4_event_v = _next_apsis(
        config,
        r,
        v,
        elapsed_s,
        "A",
        apogee_q_sequence[2],
    )
    mv4_nominal_start_s = mv4_event_s - 0.5 * float(mv4_burn.total_burn_time_min) * 60.0
    mv4_candidate, final_candidate, nominal_final_start_s = _optimize_tail_for_longitude_and_eccentricity(
        config,
        r,
        v,
        elapsed_s,
        mass_kg,
        mv4_burn,
        final_burn,
        mv4_nominal_start_s,
        target_lon_deg,
        target_i_deg,
        integration_step_s=final_integration_step_s,
    )
    if mv4_candidate is None or final_candidate is None:
        raise ValueError("连续推力尾段优化未能生成 MV4/MV5 目标解。")
    parameters.append(
        _parameter_from_candidate(
            config,
            mv4_burn,
            mv4_candidate,
            sync_a_km,
            mv4_nominal_start_s,
            float(mv4_burn.alpha_deg),
            mode="终端经度/倾角联合优化",
            objective_formula="m",
        )
    )
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


def _optimize_tail_for_longitude_and_eccentricity(
    config: dict[str, Any],
    r: np.ndarray,
    v: np.ndarray,
    elapsed_s: float,
    mass_kg: float,
    mv4_burn: Any,
    final_burn: Any,
    mv4_center_start_s: float,
    target_lon_deg: float,
    target_i_deg: float,
    *,
    integration_step_s: float,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, float]:
    cache: dict[tuple[float, float, float], tuple[dict[str, Any], dict[str, Any], float] | None] = {}
    target_a_km = float(config["target"]["a_km"])
    search_integration_step_s = max(float(integration_step_s), 120.0)

    def evaluate(
        offset_min: float,
        yaw_delta_deg: float,
        *,
        step_s: float = search_integration_step_s,
        use_cache: bool = True,
    ) -> tuple[dict[str, Any], dict[str, Any], float] | None:
        key = (round(float(offset_min), 4), round(float(yaw_delta_deg), 4), round(float(step_s), 4))
        if use_cache and key in cache:
            return cache[key]
        mv4_start_s = max(float(elapsed_s), float(mv4_center_start_s) + float(offset_min) * 60.0)
        mv4_candidate = _evaluate_apogee_candidate(
            config,
            r,
            v,
            elapsed_s,
            mass_kg,
            mv4_burn,
            target_a_km,
            mv4_start_s,
            float(mv4_burn.alpha_deg) + float(yaw_delta_deg),
            integration_step_s=step_s,
        )
        if mv4_candidate is None:
            if use_cache:
                cache[key] = None
            return None
        mv4_r = np.asarray(mv4_candidate["r_cutoff"], dtype=float)
        mv4_v = np.asarray(mv4_candidate["v_cutoff"], dtype=float)
        mv4_elapsed_s = float(mv4_candidate["cutoff_s"])
        perigee_s, _perigee_r, _perigee_v = _next_apsis(config, mv4_r, mv4_v, mv4_elapsed_s, "P", 1)
        final_center_start_s = perigee_s - 0.5 * float(final_burn.total_burn_time_min) * 60.0
        final_candidate = _optimize_final_perigee_burn_for_eccentricity(
            config,
            mv4_r,
            mv4_v,
            mv4_elapsed_s,
            float(mv4_candidate["post_mass_kg"]),
            final_burn,
            final_center_start_s,
            integration_step_s=step_s,
        )
        if final_candidate is None:
            if use_cache:
                cache[key] = None
            return None
        result = (mv4_candidate, final_candidate, final_center_start_s)
        if use_cache:
            cache[key] = result
        return result

    def score(
        offset_min: float,
        yaw_delta_deg: float,
        *,
        step_s: float = search_integration_step_s,
    ) -> float:
        item = evaluate(offset_min, yaw_delta_deg, step_s=step_s)
        if item is None:
            return 1.0e12
        mv4_candidate, final_candidate, _final_center_start_s = item
        tolerance = config["terminal_tolerance"]
        lon_error = abs(_wrap180(float(final_candidate["cutoff_longitude_deg_e"]) - float(target_lon_deg)))
        mv4_i_error = abs(float(mv4_candidate["post_i_deg"]) - float(target_i_deg))
        final_i_error = abs(float(final_candidate["post_i_deg"]) - float(target_i_deg))
        lon_excess = max(0.0, lon_error - float(tolerance["lon_deg"]))
        mv4_i_excess = max(0.0, mv4_i_error - float(tolerance["i_deg"]))
        return (
            1.0e8 * lon_excess * lon_excess
            + 1.0e8 * mv4_i_excess * mv4_i_excess
            + 2.0e3 * float(final_candidate["post_e"])
            + 50.0 * final_i_error
            + lon_error
            + 100.0 * mv4_i_error
            + 0.02 * abs(float(offset_min))
        )

    seeds = [
        (0.0, 0.0),
        (2.0, 0.75),
        (2.0, 0.5),
        (2.0, 1.0),
        (1.0, 0.75),
        (3.0, 0.75),
        (-2.0, -0.75),
        (0.0, -1.0),
        (0.0, 1.0),
    ]
    best_x = min(seeds, key=lambda item: score(item[0], item[1]))
    fine_offsets = _grid_values(float(best_x[0]), span=1.0, step=1.0)
    fine_yaws = _grid_values(float(best_x[1]), span=0.1, step=0.1)
    for offset_min in fine_offsets:
        for yaw_delta_deg in fine_yaws:
            if score(offset_min, yaw_delta_deg) < score(best_x[0], best_x[1]):
                best_x = (offset_min, yaw_delta_deg)
    best_x = _polish_tail_candidate_with_final_step(
        lambda offset_min, yaw_delta_deg: score(offset_min, yaw_delta_deg, step_s=integration_step_s),
        best_x,
    )
    best = evaluate(best_x[0], best_x[1], step_s=integration_step_s, use_cache=False)
    if best is None:
        return None, None, 0.0
    return best


def _polish_tail_candidate_with_final_step(
    score: Any,
    best_x: tuple[float, float],
) -> tuple[float, float]:
    best_offset, best_yaw_delta = float(best_x[0]), float(best_x[1])
    best_score = float(score(best_offset, best_yaw_delta))
    for offset_step, yaw_step, max_iter in ((0.1, 0.05, 6), (0.05, 0.025, 4)):
        for _ in range(max_iter):
            improved = False
            candidates = (
                (best_offset - offset_step, best_yaw_delta),
                (best_offset + offset_step, best_yaw_delta),
                (best_offset, best_yaw_delta - yaw_step),
                (best_offset, best_yaw_delta + yaw_step),
            )
            for offset_min, yaw_delta_deg in candidates:
                candidate_score = float(score(offset_min, yaw_delta_deg))
                if candidate_score < best_score:
                    best_offset = float(offset_min)
                    best_yaw_delta = float(yaw_delta_deg)
                    best_score = candidate_score
                    improved = True
            if not improved:
                break
    return best_offset, best_yaw_delta


def _optimize_final_perigee_burn_for_eccentricity(
    config: dict[str, Any],
    r: np.ndarray,
    v: np.ndarray,
    elapsed_s: float,
    mass_kg: float,
    burn: Any,
    center_start_s: float,
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

    return _best_from_grid(
        _grid_values(0.0, span=1.0, step=1.0),
        evaluate,
        lambda candidate: _final_eccentricity_score(candidate, config),
    )


def _final_eccentricity_score(candidate: dict[str, Any], config: dict[str, Any]) -> tuple[float, float, float]:
    return (
        float(candidate["post_e"]),
        abs(float(candidate["post_a_km"]) - float(config["target"]["a_km"])),
        abs(float(candidate.get("seed_time_offset_s", 0.0))),
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
