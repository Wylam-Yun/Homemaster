# 文件组织重构 Spec

Date: 2026-06-27

关联：[audit-and-refactor-spec.md](audit-and-refactor-spec.md) 第四节
依赖：[config-refactor-spec.md](config-refactor-spec.md)（`compat.py` / `runtime.py` / `llm_client.py` 已在该 spec 处置）
依赖：[providers-refactor-spec.md](providers-refactor-spec.md)（`llm_client.py` 已在该 spec 处置）
依赖：[observability-spec.md](observability-spec.md)（`trace.py` 的 JSONL 写入将被 `JsonlTraceSink` 替代）
依赖：[context-compaction-spec.md](context-compaction-spec.md)（`compact.py` 将重写）

---

## 一、问题现状（已验证）

### 1.1 根目录散落文件（本节处理 4 个）

审计 4.1 列了 7 个根目录散落文件，其中 3 个已被前面 spec 覆盖：

| 文件 | 已覆盖于 |
|------|----------|
| `runtime.py` | config-refactor-spec（迁入 `config/config.py`） |
| `llm_client.py` | providers-refactor-spec（并入 `providers/llm_client.py`） |
| `compat.py` | config-refactor-spec（删除，3 处 import 改 `from enum import StrEnum`） |

**本节处理剩下 4 个**：

| 文件 | 行数 | 应归入 | 当前导入方 |
|------|------|--------|-----------|
| `embedding_client.py` | 236 | `providers/embedding_client.py` | memory/index.py、memory/retrieval.py |
| `trace.py` | 47 | `events/`（与 sanitizer 合并） | agent/turn.py、cli/run_command.py、cli/app.py |
| `logger.py` | 32 | `events/logger.py` | 8+ 文件 |
| `prompt_loader.py` | 66 | `prompts/loader.py` | agent/turn.py、cli/doctor.py、memory |

### 1.2 `domain/home/` 多余嵌套

```
src/homemaster/domain/home/
├── contracts.py
├── grounding.py
├── tool_registry.py
└── tools.py
```

当前只有一个领域（家庭机器人），`home/` 子目录多此一举。6 个文件 import `from homemaster.domain.home.X`。

### 1.3 `agent/` 上下文管理文件碎片化

5 个上下文管理相关文件（共 691 行）：

| 文件 | 行数 | 职责 |
|------|------|------|
| `context_items.py` | 55 | ContextItem + 4 个 enum（10/13 死值） |
| `context_budget.py` | 59 | token 估算 + BudgetDecision |
| `context_providers.py` | 165 | 4 个 ContextProvider 实现 |
| `compact.py` | 183 | 压缩函数（多数死代码，将由压缩 spec 重写） |
| `context_assembler.py` | 229 | 编排器 |

### 1.4 prompts 用 `.txt` 不用 `.md`

```
prompts/
├── agent_system_prompt.txt
├── compact_summary_prompt.txt
├── memory_query_prompt.txt
├── memory_query_retry.txt
├── task_interpreter_prompt.txt
└── task_summary_prompt.txt
```

`prompt_loader.py:54` 硬编码 `f"{name}.txt"`。无技术原因。

### 1.5 `trace.py` 与 `events/sanitizer.py` 功能重叠

`trace.py:34` 的 `sanitize_for_log` 和 `events/sanitizer.py` 都做敏感字段脱敏。两套实现并存。

---

## 二、设计决策

### 2.1 决策 A：`agent/` 上下文文件合并

**选 A3：合并成 2 文件**——`context.py`（items+budget+providers+assembler）+ `compact.py`（压缩逻辑，独立保留）。

**理由**：

- 压缩 spec 会重写 `compact.py`（接 micro-compaction + LLM 摘要），让它独立保留合理
- 其余 4 文件（items/budget/providers/assembler）都是"组装"职责，合并成 `context.py`（~508 行）更紧凑
- 单文件 508 行可接受（比 5 个小文件来回跳好）

**合并后**：

```
agent/
├── context.py          (~508 行，合并自 items+budget+providers+assembler)
├── compact.py          (重写，由压缩 spec 负责)
├── generic_runtime.py
├── messages.py
├── session.py
├── state.py
├── turn.py
└── normalized.py
```

**`agent/__init__.py` re-export**：

```python
from homemaster.agent.context import (
    ContextItem, ContextPriority, ContextPlacement, ContextBudget,
    BudgetDecision, ContextProvider, ConversationProvider,
    TaskStateSnapshotProvider, RuntimeBudgetStatusProvider,
    FailureSummaryProvider, ContextAssembler, ComposedContext,
    estimate_text_tokens, estimate_json_tokens,
)
```

