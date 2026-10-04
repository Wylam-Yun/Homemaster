# AgentScope 迁移实施计划（V3.7）

**状态：Phase 0/1/2 已实施（v35-architecture 分支，远端 worktree `Homemaster-v35` 全量回归跟踪中）**
**日期：2026-10-01（实施更新 2026-10-02）**
**设计依据：`plan/V3.7/agentscope-migration-design.md`（已锁定决策 §1、映射表 §5）**

原则：全程可运行；每个 Phase 结束跑完整回归；验收门一律黑盒终态 + 逐实例断言（不接受 best-of/聚合通过）；每步留回退位。

## Phase 0 — Vendor + Spike（不动生产路径）

目标：证明"Agent + 现有工具 + 许可链 + console"端到端可行，炸掉最大不确定性。

任务清单：

- [x] `src/agentscope/` vendor：整包复制上游 `src/agentscope`（含暂不启用的 app/workspace/tui/realtime/sop/rag 等子包）+ `LICENSE`；写 `src/agentscope/VENDORED_SOURCE.md`（commit `a1f30d48b6de5bfb25a4bf2c5fbf1b6442f5106d`、版本 2.0.10dev、本地 patch list 为空）。
- [x] `pyproject.toml`：`packages.find` 的 `include` 加 `agentscope*`（**漏了 wheel 静默缺包**，setup.sh 的 `--no-editable` 必踩）；上游 core `dependencies`（实为 27 项）+ 未声明硬依赖 `requests`/`pyyaml`/`typing-extensions` 并入 `dependencies`，`mcp` 提为 core，extras 一律不装；`tool.ruff.exclude += "src/agentscope"`；`package-data` 按上游真实模式补 `agentscope` 的 `py.typed`/`_models/*.yaml`/`_cosyvoice_models/*.yaml`/`Dockerfile*.template`/alembic 四件套（`_models/` 无 `__init__.py`，漏拷不报错但 `list_models` 静默空）。**不需要** `tool.uv.sources`。
- [x] `uv lock` + `uv sync --extra dev`，验证 `agentscope.__file__` 解析到 `src/agentscope/`；`Agent`/`Toolkit`/`launch_console` 实际 import 跑通；`sys.modules` 白名单回归断言 core-only 下 `app`/`rag`/`tui`/`classifier` 不被拉起。
- [x] **字段级消息矩阵文档**（先决裁决，否则 Phase 1 在歧义上叠加）：HM canonical→`Msg` 逐字段落点，覆盖 text/image(base64+`metadata.path`)/tool_calls/`data`/`provider_metadata`/`reasoning_content`/`is_error`/`finish_reason`/`usage`/`tool_call_id`（auto-observe 关联）/`observation_of_tool_call_id`/消息顺序/`normalize_content` 语义 + nested mapping thaw/deep-copy 回归。
- [x] **快照 schema 裁决文档**：唯一权威快照 = `SessionSnapshot`（revision/generation/evidence_refs/recall/task_state）；`AgentState.model_dump` 为 `agent_state` 子树；HM 循环内态（屏障/unconsumed_observation_tool_call_id/无进展计数）持久化载体裁决；`SessionPersistenceManager` 去留拆分（trace.jsonl/session.json 兼容期/CLI 四命令数据源）。
- [x] **物理执行链路点表**（步序→hook 映射，先决裁决）：建申请/评估→`on_check_permission`（middleware 内 await 自有 protocol-v2 逐项决策卡）；批准后复核+TargetChanged+claim_step+lease+逐 step `adapter.execute`→`on_acting`（HomeTool.execute 为入口）；observe/finalize→`on_acting` 后段。
- [x] `src/homemaster/substrate/` 建包：`messages.py`（双向转换器）、`toolkit.py`（适配 + HM 侧 registry 镜像——`ToolBase` 元字段集小于 `ToolDefinition`）、`models.py`（`ProviderProfileConfig`→`ChatModelBase` 工厂）。
- [x] `tests/homemaster/substrate/test_messages.py`：HM→AS→HM round-trip 按消息矩阵逐字段断言（含 `backend_attempted` 机器字段在 `ToolResultBlock.metadata` 的往返）。
- [x] Spike 脚本（升级为全故障路径，不是 happy-path demo）：`Agent`+适配设备工具（`HomeTool`，`is_external_tool=False`）+`PhysicalSafetyMiddleware` 骨架；验证项=逐项决策卡确认（含超时与断连清扫）、批准后目标已变→`TargetChanged` 注入、backend 已开始后取消→`outcome_unknown`、mutating 超时→`outcome_unknown`、租约计数归零核对、Ctrl+C 中断；`launch_console` 仅作演示壳。
- [x] **MCP 验收矩阵**（裁决 `mcp/client.py` 去留）：上游 `MCPClient`/`Toolkit(mcps=)` 是否具备 disconnect fencing、audit-sink 失败隔离、artifact tenant ACL 写盘、`outcome_unknown` 分类、tenant 隔离（fixture tenant_id≠subject_id）；不满足则保留自有 client、仅 substrate 注册进 Toolkit。
- [x] Phase 0 出口裁决：设计 §7 三条语义缝（`metadata` 承载 `data`、`reply_stream` 事件粒度够投影、确认后重放路径下租约/TargetChanged/`outcome_unknown` 在二次 permission 检查中完整保留）。

