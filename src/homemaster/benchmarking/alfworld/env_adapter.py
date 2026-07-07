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


class _ObjectLocationResult(SimpleNamespace):
    success: bool
    feedback: str
    object_label: str | None
    source_receptacle: str | None
    object_type: str | None


class _TargetResolutionResult(SimpleNamespace):
    success: bool
    feedback: str
    resolved_kind: str | None
    resolved_label: str | None
    object_label: str | None
    source_receptacle: str | None
    object_type: str | None
    object_id: str | None


class _ManipulationResolutionResult(SimpleNamespace):
    success: bool
    feedback: str
    object_id: str | None = None
    object_label: str | None = None
    object_type: str | None = None


class _ThorActionResult(SimpleNamespace):
    success: bool
    feedback: str
    backend_actions: list[str]
    resolved: dict[str, Any]


_OBJECT_TYPE_ALIASES: dict[str, tuple[str, ...]] = {
    "basin": ("sinkbasin", "bathtubbasin"),
    "counter": ("countertop",),
    "handsoap": ("soapbar",),
    "handsoapbar": ("soapbar",),
    "microwaveoven": ("microwave",),
    "refrigerator": ("fridge",),
    "sink": ("sinkbasin",),
    "soap": ("soapbar",),
    "towelholder": ("handtowelholder",),
}


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
        self._last_go_to_object_id: str | None = None
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
        self._last_go_to_object_id = None
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

    def go_to_target(
        self,
        target: str,
        *,
        tool_name: str,
        tool_args: dict[str, Any],
    ) -> AlfworldStepResult:
        previous = self.current_state
        label = target.strip()
        command = f"go to target {label}"
        try:
            thor_env = self._resolve_thor_env()
            resolved = _resolve_navigation_target(thor_env, label)
        except Exception as exc:
            state = self._state_after_backend_failure(
                previous=previous,
                command=command,
                feedback=str(exc),
            )
            return AlfworldStepResult(
                tool_name=tool_name,
                tool_args=_model_visible_tool_args(tool_args),
                translated_command=command,
                success=False,
                failure_reason="env_error",
                state=state,
                feedback=str(exc),
            )

        enriched_args = dict(tool_args)
        if resolved.success:
            enriched_args.update({
                "resolved_kind": resolved.resolved_kind,
                "resolved_label": resolved.resolved_label,
                "object_label": resolved.object_label,
                "object_type": resolved.object_type,
                "source_receptacle": resolved.source_receptacle,
                "object_id": resolved.object_id,
            })
        if not resolved.success:
            state = self._state_after_backend_failure(
                previous=previous,
                command=command,
                feedback=resolved.feedback,
            )
            return AlfworldStepResult(
                tool_name=tool_name,
                tool_args=_model_visible_tool_args(enriched_args),
                translated_command=command,
                success=False,
                failure_reason="target_not_found",
                state=state,
                feedback=resolved.feedback,
            )

        try:
            thor_env = self._resolve_thor_env()
            nav = _teleport_to_object_ids(thor_env, [resolved.object_id])
            nav_result = self._state_from_virtual_navigation(
                previous=previous,
                command=f"virtual go to {resolved.resolved_label or label}",
                tool_name=tool_name,
                tool_args=enriched_args,
                success=nav.success,
                feedback=(
                    f"Navigation backend moved to the target area for "
                    f"{resolved.resolved_label or label}."
                    if nav.success
                    else nav.feedback
                ),
            )
        except Exception as exc:
            nav_result = self._state_from_virtual_navigation(
                previous=previous,
                command=f"virtual go to {resolved.resolved_label or label}",
                tool_name=tool_name,
                tool_args=enriched_args,
                success=False,
                feedback=str(exc),
            )
        self._last_go_to_object_id = resolved.object_id if nav_result.success else None
        feedback = (
            f"Reached {resolved.resolved_label or label}"
            if nav_result.success
            else nav_result.feedback
        )
        if nav_result.success and resolved.source_receptacle:
            feedback += f" at {resolved.source_receptacle}"
        if nav_result.success:
            feedback += "."
        return AlfworldStepResult(
            tool_name=tool_name,
            tool_args=_model_visible_tool_args(enriched_args),
            translated_command=command,
            success=nav_result.success,
            failure_reason=nav_result.failure_reason,
            state=nav_result.state,
            feedback=feedback,
        )

    def _state_from_virtual_navigation(
        self,
        *,
        previous: AlfworldEnvState,
        command: str,
        tool_name: str,
        tool_args: dict[str, Any],
        success: bool,
        feedback: str,
    ) -> AlfworldStepResult:
        won = self.is_current_goal_satisfied() if success else previous.won
        goal_rate = (
            self.current_goal_condition_success_rate()
            if success
            else previous.goal_condition_success_rate
        )
        state = AlfworldEnvState(
            episode_id=previous.episode_id,
            task=previous.task,
            observation=feedback,
            inventory=previous.inventory,
            last_command=command,
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
            translated_command=command,
            success=success,
            failure_reason=None if success else "navigation_target_not_visible",
            state=state,
            feedback=feedback,
        )

    def find_object(
        self,
        target: str,
        *,
        tool_name: str,
        tool_args: dict[str, Any],
    ) -> AlfworldStepResult:
        previous = self.current_state
        label = target.strip()
        command = f"find object {label}"
        try:
            thor_env = self._resolve_thor_env()
            found = _find_object_location(thor_env, label)
        except Exception as exc:
            state = self._state_after_backend_failure(
                previous=previous,
                command=command,
                feedback=str(exc),
            )
            return AlfworldStepResult(
                tool_name=tool_name,
                tool_args=_model_visible_tool_args(tool_args),
                translated_command=command,
                success=False,
                failure_reason="env_error",
                state=state,
                feedback=str(exc),
            )

        enriched_args = dict(tool_args)
        enriched_args.update({
            "object_label": found.object_label,
            "object_type": found.object_type,
            "source_receptacle": found.source_receptacle,
        })
        if not found.success:
            state = self._state_after_backend_failure(
                previous=previous,
                command=command,
                feedback=found.feedback,
            )
            return AlfworldStepResult(
                tool_name=tool_name,
                tool_args=_model_visible_tool_args(enriched_args),
                translated_command=command,
                success=False,
                failure_reason="object_not_found",
                state=state,
                feedback=found.feedback,
            )

        search_result = self._search_visible_object_source(
            target=found.object_type or label,
            preferred_source=found.source_receptacle,
            previous=previous,
            command=command,
            tool_name=tool_name,
            tool_args=enriched_args,
        )
        if search_result is not None:
            return search_result

        nav_result = self.virtual_navigate(
            found.object_type or label,
            tool_name=tool_name,
            tool_args=enriched_args,
        )
        feedback = (
            f"Found {found.object_label or label}. {nav_result.feedback}"
            if nav_result.success
            else nav_result.feedback
        )
        return AlfworldStepResult(
            tool_name=tool_name,
            tool_args=_model_visible_tool_args(enriched_args),
            translated_command=command,
            success=nav_result.success,
            failure_reason=nav_result.failure_reason,
            state=nav_result.state,
            feedback=feedback,
        )

    def _search_visible_object_source(
        self,
        *,
        target: str,
        preferred_source: str | None,
        previous: AlfworldEnvState,
        command: str,
        tool_name: str,
        tool_args: dict[str, Any],
    ) -> AlfworldStepResult | None:
        sources = _ordered_navigation_sources(
            previous.admissible_commands,
            preferred_source=preferred_source,
        )
        if not sources:
            return None

        final_payload: tuple[str, float, bool, bool, float, tuple[str, ...]] | None = None
        for source in sources:
            nav_command = f"go to {source}"
            try:
                obs, scores, dones, infos = self._env.step([nav_command])
            except Exception:
                continue
            observation = str(_first(obs, ""))
            reward = float(_first(scores, 0.0))
            done = bool(_first(dones, False))
            won = bool(_first_info(infos, "won", False))
            goal_rate = float(_first_info(infos, "goal_condition_success_rate", 0.0))
            admissible = tuple(
                str(item) for item in _first_info(infos, "admissible_commands", [])
            )
            final_payload = (observation, reward, done, won, goal_rate, admissible)
            if _is_invalid_feedback(observation):
                continue
            object_label = _observed_label_for_type(observation, target)
            if object_label is None:
                continue
            enriched_args = dict(tool_args)
            enriched_args["object_label"] = object_label
            enriched_args["source_receptacle"] = source
            state = AlfworldEnvState(
                episode_id=previous.episode_id,
                task=previous.task,
                observation=observation,
                inventory=previous.inventory,
                last_command=nav_command,
                last_feedback=observation,
                reward=reward,
                done=done,
                won=won,
                goal_condition_success_rate=goal_rate,
                frame_path=self._save_current_frame(step_index=previous.step_index + 1),
                step_index=previous.step_index + 1,
                invalid_action_count=previous.invalid_action_count,
                admissible_commands=admissible,
            )
            self._state = state
            return AlfworldStepResult(
                tool_name=tool_name,
                tool_args=_model_visible_tool_args(enriched_args),
                translated_command=f"{command} -> {nav_command}",
                success=True,
                failure_reason=None,
                state=state,
                feedback=f"Found {object_label} at {source}. {observation}",
            )

        if final_payload is None:
            return None
        observation, reward, done, won, goal_rate, admissible = final_payload
        state = AlfworldEnvState(
            episode_id=previous.episode_id,
            task=previous.task,
            observation=observation,
            inventory=previous.inventory,
            last_command=command,
            last_feedback=(
                f"Could not find a visible {target} at any known navigable place."
            ),
            reward=reward,
            done=done,
            won=won,
            goal_condition_success_rate=goal_rate,
            frame_path=self._save_current_frame(step_index=previous.step_index + 1),
            step_index=previous.step_index + 1,
            invalid_action_count=previous.invalid_action_count + 1,
            admissible_commands=admissible,
        )
        self._state = state
        return AlfworldStepResult(
            tool_name=tool_name,
            tool_args=_model_visible_tool_args(tool_args),
            translated_command=command,
            success=False,
            failure_reason="object_not_visible",
            state=state,
            feedback=f"Could not find a visible {target} at any known navigable place.",
        )

    def manipulate_with_thor(
        self,
        *,
        action: str,
        tool_name: str,
        tool_args: dict[str, Any],
    ) -> AlfworldStepResult:
        previous = self.current_state
        normalized_action = action.strip().lower()
        command = f"thor {normalized_action}"
        try:
            thor_env = self._resolve_thor_env()
            result = _execute_thor_manipulation(
                thor_env,
                normalized_action,
                tool_args,
                last_go_to_object_id=self._last_go_to_object_id,
            )
            won = self.is_current_goal_satisfied()
            goal_rate = self.current_goal_condition_success_rate()
        except Exception as exc:
            state = self._state_after_backend_failure(
                previous=previous,
                command=command,
                feedback=str(exc),
            )
            return AlfworldStepResult(
                tool_name=tool_name,
                tool_args=_model_visible_tool_args(tool_args),
                translated_command=command,
                success=False,
                failure_reason="env_error",
                state=state,
                feedback=str(exc),
            )

        enriched_args = dict(tool_args)
        enriched_args.update({"backend": "thor_api", "backend_actions": result.backend_actions})
        enriched_args.update({key: value for key, value in result.resolved.items() if value is not None})
        state = AlfworldEnvState(
            episode_id=previous.episode_id,
            task=previous.task,
            observation=result.feedback,
            inventory=_inventory_text(thor_env),
            last_command=command,
            last_feedback=result.feedback,
            reward=previous.reward,
            done=won,
            won=won,
            goal_condition_success_rate=goal_rate,
            frame_path=self._save_current_frame(step_index=previous.step_index + 1),
            step_index=previous.step_index + 1,
            invalid_action_count=previous.invalid_action_count + (0 if result.success else 1),
            admissible_commands=previous.admissible_commands,
        )
        self._state = state
        return AlfworldStepResult(
            tool_name=tool_name,
            tool_args=_model_visible_tool_args(enriched_args),
            translated_command=command,
            success=result.success,
            failure_reason=None if result.success else "invalid_action",
            state=state,
            feedback=result.feedback,
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


def _execute_thor_manipulation(
    thor_env: Any,
    action: str,
    tool_args: dict[str, Any],
    *,
    last_go_to_object_id: str | None = None,
) -> _ThorActionResult:
    if action == "take":
        target = _resolve_manipulation_object(
            thor_env,
            _required_tool_arg(tool_args, "object"),
            require_pickupable=True,
            preferred_object_id=last_go_to_object_id,
        )
        if not target.success:
            return _failed_thor_action(target.feedback, {"object_resolution": target})
        event = _thor_step(thor_env, {
            "action": "PickupObject",
            "objectId": target.object_id,
            "forceAction": True,
        })
        return _single_action_result(
            event,
            success_feedback=f"Picked up {target.object_type or 'object'}.",
            failure_feedback=f"Could not pick up {target.object_type or 'object'}.",
            backend_action="PickupObject",
            resolved={"object_resolution": target},
        )

    if action == "put":
        held = _held_object(thor_env)
        if held is None:
            return _failed_thor_action("No object is currently held.", {})
        target = _resolve_manipulation_object(
            thor_env,
            _required_tool_arg(tool_args, "target_receptacle"),
            require_receptacle=True,
        )
        if not target.success:
            return _failed_thor_action(target.feedback, {"target_resolution": target})
        event = _thor_step(thor_env, {
            "action": "PutObject",
            "objectId": str(held.get("objectId", "")),
            "receptacleObjectId": target.object_id,
            "forceAction": True,
            "placeStationary": True,
        })
        return _single_action_result(
            event,
            success_feedback=f"Placed the held object on/in {target.object_type or 'target'}.",
            failure_feedback=f"Could not place the held object on/in {target.object_type or 'target'}.",
            backend_action="PutObject",
            resolved={
                "held_object_id": str(held.get("objectId", "")) or None,
                "target_resolution": target,
            },
        )

    if action in {"open", "close"}:
        target = _resolve_manipulation_object(
            thor_env,
            _first_tool_arg(tool_args, "target_receptacle", "object"),
            require_openable=True,
        )
        if not target.success:
            return _failed_thor_action(target.feedback, {"target_resolution": target})
        obj = _object_by_id(thor_env, target.object_id)
        want_open = action == "open"
        if obj is not None and obj.get("isOpen") is want_open:
            state_text = "open" if want_open else "closed"
            return _ThorActionResult(
                success=True,
                feedback=f"{target.object_type or 'target'} is already {state_text}.",
                backend_actions=[],
                resolved=_resolved_payload({"target_resolution": target}),
            )
        backend = "OpenObject" if want_open else "CloseObject"
        event = _thor_step(thor_env, {
            "action": backend,
            "objectId": target.object_id,
            "forceAction": True,
        })
        return _single_action_result(
            event,
            success_feedback=f"{backend} succeeded for {target.object_type or 'target'}.",
            failure_feedback=f"Could not {action} {target.object_type or 'target'}.",
            backend_action=backend,
            resolved={"target_resolution": target},
        )

    if action == "use":
        target = _resolve_manipulation_object(
            thor_env,
            _first_tool_arg(tool_args, "object", "target_receptacle"),
            require_toggleable=True,
        )
        if not target.success:
            return _failed_thor_action(target.feedback, {"object_resolution": target})
        obj = _object_by_id(thor_env, target.object_id)
        if obj is not None and obj.get("isToggled") is True:
            return _ThorActionResult(
                success=True,
                feedback=f"{target.object_type or 'target'} is already on.",
                backend_actions=[],
                resolved=_resolved_payload({"object_resolution": target}),
            )
        event = _thor_step(thor_env, {
            "action": "ToggleObjectOn",
            "objectId": target.object_id,
            "forceAction": True,
        })
        return _single_action_result(
            event,
            success_feedback=f"Turned on {target.object_type or 'target'}.",
            failure_feedback=f"Could not turn on {target.object_type or 'target'}.",
            backend_action="ToggleObjectOn",
            resolved={"object_resolution": target},
        )

    if action == "heat":
        return _execute_heat(thor_env, tool_args)
    if action == "cool":
        return _execute_cool(thor_env, tool_args)
    if action == "clean":
        return _execute_clean(thor_env, tool_args)
    if action == "slice":
        return _failed_thor_action(
            "slice is not implemented in the THOR manipulation backend yet.",
            {},
        )
    return _failed_thor_action(f"unsupported manipulation action: {action}", {})


def _execute_heat(thor_env: Any, tool_args: dict[str, Any]) -> _ThorActionResult:
    held = _held_object(thor_env)
    if held is None:
        return _failed_thor_action("No object is currently held for heat.", {})
    tool = _resolve_manipulation_object(
        thor_env,
        _first_tool_arg(
            tool_args,
            "tool_receptacle",
            "target_receptacle",
            default="microwave",
        ),
        require_receptacle=True,
    )
    if not tool.success:
        return _failed_thor_action(tool.feedback, {"tool_resolution": tool})
    held_id = str(held.get("objectId", ""))
    actions = [
        {"action": "OpenObject", "objectId": tool.object_id, "forceAction": True},
        {"action": "PutObject", "objectId": held_id, "receptacleObjectId": tool.object_id, "forceAction": True, "placeStationary": True},
        {"action": "CloseObject", "objectId": tool.object_id, "forceAction": True},
        {"action": "ToggleObjectOn", "objectId": tool.object_id, "forceAction": True},
        {"action": "ToggleObjectOff", "objectId": tool.object_id, "forceAction": True},
        {"action": "OpenObject", "objectId": tool.object_id, "forceAction": True},
        {"action": "PickupObject", "objectId": held_id, "forceAction": True},
        {"action": "CloseObject", "objectId": tool.object_id, "forceAction": True},
    ]
    return _run_thor_macro(
        thor_env,
        actions,
        success_feedback="Heated the held object.",
        resolved={"held_object_id": held_id, "tool_resolution": tool},
    )


def _execute_cool(thor_env: Any, tool_args: dict[str, Any]) -> _ThorActionResult:
    held = _held_object(thor_env)
    if held is None:
        return _failed_thor_action("No object is currently held for cool.", {})
    tool = _resolve_manipulation_object(
        thor_env,
        _first_tool_arg(
            tool_args,
            "tool_receptacle",
            "target_receptacle",
            default="fridge",
        ),
        require_receptacle=True,
    )
    if not tool.success:
        return _failed_thor_action(tool.feedback, {"tool_resolution": tool})
    held_id = str(held.get("objectId", ""))
    actions = [
        {"action": "OpenObject", "objectId": tool.object_id, "forceAction": True},
        {"action": "PutObject", "objectId": held_id, "receptacleObjectId": tool.object_id, "forceAction": True, "placeStationary": True},
        {"action": "CloseObject", "objectId": tool.object_id, "forceAction": True},
        {"action": "OpenObject", "objectId": tool.object_id, "forceAction": True},
        {"action": "PickupObject", "objectId": held_id, "forceAction": True},
        {"action": "CloseObject", "objectId": tool.object_id, "forceAction": True},
    ]
    return _run_thor_macro(
        thor_env,
        actions,
        success_feedback="Cooled the held object.",
        resolved={"held_object_id": held_id, "tool_resolution": tool},
    )


def _execute_clean(thor_env: Any, tool_args: dict[str, Any]) -> _ThorActionResult:
    held = _held_object(thor_env)
    if held is None:
        return _failed_thor_action("No object is currently held for clean.", {})
    sink = _resolve_manipulation_object(
        thor_env,
        _first_tool_arg(
            tool_args,
            "tool_receptacle",
            "target_receptacle",
            default="sinkbasin",
        ),
        require_receptacle=True,
    )
    if not sink.success:
        return _failed_thor_action(sink.feedback, {"tool_resolution": sink})
    faucet = _nearest_object_of_type(thor_env, "faucet", sink.object_id)
    if faucet is None:
        return _failed_thor_action("No faucet found near the sinkbasin.", {"tool_resolution": sink})
    faucet_id = str(faucet.get("objectId", ""))
    held_id = str(held.get("objectId", ""))
    actions = [
        {"action": "PutObject", "objectId": held_id, "receptacleObjectId": sink.object_id, "forceAction": True, "placeStationary": True},
        {"action": "ToggleObjectOn", "objectId": faucet_id, "forceAction": True},
        {"action": "ToggleObjectOff", "objectId": faucet_id, "forceAction": True},
        {"action": "PickupObject", "objectId": held_id, "forceAction": True},
    ]
    result = _run_thor_macro(
        thor_env,
        actions,
        success_feedback="Cleaned the held object.",
        resolved={
            "held_object_id": held_id,
            "tool_resolution": sink,
            "faucet_object_id": faucet_id,
        },
    )
    if result.success:
        _clean_dirty_objects_in_receptacle(thor_env, sink.object_id)
    return result


def _run_thor_macro(
    thor_env: Any,
    actions: list[dict[str, Any]],
    *,
    success_feedback: str,
    resolved: dict[str, Any],
) -> _ThorActionResult:
    backend_actions: list[str] = []
    for action in actions:
        event = _thor_step(thor_env, action)
        backend_actions.append(str(action.get("action", "")))
        if not _event_success(event):
            return _ThorActionResult(
                success=False,
                feedback=(
                    f"{action.get('action', 'action')} failed: "
                    f"{_event_error(event) or 'Nothing happens.'}"
                ),
                backend_actions=backend_actions,
                resolved=_resolved_payload(resolved),
            )
    return _ThorActionResult(
        success=True,
        feedback=success_feedback,
        backend_actions=backend_actions,
        resolved=_resolved_payload(resolved),
    )


def _resolve_manipulation_object(
    thor_env: Any,
    value: str,
    *,
    require_pickupable: bool = False,
    require_receptacle: bool = False,
    require_openable: bool = False,
    require_toggleable: bool = False,
    preferred_object_id: str | None = None,
) -> _ManipulationResolutionResult:
    metadata = getattr(thor_env.last_event, "metadata", {})
    objects = metadata.get("objects", []) if isinstance(metadata, dict) else []
    matches = _objects_by_type(objects, value)
    if require_pickupable:
        matches = [obj for obj in matches if obj.get("pickupable") is True]
    if require_receptacle:
        matches = [obj for obj in matches if obj.get("receptacle") is True or obj.get("openable") is True]
    if require_openable:
        matches = [obj for obj in matches if obj.get("openable") is True]
    if require_toggleable:
        matches = [obj for obj in matches if obj.get("toggleable") is True]
    if not matches:
        return _ManipulationResolutionResult(
            success=False,
            feedback=f"No THOR object matched {value}.",
            object_id=None,
            object_label=None,
            object_type=None,
        )
    if preferred_object_id:
        for obj in matches:
            if str(obj.get("objectId", "")) == preferred_object_id:
                target = obj
                break
        else:
            target = _choose_object_target(matches)
    else:
        target = _choose_object_target(matches)
    return _ManipulationResolutionResult(
        success=True,
        feedback=f"Resolved {value} to {_command_type_name(target)}.",
        object_id=str(target.get("objectId", "")) or None,
        object_label=_command_label_for_object(objects, target),
        object_type=_command_type_name(target),
    )


def _single_action_result(
    event: Any,
    *,
    success_feedback: str,
    failure_feedback: str,
    backend_action: str,
    resolved: dict[str, Any],
) -> _ThorActionResult:
    success = _event_success(event)
    return _ThorActionResult(
        success=success,
        feedback=success_feedback if success else f"{failure_feedback} {_event_error(event)}".strip(),
        backend_actions=[backend_action],
        resolved=_resolved_payload(resolved),
    )


def _failed_thor_action(feedback: str, resolved: dict[str, Any]) -> _ThorActionResult:
    return _ThorActionResult(
        success=False,
        feedback=feedback,
        backend_actions=[],
        resolved=_resolved_payload(resolved),
    )


def _resolved_payload(resolved: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for prefix, value in resolved.items():
        if isinstance(value, _ManipulationResolutionResult):
            payload[f"{prefix}_object_id"] = value.object_id
            payload[f"{prefix}_object_type"] = value.object_type
        else:
            payload[prefix] = value
    return payload


def _required_tool_arg(tool_args: dict[str, Any], key: str) -> str:
    value = tool_args.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} is required")
    return value.strip()


def _first_tool_arg(
    tool_args: dict[str, Any],
    *keys: str,
    default: str | None = None,
) -> str:
    for key in keys:
        value = tool_args.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    if default is not None:
        return default
    joined = " or ".join(keys)
    raise ValueError(f"{joined} is required")


def _held_object(thor_env: Any) -> dict[str, Any] | None:
    metadata = getattr(thor_env.last_event, "metadata", {})
    inventory = metadata.get("inventoryObjects", []) if isinstance(metadata, dict) else []
    if isinstance(inventory, list) and inventory:
        held = inventory[0]
        if isinstance(held, dict):
            return held
    return None


def _inventory_text(thor_env: Any) -> str | None:
    held = _held_object(thor_env)
    if held is None:
        return "You are carrying nothing."
    object_type = _object_type_key(str(held.get("objectType") or held.get("objectId", "object")))
    return f"You are carrying: {object_type}."


def _object_by_id(thor_env: Any, object_id: str | None) -> dict[str, Any] | None:
    if not object_id:
        return None
    metadata = getattr(thor_env.last_event, "metadata", {})
    objects = metadata.get("objects", []) if isinstance(metadata, dict) else []
    for obj in objects:
        if isinstance(obj, dict) and str(obj.get("objectId", "")) == object_id:
            return obj
    return None


def _nearest_object_of_type(
    thor_env: Any,
    object_type: str,
    near_object_id: str | None,
) -> dict[str, Any] | None:
    metadata = getattr(thor_env.last_event, "metadata", {})
    objects = metadata.get("objects", []) if isinstance(metadata, dict) else []
    matches = _objects_by_type(objects, object_type)
    if not matches:
        return None
    near = _object_by_id(thor_env, near_object_id)
    if near is None:
        return _choose_object_target(matches)
    return sorted(matches, key=lambda obj: _distance_sq(obj, near))[0]


def _distance_sq(a: dict[str, Any], b: dict[str, Any]) -> float:
    apos = a.get("position") if isinstance(a, dict) else None
    bpos = b.get("position") if isinstance(b, dict) else None
    if not isinstance(apos, dict) or not isinstance(bpos, dict):
        return 0.0
    try:
        return (
            (float(apos.get("x", 0.0)) - float(bpos.get("x", 0.0))) ** 2
            + (float(apos.get("y", 0.0)) - float(bpos.get("y", 0.0))) ** 2
            + (float(apos.get("z", 0.0)) - float(bpos.get("z", 0.0))) ** 2
        )
    except (TypeError, ValueError):
        return 0.0


def _clean_dirty_objects_in_receptacle(thor_env: Any, receptacle_id: str | None) -> None:
    receptacle = _object_by_id(thor_env, receptacle_id)
    object_ids = receptacle.get("receptacleObjectIds") if isinstance(receptacle, dict) else None
    if not isinstance(object_ids, list):
        return
    for object_id in object_ids:
        obj = _object_by_id(thor_env, str(object_id))
        if obj is None or not bool(obj.get("dirtyable")) or not bool(obj.get("isDirty")):
            continue
        _thor_step(thor_env, {"action": "CleanObject", "objectId": str(object_id)})


def _thor_step(thor_env: Any, action: dict[str, Any]) -> Any:
    return thor_env.step({key: value for key, value in action.items() if value is not None})


def _event_success(event: Any) -> bool:
    metadata = getattr(event, "metadata", {})
    return bool(metadata.get("lastActionSuccess")) if isinstance(metadata, dict) else False


def _event_error(event: Any) -> str:
    metadata = getattr(event, "metadata", {})
    if isinstance(metadata, dict):
        value = metadata.get("errorMessage")
        if isinstance(value, str):
            return value
    return ""


def _teleport_to_visible_object(thor_env: Any, object_type: str) -> _NavigationResult:
    metadata = getattr(thor_env.last_event, "metadata", {})
    objects = metadata.get("objects", []) if isinstance(metadata, dict) else []
    targets = _objects_by_type(objects, object_type)
    return _teleport_to_targets(thor_env, targets, target_name=object_type)


def _teleport_to_object_ids(
    thor_env: Any,
    object_ids: list[str | None],
) -> _NavigationResult:
    target_ids = {str(item) for item in object_ids if isinstance(item, str) and item}
    metadata = getattr(thor_env.last_event, "metadata", {})
    objects = metadata.get("objects", []) if isinstance(metadata, dict) else []
    targets = [
        obj for obj in objects
        if isinstance(obj, dict) and str(obj.get("objectId", "")) in target_ids
    ]
    target_name = ", ".join(sorted(target_ids)) if target_ids else "target"
    return _teleport_to_targets(thor_env, targets, target_name=target_name)


def _teleport_to_targets(
    thor_env: Any,
    targets: list[dict[str, Any]],
    *,
    target_name: str,
) -> _NavigationResult:
    if not targets:
        return _NavigationResult(
            success=False,
            feedback=f"No {target_name} navigation target found in the scene.",
        )

    metadata = getattr(thor_env.last_event, "metadata", {})
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
                f"Navigation backend reached the area near {target_name} "
                f"({best_object_id}), "
                "but the target was not visible."
            ),
        )
    return _NavigationResult(
        success=False,
        feedback=f"Navigation backend could not teleport near {target_name}.",
    )


