# HomeMaster V3.5 架构问题与目标设计

**状态：已实现并完成静态、package-data、clean build 与远端 THOR smoke 验收（2026-09-26）**
**日期：2026-09-26**
**范围：代码架构、Application Composition、Application Runtime、ALFWorld THOR Harness**

## 1. 已锁定的架构决策

> 状态更新：2026-09-26。本地 composition、canonical tool contract、THOR Harness、benchmark
> lifecycle 和 NDJSON worker 已实现并通过静态/focused gates；远端 doctor 为 `16/16 PASS`。
> 单 episode 和 bounded provider-backed taskset CLI 均有逐实例成功、close、worker exit 与
> stderr 证据；taskset runner 现在额外写入 `worker.json`。完整 clean checkout/release gate、
> 历史 fixture 清理和架构目标文档交叉引用现已补齐；仅 commit 前的最终文档审计仍待执行。
> taskset 连续 `set_task` 已走
> `WorkerAlfworldAdapter`，并修复了 taskset 漏传 `alfworld_harness` 依赖的问题。

本次讨论锁定以下决策，后续实现和审计都以此为准：

1. **只维护 `AlfredThorEnv`。** 不再维护 `AlfredTWEnv`、TextWorld 或文本环境分支。
2. **只保留 V1.8 Oracle Harness。** 不再保留 legacy navigation、legacy manipulation、legacy feedback 或运行时二选一开关。
3. **不保留兼容导出和过渡路径。** 这是破坏性架构重构，允许在独立分支完成；旧调用方、旧测试和旧入口同步迁移或删除。
4. **`application.composition` 是唯一公共应用组装入口。** CLI、Web、Feishu、ALFWorld benchmark 都从这里创建应用；`cli.composition` 不再承担公共组装职责。
5. **`ApplicationRuntime` 是唯一通用运行边界，`AgentRuntime` 是唯一 Agent loop。** ALFWorld 不得复制 Provider loop、Context runtime、Session manager 或 ToolExecutor。
6. **ALFWorld 复杂性封装在 HomeMaster 自己的 Harness 中。** 不修改第三方 ALFWorld 源码；HomeMaster Harness 负责 THOR 场景、动作、终态、评测和记录。
7. **Tool 只维护当前 canonical 标准。** `ToolDefinition`、`RegisteredTool`、异步 executor、`ToolExecutionContext` 和 `ToolExecutionResult` 是唯一工具执行协议；旧 `ToolSpec`、旧 `ToolResult`、同步 executor 和兼容 adapter 全部删除，不在运行时保留新旧双轨。
8. **运行时只维护两个 Python 环境。** 通用环境必备 HomeMaster、MindMemOS、Provider、ApplicationRuntime、Tools、Skills 和 MCP；ALFWorld 环境只负责 ALFWorld、AI2-THOR、Torch、图像依赖和独立 worker。MindMemOS 不单独建立 Python 环境；Neo4j、Java 和 Unity 是外部运行时，不计入 Python 环境数量。
9. **ALFWorld worker 必须是真隔离。** worker 不得 import `homemaster`、主环境 `site-packages` 或 MindMemOS 源码；HomeMaster 与 worker 只通过版本化 typed IPC 协议交互。内部 benchmark transport 不再使用 loopback HTTP。
10. **复现以仓库本地绑定和锁文件为准。** 配置文件只保存 repo-local `.runtime/...` 路径；服务器绝对路径只允许出现在一次性的 setup 参数中。通用依赖和 ALFWorld 依赖分别锁定，迁移服务器只需重新绑定外部资源，不修改源码或运行命令。

## 2. 结论先行

HomeMaster 已经形成了以 `ApplicationRuntime` 为核心的 Agent 平台。当前主要问题不是缺少 Runtime，而是职责归属和动作语义没有收敛：

