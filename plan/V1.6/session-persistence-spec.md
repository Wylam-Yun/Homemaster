# HomeMaster V1.6 Session 持久化方案

Date: 2026-06-27
Status: 已定稿 — 所有决策已确认，等待实施

---

## 一、问题陈述

### 1.1 现状（已核实）

`AgentSession`（`agent/session.py`）是纯内存容器：

```python
class AgentSession:
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self._messages: list[Message] = []

    def append(self, message: Message) -> None: ...
    def replace_messages(self, messages: list[Message]) -> None: ...
    def clear(self) -> None: ...
```

零持久化逻辑：没有 `save()`、`load()`、序列化、文件 I/O。进程死了聊天记录全丢。

`TaskStateStore`（`task_state/store.py`）同样是普通 class，持 pydantic 的 `TaskSnapshot`，但没有序列化方法。

### 1.2 产品场景需求

1. **崩溃恢复**：进程崩了/被 kill/断电，下次能从最近一个稳定点接着用
2. **跨会话延续**：今天任务没做完关电脑，明天打开继续——单 session 多任务的核心需求
3. **resume 后人工确认**：崩溃后恢复到最稳定状态停下等用户输入，不自动接着跑（避免重复浪费 token）
4. **多 session 管理**：能列出历史 session、查看元信息、清理旧的

### 1.3 与其他 spec 的关系

| 关联 spec | 关系 |
|----------|------|
| observability-spec.md §4 | 定义了三文件分离（trace.jsonl + session.json + messages.jsonl），本 spec 细化 session.json 的序列化与 resume 流程 |
| context-compaction-spec.md | 压缩事件触发时也要原子保存 session.json（避免压缩后状态丢失） |

---

## 二、设计哲学

### 2.1 借鉴 Hermes 的核心思路

参考 `/Users/wylam/Documents/workspace/hermes-agent`（生产级持久化，3000+ 行 `hermes_state.py`）：

- **resume 是等待用户输入，不是自动接着跑**——崩溃发生在哪个不完整步骤，那个步骤作废，从上一个完整保存点继续
- **原子写入**：临时文件 + fsync + rename，写一半崩了不会损坏旧文件
- **图像不进持久化**：替换为文本占位符（和压缩 spec 的"历史图像剥离"理念一致）
- **session_id 人类可读 + UUID 后缀**：`YYYYMMDD_HHMMSS_xxxxxx`
- **不保留历史快照**：每次覆盖写入，历史在 trace.jsonl 里
- **task_state 只存当前态**：不存历史版本，agent 关心"现在做到哪了"

### 2.2 不照搬 Hermes 的部分

- ❌ 不用 SQLite——单进程内存应用，JSON 文件够用，避免引入 DB 依赖
- ❌ 不做双重存储（SQLite + JSON 日志）——HomeMaster 的三文件分离（trace/session/messages）已经分担职责
- ❌ 不做压缩链（parent_session_id）——HomeMaster 压缩在 session 内原地发生，不创建新 session
- ❌ 不做 FTS5 全文搜索——session 数量有限，未来需要再加
- ❌ 不做跨平台转交（handoff_state）——未来 gateway 时再做

### 2.3 核心原则

1. **数据与运行时对象分离**：session.json 只存纯数据（messages, agent_state, task_state），运行时对象（transport, tool_registry, event_sink）resume 时重新注入
2. **原子性优先**：保存要么完整成功要么完整失败，不允许半新半旧
3. **恢复到最稳定状态**：崩在不完整步骤时，该步骤作废，从上次完整保存点继续
4. **resume 不自动跑**：恢复对话历史后停下等用户输入

---

## 三、Session 文件结构

### 3.1 目录组织

```
~/.homemaster/sessions/
  └── {session_id}/
      ├── trace.jsonl              ← 增量事件流，debug 用（observability-spec §4.2）
      ├── session.json             ← 会话快照，resume 用（本 spec 核心）
      └── messages.jsonl           ← 对话历史，append-only（observability-spec §4.4）
```

### 3.2 session_id 生成

格式：`YYYYMMDD_HHMMSS_xxxxxx`

```python
session_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
# 示例: "20260627_143052_a1b2c3"
```

