# STK联动API

<cite>
**本文档引用的文件**
- [stk_link.py](file://src/smart/services/stk_link.py)
- [stk_link_page.py](file://src/smart/ui/widgets/stk_link_page.py)
- [stk_11_6_operations.md](file://src/smart/agents/skills/stk_11_6_operations.md)
- [stk_ephemeris.py](file://src/smart/services/stk_ephemeris.py)
- [test_stk_link.py](file://tests/test_stk_link.py)
- [models.py](file://src/smart/domain/models.py)
</cite>

## 目录
1. [简介](#简介)
2. [项目结构](#项目结构)
3. [核心组件](#核心组件)
4. [架构概览](#架构概览)
5. [详细组件分析](#详细组件分析)
6. [依赖关系分析](#依赖关系分析)
7. [性能考虑](#性能考虑)
8. [故障排除指南](#故障排除指南)
9. [结论](#结论)
10. [附录](#附录)

## 简介

STK联动API是SMART项目中用于与AGI STK 11.6进行集成的关键组件。该API提供了完整的STK场景管理、卫星对象操作、数据导入导出和实时通信功能。通过该API，用户可以实现从SMART项目到STK场景的无缝数据同步，包括轨道数据、姿态信息、测控站点和中继卫星的自动导入。

该系统支持两种连接模式：
- **COM接口模式**：通过Windows COM接口直接控制STK应用程序
- **Socket连接模式**：通过本地Socket连接STK的Connect服务

## 项目结构

STK联动API主要分布在以下模块中：

```mermaid
graph TB
subgraph "服务层"
A[stk_link.py<br/>主服务类]
B[stk_ephemeris.py<br/>轨道数据处理]
end
subgraph "界面层"
C[stk_link_page.py<br/>UI组件]
end
subgraph "辅助模块"
D[stk_11_6_operations.md<br/>技能文档]
E[test_stk_link.py<br/>测试用例]
F[models.py<br/>数据模型]
end
A --> B
C --> A
A --> F
D -.-> A
E -.-> A
```

**图表来源**
- [stk_link.py:1-755](file://src/smart/services/stk_link.py#L1-L755)
- [stk_link_page.py:1-324](file://src/smart/ui/widgets/stk_link_page.py#L1-L324)

**章节来源**
- [stk_link.py:1-755](file://src/smart/services/stk_link.py#L1-L755)
- [stk_link_page.py:1-324](file://src/smart/ui/widgets/stk_link_page.py#L1-L324)

## 核心组件

### StkLinkService类

StkLinkService是STK联动API的核心类，提供了所有公共接口：

#### 主要功能特性
- **场景管理**：创建、连接和管理STK场景
- **卫星对象管理**：导入卫星轨道、姿态和3D模型
- **地面资产同步**：管理测控站点和中继卫星
- **数据导出**：生成STK兼容的轨道和姿态文件
- **实时通信**：支持COM和Socket两种通信方式

#### 关键属性
- `executor`: 当前使用的命令执行器（COM或Socket）
- `_scenario_established`: 场景建立状态标志
- `_commands`: 已执行的STK命令列表

**章节来源**
- [stk_link.py:199-558](file://src/smart/services/stk_link.py#L199-L558)

## 架构概览

STK联动API采用分层架构设计，实现了清晰的关注点分离：

```mermaid
graph TB
subgraph "应用层"
UI[用户界面层]
API[API接口层]
end
subgraph "业务逻辑层"
Svc[StkLinkService<br/>核心服务]
Utils[辅助工具类]
end
subgraph "数据访问层"
Workspace[项目工作空间]
Data[数据文件]
end
subgraph "外部系统"
STK[STK 11.6应用]
COM[COM接口]
Socket[Socket连接]
end
UI --> API
API --> Svc
Svc --> Workspace
Svc --> Utils
Utils --> Data
Svc --> STK
STK --> COM
STK --> Socket
```

**图表来源**
- [stk_link.py:199-558](file://src/smart/services/stk_link.py#L199-L558)
- [stk_link_page.py:36-324](file://src/smart/ui/widgets/stk_link_page.py#L36-L324)

## 详细组件分析

### 连接建立机制

#### 启动和连接流程

```mermaid
sequenceDiagram
participant App as 应用程序
participant Service as StkLinkService
participant COM as COM接口
participant Socket as Socket连接
participant STK as STK应用
App->>Service : connect()
Service->>Service : 检查现有连接
alt COM可用
Service->>COM : 获取Active STK实例
COM->>STK : 返回Personality2
STK-->>COM : 返回根对象
COM-->>Service : 返回StkComExecutor
else Socket模式
Service->>Socket : 检查端口连接
Socket->>STK : 连接localhost : 5001
STK-->>Socket : 返回连接
Socket-->>Service : 返回StkSocketExecutor
end
Service-->>App : 返回执行器
```

**图表来源**
- [stk_link.py:111-141](file://src/smart/services/stk_link.py#L111-L141)
- [stk_link.py:144-167](file://src/smart/services/stk_link.py#L144-L167)

#### 连接模式选择策略

系统优先使用COM接口，当COM不可用时自动降级到Socket连接：

```mermaid
flowchart TD
Start([开始连接]) --> CheckCOM{"检查COM可用性"}
CheckCOM --> |可用| GetActiveSTK["获取活动STK实例"]
CheckCOM --> |不可用| LaunchSTK["启动STK应用"]
GetActiveSTK --> CheckRoot{"检查Personality2"}
CheckRoot --> |成功| UseCOM["使用COM接口"]
CheckRoot --> |失败| LaunchSTK
LaunchSTK --> CheckSocket{"检查Socket端口"}
CheckSocket --> |就绪| UseSocket["使用Socket连接"]
CheckSocket --> |未就绪| WaitReady["等待连接就绪"]
WaitReady --> CheckSocket
UseCOM --> End([连接建立])
UseSocket --> End
```

**图表来源**
- [stk_link.py:111-188](file://src/smart/services/stk_link.py#L111-L188)

**章节来源**
- [stk_link.py:111-188](file://src/smart/services/stk_link.py#L111-L188)

### 场景管理功能

#### 场景创建和同步

```mermaid
classDiagram
class StkLinkService {
+connect() StkCommandExecutor
+create_new_scenario(project) str
+has_current_scenario() bool
+sync_current_scenario_analysis_time() bool
+sync_current_scenario_time(current_utc) bool
+import_project_to_stk() StkLinkResult
-_execute(command, ignore_failure) list[str]
-_mark_scenario_established() void
}
class StkCommandExecutor {
<<interface>>
+execute(command, ignore_failure) list[str]
}
class StkComExecutor {
-_root Any
+root Any
+execute(command, ignore_failure) list[str]
}
class StkSocketExecutor {
-_host str
-_port int
+execute(command, ignore_failure) list[str]
}
StkLinkService --> StkCommandExecutor : 使用
StkCommandExecutor <|.. StkComExecutor : 实现
StkCommandExecutor <|.. StkSocketExecutor : 实现
```

**图表来源**
- [stk_link.py:199-558](file://src/smart/services/stk_link.py#L199-L558)
- [stk_link.py:52-108](file://src/smart/services/stk_link.py#L52-L108)

#### 场景同步流程

```mermaid
sequenceDiagram
participant Service as StkLinkService
participant STK as STK场景
participant Workspace as 工作空间
participant Files as 数据文件
Service->>Workspace : 加载项目配置
Workspace-->>Service : 返回项目信息
Service->>STK : 创建新场景
STK-->>Service : 返回场景句柄
Service->>Files : 读取轨道历史数据
Files-->>Service : 返回轨迹数据
Service->>STK : 导入卫星轨道
Service->>STK : 设置分析时间范围
Service->>STK : 创建地面站点
Service->>STK : 创建中继卫星
Service->>STK : 应用图形样式
STK-->>Service : 返回同步结果
```

**图表来源**
- [stk_link.py:280-337](file://src/smart/services/stk_link.py#L280-L337)

**章节来源**
- [stk_link.py:223-337](file://src/smart/services/stk_link.py#L223-L337)

### 卫星对象管理

#### 轨道数据导入

系统支持多种轨道数据格式的导入和转换：

```mermaid
flowchart TD
Start([开始导入]) --> LoadData["加载轨道历史数据"]
LoadData --> ParseData["解析CSV数据"]
ParseData --> ValidateData{"验证数据完整性"}
ValidateData --> |通过| WriteEphemeris["写入STK轨道文件"]
ValidateData --> |失败| Error["返回错误"]
WriteEphemeris --> SetState["设置卫星状态"]
SetState --> ApplyGraphics["应用图形样式"]
ApplyGraphics --> ApplyModel["应用3D模型"]
ApplyModel --> Complete([导入完成])
Error --> Complete
```

**图表来源**
- [stk_link.py:280-301](file://src/smart/services/stk_link.py#L280-L301)
- [stk_ephemeris.py:31-111](file://src/smart/services/stk_ephemeris.py#L31-L111)

#### 姿态数据处理

系统能够处理复杂的姿态数据并生成STK兼容的姿态文件：

```mermaid
sequenceDiagram
participant Service as StkLinkService
participant FlightProg as 飞行程序
participant Maneuver as 变轨策略
participant SPICE as SPICE服务
participant Output as 输出文件
Service->>FlightProg : 加载飞行程序
FlightProg-->>Service : 返回姿态事件
Service->>Maneuver : 加载变轨策略
Maneuver-->>Service : 返回机动参数
Service->>SPICE : 计算姿态矩阵
SPICE-->>Service : 返回DCM矩阵
Service->>Output : 写入姿态文件
Output-->>Service : 返回文件路径
```

**图表来源**
- [stk_link.py:385-404](file://src/smart/services/stk_link.py#L385-L404)

**章节来源**
- [stk_link.py:280-404](file://src/smart/services/stk_link.py#L280-L404)

### 数据导出功能

#### STK文件格式生成

系统支持生成多种STK兼容的数据文件：

| 文件类型 | 用途 | 格式规范 |
|---------|------|----------|
| 轨道文件(.e) | 卫星轨道导入 | STK轨道文件格式 |
| 姿态文件(.a) | 卫星姿态导入 | STK姿态文件格式 |
| 中继卫星文件(.e) | GEO中继卫星 | 固定坐标系轨道 |

#### 文件生成流程

```mermaid
flowchart TD
Input[输入数据] --> Validate[数据验证]
Validate --> GenerateHeader[生成文件头]
GenerateHeader --> ProcessData[处理数据点]
ProcessData --> WriteHeader[写入头部信息]
WriteHeader --> WriteData[写入数据内容]
WriteData --> WriteFooter[写入文件尾部]
WriteFooter --> Output[输出文件]
Validate --> |验证失败| Error[返回错误]
Error --> Output
```

**图表来源**
- [stk_ephemeris.py:49-106](file://src/smart/services/stk_ephemeris.py#L49-L106)
- [stk_link.py:560-632](file://src/smart/services/stk_link.py#L560-L632)

**章节来源**
- [stk_ephemeris.py:31-111](file://src/smart/services/stk_ephemeris.py#L31-L111)
- [stk_link.py:560-632](file://src/smart/services/stk_link.py#L560-L632)

### 实时通信功能

#### Socket连接协议

系统通过标准的STK Connect协议进行通信：

| 协议元素 | 描述 | 示例 |
|---------|------|------|
| 请求格式 | ASCII文本，以换行符结尾 | "New / Scenario Test\n" |
| 响应格式 | "ACK"或"NACK"开头 | "ACK\n"或"NACK 错误信息\n" |
| 编码方式 | UTF-8 | 全部ASCII字符 |
| 超时设置 | 3秒 | socket超时 |

#### 命令执行流程

```mermaid
sequenceDiagram
participant Client as 客户端
participant Socket as Socket连接
participant STK as STK服务
participant Parser as 响应解析器
Client->>Socket : 发送命令
Socket->>STK : TCP发送请求
STK->>Parser : 解析命令
Parser->>STK : 执行操作
STK->>Parser : 生成响应
Parser->>Socket : 返回响应
Socket->>Client : 接收并解析响应
Client->>Client : 处理结果
```

**图表来源**
- [stk_link.py:75-108](file://src/smart/services/stk_link.py#L75-L108)

**章节来源**
- [stk_link.py:75-108](file://src/smart/services/stk_link.py#L75-L108)

## 依赖关系分析

### 外部依赖

STK联动API依赖于以下关键外部组件：

```mermaid
graph TB
subgraph "STK相关"
STK116[STK 11.6应用]
COM[COM接口库]
Connect[Connect服务]
end
subgraph "Python库"
Win32COM[pywin32/win32com]
NumPy[NumPy科学计算]
Socket[Python Socket]
end
subgraph "数据处理"
SPICE[SPICE天文数据]
CSV[CSV解析]
JSON[JSON处理]
end
STK116 --> COM
STK116 --> Connect
Win32COM --> COM
NumPy --> SPICE
Socket --> Connect
```

**图表来源**
- [stk_link.py:16-17](file://src/smart/services/stk_link.py#L16-L17)
- [stk_link.py:117-119](file://src/smart/services/stk_link.py#L117-L119)

### 内部依赖关系

```mermaid
graph LR
StkLinkService --> StkComExecutor
StkLinkService --> StkSocketExecutor
StkLinkService --> StkEphemeris
StkLinkService --> ProjectWorkspace
StkLinkService --> FlightProgram
StkLinkService --> LaunchWindow
StkLinkService --> TrackingArc
StkComExecutor --> Win32COM
StkSocketExecutor --> Socket
StkEphemeris --> SPICE
StkEphemeris --> NumPy
```

**图表来源**
- [stk_link.py:18-26](file://src/smart/services/stk_link.py#L18-L26)
- [stk_link.py:57-108](file://src/smart/services/stk_link.py#L57-L108)

**章节来源**
- [stk_link.py:18-26](file://src/smart/services/stk_link.py#L18-L26)

## 性能考虑

### 连接优化策略

1. **连接池管理**：避免频繁创建和销毁连接
2. **批量命令执行**：合并多个相关命令减少网络往返
3. **缓存机制**：缓存已解析的配置和计算结果
4. **异步操作**：使用线程池处理长时间运行的操作

### 内存管理

- **数据流处理**：大文件采用流式处理避免内存溢出
- **对象生命周期**：及时释放不再使用的COM对象
- **临时文件清理**：自动清理生成的中间文件

### 并发安全

- **线程同步**：确保多线程环境下的操作安全性
- **资源锁定**：防止同时修改同一STK对象
- **异常恢复**：在网络中断时自动重连

## 故障排除指南

### 常见问题及解决方案

#### COM接口问题
- **症状**：无法连接到STK COM接口
- **原因**：pywin32未正确安装或STK未启动
- **解决**：检查Python环境中的pywin32安装，手动启动STK应用

#### Socket连接问题
- **症状**：Socket连接超时或拒绝
- **原因**：STK Connect服务未启动或端口被占用
- **解决**：确认STK 11.6已启动，检查端口5001的可用性

#### 数据格式问题
- **症状**：轨道数据导入失败
- **原因**：CSV文件格式不正确或缺少必要字段
- **解决**：验证CSV文件包含必需的列：position_x_m, position_y_m, position_z_m, velocity_x_m_s等

#### 权限问题
- **症状**：无法创建文件或访问STK
- **原因**：权限不足或路径不存在
- **解决**：以管理员权限运行，确保输出目录存在且可写

**章节来源**
- [stk_link.py:95-106](file://src/smart/services/stk_link.py#L95-L106)
- [stk_link.py:113-114](file://src/smart/services/stk_link.py#L113-L114)

### 调试技巧

1. **启用详细日志**：查看执行的每一条STK命令
2. **检查中间文件**：验证生成的轨道和姿态文件
3. **监控STK状态**：确认STK场景和对象的正确性
4. **网络诊断**：使用netstat检查Socket连接状态

## 结论

STK联动API为SMART项目提供了强大而灵活的STK集成能力。通过COM和Socket两种连接模式，系统能够在不同环境下稳定运行。其模块化设计使得功能扩展和维护变得简单，同时提供了完善的错误处理和性能优化机制。

该API的主要优势包括：
- 支持多种连接模式的自动切换
- 完整的轨道和姿态数据处理能力
- 用户友好的界面集成
- 强大的错误处理和恢复机制
- 良好的性能和并发支持

## 附录

### API使用示例

#### 基本使用流程

```python
# 创建服务实例
service = StkLinkService(workspace)

# 连接到STK
executor = service.connect()

# 创建新场景
scenario_name = service.create_new_scenario()

# 同步项目数据
result = service.import_project_to_stk()

# 清理连接
service.clear_executor()
```

#### 高级配置选项

```python
# 自定义连接参数
service = StkLinkService(
    workspace,
    executor=custom_executor
)

# 设置自定义STK路径
os.environ['STK_APP_PATH'] = 'C:\\Program Files\\AGI\\STK 116\\bin\\AgUiApplication.exe'
```

### 配置参考

#### 环境变量
- `STK_APP_PATH`: STK 11.6可执行文件路径
- `SMART_STK_HELP_CONFIG`: STK帮助配置文件路径
- `SMART_STK_HELP_KB`: STK知识库路径
- `SMART_STK_HELP_SCRIPT`: STK帮助脚本路径

#### 配置文件格式
```json
{
    "kb_path": "/path/to/stk11_help.sqlite3",
    "script_path": "/path/to/stkhelp_cli.py",
    "command": "stkhelp"
}
```

**章节来源**
- [stk_11_6_operations.md:28-32](file://src/smart/agents/skills/stk_11_6_operations.md#L28-L32)