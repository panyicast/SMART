from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shlex
import shutil


STK_116_RUNTIME_ROOT = Path(r"D:\Program Files\AGI\STK 116")
STK_116_HELP_ROOT = STK_116_RUNTIME_ROOT / "Help"
STK_116_GENERAL_HELP = STK_116_HELP_ROOT / "index.htm"
STK_116_PROGRAMMING_HELP = STK_116_HELP_ROOT / "Programming" / "index.htm"
STK_116_CONNECT_HELP = STK_116_HELP_ROOT / "Programming" / "Subsystems" / "connect" / "connect.htm"
STK_116_CONNECT_COMMAND_HELP = (
    STK_116_HELP_ROOT / "Programming" / "Subsystems" / "connectCmds" / "connectCmds.htm"
)
STK_116_RELEASE_NOTES = STK_116_HELP_ROOT / "releaseNotes.chm"
SMART_STK_HELP_KB_ENV = "SMART_STK_HELP_KB"
SMART_STK_HELP_SCRIPT_ENV = "SMART_STK_HELP_SCRIPT"
SMART_STKHELP_COMMAND_ENV = "SMART_STKHELP_COMMAND"
SMART_STK_HELP_CONFIG_ENV = "SMART_STK_HELP_CONFIG"
_DEFAULT_STK_HELP_KB_PATH = Path.home() / ".codex" / "kb" / "stk11_help.sqlite3"
_DEFAULT_STK_HELP_SCRIPT_PATH = Path.home() / ".codex" / "kb" / "stkhelp_cli.py"
_DEFAULT_STK_HELP_CONFIG_PATH = Path.home() / ".smart" / "stk_help.json"
STK_HELP_KB_PATH = _DEFAULT_STK_HELP_KB_PATH
STK_HELP_COMMAND = 'stkhelp "<query>"'
STK_HELP_FALLBACK_COMMAND = f'python "{_DEFAULT_STK_HELP_SCRIPT_PATH}" "<query>"'

_AGENT_DOC_DIR = Path(__file__).resolve().parents[1] / "agents"
_AGENT_DOC_PATH = _AGENT_DOC_DIR / "mission_agent.md"
_SKILL_DOC_PATHS = (
    _AGENT_DOC_DIR / "skills" / "mission_analysis_calculation.md",
    _AGENT_DOC_DIR / "skills" / "project_consistency_audit.md",
    _AGENT_DOC_DIR / "skills" / "stk_11_6_operations.md",
)


@dataclass(frozen=True, slots=True)
class MissionAgentProfile:
    agent_id: str
    name: str
    document_path: Path
    skill_document_paths: tuple[Path, ...]


@dataclass(frozen=True, slots=True)
class MissionAgentSkillOption:
    skill_id: str
    name: str
    document_path: Path


@dataclass(frozen=True, slots=True)
class StkHelpToolStatus:
    available: bool
    command: tuple[str, ...]
    script_path: Path | None
    kb_path: Path
    config_path: Path
    config_loaded: bool
    reason: str

    def command_for_query(self, query: str) -> list[str] | None:
        if self.command:
            return [*self.command, query]
        if self.script_path is not None:
            return [str(self.script_path), query]
        return None

    def display_command(self, query: str = "<query>") -> str:
        if self.command:
            return f'{" ".join(self.command)} "{query}"'
        if self.script_path is not None:
            return f'python "{self.script_path}" "{query}"'
        return f"{SMART_STK_HELP_SCRIPT_ENV}=<path> python <script> \"{query}\""


def resolve_stk_help_tool() -> StkHelpToolStatus:
    config_path = Path(os.environ.get(SMART_STK_HELP_CONFIG_ENV, str(_DEFAULT_STK_HELP_CONFIG_PATH))).expanduser()
    config = _load_stk_help_config(config_path)
    kb_value = _env_or_config(SMART_STK_HELP_KB_ENV, config, "kb_path", "stk_help_kb")
    script_value = _env_or_config(SMART_STK_HELP_SCRIPT_ENV, config, "script_path", "stk_help_script")
    command_text = _env_or_config(SMART_STKHELP_COMMAND_ENV, config, "command", "stkhelp_command").strip()

    kb_path = Path(kb_value or str(STK_HELP_KB_PATH)).expanduser()
    if command_text:
        command = tuple(shlex.split(command_text))
    else:
        command = ()
    if command:
        script_path = _DEFAULT_STK_HELP_SCRIPT_PATH
    else:
        configured_script = bool(script_value)
        if configured_script:
            script_path = Path(script_value).expanduser()
        elif shutil.which("stkhelp"):
            command = ("stkhelp",)
            script_path = _DEFAULT_STK_HELP_SCRIPT_PATH
        else:
            script_path = _DEFAULT_STK_HELP_SCRIPT_PATH

    missing: list[str] = []
    if not kb_path.exists():
        missing.append(f"{SMART_STK_HELP_KB_ENV}={kb_path}")
    if not command and not script_path.exists():
        missing.append(f"{SMART_STK_HELP_SCRIPT_ENV}={script_path}")
    available = not missing and (bool(command) or script_path.exists())
    reason = "available" if available else "missing " + ", ".join(missing)
    return StkHelpToolStatus(
        available=available,
        command=command,
        script_path=None if command else script_path,
        kb_path=kb_path,
        config_path=config_path,
        config_loaded=bool(config),
        reason=reason,
    )


def _load_stk_help_config(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return {str(key): str(value).strip() for key, value in payload.items() if value not in (None, "")}


def _env_or_config(env_name: str, config: dict[str, str], *keys: str) -> str:
    env_value = os.environ.get(env_name, "").strip()
    if env_value:
        return env_value
    for key in keys:
        value = config.get(key, "").strip()
        if value:
            return value
    return ""


def smart_mission_agent_profile() -> MissionAgentProfile:
    return MissionAgentProfile(
        agent_id="smart.spacecraft_mission_analysis_expert",
        name="SMART 航天器任务分析专家",
        document_path=_AGENT_DOC_PATH,
        skill_document_paths=_SKILL_DOC_PATHS,
    )


def mission_agent_skill_options() -> tuple[MissionAgentSkillOption, ...]:
    return tuple(
        MissionAgentSkillOption(
            skill_id=_skill_id_from_document(path),
            name=_title_from_document(path),
            document_path=path,
        )
        for path in _SKILL_DOC_PATHS
    )


def mission_agent_profile_for_skill(skill_id: str | None) -> MissionAgentProfile:
    normalized = (skill_id or "all").strip()
    base = smart_mission_agent_profile()
    if normalized in {"", "all"}:
        return base
    selected = tuple(
        option.document_path
        for option in mission_agent_skill_options()
        if option.skill_id == normalized
    )
    if not selected:
        return base
    return MissionAgentProfile(
        agent_id=base.agent_id,
        name=base.name,
        document_path=base.document_path,
        skill_document_paths=selected,
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


def _skill_id_from_document(path: Path) -> str:
    text = _read_text(path)
    for line in text.splitlines():
        value = line.strip().strip("`")
        if value.startswith("smart.skill."):
            return value
    return f"smart.skill.{path.stem}"
