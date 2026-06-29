# HomeMaster V1.6 可观测性方案

Date: 2026-06-24
Status: 已定稿 — 所有决策已确认，等待实施

---

## 一、问题陈述

### 1.1 现状（已核实）

当前 CLI 只输出最终结果：

```
assistant: <最终回复>
status: replied
trace: /path/to/trace
```

中间过程（模型思考、工具调用、工具结果）完全看不到。

事件系统已搭好但内容稀薄：

| 事件 | 当前 payload | 缺什么 |
|------|------------|--------|
| `tool.call_completed` | `{"is_error": false}` | **没有结果内容** |
| `tool.call_failed` | `{"is_error": true}` | **没有错误信息** |
| assistant message 的 thinking/reasoning | **不 emit** | 完全缺失 |
| assistant message 的回复文本 | **不 emit** | 完全缺失 |

`ConsoleProgressEventSink`（`events/sinks.py:67-98`）只打印事件类型和工具名，不打印内容。`JsonlEventSink` 写了 JSONL trace 但"大概率没人看"。

MiMo transport 内部解析了 `reasoning_content` 和 `thinking`（`mimo_transport.py:343-344`），但 `generic_runtime.py` 在 emit 层没透传——数据已经在，只是没显示。

### 1.2 产品场景特征

HomeMaster V1.6 是面向**老人居家**的具身 agent：

1. **长程任务**：跑 20+ 步工具调用，2 分钟无反馈让老人以为卡死
2. **开发调试依赖**：agent 行为异常时，必须看到"在想什么""调了什么工具""工具返回了什么"
3. **多渠道未来**：当前 CLI，未来飞书/微信/gateway——事件系统设计要预留渠道适配
4. **resume 需求**：进程崩溃后要从最近状态恢复，需要会话快照

---

## 二、设计哲学

### 2.1 借鉴 OpenHarness 的核心思路

参考 `/Users/wylam/Documents/workspace/OpenHarness`：

- **事件是真理源，渠道是渲染器**：同一套 StreamEvent，多种渠道（终端/TUI/Gateway）消费
- **工具结果按类型差异化渲染**：不同工具有不同关键信息，不统一截断
- **会话快照 JSON**：latest.json + session-{id}.json 双文件，支持 resume
- **流式输出 + markdown 重渲染**：逐 token 输出，整段结束后用 markdown 高亮重渲染
- **工具输出 offload**：超大工具结果写磁盘，消息里留预览

### 2.2 不照搬 OpenHarness 的部分

- ❌ 不做 React TUI（当前 CLI 够用，未来 gateway 再做渠道适配）
- ❌ 不做会话快照全量保存（改用增量 JSONL + 全量 JSON 双轨，见 §4）
- ❌ 不做 thinking 只用 spinner（要支持折叠/展开）

### 2.3 加做（HomeMaster 特有）

- ✅ **thinking 折叠显示**：默认显示第一行，`--verbose` 展开完整
- ✅ **三文件分离**：trace.jsonl（debug）+ session.json（resume）+ messages.jsonl（对话历史）
- ✅ **工具渲染规则表与压缩管线共用**：一套规则两处用
- ✅ **每次迭代后保存 session.json**：真正的崩溃恢复

---

## 三、终端输出设计

### 3.1 输出风格

中等粒度（用户选择 b 风格）：

```
> 用户: 把水杯拿到客厅

[iter 1] [thinking] 我需要先找到水杯...
[iter 1] [tool_call] robot_observe({"room": "kitchen"})
[iter 1] [tool_result] [observe] room=kitchen, 1024×1024 → image (1016 tokens)

[iter 2] [thinking] 厨房没看到水杯，去客厅找...
[iter 2] [tool_call] robot_observe({"room": "living_room"})
[iter 2] [tool_result] [observe] room=living_room → 找到水杯在茶几上

[iter 3] [tool_call] robot_navigate({"room": "living_room"})
[iter 3] [tool_result] [navigate] → living_room (success)

[iter 4] [reply] 已找到水杯，在客厅茶几上。正在拿取并送往厨房。
```

