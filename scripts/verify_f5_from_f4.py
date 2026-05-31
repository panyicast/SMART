from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from PySide6 import QtWidgets

from smart.services.data_visualization import build_visualization_series, default_launch_utc_from_configs
from smart.services.launch_window import config_from_payload
from smart.services.project_workspace import ProjectWorkspace
from smart.services.stk_link import StkLinkService
from smart.ui.i18n import I18nManager
from smart.ui.mission_state import MissionState
from smart.ui.widgets.data_visualization_page import DataVisualizationPage
from smart.ui.widgets.design_maneuver_strategy_page import DesignManeuverStrategyPage
from smart.ui.widgets.flight_program_page import FlightProgramPage
from smart.ui.widgets.launch_window_page import LaunchWindowPage
from smart.ui.widgets.maneuver_page import ManeuverPage
from smart.ui.widgets.tracking_arc_page import TrackingArcPage


F4_ROOT = REPO_ROOT / "projects" / "F4"
F5_ROOT = REPO_ROOT / "projects" / "F5"
REPORT_PATH = F5_ROOT / "data" / "f5_verification_report.json"


class FakeStkExecutor:
    def __init__(self) -> None:
        self.commands: list[str] = []
        self.root = self.FakeRoot()

    class FakeRoot:
        def __init__(self) -> None:
            self.CurrentScenario: object | None = object()

        def CloseScenario(self) -> None:
            self.CurrentScenario = None

        def NewScenario(self, _name: str) -> None:
            self.CurrentScenario = object()

    def execute(self, command: str, *, ignore_failure: bool = False) -> list[str]:
        self.commands.append(command)
        return []


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalized_json_equal(left: Path, right: Path) -> bool:
    return stable_json(read_json(left)) == stable_json(read_json(right))


def strip_generated_utc(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: strip_generated_utc(item) for key, item in value.items() if key != "generated_utc"}
    if isinstance(value, list):
        return [strip_generated_utc(item) for item in value]
    return value


def generated_json_equal(left: Path, right: Path) -> bool:
    return stable_json(strip_generated_utc(read_json(left))) == stable_json(strip_generated_utc(read_json(right)))


