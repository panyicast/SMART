from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np

from smart.services.earth_orientation import format_utc, parse_utc
from smart.services.launch_window import (
    LaunchWindowConfig,
    LaunchWindowResult,
    ManeuverInterval,
    TrackingAsset,
    _build_timeline,
    _maneuver_intervals,
    _reference_t0_utc_from_strategy,
    _shadow_flags_and_margin,
    _sun_unit_ecef_for_elapsed,
    _theta_st_matrix_from_los,
    default_tracking_assets,
    load_orbit_history_rows,
    tracking_assets_from_config,
)


TRACKING_ARC_POINT_LEADING = "leading"
TRACKING_ARC_POINT_MIDPOINT = "midpoint"
TRACKING_ARC_POINT_TRAILING = "trailing"


@dataclass(frozen=True, slots=True)
class TrackingArcSegment:
    row_label: str
    start_utc: str
    end_utc: str
    kind: str
    tooltip: str


@dataclass(frozen=True, slots=True)
class TrackingArcAssetSummary:
    name: str
    asset_type: str
    interval_count: int
    total_duration_min: float
    longest_duration_min: float


@dataclass(frozen=True, slots=True)
class TrackingArcOrbitResult:
    point_key: str
    point_label: str
    launch_utc: str
    t0_utc: str
    timeline_start_utc: str
    timeline_end_utc: str
    row_labels: list[str]
    segments: list[TrackingArcSegment]
    asset_summaries: list[TrackingArcAssetSummary]
    shadow_total_min: float
    maneuver_count: int


def compute_tracking_arcs_for_window(
    *,
    orbit_history_csv: str | Path,
    maneuver_strategy: dict[str, Any],
    config: LaunchWindowConfig,
    window: LaunchWindowResult,
    assets: list[TrackingAsset] | None = None,
) -> list[TrackingArcOrbitResult]:
    rows = load_orbit_history_rows(orbit_history_csv)
    if assets is None:
        tracking_assets = tracking_assets_from_config(config)
    else:
        tracking_assets = assets
    if assets is None and not tracking_assets:
        tracking_assets = default_tracking_assets()

    maneuvers = _maneuver_intervals(maneuver_strategy)
    reference_t0_utc = _reference_t0_utc_from_strategy(maneuver_strategy)
    timeline = _build_timeline(
        rows,
        tracking_assets,
        maneuvers=maneuvers,
        reference_t0_utc=reference_t0_utc,
    )
    return [
        _compute_tracking_arc_for_launch(
            point_key=point_key,
            point_label=point_label,
            launch_utc=launch_utc,
            rocket_flight_time_s=config.rocket_flight_time_s,
            timeline=timeline,
            maneuvers=maneuvers,
            assets=tracking_assets,
            config=config,
        )
        for point_key, point_label, launch_utc in tracking_arc_launch_points(window)
    ]


def tracking_arc_launch_points(window: LaunchWindowResult) -> list[tuple[str, str, datetime]]:
    start_utc = parse_utc(window.window_start_utc)
    end_utc = parse_utc(window.window_end_utc)
    midpoint_utc = start_utc + (end_utc - start_utc) / 2
    return [
        (TRACKING_ARC_POINT_LEADING, "窗口前沿轨道", start_utc),
        (TRACKING_ARC_POINT_MIDPOINT, "窗口中点轨道", midpoint_utc),
        (TRACKING_ARC_POINT_TRAILING, "窗口后沿轨道", end_utc),
    ]