def _find_object_location(thor_env: Any, object_type: str) -> _ObjectLocationResult:
    metadata = getattr(thor_env.last_event, "metadata", {})
    objects = metadata.get("objects", []) if isinstance(metadata, dict) else []
    targets = [
        obj for obj in _objects_by_type(objects, object_type)
        if obj.get("pickupable") is True
    ]
    if not targets:
        return _ObjectLocationResult(
            success=False,
            feedback=(
                f"No movable {object_type} object found in the current scene. "
                "Use robot_navigate for places, furniture, appliances, and receptacles."
            ),
            object_label=None,
            source_receptacle=None,
            object_type=None,
        )

    target = _choose_object_target(targets)
    object_label = _command_label_for_object(objects, target)
    source = _source_receptacle_label(objects, target)
    object_type_name = _command_type_name(target)
    source_text = f" at {source}" if source else ""
    return _ObjectLocationResult(
        success=True,
        feedback=f"Found {object_label}{source_text}.",
        object_label=object_label,
        source_receptacle=source,
        object_type=object_type_name,
    )


def _resolve_navigation_target(thor_env: Any, target: str) -> _TargetResolutionResult:
    metadata = getattr(thor_env.last_event, "metadata", {})
    objects = metadata.get("objects", []) if isinstance(metadata, dict) else []
    matches = _objects_by_type(objects, target)
    if not matches:
        return _TargetResolutionResult(
            success=False,
            feedback=f"No {target} target found in the current scene.",
            resolved_kind=None,
            resolved_label=None,
            object_label=None,
            source_receptacle=None,
            object_type=None,
            object_id=None,
        )

    pickupable = [obj for obj in matches if obj.get("pickupable") is True]
    if pickupable:
        target_obj = _choose_object_target(pickupable)
        resolved_kind = "movable_object"
    else:
        target_obj = _choose_object_target(matches)
        resolved_kind = (
            "toggle_object"
            if bool(target_obj.get("toggleable"))
            else "receptacle_or_fixture"
        )

    label = _command_label_for_object(objects, target_obj)
    object_type_name = _command_type_name(target_obj)
    source = _source_receptacle_label(objects, target_obj) if pickupable else None
    return _TargetResolutionResult(
        success=True,
        feedback=f"Resolved {target} to {label or object_type_name}.",
        resolved_kind=resolved_kind,
        resolved_label=label or object_type_name,
        object_label=label if pickupable else None,
        source_receptacle=source,
        object_type=object_type_name,
        object_id=str(target_obj.get("objectId", "")) or None,
    )