- CLI 入口同时承担公共 application composition；
- ALFWorld benchmark 编排、THOR 环境访问、目标解析、动作执行、终态判断和记录混在一起；
- V1.8 Oracle 与旧 THOR 执行代码并存，正式入口通过布尔开关选择路径；
- TextWorld 分支增加了不再需要的工具、配置、类型和测试；
- Tool registry 仍通过旧 `ToolSpec`、同步 executor 和 `legacy_adapter` 组装部分正式工具；
- `contracts.py` 与 `base.py` 仍存在重复的 Tool context/result 表达，canonical contract 尚未成为唯一执行 seam；
- Browser、Gateway、Benchmark 的入口适配器与公共 Runtime 的关系没有清楚表达。

重构目标不是重写 Agent Runtime，而是：

> 保留唯一的 ApplicationRuntime/AgentRuntime 执行核心，把入口、应用组装、通用运行时、ALFWorld Harness 和外部环境重新划清边界，并删除不再支持的旧路径。

正式 CLI 继续叫 `homemaster`，不引入 `hm`。

## 3. 术语和动作语义

### 3.1 Legacy

本文中的 **legacy** 专指 V1.8 Oracle 之前的 ALFWorld THOR 执行实现，包括：

- `env.step("go to ...")`、admissible commands 和文本 observation 驱动的导航；
- 运行时枚举、尝试多个候选位姿；
- 运行时临时解析 object，而不是消费 reset-time immutable scene snapshot；
- `_execute_thor_manipulation()` 及其直接动作分支；
- `_legacy_execution_feedback()` 和从字符串推导成功失败。

它不是另一个 Agent Runtime，而是旧的环境动作语义。由于它与 Oracle 的目标解析、动作授权、失败分类和终态证明方式不同，不再作为正式路径保留。

### 3.2 V1.8 Oracle Harness

V1.8 Oracle 不是向模型泄漏专家轨迹或标准答案，而是 Harness 内部使用确定性环境证据执行动作：

```text
reset
  -> scene object index + immutable pose snapshot
  -> grounding 到唯一场景对象
  -> snapshot lookup 唯一 pose
  -> 当前 THOR event 验证 identity / visibility / bbox / state
  -> OracleActionGateway 发出一次外部动作
  -> 返回码 + 真实终态验证
  -> AlfworldExecutionFeedback
```

```text
robot_go_to
  -> grounding -> OracleNavigationExecutor
  -> 唯一 TeleportFull -> pose / visibility / world verification

robot_manipulate
  -> grounding -> OracleManipulationExecutor
  -> action precondition -> gateway -> terminal-state verification
```

## 4. 当前真实架构与问题

本节前半记录 V3.5 开工时的基线问题；截至 2026-09-26，目标迁移已落地。当前实现链路以 `src/homemaster/application/composition`、`src/homemaster/application/runtime.py`、`src/homemaster/alfworld/harness.py` 和 `src/homemaster/alfworld/worker_client.py` 为准，历史 HTTP/混合解释器描述只用于解释迁移动机。

当前核心运行链：

```text
CLI / Web / Feishu / ALFWorld benchmark
                    ↓
          cli.composition / factory
                    ↓
          ApplicationRuntime
                    ↓
             AgentRuntime
                    ↓
 Context + Provider + ToolRegistry + ToolExecutor
                    ↓
       外部工具、浏览器、机器人或 ALFWorld
                    ↓
      EventBus / trace / session / memory
```

迁移前环境边界并没有实现上面的抽象边界，真实链路曾经是：

```text
scripts/homemaster
  -> 切换到 .runtime/alfworld-venv
  -> PYTHONPATH 注入 repo/src + 通用 site-packages + MindMemOS 源码根
  -> http_worker import homemaster.benchmarking.alfworld.env_adapter
  -> loopback HTTP -> HomeMaster-side http_client
```

