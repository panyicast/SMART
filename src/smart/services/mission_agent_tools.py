from __future__ import annotations

import csv
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

from smart.services.earth_orientation import format_utc, parse_utc
from smart.services.design_maneuver_strategy import (
    continuous_thrust_result_to_maneuver_strategy_payload,
    continuous_thrust_result_to_payload,
    default_design_maneuver_strategy_payload,
    design_maneuver_result_from_payload,
    design_maneuver_result_to_payload,
    export_continuous_thrust_orbit_history_csv,
    optimize_continuous_thrust_model_parameters,
    plan_design_maneuver_strategy,
)
from smart.services.launch_window import (
    LaunchWindowResult,
    compute_shadow_intervals_for_launch,
    compute_launch_windows,
    config_from_payload,
    default_launch_window_config,
    tracking_assets_from_config,
)
from smart.services.mission_agent import resolve_stk_help_tool
from smart.services.project_ai_context import build_project_analysis_context


BJT = timezone(timedelta(hours=8))


class MissionAgentToolError(RuntimeError):
    pass


class MissionAgentToolExecutor:
    def __init__(self, project_root: str | Path) -> None:
        self.project_root = Path(project_root).expanduser().resolve()

    def tool_specs(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "build_project_context",
                    "description": "构建当前 SMART 项目的摘要上下文，包含配置、关键 CSV 统计和文件清单。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "row_limit": {
                                "type": "integer",
                                "description": "每个 CSV 文件最多抽样行数。",
                                "minimum": 1,
                                "maximum": 20,
                                "default": 6,
                            }
                        },
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "find_launch_windows",
                    "description": "从 data/launch_window_results.csv 中查找指定北京时间日期的发射窗口。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "date_bjt": {
                                "type": "string",
                                "description": "北京时间日期，格式 YYYY-MM-DD，例如 2026-05-25。",
                            }
                        },
                        "required": ["date_bjt"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "compute_shadow_intervals_for_launch",
                    "description": (
                        "基于 data/full_orbit_history.csv 和 SMART 地影几何算法计算指定发射时刻的全部地影区间。"
                        "必须提供 launch_utc 或 launch_bjt。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "launch_utc": {
                                "type": "string",
                                "description": "发射时刻 UTC，例如 2026-05-25T07:30:00Z。",
                            },
                            "launch_bjt": {
                                "type": "string",
                                "description": "发射时刻北京时间，例如 2026-05-25 15:30:00。",
                            },
                            "rocket_flight_time_s": {
                                "type": "number",
                                "description": "火箭飞行时间秒；不填则读取 config/launch_window.json。",
                            },
                        },
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "plan_design_maneuver_strategy",
                    "description": (
                        "调用设计变轨策略页面同源脉冲变轨算法，生成 delta-v、点火时刻、经度和约束检查。"
                        "默认只返回结果；save_result=true 时写入 data/design_maneuver_results.json。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "config_override": {
                                "type": "object",
                                "description": "可选配置覆盖，按 config/design_maneuver_strategy.json 的结构递归合并。",
                                "additionalProperties": True,
                            },
                            "save_result": {
                                "type": "boolean",
                                "description": "是否保存结果到项目 data 目录。默认 false。",
                                "default": False,
                            },
                        },
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "optimize_design_continuous_thrust",
                    "description": (
                        "调用连续推力设计优化算法。优先复用已归档脉冲变轨结果；"
                        "save_result=true 时写入连续推力结果、轨道历史 CSV 和可导入变轨策略配置。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "use_archived_pulse_result": {
                                "type": "boolean",
                                "description": "存在 data/design_maneuver_results.json 时是否直接加载。默认 true。",
                                "default": True,
                            },
                            "save_result": {
                                "type": "boolean",
                                "description": "是否保存连续推力优化产物。默认 false。",
                                "default": False,
                            },
                        },
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "compute_launch_window_samples",
                    "description": (
                        "调用发射窗口页面同源算法，基于 data/full_orbit_history.csv、"
                        "config/maneuver_strategy.json 和发射窗口配置重新采样并合并窗口。"
                        "默认只返回摘要；save_result=true 时写入 samples/results CSV。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "config_override": {
                                "type": "object",
                                "description": "可选发射窗口配置覆盖，按 config/launch_window.json 的结构递归合并。",
                                "additionalProperties": True,
                            },
                            "sample_preview_limit": {
                                "type": "integer",
                                "description": "返回样本预览行数上限。默认 5。",
                                "minimum": 0,
                                "maximum": 20,
                                "default": 5,
                            },
                            "save_result": {
                                "type": "boolean",
                                "description": "是否保存 launch_window_samples.csv 和 launch_window_results.csv。默认 false。",
                                "default": False,
                            },
                        },
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "inspect_project_files",
                    "description": "检查当前项目 config/data/charts 下的文件是否存在，并返回文件大小。",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "query_stk_help",
                    "description": "查询本机 STK 11.6 全局帮助 KB，用于复核 Connect 命令和 STK API 行为。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "STK 11.6 帮助查询关键词。",
                            }
                        },
                        "required": ["query"],
                        "additionalProperties": False,
                    },
                },
            },
        ]

    def execute(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name == "build_project_context":
            return self._build_project_context(arguments)
        if name == "find_launch_windows":
            return self._find_launch_windows(arguments)
        if name == "compute_shadow_intervals_for_launch":
            return self._compute_shadow_intervals_for_launch(arguments)
        if name == "plan_design_maneuver_strategy":
            return self._plan_design_maneuver_strategy(arguments)
        if name == "optimize_design_continuous_thrust":
            return self._optimize_design_continuous_thrust(arguments)
        if name == "compute_launch_window_samples":
            return self._compute_launch_window_samples(arguments)
        if name == "inspect_project_files":
            return self._inspect_project_files()
        if name == "query_stk_help":
            return self._query_stk_help(arguments)
        raise MissionAgentToolError(f"Unknown SMART agent tool: {name}")

    def _build_project_context(self, arguments: dict[str, Any]) -> dict[str, Any]:
        row_limit = int(arguments.get("row_limit", 6) or 6)
        context = build_project_analysis_context(self.project_root, row_limit=max(1, min(20, row_limit)))
        return {
            "project_root": str(self.project_root),
            "char_count": len(context),
            "context": context,
        }

    def _find_launch_windows(self, arguments: dict[str, Any]) -> dict[str, Any]:
        date_bjt = str(arguments.get("date_bjt", "")).strip()
        if not date_bjt:
            raise MissionAgentToolError("date_bjt is required.")
        results_path = self.project_root / "data" / "launch_window_results.csv"
        config_path = self.project_root / "config" / "launch_window.json"
        rows: list[dict[str, str]] = []
        if results_path.exists():
            with results_path.open("r", encoding="utf-8-sig", newline="") as handle:
                for row in csv.DictReader(handle):
                    leading = str(row.get("窗口前沿 (北京时间)", "")).strip()
                    if leading.startswith(date_bjt):
                        rows.append({key: str(value or "") for key, value in row.items()})
        config_summary: dict[str, Any] = {}
        if config_path.exists():
            payload = json.loads(config_path.read_text(encoding="utf-8-sig"))
            config_summary = {
                "start_utc": payload.get("start_utc"),
                "end_utc": payload.get("end_utc"),
                "sample_step_min": payload.get("sample_step_min"),
                "rocket_flight_time_s": payload.get("rocket_flight_time_s"),
            }
        return {
            "date_bjt": date_bjt,
            "results_path": str(results_path),
            "match_count": len(rows),
            "windows": rows,
            "launch_window_config": config_summary,
            "note": (
                "未在 cached launch_window_results.csv 中找到该北京时间日期的窗口；"
                "如需按该日期计算，请先扩展发射窗口配置并重新生成缓存，或直接指定 launch_utc/launch_bjt。"
                if not rows
                else ""
            ),
        }

    def _compute_shadow_intervals_for_launch(self, arguments: dict[str, Any]) -> dict[str, Any]:
        launch_utc = _launch_utc_from_arguments(arguments)
        rocket_flight_time_s = _rocket_flight_time_s(self.project_root, arguments)
        orbit_history_csv = self.project_root / "data" / "full_orbit_history.csv"
        if not orbit_history_csv.exists():
            raise MissionAgentToolError(f"Missing orbit history CSV: {orbit_history_csv}")

        intervals = compute_shadow_intervals_for_launch(
            orbit_history_csv=orbit_history_csv,
            launch_utc=launch_utc,
            rocket_flight_time_s=rocket_flight_time_s,
        )
        launch_dt = parse_utc(launch_utc)
        t0_utc = launch_dt + timedelta(seconds=float(rocket_flight_time_s))
        interval_rows: list[dict[str, Any]] = []
        for index, interval in enumerate(intervals, start=1):
            start_utc = t0_utc + timedelta(minutes=float(interval.exact_start_min))
            end_utc = t0_utc + timedelta(minutes=float(interval.exact_end_min))
            duration_min = max(0.0, (end_utc - start_utc).total_seconds() / 60.0)
            interval_rows.append(
                {
                    "index": index,
                    "start_utc": format_utc(start_utc),
                    "end_utc": format_utc(end_utc),
                    "start_bjt": _format_bjt(start_utc),
                    "end_bjt": _format_bjt(end_utc),
                    "start_min_from_t0": float(interval.exact_start_min),
                    "end_min_from_t0": float(interval.exact_end_min),
                    "duration_min": duration_min,
                    "sample_start_min": interval.start_min,
                    "sample_end_min": interval.end_min,
                }
            )
        durations = [float(row["duration_min"]) for row in interval_rows]
        return {
            "launch_utc": format_utc(launch_dt),
            "launch_bjt": _format_bjt(launch_dt),
            "rocket_flight_time_s": float(rocket_flight_time_s),
            "t0_utc": format_utc(t0_utc),
            "t0_bjt": _format_bjt(t0_utc),
            "orbit_history_csv": str(orbit_history_csv),
            "interval_count": len(interval_rows),
            "total_shadow_min": float(sum(durations)),
            "longest_shadow_min": float(max(durations, default=0.0)),
            "intervals": interval_rows,
            "raw_intervals": [asdict(item) for item in intervals],
        }

    def _plan_design_maneuver_strategy(self, arguments: dict[str, Any]) -> dict[str, Any]:
        payload = _design_maneuver_config_payload(self.project_root, arguments)
        result = plan_design_maneuver_strategy(payload)

        saved_path = ""
        if bool(arguments.get("save_result", False)):
            result_path = self.project_root / "data" / "design_maneuver_results.json"
            _write_project_json(result_path, design_maneuver_result_to_payload(result))
            saved_path = str(result_path)

        return {
            "project_root": str(self.project_root),
            "config_path": str(self.project_root / "config" / "design_maneuver_strategy.json"),
            "saved_path": saved_path,
            "summary": dict(result.summary),
            "burn_count": len(result.burns),
            "burns": [_summarize_design_burn(burn) for burn in result.burns],
            "checks": [dict(item) for item in result.checks],
            "warnings": list(result.warnings),
        }

    def _optimize_design_continuous_thrust(self, arguments: dict[str, Any]) -> dict[str, Any]:
        pulse_result_path = self.project_root / "data" / "design_maneuver_results.json"
        use_archived = bool(arguments.get("use_archived_pulse_result", True))
        if use_archived and pulse_result_path.exists():
            pulse_result = design_maneuver_result_from_payload(_read_project_json(pulse_result_path))
            pulse_source = str(pulse_result_path)
        else:
            pulse_payload = _design_maneuver_config_payload(self.project_root, {})
            pulse_result = plan_design_maneuver_strategy(pulse_payload)
            pulse_source = "computed_from_config"

        result = optimize_continuous_thrust_model_parameters(pulse_result)
        saved_paths: dict[str, str] = {}
        if bool(arguments.get("save_result", False)):
            result_path = self.project_root / "data" / "design_continuous_thrust_results.json"
            history_path = self.project_root / "data" / "design_continuous_thrust_orbit_history.csv"
            strategy_path = self.project_root / "config" / "design_import_maneuver_strategy.json"
            _write_project_json(result_path, continuous_thrust_result_to_payload(result))
            export_continuous_thrust_orbit_history_csv(result, history_path)
            _write_project_json(
                strategy_path,
                continuous_thrust_result_to_maneuver_strategy_payload(result, pulse_result.config),
            )
            saved_paths = {
                "result_json": str(result_path),
                "orbit_history_csv": str(history_path),
                "maneuver_strategy_json": str(strategy_path),
            }

        return {
            "project_root": str(self.project_root),
            "pulse_result_source": pulse_source,
            "saved_paths": saved_paths,
            "hard_constraint_passed": bool(result.hard_constraint_passed),
            "failed_constraints": list(result.failed_constraints),
            "parameter_count": len(result.parameters),
            "total_propellant_kg": float(result.total_propellant_kg),
            "objective_delta_g_kg": float(result.objective_delta_g_kg),
            "time_step_s": float(result.time_step_s),
            "yaw_step_deg": float(result.yaw_step_deg),
            "orbit_history_row_count": len(result.orbit_history_rows),
            "parameters": [_summarize_continuous_parameter(item) for item in result.parameters],
        }

    def _compute_launch_window_samples(self, arguments: dict[str, Any]) -> dict[str, Any]:
        orbit_history_csv = self.project_root / "data" / "full_orbit_history.csv"
        strategy_path = self.project_root / "config" / "maneuver_strategy.json"
        if not orbit_history_csv.exists():
            raise MissionAgentToolError(f"Missing orbit history CSV: {orbit_history_csv}")
        if not strategy_path.exists():
            raise MissionAgentToolError(f"Missing maneuver strategy config: {strategy_path}")

        config_payload = _launch_window_config_payload(self.project_root, arguments)
        config = config_from_payload(config_payload)
        maneuver_strategy = _read_project_json(strategy_path)
        windows, samples = compute_launch_windows(
            orbit_history_csv=orbit_history_csv,
            maneuver_strategy=maneuver_strategy,
            config=config,
            assets=tracking_assets_from_config(config),
        )

        saved_paths: dict[str, str] = {}
        if bool(arguments.get("save_result", False)):
            samples_path = self.project_root / "data" / "launch_window_samples.csv"
            results_path = self.project_root / "data" / "launch_window_results.csv"
            _write_launch_window_samples_csv(samples_path, samples, config.rocket_flight_time_s)
            _write_launch_window_results_csv(results_path, windows, config.rocket_flight_time_s)
            saved_paths = {
                "samples_csv": str(samples_path),
                "results_csv": str(results_path),
            }

        preview_limit = max(0, min(20, int(arguments.get("sample_preview_limit", 5) or 5)))
        return {
            "project_root": str(self.project_root),
            "orbit_history_csv": str(orbit_history_csv),
            "maneuver_strategy_path": str(strategy_path),
            "saved_paths": saved_paths,
            "sample_count": len(samples),
            "pass_sample_count": sum(1 for item in samples if bool(item.get("ok"))),
            "window_count": len(windows),
            "windows": [_summarize_launch_window(window) for window in windows],
            "sample_preview": samples[:preview_limit],
            "config_summary": {
                "start_utc": config.start_utc,
                "end_utc": config.end_utc,
                "sample_step_min": config.sample_step_min,
                "rocket_flight_time_s": config.rocket_flight_time_s,
                "min_window_duration_min": config.min_window_duration_min,
            },
        }

    def _inspect_project_files(self) -> dict[str, Any]:
        inventory: dict[str, list[dict[str, Any]]] = {}
        for folder_name in ("config", "data", "charts"):
            folder = self.project_root / folder_name
            files: list[dict[str, Any]] = []
            if folder.exists():
                for path in sorted(item for item in folder.rglob("*") if item.is_file()):
                    files.append(
                        {
                            "path": path.relative_to(self.project_root).as_posix(),
                            "size_bytes": path.stat().st_size,
                        }
                    )
            inventory[folder_name] = files
        return {"project_root": str(self.project_root), "inventory": inventory}

    def _query_stk_help(self, arguments: dict[str, Any]) -> dict[str, Any]:
        query = str(arguments.get("query", "")).strip()
        if not query:
            raise MissionAgentToolError("query is required.")
        status = resolve_stk_help_tool()
        command = status.command_for_query(query)
        if not status.available or command is None:
            return {
                "query": query,
                "available": False,
                "kb_path": str(status.kb_path),
                "script_path": "" if status.script_path is None else str(status.script_path),
                "config_path": str(status.config_path),
                "config_loaded": status.config_loaded,
                "fallback_command": status.display_command(query),
                "output": f"STK help tool unavailable: {status.reason}.",
            }
        if status.script_path is not None:
            command = [sys.executable, *command]
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            check=False,
        )
        return {
            "query": query,
            "available": completed.returncode == 0,
            "returncode": completed.returncode,
            "kb_path": str(status.kb_path),
            "script_path": "" if status.script_path is None else str(status.script_path),
            "config_path": str(status.config_path),
            "config_loaded": status.config_loaded,
            "command": status.display_command(query),
            "output": (completed.stdout or completed.stderr).strip(),
        }


