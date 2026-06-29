# 用户中断机制 Spec

Date: 2026-06-27

关联：[audit-and-refactor-spec.md](audit-and-refactor-spec.md) 11.9
依赖：[session-persistence-spec.md](session-persistence-spec.md)（SIGINT 触发 save_snapshot 已在该 spec 定义）
依赖：[providers-refactor-spec.md](providers-refactor-spec.md)（LLM streaming 用 SDK，支持 `stream.close()` 中断）

---

## 一、问题现状（已验证）

### 1.1 audit 11.9 原文

"长程任务运行时，用户没有办法中途打断并获取当前进度。没有 SIGINT handler、没有 `/stop` 命令，只能 kill 进程，但 kill 后什么都没有（无 checkpoint）。"

### 1.2 当前代码验证

| 检查项 | 结果 |
|--------|------|
| `signal.SIGINT` handler | ❌ 未注册（grep `signal\.` 零结果） |
| `KeyboardInterrupt` 处理 | ❌ 未捕获（agent loop 无 try/except KeyboardInterrupt） |
| `/stop` 命令 | ❌ 不存在 |
| `cancellation_token` 字段 | ✅ `RunContext.cancellation_token` 已定义（`normalized.py:19`），但**从未被检查** |
| `cancelled` 状态 | ✅ `AgentRunStatus` 已有 `"cancelled"`（`state.py:14`），但**从未被设置** |
| `task_state.CANCELLED` | ✅ 已定义（`task_state/models.py:17,25`），但只用于工具结果，不是中断 |

### 1.3 session-persistence spec 已覆盖部分

| 项 | 已覆盖 |
|----|--------|
| SIGINT 触发 `save_snapshot` | ✅ `session-persistence-spec.md:187, 500, 527, 562` |
| `save_on_sigint: bool = True` 配置 | ✅ `session-persistence-spec.md:500` |

**本 spec 补充**：SIGINT 在 agent loop 哪个阶段生效、中断后状态如何标记、resume 如何处理中断的 session。

---

## 二、设计决策

### 2.1 决策 S3：混合中断策略

**LLM 调用中 → 立即停**；**工具执行中 → 等当前工具完成**；**迭代间 → 立即停**。

| 阶段 | SIGINT 行为 | 理由 |
|------|------------|------|
| A. LLM 调用中（`transport.stream`） | 立即中断 stream，返回 `cancelled` | 流式可中断，已生成 token 用户不要了，无副作用 |
| B. 工具执行中（`_dispatch_tools`） | 不打断当前工具，完成后检查标志位退出 | 工具可能操作外部世界（机器人动作），打断有风险 |
| C. 迭代间（工具结果 append 完，下次 LLM 前） | 立即退出 | 安全点，无副作用 |
| D. 等待用户输入（`run()` 已返回） | 默认 SIGINT 行为（退出 CLI） | 不在 agent loop 内，由 CLI 处理 |

**实现机制**：

```python
class InterruptController:
    """SIGINT 协调器：根据当前阶段决定立即停还是优雅停。"""
    def __init__(self) -> None:
        self.cancelled: bool = False
        self._current_stream: Any = None   # LLM streaming 时设
        self._in_tool: bool = False         # 工具执行中设

    def handle_sigint(self, signum, frame) -> None:
        self.cancelled = True
        if self._current_stream is not None:
            self._current_stream.close()  # SDK stream 中断
        # _in_tool 时不做事，让工具自然完成

    def set_stream(self, stream) -> None:
        self._current_stream = stream

    def clear_stream(self) -> None:
        self._current_stream = None

    def enter_tool(self) -> None:
        self._in_tool = True

    def exit_tool(self) -> None:
        self._in_tool = False
```

**线程安全说明**：signal handler 在主线程执行（Unix 语义），与主线程的 tool 执行是串行的（GIL）。`_current_stream` / `_in_tool` / `cancelled` 是普通属性赋值，GIL 保证原子性。`stream.close()` 在 handler 里调用时，主线程可能正在迭代 stream——依赖 SDK 的 `close()` 实现是线程安全的（anthropic/openai SDK 的 `Stream.close()` 是线程安全的）。

**不用 `StreamAbortedError`**：`stream.close()` 后，`with` 块的 `__exit__` 正常处理清理，不抛自定义异常。主线程在循环内通过 `controller.cancelled` 标志检测中断并 break。不需要引入 `StreamAbortedError` 异常类。

