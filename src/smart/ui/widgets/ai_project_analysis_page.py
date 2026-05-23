from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
from typing import Any

from PySide6 import QtCore, QtGui, QtWidgets

from smart.services.llm_client import (
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL_V4_FLASH,
    DEEPSEEK_MODEL_V4_PRO,
    DEEPSEEK_REASONING_EFFORT_HIGH,
    DEEPSEEK_REASONING_EFFORT_MAX,
    DEFAULT_DEEPSEEK_MODEL,
    LLMResponse,
    LLMRequestConfig,
    request_chat_completion,
)
from smart.services.mission_agent import (
    render_mission_agent_manifest,
    render_mission_agent_summary,
    render_mission_agent_system_prompt,
    resolve_stk_help_tool,
)
from smart.services.mission_agent_tools import MissionAgentToolExecutor
from smart.services.project_ai_context import build_project_analysis_context, build_project_analysis_prompt
from smart.services.project_workspace import ProjectWorkspace
from smart.services.pdf_report_export import export_pdf_report
from smart.services.report_export import export_docx_report, export_markdown_report
from smart.ui.i18n import I18nManager
from smart.ui.widgets.spinboxes import NoWheelComboBox


REPORT_FILENAME = "ai_project_analysis.md"
DOCX_REPORT_FILENAME = "ai_project_analysis.docx"
PDF_REPORT_FILENAME = "ai_project_analysis.pdf"
PROMPT_TEMPLATE_PLACEHOLDER = "选择提示词模板..."
AI_ANALYSIS_PROMPT_TEMPLATES: tuple[tuple[str, str], ...] = (
    (
        "项目综合体检",
        """项目综合分析
请作为 SMART 航天器任务分析专家，对当前项目做一次端到端体检。

分析范围：
1. 汇总项目配置、关键数据文件、已有图表和缓存结果是否完整。
2. 检查变轨策略、发射窗口、跟踪弧段、地影约束、飞行程序之间是否存在明显不一致。
3. 如需补充事实，请优先调用 SMART 本地工具；不要凭经验估算已有工具可计算的结果。
4. 工具调用默认只读，除非我明确要求保存结果，不要使用 save_result=true。

输出要求：
- 先给出结论摘要。
- 列出高风险问题、证据路径、影响范围。
- 给出建议的下一步验证顺序。
- 标明哪些结论来自工具结果，哪些只是工程判断。""",
    ),
    (
        "设计变轨策略（脉冲）复核",
        """设计变轨策略（脉冲）复核
请重点分析当前项目的设计变轨策略脉冲规划。

请执行：
1. 读取当前项目摘要和设计变轨配置。
2. 调用 plan_design_maneuver_strategy，默认 save_result=false。
3. 复核每次点火的飞行时间、飞行圈次、拱点位置、星下点经度、delta-v、燃烧时长、推进剂、控后半长轴/偏心率/倾角。
4. 检查硬约束：燃烧时长、终端半长轴、偏心率、倾角、终端经度、经度窗口。

输出要求：
- 给出脉冲规划总览表。
- 列出未通过或裕度较小的约束。
- 说明哪些参数最值得调整。
- 不要改写项目配置；如果建议保存结果，请明确说明保存会写入哪些文件。""",
    ),
    (
        "连续推力优化复核",
        """连续推力优化复核
请重点分析当前项目的连续推力优化结果和脉冲规划之间的一致性。

请执行：
1. 如已有 data/design_maneuver_results.json，调用 optimize_design_continuous_thrust 并复用已归档脉冲结果。
2. 默认 save_result=false；除非我明确要求保存，不要写入连续推力结果文件。
3. 对比连续推力各段点火/关机时间、点火/关机经度、偏航角、delta-v、推进剂、控后轨道根数。
4. 检查 hard_constraint_passed 和 failed_constraints。

输出要求：
- 给出连续推力参数表。
- 对比脉冲规划与连续推力的关键差异。
- 解释失败约束的可能原因和最小调整建议。
- 给出是否适合导入变轨策略页继续计算的判断。""",
    ),
    (
        "发射窗口约束重采样",
        """发射窗口约束重采样
请重点分析当前项目的发射窗口约束和可用窗口。

请执行：
1. 检查 data/full_orbit_history.csv、config/maneuver_strategy.json、config/launch_window.json 是否存在。
2. 调用 compute_launch_window_samples，默认 save_result=false。
3. 复核采样范围、采样步长、火箭飞行时间、最短窗口长度、地影、测控、太阳角、倾角等约束。
4. 如果需要变更参数，请只给建议，不要直接写配置。

输出要求：
- 给出窗口数量、通过样本数、主要失败约束。
- 列出最长/最短窗口和窗口前沿/后沿限制条件。
- 说明哪些约束最限制窗口宽度。
- 给出重新生成 CSV 和甘特图的建议。""",
    ),
    (
        "跟踪弧段与地影风险",
        """跟踪弧段与地影风险分析
请重点分析测控资源、跟踪弧段和地影风险。

请执行：
1. 汇总当前项目中的地面站、中继星、跟踪弧段配置和发射窗口结果。
2. 对关键发射时刻或窗口前沿，调用 compute_shadow_intervals_for_launch 计算地影区间。
3. 结合发射窗口样本中的 max_tracking_gap_min、first_orbit_shadow_min、longest_shadow_min 判断风险。
4. 若需要 STK 命令或 API 依据，调用 query_stk_help 查询 STK 11.6 帮助。

输出要求：
- 给出地影区间表、最长地影、总地影时长。
- 给出测控空窗和中继/地面站覆盖风险。
- 指出需要在 STK 或页面中复核的具体对象和时间段。""",
    ),
    (
        "飞行程序与结果一致性",
        """飞行程序与结果一致性检查
请重点检查飞行程序设计、变轨策略、发射窗口和 STK 同步结果之间是否一致。

分析重点：
1. 飞行程序中的关键事件时间是否与发射窗口 T0、变轨点火时刻、跟踪弧段一致。
2. 姿态模式、测控事件、点火事件是否覆盖关键风险时段。
3. 项目中已生成的 CSV、JSON、报告和图表是否来自同一轮参数。
4. 如果需要 STK 11.6 命令依据，调用 query_stk_help；不要使用 STK 12.2 专属行为。

输出要求：
- 按“时间基准/变轨/测控/地影/STK 同步”分组列出问题。
- 给出每项问题的证据文件和建议复核页面。
- 给出下一步最小修复任务。""",
    ),
)


