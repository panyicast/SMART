# 设计变轨策略脉冲规划算法说明

本文档说明 SMART `设计变轨策略` 页面当前使用的脉冲规划算法。算法实现位于：

- `src/smart/services/design_maneuver_strategy.py`
- 页面展示与结果存档位于 `src/smart/ui/widgets/design_maneuver_strategy_page.py`

当前算法定位为工程初设级脉冲初值规划，用于快速给出多次 A/P 点火策略初值。它不是有限推力高精度传播器，也不替代后续有限推力展开、STK/Astrogator 精化或完整姿轨耦合优化。

默认超同步转移路径采用 V5.1 硬约束相位搜索：

```text
先枚举/读取 q_AA 与 q_AP；
再搜索前段远地点控后近地点高度 hp/rp；
先筛选点火窗口、点火时长、终端 a/e/i/lon 等硬约束；
最后在硬约束可行候选中按推进剂消耗排序。
```

标准转移和 V5.1 不可用时，仍保留旧的 V4.2 简化半长轴链流程作为回退。

## 1. 输入与默认任务

主入口为：

```python
plan_design_maneuver_strategy(payload)
```

输入 `payload` 会先经过 `normalize_design_maneuver_strategy_payload()` 标准化。若输入为空，则使用 `default_design_maneuver_strategy_payload()` 默认配置。

### 1.1 初始轨道

默认初始轨道采用经典轨道根数：

| 参数 | 字段 | 默认值 | 单位 |
| --- | --- | ---: | --- |
| 历元 | `initial.t0_epoch` | `2026-04-24T13:54:27Z` | UTC |
| 初始质量 | `initial.m0_kg` | `6515.0` | kg |
| 半长轴 | `initial.a_km` | `29478.137` | km |
| 偏心率 | `initial.e` | `0.77684692` | - |
| 倾角 | `initial.i_deg` | `16.5` | deg |
| 升交点地理经度 | `initial.lon_node_deg` | `8.53237` | deg |
| 近地点幅角 | `initial.argp_deg` | `200.0` | deg |
| 平近点角 | `initial.mean_anomaly_deg` | `1.85437` | deg |

注意：`lon_node_deg` 是升交点地理经度。算法会用历元时刻格林尼治角将其转换为惯性 RAAN：

```text
RAAN_inertial = lon_node_deg + GreenwichAngle(t0)
```

### 1.2 目标轨道

| 参数 | 字段 | 默认值 | 单位 |
| --- | --- | ---: | --- |
| 目标半长轴 | `target.a_km` | `42164.2` | km |
| 目标偏心率 | `target.e` | `0.0` | - |
| 目标倾角 | `target.i_deg` | `6.0` | deg |
| 目标定点经度 | `target.lon_degE` | `120.0` | degE |

终端误差默认约束：

| 误差项 | 字段 | 默认值 |
| --- | --- | ---: |
| 半长轴误差 | `terminal_tolerance.a_km` | `1.0 km` |
| 偏心率误差 | `terminal_tolerance.e` | `1.0e-4` |
| 倾角误差 | `terminal_tolerance.i_deg` | `0.01 deg` |
| 经度误差 | `terminal_tolerance.lon_deg` | `0.01 deg` |

### 1.3 发动机与点火时长

主发动机与沉底发动机参数：

| 参数 | 字段 | 默认值 |
| --- | --- | ---: |
| 主发动机推力 | `engine.F_main_N` | `490.0 N` |
| 主发动机比冲 | `engine.Isp_main_s` | `314.1 s` |
| 姿控效率损失 | `engine.attitude_control_efficiency` | `0.0173` |
| 沉底发动机推力 | `engine.F_set_N` | `20.0 N` |
| 沉底发动机比冲 | `engine.Isp_set_s` | `290.0 s` |
| 沉底时长 | `engine.tau_set_s` | `240.0 s` |

每次点火总时长限制：

```text
burn_limit.max_total_burn_time_min = 90.0 min
```

若 `burn_limit.include_settling_in_burn_time = True`，总点火时长包含沉底段。

### 1.4 V5.1 硬约束搜索参数

