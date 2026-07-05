"""Shared types for the ALFWorld benchmark integration."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

MemoryMode = Literal["disabled", "readonly", "full"]
EnvType = Literal["AlfredTWEnv", "AlfredThorEnv"]
SplitName = Literal["train", "valid_seen", "valid_unseen"]
ObservationMode = Literal["visual_eval", "textual_debug"]
GoalType = Literal[
    "pick_and_place_simple",
    "pick_two_obj_and_place",
    "look_at_obj_in_light",
    "pick_heat_then_place_in_recep",
    "pick_cool_then_place_in_recep",
    "pick_clean_then_place_in_recep",
    "pick_and_place_with_movable_recep",
]
Difficulty = Literal["easy", "hard"]


@dataclass(frozen=True)
class Subtask:
    """One step in a long-horizon taskset.

    object/parent/toggle use ALFWorld canonical names (see alfworld_reference.md).
    traj_path is resolved by traj_index at load time; it points to the ALFWorld
    trial's traj_data.json whose task_type+pddl_params feed env.set_task(...).
    """

    goal_type: GoalType
    object: str
    parent: str | None = None
    toggle: str | None = None
    mrecep: str | None = None
    instruction: str = ""
    traj_path: Path | None = None

    def __post_init__(self) -> None:
        if self.goal_type == "look_at_obj_in_light":
            if self.toggle is None:
                raise ValueError("look_at_obj_in_light requires a toggle (DeskLamp/FloorLamp)")
        else:
            if self.parent is None:
                raise ValueError(f"{self.goal_type} requires a parent receptacle")
        if self.goal_type == "pick_and_place_with_movable_recep" and self.mrecep is None:
            raise ValueError("pick_and_place_with_movable_recep requires an mrecep")


@dataclass(frozen=True)
class Taskset:
    """A fixed FloorPlan + an ordered list of subtasks run in one persistent scene."""

    id: str
    floorplan: int
    subtasks: tuple[Subtask, ...]
    difficulty: Difficulty = "easy"
    description: str = ""

    def __post_init__(self) -> None:
        if not self.subtasks:
            raise ValueError(f"taskset {self.id} has no subtasks")


@dataclass(frozen=True)
class LongHorizonSettings:
    """Long-horizon mode: keep scene state across subtasks (C-route)."""

    keep_scene_across_subtasks: bool = True


@dataclass(frozen=True)
class FailureSimulation:
    """Per-tool failure injection. Disabled by default (ALFWorld forceAction rarely fails)."""

    enabled: bool = False
    grasp_failure_rate: float = 0.0
    put_failure_rate: float = 0.0
    navigate_failure_rate: float = 0.0

    def __post_init__(self) -> None:
        for name in ("grasp_failure_rate", "put_failure_rate", "navigate_failure_rate"):
            value = getattr(self, name)
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be in [0.0, 1.0]")


@dataclass(frozen=True)
class TasksetRunConfig:
    """Top-level config parsed from alfworld_tasksets.yaml.

    Combines the global run knobs (mirroring AlfworldBenchmarkConfig where they
    overlap) with the tasksets list. The runner reads this instead of the raw
    yaml. Every subtask must have its traj_path resolved before run time.
    """

    alfworld_root: Path
    alfworld_config: Path
    trace_root: Path
    provider_config: Path
    provider_name: str
    env_type: EnvType = "AlfredThorEnv"
    split: SplitName = "valid_unseen"
    memory_mode: MemoryMode = "disabled"
    max_invalid_actions: int = 100
    max_env_steps: int = 50
    max_tool_iterations: int = 1000
    observation_mode: ObservationMode = "visual_eval"
    seed: int = 42
    run_id: str | None = None
    failure_simulation: FailureSimulation = field(default_factory=FailureSimulation)
    long_horizon: LongHorizonSettings = field(default_factory=LongHorizonSettings)
    tasksets: tuple[Taskset, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.tasksets:
            raise ValueError("no tasksets configured")
        for ts in self.tasksets:
            for st in ts.subtasks:
                if st.traj_path is None:
                    raise ValueError(
                        f"subtask {st.goal_type}({st.object}) in taskset {ts.id} "
                        "has no traj_path; run traj_index.resolve_subtask_trajs first"
                    )


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
    max_tool_iterations: int = 1000
    provider_config: Path | None = None
    provider_name: str | None = None
    run_id: str | None = None
    debug_admissible_commands: bool = True
    observation_mode: ObservationMode = "visual_eval"
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
        if self.memory_mode not in {"disabled", "readonly", "full"}:
            raise ValueError(f"unsupported memory_mode: {self.memory_mode}")
        if self.env_type not in {"AlfredTWEnv", "AlfredThorEnv"}:
            raise ValueError(f"unsupported env_type: {self.env_type}")
        if self.split not in {"train", "valid_seen", "valid_unseen"}:
            raise ValueError(f"unsupported split: {self.split}")
        if self.observation_mode not in {"visual_eval", "textual_debug"}:
            raise ValueError(f"unsupported observation_mode: {self.observation_mode}")


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


# ----------------------------------------------------------------------
# Long-horizon taskset results (one taskset = one persistent agent session)
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class SubtaskResult:
    """Per-subtask outcome within a long-horizon taskset run."""

    index: int
    goal_type: str
    object: str
    target: str  # parent or toggle
    instruction: str
    success: bool
    failure_reason: str | None
    steps: int
    invalid_actions: int
    goal_condition_success_rate: float
    runtime_status: str
    trace_path: Path

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "goal_type": self.goal_type,
            "object": self.object,
            "target": self.target,
            "instruction": self.instruction,
            "success": self.success,
            "failure_reason": self.failure_reason,
            "steps": self.steps,
            "invalid_actions": self.invalid_actions,
            "goal_condition_success_rate": self.goal_condition_success_rate,
            "runtime_status": self.runtime_status,
            "trace_path": str(self.trace_path),
        }


@dataclass(frozen=True)
class TasksetResult:
    """Aggregate result of running one taskset (a chain of subtasks)."""

    taskset_id: str
    floorplan: int
    difficulty: str
    description: str
    subtasks: list[SubtaskResult]
    chain_success: bool  # all subtasks succeeded in order, no scene reset
    trace_dir: Path

    @property
    def success_rate(self) -> float:
        if not self.subtasks:
            return 0.0
        return sum(1 for s in self.subtasks if s.success) / len(self.subtasks)

    @property
    def chain_completed_count(self) -> int:
        """How many subtasks at the start of the chain succeeded before the first failure."""
        count = 0
        for s in self.subtasks:
            if s.success:
                count += 1
            else:
                break
        return count

    def to_dict(self) -> dict[str, Any]:
        return {
            "taskset_id": self.taskset_id,
            "floorplan": self.floorplan,
            "difficulty": self.difficulty,
            "description": self.description,
            "chain_success": self.chain_success,
            "chain_completed_count": self.chain_completed_count,
            "subtask_count": len(self.subtasks),
            "success_rate": self.success_rate,
            "trace_dir": str(self.trace_dir),
            "subtasks": [s.to_dict() for s in self.subtasks],
        }


@dataclass(frozen=True)
class TasksetRunSummary:
    """Summary across all tasksets in one run."""

    run_id: str
    taskset_results: list[TasksetResult]
    config: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "config": self.config,
            "taskset_count": len(self.taskset_results),
            "tasksets": [t.to_dict() for t in self.taskset_results],
        }