这不是两个独立环境，而是一个由启动器临时拼接出来的混合解释器。该基线已由 NDJSON worker 替换；当前代码证据和验收记录保留如下，作为迁移前问题的可追溯记录：

- `pyproject.toml` 的 `alfworld` extra 只有 `pyyaml`，没有 ALFWorld、AI2-THOR、Torch 或完整图像依赖锁；
- `scripts/setup_memory_runtime.py` 同时绑定通用 `.runtime/venv`、`.runtime/alfworld-venv` 和 `.runtime/alfworld`，但不负责安装 ALFWorld 环境；
- `scripts/homemaster` 把通用 `site-packages` 和 `mindmemos` 源码根塞进 ALFWorld 的 `PYTHONPATH`；
- `src/homemaster/benchmarking/alfworld/http_worker.py` 直接 import 主包中的 `env_adapter`、`trial_selection` 和 `types`；
- worker 还需要随机端口、token、ready fd、HTTP client/server、health 校验、截图传输和进程清理，因此环境失败时无法快速区分是 Python 依赖、IPC、Unity、DISPLAY 还是 Harness 逻辑。

审计没有发现 ALFWorld 另有一套 `AgentRuntime`、Provider loop 或 ToolExecutor。这条复用关系必须保留。

`src/homemaster/cli/composition.py` 实际是整个系统的 composition root，同时创建和连接 Provider、ToolRegistry、SkillRegistry、MCP、Memory、Neo4j、EventBus、Permission、Session、ResourceScope、Browser、Feishu 和 ALFWorld 服务。因此当前依赖方向不正确：

```text
ALFWorld / Web / LoCoMo -> cli.composition -> ApplicationRuntime
```

ALFWorld 的核心文件也混合了多个稳定职责：

```text
runner.py       episode、taskset、session、prompt、评分、记录
env_adapter.py  环境通信、grounding、navigation、manipulation、旧路径、反馈
execution.py    Oracle 执行、旧执行、状态读取、动作结果和分类
```

问题不是单纯文件过长，而是环境生命周期、目标解析、外部动作和评测记录的复杂性泄漏到 runner 和 tools。

### 4.1 环境方案比较

| 方案 | 优点 | 代价 | 结论 |
|---|---|---|---|
| 一个 Python 环境装全部依赖 | 命令最少、进程内调用简单 | AI2-THOR/Torch 与通用依赖冲突；升级任一侧都可能破坏另一侧；难以复现 | 不采用 |
| 两个 Python 环境 + 当前 loopback HTTP worker | 依赖有名义隔离；可复用现有 worker | `PYTHONPATH` 反向注入主库；HTTP、token、随机端口、health、截图和 Unity 生命周期叠加；当前只支持一个 episode/session | 只保留为迁移前现状 |
| 两个 Python 环境 + 本地 stdin/stdout NDJSON worker | 保留真正依赖隔离；没有端口/token/HTTP/健康端点；启动和排错路径短；文件路径可直接共享 | 需要定义稳定协议；worker 日志走 stderr；并发通过多 worker 管理 | **推荐** |
| 三个 Python 环境：通用、MindMemOS、ALFWorld | 可把依赖冲突进一步隔离 | MindMemOS 与 HomeMaster 的工具/runtime 仍需同进程调用；跨解释器导入和传输会重新出现；复现步骤更多 | 不采用 |

推荐方案的关键判断是：**两个环境是依赖边界，MindMemOS 不是运行边界。** MindMemOS 是通用 `.runtime/venv` 的必备组件，由同一个 ApplicationRuntime 直接使用；不能通过第三个 venv、跨解释器调用或运行时 `PYTHONPATH` 注入来接入。

### 4.2 目标环境契约

目标目录和依赖关系固定为：

```text
.runtime/
  venv/              # 通用 Python 环境
  alfworld-venv/     # ALFWorld worker Python 环境
  alfworld/          # ALFWorld 源码、configs、data/json_2.1.1
  memory/            # MindMemOS/Neo4j 相关数据
  neo4j/
  java/
```

