# 裁决⑥：Phase 2 出口裁决 — AgentScope 引擎壳收口

状态：已裁决
依据：`implementation-plan.md` Phase 2 验收门逐条对照实测结果（远端
worktree `Homemaster-v35`，v35-architecture 分支）。

## 落地形态

`AsAgentRuntime`（`substrate/runtime.py`）驱动 vendored
`agentscope.Agent.reply_stream()`；HM runtime 契约由三个 middleware +
shell 投影层共同保住：

| HM 语义 | AS 落点 | 证据 |
|---|---|---|
| 取消 / SIGINT | `InterruptController` → `_StreamAbortShim.close()` 取消挂起的 `__anext__()` task，runtime 再 `aclose()` 并 join | `test_as_runtime_sigint_aborts_model_call`（真 SIGINT） |
| deadline | `_await_with_deadline`（绝对剩余 + ensure_future + wait + 显式 cancel/join） | `test_as_runtime_cancel_with_deadline` |
| 每迭代 context 组装 / 压缩 | `ContextAssemblyMiddleware.on_model_call` 重写 `messages`/`tools`；context-length 错误触发反应式压缩重试 | wiring + e2e |
| transport 三相 commit | `ProviderObservabilityMiddleware`：request_started/response_completed/request_failed + `ProviderAttemptRecord`；流式响应包装到消费完才记 latency/usage | 全部 AS 测试的 transport.* 断言 |
| 许可 / 物理链 | `HomeToolAdapter.call` → HM `ToolExecutor`（含 `ApplicationToolExecutor.execute_for_substrate` 单发缝）；`check_permissions` 委托 `permission_checker.evaluate_tool` | permission_denied 回 `error_code=permission_denied`；app 级 device.* 能力门 |
| 观察屏障 | `ObservationBarrierMiddleware`：pending 时工具裁剪到 observe + `on_system_prompt` 注入提示；`on_check_permission` 批量/协议围栏；`on_acting` 后钩跑 `MAX_OBSERVE_FAILURES` 重试的 runtime-owned auto-observe；barrier 清除/手动观察记账 | `test_as_runtime_automatic_observation`、`test_as_runtime_pending_barrier_resume`、app 级 `automatic_observation` |
| 预算 | `ReActConfig.max_iters = max_tool_iterations + 8`（宽限），shell 用 `normal_iterations` 只计非 followup 轮 | budget_exhausted 映射测试 |
| 快照 | schema-v2：`build_snapshot_payload(engine_state, run_state, task_state_store, …)`；屏障/`unconsumed` 随 `agent_state` 子树往返 | `test_as_runtime_resume_from_v2_snapshot`、resume 测试 |
| session 镜像 | 引擎 context → `from_agent_scope` 单向投影（水位线防重/防漏 assistant.reply） | dual-run 等价 + wiring 测试 |

## 过程中的真实缝（已修）

1. `_round_schemas` NameError 让 auto-observe 抛 ExceptionGroup → 改用
   `observation_tool_name(handle.all_tool_schemas)`。
2. `auto-observe-*` runtime 自有调用会被自家 `on_check_permission` 当
   mixed batch 拒掉 → 按 id 前缀豁免。
3. `ToolCallBlock.input` 是 JSON 字符串不是 dict → 建块时 `json.dumps`。
4. `on_check_permission` 里 `call_id` 未定义 → 补上。
5. `ApplicationToolExecutor` 缺 `permission_checker` 属性 → 委托到内层
   `ToolExecutor`。
6. `ExceptionGroup` 吞真错误 → `_flatten_error` 展开叶子进 turn_failed。
7. `HintBlock`（AS 每轮注入的 runtime-state 提示）投影告警刷屏 → 降为
   DEBUG（引擎内部注释，每轮重算，非 canonical 内容）。
8. vendored `src/agentscope` 触发 legacy-terms 守卫 → SKIP_DIRS 加排除。
9. HM `unconsumed_observation_tool_call_id` 语义修正：auto 路径记 source
   action id（非 observe call id）；屏障清除的 observe 结果盖
   `observation_of_tool_call_id` 戳。

## 与 HM 语义仍有意的差异

- AS 事件序：`ModelCallEndEvent` 可先于 assistant Msg 落 context → 用
  watermark 重扫代替"读 context[-1]"。
- 自动观察的 `tool.call_*` 事件由 middleware 自发（AS 引擎不会为嵌套的
  `call_tool` 发 acting 事件）——保持 HM 事件面可见性。
