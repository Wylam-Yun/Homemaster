# HomeMaster V1.6 代码审计与重构方案

Date: 2026-06-22

## 背景

V1.5 完成了 agent-loop 迁移、task-state、context assembly、compaction 等核心功能。但迁移不彻底，存在大量遗留代码、死代码、命名混乱、过度抽象等问题。本文档记录所有发现的问题和重构方案。

---

## 一、上下文压缩逻辑问题

### 1.1 micro-compaction 未接入

`compact.py` 中定义了 `microcompact_old_tool_results()` 函数（保留最近 N 个工具结果完整，截断更早的工具结果为 head 900 + tail 500 字符），但 **从未被任何生产代码调用**。

- 定义位置：`src/homemaster/agent/compact.py:25`
- 调用情况：src/ 中零引用，tests/ 中零引用
- 预期行为：在 summary compaction 之前，先截断旧工具结果，减少 token 占用

### 1.2 LLM 摘要未接入

`compact_summary_prompt.txt` 写了详细的 LLM 摘要规则（结构化输出：User Goal、Completed Work、Active Issues 等），但 `build_basic_summary()` **不调 LLM**，只是机械提取每条消息前 500 字符拼接。

- prompt 文件：`src/homemaster/prompts/compact_summary_prompt.txt`
- 实际调用：`context_assembler._compact()` → `build_basic_summary()` → 纯字符串截断
- 问题：工具调用参数、结构化数据、中间推理全部丢失

### 1.3 当前压缩流程

```
context_assembler.prepare()
  → 估算 token，超过阈值？
  → context_assembler._compact()
    → split_preserving_recent_context()    // 分 old/recent
    → build_basic_summary(older)           // 机械截断，每条消息前 500 字符
    → build_compaction_summary_message()   // 包一层 "[CONTEXT COMPACTION]" 头
```

### 1.4 应有的压缩流程

```
context_assembler.prepare()
  → 估算 token，超过阈值？
  → context_assembler._compact()
    → microcompact_old_tool_results()      // 第一步：截断旧工具结果
    → split_preserving_recent_context()    // 第二步：分 old/recent
    → LLM summary compaction               // 第三步：调 LLM 生成结构化摘要（用 compact_summary_prompt.txt）
    → build_compaction_summary_message()   // 第四步：包装
```

### 1.5 compact.py 死函数

| 函数 | 状态 |
|------|------|
| `build_basic_summary` | 活跃（但应该改为调 LLM） |
| `build_compaction_summary_message` | 活跃 |
| `split_preserving_recent_context` | 活跃 |
| `compact_tool_result_text` | 死（仅被 microcompact_old_tool_results 内部调用） |
| `microcompact_old_tool_results` | 死（未接入） |
| `sanitize_tool_pairs` | 死（零引用） |
| `split_preserving_tool_pairs` | 死（仅测试引用） |

---

## 二、配置系统问题

### 2.1 两套配置并存

- **旧路径**：`runtime.py` → `load_provider_config()` → 读 `config/api_config.json`（扁平格式）
- **新路径**：`config/model_config.py` → `load_model_config()` → 读 `config/homemaster.json`（Pydantic 类型化）
- **桥接**：`config/resolution.py` 先试新路径，fallback 到旧路径

问题：开发阶段不应该有 fallback，应该只读一份配置。

### 2.2 runtime.py 命名冲突

`runtime.py`（根目录）是配置加载模块，`agent/generic_runtime.py` 是 agent 执行循环。两者职责完全不同，名字几乎一样。

`runtime.py` 被 12+ 个文件导入（`RuntimeConfigError`、`ProviderConfig`、路径常量），是活跃代码但命名误导。

### 2.3 runtime.py 导入时副作用

`runtime.py` 第 125-143 行在模块导入时执行文件 I/O（读 `homemaster.json`），任何 `from homemaster.runtime import RuntimeConfigError` 都会触发磁盘读取。

### 2.4 config/ 文件过多