| 参数 | 默认值 | 含义 |
| --- | ---: | --- |
| `hard_constraint_planner.enabled` | `true` | 超同步转移启用 V5.1 |
| `hard_constraint_planner.q_AA_user` | `[3, 3, 2]` | 前段远地点之间的 q 序列 |
| `hard_constraint_planner.q_AP_user` | `null` | 终端远地点到近地点的 q；为空时搜索候选 |
| `hard_constraint_planner.q_AP_candidates` | `[0, 1, 2]` | 可搜索的终端近地点候选 |
| `hard_constraint_planner.fixed_hp_targets_km` | `{1: 3933, 2: 8360}` | 用户固定的前段控后近地点高度 |
| `hard_constraint_planner.hard_raw_window` | `true` | 原始测控窗口作为硬约束 |
| `hard_constraint_planner.hard_planning_window` | `true` | 收缩规划窗口作为硬约束 |

默认 F4 参考任务中，V5.1 会搜索剩余控后近地点高度，并在满足 `terminal_tolerance.lon_deg = 0.01 deg` 的候选中选择推进剂最小者。

设计变轨策略页面左侧的 `V5.1 用户约束` 卡片，以及高级设置中，均可直接填写这些字段：

- `远地点间 q 序列`：逗号分隔，例如 `3,3,2`。当 `apsis.pattern_mode = user` 时，该序列直接决定点火次数：前段 q 个数 + 终端远地点 + 终端近地点。
- `终端 A-P q`：单个整数，例如 `0`。留空时使用 `终端 A-P q 候选` 搜索。
- `终端 A-P q 候选`：逗号分隔，例如 `0,1,2`。
- `指定控后近地点高度/km`：`点火序号:高度` 形式，例如 `1:3933,2:8360`。留空表示对应前段近地点高度由优化器搜索。

页面卡片中的 `用户序列` 复选框会切换 `apsis.pattern_mode`。若用户指定 q 序列与 `maneuver_count.user` 不一致，V5.1 以 q 序列定义的点火次数为准，并给出 warning。

## 2. 轨道类型判定

算法先计算初始远地点半径：

```text
r_a0 = a0 * (1 + e0)
h_a0 = r_a0 - Re
```

再按目标半长轴对应的同步轨道高度判断轨道类型：

- `supersynchronous_transfer`：初始远地点高于目标同步轨道，按超同步转移处理。
- `standard_transfer`：初始远地点未显著高于同步轨道，按标准多远地点转移处理。

也可以通过 `orbit_type.mode` 强制指定。

## 3. 变轨次数确定

算法先估算总速度增量与单次设计速度增量：

```text
recommended_count = ceil(total_dv_est / design_single_burn_dv)
```

再结合工程下限、用户指定次数和固定尾段要求确定实际次数：

- 若 `maneuver_count.user > 0`，采用用户指定次数。
- 若超同步 V5.1 且 `apsis.pattern_mode = user`，并且 `hard_constraint_planner.q_AA_user` 非空，则采用 `len(q_AA_user) + 2` 次。
- 超同步转移至少保留固定尾段需要的次数。
- 默认超同步工程下限为 5 次。
- 若用户指定次数小于推荐次数，产生 warning，但仍按用户指定次数计算。

## 4. 点火点序列

### 4.1 超同步转移

默认策略：

```text
n_apogee_plus_1_perigee
```

即前面若干次远地点点火，最后一次近地点点火。对 5 次超同步任务，默认点火结构为：

```text
A, A, A, A, P
```

其中：

- 前若干远地点点火用于相位/经度链、半长轴控制和倾角控制。
- 倒数第二次远地点点火为固定尾段的一部分。
- 最后一次近地点点火用于进入目标半长轴，不控制倾角。

当前算法特意约束：超同步最后一次近地点点火不做隐藏倾角修正，偏航角固定为近似纯切向。

### 4.2 标准转移

标准转移默认使用多次远地点点火：

```text
A, A, ..., A
```

标准转移场景仍允许末次进行终端倾角 trim，因为没有“最后近地点不控倾角”的超同步规则。

## 5. A/P 点事件传播

算法用简化两体轨道加可选 J2 长期项传播 A/P 点事件。

### 5.1 初始状态转换

经典根数先转为惯性位置速度：

