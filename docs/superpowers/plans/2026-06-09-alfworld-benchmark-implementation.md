# ALFWorld Benchmark Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a HomeMaster benchmark path that evaluates `GenericAgentRuntime` with ALFWorld-backed tools on `AlfredTWEnv`, with memory disabled by default and trace output suitable for benchmark analysis.

**Architecture:** Add an isolated `homemaster.benchmarking.alfworld` package containing config/types, command translation, environment adapter, benchmark tools, registry, prompt builder, tracing, and runner. The runner injects an `AlfworldEnvAdapter` through `RunContext.deps` and calls the existing `GenericAgentRuntime`; ALFWorld never becomes generic runtime state. A small generic runtime hook stops a run after tool results when benchmark conditions such as `env.won` or invalid-action limit are reached.

**Tech Stack:** Python 3.11, HomeMaster `GenericAgentRuntime`, Typer CLI, Pydantic/dataclasses, pytest, ALFWorld `AlfredTWEnv`, TextWorld batch env API, Mimo Anthropic-compatible transport.

---

## Source Spec

Use this spec as the source of truth for benchmark behavior:

- `docs/superpowers/specs/2026-06-09-alfworld-benchmark-design.md`

Important decisions from the spec:

- The benchmark evaluates HomeMaster harness behavior, not a direct Mimo-to-ALFWorld action loop.
- First version uses `AlfredTWEnv`.
- Visual fields stay in the state schema as `frame_path=None`.
- The model must use HomeMaster tools.
- Memory mode defaults to `disabled`; do not register memory tools in that mode.
- Do not show `admissible_commands` to the model, even after failures.
- Success requires `env_state.won is True`.
- Invalid actions reaching `max_invalid_actions`, default `100`, fail the episode.

## File Structure

Create:

- `src/homemaster/benchmarking/__init__.py`
  - Package marker for benchmark-specific code.
- `src/homemaster/benchmarking/alfworld/__init__.py`
  - Public exports for the ALFWorld benchmark package.
- `src/homemaster/benchmarking/alfworld/types.py`
  - Benchmark config, env state, step result, episode result, and summary dataclasses.
- `src/homemaster/benchmarking/alfworld/translator.py`
  - TextWorld command translator and future THOR translator boundary.
- `src/homemaster/benchmarking/alfworld/env_adapter.py`
  - ALFWorld config loading and single-episode adapter over `AlfredTWEnv.init_env(batch_size=1)`.
- `src/homemaster/benchmarking/alfworld/tools.py`
  - ALFWorld-backed `robot_observe`, `robot_navigate`, `robot_manipulate`, and `robot_verify`.
- `src/homemaster/benchmarking/alfworld/registry.py`
  - Benchmark registry builder with memory-mode filtering.
- `src/homemaster/benchmarking/alfworld/prompt.py`
  - Episode prompt composition from translator schema and current env state.
- `src/homemaster/benchmarking/alfworld/tracing.py`
  - JSONL trace writer and summary writer with credential-key redaction.
- `src/homemaster/benchmarking/alfworld/runner.py`
  - Benchmark runner that owns episode orchestration and calls `GenericAgentRuntime`.
- `src/homemaster/cli/benchmark_alfworld.py`
  - CLI handler separated from Typer app wiring.
- `tests/homemaster/benchmarking/test_alfworld_types.py`
- `tests/homemaster/benchmarking/test_alfworld_translator.py`
- `tests/homemaster/benchmarking/test_alfworld_env_adapter.py`
- `tests/homemaster/benchmarking/test_alfworld_tools.py`
- `tests/homemaster/benchmarking/test_alfworld_registry.py`
- `tests/homemaster/benchmarking/test_alfworld_prompt.py`
- `tests/homemaster/benchmarking/test_alfworld_tracing.py`
- `tests/homemaster/benchmarking/test_alfworld_runner.py`
- `tests/homemaster/test_cli_benchmark_alfworld.py`
- `tests/homemaster/test_alfworld_live_smoke.py`

Modify:

- `pyproject.toml`
  - Add optional `alfworld` dependency group for `pyyaml`.
  - Add pytest marker `live_alfworld`.
- `src/homemaster/tools/dispatcher.py`
  - Preserve `ToolResult.data` in `ToolResultMessage.data`.
  - Include failure data in model-visible tool result JSON.
- `tests/homemaster/test_tool_dispatcher.py`
  - Cover failure data preservation.
- `src/homemaster/agent/generic_runtime.py`
  - Add a generic optional stop-after-tools hook.
- `tests/homemaster/test_generic_agent_runtime.py`
  - Cover stop hook behavior.
- `src/homemaster/cli/app.py`
  - Add `benchmark-alfworld` Typer command.

Do not modify:

- Existing home-domain simulated robot tools.
- Generic runtime with ALFWorld-specific imports or ALFWorld-specific state.
- Memory implementation internals.

## Task 1: Preserve Tool Result Data and Add Generic Stop Hook

**Files:**

- Modify: `src/homemaster/tools/dispatcher.py`
- Modify: `tests/homemaster/test_tool_dispatcher.py`
- Modify: `src/homemaster/agent/generic_runtime.py`
- Modify: `tests/homemaster/test_generic_agent_runtime.py`

- [ ] **Step 1: Add dispatcher test for failed ToolResult data preservation**

Append this test to `tests/homemaster/test_tool_dispatcher.py`:

```python
def test_dispatch_failure_preserves_data_for_model_context() -> None:
    def executor(*, arguments: dict[str, Any], run_context: RunContext) -> ToolResult:
        return ToolResult(
            success=False,
            tool_name="robot_navigate",
            executor_mode="programmatic",
            failure_reason="invalid_action",
            data={
                "attempted_command": "go to fridge 1",
                "feedback": "Nothing happens.",
                "observation": "You are in the kitchen.",
                "done": False,
                "won": False,
                "invalid_action_count": 1,
            },
            retryable=True,
        )

    spec = ToolSpec(
        name="robot_navigate",
        description="Navigate",
        input_schema={"type": "object"},
        executor_mode="programmatic",
        executor=executor,
    )
    dispatcher = ToolDispatcher()
    dispatcher.register(spec)

    result = dispatcher.dispatch(
        tool_calls=[ToolCall(id="call_1", name="robot_navigate", arguments={})],
        run_context=_make_run_context(),
    )

    message = result[0]
    assert message.is_error is True
    assert message.data is not None
    assert message.data["failure_reason"] == "invalid_action"
    assert message.data["observation"] == "You are in the kitchen."
    assert "Nothing happens." in message.content[0].text
    assert "admissible_commands" not in message.content[0].text
```

- [ ] **Step 2: Run the dispatcher test and verify it fails**

Run:

```bash
cd /hpc2hdd/home/wyuan140/weilin_workspace/Homemaster
. .venv/bin/activate
pytest tests/homemaster/test_tool_dispatcher.py::test_dispatch_failure_preserves_data_for_model_context -q
```

Expected: FAIL because `ToolDispatcher` currently drops `ToolResult.data` and emits only `{"error": ...}` for failed tools.

- [ ] **Step 3: Update dispatcher ToolResult conversion**

In `src/homemaster/tools/dispatcher.py`, replace the `if isinstance(tool_result, ToolResult):` block with this version:

```python
            if isinstance(tool_result, ToolResult):
                if tool_result.success:
                    payload = dict(tool_result.data) if tool_result.data else {"success": True}
                    payload.setdefault("success", True)
                else:
                    payload = dict(tool_result.data) if tool_result.data else {}
                    payload.setdefault("success", False)
                    payload["failure_reason"] = tool_result.failure_reason or "unknown error"
                    payload["error"] = tool_result.failure_reason or "unknown error"
                    payload["retryable"] = tool_result.retryable

                content_text = json.dumps(payload, ensure_ascii=False)
                results.append(ToolResultMessage(
                    tool_call_id=tc.id,
                    name=tc.name,
                    content=[ContentBlock(text=content_text)],
                    is_error=not tool_result.success,
                    data=payload,
                ))
```

- [ ] **Step 4: Run dispatcher tests**

Run:

```bash
cd /hpc2hdd/home/wyuan140/weilin_workspace/Homemaster
. .venv/bin/activate
pytest tests/homemaster/test_tool_dispatcher.py -q
```

Expected: PASS.

- [ ] **Step 5: Add runtime stop-hook test**

Append this test to `tests/homemaster/test_generic_agent_runtime.py`:

```python
def test_stop_condition_can_end_run_after_tool_results() -> None:
    transport = FakeTransport()
    transport.queue_tool_call("robot_verify", {}, call_id="call_1")
    transport.queue_text("This response must not be requested.")

    def stop_condition(session: AgentSession, tool_results: list[ToolResultMessage]):
        assert session.messages[-1].role == "tool"
        assert tool_results[0].name == "robot_verify"
        from homemaster.agent.generic_runtime import RuntimeStopDecision

        return RuntimeStopDecision(
            status="failed",
            error_code="benchmark_invalid_action_limit",
            final_reply="",
            payload={"reason": "invalid action limit reached"},
        )

    runtime = GenericAgentRuntime(
        transport=transport,
        tool_executor=FakeToolExecutor(),
        max_tool_iterations=12,
        stop_condition=stop_condition,
    )
    session = AgentSession(session_id="test-stop")
    result = runtime.run(session, "run benchmark")

    assert result.status == "failed"
    assert result.error_code == "benchmark_invalid_action_limit"
    assert transport.call_count == 1
```

- [ ] **Step 6: Run the stop-hook test and verify it fails**

Run:

```bash
cd /hpc2hdd/home/wyuan140/weilin_workspace/Homemaster
. .venv/bin/activate
pytest tests/homemaster/test_generic_agent_runtime.py::test_stop_condition_can_end_run_after_tool_results -q
```

Expected: FAIL because `GenericAgentRuntime.__init__` does not accept `stop_condition`.

- [ ] **Step 7: Add generic stop-hook types and constructor argument**

In `src/homemaster/agent/generic_runtime.py`, update imports:

```python
from collections.abc import Callable
```

Add after `GenericRunResult`:

```python
@dataclass(frozen=True)
class RuntimeStopDecision:
    """Optional generic decision to stop a run after tool results are appended."""

    status: str
    final_reply: str = ""
    error_code: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


StopCondition = Callable[
    [AgentSession, list[ToolResultMessage]],
    RuntimeStopDecision | None,
]
```

Update `GenericAgentRuntime.__init__`:

```python
    def __init__(
        self,
        *,
        transport: LLMTransport,
        tool_executor: Any,  # ToolDispatcher or callable
        max_tool_iterations: int = 12,
        stop_condition: StopCondition | None = None,
    ) -> None:
        self._transport = transport
        self._tool_executor = tool_executor
        self._max_tool_iterations = max_tool_iterations
        self._stop_condition = stop_condition
```

- [ ] **Step 8: Call the stop hook after tool results are appended and events emitted**

In `src/homemaster/agent/generic_runtime.py`, after this existing block:

```python
            for tr in tool_results:
                emit(
                    "tool.call_failed" if tr.is_error else "tool.call_completed",
                    tool_call_id=tr.tool_call_id,
                    name=tr.name,
                    payload={"is_error": tr.is_error},
                    duration_ms=dispatch_ms,
                )
```

Add:

```python
            if self._stop_condition is not None:
                decision = self._stop_condition(session, tool_results)
                if decision is not None:
                    event_type = (
                        "runtime.turn_completed"
                        if decision.status == "replied"
                        else "runtime.turn_failed"
                    )
                    emit(event_type, payload={
                        "error_code": decision.error_code,
                        **decision.payload,
                    })
                    return GenericRunResult(
                        run_id=run_id,
                        status=decision.status,
                        session=session,
                        events=events,
                        final_reply=decision.final_reply,
                        error_code=decision.error_code,
                    )
```

- [ ] **Step 9: Run runtime tests**