- `max_tool_iterations=None` 时 AS 默认 50 封顶；HM 原语义是无界。MVP
  接受（HM 侧该值始终有数）。

## 出口结论

Phase 2 验收门达成：AS 引擎在 `ApplicationRuntime` 生产路径上可用且
HM 安全语义未被旁路。`generic_runtime.py` 保留为回退位，删除清单留
Phase 3（`implementation-plan.md` 已勾完对应项并附偏差记录）。

回归账：远端全量 1633 passed / 33 failed —— 逐条归因全部落在
环境或基线（用户级 Neo4j 占 7687 → memory/mcp/cli_streaming 段；
v19 缺兄弟仓 OpenHarness；web×10 基线 `metadata=` harness；
browser/v20 各 1 个 flake）。substrate 涉及目录定点 295/295 绿。

收口后 lint 清零（`ed0a597`）：`ruff check src/homemaster tests
scripts` 全过——assistant 分段还原抽出为模块级
`_assistant_from_as`（消 B023），`ToolResultMessage` 走
TYPE_CHECKING 注解，其余为机械 import/注解修复。lint 后定点回归：
远端 substrate+application 236/236 绿，本地含 permissions/agent
全套 355/355 绿，`guard_no_legacy_terms` exit=0。

## 独立 review 加固轮（export 后追加）

实现完成后以**全新上下文**跑了一轮独立 code review（runtime /
middleware / 持久化 / 接线四面），报出 7 项 HIGH。逐条真机核实后
全部修复——每一条都能在真实序列化信封/引擎上下文上复现，不是风格
问题：

| # | 发现 | 修法 |
|---|---|---|
| H1 | `messages[0]` SystemMsg 被整体替换丢弃——HM system prompt 在生产路径丢失 | `ContextAssemblyMiddleware._render` 保留 assembler 的 system prompt 为 head，AS 自身 prompt 仅作子集 |
| H2 | `ApplicationRuntime._stop_condition` 遇 `data=None` 崩 | None/Mapping 守卫 |
| H3 | attempt record 字段与 `ProviderAttemptRecord` 契约不符 → JSONL sink 每次调用必炸 | `_record_attempt` 用真实契约构造；新增 `test_as_runtime_jsonl_attempt_sink` 黑盒断言 |
| H4 | auto-observe 证据只写在 transient `ToolResponse`，进不了 canonical context | post-hook 双写：`agent.state.context` 里已持久化的 `ToolResultBlock` + 瞬态响应；`test_as_runtime_observation_lands_in_engine_context` 断言引擎终态 |
| H5 | 汇编用 `prepare()` 会丢弃 coroutine（assembler 是 async） | 统一 `_maybe_async` 走 `aprepare()` |
| H6 | 反应式压缩重试在流式下是死代码：`_call_api` 返回惰性 asyncgen，异常推迟到首个 `__anext__`；且重试复用了旧 messages | 拆两段：eager `await next_handler()` 捕获 + `_stream_with_reactive_retry` 包装消费——**只在首 chunk 之前**重试，mid-stream 失败直接失败不重放 |
| H7 | 取消/deadline 在 tool 执行中炸会留悬空 `tool_call`（无配对 `tool_result`）→ 快照恢复后 provider 拒未配对 tool_use | `runtime.py` 加 `_close_stream`/`_close_dangling_tool_calls`/`_fail_observation_fatal`；SIGINT-during-tool 测试断言引擎上下文内 call_ids==result_ids 且 state=interrupted |

另补：

- `ToolResultState.INTERRUPTED` 计入 runtime 错误态分类（原只认
  error/denied）。
- vendored `_agent.py` 给 denied/interrupted 结果块持久化 `hm` 袋
  （`backend_attempted=False`、`status`），`ToolResultEndEvent` 同步带
  上——否则 canonical `ToolResultMessage.data` 退化为 None。
- vendored `_anthropic_formatter.py`：空/仅错误的 tool_result 落
  `(empty tool output)` 占位（Anthropic 拒空 content），非 success
  态标 `is_error`，含 tool_result 的消息强制 `role=user`。
- `SessionPersistenceManager` v2 分支补 `created_at` 与
  `preserve_image_tool_call_ids`（快照往返丢字段会让屏障恢复后图片
  被剥）。
- `result_to_chunk` 的 `hm.data` 对齐 `ApplicationToolExecutor._message`
  合并语义（`status`/`backend_attempted`/`error_code` 并入
  canonical data，冲突时 `domain_status` 让位）。