验收门：

- `uv sync` 环境（开发 `.venv`）与 setup.sh 部署环境（`.runtime/venv`）都能 `import agentscope`；setup.sh import 自检加 `agentscope`；**wheel 门**：干净 `build/` 构建→空 venv 仅装 wheel→ZIP 内容与 vendored manifest（含点文件/symlink）精确相等→源码外 `import agentscope` 核 `__file__`；`git clean` checkout 上 `uv sync --frozen` 自洽。
- Spike 的许可拒绝/确认/执行/异常四路径在真实 tool 上各走一遍，外部终态（设备 backend receipt）逐次核对——不只看日志。

## Phase 1 — Provider/消息层替换（双跑期）

目标：`LLMClient`/transports 退到 `ChatModelBase` 之后，内部契约不变、外部底座已换。

任务清单：

- [x] `substrate/models.py` 落地：`ProviderProfileConfig`→`ChatModelBase`（anthropic→`AnthropicChatModel`、openai→`OpenAIChatModel`，顺带免费获得 dashscope/gemini/ollama/…）；多 key 轮询（`provider_key_index`）经 `ModelConfig` 或薄封装保留，指明封装位置与 per-key attempt 归属。
- [x] `substrate/messages.py` 接入 `ContextAssembler` 出口：发往模型前 `list[Message]`→`list[Msg]`，响应 `ChatResponse`→`AssistantMessage`。**HM canonical `agent/messages.py` 保留**（持久化/compact/投影/ALFWorld receipt 共基座），substrate 只做边界双向转换——不删。
- [x] **provider 不变量落点（硬验收门，非事后记录）**：冻结 `request_sha256`（`on_model_call` 拿到的是格式化前 `Msg`，逐字节 SHA 要在 formatter/`ChatModelBase` 子类做）、`ProviderAttemptRecord`/`AttemptCommitState` 三相 commit、delta 可见性门控重试、`transport.request_retrying` 事件、`provider_attempt_sink_factory` 注入（ALFWorld 审计用）——逐条列不变量→落点→验证方法（真实 envelope 断言、retry body SHA 逐字节相等、commit 后禁重试）。
- [ ] `cli/interactive_shell.py` **换引擎不退役壳**：REPL/斜杠命令/逐项确认卡/SIGINT+清理所有权保留，内层 run 换 `reply_stream()` 消费；`/compact`→`compress_context()`、`/new`→原地清理（无 `reset()` 方法，整换 state 会泄漏 permission_context）、`/status`→`agent.state`；`turn.py` 单轮入口归宿写明；`launch_console`/`launch_tui` 仅可选演示。

