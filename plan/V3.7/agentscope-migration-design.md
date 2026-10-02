# HomeMaster × AgentScope 迁移设计（V3.7）

**状态：设计已锁定待实施**
**日期：2026-10-01**
**范围：Agent 底座替换（messages/loop/model/toolkit/context/event）、CLI/Web 入口升级、双轨记忆 middleware 化；领域安全壳与 ALFWorld 边界保留**

## 1. 已锁定的架构决策

1. **方案：换底保留壳。** AgentScope 替换通用 agent 地基（消息模型、模型循环、Provider 封装、工具注册、上下文压缩、事件流）；HomeMaster 的领域安全资产（物理许可状态机、设备租约防陈旧、`outcome_unknown`、session generation fencing、公共事件 allowlist 投影、ALFWorld 双环境隔离）以 middleware + 自定义 `ToolBase` 形式保留，不删不改语义。
2. **AgentScope 全量源码复制进 `src/agentscope/`，作为 HomeMaster 自己的 vendored 代码。** 完整复制整个 `src/agentscope` 包（含 `app`/`workspace`/`tui`/`realtime`/`sop`/`rag`/`embedding`/`classifier`/`tts` 等暂不启用的子包——不 import 即零运行时开销，保留以后拓展空间）+ 上游 `LICENSE` + `VENDORED_SOURCE.md`（基线 `a1f30d48b6de5bfb25a4bf2c5fbf1b6442f5106d`，版本 2.0.10dev）。**不需要** `tool.uv.sources`/`dependencies` 声明——它在 HomeMaster 源码树内，`packages.find` 与 `pythonpath=[".","src"]` 天然覆盖，`import agentscope` 直接可用且随 wheel 分发。**禁止**同级目录引用与绝对路径 import；干净 clone + `uv sync` 必须自洽。依赖策略：**并入上游 core `dependencies`（实为 27 项）+ 三个上游未声明但被模块级 import 的硬依赖 `requests`/`pyyaml`/`typing-extensions`**（上游靠 dashscope/frontmatter/pydantic 传递兜底，vendor 后传递链不可信，必须显式声明）；上游 extras 一律不装。`mcp` 上游仅 `<2.0.0` 上界，与本仓 `>=1.0,<2` optional extra 兼容，vendor 后须提升为 core。"零开销"的精确含义（已核实 import 闭包）：`import agentscope` 只载 logging；`agentscope.agent` 的模块级闭包已含 `workspace`/`realtime`/全部 provider 壳，`middleware` 闭包含 `tts`/`embedding`/`tracing`——这些闭包内三方包全部落在 core+上述三项内，不引 extras；`app`/`rag`/`tui`/`classifier` 在 core-only 下**不可 import**（模块级 `fastapi`/`sqlalchemy`/`qdrant-client`/`textual`），留着=想用先 `uv add`。`json5`/`filetype`/`python-datauri`/`python-socketio` 为上游声明但 src 内零 import 的冗余项。vendored 树保持 verbatim（ruff/test 隔离 exclude），改动优先走 substrate 适配与 middleware 扩展点；确需改 vendored 源码时在 `VENDORED_SOURCE.md` 追加本地 patch 清单。Phase 3 可评估把最终用到的模块收编改名进 `homemaster/` 命名空间——那是可选清扫，不影响本计划。与 `third_party/MindMemOS` 的差异：MindMemOS 是独立 dist vendor（不改其代码），agentscope 是 in-tree vendored source（归属 HomeMaster 代码）。
3. **双轨记忆并存，不合并。** 文件记忆（`SOUL.md`/`USER.md`/`MEMORY.md` + ThreatScanner）与 MindMemOS（Neo4j+Qdrant episode/graph/向量）各封装为一个 `MiddlewareBase`，分工见 §5.3，注入边界互不重叠。
4. **CLI 换引擎不退役壳。** `launch_console` 官方定位是"无 session 管理/无持久化"的调试入口且不接 `RequireExternalExecutionEvent`（外部工具 park 后 console 无恢复路径）——只做 Phase 0 spike 演示。正式形态 = 保留 `interactive_shell` 的 REPL/斜杠命令/逐项确认卡/SIGINT 与清理所有权，内层单次 run 换成 `agent.reply_stream()` 事件消费。`/compact`→`compress_context()`；`/new` 用**原地清理**（`context.clear()`+`summary=""`+字段级清 `permission_context`/`tool_context`/`task`/`middle_context`+新 `ReplyContext`）或重建 Agent——**`AgentState` 无 `reset()` 方法，且整换 `agent.state` 会让构造期绑定的 `_engine` 持有旧 `permission_context`，造成许可规则跨会话泄漏**。可选上线 `launch_tui`（`uv add textual`）。
5. **Web 保留外壳换引擎。** `web/app.py` + 前端 `web/static_dist` 不动；运行引擎从 `ApplicationRuntime.run()` 换成 `agent.reply_stream()`，公共投影层（`events/stream_events.py` allowlist）保留，仅事件源改为 typed `AgentEvent`。**不采用** `agentscope.app.create_app`——其 session/auth/channel 语义与 HomeMaster 的 tenant ACL + generation fencing 冲突。
6. **Feishu/Gateway 保留。** `agentscope.app.channel._feishu` 存在但 HomeMaster 通道的认证/exact principal/mention/dedup/子进程隔离纪律更严，不替换。
7. **隔离面：`src/homemaster/substrate/` 是唯一允许 `import agentscope` 的包。** 其余代码只见 HomeMaster canonical 契约；agentscope 是 dev 版，API 漂移由 substrate 单点吸收 + contract test 守护。
8. **`alfworld/benchmark/adapter.py`（5252 行）不在本计划内重写。** 它的治理（拆 harness 包）独立排期，避免两个大爆炸半径叠加；迁移期仅通过领域工具接口被调用。

