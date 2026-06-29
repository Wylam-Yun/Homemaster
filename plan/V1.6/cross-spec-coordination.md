# 跨 Spec 协调决议

Date: 2026-06-27

本文件记录 V1.6 各 spec 之间冲突的统一裁决。评审发现 5 处跨 spec 冲突（A 类），各 spec 已在自身内部按本决议修复。本文件作为单一真理源，确认各 spec 对同一件事的说法一致。

---

## 决议 1：图像剥离方案（session-persistence ↔ providers-refactor）

**冲突**：session-persistence-spec §3.4 原方案把图像 `ContentBlock.type` 保持为 `"image"`，`source` 改成 `{"type": "stripped", ...}`。但 `mimo_transport.py:645-646` 会把 `type=="image"` + 任何 dict source 当真图像发给 Anthropic API，API 报错。

**裁决**：剥离时 `ContentBlock.type` 改为 `"text"`，`text` 设为占位符文本。

```python
def _strip_image_for_persistence(block: ContentBlock, *, tool_name: str, iter_index: int, args: dict) -> ContentBlock:
    return ContentBlock(
        type="text",
        text=f"[image stripped — {tool_name} @ iter {iter_index}, args={args}. See trace.jsonl for original]",
    )
```

**落地位置**：session-persistence-spec §3.4（已修）

**理由**：transport 层只对 `type=="image"` 的 block 做图像序列化，`type="text"` 的 block 当普通文本传递，不会触发 API 报错。占位符文本让 LLM 知道"这里曾有一张图，但被剥离了"，需要时可重新调 `robot_observe`。

---

## 决议 2：resume 后 agent_state 不被覆盖（session-persistence ↔ generic_runtime）

**冲突**：`generic_runtime.py:144-148` 每次 `run()` 都 `agent_state = AgentState(run_id=..., session_id=..., ...)`，外部传入的 agent_state 根本没用。session-persistence-spec §5.2 的 resume 流程无法实现。

**裁决**：`GenericAgentRuntime.run()` 加可选参数 `agent_state: AgentState | None = None`。传入则用，不传入则 new（保持现有行为）。`GenericAgentRuntime.__init__` 不变。

```python
def run(
    self,
    session: AgentSession,
    user_text: str,
    tools: list[ToolSpec] | None = None,
    *,
    user_content: list[ContentBlock] | None = None,
    event_sink: Any = None,
    run_id: str | None = None,
    settings: Any = None,
    agent_state: AgentState | None = None,  # 新增
) -> GenericRunResult:
    ...
    if agent_state is None:
        agent_state = AgentState(
            run_id=run_id,
            session_id=session.session_id,
            max_tool_iterations=self._max_tool_iterations,
        )
    ...
```

**落地位置**：session-persistence-spec §5.2（已修，含代码示例 + §9 影响范围）

**理由**：保持现有行为不变（不传 agent_state 时 new 一个），同时支持 resume 注入。`__init__` 不变避免侵入构造期。

**关联改动**：`turn.py` 的 `_build_run_context` 也要加可选参数 `agent_state` 和 `task_state_store`，让 resume 流程能注入。详见 session-persistence-spec §5.2。

---

## 决议 3：SIGINT 窗口期不一致状态（interruption ↔ session-persistence ↔ generic_runtime）

**冲突**：LLM 调用完成、`session.append(assistant_msg)` 已执行但 `tool_result` 尚未 append 时，若 SIGINT 到达触发 save_snapshot，session.json 会保存"含 tool_call 但无 tool_result"的不一致状态。resume 后 Anthropic API 报错（tool_call 缺失对应 tool_result）。

**裁决**：`save_snapshot` 加一致性校验——检测 `session.messages` 最后一条是否为含 `tool_calls` 的 assistant message。若是，回退到上一个一致保存点（丢弃最后一条 assistant message）。

```python
def save_snapshot(session: AgentSession, path: Path) -> None:
    messages = session.messages
    # 一致性校验：最后一条不能是含 tool_calls 但无对应 tool_result 的 assistant message
    if messages and isinstance(messages[-1], AssistantMessage) and messages[-1].tool_calls:
        # 丢弃最后一条不一致的 assistant message
        messages = messages[:-1]
    data = {
        "messages": [m.to_dict() for m in messages],
        "agent_state": session.agent_state.to_dict(),
        "task_state": session.task_state_store.to_snapshot_dict(),
    }
    atomic_write_json(path, data)
```

