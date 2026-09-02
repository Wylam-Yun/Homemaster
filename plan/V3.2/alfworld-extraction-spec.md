# HomeMaster V3.2 ALFWorld 外迁与统一入口 Spec

状态：待书面审阅
日期：2026-09-02
前置条件：仓库清理与环境复现 spec 全部门禁通过

## 1. 目标

ALFWorld 以后是专门适配 HomeMaster、用于持续改进 HomeMaster 的 benchmark 项目，但它不再
是 HomeMaster 核心包内部的一个特殊分支。

锁定依赖方向：

```text
ALFWorld benchmark -> HomeMaster 稳定运行接口
ALFWorld benchmark -> ALFWorld / AI2-THOR
HomeMaster -X-> ALFWorld benchmark
```

Web 和飞书只是 HomeMaster 的通用 transport。ALFWorld 通过通用 application/environment
接口被入口加载，不拥有第二套 Web server、Gateway、鉴权、会话或事件协议。

## 2. 当前问题和证据

### 2.1 实现规模

当前 `src/homemaster/benchmarking/alfworld/` 有 19 个 Python 文件、15,339 行。主要复杂度
集中在：

- `env_adapter.py`
- `execution.py`
- `types.py`
- `runner.py`

这不是可以直接删除的垃圾，而是一套完整 benchmark/environment 实现，应迁入其真正的
owner 仓库。

### 2.2 当前核心耦合点

- `cli/app.py` 直接导入 ALFWorld benchmark 命令和 tracing。
- `cli/gateway_command.py` 直接导入 `gateway.alfworld`。
- `web/serve.py` 直接构造 `AlfworldGatewayApplication`。
- `adapters/profiles.py` 把 `alfworld` 写入固定 Literal 并导入 ALFWorld tools。
- HomeMaster package-data 包含 `benchmarking/alfworld/*.json`。
- 当前扩展系统只能贡献 tools/hooks/cleanup，不能拥有 environment backend、application
  factory、配置验证或 doctor contribution。

### 2.3 错误边界

以下两个极端都不接受：

1. 继续在 HomeMaster 中保留 `if environment == "alfworld"`。
2. 在 ALFWorld 仓库复制一套 `serve`、Gateway、会话和飞书接入。

正确边界是 HomeMaster 拥有 transport 和 runtime，ALFWorld 包只提供被加载的应用实现。

## 3. 目标所有权

| 能力 | HomeMaster | ALFWorld 仓库 |
|---|---|---|
| Agent Runtime、记忆、权限、事件 | owner | consumer |
| CLI interactive/run | owner | 不复制 |
| Web server/Web Console | owner | 不复制 |
| 飞书 Gateway、鉴权、会话 | owner | 不复制 |
| 通用 application/environment 协议 | owner | implementer |
| ALFWorld env reset/step/observation | 不包含 | owner |
| ALFWorld tools/grounding/translator | 不包含 | owner |
| ALFWorld taskset/runner/scoring/tracing | 不包含 | owner |
| AI2-THOR、dataset、pose snapshot | 不包含 | owner |
| benchmark CLI | 不包含 | owner |

## 4. HomeMaster 通用扩展接口

### 4.1 注册方式

外部 Python 包通过标准 package entry point 注册 application provider：

```toml
[project.entry-points."homemaster.applications"]
alfworld = "homemaster_benchmark.application:provider"
```

HomeMaster 使用 `importlib.metadata.entry_points()` 发现 provider。核心代码不得写出
`alfworld` 的 import path、模块名判断或 fallback。

配置按 provider ID 选择实现，并把 provider 专属配置作为隔离 mapping 交给 provider 自己
验证。HomeMaster 的 Pydantic schema 不声明 ALFWorld 字段。

### 4.2 provider 必须贡献的职责

通用协议至少覆盖：

- 稳定的 provider ID 和能力描述。
- application factory。
- environment session 的创建、reset、关闭和健康检查。
- 工具 factory、run hooks 和清理回调。
- provider 专属配置验证及去敏 public summary。
- 可选 doctor checks。

协议不得要求 HomeMaster 知道 ALFWorld episode、taskset、pose、THOR 或 dataset 类型。

### 4.3 生命周期和资源 owner

