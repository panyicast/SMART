# 任务分析计算 skill

## Skill ID

`smart.skill.mission_analysis_calculation`

## 目的

围绕 SMART 项目执行和复核任务分析计算，覆盖变轨策略、发射窗口、跟踪弧段、地影和轨道状态分析。

## 能力

- 设计变轨策略（脉冲）计算，优先调用 SMART 本地 `plan_design_maneuver_strategy` 工具，不要手算点火时刻、经度和推进剂结果。
- 设计变轨策略（连续推力）优化，优先调用 SMART 本地 `optimize_design_continuous_thrust` 工具；存在已归档脉冲结果时可直接复用。
- 变轨策略与 delta-v / 转移时间复核，支持从 `config/maneuver_strategy.json` 和 `config/design_maneuver_strategy.json` 读取参数。
- 发射窗口约束分析和重新采样，优先调用 SMART 本地 `compute_launch_window_samples` 工具，支持采样步长、测控资源、太阳角、地影、倾角等参数解释与复核。
- 跟踪弧段分析，覆盖地面站、中继星、点火时段、地影时段和可见性汇总。
- 轨道根数、状态矢量、历元、坐标系转换和 STK `.e` 星历导入前检查。
- 地影区间计算，优先调用 SMART 本地 `compute_shadow_intervals_for_launch` 工具，不要凭经验估算地影事件时间。
- 图表、曲线、CSV 结果和甘特图输出的结果解释与下一步生成建议。

## 主要工具

- `build_project_context`
- `find_launch_windows`
- `compute_shadow_intervals_for_launch`
- `plan_design_maneuver_strategy`
- `optimize_design_continuous_thrust`
- `compute_launch_window_samples`
- `inspect_project_files`

## 输出

- 工程分析报告。
- 约束和参数调整建议。
- 地影区间表、总地影时长、最长地影时长。
- 设计变轨脉冲表、连续推力优化参数表、硬约束失败项。
- 需要重新生成的 CSV、曲线或甘特图清单。
- 可在 SMART 页面中复核的操作步骤。

## 约束

- 不在模型响应中声称已重新积分、重新优化或重新采样，除非工具或上下文明确提供新计算结果。
- `plan_design_maneuver_strategy`、`optimize_design_continuous_thrust`、`compute_launch_window_samples` 默认只读；只有用户明确要求写入或工具参数 `save_result=true` 时才保存项目文件。
- Launch-window 分析复用 `data/full_orbit_history.csv`，不假设已自动重新积分。
- 参数变化后必须提示重新生成 `data/launch_window_samples.csv` 和相关图表。
- 缺少 SPICE 内核、项目 CSV 或指定日期的缓存结果时，应指出具体缺口。