Run:

```bash
cd /hpc2hdd/home/wyuan140/weilin_workspace/Homemaster
. .venv/bin/activate
pytest tests/homemaster/test_generic_agent_runtime.py tests/homemaster/test_tool_dispatcher.py -q
```

Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add src/homemaster/agent/generic_runtime.py \
  src/homemaster/tools/dispatcher.py \
  tests/homemaster/test_generic_agent_runtime.py \
  tests/homemaster/test_tool_dispatcher.py
git commit -m "feat: support benchmark stop conditions"
```

## Task 2: Add Benchmark Package, Config, and Result Types

**Files:**

- Create: `src/homemaster/benchmarking/__init__.py`
- Create: `src/homemaster/benchmarking/alfworld/__init__.py`
- Create: `src/homemaster/benchmarking/alfworld/types.py`
- Create: `tests/homemaster/benchmarking/test_alfworld_types.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Add optional dependency and live marker**

Modify `pyproject.toml`:

```toml
[project.optional-dependencies]
dev = [
  "pytest>=8.2",
  "ruff>=0.6",
]
alfworld = [
  "pyyaml>=6.0",
]
```

Add the marker:

```toml
markers = [
  "live_api: runs tests that call real external APIs",
  "live_alfworld: runs tests that require an installed ALFWorld dataset/environment",
]
```

- [ ] **Step 2: Create package markers**

Create `src/homemaster/benchmarking/__init__.py`:

```python
"""Benchmark integrations for HomeMaster."""
```

Create `src/homemaster/benchmarking/alfworld/__init__.py`:

```python
"""ALFWorld benchmark integration for HomeMaster."""

from homemaster.benchmarking.alfworld.types import (
    AlfworldBenchmarkConfig,
    AlfworldEnvState,
    AlfworldEpisodeResult,
    AlfworldStepResult,
    AlfworldSummary,
)

__all__ = [
    "AlfworldBenchmarkConfig",
    "AlfworldEnvState",
    "AlfworldEpisodeResult",
    "AlfworldStepResult",
    "AlfworldSummary",
]
```

- [ ] **Step 3: Write failing tests for config and state visibility**

Create `tests/homemaster/benchmarking/test_alfworld_types.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from homemaster.benchmarking.alfworld.types import (
    AlfworldBenchmarkConfig,
    AlfworldEnvState,
)


def test_config_defaults_keep_memory_disabled_and_invalid_limit_100(tmp_path: Path) -> None:
    config = AlfworldBenchmarkConfig(
        alfworld_root=tmp_path / "alfworld",
        alfworld_config=tmp_path / "base_config.yaml",
        trace_root=tmp_path / "traces",
    )

    assert config.env_type == "AlfredTWEnv"
    assert config.split == "valid_seen"
    assert config.memory_mode == "disabled"
    assert config.max_invalid_actions == 100
    assert config.max_tool_iterations == 150


def test_config_rejects_non_positive_episode_count(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="episodes"):
        AlfworldBenchmarkConfig(
            alfworld_root=tmp_path,
            alfworld_config=tmp_path / "base_config.yaml",
            trace_root=tmp_path / "traces",
            episodes=0,
        )


def test_env_state_model_visible_dict_omits_admissible_commands() -> None:
    state = AlfworldEnvState(
        episode_id="game-1",
        task="put apple on table",
        observation="You are in the kitchen.",
        inventory=None,
        last_command="go to fridge 1",
        last_feedback="Nothing happens.",
        reward=0.0,
        done=False,
        won=False,
        goal_condition_success_rate=0.0,
        frame_path=None,
        step_index=3,
        invalid_action_count=2,
        admissible_commands=("look", "inventory"),
    )

    visible = state.to_model_visible_dict()
    debug = state.to_debug_dict()

    assert "admissible_commands" not in visible
    assert visible["frame_path"] is None
    assert visible["invalid_action_count"] == 2
    assert debug["admissible_commands"] == ["look", "inventory"]
```

- [ ] **Step 4: Run tests and verify they fail**

Run:

```bash
cd /hpc2hdd/home/wyuan140/weilin_workspace/Homemaster
. .venv/bin/activate
pytest tests/homemaster/benchmarking/test_alfworld_types.py -q
```

Expected: FAIL because the package and types do not exist.

- [ ] **Step 5: Implement types**

Create `src/homemaster/benchmarking/alfworld/types.py`:

```python
"""Shared types for the ALFWorld benchmark integration."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal


MemoryMode = Literal["disabled", "readonly", "full"]
EnvType = Literal["AlfredTWEnv", "AlfredThorEnv"]
SplitName = Literal["train", "valid_seen", "valid_unseen"]


@dataclass(frozen=True)
class AlfworldBenchmarkConfig:
    alfworld_root: Path
    alfworld_config: Path
    trace_root: Path
    env_type: EnvType = "AlfredTWEnv"
    split: SplitName = "valid_seen"
    episodes: int = 1
    memory_mode: MemoryMode = "disabled"
    max_invalid_actions: int = 100
    max_tool_iterations: int = 150
    provider_config: Path | None = None
    provider_name: str = "Mimo"
    run_id: str | None = None
    debug_admissible_commands: bool = True
    seed: int = 42

    def __post_init__(self) -> None:
        if self.episodes <= 0:
            raise ValueError("episodes must be > 0")
        if self.max_invalid_actions <= 0:
            raise ValueError("max_invalid_actions must be > 0")
        if self.max_tool_iterations <= 0:
            raise ValueError("max_tool_iterations must be > 0")
        if self.memory_mode not in {"disabled", "readonly", "full"}:
            raise ValueError(f"unsupported memory_mode: {self.memory_mode}")
        if self.env_type not in {"AlfredTWEnv", "AlfredThorEnv"}:
            raise ValueError(f"unsupported env_type: {self.env_type}")
        if self.split not in {"train", "valid_seen", "valid_unseen"}:
            raise ValueError(f"unsupported split: {self.split}")


@dataclass(frozen=True)
class AlfworldEnvState:
    episode_id: str
    task: str
    observation: str
    inventory: str | None
    last_command: str | None
    last_feedback: str | None
    reward: float
    done: bool
    won: bool
    goal_condition_success_rate: float
    frame_path: str | None
    step_index: int
    invalid_action_count: int
    admissible_commands: tuple[str, ...] = field(default_factory=tuple)

    def to_model_visible_dict(self) -> dict[str, Any]:
        return {
            "episode_id": self.episode_id,
            "task": self.task,
            "observation": self.observation,
            "inventory": self.inventory,
            "last_command": self.last_command,
            "last_feedback": self.last_feedback,
            "reward": self.reward,
            "done": self.done,
            "won": self.won,
            "goal_condition_success_rate": self.goal_condition_success_rate,
            "frame_path": self.frame_path,
            "step_index": self.step_index,
            "invalid_action_count": self.invalid_action_count,
        }

    def to_debug_dict(self) -> dict[str, Any]:
        payload = self.to_model_visible_dict()
        payload["admissible_commands"] = list(self.admissible_commands)
        return payload


@dataclass(frozen=True)
class AlfworldStepResult:
    tool_name: str
    tool_args: dict[str, Any]
    translated_command: str | None
    success: bool
    failure_reason: str | None
    state: AlfworldEnvState
    feedback: str | None = None

    def to_model_visible_data(self) -> dict[str, Any]:
        data = self.state.to_model_visible_dict()
        data.update({
            "tool_name": self.tool_name,
            "tool_args": self.tool_args,
            "translated_command": self.translated_command,
            "feedback": self.feedback,
        })
        if self.failure_reason is not None:
            data["failure_reason"] = self.failure_reason
        return data

    def to_trace_event(self) -> dict[str, Any]:
        data = self.to_model_visible_data()
        data["tool_success"] = self.success
        return data


@dataclass(frozen=True)
class AlfworldEpisodeResult:
    episode_id: str
    success: bool
    failure_reason: str | None
    steps: int
    invalid_actions: int
    goal_condition_success_rate: float
    runtime_status: str
    run_id: str
    trace_path: Path


@dataclass(frozen=True)
class AlfworldSummary:
    run_id: str
    episodes: list[AlfworldEpisodeResult]

    @property
    def success_rate(self) -> float:
        if not self.episodes:
            return 0.0
        return sum(1 for episode in self.episodes if episode.success) / len(self.episodes)

    def to_dict(self) -> dict[str, Any]:
        total = len(self.episodes)
        return {
            "run_id": self.run_id,
            "episode_count": total,
            "success_rate": self.success_rate,
            "average_goal_condition_success_rate": (
                sum(e.goal_condition_success_rate for e in self.episodes) / total
                if total else 0.0
            ),
            "average_steps": (
                sum(e.steps for e in self.episodes) / total
                if total else 0.0
            ),
            "total_invalid_actions": sum(e.invalid_actions for e in self.episodes),
            "episodes": [
                {
                    "episode_id": e.episode_id,
                    "success": e.success,
                    "failure_reason": e.failure_reason,
                    "steps": e.steps,
                    "invalid_actions": e.invalid_actions,
                    "goal_condition_success_rate": e.goal_condition_success_rate,
                    "runtime_status": e.runtime_status,
                    "run_id": e.run_id,
                    "trace_path": str(e.trace_path),
                }
                for e in self.episodes
            ],
        }
```

- [ ] **Step 6: Run tests**

Run:

```bash
cd /hpc2hdd/home/wyuan140/weilin_workspace/Homemaster
. .venv/bin/activate
pytest tests/homemaster/benchmarking/test_alfworld_types.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml \
  src/homemaster/benchmarking/__init__.py \
  src/homemaster/benchmarking/alfworld/__init__.py \
  src/homemaster/benchmarking/alfworld/types.py \
  tests/homemaster/benchmarking/test_alfworld_types.py
git commit -m "feat: add alfworld benchmark types"
```

## Task 3: Add ALFWorld Command Translator

**Files:**

- Create: `src/homemaster/benchmarking/alfworld/translator.py`
- Create: `tests/homemaster/benchmarking/test_alfworld_translator.py`

- [ ] **Step 1: Write translator tests**

Create `tests/homemaster/benchmarking/test_alfworld_translator.py`:

```python
from __future__ import annotations

import pytest

from homemaster.benchmarking.alfworld.translator import (
    TranslatorValidationError,
    create_translator,
)


def test_textworld_translator_maps_core_actions() -> None:
    translator = create_translator("AlfredTWEnv")

    assert translator.observe(mode="look") == "look"
    assert translator.observe(mode="inventory") == "inventory"
    assert translator.observe(mode="examine", target="apple 1") == "examine apple 1"
    assert translator.navigate(target_receptacle="countertop 1") == "go to countertop 1"
    assert translator.manipulate(
        action="take",
        object="apple 1",
        source_receptacle="countertop 1",
    ) == "take apple 1 from countertop 1"
    assert translator.manipulate(
        action="put",
        object="apple 1",
        target_receptacle="diningtable 1",
    ) == "move apple 1 to diningtable 1"
    assert translator.manipulate(
        action="heat",
        object="mug 1",
        tool_receptacle="microwave 1",
    ) == "heat mug 1 with microwave 1"


def test_translator_rejects_missing_conditional_arguments() -> None:
    translator = create_translator("AlfredTWEnv")

    with pytest.raises(TranslatorValidationError, match="source_receptacle"):
        translator.manipulate(action="take", object="apple 1")

    with pytest.raises(TranslatorValidationError, match="target_receptacle"):
        translator.manipulate(action="put", object="apple 1")

    with pytest.raises(TranslatorValidationError, match="tool_receptacle"):
        translator.manipulate(action="clean", object="mug 1")


def test_public_action_schema_contains_textworld_put_template() -> None:
    translator = create_translator("AlfredTWEnv")

    schema = translator.public_action_schema()
    put_actions = [
        item for item in schema["manipulation_actions"]
        if item["action"] == "put"
    ]

    assert put_actions[0]["command_template"] == "move {object} to {target_receptacle}"
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
cd /hpc2hdd/home/wyuan140/weilin_workspace/Homemaster
. .venv/bin/activate
pytest tests/homemaster/benchmarking/test_alfworld_translator.py -q
```