### 3.2 各元素显示规则

#### 思考（thinking）

- **默认**：显示第一行 + `...`（最有信息密度）
- **`--verbose`**：显示完整 thinking
- 格式：`[iter N] [thinking] {首行}...`
- **"首行"定义**：`reasoning_content` 中第一个 `\n` 之前的内容，最多 200 字符。若 `reasoning_content` 不含 `\n`，则截取前 200 字符

#### 工具调用

- 显示工具名 + 参数（JSON 单行）
- 格式：`[iter N] [tool_call] {tool_name}({args_json})`
- 参数过长截断到 200 字符

#### 工具结果

按工具类型差异化渲染（规则表见 §5）：

- 格式：`[iter N] [tool_result] {summary}`
- 默认 fallback：`[{tool}] N lines, last 10: {tail}`
- 图像工具：显示尺寸 + token 占用
- 错误：红色显示，`[tool_result] ERROR: {message[:200]}`

#### 模型回复

- 流式输出（逐 token）
- 整段结束后用 `rich.markdown.Markdown` 重渲染（语法高亮）
- 格式：`[iter N] [reply] {完整回复}`

#### 压缩事件

- 触发时显示：`[context compaction] 45K → 18K tokens (saved 60%), used LLM summary`
- reactive compaction：`[warning] Reactive compaction triggered, aggressive mode`

#### 状态行

每轮对话结束后打印状态摘要：

```
[status] iter=4, tokens=18K/500K, model=mimo-v2.5, duration=12.3s
```

### 3.3 渲染库

使用 `rich`：

- `Console` 主控制台
- `Panel` 工具结果框（按工具类型不同边框颜色）
- `Markdown` 回复重渲染
- `Syntax` 文件内容语法高亮（如果工具结果包含代码）
- `Spinner` 工具执行中等待

### 3.4 异步输出

终端打印不阻塞 agent 循环，但 sink 有同步/异步分工：

- **同步 sink**：`JsonlTraceSink`——trace 是 debug 真理源，宁可阻塞 agent loop 也不能丢事件。每个事件 emit 时同步写 trace.jsonl
- **异步 sink**：`RichConsoleSink`——终端渲染，事件 emit 到一个异步队列，独立协程/线程从队列消费并渲染
  - 队列满时丢弃旧状态事件（如 `usage.update`），保留 tool/reply 事件
  - 队列大小：默认 64，可通过配置调整
- **为什么 trace 不同步**：trace.jsonl 写盘是 O(1) append，不显著阻塞；而终端 rich 渲染可能数十 ms，累积会明显拖慢 agent loop

---

## 四、文件持久化设计

### 4.1 三文件分离

每个 session 一个目录：

```
~/.homemaster/sessions/{session_id}/
  ├── trace.jsonl              ← 增量事件流，debug 用
  ├── session.json             ← 会话快照，resume 用
  └── messages.jsonl           ← 对话历史，append-only
```

### 4.2 trace.jsonl（debug 事件流）

- **格式**：JSONL，每行一个事件，append-only
- **写入时机**：每个事件 emit 时同步写 trace.jsonl（见 §3.4 同步/异步分工）
- **内容**：完整未截断的 payload（debug 用，终端显示才摘要化）。**trace.jsonl 走旁路，不经过 `events/sanitizer.py` 的截断**——`sanitizer.py` 只用于 Console 输出（脱敏敏感字段 + 截断超长 payload）。`JsonlTraceSink` 直接写完整 payload（仅做敏感字段脱敏，不截断长度）
- **rotation 策略**：trace.jsonl 单文件超过 `trace_rotation_max_mb`（默认 100MB）时滚动为 `trace.{N}.jsonl`（`trace.1.jsonl`, `trace.2.jsonl`...）。长程任务避免单文件过大
- **字段**：