## 2. AgentScope 2.x API 勘误（一切以下方核实为准）

本仓 vendor 的是 **2.0.10dev**，与 0.x/1.x 公开文档差异巨大。已核实（commit `a1f30d4`）：

| 旧名（不存在） | 2.x 实际 | 位置 |
|---|---|---|
| `AgentBase`/`ReActAgent` | `Agent`（具体类，内建 reasoning-acting 循环） | `agentscope/agent/_agent.py:117` |
| `ServiceToolkit` | `Toolkit`（`add_tool`/`call_tool`/tool groups/MCP/skills） | `agentscope/tool/_toolkit.py:66` |
| `MsgHub`/broadcast | 无。多 agent = `observe()` + `*MultiAgentFormatter` / `TeamPipeline` / `app/message_bus` | `agent/_agent.py:381` |
| `MemoryBase`/`InMemoryMemory` | 短期 = `agent.state.context: list[Msg]`；长期 = middleware（`middleware/_longterm_memory/`） | `state/_state.py:209` |
| `state_dict` | `AgentState` 是 Pydantic，`model_dump(mode="json")`/`model_validate` | `state/_state.py` |
| `handle_interrupt` | `UserInterruptEvent` 输入或 task cancel；`ReActConfig.interruption_raise_cancelled_error` | `agent/_agent.py:1035-` |
| `print`/`x` 输出 | `reply_stream()` → typed `AgentEvent` 流（25+ 种） | `event/_event.py` |

已核实的关键构造签名：

```python
Agent(name, system_prompt, model: ChatModelBase,
      toolkit: Toolkit | None, middlewares: list[MiddlewareBase] | None,
      state: AgentState | None, offloader: Offloader | None,
      model_config: ModelConfig, context_config: ContextConfig,
      react_config: ReActConfig, injection_config: InjectionConfig)

Toolkit(tools=[...], skills_or_loaders=[...], mcps=[MCPClient...], tool_groups=[...])

MiddlewareBase 七个 hook（洋葱链）：
  on_reply / on_reasoning / on_check_permission / on_acting /
  on_model_call / on_compress_context / on_system_prompt(→str)

PermissionDecision(behavior: ALLOW|DENY|ASK|PASSTHROUGH,
                   message, suggested_rules, bypass_immune)
FunctionTool(func, name, description, input_schema, is_concurrency_safe,
             is_read_only, is_state_injected, middlewares, permission)
ToolCallBlock(id, name, input:str(JSON), state: pending|asking|allowed|submitted|finished)
ToolResultBlock(id, name, output:str|list[TextBlock|DataBlock],
                state: success|error|interrupted|denied|running, metadata)
GoalPipeline(executor, verifier, verifier_reset_context=True, max_iters, max_retries)
  # 注意：max_retries 存而未用——verifier 非 COMPLETED 收尾会无限重跑，Phase 3 需外置迭代上限
TeamPipeline(leader, members, reset_members=True)   # 无 max_retries 参数
launch_console(agent|pipeline, user_name, verbosity, max_tool_result_lines)
```

已核实的语义陷阱（评审取证，实施前必看）：