**落地位置**：session-persistence-spec §4.3（已修，含校验逻辑 + §5.4 边界情况 F）

**理由**：回退方案比"补 stub ToolResultMessage"更干净——stub 会改变 session 状态语义（多了一条假 tool_result）。回退到上一个一致保存点，resume 后 agent 从该点重新开始，用户重发消息时触发新的 LLM 调用。

---

## 决议 4：SIGINT handler 语义统一（interruption ↔ session-persistence）

**冲突**：
- session-persistence-spec §4.1 说"进程收到 SIGINT → 触发一次最终保存"——暗示 handler 里直接保存
- interruption-spec §2.1 的 `handle_sigint` 只做 `cancelled=True` + `stream.close()`，没调 save_snapshot
- interruption-spec §2.4 又说"SIGINT handler 触发 save_snapshot（已定义）"——自相矛盾

**裁决**：**SIGINT handler 只设 `cancelled=True` + `stream.close()`，不调 save_snapshot**。save_snapshot 由 main loop 检测 `cancelled` 后在 `_cancel_result` 里调（或 `run()` 返回后 CLI 调）。

**执行顺序**（统一确认）：

```
1. SIGINT 到达
2. handler: cancelled=True + stream.close()（不保存）
3. main loop 在安全点检测 cancelled
4. _cancel_result: 设 task_state paused + emit runtime.cancelled（不保存）
5. run() 返回 GenericRunResult(status="cancelled")
6. CLI 收到 result，调 save_snapshot（最终保存）
```

**落地位置**：
- interruption-spec §2.1 + §2.4（已统一为"handler 只设标志位"）
- session-persistence-spec §4.1（已说明"handler 不保存，由 run() 返回后 CLI 保存"）

**理由**：signal handler 里做 JSON 序列化 + fsync 有 reentrancy 风险（handler 可能在任意点执行，包括持有锁时）。handler 只设标志位是信号处理的最佳实践。save_snapshot 在 main loop 里调，安全且可控。

---

## 决议 5：interruption 依赖 providers 真 SSE（interruption ↔ providers-refactor）

**冲突**：interruption-spec 的 S3 策略（LLM 调用中立即停）依赖 `stream.close()` 中断 LLM streaming。但当前 transport 是假 streaming（`mimo_transport.py:135-136` 注释："For now, use non-streaming and convert to deltas"），`stream.close()` 无效。S3 不可行。

**裁决**：interruption-spec PR1 必须在 providers-refactor-spec PR1（真 SSE streaming）之后实施。

**已验证**（主 agent 真环境测试，2026-06-27）：
- anthropic SDK `messages.stream()` 调 mimo `https://token-plan-cn.xiaomimimo.com/anthropic` 成功
- 首 event 0.71s 到达（真流式，不是先收完再切片）
- `stream.close()` 中断生效（break 后 0.37s 正常退出）
- usage 在流末尾返回（input=64, output=39）
- stop_reason 正常（end_turn）

**回退路径**：若 providers-refactor 未落地，interruption-spec 退回 S2（handler 只设 `cancelled=True`，不调 `stream.close()`，等 LLM 自然结束）。配置项 `interrupt_abort_llm_stream=False` 触发回退。

**落地位置**：
- interruption-spec §8（已加实施顺序约束）
- providers-refactor-spec §8.1（SSE 风险已标"已验证 ✅"）

---

## 决议 6：ObservabilityConfig 定义归属（observability ↔ session-persistence ↔ interruption）

**冲突**：三份 spec 都引用 `ObservabilityConfig`，但没人负责定义。observability-spec §8 原文含糊（"在 ContextPolicyConfig 或新 ObservabilityConfig 中"）。

**裁决**：**observability-spec §8 负责定义 `ObservabilityConfig`**，放在 `config/observability.py`。session-persistence-spec 和 interruption-spec 只引用，不重复定义。

**字段归属**：

