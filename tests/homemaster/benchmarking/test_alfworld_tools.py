from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from homemaster.agent.normalized import RunContext
from homemaster.alfworld.benchmark.translator import create_translator
from homemaster.alfworld.outcomes import AlfworldExecutionFeedback as HarnessFeedback
from homemaster.alfworld.tools import (
    _write_trace,
    make_alfworld_robot_go_to,
    make_alfworld_robot_manipulate,
    make_alfworld_robot_verify,
)
from homemaster.alfworld.types import (
    AlfworldBenchmarkConfig,
    AlfworldEnvState,
    AlfworldStepResult,
    EpisodeOutcome,
    make_execution_feedback,
)
from homemaster.tools.contracts import PermissionSubject, ToolExecutionContext, ToolExecutionStatus


class FakeAdapter:
    def __init__(self) -> None:
        self.commands: list[str] = []
        self.go_to_calls: list[str] = []
        self.manipulation_calls: list[str] = []
        self.state = AlfworldEnvState(
            episode_id="game-1",
            task="put apple on table",
            observation="You see a remotecontrol 1.",
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
        self.state = _next_state(self.state, command)
        action = "navigate" if tool_name == "robot_go_to" else str(tool_args["action"])
        return AlfworldStepResult(
            tool_name=tool_name,
            tool_args=tool_args,
            translated_command=command,
            success=True,
            state=self.state,
            backend_action_count=1,
            execution_feedback=make_execution_feedback(
                action=action,  # type: ignore[arg-type]
                success=True,
                object_label=str(tool_args.get("object") or "") or None,
                target_label=str(
                    tool_args.get("target") or tool_args.get("target_receptacle") or ""
                )
                or None,
            ),
        )

    def go_to_target(
        self,
        target: str,
        *,
        tool_name: str,
        tool_args: dict[str, Any],
    ) -> AlfworldStepResult:
        self.go_to_calls.append(target)
        self.state = _next_state(self.state, f"go to target {target}")
        return AlfworldStepResult(
            tool_name=tool_name,
            tool_args=tool_args,
            translated_command=f"go to target {target}",
            success=True,
            state=self.state,
            backend_action_count=1,
            execution_feedback=make_execution_feedback(
                action="navigate",
                success=True,
                target_label=target,
                target_state="visible",
                target_state_status="ok",
                state_changed=True,
                state_read_status="ok",
            ),
        )

    def manipulate_with_thor(
        self,
        *,
        action: str,
        tool_name: str,
        tool_args: dict[str, Any],
    ) -> AlfworldStepResult:
        self.manipulation_calls.append(action)
        self.state = _next_state(self.state, f"thor {action}")
        return AlfworldStepResult(
            tool_name=tool_name,
            tool_args=tool_args,
            translated_command=f"thor {action}",
            success=True,
            state=self.state,
            backend_action_count=1,
            execution_feedback=make_execution_feedback(
                action=action,  # type: ignore[arg-type]
                success=True,
                object_label=str(tool_args.get("object") or "") or None,
                inventory=("remotecontrol 1",),
                inventory_status="ok",
                object_state="held",
                object_state_status="ok",
                state_changed=True,
                state_read_status="ok",
            ),
        )


def _next_state(state: AlfworldEnvState, command: str) -> AlfworldEnvState:
    return AlfworldEnvState(
        episode_id=state.episode_id,
        task=state.task,
        observation=f"after {command}",
        inventory=state.inventory,
        last_command=command,
        last_feedback=f"after {command}",
        reward=0.0,
        done=False,
        won=False,
        goal_condition_success_rate=0.0,
        frame_path=state.frame_path,
        step_index=state.step_index + 1,
        invalid_action_count=state.invalid_action_count,
    )


def _context(
    adapter: FakeAdapter,
    *,
    env_type: str = "AlfredThorEnv",
) -> RunContext:
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
            "alfworld_env": adapter,
            "alfworld_translator": create_translator(env_type),
            "alfworld_config": AlfworldBenchmarkConfig(
                alfworld_root=Path("/tmp/alfworld"),
                alfworld_config=Path("/tmp/base.yaml"),
                trace_root=Path("/tmp/traces"),
                env_type=env_type,  # type: ignore[arg-type]
            ),
        },
    )


def _payload(result: Any) -> dict[str, Any]:
    return dict(result.data)


def _execute(tool: Any, arguments: dict[str, Any], run_context: RunContext) -> Any:
    context = ToolExecutionContext(
        session_id=run_context.session_id,
        run_id=run_context.run_id,
        turn_index=run_context.turn_index,
        tool_call_id="tool-call-1",
        internal_tool_id=str(tool.definition.internal_id),
        permission_subject=PermissionSubject(subject_id="test", channel="test"),
        backend=run_context.deps.get("alfworld_env"),
        deadline=None,
        cancellation=None,
        domain_observer=None,
        working_directory=Path("/tmp"),
        services={"run_context": run_context},
    )
    return asyncio.run(tool.executor.execute(arguments, context))


