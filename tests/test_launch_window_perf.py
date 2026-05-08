"""回归保护：对 _body_plus_z_ecef_for_attitude 的优化进行等价性测试。

旧实现在每次候选评估时跑一遍 per-sample Python 循环。优化后把循环
hoist 到 _build_timeline 中，候选评估只剩向量化的 ECI→ECEF 旋转。
本测试构造同一份 timeline，独立调用 legacy 路径与新路径，验证两者
逐元素一致。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np

from smart.services.launch_window import (
    ManeuverInterval,
    _body_plus_z_ecef_for_attitude,
    _eci_direction_to_ecef,
    _normalize,
    _sun_unit_ecef_for_elapsed,
    _thrust_direction_for_state,
)


def _legacy_body_plus_z_ecef_for_attitude(
    t0_utc: datetime,
    timeline: dict,
    maneuvers: list[ManeuverInterval],
    sun_vectors_ecef: np.ndarray,
) -> np.ndarray:
    """优化前的实现，作为对照基线（与历史代码逐行等价）。"""

    elapsed_min = timeline["elapsed_min"]
    phases = timeline["phases"]
    inertial_states = timeline["inertial_states"]
    plus_z = -sun_vectors_ecef.copy()
    reference_plus_z = timeline.get("thrust_plus_z_ecef")
    reference_mask = timeline.get("thrust_attitude_mask")
    if (
        reference_plus_z is not None
        and reference_mask is not None
        and bool(reference_mask.any())
    ):
        plus_z[reference_mask] = reference_plus_z[reference_mask]
        return _normalize(plus_z)

    def maneuver_for_time(minute: float) -> ManeuverInterval | None:
        for maneuver in maneuvers:
            if maneuver.start_min - 1e-9 <= minute <= maneuver.end_min + 1e-9:
                return maneuver
        return None

    for index, (minute, phase) in enumerate(zip(elapsed_min, phases, strict=True)):
        maneuver = maneuver_for_time(float(minute))
        if phase not in {"settle", "orbit_control"} and maneuver is None:
            continue
        if maneuver is None:
            maneuver = ManeuverInterval(float(minute), float(minute), 0.0, 1)
        direction_eci = _thrust_direction_for_state(
            inertial_states[index],
            maneuver.delta_deg,
            maneuver.dv_direction,
        )
        epoch = t0_utc + timedelta(minutes=float(minute))
        plus_z[index] = _eci_direction_to_ecef(direction_eci, epoch)
    return _normalize(plus_z)


def _build_synthetic_timeline_with_attitude() -> tuple[dict, list[ManeuverInterval]]:
    """合成一段含 settle/orbit_control 阶段与一次变轨的 timeline，触发 attitude 路径。"""

    n = 60
    elapsed_min = np.arange(n, dtype=np.float64)
    phases: list[str] = []
    for minute in elapsed_min:
        if 5.0 <= minute < 10.0:
            phases.append("settle")
        elif 25.0 <= minute < 30.0:
            phases.append("orbit_control")
        else:
            phases.append("coast")

    rng = np.random.default_rng(seed=42)
    inertial_states = np.zeros((n, 6), dtype=np.float64)
    for i in range(n):
        position = rng.normal(scale=7e6, size=3)
        velocity = rng.normal(scale=7.5e3, size=3)
        inertial_states[i, :3] = position
        inertial_states[i, 3:] = velocity

    maneuvers = [
        ManeuverInterval(
            start_min=12.0,
            end_min=14.0,
            delta_deg=2.5,
            maneuver_index=1,
            dv_direction=1,
        )
    ]
    timeline = {
        "elapsed_min": elapsed_min,
        "phases": phases,
        "inertial_states": inertial_states,
    }
    return timeline, maneuvers


def test_body_plus_z_attitude_optimization_matches_legacy() -> None:
    from smart.services.launch_window import _precompute_attitude_thrust

    timeline, maneuvers = _build_synthetic_timeline_with_attitude()
    active_mask, thrust_eci = _precompute_attitude_thrust(
        timeline["elapsed_min"], timeline["phases"], timeline["inertial_states"], maneuvers
    )
    timeline["attitude_active_mask"] = active_mask
    timeline["attitude_thrust_eci"] = thrust_eci

    t0_utc = datetime(2026, 5, 1, 0, 0, 0, tzinfo=timezone.utc)
    sun_vectors = _sun_unit_ecef_for_elapsed(t0_utc, timeline["elapsed_min"])

    legacy = _legacy_body_plus_z_ecef_for_attitude(t0_utc, timeline, maneuvers, sun_vectors)
    optimized = _body_plus_z_ecef_for_attitude(t0_utc, timeline, maneuvers, sun_vectors)

    assert legacy.shape == optimized.shape
    assert np.allclose(legacy, optimized, rtol=0.0, atol=1e-12)


def test_body_plus_z_attitude_no_active_returns_normalized_anti_sun() -> None:
    from smart.services.launch_window import _precompute_attitude_thrust

    elapsed_min = np.linspace(0.0, 30.0, 32, dtype=np.float64)
    phases = ["coast"] * 32
    inertial_states = np.zeros((32, 6), dtype=np.float64)
    maneuvers: list[ManeuverInterval] = []
    active_mask, thrust_eci = _precompute_attitude_thrust(
        elapsed_min, phases, inertial_states, maneuvers
    )
    assert not bool(active_mask.any())
    timeline = {
        "elapsed_min": elapsed_min,
        "phases": phases,
        "inertial_states": inertial_states,
        "attitude_active_mask": active_mask,
        "attitude_thrust_eci": thrust_eci,
    }
    t0_utc = datetime(2026, 5, 1, 0, 0, 0, tzinfo=timezone.utc)
    sun_vectors = _sun_unit_ecef_for_elapsed(t0_utc, elapsed_min)
    plus_z = _body_plus_z_ecef_for_attitude(t0_utc, timeline, maneuvers, sun_vectors)
    expected = _normalize(-sun_vectors.copy())
    assert np.allclose(plus_z, expected, rtol=0.0, atol=1e-12)
