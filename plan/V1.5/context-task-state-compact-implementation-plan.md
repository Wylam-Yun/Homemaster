# HomeMaster V1.5 Context, Task State, and Compaction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the V1.5 context architecture from `plan/V1.5/task-state-snapshot-spec.md`: system prompt delivery, task-state snapshots, provider-driven context assembly, budgeted compaction, runtime guards, typed configuration, and cleanup of old context/state leftovers.

**Architecture:** The runtime will no longer send raw `session.messages` directly to the provider. Every model call will go through `prepare_model_context()`, which collects `ContextItem`s from providers, estimates budget from the active provider profile, compacts when needed, and renders a `ComposedContext` with `system_prompt`, messages, tools, and metrics. `AgentState` remains internal runtime bookkeeping; model-visible task/failure/budget context is derived through providers.

**Tech Stack:** Python 3.11+, Pydantic, existing HomeMaster `AgentSession`/`GenericAgentRuntime`/`MimoTransport`, pytest, ruff.

---

## File Structure

Create:

- `src/homemaster/config/model_config.py` - typed global config models for providers, runtime guards, context policy, and prompt ids.
- `src/homemaster/agent/context_items.py` - `ContextItem`, priority/freshness/placement enums, render modes, and token metadata.
- `src/homemaster/agent/context_providers.py` - provider protocol and built-in providers: conversation, task snapshot, failure summary, runtime budget status.
- `src/homemaster/agent/context_budget.py` - token estimation, context window resolution, thresholds, and budget decisions.
- `src/homemaster/agent/compact.py` - deterministic micro-compaction and LLM-summary boundary plumbing.
- `src/homemaster/agent/context_assembler.py` - orchestration of providers, budget, compaction, and final rendering.
- `src/homemaster/task_state/__init__.py` - task-state package exports.
- `src/homemaster/task_state/models.py` - `TaskSnapshot`, `TaskSubtask`, updates, statuses.
- `src/homemaster/task_state/store.py` - run-scoped `TaskStateStore`.
- `src/homemaster/task_state/tools.py` - generic `task_planner` and `task_progress_check` tool specs.
- `tests/homemaster/test_model_config.py`
- `tests/homemaster/test_transport_system_prompt.py`
- `tests/homemaster/test_agent_state_v15.py`
- `tests/homemaster/test_task_state_store.py`
- `tests/homemaster/test_task_state_tools.py`
- `tests/homemaster/test_context_budget.py`
- `tests/homemaster/test_context_assembler.py`
- `tests/homemaster/test_context_compact.py`

Modify:

- `config/homemaster.example.json` - add provider/runtime/context/prompt examples.
- `src/homemaster/runtime.py` - load provider profiles with `context_window_tokens` and `max_output_tokens`.
- `src/homemaster/config/runtime_settings.py` - add typed runtime/config fields and loader support.
- `src/homemaster/agent/state.py` - replace old minimal state with V1.5 runtime bookkeeping.
- `src/homemaster/agent/session.py` - add safe message replacement for compacted model-visible history.
- `src/homemaster/agent/generic_runtime.py` - add `AgentState`, loop guards, and `prepare_model_context()` before every model call.
- `src/homemaster/providers/transport.py` - add `system_prompt` to `stream()` and `complete()`.
- `src/homemaster/providers/mimo_transport.py` - include system prompt in Anthropic/OpenAI payloads and max output tokens when configured.
- `src/homemaster/agent/turn.py` - build settings/config/assembler and remove hard-coded `max_tool_iterations=12`.
- `src/homemaster/benchmarking/alfworld/runner.py` - inject `TaskStateStore`, context assembler, and runtime guard config.
- `src/homemaster/domain/home/tool_registry.py` or equivalent registry builder - register task-state tools for generic runs.
- `src/homemaster/prompt_loader.py` - already has `COMPACT_SUMMARY`; convert `PromptId` to `StrEnum` to satisfy ruff.
- existing tests using fake transports - update fake `stream()` signatures to accept `system_prompt`.

Delete after migration:

- `src/homemaster/agent/context_builder.py`
- old tests that only validate `ContextBuilder`
- unused `src/homemaster/tools/state_updater.py` if no active import remains
- duplicate old builtin/skill wrapper modules if no active import remains

---

## Task 1: Typed Configuration and Provider Profiles

**Files:**
- Create: `src/homemaster/config/model_config.py`
- Modify: `src/homemaster/runtime.py`
- Modify: `src/homemaster/config/runtime_settings.py`
- Modify: `config/homemaster.example.json`
- Test: `tests/homemaster/test_model_config.py`

- [ ] **Step 1: Write failing config tests**

Add `tests/homemaster/test_model_config.py`:

```python
from pathlib import Path

from homemaster.config.model_config import (
    ContextPolicyConfig,
    HomeMasterConfig,
    ProviderProfileConfig,
    RuntimeGuardConfig,
    load_model_config,
)


def test_provider_profile_carries_context_window_and_keys() -> None:
    provider = ProviderProfileConfig(
        name="mimo_v25",
        protocol="anthropic",
        base_url="https://mimo.example",
        model="MiMo-V2.5",
        api_keys=["secret-one"],
        context_window_tokens=1_000_000,
        max_output_tokens=8192,
    )

    assert provider.name == "mimo_v25"
    assert provider.context_window_tokens == 1_000_000
    assert provider.max_output_tokens == 8192
    assert provider.api_keys == ("secret-one",)


def test_context_policy_defaults_match_v15_spec() -> None:
    policy = ContextPolicyConfig()

    assert policy.auto_compact_enabled is True
    assert policy.compression_threshold_ratio == 0.50
    assert policy.recent_tail_ratio == 0.20
    assert policy.preserve_recent_agent_steps == 20
    assert policy.preserve_recent_user_turns == 3
    assert policy.safety_buffer_tokens == 13_000


def test_runtime_guard_defaults_allow_unbounded_tool_iterations() -> None:
    guards = RuntimeGuardConfig()

    assert guards.max_tool_iterations is None
    assert guards.max_consecutive_tool_errors == 5
    assert guards.max_no_progress_iterations == 20


def test_load_model_config_from_json(tmp_path: Path) -> None:
    path = tmp_path / "homemaster.json"
    path.write_text(
        """
        {
          "providers": {
            "default": "mimo_v25",
            "items": [
              {
                "name": "mimo_v25",
                "protocol": "anthropic",
                "base_url": "https://mimo.example",
                "model": "MiMo-V2.5",
                "api_keys": ["secret-one"],
                "context_window_tokens": 1000000,
                "max_output_tokens": 8192
              }
            ]
          },
          "runtime": {
            "max_tool_iterations": null,
            "max_consecutive_tool_errors": 5,
            "max_no_progress_iterations": 20
          }
        }
        """,
        encoding="utf-8",
    )

    config = load_model_config(path)

    assert isinstance(config, HomeMasterConfig)
    assert config.providers.default == "mimo_v25"
    assert config.get_provider("mimo_v25").context_window_tokens == 1_000_000
    assert config.runtime.max_tool_iterations is None
```

- [ ] **Step 2: Run tests and confirm failure**

Run:

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q tests/homemaster/test_model_config.py
```

Expected: import failure for `homemaster.config.model_config`.

- [ ] **Step 3: Add typed config models**

Create `src/homemaster/config/model_config.py`:

```python
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from homemaster.runtime import RuntimeConfigError


ProtocolName = Literal["anthropic", "openai"]