def test_go_to_uses_the_thor_oracle_adapter_boundary() -> None:
    adapter = FakeAdapter()
    result = _execute(make_alfworld_robot_go_to(), {"target": "countertop 1"}, _context(adapter))

    assert result.is_error is False
    assert adapter.commands == []
    assert adapter.go_to_calls == ["countertop 1"]
    assert _payload(result)["action"] == "navigate"
    assert _payload(result)["target"] == "countertop 1"


def test_thor_go_to_uses_oracle_adapter_boundary() -> None:
    adapter = FakeAdapter()
    result = _execute(
        make_alfworld_robot_go_to(),
        {"target": "remote control"},
        _context(adapter, env_type="AlfredThorEnv"),
    )

    assert result.is_error is False
    assert adapter.commands == []
    assert adapter.go_to_calls == ["remotecontrol 1"]
    assert _payload(result)["target"] == "remotecontrol 1"
    assert _payload(result)["target_state"] == "visible"
    assert result.backend_attempted is True
    assert "backend_attempted" not in _payload(result)


def test_typed_payload_is_the_only_model_projection() -> None:
    adapter = FakeAdapter()
    result = _execute(
        make_alfworld_robot_go_to(),
        {
            "target": "remote control",
            "object_id": "RemoteControl|0",
            "requested_pose": {"x": 1.0},
        },
        _context(adapter, env_type="AlfredThorEnv"),
    )

    payload = _payload(result)
    assert payload == {
        key: value for key, value in result.data.items() if key != "backend_attempted"
    }
    assert result.backend_attempted is True
    encoded = json.dumps(payload, sort_keys=True)
    for forbidden in (
        "tool_args",
        "object_id",
        "RemoteControl|0",
        "requested_pose",
        "observation",
        "frame_path",
    ):
        assert forbidden not in encoded


def test_validation_failure_uses_closed_typed_error_and_does_not_step() -> None:
    adapter = FakeAdapter()
    result = _execute(
        make_alfworld_robot_manipulate(),
        {"action": "take", "object": "apple 1"},
        _context(adapter),
    )

    assert adapter.commands == []
    assert result.status is ToolExecutionStatus.INVALID
    assert result.error is not None
    assert result.error.code == "invalid_tool_arguments"
    assert result.backend_attempted is False


def test_thor_manipulation_forwards_exact_typed_feedback() -> None:
    adapter = FakeAdapter()
    result = _execute(
        make_alfworld_robot_manipulate(),
        {"action": "take", "object": "remote control"},
        _context(adapter, env_type="AlfredThorEnv"),
    )

    assert adapter.manipulation_calls == ["take"]
    payload = _payload(result)
    assert payload["action"] == "take"
    assert payload["object"] == "remotecontrol 1"
    assert payload["object_state"] == "held"
    assert payload["inventory"] == ("remotecontrol 1",)


def test_verify_uses_typed_nonterminal_result_until_environment_wins() -> None:
    adapter = FakeAdapter()
    spec = make_alfworld_robot_verify()

    pending = _execute(spec, {}, _context(adapter))
    assert _payload(pending)["error"] == "action_not_applicable"
    assert pending.status is ToolExecutionStatus.FAILURE
    assert pending.error is not None

    adapter.state = AlfworldEnvState(**{**adapter.state.__dict__, "won": True, "done": True})
    completed = _execute(spec, {}, _context(adapter))
    assert _payload(completed)["success"] is True
    assert _payload(completed)["error"] is None


class _FakeHarness:
    def __init__(self, feedback: Any) -> None:
        self.feedback = feedback
        self.requests: list[Any] = []

    def execute(self, request: Any) -> Any:
        self.requests.append(request)
        return self.feedback


class _FakeTrace:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def write_event(self, event: dict[str, Any]) -> str:
        self.events.append(dict(event))
        return "trace.jsonl#sha256=fake"


def _harness_context(
    adapter: FakeAdapter,
    *,
    feedback: Any,
    trace: _FakeTrace | None = None,
    outcome: EpisodeOutcome | None = None,
) -> RunContext:
    context = _context(adapter)
    context.deps["alfworld_harness"] = _FakeHarness(feedback)
    if trace is not None:
        context.deps["alfworld_trace"] = trace
    if outcome is not None:
        context.deps["alfworld_episode_outcome"] = outcome
    return context


