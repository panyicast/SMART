# 发射窗口角度定义与计算说明

本文档记录 SMART 发射窗口计算中使用到的角度定义、计算方法、坐标系假设、默认可见性判据以及对应代码入口，供后续排查和复核使用。页面工作流、缓存文件、结果表、甘特图和性能维护说明见 `doc/launch_window_workflow.md`。

## 1. 适用范围

本文档对应当前实现：

- 代码文件：`src/smart/services/launch_window.py`
- 页面文件：`src/smart/ui/widgets/launch_window_page.py`

本文档覆盖的角度与判据包括：

- 地面站仰角 `E`
- 用户星天线覆盖角 `θst`
- 帆板太阳角 `θs`
- 中继星目标方位角 `alpha`
- 中继星目标仰角 `beta`

## 2. 计算总原则

发射窗口分析不是重新积分轨道，而是：

1. 先读取变轨策略页输出的 `full_orbit_history.csv`
2. 保持卫星相对航时下的地固坐标位置、速度、星下点轨迹和高度不变
3. 仅改变轨道 `T0` 对应的绝对时刻
4. 由于太阳方向随绝对时刻变化，地影、太阳角、测控几何会随 `T0` 改变

也就是说，发射窗口扫描本质上是“平移轨道时间零点”，不是重新改轨。

变轨策略结果文件会在点火/沉底采样点保存推力方向：

- `thrust_alpha_deg`：惯性系推力方向的 `alpha`
- `thrust_beta_deg`：惯性系推力方向的 `beta`，当前等价于策略中的 `delta_deg`
- `thrust_longitude_deg`：参考 `t0_epoch` 下推力方向的地固投影经度
- `thrust_latitude_deg`：参考 `t0_epoch` 下推力方向的地固投影纬度

发射窗口计算优先使用 `thrust_longitude_deg` / `thrust_latitude_deg`；旧结果文件没有这些字段时，才回退到根据变轨策略和惯性状态重新推导。

## 3. 坐标系与姿态约定

### 3.1 用户星位置坐标

用户星测控几何当前统一在地固坐标系（ECEF）下计算。

- 用户星位置：由 `subsatellite_longitude_deg`、`subsatellite_latitude_deg`、`subsatellite_altitude_m` 转成 ECEF
- 地面站位置：地理经纬高转 ECEF
- 中继星位置：定点经度、纬度和高度转 ECEF

当前发射窗口计算中，中继星位置按设置的经纬度和高度固定处理：

- 中继星不会随时间重新积分轨道
- 不会在扫描不同发射时刻时改变定点经纬高
- 当前实现等价于把中继星作为“固定地固坐标点”参与几何计算

对应代码：

- `_ecef_from_geodetic(...)`

### 3.2 用户星姿态约定

用户星 `+Z` 轴方向由姿态逻辑决定。

#### 非变轨/非沉底时段

卫星 `-Z` 轴指向太阳，因此：

- 用户星 `+Z = -Sun`

#### 变轨点火与沉底时段

卫星 `+Z` 轴指向点火方向。点火方向的地固经纬度优先直接读取 `full_orbit_history.csv` 中保存的 `thrust_longitude_deg` / `thrust_latitude_deg`。旧结果文件缺少这些字段时，再由变轨策略参考历元 `t0_epoch` 下的推力方向推导。扫描不同发射时刻时，该点火方向地固经纬度保持不变，不再把原始惯性速度按新的候选发射时刻重新旋转。因此：

- 用户星 `+Z = ThrustDirection`
- 候选发射时刻只改变太阳直射点，不改变表内相对航时对应的点火方向地固经纬度

对应代码：

- `_body_plus_z_ecef_for_attitude(...)`
- `_saved_thrust_attitude(...)`
- `_thrust_direction_for_state(...)`

### 3.3 中继星姿态约定

中继星整星坐标系当前按以下方式构造：

- `+Z`：指向地球中心
- `+X`：指向轨道速度方向
- `+Y`：由右手系确定，`+Y = normalize(cross(+Z, +X))`

当前实现中，中继星速度方向采用 GEO 赤道轨道切向的本地东向近似：

- `+X = normalize(cross(EarthAxis, Radial))`
- 其中 `EarthAxis = [0, 0, 1]`
- 若数值退化，则使用备用向量构造

对应代码：

- `_relay_target_angles_matrix(...)`
- `_relay_velocity_direction_ecef(...)`

## 4. 角度定义与计算公式

## 4.1 地面站仰角 `E`

### 定义

`E` 定义为：用户星在地面站本地坐标系下的仰角。

等价地说：

- 从地面站指向用户星的视线矢量
- 与地面站局部水平面的夹角

### 当前实现