- 时间戳前缀：人类可读，`hm session list` 一眼看出时间
- 6 位 UUID 后缀：保证唯一性
- 不含中文/特殊字符：路径安全

### 3.3 session.json 字段设计

```json
{
  "schema_version": 1,
  "session_id": "20260627_143052_a1b2c3",
  "created_at": 1718000000.0,
  "updated_at": 1718000123.4,
  "model": "mimo-v2.5",
  "system_prompt": "...",
  "agent_state": {
    "run_id": "...",
    "session_id": "...",
    "status": "running",
    "turn_index": 3,
    "iteration_index": 5,
    "total_model_calls": 5,
    "total_tool_calls": 8,
    "active_task_snapshot_id": "task-state-0003",
    "last_assistant_text": "...",
    "last_tool_calls": [...],
    "last_tool_results_summary": [...],
    "consecutive_tool_errors": 0,
    "no_progress_iterations": 0,
    "last_compaction": {...},
    "estimated_context_tokens": 18000,
    "provider_usage": {
      "input_tokens": 45000,
      "output_tokens": 200,
      "total_tokens": 45200
    },
    "metadata": {}
  },
  "task_state": {
    "snapshot_id": "task-state-0003",
    "goal": "把水杯拿到客厅",
    "status": "active",
    "subtasks": [...],
    "current_subtask": "fetch_object"
  },
  "messages": [
    {"role": "user", "content": [...]},
    {"role": "assistant", "content": [...], "tool_calls": [...], "reasoning_content": "..."},
    {"role": "tool", "tool_call_id": "...", "content": [...]}
  ]
}
```

**注意 - provider_usage 累加 bug 修复**：当前 `state.py:51` 的 `provider_usage` 是覆盖式赋值（`=`），导致每次 LLM 调用后覆盖而非累加。**本 spec 要求修复**：`agent_state.provider_usage` 改为累加（每次 `+=` 而非 `=`）。序列化到 session.json 的是累加后的总值（整个 session 的累计用量，而非单次调用的用量）。

### 3.4 图像序列化策略

**session.json 不存图像**：保存时把图像 `ContentBlock` 替换为文本占位符。

```python
def _strip_image_for_persistence(
    block: ContentBlock,
    *,
    tool_name: str = "unknown",
    iter_index: int = 0,
    args: dict | None = None,
) -> ContentBlock:
    """把图像 block 替换为文本占位符 block。

    关键：ContentBlock.type 必须改成 "text"（不是 "image"），
    否则 transport（如 mimo_transport.py:645-646）会把
    type=="image" + 任何 dict source 当真图像发给 API，API 报错。
    """
    if block.type == "image":
        return ContentBlock(
            type="text",
            text=(
                f"[image stripped — {tool_name} @ iter {iter_index}, "
                f"args={args}. See trace.jsonl for original]"
            ),
        )
    return block
```

理由：
- 图像 base64 几百 KB 到几 MB，存进 session.json 会让文件巨大、加载慢
- 图像时效性强，resume 后重新观察即可（和压缩 spec"图像折叠"理念一致）
- 避免 session 目录被部分删除时图像文件丢失的校验复杂度
- **`type="text"` 而非 `type="image"`**：transport 会把 `type=="image"` 的 block 当真图像发给 LLM API，即使 `source` 是占位符也会报错。改成 `type="text"` 后 transport 正常当文本传递

**trace.jsonl 保留完整图像 base64**：debug 时需要看"当时 agent 看到的图"。

**resume 后**：被剥离的图像在 messages 里是文本占位符，agent 看到的是"之前有一张图但被剥离了"，需要时会重新调用 `robot_observe`。

---

## 四、保存机制

### 4.1 保存时机

| 事件 | 触发保存 | 保存内容 |
|------|---------|---------|
| 每次迭代结束（LLM 回复 + 工具调用完成） | ✅ session.json 全量覆盖 | messages + agent_state + task_state |
| 每次消息产生 | ✅ messages.jsonl append | 单条消息 |
| 每个事件 emit | ✅ trace.jsonl append | 完整事件 payload |
| 压缩触发后 | ✅ session.json 全量覆盖 | 压缩后的新 messages + 更新的 agent_state |
| 进程收到 SIGINT | ✅ 触发一次最终保存 | 当前完整状态 |
| 进程正常退出 | ✅ 触发一次最终保存 | 当前完整状态 |