```text
HomeMaster CLI/Web/Gateway
  -> ApplicationProviderRegistry.resolve(provider_id)
  -> provider.validate(options)
  -> provider.create_application(HomeMaster services)
  -> HomeMaster Runtime owns run/session/events/permissions/memory
  -> provider owns environment reset/step/close
```

- HomeMaster 创建和关闭通用 Runtime。
- provider 创建和关闭环境资源。
- 关闭必须幂等；部分构造失败时只清理由该 provider 已成功创建的资源。
- 多个接口实现必须通过协议一致性 audit，防止鸭子类型漏实现。

### 4.4 配置示例

ALFWorld 仓库可以提供自己的去敏运行配置，例如：

```yaml
application:
  provider: alfworld
  options:
    config_path: ./configs/base_config.yaml
    split: eval_out_of_distribution
```

该文件属于 ALFWorld benchmark 仓库。运行时仍使用 HomeMaster 唯一入口：

```bash
homemaster serve --config /path/to/alfworld/homemaster.yaml
homemaster gateway --config /path/to/alfworld/homemaster.yaml
```

## 5. CLI 边界

### 5.1 留在 HomeMaster

- `homemaster run`
- `homemaster serve`
- `homemaster gateway`
- 通用 doctor/session/memory 命令

`serve` 和 `gateway` 不能有 `--alfworld` 开关；它们只解析通用配置并加载 provider。

### 5.2 迁到 ALFWorld 仓库

- `benchmark-alfworld`
- `benchmark-alfworld-taskset`
- taskset 选择、并行运行、episode 汇总和 benchmark 报告

新命令由 ALFWorld 仓库拥有：

```bash
hm-benchmark run
hm-benchmark taskset
```

不创建 `hm-benchmark serve` 或 `hm-benchmark gateway`。

## 6. 迁移清单

### 6.1 迁出 HomeMaster 的实现

- `src/homemaster/benchmarking/alfworld/`
- `src/homemaster/adapters/alfworld_entry.py`
- `src/homemaster/gateway/alfworld.py` 中 ALFWorld application/environment 逻辑
- `src/homemaster/cli/benchmark_alfworld.py`
- ALFWorld 专属配置、setup、测试 fixture、测试和用户文档
- `pyproject.toml` 的 `alfworld` extra 和 ALFWorld package-data

`gateway/alfworld.py` 中若存在真正通用的 transport 行为，必须先提取为无领域名的
HomeMaster 组件；不能把飞书连接、鉴权或通用会话代码一起搬走。

### 6.2 ALFWorld 仓库目标结构

在 `/home/haodong2/weilin/red_bird/alfworld` 中新增独立 Python package：

```text
homemaster_benchmark/
├── application/
├── environment/
├── execution/
├── tools/
├── configs/
├── cli/
└── tests/
```

确切文件拆分可以在实施计划中根据当前 5,106 行 `env_adapter.py` 和 2,544 行
`execution.py` 的职责进一步细化，但不得为了搬迁重写 ALFWorld 行为。

### 6.3 依赖和版本

- ALFWorld benchmark 声明 HomeMaster 的兼容版本范围，并在验收环境安装指定 commit 构建物。
- 本地联合开发可以使用 editable install，但验收必须使用干净安装，不依赖相邻目录隐式
  加入 `PYTHONPATH`。
- HomeMaster 的 lockfile、extras 和 import graph 中不保留 ALFWorld/AI2-THOR 依赖。
- ALFWorld 的大数据、Unity cache 和 trace 永远不进入 HomeMaster 发布物。

## 7. 分期实施

### Phase A：建立通用接口

- 扩展当前 extensions contract，使其可以注册 application/environment provider。
- 使用一个非 ALFWorld 的最小测试 provider 验证 loader、配置、生命周期和 doctor。
- CLI/Web/Gateway 先切到通用 registry，再迁任何 ALFWorld 文件。

### Phase B：在 ALFWorld 仓库建立 provider

- 创建 `homemaster_benchmark` package 和 entry point。
- 原样迁移 ALFWorld environment、tools、runner 和配置。
- 让现有 benchmark 测试在新包路径下运行。

### Phase C：统一入口接线

- 通过 HomeMaster `serve` 加载 ALFWorld provider。
- 通过 HomeMaster `gateway` 加载同一个 provider。
- 验证 Web 和飞书复用同一事件、权限、记忆和 session 管线。

### Phase D：删除核心耦合