设：

- `r_g`：地面站 ECEF 位置
- `r_s`：用户星 ECEF 位置
- `u_up = normalize(r_g)`：地面站局部天顶方向
- `u_los = normalize(r_s - r_g)`：地面站到用户星视线方向

则：

- `E = asin(dot(u_los, u_up))`

结果单位为度。

对应代码：

- `_ground_elevation_matrix(...)`

## 4.2 用户星天线覆盖角 `θst`

### 定义

`θst` 定义为：“用户星指向地面站/中继星矢量”与“用户星指向太阳矢量的反方向”之间的夹角。该角度与点火推力方向无关。

### 当前实现

设：

- `r_s`：用户星位置
- `r_t`：测控目标位置，可能是地面站，也可能是中继星
- `u_sun`：用户星指向太阳方向单位向量
- `u_ref = -u_sun`：太阳反方向单位向量
- `u_st = normalize(r_t - r_s)`：用户星指向目标方向

则：

- `θst = arccos(dot(u_st, u_ref))`

结果单位为度。

对应代码：

- `_theta_st_matrix(...)`

## 4.3 帆板太阳角 `θs`

### 定义

`θs` 定义为：卫星指向太阳的矢量与用户选择的帆板方向之间的夹角。帆板方向可选：

- 卫星 `-Z` 轴
- 卫星 `+Z` 轴

### 当前实现

默认帆板方向为卫星 `-Z` 轴，用于兼容旧配置。非点火段，卫星 `+Z = -Sun`，因此选择 `-Z` 时 `θs = 0`，选择 `+Z` 时 `θs = 180 deg`。

点火段，先把太阳方向和推力方向都表达为地面投影点：

- `u_sun`：当前候选发射时刻下的太阳直射点地心单位矢量
- `u_thrust`：变轨策略结果文件保存的推力方向地固经纬度对应的地心单位矢量；旧文件缺字段时用参考历元推导值

则：

- `central = arccos(dot(u_sun, u_thrust))`
- 选择卫星 `+Z` 轴时：`θs = central`
- 选择卫星 `-Z` 轴时：`θs = 180 deg - central`

结果单位为度。

对应代码：

- `_reference_thrust_attitude(...)`
- `_saved_thrust_attitude(...)`
- `_theta_s_deg_from_body_plus_z(...)`

## 4.4 中继星目标方位角 `alpha`

### 定义

`alpha` 定义为：中继星指向用户星矢量方向与地球赤道面的夹角，取绝对值用于可见性判据。

从当前实现角度看，`alpha` 等价于：

- 用户星方向矢量在中继星整星坐标系中的 `Y` 偏离
- 位于 `-Y` 一侧为正

### 当前实现

设：

- `u_rx`、`u_ry`、`u_rz`：中继星整星坐标系的 `+X/+Y/+Z`
- `u_los = normalize(r_user - r_relay)`：中继星指向用户星的视线方向

投影分量：

- `x = dot(u_los, u_rx)`
- `y = dot(u_los, u_ry)`
- `z = dot(u_los, u_rz)`

则：

- `alpha = atan2(-y, sqrt(x^2 + z^2))`

结果单位为度。

窗口可见性判断中使用 `abs(alpha)`。

对应代码：

- `_relay_target_angles_matrix(...)`

## 4.5 中继星目标仰角 `beta`

### 定义

`beta` 定义为：中继星指向用户星矢量在赤道面投影与中继星指向地球矢量之间的夹角，取绝对值用于可见性判据。

从当前实现角度看，`beta` 等价于：

- 用户星方向在中继星 `XOZ` 平面投影后
- 相对于中继星 `+Z` 方向的夹角
- 位于 `+X` 一侧为正

### 当前实现

仍采用上面的 `x / y / z` 分量，则：

- `beta = atan2(x, z)`

结果单位为度。

窗口可见性判断中使用 `abs(beta)`。

对应代码：

- `_relay_target_angles_matrix(...)`

## 5. 当前默认可见性判据

## 5.1 地面站可见

对任意一个地面站，若同时满足：

- `E >= ground_station_min_elevation_deg`
- `θst <= ground_station_max_theta_st_deg`

则该时刻地面站可见。多个地面站时，任意一个地面站满足上述条件即可认为该时刻地面站测控可见。

当前默认值：

- `ground_station_min_elevation_deg = 5 deg`
- `ground_station_max_theta_st_deg = 70 deg`

## 5.2 中继星可见

对任意一个中继星，若同时满足：

- `abs(alpha) <= relay_alpha_abs_max_deg`
- `abs(beta) <= relay_beta_abs_max_deg`
- `θst <= relay_max_theta_st_deg`

则该时刻中继星可见。