class _ProjectAnalysisWorker(QtCore.QObject):
    progress = QtCore.Signal(str)
    finished = QtCore.Signal(str, str)
    failed = QtCore.Signal(str)

    def __init__(
        self,
        *,
        project_root: Path,
        data_dir: Path,
        config: LLMRequestConfig,
        scope: str,
        question: str,
    ) -> None:
        super().__init__()
        self._project_root = project_root
        self._data_dir = data_dir
        self._config = config
        self._scope = scope
        self._question = question

    @QtCore.Slot()
    def run(self) -> None:
        try:
            self.progress.emit(f"[工具] build_project_analysis_context(project_root={self._project_root})")
            context = build_project_analysis_context(self._project_root)
            self.progress.emit(f"[结果] 项目摘要构建完成，字符数 {len(context):,}")
            self.progress.emit(f"[工具] build_project_analysis_prompt(scope={self._scope!r})")
            prompt = build_project_analysis_prompt(context, scope=self._scope, question=self._question)
            self.progress.emit(f"[结果] LLM prompt 已组装，字符数 {len(prompt):,}")
            self.progress.emit("[阶段] 正在调用 DeepSeek 生成 AI 分析报告。")
            self.progress.emit(
                "[DeepSeek] request_chat_completion("
                f"model={self._config.model}, base_url={self._config.base_url}, "
                f"reasoning_effort={self._config.reasoning_effort}, "
                f"thinking_enabled={self._config.thinking_enabled})"
            )
            executor = MissionAgentToolExecutor(self._project_root)
            response = request_chat_completion(
                self._config,
                prompt,
                system_prompt=render_mission_agent_system_prompt(),
                tools=executor.tool_specs(),
                tool_executor=executor.execute,
                progress_callback=self.progress.emit,
            )
            self.progress.emit(
                f"[结果] DeepSeek 返回完成，正文 {len(response.content):,} 字符，"
                f"思考 {len(response.reasoning_content):,} 字符，工具调用 {response.tool_call_count} 次"
            )
            self.progress.emit("[阶段] DeepSeek 输出完成，正在渲染并保存 Markdown 报告。")
            report = self._render_report(response)
            self._data_dir.mkdir(parents=True, exist_ok=True)
            path = self._data_dir / REPORT_FILENAME
            self.progress.emit(f"[工具] write_text(path={path})")
            path.write_text(report, encoding="utf-8")
            self.progress.emit("[阶段] 报告文件写入完成。")
        except Exception as exc:
            self.progress.emit(f"[错误] {exc}")
            self.failed.emit(str(exc))
            return
        self.finished.emit(report, str(path))

    def _render_report(self, response: LLMResponse) -> str:
        generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        return (
            "# AI 项目分析报告\n\n"
            f"- 生成时间 UTC：{generated_at}\n"
            f"- 项目目录：`{self._project_root}`\n"
            f"- 分析范围：{self._scope}\n"
            f"- 模型：`{self._config.model}`\n\n"
            f"{response.content.strip()}\n"
        )


