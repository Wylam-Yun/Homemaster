# HomeMaster 架构调研报告

> 调研方式：四路只读源码调研（Runtime/工具安全/记忆/入口链路）+ 一路交叉验证。
> 所有结论均以 `file:line` 形式标注关键证据；「未找到」一节明确列出文档声称存在但代码未实现的能力。
> 调研基线：`src/homemaster`（Python 主包）+ `src/agentscope`（vendored 决策引擎）+ `web/`（React SPA）+ `workers/alfworld_worker`（隔离子进程）。

## 0. 一句话架构定位

HomeMaster 是一个 **LLM-first 通用 Agent 运行时**：单一 `ApplicationRuntime` 为所有入口（CLI、交互 Shell、Web Console、飞书 Gateway、ALFWorld benchmark、浏览器环境）提供统一的会话生命周期、上下文工程、权限门控与工具执行；决策循环复用 vendored AgentScope 的 ReAct 引擎，但通过 `substrate/` 外壳层把 deadline、取消、上下文、权限的全部权威留在 HomeMaster 侧。

## 1. 实际系统分层

```text
┌─────────────────────────────────────────────────────────────────────┐
│ L1 入口层 Entry                                                      │
│  CLI(Typer) │ Web Console(FastAPI+React) │ Feishu Gateway │ Bench   │
│  职责：协议适配、认证、RunRequest 构造、确认通道、事件渲染              │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ RunRequest / RunResult
┌──────────────────────────────▼──────────────────────────────────────┐
│ L2 应用编排层 Application                                            │
│  compose_application(组合根) → ApplicationRuntime → RunDriver        │
│  职责：profile 组装、Session 生命周期、generation fencing、commit/save │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ engine_state / session mirror / events
┌──────────────────────────────▼──────────────────────────────────────┐
│ L3 引擎外壳层 Substrate                                              │
│  AsAgentRuntime ─(middleware 链)─> AgentScope Agent(ReAct loop)      │
│  职责：deadline/取消/快照/事件投影/协议围栏/Provider 重试             │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ tool_call / tool_result
┌──────────────────────────────▼──────────────────────────────────────┐
│ L4 通用能力层 Capabilities                                           │
│  ToolRegistry │ ToolExecutor │ PermissionChecker+Store │ Memory     │
│  Skills │ MCP │ Extensions │ Devices │ TaskState │ Artifacts │ Events│
└──────────────────────────────┬──────────────────────────────────────┘
                               │ domain tool / IPC / SDK
┌──────────────────────────────▼──────────────────────────────────────┐
│ L5 领域与环境层 Domain & Environments                                │
│  HomeWorld(模拟家庭) │ ALFWorld Harness→Worker IPC │ Playwright 浏览器 │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ 外部调用
┌──────────────────────────────▼──────────────────────────────────────┐
│ L6 外部环境 External                                                 │
│  LLM Providers │ Neo4j │ Qdrant │ MCP servers │ AI2-THOR/Unity │ 飞书 │
└─────────────────────────────────────────────────────────────────────┘
```

分层依据不是目录名而是**依赖方向与所有权**：

- 入口不直接创建 Provider/Registry/Runtime，而是经薄包装层（`BrowserApplication`/`AlfworldGatewayApplication`/`ChannelBridge`/Web 路由）改写 profile/依赖后收敛到同一个 `ApplicationRuntime.run()`（`cli/run_command.py:113-134`、`web/app.py:443-458`、`channels/bridge.py:40-112`、`alfworld/benchmark/runner.py:274-303`）。
- 组合根是两层：`compose_application`（外层全栈编排：memory/MCP/扩展/服务，`application/composition/base.py:182`）调用 `create_application`（内层运行时组装，`application/factory.py:52`，注释自称 "Single composition root"）；`profile ∈ {local_robot, alfworld, browser}` 决定工具矩阵（`adapters/profiles.py:90-136`）。
- `ApplicationRuntime` 是 session 级生命周期 owner，不import任何领域包（`application/runtime.py:167`）。
- `AsAgentRuntime` 是唯一引擎外壳；真正 loop 在 `src/agentscope/agent/_agent.py`。
- 领域语义（HomeWorld/ALFWorld/浏览器）只经工具与环境适配器进入系统，反方向零依赖。

