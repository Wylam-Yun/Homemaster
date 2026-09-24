# HomeMaster 分层架构简图

**用途：** 作为 V3.5 架构审计的快速入口。本文描述目标依赖方向，同时标出当前代码中的主要实现位置和正在迁移的边界。

## 1. 总体关系

架构审计先固定一个环境事实：项目不是“一个大 Python 环境”，也不是“三个互相传源码的环境”，而是两个有明确边界的 Python 运行时：

```text
通用 Python 环境                         ALFWorld Python 环境
(.runtime/venv)                          (.runtime/alfworld-venv)
  HomeMaster                              ALFWorld + AI2-THOR
  Provider / ApplicationRuntime           Torch / OpenCV / Pillow / NumPy
  Tools / Skills / MCP                    独立 worker
  MindMemOS 必备组件                       不 import homemaster
  Memory / Neo4j client                   不 import MindMemOS
            │                             │
            └────── versioned local NDJSON IPC ──────┘
```

MindMemOS 必须随通用环境安装，但不形成第三个 Python 环境。Neo4j、Java、Unity/Xvfb 是外部运行时，不改变 Python 环境数量。

```text
┌──────────────────────────────────────────────────────────────┐
│  入口层                                                     │
│  CLI / Web / Feishu / ALFWorld Benchmark                    │
│  负责：解析入口参数、认证、构造用户请求、选择运行 profile       │
│  不负责：创建 Provider、实现 Agent loop、实现 THOR 动作       │
└──────────────────────────────┬───────────────────────────────┘
                               │ RunRequest / profile / binding
                               v
┌──────────────────────────────────────────────────────────────┐
│  Application Composition                                     │
│  homemaster.application.composition                          │
│  负责：读取配置并组装 Provider、Tools、Skills、MCP、Memory、  │
│        EventBus、Session、Permission、ResourceScope          │
│  输出：一个配置完成但尚未执行具体 run 的 ApplicationRuntime   │
│  不负责：Agent loop、具体环境动作、benchmark scoring          │
└──────────────────────────────┬───────────────────────────────┘
                               │ constructed dependencies
                               v
┌──────────────────────────────────────────────────────────────┐
│  Application Runtime                                          │
│  ApplicationRuntime                                           │
│  负责：application resource lifecycle、Session、resume、     │
│        cancel、generation fencing、run-scoped ToolView、      │
│        Provider/Context 创建、EventBus、permission、stop     │
│  不负责：决定使用哪个具体 Provider、扫描 Skill、实现 MCP、   │
│        解析 ALFWorld object、执行 THOR action                 │
└──────────────────────────────┬───────────────────────────────┘
                               │ one run
                               v
┌──────────────────────────────────────────────────────────────┐
│  Agent Runtime                                                │
│  AgentRuntime / GenericAgentRuntime                           │
│  负责：模型请求循环、assistant/tool message、provider retry、 │
│        Context、Tool call dispatch、Agent-level stop          │
│  不负责：领域成功判断、THOR objectId、benchmark score         │
└──────────────────────────────┬───────────────────────────────┘
                               │ ordinary tools + typed dependencies
                               v
┌──────────────────────────────────────────────────────────────┐
│  通用能力层                                                   │
│  Provider / Context / ToolRegistry / ToolExecutor / Memory /  │
│  MCP / Skills / EventBus / Session / Permission               │
│  负责：通用能力和生命周期实现                                 │
│  不负责：入口编排和具体 ALFWorld 领域语义                     │
└──────────────────────────────┬───────────────────────────────┘
                               │ domain tool call
                               v
┌──────────────────────────────────────────────────────────────┐
│  ALFWorld Harness                                             │
│  homemaster.alfworld                                          │
│  负责：THOR reset、scene identity、grounding、Oracle          │
│        navigation、manipulation、terminal verification、      │
│        typed feedback、failure classification、recording       │
│  不负责：Provider、Agent loop、Memory backend、通用 Session     │
└──────────────────────────────┬───────────────────────────────┘
                               │ versioned local IPC
                               v
┌──────────────────────────────────────────────────────────────┐
│  ALFWorld Worker                                               │
│  独立 .runtime/alfworld-venv                                  │
│  只加载 ALFWorld / AI2-THOR / Unity 依赖；不 import HomeMaster │
│  stdin/stdout NDJSON；stderr 保存 worker 日志                  │
└──────────────────────────────┬───────────────────────────────┘
                               │ external calls / evidence
                               v
┌──────────────────────────────────────────────────────────────┐
│  外部环境与权威终态                                           │
│  ALFWorld / AI2-THOR / Unity / Xvfb                          │
│  负责：真实场景、物体、相机、动作返回码和最终环境状态          │
└──────────────────────────────────────────────────────────────┘
```

