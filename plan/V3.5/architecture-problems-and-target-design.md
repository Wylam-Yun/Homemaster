# HomeMaster V3.5 架构问题与目标设计

**状态：待审查**
**日期：2026-09-12**
**范围：代码架构、CLI、运行环境、ALFWorld Runtime 复用**

## 1. 结论先行

HomeMaster 当前不是没有架构，而是已经形成了一个以 `ApplicationRuntime` 为核心的 Agent 平台，但以下三个边界没有被代码和文档清楚表达：

1. CLI 入口和公共 Application Composition 混在一起；
2. ALFWorld benchmark 编排、环境适配、语义 grounding、评测记录混在一起；
3. 项目配置、机器路径、外部运行资产和启动 shell 逻辑分散在多个位置。

因此重构目标不是重写 Agent Runtime，也不是给 ALFWorld 建第二条 Agent 路径，而是：

> 保留唯一的 `ApplicationRuntime`/`AgentRuntime` 执行核心，把入口、应用组装、场景环境和机器基础设施重新划清边界。

正式 CLI 继续叫 `homemaster`。本文不引入 `hm`。

## 2. 审计依据

本次审计阅读了：

- `src/homemaster/cli/app.py`、`src/homemaster/cli/composition.py`；
- `src/homemaster/application/factory.py`、`runtime.py`、`session.py`；
- `src/homemaster/agent/generic_runtime.py`、`context.py`；
- `src/homemaster/config/config.py`；
- `scripts/homemaster`、`scripts/setup_memory_runtime.py`；
- `src/homemaster/adapters/alfworld_entry.py`；
- `src/homemaster/benchmarking/alfworld/runner.py`、`execution.py`、`env_adapter.py`、`http_client.py`、`trajectory_memory.py`；
- Web、Feishu、Memory、MCP、Browser 的入口；
- 相关架构文档和 Runtime、ALFWorld、配置测试。

当前正式入口由 `pyproject.toml` 声明：

```toml
[project.scripts]
homemaster = "homemaster.cli.app:app"
```

## 3. 当前真实架构

### 3.1 主运行链

```text
CLI / Web / Feishu / ALFWorld benchmark
                    ↓
         CLI composition / factory
                    ↓
          ApplicationRuntime
                    ↓
             AgentRuntime
                    ↓
 Context + Provider + ToolRegistry + ToolExecutor
                    ↓
       外部工具、浏览器、机器人或模拟环境
                    ↓
      EventBus / trace / session / memory
```

`ApplicationRuntime` 负责的不只是调用 Agent，还包含 Session、上下文、工具执行、自动召回、事件、资源作用域、终止决策和 generation fencing。它是系统的统一运行边界，应当保留。

### 3.2 ALFWorld 主运行链

当前 ALFWorld 通过 `AlfworldApplicationEntry` 调用公共 `create_home_application`，然后把 `RunRequest` 交给公共应用：

```text
benchmark command
  → AlfworldBenchmarkRunner
  → AlfworldApplicationEntry
  → create_home_application(tool_environment="alfworld")
  → ApplicationRuntime.run(RunRequest(...))
  → AgentRuntime
  → standard Provider / Context / Session / ToolExecutor
  → ALFWorld semantic tools
  → ALFWorld environment adapter
  → external terminal state
```

审计没有发现 ALFWorld 另有一套 `AgentRuntime`、Provider loop 或 ToolExecutor。这个复用关系是当前最重要的设计资产，重构必须继续保持。

## 4. 当前问题

### 4.1 公共组装入口放在 CLI

`cli.composition` 实际是整个系统的 composition root。它同时了解 Provider、Memory、MindMemOS、Neo4j、MCP、Skills、Extensions、Feishu、Browser、ALFWorld trajectory writer、Event sink、Permission、Artifact、Session 和 Resource scope。

因此 ALFWorld 目前形成了不理想的依赖语义：

```text
ALFWorld → cli.composition → ApplicationRuntime
```

ALFWorld 实际需要的是“公共应用组装能力”，不是 CLI。这个位置会让 Web、Feishu 和未来入口继续依赖一个什么都知道的模块。

### 4.2 CLI 入口聚合了过多层级

`cli.app` 同时注册默认交互、单次请求、Session、Cron、Memory、Gateway、Browser、Web、Benchmark、Dry-run 和 Child worker。

这些命令未必都要删除，但目前命令注册、参数解析、业务组装和生命周期管理的边界不清。用户很难从 CLI 看出 HomeMaster 的主路径，新开发者也必须从一个入口理解所有模式。

### 4.3 ALFWorld runner 太重

`AlfworldBenchmarkRunner` 同时处理：

```text
批量 task/trial 调度
单 episode 生命周期
ApplicationRuntime 启停
Session 管理
Prompt 构造
Runtime stop decision
Trace 读取
Trajectory memory 构造
结果写入
```

