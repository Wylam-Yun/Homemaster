"""Adapter around ALFWorld batch environments for HomeMaster benchmark tools."""

from __future__ import annotations

import contextlib
import json
import math
import os
import sys
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from homemaster.benchmarking.alfworld.types import (
    AlfworldBenchmarkConfig,
    AlfworldEnvState,
    AlfworldStepResult,
)


class _NavigationResult(SimpleNamespace):
    success: bool
    feedback: str


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


def build_alfworld_batch_env_with_first_trial(
    config: AlfworldBenchmarkConfig,
    *,
    first_trial_path: Path,
) -> Any:
    """Build a batch env whose reset() loads `first_trial_path` first.

    Used by the long-horizon taskset runner: the first subtask needs a real
    scene load (reset), and subsequent subtasks swap goals via advance_goal
    without resetting. We pin json_file_list to [first_trial_path] so the first
    reset loads that trial's scene + object_poses.
    """
    with _prepend_sys_path(config.alfworld_root):
        from alfworld.agents.environment import get_environment

        payload = load_alfworld_yaml(config.alfworld_config)
        env_cls = get_environment(config.env_type)
        alfred_env = env_cls(payload, train_eval=split_to_train_eval(config.split))
        # Pin the first trial before init_env triggers any file collection.
        first_trial_str = str(first_trial_path)
        if hasattr(alfred_env, "json_file_list"):
            alfred_env.json_file_list = [first_trial_str]
        if hasattr(alfred_env, "num_games"):
            alfred_env.num_games = 1
        env = alfred_env.init_env(batch_size=1)
        # init_env may have re-collected files; re-pin defensively.
        if hasattr(env, "json_file_list"):
            env.json_file_list = [first_trial_str]
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
        frame_dir: Path | None = None,
    ) -> None:
        self._env = env
        self._episode_prefix = episode_prefix
        self._seed = seed
        self._frame_dir = frame_dir
        self._state: AlfworldEnvState | None = None
        if hasattr(self._env, "seed"):
            self._env.seed(seed)

    def set_frame_dir(self, frame_dir: Path | None) -> None:
        self._frame_dir = frame_dir

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
            goal_condition_success_rate=float(
                _first_info(infos, "goal_condition_success_rate", 0.0)
            ),
            frame_path=self._save_current_frame(step_index=0),
            step_index=0,
            invalid_action_count=0,
            admissible_commands=tuple(
                str(item) for item in _first_info(infos, "admissible_commands", [])
            ),
        )
        self._state = state
        return state

    # ------------------------------------------------------------------
    # Long-horizon: advance to the next goal WITHOUT resetting the scene.
    # ------------------------------------------------------------------

    def advance_goal(self, traj_data: dict[str, Any], *, subtask_label: str) -> AlfworldEnvState:
        """Swap the current goal in the same loaded scene (C-route long horizon).

        Calls the底层 ThorEnv.set_task(traj_data, args), which only replaces
        `env.task` (a fresh BaseTask with goal_idx=0/step_num=0). It does NOT
        reload the scene or restore object_poses, so world state accumulated by
        previous subtasks is preserved.

        Verified against alfworld/env/thor_env.py:133 (set_task) and
        alfworld/env/tasks.py:14 (BaseTask.__init__ resets counters).

        Returns a refreshed AlfworldEnvState describing the new goal. The
        observation text is taken from the new traj_data's templated task desc
        so the model sees the new instruction.
        """
        thor_env = self._resolve_thor_env()
        args = _build_set_task_args(thor_env)
        try:
            from alfworld.agents.utils.misc import get_templated_task_desc
            task_desc = get_templated_task_desc(traj_data)
        except Exception:
            task_desc = str(subtask_label)

        thor_env.set_task(traj_data, args, reward_type="dense")
        # Refresh goal-satisfaction fields from the new task without stepping.
        try:
            won = bool(thor_env.get_goal_satisfied())
            pcs = thor_env.get_goal_conditions_met()
            goal_rate = float(pcs[0]) / float(pcs[1]) if pcs[1] else 0.0
        except Exception:
            won, goal_rate = False, 0.0

        previous = self._state
        # Carry over world-state-derived fields; reset per-subtask counters.
        new_state = AlfworldEnvState(
            episode_id=f"{self._episode_prefix}/{subtask_label}",
            task=task_desc,
            observation=task_desc,
            inventory=previous.inventory if previous else None,
            last_command=None,
            last_feedback=None,
            reward=0.0,
            done=False,
            won=won,
            goal_condition_success_rate=goal_rate,
            frame_path=self._save_current_frame(step_index=0),
            step_index=0,
            invalid_action_count=0,
            admissible_commands=tuple(),
        )
        self._state = new_state
        return new_state

    def is_current_goal_satisfied(self) -> bool:
        """Read the current goal's satisfaction from the底层 ThorEnv (external terminal state)."""
        thor_env = self._resolve_thor_env()
        try:
            return bool(thor_env.get_goal_satisfied())
        except Exception:
            return False

    def current_goal_condition_success_rate(self) -> float:
        thor_env = self._resolve_thor_env()
        try:
            pcs = thor_env.get_goal_conditions_met()
            return float(pcs[0]) / float(pcs[1]) if pcs[1] else 0.0
        except Exception:
            return 0.0

    def _resolve_thor_env(self) -> Any:
        """Walk the batch env wrapper to the底层 ThorEnv instance.

        AlfredThorEnv batch env: self._env.envs[0].env  (Thor thread.env = ThorEnv)
        Falls back to attribute search for non-batch / TextWorld envs.
        """
        env = self._env
        envs = getattr(env, "envs", None)
        if isinstance(envs, list | tuple) and envs:
            thor_env = getattr(envs[0], "env", None)
            if thor_env is not None:
                return thor_env
        # Fallback: search for set_task on the env itself.
        if hasattr(env, "set_task"):
            return env
        raise RuntimeError(
            "could not resolve底层 ThorEnv from the ALFWorld batch env; "
            "advance_goal / is_current_goal_satisfied require AlfredThorEnv"
        )

    def step(
        self,
        command: str,
        *,
        tool_name: str,
        tool_args: dict[str, Any],
    ) -> AlfworldStepResult:
        previous = self.current_state

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
            invalid = _is_invalid_feedback(observation)
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
                step_index=previous.step_index + 1,
                frame_path=previous.frame_path,
                invalid_action_count=previous.invalid_action_count,
                admissible_commands=previous.admissible_commands,
            )
            self._state = state
            return AlfworldStepResult(
                tool_name=tool_name,
                tool_args=_model_visible_tool_args(tool_args),
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
            step_index=previous.step_index + 1,
            frame_path=self._save_current_frame(step_index=previous.step_index + 1),
            invalid_action_count=invalid_count,
            admissible_commands=admissible,
        )
        self._state = state
        return AlfworldStepResult(
            tool_name=tool_name,
            tool_args=_model_visible_tool_args(tool_args),
            translated_command=command,
            success=not invalid,
            failure_reason="invalid_action" if invalid else None,
            state=state,
            feedback=observation,
        )

    def virtual_navigate(
        self,
        target: str,
        *,
        tool_name: str,
        tool_args: dict[str, Any],
    ) -> AlfworldStepResult:
        previous = self.current_state
        label = target.strip()
        command = f"virtual go to {label}"
        try:
            thor_env = self._resolve_thor_env()
            nav_result = _teleport_to_visible_object(thor_env, label)
            success = nav_result.success
            observation = (
                f"Navigation backend moved to the target area for {label}."
                if success
                else nav_result.feedback
            )
            won = self.is_current_goal_satisfied()
            goal_rate = self.current_goal_condition_success_rate()
        except Exception as exc:
            success = False
            observation = str(exc)
            won = previous.won
            goal_rate = previous.goal_condition_success_rate
        state = AlfworldEnvState(
            episode_id=previous.episode_id,
            task=previous.task,
            observation=observation,
            inventory=previous.inventory,
            last_command=command,
            last_feedback=observation,
            reward=previous.reward,
            done=won,
            won=won,
            goal_condition_success_rate=goal_rate,
            frame_path=self._save_current_frame(step_index=previous.step_index + 1),
            step_index=previous.step_index + 1,
            invalid_action_count=previous.invalid_action_count + (0 if success else 1),
            admissible_commands=previous.admissible_commands,
        )
        self._state = state
        return AlfworldStepResult(
            tool_name=tool_name,
            tool_args=_model_visible_tool_args(tool_args),
            translated_command=command,
            success=success,
            failure_reason=None if success else "navigation_target_not_visible",
            state=state,
            feedback=observation,
        )

    def force_toggle_unique_object_type(
        self,
        object_type: str,
        *,
        tool_name: str,
        tool_args: dict[str, Any],
    ) -> AlfworldStepResult:
        previous = self.current_state
        target_type = object_type.strip().casefold()
        try:
            thor_env = self._resolve_thor_env()
            objects = getattr(thor_env.last_event, "metadata", {}).get("objects", [])
            matches = [
                obj for obj in objects
                if str(obj.get("objectType", "")).casefold() == target_type
            ]
            if len(matches) != 1:
                feedback = (
                    f"Expected exactly one {object_type} target in the scene, "
                    f"found {len(matches)}."
                )
                state = self._state_after_backend_failure(
                    previous=previous,
                    command=f"force use {object_type}",
                    feedback=feedback,
                )
                return AlfworldStepResult(
                    tool_name=tool_name,
                    tool_args=_model_visible_tool_args(tool_args),
                    translated_command=f"force use {object_type}",
                    success=False,
                    failure_reason="ambiguous_grounding",
                    state=state,
                    feedback=feedback,
                )
            target_object_id = str(matches[0]["objectId"])
            event = thor_env.step({
                "action": "ToggleObjectOn",
                "objectId": target_object_id,
                "forceAction": True,
            })
            success = bool(event.metadata.get("lastActionSuccess"))
            feedback = (
                f"You turn on the {object_type.lower()}."
                if success
                else "Nothing happens."
            )
            won = self.is_current_goal_satisfied()
            goal_rate = self.current_goal_condition_success_rate()
        except Exception as exc:
            feedback = str(exc)
            state = self._state_after_backend_failure(
                previous=previous,
                command=f"force use {object_type}",
                feedback=feedback,
            )
            return AlfworldStepResult(
                tool_name=tool_name,
                tool_args=_model_visible_tool_args(tool_args),
                translated_command=f"force use {object_type}",
                success=False,
                failure_reason="env_error",
                state=state,
                feedback=feedback,
            )

        state = AlfworldEnvState(
            episode_id=previous.episode_id,
            task=previous.task,
            observation=feedback,
            inventory=previous.inventory,
            last_command=f"force use {object_type}",
            last_feedback=feedback,
            reward=previous.reward,
            done=won,
            won=won,
            goal_condition_success_rate=goal_rate,
            frame_path=self._save_current_frame(step_index=previous.step_index + 1),
            step_index=previous.step_index + 1,
            invalid_action_count=previous.invalid_action_count + (0 if success else 1),
            admissible_commands=previous.admissible_commands,
        )
        self._state = state
        return AlfworldStepResult(
            tool_name=tool_name,
            tool_args=_model_visible_tool_args(tool_args),
            translated_command=f"force use {object_type}",
            success=success,
            failure_reason=None if success else "invalid_action",
            state=state,
            feedback=feedback,
        )

    def _state_after_backend_failure(
        self,
        *,
        previous: AlfworldEnvState,
        command: str,
        feedback: str,
    ) -> AlfworldEnvState:
        state = AlfworldEnvState(
            episode_id=previous.episode_id,
            task=previous.task,
            observation=previous.observation,
            inventory=previous.inventory,
            last_command=command,
            last_feedback=feedback,
            reward=previous.reward,
            done=previous.done,
            won=previous.won,
            goal_condition_success_rate=previous.goal_condition_success_rate,
            frame_path=previous.frame_path,
            step_index=previous.step_index + 1,
            invalid_action_count=previous.invalid_action_count + 1,
            admissible_commands=previous.admissible_commands,
        )
        self._state = state
        return state

    def _save_current_frame(self, *, step_index: int) -> str | None:
        if self._frame_dir is None:
            return None
        frame = _latest_thor_frame(self._env)
        if frame is None:
            return None
        try:
            from PIL import Image

            self._frame_dir.mkdir(parents=True, exist_ok=True)
            path = self._frame_dir / f"frame-{step_index:04d}.png"
            Image.fromarray(frame).save(path)
            return str(path)
        except Exception:
            return None