## 2. 核心模块职责

### L2 应用编排层

| 模块 | 文件 | 职责 |
|---|---|---|
| `ApplicationRuntime` | `application/runtime.py:167` | run 准入、session 打开/恢复、turn 栅栏、cancel、compact、commit+save、aclose |
| `RunDriver` | `application/run_driver.py:119` | 单 run 装配：tool view→provider→assembler→executor→substrate；`execute()`(157-402) |
| `SessionManager`/`SessionRuntime` | `application/session.py:98-,448-` | 内存 session、generation fencing、cancel 线性化、CAS revision 持久化 |
| `ApplicationToolExecutor` | `application/tool_executor.py:26` | 构造 `ToolExecutionContext`、自身即 run deadline、observer 短路、artifact 发布 |
| `AutomaticRecallService` | `application/memory_recall.py:32-194` | generation-fenced 自动记忆召回（每代一次） |
| `RunRequest/RunResult/RunPolicy` | `application/contracts.py:60-262` | 不可变 run 契约：policy 含 `max_tool_iterations`/`deadline_s`/`stop_condition`/`capabilities` |

### L3 引擎外壳层（substrate）

| 模块 | 文件 | 职责 |
|---|---|---|
| `AsAgentRuntime` | `substrate/runtime.py:127` | 包装 AS `Agent.reply_stream`：SIGINT 接管、共享 deadline、事件投影、guard 检查、快照边界 |
| 中间件链 | `substrate/middleware_runtime.py` | `ContextAssembly`（每次模型调用前 prepare 上下文）、`ProtocolFence`（offered-tools 围栏+批次原子拒绝）、`ProviderRetry`（传输级重试）、`ProviderObservability` |
| `HomeToolAdapter`/`RunScope` | `substrate/toolkit.py:55-364` | HM `BaseTool`→AS tool 适配；`RunScope` 携带 deadline/cancellation/subject/deps；`check_permissions` 仅 ALLOW/DENY（AS ASK 刻意不用，`toolkit.py:306-309`） |
| 消息/快照 | `substrate/messages.py`、`substrate/snapshot.py` | canonical Message↔AS Msg 双向转换；schema-v2 快照（engine 权威 context + mirror 投影） |
| 模型适配 | `substrate/models.py:71-143`、`substrate/as_llm_client.py:108` | `ProviderProfileConfig`→`ChatModelBase`；`AsLLMClient` 供记忆抽取/JSON 任务用 |

**关键设计（借引擎不借语义）**：`AsAgentRuntime` 构造 `Agent` 时显式解除 AS 原生语义——`ContextConfig(compression_tool_enabled=False, compression_fallback_to_truncation=False)`、`InjectionConfig(inject_runtime_state=False)`（`substrate/runtime.py:385-397`），`ReActConfig.max_iters = HM预算+2` 仅做缓冲（373-383）；真正的停止判据是 HM 的 `max_tool_iterations`、`_check_guards`（1403-1416）与调用方 `stop_condition`（`run_driver.py:449-480`）。

### L4 通用能力层

