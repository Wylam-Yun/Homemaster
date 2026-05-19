# HomeMaster V1.4 Agent Loop Full Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the numbered stage architecture completely and leave HomeMaster as a generic message/tool-call/tool-result agent loop with home-robot capabilities exposed as tools.

**Architecture:** The runtime core owns sessions, messages, model calls, tool dispatch, tool-result messages, retries, budgets, and events. Home-robot logic lives in domain tools and memory/world packages; provider-specific response handling lives in transport adapters. CLI is a chat/session interface, not a scenario runner.

**Tech Stack:** Python 3.11, Pydantic v2, Typer, httpx, pytest, ruff.

---

## Current Findings

The current repository is not just carrying old text. The numbered stage architecture is still active in production paths:

- `src/homemaster/cli/app.py` still registers `stage` and `smoke` command groups and imports `homemaster.pipeline`.
- `src/homemaster/cli/interactive_shell.py` still guesses scenarios and calls `run_homemaster_task()`.
- `src/homemaster/cli/run_command.py` still exposes `--scenario` and prints `scenario`, `final_status`, and stage-style debug paths.
- `src/homemaster/task_runner.py` still builds stage-style results and writes `debug/stage_07/...`.
- `src/homemaster/tools/builtin.py` still wraps `pipeline.stage_runtime.run_stage02()` and `run_stage03()`.
- `src/homemaster/agent/runtime.py` is a tool loop shell, but it consumes custom JSON decisions instead of normalized assistant messages with provider-native tool calls.
- `src/homemaster/providers/mimo_decision_client.py` still asks the model for a custom `{"type": "tool_call"}` or `{"type": "finish"}` object.
- `src/homemaster/agent/state.py` is domain-state-shaped, so the generic runtime is coupled to home task fields.
- `src/homemaster/events/runtime_events.py` still includes `stage_started`, `stage_completed`, `stage_failed`, and a `stage` field.
- `src/homemaster/runtime.py`, `src/homemaster/token_budget.py`, and `config/homemaster.example.json` still expose stage-named defaults.
- `src/homemaster/pipeline/`, `src/homemaster/stages/`, numbered prompt files, numbered live cases, old docs, and tracked `var/` artifacts are still present.

The V1.4 implementation is compressed into five execution batches. Do not split this into eight or nine phases again; each batch below should end with a working, testable repo state.

## Final Package Shape

Create or preserve these package responsibilities:

- `src/homemaster/agent/`: generic runtime only. No home task schemas, no scenario logic, no domain tool implementations.
- `src/homemaster/providers/`: provider transports. MiMo/Anthropic/OpenAI response shapes are normalized here.
- `src/homemaster/tools/`: generic tool specs, registry, dispatcher, and result-message conversion.
- `src/homemaster/domain/home/`: home-robot schemas, domain state, and home tool implementations.
- `src/homemaster/memory/`: memory indexing, retrieval, commits, task records, and runtime memory storage.
- `src/homemaster/events/`: generic runtime event schema and sinks.
- `src/homemaster/cli/`: Typer commands and interactive shell only.
- `tests/homemaster/fixtures/`: lightweight fixtures for agent loop, domain tools, memory, and sessions.

Delete these package responsibilities:

- `src/homemaster/pipeline/`
- `src/homemaster/stages/`
- `src/homemaster/task_runner.py`
- `src/homemaster/scenario_catalog.py`
- `src/homemaster/scenario_runner.py`
- `src/homemaster/scenario_validator.py`
- numbered prompt assets under `src/homemaster/prompts/`
- numbered live-case artifacts under `tests/homemaster/llm_cases/`

## Batch 0: Baseline, Guard, And Static Cleanup

**Purpose:** Record the current surface, delete non-runtime historical artifacts, and install a guard before code migration starts.

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
  - `plan/V1.2/`
  - `plan/V1.3/`
  - `plan/v1.0/`
  - `plan/v1.1/`
  - `var/homemaster/debug/stage_07/fetch_cup_retry-1778074786/`
- Delete local generated artifacts if present:
  - `build/`
  - `.pytest_cache/`
  - every `__pycache__/`
  - every `*.pyc`
  - `plan/.DS_Store`
  - `tests/homemaster/llm_cases/stage_02/stage07_interactive-fetch_cup_retry_task_understanding/`
