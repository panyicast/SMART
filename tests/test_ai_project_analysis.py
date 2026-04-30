from __future__ import annotations

import json
import zipfile

from smart.services.llm_client import (
    DEFAULT_DEEPSEEK_MODEL,
    LLMRequestConfig,
    _join_endpoint,
    _parse_sse_line,
)
from smart.services.mission_agent import (
    STK_HELP_KB_PATH,
    agent_document_paths,
    render_mission_agent_manifest,
    render_mission_agent_system_prompt,
    smart_mission_agent_profile,
)
from smart.services.mission_agent_tools import MissionAgentToolExecutor
from smart.services.project_ai_context import build_project_analysis_context, build_project_analysis_prompt
from smart.services.report_export import export_docx_report, export_markdown_report
from smart.ui.main_window import _NAV_KEYS


def test_project_analysis_context_summarizes_json_and_csv(tmp_path) -> None:
    project = tmp_path / "Demo"
    (project / "config").mkdir(parents=True)
    (project / "data").mkdir()
    (project / "smart_project.json").write_text(
        json.dumps({"name": "Demo", "version": 1}, ensure_ascii=False),
        encoding="utf-8",
    )
    (project / "config" / "launch_window.json").write_text(
        json.dumps({"sample_step_min": 10.0}, ensure_ascii=False),
        encoding="utf-8",
    )
    (project / "data" / "launch_window_results.csv").write_text(
        "窗口前沿,长度/min\n2026-05-15 15:30,80.0\n2026-05-16 15:30,90.0\n",
        encoding="utf-8",
    )

    context = build_project_analysis_context(project)

    assert "smart_project.json" in context
    assert "config/launch_window.json" in context
    assert "data/launch_window_results.csv" in context
    assert "Rows: 2" in context
    assert "长度/min" in context
    assert "mean=85" in context


def test_project_analysis_prompt_includes_scope_and_question() -> None:
    prompt = build_project_analysis_prompt("context-body", scope="发射窗口结果分析", question="为什么窗口变短？")

    assert "发射窗口结果分析" in prompt
    assert "为什么窗口变短？" in prompt
    assert "SMART 航天器任务分析专家" in prompt
    assert "smart.skill.mission_analysis_calculation" in prompt
    assert "smart.skill.stk_11_6_operations" in prompt
    assert "context-body" in prompt


def test_mission_agent_profile_configures_calculation_and_stk_skills() -> None:
    profile = smart_mission_agent_profile()
    system_prompt = render_mission_agent_system_prompt(profile)
    manifest = render_mission_agent_manifest(profile)

    assert profile.agent_id == "smart.spacecraft_mission_analysis_expert"
    assert all(path.exists() for path in agent_document_paths(profile))
    assert "smart.skill.mission_analysis_calculation" in manifest
    assert "smart.skill.stk_11_6_operations" in manifest
    assert str(STK_HELP_KB_PATH) in manifest
    assert "STK 11.6" in system_prompt


def test_ai_project_analysis_nav_key_is_last() -> None:
    assert _NAV_KEYS[-1] == "nav.ai_project_analysis"


def test_llm_endpoint_join_handles_versioned_and_unversioned_base_url() -> None:
    assert _join_endpoint("https://api.deepseek.com", "chat/completions") == (
        "https://api.deepseek.com/v1/chat/completions"
    )
    assert _join_endpoint("https://api.deepseek.com/v1", "chat/completions") == (
        "https://api.deepseek.com/v1/chat/completions"
    )


def test_deepseek_request_config_defaults_to_v4_thinking() -> None:
    config = LLMRequestConfig(api_key="key")

    assert config.model == DEFAULT_DEEPSEEK_MODEL
    assert config.reasoning_effort == "high"
    assert config.thinking_enabled is True


def test_deepseek_sse_line_parser_ignores_keep_alive_and_done() -> None:
    assert _parse_sse_line(": keep-alive") is None
    assert _parse_sse_line("data: [DONE]") is None
    assert _parse_sse_line('data: {"choices": []}') == {"choices": []}


def test_mission_agent_tool_specs_expose_local_tools(tmp_path) -> None:
    executor = MissionAgentToolExecutor(tmp_path)
    tool_names = {tool["function"]["name"] for tool in executor.tool_specs()}

    assert "find_launch_windows" in tool_names
    assert "compute_shadow_intervals_for_launch" in tool_names
    assert "query_stk_help" in tool_names


def test_report_export_writes_markdown(tmp_path) -> None:
    markdown = "# AI 项目分析报告\n\n- 模型：`deepseek-v4-pro`\n"
    output_path = export_markdown_report(markdown, tmp_path / "report.md")

    assert output_path.read_text(encoding="utf-8") == markdown


def test_report_export_writes_basic_docx(tmp_path) -> None:
    markdown = (
        "# AI 项目分析报告\n\n"
        "| 序号 | 入影 UTC | 持续(min) |\n"
        "| --- | --- | --- |\n"
        "| 1 | 2026-05-25 08:05:34 | **13.38** |\n"
    )
    output_path = export_docx_report(markdown, tmp_path / "report.docx")

    with zipfile.ZipFile(output_path) as package:
        names = set(package.namelist())
        document_xml = package.read("word/document.xml").decode("utf-8")

    assert "[Content_Types].xml" in names
    assert "_rels/.rels" in names
    assert "word/_rels/document.xml.rels" in names
    assert "word/styles.xml" in names
    assert "AI 项目分析报告" in document_xml
    assert "入影 UTC" in document_xml
    assert "13.38" in document_xml


def test_report_export_converts_compact_markdown_table_to_docx_table(tmp_path) -> None:
    markdown = (
        "2.2 全部地影区间（SMART 工具直接输出）\n"
        "| # | 入影 UTC | 入影 BJT | 出影 UTC | 出影 BJT | T0+入(min) | T0+出(min) | 持续(min) | "
        "|:--:|:---|:---|:---|:---|:--:|:--:|:--:| "
        "| 1 | 2026-05-25 08:05:34 | 05-25 16:05:34 | 2026-05-25 08:18:57 | 05-25 16:18:57 | "
        "0.00 | 13.38 | 13.38 | "
        "| 2 | 2026-05-25 21:49:56 | 05-26 05:49:56 | 2026-05-25 22:13:58 | 05-26 06:13:58 | "
        "824.37 | 848.40 | 24.02 |\n"
    )
    output_path = export_docx_report(markdown, tmp_path / "compact-table.docx")

    with zipfile.ZipFile(output_path) as package:
        document_xml = package.read("word/document.xml").decode("utf-8")

    assert "<w:tbl>" in document_xml
    assert document_xml.count("<w:tr>") == 3
    assert "全部地影区间" in document_xml
    assert "入影 UTC" in document_xml
    assert "2026-05-25 21:49:56" in document_xml