```json
{
  "ts": 1718000000.123,
  "session_id": "abc123",
  "iter": 3,
  "event_type": "tool.call_completed",
  "tool_name": "robot_observe",
  "tool_args": {"room": "kitchen"},
  "tool_result": "...",  // 完整结果，不截断
  "is_error": false,
  "duration_ms": 1200,
  "tokens": {
    "input": 45000,
    "output": 200,
    "cache_read": 192,
    "real_total": 45392
  }
}
```

事件类型（扩展当前 RuntimeEvent）：

| event_type | 触发时机 | 关键字段 |
|-----------|---------|---------|
| `turn.start` | 每轮对话开始 | user_message |
| `turn.end` | 每轮对话结束 | status, duration_ms |
| `iter.start` | 每次迭代开始 | iter_index |
| `assistant.thinking` | 模型 thinking 产出 | thinking_text |
| `assistant.reply` | 模型回复产出 | reply_text |
| `tool.call_started` | 工具调用开始 | tool_name, tool_args |
| `tool.call_completed` | 工具调用完成 | tool_result, is_error, duration_ms |
| `tool.call_failed` | 工具调用失败 | error_message |
| `context.compaction` | 压缩触发 | trigger, before/after_tokens, stages |
| `context.length_error` | reactive compaction 触发 | error, retry_count |
| `usage.update` | token 使用更新 | tokens (含 cache_read) |

### 4.3 session.json（resume 快照）

- **格式**：JSON（indented，可读）
- **写入时机**：**每次迭代后覆盖更新**（用户选择 a）
- **内容**：完整会话状态

```json
{
  "session_id": "abc123",
  "created_at": 1718000000.0,
  "updated_at": 1718000123.4,
  "model": "mimo-v2.5",
  "system_prompt": "...",
  "agent_state": {
    "iteration_index": 4,
    "provider_usage": {
      "input_tokens": 45000,
      "output_tokens": 200,
      "total_tokens": 45200
    },
    "estimated_context_tokens": 18000,
    "last_compaction": {...}
  },
  "task_state": {...},
  "messages": [...]
}
```

- **resume 流程**：
  1. `hm run --resume <session_id>` 或 `hm run --continue`（继续最新）
  2. 读 `session.json`
  3. 反序列化为 `AgentSession` + `AgentState` + `TaskStateStore`
  4. 注入 `GenericAgentRuntime` 继续执行

### 4.4 messages.jsonl（对话历史）

- **格式**：JSONL，append-only
- **写入时机**：每次用户消息或模型回复产生时
- **内容**：只存用户消息和模型回复（不含工具调用细节）
- **用途**：单独查看对话流，方便导出/搜索

```json
{"ts": 1718000000.1, "role": "user", "content": "把水杯拿到客厅"}
{"ts": 1718000005.2, "role": "assistant", "content": "已找到水杯，在客厅茶几上..."}
```

### 4.5 文件生命周期

- **创建**：session 创建时建目录 + 三个空文件
- **写入**：trace/messages 增量 append，session 每次迭代覆盖
- **删除**：`/new` 命令触发时归档（不删，移到 `archived/` 子目录）
- **清理**：手动 `hm session clean` 清理旧 session（未来做）

---

## 五、工具渲染规则表（终端 + 压缩共用）

### 5.1 设计

一套规则表，终端渲染和压缩摘要共用：

```python
# agent/tool_render_rules.py

class ToolRenderRule(Protocol):
    def summary(self, tool_result: ToolResultMessage) -> str:
        """摘要化逻辑。终端和压缩共用。"""
        ...

    def terminal_format(self, summary: str) -> str:
        """终端专属格式化（颜色、Panel 等）。默认无格式。"""
        return summary

TOOL_RENDER_RULES: dict[str, ToolRenderRule] = {
    "robot_observe": ObserveRenderRule(),
    "memory_retriever": MemoryRenderRule(),
    "robot_navigate": NavigateRenderRule(),
    "robot_verify": VerifyRenderRule(),
}
```

### 5.2 各工具规则

