"""State/step operations for the ALFWorld environment adapter."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from homemaster.alfworld.benchmark._adapter_shared import (
    AlfworldEnvState,
    _build_set_task_args,
    _empty_external_read,
    _event_logical_scene,
    _execution_feedback,
    _external_event_read,
    _external_read_from_event,
    _first,
    _first_info,
    _is_invalid_feedback,
    _model_visible_tool_args,
)
from homemaster.alfworld.benchmark.scene_execution import (
    ExternalRead,
    OracleExecutionContext,
)
from homemaster.alfworld.trial_selection import (
    TrialSelectionEntry,
    trial_goal_identity,
    trial_logical_scene,
)
from homemaster.alfworld.types import (
    AlfworldGoalAdvanceResult,
    AlfworldStepResult,
)


class AlfworldAdapterStateMixin:
    """Step loop, goal advancement, and external state reads."""
    # ------------------------------------------------------------------
    # Long-horizon: advance to the next goal WITHOUT resetting the scene.
    # ------------------------------------------------------------------

    def advance_goal(
        self,
        traj_data: dict[str, Any],
        *,
        subtask_label: str,
        selection_entry: TrialSelectionEntry,
    ) -> AlfworldGoalAdvanceResult:
        previous = self.current_state
        try:
            declared_scene = trial_logical_scene(traj_data)
            goal_identity = trial_goal_identity(traj_data)
        except ValueError:
            return self._goal_advance_terminal(
                selection_entry=selection_entry,
                trigger="goal_identity_unreadable",
                action_count=0,
                before_sha256=None,
                after_sha256=None,
            )
        if goal_identity != selection_entry.goal_identity:
            return self._goal_advance_terminal(
                selection_entry=selection_entry,
                trigger="expected_goal_trial_mismatch",
                action_count=0,
                before_sha256=None,
                after_sha256=None,
            )
        if declared_scene != selection_entry.expected_logical_scene:
            return self._goal_advance_terminal(
                selection_entry=selection_entry,
                trigger="goal_scene_mismatch",
                action_count=0,
                before_sha256=None,
                after_sha256=None,
            )

        try:
            thor_env = self._resolve_thor_env()
            raw_before = getattr(thor_env, "last_event", None)
            before = _external_event_read(
                raw_before,
                event_sequence=self._event_sequence,
                thor_env=thor_env,
            )
            current_scene = _event_logical_scene(raw_before)
        except Exception:
            return self._goal_advance_terminal(
                selection_entry=selection_entry,
                trigger="goal_runtime_failed",
                action_count=0,
                before_sha256=None,
                after_sha256=None,
            )
        if current_scene != selection_entry.expected_logical_scene:
            return self._goal_advance_terminal(
                selection_entry=selection_entry,
                trigger="goal_scene_mismatch",
                action_count=0,
                before_sha256=before.world_sha256,
                after_sha256=None,
            )
        if (
            before.status != "ok"
            or before.world_sha256 is None
            or self._scene_reset_fingerprint is None
            or self._snapshot_sha256 is None
        ):
            return self._goal_advance_terminal(
                selection_entry=selection_entry,
                trigger="goal_runtime_failed",
                action_count=0,
                before_sha256=before.world_sha256,
                after_sha256=None,
            )

        try:
            from alfworld.agents.utils.misc import get_templated_task_desc

            task_desc = get_templated_task_desc(traj_data)
        except Exception:
            return self._goal_advance_terminal(
                selection_entry=selection_entry,
                trigger="goal_identity_unreadable",
                action_count=0,
                before_sha256=before.world_sha256,
                after_sha256=None,
            )

        try:
            args = _build_set_task_args(thor_env)
            thor_env.set_task(traj_data, args, reward_type="dense")
        except Exception:
            return self._goal_advance_terminal(
                selection_entry=selection_entry,
                trigger="goal_advance_rejected",
                action_count=1,
                before_sha256=before.world_sha256,
                after_sha256=None,
            )

        after = _external_event_read(
            getattr(thor_env, "last_event", None),
            event_sequence=self._event_sequence,
            thor_env=thor_env,
        )
        if after.status != "ok" or after.world_sha256 is None:
            return self._goal_advance_terminal(
                selection_entry=selection_entry,
                trigger="goal_state_unreadable",
                action_count=1,
                before_sha256=before.world_sha256,
                after_sha256=after.world_sha256,
            )
        if before.world_sha256 != after.world_sha256:
            return self._goal_advance_terminal(
                selection_entry=selection_entry,
                trigger="goal_world_drift",
                action_count=1,
                before_sha256=before.world_sha256,
                after_sha256=after.world_sha256,
            )
        try:
            won = bool(thor_env.get_goal_satisfied())
            pcs = thor_env.get_goal_conditions_met()
            if not isinstance(pcs, tuple | list) or len(pcs) != 2:
                raise ValueError("goal condition state is unreadable")
            goal_rate = float(pcs[0]) / float(pcs[1]) if pcs[1] else 0.0
        except Exception:
            return self._goal_advance_terminal(
                selection_entry=selection_entry,
                trigger="goal_state_unreadable",
                action_count=1,
                before_sha256=before.world_sha256,
                after_sha256=after.world_sha256,
            )

        self._goal_generation += 1
        if isinstance(self._pose_context, OracleExecutionContext):
            self._pose_context = replace(self._pose_context, state="invalid")
        else:
            self._pose_context = None
        new_state = AlfworldEnvState(
            episode_id=f"{self._episode_prefix}/{subtask_label}",
            task=task_desc,
            observation=task_desc,
            inventory=previous.inventory,
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
        self._trial_selection = selection_entry
        return AlfworldGoalAdvanceResult(
            backend_kind="thor",
            ready=True,
            state=new_state,
            scene_generation=self._scene_generation,
            goal_generation=self._goal_generation,
            scene_reset_fingerprint=self._scene_reset_fingerprint,
            goal_trial_fingerprint=selection_entry.goal_fingerprint,
            snapshot_sha256=self._snapshot_sha256,
            before_scene_state_sha256=before.world_sha256,
            after_scene_state_sha256=after.world_sha256,
            advance_trigger=None,
            advance_failure=None,
            classification=None,
            score_eligible=True,
            benchmark_control_action_count=1,
            cleanup_status="not_needed",
            quarantine_required=False,
            environment_disposition="ready",
            evidence_ref="goal-advance.json",
        )

    def _goal_advance_terminal(
        self,
        *,
        selection_entry: TrialSelectionEntry,
        trigger: str,
        action_count: int,
        before_sha256: str | None,
        after_sha256: str | None,
    ) -> AlfworldGoalAdvanceResult:
        cleanup = self.close()
        classification_by_trigger = {
            "expected_goal_trial_mismatch": "artifact_failure",
            "goal_scene_mismatch": "artifact_failure",
            "goal_identity_unreadable": "artifact_failure",
            "goal_advance_rejected": "execution_state_uncertain",
            "goal_state_unreadable": "execution_state_uncertain",
            "goal_world_drift": "execution_state_uncertain",
            "goal_advance_unexpected": "unclassified_execution_failure",
            "goal_runtime_failed": "runtime_failure",
            "goal_cleanup_failed": "runtime_failure",
        }
        classification = classification_by_trigger.get(trigger, "unclassified_execution_failure")
        final_code = "goal_cleanup_failed" if cleanup.status != "succeeded" else trigger
        if final_code == "goal_cleanup_failed":
            classification = "runtime_failure"
        state_uncertain = action_count > 0 or classification in {
            "execution_state_uncertain",
            "runtime_failure",
            "unclassified_execution_failure",
        }
        return AlfworldGoalAdvanceResult(
            backend_kind="thor",
            ready=False,
            state=None,
            scene_generation=self._scene_generation,
            goal_generation=self._goal_generation,
            scene_reset_fingerprint=self._scene_reset_fingerprint,
            goal_trial_fingerprint=selection_entry.goal_fingerprint,
            snapshot_sha256=self._snapshot_sha256,
            before_scene_state_sha256=before_sha256,
            after_scene_state_sha256=after_sha256,
            advance_trigger=trigger,
            advance_failure=final_code,
            classification=classification,
            score_eligible=False,
            benchmark_control_action_count=action_count,
            cleanup_status=cleanup.status,
            quarantine_required=cleanup.status != "succeeded" or state_uncertain,
            environment_disposition=("closed" if cleanup.status == "succeeded" else "quarantined"),
            evidence_ref="goal-advance.json",
        )

    def is_current_goal_satisfied(self) -> bool:
        """Read the current goal's satisfaction from the底层 ThorEnv (external terminal state)."""
        if not self._looks_like_thor_backend():
            return self.current_state.won
        thor_env = self._resolve_thor_env()
        try:
            return bool(thor_env.get_goal_satisfied())
        except Exception:
            return False

    def current_goal_condition_success_rate(self) -> float:
        if not self._looks_like_thor_backend():
            return self.current_state.goal_condition_success_rate
        thor_env = self._resolve_thor_env()
        try:
            pcs = thor_env.get_goal_conditions_met()
            return float(pcs[0]) / float(pcs[1]) if pcs[1] else 0.0
        except Exception:
            return 0.0

    def read_external_state(
        self,
        *,
        held_object_id: str | None = None,
        target_receptacle_id: str | None = None,
        exact_object_id: str | None = None,
        exact_target_id: str | None = None,
    ) -> ExternalRead:
        object_id = exact_object_id or held_object_id
        target_id = exact_target_id or target_receptacle_id
        if not object_id or not target_id:
            return _empty_external_read(status="error")
        try:
            thor_env = self._resolve_thor_env()
        except RuntimeError:
            return _empty_external_read(status="error")
        try:
            return _external_read_from_event(
                thor_env=thor_env,
                event=getattr(thor_env, "last_event", None),
                exact_object_id=object_id,
                exact_target_id=target_id,
                event_sequence=self._event_sequence,
            )
        except Exception:
            return _empty_external_read(status="error")

    def step(
        self,
        command: str,
        *,
        tool_name: str,
        tool_args: dict[str, Any],
    ) -> AlfworldStepResult:
        previous = self.current_state
        self._pose_context = None
        self._event_sequence += 1

        try:
            obs, scores, dones, infos = self._env.step([command])
            observation = str(_first(obs, ""))
            reward = float(_first(scores, 0.0))
            done = bool(_first(dones, False))
            won = bool(_first_info(infos, "won", False))
            goal_rate = float(_first_info(infos, "goal_condition_success_rate", 0.0))
            admissible = tuple(str(item) for item in _first_info(infos, "admissible_commands", []))
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
                state=state,
                execution_feedback=_execution_feedback(
                    tool_name, tool_args, success=False, failure_reason="env_error"
                ),
                feedback=str(exc),
                backend_action_count=1,
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
            state=state,
            execution_feedback=_execution_feedback(
                tool_name,
                tool_args,
                success=not invalid,
                failure_reason="invalid_action" if invalid else None,
            ),
            feedback=observation,
            backend_action_count=1,
        )