```text
(a, e, i, RAAN, argp, M) -> (r, v)
```

平近点角 `M` 经 Kepler 方程求偏近点角，再求真近点角和轨道面内位置速度，最后经 3-1-3 旋转到惯性系。

### 5.2 J2 长期项

若 `earth.use_J2 = True`，算法使用 J2 长期漂移率：

```text
p = a * (1 - e^2)
n = sqrt(mu / a^3)
factor = J2 * (Re / p)^2

RAAN_dot = -1.5 * n * factor * cos(i)
argp_dot = 0.75 * n * factor * (5*cos(i)^2 - 1)
M_dot = n + 0.75*n*factor*sqrt(1-e^2)*(3*cos(i)^2 - 1)
```

寻找下一 A/P 点时，目标平近点角为：

```text
A 点: M = pi
P 点: M = 0
```

并根据 requested event index 或 q 序列跳过若干圈。

### 5.3 星下点经度

星下点经度由惯性位置转地固近似得到：

```text
theta = GreenwichAngle(t0) + omega_earth * elapsed_s
x_ecef = cos(theta) * x_eci + sin(theta) * y_eci
y_ecef = -sin(theta) * x_eci + cos(theta) * y_eci
lon_degE = atan2(y_ecef, x_ecef)
```

输出经度归一到 `[0, 360)`。

## 6. 经度链与 q 序列

每两次点火之间由整数回归圈数 `q` 控制：

```text
第 k 次点火后的轨道周期 * q_k - 地球自转角 = 下一次点火点经度变化
```

代码中不是显式代数反解经度，而是通过：

1. 对候选 q 序列逐个试算；
2. 根据当前控后轨道传播到下一 A/P 点；
3. 搜索满足经度窗口的第 q 个候选 A/P 点；
4. 对最后一次点火前的 A/P 点，优先选最接近目标定点经度的候选。

### 6.1 q 序列生成

默认 q 上限：

```text
q_limit = apsis.q_AA_default
```

当前默认 `q_limit = 3`。

对 5 次超同步任务，默认基准 q 序列为：

```text
3, 3, 2, 1
```

若用户提供 `apsis.q_sequence_user`，算法会使用用户 q 序列，但每项仍被限制为：

```text
1 <= q <= q_limit
```

若用户未提供 q，算法会构造一组候选 q 序列，例如：

```text
base_sequence
[q-1, q-1, 1, tail_q]
[q-1, 1, 1, tail_q]
[q, q, 1, tail_q]
[1, q, min(2,q), tail_q]
[1, 1, 1, tail_q]
```

只保留长度正确且去重后的序列。

## 7. V5.1 控后近地点高度与经度控制

V5.1 对超同步转移不再把前段主变量设为均匀 Δv 或半长轴控制量，而是设为每次前段远地点点火后的目标近地点半径：

```text
rp_target_1, rp_target_2, ..., rp_target_{n_A-1}
```

UI 最后一列显示的 `控后近地点高度/km` 即：

```text
hp_plus_k = rp_plus_k - Re
```

给定 `q_AA`、`q_AP` 和一组 `rp_target` 后，算法逐段传播：

1. 第 k 次前段远地点点火锁定 `a_plus = (r_A + rp_target_k)/2`；
2. 对 alpha 做一维搜索，alpha 每次由固定 `a_plus` 反解 Δv；
3. 传播到由 `q_AA,k` 指定的下一个远地点；
4. 终端远地点解析求解 `rp_plus = r_sync` 且 `i_plus = i_target`；
5. 最后近地点按逆速度投影圆化到 `a_target`；
6. 对点火经度、时长、终端 a/e/i/lon 做硬约束检查；
7. 可行候选按总推进剂递增排序。

默认固定前两次控后近地点高度：

```text
hp_1 = 3933 km
hp_2 = 8360 km
```

剩余 `hp_3` 由硬约束优化器搜索。当前默认 q 结构为：

```text
q_AA = [3, 3, 2]
q_AP ∈ [0, 1, 2]
```

候选报告保存在 `summary.phase_diagnostics.top_candidates`，并按 `(q_AA, q_AP, rp_targets)` 去重，避免重复 Top 行。

## 7.1 V4.2 回退：半长轴控制与经度控制

