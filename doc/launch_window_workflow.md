# 发射窗口分析工作流说明

本文档记录 SMART 发射窗口页面的数据来源、缓存文件、结果表、甘特图和性能维护要点。角度定义和公式见 `doc/launch_window_angle_reference.md`。

## 1. 数据流

发射窗口分析依赖变轨策略页已经生成的项目数据：

1. 读取 `config/launch_window.json`。
2. 读取 `config/maneuver_strategy.json`。
3. 读取 `data/full_orbit_history.csv`。
4. 按 `sample_step_min` 扫描候选发射时刻。
5. 对每个候选点计算地影、太阳角、地面站可见性和中继星可见性。
6. 将连续通过样本合并为发射窗口。
7. 更新结果表、甘特图，并写入 `data/launch_window_samples.csv`。

发射窗口扫描只平移 `T0` 对应的绝对时刻；不会重新积分轨道，也不会重新生成 `full_orbit_history.csv`。

## 2. 时间约定

- 页面输入和表格显示使用北京时间。
- 配置文件和服务层接口使用 UTC 字符串。
- `候选发射时刻 + rocket_flight_time_s = 入轨 T0`。
- 结果表中的“入轨 T0 前沿”由窗口前沿发射时刻加火箭飞行时间得到。

## 3. 缓存文件

### 3.1 样本缓存

页面计算完成后会写入：

- `data/launch_window_samples.csv`

该文件保存每个候选发射时刻的评估结果，包括：

- `launch_utc`
- `t0_utc`
- `ok`
- `failure`
- `first_orbit_shadow_min`
- `longest_shadow_min`
- `constraint_results`

项目加载时，如果该文件存在，页面会直接读取样本缓存并重建结果表和甘特图。当前缓存没有配置 hash 校验；如果修改了约束、阈值、扫描步长、测控资源或计算逻辑，需要重新点击“计算发射窗口”刷新缓存。

### 3.2 结果表 CSV

点击“保存结果 CSV”会写入：

- `data/launch_window_results.csv`

该文件从当前结果表直接导出，使用 `utf-8-sig` 编码，便于 Excel 等表格软件识别中文列名。

## 4. 结果表列

当前结果表列为：

- `窗口前沿 (北京时间)`
- `窗口后沿 (北京时间)`
- `长度/min`
- `入轨 T0 前沿 (北京时间)`
- `第一圈地影/min`
- `窗口前沿轨道最长地影/min`
- `窗口后沿轨道最长地影/min`
- `窗口前沿限制条件`
- `窗口后沿限制条件`

前沿限制条件来自窗口前一个失败样本；后沿限制条件来自窗口后第一个失败样本。若边界外没有失败样本，则显示空限制或 `--`。

## 5. 甘特图显示规则

甘特图使用 `data/launch_window_samples.csv` 或本次计算得到的样本列表绘制：

- 第一行为“发射窗口计算结果”，用红色显示所有通过样本合并后的区间。
- 后续每个测控/约束条件单独一行。
- 条件通过的时间段用绿色显示。
- 条件失败的时间段保持空白。
- 鼠标悬停条带时显示北京时间起止和通过时长。

行标题较长时会省略显示，但完整名称仍保留在 tooltip 和样本数据中。

## 6. 约束结果

结构化约束结果保存在每个样本的 `constraint_results` 字段中。字段内容是 JSON 列表，元素包含：

- `name`：用于结果和甘特图展示的条件名称。
- `passed`：该候选时刻是否通过该条件。
- `enabled`：该条件是否启用。

`passed=false` 的条件不会在甘特图上绘制颜色。整体 `ok=false` 时，`failure` 保存第一个启用且失败的条件名称。

## 7. 性能维护要点

当前 F4 示例的 534 个候选点在服务层约为数秒级。维护时注意：

- 保持地影时长、最长连续时长、地面站仰角和中继星角度计算的 NumPy 向量化实现。
- 只在对应约束启用时计算太阳角、地面站测控角或中继星测控角。
- 页面进度条刷新要节流，避免每个候选点调用一次 `processEvents()`。
- 不要在候选点循环中重复读取 CSV、重复构造项目配置或重复创建 Qt 控件。
- 若后续要继续压缩时间，优先考虑候选点分块批量计算，但需要控制内存占用。

## 8. 相关代码入口

- 页面：`src/smart/ui/widgets/launch_window_page.py`
- 主入口：`compute_launch_windows(...)`
- 候选点评估：`_evaluate_candidate(...)`
- 结果合并：`merge_launch_window_samples(...)`
- 样本缓存读写：`_read_sample_csv(...)`、`_write_sample_csv(...)`
- 结果 CSV 导出：`_save_result_csv(...)`
- 甘特图控件：`LaunchWindowGanttWidget`