def _success_feedback(**overrides: Any) -> HarnessFeedback:
    values: dict[str, Any] = {
        "action": "robot_go_to",
        "success": True,
        "classification": None,
        "external_return_code": 0,
        "backend_attempted": True,
        "terminal": False,
        "won": False,
        "evidence_refs": ("worker:1:state-2",),
        "state_before": {"agent": "a"},
        "state_after": {"agent": "b"},
    }
    values.update(overrides)
    return HarnessFeedback(**values)


def test_harness_go_to_writes_action_trace_and_counts_backend_action() -> None:
    adapter = FakeAdapter()
    trace = _FakeTrace()
    outcome = EpisodeOutcome()
    context = _harness_context(
        adapter,
        feedback=_success_feedback(),
        trace=trace,
        outcome=outcome,
    )

    result = _execute(make_alfworld_robot_go_to(), {"target": "desk 1"}, context)

    assert adapter.go_to_calls == []
    assert len(trace.events) == 1
    event = trace.events[0]
    assert event["action"] == "navigate"
    assert event["tool_name"] == "robot_go_to"
    assert event["tool_args"] == {"target": "desk 1"}
    assert event["success"] is True
    assert event["tool_success"] is True
    assert event["target"] == "desk 1"
    assert event["error"] is None
    assert event["backend_action_count"] == 1
    assert outcome.backend_action_count == 1
    assert result.evidence_refs == ("worker:1:state-2", "trace.jsonl#sha256=fake")


def test_harness_manipulate_trace_uses_canonical_action_and_targets() -> None:
    adapter = FakeAdapter()
    trace = _FakeTrace()
    context = _harness_context(
        adapter,
        feedback=_success_feedback(action="take"),
        trace=trace,
    )
    _execute(
        make_alfworld_robot_manipulate(),
        {"action": "take", "object": "mug 1", "target_receptacle": "desk 1"},
        context,
    )

    assert len(trace.events) == 1
    event = trace.events[0]
    assert event["action"] == "take"
    assert event["tool_name"] == "robot_manipulate"
    assert event["object"] == "mug 1"
    assert event["target"] == "desk 1"


def test_harness_verify_writes_verify_step() -> None:
    adapter = FakeAdapter()
    trace = _FakeTrace()
    context = _harness_context(
        adapter,
        feedback=_success_feedback(action="robot_verify", backend_attempted=False, won=True),
        trace=trace,
    )
    _execute(make_alfworld_robot_verify(), {}, context)

    assert len(trace.events) == 1
    event = trace.events[0]
    assert event["action"] == "verify"
    assert event["won"] is True
    assert event["backend_action_count"] == 0


def test_harness_failure_records_error_and_skips_unattempted_count() -> None:
    adapter = FakeAdapter()
    trace = _FakeTrace()
    outcome = EpisodeOutcome()
    context = _harness_context(
        adapter,
        feedback=_success_feedback(
            success=False,
            classification="harness_operation_failure",
            backend_attempted=False,
            terminal=True,
        ),
        trace=trace,
        outcome=outcome,
    )
    _execute(make_alfworld_robot_go_to(), {"target": "desk 1"}, context)

    assert len(trace.events) == 1
    event = trace.events[0]
    assert event["error"] == "harness_operation_failure"
    assert event["classification"] == "harness_operation_failure"
    assert event["score_eligible"] is False
    assert outcome.backend_action_count == 0


def test_harness_trace_absent_still_counts_outcome_and_keeps_refs() -> None:
    adapter = FakeAdapter()
    outcome = EpisodeOutcome()
    context = _harness_context(adapter, feedback=_success_feedback(), outcome=outcome)

    result = _execute(make_alfworld_robot_go_to(), {"target": "desk 1"}, context)

    assert outcome.backend_action_count == 1
    assert result.evidence_refs == ("worker:1:state-2",)


def test_trace_keeps_internal_evidence_while_model_projection_stays_safe() -> None:
    trace = SimpleNamespace(events=[])
    context = _context(FakeAdapter())
    context.deps["alfworld_trace"] = SimpleNamespace(
        write_event=lambda event: trace.events.append(event)
    )
    step = AlfworldStepResult(
        tool_name="robot_go_to",
        tool_args={"target": "shelf 1", "object_id": "Shelf|1"},
        translated_command="go to target shelf 1",
        success=True,
        state=FakeAdapter().state,
        execution_feedback=make_execution_feedback(
            action="navigate", success=True, target_label="shelf 1"
        ),
        trace_events=({"event": "move_result", "object_id": "Shelf|1"},),
    )

    _write_trace(context, step)

    assert trace.events[0]["object_id"] == "Shelf|1"
    assert "Shelf|1" not in json.dumps(step.to_model_visible_data(), sort_keys=True)
