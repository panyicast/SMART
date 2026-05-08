from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math
from pathlib import Path
from typing import Any
from uuid import uuid4

import numpy as np

from smart.services.earth_orientation import format_utc, parse_utc
from smart.services.launch_window import (
    ManeuverInterval,
    _body_plus_z_ecef_for_attitude,
    _build_timeline,
    _maneuver_intervals,
    _reference_t0_utc_from_strategy,
    _shadow_flags_and_margin,
    _sun_unit_ecef_for_elapsed,
    load_orbit_history_rows,
)
from smart.services.tracking_arc import TrackingArcOrbitResult, TrackingArcSegment

ATTITUDE_KIND = "attitude"
DEPLOYMENT_KIND = "deployment"
MODE_SPM = "SPM"
MODE_EPM = "EPM"
MODE_AFM = "AFM"
MODE_TRANSITION = "Transition"
DEFAULT_TRANSITION_MIN = 20.0
DEFAULT_MANEUVER_ATTITUDE_PAD_MIN = 60.0


@dataclass(frozen=True, slots=True)
class FlightProgramWarning:
    severity: str
    message: str
    event_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class FlightProgramSample:
    elapsed_min: float
    mode: str
    event_name: str
    position_m: tuple[float, float, float]
    velocity_mps: tuple[float, float, float]
    subsatellite_longitude_deg: float
    subsatellite_latitude_deg: float
    altitude_m: float
    plus_z_ecef: tuple[float, float, float]
    sun_ecef: tuple[float, float, float]
    earth_ecef: tuple[float, float, float]
    in_shadow: bool


@dataclass(slots=True)
class FlightProgramSamplingContext:
    rows: list[dict[str, float | str]]
    timeline: dict[str, Any]
    elapsed_min: np.ndarray
    sun_vectors: np.ndarray
    shadow_mask: np.ndarray
    maneuvers: list[ManeuverInterval]
    t0_utc: datetime
    afm_plus_z_ecef: np.ndarray


def default_flight_program_payload() -> dict[str, Any]:
    return {
        "version": 1,
        "time_reference": "t0_elapsed_min",
        "launch_selection_mode": "window",
        "selected_launch_utc": "",
        "selected_orbit_point": "leading",
        "selected_t0_utc": "",
        "generation": {
            "transition_duration_min": DEFAULT_TRANSITION_MIN,
            "source": "manual",
        },
        "events": [],
    }


def normalize_flight_program_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    source = payload if isinstance(payload, dict) else {}
    result = default_flight_program_payload()
    result["version"] = int(source.get("version", result["version"]))
    result["time_reference"] = str(source.get("time_reference", result["time_reference"]) or "t0_elapsed_min")
    launch_selection_mode = str(source.get("launch_selection_mode", result["launch_selection_mode"]) or "window")
    result["launch_selection_mode"] = launch_selection_mode if launch_selection_mode in {"window", "manual"} else "window"
    result["selected_launch_utc"] = str(source.get("selected_launch_utc", result["selected_launch_utc"]) or "")
    result["selected_orbit_point"] = str(source.get("selected_orbit_point", result["selected_orbit_point"]) or "leading")
    result["selected_t0_utc"] = str(source.get("selected_t0_utc", result["selected_t0_utc"]) or "")
    generation = source.get("generation")
    if isinstance(generation, dict):
        result["generation"] = {
            "transition_duration_min": _finite_float(
                generation.get("transition_duration_min"),
                DEFAULT_TRANSITION_MIN,
            ),
            "source": str(generation.get("source", "manual") or "manual"),
        }
    events = source.get("events", [])
    if not isinstance(events, list):
        events = []
    result["events"] = [normalize_flight_event(item, index) for index, item in enumerate(events)]
    result["events"].sort(key=lambda item: (float(item["start_min"]), float(item["end_min"]), str(item["name"])))
    return result