通用环境的安装来源必须在根项目元数据和 `uv.lock` 中声明：

- HomeMaster 是根项目本身；
- MindMemOS 作为根项目的必备依赖或等价的本地 workspace dependency 安装到同一个 `.runtime/venv`；
- 安装完成后通过 `import mindmemos` 验证，不通过 `PYTHONPATH` 指向 `third_party/MindMemOS/src`；
- `scripts/setup.sh` 必须完成 MindMemOS 安装和 import/版本检查，不创建第二个 MindMemOS venv；
- doctor 必须始终检查 MindMemOS import、数据根、Neo4j/Java 绑定和真实读写黑盒；不能通过关闭 memory 绕过环境完整性检查。

ALFWorld 环境必须有独立的依赖锁，例如 `config/alfworld/requirements.lock` 或等价的 uv lock，至少固定：

```text
Python interpreter version
alfworld
ai2thor==2.1.0
torch
numpy
opencv
pillow
```

锁文件不能只写“当前服务器已有环境”；setup 必须能够从锁文件创建新环境，或者明确报告当前服务器只能复用一个经过 identity 校验的预装环境。任一版本变化都必须重新跑 runtime contract 和逐实例 THOR characterization。

worker 只接受一个稳定的本地协议：

```json
{"protocol":"homemaster-alfworld-v1","request_id":"...","operation":"reset|set_task|observe|act|close","payload":{}}
```

响应必须包含 `protocol`、`request_id`、`status`、`external_return_code`、`backend_attempted` 和 typed result。请求和响应均为 stdin/stdout NDJSON；worker 的第三方日志只写 stderr。截图、raw event 和大型轨迹不经过 IPC body，写入双方都可见的 repo-local artifact path，并回传路径、大小和 SHA-256。一个 worker serializes 一个 episode；需要并行时启动多个独立 worker，而不是让一个 worker 共享多个 session。

隔离黑盒必须证明：

```text
ALFWorld worker: import alfworld/ai2thor succeeds
ALFWorld worker: import homemaster/mindmemos fails
HomeMaster: import homemaster/mindmemos succeeds when memory is enabled
HomeMaster <-> worker: reset/act/close complete with matching request_id
external world: action return code and actual terminal state both verified
```

这组条件任何一项不满足，都只能报告环境未 ready，不能用“Python import 成功”替代可运行性。

### 4.3 目标迁移任务

这是环境重构的实施边界，不是继续加启动参数的补丁清单：

1. 为 MindMemOS 建立根项目必备依赖和 lock 记录，删除 ALFWorld launcher 对 `site-packages`/源码根的手工注入。
2. 为 ALFWorld 建立独立 lock 和 `setup-alfworld.sh`；setup 首次接受服务器绝对路径，之后只保留 `.runtime` binding。
3. 把 worker 代码移到独立的 worker package/source tree；worker 侧禁止 import `homemaster`、`mindmemos` 和主 benchmark package。
4. 用 stdin/stdout NDJSON 替换 `http_client.py`、`http_worker.py` 的 loopback HTTP、token、随机端口和 health endpoint；worker 的 ready response 变成协议握手。
5. 让 `scripts/homemaster` 只选择通用解释器；benchmark 通过显式 worker launcher 启动 ALFWorld，不再切换主 CLI 的 Python。
6. 更新 doctor、环境测试、ALFWorld live smoke 和复现文档；每个实例分别验证 reset、动作、close、返回码和真实终态。

## 5. Application Composition 与 ApplicationRuntime

### 5.1 Application Composition

实施状态：已完成。实现路径为 `src/homemaster/application/composition/{base,providers,tools,skills,memory,observability,profiles}.py`；公共导出位于 `src/homemaster/application/composition/__init__.py`。依赖方向由 `scripts/verify_v35_architecture.py` 的 `composition_import_direction` 和 `generic_runtime_domain_free` 检查，运行时生命周期证据见 `plan/V3.5/evidence/phase-1/README.md`。

