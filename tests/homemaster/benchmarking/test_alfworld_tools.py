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
            goal_condition_success_rate=(
                1.0 if command == "move apple 1 to diningtable 1" else 0.0
            ),
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
    assert result.data["observation"] == "You are in the kitchen."
    assert "admissible_commands" not in result.data["tool_args"]


def test_manipulate_tool_schema_describes_high_level_state_change_actions() -> None:
    spec = make_alfworld_robot_manipulate()

    assert "do not decompose" in spec.description
    assert "heat/cool/clean are abstract state-change actions" in (
        spec.input_schema["properties"]["action"]["description"]
    )
    assert "microwave for heat" in (
        spec.input_schema["properties"]["tool_receptacle"]["description"]
    )
    assert "fridge for cool" in (
        spec.input_schema["properties"]["tool_receptacle"]["description"]
    )
    assert "sinkbasin for clean" in (
        spec.input_schema["properties"]["tool_receptacle"]["description"]
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


def test_observe_inventory_uses_inventory_command() -> None:
    adapter = FakeAdapter()
    observe = make_alfworld_robot_observe()
    observe.executor(arguments={"mode": "inventory"}, run_context=_context(adapter))
    assert adapter.commands == ["inventory"]