**generic_runtime.py 接入**：

```python
def run(self, session, user_text, ...):
    controller = InterruptController()
    old_handler = signal.signal(signal.SIGINT, controller.handle_sigint)
    try:
        while ...:
            if controller.cancelled:
                return self._cancel_result(session, run_id, events, phase="iteration_boundary")

            # LLM 流式调用——通过 LLMClient.stream()（不直接调 SDK）
            # LLMClient.stream() 是 generator，内部用 `with sdk_stream:` 块
            # generator close() 时 GeneratorExit 触发 with __exit__，SDK stream 自动关闭
            stream_gen = self._llm_client.stream(messages, tools=..., ...)
            controller.set_stream(stream_gen)
            try:
                deltas = []
                for delta in stream_gen:
                    if controller.cancelled:
                        break  # signal handler 已设 cancelled，退出循环
                    deltas.append(delta)
                assistant_msg = LLMClient._aggregate_deltas(deltas)
            finally:
                controller.clear_stream()
                stream_gen.close()  # 确保 generator 退出（触发 SDK stream 关闭）

            if controller.cancelled:
                return self._cancel_result(session, run_id, events, phase="llm_call")

            # 工具调度
            for tc in tool_calls:
                if controller.cancelled:
                    break  # 不开始下一个工具
                controller.enter_tool()
                try:
                    result = self._dispatch_one(tc)
                finally:
                    controller.exit_tool()
    finally:
        signal.signal(signal.SIGINT, old_handler)
```

**不需要 `StreamAbortedError`**：`stream.close()` 后 SDK 的 `with` 块 `__exit__` 正常清理，不抛异常。主线程通过 `controller.cancelled` 标志在循环内 `break` 退出。不需要引入 `StreamAbortedError` 异常类。

**SDK stream.close() 行为风险**：`anthropic` / `openai` SDK 的 stream 对象在被 `close()` 后，`with` 块 `__exit__` 正常清理，不抛异常。主线程通过 `controller.cancelled` 标志在循环内 `break` 退出，不依赖异常。**需 live_api 早期验证**（见 §5.2）确认 `stream.close()` 不会阻塞或 hang。若 SDK 不支持 `close()`，退回 S2（handler 只设标志位，等 LLM 自然结束）。

### 2.2 决策 T1：仅 Ctrl+C

**只支持 SIGINT（Ctrl+C）**，不引入 `/stop` 命令。

**理由**：

- `/stop` 只能在"等待用户输入"阶段生效（agent loop 跑时用户没法敲命令）——而那时直接按 Ctrl+C 更直接
- CLI 是当前唯一入口，Ctrl+C 是 CLI 用户的中断直觉
- 未来飞书/微信 gateway 接入时，再为消息渠道设计 `/stop` 等命令（属 gateway 范畴，不在 V1.6）

**SIGINT 语义统一**：无论哪个阶段收到 SIGINT，都视为"用户请求取消当前 run"。

### 2.3 决策 U3：新增 `paused` 状态

**task_state 新增 `paused` 状态**，区分"用户主动暂停" vs "任务失败取消"。

| 状态机 | 触发 | task_state.status | run status |
|--------|------|------------------|------------|
| 用户 SIGINT 中断 | task_state 当前 `active` → `paused` | `paused` | `cancelled` |
| 用户 SIGINT 中断 | task_state 当前非 `active`（如 `completed`） | 不变 | `cancelled` |
| 任务失败 | `active` → `failed` | `failed` | `failed` |
| 任务正常完成 | `active` → `completed` | `completed` | `replied` / `completed` |
| Resume 恢复 | `paused` → `active` | `active` | `running` |

**`task_state/models.py` 改动**：

```python
class TaskStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"        # 新增
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
```

**工具 schema 同步**（`task_state/tools.py:177`）：

```python
"enum": ["active", "paused", "completed", "failed", "cancelled"]
```

### 2.4 中断后的保存与 resume

**SIGINT handler 语义统一**：**SIGINT handler 只设 `cancelled=True` + `stream.close()`，不调 `save_snapshot`**。理由：signal handler 里做 JSON 序列化 + fsync 有 reentrancy 风险（handler 可能在任意点执行，包括持有锁时）。