## 2. ALFWorld Harness 内部动作链

```text
模型工具请求
    │
    ├─ robot_go_to(target)
    │      │
    │      ├─ Scene grounding："mug" -> Mug 1 -> objectId
    │      ├─ Oracle pose lookup：objectId -> immutable pose
    │      ├─ OracleActionGateway -> TeleportFull
    │      └─ verify：返回码 + actual pose + visibility + world state
    │
    └─ robot_manipulate(action, object, target)
           │
           ├─ Scene grounding：自然语言目标 -> 唯一 object reference
           ├─ current event / precondition verification
           ├─ OracleActionGateway -> Pickup / Put / Open / Toggle / ...
           └─ verify：动作专用外部终态

最终统一输出：AlfworldExecutionFeedback
```

这里的三个词含义：

- **Grounding：** 把模型说的自然语言目标绑定到场景中唯一、可验证的对象。
- **Navigation：** 把 agent 移动到动作前置条件成立的确定 pose；不是模型 loop，也不是传统探索路径。
- **Manipulation：** 执行真实 THOR 动作并验证外部世界是否发生预期变化。

## 3. Benchmark 编排位置

Benchmark 只负责实验编排，不负责实现环境动作：

```text
benchmark runner
    ├─ trial selection
    ├─ 创建 ALFWorld Harness
    ├─ 创建 ApplicationRuntime session
    ├─ 构建 prompt / RunRequest
    ├─ 调用 ApplicationRuntime.run()
    ├─ 接收 typed outcome
    └─ 写 summary / score / trajectory artifact
```

普通 episode 和 taskset 的差别只应是生命周期参数：

```text
episode: 一个 scene + 一个 goal + 一个 session
taskset: 一个 scene + 多个 goal + 一个连续 session
```

它们应该共享同一个 episode lifecycle，而不是复制完整的 runtime/session/recording 编排。

## 4. 入口适配器

入口适配器只绑定入口特有协议或资源，不创建第二套 Runtime：

| 适配器 | 绑定内容 | 最终调用 |
|---|---|---|
| `BrowserApplication` | browser session factory、browser profile、capability | `ApplicationRuntime.run()` |
| `AlfworldGatewayApplication` | ALFWorld worker、固定 episode、session ownership | `ApplicationRuntime.run()` |
| Benchmark event-loop owner | 同步 benchmark 与异步 runtime 的生命周期 | `ApplicationRuntime.run()` |

这些适配器不能拥有独立的 Provider loop、ToolExecutor 或 AgentRuntime。

## 5. 目标依赖规则

```text
入口层
  -> application.composition
  -> ApplicationRuntime
  -> AgentRuntime
  -> 通用 Tools / Provider / Context
  -> homemaster.alfworld Harness
  -> versioned local NDJSON IPC
  -> ALFWorld Worker (.runtime/alfworld-venv)
  -> ALFWorld / AI2-THOR / Unity
```

禁止以下反向依赖：