def _choose_object_target(targets: list[dict[str, Any]]) -> dict[str, Any]:
    return sorted(
        targets,
        key=lambda obj: (
            not bool(obj.get("visible")),
            str(obj.get("objectId", "")),
        ),
    )[0]


def _source_receptacle_label(
    objects: list[Any],
    target: dict[str, Any],
) -> str | None:
    object_by_id = {
        str(obj.get("objectId", "")): obj
        for obj in objects
        if isinstance(obj, dict)
    }
    parent_ids = target.get("parentReceptacles") or []
    if isinstance(parent_ids, list):
        for parent_id in parent_ids:
            parent = object_by_id.get(str(parent_id))
            if parent is not None:
                label = _command_label_for_object(objects, parent)
                if label:
                    return label

    target_id = str(target.get("objectId", ""))
    for obj in objects:
        if not isinstance(obj, dict):
            continue
        recep_ids = obj.get("receptacleObjectIds") or []
        if isinstance(recep_ids, list) and target_id in {str(item) for item in recep_ids}:
            label = _command_label_for_object(objects, obj)
            if label:
                return label
    return None


def _command_label_for_object(objects: list[Any], target: dict[str, Any]) -> str | None:
    command_type = _command_type_name(target)
    if not command_type:
        return None
    same_type = sorted(
        [
            obj for obj in objects
            if isinstance(obj, dict)
            and _command_type_name(obj) == command_type
        ],
        key=lambda obj: str(obj.get("objectId", "")),
    )
    try:
        index = same_type.index(target) + 1
    except ValueError:
        index = 1
    return f"{command_type} {index}"


