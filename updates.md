# 更新记录

> 此文件由 `scripts/update_updates_md.py` 自动维护。正常执行 `git commit` 时会通过 `.githooks/commit-msg` 自动刷新。

## 2026-04-17T19:57:20+08:00 | Initial commit
- 提交：`ae5ee28`
- 影响文件：`src/smart/__init__.py`、`src/smart/domain/__init__.py`、`src/smart/domain/models.py`、`src/smart/main.py`、`src/smart/services/__init__.py`、`src/smart/services/module_catalog.py`、`src/smart/services/orbital_mechanics.py`、`src/smart/services/spice_service.py` 等 24 个文件。

## 2026-04-18T00:44:22+08:00 | Add offline SPICE kernel management and project kernel folders
- 提交：`95ec7aa`
- 影响文件：`src/smart/app_runtime.py`、`src/smart/assets/cesium/mission_view.html`、`src/smart/assets/cesium/mission_view.js`、`src/smart/assets/diagnostics/cesium_probe.html`、`src/smart/assets/diagnostics/webgl_probe.html`、`src/smart/assets/icons/smart-main-icon.svg`、`src/smart/domain/models.py`、`src/smart/main.py` 等 465 个文件。

## 2026-04-18T10:03:54+08:00 | Add update log hooks, SPICE download presets, and projects dir
- 提交：`0f492b3`
- 影响文件：`src/smart/services/project_workspace.py`、`src/smart/services/spice_service.py`、`src/smart/ui/i18n.py`、`src/smart/ui/main_window.py`、`src/smart/ui/widgets/spice_kernel_page.py`、`tests/test_project_workspace.py`、`tests/test_spice_service.py`、`tests/test_update_updates_md.py` 等 23 个文件。

## 2026-04-18T10:29:05+08:00 | Add satellite orbit initialization workflow
- 提交：`8cc78f4`
- 影响文件：`src/smart/domain/models.py`、`src/smart/services/module_catalog.py`、`src/smart/services/orbit_initialization.py`、`src/smart/services/project_workspace.py`、`src/smart/ui/i18n.py`、`src/smart/ui/main_window.py`、`src/smart/ui/mission_state.py`、`src/smart/ui/widgets/orbit_initialization_page.py` 等 10 个文件。

## 2026-04-19T00:02:24+08:00 | feat: add indexed maneuver strategy and staged orbit propagation
- 提交：`38cce75`
- 影响文件：`src/smart/services/project_workspace.py`、`tests/test_project_workspace.py`、`scripts/satellite_dynamics_equation.py`、`README.md`、`projects/F4/config/maneuver_strategy.json`。

## 2026-04-22T23:28:08+08:00 | feat: improve maneuver strategy workflow
- 提交：`b2fc1e0`
- 影响文件：`src/smart/services/orbit_initialization.py`、`src/smart/services/orbital_mechanics.py`、`src/smart/services/project_workspace.py`、`src/smart/services/spice_service.py`、`src/smart/ui/i18n.py`、`src/smart/ui/main_window.py`、`src/smart/ui/widgets/maneuver_page.py`、`src/smart/ui/widgets/orbit_designer_page.py` 等 23 个文件。

## 2026-04-24T16:24:08+08:00 | 准备实现STK接口
- 提交：`a5b7546`
- 影响文件：`scripts/full_orbit_history_subsatellite_lon_lat.svg`、`AGENTS.md`、`projects/F1/config/maneuver_strategy.json`、`projects/F1/config/orbit_initialization.json`、`projects/F1/config/satellite_status.json`、`projects/F1/data/orbit_elements.json`、`projects/F1/smart_project.json`、`projects/F4/config/orbit_initialization.json` 等 46 个文件。

## 2026-04-25T18:50:19+08:00 | feat: embed STK maneuver preview workflow
- 提交：`9cb1f72`
- 影响文件：`src/smart/services/earth_orientation.py`、`src/smart/services/project_workspace.py`、`src/smart/services/stk_ephemeris.py`、`src/smart/ui/i18n.py`、`src/smart/ui/widgets/maneuver_page.py`、`src/smart/ui/widgets/stk_graphics_views.py`、`tests/test_project_workspace.py`、`tests/test_satellite_dynamics_equation.py` 等 12 个文件。

