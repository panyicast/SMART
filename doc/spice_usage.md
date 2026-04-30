# SPICE 使用要求与使用方法

## 目标

SMART 中凡是与轨道、时间、坐标系、状态向量相关的处理，默认都应优先调用 SPICE/SpiceyPy 提供的标准函数实现，尽量避免重复维护一套本地手写算法。

适用范围包括：

- UTC/历元时间解析、归一化、转换
- 坐标系与参考系转换
- 状态向量查询
- 轨道六根数与状态向量互转
- 轨道采样与二体轨道传播
- STK 星历导入后的时间与坐标转换

只有在以下情况才允许保留手工计算兜底：

- 当前 Python 环境未安装 `spiceypy`
- 本地所需内核缺失，无法完成对应转换
- SPICE 没有直接等价接口，且功能又必须保留

## 本地内核要求

默认本地调用，不依赖在线内核服务。

推荐至少准备以下地球轨道分析常用内核并放在 `data/kernels/`：

- `naif0012.tls`
- `pck00011.tpc`
- `earth_assoc_itrf93.tf`
- `earth_latest_high_prec.bpc`
- `de440s.bsp`

建议目录结构：

```text
data/kernels/
  naif0012.tls
  pck00011.tpc
  earth_assoc_itrf93.tf
  earth_latest_high_prec.bpc
  de440s.bsp
```

支持自动发现的内核后缀：

- `.tls`
- `.tpc`
- `.tf`
- `.bsp`
- `.bpc`
- `.bc`

## 默认加载规则

`smart.services.spice_service.SpiceKernelManager` 现在会优先使用本地内核目录，并支持自动加载。

默认查找顺序：

1. 当前项目目录下的 `data/kernels/`
2. 仓库根目录下的 `data/kernels/`

同名内核按文件名去重，优先保留前面目录中的文件。

在主窗口启动或切换项目时，`MainWindow._reset_spice_workspace()` 会：

1. 配置以上两个本地内核根目录
2. 调用 `clear()` 清空已加载内核
3. 调用 `ensure_local_kernels_loaded()` 自动加载本地内核

因此，新增项目级内核时，优先放到项目自己的 `data/kernels/`，它会覆盖仓库级同名内核。

## 服务接口

核心入口文件：`src/smart/services/spice_service.py`

当前建议直接复用的接口：

- `default_local_kernel_roots(preferred_roots=None)`
  - 生成默认本地内核根目录列表
- `SpiceKernelManager.configure_local_kernel_roots(kernel_roots)`
  - 配置项目级 + 仓库级内核搜索路径
- `SpiceKernelManager.ensure_local_kernels_loaded()`
  - 自动扫描并加载本地内核
- `SpiceKernelManager.utc_to_et(utc)`
  - 使用 `str2et`
- `SpiceKernelManager.et_to_utc(et, precision=3)`
  - 使用 `et2utc`
- `SpiceKernelManager.transform_position(position_km, from_frame=..., to_frame=..., utc=...)`
  - 使用 `pxform`
- `SpiceKernelManager.transform_state(position_km, velocity_km_s, from_frame=..., to_frame=..., utc=...)`
  - 使用 `sxform`
- `SpiceKernelManager.state(target, observer, utc, frame="J2000", aberration="NONE")`
  - 使用 `spkezr`

这些接口内部会先尝试自动加载本地内核，再执行 SPICE 调用。

## 已接入的 SPICE 用法

### 1. 时间处理

`src/smart/services/orbit_initialization.py` 中的 `normalize_utc_epoch()` 优先使用：

- `str2et`
- `et2utc`

做历元解析与标准化。只有 SPICE 不可用或转换失败时，才退回 Python 标准库解析。

### 2. 轨道六根数与状态向量

`src/smart/services/orbital_mechanics.py` 中已改为 SPICE 优先：

- `state_from_true_anomaly()` 优先使用 `conics`
- `sample_orbit()` 优先使用 `conics`
- `orbital_elements_from_state_vector()` 优先使用 `oscltx`

