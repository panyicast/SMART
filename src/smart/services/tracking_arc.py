from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
import zipfile
import xml.etree.ElementTree as ET

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
_BEIJING_TZ_OFFSET = timedelta(hours=8)
_XLSX_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_XLSX_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


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


def export_tracking_arc_results_xlsx(results: list[TrackingArcOrbitResult], path: str | Path) -> Path:
    if not results:
        raise ValueError("No tracking arc results to export.")
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.suffix.lower() != ".xlsx":
        output_path = output_path.with_suffix(".xlsx")
    rows = _tracking_arc_export_rows(results)
    _write_tracking_arc_xlsx(output_path, "跟踪弧段结果", rows)
    return output_path


def compute_tracking_arcs_for_window(
    *,
    orbit_history_csv: str | Path,
    maneuver_strategy: dict[str, Any],
    config: LaunchWindowConfig,
    window: LaunchWindowResult,
    assets: list[TrackingAsset] | None = None,
) -> list[TrackingArcOrbitResult]:
    timeline, maneuvers, tracking_assets = _prepare_tracking_timeline(
        orbit_history_csv=orbit_history_csv,
        maneuver_strategy=maneuver_strategy,
        config=config,
        assets=assets,
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


def compute_tracking_arc_for_launch_time(
    *,
    orbit_history_csv: str | Path,
    maneuver_strategy: dict[str, Any],
    config: LaunchWindowConfig,
    launch_utc: str | datetime,
    assets: list[TrackingAsset] | None = None,
    point_key: str = "manual",
    point_label: str = "指定发射时刻轨道",
) -> TrackingArcOrbitResult:
    timeline, maneuvers, tracking_assets = _prepare_tracking_timeline(
        orbit_history_csv=orbit_history_csv,
        maneuver_strategy=maneuver_strategy,
        config=config,
        assets=assets,
    )
    return _compute_tracking_arc_for_launch(
        point_key=point_key,
        point_label=point_label,
        launch_utc=parse_utc(launch_utc),
        rocket_flight_time_s=config.rocket_flight_time_s,
        timeline=timeline,
        maneuvers=maneuvers,
        assets=tracking_assets,
        config=config,
    )


def _prepare_tracking_timeline(
    *,
    orbit_history_csv: str | Path,
    maneuver_strategy: dict[str, Any],
    config: LaunchWindowConfig,
    assets: list[TrackingAsset] | None = None,
) -> tuple[dict[str, Any], list[ManeuverInterval], list[TrackingAsset]]:
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
    return timeline, maneuvers, tracking_assets


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


def _tracking_arc_export_rows(results: list[TrackingArcOrbitResult]) -> list[list[Any]]:
    rows: list[list[Any]] = [
        ["轨道汇总"],
        [
            "轨道",
            "发射时刻(UTC)",
            "发射时刻(北京时间)",
            "入轨T0(UTC)",
            "入轨T0(北京时间)",
            "时间线开始(北京时间)",
            "时间线结束(北京时间)",
            "地影总时长/min",
            "点火次数",
        ],
    ]
    for result in results:
        rows.append(
            [
                result.point_label,
                result.launch_utc,
                _format_beijing_export(result.launch_utc),
                result.t0_utc,
                _format_beijing_export(result.t0_utc),
                _format_beijing_export(result.timeline_start_utc),
                _format_beijing_export(result.timeline_end_utc),
                round(result.shadow_total_min, 6),
                result.maneuver_count,
            ]
        )

    rows.extend(
        [
            [],
            ["资源汇总"],
            ["轨道", "类型", "资源", "跟踪段数", "总时长/min", "最长连续/min"],
        ]
    )
    for result in results:
        for summary in result.asset_summaries:
            rows.append(
                [
                    result.point_label,
                    "地面站" if summary.asset_type == "ground" else "中继星",
                    summary.name,
                    summary.interval_count,
                    round(summary.total_duration_min, 6),
                    round(summary.longest_duration_min, 6),
                ]
            )

    rows.extend(
        [
            [],
            ["弧段明细"],
            [
                "轨道",
                "行",
                "类型",
                "开始(UTC)",
                "开始(北京时间)",
                "开始航时(min)",
                "结束(UTC)",
                "结束(北京时间)",
                "结束航时(min)",
                "时长/min",
                "提示",
            ],
        ]
    )
    for result in results:
        t0_utc = parse_utc(result.t0_utc)
        for segment in result.segments:
            start_elapsed_min = (parse_utc(segment.start_utc) - t0_utc).total_seconds() / 60.0
            end_elapsed_min = (parse_utc(segment.end_utc) - t0_utc).total_seconds() / 60.0
            rows.append(
                [
                    result.point_label,
                    segment.row_label,
                    _segment_kind_label(segment.kind),
                    segment.start_utc,
                    _format_beijing_export(segment.start_utc),
                    round(start_elapsed_min, 6),
                    segment.end_utc,
                    _format_beijing_export(segment.end_utc),
                    round(end_elapsed_min, 6),
                    round(_segment_duration_min(segment), 6),
                    segment.tooltip,
                ]
            )
    return rows


def _segment_kind_label(kind: str) -> str:
    return {
        "burn": "点火",
        "ground": "地面站",
        "relay": "中继星",
        "shadow": "地影",
    }.get(kind, kind)


def _format_beijing_export(value: str | datetime) -> str:
    utc_value = parse_utc(value)
    return (utc_value + _BEIJING_TZ_OFFSET).strftime("%Y-%m-%d %H:%M:%S")


def _write_tracking_arc_xlsx(path: Path, sheet_name: str, rows: list[list[Any]]) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _xlsx_content_types_xml())
        archive.writestr("_rels/.rels", _xlsx_root_rels_xml())
        archive.writestr("xl/workbook.xml", _xlsx_workbook_xml(sheet_name))
        archive.writestr("xl/_rels/workbook.xml.rels", _xlsx_workbook_rels_xml())
        archive.writestr("xl/styles.xml", _xlsx_styles_xml())
        archive.writestr("xl/worksheets/sheet1.xml", _xlsx_sheet_xml(rows))