def normalize_flight_event(raw_event: Any, index: int = 0) -> dict[str, Any]:
    event = raw_event if isinstance(raw_event, dict) else {}
    start_min = _finite_float(event.get("start_min"), 0.0)
    end_min = _finite_float(event.get("end_min"), start_min)
    instant = bool(event.get("instant", False))
    if instant:
        end_min = start_min
    elif end_min < start_min:
        start_min, end_min = end_min, start_min
    kind = str(event.get("kind", ATTITUDE_KIND) or ATTITUDE_KIND)
    mode = str(event.get("mode", MODE_SPM) or MODE_SPM)
    if kind == ATTITUDE_KIND and mode not in {MODE_SPM, MODE_EPM, MODE_AFM, MODE_TRANSITION}:
        mode = MODE_SPM
    properties = event.get("properties")
    if not isinstance(properties, dict):
        properties = {}
    return {
        "id": str(event.get("id") or f"fp-{index + 1:04d}-{uuid4().hex[:8]}"),
        "name": str(event.get("name") or _default_event_name(kind, mode, index)),
        "kind": kind,
        "mode": mode,
        "start_min": float(start_min),
        "end_min": float(end_min),
        "instant": instant,
        "source": str(event.get("source", "manual") or "manual"),
        "locked": bool(event.get("locked", False)),
        "notes": str(event.get("notes", "") or ""),
        "properties": dict(properties),
    }


def generate_flight_program_draft(
    *,
    orbit_history_csv: str | Path,
    maneuver_strategy: dict[str, Any],
    tracking_result: TrackingArcOrbitResult | None = None,
    selected_orbit_point: str = "leading",
    launch_selection_mode: str = "window",
    transition_duration_min: float = DEFAULT_TRANSITION_MIN,
) -> dict[str, Any]:
    rows = load_orbit_history_rows(orbit_history_csv)
    elapsed_min = [float(row["elapsed_time_min"]) for row in rows]
    timeline_start = min(elapsed_min)
    timeline_end = max(elapsed_min)
    transition = max(0.0, float(transition_duration_min))
    maneuvers = _maneuver_intervals(maneuver_strategy)

    attitude_blocks: list[dict[str, Any]] = []
    protected: list[tuple[float, float]] = []
    for maneuver in maneuvers:
        afm_start = max(timeline_start, maneuver.start_min - DEFAULT_MANEUVER_ATTITUDE_PAD_MIN)
        afm_end = min(timeline_end, maneuver.end_min + DEFAULT_MANEUVER_ATTITUDE_PAD_MIN)
        before_start = max(timeline_start, afm_start - transition)
        if afm_start > before_start:
            attitude_blocks.append(
                _event(
                    f"T{maneuver.maneuver_index} 点火前过渡",
                    ATTITUDE_KIND,
                    MODE_TRANSITION,
                    before_start,
                    afm_start,
                    source="auto",
                    properties={"from": MODE_SPM, "to": MODE_AFM, "maneuver_index": maneuver.maneuver_index},
                )
            )
            protected.append((before_start, afm_start))
        attitude_blocks.append(
            _event(
                f"T{maneuver.maneuver_index} 点火模式",
                ATTITUDE_KIND,
                MODE_AFM,
                afm_start,
                afm_end,
                source="auto",
                properties={"maneuver_index": maneuver.maneuver_index},
            )
        )
        protected.append((afm_start, afm_end))
        after_end = min(timeline_end, afm_end + transition)
        if after_end > afm_end:
            attitude_blocks.append(
                _event(
                    f"T{maneuver.maneuver_index} 点火后过渡",
                    ATTITUDE_KIND,
                    MODE_TRANSITION,
                    afm_end,
                    after_end,
                    source="auto",
                    properties={"from": MODE_AFM, "to": MODE_SPM, "maneuver_index": maneuver.maneuver_index},
                )
            )
            protected.append((afm_end, after_end))

    occupied = sorted(protected)
    for start_min, end_min in _complement_intervals(timeline_start, timeline_end, occupied):
        if end_min - start_min < 1.0:
            continue
        attitude_blocks.append(
            _event("太阳指向巡航", ATTITUDE_KIND, MODE_SPM, start_min, end_min, source="auto")
        )

    deployment_blocks = _default_deployment_events(timeline_start, timeline_end)
    t0_utc = tracking_result.t0_utc if tracking_result is not None else ""
    payload = default_flight_program_payload()
    payload["launch_selection_mode"] = launch_selection_mode if launch_selection_mode in {"window", "manual"} else "window"
    payload["selected_launch_utc"] = tracking_result.launch_utc if tracking_result is not None else ""
    payload["selected_orbit_point"] = selected_orbit_point
    payload["selected_t0_utc"] = t0_utc
    payload["generation"] = {
        "transition_duration_min": transition,
        "source": "auto_draft",
    }
    payload["events"] = attitude_blocks + deployment_blocks
    return normalize_flight_program_payload(payload)