def _is_invalid_feedback(observation: str) -> bool:
    normalized = observation.strip().lower().rstrip(".")
    return normalized == "nothing happens"


def _build_set_task_args(thor_env: Any) -> SimpleNamespace:
    """Build the args namespace ThorEnv.set_task expects (reward_config path).

    Mirrors alfred_thor_env.py:106-112 which sets args.reward_config to the
    bundled config/rewards.json under the alfworld.agents package.
    """
    import alfworld.agents

    args = SimpleNamespace()
    args.reward_config = os.path.join(
        alfworld.agents.__path__[0], "config", "rewards.json"
    )
    return args


def _first(value: Any, default: Any) -> Any:
    if isinstance(value, list | tuple) and value:
        return value[0]
    return default


def _first_info(infos: dict[str, Any], key: str, default: Any) -> Any:
    value = infos.get(key, default)
    if isinstance(value, list | tuple) and value:
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


def _model_visible_tool_args(tool_args: dict[str, Any]) -> dict[str, Any]:
    cleaned = _drop_admissible_commands(tool_args)
    if isinstance(cleaned, dict):
        return cleaned
    return {}


def _drop_admissible_commands(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _drop_admissible_commands(item)
            for key, item in value.items()
            if str(key) != "admissible_commands"
        }
    if isinstance(value, list | tuple):
        return [_drop_admissible_commands(item) for item in value]
    return value


