from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


STK_116_RUNTIME_ROOT = Path(r"D:\Program Files\AGI\STK 116")
STK_116_HELP_ROOT = STK_116_RUNTIME_ROOT / "Help"
STK_116_GENERAL_HELP = STK_116_HELP_ROOT / "index.htm"
STK_116_PROGRAMMING_HELP = STK_116_HELP_ROOT / "Programming" / "index.htm"
STK_116_CONNECT_HELP = STK_116_HELP_ROOT / "Programming" / "Subsystems" / "connect" / "connect.htm"
STK_116_CONNECT_COMMAND_HELP = (
    STK_116_HELP_ROOT / "Programming" / "Subsystems" / "connectCmds" / "connectCmds.htm"
)
STK_116_RELEASE_NOTES = STK_116_HELP_ROOT / "releaseNotes.chm"
STK_HELP_KB_PATH = Path(r"C:\Users\panyi\.codex\kb\stk11_help.sqlite3")
STK_HELP_COMMAND = 'stkhelp "<query>"'
STK_HELP_FALLBACK_COMMAND = r'python C:\Users\panyi\.codex\kb\stkhelp_cli.py "<query>"'

_AGENT_DOC_DIR = Path(__file__).resolve().parents[1] / "agents"
_AGENT_DOC_PATH = _AGENT_DOC_DIR / "mission_agent.md"
_SKILL_DOC_PATHS = (
    _AGENT_DOC_DIR / "skills" / "mission_analysis_calculation.md",
    _AGENT_DOC_DIR / "skills" / "stk_11_6_operations.md",
)


@dataclass(frozen=True, slots=True)
class MissionAgentProfile:
    agent_id: str
    name: str
    document_path: Path
    skill_document_paths: tuple[Path, ...]


def smart_mission_agent_profile() -> MissionAgentProfile:
    return MissionAgentProfile(
        agent_id="smart.spacecraft_mission_analysis_expert",
        name="SMART 航天器任务分析专家",
        document_path=_AGENT_DOC_PATH,
        skill_document_paths=_SKILL_DOC_PATHS,
    )


def render_mission_agent_system_prompt(profile: MissionAgentProfile | None = None) -> str:
    profile = profile or smart_mission_agent_profile()
    return (
        f"你是 {profile.name}。\n\n"
        "以下是你的独立 agent 文档和 skill 文档。严格按照这些文档工作。\n\n"
        f"{render_mission_agent_manifest(profile)}\n\n"
        "回答必须使用中文 Markdown。优先调用可用工具获取项目数据和本地计算结果；"
        "如果信息不足，明确说明缺口，不要假设不存在的数据。"
    )


def render_mission_agent_manifest(profile: MissionAgentProfile | None = None) -> str:
    profile = profile or smart_mission_agent_profile()
    sections = [_read_text(profile.document_path)]
    for path in profile.skill_document_paths:
        sections.append(_read_text(path))
    return "\n\n---\n\n".join(section.strip() for section in sections if section.strip())


def render_mission_agent_summary(profile: MissionAgentProfile | None = None) -> str:
    profile = profile or smart_mission_agent_profile()
    skill_names = [
        _title_from_document(path)
        for path in profile.skill_document_paths
    ]
    return f"{profile.name}\n已启用技能：{'、'.join(skill_names)}"


def agent_document_paths(profile: MissionAgentProfile | None = None) -> tuple[Path, ...]:
    profile = profile or smart_mission_agent_profile()
    return (profile.document_path, *profile.skill_document_paths)


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return f"# Missing Document\n\n未找到 agent/skill 文档：`{path}`"


def _title_from_document(path: Path) -> str:
    text = _read_text(path)
    for line in text.splitlines():
        if line.startswith("# "):
            return line.removeprefix("# ").strip()
    return path.stem
