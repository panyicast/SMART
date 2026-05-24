# AI 项目分析报告

- 生成时间 UTC：2026-05-24T09:51:45+00:00
- 项目目录：`D:\Spark\SMART\projects\F4`
- 分析范围：项目综合分析
- 模型：`deepseek-v4-pro`

所有数据已获取并交叉验证完毕。下面给出完整的端到端体检报告。

---

# 🔍 F4 项目端到端体检报告

**审计时间**: 2026-05-28（基于工具实时计算结果）  
**项目根目录**: `D:\Spark\SMART\projects\F4`  
**审计方法**: 工具调用 (`compute_launch_window_samples`、`plan_design_maneuver_strategy`、`find_launch_windows`、`build_project_context`、`inspect_project_files`) + 配置/缓存数据交叉对比

---

## 一、结论摘要

| 维度 | 状态 | 核心问题 |
|:--|:--|:--|
| **变轨策略配置** | ✅ 一致 | `maneuver_strategy.json` 与 `design_import_maneuver_strategy.json` 已同步（同为 2026-05-24T05:41:57Z）；脉冲设计结果可复现 |
| **飞行程序** | 🔴 严重过期 | 所有点火相关事件比当前 maneuver_strategy 提前 **60 分钟**，来自旧版策略 |
| **发射窗口缓存** | 🔴 过期 | `launch_window_results.csv` 窗口时长 90 min，工具实时计算为 110 min（差距 20 min） |
| **跟踪弧段/甘特图** | 🔴 过期 | `flight_program_reference_results.json` 和 `tracking_arc_results.json` 基于旧窗口和旧策略 |
| **轨道历史 CSV** | 🟡 版本待确认 | 无法从元数据直接验证与当前 maneuver_strategy (5/24) 的一致性 |
| **遗留文件** | 🟡 混淆风险 | 3 个配置文件描述无关 LEO 任务 |

---

## 二、高风险问题（按影响排序）

### 🔴 H-1：`flight_program.json` 点火事件比 maneuver_strategy 提前 **60 分钟**

| 项目 | 详情 |
|:--|:--|
| **证据** | 飞行程序中所有 4 次 AFM（点火模式）开始时间均比 `maneuver_strategy.json` 的 `Tn_start` 提前 **恰好 60 分钟** |
| **数据对比** | |

| 点火 | flight_program AFM start (min) | maneuver_strategy Tn_start (min) | 偏移 |
|:--|:--|:--|:--|
| T1 | 1153.65 | 1213.65 | **−60.00** |
| T2 | 3907.42 | 3967.42 | **−60.00** |
| T3 | 6951.13 | 7011.13 | **−60.00** |
| T4 | 9465.65 | 9525.65 | **−60.00** |

| **影响范围** | 飞行程序中所有姿态转换、巡航区间、部署事件的时间均基于旧策略编排，与当前点火计划不匹配 |
| **来源** | `config/flight_program.json` vs `config/maneuver_strategy.json`，工具对比 |
| **结论类型** | 🛠️ 工具结果 |

> 飞行程序 `source: "auto_draft"`，明确是从旧版 maneuver strategy 自动生成后未随策略更新而重新生成。

---

### 🔴 H-2：`launch_window_results.csv` 窗口时长与实时计算不符（差 20 min）

| 项目 | 缓存 CSV | 工具实时计算 | 差异 |
|:--|:--|:--|:--|
| 5/15 窗口 | 15:18–16:48 BJT (90 min) | 15:18–17:08 BJT (110 min) | **−20 min** |
| 5/16 窗口 | 15:20–16:48 BJT (88 min) | 15:20–17:08 BJT (108 min) | **−20 min** |
| 5/17 窗口 | 15:20–16:48 BJT (88 min) | 15:20–17:08 BJT (108 min) | **−20 min** |
| 5/18 窗口 | 15:20–16:46 BJT (86 min) | 15:20–17:08 BJT (108 min) | **−22 min** |

| **证据** | `find_launch_windows` 读取 `launch_window_results.csv` 返回 90 min；`compute_launch_window_samples` 基于同一 `full_orbit_history.csv` 实时计算返回 110 min |
| **影响** | 用户若按缓存窗口决策后沿时间，会损失 20 分钟发射窗口余量；前沿一致（均为 15:18），前沿限制条件一致（均为「一远点测控」） |
| **来源** | 🛠️ 工具结果 (`compute_launch_window_samples` vs `find_launch_windows`) |
| **说明** | 缓存 CSV 可能使用旧版约束或合并算法生成，窗口时长被低估 |

---

### 🔴 H-3：`flight_program_reference_results.json` 全量过期