def _teleport_to_visible_object(thor_env: Any, object_type: str) -> _NavigationResult:
    metadata = getattr(thor_env.last_event, "metadata", {})
    objects = metadata.get("objects", []) if isinstance(metadata, dict) else []
    targets = _objects_by_type(objects, object_type)
    if not targets:
        return _NavigationResult(
            success=False,
            feedback=f"No {object_type} navigation target found in the scene.",
        )

    reachable = _reachable_positions(thor_env)
    if not reachable:
        return _NavigationResult(
            success=False,
            feedback="Navigation backend could not read reachable positions.",
        )

    agent_y = _agent_height(metadata)
    candidates = _teleport_candidates(targets, reachable, agent_y=agent_y)
    best_event = None
    best_score = -1.0
    best_visible = False
    best_object_id = ""
    target_ids = {
        str(target.get("objectId", ""))
        for target in targets
        if isinstance(target.get("objectId"), str)
    }
    for action, target_id in candidates:
        event = thor_env.step(action)
        event_metadata = getattr(event, "metadata", {})
        if not bool(event_metadata.get("lastActionSuccess")):
            continue
        visible, score = _target_visibility_score(event, target_id, target_ids)
        if score > best_score:
            best_event = event
            best_score = score
            best_visible = visible
            best_object_id = target_id
        if visible:
            return _NavigationResult(success=True, feedback="Navigation target is visible.")

    if best_event is not None:
        # Leave the camera at the best reachable pose even when the target is not
        # visible, so the returned frame is still the backend's best effort.
        return _NavigationResult(
            success=False,
            feedback=(
                f"Navigation backend reached the area near {object_type} "
                f"({best_object_id}), "
                "but the target was not visible."
            ),
        )
    return _NavigationResult(
        success=False,
        feedback=f"Navigation backend could not teleport near {object_type}.",
    )


