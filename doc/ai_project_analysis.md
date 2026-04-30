# AI 项目分析页面说明

SMART 的“AI 项目分析”页面用于通过大语言模型分析当前项目的配置和数据。页面会先构建项目摘要，并允许 DeepSeek 通过 tool calls 调用受控的 SMART 本地工具读取项目文件、查找发射窗口、计算指定发射时刻的地影区间或查询 STK 11.6 帮助，最后生成 Markdown 报告。

页面内置一个“SMART 航天器任务分析专家”agent profile。每次分析时，SMART 会把该 profile 和项目摘要一起注入 prompt，并把同一 profile 作为模型 system prompt 的基础。

## 1. 支持的接口

页面固定使用 DeepSeek V4 Chat Completion API：

- DeepSeek base URL：默认 `https://api.deepseek.com`

默认模型下拉项包括：

- `deepseek-v4-pro`
- `deepseek-v4-flash`

页面不再保留 Anthropic-compatible 或旧版 `deepseek-chat` / `deepseek-reasoner` 入口。旧模型别名会在 2026-07-24 弃用，SMART 只面向 V4 维护。

DeepSeek V4 调用默认启用：

- `stream=True`
- `stream_options={"include_usage": True}`
- `reasoning_effort="high"` 或 `"max"`
- `thinking={"type": "enabled"}`
- `tools` + `tool_choice="auto"` 的 tool calls 流程

## 2. API key 管理

API key 可通过页面输入，也可通过环境变量提供：

- `SMART_LLM_API_KEY`
- `DEEPSEEK_API_KEY`

页面点击“保存 API 配置”后，会把 base URL、模型名、接口类型和 API key 保存到本机 `QSettings`。这些信息不会写入项目目录下的 `config/*.json`。

## 3. 发送给模型的数据范围

页面不会直接上传整个项目目录，也不会上传二进制文件。发送前会构建项目摘要，主要包含：

- `smart_project.json`
- `config/*.json`
- `data/orbit_elements.json`
- `data/maneuver_snapshot.json`
- `data/full_orbit_history.csv` 的列名、行数、数值统计和少量样本行
- `data/launch_window_samples.csv` 的列名、行数、数值统计和少量样本行
- `data/launch_window_results.csv`
- 项目 `config/`、`data/`、`charts/` 文件清单

点击“生成执行预检”可以在调用模型前查看可公开的执行步骤、SMART 本地服务调用和待执行的 LLM API 调用。运行时页面会显示 DeepSeek API 返回的 `reasoning_content`、tool call 轨迹和工具结果摘要；日志不输出 API key 或完整 prompt。

## 4. 内置 agent 与 skill

内置 agent 文档和 skill 文档已独立存放，方便后续持续优化：

- `src/smart/agents/mission_agent.md`
- `src/smart/agents/skills/mission_analysis_calculation.md`
- `src/smart/agents/skills/stk_11_6_operations.md`

运行时代码 `src/smart/services/mission_agent.py` 只负责加载这些 Markdown 文档并拼装 system prompt / agent manifest。

当前启用两个 skill：

- 任务分析计算 skill：覆盖变轨策略、发射窗口、跟踪弧段、地影计算、轨道根数/状态矢量/SPICE 转换，以及曲线、CSV、甘特图等工程输出的解释和复核建议。
- STK 11.6 操作 skill：默认面向 STK 11.6，记录本机 STK 11.6 运行目录、完整帮助文档入口、Connect 帮助入口、全局 `stk11_help.sqlite3` KB 和 `stkhelp "<query>"` 检索命令。

STK 帮助文档不复制进项目目录；SMART 通过本机 STK 11.6 安装目录和全局 KB 路径引用完整文档，以避免把厂商帮助文件写入项目配置或仓库。

## 5. 报告输出

点击“AI 分析当前项目”后，页面会在后台线程中调用模型。完成后：

- 生成期间报告区会显示“正在生成 AI 分析报告”，并随摘要构建、DeepSeek 输出、工具调用和报告保存阶段更新提示。
- 报告会以 Markdown 格式化方式显示在页面右下方报告区。
- 报告保存到 `data/ai_project_analysis.md`。
- 报告区提供“导出 MD”和“导出 DOCX”按钮，可把当前报告另存为 Markdown 或 Word 文档。
- 页面右上方“执行过程与工具调用”会记录摘要构建、prompt 组装、DeepSeek `reasoning_content`、tool calls 和报告写入过程。

报告内容包含生成时间、项目目录、分析范围、模型名和模型返回的 Markdown 分析。

## 6. 维护注意事项

- 不要在 UI 线程里直接发起网络请求；页面通过后台 worker 调用 DeepSeek API。
- 不要把大 CSV 原文完整塞给模型；先做统计、抽样和截断。
- 不要把 API key 写入项目配置或提交到仓库。
- 如果后续增加自动修改配置的功能，应先生成建议并要求用户确认，不应让模型直接写入任务配置。
- 如果 STK 命令语法或 Object Model 行为不确定，应先查 STK 11.6 本机帮助或全局 KB，避免混入 STK 12.2 才支持的用法。
- 修改 agent/skill 行为时优先编辑 `src/smart/agents/**/*.md`，只有新增工具或更改 DeepSeek 协议时再改 Python 代码。