def _compute_tracking_arc_for_launch(
    *,
    point_key: str,
    point_label: str,
    launch_utc: datetime,
    rocket_flight_time_s: float,
    timeline: dict[str, Any],
    maneuvers: list[ManeuverInterval],
    assets: list[TrackingAsset],
    config: LaunchWindowConfig,
) -> TrackingArcOrbitResult:
    t0_utc = launch_utc + timedelta(seconds=float(rocket_flight_time_s))
    elapsed_min: np.ndarray = timeline["elapsed_min"]
    if elapsed_min.size == 0:
        raise ValueError("Tracking arc timeline is empty.")

    timeline_start = _epoch_from_elapsed(t0_utc, float(elapsed_min[0]))
    timeline_end = _epoch_from_elapsed(t0_utc, float(elapsed_min[-1]) + _infer_elapsed_step_min(elapsed_min))
    row_labels: list[str] = ["变轨点火时段"]
    segments: list[TrackingArcSegment] = []

    for maneuver in maneuvers:
        segment = _relative_interval_segment(
            row_label="变轨点火时段",
            start_min=maneuver.start_min,
            end_min=maneuver.end_min,
            t0_utc=t0_utc,
            kind="burn",
            tooltip_prefix=f"第 {maneuver.maneuver_index} 次变轨点火",
        )
        if segment is not None:
            segments.append(segment)

    sun_vectors = _sun_unit_ecef_for_elapsed(t0_utc, elapsed_min)
    shadow, _shadow_margin_m = _shadow_flags_and_margin(t0_utc, timeline, sun_vectors=sun_vectors)

    # Keep the same tracking attitude definition as launch-window analysis:
    # tracking antenna angle theta_st is measured against the anti-sun direction.
    theta_st_reference_ecef = -sun_vectors
    ground_theta_st_deg = _theta_st_matrix_from_los(timeline["ground_los_unit"], theta_st_reference_ecef)
    relay_theta_st_deg = _theta_st_matrix_from_los(timeline["relay_los_unit"], theta_st_reference_ecef)

    asset_summaries: list[TrackingArcAssetSummary] = []
    ground_indices: np.ndarray = timeline["ground_indices"]
    ground_elevation_deg: np.ndarray = timeline["ground_elevation_deg"]
    for column, asset_index in enumerate(ground_indices.tolist()):
        asset = assets[int(asset_index)]
        row_label = f"地面站 {asset.name}"
        row_labels.append(row_label)
        flags = (
            (ground_elevation_deg[:, column] >= config.ground_station_min_elevation_deg)
            & (ground_theta_st_deg[:, column] <= config.ground_station_max_theta_st_deg)
        )
        asset_segments = _flags_to_segments(
            row_label=row_label,
            elapsed_min=elapsed_min,
            flags=flags,
            t0_utc=t0_utc,
            kind="ground",
        )
        segments.extend(asset_segments)
        asset_summaries.append(_asset_summary(asset.name, "ground", asset_segments))

    relay_indices: np.ndarray = timeline["relay_indices"]
    relay_alpha_deg: np.ndarray = timeline["relay_alpha_deg"]
    relay_beta_deg: np.ndarray = timeline["relay_beta_deg"]
    for column, asset_index in enumerate(relay_indices.tolist()):
        asset = assets[int(asset_index)]
        row_label = f"中继星 {asset.name}"
        row_labels.append(row_label)
        flags = (
            (np.abs(relay_alpha_deg[:, column]) <= config.relay_alpha_abs_max_deg)
            & (np.abs(relay_beta_deg[:, column]) <= config.relay_beta_abs_max_deg)
            & (relay_theta_st_deg[:, column] <= config.relay_max_theta_st_deg)
        )
        asset_segments = _flags_to_segments(
            row_label=row_label,
            elapsed_min=elapsed_min,
            flags=flags,
            t0_utc=t0_utc,
            kind="relay",
        )
        segments.extend(asset_segments)
        asset_summaries.append(_asset_summary(asset.name, "relay", asset_segments))

    shadow_label = "卫星地影时段"
    row_labels.append(shadow_label)
    shadow_segments = _flags_to_segments(
        row_label=shadow_label,
        elapsed_min=elapsed_min,
        flags=shadow,
        t0_utc=t0_utc,
        kind="shadow",
    )
    segments.extend(shadow_segments)

    return TrackingArcOrbitResult(
        point_key=point_key,
        point_label=point_label,
        launch_utc=format_utc(launch_utc),
        t0_utc=format_utc(t0_utc),
        timeline_start_utc=format_utc(timeline_start),
        timeline_end_utc=format_utc(timeline_end),
        row_labels=row_labels,
        segments=segments,
        asset_summaries=asset_summaries,
        shadow_total_min=_segments_total_duration_min(shadow_segments),
        maneuver_count=len(maneuvers),
    )


