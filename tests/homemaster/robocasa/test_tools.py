"""RoboCasa tool executor + registration tests."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from homemaster.adapters.profiles import build_tool_registry
from homemaster.agent.normalized import RunContext
from homemaster.application.composition.profiles import resolve_tool_environment
from homemaster.application.tool_executor import _bind_backend
from homemaster.robocasa.types import EpisodeOutcome, RoboCasaEnvState
from homemaster.robocasa.worker_client import RoboCasaWorkerError
from homemaster.tools.contracts import (
    ConcurrencyPolicy,
    PermissionSubject,
    ToolExecutionContext,
    ToolExecutionStatus,
)


class FakeAdapter:
    backend_id = "fake:1"

    def __init__(self) -> None:
        self.state = RoboCasaEnvState(
            task_name="OpenDrawer",
            split="target",
            seed=1,
            task_language="Open the left drawer.",
            success=False,
        )
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.raise_on_invoke = False

    @property
    def current_state(self) -> RoboCasaEnvState:
        return self.state

    def observe(self, *, cameras: list[str] | None = None) -> dict[str, Any]:
        self.calls.append(("observe", {"cameras": cameras}))
        return {"state": self.state.to_model_visible_dict(), "images": []}

    def invoke(self, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        if self.raise_on_invoke:
            raise RoboCasaWorkerError("worker exploded mid-op")
        self.calls.append((operation, payload))
        return {
            "primitive": operation,
            "result": {"status": "ok"},
            "errored": False,
            "step_index": len(self.calls),
            "state": self.state.to_model_visible_dict(),
        }

    def check_success(self) -> dict[str, Any]:
        return {
            "success": self.state.success,
            "task_progress": {},
            "state": self.state.to_model_visible_dict(),
        }

    def image_artifacts(self, result: dict[str, Any]) -> list[dict[str, Any]]:
        return []


class FakeTrace:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def write_event(self, event: dict[str, Any]) -> str:
        self.events.append(event)
        return f"trace#sha256={'0' * 64}"


def _run_context(adapter: FakeAdapter, trace: FakeTrace | None = None) -> RunContext:
    outcome = EpisodeOutcome()
    return RunContext(
        session_id="s1",
        run_id="r1",
        turn_index=0,
        settings=SimpleNamespace(
            run_id="r1",
            runtime_root=Path("/tmp/runs"),
            debug_root=Path("/tmp/debug"),
            results_root=Path("/tmp/results"),
        ),
        event_sink=None,
        deps={
            "robocasa_env": adapter,
            "robocasa_trace": trace,
            "robocasa_episode_outcome": outcome,
        },
    )


def _execute(tool: Any, arguments: dict[str, Any], run_context: RunContext) -> Any:
    context = ToolExecutionContext(
        session_id=run_context.session_id,
        run_id=run_context.run_id,
        turn_index=run_context.turn_index,
        tool_call_id="tool-call-1",
        internal_tool_id=str(tool.definition.internal_id),
        permission_subject=PermissionSubject(subject_id="test", channel="test"),
        backend=run_context.deps.get("robocasa_env"),
        deadline=None,
        cancellation=None,
        domain_observer=None,
        working_directory=Path("/tmp"),
        services={"run_context": run_context},
    )
    return asyncio.run(tool.executor.execute(arguments, context))


def _tool(name: str) -> Any:
    from homemaster.robocasa.tools import build_robocasa_tools

    for candidate in build_robocasa_tools():
        if candidate.definition.model_alias == name:
            return candidate
    raise KeyError(name)


def test_skill_routes_to_adapter_and_records_trace():
    adapter, trace = FakeAdapter(), FakeTrace()
    result = _execute(_tool("robocasa_skill"), {}, _run_context(adapter, trace))
    assert result.status is ToolExecutionStatus.SUCCESS
    assert adapter.calls == [("skill", {})]
    assert result.backend_attempted is True
    assert result.evidence_refs, "mutating tools must produce evidence refs"
    assert trace.events[0]["operation"] == "skill"


def test_navigate_builds_xy_payload():
    adapter = FakeAdapter()
    result = _execute(
        _tool("robocasa_navigate"), {"x": 1.5, "y": -2.0}, _run_context(adapter)
    )
    assert result.status is ToolExecutionStatus.SUCCESS
    assert adapter.calls == [("navigate", {"xy": [1.5, -2.0]})]


def test_invalid_args_return_invalid():
    adapter = FakeAdapter()
    result = _execute(
        _tool("robocasa_navigate"), {"x": "east"}, _run_context(adapter)
    )
    assert result.status is ToolExecutionStatus.INVALID
    assert adapter.calls == []


def test_worker_error_is_outcome_unknown():
    adapter = FakeAdapter()
    adapter.raise_on_invoke = True
    run_context = _run_context(adapter)
    result = _execute(_tool("robocasa_skill"), {}, run_context)
    assert result.status is ToolExecutionStatus.OUTCOME_UNKNOWN
    assert result.backend_attempted is True
    # Transport-level (no response frame) → episode marked infra-terminal.
    outcome = run_context.deps["robocasa_episode_outcome"]
    assert outcome.terminal is True
    assert outcome.classification == "infrastructure_error"
    assert outcome.score_eligible is False


class _RejectedAdapter(FakeAdapter):
    """Simulates a worker-side pre-dispatch rejection (env untouched)."""

    def invoke(self, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        from homemaster.robocasa.worker_protocol import WorkerResponse

        frame = WorkerResponse(
            request_id="r1",
            status="error",
            external_return_code=64,
            backend_attempted=False,
            result={},
            error={"code": "invalid_payload", "message": "payload must be an object"},
        )
        raise RoboCasaWorkerError("payload must be an object", response=frame)


def test_predispatch_rejection_is_invalid_not_outcome_unknown():
    adapter = _RejectedAdapter()
    run_context = _run_context(adapter)
    result = _execute(_tool("robocasa_skill"), {}, run_context)
    assert result.status is ToolExecutionStatus.INVALID
    assert result.backend_attempted is False
    outcome = run_context.deps["robocasa_episode_outcome"]
    assert outcome.terminal is False


def test_verify_reads_environment_success():
    adapter = FakeAdapter()
    adapter.state = RoboCasaEnvState(
        task_name="t", split="target", seed=1, task_language="t", success=True
    )
    result = _execute(_tool("robocasa_verify"), {}, _run_context(adapter))
    assert result.status is ToolExecutionStatus.SUCCESS
    assert result.data["success"] is True


def test_registry_contains_all_robocasa_tools():
    registry = build_tool_registry(environment="robocasa", memory_enabled=False)
    expected = {
        "robocasa_observe",
        "robocasa_skill",
        "robocasa_arm",
        "robocasa_navigate",
        "robocasa_move_to",
        "robocasa_move_delta",
        "robocasa_rotate_pitch",
        "robocasa_set_gripper",
        "robocasa_release",
        "robocasa_scripted_grasp",
        "robocasa_move_base",
        "robocasa_back_project",
        "robocasa_world_map",
        "robocasa_verify",
    }
    assert expected <= set(registry.all_names())
    # mutating tools serialize on the shared backend resource
    skill = _tool("robocasa_skill")
    assert skill.definition.concurrency_policy is ConcurrencyPolicy.RESOURCE_KEY
    assert skill.definition.resource_key == "robocasa:backend"
    observe = _tool("robocasa_observe")
    assert observe.definition.concurrency_policy is ConcurrencyPolicy.PARALLEL


def _names(registry: Any) -> set[str]:
    return set(registry.all_names())


def test_memory_mode_gating():
    disabled = build_tool_registry(
        environment="robocasa", memory_mode="disabled", memory_enabled=False
    )
    readonly = build_tool_registry(
        environment="robocasa", memory_mode="readonly", memory_enabled=True
    )
    full = build_tool_registry(
        environment="robocasa", memory_mode="full", memory_enabled=True
    )

    assert "memory_retriever" not in _names(disabled)
    assert "memory_retriever" in _names(readonly)
    assert "mindmemos_add" not in _names(readonly)
    assert "memory_writer" not in _names(readonly)
    assert "memory_writer" in _names(full)


def test_resolve_tool_environment_passes_robocasa_through_browser_config():
    from homemaster.config import HomeMasterConfig

    config = HomeMasterConfig.model_validate(
        {
            "browser_gateway": {
                "start_url": "https://example.test",
                "allowed_origins": ["https://example.test"],
            }
        }
    )
    assert resolve_tool_environment(config, "robocasa") == "robocasa"
    assert resolve_tool_environment(config, "alfworld") == "alfworld"
    assert resolve_tool_environment(config, "local_robot") == "browser"


def test_task_progress_check_requires_external_owner_under_robocasa():
    registry = build_tool_registry(environment="robocasa", memory_enabled=False)
    tool = registry.get("task_progress_check")
    assert tool is not None
    # FunctionTool flag → completion_requires_external_owner in the run driver
    assert tool.external_terminal_owner is True


def test_bind_backend_registers_robocasa_env():
    deps: dict[str, object] = {}
    adapter = FakeAdapter()
    _bind_backend(deps, "robocasa", adapter)
    assert deps["backend"] is adapter
    assert deps["robocasa_env"] is adapter


def test_compose_tool_registry_threads_memory_mode():
    from homemaster.application.composition.tools import compose_tool_registry

    readonly = compose_tool_registry(
        environment="robocasa",
        memory_path=None,
        runtime_memory_root=Path("/tmp/mem"),
        memory_enabled=True,
        memory_tier="full",
        memory_mode="readonly",
    )
    names = set(readonly.all_names())
    assert "memory_retriever" in names
    assert "mindmemos_add" not in names