Expected: FAIL because `translator.py` does not exist.

- [ ] **Step 3: Implement translator**

Create `src/homemaster/benchmarking/alfworld/translator.py`:

```python
"""Translate HomeMaster benchmark tool arguments into ALFWorld commands."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class TranslatorValidationError(ValueError):
    """Raised when tool arguments cannot be translated into an ALFWorld command."""


def _required(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TranslatorValidationError(f"{field_name} is required")
    return value.strip()


@dataclass(frozen=True)
class AlfworldCommandTranslator:
    env_type: str
    put_template: str

    def public_action_schema(self) -> dict[str, Any]:
        return {
            "environment": self.env_type,
            "observe_modes": [
                {"mode": "look", "command_template": "look"},
                {"mode": "inventory", "command_template": "inventory"},
                {"mode": "examine", "command_template": "examine {target}"},
            ],
            "navigation": {
                "tool": "robot_navigate",
                "required": ["target_receptacle"],
                "command_template": "go to {target_receptacle}",
            },
            "manipulation_actions": [
                {
                    "action": "take",
                    "required": ["object", "source_receptacle"],
                    "command_template": "take {object} from {source_receptacle}",
                },
                {
                    "action": "put",
                    "required": ["object", "target_receptacle"],
                    "command_template": self.put_template,
                },
                {
                    "action": "open",
                    "required": ["target_receptacle"],
                    "command_template": "open {target_receptacle}",
                },
                {
                    "action": "close",
                    "required": ["target_receptacle"],
                    "command_template": "close {target_receptacle}",
                },
                {
                    "action": "use",
                    "required": ["object"],
                    "command_template": "use {object}",
                },
                {
                    "action": "heat",
                    "required": ["object", "tool_receptacle"],
                    "command_template": "heat {object} with {tool_receptacle}",
                },
                {
                    "action": "cool",
                    "required": ["object", "tool_receptacle"],
                    "command_template": "cool {object} with {tool_receptacle}",
                },
                {
                    "action": "clean",
                    "required": ["object", "tool_receptacle"],
                    "command_template": "clean {object} with {tool_receptacle}",
                },
                {
                    "action": "slice",
                    "required": ["object", "tool_receptacle"],
                    "command_template": "slice {object} with {tool_receptacle}",
                },
            ],
        }

    def observe(self, *, mode: str = "look", target: str | None = None) -> str:
        mode = mode.strip() if isinstance(mode, str) and mode.strip() else "look"
        if mode == "look":
            return "look"
        if mode == "inventory":
            return "inventory"
        if mode == "examine":
            return f"examine {_required(target, 'target')}"
        raise TranslatorValidationError(f"unsupported observe mode: {mode}")

    def navigate(self, *, target_receptacle: str) -> str:
        return f"go to {_required(target_receptacle, 'target_receptacle')}"

    def manipulate(self, *, action: str, **kwargs: object) -> str:
        action = _required(action, "action")
        if action == "take":
            obj = _required(kwargs.get("object"), "object")
            source = _required(kwargs.get("source_receptacle"), "source_receptacle")
            return f"take {obj} from {source}"
        if action == "put":
            obj = _required(kwargs.get("object"), "object")
            target = _required(kwargs.get("target_receptacle"), "target_receptacle")
            return self.put_template.format(object=obj, target_receptacle=target)
        if action in {"open", "close"}:
            target = _required(kwargs.get("target_receptacle"), "target_receptacle")
            return f"{action} {target}"
        if action == "use":
            return f"use {_required(kwargs.get('object'), 'object')}"
        if action in {"heat", "cool", "clean", "slice"}:
            obj = _required(kwargs.get("object"), "object")
            tool = _required(kwargs.get("tool_receptacle"), "tool_receptacle")
            return f"{action} {obj} with {tool}"
        raise TranslatorValidationError(f"unsupported manipulation action: {action}")


def create_translator(env_type: str) -> AlfworldCommandTranslator:
    if env_type == "AlfredTWEnv":
        return AlfworldCommandTranslator(
            env_type=env_type,
            put_template="move {object} to {target_receptacle}",
        )
    if env_type == "AlfredThorEnv":
        return AlfworldCommandTranslator(
            env_type=env_type,
            put_template="put {object} in/on {target_receptacle}",
        )
    raise TranslatorValidationError(f"unsupported env_type: {env_type}")
```

- [ ] **Step 4: Run translator tests**

Run:

```bash
cd /hpc2hdd/home/wyuan140/weilin_workspace/Homemaster
. .venv/bin/activate
pytest tests/homemaster/benchmarking/test_alfworld_translator.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/homemaster/benchmarking/alfworld/translator.py \
  tests/homemaster/benchmarking/test_alfworld_translator.py
git commit -m "feat: add alfworld command translator"
```

## Task 4: Add Environment Adapter with Fake Env Tests

**Files:**

- Create: `src/homemaster/benchmarking/alfworld/env_adapter.py`
- Create: `tests/homemaster/benchmarking/test_alfworld_env_adapter.py`

- [ ] **Step 1: Write adapter tests using a fake batch env**

Create `tests/homemaster/benchmarking/test_alfworld_env_adapter.py`:

```python
from __future__ import annotations

from homemaster.benchmarking.alfworld.env_adapter import (
    AlfworldEnvAdapter,
    split_to_train_eval,
)


class FakeBatchEnv:
    def __init__(self) -> None:
        self.actions: list[list[str]] = []
        self.reset_called = False
        self.current_admissible = ["look", "go to countertop 1"]

    def seed(self, seed: int) -> None:
        self.seed_value = seed

    def reset(self):
        self.reset_called = True
        return (
            ["Your task is to: put apple on the table\nYou are in the kitchen."],
            {
                "extra.gamefile": ["/games/pick_and_place/task/game.tw-pddl"],
                "admissible_commands": [self.current_admissible],
                "won": [False],
                "goal_condition_success_rate": [0.0],
            },
        )

    def step(self, actions: list[str]):
        self.actions.append(actions)
        command = actions[0]
        if command == "go to countertop 1":
            self.current_admissible = ["look", "take apple 1 from countertop 1"]
            return (
                ["You arrive at countertop 1. You see apple 1."],
                [0.0],
                [False],
                {
                    "admissible_commands": [self.current_admissible],
                    "won": [False],
                    "goal_condition_success_rate": [0.0],
                },
            )
        return (
            ["Nothing happens."],
            [0.0],
            [False],
            {
                "admissible_commands": [self.current_admissible],
                "won": [False],
                "goal_condition_success_rate": [0.0],
            },
        )


def test_split_to_train_eval_mapping() -> None:
    assert split_to_train_eval("train") == "train"
    assert split_to_train_eval("valid_seen") == "eval_in_distribution"
    assert split_to_train_eval("valid_unseen") == "eval_out_of_distribution"


def test_adapter_reset_normalizes_initial_state_without_visible_admissible_commands() -> None:
    env = FakeBatchEnv()
    adapter = AlfworldEnvAdapter(env=env, episode_prefix="episode", seed=123)

    state = adapter.reset()

    assert env.reset_called is True
    assert state.episode_id == "pick_and_place/task"
    assert state.step_index == 0
    assert state.frame_path is None
    assert state.to_model_visible_dict()["observation"].startswith("Your task is to")
    assert "admissible_commands" not in state.to_model_visible_dict()
    assert state.to_debug_dict()["admissible_commands"] == ["look", "go to countertop 1"]


def test_adapter_step_tracks_invalid_action_using_hidden_admissible_commands() -> None:
    adapter = AlfworldEnvAdapter(env=FakeBatchEnv(), episode_prefix="episode", seed=123)
    adapter.reset()

    valid = adapter.step("go to countertop 1", tool_name="robot_navigate", tool_args={})
    invalid = adapter.step("go to fridge 1", tool_name="robot_navigate", tool_args={})

    assert valid.success is True
    assert valid.state.invalid_action_count == 0
    assert invalid.success is False
    assert invalid.failure_reason == "invalid_action"
    assert invalid.state.invalid_action_count == 1
    assert "admissible_commands" not in invalid.to_model_visible_data()
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
cd /hpc2hdd/home/wyuan140/weilin_workspace/Homemaster
. .venv/bin/activate
pytest tests/homemaster/benchmarking/test_alfworld_env_adapter.py -q
```

Expected: FAIL because `env_adapter.py` does not exist.

- [ ] **Step 3: Implement adapter**

Create `src/homemaster/benchmarking/alfworld/env_adapter.py`:

```python
"""Adapter around ALFWorld batch environments for HomeMaster benchmark tools."""

from __future__ import annotations

import contextlib
import json
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from homemaster.benchmarking.alfworld.types import (
    AlfworldBenchmarkConfig,
    AlfworldEnvState,
    AlfworldStepResult,
)


def split_to_train_eval(split: str) -> str:
    mapping = {
        "train": "train",
        "valid_seen": "eval_in_distribution",
        "valid_unseen": "eval_out_of_distribution",
    }
    if split not in mapping:
        raise ValueError(f"unsupported ALFWorld split: {split}")
    return mapping[split]


@contextlib.contextmanager
def _prepend_sys_path(path: Path) -> Iterator[None]:
    value = str(path)
    added = value not in sys.path
    if added:
        sys.path.insert(0, value)
    try:
        yield
    finally:
        if added:
            sys.path.remove(value)


def load_alfworld_yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError(
            "pyyaml is required for ALFWorld benchmark config loading; "
            "install HomeMaster with the alfworld extra"
        ) from exc

    with path.open("r", encoding="utf-8") as reader:
        payload = yaml.safe_load(reader)
    if not isinstance(payload, dict):
        raise ValueError(f"ALFWorld config must be a mapping: {path}")
    return payload


def build_alfworld_batch_env(config: AlfworldBenchmarkConfig) -> Any:
    with _prepend_sys_path(config.alfworld_root):
        from alfworld.agents.environment import get_environment

        payload = load_alfworld_yaml(config.alfworld_config)
        env_cls = get_environment(config.env_type)
        alfred_env = env_cls(payload, train_eval=split_to_train_eval(config.split))
        env = alfred_env.init_env(batch_size=1)
        if hasattr(env, "seed"):
            env.seed(config.seed)
        return env


class AlfworldEnvAdapter:
    def __init__(
        self,
        *,
        env: Any,
        episode_prefix: str,
        seed: int,
    ) -> None:
        self._env = env
        self._episode_prefix = episode_prefix
        self._seed = seed
        self._state: AlfworldEnvState | None = None
        if hasattr(self._env, "seed"):
            self._env.seed(seed)

    @property
    def current_state(self) -> AlfworldEnvState:
        if self._state is None:
            raise RuntimeError("ALFWorld environment has not been reset")
        return self._state

    def reset(self) -> AlfworldEnvState:
        obs, infos = self._env.reset()
        observation = _first(obs, "")
        gamefile = _first_info(infos, "extra.gamefile", f"{self._episode_prefix}/unknown")
        state = AlfworldEnvState(
            episode_id=_episode_id_from_gamefile(str(gamefile), self._episode_prefix),
            task=str(observation),
            observation=str(observation),
            inventory=None,
            last_command=None,
            last_feedback=None,
            reward=0.0,
            done=False,
            won=bool(_first_info(infos, "won", False)),
            goal_condition_success_rate=float(_first_info(infos, "goal_condition_success_rate", 0.0)),
            frame_path=None,
            step_index=0,
            invalid_action_count=0,
            admissible_commands=tuple(str(item) for item in _first_info(infos, "admissible_commands", [])),
        )
        self._state = state
        return state

    def step(
        self,
        command: str,
        *,
        tool_name: str,
        tool_args: dict[str, Any],
    ) -> AlfworldStepResult:
        previous = self.current_state
        previous_commands = set(previous.admissible_commands)
        invalid = bool(previous_commands) and command not in previous_commands

        try:
            obs, scores, dones, infos = self._env.step([command])
            observation = str(_first(obs, ""))
            reward = float(_first(scores, 0.0))
            done = bool(_first(dones, False))
            won = bool(_first_info(infos, "won", False))
            goal_rate = float(_first_info(infos, "goal_condition_success_rate", 0.0))
            admissible = tuple(
                str(item) for item in _first_info(infos, "admissible_commands", [])
            )
        except Exception as exc:
            state = AlfworldEnvState(
                episode_id=previous.episode_id,
                task=previous.task,
                observation=previous.observation,
                inventory=previous.inventory,
                last_command=command,
                last_feedback=str(exc),
                reward=previous.reward,
                done=previous.done,
                won=previous.won,
                goal_condition_success_rate=previous.goal_condition_success_rate,
                frame_path=previous.frame_path,
                step_index=previous.step_index + 1,
                invalid_action_count=previous.invalid_action_count,
                admissible_commands=previous.admissible_commands,
            )
            self._state = state
            return AlfworldStepResult(
                tool_name=tool_name,
                tool_args=tool_args,
                translated_command=command,
                success=False,
                failure_reason="env_error",
                state=state,
                feedback=str(exc),
            )

        invalid_count = previous.invalid_action_count + (1 if invalid else 0)
        state = AlfworldEnvState(
            episode_id=previous.episode_id,
            task=previous.task,
            observation=observation,
            inventory=previous.inventory,
            last_command=command,
            last_feedback=observation,
            reward=reward,
            done=done,
            won=won,
            goal_condition_success_rate=goal_rate,
            frame_path=None,
            step_index=previous.step_index + 1,
            invalid_action_count=invalid_count,
            admissible_commands=admissible,
        )
        self._state = state
        return AlfworldStepResult(
            tool_name=tool_name,
            tool_args=tool_args,
            translated_command=command,
            success=not invalid,
            failure_reason="invalid_action" if invalid else None,
            state=state,
            feedback=observation,
        )


def _first(value: Any, default: Any) -> Any:
    if isinstance(value, (list, tuple)) and value:
        return value[0]
    return default


def _first_info(infos: dict[str, Any], key: str, default: Any) -> Any:
    value = infos.get(key, default)
    if isinstance(value, (list, tuple)) and value:
        return value[0]
    return value


def _episode_id_from_gamefile(gamefile: str, prefix: str) -> str:
    path = Path(gamefile)
    parts = path.parts
    if len(parts) >= 3:
        return "/".join(parts[-3:-1])
    try:
        payload = json.loads(gamefile)
        if isinstance(payload, str):
            return payload
    except ValueError:
        pass
    return f"{prefix}/unknown"
```