V4.2 回退流程把经度控制放在半长轴链上处理。

流程：

1. 对每个 q 序列，先以给定 alpha 模板建立初始速度增量分配。
2. 搜索前三次远地点速度增量，使后续 A/P 点经度链逼近目标经度。
3. 得到一组控后半长轴序列：

```text
post_a_1, post_a_2, ..., post_a_N
```

4. 这组 `post_a` 代表经度链确定的半长轴控制量：

```text
delta_a_k = post_a_k - pre_a_k
```

5. 后续偏航角优化阶段锁定这些 `post_a`，只调整 alpha/yaw 来完成倾角分配。

锁定半长轴时，给定目标控后半长轴 `target_post_a` 和偏航角 `alpha`，算法反解总速度增量 `dv`。

### 7.1 固定控后半长轴时的速度增量反解

推力方向为局部水平面方向：

```text
d = cos(alpha) * east + sin(alpha) * south
```

点火后速度：

```text
v_plus = v + dv * d
```

轨道能量关系：

```text
1 / a_target = 2 / r - |v_plus|^2 / mu
```

展开为关于 `dv` 的二次方程：

```text
|v + dv*d|^2 = 2*mu/r - mu/a_target

dv^2 + 2*(v·d)*dv + (|v|^2 - 2*mu/r + mu/a_target) = 0
```

代码取正根中的最小正值作为本次点火速度增量。

这一步保证：即使 alpha 改变，控后半长轴仍命中经度链确定的目标半长轴。

## 8. 偏航角与倾角控制

偏航角 `alpha` 是局部水平面内的推力偏航角：

```text
alpha = 0 deg      -> 沿局部 east 方向
alpha > 0 deg      -> 向 south 偏
alpha < 0 deg      -> 向 north 偏
alpha = +/-180 deg -> 近似反 east 方向
```

代码中的方向定义：

```text
r_hat = r / |r|
east = normalize(k_hat x r_hat)
north = normalize(k_hat - (k_hat·r_hat) * r_hat)
south = -north

direction = cos(alpha) * east + sin(alpha) * south
```

因此：

- 切向分量主要控制轨道能量和半长轴；
- 法向/南北分量主要改变轨道倾角；
- 固定控后半长轴后，alpha 变化会导致反解出的总 dv 随之变化；
- alpha 优化目标是在满足半长轴链和终端倾角的前提下减少推进剂。

### 8.1 倾角分配初值

进入 alpha 优化前，算法先用经度链阶段得到的半长轴控制量作为权重，生成倾角控制初值。

对超同步转移：

1. 只选择远地点 A 点作为倾角控制候选；
2. 最后一次近地点 P 点不控制倾角；
3. 倾角控制权重使用：

```text
weight_k = abs(semi_major_axis_control_km_k)
```

4. 若权重和为 0，则均匀分配；
5. 初始 alpha 大小按权重比例给出；
6. 若初始倾角高于目标倾角，alpha 取正方向；反之取负方向；
7. 最后 P 点 alpha 固定为 `-180 deg`，近似纯切向反向点火。

### 8.2 alpha 局部寻优

alpha 寻优在半长轴链锁定后进行：

```text
target_post_a_values = [burn.post_a_km for burn in best_burns]
```

然后调用同一套 burn 构造逻辑，但每次点火速度增量不再直接使用旧 `delta_v`，而是由：

```text
target_post_a + alpha -> solve dv
```

反解得到。

alpha 搜索范围来自配置：

| 类型 | 字段 |
| --- | --- |
| 前段远地点 | `alpha.front_bounds_deg` |
| 尾段远地点 | `alpha.tail_apogee_bounds_deg` |
| 尾段近地点 | `alpha.tail_perigee_bounds_deg` |
| 标准转移 | `alpha.standard_bounds_deg` |

当前搜索为坐标搜索：

```text
step = 80, 40, 20, 10, 5, 2, 1, 0.5 deg
```

若某个 alpha 正/负方向扰动使评分更优，就接受扰动并继续。

对远地点点火，alpha 搜索还会按倾角控制方向裁剪：