### 4.2 原子写入实现

```python
import os, json, tempfile
from pathlib import Path

def atomic_write_json(path: Path, data: dict) -> None:
    """原子写入 JSON 文件。
    1. 写入临时文件
    2. fsync 确保落盘
    3. rename 原子替换（POSIX 保证）
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)  # POSIX 原子重命名
```

**保证**：要么完整的旧文件，要么完整的新文件，绝不会出现半文件。

### 4.3 原子性边界

task_state 和 messages **必须一起保存**——不能出现 messages 更新了但 task_state 还是旧的。

实现：在同一个 `atomic_write_json` 调用里序列化整个 session.json，包括 messages + agent_state + task_state。一次写入要么全成功要么全失败。

```python
def save_snapshot(self, session_dir: Path) -> None:
    # 校验：最后一条 message 是否为含 tool_calls 的 assistant message
    # 若是，说明 LLM append 了但 tool_result 还没 append——这是不稳定状态
    messages_to_save = list(self._messages)
    if messages_to_save:
        last_msg = messages_to_save[-1]
        if last_msg.role == "assistant" and hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
            # 回退到上一个一致保存点：丢弃最后一条 assistant message
            messages_to_save = messages_to_save[:-1]
            # 或者：补一个 stub ToolResultMessage（标记 is_error=True）
            # 选择回退方案，因为补 stub 会改变 session 状态语义

    data = {
        "schema_version": 1,
        "session_id": self.session_id,
        "created_at": self._created_at,
        "updated_at": time.time(),
        "model": self._model,
        "system_prompt": self._system_prompt,
        "agent_state": self._agent_state.model_dump(),
        "task_state": self._task_state_store.to_snapshot_dict(),
        "messages": [self._strip_images(m).model_dump() for m in messages_to_save],
    }
    atomic_write_json(session_dir / "session.json", data)
```

**SIGINT 窗口期保护**：LLM 调用完成、`session.append(assistant_msg)` 已执行但 `tool_result` 尚未 append 时，若 SIGINT 到达触发 save_snapshot，session.json 会保存"含 tool_call 但无 tool_result"的不一致状态。resume 后 API 会报错（tool_call 缺失对应 tool_result）。上述校验检测此情况并回退到上一个一致保存点。

### 4.4 不保留历史快照

session.json 每次覆盖写入，**不保留历史版本**。

理由：
- 历史在 trace.jsonl 有完整记录
- agent 关心当前态，不关心历史快照
- 节省磁盘空间
- 如果未来需要历史版本，加 `--keep-history` 参数（保留最近 N 个）

### 4.5 TaskStateStore 序列化

新增方法：

```python
class TaskStateStore:
    def to_snapshot_dict(self) -> dict | None:
        """序列化当前 task_state 快照。无活跃任务时返回 None。"""
        if self._snapshot is None:
            return None
        return {
            "snapshot": self._snapshot.model_dump(),
            "snapshot_counter": self._snapshot_counter,
        }

    @classmethod
    def from_snapshot_dict(cls, data: dict | None, *, run_id: str) -> "TaskStateStore":
        """从快照数据恢复。run_id 是必须参数（TaskStateStore.__init__ 要求）。"""
        store = cls(run_id=run_id)
        if data is not None:
            from homemaster.task_state.models import TaskSnapshot
            store._snapshot = TaskSnapshot.model_validate(data["snapshot"])
            store._snapshot_counter = data.get("snapshot_counter", 0)
        return store
```

**只存当前态**：`_snapshot` 字段是单个 `TaskSnapshot | None`，不存历史快照列表。

**counter 直接存储**：`to_snapshot_dict` 输出 `snapshot_counter` 字段（不从 `snapshot_id` 字符串解析），`from_snapshot_dict` 直接恢复。避免 `int(store._snapshot.snapshot_id.split("-")[-1])` 这类脆弱解析。

---

## 五、Resume 机制

### 5.1 Resume 语义