| 子系统 | 关键文件 | 职责 |
|---|---|---|
| 工具契约/注册 | `tools/base.py:21,89,200`、`tools/contracts.py:130,282,480,722` | `BaseTool`/`FunctionTool`/`ToolRegistry`（模型可见名索引）；`ToolExecutionResult` 状态机 |
| 统一执行器 | `tools/executor.py:223` | 参数校验→权限判定→资源租约→`wait_for(deadline)`→异常分类；`execute_many` 并发分组（930-1021）；物理工具走 `_execute_physical`（368-555） |
| 权限 | `permissions/policy.py:77,95,168`、`permissions/store.py:251`、`permissions/models.py`、`permissions/config.py` | `evaluate_tool`（路径/命令/工具名/capability/plan-mode/allow-deny/mode）；`PermissionStore` SQLite 审批状态机（requests/items/steps/submissions/grants/events） |
| 审批通道 | `cli/confirmation.py`、`web/confirmations.py`、`gateway/confirmation.py` | CLI 逐项 1/2/3；Web pending future+HTTP submit；Feishu fail-closed（`gateway/confirmation.py:76-145`） |
| 记忆 | `memory/`（16 文件） | 分层记忆：files tier + MindMemOS full tier（详见 §5） |
| 经验管线 | `experience/` | `SessionFinalizer`（分阶段可重入 job）、`DreamingCoordinator`、`AlfworldCompileJobService`、success-path 蒸馏 |
| 设备 | `devices/lease_manager.py`、`devices/connection_pool.py`、`devices/audit.py` | per-device FIFO 租约+generation fence+tenant 校验；连接池跨租户拒绝；设备审计 JSONL |
| MCP | `mcp/client.py,adapter.py,audit.py` | 多 server 生命周期；工具原子注册；调用全落 JSONL 审计；结果 artifact 化 |
| 扩展 | `extensions/contracts.py,loader.py,hook_runner.py`、`application/extension_lifecycle.py` | digest/capability 校验的受信扩展；4 个 hook event；可注册工具进同一 registry+权限链 |
| 技能 | `skills/registry.py,loader.py,types.py` | `SKILL.md` 技能注册表（builtin+bundled），供 `load_skill` 工具解析 |
| 任务状态 | `task_state/store.py,models.py,tools.py` | `TaskStateStore` 任务进度快照；`task_progress_check` 工具 |
| 工件 | `artifacts/tool_output_store.py,publisher.py` | tenant/session/run 分区存储；opaque `hm-artifact:` 句柄；ACL+hash 复查 |
| 事件 | `events/bus.py,runtime_events.py,stream_events.py,public_projection.py,sinks.py` | `EventBus` 有界订阅；三级投影（内部富事件→本地 DTO→公开 envelope，类型+字段双 allowlist） |
| 服务工具 | `tools/service_tools.py` + `service_tool_specs.json`、`tools/runtime_services.py` | 23 个服务工具：ask_user_question/lsp/image_*/config/plan_mode/cron_*/task_*/agent/send_message/team_*/remote_trigger/mcp_auth |
| 子任务运行时 | `tools/task_runtime.py`、`cli/child_worker.py` | `agent` 工具 spawn `python -m homemaster.cli.child_worker` 子进程；task_* 后台任务管理；team_* 团队注册表 |

## 3. 关键调用链（已验证）

### 3.1 单轮 run 主链路

```
用户输入 → 入口构造 RunRequest
→ ApplicationRuntime.run (application/runtime.py:268)
   ├─ start() 幂等启动 + event_bus.start
   ├─ session_manager.open_or_resume（内存复用须 resume/taskset，否则新建）
   ├─ turn()：generation+=1, turn_index+=1, 新 CancellationSource (session.py:571-597)
   ├─ _GenerationFencedEventSink 绑定 generation
   └─ RunDriver.execute (run_driver.py:157)
      ├─ _run_tool_view：browser profile 建 run-scoped registry/executor
      ├─ provider_scope.bind；agent_state/task_state 深拷贝（本轮工作副本）
      ├─ ApplicationToolExecutor（deadline=now+run_policy.deadline_s）
      ├─ AutomaticRecallService.recall（consume_recall 门控，每代一次）
      ├─ _FencedAgentSession 包装
      ├─ AsAgentRuntime(model=provider.chat_model(), tools=[HomeToolAdapter...])
      │   └─ Agent.reply_stream ReAct 循环（agentscope/agent/_agent.py）
      │       ├─ on_model_call 前 ContextAssemblyMiddleware→assembler.aprepare
      │       ├─ tool_call → ProtocolFence 校验 → HomeToolAdapter.call
      │       │   → ApplicationToolExecutor → ToolExecutor.execute（见 3.2）
      │       ├─ 各边界 save_snapshot()→session.json
      │       └─ guards：max_tool_iterations/stop_condition/cancel/deadline
      ├─ CancelledError→CANCELLED；SessionGenerationError→CANCELLED(user_interrupted/stale)
      └─ _commit_and_save：generation-fenced apply 换入本轮状态
          → SessionFileBackend.save CAS→revisions/NNNN.json+latest.json
```

