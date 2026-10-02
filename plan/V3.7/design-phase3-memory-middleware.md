# Phase 3 设计：记忆双轨 —— 评审后定稿 C+

状态：**已定稿**（独立评审驳回草案 A，采纳修正后 C+）。对应
`agentscope-migration-design.md` §5.3 "双轨记忆注入边界（两轨并存已锁）"。
评审发现的两处**已出货回归**已随本定稿一并修复。

## 评审裁决摘要（subagent 439aab61）

草案 A（middleware 拥有 recall 时机）被驳回，核心原因：

1. **事实错误**：`AsRunHandle` 没有 generation/SessionRuntime/request.text/
   save 通路，候选 A 依赖的 plumbing 全部未在草案中枚举（评审 H2）。
2. **错误分类漂移**：middleware 内 recall 异常会落进 `_drive_reply` 的
   `except TimeoutError/Exception`——`AutomaticRecallRunDeadlineExceeded`
   被吞成 `deadline_exceeded` 结果、`SessionGenerationError` 被吞成
   `transport_error`，与 app 层契约（`pytest.raises` /
   stale_generation→CANCELLED）不符（评审 H3）。
3. **性价比失真**：同 run 重召回不是 A 独有——C+（回调内联召回）~1/5
   工作量、双引擎同语义、顺带修复出货回归。

## 修复的出货回归（本 commit 落地，先于一切选型）

**H1（已修）**：AS 路径 `on_compaction`/`context.compaction` 原本只在
reactive 重试路径触发；`_assemble` 路径的阈值/手动压缩什么都不发——
`require_recall` 在 AS 上**静默不重挂**。修复：
`ContextAssemblyMiddleware._assemble`/`_compact` 统一走
`_notify_compaction`（legacy 形状 `{trigger, after_tokens, kind}` 事件
+ 回调，顺序与 generic_runtime 一致）。测试：
`test_as_runtime_manual_compaction_emits_event_and_callback`。

**H5（已修）**：AS 原生 `compress_context()` 每轮在 `_reply_impl` 跑，
`trigger_ratio=0.9` 处仍会调 `_compress_context_impl`（含图片裁剪 +
额外 `generate_structured_output` 模型调用）静默改 `state.context`/
`summary`——无事件、无审计、摘要还因 `_render` 重建而永不到模型。
修复：`ContextAssemblyMiddleware.on_compress_context` 短路围栏（不调
`next_handler`，impl 永不执行；覆盖自动压缩与 CompressContext 工具两条
路）。测试：`test_as_runtime_native_compress_context_fenced`（spy 断言
impl 零调用）。

**M4（已修）**：`provider_attempt_context_binder` 在 AS 路径从未被调用
→ `mindmemos_feedback` 恒 `memory_feedback_context_missing`。修复：
`ProviderObservabilityMiddleware.on_check_permission` 在权限边界（唯一
确定的 pre-dispatch 点——Msg 落 context 晚于 ToolCallStart 消费，shell
侧 watermark 来不及）按轮去重绑定整批 `ToolCall` + `last_frozen_messages`
（`_render` 处深拷贝缓存，与 legacy `frozen_messages` 契约对齐）。
测试：`test_as_runtime_provider_attempt_context_binder_fires`（断言
bind 先于 exec、canonical ToolCall 形状、frozen 非空）。

**H3（已修）**：`AsAgentRuntime.run(propagate_exceptions=...)` 新 kwarg——
列出的异常类型在清理后原样上抛，不进 deadline_exceeded/transport_error
分类。应用层传入 `(SessionGenerationError,
AutomaticRecallRunDeadlineExceeded)`——保留 legacy 的
stale_generation→CANCELLED 与 recall-deadline 上抛契约。测试：
`test_as_runtime_propagate_exceptions_surface_raw`。

## 定稿方案 C+（已实施）