| 工具 | summary 格式 | terminal_format |
|------|------------|----------------|
| `robot_observe` | `[observe] room={room}, {w}×{h} → image` 或 `[observe] room={room} → {描述前 100 字}` | 青色 |
| `memory_retriever` | `[memory] query="{q}" → {N} hits, top-1: {hit[:200]}` | 蓝色 |
| `robot_navigate` | `[navigate] → {room} ({success/fail})` | 绿色 |
| `robot_verify` | `[verify] {target}: {pass/fail} - {reason[:100]}` | 黄色 |
| 默认 | `[{tool}] N lines, last 10: {tail}` | 默认色 |

### 5.3 与压缩 spec 的关系

压缩 spec §3.1.2 的"工具结果按类型摘要化"调用的就是这套规则表的 `summary()` 方法。一套规则两处用：

- **终端渲染**：`summary()` + `terminal_format()` → 实时显示
- **压缩摘要**：`summary()` → 替换原始工具结果

**`summary` 签名约定**：`summary(self, tool_result: ToolResultMessage) -> str`——接收 `ToolResultMessage` 对象（不是 dict）。压缩 spec 调用时从 event payload 重建 `ToolResultMessage` 或直接传 message 对象。

**render 规则不展示 token 消耗**：`ToolResultMessage` 不持 tokens 字段，render 规则只展示工具结果内容。如果需要 tokens 信息，从 event payload 的 `usage` 字段取（由 `usage.update` 事件携带）。

---

## 六、事件系统增强

### 6.1 RuntimeEvent 扩展

当前 RuntimeEvent 类型不全。新增/修改：

```python
# events/runtime_events.py

class RuntimeEventType(StrEnum):
    # 现有
    TURN_START = "turn.start"
    TURN_END = "turn.end"
    ITER_START = "iter.start"
    TOOL_CALL_STARTED = "tool.call_started"
    TOOL_CALL_COMPLETED = "tool.call_completed"
    TOOL_CALL_FAILED = "tool.call_failed"

    # 新增
    ASSISTANT_THINKING = "assistant.thinking"
    ASSISTANT_REPLY = "assistant.reply"
    CONTEXT_COMPACTION = "context.compaction"
    CONTEXT_LENGTH_ERROR = "context.length_error"
    USAGE_UPDATE = "usage.update"
```

### 6.2 事件 payload 增强

`generic_runtime.py` emit 事件时填充完整内容：

```python
# tool.call_completed 的 payload
emit(
    "tool.call_completed",
    tool_call_id=tr.tool_call_id,
    name=tr.name,
    payload={
        "is_error": tr.is_error,
        "result": tr.result,           # ← 新增：完整结果
        "args": tr.args,               # ← 新增：完整参数
        "duration_ms": dispatch_ms,
    },
)

# assistant.thinking（新事件）—— 在 generic_runtime.py:236 session.append(assistant_msg) 后 emit
emit(
    "assistant.thinking",
    payload={"thinking": assistant_message.reasoning_content},
)

# assistant.reply（新事件）—— 同上位置，session.append(assistant_msg) 后 emit
emit(
    "assistant.reply",
    payload={"reply": assistant_message.text},
)
```

**emit 位置说明**：`assistant.thinking` 和 `assistant.reply` 在 `generic_runtime.py` 的 `session.append(assistant_msg)` 之后立即 emit。`assistant.thinking` 仅在 `reasoning_content` 非空时 emit；`assistant.reply` 仅在 `content` 非空时 emit。

### 6.3 渠道适配预留

事件 sink 设计支持渠道扩展：

```python
class EventSink(Protocol):
    def emit(self, event: RuntimeEvent) -> None: ...

class ConsoleEventSink(EventSink):
    """CLI 终端渲染，中等粒度。"""

class VerboseConsoleEventSink(EventSink):
    """CLI 终端渲染，完整粒度（--verbose）。"""

class JsonlTraceSink(EventSink):
    """写 trace.jsonl，完整 payload。保持文件句柄常开（构造时 open，close() 时关）。
    增加 flush() 方法——每 N 个事件或迭代末尾调用一次。"""

class MessagesLogSink(EventSink):
    """写 messages.jsonl，只记用户消息和模型回复。"""

# 未来
class FeishuEventSink(EventSink):
    """飞书渠道，摘要化输出。"""
```