- [ ] **Step 4: Run adapter tests**

Run:

```bash
cd /hpc2hdd/home/wyuan140/weilin_workspace/Homemaster
. .venv/bin/activate
pytest tests/homemaster/benchmarking/test_alfworld_env_adapter.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/homemaster/benchmarking/alfworld/env_adapter.py \
  tests/homemaster/benchmarking/test_alfworld_env_adapter.py
git commit -m "feat: add alfworld environment adapter"
```

## Task 5: Add Trace Writer

**Files:**

- Create: `src/homemaster/benchmarking/alfworld/tracing.py`
- Create: `tests/homemaster/benchmarking/test_alfworld_tracing.py`

- [ ] **Step 1: Write trace tests**

Create `tests/homemaster/benchmarking/test_alfworld_tracing.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

from homemaster.benchmarking.alfworld.tracing import AlfworldTraceWriter


def test_trace_writer_writes_jsonl_and_redacts_secret_keys(tmp_path: Path) -> None:
    writer = AlfworldTraceWriter(tmp_path / "episode-1")
    writer.write_event({
        "event": "tool_step",
        "observation": "You see apple 1.",
        "api_key": "secret",
        "nested": {"auth_token": "secret", "safe": "ok"},
    })

    payload = json.loads((tmp_path / "episode-1" / "trace.jsonl").read_text().strip())

    assert payload["event"] == "tool_step"
    assert payload["api_key"] == "[REDACTED]"
    assert payload["nested"]["auth_token"] == "[REDACTED]"
    assert payload["nested"]["safe"] == "ok"
```

- [ ] **Step 2: Run trace tests and verify they fail**

Run:

```bash
cd /hpc2hdd/home/wyuan140/weilin_workspace/Homemaster
. .venv/bin/activate
pytest tests/homemaster/benchmarking/test_alfworld_tracing.py -q
```

Expected: FAIL because `tracing.py` does not exist.

- [ ] **Step 3: Implement trace writer**

Create `src/homemaster/benchmarking/alfworld/tracing.py`:

```python
"""Trace output for ALFWorld benchmark episodes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


SECRET_KEY_FRAGMENTS = ("api_key", "token", "auth", "secret", "password")


class AlfworldTraceWriter:
    def __init__(self, episode_dir: Path) -> None:
        self.episode_dir = episode_dir
        self.episode_dir.mkdir(parents=True, exist_ok=True)
        self.trace_path = self.episode_dir / "trace.jsonl"
        self.summary_path = self.episode_dir / "summary.json"

    def write_event(self, event: dict[str, Any]) -> None:
        with self.trace_path.open("a", encoding="utf-8") as writer:
            writer.write(json.dumps(_redact(event), ensure_ascii=False, sort_keys=True))
            writer.write("\n")

    def write_summary(self, summary: dict[str, Any]) -> None:
        self.summary_path.write_text(
            json.dumps(_redact(summary), ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )


def _redact(value: Any, key: str | None = None) -> Any:
    if key is not None and any(fragment in key.lower() for fragment in SECRET_KEY_FRAGMENTS):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(k): _redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, tuple):
        return [_redact(item) for item in value]
    return value
```

- [ ] **Step 4: Run trace tests**

Run:

```bash
cd /hpc2hdd/home/wyuan140/weilin_workspace/Homemaster
. .venv/bin/activate
pytest tests/homemaster/benchmarking/test_alfworld_tracing.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/homemaster/benchmarking/alfworld/tracing.py \
  tests/homemaster/benchmarking/test_alfworld_tracing.py
git commit -m "feat: add alfworld benchmark tracing"
```

## Task 6: Add ALFWorld-Backed Robot Tools

**Files:**

- Create: `src/homemaster/benchmarking/alfworld/tools.py`
- Create: `tests/homemaster/benchmarking/test_alfworld_tools.py`

- [ ] **Step 1: Write tool tests**

Create `tests/homemaster/benchmarking/test_alfworld_tools.py`:

```python
from __future__ import annotations

from pathlib import Path
from typing import Any

from homemaster.agent.normalized import RunContext
from homemaster.benchmarking.alfworld.tools import (
    make_alfworld_robot_manipulate,
    make_alfworld_robot_navigate,
    make_alfworld_robot_observe,
    make_alfworld_robot_verify,
)
from homemaster.benchmarking.alfworld.translator import create_translator
from homemaster.benchmarking.alfworld.types import AlfworldEnvState, AlfworldStepResult
from homemaster.config.runtime_settings import RuntimeSettings


class FakeAdapter:
    def __init__(self) -> None:
        self.commands: list[str] = []
        self.state = AlfworldEnvState(
            episode_id="game-1",
            task="put apple on table",
            observation="You are in the kitchen.",
            inventory=None,
            last_command=None,
            last_feedback=None,
            reward=0.0,
            done=False,
            won=False,
            goal_condition_success_rate=0.0,
            frame_path=None,
            step_index=0,
            invalid_action_count=0,
        )

    @property
    def current_state(self) -> AlfworldEnvState:
        return self.state

    def step(self, command: str, *, tool_name: str, tool_args: dict[str, Any]):
        self.commands.append(command)
        self.state = AlfworldEnvState(
            episode_id="game-1",
            task="put apple on table",
            observation=f"after {command}",
            inventory=None,
            last_command=command,
            last_feedback=f"after {command}",
            reward=0.0,
            done=False,
            won=command == "move apple 1 to diningtable 1",
            goal_condition_success_rate=1.0 if command == "move apple 1 to diningtable 1" else 0.0,
            frame_path=None,
            step_index=self.state.step_index + 1,
            invalid_action_count=self.state.invalid_action_count,
        )
        return AlfworldStepResult(
            tool_name=tool_name,
            tool_args=tool_args,
            translated_command=command,
            success=True,
            failure_reason=None,
            state=self.state,
            feedback=f"after {command}",
        )


def _context(adapter: FakeAdapter) -> RunContext:
    return RunContext(
        session_id="s1",
        run_id="r1",
        turn_index=0,
        settings=RuntimeSettings(
            run_id="r1",
            runtime_root=Path("/tmp/runs"),
            debug_root=Path("/tmp/debug"),
            results_root=Path("/tmp/results"),
        ),
        event_sink=None,
        deps={
            "alfworld_env": adapter,
            "alfworld_translator": create_translator("AlfredTWEnv"),
        },
    )


def test_navigate_tool_translates_and_steps_env() -> None:
    adapter = FakeAdapter()
    spec = make_alfworld_robot_navigate()
    result = spec.executor(
        arguments={"target_receptacle": "countertop 1"},
        run_context=_context(adapter),
    )

    assert result.success is True
    assert adapter.commands == ["go to countertop 1"]
    assert result.data["observation"] == "after go to countertop 1"
    assert "admissible_commands" not in result.data


def test_manipulate_validation_error_does_not_step_env() -> None:
    adapter = FakeAdapter()
    spec = make_alfworld_robot_manipulate()
    result = spec.executor(
        arguments={"action": "take", "object": "apple 1"},
        run_context=_context(adapter),
    )

    assert result.success is False
    assert result.failure_reason == "translator_validation_error"
    assert adapter.commands == []
    assert result.data["observation"] == "You are in the kitchen."


def test_verify_success_requires_env_won() -> None:
    adapter = FakeAdapter()
    verify = make_alfworld_robot_verify()

    not_done = verify.executor(arguments={}, run_context=_context(adapter))
    assert not_done.success is False
    assert not_done.failure_reason == "not_won_yet"

    manipulate = make_alfworld_robot_manipulate()
    manipulate.executor(
        arguments={
            "action": "put",
            "object": "apple 1",
            "target_receptacle": "diningtable 1",
        },
        run_context=_context(adapter),
    )
    done = verify.executor(arguments={}, run_context=_context(adapter))
    assert done.success is True
    assert done.data["won"] is True


def test_observe_inventory_uses_inventory_command() -> None:
    adapter = FakeAdapter()
    observe = make_alfworld_robot_observe()
    observe.executor(arguments={"mode": "inventory"}, run_context=_context(adapter))
    assert adapter.commands == ["inventory"]
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
cd /hpc2hdd/home/wyuan140/weilin_workspace/Homemaster
. .venv/bin/activate
pytest tests/homemaster/benchmarking/test_alfworld_tools.py -q
```

Expected: FAIL because `tools.py` does not exist.

- [ ] **Step 3: Implement benchmark tools**

Create `src/homemaster/benchmarking/alfworld/tools.py`:

```python
"""ALFWorld-backed robot tools for benchmark runs."""

from __future__ import annotations

from typing import Any

from homemaster.agent.normalized import RunContext
from homemaster.benchmarking.alfworld.env_adapter import AlfworldEnvAdapter
from homemaster.benchmarking.alfworld.translator import (
    AlfworldCommandTranslator,
    TranslatorValidationError,
)
from homemaster.tools.results import ToolResult
from homemaster.tools.spec import ToolSpec


def _adapter(run_context: RunContext) -> AlfworldEnvAdapter:
    adapter = run_context.deps.get("alfworld_env")
    if adapter is None:
        raise RuntimeError("missing run_context.deps['alfworld_env']")
    return adapter


def _translator(run_context: RunContext) -> AlfworldCommandTranslator:
    translator = run_context.deps.get("alfworld_translator")
    if translator is None:
        raise RuntimeError("missing run_context.deps['alfworld_translator']")
    return translator


def _result_from_step(step_result) -> ToolResult:
    data = step_result.to_model_visible_data()
    return ToolResult(
        success=step_result.success,
        tool_name=step_result.tool_name,
        executor_mode="programmatic",
        data=data,
        failure_reason=step_result.failure_reason,
        retryable=not step_result.state.done,
        summary=step_result.feedback,
    )


def _validation_failure(
    *,
    tool_name: str,
    arguments: dict[str, Any],
    run_context: RunContext,
    error: Exception,
) -> ToolResult:
    state = _adapter(run_context).current_state
    data = state.to_model_visible_dict()
    data.update({
        "tool_name": tool_name,
        "tool_args": arguments,
        "translated_command": None,
        "feedback": str(error),
    })
    return ToolResult(
        success=False,
        tool_name=tool_name,
        executor_mode="programmatic",
        data=data,
        failure_reason="translator_validation_error",
        retryable=True,
        summary=str(error),
    )


def _exec_observe(*, arguments: dict[str, Any], run_context: RunContext) -> ToolResult:
    try:
        command = _translator(run_context).observe(
            mode=arguments.get("mode", "look"),
            target=arguments.get("target"),
        )
    except TranslatorValidationError as exc:
        return _validation_failure(
            tool_name="robot_observe",
            arguments=arguments,
            run_context=run_context,
            error=exc,
        )
    return _result_from_step(
        _adapter(run_context).step(
            command,
            tool_name="robot_observe",
            tool_args=arguments,
        )
    )


def _exec_navigate(*, arguments: dict[str, Any], run_context: RunContext) -> ToolResult:
    try:
        command = _translator(run_context).navigate(
            target_receptacle=arguments.get("target_receptacle", ""),
        )
    except TranslatorValidationError as exc:
        return _validation_failure(
            tool_name="robot_navigate",
            arguments=arguments,
            run_context=run_context,
            error=exc,
        )
    return _result_from_step(
        _adapter(run_context).step(
            command,
            tool_name="robot_navigate",
            tool_args=arguments,
        )
    )


def _exec_manipulate(*, arguments: dict[str, Any], run_context: RunContext) -> ToolResult:
    try:
        command = _translator(run_context).manipulate(**arguments)
    except TranslatorValidationError as exc:
        return _validation_failure(
            tool_name="robot_manipulate",
            arguments=arguments,
            run_context=run_context,
            error=exc,
        )
    return _result_from_step(
        _adapter(run_context).step(
            command,
            tool_name="robot_manipulate",
            tool_args=arguments,
        )
    )


def _exec_verify(*, arguments: dict[str, Any], run_context: RunContext) -> ToolResult:
    state = _adapter(run_context).current_state
    data = state.to_model_visible_dict()
    data.update({
        "tool_name": "robot_verify",
        "tool_args": arguments,
        "verified": state.won,
        "expected_done": arguments.get("expected_done"),
    })
    if state.won:
        return ToolResult(
            success=True,
            tool_name="robot_verify",
            executor_mode="programmatic",
            data=data,
            summary="Environment reports won=true.",
        )
    return ToolResult(
        success=False,
        tool_name="robot_verify",
        executor_mode="programmatic",
        data=data,
        failure_reason="not_won_yet",
        retryable=not state.done,
        summary="Environment has not reported won=true.",
    )


def make_alfworld_robot_observe() -> ToolSpec:
    return ToolSpec(
        name="robot_observe",
        description="Observe the ALFWorld environment using look, inventory, or examine.",
        input_schema={
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["look", "inventory", "examine"],
                    "description": "Observation command mode.",
                },
                "target": {
                    "type": "string",
                    "description": "Object or receptacle to examine when mode is examine.",
                },
            },
        },
        executor_mode="programmatic",
        selectable_by_model=True,
        executor=_exec_observe,
    )


def make_alfworld_robot_navigate() -> ToolSpec:
    return ToolSpec(
        name="robot_navigate",
        description="Move to an ALFWorld receptacle using its environment name.",
        input_schema={
            "type": "object",
            "properties": {
                "target_receptacle": {
                    "type": "string",
                    "description": "Destination receptacle, such as countertop 1.",
                },
            },
            "required": ["target_receptacle"],
        },
        executor_mode="programmatic",
        selectable_by_model=True,
        executor=_exec_navigate,
    )


def make_alfworld_robot_manipulate() -> ToolSpec:
    return ToolSpec(
        name="robot_manipulate",
        description="Manipulate ALFWorld objects with take, put, open, close, use, heat, cool, clean, or slice.",
        input_schema={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["take", "put", "open", "close", "use", "heat", "cool", "clean", "slice"],
                },
                "object": {"type": "string"},
                "source_receptacle": {"type": "string"},
                "target_receptacle": {"type": "string"},
                "tool_receptacle": {"type": "string"},
            },
            "required": ["action"],
        },
        executor_mode="programmatic",
        selectable_by_model=True,
        executor=_exec_manipulate,
    )


def make_alfworld_robot_verify() -> ToolSpec:
    return ToolSpec(
        name="robot_verify",
        description="Check whether ALFWorld reports the task as won. This is the only benchmark success signal.",
        input_schema={
            "type": "object",
            "properties": {
                "expected_done": {
                    "type": "string",
                    "description": "Optional description of the expected completed condition.",
                },
            },
        },
        executor_mode="programmatic",
        selectable_by_model=True,
        executor=_exec_verify,
    )
```

- [ ] **Step 4: Run tool tests**

Run:

```bash
cd /hpc2hdd/home/wyuan140/weilin_workspace/Homemaster
. .venv/bin/activate
pytest tests/homemaster/benchmarking/test_alfworld_tools.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/homemaster/benchmarking/alfworld/tools.py \
  tests/homemaster/benchmarking/test_alfworld_tools.py
git commit -m "feat: add alfworld-backed robot tools"
```

## Task 7: Add Benchmark Tool Registry

**Files:**

- Create: `src/homemaster/benchmarking/alfworld/registry.py`
- Create: `tests/homemaster/benchmarking/test_alfworld_registry.py`

- [ ] **Step 1: Write registry tests**

Create `tests/homemaster/benchmarking/test_alfworld_registry.py`:

```python
from __future__ import annotations

from homemaster.benchmarking.alfworld.registry import build_alfworld_tool_registry


def test_disabled_memory_registry_excludes_memory_tools() -> None:
    registry = build_alfworld_tool_registry(memory_mode="disabled")
    names = registry.all_names()

    assert "task_interpreter" in names
    assert "robot_observe" in names
    assert "robot_navigate" in names
    assert "robot_manipulate" in names
    assert "robot_verify" in names
    assert "task_summarizer" in names
    assert "memory_retriever" not in names
    assert "target_grounder" not in names
    assert "memory_writer" not in names


def test_readonly_memory_registry_adds_retriever_only() -> None:
    names = build_alfworld_tool_registry(memory_mode="readonly").all_names()

    assert "memory_retriever" in names
    assert "memory_writer" not in names
    assert "target_grounder" not in names


def test_full_memory_registry_adds_retriever_and_writer() -> None:
    names = build_alfworld_tool_registry(memory_mode="full").all_names()

    assert "memory_retriever" in names
    assert "memory_writer" in names
    assert "target_grounder" not in names
```

- [ ] **Step 2: Run registry tests and verify they fail**

Run:

```bash
cd /hpc2hdd/home/wyuan140/weilin_workspace/Homemaster
. .venv/bin/activate
pytest tests/homemaster/benchmarking/test_alfworld_registry.py -q
```

Expected: FAIL because `registry.py` does not exist.

- [ ] **Step 3: Implement registry**

Create `src/homemaster/benchmarking/alfworld/registry.py`:

```python
"""Tool registry builder for ALFWorld benchmark mode."""

from __future__ import annotations

from pathlib import Path

from homemaster.benchmarking.alfworld.tools import (
    make_alfworld_robot_manipulate,
    make_alfworld_robot_navigate,
    make_alfworld_robot_observe,
    make_alfworld_robot_verify,
)
from homemaster.domain.home.tools import (
    make_memory_retriever,
    make_memory_writer,
    make_task_interpreter,
    make_task_summarizer,
)
from homemaster.tools.registry import ToolRegistry


def build_alfworld_tool_registry(
    *,
    memory_mode: str = "disabled",
    memory_path: Path | None = None,
    runtime_memory_root: Path | None = None,
) -> ToolRegistry:
    if memory_mode not in {"disabled", "readonly", "full"}:
        raise ValueError(f"unsupported memory_mode: {memory_mode}")

    registry = ToolRegistry()
    registry.register(make_task_interpreter())

    if memory_mode in {"readonly", "full"}:
        registry.register(make_memory_retriever(memory_path=memory_path))
    if memory_mode == "full":
        registry.register(make_memory_writer(runtime_memory_root=runtime_memory_root))

    registry.register(make_alfworld_robot_observe())
    registry.register(make_alfworld_robot_navigate())
    registry.register(make_alfworld_robot_manipulate())
    registry.register(make_alfworld_robot_verify())
    registry.register(make_task_summarizer())
    return registry
```

- [ ] **Step 4: Run registry tests**

Run:

```bash
cd /hpc2hdd/home/wyuan140/weilin_workspace/Homemaster
. .venv/bin/activate
pytest tests/homemaster/benchmarking/test_alfworld_registry.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/homemaster/benchmarking/alfworld/registry.py \
  tests/homemaster/benchmarking/test_alfworld_registry.py
git commit -m "feat: add alfworld benchmark tool registry"
```

## Task 8: Add Prompt Builder

**Files:**

- Create: `src/homemaster/benchmarking/alfworld/prompt.py`
- Create: `tests/homemaster/benchmarking/test_alfworld_prompt.py`

- [ ] **Step 1: Write prompt tests**

Create `tests/homemaster/benchmarking/test_alfworld_prompt.py`:

```python
from __future__ import annotations

from homemaster.benchmarking.alfworld.prompt import build_episode_prompt
from homemaster.benchmarking.alfworld.translator import create_translator
from homemaster.benchmarking.alfworld.types import AlfworldEnvState


def test_episode_prompt_requires_tools_and_omits_admissible_commands() -> None:
    state = AlfworldEnvState(
        episode_id="game-1",
        task="put apple on table",
        observation="You are in the kitchen.",
        inventory=None,
        last_command=None,
        last_feedback=None,
        reward=0.0,
        done=False,
        won=False,
        goal_condition_success_rate=0.0,
        frame_path=None,
        step_index=0,
        invalid_action_count=0,
        admissible_commands=("go to countertop 1",),
    )

    prompt = build_episode_prompt(
        state=state,
        translator=create_translator("AlfredTWEnv"),
        memory_mode="disabled",
        max_invalid_actions=100,
    )

    assert "must use tools" in prompt.lower()
    assert "raw ALFWorld commands" in prompt
    assert "move {object} to {target_receptacle}" in prompt
    assert "go to countertop 1" not in prompt
    assert "admissible_commands" not in prompt
    assert "Memory tools are not available" in prompt
```

- [ ] **Step 2: Run prompt tests and verify they fail**

Run:

```bash
cd /hpc2hdd/home/wyuan140/weilin_workspace/Homemaster
. .venv/bin/activate
pytest tests/homemaster/benchmarking/test_alfworld_prompt.py -q
```

Expected: FAIL because `prompt.py` does not exist.

- [ ] **Step 3: Implement prompt builder**

Create `src/homemaster/benchmarking/alfworld/prompt.py`:

```python
"""Prompt composition for ALFWorld benchmark episodes."""

from __future__ import annotations

import json
from typing import Any

from homemaster.benchmarking.alfworld.translator import AlfworldCommandTranslator
from homemaster.benchmarking.alfworld.types import AlfworldEnvState


def build_episode_prompt(
    *,
    state: AlfworldEnvState,
    translator: AlfworldCommandTranslator,
    memory_mode: str,
    max_invalid_actions: int,
) -> str:
    action_reference = _format_action_reference(translator.public_action_schema())
    memory_line = (
        "Memory tools are not available in this benchmark run."
        if memory_mode == "disabled"
        else f"Memory mode is {memory_mode}; use only the registered memory tools."
    )
    environment_json = json.dumps(
        state.to_model_visible_dict(),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    return "\n".join([
        "You are HomeMaster controlling a home assistant robot inside ALFWorld.",
        "You must use tools to complete the task. Do not answer with raw ALFWorld commands.",
        "Object and receptacle names should match the current environment language when possible.",
        "Each tool result gives the latest observation, feedback, reward, done, won, and progress.",
        "If a tool fails, recover by choosing another tool call from the new observation and feedback.",
        "Success is determined only by ALFWorld won=true, not by a verbal completion claim.",
        f"The episode fails if invalid action count reaches {max_invalid_actions}.",
        memory_line,
        "",
        "Action reference generated by the environment translator:",
        action_reference,
        "",
        "Task and current environment:",
        environment_json,
    ])


def _format_action_reference(schema: dict[str, Any]) -> str:
    lines = [f"Environment: {schema['environment']}"]
    observe = schema.get("observe_modes", [])
    if observe:
        lines.append("Observation forms:")
        for item in observe:
            lines.append(f"- mode={item['mode']}: {item['command_template']}")
    navigation = schema.get("navigation")
    if isinstance(navigation, dict):
        lines.append("Navigation form:")
        lines.append(f"- {navigation['command_template']}")
    manipulation = schema.get("manipulation_actions", [])
    if manipulation:
        lines.append("Manipulation forms:")
        for item in manipulation:
            required = ", ".join(item.get("required", []))
            lines.append(
                f"- action={item['action']}, required=[{required}]: "
                f"{item['command_template']}"
            )
    return "\n".join(lines)
```

- [ ] **Step 4: Run prompt tests**

Run:

```bash
cd /hpc2hdd/home/wyuan140/weilin_workspace/Homemaster
. .venv/bin/activate
pytest tests/homemaster/benchmarking/test_alfworld_prompt.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/homemaster/benchmarking/alfworld/prompt.py \
  tests/homemaster/benchmarking/test_alfworld_prompt.py
git commit -m "feat: add alfworld benchmark prompt builder"
```

## Task 9: Add Runner with Fake Runtime Integration Test

**Files:**

- Create: `src/homemaster/benchmarking/alfworld/runner.py`
- Create: `tests/homemaster/benchmarking/test_alfworld_runner.py`

- [ ] **Step 1: Write runner test with fake transport and fake env**

Create `tests/homemaster/benchmarking/test_alfworld_runner.py`:

```python
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

from homemaster.agent.messages import AssistantMessage, ContentBlock, Message, ToolCall
from homemaster.benchmarking.alfworld.env_adapter import AlfworldEnvAdapter
from homemaster.benchmarking.alfworld.runner import AlfworldBenchmarkRunner
from homemaster.benchmarking.alfworld.types import AlfworldBenchmarkConfig
from homemaster.providers.transport import LLMTransport, TransportDelta


class FakeTransport(LLMTransport):
    def __init__(self) -> None:
        self._responses = [
            AssistantMessage(
                tool_calls=[
                    ToolCall(
                        id="call_1",
                        name="robot_navigate",
                        arguments={"target_receptacle": "countertop 1"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            AssistantMessage(
                tool_calls=[
                    ToolCall(
                        id="call_2",
                        name="robot_manipulate",
                        arguments={
                            "action": "take",
                            "object": "apple 1",
                            "source_receptacle": "countertop 1",
                        },
                    )
                ],
                finish_reason="tool_calls",
            ),
            AssistantMessage(
                tool_calls=[
                    ToolCall(
                        id="call_3",
                        name="robot_manipulate",
                        arguments={
                            "action": "put",
                            "object": "apple 1",
                            "target_receptacle": "diningtable 1",
                        },
                    )
                ],
                finish_reason="tool_calls",
            ),
            AssistantMessage(
                content=[ContentBlock(text="done")],
                finish_reason="stop",
            ),
        ]
        self.call_count = 0
        self.seen_tools: list[list[dict[str, Any]]] = []

    def stream(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        *,
        event_sink: Any = None,
        run_id: str = "",
        session_id: str = "",
        turn_index: int | None = None,
        iteration: int | None = None,
    ) -> Iterator[TransportDelta]:
        self.seen_tools.append(tools or [])
        msg = self._responses[self.call_count]
        self.call_count += 1
        for block in msg.content:
            yield TransportDelta(type="transport.delta", text_delta=block.text)
        for tool_call in msg.tool_calls:
            yield TransportDelta(type="transport.delta", tool_call_delta=tool_call)
        yield TransportDelta(type="transport.delta", finish_reason=msg.finish_reason)


class FakeBatchEnv:
    def __init__(self) -> None:
        self.admissible = ["look", "go to countertop 1"]

    def seed(self, seed: int) -> None:
        self.seed = seed

    def reset(self):
        return (
            ["Your task is to: put apple on diningtable 1."],
            {
                "extra.gamefile": ["/games/pick_and_place/task/game.tw-pddl"],
                "admissible_commands": [self.admissible],
                "won": [False],
                "goal_condition_success_rate": [0.0],
            },
        )

    def step(self, actions: list[str]):
        command = actions[0]
        transitions = {
            "go to countertop 1": (
                "You are at countertop 1. You see apple 1.",
                ["take apple 1 from countertop 1", "look"],
                False,
                0.0,
            ),
            "take apple 1 from countertop 1": (
                "You pick up apple 1.",
                ["move apple 1 to diningtable 1", "inventory", "look"],
                False,
                0.5,
            ),
            "move apple 1 to diningtable 1": (
                "You put apple 1 on diningtable 1.",
                ["look"],
                True,
                1.0,
            ),
        }
        observation, admissible, won, gc = transitions[command]
        self.admissible = admissible
        return (
            [observation],
            [1.0 if won else 0.0],
            [won],
            {
                "admissible_commands": [admissible],
                "won": [won],
                "goal_condition_success_rate": [gc],
            },
        )


def test_runner_uses_generic_runtime_and_marks_success_on_env_won(tmp_path: Path) -> None:
    transport = FakeTransport()
    adapter = AlfworldEnvAdapter(
        env=FakeBatchEnv(),
        episode_prefix="fake",
        seed=42,
    )
    config = AlfworldBenchmarkConfig(
        alfworld_root=tmp_path / "alfworld",
        alfworld_config=tmp_path / "base_config.yaml",
        trace_root=tmp_path / "traces",
        episodes=1,
        max_tool_iterations=10,
    )
    runner = AlfworldBenchmarkRunner(
        config=config,
        transport_factory=lambda: transport,
        adapter_factory=lambda _config: adapter,
    )

    summary = runner.run()

    assert summary.success_rate == 1.0
    assert summary.episodes[0].success is True
    assert summary.episodes[0].steps == 3
    assert transport.call_count == 3
    assert "robot_navigate" in {tool["name"] for tool in transport.seen_tools[0]}
    trace_text = summary.episodes[0].trace_path.read_text(encoding="utf-8")
    assert "move apple 1 to diningtable 1" in trace_text
    assert "admissible_commands" not in trace_text
```

- [ ] **Step 2: Run runner test and verify it fails**

Run:

```bash
cd /hpc2hdd/home/wyuan140/weilin_workspace/Homemaster
. .venv/bin/activate
pytest tests/homemaster/benchmarking/test_alfworld_runner.py -q
```

Expected: FAIL because `runner.py` does not exist.

- [ ] **Step 3: Implement runner**

Create `src/homemaster/benchmarking/alfworld/runner.py`:

```python
"""Runner for HomeMaster ALFWorld benchmark episodes."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from typing import Any

from homemaster.agent.generic_runtime import (
    GenericAgentRuntime,
    RuntimeStopDecision,
    ToolSpec as RuntimeToolSpec,
)
from homemaster.agent.normalized import RunContext
from homemaster.agent.session import AgentSession
from homemaster.benchmarking.alfworld.env_adapter import (
    AlfworldEnvAdapter,
    build_alfworld_batch_env,
)
from homemaster.benchmarking.alfworld.prompt import build_episode_prompt
from homemaster.benchmarking.alfworld.registry import build_alfworld_tool_registry
from homemaster.benchmarking.alfworld.tracing import AlfworldTraceWriter
from homemaster.benchmarking.alfworld.translator import create_translator
from homemaster.benchmarking.alfworld.types import (
    AlfworldBenchmarkConfig,
    AlfworldEpisodeResult,
    AlfworldSummary,
)
from homemaster.config.runtime_settings import RuntimeSettings
from homemaster.events.sinks import JsonlEventSink
from homemaster.providers.mimo_transport import MimoTransport
from homemaster.providers.transport import LLMTransport
from homemaster.runtime import DEFAULT_CONFIG_PATH, load_provider_config
from homemaster.tools.dispatcher import ToolDispatcher


TransportFactory = Callable[[], LLMTransport]
AdapterFactory = Callable[[AlfworldBenchmarkConfig], AlfworldEnvAdapter]


class AlfworldBenchmarkRunner:
    def __init__(
        self,
        *,
        config: AlfworldBenchmarkConfig,
        transport_factory: TransportFactory | None = None,
        adapter_factory: AdapterFactory | None = None,
    ) -> None:
        self.config = config
        self._transport_factory = transport_factory or self._build_transport
        self._adapter_factory = adapter_factory or self._build_adapter
        self.run_id = config.run_id or uuid.uuid4().hex[:12]

    def run(self) -> AlfworldSummary:
        self.config.trace_root.mkdir(parents=True, exist_ok=True)
        episodes: list[AlfworldEpisodeResult] = []
        adapter = self._adapter_factory(self.config)
        for episode_index in range(self.config.episodes):
            episodes.append(self._run_episode(adapter, episode_index))
        summary = AlfworldSummary(run_id=self.run_id, episodes=episodes)
        summary_path = self.config.trace_root / self.run_id / "summary.json"
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(
            json.dumps(summary.to_dict(), ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return summary

    def _run_episode(
        self,
        adapter: AlfworldEnvAdapter,
        episode_index: int,
    ) -> AlfworldEpisodeResult:
        state = adapter.reset()
        episode_run_id = f"{self.run_id}-{episode_index + 1:04d}"
        episode_dir = self.config.trace_root / self.run_id / f"episode-{episode_index + 1:04d}"
        trace = AlfworldTraceWriter(episode_dir)
        runtime_sink = JsonlEventSink(episode_dir / "runtime")
        translator = create_translator(self.config.env_type)
        registry = build_alfworld_tool_registry(memory_mode=self.config.memory_mode)
        dispatcher = ToolDispatcher()
        tool_specs: list[RuntimeToolSpec] = []
        for name in registry.all_names():
            spec = registry.get(name)
            if spec is None:
                continue
            dispatcher.register(spec)
            if spec.selectable_by_model:
                tool_specs.append(RuntimeToolSpec(
                    name=spec.name,
                    description=spec.description,
                    input_schema=spec.input_schema,
                ))

        settings = RuntimeSettings(
            run_id=episode_run_id,
            runtime_root=self.config.trace_root / self.run_id / "runtime",
            debug_root=self.config.trace_root / self.run_id / "debug",
            results_root=self.config.trace_root / self.run_id / "results",
            config_path=self.config.provider_config,
        )
        run_context = RunContext(
            session_id=episode_run_id,
            run_id=episode_run_id,
            turn_index=0,
            settings=settings,
            event_sink=runtime_sink,
            deps={
                "alfworld_env": adapter,
                "alfworld_translator": translator,
                "alfworld_trace": trace,
                "alfworld_config": self.config,
            },
        )
        dispatcher.set_run_context(run_context)
        prompt = build_episode_prompt(
            state=state,
            translator=translator,
            memory_mode=self.config.memory_mode,
            max_invalid_actions=self.config.max_invalid_actions,
        )

        runtime = GenericAgentRuntime(
            transport=self._transport_factory(),
            tool_executor=dispatcher,
            max_tool_iterations=self.config.max_tool_iterations,
            stop_condition=self._stop_condition(adapter),
        )
        result = runtime.run(
            AgentSession(session_id=episode_run_id),
            prompt,
            tools=tool_specs,
            event_sink=runtime_sink,
            run_id=episode_run_id,
            settings=settings,
        )

        final_state = adapter.current_state
        success = final_state.won
        failure_reason = None if success else _episode_failure_reason(result.error_code, final_state.done)
        episode_result = AlfworldEpisodeResult(
            episode_id=final_state.episode_id,
            success=success,
            failure_reason=failure_reason,
            steps=final_state.step_index,
            invalid_actions=final_state.invalid_action_count,
            goal_condition_success_rate=final_state.goal_condition_success_rate,
            runtime_status=result.status,
            run_id=episode_run_id,
            trace_path=trace.trace_path,
        )
        trace.write_summary({
            "episode_id": episode_result.episode_id,
            "success": episode_result.success,
            "failure_reason": episode_result.failure_reason,
            "steps": episode_result.steps,
            "invalid_actions": episode_result.invalid_actions,
            "goal_condition_success_rate": episode_result.goal_condition_success_rate,
            "runtime_status": episode_result.runtime_status,
            "run_id": episode_result.run_id,
        })
        return episode_result

    def _stop_condition(self, adapter: AlfworldEnvAdapter):
        def decide(session: AgentSession, tool_results: list[Any]) -> RuntimeStopDecision | None:
            state = adapter.current_state
            if state.won:
                return RuntimeStopDecision(
                    status="replied",
                    final_reply="Environment reports won=true.",
                    payload={"reason": "alfworld_won"},
                )
            if state.invalid_action_count >= self.config.max_invalid_actions:
                return RuntimeStopDecision(
                    status="failed",
                    error_code="benchmark_invalid_action_limit",
                    payload={"reason": "invalid action limit reached"},
                )
            if state.done and not state.won:
                return RuntimeStopDecision(
                    status="failed",
                    error_code="benchmark_done_without_won",
                    payload={"reason": "environment ended without won=true"},
                )
            return None

        return decide

    def _build_transport(self) -> LLMTransport:
        path = self.config.provider_config or DEFAULT_CONFIG_PATH
        provider = load_provider_config(path, provider_name=self.config.provider_name)
        return MimoTransport(
            base_url=provider.base_url,
            model=provider.model,
            api_key=provider.api_keys[0],
            protocol=provider.protocol,
        )

    @staticmethod
    def _build_adapter(config: AlfworldBenchmarkConfig) -> AlfworldEnvAdapter:
        env = build_alfworld_batch_env(config)
        return AlfworldEnvAdapter(
            env=env,
            episode_prefix=config.split,
            seed=config.seed,
        )


def _episode_failure_reason(error_code: str | None, done: bool) -> str:
    if error_code:
        return error_code
    if done:
        return "done_without_won"
    return "not_won"
```