**保存**（与 session-persistence-spec 协同）：

1. SIGINT handler 设 `cancelled=True` + `stream.close()`（仅此）
2. `_cancel_result` 设置 `run status = "cancelled"` + `task_state.status = "paused"`
3. `_cancel_result` 内部调用 `save_snapshot`（或由 `run()` 返回后 CLI 调——具体由 session-persistence-spec §4.1 决定）

**Resume**（与 session-persistence-spec §5 协同）：

- `hm --resume <session_id>` 加载 session.json
- 检测 `task_state.status == "paused"` → 自动转回 `active`
- 恢复对话历史，等用户输入（不自动续跑，遵循 session-persistence-spec §5.2 的"恢复到最稳定状态"语义）
- 用户输入新消息后，agent loop 从当前 messages 继续

**Resume 不自动续跑**的理由：用户中断是因为"不想等了"或"想改方向"，自动续跑违背用户意图。让用户决定下一步。

---

## 三、`_cancel_result` 实现

```python
def _cancel_result(
    self,
    session: AgentSession,
    run_id: str,
    events: list[RuntimeEvent],
    *,
    phase: str,
) -> GenericRunResult:
    """构造中断后的 run result。"""
    emit = ...  # 复用 run() 内的 emit 闭包
    emit("runtime.cancelled", payload={"phase": phase})

    # task_state: active → paused
    # 注：task_state_store.update_status 方法需新增（见 §7 影响范围）
    run_context = getattr(self._tool_executor, "_run_context", None)
    if run_context is not None:
        task_state_store = run_context.deps.get("task_state_store")
        if task_state_store is not None:
            snapshot = task_state_store.snapshot
            if snapshot is not None and snapshot.status == TaskStatus.ACTIVE:
                task_state_store.update_status(TaskStatus.PAUSED)

    return GenericRunResult(
        run_id=run_id,
        status="cancelled",
        session=session,
        events=events,
        error_code="user_interrupted",
    )
```

**关键点**：

- 只在 `task_state.status == active` 时转 `paused`，避免误改已完成/已失败的任务
- `error_code="user_interrupted"` 区分于失败（`error_code` 为 None 或具体错误）
- `phase` 字段记录中断发生在哪个阶段，写入 trace 便于 debug

---

## 四、CLI 接入

### 4.1 CLI 入口（`cli/interactive_shell.py`）

**注意**：当前 REPL 在 `cli/interactive_shell.py`（不是 `run_command.py`）。改动挂在 `interactive_shell.py`。

```python
def run_interactive_shell():
    setup_logging()
    config = load_config()
    session = load_or_new_session(config)
    runtime = GenericAgentRuntime(...)

    while True:
        try:
            user_input = input("hm> ")
        except (EOFError, KeyboardInterrupt):
            # D 阶段：等待用户输入时 Ctrl+C → 退出 CLI
            # 如果当前有活跃 task，设 paused
            task_state_store = get_current_task_state_store()
            if task_state_store and task_state_store.snapshot and \
               task_state_store.snapshot.status == TaskStatus.ACTIVE:
                task_state_store.update_status(TaskStatus.PAUSED)
            save_snapshot(session, ...)
            print("\n再见")
            break

        if not user_input.strip():
            continue

        result = runtime.run(session, user_input, ...)
        # run() 内部已处理 A/B/C 阶段的 SIGINT
        save_snapshot(session, ...)  # 最终保存
        print(result.final_reply or "[cancelled]")
```

### 4.2 SIGINT handler 生命周期

- `runtime.run()` 进入时注册 handler，退出时恢复原 handler（`finally` 块）
- CLI 主循环（等用户输入）用默认 SIGINT 行为（抛 `KeyboardInterrupt`，被外层 try/except 捕获）

**不污染全局 signal handler**：`signal.signal` 只在 `run()` 内临时替换，`finally` 恢复。其他代码路径（如 benchmarking）不受影响。

**`signal.signal` 只能在主线程调**：若 `runtime.run()` 在子线程（如未来 benchmarking 并行场景），`signal.signal()` 会抛 `ValueError`。此时跳过 handler 注册，仅依赖 `cancelled` 标志位（外部线程设 `controller.cancelled = True`）。实现：