def _read_project_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        raise
    except Exception as exc:
        raise MissionAgentToolError(f"Failed to read JSON: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise MissionAgentToolError(f"JSON root must be an object: {path}")
    return payload


def _write_project_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _object_argument(arguments: dict[str, Any], key: str) -> dict[str, Any]:
    value = arguments.get(key)
    if value in (None, ""):
        return {}
    if not isinstance(value, dict):
        raise MissionAgentToolError(f"{key} must be an object.")
    return value


def _deep_merge_dict(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = _deep_merge_dict(current, value)
        else:
            merged[key] = value
    return merged


def _design_maneuver_config_payload(project_root: Path, arguments: dict[str, Any]) -> dict[str, Any]:
    config_path = project_root / "config" / "design_maneuver_strategy.json"
    payload = _read_project_json(config_path) if config_path.exists() else default_design_maneuver_strategy_payload()
    return _deep_merge_dict(payload, _object_argument(arguments, "config_override"))


def _launch_window_config_payload(project_root: Path, arguments: dict[str, Any]) -> dict[str, Any]:
    config_path = project_root / "config" / "launch_window.json"
    payload = _read_project_json(config_path) if config_path.exists() else default_launch_window_config()
    return _deep_merge_dict(payload, _object_argument(arguments, "config_override"))


def _summarize_design_burn(burn: Any) -> dict[str, Any]:
    return {
        "index": int(burn.index),
        "burn_type": str(burn.burn_type),
        "apsis": str(burn.apsis),
        "elapsed_min": float(burn.elapsed_min),
        "beijing_time": str(burn.beijing_time),
        "longitude_deg_e": float(burn.longitude_deg_e),
        "delta_v_mps": float(burn.delta_v_mps),
        "alpha_deg": float(burn.alpha_deg),
        "total_burn_time_min": float(burn.total_burn_time_min),
        "propellant_kg": float(burn.propellant_kg),
        "post_a_km": float(burn.post_a_km),
        "post_e": float(burn.post_e),
        "post_i_deg": float(burn.post_i_deg),
        "duration_ok": bool(burn.duration_ok),
        "longitude_ok": bool(burn.longitude_ok),
    }


def _summarize_continuous_parameter(parameter: Any) -> dict[str, Any]:
    return {
        "maneuver_index": int(parameter.maneuver_index),
        "flight_revolution": int(parameter.flight_revolution),
        "position_label": str(parameter.position_label),
        "burn_start_min": float(parameter.burn_start_min),
        "settle_end_min": float(parameter.settle_end_min),
        "cutoff_min": float(parameter.cutoff_min),
        "yaw_angle_deg": float(parameter.yaw_angle_deg),
        "ignition_longitude_deg_e": float(parameter.ignition_longitude_deg_e),
        "cutoff_longitude_deg_e": float(parameter.cutoff_longitude_deg_e),
        "delta_v_mps": float(parameter.delta_v_mps),
        "total_burn_time_min": float(parameter.total_burn_time_min),
        "propellant_kg": float(parameter.propellant_kg),
        "objective_delta_g_kg": float(parameter.objective_delta_g_kg),
        "post_a_km": float(parameter.post_a_km),
        "post_e": float(parameter.post_e),
        "post_i_deg": float(parameter.post_i_deg),
        "duration_ok": bool(parameter.duration_ok),
        "longitude_ok": bool(parameter.longitude_ok),
        "optimization_mode": str(parameter.optimization_mode),
    }


def _summarize_launch_window(window: LaunchWindowResult) -> dict[str, Any]:
    start = parse_utc(window.window_start_utc)
    end = parse_utc(window.window_end_utc)
    return {
        "window_start_utc": format_utc(start),
        "window_end_utc": format_utc(end),
        "window_start_bjt": _format_bjt(start),
        "window_end_bjt": _format_bjt(end),
        "duration_min": float(window.duration_min),
        "first_failure": str(window.first_failure),
        "first_orbit_shadow_min": float(window.first_orbit_shadow_min),
        "no_shadow_period_shadow_min": float(window.no_shadow_period_shadow_min),
        "separation_shadow_min": float(window.separation_shadow_min),
        "min_burn_sun_margin_deg": float(window.min_burn_sun_margin_deg),
        "max_tracking_gap_min": float(window.max_tracking_gap_min),
        "inclination_deg": float(window.inclination_deg),
        "window_start_longest_shadow_min": float(window.window_start_longest_shadow_min),
        "window_end_longest_shadow_min": float(window.window_end_longest_shadow_min),
        "window_start_constraint": str(window.window_start_constraint or ""),
        "window_end_constraint": str(window.window_end_constraint or ""),
    }


def _write_launch_window_samples_csv(path: Path, samples: list[dict[str, Any]], rocket_flight_time_s: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "launch_utc",
        "t0_utc",
        "rocket_flight_time_s",
        "ok",
        "failure",
        "first_orbit_shadow_min",
        "no_shadow_period_shadow_min",
        "separation_shadow_min",
        "longest_shadow_min",
        "min_burn_sun_margin_deg",
        "max_tracking_gap_min",
        "inclination_deg",
        "constraint_results",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for sample in samples:
            row = {key: sample.get(key, "") for key in columns}
            row["rocket_flight_time_s"] = float(rocket_flight_time_s)
            row["constraint_results"] = json.dumps(
                sample.get("constraint_results", []),
                ensure_ascii=False,
                separators=(",", ":"),
            )
            writer.writerow(row)


def _write_launch_window_results_csv(
    path: Path,
    windows: list[LaunchWindowResult],
    rocket_flight_time_s: float,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    headers = [
        "窗口前沿 (北京时间)",
        "窗口后沿 (北京时间)",
        "长度/min",
        "入轨 T0 前沿 (北京时间)",
        "第一圈地影/min",
        "窗口前沿轨道最长地影/min",
        "窗口后沿轨道最长地影/min",
        "窗口前沿限制条件",
        "窗口后沿限制条件",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        for window in windows:
            start = parse_utc(window.window_start_utc)
            end = parse_utc(window.window_end_utc)
            t0 = start + timedelta(seconds=float(rocket_flight_time_s))
            writer.writerow(
                [
                    _format_bjt(start),
                    _format_bjt(end),
                    f"{window.duration_min:.1f}",
                    _format_bjt(t0),
                    f"{window.first_orbit_shadow_min:.1f}",
                    f"{window.window_start_longest_shadow_min:.1f}",
                    f"{window.window_end_longest_shadow_min:.1f}",
                    str(window.window_start_constraint or "--"),
                    str(window.window_end_constraint or "--"),
                ]
            )


def _launch_utc_from_arguments(arguments: dict[str, Any]) -> str:
    launch_utc = str(arguments.get("launch_utc", "") or "").strip()
    if launch_utc:
        return format_utc(parse_utc(launch_utc))
    launch_bjt = str(arguments.get("launch_bjt", "") or "").strip()
    if not launch_bjt:
        raise MissionAgentToolError("launch_utc or launch_bjt is required.")
    normalized = launch_bjt.replace("/", "-").replace("T", " ")
    if len(normalized) == 16:
        normalized = f"{normalized}:00"
    epoch = datetime.fromisoformat(normalized)
    if epoch.tzinfo is None:
        epoch = epoch.replace(tzinfo=BJT)
    return format_utc(epoch.astimezone(timezone.utc))


def _rocket_flight_time_s(project_root: Path, arguments: dict[str, Any]) -> float:
    if arguments.get("rocket_flight_time_s") not in (None, ""):
        return float(arguments["rocket_flight_time_s"])
    config_path = project_root / "config" / "launch_window.json"
    if config_path.exists():
        payload = json.loads(config_path.read_text(encoding="utf-8-sig"))
        return float(config_from_payload(payload).rocket_flight_time_s)
    return 2134.4121


def _format_bjt(value: datetime) -> str:
    return value.astimezone(BJT).strftime("%Y-%m-%d %H:%M:%S")