- `on_acting` 只包裹 `toolkit.call_tool` 纯执行；permission 检查/context 写入在 hook 外由 `_execute_tool_call` 处理。
- middleware 收到的 `tool_call`/`tool_input` 是 **deepcopy**——改 `input_kwargs` 不影响真实调用；改写输入的唯一可用点是 `on_acting` 的 `next_handler(tool_call=改写后块)` 或确认时 `ConfirmResult.tool_call`。
- `PermissionDecision.updated_input` 是死字段（`_agent.py` 无消费点）；`ReActConfig.stop_on_reject` 同为死字段。
- `PASSTHROUGH` 在 `_execute_tool_call` 中与 `ASK` 同等处理为挂起询问——middleware 弃权必须 `await next_handler()`，不能返回 PASSTHROUGH。
- `AgentState` **无 `reset()` 方法**；`Agent.__init__` 构造期把 `PermissionEngine` 绑死到 `state.permission_context`——整换 state 会致许可规则跨会话泄漏。
- `reply()`（非 stream）在挂起态 **raise RuntimeError**——Web/CLI 引擎必须用 `reply_stream`。
- `FunctionTool` 硬编码 `is_external_tool=False`；外部设备动作若走 SUBMITTED/park 路线须 `ToolBase` 子类 + 手工 `ExternalExecutionResultEvent` 回灌（默认路线不走 park，见 §6 裁决）。
- `get_awaiting_tool_calls` 只看 context 尾部 assistant 消息——park 期间任何改写尾部 context 的操作会使挂起调用"脱锚"。
- `ExceedMaxItersEvent` 已 deprecated——判停用 `ReplyEndEvent.finished_reason`；`ToolCallEndEvent` 不含 input。

注意：`agentscope/__init__.py` 只导出 logging 工具——所有 API 从子包导入（`agentscope.agent`、`agentscope.tool`、`agentscope.message`、`agentscope.middleware`、`agentscope.permission`、`agentscope.pipeline`、`agentscope.model`、`agentscope.credential`、`agentscope.console`）。

## 3. 现状诊断摘要（证据见各 file:line，基线 = v35-architecture HEAD）

HomeMaster 已完成 V3.5 分层（入口→composition→ApplicationRuntime→AgentRuntime→能力层→领域 Harness→worker），**不是单体**；问题是 ~74K 行自研地基 + 无多 agent 抽象：

1. **`AgentRuntime` 是具体 loop 不是 Agent 抽象**（`agent/generic_runtime.py:99`）：`run()` 14 参（`:121-138`），1512 行循环体内联 SIGINT、事件发射、context 装配/压缩四态、provider 重试+冻结请求 SHA、observation 屏障、工具协议围栏、持久化、取消六相。
2. **God 组合根** `compose_application`（`application/composition/base.py:176-691`）：单函数装 ~15 子系统。
3. **类型逃逸口**：`RunContext.deps`/`ToolExecutionContext.services` 为 `dict[str,Any]`（`agent/normalized.py`、`task_state/tools.py:22-26`）。
4. **四套防陈旧语义并存**（session generation / lease generation / permission revision / snapshot revision）+ **两套 session 持久化**（`agent/session_persistence.py` 与 `application/session.py:211-378`）。
5. **多 agent 零支持**：消息是三型 union 非统一 `Msg`；无 `observe`/广播/角色分工；事件类型为散布循环体的字符串字面量。
6. **Provider 三点联动**：新增 provider 动 transports+llm_client+config，仅支持 anthropic/openai（`providers/llm_client.py:395-403`）；错误分类靠 `type(exc).__name__.lower()` 子串嗅探（`:431-451`）。
7. **许可状态机双写**：`tools/executor.py`（1163 行）与 `permissions/store.py`（~1100 行 SQLite）两份状态表达；`_roll_up_request` 集合运算优先级隐含。
8. **God 文件**：`alfworld/benchmark/adapter.py` 5252（getattr×74/Any×66 全仓第一）、`browser/playwright_session.py` 3068、`memory/mindmemos_runtime.py` ~1973、`channels/impl/feishu.py` 1508。

## 4. 目标分层与目录结构