- Delete old scripts:
  - `scripts/capture_scenario_snapshot.py`
  - `scripts/compare_all_baselines.py`
  - `scripts/render_screenshots.py`
  - `scripts/run_homemaster_scenarios.sh`

- [ ] **Step 1: Save baseline command output**

Run:

```bash
git status --short > plan/V1.4/baseline/git-status-before.md
rg -n "Stage|stage_|stage[0-9]|run_stage|stage_statuses|pipeline|src/homemaster/stages|scenario|deterministic|mock_skills|live_models|compat|shim" src tests README.md config pyproject.toml scripts plan record report log var -g '!plan/V1.4/**' -g '!*.pyc' > plan/V1.4/baseline/legacy-surface-before.txt || true
git ls-files 'var/**' 'record/**' 'report/**' 'log/**' 'docs/**' 'plan/V1.2/**' 'plan/V1.3/**' 'plan/v1.0/**' 'plan/v1.1/**' > plan/V1.4/baseline/tracked-artifacts-before.txt
```

Expected:

```text
The three baseline files exist and contain the current dirty worktree and legacy hit list.
```

- [ ] **Step 2: Add the report-only guard**

Create `scripts/guard_no_legacy_terms.py` with this behavior:

```python
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BLOCKED_PATH_PARTS = (
    "src/homemaster/pipeline/",
    "src/homemaster/stages/",
    "tests/homemaster/llm_cases/",
    "tests/homemaster/prompt_snapshots/",
    "var/homemaster/",
)
BLOCKED_TEXT = re.compile(
    r"Stage|stage_|stage[0-9]|run_stage|stage_statuses|pipeline|scenario|"
    r"deterministic|mock_skills|live_models|compat|shim"
)
IGNORED_PREFIXES = (
    ".git/",
    ".venv/",
    ".pytest_cache/",
    "plan/V1.4/",
)
IGNORED_SUFFIXES = (".pyc", ".png", ".jpg", ".jpeg", ".gif")


def tracked_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=ROOT,
        text=True,
        check=True,
        stdout=subprocess.PIPE,
    )
    return [line for line in result.stdout.splitlines() if line]


def is_ignored(path: str) -> bool:
    return path.startswith(IGNORED_PREFIXES) or path.endswith(IGNORED_SUFFIXES)


def scan() -> list[str]:
    violations: list[str] = []
    for rel in tracked_files():
        if is_ignored(rel):
            continue
        if any(part in rel for part in BLOCKED_PATH_PARTS):
            violations.append(f"{rel}: blocked legacy path")
            continue
        path = ROOT / rel
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for index, line in enumerate(text.splitlines(), start=1):
            if BLOCKED_TEXT.search(line):
                violations.append(f"{rel}:{index}: {line.strip()[:180]}")
    return violations


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report-only", action="store_true")
    args = parser.parse_args()
    violations = scan()
    for item in violations:
        print(item)
    return 0 if args.report_only or not violations else 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 3: Add the guard test in report-only mode**

Create `tests/homemaster/test_cleanup_guard.py`:

```python
from __future__ import annotations

import subprocess


