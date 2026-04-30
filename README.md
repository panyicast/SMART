# SMART

SMART 全称为 `Spacecraft Mission Analysis, Research & Toolkit`，是一个基于 Python 的航天任务分析桌面软件。

当前仓库提供的是第一版可运行 MVP，重点覆盖任务设计与工程可视化的基础能力：

- 卫星状态设置（质量、推进、天线、地面站/测控船、中继星）
- 圆轨道霍曼转移估算
- 高度/速度科学曲线绘制
- 基于 SpiceyPy 的 SPICE 服务接入骨架
- 工具栏中英文实时切换（默认中文）
- 项目管理（新建/打开）与数据/图表自动保存

## 技术选型

- GUI：PySide6
- 数值计算：NumPy
- 2D 绘图：pyqtgraph
- 3D 轨道视图：pyqtgraph OpenGL + PyOpenGL
- 星历与 SPICE：SpiceyPy

## 模块规划

- 卫星状态设置（Satellite Status）
- 变轨策略（Maneuver Strategy）
- 发射窗口分析（Launch Window Analysis）
- 跟踪弧段分析（Tracking Arc Analysis）
- 飞行程序设计（Flight Program Design）
- 科学数据可视化（Scientific Data Visualization）

目前已实装卫星状态设置、变轨策略和共享可视化栈，其余模块在应用中以可扩展页面形式预留。

## 功能文档

- 发射窗口工作流、缓存文件、结果 CSV 和甘特图说明：`doc/launch_window_workflow.md`
- 发射窗口角度定义、公式和可见性判据：`doc/launch_window_angle_reference.md`
- AI 项目分析页面、API 配置和数据发送范围：`doc/ai_project_analysis.md`
- SPICE 内核要求、默认加载顺序和调用示例：`doc/spice_usage.md`
- Cesium / Qt WebEngine 排障：`doc/cesium_troubleshooting.md`

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

## Cesium 集成注意事项

- SMART 不再依赖外部 Cesium CDN，运行时已随项目本地打包在 `src/smart/assets/cesium/vendor/Build/Cesium`。
- 若需更新本地 Cesium 版本，执行：

```powershell
.\scripts\vendor-cesium.ps1
```

- 详细排障手册见：`doc/cesium_troubleshooting.md`
- Qt WebEngine 中加载本地 Cesium 时，页面 CSP 必须允许 `blob:` worker；否则页面可能一直停在 `Loading...`。
- 如果日志中出现以下错误，优先检查 Qt 图形后端是否仍统一为 OpenGL：
  - `QQuickWidget: Failed to get a QRhi from the top-level widget's window`
  - `The top-level window is not using the expected graphics API for composition`
- 当前项目已在 `smart.app_runtime.configure_graphics_backend()` 中强制统一 Qt Quick / QWidget 图形 API 为 OpenGL。
- 若三维场景首次进入空白、点击“重新加载场景”后恢复，通常是页面首帧加载完成前场景数据发布过早；当前代码已在页面加载成功后自动重新发布场景。
- 排障时优先使用左侧导航中的 `3D 场景测试` 页面，它会绕开总览页的滚动布局，先验证 Cesium 场景本身是否正常。

## 项目管理

使用顶部菜单 `项目 / Project`：

- `新建项目 / New Project`：在仓库根目录下的 `projects/` 中创建项目文件夹
- `打开项目 / Open Project`：默认从 `projects/` 目录开始选择已有 SMART 项目

项目激活后，程序会自动保存：

- 卫星状态配置文件：`config/satellite_status.json`
- 变轨策略配置文件：`config/maneuver_strategy.json`
- 轨道与状态数据：`data/orbit_elements.json`
- 变轨策略计算结果：`data/full_orbit_history.csv`
- 发射窗口样本缓存：`data/launch_window_samples.csv`
- 发射窗口结果表：`data/launch_window_results.csv`
- 图表文件：`charts/altitude_trend.png`、`charts/velocity_trend.png`

其中 `config/satellite_status.json` 和 `config/maneuver_strategy.json` 会在项目创建时自动生成；用户修改“卫星状态设置”和“变轨策略”页面参数后会同步覆盖保存。变轨策略页面可编辑顶层 `launch_mass_kg`、`t0_orbit` 以及逐次机动参数，并用这些配置生成 `data/full_orbit_history.csv`。

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