```text
入口层        cli/(typer 命令 + console/tui 入口)  web/app.py  gateway/  channels/
                │  只构造请求/消费 reply_stream 事件，不含 agent 逻辑
substrate/      ← 唯一 import agentscope 的包（API 漂移隔离带）
                ├─ messages.py   Message↔Msg 双向转换（session 持久化用）
                ├─ models.py     ProviderProfileConfig→ChatModelBase 工厂
                └─ toolkit.py    ToolRegistry→Toolkit 过渡适配器（Phase2 删）
agents/         ← Agent 装配与 middleware（新层）
                ├─ household_agent.py   Agent 构造（model/toolkit/middlewares/configs）
                ├─ middleware/
                │   ├─ physical_safety.py   on_check_permission+on_acting
                │   │                       包裹 PermissionStore/Policy/DeviceLease
                │   ├─ observe_barrier.py   act→observe 屏障（原 model_observation）
                │   ├─ reactive_compact.py  context-length 错误→压缩→重试
                │   ├─ file_memory.py       SOUL/USER/MEMORY 常驻注入+写回工具
                │   └─ mindmemos.py         MindMemOS 召回注入+session-end 写回
                └─ tools/base.py          HomeTool(ToolBase)：stable_id/
                                          capabilities/verification_policy/provenance
application/    ← 瘦身：SessionOrchestrator（fencing/cancel/turn）+
                │  SessionFileBackend CAS（唯一持久化真源）
domain/ alfworld/ browser/ devices/ permissions/ memory/  ← 领域层原样
events/         ← 只剩公共投影（typed AgentEvent→StreamEvent allowlist）
```

依赖红线：`substrate/` 外向任何方向暴露的只有 HomeMaster 类型；`agents/` 不 import `alfworld`；`domain`/`permissions`/`devices` 不知道 agentscope 存在。

## 5. 技术映射表

| HomeMaster 现状 | AgentScope 2.x 原语 | 类别 |
|---|---|---|
| `UserMessage`/`AssistantMessage`/`ToolResultMessage`+`ContentBlock` | `Msg`+`TextBlock`/`ThinkingBlock`/`DataBlock`/`ToolCallBlock`/`ToolResultBlock` | **保留** HM messages.py 为 canonical schema（持久化/compact/投影/ALFWorld receipt 共基座），substrate 双向转换；`data`→`ToolResultBlock.metadata["homemaster"]["data"]` |
| `AgentRuntime.run()` loop | `Agent`+`ReActConfig(max_iters)` | 直接；`stop_condition`→middleware/verifier |
| `AgentSession.messages`/`AgentState` | `AgentState.context`/`summary`/`middle_context`，`model_dump` 持久化 | **保留** SessionSnapshot 为唯一权威快照（revision/generation/evidence_refs/recall/task_state），`AgentState.model_dump` 仅其中 `agent_state` 子树；HM 循环内态（屏障/无进展计数/unconsumed_observation_tool_call_id）的持久化载体在 Phase 0 裁决 |
| `ContextAssembler`（952 行压缩/装配） | `ContextConfig(trigger_ratio/reserve_ratio)`+`compress_context()`+`on_compress_context`/`on_system_prompt` middleware | 直接；reactive 压缩保留为 `on_model_call` 重试 middleware |
| `ToolRegistry`/`BaseTool`/`ToolExecutor` | `Toolkit`+`ToolBase`/`FunctionTool` | 直接；`HomeTool` 承载 stable_id/capabilities/verification/provenance + HM 侧 registry 镜像（`ToolBase` 元字段集小于 `ToolDefinition`，metadata 不得散进 middleware 闭包） |
| `PermissionChecker`/`PermissionStore`/审批/HITL | `PermissionEngine`+`PermissionDecision`+middleware 内阻塞走自有 protocol-v2 逐项决策卡（**不采用** RequireUserConfirmEvent park/resume——粒度不符且 console 无外部执行恢复路径） | 直接+保留；SQLite 真源、grant 复审、TOCTOU、TargetChanged 留在 middleware |
| 物理执行链+`outcome_unknown` | `on_check_permission`（建申请/评估/确认）→`on_acting`（批准后复核+claim_step+lease+逐 step `adapter.execute` 入口）+`ToolResultBlock.metadata` 机器字段 | **保留**（领域安全壳核心）；`is_external_tool`/`RequireExternalExecutionEvent` 为 UNVERIFIED 备选，默认不走 |
| model_observation 屏障 | `on_acting` 后段改 `ToolResponse.content` 追加 TextBlock/DataBlock（**禁止** `agent.observe()`——会把观察插在 tool_result 之前倒置时序）；屏障状态随 SessionSnapshot 持久化 | 直接（从循环内联外迁） |
| `LLMClient`+transports+`ProviderProfileConfig` | `ChatModelBase`×11 家+`formatter/*`+`credential/*`+`ModelConfig(max_retries,fallback_model)` | 直接；多 key 轮询经薄封装保留 |
| `RunContext.deps`/`services` dict | `is_state_injected=True` 工具收 `AgentState`；服务构造注入 | 直接（消逃逸口） |
| `EventBus`/`RuntimeEvent`/字符串事件 | `reply_stream()` typed `AgentEvent`+`TracingMiddleware`(OTel) | 直接；公共投影层保留只换源 |
| 文件记忆 + MindMemOS 双轨 | `FileMemoryMiddleware`+`MindMemOSMiddleware`（仿 `AgenticMemoryMiddleware`/`Mem0Middleware` 模式） | 保留（两个 middleware，边界见 §5.3） |
| `SessionManager`+generation fencing+CAS | 无对应；`SessionSnapshot`+应用层 fencing 保留 | 保留（瘦身为 SessionOrchestrator） |
| Gateway/Feishu/Web | `app/`（不采用）；保留自有通道，消费 reply_stream | 保留 |
| `mcp/client.py`（应用级连接管理器：disconnect fencing/audit 隔离/artifact ACL/`outcome_unknown`） | `MCPClient`+`Toolkit(mcps=)` | **待裁决**——上游是否具备上述语义 Phase 0 验收矩阵核实；不满足则保留自有 client、仅经 substrate 注册工具进 Toolkit |
| Skills/Extensions | `Toolkit(skills_or_loaders)`+`Skill`/`SkillLoaderBase` | 直接（hook 语义差异需逐条对齐） |
| `cli/interactive_shell.py` | `launch_console`/`launch_tui` | 直接替换 |
| ALFWorld harness/worker NDJSON | 无对应 | 保留（领域后端） |
| `DeviceLeaseManager` | 无对应 | 保留（`on_acting` 内调用） |