```python
try:
    old_handler = signal.signal(signal.SIGINT, controller.handle_sigint)
except ValueError:
    # 非主线程，signal handler 不可用，仅依赖 cancelled 标志位
    old_handler = None
```

---

## 五、验证计划

### 5.1 单元测试

| 测试 | 断言 |
|------|------|
| `test_interrupt_iteration_boundary` | SIGINT 在迭代间到达 → `run()` 返回 `status="cancelled"`，`phase="iteration_boundary"` |
| `test_interrupt_tool_phase` | SIGINT 在工具执行中到达 → 当前工具完成，下一个工具不启动，`phase` 记录正确 |
| `test_interrupt_llm_phase` | mock SDK stream，SIGINT 触发 `stream.close()` → `run()` 返回 `phase="llm_call"` |
| `test_interrupt_preserves_session` | 中断后 `session.messages` 包含已完成的工具结果，无半截消息 |
| `test_task_state_paused_on_interrupt` | 中断时 `task_state.status` 从 `active` → `paused` |
| `test_task_state_unchanged_if_not_active` | 中断时 `task_state.status` 已是 `completed` → 保持不变 |
| `test_signal_handler_restored` | `run()` 返回后，`signal.getsignal(SIGINT)` 是原 handler |
| `test_resume_clears_paused` | `hm --resume` 加载 `paused` session → 自动转 `active` |
| `test_no_global_signal_pollution` | 不调用 `runtime.run()` 时，SIGINT 行为是默认的 |

### 5.2 集成验证（live_api）

| 测试 | 断言 |
|------|------|
| `test_live_interrupt_during_llm` | 真调 mimo streaming，收到第一个 delta 后发 SIGINT → `run()` 在 1s 内返回 `cancelled` |
| `test_live_interrupt_during_tool` | agent 调长时工具（mock sleep 5s），中途 SIGINT → 工具完成，`run()` 返回 `cancelled` |
| `test_live_snapshot_saved_on_interrupt` | SIGINT 后 `session.json` 真实写入磁盘，包含中断前的 messages |
| `test_live_resume_after_interrupt` | 中断后 `hm --resume <id>` 能加载 session，`task_state` 从 `paused` → `active` |

### 5.3 黑盒门（§3 纪律）

| 门 | 断言 |
|----|------|
| 外部终态 | 中断后 `session.json` 真实落盘，包含已完成的消息（不是内存丢失） |
| 返回码 | `run()` 在 SIGINT 后 1s 内返回（不是等 LLM 自然结束）——验证"立即停 LLM"真生效 |
| per-instance | LLM 阶段中断 / 工具阶段中断 / 迭代间中断 **三种场景分别**验证，不能只测一种 |
| SDK stream.close() 行为 | live_api 验证 SDK stream 被 `close()` 后真的抛异常（不是 hang 住） |

### 5.4 回退验证

若 SDK stream.close() 不支持中断（§2.1 风险）：

- 退回 S2：handler 只设 `cancelled=True`，不调 `stream.close()`
- LLM 调用会自然结束，然后在迭代边界退出
- 代价：用户感知延迟（等 LLM 自然结束，可能几秒到几十秒）
- 验证：`test_live_interrupt_during_llm` 的"1s 内返回"放宽到"LLM 自然结束后立即返回"

---

## 六、配置项

中断相关配置统一在 `ObservabilityConfig`（`config/observability.py`，由 observability-spec §8 定义）中：

```python
# ObservabilityConfig 中的中断字段（在 observability-spec 定义）
# interrupt_enabled: bool = True           # 是否启用 SIGINT 中断
# interrupt_abort_llm_stream: bool = True  # LLM 阶段是否立即中断 stream（False = S2 退回）
```

本 spec 不单独定义配置类，从 `ObservabilityConfig` 读取上述字段。

`interrupt_abort_llm_stream=False` 时退回 S2 行为（§5.4 回退）。

---

## 七、影响范围

**新增**：
- `agent/interrupt.py` ~60 行（`InterruptController` 类）
- `task_state/models.py` 加 `PAUSED` enum 值
- `task_state/tools.py` 工具 schema enum 加 `paused`
- `task_state/store.py`：新增 `update_status(status: TaskStatus)` 方法（§3 `_cancel_result` 调用）