**召回时机保持在 app 层**（`_execute_run` 的 `_automatic_recall`，
engine-agnostic），**`rearm_recall_after_compaction` 回调从"只挂 flag"
扩展为"挂 flag + 内联召回 + 内联绑定"**：

```python
async def rearm_recall_after_compaction(_metrics):
    runtime.require_recall_after_compaction(generation)      # generation-fenced
    (attempted, mem_ctx, recalled) = await self._automatic_recall(...)
    bind_automatic_memory_context(mem_ctx)                    # assembler prelude
    bind_automatic_recalled_memories(recalled)
    run_context.deps["automatic_recalled_memories"] = recalled  # 工具面写回
    if attempted: await self._save_if_configured(...)           # 崩溃一致性
```

- 双引擎同语义：`on_compaction` 回调对 legacy/AS 都是同一闭包（H1 修复后
  AS 才会真的调到它——这也是评审指出的隐藏前置）。
- 注入通道不动摇：记忆文本仍只经 assembler（frozen_memory_context →
  system prompt parts；bind_automatic_* → prelude）。`_render` 重建
  messages[0] 时自动携带。
- 已知时序语义（记录在案）：绑定落到**下一次** `prepare`，不是触发
  compaction 的那次调用本身——prepared 在回调前已建好。
- flag 归属：`SessionRuntime.require_recall`/`consume_recall`/
  `require_recall_after_compaction` 保持 runtime-owned、持久化、
  generation-fenced，**不改快照 schema**（评审 open-Q1 裁决）。
- 取消/deadline：复用 `_await_with_remaining_deadline`（
  `handle.scope.deadline`/`executor.deadline` 同对象），
  `AutomaticRecallRunDeadlineExceeded` 经 propagate_exceptions 原样上抛。
- `FileMemoryMiddleware` 不建——file 轨注入已在 assembler
  （`FrozenMemoryContextService` 每 prepare 重取快照），middleware 化是
  空壳 YAGNI（评审确认草案此判断正确）。
- finalizer（session-end 写回）保持 app-owned——middleware 不持有 session
  生命周期（评审 open-Q4 确认无争议）。**§5.3 修正备案**：原锁定设计
  写过 `FileMemoryMiddleware`、HintBlock 注入、middleware-owned
  finalizer hook——本条记录三项均不采纳（HintBlock 不进 canonical
  history；finalizer 留 app 层）。
- middleware 边界**推迟**：等出现第二个"引擎生命周期内的记忆行为"再立
  （评审推荐的 D 变体：flag + `on_reasoning` 每轮消费——若届时实施，
  需要的 plumbing 已在 H2/H3 修复中铺好）。

## 事实更正（评审 L1/L3）

- 记忆工具名：`context_memory`（file 轨）+ `mindmemos_add/search/
  history/update/delete/feedback` + 域内 `memory_writer`；草案中
  `memory_add`/`memory_link` 为误写。全部经 `HomeToolAdapter →
  execute_for_substrate` 进 AS 链，无需迁移。
- `implementation-plan.md:55` 曾误标 `file_memory.py`/`mindmemos.py`
  middleware `[x]`——已更正为 C+ 落地描述。
- `ComposedContext.automatic_recalled_memories`（context.py:325/500/611）
  目前 write-only——deps 通路才是真消费面；记录在案待后续清理裁决。

## 验证门（已过）

- `test_as_runtime.py` 24/24 绿（含 4 个新测试钉死 H1/H5/H3/M4）。
- 定点回归 `test_substrate_*` + `test_as_runtime` + `application/`
  118/118 绿；ruff 全过。
- app 级端到端已落地：
  `test_app_runtime_agentscope_post_compaction_recalls_inline`——
  MindMemOS 桩 + 小窗口 + `_FailOnceModel` context-length 错误 →
  reactive `"aggressive"` 压缩 → 断言同一 run 内恰好两次 `search`
  调用、`context.compaction` 事件、`memory.automatic_recall` ×2、
  recall flag 消费归零。
