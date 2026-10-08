"""Lifecycle operations for the ALFWorld environment adapter."""

from __future__ import annotations

from dataclasses import replace

from homemaster.alfworld.benchmark._adapter_shared import (
    AlfworldEnvState,
    _AdapterOracleBackend,
    _close_alfworld_env,
    _episode_id_from_gamefile,
    _event_logical_scene,
    _first,
    _first_info,
    _portable_state_fingerprint,
    _unsupported_task_type,
)
from homemaster.alfworld.gateway import CleanupResult
from homemaster.alfworld.pose_snapshot import load_public_object_vocabulary
from homemaster.alfworld.reset_transaction import (
    AlfworldResetTransaction,
    ResetTransactionInput,
)
from homemaster.alfworld.trial_selection import TrialSelectionEntry
from homemaster.alfworld.types import AlfworldResetResult


class AlfworldAdapterLifecycleMixin:
    """reset/close lifecycle for the wrapped batch env."""
    def reset(
        self,
        *,
        selection_entry: TrialSelectionEntry | None = None,
    ) -> AlfworldResetResult:
        if self._lifecycle in {"closed", "quarantined"}:
            raise RuntimeError(f"cannot reset an ALFWorld adapter in {self._lifecycle} state")
        unsupported_task = _unsupported_task_type(selection_entry)
        if unsupported_task is not None:
            result = AlfworldResetResult(
                backend_kind="thor",
                ready=False, state=None, scene_generation=None, goal_generation=None,
                scene_reset_fingerprint=None,
                goal_trial_fingerprint=selection_entry.goal_fingerprint,
                snapshot_sha256=None, snapshot_ref=None, setup_trigger="setup_unexpected",
                setup_failure="setup_unexpected", classification="runtime_failure",
                score_eligible=False, setup_backend_action_count=0, recovery_status="not_needed",
                cleanup_status="not_needed", quarantine_required=False,
                environment_disposition="not_started", evidence_ref=None,
            )
            self._last_reset_result = result
            return result
        try:
            state = self._reset_state()
        except Exception:
            result = AlfworldResetResult(
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
                evidence_ref=None,
            )
            self._last_reset_result = result
            return result

        # Long-horizon tasksets must publish the immutable scene snapshot that
        # advance_goal() compares before and after set_task(). Ordinary
        # episodes keep the lighter reset path unless their selection explicitly
        # comes from the taskset manifest.
        v18_reset_required = bool(
            selection_entry is not None
            and selection_entry.identity_status == "taskset_declared"
        )
        if v18_reset_required:
            self._goal_generation = 1
        self._trial_selection = selection_entry
        if v18_reset_required:
            if selection_entry is None:
                return self._reset_identity_terminal(
                    trigger="reset_identity_unreadable",
                    goal_fingerprint=None,
                )
            try:
                thor_env = self._resolve_thor_env()
                runtime_scene = _event_logical_scene(getattr(thor_env, "last_event", None))
            except RuntimeError:
                runtime_scene = None
            if runtime_scene is None:
                return self._reset_identity_terminal(
                    trigger="reset_identity_unreadable",
                    goal_fingerprint=selection_entry.goal_fingerprint,
                )
            if runtime_scene != selection_entry.expected_logical_scene:
                return self._reset_identity_terminal(
                    trigger="runtime_scene_mismatch",
                    goal_fingerprint=selection_entry.goal_fingerprint,
                )
        goal_fingerprint = (
            selection_entry.goal_fingerprint
            if selection_entry is not None
            else _portable_state_fingerprint({"episode_id": state.episode_id, "task": state.task})
        )
        if not v18_reset_required:
            result = AlfworldResetResult(
                backend_kind="thor",
                ready=True,
                state=state,
                scene_generation=self._scene_generation,
                goal_generation=self._goal_generation,
                scene_reset_fingerprint=_portable_state_fingerprint(
                    {"episode_id": state.episode_id, "task": state.task}
                ),
                goal_trial_fingerprint=goal_fingerprint,
                snapshot_sha256=None,
                snapshot_ref=None,
                setup_trigger=None,
                setup_failure=None,
                classification=None,
                score_eligible=True,
                setup_backend_action_count=0,
                recovery_status="not_needed",
                cleanup_status="not_applicable",
                quarantine_required=False,
                environment_disposition="ready",
                evidence_ref=None,
            )
            self._lifecycle = "ready"
            self._last_reset_result = result
            return result

        backend = _AdapterOracleBackend(self)
        initial_event = backend.capture_event()
        scene_fingerprint = _portable_state_fingerprint(
            {
                "episode_id": state.episode_id,
                "world_sha256": initial_event.world_sha256,
            }
        )
        vocabulary = load_public_object_vocabulary()
        result = AlfworldResetTransaction(
            backend=backend,
            pose_store=self._pose_store,
        ).run(
            ResetTransactionInput(
                backend_kind="thor",
                state=state,
                initial_event=initial_event,
                scene_generation=self._scene_generation,
                goal_generation=self._goal_generation,
                scene_reset_fingerprint=scene_fingerprint,
                goal_trial_fingerprint=goal_fingerprint,
                algorithm_version="v18-bounded-scan-1",
                geometry_policy_version="v18-nearest-yaw-horizon-1",
                setup_time_control_version="change-time-scale-bracket-v1",
                public_semantic_vocabulary=vocabulary.object_types,
                cache_entries=(),
                snapshot_ref="oracle-pose-snapshot.json",
                evidence_ref="reset-transaction.json",
                artifact_root=(self._frame_dir.parent if self._frame_dir is not None else None),
            )
        )
        self._lifecycle = result.environment_disposition
        self._last_reset_result = result
        if not result.ready:
            self._state = None
            self._scene_reset_fingerprint = None
            self._snapshot_sha256 = None
        elif result.state is not None:
            final_state = replace(
                result.state,
                frame_path=self._save_current_frame(step_index=0),
            )
            self._state = final_state
            self._scene_reset_fingerprint = result.scene_reset_fingerprint
            self._snapshot_sha256 = result.snapshot_sha256
            result = replace(result, state=final_state)
            self._last_reset_result = result
            self._refresh_scene_object_index()
        return result

    def _reset_identity_terminal(
        self,
        *,
        trigger: str,
        goal_fingerprint: str | None,
    ) -> AlfworldResetResult:
        cleanup = self.close()
        final_code = trigger
        classification = "execution_state_uncertain"
        if cleanup.status != "succeeded":
            final_code = "scan_cleanup_failed"
            classification = "runtime_failure"
        result = AlfworldResetResult(
            backend_kind="thor",
            ready=False,
            state=None,
            scene_generation=self._scene_generation,
            goal_generation=self._goal_generation,
            scene_reset_fingerprint=None,
            goal_trial_fingerprint=goal_fingerprint,
            snapshot_sha256=None,
            snapshot_ref=None,
            setup_trigger=trigger,
            setup_failure=final_code,
            classification=classification,
            score_eligible=False,
            setup_backend_action_count=0,
            recovery_status="not_needed",
            cleanup_status=cleanup.status,
            quarantine_required=cleanup.status != "succeeded",
            environment_disposition=("closed" if cleanup.status == "succeeded" else "quarantined"),
            evidence_ref=None,
        )
        self._last_reset_result = result
        return result

    def _reset_state(self) -> AlfworldEnvState:
        obs, infos = self._env.reset()
        self._scene_generation += 1
        self._goal_generation = 0
        self._event_sequence = 0
        self._scene_object_index = None
        self._pose_context = None
        self._scene_reset_fingerprint = None
        self._snapshot_sha256 = None
        self._trial_selection = None
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
        self._refresh_scene_object_index()
        return state

    def close(self) -> CleanupResult:
        if self._lifecycle == "closed":
            return CleanupResult(status="succeeded", evidence_ref=None)
        cleanup = _close_alfworld_env(self._env)
        self._state = None
        self._pose_context = None
        self._lifecycle = "closed" if cleanup.status == "succeeded" else "quarantined"
        return cleanup