**`SessionSnapshotSink` 不是事件 sink**：`save_snapshot` 不是逐个事件驱动的——`SessionSnapshotSink` 收到单个事件时无法判断"迭代是否结束"。正确做法：由 `GenericAgentRuntime.run()` 在迭代末尾**主动调用** `AgentSession.save_snapshot()`。详见 session-persistence-spec §4。

**sink 组合用现有 `FanoutEventSink`**（`events/sinks.py:101-115` 已定义），不新增 `CompositeEventSink`（语义完全重复）。

CLI 模式下注入：

```python
from homemaster.events.sinks import FanoutEventSink

sink = FanoutEventSink([
    ConsoleEventSink(verbose=args.verbose),
    JsonlTraceSink(session_dir),
    MessagesLogSink(session_dir),
])
```

**注意**：`SessionSnapshotSink` 不在 sink 列表中——`save_snapshot` 由 runtime 在迭代末尾主动调用。

---

## 七、CLI 命令扩展

### 7.1 新增参数

```bash
hm run "把水杯拿到客厅"                    # 默认中等粒度
hm run "..." --verbose                     # 完整 thinking + 完整工具结果
hm run "..." --quiet                       # 只输出最终回复
hm run "..." --resume <session_id>         # 从指定 session 恢复
hm run "..." --continue                    # 从最新 session 恢复
hm run "..." --session-dir <path>          # 自定义 session 目录
```

### 7.2 新增子命令（未来）

```bash
hm session list                            # 列出所有 session
hm session show <id>                       # 查看 session 元信息
hm trace show <session_id>                 # 格式化查看 trace
hm trace show <session_id> --iter 3        # 只看第 3 次迭代
hm session clean --older-than 30d          # 清理 30 天前的 session
hm session export <id> --format markdown   # 导出为 markdown
```

当前 PR 只做 `--verbose` / `--quiet` / `--resume` / `--continue`，其他未来做。

---

## 八、配置项

**新增 `ObservabilityConfig` 类**（不是 `ContextPolicyConfig`），在 `config/observability.py` 中定义，Pydantic BaseModel。所有与可观测性、session 持久化、中断相关的配置统一放在这里：

```python
# config/observability.py
from pydantic import BaseModel
from pathlib import Path

class ObservabilityConfig(BaseModel):
    # ── 终端渲染 ──
    console_enabled: bool = True
    console_show_thinking: bool = True
    console_thinking_first_line_only: bool = True
    console_verbose_to_expand: bool = True
    default_output_level: str = "medium"  # medium / verbose / quiet

    # ── trace.jsonl ──
    trace_dir: str = "~/.homemaster/trace"
    trace_full_payload: bool = True       # 不截断
    trace_rotation_max_mb: int = 100      # 单文件最大 MB，超过滚动

    # ── session 持久化（session-persistence-spec 引用）──
    session_dir: str = "~/.homemaster/sessions"
    save_session_per_iteration: bool = True  # 每次迭代后保存
    save_on_sigint: bool = True              # SIGINT 时保存
    strip_images_in_snapshot: bool = True    # session.json 不存图像

    # ── 中断（interruption-spec 引用）──
    interrupt_enabled: bool = True            # 是否启用 SIGINT 中断
    interrupt_abort_llm_stream: bool = True   # LLM 阶段是否立即中断 stream（False = S2 退回）

    # ── 数据格式版本 ──
    # 实际应为代码常量 SCHEMA_VERSION = 1，放在这里是便于配置统一管理，用户不应修改
    schema_version: int = 1
```

**说明**：`schema_version` 实际是代码常量（`SCHEMA_VERSION = 1`），放在配置中是便于统一管理，用户不应修改。

**其他 spec 引用**：session-persistence-spec 和 interruption-spec 均从 `ObservabilityConfig` 读取各自字段，不再各自定义。

