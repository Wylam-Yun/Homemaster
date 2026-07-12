from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from homemaster.agent.messages import ToolCall
from homemaster.agent.normalized import RunContext
from homemaster.benchmarking.alfworld.tools import (
    _visual_tool_result,
    _write_trace,
    make_alfworld_robot_find_object,
    make_alfworld_robot_go_to,
    make_alfworld_robot_manipulate,
    make_alfworld_robot_navigate,
    make_alfworld_robot_verify,
)
from homemaster.benchmarking.alfworld.translator import create_translator
from homemaster.benchmarking.alfworld.types import (
    AlfworldBenchmarkConfig,
    AlfworldEnvState,
    AlfworldStepResult,
    Subtask,
)
from homemaster.tools.dispatcher import ToolDispatcher
from homemaster.tools.results import ToolResult
from homemaster.tools.spec import ToolSpec


class FakeAdapter:
    def __init__(self) -> None:
        self.commands: list[str] = []
        self.state = AlfworldEnvState(
            episode_id="game-1",
            task="put apple on table",
            observation="On the sofa 1, you see a remotecontrol 1. You see a sofa 1.",
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
            admissible_commands=(
                "go to sofa 1",
                "take remotecontrol 1 from sofa 1",
                "move apple 1 to diningtable 1",
            ),
        )

    @property
    def current_state(self) -> AlfworldEnvState:
        return self.state

    def step(self, command: str, *, tool_name: str, tool_args: dict[str, Any]):
        self.commands.append(command)
        invalid = "bad target" in command
        self.state = AlfworldEnvState(
            episode_id="game-1",
            task="put apple on table",
            observation="Nothing happens." if invalid else f"after {command}",
            inventory=None,
            last_command=command,
            last_feedback="Nothing happens." if invalid else f"after {command}",
            reward=0.0,
            done=False,
            won=command == "move apple 1 to diningtable 1",
            goal_condition_success_rate=(
                1.0 if command == "move apple 1 to diningtable 1" else 0.0
            ),
            frame_path=None,
            step_index=self.state.step_index + 1,
            invalid_action_count=self.state.invalid_action_count + (1 if invalid else 0),
            admissible_commands=self.state.admissible_commands,
        )
        return AlfworldStepResult(
            tool_name=tool_name,
            tool_args=tool_args,
            translated_command=command,
            success=not invalid,
            failure_reason="invalid_action" if invalid else None,
            state=self.state,
            feedback="Nothing happens." if invalid else f"after {command}",
        )

    def virtual_navigate(
        self,
        target: str,
        *,
        tool_name: str,
        tool_args: dict[str, Any],
    ):
        self.state = AlfworldEnvState(
            episode_id="game-1",
            task="put apple on table",
            observation=f"virtual navigation to {target}",
            inventory=None,
            last_command=f"virtual go to {target}",
            last_feedback=f"virtual navigation to {target}",
            reward=0.0,
            done=False,
            won=False,
            goal_condition_success_rate=0.0,
            frame_path=None,
            step_index=self.state.step_index + 1,
            invalid_action_count=self.state.invalid_action_count,
            admissible_commands=self.state.admissible_commands,
        )
        return AlfworldStepResult(
            tool_name=tool_name,
            tool_args=tool_args,
            translated_command=f"virtual go to {target}",
            success=True,
            failure_reason=None,
            state=self.state,
            feedback=f"virtual navigation to {target}",
        )

    def find_object(
        self,
        target: str,
        *,
        tool_name: str,
        tool_args: dict[str, Any],
    ):
        self.state = AlfworldEnvState(
            episode_id="game-1",
            task="put apple on table",
            observation=f"Found {target} at sofa 1.",
            inventory=None,
            last_command=f"find object {target}",
            last_feedback=f"Found {target} at sofa 1.",
            reward=0.0,
            done=False,
            won=False,
            goal_condition_success_rate=0.0,
            frame_path=None,
            step_index=self.state.step_index + 1,
            invalid_action_count=self.state.invalid_action_count,
            admissible_commands=self.state.admissible_commands,
        )
        args = dict(tool_args)
        args.update(
            {
                "object": target,
                "object_label": "remotecontrol 1",
                "source_receptacle": "sofa 1",
            }
        )
        return AlfworldStepResult(
            tool_name=tool_name,
            tool_args=args,
            translated_command=f"find object {target} -> go to sofa 1",
            success=True,
            failure_reason=None,
            state=self.state,
            feedback=f"Found {target} at sofa 1.",
        )

    def manipulate_with_thor(
        self,
        *,
        action: str,
        tool_name: str,
        tool_args: dict[str, Any],
    ):
        self.state = AlfworldEnvState(
            episode_id="game-1",
            task="put apple on table",
            observation=f"thor {action}",
            inventory="You are carrying: remotecontrol.",
            last_command=f"thor {action}",
            last_feedback=f"thor {action}",
            reward=0.0,
            done=False,
            won=False,
            goal_condition_success_rate=0.0,
            frame_path=None,
            step_index=self.state.step_index + 1,
            invalid_action_count=self.state.invalid_action_count,
            admissible_commands=self.state.admissible_commands,
        )
        args = dict(tool_args)
        args.update(
            {
                "backend": "thor_api",
                "backend_actions": ["PickupObject"],
                "object_resolution_object_id": "RemoteControl|0",
                "object_resolution_object_type": "remotecontrol",
            }
        )
        return AlfworldStepResult(
            tool_name=tool_name,
            tool_args=args,
            translated_command=f"thor {action}",
            success=True,
            failure_reason=None,
            state=self.state,
            feedback=f"thor {action}",
        )

    def go_to_target(
        self,
        target: str,
        *,
        tool_name: str,
        tool_args: dict[str, Any],
    ):
        self.state = AlfworldEnvState(
            episode_id="game-1",
            task="put apple on table",
            observation=f"Reached {target}.",
            inventory=None,
            last_command=f"go to target {target}",
            last_feedback=f"Reached {target}.",
            reward=0.0,
            done=False,
            won=False,
            goal_condition_success_rate=0.0,
            frame_path=None,
            step_index=self.state.step_index + 1,
            invalid_action_count=self.state.invalid_action_count,
            admissible_commands=self.state.admissible_commands,
        )
        args = dict(tool_args)
        args.update(
            {
                "target": target,
                "resolved_kind": "movable_object",
                "resolved_label": "remotecontrol 1",
                "object_label": "remotecontrol 1",
                "source_receptacle": "sofa 1",
            }
        )
        return AlfworldStepResult(
            tool_name=tool_name,
            tool_args=args,
            translated_command=f"go to target {target}",
            success=True,
            failure_reason=None,
            state=self.state,
            feedback=f"Reached {target}.",
        )


