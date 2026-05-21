# HomeMaster V1.4 Agent Loop Full Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the numbered stage architecture completely and leave HomeMaster as a generic message/tool-call/tool-result agent loop with home-robot capabilities exposed as domain tools.

**Architecture:** The runtime core owns sessions, messages, model calls, tool dispatch, tool-result messages, retries, budgets, and events. Home-robot logic lives in `homemaster.domain.home`, `homemaster.memory`, and registered skill/tool packages; provider-specific response handling lives in transport adapters. CLI is a chat/session interface, not a scenario runner.

**Tech Stack:** Python 3.11, Pydantic v2, Typer, httpx, pytest, ruff.

---

## Review-Corrected Ordering

The previous draft had three unsafe ordering bugs:

- It deleted `src/homemaster/pipeline/`, `src/homemaster/stages/`, and `src/homemaster/task_runner.py` before the CLI stopped importing them.
- It deleted `src/homemaster/providers/mimo_decision_client.py` while `task_runner.py` still imported `LiveMimoDecisionClient`.
- Its final guard would scan its own source file and the tracked `review/V1.2/` report, so the guard could never pass.

This version fixes the order:

1. Batch 0 installs a report-only guard that skips exactly its own source file, records baseline, and deletes historical artifacts including `review/V1.2/`.
2. Batch 1 builds the generic message/session/transport runtime but keeps old runtime entrypoints in place.
3. Batch 2 cuts CLI and tool contracts over to the generic runtime before deleting old packages.
4. Batch 3 migrates home capabilities and fixtures, then deletes old runtime packages and old tests.
5. Batch 4 enforces the guard and runs full acceptance.

Do not split this into eight or nine phases. Each batch must end in a working, testable repository state.

## Current Legacy Surface

Current production paths still include old runtime concerns:

- `src/homemaster/cli/app.py` imports `homemaster.pipeline` and `homemaster.stages.task_understanding`.
- `src/homemaster/cli/run_command.py` and `src/homemaster/cli/interactive_shell.py` import `homemaster.task_runner`.
- `src/homemaster/task_runner.py` imports `homemaster.pipeline.*` and `LiveMimoDecisionClient`.
- `src/homemaster/tools/builtin.py` imports `homemaster.pipeline.stage_runtime`.
- `src/homemaster/agent/state.py` contains home-task fields such as `task_card`, `memory_hits`, `current_location`, and `holding_object`.
- `src/homemaster/tools/spec.py` and `src/homemaster/tools/dispatcher.py` type tool execution around `AgentState`.
- Many tests still import `homemaster.pipeline`, `homemaster.stages`, or `homemaster.task_runner`.
- `review/V1.2/`, `tests/homemaster/llm_cases/`, `data/scenarios/`, `README.md`, and config files still contain numbered stage/scenario/deterministic language.

## Final Package Shape

Keep these responsibilities:

- `src/homemaster/agent/`: generic runtime only. No home task schemas, no scenario logic, no domain tool implementations.
- `src/homemaster/providers/`: provider transports. MiMo/Anthropic/OpenAI response shapes are normalized here.
- `src/homemaster/tools/`: generic tool specs, registry, dispatcher, and tool-result message conversion. No home-state imports.
- `src/homemaster/skills/`: reusable skill package loader/registry/spec layer. It is kept for future skill work, but it is not a runtime mode and must not contain `mock_skills` semantics.
- `src/homemaster/domain/home/`: home-robot schemas, domain state, and home tool implementations.
- `src/homemaster/memory/`: memory indexing, retrieval, commits, task records, and runtime memory storage.
- `src/homemaster/events/`: generic runtime event schema and sinks.
- `src/homemaster/cli/`: Typer commands and interactive shell only.
- `tests/homemaster/fixtures/`: lightweight fixtures for agent loop, domain tools, memory, and sessions.

Delete these responsibilities:

- `src/homemaster/pipeline/`
- `src/homemaster/stages/`
- `src/homemaster/task_runner.py`
- `src/homemaster/scenario_catalog.py`
- `src/homemaster/scenario_runner.py`
- `src/homemaster/scenario_validator.py`
- `src/homemaster/providers/mimo_decision_client.py`
- `src/homemaster/agent/decision.py`
- numbered prompt assets under `src/homemaster/prompts/`
- numbered live-case artifacts under `tests/homemaster/llm_cases/`
- production scenario catalog under `data/scenarios/`

Keep but rewrite these responsibilities:

- `src/homemaster/skills/loader.py`, `registry.py`, `spec.py`, and builtin skill packages. They should register reusable capabilities and provide text/tool metadata to `skill_view`, not select deterministic runtime behavior.
- `src/homemaster/events/sanitizer.py`. It should sanitize generic runtime events after the old runtime is deleted; during Batch 1 it may still tolerate legacy payloads.
- `src/homemaster/agent/context_builder.py`. It should become a generic ContextComposer or thin compatibility import for `agent/context.py`, not a second stage-aware context path.

## Global Worker Rules

- Run `git status --short` before editing.
- If a file is already modified, inspect `git diff -- <file>` and preserve existing user changes.
- Do not use broad staging commands such as `git add -u src`, `git add -u tests`, or `git add -A`.
- Stage only the exact files touched by the current batch.
- Do not delete old runtime packages until Batch 2 proves CLI no longer imports `task_runner`, `pipeline`, or `stages`.
- Do not move `data/scenarios/*` into tests as-is. Test fixtures must be sanitized and must not contain `scenario`, `runtime_modes`, `deterministic`, or numbered stage keys.
- Treat the ten home domain tools as an unordered capability set. Tool descriptions, prompts, and tests must allow 0 tool calls for chat, partial tool use for clarification, and model-chosen ordering for tasks.
- Before running any listed `git add`, run `git diff -- <file>` for every already-modified file in that batch. If the diff contains unrelated user work, stage only the intended hunks with patch staging or stop for coordinator confirmation.
- New prompt files must be written fresh. Do not copy old numbered prompt text and rename it.

## Cross-Layer Contract Rules

Batch 1 starts with schema-first work. Before writing runtime behavior, define and test these normalized contracts:

- `ContentBlock`: `type`, `text`. V1.4 only implements `type="text"`, but all message content is stored as `list[ContentBlock]`.
- `AssistantMessage`: `role`, `content`, `reasoning_content`, `tool_calls`, `finish_reason`, `usage`, `provider_metadata`.
- `ToolCall`: `id`, `name`, `arguments`. IDs are mandatory and unique inside one assistant message.
- `ToolResultMessage`: `role`, `tool_call_id`, `name`, `content`, `is_error`, `data`, `provider_metadata`.
- `RunContext`: `session_id`, `run_id`, `turn_index`, `settings`, `event_sink`, `deps`, `cancellation_token`.
- `RuntimeEvent`: `type`, `session_id`, `run_id`, `turn_index`, `tool_call_id`, `name`, `payload`, `timestamp`.

Runtime invariants:

- `LLMTransport.stream()` is the primary model API. `complete()` may exist only as a wrapper around `stream()` plus aggregation.
- Transport emits `transport.*` events while streaming; CLI live progress consumes the same `RuntimeEvent` sink as tool/runtime events.
- `reasoning_content` is stored separately and never mixed into user-visible assistant `content`.
- Runtime passes the whole `list[ToolCall]` to `ToolDispatcher`; dispatcher may run calls sequentially or concurrently, but returns one `ToolResultMessage` for every original `tool_call_id`.
- Tool Python return values are serialized into JSON text blocks before becoming tool messages. Tool failures use `is_error=True` and stay in the message loop.
- AgentRuntime ends a run only on no tool calls plus provider stop/end-turn, budget exhaustion, cancellation, or unrecoverable runtime/transport contract error.
- Domain state enters tools only through `RunContext.deps`, explicit tool registry construction, or domain-owned objects. Do not put home fields back into generic runtime state.
- Event type names use `runtime.*`, `transport.*`, and `tool.*` namespaces.

---

## Batch 0: Baseline, Guard, And Static Cleanup

**Purpose:** Record the current surface, delete non-runtime historical artifacts, and install a report-only guard before code migration starts.

**Files:**
- Create: `plan/V1.4/baseline/git-status-before.md`
- Create: `plan/V1.4/baseline/legacy-surface-before.txt`
- Create: `plan/V1.4/baseline/tracked-artifacts-before.txt`
- Create: `scripts/guard_no_legacy_terms.py`
- Create: `tests/homemaster/test_cleanup_guard.py`
- Modify: `.gitignore`
- Delete tracked history/artifacts:
  - `docs/shim_lifecycle.md`
  - `record/`
  - `report/`
  - `log/`
  - `review/V1.2/`
  - `plan/V1.2/`
  - `plan/V1.3/`
  - `plan/v1.0/`
  - `plan/v1.1/`
  - tracked `var/homemaster/**`
- Delete old scripts:
  - `scripts/capture_scenario_snapshot.py`
  - `scripts/compare_all_baselines.py`
  - `scripts/render_screenshots.py`
  - `scripts/run_homemaster_scenarios.sh`
- Delete local generated artifacts if present:
  - `build/`
  - `.pytest_cache/`
  - every `__pycache__/`
  - every `*.pyc`
  - `plan/.DS_Store`

- [ ] **Step 1: Save baseline command output**

Run:

```bash
mkdir -p plan/V1.4/baseline
git status --short > plan/V1.4/baseline/git-status-before.md
rg -n "Stage|stage_|stage[0-9]|run_stage|stage_statuses|pipeline|src/homemaster/stages|scenario|deterministic|mock_skills|live_models|compat|shim" src tests README.md config pyproject.toml scripts plan record report review log var -g '!plan/V1.4/**' -g '!*.pyc' > plan/V1.4/baseline/legacy-surface-before.txt || true
git ls-files 'var/**' 'record/**' 'report/**' 'review/**' 'log/**' 'docs/**' 'plan/V1.2/**' 'plan/V1.3/**' 'plan/v1.0/**' 'plan/v1.1/**' > plan/V1.4/baseline/tracked-artifacts-before.txt
```

Expected:

```text
The three baseline files exist and contain the current dirty worktree, legacy hit list, and tracked artifact list.
```

- [ ] **Step 2: Add the report-only guard**

Create `scripts/guard_no_legacy_terms.py` with this behavior:

- Iterate over `git ls-files`.
- Skip `.git/`, `.venv/`, `.pytest_cache/`, `plan/V1.4/`, binary/image files, and exactly `scripts/guard_no_legacy_terms.py`.
- Treat these tracked paths as blocked when they still exist:
  - `src/homemaster/pipeline/`
  - `src/homemaster/stages/`
  - `tests/homemaster/llm_cases/`
  - `tests/homemaster/prompt_snapshots/`
  - `data/scenarios/`
  - `var/homemaster/`
- Treat these text patterns as blocked:
  - `Stage`
  - `stage_`
  - `stage[0-9]`
  - `run_stage`
  - `stage_statuses`
  - `pipeline`
  - `scenario`
  - `deterministic`
  - `mock_skills`
  - `live_models`
  - `pipeline_compat`
  - `shim_lifecycle`
  - `legacy shim`
  - `legacy compat`
- In `--report-only` mode, print violations and exit 0.
- In enforced mode, print violations and exit 1.

The guard must skip itself by exact relative path instead of ignoring the whole `scripts/` directory.

- [ ] **Step 3: Add guard tests**

Create `tests/homemaster/test_cleanup_guard.py`:

```python
from __future__ import annotations

import subprocess
import sys


def test_cleanup_guard_report_only_runs() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/guard_no_legacy_terms.py", "--report-only"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_cleanup_guard_does_not_report_itself() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/guard_no_legacy_terms.py", "--report-only"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert "scripts/guard_no_legacy_terms.py" not in result.stdout
```

- [ ] **Step 4: Update `.gitignore`**

Ensure `.gitignore` ignores generated runtime and cache outputs:

```gitignore
var/homemaster/
record/
report/
log/
.pytest_cache/
__pycache__/
*.pyc
build/
```

- [ ] **Step 5: Delete static legacy artifacts**

Run:

```bash
git rm -r --ignore-unmatch docs/shim_lifecycle.md record report log review/V1.2 plan/V1.2 plan/V1.3 plan/v1.0 plan/v1.1 var/homemaster
git rm --ignore-unmatch scripts/capture_scenario_snapshot.py scripts/compare_all_baselines.py scripts/render_screenshots.py scripts/run_homemaster_scenarios.sh
rm -rf build .pytest_cache plan/.DS_Store
find . -path ./.git -prune -o -path ./.venv -prune -o \( -name __pycache__ -o -name '*.pyc' \) -print
```

If the final `find` command prints paths, remove those generated files with `rm -rf` for directories and `rm -f` for `*.pyc`.

- [ ] **Step 6: Verify Batch 0**

Run:

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/homemaster/test_cleanup_guard.py -q
python scripts/guard_no_legacy_terms.py --report-only > plan/V1.4/baseline/legacy-surface-after-static-cleanup.txt || true
git status --short
```

Expected:

```text
test_cleanup_guard.py passes.
git status shows only intentional Batch 0 files plus pre-existing user edits.
```

Commit:

```bash
git add .gitignore scripts/guard_no_legacy_terms.py tests/homemaster/test_cleanup_guard.py plan/V1.4/baseline/git-status-before.md plan/V1.4/baseline/legacy-surface-before.txt plan/V1.4/baseline/tracked-artifacts-before.txt plan/V1.4/baseline/legacy-surface-after-static-cleanup.txt
git status --short
git commit -m "chore: remove historical runtime artifacts"
```

Before committing, verify that the staged diff contains only Batch 0 files and deletions.

---

## Batch 1: Generic Agent Messages, Session, And Transport

**Purpose:** Replace custom decision JSON with a generic message/tool-call/tool-result contract while leaving current CLI entrypoints intact.

**Files:**
- Create: `src/homemaster/agent/messages.py`
- Create: `src/homemaster/agent/session.py`
- Create: `src/homemaster/agent/normalized.py`
- Create: `src/homemaster/agent/context.py`
- Create: `src/homemaster/agent/generic_runtime.py`
- Create: `src/homemaster/providers/transport.py`
- Create: `src/homemaster/providers/mimo_transport.py`
- Preserve compatibly: `src/homemaster/agent/runtime.py`
- Extend compatibly: `src/homemaster/llm_client.py`
- Modify: `src/homemaster/events/runtime_events.py`
- Modify: `src/homemaster/events/sinks.py`
- Modify: `src/homemaster/events/sanitizer.py`
- Tests:
  - Create: `tests/homemaster/test_agent_messages.py`
  - Create: `tests/homemaster/test_agent_session.py`
  - Create: `tests/homemaster/test_context_composer.py`
  - Create: `tests/homemaster/test_transport_mimo.py`
  - Replace: `tests/homemaster/test_agent_runtime.py`
  - Update: `tests/homemaster/test_llm_client.py`

Do not delete these files in Batch 1:

```text
src/homemaster/providers/mimo_decision_client.py
src/homemaster/task_runner.py
src/homemaster/pipeline/
src/homemaster/stages/
```

They still support the old entrypoint until Batch 2 cuts CLI over.

Batch 1 compatibility rules:

- Do not replace the public `AgentRuntime` constructor used by `task_runner.py`. Current `task_runner._run_agent_runtime()` still constructs `AgentRuntime` with the old decision-client API, so Batch 1 must keep that import and constructor working.
- Implement the new loop as `homemaster.agent.generic_runtime.GenericAgentRuntime` or an equivalent new symbol. `agent/runtime.py` may re-export it, but old `AgentRuntime` must remain source-compatible until Batch 2.
- `RawJsonLLMClient` must remain importable for `memory_rag.py`, `stages/task_understanding.py`, and `cli/doctor.py`.
- Existing event sinks must still tolerate old event payloads while the old CLI path exists. New runtime code should emit only generic events, but `runtime_events.py`, `sinks.py`, and `sanitizer.py` cannot drop old-shape parsing until Batch 3 deletes the old runtime.
- Add a temporary default turn budget key such as `agent_response` or `default_turn` if the new runtime needs token budgeting before Batch 4 rewrites `token_budget.py`.
- `agent/context.py` is the new implementation. `agent/context_builder.py` must either delegate to it or stay untouched in Batch 1; do not leave two divergent context builders.

- [ ] **Step 1: Define normalized schemas and invariants first**

`src/homemaster/agent/messages.py` must define these public models before runtime code depends on them:

```python
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ContentBlock(BaseModel):
    type: Literal["text"] = "text"
    text: str


def normalize_content(value: str | list[ContentBlock]) -> list[ContentBlock]:
    if isinstance(value, str):
        return [ContentBlock(text=value)] if value else []
    return value


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class UserMessage(BaseModel):
    role: Literal["user"] = "user"
    content: list[ContentBlock]


class AssistantMessage(BaseModel):
    role: Literal["assistant"] = "assistant"
    content: list[ContentBlock] = Field(default_factory=list)
    reasoning_content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    finish_reason: str | None = None
    usage: dict[str, int] | None = None
    provider_metadata: dict[str, Any] = Field(default_factory=dict)


class ToolResultMessage(BaseModel):
    role: Literal["tool"] = "tool"
    tool_call_id: str
    name: str
    content: list[ContentBlock]
    is_error: bool = False
    data: dict[str, Any] | None = None
    provider_metadata: dict[str, Any] = Field(default_factory=dict)
```

If the implementation adds convenience constructors such as `UserMessage.from_text()` or `AssistantMessage.text`, tests may use them, but the stored schema must stay block-list based. External APIs may accept `content="..."`; they must immediately call `normalize_content()` before storing the message.

`src/homemaster/agent/normalized.py` must define `RunContext` or re-export it from a dedicated module:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from homemaster.config.runtime_settings import RuntimeSettings


@dataclass(slots=True)
class RunContext:
    session_id: str
    run_id: str
    turn_index: int
    settings: RuntimeSettings
    event_sink: Any
    deps: dict[str, Any] = field(default_factory=dict)
    cancellation_token: Any | None = None
```

`src/homemaster/events/runtime_events.py` must expose generic event fields equivalent to:

```python
class RuntimeEvent(BaseModel):
    type: str
    session_id: str
    run_id: str
    turn_index: int | None = None
    tool_call_id: str | None = None
    name: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: str
```

Keep legacy event parsing tolerant in Batch 1, but new runtime code must only emit `runtime.*`, `transport.*`, and `tool.*` event types.

Batch 1 final event names:

```text
runtime.turn_started
runtime.turn_completed
runtime.turn_failed
runtime.budget_exhausted
runtime.cancelled
transport.request_started
transport.delta
transport.response_completed
transport.request_failed
tool.call_started
tool.call_completed
tool.call_failed
```

- [ ] **Step 2: Write message/session tests**

`tests/homemaster/test_agent_messages.py` must assert:

```python
from homemaster.agent.messages import (
    AssistantMessage,
    ContentBlock,
    ToolCall,
    ToolResultMessage,
    UserMessage,
    normalize_content,
)


def test_tool_result_message_round_trips() -> None:
    msg = ToolResultMessage(
        tool_call_id="call_1",
        name="memory_retriever",
        is_error=True,
        content=[ContentBlock(text='{"error":"memory file missing"}')],
        data={"path": "missing.json"},
    )
    assert msg.role == "tool"
    assert msg.tool_call_id == "call_1"
    assert msg.is_error is True
    assert msg.content[0].text == '{"error":"memory file missing"}'


def test_assistant_message_can_hold_parallel_tool_calls() -> None:
    msg = AssistantMessage(
        content=[],
        tool_calls=[
            ToolCall(
                id="call_1",
                name="memory_retriever",
                arguments={"query": "水杯"},
            ),
            ToolCall(
                id="call_2",
                name="skill_view",
                arguments={"skill": "fetch_object"},
            ),
        ],
        finish_reason="tool_calls",
    )
    assert msg.tool_calls[0].name == "memory_retriever"
    assert [call.id for call in msg.tool_calls] == ["call_1", "call_2"]


def test_reasoning_content_is_not_visible_content() -> None:
    msg = AssistantMessage(
        content=[ContentBlock(text="我可以帮你。")],
        reasoning_content="private reasoning replay",
        finish_reason="stop",
    )
    assert msg.content[0].text == "我可以帮你。"
    assert "private" not in msg.content[0].text


def test_user_message_keeps_text() -> None:
    msg = UserMessage(content=[ContentBlock(text="你好")])
    assert isinstance(msg.content, list)
    assert msg.content[0].text == "你好"


def test_normalize_content_accepts_external_strings() -> None:
    assert normalize_content("") == []
    assert normalize_content("你好")[0].text == "你好"
```

`tests/homemaster/test_agent_session.py` must assert:

```python
from homemaster.agent.messages import AssistantMessage, ContentBlock, ToolResultMessage, UserMessage
from homemaster.agent.session import AgentSession


def test_session_appends_turn_messages() -> None:
    session = AgentSession(session_id="s1")
    session.append(UserMessage(content=[ContentBlock(text="你好")]))
    session.append(AssistantMessage(content=[ContentBlock(text="你好，我在。")], finish_reason="stop"))
    assert [m.role for m in session.messages] == ["user", "assistant"]


def test_session_keeps_tool_result_after_assistant_tool_call() -> None:
    session = AgentSession(session_id="s1")
    session.append(
        ToolResultMessage(
            tool_call_id="call_1",
            name="memory_retriever",
            is_error=True,
            content=[ContentBlock(text='{"error":"missing"}')],
        )
    )
    assert session.messages[-1].role == "tool"
```

- [ ] **Step 3: Write context composer budget tests**

`tests/homemaster/test_context_composer.py` must assert:

```python
from homemaster.agent.context import ContextComposer
from homemaster.agent.messages import AssistantMessage, ContentBlock, ToolCall, ToolResultMessage, UserMessage


def test_context_composer_keeps_tool_call_pairs_when_truncating() -> None:
    composer = ContextComposer(max_messages=3)
    messages = [
        UserMessage(content=[ContentBlock(text="old")]),
        AssistantMessage(
            content=[],
            tool_calls=[ToolCall(id="call_1", name="memory_retriever", arguments={"query": "水杯"})],
            finish_reason="tool_calls",
        ),
        ToolResultMessage(
            tool_call_id="call_1",
            name="memory_retriever",
            content=[ContentBlock(text='{"items":[]}')],
        ),
        UserMessage(content=[ContentBlock(text="现在继续")]),
    ]
    context = composer.compose(messages=messages, tools=[])
    roles = [message.role for message in context.messages]
    assert roles[-3:] == ["assistant", "tool", "user"]
    assert context.messages[-2].tool_call_id == "call_1"


def test_context_composer_does_not_include_home_state_fields() -> None:
    composer = ContextComposer()
    context = composer.compose(messages=[UserMessage(content=[ContentBlock(text="你好")])], tools=[])
    serialized = context.model_dump_json()
    assert "current_location" not in serialized
    assert "holding_object" not in serialized
    assert "memory_hits" not in serialized
```