验收门：

- 同一批回归会话分别走旧 transport 与新 `ChatModelBase`，断言最终 `RunResult` 与事件序列等价（逐实例，不比聚合）。
- provider 从 2 家扩到 ≥4 家各跑一条 smoke，作为正交证据。
- CLI 新形态在真实 run 上验证流式渲染、确认提示、中断三条路径。

## Phase 2 — Agent 层替换（god-loop 退役）

目标：`generic_runtime.py` 1512 行的内联职责全部外迁，AgentScope `Agent` 成为唯一模型循环。

任务清单：

- [x] middleware 实装（实际落点与本计划写法不同，见偏差记录）：许可链/观察屏障/围栏经 `middleware_runtime.py` 三件套 + `ProtocolFenceMiddleware`（0907ede）落地；记忆轨按评审定稿 C+——**不建** `file_memory.py`/`mindmemos.py` middleware（file 轨注入本就在 assembler；recall 时机经 `rearm_recall_after_compaction` 内联，见 design-phase3-memory-middleware.md）。
- [x] `agents/tools/base.py` `HomeTool(ToolBase)` + HM 侧 registry 镜像（按名查 canonical `ToolDefinition`）：承载 `stable_id`/`required_capabilities`/`verification_policy`/`provenance`/`external_terminal_owner`/`requires_model_observation`——现有 `BaseTool` 元数据全部平移，不丢字段。
- [x] **RunScope/DomainBridge per-call 桥接对象**（`deps`/`services` dict 清零的承接载体）：每调用合成 task_state_store/completion_guard/current_tool_call_id/backend/alfworld_env/tool_registry/run_context/gateway_generation/memory_feedback/internal_tool_id/permission_subject/deadline/cancellation/domain_observer——`AgentState` 是跨调用共享态装不下 per-call 键，ALFWorld 三工具端到端跑通为验收。
- [ ] `ApplicationRuntime` 瘦身 → `SessionOrchestrator`：只留 generation fencing/cancel/turn 生命周期；dedup/registry/terminal/取消/清理逐项指派给保留 Web 层对象（运行所有权表）；`compose_application` 拆 per-domain composer + 资源图（谁拥有什么、关闭顺序）。
- [x] 持久化按 Phase 0 裁决落地：`SessionSnapshot` 唯一权威快照、`AgentState.model_dump` 为子树、`SessionFileBackend` CAS 为真源；`SessionPersistenceManager` 按裁决拆分；含 pending 观察屏障的会话 save→kill→load 后 `unconsumed_observation_tool_call_id` 与图片完整的回归。
- [ ] `web/app.py` 引擎切 `reply_stream`（两层拆分）：(a) 事件源适配 `AgentEvent`→`RuntimeEvent`/`StreamEvent`/`PublicStreamEvent`（allowlist 投影函数签名不变 + 投影等价 golden 测试集 + 负向用例：thinking/usage/raw 不外泄、无用户语义丢弃、generation 失配丢弃、无重复 final）；(b) dedup reserve/commit/rollback、approval 跨请求唤醒、WS idle disconnect、terminal final 唯一逐项断言。顺手修 `KNOWN_EVENT_TYPES` 陈旧清单与 `stream_events.py` fallback 文本。
- [ ] `substrate/toolkit.py` 过渡适配器在本阶段末删除（工具已全部原生 `ToolBase`）。

验收门：

- 旧 `AgentRuntime` 与新 `Agent` 在相同 prompt/工具集下跑同一批回归会话：取消六相、压缩（auto/reactive/manual/emergency）、许可拒绝、`outcome_unknown` 注入、`TargetChanged`——逐实例断言终态等价。
- Web 通道真跑：事件序列、final 唯一、断线重连不回放旧 generation 消息，全部按现状纪律复测。
- `deps`/`services` dict 逃逸口清零（grep 断言无新增用法）。

