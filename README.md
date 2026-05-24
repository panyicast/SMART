# SMART

SMART 全称为 `Spacecraft Mission Analysis, Research & Toolkit`，是一个面向航天任务设计与工程分析的桌面软件。项目围绕 `STK 11.6 + SPICE + PySide6` 构建统一工作流，用来解决传统任务分析中多工具切换、时间与坐标系转换易错、结果留痕分散的问题。

当前仓库提供的是一版可运行的桌面工程原型，已经覆盖项目管理、卫星3D模型配置、轨道初始化、设计变轨策略、连续推力参数优化、导入变轨策略、发射窗口计算、跟踪弧段分析、飞行程序设计、STK 联动、SPICE 内核管理、项目化数据落盘和 AI 辅助项目解读等核心链路。

<p align="center">
  <img src="projects/F4/charts/smart_workflow.svg" alt="SMART mission workflow" width="92%" />
</p>

<p align="center">
  <img src="projects/F4/charts/orbit_3d.png" alt="SMART 3D orbit view" width="30%" />
  <img src="projects/F4/charts/orbit_2d.png" alt="SMART 2D orbit plot" width="30%" />
  <img src="projects/F4/charts/launch_20260515_1640_bjt_first_maneuver_theta_s.svg" alt="SMART launch window geometry" width="30%" />
</p>

<p align="center">
  <img src="projects/F4/charts/altitude_trend.png" alt="SMART altitude trend" width="45%" />
  <img src="projects/F4/charts/velocity_trend.png" alt="SMART velocity trend" width="45%" />
</p>

## 项目定位

SMART 的目标不是单独替代 STK 或 SPICE，而是把任务建模、约束分析、图形验证、结果导出和工程说明收敛到一个可复用、可追溯的桌面分析环境中：

- UI 层统一以北京时间配置任务参数，降低人工换算成本
- 服务层优先复用 SPICE 与本地 STK 11.6 能力，减少手写公式漂移
- 图形验证基于本地桌面绘图与 OpenGL 轨道视图运行
- 项目结果按 `config / data / charts` 结构自动沉淀，便于复算和交接
- AI 分析页只读取摘要上下文做辅助说明，不直接修改任务配置

## 当前能力

- 项目管理：新建/打开项目，按 `config / data / charts` 保存配置、CSV、图表和中间结果
- 卫星3D模型配置：设计当前项目卫星 3D 模型，供 SMART 三维场景和 STK 场景导入使用
- 轨道初始化：支持经典轨道根数、TLE 和 STK `.e` 星历导入；地固系星历优先通过 SPICE 转到 J2000
- 设计变轨策略：基于 V5.1 硬约束脉冲规划搜索 q 序列、控后近地点目标、终端经度/倾角约束和方向角优化
- 连续推力优化：从脉冲规划生成 5 次连续推力点火参数，输出偏航角、点火/熄火时刻、推进剂消耗和控后轨道状态
- 导入变轨策略：把设计页生成的连续推力策略引入工程变轨页面，并计算 `full_orbit_history.csv`
- 发射窗口分析：复用变轨输出轨道历史，完成约束扫描、窗口结果表、样本缓存和甘特图输出
- 跟踪弧段分析：围绕测控可见性、发射窗口和轨道历史生成可跟踪弧段结果
- 飞行程序设计：复用变轨结果和 STK 联动数据，形成飞行程序参考段、事件表和时间线
- 任务可视化：2D/3D 轨道视图、科学曲线与结果图表
- STK 联动：面向 STK 11.6 的对象创建、轨道/姿态/图形标注和结果导出链路
- SPICE 内核管理：本地内核扫描、加载、下载提示和运行状态检查
- AI 辅助解读：面向项目摘要的任务分析说明页，支持报告式输出

## 技术选型

- GUI：PySide6
- 数值计算：NumPy
- 2D 绘图：pyqtgraph
- 3D 轨道视图：pyqtgraph OpenGL + PyOpenGL
- 星历与 SPICE：SpiceyPy

## 模块规划

- 任务首页与项目管理（Dashboard / Project）
- 卫星3D模型配置（Satellite 3D Model）
- 轨道初始化（Orbit Initialization）
- 设计变轨策略（Design Maneuver Strategy）
- 导入变轨策略（Import Maneuver Strategy）
- 发射窗口分析（Launch Window）
- 跟踪弧段分析（Tracking Arc）
- 飞行程序设计（Flight Program）
- 科学数据可视化（Scientific Data Visualization）
- STK 联动（STK Link）
- SPICE 内核管理（SPICE Kernels）
- AI 项目分析（AI Project Analysis）

当前开发重点是设计变轨策略、连续推力优化、导入变轨策略、发射窗口和飞行程序之间的数据闭环。后续仍需要继续补强 STK/Astrogator 精化、更多约束配置校验、结果版本校验和端到端工程验收。

## 功能文档