def launch_result_rows(path: Path) -> list[list[str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    normalized: list[list[str]] = []
    for row_index, row in enumerate(rows):
        if row_index == 0:
            normalized.append(row)
            continue
        fixed = []
        for cell in row:
            text = cell.strip()
            if len(text) in {16, 19} and text[:4].isdigit() and text[4] == "-" and text[13] == ":":
                text = text[:16]
            fixed.append(text)
        normalized.append(fixed)
    return normalized


def csv_summary(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
    return {
        "rows": len(rows),
        "columns": reader.fieldnames or [],
        "first": rows[0] if rows else {},
        "last": rows[-1] if rows else {},
    }


def compare_json(name: str, rel: str, checks: list[dict[str, Any]]) -> None:
    f4 = F4_ROOT / rel
    f5 = F5_ROOT / rel
    checks.append(
        {
            "name": name,
            "path": rel,
            "exists_f5": f5.exists(),
            "match": f4.exists() and f5.exists() and normalized_json_equal(f4, f5),
        }
    )


def compare_file_sha(name: str, rel: str, checks: list[dict[str, Any]]) -> None:
    f4 = F4_ROOT / rel
    f5 = F5_ROOT / rel
    checks.append(
        {
            "name": name,
            "path": rel,
            "exists_f5": f5.exists(),
            "match": f4.exists() and f5.exists() and file_sha(f4) == file_sha(f5),
        }
    )


def compare_generated_json(name: str, rel: str, checks: list[dict[str, Any]]) -> None:
    f4 = F4_ROOT / rel
    f5 = F5_ROOT / rel
    checks.append(
        {
            "name": name,
            "path": rel,
            "exists_f5": f5.exists(),
            "match": f4.exists() and f5.exists() and generated_json_equal(f4, f5),
            "normalization": "ignored generated_utc",
        }
    )


def compare_launch_results(name: str, rel: str, checks: list[dict[str, Any]]) -> None:
    f4 = F4_ROOT / rel
    f5 = F5_ROOT / rel
    checks.append(
        {
            "name": name,
            "path": rel,
            "exists_f5": f5.exists(),
            "match": f4.exists() and f5.exists() and launch_result_rows(f4) == launch_result_rows(f5),
            "normalization": "datetime minutes expanded to :00 seconds",
        }
    )


def copy_config_with_page_generation(workspace: ProjectWorkspace, i18n: I18nManager) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []

    design_payload = read_json(F4_ROOT / "config" / "design_maneuver_strategy.json")
    design_page = DesignManeuverStrategyPage(i18n, workspace)
    design_page._apply_config_to_fields(design_payload)
    path = design_page.save_config()
    steps.append({"page": "设计变轨策略", "action": "状态设置保存", "path": str(path)})
    design_page.run_planner()
    steps.append({"page": "设计变轨策略", "action": "生成脉冲规划", "status": design_page._status_label.text()})
    design_page.run_continuous_thrust_optimization()
    steps.append({"page": "设计变轨策略", "action": "优化连续推力模型参数", "status": design_page._status_label.text()})
    design_page.export_continuous_thrust_strategy()
    steps.append({"page": "设计变轨策略", "action": "导出连续推力策略 Excel"})

    maneuver_page = ManeuverPage(i18n, workspace)
    path = maneuver_page.import_design_maneuver_strategy()
    steps.append({"page": "导入变轨策略", "action": "引入变轨策略", "path": str(path)})
    maneuver_page.calculate_strategy()
    steps.append({"page": "导入变轨策略", "action": "计算变轨策略", "status": maneuver_page._status_label.text()})

    launch_payload = read_json(F4_ROOT / "config" / "launch_window.json")
    launch_page = LaunchWindowPage(i18n, workspace)
    launch_page._set_config(launch_payload)
    path = launch_page.save_config()
    steps.append({"page": "发射窗口", "action": "状态设置保存", "path": str(path)})
    launch_page.calculate_windows()
    steps.append({"page": "发射窗口", "action": "计算发射窗口", "status": launch_page._status_label.text()})

    tracking_payload = read_json(F4_ROOT / "config" / "tracking_arc.json")
    workspace.save_tracking_arc_config(tracking_payload)
    tracking_page = TrackingArcPage(i18n, workspace)
    tracking_page._window_check_timer.stop()
    tracking_page._set_config(tracking_payload)
    path = tracking_page.save_config()
    steps.append({"page": "跟踪弧段", "action": "设置保存", "path": str(path)})
    tracking_page.calculate_tracking_arcs()
    steps.append({"page": "跟踪弧段", "action": "计算跟踪弧段", "status": tracking_page._status_label.text()})

    flight_payload = read_json(F4_ROOT / "config" / "flight_program.json")
    workspace.save_flight_program_config(flight_payload)
    flight_page = FlightProgramPage(i18n, workspace, stk_link_service_factory=lambda: StkLinkService(workspace, executor=FakeStkExecutor()))
    flight_page.calculate_reference_arcs()
    steps.append({"page": "飞行程序", "action": "计算参考轨", "status": flight_page._status_label.text()})

    viz_page = DataVisualizationPage(MissionState(), i18n, workspace)
    viz_page.calculate()
    chart_paths = viz_page.export_charts(workspace.charts_dir())
    steps.append(
        {
            "page": "数据可视化",
            "action": "计算并导出图表",
            "status": viz_page._status_label.text(),
            "charts": [str(path) for path in chart_paths],
        }
    )

    fake_executor = FakeStkExecutor()
    stk_result = StkLinkService(workspace, executor=fake_executor).import_project_to_stk()
    steps.append(
        {
            "page": "STK 链接",
            "action": "离线导入链路测试",
            "scenario_name": stk_result.scenario_name,
            "satellite_name": stk_result.satellite_name,
            "ground_station_count": stk_result.ground_station_count,
            "relay_satellite_count": stk_result.relay_satellite_count,
            "command_count": len(fake_executor.commands),
            "artifacts": {
                "orbit_ephemeris_path": str(stk_result.artifacts.orbit_ephemeris_path),
                "attitude_path": str(stk_result.artifacts.attitude_path),
                "relay_ephemeris_paths": [str(path) for path in stk_result.artifacts.relay_ephemeris_paths],
            },
        }
    )
    return steps


def build_result_comparisons() -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for name, rel in [
        ("设计变轨配置", "config/design_maneuver_strategy.json"),
        ("发射窗口配置", "config/launch_window.json"),
        ("跟踪弧段配置", "config/tracking_arc.json"),
        ("飞行程序配置", "config/flight_program.json"),
        ("脉冲规划结果", "data/design_maneuver_results.json"),
        ("连续推力结果", "data/design_continuous_thrust_results.json"),
        ("跟踪弧段结果", "data/tracking_arc_results.json"),
        ("飞行程序参考轨结果", "data/flight_program_reference_results.json"),
    ]:
        compare_json(name, rel, checks)
    for name, rel in [
        ("导入变轨配置", "config/design_import_maneuver_strategy.json"),
        ("变轨策略配置", "config/maneuver_strategy.json"),
    ]:
        compare_generated_json(name, rel, checks)
    for name, rel in [
        ("连续推力轨道历史", "data/design_continuous_thrust_orbit_history.csv"),
        ("全轨道历史", "data/full_orbit_history.csv"),
        ("发射窗口样本", "data/launch_window_samples.csv"),
    ]:
        compare_file_sha(name, rel, checks)
    compare_launch_results("发射窗口结果", "data/launch_window_results.csv", checks)
    return checks


def visualization_summary(workspace: ProjectWorkspace) -> dict[str, Any]:
    strategy = workspace.load_maneuver_strategy()
    launch_config = workspace.load_launch_window_config() or {}
    flight_program = workspace.load_flight_program_config()
    rocket_flight_time_s = float(launch_config.get("rocket_flight_time_s", 2134.4121))
    launch_utc = default_launch_utc_from_configs(
        flight_program=flight_program,
        maneuver_strategy=strategy,
        rocket_flight_time_s=rocket_flight_time_s,
    )
    series = build_visualization_series(
        orbit_history_csv=workspace.data_dir() / "full_orbit_history.csv",
        maneuver_strategy=strategy or {},
        launch_utc=launch_utc,
        rocket_flight_time_s=rocket_flight_time_s,
    )
    return {
        "launch_utc": series.launch_utc,
        "t0_utc": series.t0_utc,
        "samples": int(series.elapsed_min.size),
        "first_elapsed_min": float(series.elapsed_min[0]),
        "last_elapsed_min": float(series.elapsed_min[-1]),
        "final_mass_kg": float(series.values["mass_kg"][-1]),
        "maneuver_intervals": len(series.maneuver_intervals),
    }


def main() -> int:
    if not F4_ROOT.exists():
        raise FileNotFoundError(F4_ROOT)
    if F5_ROOT.exists():
        resolved = F5_ROOT.resolve()
        projects_root = (REPO_ROOT / "projects").resolve()
        if projects_root not in resolved.parents:
            raise RuntimeError(f"Refuse to delete outside projects: {resolved}")
        shutil.rmtree(F5_ROOT)

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    i18n = I18nManager("zh")
    workspace = ProjectWorkspace()
    info = workspace.create_project("F5", REPO_ROOT / "projects")
    steps = [{"page": "项目", "action": "新建项目", "path": str(info.root_dir)}]
    steps.extend(copy_config_with_page_generation(workspace, i18n))
    app.processEvents()

    comparisons = build_result_comparisons()
    f4_workspace = ProjectWorkspace()
    f4_workspace.open_project(F4_ROOT)
    f5_workspace = ProjectWorkspace()
    f5_workspace.open_project(F5_ROOT)
    report = {
        "project": str(F5_ROOT),
        "config_source": str(F4_ROOT),
        "ignored_pages": ["3D 设置", "AI 分析"],
        "steps": steps,
        "comparisons": comparisons,
        "csv_summaries": {
            "f4_launch_window_samples": csv_summary(F4_ROOT / "data" / "launch_window_samples.csv"),
            "f5_launch_window_samples": csv_summary(F5_ROOT / "data" / "launch_window_samples.csv"),
            "f4_launch_window_results": csv_summary(F4_ROOT / "data" / "launch_window_results.csv"),
            "f5_launch_window_results": csv_summary(F5_ROOT / "data" / "launch_window_results.csv"),
        },
        "visualization": {
            "f4": visualization_summary(f4_workspace),
            "f5": visualization_summary(f5_workspace),
        },
    }
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    discrepancies = [item for item in comparisons if not item["match"]]
    print(
        json.dumps(
            {"report": str(REPORT_PATH), "discrepancies": discrepancies, "steps": len(steps)},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