### 3.2 工具调用与权限门

```
模型 tool_call → Agent._execute_tool_call（jsonschema 校验）
→ ProtocolFenceMiddleware：call 名 ∈ 本轮 offered 集（批次原子拒绝→protocol_blocked）
→ HomeToolAdapter.check_permissions → PermissionChecker.evaluate_tool
   （路径参数敏感规则→命令 deny/allow 精确集→denied_tools→capability ⊆→plan_mode→allowed_tools→mode）
→ ApplicationToolExecutor.execute_for_substrate（建 ToolExecutionContext：session/run/turn/call_id/subject/backend/deadline/services）
→ ToolExecutor.execute（executor.py:241）
   ├─ registry.get / input_model.model_validate
   ├─ read_only → evaluate_tool；deny → permission_denied
   ├─ _is_physical → _execute_physical（见 3.3）
   └─ 普通：_resource_key → DeviceLeaseManager.acquire → wait_for(invoke, deadline.remaining_s())
→ 结果分类：SUCCESS / INVALID / DENIED / FAILURE / OUTCOME_UNKNOWN
   （mutating 且 backend_started 后的 timeout/cancel/异常→OUTCOME_UNKNOWN，契约级禁 retryable）
→ artifact 发布（图片/附件→tenant ACL store→data.artifacts 回填 opaque ref）
→ result_to_chunk→ToolResultBlock（机器字段藏 metadata["hm"]）→ 回模型
```

### 3.3 物理工具审批链（Human-in-the-loop）

```
_execute_physical（executor.py:368-555）
→ adapter.prepare(args, ctx)：精确解析目标+锁 binding（TargetUnresolved→拒绝）
→ store.create_request → evaluate_physical
   （同 run 已拒→直接拒；逐项覆盖判定：once/grant 精确命中/session 内存覆盖）
→ missing_item_ids 非空 → mark_awaiting_approval → confirmation handler
   ├─ CLI：逐项读 allow_once/allow_always/reject→一次 submit
   ├─ Web：登记 pending future→发事件→等 store commit（300s 超时）
   └─ Feishu：fail-closed 不可用 → approval_channel_unavailable
→ 批准返回后再次 prepare + _binding_signature 比对（目标漂移→target_changed）
   （注：physical 工具当前仅 alfworld_physical_{go_to,manipulate}_tool，
   由 ALFWorld Web console 经 web/serve.py:206-220 registry.replace 装配，非 benchmark 默认路径）
→ matching_grants 活体复查 → store.submit（幂等：submission_id+request_revision）
→ 每步 _run_physical_step：PhysicalGateOwner.linearize 内 grant 复查+claim_step
   → _lease → adapter.execute → adapter.observe → finish_step
   （no_effect_failure→预算内重试 MAX_ATTEMPTS_DEFAULT=3；unknown→outcome_unknown）
→ finally adapter.release
```

### 3.4 记忆写读链

