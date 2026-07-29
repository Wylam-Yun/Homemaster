from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from homemaster.agent.messages import (
    AssistantMessage,
    ContentBlock,
    Message,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)
from homemaster.agent.session import AgentSession
from homemaster.application import RunResult, RunStatus
from homemaster.benchmarking.alfworld import runner as runner_module
from homemaster.benchmarking.alfworld.env_adapter import AlfworldEnvAdapter
from homemaster.benchmarking.alfworld.runner import (
    AlfworldBenchmarkRunner,
    AlfworldTasksetRunner,
    _episode_classification,
)
from homemaster.benchmarking.alfworld.types import (
    AlfworldBenchmarkConfig,
    AlfworldEnvState,
    AlfworldGoalAdvanceResult,
    AlfworldResetResult,
    EpisodeOutcome,
    Subtask,
    Taskset,
    TasksetRunConfig,
)
from homemaster.providers.attempts import (
    OutboundImageBinding,
    ProviderAttemptRecord,
)
from homemaster.providers.transports import TransportDelta


def _record_provider_attempt(
    messages: list[Message],
    tools: list[dict[str, Any]] | None,
    *,
    attempt_sink: Any,
    model_attempt_id: str,
) -> None:
    images: list[OutboundImageBinding] = []
    for message_index, message in enumerate(messages):
        for block_index, block in enumerate(message.content):
            if block.type != "image" or not isinstance(block.source, dict):
                continue
            encoded = block.source.get("data")
            if not isinstance(encoded, str):
                continue
            content_sha256 = hashlib.sha256(base64.b64decode(encoded)).hexdigest()
            images.append(
                OutboundImageBinding(
                    message_index=message_index,
                    block_index=block_index,
                    content_sha256=content_sha256,
                )
            )
    attempt_sink.record_attempt(
        ProviderAttemptRecord(
            model_attempt_id=model_attempt_id,
            request_sha256=hashlib.sha256(repr((messages, tools)).encode()).hexdigest(),
            outbound_images=tuple(images),
            stripped_images=False,
            response_completed=True,
            error_type=None,
            cause_code=None,
        )
    )