仅在 SPICE 不可用或调用失败时退回手工公式。

### 3. 参考系转换

`SpiceKernelManager.transform_position()` 和 `transform_state()` 分别封装：

- `pxform`
- `sxform`

后续新增坐标系转换时，优先从这两个接口扩展，不要在业务层重复写旋转矩阵。

### 4. 天体状态查询

`SpiceKernelManager.state()` 封装了 `spkezr`，用于目标天体相对观测体的状态向量查询。

如果后续页面或服务需要行星/卫星状态，优先走这个接口。

## STK `.e` 星历导入规则

`src/smart/services/orbit_initialization.py` 的 STK 导入逻辑当前约束如下：

- 只支持地心 `Earth` 中心体
- 只支持 `EphemerisTimePosVel` 或数值型 `TimePosVel`
- 惯性系 `J2000`、`ICRF`、`Inertial` 可直接使用
- 地固系别名 `Fixed`、`ITRF93`、`IAU_EARTH` 会尝试通过 SPICE 转为 `J2000`

若遇到以下情况会直接报错：

- 非 Earth 中心体
- 不支持的距离单位
- SPICE 无法识别的自定义坐标系
- 需要坐标转换但本地缺少对应内核

也就是说，导入 STK `.e` 时，如果文件是地固系，必须保证本地内核已具备地球定向与参考系转换能力。

## 开发要求

后续新增功能时，遵循以下规则：

1. 先查 SPICE 有没有等价函数，再决定是否自己实现。
2. 轨道、时间、坐标转换逻辑优先放在 `services/`，不要散落在 UI 页面内。
3. 只要涉及参考系转换，就优先通过 `SpiceKernelManager` 完成。
4. 只要涉及历元标准化，就优先通过 `utc_to_et()` / `et_to_utc()` 完成。
5. 如果必须保留手工兜底，代码里要明确把 SPICE 路径作为首选，手工路径作为 fallback。
6. 新增依赖某类转换的功能时，要同时补测试，覆盖：
   - SPICE 可用路径
   - SPICE 不可用或失败的 fallback 路径

## 常见调用示例

### 加载默认本地内核

```python
from smart.services.spice_service import SpiceKernelManager

manager = SpiceKernelManager()
manager.ensure_local_kernels_loaded()
```

### UTC 转 ET，再转回标准 UTC

```python
from smart.services.spice_service import SpiceKernelManager

manager = SpiceKernelManager()
et = manager.utc_to_et("2026-04-18T12:00:00Z")
utc = manager.et_to_utc(et)
```

### 坐标系下的位置/速度转换

```python
from smart.services.spice_service import SpiceKernelManager

manager = SpiceKernelManager()
position_j2000_km, velocity_j2000_km_s = manager.transform_state(
    [7000.0, 0.0, 0.0],
    [0.0, 7.5, 1.0],
    from_frame="ITRF93",
    to_frame="J2000",
    utc="2026-04-18T12:00:00Z",
)
```

### 由状态向量反求轨道根数

```python
from smart.services.orbital_mechanics import orbital_elements_from_state_vector

elements = orbital_elements_from_state_vector(
    [7000.0, 0.0, 0.0],
    [0.0, 7.5, 1.0],
)
```

### 由轨道根数采样轨道

```python
from smart.domain.models import OrbitalElements
from smart.services.orbital_mechanics import sample_orbit

elements = OrbitalElements(
    semi_major_axis_km=7000.0,
    eccentricity=0.01,
    inclination_deg=45.0,
    raan_deg=30.0,
    argument_of_periapsis_deg=60.0,
    true_anomaly_deg=10.0,
)
trajectory = sample_orbit(elements)
```

## 维护建议

- 新增项目内核时，优先放在项目目录 `data/kernels/`
- 仓库级 `data/kernels/` 适合放通用地球任务常用内核
- 不要把时间转换、坐标转换、轨道根数互转重新散写成多个版本
- 若后续需要支持更多 STK 坐标系，优先通过 SPICE frame 转换扩展