- 设计变轨策略脉冲规划算法：`doc/design_maneuver_pulse_planning_algorithm.md`
- 设计变轨策略连续推力参数优化算法：`doc/design_continuous_thrust_parameter_optimization_algorithm.md`
- 发射窗口工作流、缓存文件、结果 CSV 和甘特图说明：`doc/launch_window_workflow.md`
- 发射窗口角度定义、公式和可见性判据：`doc/launch_window_angle_reference.md`
- AI 项目分析页面、API 配置和数据发送范围：`doc/ai_project_analysis.md`
- SPICE 内核要求、默认加载顺序和调用示例：`doc/spice_usage.md`

## 快速开始

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .[dev]
smart
```

也可以直接模块启动：

```powershell
$env:PYTHONPATH = "src"
python -m smart.main
```

## 运行脚本（PowerShell）

```powershell
.\scripts\setup.ps1    # 创建 .venv 并安装依赖
.\scripts\run.ps1      # 启动 SMART 桌面程序
.\scripts\test.ps1     # 运行测试
.\scripts\install-git-hooks.ps1  # 安装 Git hooks（setup 会自动调用）
```

如果当前终端执行策略限制脚本运行：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
```

## 更新记录

- 仓库根目录 `updates.md` 由 Git hook 自动维护。
- 正常执行 `git commit` 时，`.githooks/commit-msg` 会调用 `scripts/update_updates_md.py` 自动追加本次更新记录。
- 新环境可执行 `.\scripts\setup.ps1` 或 `.\scripts\install-git-hooks.ps1` 安装 hook。
- 如果需要手工回填或重建记录，可执行：

```powershell
python .\scripts\update_updates_md.py
```

## 项目管理

使用顶部菜单 `项目 / Project`：

- `新建项目 / New Project`：在仓库根目录下的 `projects/` 中创建项目文件夹
- `打开项目 / Open Project`：默认从 `projects/` 目录开始选择已有 SMART 项目

项目激活后，程序会自动保存：

- 卫星3D模型配置文件：`config/satellite_3d_model.json`
- 轨道初始化配置：`config/orbit_initialization.json`
- 设计变轨策略配置：`config/design_maneuver_strategy.json`
- 设计页生成的导入配置：`config/design_import_maneuver_strategy.json`
- 变轨策略配置文件：`config/maneuver_strategy.json`
- 发射窗口配置：`config/launch_window.json`
- 跟踪弧段配置：`config/tracking_arc.json`
- 飞行程序配置：`config/flight_program.json`
- 轨道与状态数据：`data/orbit_elements.json`
- 设计变轨脉冲规划结果：`data/design_maneuver_results.json`
- 设计变轨连续推力结果：`data/design_continuous_thrust_results.json`
- 设计变轨连续推力轨道历史：`data/design_continuous_thrust_orbit_history.csv`
- 变轨策略计算结果：`data/full_orbit_history.csv`
- 发射窗口样本缓存：`data/launch_window_samples.csv`
- 发射窗口结果表：`data/launch_window_results.csv`
- 跟踪弧段结果：`data/tracking_arc_results.json`
- 飞行程序参考结果：`data/flight_program_reference_results.json`
- 图表文件：`charts/altitude_trend.png`、`charts/velocity_trend.png`

其中 `config/satellite_3d_model.json`、`config/design_maneuver_strategy.json`、`config/maneuver_strategy.json` 等会在项目创建时自动生成。设计变轨策略页面会存档脉冲规划和连续推力结果；重新打开项目时，如果存档存在，页面会直接加载并显示结果，不需要先重新计算。

典型工程链路：

1. 在“轨道初始化”和“卫星3D模型配置”中固化轨道初值和卫星模型。
2. 在“设计变轨策略”中生成脉冲规划，再优化连续推力模型参数。
3. 通过“导入变轨策略”把设计结果引入工程变轨页面，生成 `data/full_orbit_history.csv`。
4. 发射窗口、跟踪弧段、飞行程序和 STK 联动页面复用上述结果继续分析。

## SPICE 内核

将任务内核放置在 `data/kernels/`。SMART 当前对轨道、时间、坐标系相关处理采用 SPICE 优先策略，默认本地自动加载项目级和仓库级内核。

完整约束、内核要求、默认加载顺序、STK `.e` 导入限制与调用示例见：

- `doc/spice_usage.md`

`smart.services.spice_service` 当前提供的核心接口包括：

- 内核自动发现与加载
- UTC 与 ET 转换
- 位置/状态向量参考系转换
- 天体状态向量查询

推荐目录结构：

```text
data/kernels/
  naif0012.tls
  pck00011.tpc
  earth_assoc_itrf93.tf
  earth_latest_high_prec.bpc
  de440s.bsp
```

## 项目结构

```text
src/smart/
  domain/         # 任务与轨道领域模型
  services/       # 动力学计算与 SPICE 服务
  ui/             # 桌面界面与控件
tests/            # 数值与功能测试
data/kernels/     # 本地 SPICE 内核
```

## 验证测试

```powershell
$env:PYTHONPATH = "src"
python -m pytest
```