| 字段 | 定义于 | 用途 |
|------|--------|------|
| `console_enabled` / `console_show_thinking` / `console_thinking_first_line_only` / `console_verbose_to_expand` | observability-spec §8 | 终端渲染 |
| `trace_dir` / `trace_full_payload` / `trace_rotation_max_mb` | observability-spec §8 | trace.jsonl |
| `session_dir` / `save_session_per_iteration` / `save_on_sigint` / `strip_images_in_snapshot` | observability-spec §8（session-persistence 引用） | session 持久化 |
| `interrupt_enabled` / `interrupt_abort_llm_stream` | observability-spec §8（interruption 引用） | 中断 |
| `schema_version` | observability-spec §8（标注为代码常量，用户不应改） | 数据格式版本 |

**落地位置**：
- observability-spec §8（已定义完整 `ObservabilityConfig`）
- config-refactor-spec §2.3（已为 `config/observability.py` 保留位置）
- session-persistence-spec §8（引用，不重复定义）
- interruption-spec §6（引用，不重复定义）

---

## 决议 7：SessionSnapshotSink 去留（observability ↔ session-persistence）

**冲突**：observability-spec §6.3 把 `SessionSnapshotSink` 列为 EventSink，但 save_snapshot 是聚合语义（迭代结束），不是单事件驱动。`SessionSnapshotSink.emit(event)` 收到单个事件无法判断"迭代是否结束"。

**裁决**：**删除 `SessionSnapshotSink`**。save_snapshot 由 `GenericAgentRuntime.run()` 在迭代末尾主动调用 `AgentSession.save_snapshot()`。

**落地位置**：
- observability-spec §6.3（已删除 `SessionSnapshotSink`，说明改为 runtime 主动调）
- session-persistence-spec §4（save_snapshot 作为 `AgentSession` 方法，由 runtime 调用）

**理由**：save_snapshot 的触发时机是"迭代结束"这个聚合事件，不是单个 RuntimeEvent。把它当 EventSink 是分类错误，且无法实现"判断迭代结束"。

---

## 决议 8：CompositeEventSink 复用 FanoutEventSink（observability 内部）

**冲突**：observability-spec §6.3 新增 `CompositeEventSink`，但 `events/sinks.py:101-115` 已有 `FanoutEventSink`，语义完全重复。

**裁决**：**删除 `CompositeEventSink`**，复用现有 `FanoutEventSink`。

**落地位置**：observability-spec §6.3（已改为 `FanoutEventSink`）

---

## 实施顺序总表

基于以上决议，V1.6 实施顺序（按依赖关系）：

```
1. config-refactor-spec PR1          （基础设施，所有人依赖）
2. file-organization-spec PR1         （文件归位，纯搬移，减少后续冲突）
3. providers-refactor-spec PR1        （SDK 化 + 真 SSE，agent loop 依赖）
   └─ 里程碑：mimo SSE 真环境验证（已通过 ✅）
4. context-compaction-spec PR1        （依赖 providers 的 usage 字段 + LLMClient）
5. observability-spec PR1             （依赖 providers 的 streaming + 事件系统）
6. session-persistence-spec PR1       （依赖 observability 的事件 sink）
7. interruption-spec PR1              （依赖 providers 真 SSE + session-persistence save_snapshot）
8. file-organization-spec PR2         （与压缩 spec PR1 合并）
```

**关键依赖**：
- interruption-spec PR1 **必须**在 providers-refactor-spec PR1 之后（决议 5）
- session-persistence-spec PR1 **必须**在 observability-spec PR1 之后（决议 7）
- 所有 spec **必须**在 config-refactor-spec PR1 之后（配置系统统一）

---

## 评审修复状态

| spec | B 类必改项 | A 类跨 spec 冲突 | 评审修复记录 |
|------|-----------|-----------------|-------------|
| config-refactor-spec | 6/6 已修 | 决议 6（ObservabilityConfig 归属）已落地 | ✅ §8 |
| providers-refactor-spec | 10/10 已修 | 决议 5（SSE 验证）已落地 | ✅ §十 |
| context-compaction-spec | 17/17 已修 | 决议 4（LLMTransport 去留）已落地 | ✅ §十三 |
| observability-spec | 12/12 已修 | 决议 6/7/8 已落地 | ✅ 末尾 |
| session-persistence-spec | 9/9 已修 | 决议 1/2/3/4 已落地 | ✅ 末尾 |
| interruption-spec | 8/8 已修 | 决议 4/5 已落地 | ✅ 末尾 |
| file-organization-spec | 0（无 B 类问题） | 无 | 无需修 |

**所有 A 类冲突已在本文件统一裁决，并在各 spec 内部落地。**
