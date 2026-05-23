# 项目一致性审计 skill

## Skill ID

`smart.skill.project_consistency_audit`

## 目的

审计 SMART 项目配置、缓存数据、计算结果、图表和报告之间的一致性，优先发现过期结果、配置漂移、时间基准混用、输入输出不匹配和需要重新生成的工程产物。

## 能力

- 检查项目关键文件是否存在，包括 `smart_project.json`、`config/*.json`、`data/full_orbit_history.csv`、`data/launch_window_samples.csv`、`data/launch_window_results.csv`、`data/maneuver_snapshot.json`、设计变轨结果、连续推力结果和图表目录。
- 对比配置文件与缓存结果的时间范围、采样步长、火箭飞行时间、约束阈值、测控资源和变轨策略依赖，识别结果可能过期的情形。
- 检查 `data/full_orbit_history.csv` 是否能支撑发射窗口、地影、跟踪弧段和图表结果；缺少轨道历史时必须明确说明哪些分析不能复核。
- 检查 `config/maneuver_strategy.json`、`config/design_maneuver_strategy.json`、`config/design_import_maneuver_strategy.json`、`data/design_maneuver_results.json` 和 `data/design_continuous_thrust_results.json` 之间的来源关系和潜在漂移。
- 检查报告、CSV 和 UI 展示中的时间基准；UI 和结果表默认北京时间，服务和配置边界默认 UTC。
- 检查 STK 相关输出前置条件，包括对象命名、外部星历来源、坐标系、地心支持范围和可见文本 ASCII-only 约束。
- 产出按严重度排序的一致性问题清单、证据路径、影响范围、建议的最小修复或重算动作。

## 主要工具

- `build_project_context`
- `inspect_project_files`
- `find_launch_windows`
- `compute_shadow_intervals_for_launch`
- `plan_design_maneuver_strategy`
- `optimize_design_continuous_thrust`
- `compute_launch_window_samples`
- `query_stk_help`

## 审计流程

1. 先调用 `inspect_project_files` 或 `build_project_context` 获取文件清单、配置摘要、CSV 列名、行数、统计和样本。
2. 判断配置和结果依赖关系，不直接假设缓存仍有效。
3. 对用户关注的专题调用相应计算工具复核；没有工具结果时，只能给出风险和建议，不能声称已完成重新计算。
4. 将问题按 `高 / 中 / 低` 严重度排列，并说明证据、影响、建议动作。
5. 明确列出需要重新生成的文件，例如 `data/launch_window_samples.csv`、`data/launch_window_results.csv`、甘特图、设计变轨结果或连续推力结果。

## 输出

- 一致性审计结论。
- 高风险问题和阻断项。
- 配置与结果漂移清单。
- 缓存是否可信的判断。
- 需要重新计算、重新导出或人工复核的最小动作列表。
- 证据路径和关键字段。

## 约束

- 不把缺失文件解释为“无问题”；缺失文件是待确认风险。
- 不把文件存在解释为结果有效；必须结合配置、时间范围、采样参数和来源关系判断。
- 不直接修改项目配置或删除缓存。
- 不声称已重新生成 CSV、图表或 STK 场景，除非工具结果或上下文明确说明。
- 当发现发射窗口约束、采样步长、可见性阈值或计算逻辑变化时，应建议重新生成 `data/launch_window_samples.csv` 后再信任窗口表和甘特图。