`ContextComposer` can use a simple baseline strategy in V1.4: keep system prompt, current user input, recent N messages, and unclosed assistant/tool pairs. If budget is still exceeded, use session summary if available; otherwise fail visibly. Do not silently drop a tool result while keeping its assistant tool call.

- [ ] **Step 4: Write transport and streaming tests**

`tests/homemaster/test_transport_mimo.py` must cover:

```python
from homemaster.agent.messages import AssistantMessage
from homemaster.providers.mimo_transport import MimoTransport


def test_parse_text_response() -> None:
    payload = {"content": [{"type": "text", "text": "你好，我在。"}]}
    msg = MimoTransport.parse_response_payload(payload)
    assert isinstance(msg, AssistantMessage)
    assert msg.content[0].text == "你好，我在。"
    assert msg.tool_calls == []


def test_parse_tool_use_response() -> None:
    payload = {
        "content": [
            {"type": "tool_use", "id": "call_1", "name": "memory_retriever", "input": {"query": "水杯"}}
        ]
    }
    msg = MimoTransport.parse_response_payload(payload)
    assert msg.tool_calls[0].name == "memory_retriever"
    assert msg.tool_calls[0].arguments == {"query": "水杯"}


def test_empty_text_with_reasoning_is_not_response_missing_text() -> None:
    payload = {"content": [{"type": "thinking", "thinking": "checking"}], "stop_reason": "tool_use"}
    msg = MimoTransport.parse_response_payload(payload)
    assert msg.content == []
    assert msg.reasoning_content == "checking"


def test_stream_events_are_aggregated_into_message(fake_mimo_http) -> None:
    transport = MimoTransport(http_client=fake_mimo_http)
    events = list(transport.stream(messages=[], tools=[]))
    assert any(event.type == "transport.delta" for event in events)
    assert events[-1].type == "transport.response_completed"


def test_stream_aggregator_matches_parse_response_payload(fake_mimo_http) -> None:
    payload = {"content": [{"type": "text", "text": "你好，我在。"}], "stop_reason": "stop"}
    direct = MimoTransport.parse_response_payload(payload)
    transport = MimoTransport(http_client=fake_mimo_http.with_payload(payload))
    streamed = transport.complete(messages=[], tools=[])
    assert streamed.model_dump() == direct.model_dump()


def test_complete_uses_stream_event_sink(fake_mimo_http, event_sink) -> None:
    payload = {"content": [{"type": "text", "text": "你好，我在。"}], "stop_reason": "stop"}
    transport = MimoTransport(http_client=fake_mimo_http.with_payload(payload))
    msg = transport.complete(messages=[], tools=[], event_sink=event_sink)
    assert msg.content[0].text == "你好，我在。"
    assert [event.type for event in event_sink.events] == [
        "transport.request_started",
        "transport.delta",
        "transport.response_completed",
    ]
```

- [ ] **Step 5: Add the generic runtime beside the old runtime**

`src/homemaster/agent/generic_runtime.py` final behavior:

- accepts an `AgentSession`;
- appends the user message;
- calls `LLMTransport.stream(session.messages, tools=available_tool_manifests, run_context=run_context)`;
- aggregates transport deltas into an `AssistantMessage`;
- appends assistant messages;
- dispatches the whole `assistant_message.tool_calls` list as one batch;
- converts each `ToolResult` to a `ToolResultMessage`;
- appends the tool-result messages;
- validates that each returned tool result has a `tool_call_id` matching one original tool call;
- continues until assistant returns no tool calls with provider stop/end-turn, budget is exhausted, cancellation fires, or runtime failure occurs;
- emits generic events such as `runtime.turn_started`, `transport.request_started`, `transport.delta`, `transport.response_completed`, `tool.call_started`, `tool.call_completed`, `runtime.turn_completed`, and `runtime.turn_failed`;
- does not import `homemaster.domain.home`, `homemaster.pipeline`, `homemaster.stages`, or `homemaster.task_runner`.
- does not encode a home-tool order. The runtime may pass registered tool schemas to the model, but model output decides whether to call tools.
- only passes domain state through `RunContext.deps` and does not inspect `deps["home"]`, `deps["memory"]`, or `deps["skills"]`.

`src/homemaster/agent/runtime.py` Batch 1 behavior:

- keep the current `AgentRuntime` class source-compatible for `src/homemaster/task_runner.py`;
- do not remove or rename constructor parameters used by the old CLI path;
- may import/re-export `GenericAgentRuntime`, but must not make old `AgentRuntime` point to the new constructor yet.

`tests/homemaster/test_agent_runtime.py` must include at least these behaviors:

```python
def test_greeting_returns_text_without_tools(fake_transport, fake_dispatcher) -> None:
    fake_transport.queue_text("你好，我在。")
    result = run_fake_agent_turn("你好", transport=fake_transport, dispatcher=fake_dispatcher)
    assert result.final_reply == "你好，我在。"
    assert fake_dispatcher.calls == []


def test_tool_failure_is_appended_as_tool_message(fake_transport, failing_dispatcher) -> None:
    fake_transport.queue_tool_call("memory_retriever", {"query": "水杯"})
    fake_transport.queue_text("我没有找到相关记忆。")
    result = run_fake_agent_turn("帮我找水杯", transport=fake_transport, dispatcher=failing_dispatcher)
    assert result.session.messages[-2].role == "tool"
    assert result.session.messages[-2].is_error is True
    assert result.final_reply == "我没有找到相关记忆。"


def test_parallel_tool_call_ids_are_preserved(fake_transport, fake_dispatcher) -> None:
    fake_transport.queue_tool_calls(
        [
            ("call_1", "memory_retriever", {"query": "水杯"}),
            ("call_2", "skill_view", {"skill": "fetch_object"}),
        ]
    )
    fake_transport.queue_text("我查到了两个结果。")
    result = run_fake_agent_turn("帮我拿水杯", transport=fake_transport, dispatcher=fake_dispatcher)
    tool_messages = [msg for msg in result.session.messages if msg.role == "tool"]
    assert [msg.tool_call_id for msg in tool_messages] == ["call_1", "call_2"]


def test_runtime_stops_on_no_tool_calls_and_stop_reason(fake_transport, fake_dispatcher) -> None:
    fake_transport.queue_text("你好，我在。", finish_reason="stop")
    result = run_fake_agent_turn("你好", transport=fake_transport, dispatcher=fake_dispatcher)
    assert result.status == "replied"
    assert fake_transport.call_count == 1


def test_runtime_stops_when_max_iterations_exceeded(fake_transport, fake_dispatcher) -> None:
    fake_transport.queue_repeating_tool_call("memory_retriever", {"query": "水杯"})
    result = run_fake_agent_turn(
        "帮我找水杯",
        transport=fake_transport,
        dispatcher=fake_dispatcher,
        max_tool_iterations=1,
    )
    assert result.status == "failed"
    assert result.error_code == "max_tool_iterations_exceeded"
    assert any(event.type == "runtime.budget_exhausted" for event in result.events)


def test_runtime_fails_when_tool_result_id_mismatch(fake_transport, mismatched_dispatcher) -> None:
    fake_transport.queue_tool_call("memory_retriever", {"query": "水杯"}, call_id="call_1")
    mismatched_dispatcher.queue_result(tool_call_id="call_other", name="memory_retriever")
    result = run_fake_agent_turn("帮我找水杯", transport=fake_transport, dispatcher=mismatched_dispatcher)
    assert result.status == "failed"
    assert result.error_code == "tool_result_id_mismatch"
    assert any(event.type == "runtime.turn_failed" for event in result.events)


def test_runtime_handles_finish_reason_length_as_failure(fake_transport, fake_dispatcher) -> None:
    fake_transport.queue_text("截断回复", finish_reason="length")
    result = run_fake_agent_turn("你好", transport=fake_transport, dispatcher=fake_dispatcher)
    assert result.status == "failed"
    assert result.error_code == "model_output_truncated"
    assert "截断" not in result.final_reply or result.final_reply.startswith("模型回复被截断")
```

Use the repository's actual fake runtime helper names when implementing these tests; the required behavior is the important part.

- [ ] **Step 6: Keep old decision client isolated**

Run:

```bash
rg -n "mimo_decision_client|LiveMimoDecisionClient" src/homemaster tests/homemaster
```

Expected in Batch 1:

```text
Matches may remain only in task_runner.py, old tests, and providers/mimo_decision_client.py.
No new Batch 1 files import LiveMimoDecisionClient.
```

Also verify the old runtime symbol can still be constructed by the old path:

```bash
PYTHONPATH=src .venv/bin/python - <<'PY'
from homemaster.agent.runtime import AgentRuntime

print(AgentRuntime.__name__)
PY
```

Expected:

```text
AgentRuntime
```

Also run:

```bash
PYTHONPATH=src .venv/bin/python - <<'PY'
from homemaster.llm_client import RawJsonLLMClient
from homemaster.events.runtime_events import RuntimeEvent

print(RawJsonLLMClient.__name__)
print(RuntimeEvent.__name__)
PY
```

Expected:

```text
RawJsonLLMClient
RuntimeEvent
```

- [ ] **Step 7: Verify Batch 1**

Run:

```bash
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/homemaster/test_agent_messages.py \
  tests/homemaster/test_agent_session.py \
  tests/homemaster/test_context_composer.py \
  tests/homemaster/test_transport_mimo.py \
  tests/homemaster/test_agent_runtime.py \
  tests/homemaster/test_llm_client.py -q
PYTHONPATH=src .venv/bin/python -m ruff check \
  src/homemaster/agent \
  src/homemaster/providers \
  src/homemaster/llm_client.py \
  src/homemaster/events \
  tests/homemaster/test_agent_messages.py \
  tests/homemaster/test_agent_session.py \
  tests/homemaster/test_context_composer.py \
  tests/homemaster/test_transport_mimo.py \
  tests/homemaster/test_agent_runtime.py \
  tests/homemaster/test_llm_client.py
```

Expected:

```text
Selected tests pass.
ruff reports no errors for Batch 1 files.
```

Commit:

```bash
git diff -- src/homemaster/llm_client.py src/homemaster/events/runtime_events.py src/homemaster/events/sinks.py src/homemaster/events/sanitizer.py
git add src/homemaster/agent/messages.py src/homemaster/agent/session.py src/homemaster/agent/normalized.py src/homemaster/agent/context.py src/homemaster/agent/generic_runtime.py src/homemaster/agent/runtime.py src/homemaster/providers/transport.py src/homemaster/providers/mimo_transport.py src/homemaster/llm_client.py src/homemaster/events/runtime_events.py src/homemaster/events/sinks.py src/homemaster/events/sanitizer.py tests/homemaster/test_agent_messages.py tests/homemaster/test_agent_session.py tests/homemaster/test_context_composer.py tests/homemaster/test_transport_mimo.py tests/homemaster/test_agent_runtime.py tests/homemaster/test_llm_client.py
git status --short
git commit -m "feat: add generic agent message transport loop"
```

Before committing, verify that no `task_runner.py`, `pipeline/`, `stages/`, or `mimo_decision_client.py` deletion is staged.

---

## Batch 2: CLI Cutover, Generic State, And Tool Contract Boundary

**Purpose:** Make the visible product run through the generic runtime before old runtime packages are removed.