这些功能都属于 ALFWorld，但混在一个 runner 中，使 Agent 逻辑、环境失败、评测失败和记录失败难以快速区分。

### 4.4 ALFWorld 文件的概念边界不清

`execution.py` 实际主要是环境动作后的状态判断、step limit、goal 完成、失败分类和 `RuntimeStopDecision`，不是第二套 Agent execution。

`env_adapter.py` 同时包含环境通信、observation 解码、scene object index、canonical label、target grounding、pose/navigation、动作执行和错误分类。

这些逻辑都应该留在 ALFWorld，但应按环境层、语义层、评测层分开表达。

### 4.5 换服务器需要手工理解太多隐式约定

当前运行环境涉及：

```text
config/homemaster.yaml
HOMEMASTER_CONFIG_PATH
环境变量覆盖
scripts/homemaster
.runtime/venv
.runtime/alfworld-venv
.runtime/alfworld
setup_memory_runtime.py
Neo4j / Java / MindMemOS
ALFWORLD_DATA / PYTHONPATH
```

`scripts/homemaster` 不只是启动器，还负责 Python 选择、ALFWorld 环境切换、MindMemOS source discovery、`PYTHONPATH` 拼接和 runtime check。能力本身有合理性，但连接关系分散在 shell、配置和代码中，导致服务器迁移依赖人工记忆。

### 4.6 可选重依赖影响基础路径

普通 Agent、Memory、Browser、Feishu、Web 和 ALFWorld 共处一个项目。ALFWorld 还需要独立 Python、Torch、AI2-THOR、Unity/Xvfb 等环境。

目标不是删除这些能力，而是让基础运行不必默认感知所有重依赖，按 profile 或 command 选择能力。

### 4.7 文档和代码入口不一致

架构文档已经描述“公共 Runtime + 场景适配器”，但代码阅读入口仍然是巨大的 CLI composition 和 benchmark runner。文档中的架构原则没有完全体现在代码边界上。

## 5. 必须保留的设计

- `ApplicationRuntime` 作为统一应用运行边界；
- `AgentRuntime` 作为唯一 Agent loop；
- Provider、ContextAssembler、ToolExecutor 的注入机制；
- SessionManager、generation fencing、EventBus、trace；
- `RunResourceScope` 的资源绑定和清理；
- Memory recall、write queue、evidence ledger、session finalizer；
- ALFWorld 的 reset、pose snapshot、外部动作网关、terminal evidence；
- `homemaster` 作为唯一正式 CLI 名称。

## 6. 目标架构

### 6.1 目标依赖方向

```text
CLI / Web / Feishu / ALFWorld command
                    ↓
          Application Composition
                    ↓
          ApplicationRuntime
                    ↓
             AgentRuntime
                    ↓
       Provider / Tools / Memory / Events
                    ↓
        Browser / Feishu / ALFWorld / MCP
```

入口层选择场景，Application Composition 负责组装，ApplicationRuntime 负责运行，具体环境只实现 adapter 或 port。

### 6.2 公共 Application Composition

把当前 `cli.composition.create_home_application` 的公共部分迁移到 application 层，例如：

```text
homemaster.application.composition
```

目标调用关系：

```text
homemaster CLI ──────┐
Web ──────────────────┤
Feishu ───────────────┤ → application.composition → ApplicationRuntime
ALFWorld benchmark ──┘
```

内部可以按职责拆 helper，但仍然只创建同一套 Runtime：

```text
base          # Session、EventBus、ResourceScope、ApplicationRuntime
providers     # chat/embedding provider
tools         # core/domain/MCP tools
memory        # MindMemOS、recall、queue、trajectory writer
observability # trace、artifact、log sink
integrations  # Browser、Feishu、extension
profiles      # 不同入口所需的组合
```

第一步可以保留 `cli.composition` 兼容导出，避免一次性破坏测试和外部调用。

### 6.3 ALFWorld 目标边界

ALFWorld 可以注入或替换：

```text
Environment backend
Domain/semantic tools
Runtime stop policy
Evaluation policy
Trajectory recorder
```

ALFWorld 不得替换：

```text
Agent loop
Provider abstraction
Context runtime
Session manager
Generic tool executor
Event model
Memory lifecycle
```

建议把 ALFWorld 的职责表达为：

```text
alfworld/
├── benchmark_runner.py       # 多任务调度
├── episode_runner.py         # 单 episode
├── environment/              # worker、adapter、observation、lifecycle
├── semantics/                # object index、grounding、navigation
├── evaluation/               # stop policy、terminal、failure
└── recording/                # trace → trajectory memory
```

这是 ALFWorld 内部职责整理，不是创建第二套 Agent Runtime。