- 初始倾角高于目标倾角时，远地点 alpha 下界裁剪到 `0 deg`，只允许正偏航压倾角；
- 初始倾角低于目标倾角时，远地点 alpha 上界裁剪到 `0 deg`，只允许负偏航抬倾角；
- 最后近地点点火仍按局部水平面内逆速度方向计算，不参与远地点压倾角符号裁剪。

## 9. 评分函数与优化目标

算法评分函数 `_phase_score()` 返回一个可排序 tuple。优化时 Python 直接按 tuple 字典序比较。

当前评分顺序：

```text
(
    invalid,
    terminal_lon_excess + duration_penalty + warning_penalty,
    terminal_i_excess,
    terminal_a_excess,
    total_propellant_kg,
    terminal_lon_abs_error,
    max_burn_duration_min,
    uniform_spread_mps,
)
```

含义：

1. 先排除存在 warning 或点火时长超限的方案；
2. 再优先满足终端经度误差；
3. 再满足终端倾角误差；
4. 再满足终端半长轴误差；
5. 约束满足后，最小化总推进剂消耗；
6. 推进剂相近时，再比较终端经度绝对误差、最大点火时长和速度增量离散度。

终端经度超差项：

```text
terminal_lon_excess = max(0, abs(lon_error) - terminal_tolerance.lon_deg)
```

终端倾角超差项：

```text
terminal_i_excess = max(0, abs(i_error) - terminal_tolerance.i_deg)
```

终端半长轴超差项：

```text
terminal_a_excess = max(0, abs(a_error) - terminal_tolerance.a_km)
```

点火时长超限惩罚：

```text
duration_penalty = max(0, max_duration - max_total_burn_time_min) * 1000
```

warning 惩罚：

```text
warning_penalty = 1000 if warnings else 0
```

## 10. 推进剂与点火时长计算

每次点火由沉底段和主发动机段组成。

### 10.1 沉底段

若启用沉底：

```text
tau_set = engine.tau_set_s
dv_set = F_set / m0 * tau_set
c_set = Isp_set * g0
mp_set = m0 * (1 - exp(-dv_set / c_set))
```

否则：

```text
tau_set = 0
mp_set = 0
dv_set = 0
```

### 10.2 主发动机段

主发动机有效比冲考虑姿控效率损失：

```text
Isp_main_eff = Isp_main / (1 + attitude_control_efficiency)
c_main_eff = Isp_main_eff * g0
```

主发动机承担剩余速度增量：

```text
dv_main = max(0, dv_total - dv_set)
```

推进剂消耗：

```text
mp_main = m_after_set * (1 - exp(-dv_main / c_main_eff))
```

主发动机流量：

```text
mdot_main = F_main / c_main_eff
tau_main = mp_main / mdot_main
```

总推进剂：

```text
propellant = mp_set + mp_main
```

总点火时长：

```text
total_time = tau_set + tau_main
```

若 `include_settling_in_burn_time = False`，总点火时长只取主发动机段。

## 11. 固定尾段

超同步默认启用固定尾段：

```text
supersynchronous_transfer.tail_fixed_enabled = True
supersynchronous_transfer.tail_fixed_count = 2
```

默认固定目标：

```text
倒数第二次远地点点火后: a = 47271.168509 km
最后一次近地点点火后:   a = 42164.2 km
```

固定尾段若未显式给定 `dv_tail_apogee_fixed_mps` / `dv_tail_perigee_fixed_mps`，算法按目标控后半长轴反解 dv。

## 12. 手动第一次半长轴控制量

配置字段支持手动指定 MV1 的半长轴控制量：

```text
distribution.first_post_a_control_km
```

若该字段存在，则 MV1 目标控后半长轴改为：

```text
target_post_a_1 = pre_a_1 + first_post_a_control_km
```

之后算法重新优化后续 q、半长轴链和 alpha。

## 13. 输出结果

算法输出 `DesignManeuverResult`，包括：

```python
DesignManeuverResult(
    config,
    summary,
    burns,
    checks,
    warnings,
)
```

### 13.1 summary

关键 summary 字段：