调用方统一 `from homemaster.agent import ContextAssembler, ...`。

### 2.2 决策 B：`domain/home/` → `domain/`

**选 B1：去掉 `home/` 嵌套**。

**理由**：当前只有一个领域，YAGNI。6 个 import 改路径即可。未来真有第二个领域（如 `office/`）再加嵌套。

**迁移后**：

```
domain/
├── __init__.py
├── contracts.py
├── grounding.py
├── tool_registry.py
└── tools.py
```

**import 改写**：

```python
# 之前
from homemaster.domain.home.contracts import ...
from homemaster.domain.home.grounding import ...
from homemaster.domain.home.tool_registry import ...
from homemaster.domain.home.tools import ...

# 之后
from homemaster.domain.contracts import ...
from homemaster.domain.grounding import ...
from homemaster.domain.tool_registry import ...
from homemaster.domain.tools import ...
```

### 2.3 决策 C：`trace.py` / `events/sanitizer.py` 合并

**选 C1：`trace.py` 删除，功能迁入 `events/`**。

**理由**：`sanitize_for_log` 是脱敏，归属 `events/sanitizer.py` 更合理（名字就叫 sanitizer）。`trace.py` 的 JSONL/JSON 写入功能在可观测性 spec 里会被 `JsonlTraceSink` 等替代。

**迁移**：

| `trace.py` 函数 | 迁入 |
|----------------|------|
| `sanitize_for_log` | `events/sanitizer.py`（合并到现有脱敏逻辑） |
| `append_jsonl_event` | `events/sinks.py`（可观测性 spec 的 `JsonlTraceSink` 内部用） |
| `write_json` | `events/sinks.py` |

**迁移后 `events/sanitizer.py` 提供**：

```python
def sanitize_for_log(value: Any) -> Any:
    """递归脱敏：api_key/token/authorization 等字段替换为 [REDACTED]。"""
    ...
```

调用方统一 `from homemaster.events.sanitizer import sanitize_for_log`。

### 2.4 决策 D：prompts `.txt` → `.md`

**选 D1：全部改 `.md`**。

**理由**：无技术原因。`.md` 更通用，结构化 prompt（如 `compact_summary_prompt` 的 JSON 输出格式）用 markdown 更自然。

**迁移**：

```
prompts/
├── agent_system_prompt.md       (原 .txt)
├── compact_summary_prompt.md    (原 .txt)
├── memory_query_prompt.md       (原 .txt)
├── memory_query_retry.md        (原 .txt)
├── task_interpreter_prompt.md   (原 .txt)
└── task_summary_prompt.md       (原 .txt)
```

**`prompts/loader.py`（原 `prompt_loader.py`）改动**：

```python
# 之前
path = _PROMPTS_DIR / f"{name}.txt"

# 之后
path = _PROMPTS_DIR / f"{name}.md"
```

**`pyproject.toml` package-data 改动**：

```toml
# 之前
[tool.setuptools.package-data]
homemaster = ["prompts/*.txt"]

# 之后
[tool.setuptools.package-data]
homemaster = ["prompts/*.md"]
```

### 2.5 根目录文件归位汇总

| 文件 | 行数 | 迁入 | 备注 |
|------|------|------|------|
| `embedding_client.py` | 236 | `providers/embedding_client.py` | 与 `providers/llm_client.py` 同属传输层 |
| `trace.py` | 47 | `events/`（拆分到 sanitizer + sinks） | 与 sanitizer 合并 |
| `logger.py` | 32 | `events/logger.py` | 与 events/ 同属观测层 |
| `prompt_loader.py` | 66 | `prompts/loader.py` | 与 prompts/ 放一起 |

---

## 三、待删除/合并清单

### 3.1 删除（本节独有）

| 文件 | 行数 | 处置 |
|------|------|------|
| `src/homemaster/trace.py` | 47 | 删除，`sanitize_for_log` 迁入 `events/sanitizer.py`，`append_jsonl_event`/`write_json` 迁入 `events/sinks.py` |
| `src/homemaster/logger.py` | 32 | 迁入 `events/logger.py` |
| `src/homemaster/prompt_loader.py` | 66 | 迁入 `prompts/loader.py` |
| `src/homemaster/embedding_client.py` | 236 | 迁入 `providers/embedding_client.py` |
| `src/homemaster/agent/context_items.py` | 55 | 合并入 `agent/context.py` |
| `src/homemaster/agent/context_budget.py` | 59 | 合并入 `agent/context.py` |
| `src/homemaster/agent/context_providers.py` | 165 | 合并入 `agent/context.py` |
| `src/homemaster/agent/context_assembler.py` | 229 | 合并入 `agent/context.py` |
| `src/homemaster/domain/home/` | 4 文件 | 提到 `domain/`，删 `home/` 目录 |