class ProviderProfileConfig(BaseModel):
    name: str
    protocol: ProtocolName
    base_url: str
    model: str
    api_keys: tuple[str, ...] = Field(default_factory=tuple)
    context_window_tokens: int | None = None
    max_output_tokens: int | None = None
    embedding_url: str | None = None

    @field_validator("base_url")
    @classmethod
    def _strip_base_url(cls, value: str) -> str:
        return value.rstrip("/")

    @field_validator("api_keys", mode="before")
    @classmethod
    def _normalize_api_keys(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            return (value,)
        if isinstance(value, list):
            return tuple(str(item) for item in value if str(item))
        if isinstance(value, tuple):
            return tuple(str(item) for item in value if str(item))
        raise ValueError("api_keys must be a string or list of strings")


class ProviderConfigSection(BaseModel):
    default: str = "Mimo"
    items: list[ProviderProfileConfig] = Field(default_factory=list)


class ContextPolicyConfig(BaseModel):
    auto_compact_enabled: bool = True
    compression_threshold_ratio: float = 0.50
    recent_tail_ratio: float = 0.20
    preserve_recent_agent_steps: int = 20
    preserve_recent_user_turns: int = 3
    token_estimation_padding: float = 4 / 3
    safety_buffer_tokens: int = 13_000
    image_token_estimate: int = 4096
    enabled_providers: tuple[str, ...] = (
        "conversation",
        "task_state_snapshot",
        "failure_summary",
        "runtime_budget_status",
        "memory",
        "skills",
    )


class RuntimeGuardConfig(BaseModel):
    max_tool_iterations: int | None = None
    max_consecutive_tool_errors: int = 5
    max_no_progress_iterations: int = 20
    max_wall_clock_minutes: float | None = None
    runtime_root: Path = Path("/tmp/homemaster/runs")
    debug_root: Path = Path("/tmp/homemaster/debug")
    results_root: Path = Path("/tmp/homemaster/results")


class PromptConfig(BaseModel):
    agent_system_prompt: str = "agent_system_prompt"
    compact_summary_prompt: str = "compact_summary_prompt"


class HomeMasterConfig(BaseModel):
    providers: ProviderConfigSection = Field(default_factory=ProviderConfigSection)
    context: ContextPolicyConfig = Field(default_factory=ContextPolicyConfig)
    runtime: RuntimeGuardConfig = Field(default_factory=RuntimeGuardConfig)
    prompts: PromptConfig = Field(default_factory=PromptConfig)

    def get_provider(self, name: str | None = None) -> ProviderProfileConfig:
        target = (name or self.providers.default).casefold()
        for provider in self.providers.items:
            if provider.name.casefold() == target:
                return provider
        raise RuntimeConfigError(f"provider {name or self.providers.default!r} not found")


def load_model_config(config_path: str | Path | None = None) -> HomeMasterConfig:
    from homemaster.runtime import HOMEMASTER_CONFIG_PATH

    path = Path(config_path) if config_path is not None else HOMEMASTER_CONFIG_PATH
    if not path.is_absolute():
        from homemaster.runtime import REPO_ROOT

        path = REPO_ROOT / path
    if not path.exists():
        return HomeMasterConfig()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise RuntimeConfigError(f"invalid homemaster config JSON: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeConfigError(f"homemaster config must be a JSON object: {path}")
    return HomeMasterConfig.model_validate(payload)
```

- [ ] **Step 4: Extend existing provider config return shape**

Modify `src/homemaster/runtime.py`:

```python
@dataclass(frozen=True)
class ProviderConfig:
    name: str
    base_url: str
    model: str
    api_keys: tuple[str, ...]
    protocol: str
    embedding_url: str | None = None
    context_window_tokens: int | None = None
    max_output_tokens: int | None = None

    def public_summary(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "base_url": self.base_url,
            "model": self.model,
            "protocol": self.protocol,
            "embedding_url": self.embedding_url,
            "api_key_count": len(self.api_keys),
            "context_window_tokens": self.context_window_tokens,
            "max_output_tokens": self.max_output_tokens,
        }
```

In `_provider_from_payload`, add:

```python
context_window_tokens = payload.get("context_window_tokens")
if context_window_tokens is not None and not isinstance(context_window_tokens, int):
    raise RuntimeConfigError("provider.context_window_tokens must be an integer or null")
max_output_tokens = payload.get("max_output_tokens")
if max_output_tokens is not None and not isinstance(max_output_tokens, int):
    raise RuntimeConfigError("provider.max_output_tokens must be an integer or null")
return ProviderConfig(
    name=name,
    base_url=base_url,
    model=model,
    api_keys=api_keys,
    protocol=protocol,
    embedding_url=embedding_url,
    context_window_tokens=context_window_tokens,
    max_output_tokens=max_output_tokens,
)
```

- [ ] **Step 5: Extend RuntimeSettings**

Modify `src/homemaster/config/runtime_settings.py`:

```python
from homemaster.config.model_config import ContextPolicyConfig, PromptConfig, RuntimeGuardConfig


class RuntimeSettings(BaseModel):
    run_id: str
    max_turns: int = 12
    runtime_root: Path
    debug_root: Path
    results_root: Path
    provider_name: str = "Mimo"
    embedding_provider_name: str = "MemoryEmbedding"
    config_path: Path | None = None
    memory_path: Path | None = None
    world_path: Path | None = None
    context: ContextPolicyConfig = Field(default_factory=ContextPolicyConfig)
    runtime_guards: RuntimeGuardConfig = Field(default_factory=RuntimeGuardConfig)
    prompts: PromptConfig = Field(default_factory=PromptConfig)
```

Update tests that construct `RuntimeSettings` if import order requires adding `Field`.

- [ ] **Step 6: Update example config**

Modify `config/homemaster.example.json` to include non-secret example fields:

```json
{
  "providers": {
    "default": "mimo_v25",
    "items": [
      {
        "name": "mimo_v25",
        "protocol": "anthropic",
        "base_url": "https://api.example/v1",
        "model": "MiMo-V2.5",
        "api_keys": ["replace-with-local-key"],
        "context_window_tokens": 1000000,
        "max_output_tokens": 8192
      }
    ]
  },
  "context": {
    "auto_compact_enabled": true,
    "compression_threshold_ratio": 0.5,
    "recent_tail_ratio": 0.2,
    "preserve_recent_agent_steps": 20,
    "preserve_recent_user_turns": 3,
    "token_estimation_padding": 1.333,
    "safety_buffer_tokens": 13000,
    "image_token_estimate": 4096
  },
  "runtime": {
    "max_tool_iterations": null,
    "max_consecutive_tool_errors": 5,
    "max_no_progress_iterations": 20,
    "max_wall_clock_minutes": null
  }
}
```

Merge this with existing retrieval/grounding sections instead of replacing them.

- [ ] **Step 7: Run tests**

Run:

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q tests/homemaster/test_model_config.py tests/homemaster/test_runtime_settings.py tests/homemaster/test_llm_client.py
```

Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add src/homemaster/config/model_config.py src/homemaster/runtime.py src/homemaster/config/runtime_settings.py config/homemaster.example.json tests/homemaster/test_model_config.py tests/homemaster/test_runtime_settings.py tests/homemaster/test_llm_client.py
git commit -m "feat: add typed model and context config"
```

---

## Task 2: System Prompt Transport Path

**Files:**
- Modify: `src/homemaster/providers/transport.py`
- Modify: `src/homemaster/providers/mimo_transport.py`
- Modify: `src/homemaster/prompt_loader.py`
- Test: `tests/homemaster/test_transport_system_prompt.py`
- Test: update fake transport signatures in existing tests

- [ ] **Step 1: Write failing transport tests**

Create `tests/homemaster/test_transport_system_prompt.py`:

```python
from homemaster.agent.messages import ContentBlock, UserMessage
from homemaster.providers.mimo_transport import MimoTransport


def test_anthropic_payload_includes_system_prompt() -> None:
    transport = MimoTransport(
        base_url="https://example.invalid",
        model="m",
        api_key="secret",
        protocol="anthropic",
    )

    payload = transport._build_request_payload(
        [UserMessage(content=[ContentBlock(text="hello")])],
        tools=None,
        system_prompt="You are HomeMaster.",
    )

    assert payload["system"] == "You are HomeMaster."
    assert payload["messages"][0]["role"] == "user"


def test_openai_payload_prepends_system_message() -> None:
    transport = MimoTransport(
        base_url="https://example.invalid",
        model="m",
        api_key="secret",
        protocol="openai",
    )

    payload = transport._build_request_payload(
        [UserMessage(content=[ContentBlock(text="hello")])],
        tools=None,
        system_prompt="You are HomeMaster.",
    )

    assert payload["messages"][0] == {"role": "system", "content": "You are HomeMaster."}
    assert payload["messages"][1]["role"] == "user"
```

- [ ] **Step 2: Run tests and confirm failure**

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q tests/homemaster/test_transport_system_prompt.py
```

Expected: `_build_request_payload()` does not accept `system_prompt`.

- [ ] **Step 3: Update transport interface**

Modify `src/homemaster/providers/transport.py`:

```python
def stream(
    self,
    messages: list[Message],
    tools: list[dict[str, Any]] | None = None,
    *,
    system_prompt: str = "",
    event_sink: Any = None,
    run_id: str = "",
    session_id: str = "",
    turn_index: int | None = None,
    iteration: int | None = None,
) -> Iterator[TransportDelta]:
    ...
```

Also pass `system_prompt` through `complete()`:

```python
deltas = list(self.stream(
    messages,
    tools,
    system_prompt=system_prompt,
    event_sink=event_sink,
    run_id=run_id,
    session_id=session_id,
    turn_index=turn_index,
    iteration=iteration,
))
```

- [ ] **Step 4: Update MimoTransport payload builders**

Modify `src/homemaster/providers/mimo_transport.py` signatures:

```python
def stream(..., system_prompt: str = "", ...) -> Iterator[TransportDelta]:
    payload = self._build_request_payload(messages, tools, system_prompt=system_prompt)

def _build_request_payload(
    self,
    messages: list[Message],
    tools: list[dict[str, Any]] | None = None,
    *,
    system_prompt: str = "",
) -> dict[str, Any]:
    if self._protocol == "anthropic":
        return self._build_anthropic_payload(messages, tools, system_prompt=system_prompt)
    return self._build_openai_payload(messages, tools, system_prompt=system_prompt)
```

In `_build_anthropic_payload`:

```python
if system_prompt.strip():
    payload["system"] = system_prompt.strip()
```

In `_build_openai_payload`:

```python
api_messages: list[dict[str, Any]] = []
if system_prompt.strip():
    api_messages.append({"role": "system", "content": system_prompt.strip()})
```

- [ ] **Step 5: Fix fake transports in tests**

Update fake `stream()` methods in:

- `tests/homemaster/test_generic_agent_runtime.py`
- `tests/homemaster/test_agent_loop_acceptance.py`
- `tests/homemaster/test_agent_turn_cli_adapter.py`
- `tests/homemaster/benchmarking/test_alfworld_runner.py`

Use this compatible signature:

```python
def stream(
    self,
    messages,
    tools=None,
    *,
    system_prompt="",
    event_sink=None,
    run_id="",
    session_id="",
    turn_index=None,
    iteration=None,
):
    self.last_system_prompt = system_prompt
    yield from self._events
```

- [ ] **Step 6: Convert PromptId to StrEnum**

Modify `src/homemaster/prompt_loader.py`:

```python
from enum import StrEnum


class PromptId(StrEnum):
    AGENT_SYSTEM = "agent_system_prompt"
    COMPACT_SUMMARY = "compact_summary_prompt"
    TASK_INTERPRETER = "task_interpreter_prompt"
    MEMORY_QUERY = "memory_query_prompt"
    MEMORY_QUERY_RETRY = "memory_query_retry"
    TASK_SUMMARY = "task_summary_prompt"
```

- [ ] **Step 7: Run tests and ruff**

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q tests/homemaster/test_transport_system_prompt.py tests/homemaster/test_transport_mimo.py tests/homemaster/test_prompt_externalization.py
.venv/bin/python -m ruff check src/homemaster/prompt_loader.py src/homemaster/providers/transport.py src/homemaster/providers/mimo_transport.py
```

Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add src/homemaster/providers/transport.py src/homemaster/providers/mimo_transport.py src/homemaster/prompt_loader.py tests/homemaster/test_transport_system_prompt.py tests/homemaster/test_transport_mimo.py tests/homemaster/test_prompt_externalization.py
git commit -m "feat: send system prompts through model transport"
```

---

## Task 3: AgentState Runtime Bookkeeping

**Files:**
- Modify: `src/homemaster/agent/state.py`
- Modify: `src/homemaster/agent/generic_runtime.py`
- Test: `tests/homemaster/test_agent_state_v15.py`
- Test: update `tests/homemaster/test_agent_state.py`

- [ ] **Step 1: Write failing AgentState tests**

Create `tests/homemaster/test_agent_state_v15.py`:

```python
from homemaster.agent.state import AgentState, CompactionRecord, ProviderUsage


def test_agent_state_tracks_runtime_counters() -> None:
    state = AgentState(run_id="r1", session_id="s1")

    assert state.status == "running"
    assert state.turn_index == 0
    assert state.iteration_index == 0
    assert state.total_model_calls == 0
    assert state.total_tool_calls == 0
    assert state.consecutive_tool_errors == 0
    assert state.no_progress_iterations == 0


def test_agent_state_records_provider_usage_and_compaction() -> None:
    state = AgentState(run_id="r1", session_id="s1")
    state.provider_usage = ProviderUsage(input_tokens=10, output_tokens=3, total_tokens=13)
    state.last_compaction = CompactionRecord(
        kind="micro",
        before_tokens=120_000,
        after_tokens=80_000,
        reason="auto",
    )

    dumped = state.model_dump(mode="json")

    assert dumped["provider_usage"]["input_tokens"] == 10
    assert dumped["last_compaction"]["kind"] == "micro"
```

- [ ] **Step 2: Run tests and confirm failure**

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q tests/homemaster/test_agent_state_v15.py
```

Expected: missing new fields/classes.

- [ ] **Step 3: Replace AgentState model**

Modify `src/homemaster/agent/state.py`:

```python
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


AgentRunStatus = Literal["running", "waiting_user", "replied", "completed", "failed", "cancelled"]
CompactionKind = Literal["none", "micro", "summary", "reactive", "emergency"]


class ProviderUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


class CompactionRecord(BaseModel):
    kind: CompactionKind = "none"
    before_tokens: int = 0
    after_tokens: int = 0
    reason: str = ""


class AgentState(BaseModel):
    run_id: str = ""
    session_id: str = ""
    status: AgentRunStatus = "running"
    turn_index: int = 0
    iteration_index: int = 0
    total_model_calls: int = 0
    total_tool_calls: int = 0
    active_task_snapshot_id: str | None = None
    last_assistant_text: str | None = None
    last_tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    last_tool_results_summary: list[dict[str, Any]] = Field(default_factory=list)
    consecutive_tool_errors: int = 0
    no_progress_iterations: int = 0
    last_progress_marker: str | None = None
    last_compaction: CompactionRecord | None = None
    estimated_context_tokens: int = 0
    provider_usage: ProviderUsage | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def begin_iteration(self, iteration: int) -> None:
        self.iteration_index = iteration
        self.total_model_calls += 1

    def record_tool_results(self, summaries: list[dict[str, Any]]) -> None:
        self.total_tool_calls += len(summaries)
        self.last_tool_results_summary = summaries
        if summaries and all(item.get("is_error") for item in summaries):
            self.consecutive_tool_errors += len(summaries)
        else:
            self.consecutive_tool_errors = 0
```

- [ ] **Step 4: Instantiate AgentState in runtime**

Modify `GenericAgentRuntime.run()` to create or accept state:

```python
agent_state = AgentState(run_id=run_id, session_id=session.session_id)
```

Before each model call:

```python
agent_state.begin_iteration(iteration)
```

After assistant aggregation:

```python
agent_state.last_assistant_text = assistant_msg.text
if assistant_msg.usage:
    input_tokens = int(
        assistant_msg.usage.get("input_tokens")
        or assistant_msg.usage.get("prompt_tokens")
        or 0
    )
    output_tokens = int(
        assistant_msg.usage.get("output_tokens")
        or assistant_msg.usage.get("completion_tokens")
        or 0
    )
    agent_state.provider_usage = ProviderUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
    )
```

After tool dispatch:

```python
agent_state.record_tool_results([
    {
        "tool_call_id": tr.tool_call_id,
        "name": tr.name,
        "is_error": tr.is_error,
        "text": "\\n".join(block.text for block in tr.content if block.text)[:500],
    }
    for tr in tool_results
])
```

- [ ] **Step 5: Run state/runtime tests**

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q tests/homemaster/test_agent_state.py tests/homemaster/test_agent_state_v15.py tests/homemaster/test_generic_agent_runtime.py
```

Expected: all pass after updating old tests to the new fields.

- [ ] **Step 6: Commit**

```bash
git add src/homemaster/agent/state.py src/homemaster/agent/generic_runtime.py tests/homemaster/test_agent_state.py tests/homemaster/test_agent_state_v15.py tests/homemaster/test_generic_agent_runtime.py
git commit -m "feat: add v15 agent runtime state"
```

---

## Task 4: TaskStateStore and Task-State Tools

**Files:**
- Create: `src/homemaster/task_state/__init__.py`
- Create: `src/homemaster/task_state/models.py`
- Create: `src/homemaster/task_state/store.py`
- Create: `src/homemaster/task_state/tools.py`
- Modify: domain or benchmark tool registry to register the new tools
- Test: `tests/homemaster/test_task_state_store.py`
- Test: `tests/homemaster/test_task_state_tools.py`

- [ ] **Step 1: Write store tests**

Create `tests/homemaster/test_task_state_store.py`:

```python
import pytest

from homemaster.task_state.models import TaskProgressUpdate, TaskStatus
from homemaster.task_state.store import TaskStateStore, TaskStateStoreError


def test_create_plan_normalizes_snapshot() -> None:
    store = TaskStateStore(run_id="r1")

    snapshot = store.create_or_replace_plan(
        goal="put a hot apple in fridge",
        subtasks=[
            {"id": "find_apple", "description": "Find an apple."},
            {"id": "heat_apple", "description": "Heat the apple.", "status": "in_progress"},
        ],
        current_subtask="heat_apple",
        next_focus="Use the microwave.",
    )

    assert snapshot.status == TaskStatus.ACTIVE
    assert snapshot.current_subtask == "heat_apple"
    assert snapshot.subtasks[0].status == "pending"
    assert snapshot.subtasks[1].status == "in_progress"


def test_duplicate_subtask_ids_fail() -> None:
    store = TaskStateStore(run_id="r1")

    with pytest.raises(TaskStateStoreError, match="duplicate subtask id"):
        store.create_or_replace_plan(
            goal="goal",
            subtasks=[
                {"id": "a", "description": "A"},
                {"id": "a", "description": "B"},
            ],
        )


def test_progress_update_requires_existing_subtask() -> None:
    store = TaskStateStore(run_id="r1")
    store.create_or_replace_plan(goal="goal", subtasks=[{"id": "a", "description": "A"}])

    with pytest.raises(TaskStateStoreError, match="unknown subtask"):
        store.apply_progress_updates([
            TaskProgressUpdate(subtask_id="missing", status="completed", evidence=["done"])
        ])


def test_completed_snapshot_model_view_is_bounded() -> None:
    store = TaskStateStore(run_id="r1")
    store.create_or_replace_plan(
        goal="goal",
        subtasks=[{"id": "a", "description": "A", "evidence": ["e1", "e2", "e3"]}],
    )
    snapshot = store.mark_completed(final_summary="completed goal")

    visible = snapshot.to_model_visible_dict(max_evidence_per_subtask=2)

    assert visible["status"] == "completed"
    assert visible["completion_summary"] == "completed goal"
    assert visible["subtasks"][0]["evidence"] == ["e2", "e3"]
```

- [ ] **Step 2: Add task-state models**

Create `src/homemaster/task_state/models.py`:

```python
from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class TaskStatus(StrEnum):
    NONE = "none"
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SubtaskStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"
    UNCERTAIN = "uncertain"


class TaskSubtask(BaseModel):
    id: str
    description: str
    status: SubtaskStatus = SubtaskStatus.PENDING
    evidence: list[str] = Field(default_factory=list)


class TaskSnapshot(BaseModel):
    type: str = "task_state_snapshot"
    snapshot_id: str
    status: TaskStatus = TaskStatus.ACTIVE
    goal: str
    current_subtask: str | None = None
    next_focus: str | None = None
    open_questions: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    subtasks: list[TaskSubtask] = Field(default_factory=list)
    updated_at_iteration: int = 0
    completion_summary: str | None = None

    def to_model_visible_dict(self, *, max_evidence_per_subtask: int = 2) -> dict[str, Any]:
        payload = self.model_dump(mode="json")
        payload["subtasks"] = [
            {
                "id": subtask.id,
                "description": subtask.description,
                "status": subtask.status.value,
                "evidence": subtask.evidence[-max_evidence_per_subtask:],
            }
            for subtask in self.subtasks
        ]
        return payload


class TaskProgressUpdate(BaseModel):
    subtask_id: str
    status: SubtaskStatus
    evidence: list[str]
```

- [ ] **Step 3: Add TaskStateStore**

Create `src/homemaster/task_state/store.py`:

```python
from __future__ import annotations

from typing import Any

from homemaster.task_state.models import (
    SubtaskStatus,
    TaskProgressUpdate,
    TaskSnapshot,
    TaskStatus,
    TaskSubtask,
)


class TaskStateStoreError(RuntimeError):
    pass


class TaskStateStore:
    def __init__(self, *, run_id: str) -> None:
        self._run_id = run_id
        self._snapshot_counter = 0
        self._snapshot: TaskSnapshot | None = None

    @property
    def snapshot(self) -> TaskSnapshot | None:
        return self._snapshot

    def create_or_replace_plan(
        self,
        *,
        goal: str,
        subtasks: list[dict[str, Any]],
        current_subtask: str | None = None,
        next_focus: str | None = None,
        open_questions: list[str] | None = None,
        constraints: list[str] | None = None,
        updated_at_iteration: int = 0,
    ) -> TaskSnapshot:
        if not subtasks:
            raise TaskStateStoreError("subtasks must not be empty")
        seen: set[str] = set()
        parsed: list[TaskSubtask] = []
        for item in subtasks:
            subtask = TaskSubtask.model_validate(item)
            if subtask.id in seen:
                raise TaskStateStoreError(f"duplicate subtask id: {subtask.id}")
            seen.add(subtask.id)
            parsed.append(subtask)
        if current_subtask is not None and current_subtask not in seen:
            raise TaskStateStoreError(f"unknown current_subtask: {current_subtask}")
        self._snapshot_counter += 1
        self._snapshot = TaskSnapshot(
            snapshot_id=f"task-state-{self._snapshot_counter:04d}",
            status=TaskStatus.ACTIVE,
            goal=goal,
            current_subtask=current_subtask,
            next_focus=next_focus,
            open_questions=open_questions or [],
            constraints=constraints or ["Only use model-visible observations and tool results."],
            subtasks=parsed,
            updated_at_iteration=updated_at_iteration,
        )
        return self._snapshot

    def apply_progress_updates(
        self,
        updates: list[TaskProgressUpdate],
        *,
        current_subtask: str | None = None,
        next_focus: str | None = None,
        updated_at_iteration: int = 0,
    ) -> TaskSnapshot:
        if self._snapshot is None:
            raise TaskStateStoreError("no_active_task_plan")
        by_id = {subtask.id: subtask for subtask in self._snapshot.subtasks}
        for update in updates:
            if update.subtask_id not in by_id:
                raise TaskStateStoreError(f"unknown subtask: {update.subtask_id}")
            subtask = by_id[update.subtask_id]
            subtask.status = update.status
            subtask.evidence.extend(update.evidence)
        if current_subtask is not None:
            if current_subtask not in by_id:
                raise TaskStateStoreError(f"unknown current_subtask: {current_subtask}")
            self._snapshot.current_subtask = current_subtask
        if next_focus is not None:
            self._snapshot.next_focus = next_focus
        self._snapshot.updated_at_iteration = updated_at_iteration
        return self._snapshot

    def mark_completed(self, *, final_summary: str, updated_at_iteration: int = 0) -> TaskSnapshot:
        if self._snapshot is None:
            raise TaskStateStoreError("no_active_task_plan")
        self._snapshot.status = TaskStatus.COMPLETED
        self._snapshot.completion_summary = final_summary
        self._snapshot.current_subtask = None
        self._snapshot.next_focus = None
        self._snapshot.updated_at_iteration = updated_at_iteration
        return self._snapshot
```

- [ ] **Step 4: Add task-state tool specs**

Create `src/homemaster/task_state/tools.py` with functions returning existing `ToolSpec` objects:

```python
from __future__ import annotations

from homemaster.agent.messages import ContentBlock, ToolResultMessage
from homemaster.agent.normalized import RunContext
from homemaster.task_state.models import TaskProgressUpdate
from homemaster.task_state.store import TaskStateStore, TaskStateStoreError
from homemaster.tools.spec import ToolSpec


def _store(run_context: RunContext) -> TaskStateStore:
    value = run_context.deps.get("task_state_store")
    if not isinstance(value, TaskStateStore):
        raise TaskStateStoreError("no task_state_store in run_context.deps")
    return value


def task_planner_tool() -> ToolSpec:
    def executor(*, arguments: dict, run_context: RunContext) -> ToolResultMessage:
        store = _store(run_context)
        snapshot = store.create_or_replace_plan(
            goal=str(arguments["goal"]),
            subtasks=list(arguments["subtasks"]),
            current_subtask=arguments.get("current_subtask"),
            next_focus=arguments.get("next_focus"),
            open_questions=arguments.get("open_questions") or [],
            constraints=arguments.get("constraints") or [],
            updated_at_iteration=run_context.turn_index,
        )
        return ToolResultMessage(
            tool_call_id="",
            name="task_planner",
            content=[ContentBlock(text=snapshot.model_dump_json(indent=2))],
            data=snapshot.to_model_visible_dict(),
        )

    return ToolSpec(
        name="task_planner",
        description="Create or replace the model-owned task plan snapshot.",
        input_schema={
            "type": "object",
            "properties": {
                "goal": {"type": "string"},
                "subtasks": {"type": "array", "items": {"type": "object"}},
                "current_subtask": {"type": "string"},
                "next_focus": {"type": "string"},
                "open_questions": {"type": "array", "items": {"type": "string"}},
                "constraints": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["goal", "subtasks"],
        },
        executor=executor,
    )
```

Add `task_progress_check_tool()` similarly with `TaskProgressUpdate.model_validate(item)` and `store.apply_progress_updates(...)`.

- [ ] **Step 5: Register tools**

Add the task-state tools to the generic/home registry used by CLI and ALFWorld. If `build_home_tool_registry()` is the shared path, register there:

```python
from homemaster.task_state.tools import task_planner_tool, task_progress_check_tool

registry.register(task_planner_tool())
registry.register(task_progress_check_tool())
```

If ALFWorld uses a separate registry, register the same generic tools in `AlfworldRunner._register_tools()`.

- [ ] **Step 6: Run tests**

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q tests/homemaster/test_task_state_store.py tests/homemaster/test_task_state_tools.py tests/homemaster/test_tool_registry.py
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add src/homemaster/task_state tests/homemaster/test_task_state_store.py tests/homemaster/test_task_state_tools.py src/homemaster/domain/home/tool_registry.py
git commit -m "feat: add model-owned task state tools"
```

---

## Task 5: Context Items, Providers, and Budget Manager

**Files:**
- Create: `src/homemaster/agent/context_items.py`
- Create: `src/homemaster/agent/context_providers.py`
- Create: `src/homemaster/agent/context_budget.py`
- Test: `tests/homemaster/test_context_budget.py`
- Test: `tests/homemaster/test_context_assembler.py`

- [ ] **Step 1: Write budget tests**

Create `tests/homemaster/test_context_budget.py`:

```python
from homemaster.agent.context_budget import (
    BudgetDecision,
    ContextBudget,
    estimate_text_tokens,
)


def test_estimate_text_tokens_handles_ascii_and_cjk() -> None:
    assert estimate_text_tokens("abcd") == 1
    assert estimate_text_tokens("水杯") >= 1


def test_budget_thresholds_scale_with_context_window() -> None:
    budget = ContextBudget(
        context_window_tokens=1_000_000,
        max_output_tokens=8192,
        threshold_ratio=0.5,
        recent_tail_ratio=0.2,
        safety_buffer_tokens=13_000,
    )

    assert budget.compaction_threshold_tokens == 500_000
    assert budget.recent_tail_budget_tokens == 100_000
    assert budget.should_compact(499_999) is BudgetDecision.NO_COMPACT
    assert budget.should_compact(500_000) is BudgetDecision.COMPACT
```

- [ ] **Step 2: Add context item model**

Create `src/homemaster/agent/context_items.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Callable

from homemaster.agent.messages import Message


class ContextPriority(StrEnum):
    REQUIRED = "required"
    IMPORTANT = "important"
    AUXILIARY = "auxiliary"
    TRACE_ONLY = "trace_only"


class ContextFreshness(StrEnum):
    CURRENT = "current"
    RECENT = "recent"
    OLD = "old"
    ARCHIVED = "archived"


class ContextPlacement(StrEnum):
    SYSTEM_PROMPT = "system_prompt"
    CONTEXT_PRELUDE = "context_prelude"
    CONVERSATION = "conversation"
    TOOL_SCHEMA = "tool_schema"
    TRACE_ONLY = "trace_only"


class RenderMode(StrEnum):
    FULL = "full"
    COMPACT = "compact"
    SUMMARY = "summary"
    POINTER = "pointer"


RenderedContext = str | list[Message]


@dataclass(frozen=True)
class ContextItem:
    id: str
    kind: str
    priority: ContextPriority
    freshness: ContextFreshness
    placement: ContextPlacement
    token_estimate: int
    render: Callable[[RenderMode], RenderedContext]
    group_id: str | None = None
    depends_on: tuple[str, ...] = field(default_factory=tuple)
    mode: RenderMode = RenderMode.FULL
```

- [ ] **Step 3: Add budget manager**

Create `src/homemaster/agent/context_budget.py`:

```python
from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum


class BudgetDecision(Enum):
    NO_COMPACT = "no_compact"
    COMPACT = "compact"


def estimate_text_tokens(text: str) -> int:
    if not text:
        return 0
    cjk = sum(1 for char in text if "\u4e00" <= char <= "\u9fff")
    non_cjk = max(0, len(text) - cjk)
    return max(1, math.ceil(cjk / 2) + math.ceil(non_cjk / 4))


def estimate_json_tokens(value: object) -> int:
    import json

    return estimate_text_tokens(json.dumps(value, ensure_ascii=False, sort_keys=True))


@dataclass(frozen=True)
class ContextBudget:
    context_window_tokens: int
    max_output_tokens: int
    threshold_ratio: float = 0.50
    recent_tail_ratio: float = 0.20
    safety_buffer_tokens: int = 13_000
    token_estimation_padding: float = 4 / 3
    image_token_estimate: int = 4096

    @property
    def compaction_threshold_tokens(self) -> int:
        ratio_threshold = int(self.context_window_tokens * self.threshold_ratio)
        hard_cap = self.context_window_tokens - self.max_output_tokens - self.safety_buffer_tokens
        return max(1, min(ratio_threshold, hard_cap))

    @property
    def recent_tail_budget_tokens(self) -> int:
        return max(1, int(self.compaction_threshold_tokens * self.recent_tail_ratio))

    def padded(self, tokens: int) -> int:
        return int(tokens * self.token_estimation_padding)

    def should_compact(self, estimated_input_tokens: int) -> BudgetDecision:
        if estimated_input_tokens >= self.compaction_threshold_tokens:
            return BudgetDecision.COMPACT
        return BudgetDecision.NO_COMPACT
```

- [ ] **Step 4: Add providers**

Create `src/homemaster/agent/context_providers.py`:

```python
from __future__ import annotations

import json
from typing import Protocol

from homemaster.agent.context_budget import estimate_text_tokens
from homemaster.agent.context_items import (
    ContextFreshness,
    ContextItem,
    ContextPlacement,
    ContextPriority,
    RenderMode,
)
from homemaster.agent.messages import ContentBlock, UserMessage
from homemaster.agent.session import AgentSession
from homemaster.agent.state import AgentState
from homemaster.task_state.store import TaskStateStore


class ContextProvider(Protocol):
    name: str

    def collect(self) -> list[ContextItem]:
        ...


def _json_text(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


class TaskStateSnapshotProvider:
    name = "task_state_snapshot"

    def __init__(self, store: TaskStateStore | None) -> None:
        self._store = store

    def collect(self) -> list[ContextItem]:
        snapshot = self._store.snapshot if self._store else None
        if snapshot is None:
            return []
        visible = snapshot.to_model_visible_dict()
        text = "# Task State Snapshot\n" + _json_text(visible)
        priority = (
            ContextPriority.REQUIRED
            if visible.get("status") == "active"
            else ContextPriority.IMPORTANT
        )
        return [
            ContextItem(
                id="task_state_snapshot",
                kind="task_state_snapshot",
                priority=priority,
                freshness=ContextFreshness.CURRENT,
                placement=ContextPlacement.CONTEXT_PRELUDE,
                token_estimate=estimate_text_tokens(text),
                render=lambda _mode, text=text: text,
            )
        ]


class RuntimeBudgetStatusProvider:
    name = "runtime_budget_status"

    def __init__(self, state: AgentState) -> None:
        self._state = state

    def collect(self) -> list[ContextItem]:
        payload = {
            "type": "runtime_budget_status",
            "iteration_index": self._state.iteration_index,
            "max_tool_iterations": None,
            "consecutive_tool_errors": self._state.consecutive_tool_errors,
            "no_progress_iterations": self._state.no_progress_iterations,
            "estimated_context_tokens": self._state.estimated_context_tokens,
            "last_compaction": (
                self._state.last_compaction.kind
                if self._state.last_compaction is not None
                else "none"
            ),
        }
        text = "# Runtime Budget Status\n" + _json_text(payload)
        return [
            ContextItem(
                id="runtime_budget_status",
                kind="runtime_budget_status",
                priority=ContextPriority.IMPORTANT,
                freshness=ContextFreshness.CURRENT,
                placement=ContextPlacement.CONTEXT_PRELUDE,
                token_estimate=estimate_text_tokens(text),
                render=lambda _mode, text=text: text,
            )
        ]
```

Add `FailureSummaryProvider` and `ConversationProvider` in the same file. `ConversationProvider` should return one item containing `session.messages`; its renderer returns the selected message list.

- [ ] **Step 5: Run tests**

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q tests/homemaster/test_context_budget.py tests/homemaster/test_context_assembler.py
```

Expected: budget tests pass; assembler tests may still fail until Task 6.

- [ ] **Step 6: Commit**

```bash
git add src/homemaster/agent/context_items.py src/homemaster/agent/context_providers.py src/homemaster/agent/context_budget.py tests/homemaster/test_context_budget.py tests/homemaster/test_context_assembler.py
git commit -m "feat: add context items and budget policy"
```

---

## Task 6: Context Assembler and Micro Compaction

**Files:**
- Create: `src/homemaster/agent/compact.py`
- Create: `src/homemaster/agent/context_assembler.py`
- Modify: `src/homemaster/agent/session.py`
- Test: `tests/homemaster/test_context_assembler.py`
- Test: `tests/homemaster/test_context_compact.py`

- [ ] **Step 1: Write assembler test**

Add to `tests/homemaster/test_context_assembler.py`:

```python
from homemaster.agent.context_assembler import ContextAssembler
from homemaster.agent.messages import ContentBlock, UserMessage
from homemaster.agent.session import AgentSession
from homemaster.agent.state import AgentState
from homemaster.config.model_config import ContextPolicyConfig, ProviderProfileConfig
from homemaster.task_state.store import TaskStateStore


def test_assembler_injects_snapshot_without_appending_to_session() -> None:
    session = AgentSession(session_id="s1")
    session.append(UserMessage(content=[ContentBlock(text="put a hot apple in fridge")]))
    store = TaskStateStore(run_id="r1")
    store.create_or_replace_plan(
        goal="put a hot apple in fridge",
        subtasks=[{"id": "find_apple", "description": "Find apple"}],
    )
    assembler = ContextAssembler(
        provider=ProviderProfileConfig(
            name="mimo_v25",
            protocol="anthropic",
            base_url="https://mimo.example",
            model="MiMo-V2.5",
            api_keys=["secret"],
            context_window_tokens=1_000_000,
            max_output_tokens=8192,
        ),
        policy=ContextPolicyConfig(),
        system_prompt="You are HomeMaster.",
    )

    context = assembler.prepare(
        session=session,
        agent_state=AgentState(run_id="r1", session_id="s1"),
        task_state_store=store,
        tools=[],
    )

    assert context.system_prompt == "You are HomeMaster."
    assert any("task_state_snapshot" in block.text for message in context.messages for block in message.content)
    assert len(session.messages) == 1
```

- [ ] **Step 2: Add session replacement method**

Modify `src/homemaster/agent/session.py`:

```python
def replace_messages(self, messages: list[Message]) -> None:
    self._messages = list(messages)
```

- [ ] **Step 3: Add micro compact helpers**

Create `src/homemaster/agent/compact.py`:

```python
from __future__ import annotations

from homemaster.agent.messages import AssistantMessage, ContentBlock, Message, ToolResultMessage


TOOL_RESULT_COMPACT_PREFIX = "[tool result compacted]"


def compact_tool_result_text(text: str, *, head_chars: int = 900, tail_chars: int = 500) -> str:
    if len(text) <= head_chars + tail_chars + 200:
        return text
    return (
        f"{TOOL_RESULT_COMPACT_PREFIX} original_chars={len(text)}\n"
        f"{text[:head_chars]}\n...\n{text[-tail_chars:]}"
    )


def microcompact_old_tool_results(
    messages: list[Message],
    *,
    keep_recent_tool_results: int,
) -> tuple[list[Message], int]:
    tool_indexes = [index for index, msg in enumerate(messages) if isinstance(msg, ToolResultMessage)]
    if len(tool_indexes) <= keep_recent_tool_results:
        return list(messages), 0
    keep = set(tool_indexes[-keep_recent_tool_results:])
    compacted: list[Message] = []
    saved = 0
    for index, msg in enumerate(messages):
        if isinstance(msg, ToolResultMessage) and index not in keep:
            text = "\n".join(block.text for block in msg.content if block.text)
            compact_text = compact_tool_result_text(text)
            if compact_text != text:
                saved += max(0, len(text) - len(compact_text)) // 4
            compacted.append(msg.model_copy(update={"content": [ContentBlock(text=compact_text)]}))
        else:
            compacted.append(msg)
    return compacted, saved


def sanitize_tool_pairs(messages: list[Message]) -> list[Message]:
    result_ids = {
        msg.tool_call_id
        for msg in messages
        if isinstance(msg, ToolResultMessage)
    }
    sanitized: list[Message] = []
    for msg in messages:
        if isinstance(msg, AssistantMessage) and msg.tool_calls:
            kept_calls = [tool_call for tool_call in msg.tool_calls if tool_call.id in result_ids]
            if kept_calls or msg.content:
                sanitized.append(msg.model_copy(update={"tool_calls": kept_calls}))
        else:
            sanitized.append(msg)
    return sanitized
```

- [ ] **Step 4: Add ContextAssembler**

Create `src/homemaster/agent/context_assembler.py`:

```python
from __future__ import annotations

from dataclasses import dataclass

from homemaster.agent.context_budget import ContextBudget, estimate_text_tokens
from homemaster.agent.context_providers import (
    RuntimeBudgetStatusProvider,
    TaskStateSnapshotProvider,
)
from homemaster.agent.messages import ContentBlock, Message, UserMessage
from homemaster.agent.session import AgentSession
from homemaster.agent.state import AgentState
from homemaster.config.model_config import ContextPolicyConfig, ProviderProfileConfig
from homemaster.task_state.store import TaskStateStore


@dataclass
class ContextMetrics:
    estimated_tokens: int
    compaction_triggered: bool = False
    compaction_kind: str = "none"


@dataclass
class ComposedContext:
    messages: list[Message]
    system_prompt: str
    tools: list[dict] | None
    metrics: ContextMetrics


class ContextAssembler:
    def __init__(
        self,
        *,
        provider: ProviderProfileConfig,
        policy: ContextPolicyConfig,
        system_prompt: str,
    ) -> None:
        self._provider = provider
        self._policy = policy
        self._system_prompt = system_prompt

    def _budget(self) -> ContextBudget:
        return ContextBudget(
            context_window_tokens=self._provider.context_window_tokens or 200_000,
            max_output_tokens=self._provider.max_output_tokens or 4096,
            threshold_ratio=self._policy.compression_threshold_ratio,
            recent_tail_ratio=self._policy.recent_tail_ratio,
            safety_buffer_tokens=self._policy.safety_buffer_tokens,
            token_estimation_padding=self._policy.token_estimation_padding,
            image_token_estimate=self._policy.image_token_estimate,
        )

    def prepare(
        self,
        *,
        session: AgentSession,
        agent_state: AgentState,
        task_state_store: TaskStateStore | None,
        tools: list[dict] | None,
    ) -> ComposedContext:
        prelude_texts: list[str] = []
        for provider in (
            TaskStateSnapshotProvider(task_state_store),
            RuntimeBudgetStatusProvider(agent_state),
        ):
            for item in provider.collect():
                rendered = item.render(item.mode)
                if isinstance(rendered, str):
                    prelude_texts.append(rendered)

        messages = session.messages
        if prelude_texts:
            messages = [
                UserMessage(
                    content=[
                        ContentBlock(
                            text="# Runtime Context\n"
                            + "\n\n".join(prelude_texts)
                            + "\n\nThis runtime context is not a new user request."
                        )
                    ]
                ),
                *messages,
            ]

        estimated = estimate_text_tokens(self._system_prompt)
        estimated += sum(
            estimate_text_tokens(block.text)
            for message in messages
            for block in message.content
            if block.text
        )
        budget = self._budget()
        padded = budget.padded(estimated)
        agent_state.estimated_context_tokens = padded

        return ComposedContext(
            messages=messages,
            system_prompt=self._system_prompt,
            tools=tools,
            metrics=ContextMetrics(estimated_tokens=padded),
        )
```

- [ ] **Step 5: Run assembler/compact tests**

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q tests/homemaster/test_context_assembler.py tests/homemaster/test_context_compact.py
```

Expected: all pass after adding compact tests for `compact_tool_result_text`.

- [ ] **Step 6: Commit**

```bash
git add src/homemaster/agent/context_assembler.py src/homemaster/agent/compact.py src/homemaster/agent/session.py tests/homemaster/test_context_assembler.py tests/homemaster/test_context_compact.py
git commit -m "feat: assemble model context before transport"
```

---

## Task 7: Runtime Integration and Loop Guards

**Files:**
- Modify: `src/homemaster/agent/generic_runtime.py`
- Modify: `src/homemaster/agent/turn.py`
- Modify: `src/homemaster/benchmarking/alfworld/runner.py`
- Test: `tests/homemaster/test_generic_agent_runtime.py`
- Test: `tests/homemaster/test_agent_turn_cli_adapter.py`
- Test: `tests/homemaster/benchmarking/test_alfworld_runner.py`

- [ ] **Step 1: Add failing runtime context-preparation test**

Add to `tests/homemaster/test_generic_agent_runtime.py`:

```python
def test_runtime_passes_system_prompt_from_context_assembler() -> None:
    transport = FakeTransport([TransportDelta(type="text", text_delta="done", finish_reason="stop")])
    runtime = GenericAgentRuntime(
        transport=transport,
        tool_executor=lambda *_args, **_kwargs: [],
        max_tool_iterations=None,
        system_prompt="You are HomeMaster.",
    )
    session = AgentSession(session_id="s1")

    runtime.run(session, "hello", tools=[])

    assert transport.last_system_prompt == "You are HomeMaster."
```

- [ ] **Step 2: Let max_tool_iterations be optional**

Modify `GenericAgentRuntime.__init__`:

```python
max_tool_iterations: int | None = None,
```

Loop:

```python
iteration = 0
while self._max_tool_iterations is None or iteration < self._max_tool_iterations:
    ...
    iteration += 1
```

Keep the existing `max_tool_iterations_exceeded` failure only when the value is not `None`.

- [ ] **Step 3: Add context assembler to runtime constructor**

```python
def __init__(
    self,
    *,
    transport: LLMTransport,
    tool_executor: Any,
    max_tool_iterations: int | None = None,
    stop_condition: StopCondition | None = None,
    context_assembler: ContextAssembler | None = None,
    system_prompt: str = "",
) -> None:
```

If no assembler is supplied, build a minimal one with provider fallback:

```python
self._context_assembler = context_assembler
self._system_prompt = system_prompt
```

In `run()`, before `transport.stream()`:

```python
tool_schemas = [...]
context_messages = session.messages
context_system_prompt = self._system_prompt
context_tools = tool_schemas if tool_schemas else None
if self._context_assembler is not None:
    task_state_store = None
    run_context = getattr(self._tool_executor, "_run_context", None)
    if run_context is not None:
        task_state_store = run_context.deps.get("task_state_store")
    composed = self._context_assembler.prepare(
        session=session,
        agent_state=agent_state,
        task_state_store=task_state_store,
        tools=context_tools,
    )
    context_messages = composed.messages
    context_system_prompt = composed.system_prompt
    context_tools = composed.tools
```

Call:

```python
deltas = list(self._transport.stream(
    context_messages,
    tools=context_tools,
    system_prompt=context_system_prompt,
    event_sink=event_sink,
    run_id=run_id,
    session_id=session.session_id,
    turn_index=agent_state.turn_index,
    iteration=iteration,
))
```

- [ ] **Step 4: Add loop guards**

After tool results:

```python
if agent_state.consecutive_tool_errors >= settings.runtime_guards.max_consecutive_tool_errors:
    emit("runtime.guard_triggered", payload={"guard": "max_consecutive_tool_errors"})
    return GenericRunResult(
        run_id=run_id,
        status="failed",
        session=session,
        events=events,
        error_code="max_consecutive_tool_errors",
    )
if agent_state.no_progress_iterations >= settings.runtime_guards.max_no_progress_iterations:
    emit("runtime.guard_triggered", payload={"guard": "max_no_progress_iterations"})
    return GenericRunResult(
        run_id=run_id,
        status="failed",
        session=session,
        events=events,
        error_code="max_no_progress_iterations",
    )
```

Guard access must tolerate `settings is None`.

- [ ] **Step 5: Update CLI adapter**

In `src/homemaster/agent/turn.py`, load prompt:

```python
from homemaster.prompt_loader import PromptId, load_prompt

system_prompt = load_prompt(PromptId.AGENT_SYSTEM)
```

Build `ContextAssembler` from resolved provider/profile and settings. Use `max_tool_iterations=run_context.settings.runtime_guards.max_tool_iterations`, not hard-coded 12.

- [ ] **Step 6: Update ALFWorld runner**

Create `TaskStateStore` per episode:

```python
from homemaster.task_state.store import TaskStateStore

task_state_store = TaskStateStore(run_id=episode_run_id)
deps={
    "alfworld_env": adapter,
    "alfworld_translator": translator,
    "alfworld_trace": trace,
    "alfworld_config": self.config,
    "task_state_store": task_state_store,
}
```

Pass a `ContextAssembler` to `GenericAgentRuntime`.

- [ ] **Step 7: Run integration tests**

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q tests/homemaster/test_generic_agent_runtime.py tests/homemaster/test_agent_turn_cli_adapter.py tests/homemaster/benchmarking/test_alfworld_runner.py
```

Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add src/homemaster/agent/generic_runtime.py src/homemaster/agent/turn.py src/homemaster/benchmarking/alfworld/runner.py tests/homemaster/test_generic_agent_runtime.py tests/homemaster/test_agent_turn_cli_adapter.py tests/homemaster/benchmarking/test_alfworld_runner.py
git commit -m "feat: prepare context before every model call"
```

---

## Task 8: Full Compaction and Reactive Retry

**Files:**
- Modify: `src/homemaster/agent/compact.py`
- Modify: `src/homemaster/agent/context_assembler.py`
- Modify: `src/homemaster/agent/generic_runtime.py`
- Test: `tests/homemaster/test_context_compact.py`
- Test: `tests/homemaster/test_generic_agent_runtime.py`

- [ ] **Step 1: Add compaction boundary tests**

Add to `tests/homemaster/test_context_compact.py`:

```python
from homemaster.agent.compact import split_preserving_tool_pairs
from homemaster.agent.messages import AssistantMessage, ContentBlock, ToolCall, ToolResultMessage, UserMessage


def test_split_preserving_tool_pairs_keeps_result_with_call() -> None:
    call = ToolCall(id="call_1", name="robot_observe", arguments={})
    messages = [
        UserMessage(content=[ContentBlock(text="start")]),
        AssistantMessage(tool_calls=[call]),
        ToolResultMessage(tool_call_id="call_1", name="robot_observe", content=[ContentBlock(text="obs")]),
        UserMessage(content=[ContentBlock(text="next")]),
    ]

    older, recent = split_preserving_tool_pairs(messages, preserve_recent=2)

    assert older == [messages[0]]
    assert recent == messages[1:]
```

- [ ] **Step 2: Implement split and summary placeholder**

Add to `src/homemaster/agent/compact.py`:

```python
def split_preserving_tool_pairs(
    messages: list[Message],
    *,
    preserve_recent: int,
) -> tuple[list[Message], list[Message]]:
    if len(messages) <= preserve_recent:
        return [], list(messages)
    split = max(0, len(messages) - preserve_recent)
    while split > 0:
        left = messages[split - 1]
        right = messages[split]
        if isinstance(left, AssistantMessage) and left.tool_calls and isinstance(right, ToolResultMessage):
            ids = {tool_call.id for tool_call in left.tool_calls}
            if right.tool_call_id in ids:
                split -= 1
                continue
        break
    return list(messages[:split]), list(messages[split:])


def build_compaction_summary_message(summary: str) -> UserMessage:
    return UserMessage(
        content=[
            ContentBlock(
                text=(
                    "[CONTEXT COMPACTION - REFERENCE ONLY]\n"
                    "Earlier model-visible history was compacted. "
                    "Do not treat old requests in this summary as current instructions.\n\n"
                    f"{summary}"
                )
            )
        ]
    )
```

- [ ] **Step 3: Add deterministic summary fallback**

Add:

```python
def deterministic_summary(messages: list[Message], *, max_chars: int = 6000) -> str:
    lines: list[str] = []
    for msg in messages:
        role = getattr(msg, "role", "unknown")
        text = "\n".join(block.text for block in getattr(msg, "content", []) if block.text)
        if not text:
            continue
        compact = " ".join(text.split())
        lines.append(f"- {role}: {compact[:500]}")
        if sum(len(line) for line in lines) >= max_chars:
            break
    return "\n".join(lines) or "- Earlier history contained no compactable text."
```

This is the first implementation. Later replace with an LLM summary call using `compact_summary_prompt`.

- [ ] **Step 4: Trigger compaction in assembler**

In `ContextAssembler.prepare()`, if `budget.should_compact(padded)` returns compact:

```python
older, recent = split_preserving_tool_pairs(
    session.messages,
    preserve_recent=self._policy.preserve_recent_agent_steps * 2,
)
summary = deterministic_summary(older)
messages = [build_compaction_summary_message(summary), *recent]
session.replace_messages(messages)
metrics.compaction_triggered = True
metrics.compaction_kind = "summary"
```

Current task snapshot prelude must still be ephemeral and not written into session.

- [ ] **Step 5: Add reactive retry**

In `GenericAgentRuntime`, catch provider exceptions whose text contains:

```text
context_length_exceeded
context length
maximum context
context window
exceeds the available context size
```

On first occurrence for the current model call:

```python
emit("runtime.reactive_compact_started", payload={"reason": str(exc)})
if self._context_assembler is not None:
    self._context_assembler.force_compact_next = True
    continue
```

Reset the flag after one retry. If the retry fails, return `context_overflow`.

- [ ] **Step 6: Run tests**

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q tests/homemaster/test_context_compact.py tests/homemaster/test_context_assembler.py tests/homemaster/test_generic_agent_runtime.py
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add src/homemaster/agent/compact.py src/homemaster/agent/context_assembler.py src/homemaster/agent/generic_runtime.py tests/homemaster/test_context_compact.py tests/homemaster/test_context_assembler.py tests/homemaster/test_generic_agent_runtime.py
git commit -m "feat: compact context before overflow"
```

---

## Task 9: Remove Old Context Builder and Dead Runtime Paths

**Files:**
- Delete: `src/homemaster/agent/context_builder.py`
- Delete or rewrite: `tests/homemaster/test_mimo_decision_with_context.py`
- Evaluate/delete: `src/homemaster/tools/state_updater.py`
- Evaluate/delete: duplicate old `src/homemaster/tools/builtin.py`, `src/homemaster/tools/skill_tools.py`
- Modify: imports/tests affected by deletion

- [ ] **Step 1: Find active imports**

Run:

```bash
rg -n "ContextBuilder|agent.context_builder|StateUpdater|tools.state_updater|tools.builtin|skill_tools" src tests
```

Expected before cleanup: old tests and possibly dead modules appear.

- [ ] **Step 2: Delete ContextBuilder**

Remove:

```bash
git rm src/homemaster/agent/context_builder.py tests/homemaster/test_mimo_decision_with_context.py
```

If the test file contains any still-relevant assertions, move them into `tests/homemaster/test_context_assembler.py`.

- [ ] **Step 3: Delete unused state updater if no active imports**

If `rg` shows only tests or no imports:

```bash
git rm src/homemaster/tools/state_updater.py tests/homemaster/test_state_updater.py
```

If no test file exists, only delete the module.

- [ ] **Step 4: Remove duplicate tool modules only after import audit**

Run:

```bash
rg -n "from homemaster.tools.builtin|import homemaster.tools.builtin|from homemaster.tools.skill_tools|import homemaster.tools.skill_tools" src tests
```

If no active imports remain:

```bash
git rm src/homemaster/tools/builtin.py src/homemaster/tools/skill_tools.py
```

If imports remain in old tests only, delete or migrate those tests to the active domain registry.

- [ ] **Step 5: Run full non-live tests**

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q -m "not live_api and not live_alfworld"
.venv/bin/python -m ruff check .
```

Expected: tests pass and ruff has no UP042 after PromptId conversion.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "chore: remove old context builder runtime path"
```

---

## Task 10: End-to-End Verification and Docs

**Files:**
- Modify: `plan/V1.5/task-state-snapshot-spec.md` if implementation details changed.
- Create or update: `plan/V1.5/test-results/context-task-state-compact.md`

- [ ] **Step 1: Run focused unit tests**

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q \
  tests/homemaster/test_model_config.py \
  tests/homemaster/test_transport_system_prompt.py \
  tests/homemaster/test_agent_state_v15.py \
  tests/homemaster/test_task_state_store.py \
  tests/homemaster/test_task_state_tools.py \
  tests/homemaster/test_context_budget.py \
  tests/homemaster/test_context_assembler.py \
  tests/homemaster/test_context_compact.py
```

Expected: all pass.

- [ ] **Step 2: Run runtime and ALFWorld non-live tests**

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q \
  tests/homemaster/test_generic_agent_runtime.py \
  tests/homemaster/test_agent_loop_acceptance.py \
  tests/homemaster/test_agent_turn_cli_adapter.py \
  tests/homemaster/benchmarking/test_alfworld_runner.py
```

Expected: all pass.

- [ ] **Step 3: Run full non-live suite**

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q -m "not live_api and not live_alfworld"
```

Expected: all pass.

- [ ] **Step 4: Run lint**

```bash
.venv/bin/python -m ruff check .
```

Expected: pass.

- [ ] **Step 5: Write test result summary**

Create `plan/V1.5/test-results/context-task-state-compact.md`:

```markdown
# V1.5 Context, Task State, and Compaction Test Results

Date: 2026-06-13

## Commands

- `PYTHONPATH=src .venv/bin/python -m pytest -q -m "not live_api and not live_alfworld"`
- `.venv/bin/python -m ruff check .`

## Result

- pytest: passed
- ruff: passed

## Notes

- System prompt is sent through provider payloads.
- Task snapshots are injected ephemerally and are not appended to `AgentSession`.
- Context compaction preserves tool-call/tool-result pairs.
- `max_tool_iterations=null` is supported with error/no-progress guards.
```

- [ ] **Step 6: Commit**

```bash
git add plan/V1.5/test-results/context-task-state-compact.md
git commit -m "test: record v15 context implementation results"
```

---

## Self-Review Checklist

- [ ] System prompt delivery is covered by Tasks 2 and 7.
- [ ] MiMo-V2.5 context window is model-profile config, not hard-coded, covered by Task 1.
- [ ] `max_tool_iterations=null` plus safety guards is covered by Tasks 1 and 7.
- [ ] Task snapshots are active `required`, completed `important`, covered by Task 4 and Task 5.
- [ ] FailureSummary and RuntimeBudgetStatus are derived from AgentState, covered by Tasks 3 and 5.
- [ ] Compact runs before every model call, covered by Tasks 6 and 7.
- [ ] Tool-call/tool-result pair preservation is covered by Task 8.
- [ ] Old `ContextBuilder` and stale runtime leftovers are removed in Task 9.
- [ ] Prompt files are already created and covered by Task 2 verification.
