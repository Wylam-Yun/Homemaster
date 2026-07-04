from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from homemaster.agent.messages import AssistantMessage, ContentBlock, Message, ToolCall, UserMessage
from homemaster.benchmarking.alfworld.env_adapter import AlfworldEnvAdapter
from homemaster.benchmarking.alfworld.runner import AlfworldBenchmarkRunner
from homemaster.benchmarking.alfworld.types import AlfworldBenchmarkConfig
from homemaster.providers.transports import TransportDelta


class FakeTransport:
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
        self.seen_system_prompts: list[str] = []
        self.seen_messages: list[list[Message]] = []

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
        self.seen_tools.append(tools or [])
        self.seen_system_prompts.append(system_prompt)
        self.seen_messages.append(messages)
        msg = self._responses[self.call_count]
        self.call_count += 1
        for block in msg.content:
            yield TransportDelta(type="transport.delta", text_delta=block.text)
        for tool_call in msg.tool_calls:
            yield TransportDelta(type="transport.delta", tool_call_delta=tool_call)
        yield TransportDelta(type="transport.delta", finish_reason=msg.finish_reason)


class RepeatingNavigateTransport:
    def __init__(self) -> None:
        self.call_count = 0

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
        self.call_count += 1
        yield TransportDelta(
            type="transport.delta",
            tool_call_delta=ToolCall(
                id=f"nav_{self.call_count}",
                name="robot_navigate",
                arguments={"target_receptacle": "countertop 1"},
            ),
        )
        yield TransportDelta(type="transport.delta", finish_reason="tool_calls")


class FakeBatchEnv:
    def __init__(self) -> None:
        self.admissible = ["look", "go to countertop 1"]
        self.seed_value: int | None = None

    def seed(self, seed: int) -> None:
        self.seed_value = seed

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
        observation, admissible, won, goal_rate = transitions[command]
        self.admissible = admissible
        return (
            [observation],
            [1.0 if won else 0.0],
            [won],
            {
                "admissible_commands": [admissible],
                "won": [won],
                "goal_condition_success_rate": [goal_rate],
            },
        )


class NeverDoneLookEnv:
    def __init__(self) -> None:
        self.step_count = 0

    def seed(self, seed: int) -> None:
        pass

    def reset(self):
        return (
            ["Your task is to: go to countertop 1."],
            {
                "extra.gamefile": ["/games/look_at_obj_in_light/task/game.tw-pddl"],
                "admissible_commands": [["go to countertop 1"]],
                "won": [False],
                "goal_condition_success_rate": [0.0],
            },
        )

    def step(self, actions: list[str]):
        self.step_count += 1
        assert actions == ["go to countertop 1"]
        return (
            [f"You arrive at countertop 1 step {self.step_count}."],
            [0.0],
            [False],
            {
                "admissible_commands": [["go to countertop 1"]],
                "won": [False],
                "goal_condition_success_rate": [0.0],
            },
        )


def _provider_config(tmp_path: Path) -> Path:
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
                "base_url": "https://mimo.example/anthropic",
                "model": "mimo-v2.5",
                "api_keys": ["test-key"],
                "context_window_tokens": null,
                "max_output_tokens": null
              }
            ]
          }
        }
        """,
        encoding="utf-8",
    )
    return path


def test_runner_uses_generic_runtime_and_marks_success_on_env_won(
    tmp_path: Path,
) -> None:
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
        provider_config=_provider_config(tmp_path),
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
    assert transport.seen_system_prompts[0]
    assert any(
        isinstance(message, UserMessage)
        and any("runtime_budget_status" in block.text for block in message.content)
        for message in transport.seen_messages[0]
    )
    assert "robot_navigate" in {tool["name"] for tool in transport.seen_tools[0]}
    assert "robot_inspect_view" in {tool["name"] for tool in transport.seen_tools[0]}
    assert "robot_observe" not in {tool["name"] for tool in transport.seen_tools[0]}
    assert "task_planner" in {tool["name"] for tool in transport.seen_tools[0]}
    assert "task_progress_check" in {tool["name"] for tool in transport.seen_tools[0]}
    run_dir = tmp_path / "traces" / "valid" / summary.run_id
    assert summary.episodes[0].trace_path == run_dir / "episode-0001" / "trace.jsonl"
    assert (run_dir / "episode-0001" / "model_trace.jsonl").exists()
    assert (run_dir / "episode-0001" / "trajectory.md").exists()
    assert (run_dir / "readable_trajectories.md").exists()
    assert summary.config["trace_bucket"] == "valid"
    assert summary.config["provider_name"] == "mimo_v25"
    trace_text = summary.episodes[0].trace_path.read_text(encoding="utf-8")
    assert "move apple 1 to diningtable 1" in trace_text
    assert "admissible_commands" not in trace_text
    model_trace_lines = (run_dir / "episode-0001" / "model_trace.jsonl").read_text(
        encoding="utf-8"
    ).splitlines()
    episode_started = json.loads(model_trace_lines[0])
    assert episode_started["state"] == {
        "episode_id": "pick_and_place/task",
        "frame_path": None,
        "task": "Your task is to: put apple on diningtable 1.",
    }
    assert "observation" not in episode_started["state"]
    assert "goal_condition_success_rate" not in episode_started["state"]


def test_runner_stops_at_environment_step_limit(tmp_path: Path) -> None:
    transport = RepeatingNavigateTransport()
    fake_env = NeverDoneLookEnv()
    adapter = AlfworldEnvAdapter(
        env=fake_env,
        episode_prefix="fake",
        seed=42,
    )
    config = AlfworldBenchmarkConfig(
        alfworld_root=tmp_path / "alfworld",
        alfworld_config=tmp_path / "base_config.yaml",
        trace_root=tmp_path / "traces",
        episodes=1,
        max_env_steps=2,
        max_tool_iterations=300,
        provider_config=_provider_config(tmp_path),
    )
    runner = AlfworldBenchmarkRunner(
        config=config,
        transport_factory=lambda: transport,
        adapter_factory=lambda _config: adapter,
    )

    summary = runner.run()

    assert summary.success_rate == 0.0
    assert summary.episodes[0].success is False
    assert summary.episodes[0].steps == 2
    assert summary.episodes[0].failure_reason == "benchmark_env_step_limit"
    assert fake_env.step_count == 2
    assert transport.call_count == 2