- `ApplicationRuntime` 不导入 `homemaster.alfworld`；
- `AgentRuntime` 不知道 THOR、objectId 或 benchmark scorer；
- `homemaster.alfworld` 不创建 Provider、AgentRuntime 或 Memory backend；
- ALFWorld worker 不 import `homemaster`、主环境 `site-packages` 或 MindMemOS；
- HomeMaster 与 worker 不通过随机端口、token、loopback HTTP 或共享主库源码通信；
- benchmark runner 不直接构造 `LLMClient`、`ToolRegistry` 或 `AgentRuntime`；
- Web、Feishu、ALFWorld 不依赖 `homemaster.cli.composition`；
- 不支持 `AlfredTWEnv`、TextWorld 或 legacy THOR action path。

## 6. 当前代码迁移对照

| 目标层 | 当前主要位置 | 迁移方向 |
|---|---|---|
| 入口层 | `cli/`, `web/`, `gateway/`, `benchmarking/alfworld/runner.py` | 保留入口职责，删除公共组装逻辑 |
| Application Composition | `cli/composition.py` | 移到 `application/composition/`，不保留旧兼容入口 |
| Application Runtime | `application/runtime.py`, `application/factory.py` | 保留，收紧依赖接口 |
| Agent Runtime | `agent/generic_runtime.py` | 保留唯一 Agent loop |
| 通用能力 | `providers/`, `tools/`, `memory/`, `mcp/`, `skills/`, `events/` | 由 composition 组装，由 Runtime 使用和管理生命周期 |
| ALFWorld Harness | `benchmarking/alfworld/env_adapter.py`, `execution.py`, `tools.py` | 收敛为单一 Oracle Harness，删除 TextWorld 和 legacy |
| Benchmark 编排 | `benchmarking/alfworld/runner.py` | 拆出 episode/taskset lifecycle，共享编排核心 |
| 外部环境 | ALFWorld / AI2-THOR / Unity / Xvfb | 只通过 Harness 访问 |

## 7. 复现与安装边界

目标复现路径固定为：

```bash
git clone <repo>
cd Homemaster
cp config/homemaster.example.yaml config/homemaster.yaml
# 手动填写 provider、memory 和服务器相关配置
./scripts/setup.sh
./scripts/setup-alfworld.sh --root /path/to/alfworld
scripts/homemaster doctor
scripts/homemaster benchmark-alfworld ...
```

`setup-alfworld.sh` 是目标命令，当前仓库尚未实现；当前仍使用
`scripts/setup_memory_runtime.py` 和 loopback HTTP launcher。它们列在图中是为了锁定迁移后的用户体验，不能被审计误认为已经交付。

其中：

- `setup.sh` 只创建通用 `.runtime/venv`，并安装根项目锁定依赖；
- `setup.sh` 必须安装/校验 MindMemOS，不创建新解释器；根项目应通过必备依赖/lock 安装，不能再靠 `PYTHONPATH` 注入源码；
- `setup-alfworld.sh` 创建或校验独立 ALFWorld 环境，并把 Python、源码/数据根绑定到 `.runtime/`；
- `.runtime/` 和真实配置均是机器本地状态，不进入 Git；
- 日常命令只读 `.runtime/`，不要求用户再次输入 `/home/...`、`/data1/...` 等服务器绝对路径。

worker 协议使用 stdin/stdout NDJSON；大型截图和 raw event 走双方共享的 `.runtime` artifact path + SHA-256，不通过 HTTP 或 JSON body 传图片。

验收必须按环境分别通过：

1. 通用环境：`import homemaster`、`import mindmemos`、配置解析、Neo4j/Java 绑定检查和真实 Memory 读写检查。
2. ALFWorld 环境：worker Python 可 import `alfworld`、`ai2thor`，配置和 `data/json_2.1.1` 存在，Unity/Display 可启动。
3. 隔离黑盒：在 ALFWorld worker 中检查 `homemaster`、`mindmemos` 不可导入；HomeMaster 侧能通过 IPC 完成一次 reset、一次动作请求和一次 close，并核对 worker 返回码与真实环境终态。

当前 `scripts/homemaster` 的 `PYTHONPATH` 注入和 loopback HTTP 是迁移前实现，不能作为目标架构或复现步骤继续保留。
