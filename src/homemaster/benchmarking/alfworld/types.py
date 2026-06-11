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
    max_env_steps: int = 50
    max_tool_iterations: int = 300
    max_output_tokens: int | None = None
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
        if self.max_env_steps <= 0:
            raise ValueError("max_env_steps must be > 0")
        if self.max_tool_iterations <= 0:
            raise ValueError("max_tool_iterations must be > 0")
        if self.max_output_tokens is not None and self.max_output_tokens <= 0:
            raise ValueError("max_output_tokens must be > 0 when set")
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
    config: dict[str, Any] = field(default_factory=dict)

    @property
    def success_rate(self) -> float:
        if not self.episodes:
            return 0.0
        return sum(1 for episode in self.episodes if episode.success) / len(self.episodes)

    def to_dict(self) -> dict[str, Any]:
        total = len(self.episodes)
        return {
            "run_id": self.run_id,
            "config": self.config,
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
