"""Adapter around ALFWorld batch environments for HomeMaster benchmark tools."""

from __future__ import annotations

import contextlib
import json
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
        planner_location: str | None = None,
    ) -> AlfworldStepResult:
        previous = self.current_state
        label = target.strip()
        command = f"virtual go to {label}"
        observation = f"Navigation backend moved to the target area for {label}."
        failure_reason = None
        success = True
        if planner_location:
            command = f"teleport to {planner_location}"
            try:
                thor_env = self._resolve_thor_env()
                metadata = getattr(thor_env.last_event, "metadata", {})
                agent = metadata.get("agent", {}) if isinstance(metadata, dict) else {}
                position = agent.get("position", {}) if isinstance(agent, dict) else {}
                agent_height = float(position.get("y", 0.9010564))
                event = thor_env.step(
                    _location_to_teleport(planner_location, agent_height=agent_height)
                )
                success = bool(event.metadata.get("lastActionSuccess"))
                if success:
                    observation = f"Navigation backend teleported to {label}."
                else:
                    observation = "Nothing happens."
                    failure_reason = "invalid_action"
            except Exception as exc:
                success = False
                observation = str(exc)
                failure_reason = "env_error"
        state = AlfworldEnvState(
            episode_id=previous.episode_id,
            task=previous.task,
            observation=observation,
            inventory=previous.inventory,
            last_command=command,
            last_feedback=observation,
            reward=previous.reward,
            done=previous.done,
            won=previous.won,
            goal_condition_success_rate=previous.goal_condition_success_rate,
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
            failure_reason=failure_reason,
            state=state,
            feedback=observation,
        )

    def force_toggle_unique_object_type(
        self,
        object_type: str,
        *,
        tool_name: str,
        tool_args: dict[str, Any],
        object_id: str | None = None,
    ) -> AlfworldStepResult:
        previous = self.current_state
        target_type = object_type.strip().casefold()
        try:
            thor_env = self._resolve_thor_env()
            objects = getattr(thor_env.last_event, "metadata", {}).get("objects", [])
            target_object_id = object_id.strip() if isinstance(object_id, str) else ""
            if target_object_id:
                matches = [
                    obj for obj in objects
                    if str(obj.get("objectId", "")) == target_object_id
                ]
            else:
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

    def force_pickup_object(
        self,
        object_type: str,
        *,
        tool_name: str,
        tool_args: dict[str, Any],
        object_id: str | None = None,
    ) -> AlfworldStepResult:
        previous = self.current_state
        target_type = object_type.strip().casefold()
        try:
            thor_env = self._resolve_thor_env()
            objects = getattr(thor_env.last_event, "metadata", {}).get("objects", [])
            target_object_id = object_id.strip() if isinstance(object_id, str) else ""
            if target_object_id:
                matches = [
                    obj for obj in objects
                    if str(obj.get("objectId", "")) == target_object_id
                ]
            else:
                matches = [
                    obj for obj in objects
                    if str(obj.get("objectType", "")).casefold() == target_type
                ]
            if len(matches) != 1:
                feedback = (
                    f"Expected exactly one {object_type} pickup target in the scene, "
                    f"found {len(matches)}."
                )
                state = self._state_after_backend_failure(
                    previous=previous,
                    command=f"force take {object_type}",
                    feedback=feedback,
                )
                return AlfworldStepResult(
                    tool_name=tool_name,
                    tool_args=_model_visible_tool_args(tool_args),
                    translated_command=f"force take {object_type}",
                    success=False,
                    failure_reason="ambiguous_grounding",
                    state=state,
                    feedback=feedback,
                )
            target_object_id = str(matches[0]["objectId"])
            event = thor_env.step({
                "action": "PickupObject",
                "objectId": target_object_id,
                "forceAction": True,
            })
            success = bool(event.metadata.get("lastActionSuccess"))
            feedback = f"You pick up the {object_type.lower()}." if success else "Nothing happens."
            won = self.is_current_goal_satisfied()
            goal_rate = self.current_goal_condition_success_rate()
        except Exception as exc:
            feedback = str(exc)
            state = self._state_after_backend_failure(
                previous=previous,
                command=f"force take {object_type}",
                feedback=feedback,
            )
            return AlfworldStepResult(
                tool_name=tool_name,
                tool_args=_model_visible_tool_args(tool_args),
                translated_command=f"force take {object_type}",
                success=False,
                failure_reason="env_error",
                state=state,
                feedback=feedback,
            )

        state = AlfworldEnvState(
            episode_id=previous.episode_id,
            task=previous.task,
            observation=feedback,
            inventory=object_type.lower() if success else previous.inventory,
            last_command=f"force take {object_type}",
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
            translated_command=f"force take {object_type}",
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


def _location_to_teleport(location: str, *, agent_height: float) -> dict[str, Any]:
    parts = location.split("|")
    if len(parts) != 5 or parts[0] != "loc":
        raise ValueError(f"unsupported planner location: {location}")
    x, z, rotation, horizon = [int(part) for part in parts[1:]]
    return {
        "action": "TeleportFull",
        "x": x * 0.25,
        "y": agent_height,
        "z": z * 0.25,
        "rotateOnTeleport": True,
        "rotation": rotation * 90,
        "horizon": horizon,
    }
