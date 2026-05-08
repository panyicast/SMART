# SMART Cesium 三维场景排障手册

## 目的

本文档记录 SMART 在 `Qt WebEngine + CesiumJS + pyqtgraph OpenGL` 组合下踩过的实际问题、现象、根因和处理办法，避免后续重复排查同一类故障。

## 当前结论

1. SMART 内嵌 Cesium 不应再依赖外部 Cesium CDN。
2. Cesium 运行时必须使用本地打包资源：
   `src/smart/assets/cesium/vendor/Build/Cesium`
3. 页面 CSP 必须允许 `blob:` workers。
4. Qt Quick / Qt WebEngine / QWidget 顶层窗口必须统一使用 OpenGL 图形 API。
5. 首次页面加载完成后，Python 侧必须主动重新发布一次场景 payload，避免初始化竞态导致首屏空白。
6. 排障时优先使用左侧导航中的 `3D 场景测试` 页面，而不是直接盯着 Dashboard。

## 推荐排障顺序

1. 先运行 WebEngine 诊断工具：

```powershell
.\scripts\diagnose-webengine.ps1
```

2. 在诊断工具中确认：
   `WebGL Probe` 正常渲染蓝色画布。
3. 在诊断工具中确认：
   `Cesium Probe` 正常显示浅色背景、地球和橙色点。
4. 启动 SMART，先打开左侧 `3D 场景测试` 页面。
5. 只有当 `3D 场景测试` 页面正常而 Dashboard 不正常时，才继续排查 Dashboard 的布局嵌入问题。

## 自动化冒烟（不依赖 Qt WebEngine）

当不确定问题是 Qt 集成层还是页面 / Cesium 资源本身时，可先用无头 Chromium
直接打开仓库中的诊断 HTML，把 Qt 这一层从因果链中剔除：

```powershell
# 安装可选诊断依赖（首次执行时需要）
.\.venv\Scripts\python.exe -m pip install "playwright>=1.50,<2"
.\.venv\Scripts\python.exe -m playwright install chromium

# 跑一遍 webgl_probe + cesium_probe
.\.venv\Scripts\python.exe scripts\cesium_diagnostics.py

# 加上 mission_view，并以可见模式打开浏览器
.\.venv\Scripts\python.exe scripts\cesium_diagnostics.py --include-mission --headed
```

输出位于 `output/playwright/`：

- `webgl_probe.png` / `cesium_probe.png` 等截图
- `report.json` 含每个页面的状态、耗时、控制台告警与错误列表

**判读规则：**

- 两个探针都 `PASS` → 资源 / Cesium / WebGL 链路 OK，问题大概率在 Qt WebEngine
  集成层（CSP、frame、graphics backend、scene 时序），按 `AGENTS.md`
  *Cesium / Qt WebEngine Pitfalls* 顺序排查。
- 探针 `FAIL` → 问题在本地资源（Cesium 包、HTML、CSP 自身）。检查
  `src/smart/assets/cesium/vendor/Build/Cesium` 是否完整，必要时执行
  `.\scripts\vendor-cesium.ps1` 重新拉取。

也提供了一个 console script：装包后可直接 `smart-cesium-diagnostics`。

## 现象与根因对照

### 现象 1

三维页面整块发黑，但状态栏显示“已就绪”。

### 根因 1

不要先假设是 GPU 或 WebGL 故障。需要先用诊断页区分：

- 如果 `WebGL Probe` 正常，说明 WebGL 上下文本身可用。
- 如果 `Cesium Probe` 也正常，说明问题不在通用 Cesium 运行时，而在 SMART 自己的嵌入路径、页面脚本或初始化顺序。

### 现象 2

控制台出现类似错误：

```text
QQuickWidget: Failed to get a QRhi from the top-level widget's window
The top-level window is not using the expected graphics API for composition
```

### 根因 2

Qt Quick 选择的合成图形 API 与顶层 `QWidget` 窗口不一致。

SMART 同时使用了：

- `pyqtgraph.opengl.GLViewWidget`
- `Qt WebEngine`
- Qt Quick 内部合成

如果其中一部分走 D3D11，另一部分走 OpenGL，就会出现该类报错。

### 处理 2

必须在应用启动阶段统一图形 API：

- `QSG_RHI_BACKEND=opengl`
- `QT_OPENGL=desktop`
- `QQuickWindow.setGraphicsApi(OpenGL)`

对应代码入口：

- `src/smart/app_runtime.py`

## 现象 3

