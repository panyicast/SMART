# 更新记录

> 此文件由 `scripts/update_updates_md.py` 自动维护。正常执行 `git commit` 时会通过 `.githooks/commit-msg` 自动刷新。

## 2026-05-01T00:39:03+08:00 | Initial commit
- 提交：`ff7eefc`
- 影响文件：`src/smart/__init__.py`、`src/smart/agents/mission_agent.md`、`src/smart/agents/skills/mission_analysis_calculation.md`、`src/smart/agents/skills/stk_11_6_operations.md`、`src/smart/app_runtime.py`、`src/smart/assets/cesium/mission_view.html`、`src/smart/assets/cesium/mission_view.js`、`src/smart/assets/diagnostics/cesium_probe.html` 等 551 个文件。

## 2026-05-01T00:40:21+08:00 | Update changelog after repository republish
- 提交：`68e3099`
- 影响文件：自动刷新记录，无额外文件。

## 2026-05-01T00:42:52+08:00 | Improve GitHub repository presentation
- 提交：`445c020`
- 影响文件：`README.md`。

## 2026-05-01T00:43:21+08:00 | Refresh update log
- 提交：`75af207`
- 影响文件：自动刷新记录，无额外文件。

## 2026-05-02T11:16:25+08:00 | Refactor maneuver visualization off STK
- 提交：`d10ba9e`
- 影响文件：`src/smart/ui/i18n.py`、`src/smart/ui/widgets/maneuver_page.py`、`src/smart/ui/widgets/orbit_views.py`、`scripts/init-planning-session.ps1`、`AGENTS.md`、`doc/planning_templates/findings.md`、`doc/planning_templates/progress.md`、`doc/planning_templates/task_plan.md` 等 10 个文件。

## 2026-05-02T17:18:57+08:00 | Update overview dashboard and remove orbit initialization page
- 提交：`18c82dc`
- 影响文件：`src/smart/services/module_catalog.py`、`src/smart/ui/i18n.py`、`src/smart/ui/main_window.py`、`src/smart/ui/widgets/dashboard_page.py`、`src/smart/ui/widgets/orbit_initialization_page.py`、`projects/F4/config/satellite_status.json`、`projects/F4/config/tracking_arc.json`、`projects/F4/smart_project.json`。

## 2026-05-02T17:19:15+08:00 | Update project changelog
- 提交：`0f3b15c`
- 影响文件：自动刷新记录，无额外文件。

## 2026-05-02T17:19:57+08:00 | Finalize update log
- 提交：`dabba21`
- 影响文件：自动刷新记录，无额外文件。

## 2026-05-02T19:24:18+08:00 | Refresh SMART UI visual system
- 提交：`281dc73`
- 影响文件：`src/smart/assets/fonts/Noto_Sans_SC/OFL.txt`、`src/smart/assets/fonts/Noto_Sans_SC/README.txt`、`src/smart/ui/i18n.py`、`src/smart/ui/theme.py`、`src/smart/ui/widgets/dashboard_page.py`、`src/smart/ui/widgets/data_visualization_page.py`、`src/smart/ui/widgets/launch_window_page.py`、`src/smart/ui/widgets/orbit_views.py` 等 16 个文件。

## 2026-05-02T19:25:57+08:00 | Update changelog for UI refresh
- 提交：`9963915`
- 影响文件：自动刷新记录，无额外文件。

## 2026-05-05T19:45:38+08:00 | Add flight program workflow and orbit preview updates
- 提交：`待写入本次提交`
- 影响文件：`src/smart/assets/cesium/mission_view.html`、`src/smart/assets/cesium/mission_view.js`、`src/smart/services/flight_program.py`、`src/smart/services/project_workspace.py`、`src/smart/ui/main_window.py`、`src/smart/ui/theme.py`、`src/smart/ui/widgets/cesium_mission_view.py`、`src/smart/ui/widgets/flight_program_page.py` 等 14 个文件。

## 2026-05-08T22:30:00+08:00 | Refine flight-program layout, scene linkage, and Gantt interactions
- 提交：`待写入本次提交`
- 影响文件：`AGENTS.md`、`src/smart/services/flight_program.py`、`src/smart/ui/widgets/flight_program_page.py`、`src/smart/ui/widgets/launch_window_page.py`、`src/smart/ui/widgets/orbit_views.py`、`src/smart/ui/widgets/table_editing.py`、`src/smart/ui/widgets/tracking_arc_page.py`、`tests/test_flight_program.py`、`tests/test_flight_program_page.py`、`tests/test_launch_window_page.py`、`tests/test_tracking_arc.py` 等文件。
- 主要修改：
  - 飞行程序页面重构为“参考时段 / 卫星姿态设置 / 主要飞行事件”三表布局，移除右侧事件检查器，右侧仅保留实时状态 3D 场景。
  - 表格列结构和交互调整：参考时段去掉“来源/显示”；主要飞行事件去掉“类型/模式”；卫星姿态设置去掉“类型/瞬时”；姿态模式改为 `SPM/EPM/AFM/Transition` 预设下拉。
  - `锁定` / `瞬时` 布尔列改为按钮交互；`锁定=是` 时仅允许解锁，并阻止其他编辑、复制、删除；“是”状态增加高亮文字色。
  - 卫星姿态时间编辑增加联动：改开始时间时自动调整前一姿态段结束时间，改结束时间时自动调整后一姿态段开始时间；前后段锁定或时间越界时给出冲突报警。
  - 表格导航增强：上下键行选择、回车跳转当前行时刻、姿态表右键菜单增加“删除当前姿态 / 跳转到当前姿态段”，主要飞行事件表去掉姿态新增动作。
  - 3D 场景联动增强：地球按轨道历史参考相位自转，太阳/地球方向与覆盖信息按当前采样刷新；新增星下点红色标记；地球球面细分提高以改善贴图清晰度。
  - 服务层新增全过程姿态指向序列能力 `sample_flight_program_states(...)`，可按姿态设置展开全时间轴 `plus_z_ecef / sun_ecef / earth_ecef` 采样结果。
  - 发射窗口页和跟踪弧页甘特图新增局部缩放、拖动和平移、双击重置的基础逻辑，并补充对应测试。
- 已知问题：
  - 甘特图在实际界面里鼠标滚轮仍可能优先触发页面滚动，局部缩放尚未稳定生效；当前测试主要覆盖控件内部缩放逻辑和事件转发辅助路径，真实界面事件链路还需要继续排查。