**运行中写**：`mindmemos_add` → 证据门（当前 turn 成功 evidence）→ `EmbeddedMindMemOS.add_flat`（MemoryWrite：vector_pending+enrichment_pending+Source+EXTRACTED_FROM）→ `writer.write(consistency="strong")` → 逐项读回（active+内容+稀疏向量+图边）→ enrichment 队列（并发=2，与 add_queue 是两条独立队列）补 dense 向量+实体抽取→float32 容差读回。`add_flat` 同时是 trajectory/derived-experience 的确定性直写路径（`memory/mindmemos_writes.py`）。
`mindmemos_update`：`provenance_seq` 严格递增否则 `memory_stale_observation`；`update_versioned` 新版本+旧 archive+DERIVED_FROM 边+读回验证。

**运行后写（收尾）**：`ApplicationSession.close()` → `SessionFinalizationController.enqueue` → `MemoryAddQueue` 单 FIFO worker → `SessionFinalizer`：
`runtime_events.jsonl` 过滤 → `build_schema_episode`（配对 tool_steps、冲突即 raise）→ `add_schema_episode`（canonical≤3MiB、UUID5 幂等 add_record_id、per-episode lock）→ per-type 终态验证 → `feedback_implicit` → `dreaming_counter`→`DreamingCoordinator`（阈值触发→`mindmemos.dream`→逐动作核验+水位线）。
job 全程落 `experience_jobs/<sha256>/job.json` 分 phase 可重入。

**读/召回**：run 开始前 `consume_recall(generation)` 门控一次 → query=压缩摘要+任务态+用户消息 → `search(top_k=5, vanilla, rerank=False)` → `<memory-context>` 块 → `bind_automatic_memory_context` → prelude → "# Runtime Context" UserMessage（注明非新用户请求）。压缩后 `rearm_recall_after_compaction` 立即 inline 重召回，下一次 prepare 生效。
文件记忆另一路：`FrozenMemoryContextService.snapshot` 直接拼进 system prompt（每 session 冻结）。

### 3.5 ALFWorld 端到端链（15 步摘要）

```
benchmark-alfworld 命令 → AlfworldBenchmarkRunner（trial 选择/manifest 校验）
→ WorkerAlfworldAdapter.start → AlfworldWorkerClient.start（子进程 workers/alfworld_worker）
→ NDJSON reset → worker ThorBackend：加载 trial+seed+env.reset()+logical scene 校验
→ AlfworldHarness.reset→SceneSnapshot → build_episode_prompt
→ compose_application(profile="alfworld") + RunRequest(stop_condition=env won/done)
→ ApplicationRuntime.run → RunDriver → AsAgentRuntime ReAct
→ 模型 robot_manipulate → AlfworldHarness.execute：
    observe→capture_scene→陈旧检查(state_sequence+scene_generation)
    →scene.ground(必须唯一)→OracleActionGateway→backend.act
→ worker env.step → BackendReceipt(external_return_code+state+evidence)
→ AlfworldExecutionFeedback(success,terminal=done,won)
→ stop_condition：won→成功；done&&!won→benchmark_done_without_won
→ close 帧+进程退出码+stderr 核对
```

## 4. 关键代码路径速查