**只恢复对话历史 + 运行状态 + 任务进度，然后停下等用户输入。**

- ❌ 不自动接着跑未完成的任务
- ❌ 不自动重新调用 LLM
- ✅ 恢复到上次完整保存点，等用户下一条消息

理由（"恢复到最稳定状态"）：
- 崩溃后用户可能想先看状态再决定是否继续
- 自动接着跑可能重复浪费 token
- 崩在 LLM 生成中：那次 LLM 调用作废，从上次保存点重新开始（用户重发消息时触发新 LLM 调用）
- 崩在工具执行中：那个工具结果丢失（未保存），LLM 没看到它，相当于工具没执行

### 5.2 Resume 流程

```python
def resume_session(
    session_id: str,
    *,
    session_dir: Path,
    llm_client: LLMClient,  # 不再是 LLMTransport（V1.6 按 providers-refactor-spec 删除该抽象）
    tool_registry: ToolRegistry,
    event_sink_factory: Callable[[], EventSink],
) -> GenericAgentRuntime:
    # 1. 加载 session.json
    snapshot = load_session_json(session_dir / f"{session_id}" / "session.json")

    # 2. 校验 schema_version
    if snapshot["schema_version"] != 1:
        raise SessionSchemaError(...)

    # 3. 反序列化纯数据
    session = AgentSession(session_id=snapshot["session_id"])
    for msg_data in snapshot["messages"]:
        session.append(Message.model_validate(msg_data))  # 图像是占位符

    agent_state = AgentState.model_validate(snapshot["agent_state"])
    task_state_store = TaskStateStore.from_snapshot_dict(
        snapshot["task_state"],
        run_id=agent_state.run_id,
    )

    # 4. 重新构造运行时对象（不序列化）
    event_sink = event_sink_factory()  # 注入 ConsoleEventSink + JsonlTraceSink + ...

    # 5. 组装 runtime——传入外部 agent_state 和 task_state_store
    runtime = GenericAgentRuntime(
        llm_client=llm_client,  # V1.6 改用 LLMClient（providers-refactor-spec）
        tool_registry=tool_registry,
        session=session,
        event_sink=event_sink,
        system_prompt=snapshot["system_prompt"],
        agent_state=agent_state,           # 注入恢复的 agent_state
        task_state_store=task_state_store,  # 注入恢复的 task_state_store
    )

    # 6. 返回，等用户输入
    return runtime
```

**`GenericAgentRuntime.run()` 改造**：

```python
# generic_runtime.py

class GenericAgentRuntime:
    def __init__(self, ...):
        # 不变：__init__ 不持 agent_state / task_state_store
        ...

    def run(
        self,
        session: AgentSession,
        user_text: str,
        *,
        agent_state: AgentState | None = None,
        task_state_store: TaskStateStore | None = None,
    ) -> GenericRunResult:
        # 传入则用（resume 场景），不传入则 new（新 session 场景）
        if agent_state is None:
            agent_state = AgentState(run_id=..., session_id=session.session_id)
        if task_state_store is None:
            task_state_store = TaskStateStore(run_id=agent_state.run_id)

        # ... 现有 agent loop
```

**`_build_run_context` 改造**（`turn.py:88-118`）：

```python
def _build_run_context(
    ...,
    task_state_store: TaskStateStore | None = None,
    agent_state: AgentState | None = None,
) -> RunContext:
    # 传入则用，不传入则 new
    if task_state_store is None:
        task_state_store = TaskStateStore(run_id=...)
    if agent_state is None:
        agent_state = AgentState(run_id=...)
    ...
```

### 5.3 运行时对象注入

**数据与运行时对象分离**：

| 类别 | 序列化？ | resume 处理 |
|------|---------|------------|
| messages | ✅ | model_validate 反序列化 |
| agent_state | ✅ | model_validate 反序列化 |
| task_state | ✅ | from_snapshot_dict 反序列化 |
| system_prompt | ✅ | 直接字符串 |
| model name | ✅ | 用于校验 transport 一致 |
| transport | ❌ | 外部注入（持 HTTP 连接、API key） |
| tool_registry | ❌ | 外部注入（持工具实例、机器人连接） |
| event_sink | ❌ | 外部注入（持文件句柄） |
| context_assembler | ❌ | 由 transport + policy 重新构造 |