| 文件 | 行数 | 职责 | 状态 |
|------|------|------|------|
| `model_config.py` | 120 | Pydantic 配置模型 + 加载器 | 主文件 |
| `runtime_settings.py` | 82 | 运行时设置 | 可并入 model_config |
| `model_profiles.py` | 43 | 只知道 mimo v2.5 的 context window | 可内联 |
| `resolution.py` | 59 | 旧/新配置桥接 | 统一配置后删除 |
| `runtime_paths.py` | 39 | 一个 validate_run_id 函数 | 生产代码零引用，可内联或删除 |

### 2.5 nvidia_api_config.json 完全死文件

全项目零引用，无任何代码、测试、文档引用此文件。包含重复的 API key。可直接删除。

### 2.6 homemaster.example.json

仅文档用，运行时不加载。真正的 `homemaster.json` 不存在于磁盘上，运行时静默使用默认值。

---

## 三、死代码清单

### 3.1 compact.py 死函数（见 1.5）

### 3.2 context_items.py 死 enum 值

13 个 enum 值中 10 个从未被引用：

- `ContextPriority.AUXILIARY`、`ContextPriority.TRACE_ONLY`
- `ContextFreshness.RECENT`、`ContextFreshness.OLD`、`ContextFreshness.ARCHIVED`
- `ContextPlacement.SYSTEM_PROMPT`、`ContextPlacement.TOOL_SCHEMA`
- `RenderMode.COMPACT`、`RenderMode.SUMMARY`、`RenderMode.POINTER`

### 3.3 context_budget.py 死字段

- `ContextBudget.recent_tail_budget_tokens` — 属性从未被读取
- `ContextBudget.image_token_estimate` — 字段被设置但从未被读取

### 3.4 compat.py

Python 3.10 `StrEnum` polyfill。`pyproject.toml` 要求 `>=3.11`，整个文件可删，3 处 import 改 `from enum import StrEnum`。

---

## 四、文件组织问题

### 4.1 根目录散落文件

以下 7 个文件散落在 `src/homemaster/` 根目录，不属于任何文件夹：

| 文件 | 应归入 |
|------|--------|
| `runtime.py` | `config/` |
| `llm_client.py` | `providers/` |
| `embedding_client.py` | `providers/` |
| `trace.py` | `events/` |
| `logger.py` | `events/` |
| `prompt_loader.py` | `prompts/` |
| `compat.py` | 删除 |

### 4.2 domain/home/ 多余嵌套

当前只有一个领域（家庭机器人），`domain/home/` 可简化为 `domain/`。

### 4.3 agent/ 文件过多

11 个文件，其中 `context_items.py`、`context_budget.py`、`context_providers.py`、`compact.py`、`context_assembler.py` 5 个文件都是上下文管理相关，可合并为一个 `context.py`。

### 4.4 prompts 用 .txt 不用 .md

没有技术原因。`.md` 更通用，可利用 markdown 格式化。`compact_summary_prompt.txt` 的结构化输出用 `.md` 更自然。

---

## 五、Session 无持久化

`session.py` 是纯内存容器，零持久化逻辑。没有 `save()`、`load()`、序列化、数据库写入。进程死了聊天记录就没了。`session_id` 只是标签，不做查找。

**需要添加：** JSON 序列化，支持 save/load/resume。

---

## 六、终端可观测性缺失

### 6.1 现状

CLI 只输出最终结果：

```
assistant: <最终回复>
status: replied
trace: /path/to/trace
```

中间过程完全看不到：模型的思考、工具调用、工具结果。

### 6.2 问题

- `ConsoleProgressEventSink` 只打印事件类型和工具名，不打印内容
- `generic_runtime.py` 的事件 payload 信息不足：
  - `tool.call_completed` 的 payload 只有 `{"is_error": false}`，没有结果内容
  - assistant message 的 thinking/reasoning 没有 emit
- `JsonlEventSink` 写的 JSONL trace 文件大概率没人看

### 6.3 目标

终端实时输出完整 agent 过程：