| 主题 | 路径 |
|---|---|
| run 准入/生命周期 | `application/runtime.py:268-345` |
| 单 run 装配 | `application/run_driver.py:157-402` |
| ReAct 外壳 | `substrate/runtime.py:159-446` |
| 协议围栏 | `substrate/middleware_runtime.py`（ProtocolFenceMiddleware） |
| 权限判定 | `permissions/policy.py:95-166`（普通）`168-243`（物理） |
| 审批状态机 | `permissions/store.py:251-`（submit 591、claim_step 811、finish_step 874、recover 1167） |
| 物理执行 | `tools/executor.py:368-555`（steps 712-889） |
| 结果契约 | `tools/contracts.py:480-`（OUTCOME_UNKNOWN 强制 548-571） |
| Session/CAS | `application/session.py:98-216`（runtime）`219-`（file backend）`448-`（manager） |
| 快照 | `substrate/snapshot.py:80-167`（v2 build/parse） |
| 上下文 | `agent/context.py:72-1006`（预算 389-549、usage anchor 617-674、压缩 764-887） |
| 自动召回 | `application/memory_recall.py:32-194`、`memory/automatic_recall.py:20-135` |
| MindMemOS 嵌入 | `memory/mindmemos_runtime.py:376-1976`（add_flat 782-1064、enrich 1148-1338、schema episode 1385-1477、update_versioned 1552-1701、dream 1917-1933） |
| 文件记忆 | `memory/file_store.py:20-272`（ThreatScanner 51-108） |
| 证据账本 | `memory/evidence.py:34-168` |
| 收尾管线 | `experience/finalizer.py:84-354`、`experience/session_finalization.py:17-69`、`experience/dreaming_state.py:186-401` |
| 队列 | `memory/add_queue.py:57-330`（单 worker FIFO）、`memory/enrichment_queue.py:34-201` |
| ALFWorld harness | `alfworld/harness.py:19-134`、`alfworld/scene.py:20-97`、`alfworld/actions.py:11-36`、`alfworld/lifecycle.py:11-58` |
| Worker IPC | `alfworld/worker_client.py:108-228,491-591`、`workers/alfworld_worker/`（protocol.py:8-37） |
| 权限适配 | `alfworld/permission_adapter.py:60-89,171-261,505-569` |
| 领域模拟 | `domain/home_backend.py:26-268`、`domain/tools.py:130-509` |
| 入口 | `cli/app.py`、`cli/run_command.py`、`cli/interactive_shell.py`、`web/app.py`、`web/serve.py`、`channels/bridge.py`、`gateway/runtime.py` |
| 工具矩阵 | `adapters/profiles.py:90-136`（build_tool_registry）`277-382`（physical/alfworld 变体） |
| Web 链路 | `web/run_registry.py:48-195`、`web/event_hub.py:79-213`、`web/event_projection.py:40-120`、`web/confirmations.py:85-321` |
| 飞书 | `channels/impl/feishu.py:85-1267`、`channels/bus.py:24-205`、`channels/router.py:27-52`、`gateway/runtime.py:74-589` |
| 服务工具 | `tools/service_tools.py`、`_SERVICE_TOOL_NAMES`（23 个）、`tools/runtime_services.py` |
| 子 agent | `tools/task_runtime.py:19-`（TaskType）、`cli/child_worker.py` |

## 5. 设计亮点与技术取舍

### 亮点 1：借引擎不借语义（substrate 外壳模式）
- **问题**：自研 ReAct loop 成本高且易错；直接用第三方框架又会丢 deadline/取消/权限语义。
- **设计**：复用 vendored AgentScope `Agent` 做决策循环，但 HM 外壳持有全部语义权威——AS 原生 injection/context mutator 显式关闭，AS ASK 权限通道弃用，`max_iters` 仅缓冲。
- **实现**：`substrate/runtime.py:368-397,452-559,925-945,1345-1416`；`toolkit.py:306-309`。
- **收益**：引擎可换（vendored→上游更新或换框架）而语义不变；每一层职责可面试级讲清。

### 亮点 2：generation fencing = 全量写屏障
- **问题**：并发 run、用户取消、断线重连会踩坏 session 状态与事件流。
- **设计**：单一 generation 计数覆盖 session 变更、事件发布、revision commit 三类写；`cancel()` 自增 generation 使旧 writer 以 typed `SessionGenerationError` 显形。
- **实现**：`session.py:132-156,172-197,571-607`；`_FencedAgentSession`（`run_driver.py:61-116`）；`_GenerationFencedEventSink`（`runtime.py:124-160`）；`gateway_generation` 生产时固化。
- **收益**：取消=插队新代际，旧任务自动失效；断连重放不会把旧事件贴成新代际。