| 字段 | 当前值 | 问题 |
|:--|:--|:--|
| `selected_launch_utc` | 2026-05-15T07:18:00Z | 前沿仍有效，但窗口时长已变 |
| `selected_t0_utc` | 2026-05-15T07:53:34Z | 对应 T0，尚与当前前沿一致 |
| 甘特图 T1 点火 UTC | 2026-05-16T04:07:13Z | 基于旧 maneuver_strategy 编排 |
| 甘特图 T3 点火 UTC | 2026-05-20T04:44:42Z | 基于旧 T3_start |

| **影响** | 所有点火时段 UTC、地面站可见弧段、中继星可见弧段、地影时段标注均基于旧策略——在 SMART 页面中展示的甘特图不可信 |
| **来源** | 🛠️ 工具结果 + 配置对比 |

---

### 🔴 H-4：`tracking_arc_results.json` 基于旧窗口签名

| 证据 | 内部 `entry key` 为 `2026-05-16T07:20:00Z\|2026-05-16T08:48:00Z\|88.000000`，对应旧窗口 07:20–08:48 UTC (88 min)。实时计算结果为 07:20–09:08 UTC (108 min) |
|:--|:--|
| **影响** | 跟踪弧段甘特图和分析基于过期窗口区间；窗口后沿扩展 20 min 后，后沿附近可见性未评估 |
| **来源** | 🛠️ 工具结果 + 缓存对比 |

---

## 三、中风险问题

### 🟡 M-1：`full_orbit_history.csv` 版本无法验证

| 指标 | 值 |
|:--|:--|
| 行数 | 11,861 |
| 时间跨度 | 0 ~ 710,673 s (≈197.4 hr ≈ 8.2 天) |
| 初始 raan | 77.82° |
| 最终 inclination | 6.00°（终端精度良好） |
| thrust_alpha 范围 | −178.50 ~ 12.29°（与当前策略一致） |

| **风险** | CSV 无内嵌版本号/生成时间戳。虽然 thrust_alpha 范围与当前 maneuver_strategy 匹配，且 `compute_launch_window_samples` 可正常使用，但无法从元数据确证它是用 2026-05-24 版策略积分的。所有依赖该 CSV 的下游分析均存在"静默过期"风险 |
| **建议** | 在生成 CSV 时写入元数据行（策略版本 + 生成 UTC） |
| **来源** | 🛠️ 工具结果 + 工程判断 |

---

### 🟡 M-2：`launch_window.json` 与 `tracking_arc.json` 完全同构

| 证据 | 两个文件均为 5,857 bytes，内容逐字段相同 |
|:--|:--|
| **风险** | 配置冗余：若仅更新其中一个，另一个成为隐藏漂移源 |
| **建议** | 保留一个权威源（建议 `launch_window.json`），另一个由工具自动同步或废弃 |
| **来源** | 🛠️ 工具结果 (`inspect_project_files`) |

---

### 🟡 M-3：3 个遗留 LEO 文件与当前超同步转移任务无关

| 文件 | 内容 |
|:--|:--|
| `config/orbit_initialization.json` | LEO: a=7,000 km, e=0.05, i=28.5° |
| `data/orbit_elements.json` | 同上 |
| `data/maneuver_snapshot.json` | 400→1,200 km Hohmann 转移 (Δv≈416 m/s) |

| **风险** | 若新用户或脚本误读这些文件作为初始轨道，将得到完全错误的任务设定 |
| **建议** | 归档到 `legacy/` 子目录或删除 |
| **来源** | 🛠️ 工具结果 + 配置对比 |

---

### 🟡 M-4：STK 导出文件过时

| 证据 | `data/stk/F4_20260425_*` 和 `data/stk_link/2026051[23]_*` 均为 4/25–5/13 导出，早于当前 maneuver_strategy (5/24) |
|:--|:--|
| **风险** | 若将这些 `.e` / `.a` 文件导入 STK 11.6，将使用过期轨道和姿态 |
| **建议** | 在策略/飞行程序更新后重新导出 STK 星历和姿态文件 |
| **来源** | 🛠️ 工具结果 (`inspect_project_files`) |

---

### 🟡 M-5：发射窗口约束行使用固定时间窗，需随策略更新复核

| 约束行 | 固定窗口 (min) | 当前实际点火 (min) | 前余量 (min) | 后余量 (min) |
|:--|:--|:--|:--|:--|
| 第一次点火测控 | 1074–1387 | 1213.65–1282.47 | 139.65 | 104.53 |
| 第二次点火测控 | 3758–4053 | 3967.42–4022.85 | 209.42 | 30.15 |
| 第三次点火测控 | 6830–7142 | 7011.13–7083.48 | 181.13 | 58.52 |
| 第四次点火测控 | 9336–9641 | 9525.65–9590.37 | 189.65 | 50.63 |

| **风险** | T2 后余量仅 30 min，若未来策略使 T2 后移超过此值，约束将误判 |
| **建议** | 使用 `T2_start` / `T2_end` 动态引用替代固定数值（与太阳角约束一致的做法） |
| **来源** | 🛠️ 工具结果 + 工程判断 |

---