```
> 用户: 把水杯拿到客厅

[思考] 用户要我找水杯并拿到客厅，先检索记忆...

[工具调用] memory_retriever({"query": "水杯", ...})
[工具结果] 找到 3 条记忆: 水杯在厨房桌上...

[工具调用] robot_navigate({"room": "kitchen"})
[工具结果] 已到达厨房

[回复] 已找到水杯，在厨房桌上。正在拿取并送往客厅。
```

### 6.4 方案

1. `generic_runtime.py` emit 事件时填充完整内容（thinking、reply、tool args、tool result）
2. `ConsoleProgressEventSink` 改为异步队列 + 完整打印（不阻塞 agent 循环）
3. 保持 RuntimeEvent 结构和事件类型
4. `JsonlEventSink` 保留（写完整 payload 到 JSONL，供后续分析）

---

## 七、providers/ 过度抽象

### 7.1 抽象层多余

`transport.py` 定义 `LLMTransport` 抽象基类，`mimo_transport.py` 是 **唯一实现**。当前只有一个 provider，抽象层是多余的。

### 7.2 假 streaming

`MimoTransport.stream()` 名为 stream，实际做同步 POST 然后 yield deltas。注释写着 "Real SSE streaming can be added later"。

### 7.3 llm_client.py 功能重叠

`llm_client.py`（根目录）是独立的单次 JSON 调用客户端，给 `memory/retrieval.py` 和 `cli/doctor.py` 用。和 `MimoTransport` 功能重叠但 JSON 提取逻辑不同。

---

## 八、Events/ 系统评估

### 8.1 当前规模

3 个文件 228 行：`runtime_events.py`（58 行）、`sanitizer.py`（47 行）、`sinks.py`（123 行）。

### 8.2 评估

- `RuntimeEvent` 结构合理，保留
- `EventSink` Protocol 合理，保留
- `JsonlEventSink` — 如果终端可观测性做好了，JSONL trace 的价值降低，但保留供 benchmark 分析
- `sanitizer.py` — 开发阶段不需要脱敏，但保留无害
- `ConsoleProgressEventSink` — 需要增强（见第六节）

---

## 九、Memory/ 系统评估

### 9.1 现状

纯 RAG 系统，读 JSON `object_memory.json`，不管理任何 `.md` 文件。4 个文件：

- `index.py`（282 行）— BM25 索引 + embedding 缓存
- `tokenizer.py`（115 行）— jieba 中文分词
- `retrieval.py`（1072 行）— RAG 编排，最大的文件
- `runtime_store.py`（109 行）— 运行时记忆覆盖层

### 9.2 问题

- `retrieval.py` 1072 行过大，包含 LLM query 生成、embedding 适配、hit 融合、trace 输出、5 个硬编码测试用例
- 没有 memory.md / user.md 管理（auto-memory 功能缺失）

---

## 十、重构方案汇总

### 10.1 删除

| 目标 | 原因 |
|------|------|
| `compat.py` | Python >=3.11 不需要 |
| `config/nvidia_api_config.json` | 零引用 |
| `config/runtime_paths.py` | 生产代码零引用 |
| `config/resolution.py` | 统一配置后桥接层消失 |
| `compact.py` 中 4 个死函数 | 未接入或零引用 |
| `context_items.py` 中 10 个死 enum 值 | 从未被引用 |
| `context_budget.py` 中 2 个死字段 | 从未被读取 |

### 10.2 合并

| 来源 | 目标 | 说明 |
|------|------|------|
| `runtime.py` + `config/model_config.py` + `config/runtime_settings.py` + `config/model_profiles.py` | `config/config.py` | 统一配置，~250 行 |
| `agent/context_items.py` + `agent/context_budget.py` + `agent/context_providers.py` + `agent/compact.py` + `agent/context_assembler.py` | `agent/context.py` | 上下文管理统一，~600 行 |
| `providers/transport.py` + `providers/mimo_transport.py` | `providers/transport.py` | 去掉抽象基类 |
| `domain/home/*` → `domain/*` | 去掉 `home/` 子目录 | 当前只有一个领域 |

### 10.3 移动