def _xlsx_sheet_xml(rows: list[list[Any]]) -> str:
    ET.register_namespace("", _XLSX_NS)
    worksheet = ET.Element(f"{{{_XLSX_NS}}}worksheet")
    max_columns = max((len(row) for row in rows), default=1)
    ET.SubElement(worksheet, f"{{{_XLSX_NS}}}dimension", {"ref": f"A1:{_xlsx_col_name(max_columns)}{max(len(rows), 1)}"})
    sheet_views = ET.SubElement(worksheet, f"{{{_XLSX_NS}}}sheetViews")
    ET.SubElement(sheet_views, f"{{{_XLSX_NS}}}sheetView", {"workbookViewId": "0"})
    cols = ET.SubElement(worksheet, f"{{{_XLSX_NS}}}cols")
    ET.SubElement(cols, f"{{{_XLSX_NS}}}col", {"min": "1", "max": "2", "width": "24", "customWidth": "1"})
    ET.SubElement(cols, f"{{{_XLSX_NS}}}col", {"min": "3", "max": str(max_columns), "width": "20", "customWidth": "1"})
    sheet_data = ET.SubElement(worksheet, f"{{{_XLSX_NS}}}sheetData")
    for row_index, values in enumerate(rows, start=1):
        row = ET.SubElement(sheet_data, f"{{{_XLSX_NS}}}row", {"r": str(row_index)})
        section_row = len(values) == 1 and bool(values[0])
        for col_index, value in enumerate(values, start=1):
            style_id = 1 if section_row or row_index in {2, 6} or (row_index > 6 and values and values[0] == "轨道") else 2
            _append_xlsx_cell(row, row_index, col_index, value, style_id=style_id)
    return ET.tostring(worksheet, encoding="utf-8", xml_declaration=True).decode("utf-8")


def _append_xlsx_cell(row: ET.Element, row_index: int, col_index: int, value: Any, *, style_id: int) -> None:
    cell = ET.SubElement(row, f"{{{_XLSX_NS}}}c", {"r": f"{_xlsx_col_name(col_index)}{row_index}", "s": str(style_id)})
    if value is None or value == "":
        return
    if isinstance(value, str):
        cell.set("t", "inlineStr")
        inline = ET.SubElement(cell, f"{{{_XLSX_NS}}}is")
        text = ET.SubElement(inline, f"{{{_XLSX_NS}}}t")
        text.text = value
        return
    value_node = ET.SubElement(cell, f"{{{_XLSX_NS}}}v")
    value_node.text = f"{float(value):.12g}"


def _xlsx_col_name(index: int) -> str:
    name = ""
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def _xlsx_content_types_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
  <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
</Types>"""


def _xlsx_root_rels_xml() -> str:
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="{_REL_NS}">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""


def _xlsx_workbook_rels_xml() -> str:
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="{_REL_NS}">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>"""


def _xlsx_workbook_xml(sheet_name: str) -> str:
    escaped_name = sheet_name.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="{_XLSX_NS}" xmlns:r="{_XLSX_REL_NS}">
  <sheets><sheet name="{escaped_name}" sheetId="1" r:id="rId1"/></sheets>
</workbook>"""


def _xlsx_styles_xml() -> str:
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="{_XLSX_NS}">
  <fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts>
  <fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FFD9EAF7"/><bgColor indexed="64"/></patternFill></fill></fills>
  <borders count="2"><border><left/><right/><top/><bottom/><diagonal/></border><border><left style="thin"><color auto="1"/></left><right style="thin"><color auto="1"/></right><top style="thin"><color auto="1"/></top><bottom style="thin"><color auto="1"/></bottom><diagonal/></border></borders>
  <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
  <cellXfs count="3"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf><xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf></cellXfs>
  <cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>"""


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