- 删除 HomeMaster 的 ALFWorld 模块、CLI 命令、flags、Literal 和 package-data。
- 删除临时导入桥；不长期维护兼容 wrapper。
- 更新两个仓库的所有脚本和文档到新入口。

### Phase E：独立发布验证

- HomeMaster 在未安装 ALFWorld 的干净环境构建并运行。
- ALFWorld benchmark 在只通过声明依赖获得 HomeMaster 的环境构建并运行。
- 离线 HomeMaster bundle 不携带 ALFWorld；ALFWorld 如需离线交付，另做独立 bundle。

## 8. 错误处理

- provider 未安装：报告 provider ID、发现到的 provider 列表和安装责任，不回退 local_robot。
- entry point 重名：启动失败并列出两个发行包，不按发现顺序随机选择。
- provider 配置错误：错误路径限定在 `application.options`，secret 值必须 redacted。
- environment 创建失败：provider 清理部分资源，HomeMaster 不创建活跃 session。
- environment 运行中断开：通过通用结构化错误进入 Runtime，不由 Web/Gateway 特判。
- provider 关闭失败：记录结构化错误并继续关闭 HomeMaster 自己拥有的资源。

## 9. 验收标准

### 9.1 HomeMaster 独立门

在完全未安装 `alfworld` 和 `homemaster_benchmark` 的干净环境中逐项断言：

1. `uv sync --frozen` 基础安装成功。
2. HomeMaster import、CLI、必选记忆、Web 和 Gateway 通用测试成功。
3. `rg` 在 `src/homemaster`、当前配置模板和 `pyproject.toml` 中找不到 ALFWorld 运行耦合。
4. 构建物中不存在 `benchmarking/alfworld`、AI2-THOR 或 ALFWorld package-data。
5. 非 ALFWorld 测试 provider 能通过 CLI、Web、Gateway 三个入口运行并完成生命周期关闭。

历史 changelog、归档报告中描述 ALFWorld 不计为运行耦合。

### 9.2 ALFWorld benchmark 门

在 ALFWorld 仓库的干净环境中逐目标断言：

1. 安装 ALFWorld benchmark 及其声明的 HomeMaster 版本，返回码为 0。
2. entry point 可被 HomeMaster 枚举并唯一解析。
3. `hm-benchmark run` 真实完成至少一个 episode。
4. 每个 episode 分别核对环境返回状态、任务终态和关闭状态，不能用全局 best/any 掩盖
   单例失败。
5. `hm-benchmark taskset` 逐任务记录结果和失败原因。
6. HomeMaster `serve` 加载同一 provider，真实 reset 环境并执行一步动作。
7. HomeMaster `gateway` 加载同一 provider；飞书连接的 live 验收按可用凭据独立执行，不能
   用内部 trace 替代外部返回码和会话终态。

### 9.3 依赖方向门

- 使用 import graph 和 wheel 解包双重检查 HomeMaster 不引用 ALFWorld。
- 暂时移走 ALFWorld checkout 后，HomeMaster 全部非 benchmark 验收仍成功。
- 暂时移走 HomeMaster checkout 后，已安装的 ALFWorld benchmark 仍从环境中的声明版本运行，
  证明没有相邻源码路径依赖。

## 10. 文档同源

HomeMaster 更新：

- README 删除内置 ALFWorld 能力和命令。
- application/runtime 架构文档描述 provider registry。
- Web、Gateway 用户指南使用通用 provider 配置。
- CHANGELOG 明确公开 CLI 移除和依赖方向变化。

ALFWorld 仓库更新：

- README 说明它是 HomeMaster 专用 benchmark。
- 用户指南覆盖安装、taskset、真实 episode 和统一 Web/Gateway 入口。
- 架构文档描述 ALFWorld provider 对 HomeMaster contract 的实现。
- CHANGELOG 记录迁入来源、兼容 HomeMaster 版本和命令变化。

## 11. 非目标

- 不在迁移过程中重写 ALFWorld 算法、任务定义或评分逻辑。
- 不因迁移改变 benchmark 分数基线。
- 不给 ALFWorld 创建第二套 Web server 或飞书 Gateway。
- 不让 HomeMaster 的通用配置 schema 包含 ALFWorld 字段。
- 不保留长期 `benchmark-alfworld` 兼容命令或 `--alfworld` flags。
- 不把 ALFWorld、AI2-THOR 或数据集加入 HomeMaster 离线 bundle。