def _objects_by_type(objects: list[Any], object_type: str) -> list[dict[str, Any]]:
    key = _object_type_key(object_type)
    return [
        obj for obj in objects
        if isinstance(obj, dict) and _object_type_key(str(obj.get("objectType", ""))) == key
    ]


def _object_type_key(value: str) -> str:
    return "".join(ch for ch in value.casefold() if ch.isalnum())


def _reachable_positions(thor_env: Any) -> list[dict[str, float]]:
    event = thor_env.step({"action": "GetReachablePositions"})
    metadata = getattr(event, "metadata", {})
    positions = metadata.get("reachablePositions", []) if isinstance(metadata, dict) else []
    reachable: list[dict[str, float]] = []
    for position in positions:
        if not isinstance(position, dict):
            continue
        try:
            reachable.append({
                "x": float(position["x"]),
                "z": float(position["z"]),
            })
        except (KeyError, TypeError, ValueError):
            continue
    return reachable


def _agent_height(metadata: dict[str, Any]) -> float:
    try:
        return float(metadata["agent"]["position"]["y"])
    except (KeyError, TypeError, ValueError):
        return 0.9010564


def _teleport_candidates(
    targets: list[dict[str, Any]],
    reachable: list[dict[str, float]],
    *,
    agent_y: float,
) -> list[tuple[dict[str, Any], str]]:
    output: list[tuple[dict[str, Any], str, float]] = []
    for target in targets:
        object_id = str(target.get("objectId", ""))
        for action, distance in _single_target_teleport_candidates(
            target,
            reachable,
            agent_y=agent_y,
        ):
            output.append((action, object_id, distance))
    output.sort(key=lambda item: item[2])
    return [(action, object_id) for action, object_id, _ in output]