- Agent 构造显式钉 `ContextConfig(compression_tool_enabled=False,
  compression_fallback_to_truncation=False)` +
  `InjectionConfig(inject_runtime_state=False)`——关掉 AS 原生压缩/
  图片裁剪/runtime-state 注入，HM assembler 是唯一上下文权威
  （`test_as_runtime_native_context_transforms_disabled` spy 构造参数）。

新增对抗测试（`test_as_runtime.py`，+7 项）：assembler 接线断言
system prompt 落 messages[0]、eager/惰性流式两条压缩重试路径、
mid-stream 失败不重试、denied 结果 canonical data、JSONL attempt
sink 端到端、SIGINT 打中 tool 执行时无悬空 tool_call、观察证据落
engine context、native transform 关闭断言、Anthropic 空结果格式化。

加固后定点回归：`tests/homemaster/test_substrate_* +
test_as_runtime + application/` 211/211 绿，ruff 全过。

## 待办

- 第二个独立 review subagent（针对本轮修复的复核）仍在后台跑，结论
  未出；若再报新缺陷按同一纪律处理。
- Phase 3：删除 `generic_runtime.py` 回退位、CLI/Web 面改造、
  MindMemOS 深度集成——见 `implementation-plan.md`。

## 协议围栏归属裁决（加固轮期间核实，已移植）

legacy `generic_runtime` 在模型层有三道协议围栏，其中两道是**原子
拒批**（一批里有一个不可用/越权调用 → 整批拒，附带调用拿
`tool_batch_contains_*`）。evidence-map 审计发现 AS per-call 报错会让
有效附带调用先执行（部分副作用）——已按 legacy 语义移植，不再只是
fail-closed 等价：

| 围栏 | legacy 位置 | AS 路径落点 | 状态 |
|---|---|---|---|
| 观察屏障批量/协议 | 循环内联 | `ObservationBarrierMiddleware.on_check_permission` | 已全对齐 |
| unknown tool 原子拒批 | `unavailable_tool_protocol_results` | `ProtocolFenceMiddleware`（`on_model_call` 记 offered 集 + `on_check_permission` 拒整批） | **已移植** |
| terminal allowlist 原子拒批 | `terminal_command_protocol_results` | 同上，`exact-match` 校验全部 terminal 调用 | **已移植** |

移植要点（`ProtocolFenceMiddleware`，middleware_runtime.py）：

- `on_model_call` 在 `ObservationBarrierMiddleware` 之后记录本迭代实际
  offered 工具名集（屏障裁剪后的真实集合，与 legacy "当前可用"语义一致）。
- `on_check_permission` 取当前轮全部 tool_call 做两阶段判批：先判
  unavailable（offered 集之外），再判 terminal 越权（`allowed_terminal_commands`
  精确匹配）；任一污染 → 整批每个调用都被 DENY。
- 豁免：runtime-owned `auto-observe-*` 调用与不在当前轮的调用不参与判批。
- DENY `message` 直接携带 legacy 同款 JSON payload
  （`status/protocol_blocked`、`error_code`、`rejected_tool`、
  `unavailable_tools`、`message`），模型面逐字节对齐；事件保留
  `tool.protocol_rejected` / `terminal.command_protocol_rejected` 原名。

已记录的等价分歧（接受，不影响安全语义）：

1. 从未 offered 的调用本身到不了权限链（AS 先查 tool 解析，无名工具无
   tool 对象 → `Toolkit.call_tool` 原生 `ToolNotFoundError` error result）。
   该调用仍不出活、不触后端；批量原子性由对附带调用的 DENY 保证。
2. 被 DENY 的结果 canonical `data.status` 是 `"denied"` 而非 legacy 的
   `"protocol_blocked"`；`error_code` 进 content JSON 与事件 payload，
   不进 `ToolResultMessage.data`（核实过 `.data.error_code` 无下游消费者）。
   `backend_attempted=False` 在 `data` 与事件中均保留。
3. `_hm_denial_data` 临时属性通道被否掉并回滚：AS `_check_permission`
   深拷贝 ToolCallBlock，middleware 改的副本到不了 `_handle_error_tool_call`
   使用的原对象。最终走 denial message 携带 payload——这是非拷贝敏感的
   运输路径。

测试：`test_as_runtime.py` 批量原子性（伴随调用零执行）、terminal
白名单拒批、违反者 vs 伴随调用 error_code 区分、真 serialized envelope
断言。定点回归 216/216 绿（`test_substrate_*` + `test_as_runtime` +
`application/`），ruff 全过。提交 `0907ede`。