Application Composition 回答：**这个应用实例由哪些实现组成？**

目标位置：

```text
src/homemaster/application/composition/
```

它负责：

- 读取和解析配置，选择 profile/environment；
- 创建 Provider factory；
- 创建 ToolRegistry、ToolExecutor 和权限配置；
- 创建 SkillRegistry；
- 创建 MCP manager 和动态工具注册；
- 创建 Memory、MindMemOS、Neo4j、queues、evidence ledger；
- 创建 EventBus、artifact、trace、session services；
- 创建 `RunResourceScope` 和 `ContextAssemblerFactory`；
- 创建并返回 `ApplicationRuntime`。

可以拆成以下内部模块：

```text
base.py          EventBus、Session、ResourceScope、Runtime
providers.py     chat / embedding provider factory
tools.py         common tools、profile tools、MCP tools
skills.py        SkillRegistry
memory.py        MindMemOS、Neo4j、memory queues
observability.py trace、artifact、audit sink
profiles.py      local_robot、browser、alfworld profile
```

这些模块只负责组装，不运行 Agent loop。

### 5.2 ApplicationRuntime

实施状态：已完成。`src/homemaster/application/runtime.py` 负责 run/session/resource lifecycle，入口适配器通过 `RunRequest` 注入依赖；`tests/homemaster/application/` 覆盖 start/close、取消、generation fencing 和 context projection。架构边界审计见 `plan/V3.5/evidence/phase-4/README.md`。

ApplicationRuntime 回答：**已经组装好的应用实例，如何执行一次 run？**

它负责：

- application-owned resource 的 start / close；
- SessionManager、session、resume、cancel 和 generation fencing；
- 为每个 run 创建 Provider、Context、ToolView 和 ToolExecutor；
- 调用唯一的 `AgentRuntime`；
- 处理 EventBus、trace、permission、verification；
- 执行 stop condition、terminal decision 和 cleanup。

它不负责：

- 自己扫描配置并决定 Provider 实现；
- 自己扫描 Skill 文件；
- 自己发现和连接具体 MCP server；
- 自己判断当前是 CLI、Browser 还是 ALFWorld；
- 自己实现 ALFWorld grounding、navigation 或 THOR action。

```text
Application Composition 决定“用什么”；ApplicationRuntime 决定“怎么运行”。
```

Runtime 可以持有并管理 Composition 创建的对象，但不能变成新的 God Object。

## 6. 入口适配器

入口适配器不是第二套 Runtime，而是把入口特有的协议或资源绑定到同一个 `ApplicationRuntime`。

### BrowserApplication

负责注入 browser session factory、选择 `profile="browser"`、按 capability 控制 `browser.eval`，然后委托底层 `ApplicationRuntime.run()`。它不执行 Agent loop，也不拥有 Provider。

### AlfworldGatewayApplication

负责把一个 `AlfworldWorkerClient` 绑定到固定 session，将 environment、trace、terminal owner 放入 `RunRequest`，防止多个 session 争用同一个 episode，然后委托底层 Runtime。worker 使用 stdin/stdout NDJSON；它是 Gateway transport/session binding，不是 benchmark runner。实现为 `src/homemaster/gateway/alfworld.py`，真实证据为 `plan/V3.5/evidence/phase-5/gateway-smoke-live/summary.json`。

### AlfworldApplicationEntry（已删除）

旧的同步 benchmark/runtime wrapper 已删除。当前 `src/homemaster/alfworld/benchmark/runner.py` 显式使用 composition 并由 async lifecycle 管理 episode/taskset；同步 CLI 只负责 event-loop owner。删除门由 `test_v35_audit_commands.py` 和架构审计共同覆盖。

## 7. ALFWorld Harness 目标边界