**总计：8 个根目录/agent 文件 + 1 个目录嵌套消失。**

### 3.2 重命名

| 来源 | 目标 |
|------|------|
| `prompts/*.txt`（6 个文件） | `prompts/*.md` |

### 3.3 import 迁移（约 20+ 文件）

```python
# 根目录文件归位
from homemaster.embedding_client import ...      → from homemaster.providers.embedding_client import ...
from homemaster.trace import ...                 → from homemaster.events.sanitizer import sanitize_for_log
                                                 + from homemaster.events.sinks import append_jsonl_event, write_json
from homemaster.logger import ...                → from homemaster.events.logger import ...
from homemaster.prompt_loader import ...         → from homemaster.prompts.loader import ...

# agent/ 上下文文件合并
from homemaster.agent.context_items import ...   → from homemaster.agent.context import ...
from homemaster.agent.context_budget import ...  → from homemaster.agent.context import ...
from homemaster.agent.context_providers import ... → from homemaster.agent.context import ...
from homemaster.agent.context_assembler import ... → from homemaster.agent.context import ...

# domain/home/ 去嵌套
from homemaster.domain.home.X import ...         → from homemaster.domain.X import ...
```

---

## 四、实现纪律

### 4.1 纯文件搬移，零逻辑改动

本 spec 只做文件搬移 + import 改路径 + 后缀改名。**不修改任何函数逻辑**。

`agent/context.py` 合并时，4 个文件的内容直接拼接（按 items → budget → providers → assembler 顺序），删除原文件的 module docstring（合并成一个），保留所有函数/类的实现。

### 4.2 合并后 `context.py` 结构

```python
"""Context assembly — items, budget, providers, assembler."""

# === from context_items.py ===
class ContextPriority(StrEnum): ...
class ContextFreshness(StrEnum): ...
class ContextPlacement(StrEnum): ...
class RenderMode(StrEnum): ...
@dataclass(frozen=True)
class ContextItem: ...

# === from context_budget.py ===
class BudgetDecision(Enum): ...
def estimate_text_tokens(text: str) -> int: ...
def estimate_json_tokens(value: object) -> int: ...
@dataclass(frozen=True)
class ContextBudget: ...

# === from context_providers.py ===
class ContextProvider(Protocol): ...
class TaskStateSnapshotProvider: ...
class RuntimeBudgetStatusProvider: ...
class FailureSummaryProvider: ...
class ConversationProvider: ...

# === from context_assembler.py ===
@dataclass
class ContextMetrics: ...
@dataclass
class ComposedContext: ...
class ContextAssembler: ...
def estimate_messages_tokens(messages): ...
def estimate_tools_tokens(tools): ...
```

### 4.3 `events/sanitizer.py` 合并后

`events/sanitizer.py` 现有脱敏逻辑 + `trace.py:34` 的 `sanitize_for_log` 合并。若两者实现一致，保留 `events/sanitizer.py` 现有版本，删除 `trace.py` 版本。若有差异，以 `events/sanitizer.py` 为准（更新更全）。

### 4.4 prompts `.md` 改名保留 git history

用 `git mv` 重命名（保留 file history）：

```bash
git mv src/homemaster/prompts/agent_system_prompt.txt src/homemaster/prompts/agent_system_prompt.md
# ... 其余 5 个
```

### 4.5 兼容性

- **不保留**旧 import 路径（不做 deprecation shim）
- 调用方一次性迁移，迁移完跑全量测试
- `prompts/*.txt` 改名后，`pyproject.toml` 的 `package-data` 同步更新

---

## 五、验证计划

### 5.1 单元测试

| 测试 | 断言 |
|------|------|
| `test_context_module_imports` | `from homemaster.agent.context import ContextAssembler, ContextItem, ContextBudget, ...` 全部成功 |
| `test_context_no_circular_import` | `agent/context.py` 不与 `agent/state.py`、`agent/session.py` 循环依赖 |
| `test_domain_imports` | `from homemaster.domain.contracts import ...` 等 4 个全部成功 |
| `test_events_sanitizer_imports` | `from homemaster.events.sanitizer import sanitize_for_log` 成功 |
| `test_events_logger_imports` | `from homemaster.events.logger import get_logger, setup_logging` 成功 |
| `test_prompts_loader_imports` | `from homemaster.prompts.loader import load_prompt, render, PromptId` 成功 |
| `test_prompts_md_loading` | `load_prompt(PromptId.AGENT_SYSTEM)` 能读到 `.md` 文件 |
| `test_providers_embedding_imports` | `from homemaster.providers.embedding_client import ...` 成功 |