日志中持续出现：

```text
ssl_client_socket_impl.cc:915 handshake failed ... net_error -101
```

### 根因 3

Qt WebEngine 与外部 Cesium 站点 TLS 握手失败，导致无法从 CDN 拉取 `Cesium.js`、`widgets.css` 等资源。

### 处理 3

不能继续依赖 CDN，必须改为本地打包 Cesium 运行时。

当前本地运行时目录：

```text
src/smart/assets/cesium/vendor/Build/Cesium
```

更新本地 Cesium 的脚本：

```powershell
.\scripts\vendor-cesium.ps1
```

## 现象 4

页面不再报 CDN 错误，但始终停在 `Loading...`。

### 根因 4

本地 Cesium 已经加载，但页面 CSP 阻止了 Cesium 创建 `blob:` workers。

实际遇到过的错误类似：

```text
Refused to create a worker from 'blob:file://...'
```

### 处理 4

以下页面的 CSP 必须允许：

- `script-src blob:`
- `worker-src blob:`

相关文件：

- `src/smart/assets/cesium/mission_view.html`
- `src/smart/assets/diagnostics/cesium_probe.html`

## 现象 5

首次打开 `3D 场景测试` 页面空白，但点击“重新加载场景”后立即正常。

### 根因 5

这是初始化竞态，不是 Cesium 渲染能力问题。

竞态路径如下：

1. HTML 页面完成初始化后，通过 `QWebChannel` 调用 `requestScene()`
2. 这时 Python 侧可能尚未把 `_page_loaded` 置为 `True`
3. `_publish_scene()` 因条件未满足直接返回
4. 首次 payload 没发出去
5. 用户手动点“重新加载场景”后，时序改变，于是恢复正常

### 处理 5

在 `loadFinished(ok=True)` 后，由 Python 侧主动再发布一次 scene payload。

相关代码入口：

- `src/smart/ui/widgets/cesium_mission_view.py`

## 当前关键文件

- `src/smart/app_runtime.py`
  负责统一 Qt 图形后端
- `src/smart/webengine_diagnostics.py`
  独立诊断窗口，包含 `chrome://gpu`、`WebGL Probe`、`Cesium Probe`
- `scripts/diagnose-webengine.ps1`
  一键启动诊断工具
- `src/smart/assets/diagnostics/webgl_probe.html`
  验证 WebGL 上下文
- `src/smart/assets/diagnostics/cesium_probe.html`
  验证独立 Cesium 运行时
- `src/smart/assets/cesium/mission_view.html`
  SMART 内嵌任务场景页面
- `src/smart/assets/cesium/mission_view.js`
  SMART 任务场景前端逻辑
- `src/smart/ui/widgets/cesium_mission_view.py`
  Python 侧 WebChannel、状态同步、场景发布
- `src/smart/ui/widgets/scene_test_page.py`
  独立 3D 场景测试页面

## 正确的恢复动作

### 当看到 TLS 握手失败

不要继续调布局、相机、实体数据。

应该直接做：

1. 确认页面是否仍引用 CDN
2. 如果引用 CDN，改为本地 `vendor` 资源
3. 重启 SMART

### 当看到 `QQuickWidget` / `QRhi` 错误

不要继续调 Cesium JS。

应该直接做：

1. 检查 `smart.app_runtime.configure_graphics_backend()`
2. 确认 Qt Quick 与 QWidget 统一为 OpenGL
3. 完全退出并重启 SMART

### 当页面停在 `Loading...`

应该按顺序检查：

1. 本地 Cesium 资源是否完整
2. CSP 是否允许 `blob:` workers
3. 页面首次加载后是否重新发布了 scene payload

## 后续开发规则

1. 新增 Cesium 页面时，默认复用本地 `vendor` 运行时，不要再写 CDN 链接。
2. 调整 HTML CSP 时，先确认 Cesium worker 仍然能创建。
3. 如果改动三维场景页面，先验证：
   `smart-webengine-diagnostics`
4. 再验证：
   `3D 场景测试`
5. 最后才验证 Dashboard 内嵌页面。
6. 如果用户反馈“黑的”或“停在 loading”，先收集：
   - 页面状态文案
   - 控制台日志
   - 是否点击“重新加载场景”后恢复

## 快速命令

启动主程序：

```powershell
smart
```

启动诊断工具：

```powershell
.\scripts\diagnose-webengine.ps1
```

更新本地 Cesium：

```powershell
.\scripts\vendor-cesium.ps1
```