### 7.1 目标位置

实施状态：已完成。以下文件已存在并由 `scripts/verify_v35_architecture.py` 的 `required_v35_artifacts` 检查：`harness.py`、`backend.py`、`lifecycle.py`、`scene.py`、`actions.py`、`outcomes.py`、`recording.py`；benchmark 编排位于 `src/homemaster/alfworld/benchmark/`。

将 HomeMaster 自己维护的 THOR Harness 从 `benchmarking/alfworld` 的混合目录中收敛出来：

```text
src/homemaster/alfworld/
    harness.py       # 对工具层提供深接口
    backend.py       # THOR / ALFWorld 外部调用和 raw event
    lifecycle.py     # reset、scene snapshot、goal advance、close
    scene.py         # current observation、object index、grounding
    actions.py       # navigation、manipulation、终态校验
    outcomes.py      # terminal、typed feedback、classification
    recording.py     # trace、trajectory memory、artifact
    benchmark/
        runner.py    # 多 trial / taskset 调度
        episode.py   # 单 goal lifecycle
        taskset.py   # 连续 goal lifecycle
```

这是职责目标，不要求机械搬运文件；拆分必须围绕稳定接口和调用方向进行。

### 7.2 责任边界

实施状态：已完成。fake backend 对抗测试位于 `tests/homemaster/alfworld/test_harness_contract.py`；真实 THOR worker 的 per-trial reset/action/return-code/raw-state/close/cleanup 证据位于 `plan/V3.5/evidence/phase-5/gateway-smoke-live/`、`live-worker-use-20260926/` 和 `taskset-worker-easy_living_room_219/`。

`backend.py` 只访问 ALFWorld/AI2-THOR：发送请求、读取 raw event、读取返回码、关闭环境。不得包含 prompt、Provider 或评分策略。

`lifecycle.py` 负责 reset identity、controlled-time scan、immutable pose snapshot、goal advance、cleanup 和 quarantine。它决定环境是否 ready，但不解释模型动作。

`scene.py` 负责 scene identity、当前 observation、`SceneObjectIndex`、grounding、visibility、inventory 和 snapshot freshness。Grounding 的含义是：

```text
模型说 “mug” -> 场景中的唯一 Mug 1 -> 具体 objectId
```

`actions.py` 负责：

```text
navigation:
    object reference -> immutable pose -> TeleportFull -> pose/visibility verification

manipulation:
    object/target reference -> action precondition -> THOR action -> terminal-state verification
```

Navigation 不是传统路径规划，而是把 agent 移动到动作前置条件成立的确定 pose。Manipulation 才是 `PickupObject`、`PutObject`、`OpenObject`、`ToggleObject` 等真实动作。所有外部动作必须经过唯一 `OracleActionGateway`。

`outcomes.py` 负责 `AlfworldExecutionFeedback`、terminal owner、failure classification、score eligibility 和 stop condition 所需的权威状态。不得从普通字符串 observation 猜测成功。

`recording.py` 负责 trace、raw event artifact、trajectory memory 和 summary 输入。记录不能反过来决定环境动作成功。

`benchmark/episode.py` 与 `benchmark/taskset.py` 只负责编排 trial、Harness、ApplicationRuntime session、prompt、RunRequest、Runtime 调用和记录。它们不实现 grounding、navigation、manipulation，也不创建第二套 Provider 或 Agent loop。

### 7.3 工具层接口

实施状态：已完成。canonical tool mapping 位于 `src/homemaster/alfworld/tools.py`，Harness 深接口位于 `src/homemaster/alfworld/harness.py`；`tests/homemaster/v35/test_tool_interface_audit.py` 验证所有公开 registry entry 使用 async canonical executor。

工具层只把模型 schema 映射为 typed action request：

```text
robot_go_to(target)
robot_manipulate(action, object, target_receptacle, ...)
        ↓
AlfworldHarness.execute(request)
        ↓
ground -> navigate -> manipulate -> verify -> feedback
```