**修改**：
- `agent/generic_runtime.py`：
  - `run()` 注册/恢复 SIGINT handler（主线程检测，非主线程跳过）
  - LLM streaming 用 `cancelled` 标志 + `stream.close()`（不引入 `StreamAbortedError`）
  - 工具调度包 `enter_tool` / `exit_tool` + 循环内 `if controller.cancelled: break`
  - 新增 `_cancel_result` 方法（含 `task_state_store.update_status` 调用）
- `cli/interactive_shell.py`（**不是 `run_command.py`**）：
  - 主循环 `try/except KeyboardInterrupt` 处理 D 阶段
  - D 阶段也设 paused（如果 task active）
  - `--resume` 加载时 `paused` → `active`
- `events/runtime_events.py`：新增 `runtime.cancelled` 事件类型（已有则确认 payload 格式）

**删除**：
- 无（`RunContext.cancellation_token` 字段保留，可复用为 `InterruptController` 的载体）

---

## 八、PR 拆分

### PR1：核心中断机制

1. `agent/interrupt.py`（`InterruptController`）
2. `task_state/models.py` 加 `PAUSED`
3. `task_state/tools.py` schema enum 加 `paused`
4. `task_state/store.py` 新增 `update_status()` 方法
5. `generic_runtime.py` 接入 SIGINT handler + 阶段感知
6. `_cancel_result` 实现（含 task_state 转换）
7. `cli/interactive_shell.py` 主循环 `KeyboardInterrupt` 处理（D 阶段也设 paused）
8. `--resume` 时 `paused` → `active`
9. `ObservabilityConfig` 新增中断字段
10. 单测 + live_api 验证 SDK stream.close() 行为
11. 文档同源更新

**实施顺序约束**：interruption-spec PR1 必须在 providers-refactor-spec PR1（真 SSE streaming）之后实施。若 providers-refactor 未落地，`stream.close()` 不可用，退回 S2（handler 只设标志位，等 LLM 自然结束）。

### PR2：无

本 spec 无 PR2 延迟项。若 §5.4 回退触发（SDK 不支持 stream 中断），则在 PR1 内退回 S2，不开 PR2。

---

## 九、与其他 spec 的协同

| spec | 协同点 |
|------|--------|
| session-persistence-spec | SIGINT 触发 `save_snapshot` 已定义；本 spec 补充中断后 task_state 状态转换 + resume 清理 `paused` |
| providers-refactor-spec | LLM streaming 用 SDK，`stream.close()` 是中断 LLM 的手段；若 SDK 不支持则退回 S2 |
| observability-spec | `runtime.cancelled` 事件 + trace 记录中断 phase |
| audit 11.9 | 直接解决 |
| audit 11.8 loop guard | 中断机制不替代 loop guard，但提供了"用户主动退出"的逃生口（loop guard 是自动兜底，中断是用户主动） |

---

## 十、评审修复记录（2026-06-27）

| # | 问题 | 修复 |
|---|------|------|
| 21 | `TaskStateStore.update_status` 方法不存在但 §3 调用 | §3 加注释标注；§7 影响范围加"新增 `update_status()` 方法"；§8 PR 拆分加第 4 项 |
| 22 | D 阶段 SIGINT 不设 paused，与 A/B/C 不一致 | §4.1 D 阶段也设 paused（如果 task active） |
| 23 | REPL 在 `interactive_shell.py` 不在 `run_command.py` | §4.1 明确改动挂在 `cli/interactive_shell.py`；§7 影响范围更新 |
| 24 | `StreamAbortedError` 异常类不存在 | §2.1 删除 `StreamAbortedError`，改为用 `cancelled` 标志 + `stream.close()`，依赖 `with` 块正常退出 |
| 25 | `_current_stream` / `_in_tool` 线程安全 | §2.1 加线程安全说明（GIL 保证原子性，SDK `close()` 线程安全） |
| 26 | `signal.signal` 只能在主线程调 | §4.2 加说明：子线程跳过 handler 注册，仅依赖 `cancelled` 标志位 |
| 27 | 实施顺序约束未说明 | §8 PR 拆分加"必须在 providers-refactor-spec PR1 之后实施" |
| 28 | §2.4 说 handler 调 save_snapshot，§2.1 只设标志位——矛盾 | §2.4 统一为"handler 只设 cancelled + stream.close()，不调 save_snapshot"，理由：reentrancy 风险 |