## Phase 3 — 多 Agent 化与清退

目标：拿到迁移红利，删除被替代的自研件。

任务清单：

- [x] `GoalPipeline` 接 benchmark goal 判定：按裁决走**候选 A（Tier-1）**——不直接采用 vendored `GoalPipeline`，HM 自有 `benchmarking/verdict.py`：`AsLLMClient.complete_json` + typed `VerdictVerifier` 协议，verdict `pass|fail|impossible`+provenance（`env`/`typed`/`llm`），LLM 永不覆写环境权威 `won=true`，cancel/deadline 守护 + 有界重试（commit `786881b`）。
- [ ] `TeamPipeline` 多 Agent 分工：**用户裁决记入 backlog**——"管家 leader + 设备/记忆/日程 member"为后续版本立项项，V3.7 不做。
- [x] 双轨 middleware：评审定稿 **C+ 不建** `file_memory.py`/`mindmemos.py` middleware——file 轨注入本就在 assembler，recall 时机经 `rearm_recall_after_compaction` 内联（见 design-phase3-memory-middleware.md）。
- [x] 删除清单：`providers/llm_client.py`+`providers/transports/`、`agent/generic_runtime.py`、`agent/turn.py` 已删（步骤 8-9，`b3a90e5`）；`HOMEMASTER_ENGINE`/`provider_client.engine` 一并移除。`tools/executor.py` 整体保留（HomeToolAdapter 消费，AS 无等价物）；`mcp/client.py` 按 Phase-0 矩阵裁决保留自有 client。**substrate 适配器：用户裁决"排期做原生迁移"**——HM 工具迁 AS 原生 `ToolBase` + `RunContext.deps` per-call 桥随适配器一同下线，列为后续版本项（见裁决记录）。
- [x] 清理 `terminal_command_protocol_results` 等字符串路由（context_projection 已裁至仅 `project_model_tool_context`）；`RunContext.deps` 残留清理并入原生迁移项。
- [x] 反向 import 审计：`scripts/guard_no_legacy_terms.py` + 架构边界测试钉住 `REMOVED_DEAD_PATHS`。
- [x] `ApplicationRuntime` → `SessionOrchestrator` 瘦身：runtime.py 1212→~510 行，职责切分为 `runtime.py`（turn/generation fence/cancel/status/compact/aclose 所有权关闭序/最终 commit+save）、`run_driver.py`（per-run `RunDriver`：tool view→provider→assembler→executor→AS agent→recall 装配，`commit_fn`/`save_fn` 回调保持 fence 与 scope 内排序）、`extension_lifecycle.py`（`ExtensionLifecycle`）、`memory_recall.py`（`AutomaticRecallService`）。per-run 构造保公开属性重绑契约；`_browser_run_scopes` 留 runtime 持关闭序；`_FencedAgentSession`/`AutomaticRecallRunDeadlineExceeded` re-export 兼容。死代码 `Deadline`/`_backend_generation`/未读 registry 预计算一并删（见 design-session-orchestrator.md）。

验收门：

- 完整回归 + Feishu/Web 真通道 smoke + ALFWorld episode 端到端（worker IPC 不动）。
- 删除清单逐项有"最后一个调用方"证据（grep 为零 + 测试全绿）。

## 全局验收边界