当前默认值：

- `relay_alpha_abs_max_deg = 20 deg`
- `relay_beta_abs_max_deg = 40 deg`
- `relay_max_theta_st_deg = 80 deg`

## 5.3 总测控覆盖

当前总测控覆盖按“地面站覆盖 OR 中继星覆盖”处理：

- `covered = ground_covered OR relay_covered`

测控弧段连续性、点火前后跟踪要求等，都是在这个 `covered` 序列上进一步判定。

发射窗口输出不再按最小窗口长度过滤。只要存在连续通过样本，无论合并后的窗口长度是多少，都输出到结果表。

对应代码：

- `_evaluate_candidate(...)`

## 5.4 窗口边界地影输出

结果表除第一圈地影外，还输出两个边界指标：

- `窗口前沿轨道最长地影/min`
- `窗口后沿轨道最长地影/min`

前沿指标取窗口第一个通过样本的 `longest_shadow_min`。后沿指标优先取窗口后第一个失败样本的 `longest_shadow_min`；如果窗口后没有失败样本，则取窗口最后一个通过样本。这样两个值与窗口前沿/后沿限制条件使用的样本边界保持一致。

对应代码：

- `_evaluate_candidate(...)`
- `_merge_pass_samples(...)`

## 6. 结构化约束与角度类型映射

发射窗口限制条件表中，与角度有关的结构化类型如下：

- `ground_elevation`：地面站仰角 `E`
- `theta_s`：帆板太阳角 `θs`
- `theta_st`：用户星天线覆盖角 `θst`
- `relay_alpha_abs`：中继星 `abs(alpha)`
- `relay_beta_abs`：中继星 `abs(beta)`
- `inclination`：轨道倾角

当前限制条件表是结构化枚举，不再依赖运行时文本规则解析。表格保存条件类型和时间段；时间段既可使用绝对航时分钟数，也可使用 `T1_start-180`、`T1_end+60` 这类变轨参数表达式。阈值统一来自上方全局设置。例如地面站可见使用地面站仰角下限和地面站 `θst` 上限，中继星可见使用 `alpha/beta/θst` 三个中继星全局阈值，`theta_s` 使用点火期间 `θs` 全局上限。

对应代码：

- `CONSTRAINT_TYPE_*`
- `_evaluate_constraint_entry(...)`

## 7. 与图片定义的一致性说明

当前实现与用户确认过的定义对应关系如下：

- `E`：地面站本地坐标系下仰角
- `θst`：用户星指向目标矢量与用户星指向太阳矢量反方向的夹角
- `θs`：太阳矢量与用户选择的帆板方向（用户星 `+Z` 或 `-Z` 轴）夹角
- `alpha`：中继星指向用户星矢量相对赤道面的偏离角，判据取绝对值
- `beta`：中继星指向用户星矢量在赤道面投影后，相对中继星指地 `+Z` 的夹角，判据取绝对值

需要注意两点：

1. 中继星 `+X` 当前采用 GEO 切向东向近似，不是外部轨道器提供的高精度状态。
2. `theta_s` 按用户选择的帆板方向（`+Z/-Z` 轴）计算，不在两面之间自动取更优值；点火方向地固经纬度按变轨策略参考历元固定。
3. `theta_st` 与点火方向无关，点火期间仍按反太阳方向作为参考轴。

## 8. 排查建议

后续如果出现“窗口数量变化”“测控弧段异常”“单日窗口消失”这类问题，优先检查：

1. `full_orbit_history.csv` 是否变化
2. `rocket_flight_time_s` 是否变化
3. 地面站/中继星预置经度或启用状态是否变化
4. 地面站和中继星可见性阈值是否变化
5. 用户星姿态逻辑是否变化
6. 中继星 `+X` 方向构造是否变化
7. `data/launch_window_samples.csv` 是否为旧配置下的缓存

建议排查顺序：

1. 先确认 `T0` 平移是否正确
2. 再确认太阳方向是否变化
3. 再确认 `E / θst / alpha / beta / θs` 单项曲线是否符合预期
4. 再确认样本缓存是否已按当前配置重新计算
5. 最后确认结构化约束组合是否筛掉了候选样本

## 9. 代码入口速查

- 主计算入口：`compute_launch_windows(...)`
- 候选样本评估：`_evaluate_candidate(...)`
- 结构化约束：`_evaluate_constraint_entry(...)`
- 用户星姿态：`_body_plus_z_ecef_for_attitude(...)`
- 地面站仰角：`_ground_elevation_matrix(...)`
- 用户星天线覆盖角：`_theta_st_matrix(...)`
- 中继星 `alpha / beta`：`_relay_target_angles_matrix(...)`