---

## 九、影响范围估算

**新增**：
- `config/observability.py` ~60 行
  - `ObservabilityConfig` 类（本 spec §8 定义，session-persistence 和 interruption 引用）
- `events/sinks.py` 重写 + ~400 行
  - `ConsoleEventSink`（中等粒度）
  - `VerboseConsoleEventSink`
  - `JsonlTraceSink`（保持文件句柄常开，每 N 事件 flush）
  - `MessagesLogSink`
  - 复用现有 `FanoutEventSink`（不新增 `CompositeEventSink`）
- `agent/tool_render_rules.py` ~150 行
  - 工具渲染规则表
- `events/runtime_events.py` 扩展 + ~50 行
  - 新事件类型
  - payload 字段
- `events/sanitizer.py` 改造：
  - 新增 `sanitize_for_trace(value)` 函数（只脱敏不截断），`JsonlTraceSink` 用这个
  - 现有 `sanitize` 函数保持不变（脱敏 + 截断，Console 用）

**修改**：
- `generic_runtime.py`：
  - emit 时填充完整 payload
  - `session.append(assistant_msg)` 后新增 emit `assistant.thinking`（reasoning_content 非空时）和 `assistant.reply`（content 非空时）
  - 新增 thinking/reply/compaction 事件 emit
  - 支持多 sink 注入（`FanoutEventSink`）
- `cli.py`：
  - 新增 `--verbose` / `--quiet` / `--resume` / `--continue` 参数
  - session 目录初始化
  - sink 组合注入
- `agent/session.py`：
  - 新增 `save_snapshot()` / `load_snapshot()` 方法
  - 序列化/反序列化
- `turn.py:128, 137`：
  - `ConsoleProgressEventSink` 引用改为新的 `RichConsoleSink`

**删除**：
- `ConsoleProgressEventSink`（被 `ConsoleEventSink` 取代）
- `SessionSnapshotSink` 概念（save_snapshot 由 runtime 主动调用，不是事件 sink）
- `CompositeEventSink`（用现有 `FanoutEventSink` 替代）

---

## 十、与其他重构的依赖关系

| 依赖项 | 关系 |
|--------|------|
| 压缩 spec §7 可观测性 | 本 spec 实现压缩 spec 的可观测性需求 |
| 压缩 spec §3.1.2 工具结果摘要化 | 共用 `ToolRenderRule` 规则表 |
| 压缩 spec §6 token 估算 | `usage.update` 事件 emit 真实 usage |
| audit 第五节 Session 持久化 | 本 spec 实现 session.json 快照 |
| audit 第六节 终端可观测性 | 本 spec 直接解决 |
| audit 1.5 死函数 | `ConsoleProgressEventSink` 删除 |

---

## 十一、落地分两个 PR

### PR1：可观测性核心

1. `RuntimeEvent` 类型扩展（§6.1）
2. `generic_runtime.py` emit 时填充完整 payload + `session.append(assistant_msg)` 后 emit thinking/reply（§6.2）
3. `ConsoleEventSink`（中等粒度，§3）
4. `VerboseConsoleEventSink`（完整粒度）
5. `JsonlTraceSink`（写 trace.jsonl，句柄常开 + flush + rotation）
6. `MessagesLogSink`（写 messages.jsonl）
7. `FanoutEventSink`（复用现有，多 sink 组合）
8. `tool_render_rules.py` 规则表（§5）
9. CLI 新增 `--verbose` / `--quiet` 参数
10. `session.py` 新增 `save_snapshot()` / `load_snapshot()`
11. `ObservabilityConfig` 配置项（§8，在 `config/observability.py` 定义）
12. `turn.py:128, 137` 的 `ConsoleProgressEventSink` 改为 `RichConsoleSink`
13. 删除 `ConsoleProgressEventSink`

### PR2：resume + trace 查看

14. CLI 新增 `--resume` / `--continue` 参数
15. `hm session list` 子命令
16. `hm session show <id>` 子命令
17. `hm trace show <session_id>` 子命令（格式化查看）
18. `hm session export <id> --format markdown` 导出
19. `hm session clean` 清理旧 session