def _command_type_name(obj: dict[str, Any]) -> str:
    return _object_type_key(str(obj.get("objectType", "")))


def _objects_by_type(objects: list[Any], object_type: str) -> list[dict[str, Any]]:
    query_key = _object_query_key(object_type)
    full_query_key = _object_type_key(object_type)
    if not query_key and not full_query_key:
        return []
    typed_objects = [obj for obj in objects if isinstance(obj, dict)]

    exact_matches = [
        obj for obj in typed_objects
        if full_query_key and full_query_key in _exact_object_keys(objects, obj)
    ]
    if exact_matches:
        return exact_matches

    type_matches = [
        obj for obj in typed_objects
        if _object_query_key(str(obj.get("objectType", ""))) == query_key
    ]
    if type_matches:
        return type_matches

    alias_matches = _objects_by_alias(typed_objects, query_key)
    if alias_matches:
        return alias_matches

    suffix_type_keys = {
        _object_query_key(str(obj.get("objectType", "")))
        for obj in typed_objects
        if _object_query_key(str(obj.get("objectType", ""))).endswith(query_key)
        or query_key.endswith(_object_query_key(str(obj.get("objectType", ""))))
    }
    if len(suffix_type_keys) == 1:
        (matched_type,) = tuple(suffix_type_keys)
        return [
            obj for obj in typed_objects
            if _object_query_key(str(obj.get("objectType", ""))) == matched_type
        ]
    return []