def _flags_to_segments(
    *,
    row_label: str,
    elapsed_min: np.ndarray,
    flags: np.ndarray,
    t0_utc: datetime,
    kind: str,
) -> list[TrackingArcSegment]:
    if elapsed_min.size == 0:
        return []
    values = np.asarray(flags, dtype=bool)
    if values.size != elapsed_min.size:
        raise ValueError("Tracking flag series length does not match the timeline.")

    step_min = _infer_elapsed_step_min(elapsed_min)
    intervals: list[tuple[float, float]] = []
    active_start: float | None = None
    active_end: float | None = None
    for index, flag in enumerate(values):
        if not bool(flag):
            if active_start is not None and active_end is not None:
                intervals.append((active_start, active_end))
                active_start = None
                active_end = None
            continue
        start_min = float(elapsed_min[index])
        end_min = float(elapsed_min[index + 1]) if index + 1 < elapsed_min.size else start_min + step_min
        if end_min <= start_min:
            end_min = start_min + step_min
        if active_start is None:
            active_start = start_min
            active_end = end_min
        elif active_end is not None and abs(start_min - active_end) <= 1e-6:
            active_end = end_min
        else:
            intervals.append((active_start, active_end))
            active_start = start_min
            active_end = end_min

    if active_start is not None and active_end is not None:
        intervals.append((active_start, active_end))

    return [
        _relative_interval_segment(
            row_label=row_label,
            start_min=start_min,
            end_min=end_min,
            t0_utc=t0_utc,
            kind=kind,
            tooltip_prefix=row_label,
        )
        for start_min, end_min in intervals
        if end_min > start_min
    ]


def _relative_interval_segment(
    *,
    row_label: str,
    start_min: float,
    end_min: float,
    t0_utc: datetime,
    kind: str,
    tooltip_prefix: str,
) -> TrackingArcSegment | None:
    if end_min <= start_min:
        return None
    start_utc = _epoch_from_elapsed(t0_utc, start_min)
    end_utc = _epoch_from_elapsed(t0_utc, end_min)
    duration_min = max(0.0, (end_utc - start_utc).total_seconds() / 60.0)
    tooltip = f"{tooltip_prefix}\n{format_utc(start_utc)} - {format_utc(end_utc)}\n{duration_min:.1f} min"
    return TrackingArcSegment(
        row_label=row_label,
        start_utc=format_utc(start_utc),
        end_utc=format_utc(end_utc),
        kind=kind,
        tooltip=tooltip,
    )


def _asset_summary(name: str, asset_type: str, segments: list[TrackingArcSegment]) -> TrackingArcAssetSummary:
    durations = [_segment_duration_min(segment) for segment in segments]
    return TrackingArcAssetSummary(
        name=name,
        asset_type=asset_type,
        interval_count=len(segments),
        total_duration_min=float(sum(durations)),
        longest_duration_min=float(max(durations, default=0.0)),
    )


def _segments_total_duration_min(segments: list[TrackingArcSegment]) -> float:
    return float(sum(_segment_duration_min(segment) for segment in segments))


def _segment_duration_min(segment: TrackingArcSegment) -> float:
    start_utc = parse_utc(segment.start_utc)
    end_utc = parse_utc(segment.end_utc)
    return max(0.0, (end_utc - start_utc).total_seconds() / 60.0)


def _epoch_from_elapsed(t0_utc: datetime, elapsed_min: float) -> datetime:
    return t0_utc + timedelta(minutes=float(elapsed_min))


def _infer_elapsed_step_min(elapsed_min: np.ndarray) -> float:
    if elapsed_min.size <= 1:
        return 1.0
    diffs = np.diff(elapsed_min)
    positive = diffs[diffs > 1e-9]
    if positive.size == 0:
        return 1.0
    return float(np.median(positive))
