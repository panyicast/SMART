from __future__ import annotations

import json
import zipfile

from PySide6 import QtCore, QtWidgets

from smart.services.llm_client import (
    DEFAULT_DEEPSEEK_MODEL,
    LLMRequestConfig,
    _join_endpoint,
    _parse_sse_line,
    request_chat_completion,
)
import smart.services.llm_client as llm_client
from smart.services.mission_agent import (
    agent_document_paths,
    render_mission_agent_manifest,
    render_mission_agent_system_prompt,
    resolve_stk_help_tool,
    smart_mission_agent_profile,
)
from smart.services.mission_agent_tools import MissionAgentToolExecutor
import smart.services.mission_agent_tools as mission_agent_tools
from smart.services.design_maneuver_strategy import (
    DesignManeuverBurn,
    DesignManeuverResult,
    default_design_maneuver_strategy_payload,
)
from smart.services.project_ai_context import build_project_analysis_context, build_project_analysis_prompt
from smart.services.report_export import export_docx_report, export_markdown_report
from smart.services.pdf_report_export import export_pdf_report, _normalize_pdf_table_cell, _table_col_widths
from smart.services.project_workspace import ProjectWorkspace
from smart.ui.main_window import _NAV_KEYS
from smart.ui.i18n import I18nManager
from smart.ui.widgets.ai_project_analysis_page import AIProjectAnalysisPage, AI_ANALYSIS_PROMPT_TEMPLATES
from smart.ui.widgets.spinboxes import NoWheelComboBox


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
    assert "STK 11.6" in system_prompt
    assert "SMART_STK_HELP_CONFIG" in manifest
    assert "SMART_STK_HELP_KB" in manifest
    assert "SMART_STK_HELP_SCRIPT" in manifest


def test_ai_project_analysis_nav_key_is_last() -> None:
    assert _NAV_KEYS[-1] == "nav.ai_project_analysis"


def test_ai_project_analysis_page_prioritizes_task_and_report(tmp_path) -> None:
    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    workspace = ProjectWorkspace()
    workspace.create_project("ai-page-layout", tmp_path)
    settings = QtCore.QSettings(str(tmp_path / "settings.ini"), QtCore.QSettings.Format.IniFormat)

    page = AIProjectAnalysisPage(I18nManager("zh"), workspace, settings)
    try:
        assert not hasattr(page, "_scope_combo")
        assert not hasattr(page, "_preview_button")
        assert isinstance(page._prompt_template_combo, NoWheelComboBox)
        assert isinstance(page._model_combo, NoWheelComboBox)
        assert isinstance(page._reasoning_effort_combo, NoWheelComboBox)
        assert page._tools_help_button.text() == "查看 Skill / Tools"
        assert "plan_design_maneuver_strategy" in page._render_tools_help_text()
        assert "smart.skill.mission_analysis_calculation" in page._render_skill_help_markdown()
        assert page._api_group.maximumHeight() <= 44
        assert page._agent_group.maximumHeight() <= 44
        assert page._trace_card.isHidden()
        assert page._run_state_label.text() == "待生成"

        template_index = page._prompt_template_combo.findText(AI_ANALYSIS_PROMPT_TEMPLATES[0][0])
        assert template_index > 0
        page._prompt_template_combo.setCurrentIndex(template_index)
        assert AI_ANALYSIS_PROMPT_TEMPLATES[0][1] in page._question_edit.toPlainText()

        page._question_edit.setPlainText("重点分析发射窗口结果\n检查约束是否合理。")
        page.preview_context()

        preflight_text = page._preflight_view.toPlainText()
        assert "安全边界" in preflight_text
        assert "不发送完整大 CSV" in preflight_text
        assert "项目摘要字符数" in preflight_text
        assert "待发送 prompt 字符数" in preflight_text
        assert "重点分析发射窗口结果" in page._trace_view.toPlainText()
    finally:
        page.deleteLater()


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


def test_deepseek_tool_round_preserves_reasoning_content(monkeypatch) -> None:
    captured_messages: list[list[dict[str, object]]] = []

    def fake_stream(config, messages, *, tools, progress_callback, expose_reasoning):
        captured_messages.append([dict(item) for item in messages])
        if len(captured_messages) == 1:
            return llm_client._AssistantTurn(
                content="",
                reasoning_content="tool reasoning",
                tool_calls=[
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "build_project_context", "arguments": "{}"},
                    }
                ],
                usage=None,
            )
        return llm_client._AssistantTurn(
            content="done",
            reasoning_content="final reasoning",
            tool_calls=[],
            usage=None,
        )

    monkeypatch.setattr(llm_client, "_stream_deepseek_turn", fake_stream)

    response = request_chat_completion(
        LLMRequestConfig(api_key="key", expose_reasoning=False),
        "prompt",
        tools=[{"type": "function", "function": {"name": "build_project_context"}}],
        tool_executor=lambda _name, _arguments: {"ok": True},
    )

    assert response.content == "done"
    assert "reasoning_content" in captured_messages[1][2]
    assert captured_messages[1][2]["reasoning_content"] == "tool reasoning"