def validate_flight_program(
    payload: dict[str, Any],
    *,
    maneuver_strategy: dict[str, Any] | None = None,
    reference_segments: list[TrackingArcSegment] | None = None,
) -> list[FlightProgramWarning]:
    program = normalize_flight_program_payload(payload)
    warnings: list[FlightProgramWarning] = []
    attitude_events = [
        item for item in program["events"] if item["kind"] == ATTITUDE_KIND and not bool(item.get("instant"))
    ]
    attitude_events.sort(key=lambda item: (float(item["start_min"]), float(item["end_min"])))
    for left, right in zip(attitude_events, attitude_events[1:], strict=False):
        if float(left["end_min"]) > float(right["start_min"]) + 1e-6:
            warnings.append(
                FlightProgramWarning(
                    "warning",
                    f"姿态事件重叠：{left['name']} 与 {right['name']}",
                    (str(left["id"]), str(right["id"])),
                )
            )
        elif float(right["start_min"]) - float(left["end_min"]) > 1.0:
            warnings.append(
                FlightProgramWarning(
                    "info",
                    f"姿态事件存在空档：T0+{float(left['end_min']):.1f} 到 T0+{float(right['start_min']):.1f} min",
                    (str(left["id"]), str(right["id"])),
                )
            )

    if maneuver_strategy is not None:
        for maneuver in _maneuver_intervals(maneuver_strategy):
            covering = [
                event
                for event in attitude_events
                if event["mode"] == MODE_AFM
                and float(event["start_min"]) <= maneuver.start_min + 1e-6
                and float(event["end_min"]) >= maneuver.end_min - 1e-6
            ]
            if not covering:
                warnings.append(
                    FlightProgramWarning(
                        "warning",
                        f"T{maneuver.maneuver_index} 点火区间未被 AFM 完整覆盖。",
                    )
                )

    shadow_segments = [item for item in reference_segments or [] if item.kind == "shadow"]
    if shadow_segments:
        for event in program["events"]:
            if event["kind"] != DEPLOYMENT_KIND:
                continue
            if _event_intersects_shadow(event, shadow_segments, program.get("selected_t0_utc", "")):
                warnings.append(
                    FlightProgramWarning(
                        "warning",
                        f"{event['name']} 与地影时段存在交叠。",
                        (str(event["id"]),),
                    )
                )
    return warnings


def sample_flight_program_state(
    *,
    orbit_history_csv: str | Path,
    maneuver_strategy: dict[str, Any],
    payload: dict[str, Any],
    elapsed_min: float,
    t0_utc: str | datetime | None = None,
    context: FlightProgramSamplingContext | None = None,
) -> FlightProgramSample:
    sampling = context or build_flight_program_sampling_context(
        orbit_history_csv=orbit_history_csv,
        maneuver_strategy=maneuver_strategy,
        payload=payload,
        t0_utc=t0_utc,
    )
    elapsed = sampling.elapsed_min
    index = int(np.argmin(np.abs(elapsed - float(elapsed_min))))
    attitude_events = _attitude_events_for_payload(payload)
    return _sample_from_context_index(sampling, index=index, attitude_events=attitude_events)