### 5.4 Resume 后的边界情况处理

#### 情况 A：LLM 生成中崩溃

```
iter 5: [SAVE at iter 4 end] → LLM 生成中 → 崩溃
```

resume 时：
- session.json 是 iter 4 结束的状态
- messages 最后一条是 tool_result（iter 4 的）
- agent_state.iteration_index == 4
- **agent 等用户输入**——不自动重新调 LLM
- 用户重发消息时，触发 iter 5 的新 LLM 调用

#### 情况 B：工具执行中崩溃

```
iter 5: LLM → tool_call → [工具执行中崩溃]
```

resume 时：
- session.json 是 iter 4 结束的状态
- iter 5 的 LLM 回复（含 tool_call）**未保存**（迭代未完成）
- tool_result 也未保存
- **agent 等用户输入**——上次迭代的 LLM 调用作废
- 用户重发消息时，从 iter 4 状态重新开始

#### 情况 C：session.json 写入中崩溃

```
[SAVE 中崩溃]
```

resume 时：
- `atomic_write_json` 保证：要么完整的旧 session.json，要么完整的新 session.json
- 不会出现半文件
- 加载时正常解析

#### 情况 D：session.json 完好但 trace.jsonl 损坏

```
trace.jsonl 最后一行写一半
```

resume 时：
- session.json 完好，正常加载
- trace.jsonl 最后一行 JSON 解析失败——跳过该行，记 warning
- 不影响 resume

#### 情况 E：模型不一致

resume 时检测 `snapshot["model"]` 和当前配置的 transport 模型是否一致：

- 一致：正常 resume
- 不一致：打印 warning，提示"模型已变更，历史 messages 可能影响行为"，让用户决定是否继续

#### 情况 F：SIGINT 在 LLM append 后、tool_result append 前到达

```
iter 5: LLM 返回含 tool_call 的 assistant_msg → session.append(assistant_msg) → [SIGINT 到达]
```

此时 session.messages 最后一条是含 `tool_calls` 的 assistant message，但没有对应的 tool_result。若直接保存，resume 后 API 会报错（tool_call 缺失 tool_result）。

处理：`save_snapshot` 内校验（§4.3），检测到这种不一致状态时回退到上一个一致保存点（丢弃最后一条 assistant message）。resume 后 agent 从 iter 4 结束状态重新开始，用户重发消息时触发新的 LLM 调用。

---

## 六、CLI 命令

### 6.1 新增参数

```bash
hm run "..."                          # 新建 session
hm run "..." --resume <session_id>    # 恢复指定 session（等用户输入）
hm run "..." --continue               # 恢复最新 session（等价于 --resume latest）
```

注意：`--resume` / `--continue` 后面如果跟了用户消息，则恢复后立即处理该消息；如果不跟消息，则恢复后进入等待状态（如果是 REPL）或打印"已恢复 session，请输入下一条消息"提示（如果是单轮 CLI）。

### 6.2 新增子命令（PR2）

```bash
hm session list                       # 列出所有 session
hm session show <id>                  # 查看 session 元信息
hm session delete <id>                # 删除 session
hm session clean --older-than 30d     # 清理 30 天前的 session
hm session export <id> --format markdown  # 导出为 markdown
```

### 6.3 hm session list 输出示例

```
SESSION ID                          CREATED              ITER  TOKENS   STATUS
20260627_143052_a1b2c3              2026-06-27 14:30     5     18K      running
20260627_101530_x7y8z9              2026-06-27 10:15     12    45K      replied
20260626_182015_m3n4o5              2026-06-26 18:20     3     8K       replied
```

---

## 七、AgentSession 改造

### 7.1 新增方法