**Files:**
- Modify: `src/homemaster/agent/runtime.py`
- Modify: `src/homemaster/agent/state.py`
- Modify: `src/homemaster/agent/context_builder.py` or replace it with generic context assembly
- Modify: `src/homemaster/tools/spec.py`
- Modify: `src/homemaster/tools/dispatcher.py`
- Modify: `src/homemaster/tools/results.py`
- Modify: `src/homemaster/tools/registry.py`
- Modify: `src/homemaster/tools/state_updater.py`
- Modify: `src/homemaster/tools/builtin.py`
- Modify: `src/homemaster/tools/simulated.py`
- Modify: `src/homemaster/tools/skill_tools.py`
- Modify: `src/homemaster/memory/context_snapshot.py`
- Create: `src/homemaster/agent/turn.py`
- Modify: `src/homemaster/cli/app.py`
- Modify: `src/homemaster/cli/run_command.py`
- Modify: `src/homemaster/cli/interactive_shell.py`
- Modify: `src/homemaster/cli/doctor.py`
- Modify: `src/homemaster/cli/errors.py`
- Tests:
  - Update: `tests/homemaster/test_agent_state.py`
  - Update: `tests/homemaster/test_tool_dispatcher.py`
  - Update: `tests/homemaster/test_tool_registry.py`
  - Create or update: `tests/homemaster/test_cli_help.py`
  - Replace: `tests/homemaster/test_cli_interactive.py`
  - Create or replace: `tests/homemaster/test_cli_run.py`
  - Update: `tests/homemaster/test_cli_doctor.py`

Do not delete old runtime packages in this batch. They become unreachable from CLI first; deletion happens in Batch 3.

- [ ] **Step 1: Genericize AgentState and tool executor typing**

`AgentState` must no longer contain home-domain fields. It may contain only generic runtime/session fields:

```python
class AgentState(BaseModel):
    run_id: str = ""
    user_request: str = ""
    status: Literal["running", "replied", "tool_loop_completed", "failed"] = "running"
    turn_index: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)
    tool_results: list[dict[str, Any]] = Field(default_factory=list)
    last_assistant_text: str | None = None
```

`ToolExecutor` in `src/homemaster/tools/spec.py` must accept generic state:

```python
from typing import Any, Protocol

from homemaster.agent.normalized import RunContext
from homemaster.tools.results import ToolResult


class ToolExecutor(Protocol):
    def __call__(
        self,
        *,
        arguments: dict[str, Any],
        run_context: RunContext,
    ) -> ToolResult:
        raise NotImplementedError
```

`ToolDispatcher` must accept a whole `list[ToolCall]` and return `list[ToolResultMessage]`, not a single ad hoc result. It may execute sequentially in V1.4, but the public contract must preserve parallel provider semantics and tool_call_id mapping. `ToolDispatcher` may keep a generic `AgentState` for runtime metadata, but it must not expose that state as a domain data channel to tool executors.

If a home tool needs `current_location`, `holding_object`, `selected_target`, or `memory_hits`, that data belongs in `src/homemaster/domain/home/state.py` and is passed through `run_context.deps["home"]`, `run_context.deps["memory"]`, or a domain state object supplied by the home tool registry. Generic `AgentState.metadata` may hold opaque runtime debug metadata, but domain tools must not read `state.metadata["home"]` or any equivalent runtime-visible domain channel.

`src/homemaster/memory/context_snapshot.py` must stop accepting or reading typed home-shaped `AgentState` fields such as `memory_hits`, `memory_context_snapshot`, and `user_context_snapshot`. Rewrite it to accept explicit memory/user records, a plain metadata mapping, or a home-domain state object supplied by `homemaster.domain.home`. Batch 2 cannot remove those fields from `AgentState` while leaving this module coupled to them.

`src/homemaster/tools/builtin.py`, `src/homemaster/tools/simulated.py`, and `src/homemaster/tools/skill_tools.py` must follow the same boundary:

- no imports from `homemaster.pipeline`, `homemaster.stages`, or `homemaster.task_runner`;
- no direct reads of `AgentState.current_location`, `AgentState.holding_object`, `AgentState.memory_hits`, or `AgentState.selected_target`;
- `skill_tools.py` may read `homemaster.skills.registry` and `homemaster.skills.loader`, but only as skill package metadata / ToolSpec contributors.

In this batch, after CLI no longer calls `task_runner.py`, update `src/homemaster/agent/runtime.py` so the public `AgentRuntime` name points to the generic loop or remove the old compatibility class. Do not do this before the CLI cutover tests pass.

- [ ] **Step 2: Write boundary tests**

`tests/homemaster/test_agent_state.py` must assert:

```python
from homemaster.agent.state import AgentState


def test_agent_state_has_no_home_task_fields() -> None:
    fields = set(AgentState.model_fields)
    assert "task_card" not in fields
    assert "memory_hits" not in fields
    assert "current_location" not in fields
    assert "holding_object" not in fields
    assert "selected_target" not in fields
```

Add a context boundary assertion:

```python
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_agent_context_composer_has_no_home_task_fields() -> None:
    for rel in ("src/homemaster/agent/context.py", "src/homemaster/agent/context_builder.py"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert "task_card" not in text
        assert "target_candidates" not in text
        assert "current_location" not in text
        assert "holding_object" not in text
        assert "memory_hits" not in text
```

`tests/homemaster/test_tool_dispatcher.py` must assert:

```python
from homemaster.agent.state import AgentState
from homemaster.agent.messages import ToolCall
from homemaster.agent.normalized import RunContext
from homemaster.config.runtime_settings import RuntimeSettings
from homemaster.tools.dispatcher import ToolDispatcher
from homemaster.tools.results import ToolResult
from homemaster.tools.spec import ToolSpec


def test_dispatcher_accepts_generic_agent_state(tmp_path) -> None:
    def executor(*, arguments, run_context):
        assert run_context.deps == {}
        return ToolResult(success=True, tool_name="echo", executor_mode="programmatic", data=arguments)

    spec = ToolSpec(
        name="echo",
        description="Echo input",
        input_schema={"type": "object", "required": ["text"]},
        executor_mode="programmatic",
        executor=executor,
    )
    settings = RuntimeSettings(
        run_id="r1",
        runtime_root=tmp_path,
        debug_root=tmp_path / "debug",
        results_root=tmp_path / "results",
    )
    result = ToolDispatcher().dispatch(
        tool_calls=[ToolCall(id="call_1", name="echo", arguments={"text": "hi"})],
        run_context=RunContext(
            session_id="s1",
            run_id="r1",
            turn_index=0,
            settings=settings,
            event_sink=None,
        ),
    )
    assert result[0].tool_call_id == "call_1"
    assert result[0].is_error is False


def test_dispatcher_passes_run_context_deps_without_interpreting_them(tmp_path) -> None:
    sentinel_home = object()

    def executor(*, arguments, run_context):
        assert run_context.deps["home"] is sentinel_home
        return ToolResult(success=True, tool_name="uses_home", executor_mode="programmatic", data={})

    spec = ToolSpec(
        name="uses_home",
        description="Uses opaque home deps",
        input_schema={"type": "object"},
        executor_mode="programmatic",
        executor=executor,
    )
    settings = RuntimeSettings(run_id="r1", runtime_root=tmp_path, debug_root=tmp_path / "debug", results_root=tmp_path / "results")
    result = ToolDispatcher().dispatch(
        tool_calls=[ToolCall(id="call_1", name="uses_home", arguments={})],
        run_context=RunContext(
            session_id="s1",
            run_id="r1",
            turn_index=0,
            settings=settings,
            event_sink=None,
            deps={"home": sentinel_home},
        ),
    )
    assert result[0].tool_call_id == "call_1"
```

- [ ] **Step 3: Create `agent.turn` as the CLI-facing runtime adapter**

`src/homemaster/agent/turn.py` must expose this public API:

```text
AgentTurnResult dataclass fields:
- run_id: str
- status: str
- final_reply: str
- trace_path: Path | None
- run_dir: Path | None
- tool_events: list[RuntimeEvent]

run_single_turn keyword-only parameters:
- utterance: str
- run_id: str | None = None
- world_path: Path | None = None
- memory_path: Path | None = None
- progress: bool = False
- returns AgentTurnResult

run_agent_turn parameters:
- session: AgentSession
- text: str
- progress: bool = False
- returns AgentTurnResult
```

Rules:

- No import of `homemaster.task_runner`, `homemaster.pipeline`, or `homemaster.stages`.
- `你好` can return a normal assistant reply without forcing a home tool call.
- Tool progress is emitted through event sinks, not through stage labels.
- `AgentTurnResult.status` uses generic values only: `replied`, `tool_loop_completed`, or `failed`.

- [ ] **Step 4: Rewrite CLI tests**

`tests/homemaster/test_cli_help.py` must assert:

```python
from typer.testing import CliRunner

from homemaster.cli.app import app


def test_help_exposes_only_final_commands() -> None:
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "run" in result.stdout
    assert "shell" in result.stdout
    assert "doctor" in result.stdout
    assert "stage" not in result.stdout.lower()
    assert "smoke" not in result.stdout.lower()
    assert "scenario" not in result.stdout.lower()
```

`tests/homemaster/test_cli_interactive.py` must define its own fake result instead of relying on undefined fixtures:

```python
from dataclasses import dataclass
from pathlib import Path

from typer.testing import CliRunner

from homemaster.cli.app import app


@dataclass(frozen=True)
class FakeTurn:
    run_id: str = "r1"
    status: str = "replied"
    final_reply: str = "你好，我在。"
    trace_path: Path | None = None
    run_dir: Path | None = None
    tool_events: list = None


def test_shell_greeting_returns_reply_without_task_status(monkeypatch) -> None:
    monkeypatch.setattr(
        "homemaster.cli.interactive_shell.run_agent_turn",
        lambda session, text, progress=False: FakeTurn(tool_events=[]),
    )
    result = CliRunner().invoke(app, ["shell"], input="你好\n/exit\n")
    assert result.exit_code == 0
    assert "你好，我在。" in result.stdout
    assert "final_status" not in result.stdout
    assert "scenario" not in result.stdout
    assert "stage" not in result.stdout.lower()
```

`tests/homemaster/test_cli_run.py` must assert:

```python
from dataclasses import dataclass
from pathlib import Path

from typer.testing import CliRunner

from homemaster.cli.app import app


@dataclass(frozen=True)
class FakeTurn:
    run_id: str
    status: str
    final_reply: str
    trace_path: Path | None
    run_dir: Path | None
    tool_events: list


def test_run_command_prints_assistant_reply_and_trace(monkeypatch, tmp_path) -> None:
    trace = tmp_path / "runs" / "r1" / "events.jsonl"
    monkeypatch.setattr(
        "homemaster.cli.run_command.run_single_turn",
        lambda **kwargs: FakeTurn("r1", "replied", "已完成。", trace, tmp_path / "runs" / "r1", []),
    )
    result = CliRunner().invoke(app, ["run", "--utterance", "帮我拿个水"])
    assert result.exit_code == 0
    assert "assistant: 已完成。" in result.stdout
    assert "trace:" in result.stdout
    assert "stage" not in result.stdout.lower()
    assert "scenario" not in result.stdout.lower()
```

- [ ] **Step 5: Rewrite CLI commands**

`src/homemaster/cli/app.py` final command set:

```text
homemaster run --utterance TEXT [--world PATH] [--memory PATH] [--run-id ID] [--progress]
homemaster shell
homemaster doctor [--live] [--json]
```

Remove:

```text
homemaster stage <subcommand>
homemaster smoke <subcommand>
homemaster contract-smoke
homemaster understand
--scenario
```

`src/homemaster/cli/interactive_shell.py` final shell commands:

```text
/new
/status
/debug
/events
/doctor
/exit
```

Rules:

- no `_guess_scenario`;
- no fixed run id such as `interactive-fetch_cup_retry`;
- each user turn gets a unique safe run id;
- progress events are shown by default in shell;
- `/status` reports the last agent turn status: `idle`, `replied`, `tool_loop_completed`, or `failed`;
- `/debug` prints the current run directory;
- `/events` prints the last trace path or tails the last trace file.

- [ ] **Step 6: Prove old runtime is unreachable from CLI**

Run:

```bash
rg -n "homemaster\\.(pipeline|stages|task_runner)|from homemaster import task_runner|--scenario|_guess_scenario|final_status|stage_07" src/homemaster/cli tests/homemaster/test_cli_help.py tests/homemaster/test_cli_interactive.py tests/homemaster/test_cli_run.py
rg -n "homemaster\\.(pipeline|stages|task_runner)|current_location|holding_object|memory_hits|selected_target" src/homemaster/tools src/homemaster/agent/state.py
rg -n "task_card|target_candidates|current_location|holding_object|memory_hits" src/homemaster/agent/context.py src/homemaster/agent/context_builder.py
```

Expected:

```text
No matches.
```

- [ ] **Step 7: Verify Batch 2**

Run:

```bash
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/homemaster/test_agent_state.py \
  tests/homemaster/test_tool_dispatcher.py \
  tests/homemaster/test_tool_registry.py \
  tests/homemaster/test_cli_help.py \
  tests/homemaster/test_cli_interactive.py \
  tests/homemaster/test_cli_run.py \
  tests/homemaster/test_cli_doctor.py -q
PYTHONPATH=src .venv/bin/python -m ruff check \
  src/homemaster/agent \
  src/homemaster/tools \
  src/homemaster/cli \
  tests/homemaster/test_agent_state.py \
  tests/homemaster/test_tool_dispatcher.py \
  tests/homemaster/test_tool_registry.py \
  tests/homemaster/test_cli_help.py \
  tests/homemaster/test_cli_interactive.py \
  tests/homemaster/test_cli_run.py \
  tests/homemaster/test_cli_doctor.py
```

Expected:

```text
Selected tests pass.
ruff reports no errors for Batch 2 files.
CLI files no longer import old runtime packages.
```

Commit:

```bash
git diff -- src/homemaster/agent/runtime.py src/homemaster/agent/state.py src/homemaster/agent/context_builder.py src/homemaster/tools/spec.py src/homemaster/tools/dispatcher.py src/homemaster/tools/results.py src/homemaster/tools/registry.py src/homemaster/tools/state_updater.py src/homemaster/tools/builtin.py src/homemaster/tools/simulated.py src/homemaster/tools/skill_tools.py src/homemaster/cli/app.py src/homemaster/cli/run_command.py src/homemaster/cli/interactive_shell.py src/homemaster/cli/doctor.py src/homemaster/cli/errors.py
git add src/homemaster/agent/runtime.py src/homemaster/agent/state.py src/homemaster/agent/context_builder.py src/homemaster/agent/turn.py src/homemaster/tools/spec.py src/homemaster/tools/dispatcher.py src/homemaster/tools/results.py src/homemaster/tools/registry.py src/homemaster/tools/state_updater.py src/homemaster/tools/builtin.py src/homemaster/tools/simulated.py src/homemaster/tools/skill_tools.py src/homemaster/cli/app.py src/homemaster/cli/run_command.py src/homemaster/cli/interactive_shell.py src/homemaster/cli/doctor.py src/homemaster/cli/errors.py tests/homemaster/test_agent_state.py tests/homemaster/test_tool_dispatcher.py tests/homemaster/test_tool_registry.py tests/homemaster/test_cli_help.py tests/homemaster/test_cli_interactive.py tests/homemaster/test_cli_run.py tests/homemaster/test_cli_doctor.py
git status --short
git commit -m "feat: cut cli over to generic agent turns"
```

Before committing, verify that old runtime package deletions are not staged yet.

---

## Batch 3: Domain Tool Migration, Fixture Sanitization, And Old Runtime Deletion

**Purpose:** Preserve home-robot capabilities as tools, move memory code into its package, sanitize test fixtures, and delete the old fixed-flow runtime.

**Files:**
- Create: `src/homemaster/domain/home/__init__.py`
- Create: `src/homemaster/domain/home/contracts.py`
- Create: `src/homemaster/domain/home/state.py`
- Create: `src/homemaster/domain/home/tools.py`
- Create: `src/homemaster/domain/home/tool_registry.py`
- Create: `src/homemaster/domain/home/grounding.py`
- Create: `src/homemaster/domain/home/planning_context.py`
- Create: `src/homemaster/domain/home/world_overlay.py`
- Move/rewrite into `src/homemaster/domain/home/` or delete old-only code:
  - `src/homemaster/contracts.py`
  - `src/homemaster/execution_state.py`
  - `src/homemaster/failure_log.py`
  - `src/homemaster/failure_rule_provider.py`
  - `src/homemaster/grounding.py`
  - `src/homemaster/orchestration_validator.py`
  - `src/homemaster/planning_context.py`
  - `src/homemaster/world_overlay.py`
- Rewrite package entrypoints:
  - `src/homemaster/__init__.py`
  - `src/homemaster/agent/__init__.py`
- Cleanup after old runtime deletion:
  - `src/homemaster/events/runtime_events.py`
  - `src/homemaster/events/sinks.py`
  - `src/homemaster/events/sanitizer.py`
- Rewrite or delete old debug/artifact helper:
  - `src/homemaster/trace.py`
- Delete or migrate old recovery-loop config:
  - `src/homemaster/recovery_config.py`
- Keep and rewrite: `src/homemaster/skills/loader.py`
- Keep and rewrite: `src/homemaster/skills/registry.py`
- Keep and rewrite: `src/homemaster/skills/spec.py`
- Keep and rewrite: `src/homemaster/skills/builtin/*/SKILL.md`
- Move into `src/homemaster/memory/`:
  - `src/homemaster/memory/__init__.py` must be rewritten in generic memory/tool terms; remove existing Stage 03 / Stage 06 wording.
  - `fact_memory.py`
  - `memory_commit.py`
  - `memory_index.py`
  - `memory_profile.py`
  - `memory_rag.py`
  - `memory_tokenizer.py`
  - `runtime_memory_store.py`
  - `task_record.py`
- Rewrite memory/embedding terminology:
  - `src/homemaster/embedding_client.py`
- Delete:
  - `src/homemaster/pipeline/`
  - `src/homemaster/stages/`
  - `src/homemaster/task_runner.py`
  - `src/homemaster/scenario_catalog.py`
  - `src/homemaster/scenario_runner.py`
  - `src/homemaster/scenario_validator.py`
  - `src/homemaster/providers/mimo_decision_client.py`
  - `src/homemaster/agent/decision.py`
  - `src/homemaster/prompts/stage_*.txt`
  - `tests/homemaster/llm_cases/`
  - `tests/homemaster/prompt_snapshots/`
  - `tests/homemaster/prompt_snapshot_export.py`
  - `data/scenarios/`
- Create sanitized fixtures under:
  - `tests/homemaster/fixtures/home_tasks/fetch_cup_retry/`
  - `tests/homemaster/fixtures/home_tasks/check_medicine_success/`
  - `tests/homemaster/fixtures/home_tasks/object_not_found/`
- Tests:
  - Create: `tests/homemaster/test_domain_home_tools.py`
  - Create: `tests/homemaster/test_domain_memory_tools.py`
  - Create: `tests/homemaster/test_domain_import_boundaries.py`
  - Create or update: `tests/homemaster/test_skills_registry.py`
  - Rewrite or delete: `tests/homemaster/test_skill_loader.py`
  - Rewrite or delete: `tests/homemaster/test_skill_registry_phase4.py`
  - Rewrite or delete: `tests/homemaster/test_embedding_degradation.py`
  - Rewrite or delete: `tests/homemaster/test_recovery_config.py`
  - Rewrite or delete every old test that imports `homemaster.pipeline`, `homemaster.stages`, or `homemaster.task_runner`.

- [ ] **Step 1: Inventory imports before editing tests**

Run:

```bash
rg -l "homemaster\\.(pipeline|stages|task_runner)|from homemaster import task_runner" tests/homemaster -g '*.py' | sort > plan/V1.4/baseline/old-test-imports-before-batch3.txt
cat plan/V1.4/baseline/old-test-imports-before-batch3.txt
rg -l "homemaster\\.(scenario_catalog|scenario_runner|scenario_validator)|mimo_decision_client|homemaster\\.(fact_memory|memory_commit|memory_index|memory_profile|memory_rag|memory_tokenizer|runtime_memory_store|task_record)|homemaster\\.(contracts|execution_state|failure_log|failure_rule_provider|grounding|orchestration_validator|planning_context|world_overlay|recovery_config)" src tests -g '*.py' | sort > plan/V1.4/baseline/moved-or-deleted-imports-before-batch3.txt
cat plan/V1.4/baseline/moved-or-deleted-imports-before-batch3.txt
```

Expected current files include at least:

```text
tests/homemaster/test_agent_result_mapping.py
tests/homemaster/test_executor.py
tests/homemaster/test_frontdoor.py
tests/homemaster/test_homemaster_config.py
tests/homemaster/test_pipeline_core.py
tests/homemaster/test_prompt_externalization.py
tests/homemaster/test_recovery.py
tests/homemaster/test_recovery_loop.py
tests/homemaster/test_runtime_events.py
tests/homemaster/test_runtime_mode.py
tests/homemaster/test_scenario_snapshot.py
tests/homemaster/test_skill_registry.py
tests/homemaster/test_skill_selector.py
tests/homemaster/test_stage_01_llm_contract_smoke.py
tests/homemaster/test_stage_01_pipeline.py
tests/homemaster/test_stage_02_task_understanding_live.py
tests/homemaster/test_stage_04_grounding_context.py
tests/homemaster/test_stage_05_debug_assets_do_not_contain_secrets.py
tests/homemaster/test_stage_05_orchestration_live.py
tests/homemaster/test_stage_06_summary_memory_live.py
tests/homemaster/test_stage_07_scenarios_live.py
tests/homemaster/test_stage_registry.py
tests/homemaster/test_task_runner.py
tests/homemaster/test_task_runner_agent.py
```

Handling rules:

- Delete tests that only assert old stage/pipeline behavior.
- Rewrite tests that cover reusable algorithms to import their new domain/memory modules.
- Rewrite skills tests to assert generic skill package registration, not `mock_skills` or deterministic runtime mode selection.
- Rewrite `tests/homemaster/test_skill_loader.py` and `tests/homemaster/test_skill_registry_phase4.py` so they use the final skills API (`tool_names`, `load_builtin_skills()`, `SkillRegistry.all()` / `all_names()`), or delete them if their only purpose is old `allowed_tools` validation.
- Rewrite `tests/homemaster/test_embedding_degradation.py` to test `homemaster.memory.retrieval` or the `memory_retriever` tool against sanitized fixtures under `tests/homemaster/fixtures/home_tasks/`. It must not import top-level `homemaster.memory_rag`, read `data/scenarios`, or assert old `case_dir/actual.json` debug artifacts.
- Delete `tests/homemaster/test_recovery_config.py` if `recovery_config.py` is removed. If retry behavior remains, rewrite it around generic runtime retry budget / max tool iterations, not recovery-loop config.
- Rewrite event tests to assert generic runtime events only. After old runtime deletion, event modules should not retain old stage event types or stage progress labels.
- Do not leave skipped tests for deleted architecture.

- [ ] **Step 2: Write domain tool tests**

`tests/homemaster/test_domain_home_tools.py` must assert:

```python
from types import SimpleNamespace

from homemaster.agent.normalized import RunContext
from homemaster.config.runtime_settings import RuntimeSettings
from homemaster.domain.home.tool_registry import build_home_tool_registry
from homemaster.tools.results import ToolResult


class FakeTransportRuntime:
    def run_once(self, text: str):
        assert text == "你好"
        return SimpleNamespace(status="replied", tool_call_count=0)


def test_greeting_does_not_require_home_tools() -> None:
    result = FakeTransportRuntime().run_once("你好")
    assert result.status == "replied"
    assert result.tool_call_count == 0


def test_home_tool_registry_exposes_robot_tools() -> None:
    registry = build_home_tool_registry()
    names = set(registry.all_names())
    assert {
        "task_interpreter",
        "memory_retriever",
        "target_grounder",
        "skill_view",
        "robot_navigate",
        "robot_observe",
        "robot_manipulate",
        "robot_verify",
        "memory_writer",
        "task_summarizer",
    } <= names


def test_tool_failure_is_tool_result(tmp_path) -> None:
    registry = build_home_tool_registry(memory_path=tmp_path / "missing.json")
    tool = registry.get("memory_retriever")
    result = tool.executor(
        arguments={"query": "水杯"},
        run_context=RunContext(
            session_id="s1",
            run_id="r1",
            turn_index=0,
            settings=RuntimeSettings(
                run_id="r1",
                runtime_root=tmp_path,
                debug_root=tmp_path / "debug",
                results_root=tmp_path / "results",
            ),
            event_sink=None,
            deps={},
        ),
    )
    assert isinstance(result, ToolResult)
    assert result.success is False
    assert "missing" in (result.summary or result.failure_reason or "").lower() or "not found" in (result.summary or result.failure_reason or "").lower()
```

`tests/homemaster/test_skills_registry.py` must assert:

```python
from homemaster.skills.loader import load_builtin_skills
from homemaster.skills.registry import SkillRegistry


def test_builtin_skills_register_as_metadata_packages() -> None:
    registry = SkillRegistry()
    load_builtin_skills(registry)
    names = set(registry.all_names())
    assert "fetch_object" in names
    assert "check_object_state" in names
    skill = registry.get("fetch_object")
    assert skill.tool_names
    assert hasattr(skill, "metadata")
    assert hasattr(skill, "system_prompt_fragment")


def test_skills_do_not_define_runtime_modes() -> None:
    registry = SkillRegistry()
    load_builtin_skills(registry)
    for skill in registry.all():
        text = (skill.description or "") + " " + " ".join(skill.tool_names)
        assert "mock_skills" not in text
        assert "deterministic" not in text


def test_skill_view_uses_progressive_disclosure() -> None:
    registry = SkillRegistry()
    load_builtin_skills(registry)
    skill = registry.get("fetch_object")
    assert "full_prompt" not in skill.metadata
    assert set(skill.tool_names)
```

- [ ] **Step 3: Implement home tool registry**

`src/homemaster/domain/home/tool_registry.py` must provide:

```python
from __future__ import annotations

from pathlib import Path

from homemaster.domain.home.tools import (
    make_memory_retriever,
    make_memory_writer,
    make_robot_manipulate,
    make_robot_navigate,
    make_robot_observe,
    make_robot_verify,
    make_skill_view,
    make_target_grounder,
    make_task_interpreter,
    make_task_summarizer,
)
from homemaster.tools.registry import ToolRegistry


def build_home_tool_registry(
    *,
    world_path: Path | None = None,
    memory_path: Path | None = None,
    runtime_memory_root: Path | None = None,
) -> ToolRegistry:
    registry = ToolRegistry()
    for spec in (
        make_task_interpreter(),
        make_memory_retriever(memory_path=memory_path),
        make_target_grounder(world_path=world_path),
        make_skill_view(),
        make_robot_navigate(),
        make_robot_observe(),
        make_robot_manipulate(),
        make_robot_verify(),
        make_memory_writer(runtime_memory_root=runtime_memory_root),
        make_task_summarizer(),
    ):
        registry.register(spec)
    return registry
```

Rules:

- Domain tools may import `homemaster.tools.*`, `homemaster.memory.*`, and `homemaster.domain.home.*`.
- `make_skill_view()` should query `homemaster.skills.registry` or an injected `SkillRegistry`; it must not hard-code old builtin skill behavior.
- Domain tools must not import `homemaster.agent.runtime`, `homemaster.cli`, `homemaster.pipeline`, `homemaster.stages`, or `homemaster.task_runner`.
- Domain tools must return `ToolResult`; they must not return stage result dataclasses.

- [ ] **Step 4: Rewrite skills registry as a generic plugin metadata layer**

Keep `src/homemaster/skills/`. Do not delete it.

Required final behavior:

- `SkillSpec` describes a reusable skill package: name, description, `tool_names`, optional prompt/resource paths, and safety metadata. If the current implementation uses `allowed_tools`, either migrate to `tool_names` or provide a compatibility alias so tests and callers use one final API consistently.
- `SkillSpec` final shape includes `tools: list[ToolSpec]` or `tool_names: list[str]`, `system_prompt_fragment: str | None`, and `metadata: SkillMeta | dict[str, Any]`. Choose one concrete representation before implementation starts and make every test/snippet use that representation consistently.
- `SkillRegistry` supports `register()`, `get()`, `all()`, and `all_names()`. If the current registry only exposes `list_specs()`, add `all()` as the final public API or update every snippet in this plan to the chosen final name before implementation starts.
- `load_builtin_skills()` loads builtin skill metadata from `src/homemaster/skills/builtin/*/SKILL.md`.
- Progressive disclosure is mandatory: ContextComposer must not concatenate every builtin skill markdown into the system prompt. Default model context may include short skill metadata and `skill_view`; long skill instructions, examples, and resource text are returned only when `skill_view` is called.
- Builtin skill markdown must be rewritten as metadata registry documentation, not edited in place around the old workflow. It may describe capabilities and contributed tools, but must not contain numbered strategies, fixed step order, old tool names (`understand_task`, `retrieve_memory`, `ground_target`, `get_skill`, `update_memory`), numbered stages, scenario catalog, deterministic runtime mode, or `mock_skills`.
- The skills package must not import `homemaster.agent.runtime`, `homemaster.cli`, `homemaster.pipeline`, `homemaster.stages`, or `homemaster.task_runner`.

Run after rewriting:

```bash
rg -n "homemaster\\.(pipeline|stages|task_runner)|mock_skills|deterministic|scenario|Stage|stage_|allowed_tools|understand_task|retrieve_memory|ground_target|get_skill|update_memory|Strategy|Step [0-9]|^[[:space:]]*[0-9]+\\." src/homemaster/skills tests/homemaster/test_skills_registry.py tests/homemaster/test_skill_loader.py tests/homemaster/test_skill_registry_phase4.py
```

Expected:

```text
No matches.
```

- [ ] **Step 5: Move memory modules and update imports**

Use `git mv`:

```bash
mkdir -p src/homemaster/memory
git mv src/homemaster/fact_memory.py src/homemaster/memory/fact_memory.py
git mv src/homemaster/memory_commit.py src/homemaster/memory/commit.py
git mv src/homemaster/memory_index.py src/homemaster/memory/index.py
git mv src/homemaster/memory_profile.py src/homemaster/memory/profile.py
git mv src/homemaster/memory_rag.py src/homemaster/memory/retrieval.py
git mv src/homemaster/memory_tokenizer.py src/homemaster/memory/tokenizer.py
git mv src/homemaster/runtime_memory_store.py src/homemaster/memory/runtime_store.py
git mv src/homemaster/task_record.py src/homemaster/memory/task_record.py
```

Then update imports:

```bash
rg -n "homemaster\\.(fact_memory|memory_commit|memory_index|memory_profile|memory_rag|memory_tokenizer|runtime_memory_store|task_record)" src tests
```

Expected after edits:

```text
No matches.
```

Also rewrite `src/homemaster/memory/__init__.py` and moved memory module docstrings so they only describe generic memory package behavior. They must not mention numbered stages, stage-specific retrieval, or stage-specific commit flows. Rewrite `src/homemaster/embedding_client.py` from Stage 03 memory RAG wording to generic embedding/retrieval wording.

- [ ] **Step 6: Move or delete top-level home-domain modules**

Handle these files explicitly:

```text
src/homemaster/contracts.py
src/homemaster/execution_state.py
src/homemaster/failure_log.py
src/homemaster/failure_rule_provider.py
src/homemaster/grounding.py
src/homemaster/orchestration_validator.py
src/homemaster/planning_context.py
src/homemaster/world_overlay.py
```

Rules:

- Reusable schemas and algorithms move under `src/homemaster/domain/home/`.
- Old-only pipeline/stage/scenario wrappers are deleted.
- Tests must import the new domain modules, not top-level legacy modules.

After edits:

```bash
rg -n "homemaster\\.(contracts|execution_state|failure_log|failure_rule_provider|grounding|orchestration_validator|planning_context|world_overlay)" src tests -g '*.py'
```

Expected:

```text
No matches.
```

- [ ] **Step 6.5: Rewrite package entrypoints and old helper modules**

`src/homemaster/__init__.py` final behavior:

- describe HomeMaster as a generic agent runtime package plus domain tool packages;
- do not mention backward-compatibility shims, pipeline, stages, scenario runner, or old facades;
- do not export `pipeline`, `stages`, `task_runner`, `scenario_runner`, `scenario_catalog`, or `scenario_validator` in `__all__`.

`src/homemaster/agent/__init__.py` final behavior:

- describe messages, sessions, generic runtime, context composition, and turn loop;
- do not mention decision contracts or replacing a legacy pipeline stage loop;
- do not export `decision`.

`src/homemaster/trace.py` must be rewritten or removed. If kept, it must be a generic artifact/event helper and must not mention stage runs, `result.md`, `actual.json`, `expected.json`, `llm_samples.jsonl`, or stage debug assets.

`src/homemaster/recovery_config.py` must be deleted unless a generic retry budget setting replaces it. Any replacement belongs with the generic runtime/turn configuration and should use names such as `retry_budget` or `max_tool_iterations`, not recovery-loop names.

- [ ] **Step 7: Sanitize fixtures before deleting `data/scenarios`**

Create minimal fixtures manually. Do not `git mv` scenario directories as-is.

For each retained case, create:

```text
tests/homemaster/fixtures/home_tasks/<case>/case.json
tests/homemaster/fixtures/home_tasks/<case>/world.json
tests/homemaster/fixtures/home_tasks/<case>/memory.json
```

`case.json` schema:

```json
{
  "name": "fetch_cup_retry",
  "utterance": "去厨房找水杯，然后拿给我",
  "expected": {
    "reply_contains_any": ["水杯", "完成", "我在处理"],
    "tool_names_any": ["task_interpreter", "memory_retriever", "robot_observe"]
  }
}
```

Forbidden fixture keys:

```text
scenario
runtime_modes
deterministic
stage
stage_
stage_statuses
```

Before deleting `data/scenarios`, record what was intentionally dropped:

```bash
find data/scenarios -maxdepth 1 -mindepth 1 -type d -print | sort > plan/V1.4/baseline/scenario-fixtures-before-deletion.txt
```

- [ ] **Step 8: Delete old runtime packages and old assets**

Run only after Batch 2 CLI tests pass and Batch 3 domain tests exist:

```bash
rg -n "homemaster\\.(pipeline|stages|task_runner)|from homemaster import task_runner|LiveMimoDecisionClient" src/homemaster tests/homemaster -g '*.py'
```