def _context(
    adapter: FakeAdapter, *, observation_mode: str = "textual_debug", env_type: str = "AlfredTWEnv"
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
            "alfworld_translator": create_translator("AlfredTWEnv"),
            "alfworld_config": AlfworldBenchmarkConfig(
                alfworld_root=Path("/tmp/alfworld"),
                alfworld_config=Path("/tmp/base_config.yaml"),
                trace_root=Path("/tmp/traces"),
                observation_mode=observation_mode,
                env_type=env_type,  # type: ignore[arg-type]
            ),
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


def test_write_trace_flushes_internal_events_before_model_safe_tool_step() -> None:
    trace = SimpleNamespace(events=[])
    context = _context(FakeAdapter())
    context.deps["alfworld_trace"] = SimpleNamespace(
        write_event=lambda event: trace.events.append(event)
    )
    step_result = AlfworldStepResult(
        tool_name="robot_go_to",
        tool_args={
            "target": "shelf 1",
            "object_id": "Shelf|1",
            "nested": {
                "raw_event_ref": "raw/navigation.json",
                "requested_pose": {"x": 1.0},
            },
        },
        translated_command="go to target shelf 1",
        success=True,
        failure_reason=None,
        state=FakeAdapter().state,
        trace_events=(
            {
                "event": "move_result",
                "object_id": "Shelf|1",
                "raw_event_ref": "raw/navigation.json",
                "requested_pose": {"x": 1.0},
            },
            {"event": "execution_terminal", "classification": "success"},
        ),
    )

    _write_trace(context, step_result)

    assert [event.get("event") for event in trace.events] == [
        "move_result",
        "execution_terminal",
        None,
    ]
    visible = step_result.to_model_visible_data()
    encoded = json.dumps(visible, ensure_ascii=False, sort_keys=True)
    assert "Shelf|1" not in encoded
    assert "raw/navigation.json" not in encoded
    assert "requested_pose" not in encoded
    assert "move_result" not in encoded
    assert trace.events[0]["object_id"] == "Shelf|1"


def test_navigate_tool_schema_is_for_places_not_objects() -> None:
    spec = make_alfworld_robot_navigate()

    assert spec.selectable_by_model is False
    assert "known place" in spec.description
    assert (
        "Movable target object"
        not in (spec.input_schema["properties"]["target_receptacle"]["description"])
    )


def test_go_to_tool_uses_unified_target_backend() -> None:
    adapter = FakeAdapter()
    spec = make_alfworld_robot_go_to()
    result = spec.executor(
        arguments={"target": "remote control"},
        run_context=_context(adapter),
    )

    assert result.success is True
    assert adapter.commands == []
    assert result.data["tool_name"] == "robot_go_to"
    assert result.data["tool_args"]["target"] == "remotecontrol 1"
    assert result.data["tool_args"]["resolved_kind"] == "movable_object"
    assert result.data["tool_args"]["resolved_label"] == "remotecontrol 1"
    assert result.data["tool_args"]["object_label"] == "remotecontrol 1"
    assert result.data["tool_args"]["source_receptacle"] == "sofa 1"


def test_visual_eval_go_to_exposes_target_payload() -> None:
    adapter = FakeAdapter()
    spec = make_alfworld_robot_go_to()
    result = spec.executor(
        arguments={"target": "remote control"},
        run_context=_context(adapter, observation_mode="visual_eval"),
    )

    assert result.is_error is False
    text = "\n".join(block.text for block in result.content if block.text)
    assert json.loads(text) == {
        "success": True,
        "target": {
            "target": "remotecontrol 1",
            "resolved_kind": "movable_object",
            "resolved_label": "remotecontrol 1",
            "object_label": "remotecontrol 1",
            "source_receptacle": "sofa 1",
        },
    }


def test_find_object_tool_returns_canonical_label_and_source() -> None:
    adapter = FakeAdapter()
    spec = make_alfworld_robot_find_object()
    result = spec.executor(
        arguments={"object": "remote control"},
        run_context=_context(adapter),
    )

    assert result.success is True
    assert spec.selectable_by_model is False
    assert adapter.commands == []
    assert result.data["tool_name"] == "robot_find_object"
    assert result.data["tool_args"]["object"] == "remotecontrol 1"
    assert result.data["tool_args"]["object_label"] == "remotecontrol 1"
    assert result.data["tool_args"]["source_receptacle"] == "sofa 1"
    assert result.data["translated_command"] == ("find object remotecontrol 1 -> go to sofa 1")


def test_visual_eval_find_object_exposes_found_label_and_source() -> None:
    adapter = FakeAdapter()
    spec = make_alfworld_robot_find_object()
    result = spec.executor(
        arguments={"object": "remote control"},
        run_context=_context(adapter, observation_mode="visual_eval"),
    )

    assert result.is_error is False
    text = "\n".join(block.text for block in result.content if block.text)
    assert json.loads(text) == {
        "found_object": {
            "object": "remotecontrol 1",
            "object_label": "remotecontrol 1",
            "source_receptacle": "sofa 1",
        },
        "success": True,
    }


def test_tool_results_filter_admissible_commands_from_tool_args() -> None:
    adapter = FakeAdapter()
    spec = make_alfworld_robot_navigate()
    result = spec.executor(
        arguments={
            "target_receptacle": "countertop 1",
            "admissible_commands": ["look"],
            "nested": {"admissible_commands": ["inventory"]},
        },
        run_context=_context(adapter),
    )

    assert result.success is True
    assert result.data["tool_args"] == {
        "target_receptacle": "countertop 1",
        "nested": {},
        "grounding": {
            "target_receptacle": {
                "kind": None,
                "matched_label": None,
                "method": "unchanged",
            },
        },
    }


def test_manipulate_validation_error_does_not_step_env() -> None:
    adapter = FakeAdapter()
    spec = make_alfworld_robot_manipulate()
    result = spec.executor(
        arguments={
            "action": "take",
            "object": "apple 1",
            "admissible_commands": ["look"],
        },
        run_context=_context(adapter),
    )

    assert result.success is False
    assert result.failure_reason == "translator_validation_error"
    assert adapter.commands == []
    assert result.data["step_index"] == 0
    assert "admissible_commands" not in result.data["tool_args"]


def test_manipulate_tool_schema_describes_high_level_state_change_actions() -> None:
    spec = make_alfworld_robot_manipulate()

    assert "do not decompose" in spec.description
    assert (
        "heat/cool/clean are abstract state-change actions"
        in (spec.input_schema["properties"]["action"]["description"])
    )
    assert (
        "microwave for heat" in (spec.input_schema["properties"]["tool_receptacle"]["description"])
    )
    assert "fridge for cool" in (spec.input_schema["properties"]["tool_receptacle"]["description"])
    assert (
        "sinkbasin for clean" in (spec.input_schema["properties"]["tool_receptacle"]["description"])
    )


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


def test_visual_eval_tool_result_hides_textual_feedback_and_scores() -> None:
    adapter = FakeAdapter()
    spec = make_alfworld_robot_navigate()

    result = spec.executor(
        arguments={"target_receptacle": "countertop 1"},
        run_context=_context(adapter, observation_mode="visual_eval"),
    )

    assert result.is_error is False
    assert result.data is not None
    assert result.data["observation"] == "after go to countertop 1"
    text = "\n".join(block.text for block in result.content if block.text)
    assert json.loads(text) == {"success": True}
    assert "observation" not in text
    assert "feedback" not in text
    assert "goal_condition_success_rate" not in text


def test_visual_eval_validation_error_returns_minimal_error() -> None:
    adapter = FakeAdapter()
    spec = make_alfworld_robot_manipulate()

    result = spec.executor(
        arguments={"action": "take", "object": "apple 1"},
        run_context=_context(adapter, observation_mode="visual_eval"),
    )

    assert result.is_error is True
    text = "\n".join(block.text for block in result.content if block.text)
    assert json.loads(text) == {"error": "invalid_tool_arguments", "success": False}
    assert "source_receptacle is required" not in text


def test_visual_eval_action_failure_is_model_feedback_not_runtime_error() -> None:
    adapter = FakeAdapter()
    spec = make_alfworld_robot_navigate()

    result = spec.executor(
        arguments={"target_receptacle": "bad target"},
        run_context=_context(adapter, observation_mode="visual_eval"),
    )

    assert result.is_error is False
    assert result.data is not None
    assert result.data["failure_reason"] == "invalid_action"
    text = "\n".join(block.text for block in result.content if block.text)
    assert json.loads(text) == {"error": "action_failed", "success": False}
    assert "Nothing happens." not in text


def test_manipulate_grounds_natural_object_name_to_visible_instance() -> None:
    adapter = FakeAdapter()
    spec = make_alfworld_robot_manipulate()

    result = spec.executor(
        arguments={
            "action": "take",
            "object": "remote control",
            "source_receptacle": "sofa",
        },
        run_context=_context(adapter),
    )

    assert result.success is True
    assert adapter.commands == ["take remotecontrol 1 from sofa 1"]
    assert result.data["tool_args"]["object"] == "remotecontrol 1"
    assert result.data["tool_args"]["source_receptacle"] == "sofa 1"


def test_manipulate_uses_thor_backend_for_thor_env() -> None:
    adapter = FakeAdapter()
    spec = make_alfworld_robot_manipulate()

    result = spec.executor(
        arguments={
            "action": "take",
            "object": "remote control",
        },
        run_context=_context(adapter, env_type="AlfredThorEnv"),
    )

    assert result.success is True
    assert adapter.commands == []
    assert result.data["translated_command"] == "thor take"
    assert result.data["tool_args"]["backend"] == "thor_api"
    assert "backend_actions" not in result.data["tool_args"]
    assert "object_resolution_object_id" not in result.data["tool_args"]


def test_navigate_to_current_toggle_target_uses_virtual_navigation() -> None:
    adapter = FakeAdapter()
    spec = make_alfworld_robot_navigate()
    context = _context(adapter)
    context.deps["alfworld_current_subtask"] = Subtask(
        goal_type="look_at_obj_in_light",
        object="RemoteControl",
        toggle="FloorLamp",
        instruction="check remote under the floor lamp",
    )

    result = spec.executor(
        arguments={"target_receptacle": "floor lamp 1"},
        run_context=context,
    )

    assert result.success is True
    assert adapter.commands == []
    assert result.data["translated_command"] == "virtual go to floorlamp"


def test_take_current_subtask_object_does_not_use_expert_force_pickup() -> None:
    adapter = FakeAdapter()
    spec = make_alfworld_robot_manipulate()
    context = _context(adapter)
    context.deps["alfworld_current_subtask"] = Subtask(
        goal_type="look_at_obj_in_light",
        object="RemoteControl",
        toggle="FloorLamp",
        instruction="check remote under the floor lamp",
    )

    result = spec.executor(
        arguments={
            "action": "take",
            "object": "remote control",
            "source_receptacle": "sofa 1",
        },
        run_context=context,
    )

    assert result.success is True
    assert adapter.commands == ["take remotecontrol 1 from sofa 1"]
    assert result.data["translated_command"] == "take remotecontrol 1 from sofa 1"


def test_visual_eval_verify_failure_returns_not_complete() -> None:
    adapter = FakeAdapter()
    spec = make_alfworld_robot_verify()

    result = spec.executor(
        arguments={},
        run_context=_context(adapter, observation_mode="visual_eval"),
    )

    assert result.is_error is False
    text = "\n".join(block.text for block in result.content if block.text)
    assert json.loads(text) == {"error": "not_complete", "success": False}


def _visible_json(result: Any) -> dict[str, Any]:
    text = "\n".join(block.text for block in result.content if block.text)
    return json.loads(text)


def _put_projection_data(
    *,
    inventory: list[str],
    object_state: str,
    state_changed: bool,
    detail: Any = None,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "action": "put",
        "object": "pencil 1",
        "target": "shelf 1",
        "inventory": inventory,
        "object_state": object_state,
        "state_changed": state_changed,
        "detail": detail,
        "tool_args": {
            "action": "put",
            "object": "pencil 1",
            "target_receptacle": "shelf 1",
        },
        **extra,
    }


@pytest.mark.parametrize(
    (
        "success",
        "failure_reason",
        "inventory",
        "object_state",
        "state_changed",
        "detail",
        "expected",
    ),
    [
        (
            True,
            None,
            [],
            "placed",
            True,
            None,
            {
                "success": True,
                "action": "put",
                "object": "pencil 1",
                "target": "shelf 1",
                "inventory": [],
                "object_state": "placed",
                "state_changed": True,
            },
        ),
        (
            False,
            "object_not_held",
            [],
            "not_held",
            False,
            "No object is currently held.",
            {
                "success": False,
                "action": "put",
                "object": "pencil 1",
                "target": "shelf 1",
                "inventory": [],
                "object_state": "not_held",
                "state_changed": False,
                "error": "object_not_held",
                "detail": "No object is currently held.",
            },
        ),
        (
            False,
            "navigation_required",
            ["pencil 1"],
            "held",
            False,
            "A current observation of shelf 1 is required.",
            {
                "success": False,
                "action": "put",
                "object": "pencil 1",
                "target": "shelf 1",
                "inventory": ["pencil 1"],
                "object_state": "held",
                "state_changed": False,
                "error": "navigation_required",
                "detail": "A current observation of shelf 1 is required.",
            },
        ),
        (
            False,
            "placement_failed",
            ["pencil 1"],
            "held",
            False,
            "No valid Receptacle found",
            {
                "success": False,
                "action": "put",
                "object": "pencil 1",
                "target": "shelf 1",
                "inventory": ["pencil 1"],
                "object_state": "held",
                "state_changed": False,
                "error": "placement_failed",
                "detail": "No valid Receptacle found",
            },
        ),
        (
            False,
            "harness_operation_failure",
            ["pencil 1"],
            "held",
            False,
            "All locked local poses were exhausted.",
            {
                "success": False,
                "action": "put",
                "object": "pencil 1",
                "target": "shelf 1",
                "inventory": ["pencil 1"],
                "object_state": "held",
                "state_changed": False,
                "error": "harness_operation_failure",
                "detail": "All locked local poses were exhausted.",
            },
        ),
    ],
)
def test_visual_put_feedback_has_exact_state_and_latest_image(
    tmp_path: Path,
    success: bool,
    failure_reason: str | None,
    inventory: list[str],
    object_state: str,
    state_changed: bool,
    detail: str | None,
    expected: dict[str, Any],
) -> None:
    latest_frame = tmp_path / "latest.png"
    latest_frame.write_bytes(b"latest-frame")

    result = _visual_tool_result(
        name="robot_manipulate",
        success=success,
        data=_put_projection_data(
            inventory=inventory,
            object_state=object_state,
            state_changed=state_changed,
            detail=detail,
        ),
        failure_reason=failure_reason,
        frame_path=str(latest_frame),
    )

    assert _visible_json(result) == expected
    images = [block for block in result.content if block.type == "image"]
    assert len(images) == 1
    assert images[0].metadata["path"] == str(latest_frame)


def test_visual_put_detail_is_clean_pass_through_or_stably_redacted() -> None:
    clean = _visual_tool_result(
        name="robot_manipulate",
        success=False,
        data=_put_projection_data(
            inventory=["pencil 1"],
            object_state="held",
            state_changed=False,
            detail="No valid Receptacle found",
        ),
        failure_reason="placement_failed",
        frame_path=None,
    )
    assert _visible_json(clean)["detail"] == "No valid Receptacle found"
    assert "detail_redacted" not in _visible_json(clean)

    tainted = _visual_tool_result(
        name="robot_manipulate",
        success=False,
        data=_put_projection_data(
            inventory=["pencil 1"],
            object_state="held",
            state_changed=False,
            detail=(
                "object Pencil|-01.57|+00.88|+00.83 at (-1.57, 0.88, 0.83); "
                "candidate poses [pose-a, pose-b]; expert target shelf 4"
            ),
        ),
        failure_reason="placement_failed",
        frame_path=None,
    )
    payload = _visible_json(tainted)
    assert payload["detail"] == (
        "object [REDACTED_OBJECT_ID] at [REDACTED_COORDINATES]; "
        "[REDACTED_CANDIDATES]; [REDACTED_EXPERT]"
    )
    assert payload["detail_redacted"] is True


class _ExplodingDetail:
    def __str__(self) -> str:
        raise RuntimeError("detail projection failed")


def test_visual_put_projection_failure_becomes_unclassified_terminal() -> None:
    result = _visual_tool_result(
        name="robot_manipulate",
        success=False,
        data=_put_projection_data(
            inventory=["pencil 1"],
            object_state="held",
            state_changed=False,
            detail=_ExplodingDetail(),
        ),
        failure_reason="placement_failed",
        frame_path=None,
    )

    payload = _visible_json(result)
    assert payload == {
        "success": False,
        "action": "put",
        "object": "pencil 1",
        "target": "shelf 1",
        "inventory": ["pencil 1"],
        "object_state": "held",
        "state_changed": False,
        "error": "unclassified_execution_failure",
        "detail": "Execution detail could not be safely projected.",
        "detail_redacted": True,
    }
    assert result.data is not None
    assert result.data["terminal"] is True
    assert result.data["classification"] == "unclassified_execution_failure"
    assert result.data["score_eligible"] is False


def test_visual_put_payload_recursively_excludes_internal_execution_fields() -> None:
    result = _visual_tool_result(
        name="robot_manipulate",
        success=True,
        data=_put_projection_data(
            inventory=[],
            object_state="placed",
            state_changed=True,
            resolved_object_id="Pencil|-01.57|+00.88|+00.83",
            internal={
                "candidate_pose": {"x": -1.57, "y": 0.88, "z": 0.83},
                "scene_objects": ["Pencil|-01.57|+00.88|+00.83"],
                "expert_answer": "shelf 4",
            },
        ),
        frame_path=None,
    )

    payload = _visible_json(result)
    assert payload == {
        "success": True,
        "action": "put",
        "object": "pencil 1",
        "target": "shelf 1",
        "inventory": [],
        "object_state": "placed",
        "state_changed": True,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    for forbidden in (
        "objectId",
        "resolved_object_id",
        "candidate",
        "scene_objects",
        "expert",
        "-1.57",
    ):
        assert forbidden not in encoded


def test_terminal_outcome_skips_later_robot_tools_in_same_assistant_turn() -> None:
    outcome = SimpleNamespace(
        terminal=False,
        classification=None,
        score_eligible=True,
        terminal_tool_call_id=None,
        agent_tool_call_count=0,
        backend_action_count=0,
    )
    adapter = SimpleNamespace(touches=[], invalid_action_count=0)

    def terminal_executor(*, arguments: dict[str, Any], run_context: RunContext):
        del arguments, run_context
        adapter.touches.append("robot_manipulate")
        outcome.terminal = True
        outcome.classification = "harness_operation_failure"
        outcome.score_eligible = False
        outcome.terminal_tool_call_id = "call_1"
        outcome.agent_tool_call_count += 1
        outcome.backend_action_count += 3
        return ToolResult(
            success=False,
            tool_name="robot_manipulate",
            executor_mode="programmatic",
            data={
                "terminal": True,
                "classification": "harness_operation_failure",
                "score_eligible": False,
            },
            failure_reason="harness_operation_failure",
        )

    def forbidden_executor(*, arguments: dict[str, Any], run_context: RunContext):
        del arguments, run_context
        adapter.touches.append("robot_go_to")
        adapter.invalid_action_count += 1
        outcome.agent_tool_call_count += 1
        outcome.backend_action_count += 1
        return ToolResult(
            success=True,
            tool_name="robot_go_to",
            executor_mode="programmatic",
        )

    dispatcher = ToolDispatcher()
    for name, executor in (
        ("robot_manipulate", terminal_executor),
        ("robot_go_to", forbidden_executor),
    ):
        dispatcher.register(
            ToolSpec(
                name=name,
                description=name,
                executor_mode="programmatic",
                executor=executor,
            )
        )
    context = _context(FakeAdapter())
    context.deps["alfworld_env"] = adapter
    context.deps["alfworld_episode_outcome"] = outcome

    results = dispatcher.dispatch(
        tool_calls=[
            ToolCall(id="call_1", name="robot_manipulate", arguments={}),
            ToolCall(id="call_2", name="robot_go_to", arguments={}),
        ],
        run_context=context,
    )

    assert adapter.touches == ["robot_manipulate"]
    assert adapter.invalid_action_count == 0
    assert outcome.agent_tool_call_count == 1
    assert outcome.backend_action_count == 3
    assert len(results) == 2
    assert results[1].data == {
        "success": False,
        "error": "episode_terminated",
        "terminal": True,
        "classification": "harness_operation_failure",
        "score_eligible": False,
    }