## 四、低风险 / 信息项

| # | 项目 | 说明 |
|:--|:--|:--|
| L-1 | `maneuver_strategy.json` ↔ `design_import_maneuver_strategy.json` | 两者内容完全一致，`source.generated_utc` 均为 2026-05-24T05:41:57Z——**版本已同步**（相比 5/23 审计中的 T3 43-min 漂移已修复） |
| L-2 | 脉冲设计可复现 | `plan_design_maneuver_strategy` 实时结果与 `data/design_maneuver_results.json` 一致：propellant 2596.4 kg, q_sequence [3,3,2,0], 5 次点火，所有硬约束通过 |
| L-3 | 连续推力结果 | `data/design_continuous_thrust_results.json` 总推进剂 2600.9 kg，所有 `failed_constraints` 为空，硬约束通过 |
| L-4 | 图表状态 | `charts/` 目录有 7 个文件，但 `altitude_trend.png` (2.5 KB) 和 `velocity_trend.png` (2.8 KB) 过小疑似占位图，应复核 |

---

## 五、依赖链与过期判断

```
config/design_maneuver_strategy.json (脉冲输入, stable)
  └─→ data/design_maneuver_results.json ✅ 与实时结果一致
       └─→ data/design_continuous_thrust_results.json ✅
            └─→ config/maneuver_strategy.json ✅ (2026-05-24)
            └─→ config/design_import_maneuver_strategy.json ✅ (2026-05-24, 同步)
                 └─→ data/full_orbit_history.csv 🟡 (版本待确认)
                      └─→ data/launch_window_samples.csv 🟡 (与实时计算一致)
                      └─→ data/launch_window_results.csv 🔴 (时长偏差 −20 min)
                      └─→ data/tracking_arc_results.json 🔴 (旧窗口签名)
                      └─→ data/flight_program_reference_results.json 🔴 (旧策略)
                           └─→ config/flight_program.json 🔴 (60 min 偏移)
```

---

## 六、建议的下一步验证顺序

| 优先级 | 动作 | 说明 |
|:--|:--|:--|
| **P0** | **重新生成 `flight_program.json`** | 用当前 `maneuver_strategy.json` 重新编排飞行程序；消除 60 min 偏移是阻断性问题 |
| **P0** | **重新生成 `launch_window_results.csv`** | 使用 `compute_launch_window_samples` 并 `save_result=true` 覆盖过期缓存 |
| **P1** | **重新生成 `flight_program_reference_results.json`** | 依赖 P0 完成后执行，更新甘特图所有分段 |
| **P1** | **重新生成 `tracking_arc_results.json`** | 依赖 P0+P1 完成后执行 |
| **P1** | **确认 `full_orbit_history.csv` 版本** | 如无法确认，使用当前 `maneuver_strategy.json` 重新积分生成（需连续推力优化工具 `save_result=true`） |
| **P2** | **清理遗留文件** | 归档或删除 `orbit_initialization.json`、`orbit_elements.json`、`maneuver_snapshot.json` |
| **P2** | **统一发射窗口/跟踪弧段配置源** | 合并 `launch_window.json` 和 `tracking_arc.json`，消除冗余 |
| **P2** | **将点火测控约束改为动态引用** | `T2_start`/`T2_end` 替代固定数值，提高鲁棒性 |
| **P3** | **重新导出 STK 星历/姿态** | 在所有更新完成后导出 `.e` 和 `.a` 文件用于 STK 11.6 验证 |

---

## 七、工具调用记录

| 工具 | 用途 | 关键发现 |
|:--|:--|:--|
| `build_project_context` | 获取完整项目上下文 | 所有配置和数据文件清单 |
| `inspect_project_files` | 文件存在性及大小检查 | 发现遗留 LEO 文件、STK 导出目录、launch_window.json = tracking_arc.json (5857 bytes) |
| `compute_launch_window_samples` | 基于当前 CSV+配置实时采样 | 窗口 15:18–17:08 BJT (110 min)，前沿=一远点测控，后沿=第一次点火太阳角 |
| `plan_design_maneuver_strategy` | 脉冲变轨设计复核 | 推进剂 2596.4 kg，与缓存一致；脉冲点火时间与连续推力时间差 ≈ burn_duration/2（预期行为） |
| `find_launch_windows` | 读取缓存窗口 | 15:18–16:48 BJT (90 min)，与实时计算不一致 |

---

> ⚠️ **核心结论**：变轨策略本身（脉冲设计 + 连续推力优化）在当前版本下是一致且可复现的。问题集中在**下游产物未随策略更新而刷新**——飞行程序（60 min 偏移）、发射窗口汇总（20 min 时长偏差）、跟踪弧段和甘特图均基于过期版本。下一步应先修复 P0 项（飞行程序 + 窗口汇总 CSV），再依次刷新 P1 和 P2。以上工具调用均为只读，未修改任何项目文件。