| 来源 | 目标 | 说明 |
|------|------|------|
| `llm_client.py` | `providers/llm_client.py` | 和 transport 同属传输层 |
| `embedding_client.py` | `providers/embedding_client.py` | 同上 |
| `trace.py` | `events/trace.py` | 和 events/ 同属观测层 |
| `logger.py` | `events/logger.py` | 同上 |
| `prompt_loader.py` | `prompts/loader.py` | 和 prompts/ 放一起 |

### 10.4 新增/增强

| 目标 | 内容 |
|------|------|
| 上下文压缩 | 接入 micro-compaction + LLM 摘要 |
| Session 持久化 | JSON 序列化，save/load/resume |
| 终端可观测性 | 增强事件 payload + 异步打印完整 agent 过程 |
| prompts 格式 | `.txt` → `.md` |

### 10.5 目标文件夹结构

```
src/homemaster/
├── __init__.py
├── cli.py                    ← 合并 cli/ 6 个文件
├── config/
│   ├── config.py                (统一配置)
│   └── api_config.json          (唯一配置文件)
├── engine/                   ← 原 agent/
│   ├── messages.py
│   ├── session.py               (+ 持久化)
│   ├── state.py
│   ├── context.py               (合并 5 个文件)
│   └── runtime.py               (GenericAgentRuntime)
├── providers/
│   ├── transport.py             (MimoTransport，去掉抽象)
│   ├── llm_client.py            (从根目录移入)
│   └── embedding_client.py      (从根目录移入)
├── domain/                   ← 去掉 home/ 嵌套
│   ├── contracts.py
│   ├── grounding.py
│   ├── tools.py
│   └── tool_registry.py
├── memory/                   ← 保持不变
│   ├── index.py
│   ├── tokenizer.py
│   ├── retrieval.py
│   └── runtime_store.py
├── events/
│   ├── events.py                (RuntimeEvent + EventSink)
│   ├── sinks.py                 (增强 ConsoleProgressEventSink)
│   ├── sanitizer.py
│   ├── trace.py                 (从根目录移入)
│   └── logger.py                (从根目录移入)
├── prompts/
│   ├── loader.py                (从根目录移入)
│   └── *.md                     (.txt → .md)
├── skills/                   ← 保持不变
├── task_state/               ← 保持不变
├── tools/                    ← 保持不变
└── benchmarking/             ← 保持不变
```

---

## 十一、长程任务问题分析

以下问题在任务迭代次数增多（20+ 次工具调用）时会暴露。

### 11.1 压缩后信息丢失严重（致命）

当前压缩是机械截断（每条消息前 500 字符），压缩后：

- 工具调用参数全部丢失 — agent 不知道之前调了什么工具、传了什么参数
- 工具结果全部丢失 — agent 不知道之前观察到了什么
- 推理过程全部丢失 — agent 不知道之前为什么做那些决策

Task state snapshot 作为 CONTEXT_PRELUDE 单独注入（不被压缩），但它是结论（"当前在做 fetch_object"），没有支撑证据（"为什么去厨房、观察到了什么"）。agent 知道目标但忘了过程。

后果：agent 压缩后会重复执行已经做过的操作。比如已经找到水杯在厨房桌上，压缩后又去搜索、又去导航、又去观察。

### 11.2 No-progress 检测太粗糙（高）

`_tool_result_signature` 只比对工具名 + 是否错误 + 结果前 300 字符：

```python
def _tool_result_signature(summaries):
    return tuple(
        (name, is_error, text[:300])
        for item in summaries
    )
```

- agent 在错误的房间找东西，每次结果都是 "未在 kitchen 找到目标" — 但如果错误消息略有变化（比如包含不同的搜索词），签名就不同，不算 no-progress
- agent 可以在 20 个不同位置搜索同一物体，每次都不同结果，no-progress 永远是 0
- 缺少"任务级"进度判断 — 只看单次工具结果是否重复，不看任务整体是否有推进

### 11.3 Token 累积速度不可控（中）

每次迭代增加：assistant message (~200-2000 tokens) + tool results (每个 ~100-2000 tokens)。