`benchmarking/alfworld/tools.py` 不应继续拥有完整的 grounding、环境分支和终态逻辑。

## 8. 明确删除范围

以下内容直接删除或改写，不迁移为兼容层：

- `AlfredTWEnv` 和所有 TextWorld 分支；
- translator 的 TextWorld / 双环境逻辑；
- `_ENABLE_V18_RESET_TRANSACTION` 和 `require_v18_reset`；
- `virtual_navigate()`、`find_object()` 等旧导航旁路；
- admissible-command 搜索和运行时候选 pose 枚举；
- `_execute_thor_manipulation()`；
- `_legacy_execution_feedback()`；
- `ManipulationExecutor`、`LegacyManipulationExecutor`、`ManipulationRouter`；
- 以 `cli.composition` 为公共依赖的非 CLI 入口；
- `AlfworldApplicationEntry` 这种隐式 composition/lifecycle wrapper。

### 8.1 Tool 协议硬切

实施状态：已完成。旧 `ToolSpec`、旧结果类型、legacy adapter、旧导航别名和 HTTP worker 文件均已从源码路径删除；静态删除门和全量测试已通过。发布包边界由 `scripts/verify_v35_release.py` 验证。

Tool 迁移采用破坏性硬切。这里的“硬切”是架构和 API 决策，不是新增一个运行时 mode：迁移完成后，主线不再接受旧 Tool API，也不再通过 adapter、alias 或 fallback 继续运行旧工具。

唯一允许的工具链是：

```text
ToolDefinition
  -> RegisteredTool
  -> async executor
  -> ToolExecutionResult
  -> model-facing ToolRegistry projection
```

直接删除以下旧协议和兼容实现：

- `src/homemaster/tools/spec.py` 中的 `ToolSpec`；
- `src/homemaster/tools/results.py` 中的旧 `ToolResult`；
- `src/homemaster/tools/legacy_adapter.py` 全部内容；
- `src/homemaster/tools/adapters.py` 中的 `from_tool_spec()`；
- `from_registered_tool()` 中通过 `_executor` 检测并运行同步旧 executor 的分支；
- `src/homemaster/adapters/profiles.py` 中的 `build_universal_tool_registry()`；
- `robot_navigate`、`robot_find_object` 等旧工具名和旧 trace/test fixture。

以下模块必须直接生产 canonical `RegisteredTool`，不得先生产 `ToolSpec` 再适配：

- `src/homemaster/domain/tools.py`；
- `src/homemaster/task_state/tools.py`；
- `src/homemaster/benchmarking/alfworld/tools.py`。

迁移时保留真正的领域行为，但改变其协议和归属：

- Home domain 的 task、memory、skill、robot 行为改为 async canonical executor；
- ALFWorld 的 grounding、Oracle navigation、Oracle manipulation、terminal verification 和 trace 继续保留在 Harness/domain 实现中；
- ALFWorld tool 只负责 schema 到 typed request 的映射，并委托 `AlfworldHarness`；
- `AlfredTWEnv`、TextWorld translator 和所有旧执行旁路一并删除；
- `robot_go_to` 是唯一模型可见的 Home/ALFWorld 导航名，不再保留 `robot_navigate` alias。

`from_registered_tool()` 如仍作为 rich registration 到模型可见普通名 registry 的投影 seam，可以保留；但它只能调用 canonical async executor，不能再读取 executor 私有字段或识别旧协议。

`contracts.py` 中的 canonical context/result 是唯一真相源。`base.py` 如果继续存在，只能作为模型可见 registry 的投影实现，不得再定义语义重复的第二套 `ToolExecutionContext` 或 `ToolResult`；迁移完成后，权限、验证、资源租约和 application tool dispatch 必须使用同一套 canonical context/result。

### 8.2 Git 分支的含义