### 亮点 3：OUTCOME_UNKNOWN + backend_attempted 一等状态
- **问题**："调用已发出但结果未知"（超时/取消/断连）是普通失败无法表达的语义，盲目重试会重复副作用。
- **设计**：`ToolExecutionResult` 契约构造时强制三件套（`backend_attempted`+`outcome_certainty`+禁 retryable）；mutating 且 started 后的不确定终局统一归类；物理 step 报 unknown 前先 best-effort observe+finish_step。
- **实现**：`contracts.py:548-571`；`executor.py:325-357,595-606,771-845,1158-1171`；MCP `McpCallError` 同规（`mcp/adapter.py:69-72`）。
- **收益**：外部世界的真实不确定性被显式建模，不做乐观假设。

### 亮点 4：物理精确授权链 + TOCTOU 双防线
- **问题**：审批等待期间目标可能移动、授权可能撤销——批一次就放手不安全。
- **设计**：精确 resource key 逐项授权（SQLite 状态机）；批准返回后重新 prepare+`_binding_signature` 比对；每步执行前 `linearize` 内第三次 grant 复查+原子 claim。
- **实现**：`executor.py:389-517,712-889`；`store.py:536-590,811-889`；`alfworld/permission_adapter.py`。
- **收益**：审批语义精确到「这个物体×这个动作×这个环境」，撤销即时生效。

### 亮点 5：记忆系统的「receipt≠终态」全栈验证
- **问题**：记忆写入的 ack 不等于真落库；向量/图/文档三存储更难保证一致。
- **设计**：所有写路径以「读回 active raw+内容相等+图边存在+float32 容差逐维」收尾；写前有证据门（当前 turn 成功 evidence）与不可变身份（dedupe_key/memory_type）；后台管线全部幂等可重入（UUID5 add_record_id/job.json phase/水位线）。
- **实现**：`mindmemos_runtime.py:963-1064,1148-1338`；`memory_tools.py:901-978`；`finalizer.py:386-496`；`dreaming_state.py:340-401`；`evidence.py:59-145`。
- **收益**：记忆可审计、可追溯、可恢复；污染面被 evidence scope 锁住。

### 亮点 6：协议围栏 + 三级事件投影
- **问题**：模型可能调用未供出的工具名（provider 伪造/串轮污染）；内部富事件直接外传会泄漏 thinking/usage。
- **设计**：`ProtocolFenceMiddleware` 记录每轮 offered 集并整批原子拒绝越界 call；事件三级投影（内部 RuntimeEvent→本地 DTO→公开 envelope）类型+字段双 allowlist，无公共语义内容直接丢弃。
- **实现**：`middleware_runtime.py`（fence）；`public_projection.py:19-105`；`stream_events.py:95-187`。
- **收益**：对外信任边界清晰，公网通道（飞书）零泄漏。

### 技术取舍（值得主动讲）
- **审批只给物理工具**：普通工具不打扰用户（mode+deny-list 控制），物理副作用走精确授权链——用交互成本换安全精度的取舍（`policy.py:162-166` 注释明述）。
- **MCP 工具一律按 mutating fail-closed**：外部 annotation 未验证不信任，宁可误报 mutating 也不漏放（`mcp/adapter.py:176-180`）。
- **resume 粒度=turn 边界**：cancel 不 commit revision，应用层 `revisions/` 是唯一活跃 resume 源（substrate `session.json` 是更细的观测旁路，不进恢复路径）——用"丢半路进度"换"快照永远一致"（`run_driver.py:361-386`）。
- **文件记忆每 session 冻结**：`FrozenMemoryContextService` 只增不删——prompt 稳定 vs 记忆新鲜度的取舍（`context_service.py:16-34`）。
- **ALFWorld worker 独立 venv+NDJSON**：不共依赖、不走 HTTP，stdout 纯协议+第三方输出重定向 stderr——用进程隔离换依赖地狱（`workers/alfworld_worker/__main__.py:31-63`）。
- **无 rerank、无 SSE、Feishu 无审批**：召回固定 vanilla top_k=5；Web 只有 WS；飞书审批 fail-closed——明确的实现边界（见 §6）。