def _single_target_teleport_candidates(
    target: dict[str, Any],
    reachable: list[dict[str, float]],
    *,
    agent_y: float,
) -> list[tuple[dict[str, Any], float]]:
    target_position = target.get("position", {})
    target_x = float(target_position.get("x", 0.0))
    target_y = float(target_position.get("y", agent_y))
    target_z = float(target_position.get("z", 0.0))
    nearest = sorted(
        reachable,
        key=lambda point: (point["x"] - target_x) ** 2 + (point["z"] - target_z) ** 2,
    )[:12]
    actions: list[tuple[dict[str, Any], float]] = []
    seen: set[tuple[float, float, int, int]] = set()
    for point in nearest:
        distance = math.hypot(target_x - point["x"], target_z - point["z"])
        base_rotation = _quantized_rotation(
            target_x - point["x"],
            target_z - point["z"],
        )
        base_horizon = _quantized_horizon(target_y - agent_y, distance)
        rotations = _ordered_unique_ints([
            base_rotation,
            base_rotation - 90,
            base_rotation + 90,
            base_rotation + 180,
            0,
            90,
            180,
            270,
        ], modulo=360)
        horizons = _ordered_unique_ints([
            base_horizon,
            base_horizon - 15,
            base_horizon + 15,
            0,
            15,
            30,
            45,
            60,
        ])
        for rotation in rotations:
            for horizon in horizons:
                if horizon < -30 or horizon > 60:
                    continue
                key = (round(point["x"], 3), round(point["z"], 3), rotation, horizon)
                if key in seen:
                    continue
                seen.add(key)
                actions.append((
                    {
                        "action": "TeleportFull",
                        "x": point["x"],
                        "y": agent_y,
                        "z": point["z"],
                        "rotateOnTeleport": True,
                        "rotation": rotation,
                        "horizon": horizon,
                    },
                    distance,
                ))
    return actions


def _quantized_rotation(dx: float, dz: float) -> int:
    if abs(dx) < 1e-6 and abs(dz) < 1e-6:
        return 0
    yaw = math.degrees(math.atan2(dx, dz))
    return int(round(yaw / 90.0) * 90) % 360


def _quantized_horizon(dy: float, horizontal_distance: float) -> int:
    pitch = math.degrees(math.atan2(dy, max(horizontal_distance, 1e-3)))
    # AI2-THOR horizon is positive when looking down. For targets below the
    # camera, dy is negative and the desired horizon should be positive.
    horizon = int(round((-pitch) / 15.0) * 15)
    return max(-30, min(60, horizon))


def _ordered_unique_ints(values: list[int], *, modulo: int | None = None) -> list[int]:
    seen: set[int] = set()
    output: list[int] = []
    for value in values:
        item = value % modulo if modulo else value
        if item in seen:
            continue
        seen.add(item)
        output.append(item)
    return output


def _target_visibility_score(
    event: Any,
    object_id: str,
    target_ids: set[str] | None = None,
) -> tuple[bool, float]:
    metadata = getattr(event, "metadata", {})
    objects = metadata.get("objects", []) if isinstance(metadata, dict) else []
    ids = target_ids or {object_id}
    target = next(
        (obj for obj in objects if isinstance(obj, dict) and obj.get("objectId") in ids),
        None,
    )
    visible = bool(target and target.get("visible"))
    score = 1.0 if visible else 0.0
    detections = getattr(event, "instance_detections2D", None)
    if isinstance(detections, dict):
        for target_id in ids:
            if target_id in detections:
                score += _bbox_area_score(detections[target_id])
                visible = True
                break
    return visible, score


def _bbox_area_score(box: Any) -> float:
    try:
        x1, y1, x2, y2 = [float(item) for item in box]
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, x2 - x1) * max(0.0, y2 - y1)


def _latest_thor_frame(env: Any) -> Any | None:
    pending = [env]
    seen: set[int] = set()
    while pending:
        item = pending.pop(0)
        if item is None or id(item) in seen:
            continue
        seen.add(id(item))
        event = getattr(item, "last_event", None)
        frame = getattr(event, "frame", None)
        if frame is not None:
            return frame
        envs = getattr(item, "envs", None)
        if isinstance(envs, list | tuple) and envs:
            pending.append(envs[0])
        for attr in ("env", "controller"):
            nested = getattr(item, attr, None)
            if nested is not None:
                pending.append(nested)
    return None