Expected before deletion:

```text
Matches exist only in files that this batch will delete or rewrite.
```

Then delete:

```bash
git rm -r --ignore-unmatch src/homemaster/pipeline src/homemaster/stages
git rm --ignore-unmatch src/homemaster/task_runner.py src/homemaster/scenario_catalog.py src/homemaster/scenario_runner.py src/homemaster/scenario_validator.py
git rm --ignore-unmatch src/homemaster/providers/mimo_decision_client.py src/homemaster/agent/decision.py
git rm --ignore-unmatch src/homemaster/recovery_config.py
git rm --ignore-unmatch src/homemaster/prompts/stage_*.txt
git rm -r --ignore-unmatch tests/homemaster/llm_cases tests/homemaster/prompt_snapshots data/scenarios
git rm --ignore-unmatch tests/homemaster/prompt_snapshot_export.py
```

Delete or rewrite every test listed in `plan/V1.4/baseline/old-test-imports-before-batch3.txt`.
Update every import listed in `plan/V1.4/baseline/moved-or-deleted-imports-before-batch3.txt`; after this step there must be no imports of deleted scenario/MiMo decision modules or moved top-level memory modules.

- [ ] **Step 9: Clean legacy event compatibility**

Now that old runtime packages are deleted, remove old event shapes and progress labels from:

```text
src/homemaster/events/runtime_events.py
src/homemaster/events/sinks.py
src/homemaster/events/sanitizer.py
tests/homemaster/test_runtime_events.py
```

Required final event vocabulary:

```text
runtime.turn_started
transport.request_started
transport.delta
transport.response_completed
transport.request_failed
tool.call_started
tool.call_completed
tool.call_failed
runtime.turn_completed
runtime.turn_failed
runtime.budget_exhausted
runtime.cancelled
```

Forbidden event vocabulary:

```text
stage_started
stage_completed
stage_failed
stage
stage_statuses
```

- [ ] **Step 10: Verify Batch 3**

Run:

```bash
rg -n "homemaster\\.(pipeline|stages|task_runner)|from homemaster import task_runner|LiveMimoDecisionClient|mimo_decision_client" src/homemaster tests/homemaster -g '*.py'
rg -n "Stage|stage_|stage[0-9]|run_stage|stage_statuses|pipeline|scenario|deterministic|mock_skills|live_models|pipeline_compat|shim_lifecycle|legacy shim|legacy compat|stage runs|result\\.md|llm_samples\\.jsonl" src/homemaster tests/homemaster -g '!*.pyc'
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/homemaster/test_domain_home_tools.py \
  tests/homemaster/test_domain_memory_tools.py \
  tests/homemaster/test_skills_registry.py \
  tests/homemaster/test_domain_import_boundaries.py \
  tests/homemaster/test_agent_runtime.py \
  tests/homemaster/test_cli_help.py \
  tests/homemaster/test_cli_interactive.py \
  tests/homemaster/test_cli_run.py -q
PYTHONPATH=src .venv/bin/python -m ruff check src/homemaster tests/homemaster
```

Expected:

```text
The two rg commands print no matches in `src/homemaster` and `tests/homemaster`.
Selected tests pass.
ruff reports no errors.
```

Commit:

```bash
git add src/homemaster/domain/home/__init__.py src/homemaster/domain/home/contracts.py src/homemaster/domain/home/state.py src/homemaster/domain/home/tools.py src/homemaster/domain/home/tool_registry.py src/homemaster/domain/home/grounding.py src/homemaster/domain/home/planning_context.py src/homemaster/domain/home/world_overlay.py
git diff -- src/homemaster/__init__.py src/homemaster/agent/__init__.py src/homemaster/contracts.py src/homemaster/execution_state.py src/homemaster/failure_log.py src/homemaster/failure_rule_provider.py src/homemaster/grounding.py src/homemaster/orchestration_validator.py src/homemaster/planning_context.py src/homemaster/world_overlay.py src/homemaster/trace.py src/homemaster/events/runtime_events.py src/homemaster/events/sinks.py src/homemaster/events/sanitizer.py tests/homemaster/test_runtime_events.py
git add src/homemaster/__init__.py src/homemaster/agent/__init__.py src/homemaster/contracts.py src/homemaster/execution_state.py src/homemaster/failure_log.py src/homemaster/failure_rule_provider.py src/homemaster/grounding.py src/homemaster/orchestration_validator.py src/homemaster/planning_context.py src/homemaster/world_overlay.py src/homemaster/trace.py src/homemaster/events/runtime_events.py src/homemaster/events/sinks.py src/homemaster/events/sanitizer.py tests/homemaster/test_runtime_events.py
git diff -- src/homemaster/skills/loader.py src/homemaster/skills/registry.py src/homemaster/skills/spec.py src/homemaster/skills/builtin/check_object_state/SKILL.md src/homemaster/skills/builtin/fetch_object/SKILL.md tests/homemaster/test_skills_registry.py
git add src/homemaster/skills/loader.py src/homemaster/skills/registry.py src/homemaster/skills/spec.py src/homemaster/skills/builtin/check_object_state/SKILL.md src/homemaster/skills/builtin/fetch_object/SKILL.md tests/homemaster/test_skills_registry.py tests/homemaster/test_skill_loader.py tests/homemaster/test_skill_registry_phase4.py
git add src/homemaster/embedding_client.py src/homemaster/memory/__init__.py src/homemaster/memory/fact_memory.py src/homemaster/memory/commit.py src/homemaster/memory/index.py src/homemaster/memory/profile.py src/homemaster/memory/retrieval.py src/homemaster/memory/tokenizer.py src/homemaster/memory/runtime_store.py src/homemaster/memory/task_record.py
git add tests/homemaster/fixtures/home_tasks/fetch_cup_retry/case.json tests/homemaster/fixtures/home_tasks/fetch_cup_retry/world.json tests/homemaster/fixtures/home_tasks/fetch_cup_retry/memory.json tests/homemaster/fixtures/home_tasks/check_medicine_success/case.json tests/homemaster/fixtures/home_tasks/check_medicine_success/world.json tests/homemaster/fixtures/home_tasks/check_medicine_success/memory.json tests/homemaster/fixtures/home_tasks/object_not_found/case.json tests/homemaster/fixtures/home_tasks/object_not_found/world.json tests/homemaster/fixtures/home_tasks/object_not_found/memory.json
git add tests/homemaster/test_domain_home_tools.py tests/homemaster/test_domain_memory_tools.py tests/homemaster/test_domain_import_boundaries.py tests/homemaster/test_embedding_degradation.py tests/homemaster/test_recovery_config.py plan/V1.4/baseline/old-test-imports-before-batch3.txt plan/V1.4/baseline/moved-or-deleted-imports-before-batch3.txt plan/V1.4/baseline/scenario-fixtures-before-deletion.txt
git status --short
git commit -m "feat: migrate home capabilities to domain tools"
```

Before committing, inspect both inventory files and stage their entries manually. Use `git rm -- <path>` for tests that were deleted with the old architecture, `git add <path>` only for tests intentionally rewritten in this batch, and patch staging for any already-dirty file with unrelated user edits. Do not use command substitution or broad staging against the inventory output. `git diff --cached --name-only` must include old runtime deletions caused by `git rm` and every intentionally rewritten/deleted test from the two inventory files, but it must not include unrelated pre-existing user edits.

Batch 3 is not expected to make the entire repository pass the final legacy-term guard. README, config, `pyproject.toml`, and final prompt-loader cleanup are intentionally left for Batch 4. Batch 3 verification is scoped to `src/homemaster` and `tests/homemaster` after the old runtime packages and old tests are removed.

---

## Batch 4: Config, Prompts, Final Guard, And Acceptance

**Purpose:** Make cleanup irreversible, replace user-facing docs/config with agent-loop language, and prove the new main chain works.

**Files:**
- Modify: `config/homemaster.example.json`
- Modify: `src/homemaster/runtime.py`
- Modify: `src/homemaster/token_budget.py`
- Modify: `src/homemaster/config/runtime_settings.py`
- Modify: `src/homemaster/prompts/`
- Modify: `src/homemaster/prompt_loader.py`
- Modify: `README.md`
- Modify: `pyproject.toml`
- Modify: `scripts/guard_no_legacy_terms.py`
- Modify: `tests/homemaster/test_cleanup_guard.py`
- Modify: `tests/homemaster/test_import_boundaries.py`
- Update:
  - `tests/homemaster/test_homemaster_config.py`
  - `tests/homemaster/test_runtime_settings.py`
  - `tests/homemaster/test_token_budget.py`
  - `tests/homemaster/test_prompt_externalization.py` if it remains

- [ ] **Step 1: Rewrite config and token budget names**

`config/homemaster.example.json` token keys:

```json
{
  "token_budget": {
    "initial_max_tokens": {
      "agent_response": 4096,
      "tool_task_interpreter": 4096,
      "tool_memory_query": 4096,
      "tool_task_summarizer": 8192
    }
  }
}
```

`src/homemaster/token_budget.py` must expose these keys and reject unknown configured keys with a clear `RuntimeConfigError`.

`src/homemaster/config/runtime_settings.py` must remove old runtime configuration fields:

```text
scenario
live_models
mock_skills
runtime_modes
deterministic
skill_mode
scenario_root
case_dir
executor_step_multiplier
```

`skill_mode="simulated"` is still a production runtime mode and must be removed. If tests need fake tools, simulated tools, fake transports, or isolated case directories, inject them through test fixtures, `tests/homemaster/test_doubles/`, explicit domain tool registry construction, or CLI test monkeypatching, not through production runtime settings.

Update `tests/homemaster/test_runtime_settings.py` in the same batch. It must no longer contain literal blocked terms such as `mock_skills` or `live_models`, nor assertions for removed fields such as `skill_mode`, `scenario_root`, `case_dir`, or `executor_step_multiplier`. The final tests should assert the generic runtime settings shape and absence of old runtime-mode fields, or be deleted if they only exercised deprecated mode rejection.

Delete old config keys:

```text
stage_01_smoke
stage_02_task_card
stage_03_memory_query
stage_05_orchestration
stage_05_step_decision
stage_05_recovery
stage_06_summary
executor.step_multiplier
runtime_defaults.live_models
runtime_defaults.mock_skills
runtime_defaults.skill_mode
```

`pyproject.toml` is mandatory in this batch. Rewrite project description and package/test configuration so it contains no `pipeline`, numbered stage, scenario-runner, or legacy shim text outside `plan/V1.4/`. Remove obsolete per-file ignores for files deleted in Batch 3.

Explicit `pyproject.toml` cleanup requirements:

- Remove legacy shim per-file ignores for `src/homemaster/stage_04.py`, `src/homemaster/stage_05.py`, `src/homemaster/stage_06.py`, `src/homemaster/pipeline_core.py`, `src/homemaster/pipeline_stages.py`, `src/homemaster/recovery.py`, and any other deleted compatibility facade.
- Rewrite the project description so it does not say task pipeline.
- Add a guard or test assertion that those deleted facade path strings do not appear in `pyproject.toml`.

- [ ] **Step 2: Rebuild prompt assets from scratch**

Delete numbered prompt files in Batch 3 if they still exist. Create fresh prompt files with agent/tool names:

```text
src/homemaster/prompts/agent_system_prompt.txt
src/homemaster/prompts/task_interpreter_prompt.txt
src/homemaster/prompts/memory_query_prompt.txt
src/homemaster/prompts/task_summary_prompt.txt
```

Prompt writing rules:

- Do not copy text from any old `stage_*.txt` file.
- `agent_system_prompt.txt` should describe a general agent loop: answer directly when no tool is needed, ask clarifying questions when information is missing, call tools only when useful, and summarize tool results naturally.
- Tool-specific prompts may describe a single tool's input/output contract, but must not tell the model to call another tool before or after it.
- No prompt may contain numbered stage names, scenario names, deterministic runtime language, `mock_skills`, `pipeline`, `orchestration`, `step_decision`, `recovery stage`, or equivalent fixed-flow wording.
- No prompt may say or imply "always call task_interpreter first" or "verify before summary". These decisions belong to the model at runtime.

Update `src/homemaster/prompt_loader.py` in this batch. It currently references old numbered prompt names, so it must be rewritten to load only the new prompt files and its docstring/examples must not mention numbered stages. Do not keep an arbitrary filename loader that can still load deleted `stage_*.txt` prompts by name. Prefer an enum or closed prompt-id API such as `load_prompt(PromptId.AGENT_SYSTEM)` or `load_prompt(\"agent_system\")`.

Add or update `tests/homemaster/test_prompt_externalization.py` if it remains:

```python
from pathlib import Path

PROMPT_DIR = Path("src/homemaster/prompts")
FORBIDDEN = (
    "Stage",
    "stage_",
    "pipeline",
    "scenario",
    "deterministic",
    "mock_skills",
    "orchestration",
    "step_decision",
    "verify before summary",
    "always call task_interpreter first",
)


def test_new_prompts_do_not_encode_fixed_flow() -> None:
    for path in PROMPT_DIR.glob("*.txt"):
        text = path.read_text(encoding="utf-8")
        for term in FORBIDDEN:
            assert term not in text, f"{path} contains {term}"


def test_prompt_loader_rejects_deleted_numbered_prompt_names() -> None:
    from homemaster.prompt_loader import load_prompt

    try:
        load_prompt("stage_01_task_card_prompt")
    except (KeyError, ValueError, FileNotFoundError):
        return
    raise AssertionError("prompt loader accepted a deleted numbered prompt")
```

- [ ] **Step 3: Add final agent-loop behavior acceptance tests**

Create or update a focused test file such as `tests/homemaster/test_agent_loop_acceptance.py`:

```python
def test_chat_turn_uses_zero_tools(fake_agent_runtime) -> None:
    result = fake_agent_runtime.run_once("你好")
    assert result.final_reply
    assert result.tool_call_count == 0
    assert result.status == "replied"


def test_ambiguous_request_can_ask_clarifying_question(fake_agent_runtime) -> None:
    result = fake_agent_runtime.run_once("帮我拿那个东西")
    assert result.status == "replied"
    assert any(word in result.final_reply for word in ("哪个", "哪一个", "请告诉我"))
    assert result.tool_call_count <= 1


def test_task_turn_streams_model_and_tool_events(fake_agent_runtime) -> None:
    result = fake_agent_runtime.run_once("帮我拿个水")
    event_types = [event.type for event in result.events]
    assert "transport.request_started" in event_types
    assert "tool.call_started" in event_types
    assert "tool.call_completed" in event_types
```

Use the repository's actual fake runtime fixture names or define the fake runtime in this test file. Do not commit a test that references an undefined `fake_agent_runtime` fixture. The required acceptance behavior is fixed: chat can use 0 tools; ambiguous input can ask instead of planning; task input exposes model/tool progress.

- [ ] **Step 4: Enforce guard**

Change `tests/homemaster/test_cleanup_guard.py` to:

```python
from __future__ import annotations

import subprocess
import sys


def test_no_legacy_terms_remain_in_tracked_product_files() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/guard_no_legacy_terms.py"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert result.returncode == 0, result.stdout + result.stderr
```

The guard may skip only:

```text
plan/V1.4/
.git/
.venv/
.pytest_cache/
binary/image files
scripts/guard_no_legacy_terms.py itself
```

It must not ignore `src/`, `tests/`, `README.md`, `config/`, other `scripts/`, or tracked `data/`.

Final guard term rules:

- Block old package paths and identifiers exactly: `src/homemaster/pipeline/`, `src/homemaster/stages/`, `homemaster.pipeline`, `homemaster.stages`, `homemaster.task_runner`, `stage_`, `stage[0-9]`, `run_stage`, `stage_statuses`, `final_status`, `mock_skills`, `live_models`, `runtime_modes`, `deterministic`.
- Do not block broad substrings `compat` or `shim` by themselves. Only block targeted old-architecture terms such as `pipeline_compat`, `shim_lifecycle`, `legacy shim`, and `legacy compat`.
- Block legacy debug/live-case asset patterns: `llm_cases`, `prompt_snapshots`, `stage runs`, `llm_samples.jsonl`, and `result.md`. Also fail when tracked product/test files contain old case-artifact field clusters such as `case_dir` together with `actual.json`/`expected.json`/`input.json`.
- Block legacy package exports in `__all__`, including `pipeline`, `stages`, `task_runner`, `scenario_runner`, `scenario_catalog`, `scenario_validator`, and `decision`.
- Block deleted facade path strings in `pyproject.toml`: `stage_04.py`, `stage_05.py`, `stage_06.py`, `pipeline_core.py`, `pipeline_stages.py`, and `recovery.py`.
- The guard may report text in `plan/V1.4/` only if run with an explicit `--include-plan` debugging option; default enforced mode skips plan files so the plan can name forbidden terms.

- [ ] **Step 5: Rewrite import boundary tests**

`tests/homemaster/test_import_boundaries.py` final assertions:

```python
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_generic_agent_core_does_not_import_home_domain() -> None:
    for path in (ROOT / "src/homemaster/agent").glob("*.py"):
        if path.name in {"turn.py"}:
            continue
        text = path.read_text(encoding="utf-8")
        assert "homemaster.domain.home" not in text
        assert "homemaster.memory." not in text


def test_generic_tools_do_not_import_home_domain() -> None:
    for path in (ROOT / "src/homemaster/tools").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "homemaster.domain.home" not in text


def test_agent_context_composer_has_no_home_task_fields() -> None:
    for rel in ("src/homemaster/agent/context.py", "src/homemaster/agent/context_builder.py"):
        path = ROOT / rel
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        assert "task_card" not in text
        assert "target_candidates" not in text
        assert "current_location" not in text
        assert "holding_object" not in text
        assert "memory_hits" not in text


def test_skills_do_not_import_runtime_or_cli() -> None:
    for path in (ROOT / "src/homemaster/skills").rglob("*"):
        if path.suffix not in {".py", ".md"}:
            continue
        text = path.read_text(encoding="utf-8")
        assert "homemaster.agent.runtime" not in text
        assert "homemaster.cli" not in text
        assert "homemaster.pipeline" not in text
        assert "homemaster.stages" not in text


def test_domain_tools_do_not_import_cli_or_runtime_loop() -> None:
    for path in (ROOT / "src/homemaster/domain/home").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "homemaster.cli" not in text
        assert "homemaster.agent.runtime" not in text


def test_deleted_legacy_packages_are_absent() -> None:
    assert not (ROOT / "src/homemaster/pipeline").exists()
    assert not (ROOT / "src/homemaster/stages").exists()
    assert not (ROOT / "src/homemaster/task_runner.py").exists()
    assert not (ROOT / "src/homemaster/providers/mimo_decision_client.py").exists()


def test_package_entrypoints_do_not_export_deleted_legacy_packages() -> None:
    for rel in ("src/homemaster/__init__.py", "src/homemaster/agent/__init__.py"):
        text = read(rel)
        assert '"pipeline"' not in text
        assert '"stages"' not in text
        assert '"task_runner"' not in text
        assert '"decision"' not in text
```

- [ ] **Step 6: Run final automated checks**

Run:

```bash
python scripts/guard_no_legacy_terms.py
PYTHONPATH=src .venv/bin/python -m pytest -q
PYTHONPATH=src .venv/bin/python -m ruff check .
git status --short --ignored
```

Expected:

```text
guard prints no violations and exits 0.
pytest passes.
ruff reports no errors.
git status shows no tracked runtime/debug/cache outputs.
```

- [ ] **Step 7: Run CLI acceptance checks**

Run:

```bash
PYTHONPATH=src .venv/bin/python -m homemaster.cli --help
PYTHONPATH=src .venv/bin/python -m homemaster.cli run --utterance "你好" --run-id cli-greeting-smoke --progress
PYTHONPATH=src .venv/bin/python -m homemaster.cli run --utterance "帮我拿个水" --run-id cli-fetch-water-smoke --progress
```

Expected:

```text
--help lists run, shell, doctor only.
Greeting returns an assistant reply and no tool task status.
Fetch-water run prints model/tool progress and a final assistant reply.
No output contains numbered stage labels, scenario labels, final_status, or task-runner debug paths.
```

- [ ] **Step 8: Commit final guard and docs**

Commit:

```bash
git diff -- README.md config/homemaster.example.json src/homemaster/runtime.py src/homemaster/token_budget.py src/homemaster/config/runtime_settings.py src/homemaster/prompt_loader.py tests/homemaster/test_homemaster_config.py tests/homemaster/test_runtime_settings.py tests/homemaster/test_token_budget.py tests/homemaster/test_prompt_externalization.py
git add README.md pyproject.toml config/homemaster.example.json src/homemaster/runtime.py src/homemaster/token_budget.py src/homemaster/config/runtime_settings.py src/homemaster/prompts/agent_system_prompt.txt src/homemaster/prompts/task_interpreter_prompt.txt src/homemaster/prompts/memory_query_prompt.txt src/homemaster/prompts/task_summary_prompt.txt src/homemaster/prompt_loader.py scripts/guard_no_legacy_terms.py tests/homemaster/test_cleanup_guard.py tests/homemaster/test_import_boundaries.py tests/homemaster/test_homemaster_config.py tests/homemaster/test_runtime_settings.py tests/homemaster/test_token_budget.py tests/homemaster/test_prompt_externalization.py tests/homemaster/test_agent_loop_acceptance.py
git status --short
git commit -m "test: enforce generic agent loop boundary"
```

If `tests/homemaster/test_prompt_externalization.py` was deleted in Batch 3, omit it from `git add`.

## Handoff Rules For Low-Level Agents

- Each worker must run `git status --short` before editing.
- Each worker must only touch files listed in its batch.
- If a file is already modified, inspect `git diff -- <file>` and preserve existing user changes.
- Do not use broad staging commands such as `git add -u src`, `git add -u tests`, `git add -A`, or `git add .`.
- Do not reintroduce `src/homemaster/pipeline/`, `src/homemaster/stages/`, numbered prompt files, scenario runner APIs, or fixed-flow result fields.
- Keep tests narrow inside the batch, then run the listed verification commands.
- Commit once per batch with the exact commit message shown unless the parent coordinator changes it.

## Final Done Definition

V1.4 is done only when all of these are true:

- `python scripts/guard_no_legacy_terms.py` exits 0.
- `PYTHONPATH=src .venv/bin/python -m pytest -q` passes.
- `PYTHONPATH=src .venv/bin/python -m ruff check .` passes.
- CLI help exposes only `run`, `shell`, and `doctor`.
- `你好` returns a normal assistant reply without creating a home task.
- `帮我拿个水` runs through generic model/tool/result turns with live progress.
- `AgentRuntime` tests pass without importing `homemaster.domain.home`.
- Generic `homemaster.tools` tests pass without importing `homemaster.domain.home`.
- The repository has no tracked runtime/debug artifacts.
- No tracked file outside `plan/V1.4/` and `scripts/guard_no_legacy_terms.py` contains the blocked legacy terms.