```python
class AgentSession:
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self._messages: list[Message] = []
        self._created_at: float = time.time()

    # 现有方法保留
    @property
    def messages(self) -> list[Message]: ...
    def append(self, message: Message) -> None: ...
    def replace_messages(self, messages: list[Message]) -> None: ...
    def clear(self) -> None: ...

    # 新增
    def to_snapshot_dict(
        self,
        *,
        agent_state: AgentState,
        task_state_store: TaskStateStore,
        model: str,
        system_prompt: str,
    ) -> dict:
        """序列化整个 session 状态。图像被剥离为占位符。"""
        ...

    @classmethod
    def from_snapshot_dict(cls, data: dict) -> tuple[AgentSession, AgentState, TaskStateStore]:
        """从快照恢复。返回 (session, agent_state, task_state_store)。"""
        ...

    @staticmethod
    def _strip_image_for_persistence(block: ContentBlock) -> ContentBlock:
        """把图像 block 替换为占位符 block。"""
        ...
```

### 7.2 设计要点

- `to_snapshot_dict` 是 session 的职责，但它需要 agent_state 和 task_state_store 作为参数（session 不持有它们，避免循环依赖）
- `from_snapshot_dict` 一次性返回三个对象（session, agent_state, task_state_store），保证原子性
- 图像剥离在 `_strip_image_for_persistence` 里集中处理，不污染 Message 的序列化

---

## 八、配置项

session 持久化相关配置统一在 `ObservabilityConfig`（`config/observability.py`，由 observability-spec §8 定义）中：

```python
# ObservabilityConfig 中的 session 持久化字段（在 observability-spec 定义）
# session_dir: str = "~/.homemaster/sessions"
# save_session_per_iteration: bool = True  # 每次迭代后保存
# save_on_sigint: bool = True              # SIGINT 时保存
# strip_images_in_snapshot: bool = True    # session.json 不存图像
# schema_version: int = 1                  # session.json 格式版本（代码常量）
```

本 spec 不单独定义配置类，从 `ObservabilityConfig` 读取上述字段。

---

## 九、影响范围估算

**新增**：
- `agent/session_persistence.py` ~200 行
  - `atomic_write_json`
  - `save_snapshot`
  - `load_session_json`
  - `resume_session`
  - 图像剥离工具函数
- `agent/session.py` 新增方法 ~80 行
  - `to_snapshot_dict` / `from_snapshot_dict` / `_strip_image_for_persistence`
- `task_state/store.py` 新增方法 ~30 行
  - `to_snapshot_dict` / `from_snapshot_dict`（counter 直接存储，不解析 snapshot_id）
- `cli/` 新增子命令 ~150 行
  - `hm session list/show/delete/clean/export`

**修改**：
- `generic_runtime.py`：
  - `run()` 加可选参数 `agent_state: AgentState | None = None` 和 `task_state_store: TaskStateStore | None = None`（§5.2）
  - 每次迭代结束调用 `save_snapshot`
  - 压缩后调用 `save_snapshot`
  - SIGINT handler 触发 `save_snapshot`
- `turn.py:88-118`：
  - `_build_run_context` 加可选参数 `task_state_store` 和 `agent_state`（§5.2）
- `state.py:51`：
  - `provider_usage` 从覆盖式（`=`）改为累加式（`+=`）（§3.3 注意）
- `cli.py`：
  - 新增 `--resume` / `--continue` 参数
  - session 目录初始化
  - resume 流程接入

**删除**：
- 无（AgentSession 原有方法保留）

---

## 十、与其他重构的依赖关系

| 依赖项 | 关系 |
|--------|------|
| observability-spec §4 三文件分离 | 本 spec 细化 session.json 的序列化与 resume |
| observability-spec §6 事件系统 | save_snapshot 作为事件 sink 的一种实现 |
| context-compaction-spec | 压缩后触发 save_snapshot |
| audit 第五节 Session 无持久化 | 本 spec 直接解决 |
| audit 11.6 checkpoint/resume | 本 spec 实现 resume（checkpoint 概念简化为"每次迭代保存"） |

---

## 十一、落地分两个 PR

### PR1：Session 持久化核心

1. `atomic_write_json` 工具函数（§4.2）
2. `AgentSession.to_snapshot_dict` / `from_snapshot_dict`（§7）
3. `AgentSession._strip_image_for_persistence`（§3.4）
4. `TaskStateStore.to_snapshot_dict` / `from_snapshot_dict`（§4.5）
5. `save_snapshot` 函数（§4.3）
6. `load_session_json` 函数
7. `resume_session` 函数（§5.2）
8. `generic_runtime.py` 每次迭代结束 + 压缩后调用 save_snapshot
9. SIGINT handler 触发 save_snapshot
10. CLI `--resume` / `--continue` 参数
11. `ObservabilityConfig` 新增 session 持久化字段
12. 边界情况处理（§5.4 的 A/B/C/D/E）