本次硬切可以在独立 Git 分支完成，例如 `refactor/remove-tool-legacy`。分支只用于隔离半成品：迁移期间允许该分支暂时无法通过全量测试，`main` 不受影响；迁移完成并通过验收后合并回 `main`。分支合并后不得把旧兼容路径带回主线，也不得为了合并而增加新旧双模式。

唯一 THOR 动作链：

```text
robot_go_to      -> OracleNavigationExecutor
robot_manipulate -> OracleManipulationExecutor
```

## 9. 重构后的依赖方向

```text
CLI / Web / Feishu / ALFWorld benchmark
                    ↓
          application.composition
                    ↓
          ApplicationRuntime
                    ↓
             AgentRuntime
                    ↓
       Provider / Context / Tools / Session
                    ↓
        homemaster.alfworld Harness
                    ↓
             ALFWorld / AI2-THOR
```

旁路记录关系：

```text
ApplicationRuntime -> EventBus / Session / Memory / Trace
ALFWorld Harness  -> raw event / terminal evidence / trajectory recording
Benchmark Runner  -> summary / score / trial orchestration
```

禁止反向依赖：

- `ApplicationRuntime` 不导入 `homemaster.alfworld`；
- `AgentRuntime` 不知道 THOR、objectId 或 benchmark scorer；
- `homemaster.alfworld` 不创建 Provider、AgentRuntime 或 Memory backend；
- benchmark runner 不直接构造 LLMClient、ToolRegistry 或 AgentRuntime；
- CLI 不再是公共 application composition owner。

## 10. 重新审计清单

### Application 层

- [x] 非 CLI 模块不再 import `homemaster.cli.composition`；
- [x] 公共 application 创建只来自 `homemaster.application.composition`；
- [x] Provider、Skill、MCP、Memory 的创建位于 composition，生命周期由 Runtime 管理；
- [x] `ApplicationRuntime` 不包含入口判断和领域动作实现；
- [x] 入口适配器只做 transport/capability/session binding。

### ALFWorld 层

- [x] 源码、配置、文档和测试中不再支持 `AlfredTWEnv`；
- [x] 源码中不再存在 legacy navigation/manipulation/feedback 主路径；
- [x] 不再存在 `require_v18_reset` 或类似双路径开关；
- [x] `robot_go_to` 和 `robot_manipulate` 只有 Oracle Harness 实现；
- [x] grounding、navigation、manipulation、verification 的责任分别可定位；
- [x] episode 与 taskset 共享同一生命周期执行模块，而不是复制完整编排；
- [x] benchmark runner 不构造第二套 Agent/Provider/ToolExecutor；
- [x] 每个外部动作都有返回码和真实终态黑盒验证。

### 文档和测试

- [x] `architecture/` 简图与代码依赖方向一致；
- [x] 架构文档不再把 legacy 或 TextWorld 描述为受支持能力；
- [x] 删除旧路径后同步删除旧测试和旧 fixture；
- [x] 源码中不再导入 `ToolSpec`、旧 `ToolResult`、`legacy_adapter` 或 `from_tool_spec`；
- [x] 每个正式 registry entry 都直接来自 canonical `RegisteredTool`，不存在同步 executor fallback；
- [x] `ToolExecutionContext`、`ToolExecutionResult` 只有一套有效语义，权限、验证和 dispatch 不再跨两套 context/result 传递；
- [x] verifier 在所有需要验证的工具上真实执行，不能因为旧 `ToolResultMessage` 或 `_executor` 分支被绕过；
- [x] `robot_navigate`、`robot_find_object` 和 `build_universal_tool_registry()` 的调用方已迁移或删除；
- [x] 重新跑 import boundary、interface audit、ALFWorld Harness、Application Runtime 和真实 THOR 黑盒门；
- [x] 失败必须按 instance/episode/taskset 分别记录，不能用聚合成功掩盖单项失败。
