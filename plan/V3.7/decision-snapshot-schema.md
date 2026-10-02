# 裁决②：快照 Schema 裁决

状态：已裁决（Phase 0 先决文档）
问题：迁移后 session 持久化的权威 schema 是什么？`homemaster.agent.state.AgentState`、
`agentscope.state.AgentState`、`application.session.SessionSnapshot` 三者怎么分工？

## 0. 三者的真实职责（源码实测）

| 对象 | 位置 | 内容 | 生命周期 |
|---|---|---|---|
| `application.session.SessionSnapshot` | `application/session.py:76` | `session_id` + `revision`（CAS）+ `generation`（fencing）+ `environment_ref` + `payload` | 控制面权威信封；`SessionBackend.save(expected_revision=…)` 做 CAS |
| `homemaster.agent.state.AgentState` | `agent/state.py:40` | run 簿记：status、turn/iteration 计数、`consecutive_tool_errors`、`no_progress_iterations`、`last_compaction`、`provider_usage`、`pending_model_observation`、`unconsumed_observation_tool_call_id`、metadata | 单 run 内可变，快照时整体 model_dump |
| `agentscope.state.AgentState` | `agentscope/state/_state.py:209` | `session_id`、`summary`（压缩摘要）、`context: list[Msg]`、`reply_context{reply_id, cur_iter, structured_*}`、`permission_context`、`tool_context`（读缓存+激活组）、`tasks_context`、`middle_context`（middleware 跨 reply 状态） | Agent 拥有的全部会话态 |

`SessionRuntime` 另持不可序列化成员：locks、`active_task`、`cancellation`、`last_result`、`_compaction_request`、`require_recall`——从不进快照。

## 1. 裁决

**信封不变。** `SessionSnapshot{revision, generation, environment_ref, payload}` 原样保留——
CAS 写盘、generation fencing、`SessionConflictError`/`SessionGenerationError` 语义不动。

**payload 升级为 schema_version=2**，在 v1 字段上新增：

```jsonc
{
  "schema_version": 2,
  "session_id": "…", "created_at": …, "saved_at": …,
  "model": "…", "system_prompt": "…",
  "messages": [ … ],              // HM 消息投影（读者侧：export/list/messages.jsonl）
  "agent_state": { … },           // HM AgentState.model_dump——run 簿记照旧
  "task_state": { … },            // TaskStateStore 快照照旧
  "agentscope_state": {           // 新增：AS AgentState.model_dump(mode="json")
    "session_id": "…",
    "summary": …,
    "context": [ …Msg… ],         // 引擎权威上下文（含 tool_call/result block）
    "reply_context": {…},
    "permission_context": {…},
    "tool_context": {…},
    "tasks_context": {…},
    "middle_context": {…}
  },
  "canonical_evidence_refs": […], // 新增：原属 SessionRuntime 内存字段，落盘
  "require_recall": true          // 新增：compaction 后强制 recall 标志落盘
}
```

### 双写与权威分工

| 字段 | 权威 | 理由 |
|---|---|---|
| `agentscope_state.context` | **引擎权威** | AS 上下文含 ToolCallBlock.state（ASKING/SUBMITTED/FINISHED）、block id、时间戳——挂起/恢复语义只能靠它，HM messages 投影表达不了 |
| `messages` | **投影**，从 AS context 经 `from_agent_scope` 派生 | 读者面（list_sessions 的 message_count、export、审计）继续消费 HM schema；单一事实源仍是 AS context，写快照时同刻生成两份，转换不对称由裁决①的 round-trip 测试堵死 |
| `agent_state`（HM 簿记） | 保留为独立字段 | 与 AS AgentState **字段零重叠**：AS 管对话上下文，HM 管 run 策略簿记（no_progress/观察屏障/compaction 记录）。不合并，防止职责糊掉 |
| `canonical_evidence_refs` / `require_recall` | 显式落盘（新） | 这两字段现在只在内存，跨进程 resume 丢失；schema v2 顺手补上，成本一行 |

### Resume 加载顺序（fail-closed）

1. `payload["schema_version"] > 2` → 拒绝（不向未来写）
2. `agentscope_state` 存在 → `AS AgentState.model_validate` 恢复上下文；`messages` 只做读者投影，不一致时以 `agentscope_state.context` 为准并记 warning
3. `agentscope_state` 缺失且 `schema_version <= 2` → **旧快照兼容路径**：`messages` 经 `to_agent_scope` 转成 context，AS 其余子上下文用默认值；迁移过的会话带 `metadata["migrated_from"]=1` 标记
4. `agent_state`/`task_state`/`canonical_evidence_refs`/`require_recall` 各自独立 model_validate，缺失用默认——与 v1 行为一致

### 命名裁决

两态同名 `AgentState` 是最大混淆源。substrate 边界内强制别名：

```python
from homemaster.agent.state import AgentState as RunBookkeepingState
from agentscope.state import AgentState as EngineState
```

代码/文档/日志里一律用 `run_state` vs `engine_state` 称呼；裸写 `AgentState` 视为 review 阻断项。

## 2. 显式否决项

| 方案 | 否决理由 |
|---|---|
| 用 `AS AgentState.model_dump(mode="json")` 直接当快照 | 丢 revision/generation/environment_ref/CAS 信封，丢 HM run 簿记与 task_state——把控制面状态降格成引擎内部态 |
| 只存 HM messages，resume 时转 AS context | `to_agent_scope` 是投影不是恒等映射：ToolCallBlock.state/时间戳/hint 回不去，`has_awaiting_tool_calls` 的 ASKING/SUBMITTED 恢复语义断裂 |
| 合并 HM AgentState 进 AS AgentState（挂 middle_context） | 两套生命周期不同：HM 簿记随 run 重建部分字段，AS state 随 reply 演化；塞一起只能靠约定防串味，schema 上就不是一回事 |

## 3. 验收断言

- 快照写出→读回：`agentscope_state` 全字段 `model_validate` 相等；`messages` 与 `from_agent_scope(context)` 派生结果逐字段相等
- v1 快照（无 `agentscope_state`）可加载，resume 后 context 含全部可转换消息
- CAS：`save(expected_revision=stale)` 仍抛 `SessionConflictError`；generation 不匹配仍抛 `SessionGenerationError`
- 尾部悬挂 tool_call 的 ASKING/SUBMITTED block 在 resume 后可被 `has_awaiting_tool_calls` 检出（恢复语义走引擎权威，不经投影）