| 不变量 | 验证方式 |
|---|---|
| 每 Phase 末系统可运行 | 全量 pytest + 一条端到端 run |
| 安全语义零回退 | 故障路径注入（deny/confirm-timeout/outcome_unknown/TargetChanged/stale generation）逐实例断言；禁止 best/any 聚合 |
| 消息往返无损 | substrate round-trip 性质测试（含 image/tool_call/data/provider_metadata/backend_attempted/observation_of_tool_call_id/nested thaw） |
| 环境可移植 | 干净 clone + `uv sync --frozen` + wheel 空 venv manifest 相等 + 源码外 `import agentscope` + 一条 smoke run |
| 事件投影不泄字段 | 公共 allowlist 单测 + 真实 outbound 序列断言无 duplicate final + `AgentEvent`→投影等价 golden |
| 真实 envelope | provider-facing tool_result `content` 内 `backend_attempted`/receipt/observe 图片 block 数逐实例断言 |
| 外部终态黑盒门 | 断言真实外部终态变化 + 外部返回码，不接受内部 trace |
| 去重纪律 | 同 ID 失败重投成功、成功重投拒绝分别测试 |
| 关停纪律 | absolute deadline + `wait` 硬上限 + orphan channel 出站 socket 消失 |
| 信号纪律 | 真实 SIGINT 注入耗时 finalizer/close 边界 |
| stderr 门 | terminal output 之后 stderr 无 traceback |
| tenant 边界 | fixture tenant_id≠subject_id，真实 partition 读写 |
| 迟延 import | `sys.modules` 白名单断言 core-only 下未启用子包不被拉起 |
| 反回声 | 双跑等价之外补一条真实 provider smoke（同源验证不算独立证据） |

## 交接与排期建议

1. Phase 0 是最小可证伪单元，先做；spike 若证明许可链塞不进 middleware hook，回设计 §1.1 重新选型（降级方案：保留 `AgentRuntime` 壳、仅换 model/message 层）。
2. alfworld/adapter.py 拆分独立排期，不与本计划并行合入。
3. 每 Phase 一个 PR/分支；merge 前过本文件验收门。

## 实施偏差记录（Phase 1/2 落地对照）

- middleware 未按文件名拆 5 个文件：`middleware_runtime.py` 收敛为
  `ContextAssemblyMiddleware`（on_model_call 改写 + 反应式压缩重试）、
  `ProviderObservabilityMiddleware`（transport.* 三相 + attempt record，
  流式 async-gen 包装）、`ObservationBarrierMiddleware`（屏障裁剪/prompt
  注入 + 批量协议围栏 + 自动观察重试循环 + 手动观察记账）。物理执行链
  按裁决③留在 HM executor 内（AS 层只见普通本地工具），故无独立
  `physical_safety.py` middleware——`RunScopeMiddleware` 只做
  tool_call_id contextvar 桥接。
- `HomeToolAdapter` 落在 `substrate/toolkit.py`（非 `agents/tools/base.py`），
  HM `BaseTool` 元数据经 `result_to_chunk` 的 `metadata["hm"]` 侧袋透传。
- `ApplicationRuntime` 未瘦身成 `SessionOrchestrator`：`_execute_run` 内
  按 `chat_model()` duck-type 分支到 `AsAgentRuntime`，`generic_runtime.py`
  保留至 Phase 3 删除清单统一处理。
- AS `ReActConfig.max_iters = max_tool_iterations + 观察宽限(8)`；正常迭代
  计数由 shell 侧 `normal_iterations` 执行（屏障/unconsumed 轮次免费），
  `exceed_max_iters` 仅作兜底映射。
- 快照 schema-v2：`build_snapshot_payload(engine_state, run_state, …)`，
  pending `ModelObservationBarrier` 与 `unconsumed_observation_tool_call_id`
  随 `agent_state` 子树往返（resume 回归覆盖）。
- `web/app.py` 未直接改：provider 暴露 `chat_model()` 时 AS 引擎经
  `ApplicationRuntime` 传递生效，事件仍以 `RuntimeEvent` 投影。
- MCP 裁决④已收口：`homemaster/mcp/*` 保留为唯一传输层，验收断言
  `tests/homemaster/test_substrate_mcp.py`（真 stdio server 经
  HomeToolAdapter 全链）。
- vendored patch list：①`middleware/__init__` lazy 化（防 extras 依赖
  import 泄漏）；②`stop_reason`/`done_reason` 保真进 `ChatResponse.
  metadata`（openai_chat/anthropic/ollama 共 5 处）。