- [ ] **Step 4: Add trace writing from tools**

In `src/homemaster/benchmarking/alfworld/tools.py`, add this helper after `_validation_failure`:

```python
def _write_trace(run_context: RunContext, step_result: Any) -> None:
    trace = run_context.deps.get("alfworld_trace")
    if trace is not None:
        trace.write_event(step_result.to_trace_event())
```

Replace `_exec_observe` with:

```python
def _exec_observe(*, arguments: dict[str, Any], run_context: RunContext) -> ToolResult:
    try:
        command = _translator(run_context).observe(
            mode=arguments.get("mode", "look"),
            target=arguments.get("target"),
        )
    except TranslatorValidationError as exc:
        return _validation_failure(
            tool_name="robot_observe",
            arguments=arguments,
            run_context=run_context,
            error=exc,
        )
    step_result = _adapter(run_context).step(
        command,
        tool_name="robot_observe",
        tool_args=arguments,
    )
    _write_trace(run_context, step_result)
    return _result_from_step(step_result)
```

Replace `_exec_navigate` with:

```python
def _exec_navigate(*, arguments: dict[str, Any], run_context: RunContext) -> ToolResult:
    try:
        command = _translator(run_context).navigate(
            target_receptacle=arguments.get("target_receptacle", ""),
        )
    except TranslatorValidationError as exc:
        return _validation_failure(
            tool_name="robot_navigate",
            arguments=arguments,
            run_context=run_context,
            error=exc,
        )
    step_result = _adapter(run_context).step(
        command,
        tool_name="robot_navigate",
        tool_args=arguments,
    )
    _write_trace(run_context, step_result)
    return _result_from_step(step_result)
```

Replace `_exec_manipulate` with:

```python
def _exec_manipulate(*, arguments: dict[str, Any], run_context: RunContext) -> ToolResult:
    try:
        command = _translator(run_context).manipulate(**arguments)
    except TranslatorValidationError as exc:
        return _validation_failure(
            tool_name="robot_manipulate",
            arguments=arguments,
            run_context=run_context,
            error=exc,
        )
    step_result = _adapter(run_context).step(
        command,
        tool_name="robot_manipulate",
        tool_args=arguments,
    )
    _write_trace(run_context, step_result)
    return _result_from_step(step_result)
```

Do not include `admissible_commands` in `to_trace_event()` for the primary trace. Debug traces can be added as a separate field in a future ablation, but not in this first implementation.

- [ ] **Step 5: Run runner and tool tests**

Run:

```bash
cd /hpc2hdd/home/wyuan140/weilin_workspace/Homemaster
. .venv/bin/activate
pytest tests/homemaster/benchmarking/test_alfworld_runner.py \
  tests/homemaster/benchmarking/test_alfworld_tools.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/homemaster/benchmarking/alfworld/runner.py \
  src/homemaster/benchmarking/alfworld/tools.py \
  tests/homemaster/benchmarking/test_alfworld_runner.py
git commit -m "feat: add alfworld benchmark runner"
```

## Task 10: Add CLI Command

**Files:**

- Create: `src/homemaster/cli/benchmark_alfworld.py`
- Modify: `src/homemaster/cli/app.py`
- Create: `tests/homemaster/test_cli_benchmark_alfworld.py`

- [ ] **Step 1: Write CLI tests**

Create `tests/homemaster/test_cli_benchmark_alfworld.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from typer.testing import CliRunner

from homemaster.cli.app import app


@dataclass(frozen=True)
class FakeSummary:
    run_id: str = "run-1"

    @property
    def success_rate(self) -> float:
        return 1.0

    def to_dict(self):
        return {
            "run_id": self.run_id,
            "episode_count": 1,
            "success_rate": 1.0,
            "average_steps": 3.0,
            "total_invalid_actions": 0,
        }


def test_benchmark_alfworld_cli_invokes_handler(monkeypatch, tmp_path: Path) -> None:
    captured = {}

    def fake_handle(**kwargs):
        captured.update(kwargs)
        return FakeSummary()

    monkeypatch.setattr("homemaster.cli.benchmark_alfworld.handle_benchmark_alfworld", fake_handle)

    result = CliRunner().invoke(app, [
        "benchmark-alfworld",
        "--alfworld-root", str(tmp_path / "alfworld"),
        "--alfworld-config", str(tmp_path / "base_config.yaml"),
        "--trace-root", str(tmp_path / "traces"),
        "--episodes", "1",
        "--memory-mode", "disabled",
        "--max-invalid-actions", "100",
        "--max-tool-iterations", "150",
    ])

    assert result.exit_code == 0
    assert captured["episodes"] == 1
    assert captured["memory_mode"] == "disabled"
    assert "success_rate: 1.000" in result.stdout


def test_benchmark_alfworld_help_exposes_key_options() -> None:
    result = CliRunner().invoke(app, ["benchmark-alfworld", "--help"])

    assert result.exit_code == 0
    assert "--alfworld-root" in result.stdout
    assert "--max-invalid-actions" in result.stdout
    assert "--memory-mode" in result.stdout
```

- [ ] **Step 2: Run CLI tests and verify they fail**

Run:

```bash
cd /hpc2hdd/home/wyuan140/weilin_workspace/Homemaster
. .venv/bin/activate
pytest tests/homemaster/test_cli_benchmark_alfworld.py -q
```

Expected: FAIL because the CLI command does not exist.

- [ ] **Step 3: Implement CLI handler**

Create `src/homemaster/cli/benchmark_alfworld.py`:

```python
"""CLI handler for ALFWorld benchmark runs."""

from __future__ import annotations

from pathlib import Path

from homemaster.benchmarking.alfworld.runner import AlfworldBenchmarkRunner
from homemaster.benchmarking.alfworld.types import AlfworldBenchmarkConfig, AlfworldSummary
from homemaster.logger import setup_logging


def handle_benchmark_alfworld(
    *,
    alfworld_root: Path,
    alfworld_config: Path,
    trace_root: Path,
    env_type: str = "AlfredTWEnv",
    split: str = "valid_seen",
    episodes: int = 1,
    memory_mode: str = "disabled",
    max_invalid_actions: int = 100,
    max_tool_iterations: int = 150,
    provider_config: Path | None = None,
    provider_name: str = "Mimo",
    run_id: str | None = None,
    log_level: str = "INFO",
) -> AlfworldSummary:
    setup_logging(level=log_level)
    config = AlfworldBenchmarkConfig(
        alfworld_root=alfworld_root,
        alfworld_config=alfworld_config,
        trace_root=trace_root,
        env_type=env_type,  # type: ignore[arg-type]
        split=split,  # type: ignore[arg-type]
        episodes=episodes,
        memory_mode=memory_mode,  # type: ignore[arg-type]
        max_invalid_actions=max_invalid_actions,
        max_tool_iterations=max_tool_iterations,
        provider_config=provider_config,
        provider_name=provider_name,
        run_id=run_id,
    )
    return AlfworldBenchmarkRunner(config=config).run()
```

- [ ] **Step 4: Wire Typer command**

In `src/homemaster/cli/app.py`, add import:

```python
from homemaster.cli.benchmark_alfworld import handle_benchmark_alfworld
```

Add this command before `if __name__ == "__main__":`:

```python
@app.command("benchmark-alfworld")
def benchmark_alfworld_command(
    alfworld_root: Annotated[
        Path,
        typer.Option("--alfworld-root", help="Path to the local ALFWorld repository."),
    ],
    alfworld_config: Annotated[
        Path,
        typer.Option("--alfworld-config", help="Path to ALFWorld YAML config."),
    ],
    trace_root: Annotated[
        Path,
        typer.Option("--trace-root", help="Output directory for benchmark traces."),
    ] = Path("/tmp/homemaster/alfworld"),
    env_type: Annotated[
        str,
        typer.Option("--env-type", help="ALFWorld environment type."),
    ] = "AlfredTWEnv",
    split: Annotated[
        str,
        typer.Option("--split", help="train, valid_seen, or valid_unseen."),
    ] = "valid_seen",
    episodes: Annotated[
        int,
        typer.Option("--episodes", help="Number of episodes to run."),
    ] = 1,
    memory_mode: Annotated[
        str,
        typer.Option("--memory-mode", help="disabled, readonly, or full."),
    ] = "disabled",
    max_invalid_actions: Annotated[
        int,
        typer.Option("--max-invalid-actions", help="Fail an episode after this many invalid actions."),
    ] = 100,
    max_tool_iterations: Annotated[
        int,
        typer.Option("--max-tool-iterations", help="Maximum HomeMaster tool iterations per episode."),
    ] = 150,
    provider_config: Annotated[
        Path | None,
        typer.Option("--api-config", help="Optional provider config JSON override."),
    ] = None,
    provider_name: Annotated[
        str,
        typer.Option("--provider-name", help="Provider name from the API config."),
    ] = "Mimo",
    run_id: Annotated[
        str | None,
        typer.Option("--run-id", help="Stable benchmark run id."),
    ] = None,
    log_level: Annotated[
        str,
        typer.Option("--log-level", help="Logging level."),
    ] = "INFO",
) -> None:
    """Run HomeMaster on ALFWorld benchmark episodes."""
    try:
        summary = handle_benchmark_alfworld(
            alfworld_root=alfworld_root,
            alfworld_config=alfworld_config,
            trace_root=trace_root,
            env_type=env_type,
            split=split,
            episodes=episodes,
            memory_mode=memory_mode,
            max_invalid_actions=max_invalid_actions,
            max_tool_iterations=max_tool_iterations,
            provider_config=provider_config,
            provider_name=provider_name,
            run_id=run_id,
            log_level=log_level,
        )
        typer.echo(f"run_id: {summary.run_id}")
        typer.echo(f"episodes: {len(summary.episodes)}")
        typer.echo(f"success_rate: {summary.success_rate:.3f}")
        typer.echo(f"trace_root: {trace_root / summary.run_id}")
    except Exception as exc:
        render_error_and_exit(exc)
```

- [ ] **Step 5: Run CLI tests**

Run:

```bash
cd /hpc2hdd/home/wyuan140/weilin_workspace/Homemaster
. .venv/bin/activate
pytest tests/homemaster/test_cli_benchmark_alfworld.py tests/homemaster/test_cli_help.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/homemaster/cli/app.py \
  src/homemaster/cli/benchmark_alfworld.py \
  tests/homemaster/test_cli_benchmark_alfworld.py
git commit -m "feat: add alfworld benchmark cli"
```

## Task 11: Add Live ALFWorld Smoke Test

**Files:**

- Create: `tests/homemaster/test_alfworld_live_smoke.py`

- [ ] **Step 1: Write live smoke test gated by environment variables**

Create `tests/homemaster/test_alfworld_live_smoke.py`:

```python
from __future__ import annotations

import os
from pathlib import Path

import pytest

from homemaster.benchmarking.alfworld.env_adapter import (
    AlfworldEnvAdapter,
    build_alfworld_batch_env,
)
from homemaster.benchmarking.alfworld.types import AlfworldBenchmarkConfig


@pytest.mark.live_alfworld
def test_live_alfworld_textworld_reset_and_look(tmp_path: Path) -> None:
    root = os.environ.get("HOMEMASTER_ALFWORLD_ROOT")
    config_path = os.environ.get("HOMEMASTER_ALFWORLD_CONFIG")
    if not root or not config_path:
        pytest.skip("set HOMEMASTER_ALFWORLD_ROOT and HOMEMASTER_ALFWORLD_CONFIG")

    config = AlfworldBenchmarkConfig(
        alfworld_root=Path(root),
        alfworld_config=Path(config_path),
        trace_root=tmp_path / "traces",
        env_type="AlfredTWEnv",
        split=os.environ.get("HOMEMASTER_ALFWORLD_SPLIT", "valid_seen"),  # type: ignore[arg-type]
        episodes=1,
    )
    env = build_alfworld_batch_env(config)
    adapter = AlfworldEnvAdapter(env=env, episode_prefix=config.split, seed=42)
    state = adapter.reset()

    assert state.observation
    assert state.frame_path is None
    result = adapter.step("look", tool_name="robot_observe", tool_args={"mode": "look"})
    assert result.state.step_index == 1
    assert result.state.observation
```

- [ ] **Step 2: Run normal suite and verify live test is skipped by default**

Run:

```bash
cd /hpc2hdd/home/wyuan140/weilin_workspace/Homemaster
. .venv/bin/activate
pytest tests/homemaster/test_alfworld_live_smoke.py -q
```

Expected: SKIPPED unless `HOMEMASTER_ALFWORLD_ROOT` and `HOMEMASTER_ALFWORLD_CONFIG` are set.

- [ ] **Step 3: Run live smoke on HPC2 TextWorld environment**

Use the Python environment that can import both HomeMaster and ALFWorld. On HPC2, prefer the ALFWorld environment, then install HomeMaster editable into it:

```bash
ssh HPC2_Outside
cd /hpc2hdd/home/wyuan140/weilin_workspace
. alfworld/alfworld_env/bin/activate
python -m pip install -e "Homemaster[dev,alfworld]"
export HOMEMASTER_ALFWORLD_ROOT=/hpc2hdd/home/wyuan140/weilin_workspace/alfworld
export HOMEMASTER_ALFWORLD_CONFIG=/hpc2hdd/home/wyuan140/weilin_workspace/alfworld/configs/base_config.yaml
cd Homemaster
pytest tests/homemaster/test_alfworld_live_smoke.py -m live_alfworld -q
```

Expected: PASS with `AlfredTWEnv` reset and `look`.

- [ ] **Step 4: Commit**

```bash
git add tests/homemaster/test_alfworld_live_smoke.py
git commit -m "test: add live alfworld textworld smoke"
```

## Task 12: Run One Live Benchmark Episode

**Files:**

- No code files.
- Uses CLI and trace outputs.

- [ ] **Step 1: Ensure provider config uses verified Mimo config**

Use the existing verified config path:

```bash
/hpc2hdd/home/wyuan140/weilin_workspace/Homemaster/config/api_config.json
```

Do not print the API key. The benchmark trace redaction should also protect keys if any config payload accidentally enters trace data.

- [ ] **Step 2: Run one TextWorld episode**

Run:

```bash
ssh HPC2_Outside
cd /hpc2hdd/home/wyuan140/weilin_workspace
. alfworld/alfworld_env/bin/activate
python -m pip install -e "Homemaster[dev,alfworld]"
export PYTHONPATH=/hpc2hdd/home/wyuan140/weilin_workspace/alfworld:$PYTHONPATH
cd Homemaster
python -m homemaster.cli benchmark-alfworld \
  --alfworld-root /hpc2hdd/home/wyuan140/weilin_workspace/alfworld \
  --alfworld-config /hpc2hdd/home/wyuan140/weilin_workspace/alfworld/configs/base_config.yaml \
  --env-type AlfredTWEnv \
  --split valid_seen \
  --episodes 1 \
  --memory-mode disabled \
  --max-invalid-actions 100 \
  --max-tool-iterations 150 \
  --trace-root /tmp/homemaster/alfworld \
  --api-config /hpc2hdd/home/wyuan140/weilin_workspace/Homemaster/config/api_config.json \
  --provider-name Mimo
```

Expected stdout shape:

```text
run_id: <id>
episodes: 1
success_rate: <0.000 or 1.000>
trace_root: /tmp/homemaster/alfworld/<id>
```

Expected files:

```text
/tmp/homemaster/alfworld/<id>/summary.json
/tmp/homemaster/alfworld/<id>/episode-0001/trace.jsonl
/tmp/homemaster/alfworld/<id>/episode-0001/summary.json
```

- [ ] **Step 3: Inspect trace for required behavior**

Run:

```bash
python - <<'PY'
from pathlib import Path
import json

root = sorted(Path('/tmp/homemaster/alfworld').iterdir())[-1]
trace = root / 'episode-0001' / 'trace.jsonl'
print(root)
for line in trace.read_text(encoding='utf-8').splitlines()[:5]:
    payload = json.loads(line)
    assert 'admissible_commands' not in payload
    assert 'api_key' not in payload
    assert 'auth_token' not in payload
    print(payload.get('tool_name'), payload.get('translated_command'), payload.get('won'))
PY
```

Expected: The script prints the latest run directory and several tool steps without assertion failure.

- [ ] **Step 4: Commit live-run documentation if a README note is added**

If a short usage note is added, place it at:

```text
docs/superpowers/specs/2026-06-09-alfworld-benchmark-design.md
```

Commit command:

```bash
git add docs/superpowers/specs/2026-06-09-alfworld-benchmark-design.md
git commit -m "docs: document alfworld benchmark smoke command"
```

If no documentation file changes are made, do not create a commit for this task.

## Task 13: Full Verification

**Files:**

- All files touched by previous tasks.

- [ ] **Step 1: Run focused benchmark tests**

Run:

```bash
cd /hpc2hdd/home/wyuan140/weilin_workspace/Homemaster
. .venv/bin/activate
pytest tests/homemaster/benchmarking \
  tests/homemaster/test_cli_benchmark_alfworld.py \
  tests/homemaster/test_generic_agent_runtime.py \
  tests/homemaster/test_tool_dispatcher.py -q
```

Expected: PASS.

- [ ] **Step 2: Run lint**

Run:

```bash
cd /hpc2hdd/home/wyuan140/weilin_workspace/Homemaster
. .venv/bin/activate
ruff check src/homemaster tests/homemaster
```

Expected: PASS.

- [ ] **Step 3: Run full test suite**

Run:

```bash
cd /hpc2hdd/home/wyuan140/weilin_workspace/Homemaster
. .venv/bin/activate
pytest -q
```

Expected: PASS or only live tests skipped. Any failure in existing unrelated tests must be inspected before merging.

- [ ] **Step 4: Verify git diff boundaries**

Run:

```bash
git status --short
git diff --stat
git diff -- src/homemaster/agent/generic_runtime.py \
  src/homemaster/tools/dispatcher.py \
  src/homemaster/benchmarking/alfworld \
  src/homemaster/cli \
  tests/homemaster
```

Expected: Diff is limited to benchmark integration, generic stop hook, dispatcher data preservation, CLI command, tests, and pyproject metadata.

## Engineering Notes

- The model-visible context update after each ALFWorld step is the `ToolResultMessage` appended by `GenericAgentRuntime`. No separate benchmark loop should append custom messages outside the runtime.
- Successful navigation does not require special context injection. `robot_navigate` returns latest observation, feedback, `done`, `won`, and progress in the tool result; the next model call sees that tool message.
- Failed ALFWorld commands use the same path. The failed tool result includes observation and feedback, but does not include `admissible_commands`.
- `admissible_commands` may be used internally by the adapter to count invalid actions. That use is hidden from the model.
- `AlfredThorEnv` visual support should enter by adding a THOR adapter behind the same adapter interface and setting `frame_path` to saved frames. Do not change `GenericAgentRuntime` for visual mode.

## Self-Review Checklist

- Spec coverage:
  - HomeMaster runtime owns the loop: Task 9 calls `GenericAgentRuntime`.
  - ALFWorld-backed tools: Task 6.
  - Memory disabled by omission: Task 7.
  - Failed actions recover through tool result context: Tasks 1, 4, and 6.
  - `env_state.won` is success: Tasks 6 and 9.
  - Invalid action threshold: Tasks 1 and 9.
  - Visual-compatible fields: Task 2 and Task 4 use `frame_path=None`.
  - CLI/config driven: Task 10.
  - Trace output without secrets: Task 5 and Task 9.
- Placeholder scan:
  - No `TBD`, no `TODO`, and no unspecified file paths.
- Type consistency:
  - `AlfworldBenchmarkConfig`, `AlfworldEnvState`, `AlfworldStepResult`, `AlfworldEpisodeResult`, and `AlfworldSummary` are defined before use.
  - `create_translator("AlfredTWEnv")` is used consistently.
  - `RunContext.deps["alfworld_env"]` and `RunContext.deps["alfworld_translator"]` are the only benchmark dependencies required by tool executors.