def sample_flight_program_states(
    *,
    orbit_history_csv: str | Path,
    maneuver_strategy: dict[str, Any],
    payload: dict[str, Any],
    t0_utc: str | datetime | None = None,
    context: FlightProgramSamplingContext | None = None,
) -> list[FlightProgramSample]:
    sampling = context or build_flight_program_sampling_context(
        orbit_history_csv=orbit_history_csv,
        maneuver_strategy=maneuver_strategy,
        payload=payload,
        t0_utc=t0_utc,
    )
    attitude_events = _attitude_events_for_payload(payload)
    return [
        _sample_from_context_index(sampling, index=index, attitude_events=attitude_events)
        for index in range(len(sampling.elapsed_min))
    ]


def build_flight_program_sampling_context(
    *,
    orbit_history_csv: str | Path,
    maneuver_strategy: dict[str, Any],
    payload: dict[str, Any] | None = None,
    t0_utc: str | datetime | None = None,
    rows: list[dict[str, float | str]] | None = None,
) -> FlightProgramSamplingContext:
    orbit_rows = rows if rows is not None else load_orbit_history_rows(orbit_history_csv)
    maneuvers = _maneuver_intervals(maneuver_strategy)
    timeline = _build_timeline(
        orbit_rows,
        [],
        maneuvers=maneuvers,
        reference_t0_utc=_reference_t0_utc_from_strategy(maneuver_strategy),
    )
    elapsed = timeline["elapsed_min"]
    normalized_payload = payload if isinstance(payload, dict) else {}
    epoch = _resolve_t0_utc(t0_utc, normalized_payload, maneuver_strategy)
    sun_vectors = _sun_unit_ecef_for_elapsed(epoch, elapsed)
    shadow_mask, _margin = _shadow_flags_and_margin(epoch, timeline, sun_vectors=sun_vectors)
    afm_plus_z_ecef = _body_plus_z_ecef_for_attitude(epoch, timeline, maneuvers, sun_vectors)
    return FlightProgramSamplingContext(
        rows=orbit_rows,
        timeline=timeline,
        elapsed_min=elapsed,
        sun_vectors=sun_vectors,
        shadow_mask=shadow_mask,
        maneuvers=maneuvers,
        t0_utc=epoch,
        afm_plus_z_ecef=afm_plus_z_ecef,
    )


def active_attitude_event(payload: dict[str, Any], elapsed_min: float) -> dict[str, Any] | None:
    program = normalize_flight_program_payload(payload)
    matches = _active_attitude_event_from_events(_attitude_events_from_program(program), elapsed_min)
    if not matches:
        return None
    return matches[0]


def _sample_from_context_index(
    sampling: FlightProgramSamplingContext,
    *,
    index: int,
    attitude_events: list[dict[str, Any]],
) -> FlightProgramSample:
    rows = sampling.rows
    timeline = sampling.timeline
    sample_min = float(sampling.elapsed_min[index])
    matches = _active_attitude_event_from_events(attitude_events, sample_min)
    event = None if not matches else matches[0]
    mode = MODE_SPM if event is None else str(event["mode"])
    plus_z = _plus_z_for_mode(
        mode=mode,
        timeline=timeline,
        sun_vectors=sampling.sun_vectors,
        index=index,
        t0_utc=sampling.t0_utc,
        maneuvers=sampling.maneuvers,
        afm_plus_z_ecef=sampling.afm_plus_z_ecef,
    )
    position = np.asarray(timeline["positions"][index], dtype=np.float64)
    row = rows[index]
    earth_unit = _normalize_vector(-position)
    return FlightProgramSample(
        elapsed_min=sample_min,
        mode=mode,
        event_name="" if event is None else str(event["name"]),
        position_m=tuple(float(value) for value in position),
        velocity_mps=(
            float(row.get("velocity_x_m_s", 0.0)),
            float(row.get("velocity_y_m_s", 0.0)),
            float(row.get("velocity_z_m_s", 0.0)),
        ),
        subsatellite_longitude_deg=float(row.get("subsatellite_longitude_deg", 0.0)),
        subsatellite_latitude_deg=float(row.get("subsatellite_latitude_deg", 0.0)),
        altitude_m=float(row.get("subsatellite_altitude_m", row.get("orbit_height_m", 0.0))),
        plus_z_ecef=tuple(float(value) for value in plus_z),
        sun_ecef=tuple(float(value) for value in sampling.sun_vectors[index]),
        earth_ecef=tuple(float(value) for value in earth_unit),
        in_shadow=bool(sampling.shadow_mask[index]),
    )