---

## 十二、决策汇总（已全部确认）

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 三文件分离 | trace.jsonl + session.json + messages.jsonl | debug/resume/对话历史各司其职 |
| thinking 显示 | c：默认首行 + `...`，`--verbose` 展开 | 平衡信息密度与刷屏 |
| 工具渲染规则表 | 终端与压缩共用 `ToolRenderRule` | 一套规则两处用，避免重复 |
| trace 字段设计 | 完整未截断 payload + tokens（含 cache_read） | debug 用，终端显示才摘要化 |
| resume 粒度 | a：每次迭代后保存 session.json | 真正的崩溃恢复 |
| 输出风格 | 中等粒度（b 风格） | 工具调用 + 结果摘要 |
| 渲染库 | rich | Panel/Markdown/Syntax/Spinner |
| 异步输出 | 事件队列 + 独立消费协程 | 不阻塞 agent 循环 |
| 渠道适配 | 事件 sink 抽象，当前只做 CLI | 预留飞书/微信/gateway |
| 会话快照格式 | JSON（indented，可读） | 自包含，resume 简单 |
| trace 格式 | JSONL（append-only） | O(1) 写入，适合高频 |
| 飞书/微信 | 当前不做，未来 gateway 专门做 | 专注 CLI |

---

## 十三、未决事项

无。所有决策已确认。实施时如遇新问题再讨论。

---

## 十四、评审修复记录（2026-06-27）

| # | 问题 | 修复 |
|---|------|------|
| 1 | `ObservabilityConfig` 定义含糊（"在 ContextPolicyConfig 或新 ObservabilityConfig 中"） | §8 明确"新增 `ObservabilityConfig` 类"，给出完整 Pydantic 定义，包含所有三份 spec 引用的字段。说明 `schema_version` 实际是代码常量 |
| 2 | `SessionSnapshotSink` 作为 EventSink 不合理（单个事件无法判断迭代结束） | §6.3 删除 `SessionSnapshotSink`，说明 save_snapshot 由 runtime 在迭代末尾主动调用 |
| 3 | `CompositeEventSink` 和现有 `FanoutEventSink` 语义重复 | §6.3 删除 `CompositeEventSink`，改用现有 `FanoutEventSink` |
| 4 | `events/sanitizer.py` 截断 payload，与 trace 完整 payload 冲突 | §4.2 明确 trace.jsonl 走旁路不经过 sanitizer 截断；§9 加 `sanitize_for_trace()` 函数（只脱敏不截断） |
| 5 | trace.jsonl 无 rotation 策略 | §4.2 加 rotation 策略（单文件超 `trace_rotation_max_mb` 滚动） |
| 6 | `JsonlTraceSink` 每次 emit 都 open/close 文件 | §6.3 明确句柄常开（构造时 open），增加 flush() 方法 |
| 7 | `ConsoleProgressEventSink` 删除迁移未列 | §9 加迁移路径：`turn.py:128,137` 引用改为 `RichConsoleSink` |
| 8 | `tool_render_rules.summary()` 类型契约不一致 | §5.3 明确 `summary` 接收 `ToolResultMessage`（不是 dict），压缩 spec 调用时重建或传 message 对象 |
| 9 | `robot_observe` render 规则含 `{tokens}` 但 `ToolResultMessage` 不持 tokens | §5.2 移除 `{tokens}`，§5.3 说明 render 规则不展示 token 消耗 |
| 10 | `assistant.thinking` / `assistant.reply` emit 位置未明确 | §6.2 明确 `generic_runtime.py:236` `session.append(assistant_msg)` 后 emit |
| 11 | thinking "首行"定义模糊 | §3.2 明确"首行 = 第一个 `\n` 之前的内容，最多 200 字符" |
| 12 | 异步队列丢事件风险 | §3.4 明确同步/异步分工：`JsonlTraceSink` 同步，`RichConsoleSink` 异步 |