| 字段 | 含义 |
| --- | --- |
| `orbit_type` | 轨道类型 |
| `recommended_count` | 推荐变轨次数 |
| `actual_count` | 实际采用次数 |
| `apsis_pattern` | 点火点序列 |
| `q_sequence` | 点火间 q 序列 |
| `phase_optimized` | q / Δv / alpha 是否改变过 |
| `phase_delta_v_optimized` | Δv 是否经过相位链优化 |
| `phase_alpha_optimized` | alpha 是否经过倾角/推进剂优化 |
| `optimized_propellant_kg` | 当前方案总推进剂 |
| `terminal_errors` | 终端半长轴、偏心率、倾角、经度误差 |

### 13.2 burns

每次点火输出 `DesignManeuverBurn`：

| 字段 | 含义 |
| --- | --- |
| `index` | 变轨序号 |
| `burn_type` | `front` / `tail_fixed` / `normal` |
| `apsis` | `A` 远地点或 `P` 近地点 |
| `elapsed_min` | 相对初始历元航时 |
| `beijing_time` | 北京时间 |
| `longitude_deg_e` | 点火点星下点经度 |
| `delta_v_mps` | 本次速度增量 |
| `alpha_deg` | 计算的变轨推力偏航角 |
| `target_post_a_km` | 若固定控后半长轴，则记录目标值 |
| `post_a_km` | 控后半长轴 |
| `post_e` | 控后偏心率 |
| `post_i_deg` | 控后倾角 |
| `orbit_period_min` | 控后轨道周期 |
| `propellant_kg` | 本次推进剂消耗 |
| `post_mass_kg` | 控后卫星质量 |
| `semi_major_axis_control_km` | 本次半长轴控制量 |

### 13.3 checks

当前约束检查包括：

- 点火经度窗口；
- 最大总点火时长；
- 均匀性；
- 终端半长轴误差；
- 终端偏心率误差；
- 终端倾角误差；
- 终端经度误差。

## 14. UI 表格显示规则

`设计变轨策略` 页面会显示一行 `分离点` 和多行 MV：

| 列 | 含义 |
| --- | --- |
| 空列 | 分离点 / MV 编号 |
| 航时/min | 相对初始历元航时 |
| 飞行圈次 | 回归圈计数 |
| 位置 | 近地点 / 远地点 |
| 星下点经度/degE | 点火点星下点经度 |
| 控后半长轴/km | 控后半长轴 |
| 轨道周期/min | 控后轨道周期 |
| 轨道倾角/deg | 控后倾角 |
| 速度增量/(m/s) | 本次速度增量 |
| 计算的变轨推力偏航角/deg | alpha |
| 点火时长/min | 本次总点火时长 |
| 推进剂消耗/kg | 本次推进剂 |
| 控后卫星质量/kg | 控后质量 |
| 控后近地点高度/km | `post_a_km * (1 - post_e) - Re` |

所有数值在 UI 表格中保留小数点后两位。

## 15. 存档与加载

点击 `生成脉冲规划` 后：

1. 页面调用 `plan_design_maneuver_strategy()`；
2. 结果写入：

```text
data/design_maneuver_results.json
```

3. 页面刷新或项目重新加载时，若该存档存在，会自动加载并显示。

## 16. 当前算法限制

当前算法有以下工程限制：

1. 使用简化脉冲模型，不做有限推力弧段积分。
2. A/P 点传播使用两体加 J2 长期项，不是完整摄动模型。
3. alpha 搜索是有限步长坐标搜索，不是全局最优保证。
4. 评分函数按工程约束优先，若约束冲突，会选择排序意义下最优，而不是证明无解。
5. 超同步最后近地点不控倾角是当前任务规则，若任务策略改变，需要同步修改约束。
6. 当前不会把规划结果自动写入 `maneuver_strategy.json`，仍作为设计变轨策略页面的独立初值规划输出。

## 17. 复核建议

对新任务使用该算法后，建议复核：

1. `q_sequence` 是否满足任务约束，尤其单次 q 是否不超过工程上限。
2. 最后一次点火经度误差是否在 `0.01 deg` 内。
3. 最终倾角误差是否在 `0.01 deg` 内。
4. 最大点火时长是否小于 `max_total_burn_time_min`。
5. 最后一次近地点点火是否没有承担倾角控制。
6. MV1 手动半长轴控制量修改后，终端经度与倾角是否仍通过约束。
