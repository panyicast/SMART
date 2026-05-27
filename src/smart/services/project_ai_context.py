from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from statistics import mean
from typing import Any

from smart.services.mission_agent import MissionAgentProfile, render_mission_agent_manifest


DEFAULT_CONTEXT_ROW_LIMIT = 6
DEFAULT_CONTEXT_CHAR_LIMIT = 80_000


def build_project_analysis_context(
    project_root: str | Path,
    *,
    row_limit: int = DEFAULT_CONTEXT_ROW_LIMIT,
    char_limit: int = DEFAULT_CONTEXT_CHAR_LIMIT,
) -> str:
    root = Path(project_root).expanduser().resolve()
    if not root.exists():
        raise FileNotFoundError(f"Project root not found: {root}")

    sections: list[str] = [
        "# SMART Project Context",
        "",
        f"- Project root: {root}",
    ]

    meta_path = root / "smart_project.json"
    if meta_path.exists():
        sections.extend(["", "## Project Metadata", "### smart_project.json", _summarize_json(meta_path)])

    config_dir = root / "config"
    if config_dir.exists():
        sections.extend(["", "## Configuration Files"])
        for path in sorted(config_dir.glob("*.json")):
            sections.extend(["", f"### {path.relative_to(root).as_posix()}", _summarize_json(path)])

    data_dir = root / "data"
    if data_dir.exists():
        sections.extend(["", "## Data Files"])
        for path in _selected_data_files(data_dir):
            sections.extend(["", f"### {path.relative_to(root).as_posix()}", _summarize_data_file(path, row_limit=row_limit)])

    file_inventory = _file_inventory(root)
    if file_inventory:
        sections.extend(["", "## Project File Inventory", file_inventory])

    content = "\n".join(sections).strip() + "\n"
    if len(content) > char_limit:
        return content[:char_limit] + "\n\n[Context truncated by SMART]\n"
    return content


def build_project_analysis_prompt(
    context: str,
    *,
    scope: str,
    question: str = "",
    agent_profile: MissionAgentProfile | None = None,
) -> str:
    scope_text = scope.strip() or "项目综合分析"
    question_text = question.strip()
    extra = f"\n用户补充问题：{question_text}\n" if question_text else ""
    agent_manifest = render_mission_agent_manifest(agent_profile)
    return (
        "请基于下面的 SMART 内置 agent profile、项目配置和数据摘要进行工程分析。"
        "回答使用中文 Markdown，优先指出风险、异常、配置不一致、关键结论和建议的下一步验证。"
        "不要编造未在上下文中出现的数据；如果信息不足，请明确说明缺口。\n\n"
        f"分析范围：{scope_text}\n"
        f"{extra}\n"
        "内置 agent profile：\n\n"
        f"{agent_manifest}\n\n"
        "项目上下文如下：\n\n"
        f"{context}"
    )


def _selected_data_files(data_dir: Path) -> list[Path]:
    preferred_names = [
        "orbit_elements.json",
        "full_orbit_history.csv",
        "launch_window_samples.csv",
        "launch_window_results.csv",
        "ai_project_analysis.md",
    ]
    selected = [data_dir / name for name in preferred_names if (data_dir / name).exists()]
    extra_csv = sorted(path for path in data_dir.glob("*.csv") if path not in selected)
    extra_json = sorted(path for path in data_dir.glob("*.json") if path not in selected)
    return selected + extra_json[:6] + extra_csv[:6]


def _summarize_data_file(path: Path, *, row_limit: int) -> str:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return _summarize_csv(path, row_limit=row_limit)
    if suffix == ".json":
        return _summarize_json(path)
    if suffix == ".md":
        return _summarize_text(path, max_chars=5_000)
    return f"- Size: {_format_bytes(path.stat().st_size)}"


def _summarize_json(path: Path) -> str:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        return f"- Could not parse JSON: {exc}"
    text = json.dumps(_compact_payload(payload), ensure_ascii=False, indent=2)
    return _truncate(text, 8_000)


def _summarize_text(path: Path, *, max_chars: int) -> str:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except Exception as exc:
        return f"- Could not read text: {exc}"
    return _truncate(text, max_chars)


def _summarize_csv(path: Path, *, row_limit: int) -> str:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            columns = list(reader.fieldnames or [])
            samples: list[dict[str, str]] = []
            numeric_values: dict[str, list[float]] = {column: [] for column in columns}
            row_count = 0
            for row in reader:
                row_count += 1
                if len(samples) < row_limit:
                    samples.append({key: str(value or "") for key, value in row.items()})
                for column in columns:
                    value = _to_float(row.get(column))
                    if value is not None and math.isfinite(value):
                        numeric_values[column].append(value)
    except Exception as exc:
        return f"- Could not parse CSV: {exc}"

    lines = [
        f"- Size: {_format_bytes(path.stat().st_size)}",
        f"- Rows: {row_count}",
        f"- Columns: {', '.join(columns) if columns else '(none)'}",
    ]
    stats = _numeric_stats(numeric_values)
    if stats:
        lines.extend(["", "Numeric statistics:", stats])
    if samples:
        lines.extend(["", "Sample rows:", "```json", json.dumps(samples, ensure_ascii=False, indent=2), "```"])
    return "\n".join(lines)


def _numeric_stats(values_by_column: dict[str, list[float]], limit: int = 16) -> str:
    rows: list[str] = []
    for column, values in values_by_column.items():
        if not values:
            continue
        rows.append(
            f"- {column}: count={len(values)}, min={min(values):.6g}, max={max(values):.6g}, mean={mean(values):.6g}"
        )
        if len(rows) >= limit:
            break
    return "\n".join(rows)


def _compact_payload(value: Any, *, max_list_items: int = 16, max_dict_items: int = 80) -> Any:
    if isinstance(value, dict):
        items = list(value.items())
        compact = {str(key): _compact_payload(item) for key, item in items[:max_dict_items]}
        if len(items) > max_dict_items:
            compact["..."] = f"{len(items) - max_dict_items} more keys"
        return compact
    if isinstance(value, list):
        compact_list = [_compact_payload(item) for item in value[:max_list_items]]
        if len(value) > max_list_items:
            compact_list.append(f"... {len(value) - max_list_items} more items")
        return compact_list
    return value


def _file_inventory(root: Path) -> str:
    lines: list[str] = []
    for folder_name in ("config", "data", "charts"):
        folder = root / folder_name
        if not folder.exists():
            continue
        files = sorted(path for path in folder.rglob("*") if path.is_file())
        lines.append(f"- {folder_name}/: {len(files)} files")
        for path in files[:12]:
            lines.append(f"  - {path.relative_to(root).as_posix()} ({_format_bytes(path.stat().st_size)})")
        if len(files) > 12:
            lines.append(f"  - ... {len(files) - 12} more files")
    return "\n".join(lines)


def _to_float(value: object) -> float | None:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _truncate(text: str, max_chars: int) -> str:
    return text if len(text) <= max_chars else text[:max_chars] + "\n[truncated]\n"


def _format_bytes(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"