def test_mission_agent_tool_specs_expose_local_tools(tmp_path) -> None:
    executor = MissionAgentToolExecutor(tmp_path)
    tool_names = {tool["function"]["name"] for tool in executor.tool_specs()}

    assert "find_launch_windows" in tool_names
    assert "compute_shadow_intervals_for_launch" in tool_names
    assert "plan_design_maneuver_strategy" in tool_names
    assert "optimize_design_continuous_thrust" in tool_names
    assert "compute_launch_window_samples" in tool_names
    assert "query_stk_help" in tool_names


def test_design_maneuver_agent_tool_uses_override_and_saves(monkeypatch, tmp_path) -> None:
    project = tmp_path / "Demo"
    (project / "config").mkdir(parents=True)
    (project / "data").mkdir()
    (project / "config" / "design_maneuver_strategy.json").write_text(
        json.dumps(default_design_maneuver_strategy_payload(), ensure_ascii=False),
        encoding="utf-8",
    )

    def fake_plan(payload):
        assert payload["initial"]["m0_kg"] == 6400.0
        return DesignManeuverResult(
            config=payload,
            summary={"total_delta_v_mps": 12.5},
            burns=[
                DesignManeuverBurn(
                    index=1,
                    burn_type="raise_apogee",
                    apsis="perigee",
                    elapsed_min=10.0,
                    beijing_time="2026-04-24 22:04:27",
                    longitude_deg_e=101.0,
                    delta_v_mps=12.5,
                    alpha_deg=0.0,
                    target_post_a_km=42164.0,
                    total_burn_time_min=2.0,
                    propellant_kg=4.0,
                    post_a_km=30000.0,
                    post_e=0.7,
                    post_i_deg=16.5,
                    duration_ok=True,
                    longitude_ok=True,
                )
            ],
            checks=[{"name": "duration", "passed": True}],
            warnings=[],
        )

    monkeypatch.setattr(mission_agent_tools, "plan_design_maneuver_strategy", fake_plan)
    executor = MissionAgentToolExecutor(project)

    result = executor.execute(
        "plan_design_maneuver_strategy",
        {"config_override": {"initial": {"m0_kg": 6400.0}}, "save_result": True},
    )

    assert result["burn_count"] == 1
    assert result["burns"][0]["delta_v_mps"] == 12.5
    assert (project / "data" / "design_maneuver_results.json").exists()