def _attitude_events_for_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return _attitude_events_from_program(normalize_flight_program_payload(payload))


def _attitude_events_from_program(program: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        item
        for item in program["events"]
        if item["kind"] == ATTITUDE_KIND and not bool(item.get("instant"))
    ]


def _active_attitude_event_from_events(
    attitude_events: list[dict[str, Any]],
    elapsed_min: float,
) -> list[dict[str, Any]]:
    matches = [
        item
        for item in attitude_events
        if float(item["start_min"]) - 1e-6 <= elapsed_min <= float(item["end_min"]) + 1e-6
    ]
    if not matches:
        return []
    priority = {MODE_AFM: 0, MODE_TRANSITION: 1, MODE_EPM: 2, MODE_SPM: 3}
    matches.sort(key=lambda item: (priority.get(str(item["mode"]), 9), float(item["start_min"])))
    return matches


def _event(
    name: str,
    kind: str,
    mode: str,
    start_min: float,
    end_min: float,
    *,
    source: str,
    instant: bool = False,
    properties: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return normalize_flight_event(
        {
            "id": f"fp-{uuid4().hex[:10]}",
            "name": name,
            "kind": kind,
            "mode": mode,
            "start_min": start_min,
            "end_min": start_min if instant else end_min,
            "instant": instant,
            "source": source,
            "locked": False,
            "notes": "",
            "properties": properties or {},
        }
    )


def _default_event_name(kind: str, mode: str, index: int) -> str:
    if kind == DEPLOYMENT_KIND:
        return f"主要事件 {index + 1}"
    return f"{mode} 姿态 {index + 1}"


def _default_deployment_events(timeline_start: float, timeline_end: float) -> list[dict[str, Any]]:
    solar_start = min(max(timeline_start, 20.0), timeline_end)
    solar_end = min(max(solar_start + 15.0, solar_start), timeline_end)
    antenna_start = min(max(timeline_start, 45.0), timeline_end)
    antenna_end = min(max(antenna_start + 10.0, antenna_start), timeline_end)
    return [
        _event(
            "太阳翼展开",
            DEPLOYMENT_KIND,
            "SolarArrayDeploy",
            solar_start,
            solar_end,
            source="auto",
            properties={"subsystem": "solar_array"},
        ),
        _event(
            "通信天线展开",
            DEPLOYMENT_KIND,
            "AntennaDeploy",
            antenna_start,
            antenna_end,
            source="auto",
            properties={"subsystem": "communication_antenna"},
        ),
    ]


def _tracking_visibility_segments(result: TrackingArcOrbitResult | None) -> list[tuple[float, float, str]]:
    if result is None:
        return []
    t0 = parse_utc(result.t0_utc)
    segments: list[tuple[float, float, str]] = []
    for segment in result.segments:
        if segment.kind not in {"ground", "relay"}:
            continue
        start = (parse_utc(segment.start_utc) - t0).total_seconds() / 60.0
        end = (parse_utc(segment.end_utc) - t0).total_seconds() / 60.0
        if end > start:
            segments.append((start, end, segment.row_label))
    segments.sort(key=lambda item: (item[0], item[1]))
    return segments


def _subtract_intervals(start: float, end: float, blockers: list[tuple[float, float]]) -> list[tuple[float, float]]:
    spans = [(start, end)]
    for block_start, block_end in sorted(blockers):
        next_spans: list[tuple[float, float]] = []
        for span_start, span_end in spans:
            if block_end <= span_start or block_start >= span_end:
                next_spans.append((span_start, span_end))
                continue
            if block_start > span_start:
                next_spans.append((span_start, min(block_start, span_end)))
            if block_end < span_end:
                next_spans.append((max(block_end, span_start), span_end))
        spans = next_spans
    return [(left, right) for left, right in spans if right > left]


def _complement_intervals(start: float, end: float, intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    if end <= start:
        return []
    merged: list[tuple[float, float]] = []
    for left, right in sorted((max(start, a), min(end, b)) for a, b in intervals if b > start and a < end):
        if not merged or left > merged[-1][1] + 1e-6:
            merged.append((left, right))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], right))
    gaps: list[tuple[float, float]] = []
    cursor = start
    for left, right in merged:
        if left > cursor:
            gaps.append((cursor, left))
        cursor = max(cursor, right)
    if cursor < end:
        gaps.append((cursor, end))
    return gaps