def _exact_object_keys(objects: list[Any], obj: dict[str, Any]) -> set[str]:
    keys = {
        _object_type_key(str(obj.get("objectType", ""))),
        _object_type_key(str(obj.get("objectId", ""))),
    }
    object_id = str(obj.get("objectId", ""))
    if "|" in object_id:
        keys.add(_object_type_key(object_id.split("|", 1)[0]))
    label = _command_label_for_object(objects, obj)
    if label:
        keys.add(_object_type_key(label))
    return {key for key in keys if key}


def _objects_by_alias(
    objects: list[dict[str, Any]],
    query_key: str,
) -> list[dict[str, Any]]:
    alias_targets = _OBJECT_TYPE_ALIASES.get(query_key, ())
    if not alias_targets:
        return []
    alias_target_set = set(alias_targets)
    return [
        obj for obj in objects
        if _object_query_key(str(obj.get("objectType", ""))) in alias_target_set
    ]


def _ordered_navigation_sources(
    admissible_commands: tuple[str, ...],
    *,
    preferred_source: str | None,
) -> list[str]:
    sources: list[str] = []
    if preferred_source:
        sources.append(preferred_source)
    prefix = "go to "
    for command in admissible_commands:
        if not command.startswith(prefix):
            continue
        source = command[len(prefix):].strip()
        if source:
            sources.append(source)
    output: list[str] = []
    seen: set[str] = set()
    for source in sources:
        key = _object_type_key(source)
        if key in seen:
            continue
        seen.add(key)
        output.append(source)
    return output


def _object_query_key(value: str) -> str:
    key = _object_type_key(value)
    while key and key[-1].isdigit():
        key = key[:-1]
    return key


def _object_type_key(value: str) -> str:
    return "".join(ch for ch in value.casefold() if ch.isalnum())


def _observed_label_for_type(observation: str, object_type: str) -> str | None:
    key = _object_query_key(object_type)
    if not key:
        return None
    import re

    pattern = re.compile(r"\b([a-z]+)\s+(\d+)\b", re.IGNORECASE)
    for match in pattern.finditer(observation):
        label = f"{match.group(1).casefold()} {match.group(2)}"
        if _object_query_key(label) == key:
            return label
    return None


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