## 5.3 双轨记忆注入边界（决策已锁：两轨并存）

| | `FileMemoryMiddleware` | `MindMemOSMiddleware` |
|---|---|---|
| 语义分工 | "你是谁"——身份/偏好/常驻上下文 | "发生过什么"——episode/graph/结构化事实 |
| 数据源 | `memory/file_store.py`+`context_service.py` 快照 | `EmbeddedMindMemOS`+`automatic_recall`+`add_queue`/`enrichment_queue` |
| `on_reply` | 每 session 快照一次注入 `HintBlock`（平移 `FrozenMemoryContextService`） | query 召回 top-k 注入 `HintBlock`（平移 `automatic_recall`） |
| 写回 | `memory_add`/`memory_link` 工具（agentic） | `on_compress_context` 追加+session-end finalizer 钩子 |
| 边界规则 | 只注入常驻身份层 | 只注入检索召回层；`require_recall_after_compaction` 语义保留在 middleware 内 |

## 6. 核心流转原型（物理设备动作场景）

最差场景 = "用户让机器人关灯"：现状穿越 `generic_runtime`（观察屏障）+`tool_executor`（许可状态机）+`executor`（物理门）+session fencing 四处。目标形态：

```python
# agents/middleware/physical_safety.py（骨架，语义边界已核实——
# 收到 deepcopy、只允许显式 ALLOW/DENY/ASK、PASSTHROUGH≈ASK 不可用、
# 输入改写只能在 on_acting 的 next_handler(tool_call=...) 上）
class PhysicalSafetyMiddleware(MiddlewareBase):
    def __init__(self, store: PermissionStore, policy: PermissionPolicy,
                 leases: DeviceLeaseManager,
                 confirm: ConfirmationHandler) -> None: ...

    async def on_check_permission(self, agent, input_kwargs, next_handler):
        # 非物理工具 → next_handler()（弃权唯一合法方式，不是 PASSTHROUGH）
        # 物理工具：store.create_request（副作用前 reserve）→ policy.evaluate
        #   → DENY 直接返回 / ASK(bypass_immune=True)
        #   确认交互 = 在 middleware 内 await 自有 protocol-v2 逐项决策卡
        #   （spike 必须验证长 await + 取消传播；确认放行后的重放会再次走
        #    permission 链——租约/TargetChanged 在 on_acting 复检）
        ...

    async def on_acting(self, agent, input_kwargs, next_handler):
        # 批准后目标绑定复核 → TargetChanged → claim_step
        #   → leases.acquire(resource_key, generation) → next_handler()
        #     （HomeTool.execute = adapter.execute 入口，不调外部执行路线）
        # backend_attempted=True 且终态不可证 → ToolResultBlock.metadata 标
        #   outcome_unknown、retryable=False（不可重试语义保留）
        ...
```