def _event_intersects_shadow(event: dict[str, Any], shadow_segments: list[TrackingArcSegment], t0_utc: object) -> bool:
    if not t0_utc:
        return False
    try:
        t0 = parse_utc(str(t0_utc))
    except Exception:
        return False
    event_start = float(event["start_min"])
    event_end = float(event["end_min"])
    for segment in shadow_segments:
        start = (parse_utc(segment.start_utc) - t0).total_seconds() / 60.0
        end = (parse_utc(segment.end_utc) - t0).total_seconds() / 60.0
        if event_start <= end and event_end >= start:
            return True
    return False


def _plus_z_for_mode(
    *,
    mode: str,
    timeline: dict[str, Any],
    sun_vectors: np.ndarray,
    index: int,
    t0_utc: datetime,
    maneuvers: list[ManeuverInterval],
    afm_plus_z_ecef: np.ndarray | None = None,
) -> np.ndarray:
    afm_reference = afm_plus_z_ecef
    if mode == MODE_EPM:
        return _normalize_vector(-np.asarray(timeline["positions"][index], dtype=np.float64))
    if mode == MODE_AFM:
        if afm_reference is None:
            afm_reference = _body_plus_z_ecef_for_attitude(t0_utc, timeline, maneuvers, sun_vectors)
        return _normalize_vector(afm_reference[index])
    if mode == MODE_TRANSITION:
        spm = _normalize_vector(-sun_vectors[index])
        if afm_reference is None:
            afm_reference = _body_plus_z_ecef_for_attitude(t0_utc, timeline, maneuvers, sun_vectors)
        afm = afm_reference[index]
        return _normalize_vector(spm + afm)
    return _normalize_vector(-sun_vectors[index])


def _resolve_t0_utc(
    t0_utc: str | datetime | None,
    payload: dict[str, Any],
    maneuver_strategy: dict[str, Any],
) -> datetime:
    if isinstance(t0_utc, datetime):
        return t0_utc
    if t0_utc:
        return parse_utc(str(t0_utc))
    selected = str(payload.get("selected_t0_utc", "") or "")
    if selected:
        return parse_utc(selected)
    reference = _reference_t0_utc_from_strategy(maneuver_strategy)
    if reference is not None:
        return reference
    return parse_utc(format_utc(datetime.utcnow()))


def _normalize_vector(vector: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vector))
    if norm <= 1e-12 or not math.isfinite(norm):
        return np.asarray([0.0, 0.0, 1.0], dtype=np.float64)
    return np.asarray(vector, dtype=np.float64) / norm


def _finite_float(value: object, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float(default)
    return parsed if math.isfinite(parsed) else float(default)