## 6. 当前架构的实现边界（代码核实）

| 能力 | 状态 | 证据 |
|---|---|---|
| 独立 `AgentRuntime` 类 | 不存在，已并入 `AsAgentRuntime` | `substrate/runtime.py:127`；注释 1403-1404 |
| `RunPolicy.max_turns` | 字段存在但无消费方（死配置） | `application/contracts.py:67` |
| 非物理工具交互确认 | 不存在（evaluate_tool 无 requires_confirmation 分支） | `permissions/policy.py:95-166`；`executor.py:278-289` 为死路径 |
| 风险等级（risk_level） | 不存在；只有 PermissionMode | `permissions/` 全目录无该字段 |
| Feishu 审批 | fail-closed 不可用 | `gateway/confirmation.py:76-145` |
| Web SSE | 不存在；仅 WebSocket | `web/app.py:1067-1126` |
| 记忆 rerank | 未启用；vanilla top_k=5 | `application/memory_recall.py:89-99`、`tools/memory_tools.py:383-389` |
| `tool.auto` capability | 定义了但无读取方（元数据占位） | `tools/contracts.py:158` 等 5 处定义，0 处判定 |
| `MemoryAddQueue.enqueue`（flat-add job 入口） | 无 src 调用者（队列实际只用 `enqueue_work`） | `memory/add_queue.py:109-136` 仅测试覆盖 |
| `EmbeddedMindMemOS.add()/add_record()`（原生 LLM 提取管线） | 无 src 调用者；`add_schema_episode` 是唯一入口；**`add_flat` 是活跃的确定性直写路径**（mindmemos_add 工具/trajectory/derived-experience 4 个调用方） | `memory/mindmemos_writes.py:28,82`、`memory/mindmemos_runtime.py` |
| AS 长期记忆 middleware | vendored 存在但未接线 | `agentscope/middleware/_longterm_memory/` |
| `context_projection.py` | no-op 透传 | `agent/context_projection.py:10-18` |
| 飞书群 @机器人 精确匹配 | 代码未见（CLAUDE.md 规则有述，实现未见） | `channels/impl/feishu.py:899-997` 只查 chat_type |
| 真实机器人/VLA/VLM | 未接入；skill_mode=simulated | README；`domain/home_backend.py` 文件世界 |
| QA probe 工具面隔离 | 仅靠不开 session 跳过 finalization；mindmemos_* 工具未摘除 | `benchmarking/locomo/runner.py:373-409` |

## 7. 值得面试深挖的「数据流×控制流」关系

- **控制流**：入口→`ApplicationRuntime`（session owner）→`RunDriver`（run assembler）→`AsAgentRuntime`（engine shell）→`AgentScope Agent`（loop owner）→`ApplicationToolExecutor`→`ToolExecutor`→工具体。
- **状态流**：`turn()→generation+1`；本轮 `agent_state`/`task_state` 深拷贝工作副本→终局 `apply(generation)` 原子换入→CAS revision 落盘；cancel 不 commit。
- **记忆流**：run 前召回（generation 门控）→run 中 evidence 登记+`mindmemos_*` 工具→session 关闭→Finalizer FIFO→schema episode→feedback→dreaming→（下次 run）召回命中。
- **审批流**：physical call→prepare→request→missing→awaiting→入口 handler→submit→re-prepare+signature→per-step grant 复查→execute→observe→finish。
- **事件流**：`RuntimeEvent`（含 gateway_generation）→`EventBus` 有界→内部 JSONL 全量；`public_stream`→projection allowlist→Web WS/飞书出站；`stream_events`→CLI 渲染。
- **工件流**：工具产出大数据/图片→`ToolOutputStore`（tenant/session/run ACL）→`hm-artifact:` 句柄回模型→公共事件只带 opaque ref。