def test_stk_help_tool_reports_missing_configuration(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SMART_STK_HELP_CONFIG", str(tmp_path / "missing_config.json"))
    monkeypatch.setenv("SMART_STK_HELP_KB", str(tmp_path / "missing.sqlite3"))
    monkeypatch.setenv("SMART_STK_HELP_SCRIPT", str(tmp_path / "missing_cli.py"))
    monkeypatch.delenv("SMART_STKHELP_COMMAND", raising=False)
    executor = MissionAgentToolExecutor(tmp_path)

    result = executor.execute("query_stk_help", {"query": "Connect Units"})

    assert result["available"] is False
    assert "SMART_STK_HELP_KB" in result["output"]
    assert "SMART_STK_HELP_SCRIPT" in result["output"]
    assert "missing_cli.py" in result["fallback_command"]
    assert result["config_loaded"] is False


def test_stk_help_tool_uses_env_script(monkeypatch, tmp_path) -> None:
    kb_path = tmp_path / "stk11_help.sqlite3"
    kb_path.write_text("", encoding="utf-8")
    script_path = tmp_path / "stkhelp_cli.py"
    script_path.write_text(
        "import sys\nprint('RESULT:' + sys.argv[1])\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SMART_STK_HELP_KB", str(kb_path))
    monkeypatch.setenv("SMART_STK_HELP_SCRIPT", str(script_path))
    monkeypatch.setenv("SMART_STK_HELP_CONFIG", str(tmp_path / "missing_config.json"))
    monkeypatch.delenv("SMART_STKHELP_COMMAND", raising=False)
    executor = MissionAgentToolExecutor(tmp_path)

    result = executor.execute("query_stk_help", {"query": "Connect Units"})

    assert result["available"] is True
    assert result["script_path"] == str(script_path)
    assert result["output"] == "RESULT:Connect Units"


def test_stk_help_tool_uses_config_file(monkeypatch, tmp_path) -> None:
    kb_path = tmp_path / "stk11_help.sqlite3"
    kb_path.write_text("", encoding="utf-8")
    script_path = tmp_path / "stkhelp_cli.py"
    script_path.write_text(
        "import sys\nprint('CONFIG:' + sys.argv[1])\n",
        encoding="utf-8",
    )
    config_path = tmp_path / "stk_help.json"
    config_path.write_text(
        json.dumps(
            {
                "kb_path": str(kb_path),
                "script_path": str(script_path),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("SMART_STK_HELP_CONFIG", str(config_path))
    monkeypatch.delenv("SMART_STK_HELP_KB", raising=False)
    monkeypatch.delenv("SMART_STK_HELP_SCRIPT", raising=False)
    monkeypatch.delenv("SMART_STKHELP_COMMAND", raising=False)
    executor = MissionAgentToolExecutor(tmp_path)

    result = executor.execute("query_stk_help", {"query": "Connect Units"})

    assert result["available"] is True
    assert result["config_path"] == str(config_path)
    assert result["config_loaded"] is True
    assert result["kb_path"] == str(kb_path)
    assert result["script_path"] == str(script_path)
    assert result["output"] == "CONFIG:Connect Units"


def test_stk_help_env_overrides_config_file(monkeypatch, tmp_path) -> None:
    config_kb = tmp_path / "config.sqlite3"
    config_kb.write_text("", encoding="utf-8")
    env_kb = tmp_path / "env.sqlite3"
    env_kb.write_text("", encoding="utf-8")
    config_path = tmp_path / "stk_help.json"
    config_path.write_text(
        json.dumps({"kb_path": str(config_kb), "command": "config-command"}, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setenv("SMART_STK_HELP_CONFIG", str(config_path))
    monkeypatch.setenv("SMART_STK_HELP_KB", str(env_kb))
    monkeypatch.setenv("SMART_STKHELP_COMMAND", "env-command")
    monkeypatch.delenv("SMART_STK_HELP_SCRIPT", raising=False)

    status = resolve_stk_help_tool()

    assert status.available is True
    assert status.kb_path == env_kb
    assert status.command == ("env-command",)


def test_resolve_stk_help_tool_uses_env_command(monkeypatch, tmp_path) -> None:
    kb_path = tmp_path / "stk11_help.sqlite3"
    kb_path.write_text("", encoding="utf-8")
    monkeypatch.setenv("SMART_STK_HELP_CONFIG", str(tmp_path / "missing_config.json"))
    monkeypatch.setenv("SMART_STK_HELP_KB", str(kb_path))
    monkeypatch.setenv("SMART_STKHELP_COMMAND", "stkhelp-custom")
    monkeypatch.setenv("SMART_STK_HELP_SCRIPT", str(tmp_path / "missing.py"))

    status = resolve_stk_help_tool()

    assert status.available is True
    assert status.command == ("stkhelp-custom",)
    assert status.script_path is None


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


def test_pdf_report_table_symbols_and_widths_are_export_safe() -> None:
    rows = [
        ["发射时刻 UTC", "T0 UTC", "失败约束", "转移轨道", "一远点测控", "三次测控", "四次测控", "一次太阳角"],
        ["07:42", "08:17", "✅ 窗口打开", "✅", "✅", "✅", "✅", "✅"],
        ["09:08", "09:43", "第一次点火太阳角", "—", "—", "—", "—", "❌"],
    ]

    normalized = [[_normalize_pdf_table_cell(cell) for cell in row] for row in rows]
    widths = _table_col_widths(normalized, 480.0)

    assert normalized[1][2] == "√ 窗口打开"
    assert normalized[2][7] == "×"
    assert widths is not None
    assert round(sum(widths), 6) == 480.0
    assert widths[2] > widths[3]


def test_docx_and_pdf_exports_normalize_emoji_status_symbols(tmp_path) -> None:
    markdown = (
        "| 发射时刻 UTC | T0 UTC | 失败约束 | 转移轨道 | 一远点测控 | 一次太阳角 |\n"
        "| --- | --- | --- | --- | --- | --- |\n"
        "| 07:42 | 08:17 | ✅ 窗口打开 | ✅ | ✅ | ✅ |\n"
        "| 09:08 | 09:43 | 第一次点火太阳角 | — | — | ❌ |\n"
    )

    docx_path = export_docx_report(markdown, tmp_path / "symbols.docx")
    pdf_path = export_pdf_report(markdown, tmp_path / "symbols.pdf")

    with zipfile.ZipFile(docx_path) as package:
        document_xml = package.read("word/document.xml").decode("utf-8")

    assert "✅" not in document_xml
    assert "❌" not in document_xml
    assert "√ 窗口打开" in document_xml
    assert "×" in document_xml
    assert pdf_path.exists()
    assert pdf_path.stat().st_size > 0