### PR2：Session 管理命令

13. `hm session list`
14. `hm session show <id>`
15. `hm session delete <id>`
16. `hm session clean --older-than`
17. `hm session export <id> --format markdown`

---

## 十二、决策汇总（已全部确认）

| 决策点 | 选择 | 理由 |
|--------|------|------|
| snapshot 保存内容 | messages + agent_state + task_state | 完整恢复任务进度 |
| 触发时机 | session.json 每次迭代结束覆盖 + messages/trace 增量 append | 稳定点 + 完整流 |
| 原子写入 | 临时文件 + fsync + rename | POSIX 原子，简单可靠 |
| resume 语义 | 只恢复对话，等用户输入 | 恢复到最稳定状态，不自动跑 |
| session_id 格式 | `YYYYMMDD_HHMMSS_xxxxxx` | 人类可读 + 唯一 |
| 运行时对象注入 | 数据与对象分离 | transport/tool_registry/event_sink 重新注入 |
| 图像序列化 | session.json 不存（用占位符），trace.jsonl 保留完整 | session.json 保持小，debug 完整性 |
| task_state 保存 | 只存当前态，不存历史 | agent 关心"现在做到哪了" |
| task_state 与 messages 原子性 | 一起保存，要么都新要么都旧 | 保持状态一致 |
| 历史快照 | 不保留，每次覆盖 | 历史在 trace.jsonl 里 |
| SQLite | 不引入 | 单进程 JSON 够用 |
| 压缩链 | 不做 | 压缩在 session 内原地发生 |
| schema_version | 字段保留，当前 = 1 | 未来格式变更时迁移 |

---

## 十三、未决事项

无。所有决策已确认。实施时如遇新问题再讨论。

---

## 十四、评审修复记录（2026-06-27）

| # | 问题 | 修复 |
|---|------|------|
| 13 | 图像剥离方案会炸 transport：`type="image"` + 占位符 source 会让 API 报错 | §3.4 改为 `ContentBlock(type="text", text="[image stripped ...]")`，transport 不会当图像发 |
| 16 | `from_snapshot_dict` 的 `cls()` 缺 `run_id` 会 TypeError | §4.5 改为 `cls(run_id=run_id)`，`run_id` 为必须参数 |
| 17 | `TaskSnapshot` 没 `created_at` 字段但 §3.3 字段示例有 | §3.3 字段示例删除 `created_at` |
| 18 | `_snapshot_counter` 恢复依赖 `int(snapshot_id.split("-")[-1])` 脆弱 | §4.5 `to_snapshot_dict` 输出 `snapshot_counter` 字段，`from_snapshot_dict` 直接恢复 |
| 19 | SIGINT 窗口期不一致状态：LLM append 后、tool_result append 前 SIGINT 到达，session.json 含 tool_call 无 tool_result | §4.3 `save_snapshot` 加校验：检测最后一条 message 为含 tool_calls 的 assistant message 时回退到上一个一致保存点；§5.4 加情况 F |
| 20 | `provider_usage` 累加 bug（`state.py:51` 覆盖式） | §3.3 加注意说明修复为累加式；§9 影响范围加 `state.py:51` 改造 |
| 14 | `generic_runtime.run()` 不接受外部 agent_state，resume 跑不通 | §5.2 `run()` 加可选参数 `agent_state` 和 `task_state_store`；§9 影响范围加 |
| 15 | `turn.py` `_build_run_context` 每次 new `TaskStateStore`，无法注入 | §5.2 `_build_run_context` 加可选参数 `task_state_store` 和 `agent_state`；§9 影响范围加 |
| 8 | `ObservabilityConfig` 引用（session 持久化字段归属） | §8 明确从 `config/observability.py` 的 `ObservabilityConfig` 读取，本 spec 不单独定义 |
