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
from smart.services.launch_window import (
    compute_shadow_intervals_for_launch,
    config_from_payload,
)
from smart.services.mission_agent import STK_HELP_FALLBACK_COMMAND, STK_HELP_KB_PATH
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
        script_path = Path(r"C:\Users\panyi\.codex\kb\stkhelp_cli.py")
        if not STK_HELP_KB_PATH.exists() or not script_path.exists():
            return {
                "query": query,
                "available": False,
                "fallback_command": STK_HELP_FALLBACK_COMMAND.replace("<query>", query),
                "output": "STK help KB or fallback script is missing.",
            }
        completed = subprocess.run(
            [sys.executable, str(script_path), query],
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
            "output": (completed.stdout or completed.stderr).strip(),
        }


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
