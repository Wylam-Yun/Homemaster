"""Load alfworld_tasksets.yaml into a TasksetRunConfig.

The yaml has two sections:
  run:      global knobs (provider, limits, failure_simulation, long_horizon, ...)
  tasksets: list of {id, floorplan, difficulty, description, subtasks: [...]}

Each subtask is {goal_type, object, parent|toggle, [mrecep], instruction}.
After parsing, traj_index.resolve_subtask_trajs attaches a traj_path to every
subtask so the runner can call env.set_task(...) without synthesizing traj_data.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from homemaster.benchmarking.alfworld.traj_index import resolve_subtask_trajs
from homemaster.benchmarking.alfworld.types import (
    FailureSimulation,
    LongHorizonSettings,
    Subtask,
    Taskset,
    TasksetRunConfig,
)

_VALID_GOAL_TYPES: set[str] = {
    "pick_and_place_simple",
    "pick_two_obj_and_place",
    "look_at_obj_in_light",
    "pick_heat_then_place_in_recep",
    "pick_cool_then_place_in_recep",
    "pick_clean_then_place_in_recep",
    "pick_and_place_with_movable_recep",
}


def _require(mapping: dict[str, Any], key: str, parent: str) -> Any:
    if key not in mapping:
        raise ValueError(f"alfworld_tasksets.yaml: {parent} missing required field '{key}'")
    return mapping[key]


def _parse_subtask(raw: dict[str, Any], taskset_id: str, index: int) -> Subtask:
    goal_type = _require(raw, "goal_type", f"taskset[{taskset_id}].subtasks[{index}]")
    if goal_type not in _VALID_GOAL_TYPES:
        raise ValueError(
            f"taskset[{taskset_id}].subtasks[{index}]: unsupported goal_type '{goal_type}'. "
            f"valid: {sorted(_VALID_GOAL_TYPES)}"
        )
    obj = _require(raw, "object", f"taskset[{taskset_id}].subtasks[{index}]")
    return Subtask(
        goal_type=goal_type,  # type: ignore[arg-type]
        object=str(obj),
        parent=raw.get("parent"),
        toggle=raw.get("toggle"),
        mrecep=raw.get("mrecep"),
        instruction=str(raw.get("instruction", "")),
    )


def _parse_taskset(raw: dict[str, Any]) -> Taskset:
    ts_id = str(_require(raw, "id", "taskset"))
    floorplan = int(_require(raw, "floorplan", f"taskset[{ts_id}]"))
    raw_subtasks = _require(raw, "subtasks", f"taskset[{ts_id}]")
    if not isinstance(raw_subtasks, list) or not raw_subtasks:
        raise ValueError(f"taskset[{ts_id}].subtasks must be a non-empty list")
    subtasks = tuple(_parse_subtask(st, ts_id, i) for i, st in enumerate(raw_subtasks))
    difficulty = str(raw.get("difficulty", "easy"))
    if difficulty not in {"easy", "hard"}:
        raise ValueError(
            f"taskset[{ts_id}].difficulty must be 'easy' or 'hard', got '{difficulty}'"
        )
    return Taskset(
        id=ts_id,
        floorplan=floorplan,
        subtasks=subtasks,
        difficulty=difficulty,  # type: ignore[arg-type]
        description=str(raw.get("description", "")),
    )


def _parse_failure_simulation(raw: dict[str, Any] | None) -> FailureSimulation:
    if raw is None:
        return FailureSimulation()
    return FailureSimulation(
        enabled=bool(raw.get("enabled", False)),
        grasp_failure_rate=float(raw.get("grasp_failure_rate", 0.0)),
        put_failure_rate=float(raw.get("put_failure_rate", 0.0)),
        navigate_failure_rate=float(raw.get("navigate_failure_rate", 0.0)),
    )


def _parse_long_horizon(raw: dict[str, Any] | None) -> LongHorizonSettings:
    if raw is None:
        return LongHorizonSettings()
    return LongHorizonSettings(
        keep_scene_across_subtasks=bool(raw.get("keep_scene_across_subtasks", True)),
    )


def load_taskset_config(
    path: Path,
    *,
    alfworld_root: Path,
    alfworld_config: Path,
) -> TasksetRunConfig:
    """Parse the yaml and resolve every subtask's traj_path.

    alfworld_root / alfworld_config are passed in by the CLI (they are not in the
    yaml because they are machine-specific paths on the THOR host).
    """
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError(
            "pyyaml is required to load alfworld_tasksets.yaml; "
            "install with `pip install pyyaml`"
        ) from exc

    with path.open("r", encoding="utf-8") as reader:
        payload = yaml.safe_load(reader)
    if not isinstance(payload, dict):
        raise ValueError(f"alfworld_tasksets.yaml must be a mapping: {path}")

    run = _require(payload, "run", "top level")
    raw_tasksets = _require(payload, "tasksets", "top level")
    if not isinstance(raw_tasksets, list) or not raw_tasksets:
        raise ValueError("alfworld_tasksets.yaml: tasksets must be a non-empty list")

    tasksets = [_parse_taskset(ts) for ts in raw_tasksets]

    split = str(run.get("split", "valid_unseen"))
    if split not in {"train", "valid_seen", "valid_unseen"}:
        raise ValueError(f"run.split must be train/valid_seen/valid_unseen, got '{split}'")

    # Resolve traj_data.json for every subtask before constructing the config
    # (TasksetRunConfig.__post_init__ rejects subtasks with traj_path=None).
    resolve_subtask_trajs(tasksets, alfworld_root=alfworld_root, split=split)

    return TasksetRunConfig(
        alfworld_root=alfworld_root,
        alfworld_config=alfworld_config,
        trace_root=Path(run.get("trace_root", "./var/alfworld-trace")),
        provider_config=Path(_require(run, "provider_config", "run")),
        provider_name=str(_require(run, "provider_name", "run")),
        env_type=run.get("env_type", "AlfredThorEnv"),  # type: ignore[arg-type]
        split=split,  # type: ignore[arg-type]
        memory_mode=run.get("memory_mode", "disabled"),  # type: ignore[arg-type]
        max_invalid_actions=int(run.get("max_invalid_actions", 100)),
        max_env_steps=int(run.get("max_env_steps", 50)),
        max_tool_iterations=int(run.get("max_tool_iterations", 1000)),
        observation_mode=run.get("observation_mode", "visual_eval"),  # type: ignore[arg-type]
        seed=int(run.get("seed", 42)),
        run_id=run.get("run_id"),
        failure_simulation=_parse_failure_simulation(run.get("failure_simulation")),
        long_horizon=_parse_long_horizon(run.get("long_horizon")),
        tasksets=tuple(tasksets),
    )