def test_legacy_guard_runs_in_report_only_mode() -> None:
    result = subprocess.run(
        ["python", "scripts/guard_no_legacy_terms.py", "--report-only"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert result.returncode == 0, result.stderr
```

- [ ] **Step 4: Update `.gitignore` for runtime and local artifacts**

Ensure `.gitignore` contains:

```gitignore
var/
build/
*.egg-info/
.pytest_cache/
__pycache__/
*.pyc
.DS_Store
.cache/homemaster/
```

- [ ] **Step 5: Delete static legacy artifacts**

Run:

```bash
git rm -r docs/shim_lifecycle.md record report log plan/V1.2 plan/V1.3 plan/v1.0 plan/v1.1 var/homemaster/debug/stage_07/fetch_cup_retry-1778074786
rm -rf build .pytest_cache plan/.DS_Store tests/homemaster/llm_cases/stage_02/stage07_interactive-fetch_cup_retry_task_understanding
find . -path ./.git -prune -o -path ./.venv -prune -o \( -name __pycache__ -o -name '*.pyc' \) -print
```

If the final `find` command prints paths, remove those local generated files with `rm -rf` for directories and `rm -f` for `*.pyc`.

- [ ] **Step 6: Verify Batch 0**

Run:

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/homemaster/test_cleanup_guard.py -q
python scripts/guard_no_legacy_terms.py --report-only > plan/V1.4/baseline/legacy-surface-after-static-cleanup.txt || true
git status --short
```

Expected:

```text
1 passed
git status shows only intentional V1.4 cleanup files plus pre-existing user edits.
```

Commit:

```bash
git add .gitignore scripts/guard_no_legacy_terms.py tests/homemaster/test_cleanup_guard.py plan/V1.4/baseline
git add -u docs record report log plan var
git commit -m "chore: remove historical pipeline artifacts"
```

## Batch 1: Generic Agent Messages, Session, And Transport

**Purpose:** Replace custom decision JSON with a generic message/tool-call/tool-result contract.

**Files:**
- Create: `src/homemaster/agent/messages.py`
- Create: `src/homemaster/agent/session.py`
- Create: `src/homemaster/agent/normalized.py`
- Create: `src/homemaster/agent/context.py`
- Create: `src/homemaster/providers/transport.py`
- Create: `src/homemaster/providers/mimo_transport.py`
- Delete after migration: `src/homemaster/agent/decision.py`
- Delete after migration: `src/homemaster/providers/mimo_decision_client.py`
- Rewrite: `src/homemaster/agent/runtime.py`
- Rewrite or simplify: `src/homemaster/llm_client.py`
- Modify: `src/homemaster/events/runtime_events.py`
- Modify: `src/homemaster/events/sinks.py`
- Tests:
  - Create: `tests/homemaster/test_agent_messages.py`
  - Create: `tests/homemaster/test_agent_session.py`
  - Create: `tests/homemaster/test_transport_mimo.py`
  - Replace: `tests/homemaster/test_agent_runtime.py`
  - Delete: `tests/homemaster/test_agent_decision_contract.py`
  - Delete: `tests/homemaster/test_mimo_decision_client.py`
  - Delete: `tests/homemaster/test_mimo_decision_with_context.py`

- [ ] **Step 1: Write failing message/session tests**

`tests/homemaster/test_agent_messages.py` must assert:

```python
from homemaster.agent.messages import AgentMessage, ToolCall, ToolResultMessage


def test_tool_result_message_round_trips_to_model_content() -> None:
    result = ToolResultMessage(
        tool_call_id="call_1",
        name="memory_retriever",
        success=False,
        content="memory path missing",
        data={"retryable": True},
    )
    assert result.role == "tool"
    assert result.to_model_content()["tool_call_id"] == "call_1"
    assert result.to_model_content()["content"] == "memory path missing"


def test_assistant_message_can_hold_content_and_tool_calls() -> None:
    msg = AgentMessage.assistant(
        content="我先查一下记忆。",
        tool_calls=[ToolCall(id="call_1", name="memory_retriever", arguments={"query": "水"})],
    )
    assert msg.role == "assistant"
    assert msg.tool_calls[0].name == "memory_retriever"
```

`tests/homemaster/test_agent_session.py` must assert:

```python
from homemaster.agent.session import AgentSession


def test_session_appends_user_and_assistant_messages() -> None:
    session = AgentSession.new(session_id="s1", run_id="r1")
    session.add_user_message("你好")
    session.add_assistant_message("你好，我在。")
    assert [m.role for m in session.messages] == ["user", "assistant"]
    assert session.turn_index == 1
```

- [ ] **Step 2: Implement message/session models**

`src/homemaster/agent/messages.py` public shape:

```python
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class AgentMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def user(cls, content: str) -> "AgentMessage":
        return cls(role="user", content=content)

    @classmethod
    def assistant(cls, content: str = "", tool_calls: list[ToolCall] | None = None) -> "AgentMessage":
        return cls(role="assistant", content=content, tool_calls=tool_calls or [])


class ToolResultMessage(AgentMessage):
    role: Literal["tool"] = "tool"
    tool_call_id: str
    name: str
    success: bool

    def to_model_content(self) -> dict[str, Any]:
        return {
            "tool_call_id": self.tool_call_id,
            "name": self.name,
            "content": self.content,
            "data": self.data,
            "success": self.success,
        }
```

`src/homemaster/agent/session.py` public shape:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from homemaster.agent.messages import AgentMessage


@dataclass
class AgentSession:
    session_id: str
    run_id: str
    messages: list[AgentMessage] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    turn_index: int = 0

    @classmethod
    def new(cls, *, session_id: str, run_id: str) -> "AgentSession":
        return cls(session_id=session_id, run_id=run_id)

    def add_user_message(self, content: str) -> AgentMessage:
        message = AgentMessage.user(content)
        self.messages.append(message)
        self.turn_index += 1
        return message

    def add_assistant_message(self, content: str) -> AgentMessage:
        message = AgentMessage.assistant(content)
        self.messages.append(message)
        return message
```

- [ ] **Step 3: Define normalized transport contract**

`src/homemaster/agent/normalized.py`:

```python
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from homemaster.agent.messages import ToolCall


class NormalizedAssistantMessage(BaseModel):
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    finish_reason: str = ""
    reasoning: str = ""
    usage: dict[str, Any] = Field(default_factory=dict)
    raw_response: dict[str, Any] = Field(default_factory=dict)

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)
```

`src/homemaster/providers/transport.py`:

```python
from __future__ import annotations

from typing import Protocol

from homemaster.agent.messages import AgentMessage
from homemaster.agent.normalized import NormalizedAssistantMessage
from homemaster.tools.spec import ToolSpec


class LLMTransport(Protocol):
    def complete(
        self,
        *,
        messages: list[AgentMessage],
        tools: list[ToolSpec],
        max_tokens: int,
        temperature: float = 0.0,
    ) -> NormalizedAssistantMessage: ...
```

- [ ] **Step 4: Implement MiMo transport**

`src/homemaster/providers/mimo_transport.py` must:

- convert `AgentMessage` objects into Anthropic `/v1/messages` format;
- send `tools` as native Anthropic tools when protocol is `anthropic`;
- parse Anthropic `text` and `tool_use` blocks into `NormalizedAssistantMessage`;
- parse OpenAI `message.content` and `message.tool_calls` when protocol is `openai`;
- preserve `response_missing_text` as non-fatal when tool calls exist;
- raise `LLMProviderResponseError` only when there is neither visible content nor tool calls.

The test fixture response should include:

```python
anthropic_tool_response = {
    "content": [
        {"type": "text", "text": "我先查一下。"},
        {
            "type": "tool_use",
            "id": "toolu_1",
            "name": "memory_retriever",
            "input": {"query": "水杯"},
        },
    ],
    "stop_reason": "tool_use",
}
```

- [ ] **Step 5: Rewrite runtime event names**

Modify `src/homemaster/events/runtime_events.py`:

- remove `stage_started`, `stage_completed`, `stage_failed`;
- rename dataclass field `stage` to `component`;
- add `assistant_message_received`, `tool_result_appended`, `final_reply_emitted`, `model_call_started`, `model_call_completed`, `model_call_failed`;
- keep JSON serialization stable by always including all fields.

Update `src/homemaster/events/sinks.py` so progress output uses model/tool/reply events and never prints numbered stage labels.

- [ ] **Step 6: Rewrite AgentRuntime**

`src/homemaster/agent/runtime.py` must expose:

```python
from dataclasses import dataclass
from pathlib import Path

from homemaster.agent.session import AgentSession
from homemaster.agent.messages import AgentMessage


@dataclass(frozen=True)
class AgentRunResult:
    run_id: str
    status: str
    final_reply: str
    session: AgentSession
    trace_path: Path | None


class AgentRuntime:
    def run_turn(self, *, session: AgentSession, user_input: str) -> AgentRunResult:
        ...
```

Required loop behavior:

- append the user message;
- call `ContextComposer` to produce model messages;
- call `LLMTransport.complete()`;
- append assistant content/tool calls as an assistant message;
- if there are tool calls, dispatch all calls, append `ToolResultMessage` objects, and continue;
- if there are no tool calls, return `status="replied"` and `final_reply=<assistant content>`;
- if `max_turns` is exceeded, return `status="failed"` with a human-readable final reply;
- invalid tool names and validation failures become tool-result messages visible to the next model turn.

- [ ] **Step 7: Verify Batch 1**

Run:

```bash
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/homemaster/test_agent_messages.py \
  tests/homemaster/test_agent_session.py \
  tests/homemaster/test_transport_mimo.py \
  tests/homemaster/test_agent_runtime.py -q
PYTHONPATH=src .venv/bin/python -m ruff check src/homemaster/agent src/homemaster/providers tests/homemaster/test_agent_messages.py tests/homemaster/test_agent_session.py tests/homemaster/test_transport_mimo.py tests/homemaster/test_agent_runtime.py
```

Expected:

```text
All selected tests pass.
ruff reports no errors.
```

Commit:

```bash
git add src/homemaster/agent src/homemaster/providers src/homemaster/llm_client.py src/homemaster/events tests/homemaster/test_agent_messages.py tests/homemaster/test_agent_session.py tests/homemaster/test_transport_mimo.py tests/homemaster/test_agent_runtime.py
git rm src/homemaster/agent/decision.py src/homemaster/providers/mimo_decision_client.py tests/homemaster/test_agent_decision_contract.py tests/homemaster/test_mimo_decision_client.py tests/homemaster/test_mimo_decision_with_context.py
git commit -m "feat: add generic agent transport loop"
```

## Batch 2: Domain Tool Migration And Old Runtime Deletion

**Purpose:** Move home-robot capabilities behind tools, then delete the old fixed-flow runtime.

**Files:**
- Create: `src/homemaster/domain/home/__init__.py`
- Create: `src/homemaster/domain/home/contracts.py`
- Create: `src/homemaster/domain/home/state.py`
- Create: `src/homemaster/domain/home/tools.py`
- Create: `src/homemaster/domain/home/tool_registry.py`
- Move/merge:
  - `src/homemaster/contracts.py` -> `src/homemaster/domain/home/contracts.py`
  - `src/homemaster/grounding.py` -> `src/homemaster/domain/home/grounding.py`
  - `src/homemaster/planning_context.py` -> `src/homemaster/domain/home/planning_context.py`
  - `src/homemaster/world_overlay.py` -> `src/homemaster/domain/home/world_overlay.py`
  - `src/homemaster/failure_log.py` -> `src/homemaster/domain/home/failure_log.py`
  - `src/homemaster/failure_rule_provider.py` -> `src/homemaster/domain/home/failure_rules.py`
  - `src/homemaster/recovery_config.py` -> `src/homemaster/domain/home/recovery_config.py`
  - `src/homemaster/orchestration_validator.py` -> remove if no longer used by generic loop, otherwise fold validation into `domain/home/contracts.py`
  - `src/homemaster/execution_state.py` -> fold into `domain/home/state.py`
- Move memory modules into `src/homemaster/memory/`:
  - `fact_memory.py`
  - `memory_commit.py`
  - `memory_index.py`
  - `memory_profile.py`
  - `memory_rag.py`
  - `memory_tokenizer.py`
  - `runtime_memory_store.py`
  - `task_record.py`
- Delete:
  - `src/homemaster/pipeline/`
  - `src/homemaster/stages/`
  - `src/homemaster/task_runner.py`
  - `src/homemaster/scenario_catalog.py`
  - `src/homemaster/scenario_runner.py`
  - `src/homemaster/scenario_validator.py`
  - `tests/homemaster/test_pipeline_core.py`
  - `tests/homemaster/test_stage_registry.py`
  - `tests/homemaster/test_task_runner.py`
  - `tests/homemaster/test_task_runner_agent.py`
  - `tests/homemaster/test_scenario_runner.py`
  - `tests/homemaster/test_scenario_snapshot.py`
  - `tests/homemaster/test_scenario_validator.py`
- Tests:
  - Create: `tests/homemaster/test_domain_home_tools.py`
  - Create: `tests/homemaster/test_domain_memory_tools.py`
  - Create: `tests/homemaster/test_domain_import_boundaries.py`

- [ ] **Step 1: Write domain tool tests first**

`tests/homemaster/test_domain_home_tools.py` must assert:

```python
from homemaster.agent.messages import ToolResultMessage
from homemaster.domain.home.tool_registry import build_home_tool_registry


def test_greeting_does_not_require_home_tools(fake_runtime) -> None:
    result = fake_runtime.run_once("你好")
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


def test_tool_failure_is_a_tool_result_message(tmp_path) -> None:
    registry = build_home_tool_registry(memory_path=tmp_path / "missing.json")
    tool = registry.get("memory_retriever")
    result = tool.executor(arguments={"query": "水杯"}, state={}, settings={})
    message = ToolResultMessage(
        tool_call_id="call_1",
        name="memory_retriever",
        success=result.success,
        content=result.summary or result.failure_reason or "",
        data=result.data,
    )
    assert message.success is False
    assert "missing" in message.content.lower() or "not found" in message.content.lower()
```

- [ ] **Step 2: Implement home tool registry**

`src/homemaster/domain/home/tool_registry.py`:

```python
from __future__ import annotations

from pathlib import Path

from homemaster.tools.registry import ToolRegistry
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

- [ ] **Step 3: Implement domain tools as wrappers around renamed algorithms**

`src/homemaster/domain/home/tools.py` must provide tool specs with these names:

```text
task_interpreter
memory_retriever
target_grounder
skill_view
robot_navigate
robot_observe
robot_manipulate
robot_verify
memory_writer
task_summarizer
```

Rules:

- `task_interpreter` may call MiMo through `LLMTransport` only if needed; it must not force every user message into a task card.
- `memory_retriever` wraps the current memory retrieval algorithm after it is moved under `src/homemaster/memory/`.
- `target_grounder` wraps current grounding logic after it is moved under `src/homemaster/domain/home/grounding.py`.
- robot tools use the current simulated behavior from `src/homemaster/tools/simulated.py`, but they must be renamed to `robot_*` and return `ToolResult` only.
- no domain tool may import `homemaster.agent.runtime`.
- no domain tool may write numbered debug paths.

- [ ] **Step 4: Move memory modules and update imports**

Use `git mv` for these moves:

```bash
git mv src/homemaster/fact_memory.py src/homemaster/memory/fact_memory.py
git mv src/homemaster/memory_commit.py src/homemaster/memory/commit.py
git mv src/homemaster/memory_index.py src/homemaster/memory/index.py
git mv src/homemaster/memory_profile.py src/homemaster/memory/profile.py
git mv src/homemaster/memory_rag.py src/homemaster/memory/retrieval.py
git mv src/homemaster/memory_tokenizer.py src/homemaster/memory/tokenizer.py
git mv src/homemaster/runtime_memory_store.py src/homemaster/memory/runtime_store.py
git mv src/homemaster/task_record.py src/homemaster/memory/task_record.py
```

Then update imports with `rg`-guided edits:

```bash
rg -n "homemaster\\.(fact_memory|memory_commit|memory_index|memory_profile|memory_rag|memory_tokenizer|runtime_memory_store|task_record)" src tests
```

Expected after edits:

```text
No matches.
```

- [ ] **Step 5: Delete old runtime packages**

Run only after the new domain tool tests and generic runtime tests pass:

```bash
git rm -r src/homemaster/pipeline src/homemaster/stages
git rm src/homemaster/task_runner.py src/homemaster/scenario_catalog.py src/homemaster/scenario_runner.py src/homemaster/scenario_validator.py
```

- [ ] **Step 6: Verify Batch 2**

Run:

```bash
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/homemaster/test_domain_home_tools.py \
  tests/homemaster/test_domain_memory_tools.py \
  tests/homemaster/test_domain_import_boundaries.py \
  tests/homemaster/test_agent_runtime.py -q
PYTHONPATH=src .venv/bin/python -m ruff check src/homemaster tests/homemaster/test_domain_home_tools.py tests/homemaster/test_domain_memory_tools.py tests/homemaster/test_domain_import_boundaries.py
```

Expected:

```text
All selected tests pass.
ruff reports no errors.
```

Commit:

```bash
git add src/homemaster/domain src/homemaster/memory src/homemaster/tools tests/homemaster/test_domain_home_tools.py tests/homemaster/test_domain_memory_tools.py tests/homemaster/test_domain_import_boundaries.py
git add -u src/homemaster tests/homemaster
git commit -m "feat: migrate home capabilities to domain tools"
```

## Batch 3: CLI, Config, Prompts, And Fixtures

**Purpose:** Make the visible product a chat/task agent shell and rebuild tests around the new loop.

**Files:**
- Rewrite:
  - `src/homemaster/cli/app.py`
  - `src/homemaster/cli/interactive_shell.py`
  - `src/homemaster/cli/run_command.py`
  - `src/homemaster/cli/doctor.py`
  - `src/homemaster/cli/errors.py`
  - `src/homemaster/runtime.py`
  - `src/homemaster/token_budget.py`
  - `config/homemaster.example.json`
  - `README.md`
  - `pyproject.toml`
- Prompt cleanup:
  - Delete: every `src/homemaster/prompts/stage_*.txt`
  - Create: `src/homemaster/prompts/agent_system_prompt.txt`
  - Create: `src/homemaster/prompts/task_interpreter_prompt.txt`
  - Create: `src/homemaster/prompts/memory_query_prompt.txt`
  - Create: `src/homemaster/prompts/task_summary_prompt.txt`
- Fixture cleanup:
  - Delete: `tests/homemaster/llm_cases/`
  - Delete: `tests/homemaster/prompt_snapshots/`
  - Delete: `tests/homemaster/prompt_snapshot_export.py`
  - Create: `tests/homemaster/fixtures/agent_loop/`
  - Create: `tests/homemaster/fixtures/home_tasks/`
  - Create: `tests/homemaster/fixtures/sessions/`
- Delete old tests:
  - every `tests/homemaster/test_stage_*.py`
  - `tests/homemaster/test_prompt_externalization.py`
  - `tests/homemaster/test_frontdoor.py` if it only asserts old task-understanding entrypoints
  - `tests/homemaster/test_runtime_mode.py`
- Keep and update:
  - `tests/homemaster/test_cli_interactive.py`
  - `tests/homemaster/test_cli_run.py`
  - `tests/homemaster/test_cli_doctor.py`
  - `tests/homemaster/test_cli_help.py`
  - `tests/homemaster/test_homemaster_config.py`
  - `tests/homemaster/test_token_budget.py`
  - memory/domain algorithm tests after import updates

- [ ] **Step 1: Rewrite CLI expectations**

`tests/homemaster/test_cli_interactive.py` must include:

```python
def test_shell_greeting_returns_reply_without_task_status(cli_runner, monkeypatch) -> None:
    monkeypatch.setattr(
        "homemaster.cli.interactive_shell.run_agent_turn",
        lambda session, text: FakeTurn(status="replied", final_reply="你好，我在。", tool_events=[]),
    )
    result = cli_runner.invoke(app, ["shell"], input="你好\n/exit\n")
    assert "你好，我在。" in result.stdout
    assert "final_status" not in result.stdout
    assert "scenario" not in result.stdout
```

`tests/homemaster/test_cli_run.py` must include:

```python
def test_run_command_prints_assistant_reply_and_trace(cli_runner, monkeypatch, tmp_path) -> None:
    trace = tmp_path / "runs" / "r1" / "events.jsonl"
    monkeypatch.setattr(
        "homemaster.cli.run_command.run_single_turn",
        lambda **kwargs: FakeTurn(status="replied", final_reply="已完成。", trace_path=trace),
    )
    result = cli_runner.invoke(app, ["run", "--utterance", "帮我拿个水"])
    assert result.exit_code == 0
    assert "assistant: 已完成。" in result.stdout
    assert "trace:" in result.stdout
    assert "stage" not in result.stdout.lower()
```

- [ ] **Step 2: Rewrite CLI commands**

`src/homemaster/cli/app.py` final command set:

```text
homemaster run --utterance TEXT [--world PATH] [--memory PATH] [--run-id ID] [--progress]
homemaster shell
homemaster doctor [--live] [--json]
```

Remove:

```text
homemaster stage ...
homemaster smoke ...
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
- `/events` tails or prints the last trace path.

- [ ] **Step 3: Rewrite config and token budget names**

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

`src/homemaster/token_budget.py` must expose these keys and reject unknown configured keys with a clear `RuntimeConfigError`. Update `tests/homemaster/test_token_budget.py` and `tests/homemaster/test_homemaster_config.py` accordingly.

- [ ] **Step 4: Rebuild prompt assets**

Delete numbered prompt files:

```bash
git rm src/homemaster/prompts/stage_*.txt
git rm -r tests/homemaster/prompt_snapshots tests/homemaster/llm_cases
git rm tests/homemaster/prompt_snapshot_export.py
```

Create prompt files with names listed above. Update `pyproject.toml` package data only if needed:

```toml
[tool.setuptools.package-data]
homemaster = ["prompts/*.txt"]
```

Keep `src/homemaster/prompt_loader.py` only if it loads the new prompt names without mentioning numbered stage examples.

- [ ] **Step 5: Move fixture data out of production scenario paths**

The final product should not need `data/scenarios/` to run. Use `git mv` to move high-value test fixtures:

```bash
mkdir -p tests/homemaster/fixtures/home_tasks
git mv data/scenarios/fetch_cup_retry tests/homemaster/fixtures/home_tasks/fetch_cup_retry
git mv data/scenarios/check_medicine_success tests/homemaster/fixtures/home_tasks/check_medicine_success
git mv data/scenarios/object_not_found tests/homemaster/fixtures/home_tasks/object_not_found
git rm -r data/scenarios
```

Tests that need more cases can add small fixture JSON under `tests/homemaster/fixtures/home_tasks/`. Do not keep a production scenario catalog.

- [ ] **Step 6: Verify Batch 3**

Run:

```bash
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/homemaster/test_cli_help.py \
  tests/homemaster/test_cli_interactive.py \
  tests/homemaster/test_cli_run.py \
  tests/homemaster/test_cli_doctor.py \
  tests/homemaster/test_homemaster_config.py \
  tests/homemaster/test_token_budget.py -q
PYTHONPATH=src .venv/bin/python -m ruff check src/homemaster tests/homemaster
```

Expected:

```text
All selected tests pass.
ruff reports no errors.
```

Commit:

```bash
git add README.md pyproject.toml config src/homemaster/cli src/homemaster/runtime.py src/homemaster/token_budget.py src/homemaster/prompts tests/homemaster/fixtures tests/homemaster/test_cli_help.py tests/homemaster/test_cli_interactive.py tests/homemaster/test_cli_run.py tests/homemaster/test_cli_doctor.py tests/homemaster/test_homemaster_config.py tests/homemaster/test_token_budget.py
git add -u src tests data
git commit -m "feat: rebuild cli around agent sessions"
```

## Batch 4: Final Guard, Import Boundaries, And Acceptance

**Purpose:** Make the cleanup irreversible and prove the new main chain works.

**Files:**
- Modify: `scripts/guard_no_legacy_terms.py`
- Modify: `tests/homemaster/test_cleanup_guard.py`
- Modify: `tests/homemaster/test_import_boundaries.py`
- Modify: `README.md`
- Add final smoke fixtures under `tests/homemaster/fixtures/agent_loop/` if missing.

- [ ] **Step 1: Switch guard to enforced mode**

Change `tests/homemaster/test_cleanup_guard.py` to:

```python
from __future__ import annotations

import subprocess


def test_no_legacy_terms_remain_in_tracked_product_files() -> None:
    result = subprocess.run(
        ["python", "scripts/guard_no_legacy_terms.py"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert result.returncode == 0, result.stdout + result.stderr
```

The guard may ignore only:

```text
plan/V1.4/
.git/
.venv/
binary/image files
```

It must not ignore `src/`, `tests/`, `README.md`, `config/`, `scripts/`, or tracked `data/`.

- [ ] **Step 2: Rewrite import boundary tests**

`tests/homemaster/test_import_boundaries.py` final assertions:

```python
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_agent_runtime_does_not_import_home_domain() -> None:
    text = read("src/homemaster/agent/runtime.py")
    assert "homemaster.domain.home" not in text
    assert "homemaster.memory" not in text


def test_domain_tools_do_not_import_cli_or_runtime_loop() -> None:
    for path in (ROOT / "src/homemaster/domain/home").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "homemaster.cli" not in text
        assert "homemaster.agent.runtime" not in text


def test_deleted_legacy_packages_are_absent() -> None:
    assert not (ROOT / "src/homemaster/pipeline").exists()
    assert not (ROOT / "src/homemaster/stages").exists()
    assert not (ROOT / "src/homemaster/task_runner.py").exists()
```

- [ ] **Step 3: Run final automated checks**

Run:

```bash
python scripts/guard_no_legacy_terms.py
PYTHONPATH=src .venv/bin/python -m pytest -q
PYTHONPATH=src .venv/bin/python -m ruff check .
git status --short --ignored
```

Expected:

```text
guard prints nothing and exits 0
pytest passes
ruff reports no errors
git status shows no tracked runtime/debug/cache outputs
```

- [ ] **Step 4: Run CLI acceptance checks**

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
No output contains numbered stage labels.
```

- [ ] **Step 5: Commit final guard**

Commit:

```bash
git add README.md scripts/guard_no_legacy_terms.py tests/homemaster/test_cleanup_guard.py tests/homemaster/test_import_boundaries.py tests/homemaster/fixtures
git commit -m "test: enforce agent loop cleanup guard"
```

## Handoff Rules For Low-Level Agents

- Each worker must run `git status --short` before editing.
- Each worker must only touch files listed in its batch.
- If a file is already modified, inspect `git diff -- <file>` and preserve existing user changes.
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
- The repository has no tracked runtime/debug artifacts.