假设每次迭代 2 个工具调用、每个结果 500 tokens：
- 10 次迭代 ≈ 15,000 tokens
- 30 次迭代 ≈ 45,000 tokens
- 加上 system prompt (~3000) + task state (~500) + 预留输出 (8192) + safety buffer (13000) ≈ 25,000

MiMo v2.5 的 context window 是 1M tokens，阈值 50% = 500K tokens，要 300+ 次迭代才触发压缩。但 context window 更小的模型（128K）30-40 次迭代就可能触发。

### 11.4 Reactive compaction 只重试一次（中）

```python
if _is_context_length_error(str(exc)):
    if reactive_compact_retries >= 1:  # 第二次就直接失败
        return GenericRunResult(status="failed", ...)
    reactive_compact_retries += 1
    self._context_assembler.force_compact_next = True
    continue
```

压缩后如果 context 仍然超长（比如压缩不够激进），直接失败。不尝试更激进的压缩策略（micro-compaction + summary compaction 组合），也不尝试丢弃更多旧消息。

### 11.5 ProviderUsage 被覆盖而非累加（高）

```python
# 每次迭代覆盖，不是累加
agent_state.provider_usage = ProviderUsage(
    input_tokens=input_tokens,
    output_tokens=output_tokens,
    total_tokens=input_tokens + output_tokens,
)
```

长程任务跑完后，只知道最后一次迭代的 token 消耗，不知道整个任务总共消耗了多少 token。对成本追踪和 benchmark 评估是致命的。

### 11.6 没有 checkpoint / resume（高）

- session 是纯内存，进程死了全部丢失
- 没有中间状态保存
- 长程任务跑了一半崩了，必须从头开始
- 没有办法从某个中间点恢复

### 11.7 工具结果大小无上限（中）

memory_retriever 返回最多 5 条 JSON 记录，但每条记录的大小没有限制。robot_observe、robot_verify 等工具的结果也没有截断。长程任务中大量工具结果会快速填满 context。

### 11.8 Loop guard 边界情况（中）

- `max_consecutive_tool_errors = 5` — 但如果 agent 交替调用成功和失败的工具（成功、失败、成功、失败...），consecutive 永远不到 5
- `max_no_progress_iterations = 20` — 但检测太粗糙（见 11.2）
- `max_wall_clock_minutes` 在 config 里定义了，但 `generic_runtime.py` 从未检查它。一个卡住的 LLM 调用可以让任务无限等待
- 没有总 token 预算 — 没有"这个任务最多花 100K tokens"的限制

### 11.9 没有用户中断机制（中）

- 长程任务运行时，用户没有办法中途打断并获取当前进度
- 没有 SIGINT handler
- 没有 `/stop` 命令
- 只能 kill 进程，但 kill 后什么都没有（无 checkpoint）

### 11.10 任务规划可能自我矛盾（低）

Task state 是模型自管理的。长程任务中：
- 模型可能在 iteration 5 制定了计划 A
- 压缩后，计划 A 的 CONTEXT_PRELUDE 还在，但支撑证据没了
- 模型可能在 iteration 20 基于新信息制定计划 B，和计划 A 矛盾
- 没有机制检测或解决计划冲突

### 11.11 长程任务问题优先级

| 问题 | 严重程度 | 影响 |
|------|----------|------|
| 压缩后信息丢失 | 致命 | agent 重复操作、忘记已知信息 |
| No-progress 检测粗糙 | 高 | agent 可以无限循环做无用功 |
| 没有 checkpoint/resume | 高 | 崩了就从头来 |
| ProviderUsage 覆盖 | 高 | 无法追踪总成本 |
| 没有 wall-clock 超时 | 高 | 可能无限等待 |
| 没有用户中断 | 中 | 无法中途停止 |
| 工具结果无大小限制 | 中 | context 快速膨胀 |
| Reactive compaction 只重试一次 | 中 | 压缩不够就直接失败 |
| Token 累积速度不可控 | 中 | 小模型下快速触发压缩 |
| 任务规划自我矛盾 | 低 | 压缩后可能出现 |