class AIProjectAnalysisPage(QtWidgets.QWidget):
    def __init__(
        self,
        i18n: I18nManager,
        workspace: ProjectWorkspace,
        settings: QtCore.QSettings,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._i18n = i18n
        self._workspace = workspace
        self._settings = settings
        self._thread: QtCore.QThread | None = None
        self._worker: _ProjectAnalysisWorker | None = None
        self._reasoning_buffer = ""
        self._reasoning_active = False
        self._current_report_markdown = ""
        self._report_loading = False

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(18)

        self._title_label = QtWidgets.QLabel()
        self._title_label.setProperty("role", "pageTitle")
        root.addWidget(self._title_label)

        self._subtitle_label = QtWidgets.QLabel()
        self._subtitle_label.setProperty("role", "pageBody")
        self._subtitle_label.setWordWrap(True)
        root.addWidget(self._subtitle_label)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_control_panel())
        splitter.addWidget(self._build_output_panel())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        root.addWidget(splitter, 1)

        self._load_settings()
        self._i18n.language_changed.connect(self.retranslate)
        self.retranslate()

    def _build_control_panel(self) -> QtWidgets.QWidget:
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setMinimumWidth(390)
        scroll.setMaximumWidth(520)

        panel = QtWidgets.QWidget()
        scroll.setWidget(panel)
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 14, 0)
        layout.setSpacing(14)

        task_card = self._card()
        task_layout = QtWidgets.QVBoxLayout(task_card)
        task_layout.setContentsMargins(18, 18, 18, 18)
        task_layout.setSpacing(12)

        task_title = QtWidgets.QLabel("分析任务")
        task_title.setProperty("role", "cardTitle")
        task_layout.addWidget(task_title)

        prompt_label = QtWidgets.QLabel("分析提示词")
        prompt_label.setProperty("role", "cardCaption")
        task_layout.addWidget(prompt_label)

        self._prompt_template_combo = NoWheelComboBox()
        self._prompt_template_combo.setEditable(False)
        self._prompt_template_combo.addItem(PROMPT_TEMPLATE_PLACEHOLDER, "")
        for label, template in AI_ANALYSIS_PROMPT_TEMPLATES:
            self._prompt_template_combo.addItem(label, template)
        self._prompt_template_combo.currentIndexChanged.connect(self._apply_prompt_template)
        task_layout.addWidget(self._prompt_template_combo)

        self._question_edit = QtWidgets.QPlainTextEdit()
        self._question_edit.setPlaceholderText(
            "选择上方模板后可直接修改；也可以直接写分析范围、关注内容和输出要求。"
        )
        self._question_edit.setFixedHeight(184)
        task_layout.addWidget(self._question_edit)

        button_row = QtWidgets.QHBoxLayout()
        button_row.setSpacing(10)
        self._preview_button = QtWidgets.QPushButton("生成执行预检")
        self._preview_button.clicked.connect(self.preview_context)
        self._analyze_button = QtWidgets.QPushButton("AI 分析当前项目")
        self._analyze_button.setProperty("variant", "primaryAction")
        self._analyze_button.clicked.connect(self.run_analysis)
        button_row.addWidget(self._preview_button)
        button_row.addWidget(self._analyze_button)
        task_layout.addLayout(button_row)

        layout.addWidget(task_card)

        preflight_card = self._card()
        preflight_layout = QtWidgets.QVBoxLayout(preflight_card)
        preflight_layout.setContentsMargins(18, 18, 18, 18)
        preflight_layout.setSpacing(10)
        preflight_title = QtWidgets.QLabel("项目预检")
        preflight_title.setProperty("role", "cardTitle")
        preflight_layout.addWidget(preflight_title)
        self._preflight_view = QtWidgets.QPlainTextEdit()
        self._preflight_view.setReadOnly(True)
        self._preflight_view.setMaximumBlockCount(200)
        self._preflight_view.setFixedHeight(150)
        self._preflight_view.setPlainText("打开项目后点击“生成执行预检”，确认摘要范围、工具状态和安全边界。")
        preflight_layout.addWidget(self._preflight_view)
        layout.addWidget(preflight_card)

        api_card = QtWidgets.QGroupBox("模型配置")
        api_card.setCheckable(True)
        api_card.setChecked(False)
        self._api_group = api_card
        api_layout = QtWidgets.QFormLayout(api_card)
        api_layout.setContentsMargins(18, 18, 18, 18)
        api_layout.setSpacing(12)

        self._base_url_edit = QtWidgets.QLineEdit()
        self._base_url_edit.setClearButtonEnabled(True)
        api_layout.addRow("DeepSeek base_url", self._base_url_edit)

        self._api_key_edit = QtWidgets.QLineEdit()
        self._api_key_edit.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
        self._api_key_edit.setClearButtonEnabled(True)
        api_layout.addRow("api_key", self._api_key_edit)

        self._model_combo = NoWheelComboBox()
        self._model_combo.setEditable(False)
        for label, model in (
            ("deepseek-v4-pro", DEEPSEEK_MODEL_V4_PRO),
            ("deepseek-v4-flash", DEEPSEEK_MODEL_V4_FLASH),
        ):
            self._model_combo.addItem(label, model)
        api_layout.addRow("model*", self._model_combo)

        self._reasoning_effort_combo = NoWheelComboBox()
        self._reasoning_effort_combo.addItem("high", DEEPSEEK_REASONING_EFFORT_HIGH)
        self._reasoning_effort_combo.addItem("max", DEEPSEEK_REASONING_EFFORT_MAX)
        api_layout.addRow("reasoning_effort", self._reasoning_effort_combo)

        self._thinking_checkbox = QtWidgets.QCheckBox("启用 DeepSeek thinking / reasoning_content")
        api_layout.addRow("thinking", self._thinking_checkbox)

        self._save_settings_button = QtWidgets.QPushButton("保存 API 配置")
        self._save_settings_button.clicked.connect(self._save_settings)
        api_layout.addRow("", self._save_settings_button)

        layout.addWidget(api_card)

        agent_card = QtWidgets.QGroupBox("本地工具与专家")
        agent_card.setCheckable(True)
        agent_card.setChecked(False)
        self._agent_group = agent_card
        agent_layout = QtWidgets.QVBoxLayout(agent_card)
        agent_layout.setContentsMargins(18, 18, 18, 18)
        agent_layout.setSpacing(10)

        self._agent_summary_label = QtWidgets.QLabel(render_mission_agent_summary())
        self._agent_summary_label.setProperty("role", "pageBody")
        self._agent_summary_label.setWordWrap(True)
        agent_layout.addWidget(self._agent_summary_label)

        self._agent_skills_view = QtWidgets.QPlainTextEdit(render_mission_agent_manifest())
        self._agent_skills_view.setReadOnly(True)
        self._agent_skills_view.setFixedHeight(150)
        agent_layout.addWidget(self._agent_skills_view)

        layout.addWidget(agent_card)
        api_card.toggled.connect(lambda checked: self._set_group_body_visible(api_card, checked))
        agent_card.toggled.connect(lambda checked: self._set_group_body_visible(agent_card, checked))
        self._set_group_body_visible(api_card, False)
        self._set_group_body_visible(agent_card, False)

        self._status_label = QtWidgets.QLabel()
        self._status_label.setProperty("role", "pageBody")
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)
        layout.addStretch(1)
        return scroll

    def _build_output_panel(self) -> QtWidgets.QWidget:
        panel = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        report_card = self._card()
        report_card.setMinimumHeight(520)
        report_layout = QtWidgets.QVBoxLayout(report_card)
        report_layout.setContentsMargins(18, 18, 18, 18)
        report_layout.setSpacing(10)
        report_header = QtWidgets.QHBoxLayout()
        report_title = QtWidgets.QLabel("AI 分析报告")
        report_title.setProperty("role", "cardTitle")
        report_header.addWidget(report_title)
        report_header.addStretch(1)
        self._run_state_label = QtWidgets.QLabel("待生成")
        self._run_state_label.setProperty("role", "cardCaption")
        report_header.addWidget(self._run_state_label)
        self._export_md_button = QtWidgets.QPushButton("导出 MD")
        self._export_md_button.clicked.connect(self._export_markdown_report)
        self._export_docx_button = QtWidgets.QPushButton("导出 DOCX")
        self._export_docx_button.clicked.connect(self._export_docx_report)
        self._export_pdf_button = QtWidgets.QPushButton("导出 PDF")
        self._export_pdf_button.clicked.connect(self._export_pdf_report)
        report_header.addWidget(self._export_md_button)
        report_header.addWidget(self._export_docx_button)
        report_header.addWidget(self._export_pdf_button)
        report_layout.addLayout(report_header)
        self._report_view = QtWidgets.QTextBrowser()
        self._report_view.setReadOnly(True)
        self._report_view.setOpenExternalLinks(True)
        self._report_view.setPlaceholderText(f"分析完成后，报告会显示在这里并保存到 data/{REPORT_FILENAME}。")
        report_layout.addWidget(self._report_view)
        layout.addWidget(report_card, 1)

        self._trace_toggle = QtWidgets.QToolButton()
        self._trace_toggle.setText("查看执行日志")
        self._trace_toggle.setCheckable(True)
        self._trace_toggle.setChecked(False)
        self._trace_toggle.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._trace_toggle.toggled.connect(self._set_trace_visible)
        layout.addWidget(self._trace_toggle, 0)

        self._trace_card = self._card()
        self._trace_card.setMaximumHeight(240)
        preview_layout = QtWidgets.QVBoxLayout(self._trace_card)
        preview_layout.setContentsMargins(18, 18, 18, 18)
        preview_layout.setSpacing(10)
        preview_title = QtWidgets.QLabel("执行过程与工具调用")
        preview_title.setProperty("role", "cardTitle")
        preview_layout.addWidget(preview_title)
        self._trace_view = QtWidgets.QPlainTextEdit()
        self._trace_view.setReadOnly(True)
        self._trace_view.setPlaceholderText(
            "这里显示 DeepSeek reasoning_content、SMART 本地工具调用、LLM API 调用和报告保存情况。"
        )
        self._trace_view.setMinimumHeight(100)
        self._trace_view.setMaximumHeight(150)
        preview_layout.addWidget(self._trace_view)
        layout.addWidget(self._trace_card, 0)
        self._set_trace_visible(False)
        self._update_export_buttons()
        return panel

    def preview_context(self) -> None:
        if self._workspace.current_project is None or self._workspace.root_dir is None:
            self._set_status("请先打开一个 SMART 项目。")
            return
        self._reset_trace()
        self._append_trace("[说明] 这里显示 DeepSeek API 返回的 reasoning_content，以及 SMART 本地工具调用轨迹。")
        self._append_trace(f"[工具] build_project_analysis_context(project_root={self._workspace.root_dir})")
        try:
            context = build_project_analysis_context(self._workspace.root_dir)
        except Exception as exc:
            self._append_trace(f"[错误] 构建项目摘要失败：{exc}")
            self._set_status(f"构建项目摘要失败：{exc}")
            return
        self._append_trace(f"[结果] 项目摘要构建完成，字符数 {len(context):,}")
        scope = self._analysis_scope()
        self._append_trace(f"[工具] build_project_analysis_prompt(scope={scope!r})")
        prompt = build_project_analysis_prompt(
            context,
            scope=scope,
            question=self._question_edit.toPlainText(),
        )
        self._append_trace(f"[结果] 待发送 LLM prompt 已组装，字符数 {len(prompt):,}")
        self._append_trace(
            "[待执行] request_chat_completion(...) 将以 DeepSeek V4 + thinking + tool calls 调用。"
        )
        self._append_trace(
            "[工具] 已注册 build_project_context/find_launch_windows/compute_shadow_intervals_for_launch/"
            "plan_design_maneuver_strategy/optimize_design_continuous_thrust/"
            "compute_launch_window_samples/query_stk_help"
        )
        self._append_stk_help_status()
        self._set_preflight_summary(context=context, prompt=prompt)
        self._set_status("执行预检完成。项目摘要、工具状态和安全边界已更新。")

    def run_analysis(self) -> None:
        if self._thread is not None:
            self._set_status("已有 AI 分析任务正在运行。")
            return
        if self._workspace.current_project is None or self._workspace.root_dir is None:
            self._set_status("请先打开一个 SMART 项目。")
            return
        try:
            config = self._request_config()
        except ValueError as exc:
            self._set_status(str(exc))
            return

        self._save_settings()
        self._set_busy(True)
        self._set_report_loading_message("正在生成 AI 分析报告", "正在构建项目摘要，随后会调用 DeepSeek 生成分析正文。")
        self._update_export_buttons()
        self._reset_trace()
        self._append_trace("[说明] 这里显示 DeepSeek API 返回的 reasoning_content，以及 SMART 本地工具调用轨迹。")
        self._append_trace("[开始] AI 分析当前项目")
        self._append_stk_help_status()
        self._set_preflight_summary()

        self._thread = QtCore.QThread(self)
        self._worker = _ProjectAnalysisWorker(
            project_root=self._workspace.root_dir,
            data_dir=self._workspace.data_dir(),
            config=config,
            scope=self._analysis_scope(),
            question=self._question_edit.toPlainText(),
        )
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._handle_worker_progress)
        self._worker.finished.connect(self._on_analysis_finished)
        self._worker.failed.connect(self._on_analysis_failed)
        self._worker.finished.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.finished.connect(self._clear_thread)
        self._thread.start()

    def _request_config(self) -> LLMRequestConfig:
        base_url = self._base_url_edit.text().strip()
        api_key = self._api_key_edit.text().strip()
        model = self._selected_model()
        if not base_url:
            raise ValueError("请填写 DeepSeek base_url。")
        if not api_key:
            raise ValueError("请填写 api_key，或设置 SMART_LLM_API_KEY / DEEPSEEK_API_KEY 环境变量。")
        if not model:
            raise ValueError("请选择或填写 model。")
        return LLMRequestConfig(
            base_url=base_url,
            api_key=api_key,
            model=model,
            reasoning_effort=str(self._reasoning_effort_combo.currentData() or DEEPSEEK_REASONING_EFFORT_HIGH),
            thinking_enabled=self._thinking_checkbox.isChecked(),
        )

    def _selected_model(self) -> str:
        text = self._model_combo.currentText().strip()
        for index in range(self._model_combo.count()):
            if text == self._model_combo.itemText(index):
                data = self._model_combo.itemData(index)
                return str(data or text).strip()
        return text

    def _on_analysis_finished(self, report: str, path: str) -> None:
        self._set_report_markdown(report)
        self._append_trace(f"[完成] AI 分析完成，报告已保存：{path}")
        self._set_status(f"AI 分析完成，报告已保存：{path}")
        self._set_run_state("完成")
        self._set_busy(False)

    def _on_analysis_failed(self, error: str) -> None:
        self._append_trace(f"[失败] AI 分析失败：{error}")
        self._set_report_error_message(error)
        if "LLM" in error or "HTTP" in error:
            self._set_status(f"AI 分析失败：{error}")
        else:
            self._set_status(f"项目分析失败：{error}")
        self._set_run_state("失败")
        self._set_busy(False)

    def _clear_thread(self) -> None:
        self._thread = None
        self._worker = None

    def _load_settings(self) -> None:
        self._base_url_edit.setText(
            self._setting_text("ai/deepseek_base_url", os.environ.get("SMART_LLM_BASE_URL", DEEPSEEK_BASE_URL))
        )
        api_key = self._setting_text("ai/api_key", "")
        if not api_key:
            api_key = os.environ.get("SMART_LLM_API_KEY") or os.environ.get("DEEPSEEK_API_KEY") or ""
        self._api_key_edit.setText(api_key)
        model = self._setting_text("ai/model", os.environ.get("SMART_LLM_MODEL", DEFAULT_DEEPSEEK_MODEL))
        self._set_model(model)
        effort = self._setting_text("ai/reasoning_effort", DEEPSEEK_REASONING_EFFORT_HIGH)
        effort_index = self._reasoning_effort_combo.findData(effort)
        self._reasoning_effort_combo.setCurrentIndex(max(0, effort_index))
        thinking_enabled = self._settings.value("ai/thinking_enabled", True, type=bool)
        self._thinking_checkbox.setChecked(bool(thinking_enabled))

    def _save_settings(self) -> None:
        self._settings.setValue("ai/deepseek_base_url", self._base_url_edit.text().strip())
        self._settings.setValue("ai/model", self._selected_model())
        self._settings.setValue("ai/reasoning_effort", self._reasoning_effort_combo.currentData())
        self._settings.setValue("ai/thinking_enabled", self._thinking_checkbox.isChecked())
        self._settings.setValue("ai/api_key", self._api_key_edit.text().strip())
        self._settings.sync()
        self._set_status("DeepSeek API 配置已保存到本机设置。")

    def _setting_text(self, key: str, default: str) -> str:
        value = self._settings.value(key, default)
        return str(value if value is not None else default)

    def _set_model(self, model: str) -> None:
        for index in range(self._model_combo.count()):
            if str(self._model_combo.itemData(index)) == model:
                self._model_combo.setCurrentIndex(index)
                return
        self._model_combo.setCurrentIndex(0)

    @QtCore.Slot(int)
    def _apply_prompt_template(self, index: int) -> None:
        if index <= 0:
            return
        template = str(self._prompt_template_combo.itemData(index) or "").strip()
        if not template:
            return
        self._question_edit.setPlainText(template)
        cursor = self._question_edit.textCursor()
        cursor.movePosition(QtGui.QTextCursor.MoveOperation.End)
        self._question_edit.setTextCursor(cursor)
        self._question_edit.setFocus(QtCore.Qt.FocusReason.OtherFocusReason)
        self._set_status("已套用提示词模板，可在文本框中继续修改。")

    def _set_busy(self, busy: bool) -> None:
        for widget in (
            self._base_url_edit,
            self._api_key_edit,
            self._model_combo,
            self._reasoning_effort_combo,
            self._thinking_checkbox,
            self._prompt_template_combo,
            self._question_edit,
            self._preview_button,
            self._analyze_button,
            self._save_settings_button,
        ):
            widget.setEnabled(not busy)
        self._set_run_state("生成中" if busy else ("报告就绪" if self._current_report_markdown.strip() else "待生成"))
        self._update_export_buttons(busy=busy)

    def _set_report_markdown(self, report: str) -> None:
        self._current_report_markdown = report
        self._report_loading = False
        try:
            markdown_features = getattr(QtGui.QTextDocument, "MarkdownFeature", None)
            github_dialect = getattr(markdown_features, "MarkdownDialectGitHub", None)
            if github_dialect is not None:
                self._report_view.document().setMarkdown(report, github_dialect)
            else:
                self._report_view.setMarkdown(report)
        except Exception:
            self._report_view.setPlainText(report)
        self._update_export_buttons()

    def _set_report_loading_message(self, title: str, detail: str) -> None:
        self._report_loading = True
        self._current_report_markdown = ""
        message = (
            f"## {title}\n\n"
            f"{detail}\n\n"
            "- 当前页面会持续更新“执行日志”。\n"
            "- DeepSeek 输出完成后，这里会自动替换为格式化后的 Markdown 报告。"
        )
        try:
            self._report_view.setMarkdown(message)
        except Exception:
            self._report_view.setPlainText(f"{title}\n\n{detail}")
        self._set_status(detail)
        self._update_export_buttons()

    def _set_report_error_message(self, error: str) -> None:
        self._report_loading = False
        self._current_report_markdown = ""
        message = (
            "## AI 分析报告生成失败\n\n"
            f"{error}\n\n"
            "请检查 DeepSeek API 配置、网络连接或项目数据后重新生成。"
        )
        try:
            self._report_view.setMarkdown(message)
        except Exception:
            self._report_view.setPlainText(f"AI 分析报告生成失败\n\n{error}")
        self._update_export_buttons()

    def _update_export_buttons(self, *, busy: bool | None = None) -> None:
        enabled = bool(self._current_report_markdown.strip())
        if busy is None:
            busy = self._thread is not None
        self._export_pdf_button.setEnabled(enabled and not busy)
        self._export_md_button.setEnabled(enabled and not busy)
        self._export_docx_button.setEnabled(enabled and not busy)

    @QtCore.Slot()
    def _export_markdown_report(self) -> None:
        if not self._current_report_markdown.strip():
            self._set_status("当前没有可导出的 AI 分析报告。")
            return
        default_path = self._default_export_path(REPORT_FILENAME)
        file_path, _selected_filter = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "导出 Markdown 报告",
            str(default_path),
            "Markdown 文件 (*.md);;所有文件 (*)",
        )
        if not file_path:
            return
        try:
            output_path = export_markdown_report(
                self._current_report_markdown,
                self._normalized_export_path(file_path, ".md"),
            )
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "导出失败", f"导出 Markdown 报告失败：{exc}")
            self._set_status(f"导出 Markdown 报告失败：{exc}")
            return
        self._set_status(f"Markdown 报告已导出：{output_path}")

    @QtCore.Slot()
    def _export_docx_report(self) -> None:
        if not self._current_report_markdown.strip():
            self._set_status("当前没有可导出的 AI 分析报告。")
            return
        default_path = self._default_export_path(DOCX_REPORT_FILENAME)
        file_path, _selected_filter = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "导出 DOCX 报告",
            str(default_path),
            "Word 文档 (*.docx);;所有文件 (*)",
        )
        if not file_path:
            return
        try:
            output_path = export_docx_report(
                self._current_report_markdown,
                self._normalized_export_path(file_path, ".docx"),
            )
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "导出失败", f"导出 DOCX 报告失败：{exc}")
            self._set_status(f"导出 DOCX 报告失败：{exc}")
            return
        self._set_status(f"DOCX 报告已导出：{output_path}")

    @QtCore.Slot()
    def _export_pdf_report(self) -> None:
        if not self._current_report_markdown.strip():
            self._set_status("当前没有可导出的 AI 分析报告。")
            return
        default_path = self._default_export_path(PDF_REPORT_FILENAME)
        file_path, _selected_filter = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "导出 PDF 报告",
            str(default_path),
            "PDF 文档 (*.pdf);;所有文件 (*)",
        )
        if not file_path:
            return
        try:
            project = self._workspace.current_project
            project_name = project.name if project is not None else ""
            output_path = export_pdf_report(
                self._current_report_markdown,
                self._normalized_export_path(file_path, ".pdf"),
                project_name=project_name,
            )
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "导出失败", f"导出 PDF 报告失败：{exc}")
            self._set_status(f"导出 PDF 报告失败：{exc}")
            return
        self._set_status(f"PDF 报告已导出：{output_path}")

    def _default_export_path(self, filename: str) -> Path:
        if self._workspace.current_project is not None:
            return self._workspace.data_dir() / filename
        return Path.home() / filename

    def _normalized_export_path(self, file_path: str, suffix: str) -> Path:
        output_path = Path(file_path)
        if not output_path.suffix:
            output_path = output_path.with_suffix(suffix)
        return output_path

    def _set_status(self, text: str) -> None:
        self._status_label.setText(text)

    def _analysis_scope(self) -> str:
        text = self._question_edit.toPlainText().strip()
        if not text:
            return "项目综合分析"
        first_line = text.splitlines()[0].strip()
        return first_line[:80] or "用户提示词分析"

    def _set_run_state(self, text: str) -> None:
        if hasattr(self, "_run_state_label"):
            self._run_state_label.setText(text)

    def _set_group_body_visible(self, group: QtWidgets.QGroupBox, visible: bool) -> None:
        for child in group.findChildren(
            QtWidgets.QWidget,
            options=QtCore.Qt.FindChildOption.FindDirectChildrenOnly,
        ):
            child.setVisible(visible)
        group.setMaximumHeight(16_777_215 if visible else 44)
        group.updateGeometry()

    def _set_trace_visible(self, visible: bool) -> None:
        if hasattr(self, "_trace_card"):
            self._trace_card.setVisible(visible)
        if hasattr(self, "_trace_toggle"):
            self._trace_toggle.setText("隐藏执行日志" if visible else "查看执行日志")

    def _set_preflight_summary(self, *, context: str | None = None, prompt: str | None = None) -> None:
        if not hasattr(self, "_preflight_view"):
            return
        if self._workspace.current_project is None or self._workspace.root_dir is None:
            self._preflight_view.setPlainText("未打开项目。")
            return
        project_root = self._workspace.root_dir
        config_count = len(list((project_root / "config").glob("*.json"))) if (project_root / "config").exists() else 0
        data_files = list((project_root / "data").glob("*")) if (project_root / "data").exists() else []
        chart_count = len(list((project_root / "charts").glob("*"))) if (project_root / "charts").exists() else 0
        status = resolve_stk_help_tool()
        lines = [
            f"项目：{project_root.name}",
            f"配置摘要：config/*.json 共 {config_count} 个",
            f"数据文件：data/* 共 {len(data_files)} 个",
            f"图表文件：charts/* 共 {chart_count} 个",
            f"STK Help：{'可用' if status.available else '不可用'}",
        ]
        if context is not None:
            lines.append(f"项目摘要字符数：{len(context):,}")
        if prompt is not None:
            lines.append(f"待发送 prompt 字符数：{len(prompt):,}")
        lines.extend(
            [
                "",
                "安全边界：",
                "- 只发送项目摘要、统计和少量样本",
                "- 不发送完整大 CSV、二进制、SPICE kernels、tmp 文件",
                "- 不写入项目配置，不上传 API key",
            ]
        )
        self._preflight_view.setPlainText("\n".join(lines))

    def _append_stk_help_status(self) -> None:
        status = resolve_stk_help_tool()
        state = "可用" if status.available else "不可用"
        config_state = "已加载" if status.config_loaded else "未加载"
        self._append_trace(
            f"[工具] STK Help {state}；配置={status.config_path}（{config_state}）；"
            f"KB={status.kb_path}；命令={status.display_command()}"
        )
        if not status.available:
            self._append_trace(
                "[提示] 可设置 SMART_STK_HELP_CONFIG 配置文件，或设置 SMART_STK_HELP_KB、"
                "SMART_STK_HELP_SCRIPT、SMART_STKHELP_COMMAND 来启用 STK Help。"
            )

    @QtCore.Slot(str)
    def _handle_worker_progress(self, text: str) -> None:
        self._append_trace(text)
        if text.startswith("[工具] build_project_analysis_context"):
            self._set_report_loading_message("正在生成 AI 分析报告", "正在构建项目摘要。")
        elif text.startswith("[结果] LLM prompt 已组装"):
            self._set_report_loading_message("正在生成 AI 分析报告", "项目摘要已完成，正在准备 DeepSeek 请求。")
        elif text.startswith("[DeepSeek] request round"):
            self._set_report_loading_message("正在生成 AI 分析报告", "DeepSeek 请求已发送，正在等待模型响应。")
        elif text.startswith("[DeepSeek 思考流] "):
            if self._report_loading:
                self._set_status("DeepSeek 正在思考并生成报告。")
        elif text.startswith("[DeepSeek] 正在生成报告正文"):
            self._set_report_loading_message("正在生成 AI 分析报告", "DeepSeek 正在输出报告正文。")
        elif text.startswith("[工具调用]"):
            self._set_report_loading_message("正在生成 AI 分析报告", "DeepSeek 正在调用 SMART 本地工具补充计算结果。")
        elif text.startswith("[工具结果]"):
            self._set_status("SMART 工具结果已返回，DeepSeek 将继续生成报告。")
        elif text.startswith("[阶段] DeepSeek 输出完成"):
            self._set_report_loading_message("正在整理 AI 分析报告", "DeepSeek 输出完成，正在渲染并保存 Markdown 报告。")
        elif text.startswith("[工具] write_text"):
            self._set_status("正在写入 AI 分析报告文件。")

    @QtCore.Slot(str)
    def _append_trace(self, text: str) -> None:
        if text.startswith("[DeepSeek 思考流] "):
            self._append_reasoning_delta(text.removeprefix("[DeepSeek 思考流] "))
            return
        self._flush_reasoning_buffer()
        timestamp = datetime.now().strftime("%H:%M:%S")
        self._trace_view.appendPlainText(f"{timestamp} {text}")

    def _reset_trace(self) -> None:
        self._reasoning_buffer = ""
        self._reasoning_active = False
        self._trace_view.clear()

    def _append_reasoning_delta(self, delta: str) -> None:
        if not delta:
            return
        self._reasoning_buffer += delta
        if not self._reasoning_active:
            timestamp = datetime.now().strftime("%H:%M:%S")
            self._trace_view.appendPlainText(f"{timestamp} [DeepSeek 思考]")
            self._reasoning_active = True
        self._rewrite_last_reasoning_block()

    def _flush_reasoning_buffer(self) -> None:
        if not self._reasoning_active:
            return
        self._rewrite_last_reasoning_block()
        self._trace_view.appendPlainText("")
        self._reasoning_buffer = ""
        self._reasoning_active = False

    def _rewrite_last_reasoning_block(self) -> None:
        cursor = self._trace_view.textCursor()
        cursor.movePosition(QtGui.QTextCursor.MoveOperation.End)
        cursor.movePosition(QtGui.QTextCursor.MoveOperation.StartOfBlock, QtGui.QTextCursor.MoveMode.KeepAnchor)
        cursor.removeSelectedText()
        cursor.insertText(self._formatted_reasoning_preview())
        self._trace_view.setTextCursor(cursor)

    def _formatted_reasoning_preview(self) -> str:
        text = self._reasoning_buffer.strip()
        if len(text) > 6000:
            text = text[-6000:]
            text = "[...前文已折叠]\n" + text
        return text

    def retranslate(self, _language: str | None = None) -> None:
        self._title_label.setText(self._i18n.t("ai_project.title"))
        self._subtitle_label.setText(self._i18n.t("ai_project.subtitle"))

    @staticmethod
    def _card() -> QtWidgets.QFrame:
        card = QtWidgets.QFrame()
        card.setProperty("role", "card")
        return card