class FakeTransport:
    def __init__(self) -> None:
        self._responses = [
            AssistantMessage(
                tool_calls=[ToolCall(id="observe_1", name="observe", arguments={})],
                finish_reason="tool_calls",
            ),
            AssistantMessage(
                tool_calls=[
                    ToolCall(
                        id="call_1",
                        name="robot_go_to",
                        arguments={"target": "countertop 1"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            AssistantMessage(
                tool_calls=[ToolCall(id="observe_2", name="observe", arguments={})],
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
                tool_calls=[ToolCall(id="observe_3", name="observe", arguments={})],
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
                tool_calls=[ToolCall(id="observe_4", name="observe", arguments={})],
                finish_reason="tool_calls",
            ),
        ]
        self.call_count = 0
        self.seen_tools: list[list[dict[str, Any]]] = []
        self.seen_system_prompts: list[str] = []
        self.seen_messages: list[list[Message]] = []

    async def stream(
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
        attempt_sink: Any = None,
        model_attempt_id: str = "attempt",
    ) -> AsyncIterator[TransportDelta]:
        self.seen_tools.append(tools or [])
        self.seen_system_prompts.append(system_prompt)
        self.seen_messages.append(messages)
        if attempt_sink is not None:
            _record_provider_attempt(
                messages,
                tools,
                attempt_sink=attempt_sink,
                model_attempt_id=model_attempt_id,
            )
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

    async def stream(
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
        attempt_sink: Any = None,
        model_attempt_id: str = "attempt",
    ) -> AsyncIterator[TransportDelta]:
        self.call_count += 1
        if attempt_sink is not None:
            _record_provider_attempt(
                messages,
                tools,
                attempt_sink=attempt_sink,
                model_attempt_id=model_attempt_id,
            )
        tool_call = (
            ToolCall(id=f"observe_{self.call_count}", name="observe", arguments={})
            if self.call_count % 2
            else ToolCall(
                id=f"nav_{self.call_count}",
                name="robot_go_to",
                arguments={"target": "countertop 1"},
            )
        )
        yield TransportDelta(type="transport.delta", tool_call_delta=tool_call)
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


def _attach_fake_screenshot(
    adapter: AlfworldEnvAdapter,
    tmp_path: Path,
) -> dict[str, int]:
    image = __import__("PIL.Image", fromlist=["Image"])
    frame_path = tmp_path / "fake-alfworld-frame.png"
    image.new("RGB", (2, 2), (32, 96, 160)).save(frame_path)
    counts = {"screenshot": 0}

    async def screenshot() -> bytes:
        counts["screenshot"] += 1
        return frame_path.read_bytes()

    adapter.screenshot = screenshot  # type: ignore[method-assign]
    return counts


def _taskset_fixture(
    tmp_path: Path,
    *,
    taskset_id: str,
    count: int = 3,
) -> tuple[Path, Taskset]:
    alfworld_root = tmp_path / "alfworld"
    trial_root = alfworld_root / "data" / "json_2.1.1"
    subtasks = []
    for index in range(count):
        trial_path = trial_root / f"trial-{index + 1}" / "traj_data.json"
        trial_path.parent.mkdir(parents=True)
        trial_path.write_text(
            json.dumps(
                {
                    "task_type": "pick_and_place_simple",
                    "pddl_params": {
                        "object_target": "Pencil",
                        "parent_target": "Shelf",
                        "toggle_target": None,
                        "mrecep_target": None,
                        "object_sliced": False,
                    },
                    "scene": {"floor_plan": "FloorPlan1"},
                }
            ),
            encoding="utf-8",
        )
        subtasks.append(
            Subtask(
                goal_type="pick_and_place_simple",
                object="Pencil",
                parent="Shelf",
                instruction=f"put pencil on shelf {index + 1}",
                traj_path=trial_path,
            )
        )
    return alfworld_root, Taskset(
        id=taskset_id,
        floorplan=1,
        subtasks=tuple(subtasks),
    )


def test_build_pinned_adapter_passes_first_trial_path_by_keyword(
    monkeypatch,
    tmp_path: Path,
) -> None:
    observed: dict[str, Any] = {}

    def build_env(
        config: AlfworldBenchmarkConfig,
        *,
        first_trial_path: Path,
    ) -> object:
        observed["config"] = config
        observed["first_trial_path"] = first_trial_path
        return object()

    monkeypatch.setattr(
        runner_module,
        "build_alfworld_batch_env_with_first_trial",
        build_env,
    )
    config = AlfworldBenchmarkConfig(
        alfworld_root=tmp_path / "alfworld",
        alfworld_config=tmp_path / "base_config.yaml",
        trace_root=tmp_path / "traces",
        env_type="AlfredThorEnv",
        provider_config=_provider_config(tmp_path),
    )
    runner = AlfworldBenchmarkRunner(config=config)

    adapter = runner._build_pinned_adapter(  # noqa: SLF001
        SimpleNamespace(trial_id="case-1/traj_data.json")
    )

    assert adapter._require_v18_reset is True  # noqa: SLF001
    assert observed == {
        "config": config,
        "first_trial_path": (
            config.alfworld_root / "data" / "json_2.1.1" / "case-1" / "traj_data.json"
        ),
    }


def test_terminal_harness_navigation_failure_wins_over_runtime_budget_error() -> None:
    outcome = EpisodeOutcome(agent_tool_call_count=20, backend_action_count=1)
    outcome.mark_terminal(
        classification="harness_navigation_failure",
        tool_call_id="call_navigation",
    )

    assert (
        _episode_classification(
            success=False,
            failure_reason="max_tool_iterations_exceeded",
            outcome=outcome,
        )
        == "harness_navigation_failure"
    )
    assert outcome.score_eligible is False


def test_runner_uses_application_runtime_and_marks_success_on_env_won(
    tmp_path: Path,
) -> None:
    transport = FakeTransport()
    adapter = AlfworldEnvAdapter(
        env=FakeBatchEnv(),
        episode_prefix="fake",
        seed=42,
    )
    counts = _attach_fake_screenshot(adapter, tmp_path)
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
    assert transport.call_count == 7
    assert counts["screenshot"] == 4
    assert all(block.type != "image" for block in transport.seen_messages[0][0].content)
    assert any(
        block.type == "image" for message in transport.seen_messages[1] for block in message.content
    )
    assert transport.seen_system_prompts[0]
    assert any(
        isinstance(message, UserMessage)
        and any("runtime_budget_status" in block.text for block in message.content)
        for message in transport.seen_messages[0]
    )
    tool_names = {tool["name"] for tool in transport.seen_tools[0]}
    assert "robot_go_to" in tool_names
    assert "robot_navigate" not in tool_names
    assert "robot_inspect_view" not in tool_names
    assert "robot_observe" not in tool_names
    assert "task_planner" in tool_names
    assert "task_progress_check" in tool_names
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
    model_trace_lines = (
        (run_dir / "episode-0001" / "model_trace.jsonl").read_text(encoding="utf-8").splitlines()
    )
    episode_started = json.loads(model_trace_lines[0])
    assert episode_started["state"] == {
        "episode_id": "pick_and_place/task",
        "frame_path": None,
        "task": "Your task is to: put apple on diningtable 1.",
    }
    assert "observation" not in episode_started["state"]
    assert "goal_condition_success_rate" not in episode_started["state"]
    attempts_path = run_dir / "episode-0001" / "provider_attempts.jsonl"
    attempts = [json.loads(line) for line in attempts_path.read_text().splitlines()]
    assert len(attempts) == transport.call_count
    assert attempts[0]["outbound_images"] == []
    assert len(attempts[1]["outbound_images"]) == 1


def test_consecutive_explicit_observes_send_the_same_current_frame_each_time(
    tmp_path: Path,
) -> None:
    class ConsecutiveObserveTransport(FakeTransport):
        def __init__(self) -> None:
            super().__init__()
            self._responses = [
                AssistantMessage(
                    tool_calls=[ToolCall(id="observe-first", name="observe", arguments={})],
                    finish_reason="tool_calls",
                ),
                AssistantMessage(
                    tool_calls=[ToolCall(id="observe-second", name="observe", arguments={})],
                    finish_reason="tool_calls",
                ),
                AssistantMessage(
                    content=[ContentBlock(text="observed twice")],
                    finish_reason="stop",
                ),
            ]

    transport = ConsecutiveObserveTransport()
    adapter = AlfworldEnvAdapter(env=FakeBatchEnv(), episode_prefix="fake", seed=42)
    counts = _attach_fake_screenshot(adapter, tmp_path)
    runner = AlfworldBenchmarkRunner(
        config=AlfworldBenchmarkConfig(
            alfworld_root=tmp_path / "alfworld",
            alfworld_config=tmp_path / "base_config.yaml",
            trace_root=tmp_path / "traces",
            episodes=1,
            max_tool_iterations=3,
            provider_config=_provider_config(tmp_path),
        ),
        transport_factory=lambda: transport,
        adapter_factory=lambda _config: adapter,
    )

    summary = runner.run()

    attempts_path = (
        tmp_path / "traces" / "valid" / summary.run_id / "episode-0001" / "provider_attempts.jsonl"
    )
    attempts = [json.loads(line) for line in attempts_path.read_text().splitlines()]
    first = attempts[1]["outbound_images"][-1]
    second = attempts[2]["outbound_images"][-1]
    assert counts["screenshot"] == 2
    assert first["content_sha256"] == second["content_sha256"]


def test_same_response_observe_plus_mutation_is_rejected_without_side_effects(
    tmp_path: Path,
) -> None:
    class BatchTransport(FakeTransport):
        def __init__(self) -> None:
            super().__init__()
            self._responses = [
                AssistantMessage(
                    tool_calls=[
                        ToolCall(id="observe-batch", name="observe", arguments={}),
                        ToolCall(
                            id="action-batch",
                            name="robot_go_to",
                            arguments={"target": "countertop 1"},
                        ),
                    ],
                    finish_reason="tool_calls",
                ),
                AssistantMessage(
                    content=[ContentBlock(text="stopped after rejected batch")],
                    finish_reason="stop",
                ),
            ]

    class CountingEnv(FakeBatchEnv):
        def __init__(self) -> None:
            super().__init__()
            self.step_count = 0

        def step(self, actions: list[str]):
            self.step_count += 1
            return super().step(actions)

    transport = BatchTransport()
    env = CountingEnv()
    adapter = AlfworldEnvAdapter(env=env, episode_prefix="fake", seed=42)
    counts = _attach_fake_screenshot(adapter, tmp_path)
    runner = AlfworldBenchmarkRunner(
        config=AlfworldBenchmarkConfig(
            alfworld_root=tmp_path / "alfworld",
            alfworld_config=tmp_path / "base_config.yaml",
            trace_root=tmp_path / "traces",
            episodes=1,
            max_tool_iterations=3,
            provider_config=_provider_config(tmp_path),
        ),
        transport_factory=lambda: transport,
        adapter_factory=lambda _config: adapter,
    )

    summary = runner.run()

    assert summary.episodes[0].success is False
    assert env.step_count == 0
    assert counts["screenshot"] == 0
    action_result = next(
        message
        for message in transport.seen_messages[1]
        if isinstance(message, ToolResultMessage) and message.tool_call_id == "action-batch"
    )
    assert action_result.is_error is True
    assert action_result.data is not None
    assert action_result.data["error_code"] == "model_observation_batch_rejected"


def test_continuous_taskset_shares_session_but_isolates_attempt_and_view_correlation(
    monkeypatch,
    tmp_path: Path,
) -> None:
    initial_state = AlfworldEnvState(
        episode_id="taskset-entry",
        task="put pencil on shelf",
        observation="ready",
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

    class TasksetAdapter:
        backend_id = "alfworld:taskset-entry"

        def __init__(self) -> None:
            self.current_state = initial_state
            self.generation = 0
            self.state_sequence = 0
            self.event_sequence = 0
            self.application_run_id = ""
            self.screenshot_count = 0
            self.close_count = 0

        def bind_application_run(self, run_id: str, generation: int) -> None:
            self.application_run_id = run_id
            self.generation = generation

        def set_frame_dir(self, _path: Path) -> None:
            return None

        def reset(self, *, selection_entry: Any) -> AlfworldResetResult:
            return AlfworldResetResult(
                backend_kind="textworld",
                ready=True,
                state=self.current_state,
                scene_generation=None,
                goal_generation=1,
                scene_reset_fingerprint=None,
                goal_trial_fingerprint=selection_entry.goal_fingerprint,
                snapshot_sha256=None,
                snapshot_ref=None,
                setup_trigger=None,
                setup_failure=None,
                classification=None,
                score_eligible=True,
                setup_backend_action_count=0,
                recovery_status="not_applicable",
                cleanup_status="not_applicable",
                quarantine_required=False,
                environment_disposition="ready",
                evidence_ref=None,
            )

        def advance_goal(
            self,
            _traj_data: dict[str, Any],
            *,
            subtask_label: str,
            selection_entry: Any,
        ) -> AlfworldGoalAdvanceResult:
            del subtask_label
            self.state_sequence += 1
            self.event_sequence += 1
            self.current_state = replace(
                initial_state,
                episode_id="taskset-entry-second",
                task="put second pencil on shelf",
            )
            return AlfworldGoalAdvanceResult(
                backend_kind="textworld",
                ready=True,
                state=self.current_state,
                scene_generation=None,
                goal_generation=2,
                scene_reset_fingerprint=None,
                goal_trial_fingerprint=selection_entry.goal_fingerprint,
                snapshot_sha256=None,
                before_scene_state_sha256=None,
                after_scene_state_sha256=None,
                advance_trigger=None,
                advance_failure=None,
                classification=None,
                score_eligible=True,
                benchmark_control_action_count=1,
                cleanup_status="not_needed",
                quarantine_required=False,
                environment_disposition="ready",
                evidence_ref=None,
            )

        async def screenshot(self) -> bytes:
            self.screenshot_count += 1
            return base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAFklEQVR4nGNUSFjAwMDA"
                "xMDAwMDAAAANKgEkX1CfuAAAAABJRU5ErkJggg=="
            )

        def is_current_goal_satisfied(self) -> bool:
            return self.current_state.won

        def current_goal_condition_success_rate(self) -> float:
            return self.current_state.goal_condition_success_rate

        def close(self) -> None:
            self.close_count += 1

    adapter = TasksetAdapter()

    class TasksetTransport:
        def __init__(self) -> None:
            self.call_count = 0

        async def stream(
            self,
            messages: list[Message],
            tools: list[dict[str, Any]] | None = None,
            *,
            attempt_sink: Any = None,
            model_attempt_id: str = "attempt",
            **_kwargs: Any,
        ) -> AsyncIterator[TransportDelta]:
            if attempt_sink is not None:
                _record_provider_attempt(
                    messages,
                    tools,
                    attempt_sink=attempt_sink,
                    model_attempt_id=model_attempt_id,
                )
            self.call_count += 1
            if self.call_count % 2:
                yield TransportDelta(
                    type="transport.delta",
                    tool_call_delta=ToolCall(
                        id=f"observe-{self.call_count}",
                        name="observe",
                        arguments={},
                    ),
                )
                yield TransportDelta(type="transport.delta", finish_reason="tool_calls")
                return
            adapter.current_state = replace(
                adapter.current_state,
                done=True,
                won=True,
                goal_condition_success_rate=1.0,
            )
            yield TransportDelta(
                type="transport.delta",
                text_delta="goal complete",
                finish_reason="stop",
            )

    transport = TasksetTransport()
    alfworld_root, taskset = _taskset_fixture(
        tmp_path,
        taskset_id="entry-sharing",
        count=2,
    )
    runner = AlfworldTasksetRunner(
        taskset_config=TasksetRunConfig(
            alfworld_root=alfworld_root,
            alfworld_config=tmp_path / "base_config.yaml",
            trace_root=tmp_path / "traces",
            provider_config=_provider_config(tmp_path),
            provider_name="mimo_v25",
            run_id="taskset-entry-sharing",
            tasksets=(taskset,),
        ),
        transport_factory=lambda: transport,
    )
    monkeypatch.setattr(
        runner_module,
        "build_alfworld_batch_env_with_first_trial",
        lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr(runner_module, "AlfworldEnvAdapter", lambda **_kwargs: adapter)

    summary = runner.run()

    result = summary.taskset_results[0]
    assert [subtask.success for subtask in result.subtasks] == [True, True]
    assert adapter.screenshot_count == 2
    assert adapter.close_count == 1
    taskset_dir = runner.run_dir / "taskset-entry-sharing"
    session_dirs = list((taskset_dir / "sessions").iterdir())
    assert len(session_dirs) == 1
    ledgers = []
    for index in (1, 2):
        path = taskset_dir / f"subtask-{index:02d}" / "provider_attempts.jsonl"
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        assert rows
        ledgers.append(rows)
    attempt_ids = [{row["model_attempt_id"] for row in rows} for rows in ledgers]
    image_hashes = [
        {binding["content_sha256"] for row in rows for binding in row["outbound_images"]}
        for rows in ledgers
    ]
    assert attempt_ids[0].isdisjoint(attempt_ids[1])
    assert all(image_hashes)


def test_runner_stops_at_environment_step_limit(tmp_path: Path) -> None:
    transport = RepeatingNavigateTransport()
    fake_env = NeverDoneLookEnv()
    adapter = AlfworldEnvAdapter(
        env=fake_env,
        episode_prefix="fake",
        seed=42,
    )
    _attach_fake_screenshot(adapter, tmp_path)
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
    assert transport.call_count == 5


def test_runner_stops_on_terminal_outcome_before_next_llm_call(tmp_path: Path) -> None:
    terminal_payload = {
        "terminal": True,
        "classification": "harness_operation_failure",
        "score_eligible": False,
    }
    terminal_result = ToolResultMessage(
        tool_call_id="call_terminal",
        name="robot_manipulate",
        content=[ContentBlock(text=json.dumps(terminal_payload))],
        is_error=True,
        data=terminal_payload,
    )
    adapter = SimpleNamespace(
        current_state=AlfworldEnvState(
            episode_id="episode-terminal",
            task="put pencil on shelf",
            observation="",
            inventory="You are carrying: pencil.",
            last_command="put pencil 1 on shelf 1",
            last_feedback="All locked local poses were exhausted.",
            reward=0.0,
            done=False,
            won=False,
            goal_condition_success_rate=0.0,
            frame_path=None,
            step_index=1,
            invalid_action_count=0,
        )
    )
    runner = AlfworldBenchmarkRunner(
        config=AlfworldBenchmarkConfig(
            alfworld_root=tmp_path / "alfworld",
            alfworld_config=tmp_path / "base_config.yaml",
            trace_root=tmp_path / "traces",
        )
    )

    decision = runner._stop_condition(adapter)(
        AgentSession(session_id="episode-terminal"),
        [terminal_result],
    )

    assert decision is not None
    assert decision.status == "failed"
    assert decision.error_code == "harness_operation_failure"
    assert decision.payload["terminal"] is True
    assert decision.payload["classification"] == terminal_payload["classification"]
    assert decision.payload["score_eligible"] is terminal_payload["score_eligible"]


def test_taskset_runner_propagates_terminal_outcome_and_marks_remaining_not_run(
    monkeypatch,
    tmp_path: Path,
) -> None:
    state = AlfworldEnvState(
        episode_id="taskset-terminal",
        task="put pencil on shelf",
        observation="",
        inventory="You are carrying: pencil.",
        last_command="put pencil 1 on shelf 1",
        last_feedback="All locked local poses were exhausted.",
        reward=0.0,
        done=False,
        won=False,
        goal_condition_success_rate=0.0,
        frame_path=None,
        step_index=3,
        invalid_action_count=0,
    )

    class FakeTasksetAdapter:
        def __init__(self) -> None:
            self.current_state = state
            self.reset_calls = 0
            self.advance_goal_calls = 0
            self.close_calls = 0

        def set_frame_dir(self, _path: Path) -> None:
            return None

        def reset(self, *, selection_entry: Any) -> AlfworldResetResult:
            self.reset_calls += 1
            return AlfworldResetResult(
                backend_kind="textworld",
                ready=True,
                state=self.current_state,
                scene_generation=None,
                goal_generation=1,
                scene_reset_fingerprint=None,
                goal_trial_fingerprint=selection_entry.goal_fingerprint,
                snapshot_sha256=None,
                snapshot_ref=None,
                setup_trigger=None,
                setup_failure=None,
                classification=None,
                score_eligible=True,
                setup_backend_action_count=0,
                recovery_status="not_applicable",
                cleanup_status="not_applicable",
                quarantine_required=False,
                environment_disposition="ready",
                evidence_ref=None,
            )

        def advance_goal(self, *_args: Any, **_kwargs: Any) -> AlfworldEnvState:
            self.advance_goal_calls += 1
            raise AssertionError("infrastructure failure must stop before the next subtask")

        def is_current_goal_satisfied(self) -> bool:
            return False

        def current_goal_condition_success_rate(self) -> float:
            return 0.0

        def close(self) -> None:
            self.close_calls += 1

    adapter = FakeTasksetAdapter()
    observed_outcomes: list[EpisodeOutcome] = []

    observed_requests = []

    class SessionManager:
        def __init__(self) -> None:
            self.sessions: dict[str, AgentSession] = {}

        def get(self, session_id: str) -> Any:
            return SimpleNamespace(session=self.sessions[session_id])

    class TerminalApplicationEntry:
        def __init__(self, **_kwargs: Any) -> None:
            self.application = SimpleNamespace(session_manager=SessionManager())

        def run(self, request: Any) -> RunResult:
            observed_requests.append(request)
            assert request.session_id is not None
            self.application.session_manager.sessions.setdefault(
                request.session_id,
                AgentSession(request.session_id),
            )
            outcome = request.dependencies["alfworld_episode_outcome"]
            assert isinstance(outcome, EpisodeOutcome)
            observed_outcomes.append(outcome)
            outcome.agent_tool_call_count = 2
            outcome.backend_action_count = 5
            outcome.mark_terminal(
                classification="harness_operation_failure",
                tool_call_id="call_terminal",
                evidence_ref="attempts.jsonl#execution_terminal",
            )
            return RunResult(
                run_id="application-run",
                session_id=request.session_id,
                status=RunStatus.FAILED,
                error_code="generic_runtime_failure",
            )

        def close(self) -> None:
            return None

    monkeypatch.setattr(
        runner_module,
        "AlfworldApplicationEntry",
        TerminalApplicationEntry,
    )
    monkeypatch.setattr(
        runner_module,
        "build_alfworld_batch_env_with_first_trial",
        lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr(
        runner_module,
        "AlfworldEnvAdapter",
        lambda **_kwargs: adapter,
    )
    monkeypatch.setattr(
        runner_module,
        "build_episode_prompt",
        lambda **_kwargs: "taskset prompt",
    )
    alfworld_root, taskset = _taskset_fixture(
        tmp_path,
        taskset_id="infra-terminal",
    )
    runner = AlfworldTasksetRunner(
        taskset_config=TasksetRunConfig(
            alfworld_root=alfworld_root,
            alfworld_config=tmp_path / "base_config.yaml",
            trace_root=tmp_path / "traces",
            provider_config=_provider_config(tmp_path),
            provider_name="mimo_v25",
            run_id="taskset-run",
            tasksets=(taskset,),
        ),
        transport_factory=lambda: object(),
    )

    summary = runner.run()
    result = summary.taskset_results[0]
    payload = result.to_dict()
    persisted = json.loads((runner.run_dir / "summary.json").read_text(encoding="utf-8"))

    assert len(observed_outcomes) == 1
    assert len(observed_requests) == 1
    assert observed_requests[0].continuous_taskset is True
    assert observed_requests[0].environment is adapter
    assert adapter.reset_calls == 1
    assert adapter.advance_goal_calls == 0
    assert adapter.close_calls == 1
    assert len(result.subtasks) == 3
    assert result.classification == "harness_operation_failure"
    assert result.score_eligible is False
    assert persisted["agent_scored_tasksets"] == 0
    assert persisted["harness_invalid_tasksets"] == 1
    assert persisted["harness_operation_failures"] == 1
    assert persisted["harness_valid_coverage"] == 0.0
    assert persisted["formal_score_available"] is False
    assert persisted["not_run_subtasks"] == 2
    assert payload["subtasks"][0]["classification"] == "harness_operation_failure"
    assert payload["subtasks"][0]["failure_reason"] == "harness_operation_failure"
    assert payload["subtasks"][0]["score_eligible"] is False
    assert payload["subtasks"][0]["agent_tool_call_count"] == 2
    assert payload["subtasks"][0]["backend_action_count"] == 5
    assert payload["subtasks"][0]["terminal_tool_call_id"] == "call_terminal"
    assert payload["subtasks"][0]["terminal_evidence_ref"] == ("attempts.jsonl#execution_terminal")
    assert [item["classification"] for item in payload["subtasks"][1:]] == [None, None]
    assert [item["not_run_reason"] for item in payload["subtasks"][1:]] == [
        "prior_infrastructure_failure",
        "prior_infrastructure_failure",
    ]
    assert [item["blocked_by_classification"] for item in payload["subtasks"][1:]] == [
        "harness_operation_failure",
        "harness_operation_failure",
    ]
    assert [item["runtime_status"] for item in payload["subtasks"][1:]] == [
        "not_run",
        "not_run",
    ]
    assert [item["agent_tool_call_count"] for item in payload["subtasks"][1:]] == [0, 0]
    assert [item["backend_action_count"] for item in payload["subtasks"][1:]] == [0, 0]
    assert (
        json.loads(
            (runner.run_dir / "taskset-infra-terminal" / "subtask-02" / "summary.json").read_text(
                encoding="utf-8"
            )
        )["not_run_reason"]
        == "prior_infrastructure_failure"
    )
    assert payload["root_terminal"]["phase"] == "subtask_execution"
    assert payload["root_terminal"]["subtask_index"] == 0


def test_taskset_reset_terminal_stops_before_model_and_transport(
    monkeypatch,
    tmp_path: Path,
) -> None:
    class ResetTerminalAdapter:
        def __init__(self) -> None:
            self.close_calls = 0

        def set_frame_dir(self, _path: Path) -> None:
            return None

        def reset(self, *, selection_entry: Any) -> AlfworldResetResult:
            assert selection_entry.expected_logical_scene == "FloorPlan1"
            return AlfworldResetResult(
                backend_kind="thor",
                ready=False,
                state=None,
                scene_generation=None,
                goal_generation=None,
                scene_reset_fingerprint=None,
                goal_trial_fingerprint=None,
                snapshot_sha256=None,
                snapshot_ref=None,
                setup_trigger="external_reset_failed",
                setup_failure="external_reset_failed",
                classification="runtime_failure",
                score_eligible=False,
                setup_backend_action_count=0,
                recovery_status="not_needed",
                cleanup_status="not_needed",
                quarantine_required=False,
                environment_disposition="not_started",
                evidence_ref="reset-terminal.json",
            )

        def close(self) -> None:
            self.close_calls += 1

    adapter = ResetTerminalAdapter()
    transport_calls: list[None] = []

    def transport_factory() -> object:
        transport_calls.append(None)
        raise AssertionError("reset terminal must stop before transport construction")

    monkeypatch.setattr(
        runner_module,
        "build_alfworld_batch_env_with_first_trial",
        lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr(runner_module, "AlfworldEnvAdapter", lambda **_kwargs: adapter)
    alfworld_root, taskset = _taskset_fixture(tmp_path, taskset_id="reset-terminal")
    runner = AlfworldTasksetRunner(
        taskset_config=TasksetRunConfig(
            alfworld_root=alfworld_root,
            alfworld_config=tmp_path / "base_config.yaml",
            trace_root=tmp_path / "traces",
            provider_config=_provider_config(tmp_path),
            provider_name="mimo_v25",
            run_id="taskset-reset-terminal",
            tasksets=(taskset,),
        ),
        transport_factory=transport_factory,
    )

    result = runner.run().taskset_results[0]
    payload = result.to_dict()

    assert transport_calls == []
    assert adapter.close_calls == 1
    assert payload["classification"] == "runtime_failure"
    assert payload["root_terminal"]["phase"] == "reset_setup"
    assert payload["root_terminal"]["subtask_index"] is None
    assert payload["root_terminal"]["control_terminal_record"]["final_code"] == (
        "external_reset_failed"
    )
    assert [row["execution_status"] for row in payload["subtasks"]] == [
        "not_run",
        "not_run",
        "not_run",
    ]
    assert [row["not_run_reason"] for row in payload["subtasks"]] == [
        "taskset_setup_failure",
        "taskset_setup_failure",
        "taskset_setup_failure",
    ]
    assert all(row["classification"] is None for row in payload["subtasks"])
    assert not (
        runner.run_dir / "taskset-reset-terminal" / "subtask-01" / "model_trace.jsonl"
    ).exists()


def test_taskset_goal_terminal_stops_current_subtask_before_transport(
    monkeypatch,
    tmp_path: Path,
) -> None:
    state = AlfworldEnvState(
        episode_id="taskset-goal-terminal",
        task="put pencil on shelf",
        observation="",
        inventory=None,
        last_command=None,
        last_feedback=None,
        reward=0.0,
        done=True,
        won=True,
        goal_condition_success_rate=1.0,
        frame_path=None,
        step_index=1,
        invalid_action_count=0,
    )

    class GoalTerminalAdapter:
        def __init__(self) -> None:
            self.current_state = state
            self.advance_goal_calls = 0
            self.close_calls = 0

        def set_frame_dir(self, _path: Path) -> None:
            return None

        def reset(self, *, selection_entry: Any) -> AlfworldResetResult:
            return AlfworldResetResult(
                backend_kind="thor",
                ready=True,
                state=state,
                scene_generation=1,
                goal_generation=1,
                scene_reset_fingerprint="a" * 64,
                goal_trial_fingerprint=selection_entry.goal_fingerprint,
                snapshot_sha256="b" * 64,
                snapshot_ref="snapshot.json",
                setup_trigger=None,
                setup_failure=None,
                classification=None,
                score_eligible=True,
                setup_backend_action_count=7,
                recovery_status="restored",
                cleanup_status="not_needed",
                quarantine_required=False,
                environment_disposition="ready",
                evidence_ref="reset.json",
            )

        def advance_goal(
            self,
            _traj_data: dict[str, Any],
            *,
            subtask_label: str,
            selection_entry: Any,
        ) -> AlfworldGoalAdvanceResult:
            assert subtask_label.endswith("subtask-02")
            self.advance_goal_calls += 1
            return AlfworldGoalAdvanceResult(
                backend_kind="thor",
                ready=False,
                state=None,
                scene_generation=1,
                goal_generation=1,
                scene_reset_fingerprint="a" * 64,
                goal_trial_fingerprint=selection_entry.goal_fingerprint,
                snapshot_sha256="b" * 64,
                before_scene_state_sha256="c" * 64,
                after_scene_state_sha256=None,
                advance_trigger="goal_advance_rejected",
                advance_failure="goal_advance_rejected",
                classification="execution_state_uncertain",
                score_eligible=False,
                benchmark_control_action_count=1,
                cleanup_status="succeeded",
                quarantine_required=True,
                environment_disposition="closed",
                evidence_ref="goal-advance.json",
            )

        def is_current_goal_satisfied(self) -> bool:
            return True

        def current_goal_condition_success_rate(self) -> float:
            return 1.0

        def close(self) -> None:
            self.close_calls += 1

    adapter = GoalTerminalAdapter()
    transport_calls: list[None] = []
    observed_requests = []

    def transport_factory() -> object:
        transport_calls.append(None)
        return object()

    class SessionManager:
        def __init__(self) -> None:
            self.sessions: dict[str, AgentSession] = {}

        def get(self, session_id: str) -> Any:
            return SimpleNamespace(session=self.sessions[session_id])

    class SuccessfulApplicationEntry:
        def __init__(self, *, transport_factory: Any, **_kwargs: Any) -> None:
            self._transport_factory = transport_factory
            self.application = SimpleNamespace(session_manager=SessionManager())

        def run(self, request: Any) -> RunResult:
            observed_requests.append(request)
            self._transport_factory()
            assert request.session_id is not None
            self.application.session_manager.sessions.setdefault(
                request.session_id,
                AgentSession(request.session_id),
            )
            return RunResult(
                run_id="application-run",
                session_id=request.session_id,
                status=RunStatus.REPLIED,
            )

        def close(self) -> None:
            return None

    monkeypatch.setattr(
        runner_module,
        "AlfworldApplicationEntry",
        SuccessfulApplicationEntry,
    )
    monkeypatch.setattr(
        runner_module,
        "build_alfworld_batch_env_with_first_trial",
        lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr(runner_module, "AlfworldEnvAdapter", lambda **_kwargs: adapter)
    monkeypatch.setattr(
        runner_module,
        "build_episode_prompt",
        lambda **_kwargs: "taskset prompt",
    )
    alfworld_root, taskset = _taskset_fixture(tmp_path, taskset_id="goal-terminal")
    runner = AlfworldTasksetRunner(
        taskset_config=TasksetRunConfig(
            alfworld_root=alfworld_root,
            alfworld_config=tmp_path / "base_config.yaml",
            trace_root=tmp_path / "traces",
            provider_config=_provider_config(tmp_path),
            provider_name="mimo_v25",
            run_id="taskset-goal-terminal",
            tasksets=(taskset,),
        ),
        transport_factory=transport_factory,
    )

    result = runner.run().taskset_results[0]
    payload = result.to_dict()

    assert transport_calls == [None]
    assert len(observed_requests) == 1
    assert observed_requests[0].continuous_taskset is True
    assert observed_requests[0].environment is adapter
    assert adapter.advance_goal_calls == 1
    assert adapter.close_calls == 1
    assert payload["root_terminal"]["phase"] == "goal_advance"
    assert payload["root_terminal"]["subtask_index"] == 1
    assert payload["setup_backend_action_count"] == 7
    assert payload["benchmark_control_action_count"] == 1
    assert payload["model_backend_action_count"] == 0
    assert payload["total_backend_action_count"] == 7
    assert payload["total_external_action_count"] == 8
    assert payload["subtasks"][0]["execution_status"] == "executed"
    assert payload["subtasks"][0]["classification"] == "agent_success"
    assert payload["subtasks"][1]["not_run_reason"] == "goal_advance_failure"
    assert payload["subtasks"][2]["not_run_reason"] == ("prior_infrastructure_failure")
    assert payload["subtasks"][1]["classification"] is None
    assert payload["subtasks"][2]["classification"] is None
    assert not (
        runner.run_dir / "taskset-goal-terminal" / "subtask-02" / "model_trace.jsonl"
    ).exists()