```python
# agents/middleware/observe_barrier.py
class ObserveAfterActMiddleware(MiddlewareBase):
    """requires_model_observation 的工具成功后，在 on_acting 的
    next_handler() 返回后改写 ToolResponse.content——追加 TextBlock+
    DataBlock(图像)。禁止 agent.observe()：它会把观察插在 tool_result
    之前倒置时序。屏障状态（pending/unconsumed_tool_call_id）随
    SessionSnapshot 持久化，崩溃恢复后可续跑。"""

# agents/household_agent.py
def build_household_agent(*, safety, file_mem, mindmemos, api_key) -> Agent:
    return Agent(
        name="homemaster", system_prompt=...,
        model=AnthropicChatModel(credential=AnthropicCredential(api_key=api_key),
                                 model="claude-sonnet-4-5"),
        toolkit=Toolkit(tools=[...HomeTool/FunctionTool 包装 domain/tools.py...],
                        mcps=[MCPClient(...)]),
        middlewares=[safety, ObserveAfterActMiddleware(), file_mem, mindmemos],
        model_config=ModelConfig(max_retries=3),
        context_config=ContextConfig(trigger_ratio=0.8, reserve_ratio=0.1),
        react_config=ReActConfig(max_iters=12),   # 原 max_tool_iterations
    )

# 验收环（benchmark/goal 判定的目标形态）——verifier 用无工具 Agent+
# structured_schema；GoalPipeline 内层 while 无上界，外置迭代上限包住
GoalPipeline(executor=household, verifier=verifier_agent,
             verifier_reset_context=True, max_iters=6)
```

## 7. 风险登记（开工前逐条签认）

1. **语义保真是最大风险**：`backend_attempted`/`outcome_unknown`/`TargetChanged`/generation fencing 在 adapter 中丢失即安全回退。每个 Phase 验收门必须注入这些故障路径，禁止只跑 happy path。
2. **dev 版 API 漂移**：锁 commit + substrate 单点隔离 + contract test（升级 vendor 时跑）。
3. **两条语义缝待 Phase 0 探针裁决**：`ToolResultBlock.metadata` 能否完整承载 `data` 机器协议字段；`reply_stream` 事件粒度能否维持现有公共投影 allowlist 语义。
4. **MindMemOS 本地源码覆盖**继续生效（`third_party` 独立 dist 模式不动）；`agentscope` 升级 = `src/agentscope/` 整树重放新 commit + 同步 VENDORED_SOURCE.md 本地 patch 清单 + 跑 contract test。
5. **vendored 树纪律**：`src/agentscope/` 保持 verbatim、ruff/覆盖率 exclude；本地如需补丁必须记入 `VENDORED_SOURCE.md` 的 patch list，否则上游重放会静默丢改动。加 vendor-audit 测试：锁定 commit 文件 hash 清单 + 本树复核（复用 manifest 相等机制）。
6. **上游死字段/死参数不得依赖**（评审取证）：`PermissionDecision.updated_input`、`ReActConfig.stop_on_reject`、`GoalPipeline.max_retries`（存而未用，内层 while 无上界）、`PASSTHROUGH`（≈ASK）。
7. **挂起态脆弱不变量**：`get_awaiting_tool_calls` 只看 context 尾部 assistant 消息——park 期间写 context 的 middleware 会使挂起调用脱锚；`reply()` 在挂起态 raise RuntimeError（web/cli 必须 reply_stream）；ConfirmResult/ExternalResult 事件须按 reply_id+generation 过滤并把上游 ValueError 转译为 typed stale-rejection。
8. **打包断言已修正**：`packages.find` 的 `include` 必须加 `agentscope*`（原配置只收 `homemaster*`/`mindmemos*`，wheel 会静默缺包）；验收门升级为干净 build→空 venv 装 wheel→ZIP 内容与 manifest 精确相等→源码外 `import agentscope`。
9. **现存一致性顺手修正**：`KNOWN_EVENT_TYPES` 缺 `permission.grant_changed`/`tool.execution_published`/`transport.request_retrying`/`runtime.reactive_compact_started`；`stream_events.py:161` 用 `event.type` 作 ErrorEvent 兜底文本与"禁止内部 type 作 fallback"纪律有摩擦——迁移时一并清理。