### 5.2 集成验证

- `hm` CLI 启动正常
- `python -c "import homemaster.agent.context; import homemaster.domain; import homemaster.events.sanitizer; import homemaster.events.logger; import homemaster.prompts.loader; import homemaster.providers.embedding_client"` 无报错
- 现有所有测试通过（迁移 import 后）

### 5.3 静态检查

- `ruff check` 无 unused import
- `ruff check` 无 circular import 警告
- `grep -r "from homemaster.trace\|from homemaster.logger\|from homemaster.prompt_loader\|from homemaster.embedding_client\|from homemaster.domain.home\|from homemaster.agent.context_items\|from homemaster.agent.context_budget\|from homemaster.agent.context_providers\|from homemaster.agent.context_assembler"` 返回空（确认所有旧 import 已迁移）

### 5.4 黑盒门（§3 纪律）

| 门 | 断言 |
|----|------|
| 外部终态 | `hm` CLI 能加载所有 prompt（`.md` 文件真实被读取，不是 fallback 到默认值） |
| 返回码 | 所有测试 exit 0 |
| per-instance | 6 个 prompt 文件**分别**验证可加载（不是只验一个） |

---

## 六、PR 拆分

### PR1：根目录文件归位 + domain 去嵌套

1. `embedding_client.py` → `providers/embedding_client.py`
2. `trace.py` 拆分到 `events/sanitizer.py` + `events/sinks.py`
3. `logger.py` → `events/logger.py`
4. `prompt_loader.py` → `prompts/loader.py`
5. `domain/home/*` → `domain/*`（删 `home/` 目录）
6. 迁移约 20 文件的 import
7. 单测 + 集成验证
8. 文档同源更新

### PR2：agent/ 上下文文件合并 + prompts .md 改名

1. `agent/context_items.py` + `context_budget.py` + `context_providers.py` + `context_assembler.py` → `agent/context.py`
2. `agent/__init__.py` re-export
3. `prompts/*.txt` → `prompts/*.md`（`git mv`）
4. `prompts/loader.py` 改后缀
5. `pyproject.toml` 改 `package-data`
6. 迁移 import
7. 单测 + 集成验证
8. 文档同源更新

**PR2 可与压缩 spec 的 PR1 合并**：压缩 spec 会重写 `compact.py`，同时合并 `context.py` 能减少冲突。

---

## 七、目标文件夹结构（V1.6 完成后）

```
src/homemaster/
├── __init__.py
├── cli/
├── config/
│   ├── __init__.py
│   ├── config.py                (统一配置，由 config-refactor-spec)
│   └── (删除 model_profiles / runtime_settings / resolution / runtime_paths)
├── agent/
│   ├── __init__.py
│   ├── context.py               (合并 4 文件，本 spec)
│   ├── compact.py               (重写，由压缩 spec)
│   ├── generic_runtime.py
│   ├── messages.py
│   ├── session.py               (+ 持久化，由 session-persistence-spec)
│   ├── state.py
│   ├── turn.py
│   └── normalized.py
├── providers/
│   ├── __init__.py
│   ├── llm_client.py            (由 providers-refactor-spec)
│   ├── embedding_client.py      (本 spec，从根目录迁入)
│   ├── errors.py                (由 providers-refactor-spec)
│   ├── json_utils.py            (由 providers-refactor-spec)
│   └── transports/
│       ├── base.py
│       ├── types.py
│       ├── anthropic.py
│       └── openai_chat.py
├── domain/                      (本 spec，去掉 home/ 嵌套)
│   ├── __init__.py
│   ├── contracts.py
│   ├── grounding.py
│   ├── tool_registry.py
│   └── tools.py
├── memory/
├── events/
│   ├── __init__.py
│   ├── events.py                (RuntimeEvent + EventSink)
│   ├── sinks.py                 (增强，含 append_jsonl_event/write_json，本 spec)
│   ├── sanitizer.py             (增强，含 sanitize_for_log，本 spec)
│   └── logger.py                (本 spec，从根目录迁入)
├── prompts/
│   ├── __init__.py
│   ├── loader.py                (本 spec，从根目录迁入)
│   └── *.md                     (本 spec，.txt → .md)
├── skills/
├── task_state/
├── tools/
└── benchmarking/
```

**根目录从 8 个散落 `.py` 文件 → 0 个**（全部归位）。