## 2026-04-25T18:54:20+08:00 | chore: ignore generated STK preview artifacts
- 提交：`6db74e3`
- 影响文件：`.gitignore`。

## 2026-04-25T22:46:36+08:00 | perf: optimize STK preview generation
- 提交：`09bbeda`
- 影响文件：`src/smart/services/earth_orientation.py`、`src/smart/services/spice_service.py`、`src/smart/services/stk_ephemeris.py`、`src/smart/ui/i18n.py`、`src/smart/ui/widgets/maneuver_page.py`、`src/smart/ui/widgets/stk_graphics_views.py`、`tests/test_stk_ephemeris.py`、`scripts/satellite_dynamics_equation.py`。

## 2026-04-28T21:02:02+08:00 | feat: support parameterized launch constraints
- 提交：`6099ae4`
- 影响文件：`src/smart/services/launch_window.py`、`src/smart/ui/widgets/launch_window_page.py`、`tests/test_launch_window.py`、`tests/test_launch_window_page.py`、`doc/launch_window_angle_reference.md`。

## 2026-04-29T11:51:00+08:00 | Update launch window analysis workflow
- 提交：`2079f7a`
- 影响文件：`src/smart/services/launch_window.py`、`src/smart/services/project_workspace.py`、`src/smart/ui/main_window.py`、`src/smart/ui/widgets/launch_window_page.py`、`src/smart/ui/widgets/maneuver_page.py`、`src/smart/ui/widgets/spinboxes.py`、`tests/test_launch_window.py`、`tests/test_project_workspace.py` 等 21 个文件。

## 2026-04-29T11:52:50+08:00 | Update changelog for launch window workflow
- 提交：`858c607`
- 影响文件：自动刷新记录，无额外文件。

## 2026-04-29T17:42:01+08:00 | Optimize launch window calculation
- 提交：`5db61f7`
- 影响文件：`src/smart/services/launch_window.py`、`src/smart/ui/widgets/launch_window_page.py`、`tests/test_launch_window.py`、`projects/F4/config/launch_window.json`、`projects/F4/smart_project.json`、`projects/F4/data/launch_window_samples.csv`。

## 2026-04-29T17:42:48+08:00 | Update changelog for launch window optimization
- 提交：`3fdd838`
- 影响文件：自动刷新记录，无额外文件。

## 2026-04-29T21:09:51+08:00 | Add AI project analysis page
- 提交：`7a5142f`
- 影响文件：`src/smart/services/llm_client.py`、`src/smart/services/project_ai_context.py`、`src/smart/ui/i18n.py`、`src/smart/ui/main_window.py`、`src/smart/ui/widgets/ai_project_analysis_page.py`、`tests/test_ai_project_analysis.py`、`README.md`、`AGENTS.md` 等 15 个文件。

## 2026-04-29T21:10:15+08:00 | Update changelog for AI project analysis
- 提交：`ea2448f`
- 影响文件：自动刷新记录，无额外文件。

## 2026-04-29T22:01:03+08:00 | Add tracking arc analysis page
- 提交：`cccbe24`
- 影响文件：`src/smart/services/project_workspace.py`、`src/smart/services/tracking_arc.py`、`src/smart/ui/main_window.py`、`src/smart/ui/widgets/tracking_arc_page.py`、`tests/test_project_workspace.py`、`tests/test_tracking_arc.py`、`projects/F4/config/launch_window.json`、`projects/F4/smart_project.json`。

## 2026-04-29T22:02:59+08:00 | Update changelog for tracking arc analysis
- 提交：`3b6e136`
- 影响文件：自动刷新记录，无额外文件。

## 2026-04-30T11:39:46+08:00 | Add DeepSeek mission analysis agent reporting
- 提交：`198cc02`
- 影响文件：`src/smart/agents/mission_agent.md`、`src/smart/agents/skills/mission_analysis_calculation.md`、`src/smart/agents/skills/stk_11_6_operations.md`、`src/smart/services/llm_client.py`、`src/smart/services/mission_agent.py`、`src/smart/services/mission_agent_tools.py`、`src/smart/services/project_ai_context.py`、`src/smart/services/report_export.py` 等 16 个文件。

## 2026-04-30T11:40:18+08:00 | Update changelog for mission analysis agent reporting
- 提交：`待写入本次提交`
- 影响文件：自动刷新记录，无额外文件。
