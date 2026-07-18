"""Adapter around ALFWorld batch environments for HomeMaster benchmark tools."""

from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
import sys
import time
from collections.abc import Iterator
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from homemaster.benchmarking.alfworld.execution import (
    AgentPose,
    ExecutionBudget,
    ExternalActionResult,
    ExternalRead,
    ManipulationExecutor,
    NavigationAnchorResolver,
    OracleExecutionContext,
    OracleManipulationExecutor,
    OracleNavigationExecutor,
    PoseContext,
    PutExecutionRequest,
    ReadStatus,
    SceneObjectIndex,
    SceneObjectRef,
)
from homemaster.benchmarking.alfworld.gateway import (
    CleanupResult,
    ExternalActionRequest,
    ExternalEventRead,
    OracleActionGateway,
)
from homemaster.benchmarking.alfworld.model_view import (
    AlfworldModelViewObserver,
    FrameLedger,
    VisibleObjectView,
)
from homemaster.benchmarking.alfworld.pose_snapshot import (
    FrozenOraclePoseStore,
    OraclePose,
    SceneObjectScanInput,
    load_public_object_vocabulary,
)
from homemaster.benchmarking.alfworld.reset_transaction import (
    AlfworldResetTransaction,
    ResetTransactionInput,
)
from homemaster.benchmarking.alfworld.trial_selection import (
    TrialSelectionEntry,
    trial_goal_identity,
    trial_logical_scene,
)
from homemaster.benchmarking.alfworld.types import (
    AlfworldBenchmarkConfig,
    AlfworldEnvState,
    AlfworldExecutionFeedback,
    AlfworldGoalAdvanceResult,
    AlfworldResetResult,
    AlfworldStepResult,
    make_execution_feedback,
)


class _NavigationResult(SimpleNamespace):
    success: bool
    feedback: str
    failure_reason: str | None
    budget_stop_reason: str | None
    backend_action_count: int
    event: Any | None
    actual_pose: AgentPose | None
    reachable: list[dict[str, float]]
    trace_events: tuple[dict[str, Any], ...]
    context_id: str | None
    locked_candidates_hash: str | None
    candidates_attempted: int


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

_DEFAULT_NAVIGATION_MAX_CANDIDATES = 65
_DEFAULT_NAVIGATION_MAX_BACKEND_ACTIONS = 66
_DEFAULT_NAVIGATION_MAX_ELAPSED_MS = 34_804.0
_DEFAULT_PUT_MAX_CANDIDATES = 9
_DEFAULT_PUT_MAX_BACKEND_ACTIONS = 17
_DEFAULT_PUT_MAX_ELAPSED_MS = 5_669.0


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
        require_v18_reset: bool = False,
    ) -> None:
        self._env = env
        self._episode_prefix = episode_prefix
        self._seed = seed
        self._frame_dir = frame_dir
        self._require_v18_reset = require_v18_reset
        self._state: AlfworldEnvState | None = None
        self._last_go_to_object_id: str | None = None
        self._scene_generation = 0
        self._goal_generation = 0
        self._event_sequence = 0
        self._scene_object_index: SceneObjectIndex | None = None
        self._pose_context: PoseContext | OracleExecutionContext | None = None
        self._scene_reset_fingerprint: str | None = None
        self._snapshot_sha256: str | None = None
        self._trial_selection: TrialSelectionEntry | None = None
        self._pose_store = FrozenOraclePoseStore()
        self._frame_ledger = FrameLedger()
        self._model_view_observer = AlfworldModelViewObserver(
            frame_ledger=self._frame_ledger
        )
        self._lifecycle = "not_started"
        self._last_reset_result: AlfworldResetResult | None = None
        self._navigation_budget = SimpleNamespace(
            max_navigation_candidates=_DEFAULT_NAVIGATION_MAX_CANDIDATES,
            max_navigation_backend_actions=_DEFAULT_NAVIGATION_MAX_BACKEND_ACTIONS,
            max_navigation_elapsed_ms=_DEFAULT_NAVIGATION_MAX_ELAPSED_MS,
        )
        self._put_budget = ExecutionBudget(
            max_pose_candidates=_DEFAULT_PUT_MAX_CANDIDATES,
            max_backend_actions=_DEFAULT_PUT_MAX_BACKEND_ACTIONS,
            max_elapsed_ms=_DEFAULT_PUT_MAX_ELAPSED_MS,
        )
        self._monotonic_ms = lambda: time.perf_counter() * 1000.0
        if hasattr(self._env, "seed"):
            self._env.seed(seed)

    def set_frame_dir(self, frame_dir: Path | None) -> None:
        self._frame_dir = frame_dir

    @property
    def current_state(self) -> AlfworldEnvState:
        if self._state is None:
            raise RuntimeError("ALFWorld environment has not been reset")
        return self._state

    @property
    def current_pose_context(self) -> PoseContext | OracleExecutionContext | None:
        return self._pose_context

    @property
    def lifecycle(self) -> str:
        return self._lifecycle

    @property
    def last_reset_result(self) -> AlfworldResetResult | None:
        return self._last_reset_result

    @property
    def model_view_observer(self) -> AlfworldModelViewObserver:
        return self._model_view_observer

    def reset(
        self,
        *,
        selection_entry: TrialSelectionEntry | None = None,
    ) -> AlfworldResetResult:
        if self._lifecycle in {"closed", "quarantined"}:
            raise RuntimeError(f"cannot reset an ALFWorld adapter in {self._lifecycle} state")
        try:
            state = self._reset_state()
        except Exception:
            result = AlfworldResetResult(
                backend_kind="thor" if self._looks_like_thor_backend() else "textworld",
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

        if self._require_v18_reset:
            self._goal_generation = 1
        self._trial_selection = selection_entry
        if self._require_v18_reset:
            if selection_entry is None:
                return self._reset_identity_terminal(
                    trigger="reset_identity_unreadable",
                    goal_fingerprint=None,
                )
            try:
                thor_env = self._resolve_thor_env()
                runtime_scene = _event_logical_scene(
                    getattr(thor_env, "last_event", None)
                )
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
            else _portable_state_fingerprint(
                {"episode_id": state.episode_id, "task": state.task}
            )
        )
        if not self._require_v18_reset or not self._looks_like_thor_backend():
            result = AlfworldResetResult(
                backend_kind="textworld",
                ready=True,
                state=state,
                scene_generation=None,
                goal_generation=self._goal_generation,
                scene_reset_fingerprint=None,
                goal_trial_fingerprint=goal_fingerprint,
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
            environment_disposition=(
                "closed" if cleanup.status == "succeeded" else "quarantined"
            ),
            evidence_ref=None,
        )
        self._last_reset_result = result
        return result

    def _reset_state(self) -> AlfworldEnvState:
        obs, infos = self._env.reset()
        self._frame_ledger = FrameLedger()
        self._model_view_observer = AlfworldModelViewObserver(
            frame_ledger=self._frame_ledger
        )
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

    def _looks_like_thor_backend(self) -> bool:
        try:
            thor_env = self._resolve_thor_env()
        except RuntimeError:
            return False
        event = getattr(thor_env, "last_event", None)
        metadata = getattr(event, "metadata", None)
        return callable(getattr(thor_env, "step", None)) and isinstance(metadata, dict)

    def close(self) -> CleanupResult:
        if self._lifecycle == "closed":
            return CleanupResult(status="succeeded", evidence_ref=None)
        cleanup = _close_alfworld_env(self._env)
        self._state = None
        self._pose_context = None
        self._lifecycle = "closed" if cleanup.status == "succeeded" else "quarantined"
        return cleanup

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
            before = _external_event_read(raw_before, event_sequence=self._event_sequence)
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
            environment_disposition=(
                "closed" if cleanup.status == "succeeded" else "quarantined"
            ),
            evidence_ref="goal-advance.json",
        )

    def _refresh_scene_object_index(self) -> None:
        try:
            thor_env = self._resolve_thor_env()
        except RuntimeError:
            return
        event = getattr(thor_env, "last_event", None)
        metadata = getattr(event, "metadata", None)
        if not isinstance(metadata, dict):
            return
        objects = metadata.get("objects")
        if not isinstance(objects, list):
            return
        self._scene_object_index = SceneObjectIndex.from_objects(
            objects=objects,
            scene_generation=self._scene_generation,
            snapshot_event_sequence=self._event_sequence,
        )

    def _indexed_navigation_target(
        self,
        thor_env: Any,
        label: str,
    ) -> _TargetResolutionResult:
        if self._scene_object_index is None:
            self._refresh_scene_object_index()
        if self._scene_object_index is None:
            return _resolve_navigation_target(thor_env, label)
        return _resolve_navigation_target(
            thor_env,
            label,
            scene_index=self._scene_object_index,
        )

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

    def move_to(self, *, pose: AgentPose) -> ExternalActionResult:
        try:
            thor_env = self._resolve_thor_env()
            event = _thor_step(thor_env, _teleport_action_from_pose(pose))
        except Exception as exc:
            return ExternalActionResult(
                status="uncertain",
                raw_event_ref=None,
                raw_event_hash=None,
                detail=str(exc),
            )
        self._event_sequence += 1
        return _external_action_from_event(event, event_sequence=self._event_sequence)

    def put_object(
        self,
        *,
        object_id: str,
        receptacle_object_id: str,
    ) -> ExternalActionResult:
        try:
            thor_env = self._resolve_thor_env()
            event = _thor_step(
                thor_env,
                {
                    "action": "PutObject",
                    "objectId": object_id,
                    "receptacleObjectId": receptacle_object_id,
                    "forceAction": True,
                    "placeStationary": True,
                },
            )
        except Exception as exc:
            return ExternalActionResult(
                status="uncertain",
                raw_event_ref=None,
                raw_event_hash=None,
                detail=str(exc),
            )
        self._event_sequence += 1
        return _external_action_from_event(event, event_sequence=self._event_sequence)

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
        self._pose_context = None

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
                execution_feedback=_legacy_execution_feedback(
                    tool_name, tool_args, success=False, failure_reason="env_error"
                ),
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
            state=state,
            execution_feedback=_legacy_execution_feedback(
                tool_name,
                tool_args,
                success=not invalid,
                failure_reason="invalid_action" if invalid else None,
            ),
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
        self._pose_context = None
        label = target.strip()
        command = f"virtual go to {label}"
        backend_action_count = 0
        try:
            thor_env = self._resolve_thor_env()
            nav_result = _teleport_to_visible_object(thor_env, label)
            backend_action_count = nav_result.backend_action_count
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
            state=state,
            execution_feedback=_legacy_execution_feedback(
                tool_name,
                tool_args,
                success=success,
                failure_reason=None if success else "navigation_target_not_visible",
            ),
            feedback=observation,
            backend_action_count=backend_action_count,
        )

    def go_to_target(
        self,
        target: str,
        *,
        tool_name: str,
        tool_args: dict[str, Any],
    ) -> AlfworldStepResult:
        if self._require_v18_reset:
            return self._go_to_target_v18(
                target,
                tool_name=tool_name,
                tool_args=tool_args,
            )
        previous = self.current_state
        previous_pose_context = self._pose_context
        self._pose_context = None
        label = target.strip()
        command = f"go to target {label}"
        tool_call_id = str(tool_args.get("tool_call_id") or tool_name)
        navigation_context_id = (
            f"navigation-{self._scene_generation}-{self._goal_generation}-"
            f"{self._event_sequence}-{tool_call_id}"
        )
        try:
            thor_env = self._resolve_thor_env()
            resolved = self._indexed_navigation_target(thor_env, label)
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
                state=state,
                execution_feedback=_legacy_execution_feedback(
                    tool_name, tool_args, success=False, failure_reason="env_error"
                ),
                feedback=str(exc),
                trace_events=_empty_execution_trace_events(
                    execution_kind="navigation",
                    context_id=navigation_context_id,
                    scene_generation=self._scene_generation,
                    goal_generation=self._goal_generation,
                    source_event_sequence=self._event_sequence,
                    tool_call_id=tool_call_id,
                    classification="env_error",
                    budget_limit={
                        "candidates": self._navigation_budget.max_navigation_candidates,
                        "backend_actions": (self._navigation_budget.max_navigation_backend_actions),
                        "elapsed_ms": self._navigation_budget.max_navigation_elapsed_ms,
                    },
                ),
            )

        enriched_args = dict(tool_args)
        if resolved.success:
            enriched_args.update(
                {
                    "resolved_kind": resolved.resolved_kind,
                    "resolved_label": resolved.resolved_label,
                    "object_label": resolved.object_label,
                    "object_type": resolved.object_type,
                    "source_receptacle": resolved.source_receptacle,
                    "object_id": resolved.object_id,
                }
            )
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
                state=state,
                execution_feedback=_legacy_execution_feedback(
                    tool_name, enriched_args, success=False, failure_reason="target_not_found"
                ),
                feedback=resolved.feedback,
                trace_events=_empty_execution_trace_events(
                    execution_kind="navigation",
                    context_id=navigation_context_id,
                    scene_generation=self._scene_generation,
                    goal_generation=self._goal_generation,
                    source_event_sequence=self._event_sequence,
                    tool_call_id=tool_call_id,
                    classification="target_not_found",
                    budget_limit={
                        "candidates": self._navigation_budget.max_navigation_candidates,
                        "backend_actions": (self._navigation_budget.max_navigation_backend_actions),
                        "elapsed_ms": self._navigation_budget.max_navigation_elapsed_ms,
                    },
                ),
            )

        try:
            thor_env = self._resolve_thor_env()
            nav = _teleport_to_object_ids(
                thor_env,
                [resolved.object_id],
                navigation_budget=self._navigation_budget,
                monotonic_ms=self._monotonic_ms,
                context_id=navigation_context_id,
                scene_generation=self._scene_generation,
                goal_generation=self._goal_generation,
                source_event_sequence=self._event_sequence,
                tool_call_id=tool_call_id,
            )
            self._event_sequence += nav.backend_action_count
            if nav.budget_stop_reason is not None:
                enriched_args["budget_stop_reason"] = nav.budget_stop_reason
            nav_result = self._state_from_virtual_navigation(
                previous=previous,
                command=f"virtual go to {resolved.resolved_label or label}",
                tool_name=tool_name,
                tool_args=enriched_args,
                success=nav.success,
                failure_reason=nav.failure_reason,
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
                failure_reason="execution_state_uncertain",
                feedback=str(exc),
            )
            nav = _NavigationResult(
                success=False,
                failure_reason="execution_state_uncertain",
                feedback=str(exc),
                budget_stop_reason=None,
                backend_action_count=0,
                event=None,
                actual_pose=None,
                reachable=[],
                trace_events=_empty_execution_trace_events(
                    execution_kind="navigation",
                    context_id=navigation_context_id,
                    scene_generation=self._scene_generation,
                    goal_generation=self._goal_generation,
                    source_event_sequence=self._event_sequence,
                    tool_call_id=tool_call_id,
                    classification="execution_state_uncertain",
                    budget_limit={
                        "candidates": self._navigation_budget.max_navigation_candidates,
                        "backend_actions": (self._navigation_budget.max_navigation_backend_actions),
                        "elapsed_ms": self._navigation_budget.max_navigation_elapsed_ms,
                    },
                ),
                context_id=navigation_context_id,
                locked_candidates_hash=_navigation_candidates_hash(()),
                candidates_attempted=0,
            )
        self._last_go_to_object_id = resolved.object_id if nav_result.success else None
        trace_events = list(getattr(nav, "trace_events", ()))
        if previous_pose_context is not None:
            trace_events.insert(
                0,
                _pose_context_invalidated_trace_event(
                    previous_pose_context,
                    reason="superseded_by_navigation",
                ),
            )
        if nav_result.success and resolved.object_id and nav.event is not None:
            self._pose_context = self._new_pose_context(
                nav=nav,
                anchor_object_id=resolved.object_id,
                tool_name=tool_name,
                tool_args=tool_args,
            )
            if self._pose_context is not None:
                insertion_index = max(0, len(trace_events) - 1)
                trace_events.insert(
                    insertion_index,
                    _pose_context_created_trace_event(self._pose_context),
                )
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
            state=nav_result.state,
            execution_feedback=_legacy_execution_feedback(
                tool_name,
                enriched_args,
                success=nav_result.success,
                failure_reason=nav_result.failure_reason,
            ),
            feedback=feedback,
            backend_action_count=nav.backend_action_count,
            trace_events=tuple(trace_events),
        )

    def _go_to_target_v18(
        self,
        target: str,
        *,
        tool_name: str,
        tool_args: dict[str, Any],
    ) -> AlfworldStepResult:
        previous = self.current_state
        command = f"go to target {target.strip()}"
        try:
            thor_env = self._resolve_thor_env()
            raw_event = getattr(thor_env, "last_event", None)
            backend = _AdapterOracleBackend(self)
            current_event = backend.capture_event()
            if (
                self._scene_object_index is None
                or self._scene_reset_fingerprint is None
                or self._snapshot_sha256 is None
            ):
                raise RuntimeError("V1.8 navigation identity is unavailable")
            result = OracleNavigationExecutor(
                scene_index=self._scene_object_index,
                public_object_types=load_public_object_vocabulary().object_types,
                visible_object_view=VisibleObjectView(
                    event=raw_event,
                    event_sequence=self._event_sequence,
                    committed_view=self._model_view_observer.current_view,
                ),
                current_event=current_event,
                pose_store=self._pose_store,
                parent_resolver=NavigationAnchorResolver(),
                gateway=OracleActionGateway(backend=backend),
            ).execute(
                target,
                scene_generation=self._scene_generation,
                goal_generation=self._goal_generation,
                scene_reset_fingerprint=self._scene_reset_fingerprint,
                snapshot_sha256=self._snapshot_sha256,
            )
        except Exception:
            result = None

        if result is None:
            return AlfworldStepResult(
                tool_name=tool_name,
                tool_args=_model_visible_tool_args(tool_args),
                translated_command=command,
                success=False,
                state=previous,
                execution_feedback=_legacy_execution_feedback(
                    tool_name,
                    tool_args,
                    success=False,
                    failure_reason="execution_state_uncertain",
                ),
                feedback="The current execution state could not be verified.",
            )

        if result.backend_action_count == 0:
            feedback = _safe_navigation_feedback(result.error, target)
            return AlfworldStepResult(
                tool_name=tool_name,
                tool_args=_model_visible_tool_args(tool_args),
                translated_command=command,
                success=False,
                state=previous,
                execution_feedback=_navigation_execution_feedback(result.error, target),
                feedback=feedback,
                backend_action_count=0,
                trace_events=result.trace_events,
            )

        self._pose_context = None
        state_result = self._state_from_virtual_navigation(
            previous=previous,
            command=command,
            tool_name=tool_name,
            tool_args=tool_args,
            success=result.success,
            failure_reason=result.error,
            feedback=(
                f"Reached {target.strip()}."
                if result.success
                else _safe_navigation_feedback(result.error, target)
            ),
        )
        if result.success:
            self._pose_context = result.context
        return replace(
            state_result,
            execution_feedback=_navigation_execution_feedback(
                result.error,
                target,
                success=result.success,
                state_changed=True if result.success else None,
            ),
            backend_action_count=result.backend_action_count,
            trace_events=result.trace_events,
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
        failure_reason: str | None = None,
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
            invalid_action_count=previous.invalid_action_count,
            admissible_commands=previous.admissible_commands,
        )
        self._state = state
        return AlfworldStepResult(
            tool_name=tool_name,
            tool_args=_model_visible_tool_args(tool_args),
            translated_command=command,
            success=success,
            state=state,
            execution_feedback=_legacy_execution_feedback(
                tool_name,
                tool_args,
                success=success,
                failure_reason=(
                    None if success else (failure_reason or "harness_navigation_failure")
                ),
            ),
            feedback=feedback,
        )

    def _new_pose_context(
        self,
        *,
        nav: _NavigationResult,
        anchor_object_id: str,
        tool_name: str,
        tool_args: dict[str, Any],
    ) -> PoseContext | None:
        current_pose = nav.actual_pose
        if current_pose is None:
            return None
        local_candidates = _local_pose_candidates(
            event=nav.event,
            anchor_object_id=anchor_object_id,
            reachable=nav.reachable,
            current_pose=current_pose,
        )
        tool_call_id = str(tool_args.get("tool_call_id") or tool_name)
        return PoseContext.lock(
            context_id=(
                f"pose-{self._scene_generation}-{self._goal_generation}-"
                f"{self._event_sequence}-{anchor_object_id}"
            ),
            scene_generation=self._scene_generation,
            goal_generation=self._goal_generation,
            source_event_sequence=self._event_sequence,
            source_frame_hash=_event_hash(nav.event),
            anchor_object_id=anchor_object_id,
            current_actual_pose=current_pose,
            local_candidates=local_candidates,
            created_tool_call_id=tool_call_id,
        )

    def find_object(
        self,
        target: str,
        *,
        tool_name: str,
        tool_args: dict[str, Any],
    ) -> AlfworldStepResult:
        previous = self.current_state
        self._pose_context = None
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
                state=state,
                execution_feedback=_legacy_execution_feedback(
                    tool_name, tool_args, success=False, failure_reason="env_error"
                ),
                feedback=str(exc),
            )

        enriched_args = dict(tool_args)
        enriched_args.update(
            {
                "object_label": found.object_label,
                "object_type": found.object_type,
                "source_receptacle": found.source_receptacle,
            }
        )
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
                state=state,
                execution_feedback=_legacy_execution_feedback(
                    tool_name,
                    enriched_args,
                    success=False,
                    failure_reason="object_not_found",
                ),
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
            state=nav_result.state,
            execution_feedback=_legacy_execution_feedback(
                tool_name,
                enriched_args,
                success=nav_result.success,
                failure_reason=nav_result.failure_reason,
            ),
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
            admissible = tuple(str(item) for item in _first_info(infos, "admissible_commands", []))
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
                state=state,
                execution_feedback=_legacy_execution_feedback(
                    tool_name, enriched_args, success=True, failure_reason=None
                ),
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
            last_feedback=(f"Could not find a visible {target} at any known navigable place."),
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
            state=state,
            execution_feedback=_legacy_execution_feedback(
                tool_name, tool_args, success=False, failure_reason="object_not_visible"
            ),
            feedback=f"Could not find a visible {target} at any known navigable place.",
        )

    def manipulate_with_thor(
        self,
        *,
        action: str,
        tool_name: str,
        tool_args: dict[str, Any],
    ) -> AlfworldStepResult:
        normalized_action = action.strip().lower()
        if self._require_v18_reset:
            return self._manipulate_with_thor_v18(
                normalized_action,
                tool_name=tool_name,
                tool_args=tool_args,
            )
        if normalized_action == "put":
            return self._manipulate_put_with_executor(
                tool_name=tool_name,
                tool_args=tool_args,
            )

        previous = self.current_state
        self._pose_context = None
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
                state=state,
                execution_feedback=_legacy_execution_feedback(
                    tool_name, tool_args, success=False, failure_reason="env_error"
                ),
                feedback=str(exc),
            )

        enriched_args = dict(tool_args)
        enriched_args.update(
            {
                "backend": "thor_api",
                "backend_actions": result.backend_actions,
            }
        )
        enriched_args.update(
            {key: value for key, value in result.resolved.items() if value is not None}
        )
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
            state=state,
            execution_feedback=_legacy_execution_feedback(
                tool_name,
                enriched_args,
                success=result.success,
                failure_reason=None if result.success else "invalid_action",
            ),
            feedback=result.feedback,
            backend_action_count=len(result.backend_actions),
        )

    def _manipulate_with_thor_v18(
        self,
        action: str,
        *,
        tool_name: str,
        tool_args: dict[str, Any],
    ) -> AlfworldStepResult:
        previous = self.current_state
        allowed = {"take", "open", "close", "put", "use", "slice", "heat", "cool", "clean"}
        if action not in allowed:
            execution_feedback = make_execution_feedback(
                action="verify",
                success=False,
                error="invalid_tool_arguments",
            )
            return AlfworldStepResult(
                tool_name=tool_name,
                tool_args=_model_visible_tool_args(tool_args),
                translated_command=f"thor {action}",
                success=False,
                state=previous,
                execution_feedback=execution_feedback,
                feedback=execution_feedback.to_model_payload()["detail"],
            )
        try:
            thor_env = self._resolve_thor_env()
            raw_event = getattr(thor_env, "last_event", None)
            backend = _AdapterOracleBackend(self)
            current_event = backend.capture_event()
            if self._scene_object_index is None:
                raise RuntimeError("scene object index is unavailable")
            context = (
                self._pose_context
                if isinstance(self._pose_context, OracleExecutionContext)
                else None
            )
            object_label = str(tool_args.get("object") or "").strip() or None
            target_label = str(
                tool_args.get("tool_receptacle")
                or tool_args.get("target_receptacle")
                or ""
            ).strip() or None
            result = OracleManipulationExecutor(
                scene_index=self._scene_object_index,
                visible_object_view=VisibleObjectView(
                    event=raw_event,
                    event_sequence=self._event_sequence,
                    committed_view=self._model_view_observer.current_view,
                ),
                current_event=current_event,
                raw_event=raw_event,
                raw_event_reader=lambda: getattr(thor_env, "last_event", None),
                context=context,
                gateway=OracleActionGateway(backend=backend),
                scene_generation=self._scene_generation,
                goal_generation=self._goal_generation,
            ).execute(
                action,
                object_label=object_label,
                target_label=target_label,
            )
        except Exception:
            execution_feedback = make_execution_feedback(
                action=action,
                success=False,
                error="execution_state_uncertain",
                object_label=str(tool_args.get("object") or "").strip() or None,
                target_label=str(
                    tool_args.get("tool_receptacle")
                    or tool_args.get("target_receptacle")
                    or ""
                ).strip()
                or None,
            )
            result = SimpleNamespace(
                feedback=execution_feedback,
                context=None,
                backend_action_count=0,
                trace_events=(),
            )

        self._pose_context = result.context
        payload = result.feedback.to_model_payload()
        if result.backend_action_count == 0:
            return AlfworldStepResult(
                tool_name=tool_name,
                tool_args=_model_visible_tool_args(tool_args),
                translated_command=f"thor {action}",
                success=result.feedback.success,
                state=previous,
                execution_feedback=result.feedback,
                feedback=payload["detail"],
                backend_action_count=0,
                trace_events=result.trace_events,
            )

        won = self.is_current_goal_satisfied() if result.feedback.success else previous.won
        goal_rate = (
            self.current_goal_condition_success_rate()
            if result.feedback.success
            else previous.goal_condition_success_rate
        )
        inventory = result.feedback.inventory
        state = AlfworldEnvState(
            episode_id=previous.episode_id,
            task=previous.task,
            observation=str(payload["detail"] or "Action completed."),
            inventory=(
                "You are carrying: " + ", ".join(inventory)
                if inventory
                else "You are carrying nothing."
            ),
            last_command=f"thor {action}",
            last_feedback=str(payload["detail"] or "Action completed."),
            reward=previous.reward,
            done=won,
            won=won,
            goal_condition_success_rate=goal_rate,
            frame_path=self._save_current_frame(step_index=previous.step_index + 1),
            step_index=previous.step_index + 1,
            invalid_action_count=previous.invalid_action_count,
            admissible_commands=previous.admissible_commands,
        )
        self._state = state
        return AlfworldStepResult(
            tool_name=tool_name,
            tool_args=_model_visible_tool_args(tool_args),
            translated_command=f"thor {action}",
            success=result.feedback.success,
            state=state,
            execution_feedback=result.feedback,
            feedback=payload["detail"],
            backend_action_count=result.backend_action_count,
            trace_events=result.trace_events,
        )

    def _manipulate_put_with_executor(
        self,
        *,
        tool_name: str,
        tool_args: dict[str, Any],
    ) -> AlfworldStepResult:
        previous = self.current_state
        command = "thor put"
        requested_object = str(tool_args.get("object") or "").strip()
        requested_target = str(tool_args.get("target_receptacle") or "").strip()

        if not requested_object or not requested_target:
            return self._put_step_result(
                previous=previous,
                thor_env=None,
                tool_name=tool_name,
                tool_args=tool_args,
                command=command,
                classification="invalid_tool_arguments",
                feedback="object and target_receptacle are required for put.",
                object_label=requested_object or None,
                target_label=requested_target or None,
                inventory_ids=(),
                object_state="unknown",
            )

        try:
            thor_env = self._resolve_thor_env()
        except RuntimeError as exc:
            self._pose_context = None
            return self._put_step_result(
                previous=previous,
                thor_env=None,
                tool_name=tool_name,
                tool_args=tool_args,
                command=command,
                classification="execution_state_uncertain",
                feedback=str(exc),
                object_label=requested_object,
                target_label=requested_target,
                inventory_ids=(),
                object_state="unknown",
                terminal=True,
                score_eligible=False,
            )

        scene_index = self._scene_object_index
        if scene_index is None or scene_index.scene_generation != self._scene_generation:
            self._scene_object_index = None
            self._refresh_scene_object_index()
            scene_index = self._scene_object_index
        if scene_index is None:
            self._pose_context = None
            return self._put_step_result(
                previous=previous,
                thor_env=thor_env,
                tool_name=tool_name,
                tool_args=tool_args,
                command=command,
                classification="harness_grounding_failure",
                feedback="Could not read the authoritative scene object index.",
                object_label=requested_object,
                target_label=requested_target,
                inventory_ids=(),
                object_state="unknown",
                terminal=True,
                score_eligible=False,
            )

        object_ref = scene_index.resolve(requested_object)
        target_ref = scene_index.resolve(requested_target)
        if object_ref is None or target_ref is None:
            missing_label = requested_object if object_ref is None else requested_target
            return self._put_step_result(
                previous=previous,
                thor_env=thor_env,
                tool_name=tool_name,
                tool_args=tool_args,
                command=command,
                classification="target_not_found",
                feedback=f"No exact scene object matched {missing_label}.",
                object_label=(
                    object_ref.canonical_label if object_ref is not None else requested_object
                ),
                target_label=(
                    target_ref.canonical_label if target_ref is not None else requested_target
                ),
                inventory_ids=_inventory_object_ids(thor_env),
                object_state="unknown",
                held_object_id=object_ref.object_id if object_ref is not None else None,
                target_object_id=target_ref.object_id if target_ref is not None else None,
                scene_index=scene_index,
            )

        object_label = object_ref.canonical_label
        target_label = target_ref.canonical_label
        inventory_ids = _inventory_object_ids(thor_env)
        if object_ref.metadata.get("pickupable") is not True:
            return self._put_step_result(
                previous=previous,
                thor_env=thor_env,
                tool_name=tool_name,
                tool_args=tool_args,
                command=command,
                classification="action_not_applicable",
                feedback=f"{object_label} is not pickupable.",
                object_label=object_label,
                target_label=target_label,
                inventory_ids=inventory_ids,
                object_state="unknown",
                held_object_id=object_ref.object_id,
                target_object_id=target_ref.object_id,
                scene_index=scene_index,
            )
        if target_ref.metadata.get("receptacle") is not True:
            return self._put_step_result(
                previous=previous,
                thor_env=thor_env,
                tool_name=tool_name,
                tool_args=tool_args,
                command=command,
                classification="target_not_receptacle",
                feedback=f"{target_label} is not a receptacle.",
                object_label=object_label,
                target_label=target_label,
                inventory_ids=inventory_ids,
                object_state=("held" if object_ref.object_id in inventory_ids else "not_held"),
                held_object_id=object_ref.object_id,
                target_object_id=target_ref.object_id,
                scene_index=scene_index,
            )

        before = self.read_external_state(
            held_object_id=object_ref.object_id,
            target_receptacle_id=target_ref.object_id,
        )
        if before.status != "ok":
            self._pose_context = None
            return self._put_step_result(
                previous=previous,
                thor_env=thor_env,
                tool_name=tool_name,
                tool_args=tool_args,
                command=command,
                classification="execution_state_uncertain",
                feedback="Could not prove the exact pre-put external state.",
                object_label=object_label,
                target_label=target_label,
                inventory_ids=before.inventory_object_ids,
                object_state="unknown",
                held_object_id=object_ref.object_id,
                target_object_id=target_ref.object_id,
                scene_index=scene_index,
                terminal=True,
                score_eligible=False,
            )
        if (
            before.held_object_id != object_ref.object_id
            or object_ref.object_id not in before.inventory_object_ids
        ):
            self._pose_context = None
            return self._put_step_result(
                previous=previous,
                thor_env=thor_env,
                tool_name=tool_name,
                tool_args=tool_args,
                command=command,
                classification="object_not_held",
                feedback=f"{object_label} is not currently held.",
                object_label=object_label,
                target_label=target_label,
                inventory_ids=before.inventory_object_ids,
                object_state="not_held",
                held_object_id=object_ref.object_id,
                target_object_id=target_ref.object_id,
                scene_index=scene_index,
            )

        pose_context = self._pose_context
        context_is_valid = (
            pose_context is not None
            and pose_context.scene_generation == self._scene_generation
            and pose_context.goal_generation == self._goal_generation
            and pose_context.source_event_sequence == self._event_sequence
            and pose_context.anchor_object_id == target_ref.object_id
            and before.actual_agent_pose is not None
            and before.actual_agent_pose.matches(pose_context.current_actual_pose)
        )
        if not context_is_valid or pose_context is None:
            self._pose_context = None
            return self._put_step_result(
                previous=previous,
                thor_env=thor_env,
                tool_name=tool_name,
                tool_args=tool_args,
                command=command,
                classification="navigation_required",
                feedback=f"Navigate to {target_label} before putting the object.",
                object_label=object_label,
                target_label=target_label,
                inventory_ids=before.inventory_object_ids,
                object_state="held",
                held_object_id=object_ref.object_id,
                target_object_id=target_ref.object_id,
                scene_index=scene_index,
            )

        request = PutExecutionRequest(
            tool_call_id=str(tool_args.get("tool_call_id") or tool_name),
            held_object_id=object_ref.object_id,
            target_receptacle_id=target_ref.object_id,
            pose_context=pose_context,
        )
        execution = ManipulationExecutor(
            backend=self,
            budget=self._put_budget,
            monotonic_ms=self._monotonic_ms,
        ).execute_put(request)
        self._pose_context = None

        final_read = execution.final_read
        final_inventory = (
            final_read.inventory_object_ids
            if final_read is not None
            else _inventory_object_ids(thor_env)
        )
        state_changed = final_read is not None and final_read.action_state != before.action_state
        object_state = (
            "placed"
            if execution.success
            else "held"
            if object_ref.object_id in final_inventory
            else "unknown"
        )
        detail = execution.detail or _event_error(getattr(thor_env, "last_event", None))
        feedback = (
            f"Placed {object_label} on/in {target_label}."
            if execution.success
            else detail
            or (
                "The put result contradicted the external terminal state."
                if execution.classification == "execution_state_uncertain"
                else f"No locked local pose could place {object_label} on/in {target_label}."
            )
        )
        terminal = execution.classification != "success"
        return self._put_step_result(
            previous=previous,
            thor_env=thor_env,
            tool_name=tool_name,
            tool_args=tool_args,
            command=command,
            classification=execution.classification,
            feedback=feedback,
            object_label=object_label,
            target_label=target_label,
            inventory_ids=final_inventory,
            object_state=object_state,
            state_changed=state_changed,
            held_object_id=object_ref.object_id,
            target_object_id=target_ref.object_id,
            scene_index=scene_index,
            locked_candidates_hash=execution.locked_candidates_hash,
            pose_candidates_attempted=execution.pose_candidates_attempted,
            put_attempt_count=execution.put_attempt_count,
            backend_action_count=execution.backend_action_count,
            budget_stop_reason=execution.budget_stop_reason,
            detail=detail,
            terminal=terminal,
            score_eligible=not terminal,
            success=execution.success,
            trace_events=execution.trace_events,
        )

    def _put_step_result(
        self,
        *,
        previous: AlfworldEnvState,
        thor_env: Any | None,
        tool_name: str,
        tool_args: dict[str, Any],
        command: str,
        classification: str,
        feedback: str,
        object_label: str | None,
        target_label: str | None,
        inventory_ids: tuple[str, ...],
        object_state: str,
        state_changed: bool = False,
        held_object_id: str | None = None,
        target_object_id: str | None = None,
        scene_index: SceneObjectIndex | None = None,
        locked_candidates_hash: str | None = None,
        pose_candidates_attempted: int = 0,
        put_attempt_count: int = 0,
        backend_action_count: int = 0,
        budget_stop_reason: str | None = None,
        detail: str = "",
        terminal: bool = False,
        score_eligible: bool = True,
        success: bool = False,
        trace_events: tuple[dict[str, Any], ...] = (),
    ) -> AlfworldStepResult:
        if not trace_events:
            trace_events = _empty_execution_trace_events(
                execution_kind="put",
                context_id=(
                    f"put-{self._scene_generation}-{self._goal_generation}-"
                    f"{self._event_sequence}-{tool_args.get('tool_call_id') or tool_name}"
                ),
                scene_generation=self._scene_generation,
                goal_generation=self._goal_generation,
                source_event_sequence=self._event_sequence,
                tool_call_id=str(tool_args.get("tool_call_id") or tool_name),
                classification="success" if success else classification,
                budget_limit={
                    "candidates": self._put_budget.max_pose_candidates,
                    "backend_actions": self._put_budget.max_backend_actions,
                    "elapsed_ms": self._put_budget.max_elapsed_ms,
                },
                backend_action_count=backend_action_count,
                candidate_count=pose_candidates_attempted,
                put_attempt_count=put_attempt_count,
                budget_stop_reason=budget_stop_reason,
                held_object_id=held_object_id,
                target_receptacle_id=target_object_id,
            )
        enriched_args = dict(tool_args)
        enriched_args.update(
            {
                "action": "put",
                "object": object_label,
                "target": target_label,
                "inventory": _inventory_labels(scene_index, inventory_ids),
                "object_state": object_state,
                "state_changed": state_changed,
                "detail": detail or feedback,
                "final_classification": classification,
                "held_object_id": held_object_id,
                "target_object_id": target_object_id,
                "locked_candidates_hash": locked_candidates_hash,
                "pose_candidates_attempted": pose_candidates_attempted,
                "put_attempt_count": put_attempt_count,
                "backend_action_count": backend_action_count,
                "budget_stop_reason": budget_stop_reason,
                "terminal": terminal,
                "score_eligible": score_eligible,
            }
        )

        if thor_env is not None:
            won = self.is_current_goal_satisfied()
            goal_rate = self.current_goal_condition_success_rate()
            inventory_text = _inventory_text(thor_env)
        else:
            won = previous.won
            goal_rate = previous.goal_condition_success_rate
            inventory_text = previous.inventory
        state = AlfworldEnvState(
            episode_id=previous.episode_id,
            task=previous.task,
            observation=feedback,
            inventory=inventory_text,
            last_command=command,
            last_feedback=feedback,
            reward=previous.reward,
            done=won,
            won=won,
            goal_condition_success_rate=goal_rate,
            frame_path=self._save_current_frame(step_index=previous.step_index + 1),
            step_index=previous.step_index + 1,
            invalid_action_count=previous.invalid_action_count,
            admissible_commands=previous.admissible_commands,
        )
        self._state = state
        return AlfworldStepResult(
            tool_name=tool_name,
            tool_args=_model_visible_tool_args(enriched_args),
            translated_command=command,
            success=success,
            state=state,
            execution_feedback=_legacy_execution_feedback(
                tool_name,
                enriched_args,
                success=success,
                failure_reason=None if success else classification,
            ),
            feedback=feedback,
            backend_action_count=backend_action_count,
            trace_events=trace_events,
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
                obj for obj in objects if str(obj.get("objectType", "")).casefold() == target_type
            ]
            if len(matches) != 1:
                feedback = (
                    f"Expected exactly one {object_type} target in the scene, found {len(matches)}."
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
                    state=state,
                    execution_feedback=_legacy_execution_feedback(
                        tool_name,
                        tool_args,
                        success=False,
                        failure_reason="ambiguous_grounding",
                    ),
                    feedback=feedback,
                )
            target_object_id = str(matches[0]["objectId"])
            event = thor_env.step(
                {
                    "action": "ToggleObjectOn",
                    "objectId": target_object_id,
                    "forceAction": True,
                }
            )
            success = bool(event.metadata.get("lastActionSuccess"))
            feedback = f"You turn on the {object_type.lower()}." if success else "Nothing happens."
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
                state=state,
                execution_feedback=_legacy_execution_feedback(
                    tool_name, tool_args, success=False, failure_reason="env_error"
                ),
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
            state=state,
            execution_feedback=_legacy_execution_feedback(
                tool_name,
                tool_args,
                success=success,
                failure_reason=None if success else "invalid_action",
            ),
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
            self._frame_ledger.record_frame(path, event_sequence=self._event_sequence)
            return str(path)
        except Exception:
            return None


def _is_invalid_feedback(observation: str) -> bool:
    normalized = observation.strip().lower().rstrip(".")
    return normalized == "nothing happens"


def _safe_navigation_feedback(error: str | None, target: str) -> str:
    label = target.strip() or "the requested target"
    templates = {
        "target_not_found": f"{label} is not a supported target.",
        "target_not_visible": f"{label} is not visible in the current view.",
        "object_already_held": f"{label} is already held.",
        "oracle_anchor_unresolved": f"No verified navigation anchor is available for {label}.",
        "oracle_pose_missing": f"No verified navigation pose is available for {label}.",
        "oracle_pose_malformed": f"The verified navigation pose for {label} is invalid.",
        "oracle_navigation_failed": f"Navigation to {label} was rejected.",
        "oracle_pose_mismatch": f"Navigation to {label} did not reach the verified pose.",
        "oracle_target_not_visible": f"{label} was not visible after navigation.",
        "execution_state_uncertain": "The current execution state could not be verified.",
    }
    return templates.get(error, "Navigation could not be completed.")


def _navigation_execution_feedback(
    error: str | None,
    target: str,
    *,
    success: bool = False,
    state_changed: bool | None = False,
) -> AlfworldExecutionFeedback:
    target_state = None
    target_state_status = "not_applicable"
    if success:
        target_state = "visible"
        target_state_status = "ok"
    elif error in {"target_not_visible", "oracle_target_not_visible"}:
        target_state = "not_visible"
        target_state_status = "ok"
    return make_execution_feedback(
        action="navigate",
        success=success,
        error=error,
        target_label=target,
        target_state=target_state,
        target_state_status=target_state_status,
        state_changed=state_changed,
        state_read_status="ok" if state_changed is not None else "not_applicable",
    )


def _legacy_execution_feedback(
    tool_name: str,
    tool_args: dict[str, Any],
    *,
    success: bool,
    failure_reason: str | None,
) -> AlfworldExecutionFeedback:
    raw_action = str(tool_args.get("action") or "").strip().lower()
    allowed_actions = {
        "take",
        "open",
        "close",
        "put",
        "use",
        "slice",
        "heat",
        "cool",
        "clean",
        "verify",
    }
    if raw_action in allowed_actions:
        action = raw_action
    elif tool_name in {"robot_go_to", "robot_navigate", "robot_find_object"}:
        action = "navigate"
    else:
        action = "verify"
    error_map = {
        "env_error": "execution_state_uncertain",
        "invalid_action": "invalid_tool_arguments",
        "navigation_target_not_visible": "target_not_visible",
        "object_not_visible": "target_not_visible",
        "object_not_found": "target_not_found",
        "ambiguous_grounding": "unclassified_execution_failure",
        "harness_navigation_failure": "oracle_navigation_failed",
    }
    closed_errors = {
        "invalid_tool_arguments",
        "unknown_tool",
        "target_not_found",
        "target_not_visible",
        "object_already_held",
        "object_not_held",
        "target_not_receptacle",
        "target_closed",
        "action_not_applicable",
        "navigation_required",
        "oracle_anchor_unresolved",
        "oracle_pose_missing",
        "oracle_pose_malformed",
        "oracle_navigation_failed",
        "oracle_pose_mismatch",
        "oracle_target_not_visible",
        "harness_operation_failure",
        "execution_state_uncertain",
        "unclassified_execution_failure",
    }
    mapped = error_map.get(failure_reason or "", failure_reason)
    if mapped not in closed_errors:
        mapped = "unclassified_execution_failure"
    object_label = str(tool_args.get("object") or "").strip() or None
    target_label = str(
        tool_args.get("target")
        or tool_args.get("target_receptacle")
        or tool_args.get("tool_receptacle")
        or ""
    ).strip() or None
    return make_execution_feedback(
        action=action,
        success=success,
        error=None if success else mapped,
        object_label=object_label,
        target_label=target_label,
    )


def _build_set_task_args(thor_env: Any) -> SimpleNamespace:
    """Build the args namespace ThorEnv.set_task expects (reward_config path).

    Mirrors alfred_thor_env.py:106-112 which sets args.reward_config to the
    bundled config/rewards.json under the alfworld.agents package.
    """
    import alfworld.agents

    args = SimpleNamespace()
    args.reward_config = os.path.join(alfworld.agents.__path__[0], "config", "rewards.json")
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
        event = _thor_step(
            thor_env,
            {
                "action": "PickupObject",
                "objectId": target.object_id,
                "forceAction": True,
            },
        )
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
        event = _thor_step(
            thor_env,
            {
                "action": "PutObject",
                "objectId": str(held.get("objectId", "")),
                "receptacleObjectId": target.object_id,
                "forceAction": True,
                "placeStationary": True,
            },
        )
        return _single_action_result(
            event,
            success_feedback=f"Placed the held object on/in {target.object_type or 'target'}.",
            failure_feedback=(
                f"Could not place the held object on/in {target.object_type or 'target'}."
            ),
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
        event = _thor_step(
            thor_env,
            {
                "action": backend,
                "objectId": target.object_id,
                "forceAction": True,
            },
        )
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
        event = _thor_step(
            thor_env,
            {
                "action": "ToggleObjectOn",
                "objectId": target.object_id,
                "forceAction": True,
            },
        )
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
        {
            "action": "PutObject",
            "objectId": held_id,
            "receptacleObjectId": tool.object_id,
            "forceAction": True,
            "placeStationary": True,
        },
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
        {
            "action": "PutObject",
            "objectId": held_id,
            "receptacleObjectId": tool.object_id,
            "forceAction": True,
            "placeStationary": True,
        },
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
        {
            "action": "PutObject",
            "objectId": held_id,
            "receptacleObjectId": sink.object_id,
            "forceAction": True,
            "placeStationary": True,
        },
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
        matches = [
            obj for obj in matches if obj.get("receptacle") is True or obj.get("openable") is True
        ]
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
        feedback=(
            success_feedback if success else f"{failure_feedback} {_event_error(event)}".strip()
        ),
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


def _inventory_object_ids(thor_env: Any) -> tuple[str, ...]:
    metadata = getattr(thor_env.last_event, "metadata", {})
    inventory = metadata.get("inventoryObjects", []) if isinstance(metadata, dict) else []
    if not isinstance(inventory, list):
        return ()
    return tuple(
        sorted(
            str(item.get("objectId", ""))
            for item in inventory
            if isinstance(item, dict) and str(item.get("objectId", ""))
        )
    )


def _inventory_labels(
    scene_index: SceneObjectIndex | None,
    inventory_object_ids: tuple[str, ...],
) -> list[str]:
    labels_by_id = (
        {
            object_ref.object_id: object_ref.canonical_label
            for object_ref in scene_index.by_canonical_label.values()
        }
        if scene_index is not None
        else {}
    )
    return [
        labels_by_id.get(
            object_id,
            _object_type_key(object_id.split("|", maxsplit=1)[0]) or "object",
        )
        for object_id in inventory_object_ids
    ]


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


def _event_action_status(event: Any) -> str | None:
    metadata = getattr(event, "metadata", None)
    if not isinstance(metadata, dict):
        return None
    value = metadata.get("lastActionSuccess")
    if value is True:
        return "success"
    if value is False:
        return "failure"
    return None


def _event_error(event: Any) -> str:
    metadata = getattr(event, "metadata", {})
    if isinstance(metadata, dict):
        value = metadata.get("errorMessage")
        if isinstance(value, str):
            return value
    return ""


def _event_hash(event: Any) -> str:
    metadata = getattr(event, "metadata", None)
    try:
        encoded = json.dumps(
            metadata,
            allow_nan=False,
            default=str,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError):
        encoded = repr(metadata).encode("utf-8", errors="replace")
    frame = getattr(event, "frame", None)
    tobytes = getattr(frame, "tobytes", None)
    if callable(tobytes):
        encoded += tobytes()
    return hashlib.sha256(encoded).hexdigest()


def _navigation_candidates_hash(
    candidates: tuple[tuple[dict[str, Any], str], ...],
) -> str:
    encoded = json.dumps(
        [
            {
                "target_object_id": target_id,
                "requested_pose": (
                    asdict(pose) if (pose := _agent_pose_from_action(action)) else None
                ),
            }
            for action, target_id in candidates
        ],
        allow_nan=False,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _empty_execution_trace_events(
    *,
    execution_kind: str,
    context_id: str,
    scene_generation: int,
    goal_generation: int,
    source_event_sequence: int,
    tool_call_id: str,
    classification: str,
    budget_limit: dict[str, int | float],
    backend_action_count: int = 0,
    candidate_count: int = 0,
    put_attempt_count: int = 0,
    budget_stop_reason: str | None = None,
    held_object_id: str | None = None,
    target_receptacle_id: str | None = None,
) -> tuple[dict[str, Any], ...]:
    locked_candidates_hash = _navigation_candidates_hash(())
    budget_used: dict[str, int | float] = {
        "candidates": candidate_count,
        "backend_actions": backend_action_count,
        "elapsed_ms": 0.0,
    }
    if execution_kind == "put":
        budget_used["put_attempts"] = put_attempt_count
    common = {
        "execution_kind": execution_kind,
        "context_kind": "navigation" if execution_kind == "navigation" else "pose",
        "tool_call_id": tool_call_id,
        "context_id": context_id,
        "scene_generation": scene_generation,
        "goal_generation": goal_generation,
        "source_event_sequence": source_event_sequence,
        "locked_candidates_hash": locked_candidates_hash,
        "held_object_id": held_object_id,
        "target_receptacle_id": target_receptacle_id,
        "attempt_id": None,
        "attempt_phase": "preflight",
        "budget_limit": dict(budget_limit),
        "budget_used": budget_used,
        "budget_stop_reason": budget_stop_reason,
        "requested_pose": None,
        "actual_pose": None,
        "raw_event_ref": None,
        "raw_event_hash": None,
    }
    return (
        {"event": "context_created", **common, "locked_candidates": []},
        {
            "event": "context_invalidated",
            **common,
            "invalidation_reason": classification,
        },
        {
            "event": "execution_terminal",
            **common,
            "classification": classification,
            "success": classification == "success",
        },
    )


def _pose_context_created_trace_event(context: PoseContext) -> dict[str, Any]:
    return {
        "event": "context_created",
        "execution_kind": "navigation",
        "context_kind": "pose",
        "tool_call_id": context.created_tool_call_id,
        "context_id": context.context_id,
        "scene_generation": context.scene_generation,
        "goal_generation": context.goal_generation,
        "source_event_sequence": context.source_event_sequence,
        "source_frame_hash": context.source_frame_hash,
        "anchor_object_id": context.anchor_object_id,
        "locked_candidates_hash": context.candidates_hash,
        "locked_candidates": [asdict(candidate) for candidate in context.locked_candidates],
        "actual_pose": asdict(context.current_actual_pose),
        "attempt_id": None,
        "attempt_phase": "navigation_success_pose_context",
        "budget_stop_reason": None,
    }


def _pose_context_invalidated_trace_event(
    context: PoseContext,
    *,
    reason: str,
) -> dict[str, Any]:
    return {
        "event": "context_invalidated",
        "execution_kind": "navigation",
        "context_kind": "pose",
        "tool_call_id": context.created_tool_call_id,
        "context_id": context.context_id,
        "scene_generation": context.scene_generation,
        "goal_generation": context.goal_generation,
        "source_event_sequence": context.source_event_sequence,
        "source_frame_hash": context.source_frame_hash,
        "anchor_object_id": context.anchor_object_id,
        "locked_candidates_hash": context.candidates_hash,
        "invalidation_reason": reason,
        "attempt_id": None,
        "attempt_phase": None,
        "budget_stop_reason": None,
    }


def _external_action_from_event(
    event: Any,
    *,
    event_sequence: int,
) -> ExternalActionResult:
    status = _event_action_status(event)
    if status is None:
        return ExternalActionResult(
            status="uncertain",
            raw_event_ref=None,
            raw_event_hash=None,
            detail="External action returned no authoritative status.",
            actual_agent_pose=_agent_pose_from_event(event),
        )
    return ExternalActionResult(
        status=status,
        raw_event_ref=f"event:{event_sequence}",
        raw_event_hash=_event_hash(event),
        detail=_event_error(event),
        actual_agent_pose=_agent_pose_from_event(event),
    )


def _empty_external_read(*, status: ReadStatus) -> ExternalRead:
    return ExternalRead(
        status=status,
        raw_event_ref=None,
        raw_event_hash=None,
        inventory_object_ids=(),
        held_object_id=None,
        exact_object_present=False,
        object_parent_ids=(),
        target_child_ids=(),
        actual_agent_pose=None,
        goal_summary={},
        exact_object_is_picked_up=None,
    )


def _external_read_from_event(
    *,
    thor_env: Any,
    event: Any,
    exact_object_id: str,
    exact_target_id: str,
    event_sequence: int,
) -> ExternalRead:
    if event is None:
        return _empty_external_read(status="missing")
    metadata = getattr(event, "metadata", None)
    if not isinstance(metadata, dict):
        return _empty_external_read(status="error")
    objects = metadata.get("objects")
    inventory = metadata.get("inventoryObjects")
    if not isinstance(objects, list) or not isinstance(inventory, list):
        return _empty_external_read(status="missing")

    exact_object = next(
        (
            item
            for item in objects
            if isinstance(item, dict) and str(item.get("objectId", "")) == exact_object_id
        ),
        None,
    )
    exact_target = next(
        (
            item
            for item in objects
            if isinstance(item, dict) and str(item.get("objectId", "")) == exact_target_id
        ),
        None,
    )
    inventory_ids = tuple(
        sorted(
            str(item.get("objectId", ""))
            for item in inventory
            if isinstance(item, dict) and str(item.get("objectId", ""))
        )
    )
    held_object_id = inventory_ids[0] if inventory_ids else None
    pose = _agent_pose_from_event(event)
    status = (
        "ok"
        if exact_object is not None and exact_target is not None and pose is not None
        else "missing"
    )
    picked_up = exact_object.get("isPickedUp") if exact_object is not None else None
    exact_object_is_picked_up = picked_up if isinstance(picked_up, bool) else None
    try:
        met, total = thor_env.get_goal_conditions_met()
        goal_summary: dict[str, Any] = {"met": int(met), "total": int(total)}
    except Exception:
        goal_summary = {}
    return ExternalRead(
        status=status,
        raw_event_ref=f"event:{event_sequence}",
        raw_event_hash=_event_hash(event),
        inventory_object_ids=inventory_ids,
        held_object_id=held_object_id,
        exact_object_present=exact_object is not None,
        object_parent_ids=_string_tuple(
            exact_object.get("parentReceptacles") if exact_object is not None else None
        ),
        target_child_ids=_string_tuple(
            exact_target.get("receptacleObjectIds") if exact_target is not None else None
        ),
        actual_agent_pose=pose,
        goal_summary=goal_summary,
        exact_object_is_picked_up=exact_object_is_picked_up,
    )


def _string_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list | tuple):
        return ()
    return tuple(str(item) for item in value if isinstance(item, str) and item)


def _teleport_to_visible_object(thor_env: Any, object_type: str) -> _NavigationResult:
    metadata = getattr(thor_env.last_event, "metadata", {})
    objects = metadata.get("objects", []) if isinstance(metadata, dict) else []
    targets = _objects_by_type(objects, object_type)
    return _teleport_to_targets(thor_env, targets, target_name=object_type)


def _teleport_to_object_ids(
    thor_env: Any,
    object_ids: list[str | None],
    *,
    navigation_budget: Any | None = None,
    monotonic_ms: Any | None = None,
    context_id: str | None = None,
    scene_generation: int = 0,
    goal_generation: int = 0,
    source_event_sequence: int = 0,
    tool_call_id: str | None = None,
) -> _NavigationResult:
    target_ids = {str(item) for item in object_ids if isinstance(item, str) and item}
    metadata = getattr(thor_env.last_event, "metadata", {})
    objects = metadata.get("objects", []) if isinstance(metadata, dict) else []
    targets = [
        obj
        for obj in objects
        if isinstance(obj, dict) and str(obj.get("objectId", "")) in target_ids
    ]
    target_name = ", ".join(sorted(target_ids)) if target_ids else "target"
    return _teleport_to_targets(
        thor_env,
        targets,
        target_name=target_name,
        navigation_budget=navigation_budget,
        monotonic_ms=monotonic_ms,
        context_id=context_id,
        scene_generation=scene_generation,
        goal_generation=goal_generation,
        source_event_sequence=source_event_sequence,
        tool_call_id=tool_call_id,
    )


def _teleport_to_targets(
    thor_env: Any,
    targets: list[dict[str, Any]],
    *,
    target_name: str,
    navigation_budget: Any | None = None,
    monotonic_ms: Any | None = None,
    context_id: str | None = None,
    scene_generation: int = 0,
    goal_generation: int = 0,
    source_event_sequence: int = 0,
    tool_call_id: str | None = None,
) -> _NavigationResult:
    budget = navigation_budget or SimpleNamespace(
        max_navigation_candidates=_DEFAULT_NAVIGATION_MAX_CANDIDATES,
        max_navigation_backend_actions=_DEFAULT_NAVIGATION_MAX_BACKEND_ACTIONS,
        max_navigation_elapsed_ms=_DEFAULT_NAVIGATION_MAX_ELAPSED_MS,
    )
    clock = monotonic_ms or (lambda: time.perf_counter() * 1000.0)
    started_ms = float(clock())
    trace_events: list[dict[str, Any]] = []
    attempted_count = 0
    backend_action_count = 0
    locked_candidates: tuple[tuple[dict[str, Any], str], ...] = ()
    locked_candidates_hash = _navigation_candidates_hash(locked_candidates)
    resolved_context_id = context_id or (
        f"navigation-{scene_generation}-{goal_generation}-{source_event_sequence}"
    )

    def budget_limit() -> dict[str, int | float]:
        return {
            "candidates": int(budget.max_navigation_candidates),
            "backend_actions": int(budget.max_navigation_backend_actions),
            "elapsed_ms": float(budget.max_navigation_elapsed_ms),
        }

    def budget_used() -> dict[str, int | float]:
        return {
            "candidates": attempted_count,
            "backend_actions": backend_action_count,
            "elapsed_ms": max(0.0, float(clock()) - started_ms),
        }

    def add_trace(event_name: str, **details: Any) -> None:
        payload: dict[str, Any] = {
            "event": event_name,
            "execution_kind": "navigation",
            "context_kind": "navigation",
            "tool_call_id": tool_call_id,
            "context_id": resolved_context_id,
            "scene_generation": scene_generation,
            "goal_generation": goal_generation,
            "source_event_sequence": source_event_sequence,
            "locked_candidates_hash": locked_candidates_hash,
            "attempt_id": None,
            "attempt_phase": None,
            "budget_limit": budget_limit(),
            "budget_used": budget_used(),
            "budget_stop_reason": None,
        }
        payload.update(details)
        trace_events.append(payload)

    def finish(
        *,
        success: bool,
        feedback: str,
        failure_reason: str | None,
        budget_stop_reason: str | None,
        event: Any | None,
        actual_pose: AgentPose | None,
        reachable: list[dict[str, float]],
    ) -> _NavigationResult:
        add_trace(
            "context_invalidated",
            invalidation_reason=(
                "navigation_completed" if success else budget_stop_reason or failure_reason
            ),
        )
        add_trace(
            "execution_terminal",
            classification="success" if success else failure_reason,
            success=success,
            budget_stop_reason=budget_stop_reason,
            actual_pose=asdict(actual_pose) if actual_pose is not None else None,
            raw_event_ref=(
                f"event:{source_event_sequence + backend_action_count}"
                if event is not None and backend_action_count > 0
                else None
            ),
            raw_event_hash=_event_hash(event) if event is not None else None,
        )
        return _NavigationResult(
            success=success,
            feedback=feedback,
            failure_reason=failure_reason,
            budget_stop_reason=budget_stop_reason,
            backend_action_count=backend_action_count,
            event=event,
            actual_pose=actual_pose,
            reachable=reachable,
            trace_events=tuple(trace_events),
            context_id=resolved_context_id,
            locked_candidates_hash=locked_candidates_hash,
            candidates_attempted=attempted_count,
        )

    if not targets:
        add_trace("context_created", locked_candidates=[])
        return finish(
            success=False,
            feedback=f"No {target_name} navigation target found in the scene.",
            failure_reason="harness_navigation_failure",
            budget_stop_reason=None,
            event=None,
            actual_pose=None,
            reachable=[],
        )

    metadata = getattr(thor_env.last_event, "metadata", {})
    state_read_started_ms = float(clock())
    add_trace(
        "state_read_started",
        attempt_phase="reachable_positions",
    )
    backend_action_count = 1
    try:
        reachable = _reachable_positions(thor_env)
    except Exception as exc:
        event = getattr(thor_env, "last_event", None)
        add_trace(
            "state_read_result",
            attempt_phase="reachable_positions",
            external_status="error",
            raw_event_ref=(
                f"event:{source_event_sequence + backend_action_count}"
                if event is not None
                else None
            ),
            raw_event_hash=_event_hash(event) if event is not None else None,
            state_read_elapsed_ms=max(0.0, float(clock()) - state_read_started_ms),
        )
        add_trace("context_created", locked_candidates=[])
        return finish(
            success=False,
            feedback=str(exc),
            failure_reason="execution_state_uncertain",
            budget_stop_reason=None,
            event=None,
            actual_pose=None,
            reachable=[],
        )
    reachable_event = getattr(thor_env, "last_event", None)
    add_trace(
        "state_read_result",
        attempt_phase="reachable_positions",
        external_status="success",
        raw_event_ref=f"event:{source_event_sequence + backend_action_count}",
        raw_event_hash=(_event_hash(reachable_event) if reachable_event is not None else None),
        state_read_elapsed_ms=max(0.0, float(clock()) - state_read_started_ms),
    )
    if not reachable:
        add_trace("context_created", locked_candidates=[])
        return finish(
            success=False,
            feedback="Navigation backend could not read reachable positions.",
            failure_reason="harness_navigation_failure",
            budget_stop_reason=None,
            event=None,
            actual_pose=None,
            reachable=[],
        )

    agent_y = _agent_height(metadata)
    locked_candidates = tuple(_teleport_candidates(targets, reachable, agent_y=agent_y))
    locked_candidates_hash = _navigation_candidates_hash(locked_candidates)
    add_trace(
        "context_created",
        anchor_object_ids=sorted({target_id for _action, target_id in locked_candidates}),
        locked_candidates=[
            {
                "target_object_id": target_id,
                "requested_pose": (
                    asdict(pose) if (pose := _agent_pose_from_action(action)) else None
                ),
            }
            for action, target_id in locked_candidates
        ],
    )
    for candidate_index, (action, target_id) in enumerate(locked_candidates, start=1):
        stop_reason = _navigation_budget_stop(
            budget=budget,
            attempted_count=attempted_count,
            backend_action_count=backend_action_count,
            elapsed_ms=max(0.0, float(clock()) - started_ms),
        )
        if stop_reason is not None:
            final_event = getattr(thor_env, "last_event", None)
            return finish(
                success=False,
                feedback=f"Navigation stopped at fixed budget: {stop_reason}.",
                failure_reason="harness_navigation_failure",
                budget_stop_reason=stop_reason,
                event=final_event,
                actual_pose=_agent_pose_from_event(final_event),
                reachable=reachable,
            )

        requested_pose = _agent_pose_from_action(action)
        before_pose = _agent_pose_from_event(getattr(thor_env, "last_event", None))
        attempted_count += 1
        attempt_id = f"{resolved_context_id}:attempt-{candidate_index:04d}"
        add_trace(
            "attempt_started",
            attempt_id=attempt_id,
            attempt_phase="navigation_candidate",
            requested_pose=asdict(requested_pose) if requested_pose is not None else None,
            actual_pose=asdict(before_pose) if before_pose is not None else None,
            anchor_object_id=target_id,
        )
        move_started_ms = float(clock())
        add_trace(
            "move_started",
            attempt_id=attempt_id,
            attempt_phase="navigation_candidate",
            requested_pose=asdict(requested_pose) if requested_pose is not None else None,
            anchor_object_id=target_id,
        )
        backend_action_count += 1
        try:
            event = thor_env.step(action)
        except Exception as exc:
            add_trace(
                "move_result",
                attempt_id=attempt_id,
                attempt_phase="navigation_candidate",
                requested_pose=(asdict(requested_pose) if requested_pose is not None else None),
                actual_pose=None,
                external_status="uncertain",
                raw_event_ref=None,
                raw_event_hash=None,
                move_elapsed_ms=max(0.0, float(clock()) - move_started_ms),
            )
            add_trace(
                "observation_read_result",
                attempt_id=attempt_id,
                attempt_phase="navigation_candidate",
                observation_status="not_evaluated",
                raw_event_ref=None,
                raw_event_hash=None,
            )
            return finish(
                success=False,
                feedback=str(exc),
                failure_reason="execution_state_uncertain",
                budget_stop_reason=None,
                event=None,
                actual_pose=None,
                reachable=reachable,
            )
        if event is None:
            add_trace(
                "move_result",
                attempt_id=attempt_id,
                attempt_phase="navigation_candidate",
                requested_pose=(asdict(requested_pose) if requested_pose is not None else None),
                actual_pose=None,
                external_status="uncertain",
                raw_event_ref=None,
                raw_event_hash=None,
                move_elapsed_ms=max(0.0, float(clock()) - move_started_ms),
            )
            add_trace(
                "observation_read_result",
                attempt_id=attempt_id,
                attempt_phase="navigation_candidate",
                observation_status="not_evaluated",
                raw_event_ref=None,
                raw_event_hash=None,
            )
            return finish(
                success=False,
                feedback="TeleportFull returned no event.",
                failure_reason="execution_state_uncertain",
                budget_stop_reason=None,
                event=None,
                actual_pose=None,
                reachable=reachable,
            )

        action_status = _event_action_status(event)
        actual_pose = _agent_pose_from_event(
            event,
            requested_pose=requested_pose,
            allow_partial_requested_fallback=True,
        )
        raw_event_ref = f"event:{source_event_sequence + backend_action_count}"
        raw_event_hash = _event_hash(event)
        add_trace(
            "move_result",
            attempt_id=attempt_id,
            attempt_phase="navigation_candidate",
            requested_pose=asdict(requested_pose) if requested_pose is not None else None,
            actual_pose=asdict(actual_pose) if actual_pose is not None else None,
            external_status=action_status or "uncertain",
            raw_event_ref=raw_event_ref,
            raw_event_hash=raw_event_hash,
            move_elapsed_ms=max(0.0, float(clock()) - move_started_ms),
        )
        if action_status is None or requested_pose is None or actual_pose is None:
            add_trace(
                "observation_read_result",
                attempt_id=attempt_id,
                attempt_phase="navigation_candidate",
                observation_status="not_evaluated",
                raw_event_ref=raw_event_ref,
                raw_event_hash=raw_event_hash,
            )
            return finish(
                success=False,
                feedback="Could not prove TeleportFull return or actual pose.",
                failure_reason="execution_state_uncertain",
                budget_stop_reason=None,
                event=event,
                actual_pose=actual_pose,
                reachable=reachable,
            )
        if action_status == "success" and not actual_pose.matches(requested_pose):
            add_trace(
                "observation_read_result",
                attempt_id=attempt_id,
                attempt_phase="navigation_candidate",
                observation_status="not_evaluated",
                raw_event_ref=raw_event_ref,
                raw_event_hash=raw_event_hash,
            )
            return finish(
                success=False,
                feedback="TeleportFull succeeded but actual pose did not match the request.",
                failure_reason="execution_state_uncertain",
                budget_stop_reason=None,
                event=event,
                actual_pose=actual_pose,
                reachable=reachable,
            )
        if action_status == "failure":
            if before_pose is None or not actual_pose.matches(before_pose):
                add_trace(
                    "observation_read_result",
                    attempt_id=attempt_id,
                    attempt_phase="navigation_candidate",
                    observation_status="not_evaluated",
                    raw_event_ref=raw_event_ref,
                    raw_event_hash=raw_event_hash,
                )
                return finish(
                    success=False,
                    feedback="TeleportFull failed but actual pose changed or was unreadable.",
                    failure_reason="execution_state_uncertain",
                    budget_stop_reason=None,
                    event=event,
                    actual_pose=actual_pose,
                    reachable=reachable,
                )
            add_trace(
                "observation_read_result",
                attempt_id=attempt_id,
                attempt_phase="navigation_candidate",
                observation_status="not_evaluated",
                raw_event_ref=raw_event_ref,
                raw_event_hash=raw_event_hash,
            )
            continue

        observation_started_ms = float(clock())
        observation = _exact_target_observation(event, target_id)
        if observation is None:
            add_trace(
                "observation_read_result",
                attempt_id=attempt_id,
                attempt_phase="navigation_candidate",
                observation_status="missing",
                raw_event_ref=raw_event_ref,
                raw_event_hash=raw_event_hash,
                state_read_elapsed_ms=max(0.0, float(clock()) - observation_started_ms),
            )
            return finish(
                success=False,
                feedback="The exact navigation target was missing from the final event.",
                failure_reason="execution_state_uncertain",
                budget_stop_reason=None,
                event=event,
                actual_pose=actual_pose,
                reachable=reachable,
            )
        exact_visible, exact_detected, bbox_area = observation
        add_trace(
            "observation_read_result",
            attempt_id=attempt_id,
            attempt_phase="navigation_candidate",
            observation_status="ok",
            exact_target_visible=exact_visible,
            exact_target_detected=exact_detected,
            bbox_area=bbox_area,
            raw_event_ref=raw_event_ref,
            raw_event_hash=raw_event_hash,
            state_read_elapsed_ms=max(0.0, float(clock()) - observation_started_ms),
        )
        if exact_visible and exact_detected and bbox_area > 0:
            return finish(
                success=True,
                feedback="Navigation target passed the exact observation gate.",
                failure_reason=None,
                budget_stop_reason=None,
                event=event,
                actual_pose=actual_pose,
                reachable=reachable,
            )

    final_event = getattr(thor_env, "last_event", None)
    return finish(
        success=False,
        feedback=f"Navigation candidates were exhausted for {target_name}.",
        failure_reason="harness_navigation_failure",
        budget_stop_reason="candidates_exhausted",
        event=final_event,
        actual_pose=_agent_pose_from_event(final_event),
        reachable=reachable,
    )


def _find_object_location(thor_env: Any, object_type: str) -> _ObjectLocationResult:
    metadata = getattr(thor_env.last_event, "metadata", {})
    objects = metadata.get("objects", []) if isinstance(metadata, dict) else []
    targets = [
        obj for obj in _objects_by_type(objects, object_type) if obj.get("pickupable") is True
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


def _resolve_navigation_target(
    thor_env: Any,
    target: str,
    *,
    scene_index: SceneObjectIndex | None = None,
) -> _TargetResolutionResult:
    metadata = getattr(thor_env.last_event, "metadata", {})
    objects = metadata.get("objects", []) if isinstance(metadata, dict) else []
    target_obj: dict[str, Any] | None = None
    if scene_index is not None:
        indexed = _resolve_scene_object_ref(scene_index, target)
        if indexed is not None:
            target_obj = next(
                (
                    obj
                    for obj in objects
                    if isinstance(obj, dict) and str(obj.get("objectId", "")) == indexed.object_id
                ),
                indexed.metadata,
            )
    else:
        matches = _objects_by_type(objects, target)
        if matches:
            target_obj = _choose_object_target(matches)
    if target_obj is None:
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

    pickupable = target_obj.get("pickupable") is True
    if pickupable:
        resolved_kind = "movable_object"
    else:
        resolved_kind = (
            "toggle_object" if bool(target_obj.get("toggleable")) else "receptacle_or_fixture"
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


def _resolve_scene_object_ref(
    scene_index: SceneObjectIndex,
    target: str,
) -> SceneObjectRef | None:
    direct = scene_index.resolve(target)
    if direct is not None:
        return direct
    words = target.casefold().strip().split()
    instance = words[-1] if words and words[-1].isdigit() else None
    base = " ".join(words[:-1] if instance is not None else words)
    aliases = _OBJECT_TYPE_ALIASES.get(_object_query_key(base), ())
    for alias in aliases:
        candidate = f"{alias} {instance}" if instance is not None else alias
        resolved = scene_index.resolve(candidate)
        if resolved is not None:
            return resolved
    return None


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
    object_by_id = {str(obj.get("objectId", "")): obj for obj in objects if isinstance(obj, dict)}
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
            obj
            for obj in objects
            if isinstance(obj, dict) and _command_type_name(obj) == command_type
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
        obj
        for obj in typed_objects
        if full_query_key and full_query_key in _exact_object_keys(objects, obj)
    ]
    if exact_matches:
        return exact_matches

    type_matches = [
        obj
        for obj in typed_objects
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
            obj
            for obj in typed_objects
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
        obj
        for obj in objects
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
        source = command[len(prefix) :].strip()
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
    if event is None:
        raise RuntimeError("GetReachablePositions returned no event")
    metadata = getattr(event, "metadata", None)
    if not isinstance(metadata, dict):
        raise RuntimeError("GetReachablePositions returned unreadable metadata")
    if metadata.get("lastActionSuccess") is not True:
        raise RuntimeError(
            _event_error(event) or "GetReachablePositions was rejected by the backend"
        )
    positions = metadata.get("reachablePositions")
    if not isinstance(positions, list):
        positions = metadata.get("actionReturn")
    if not isinstance(positions, list):
        raise RuntimeError("GetReachablePositions returned no position list")
    reachable: list[dict[str, float]] = []
    for position in positions:
        if not isinstance(position, dict):
            continue
        try:
            reachable.append(
                {
                    "x": float(position["x"]),
                    "z": float(position["z"]),
                }
            )
        except (KeyError, TypeError, ValueError):
            continue
    return reachable


def _navigation_budget_stop(
    *,
    budget: Any,
    attempted_count: int,
    backend_action_count: int,
    elapsed_ms: float,
) -> str | None:
    if backend_action_count >= int(budget.max_navigation_backend_actions):
        return "max_navigation_backend_actions"
    if elapsed_ms >= float(budget.max_navigation_elapsed_ms):
        return "max_navigation_elapsed_ms"
    if attempted_count >= int(budget.max_navigation_candidates):
        return "max_navigation_candidates"
    return None


def _agent_pose_from_action(action: dict[str, Any]) -> AgentPose | None:
    try:
        return AgentPose(
            x=float(action["x"]),
            y=float(action["y"]),
            z=float(action["z"]),
            rotation=float(action["rotation"]),
            horizon=float(action["horizon"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _agent_pose_from_event(
    event: Any,
    *,
    requested_pose: AgentPose | None = None,
    allow_partial_requested_fallback: bool = False,
) -> AgentPose | None:
    metadata = getattr(event, "metadata", None)
    if not isinstance(metadata, dict):
        return None
    agent = metadata.get("agent")
    if not isinstance(agent, dict):
        return None
    position = agent.get("position")
    rotation = agent.get("rotation")
    try:
        if not isinstance(position, dict) or not isinstance(rotation, dict):
            raise KeyError("agent pose mapping missing")
        return AgentPose(
            x=float(position["x"]),
            y=float(position["y"]),
            z=float(position["z"]),
            rotation=float(rotation["y"]),
            horizon=float(agent["cameraHorizon"]),
        )
    except (KeyError, TypeError, ValueError):
        if (
            allow_partial_requested_fallback
            and requested_pose is not None
            and isinstance(position, dict)
            and "y" in position
        ):
            try:
                return AgentPose(
                    x=requested_pose.x,
                    y=float(position["y"]),
                    z=requested_pose.z,
                    rotation=requested_pose.rotation,
                    horizon=requested_pose.horizon,
                )
            except (TypeError, ValueError):
                return None
        return None


def _teleport_action_from_pose(pose: AgentPose) -> dict[str, Any]:
    return {
        "action": "TeleportFull",
        "x": pose.x,
        "y": pose.y,
        "z": pose.z,
        "rotateOnTeleport": True,
        "rotation": pose.rotation,
        "horizon": pose.horizon,
    }


def _exact_target_observation(
    event: Any,
    object_id: str,
) -> tuple[bool, bool, float] | None:
    metadata = getattr(event, "metadata", None)
    if not isinstance(metadata, dict):
        return None
    objects = metadata.get("objects")
    if not isinstance(objects, list):
        return None
    target = next(
        (
            item
            for item in objects
            if isinstance(item, dict) and str(item.get("objectId", "")) == object_id
        ),
        None,
    )
    if target is None:
        return None
    detections = getattr(event, "instance_detections2D", None)
    box = detections.get(object_id) if isinstance(detections, dict) else None
    area = _bbox_area_score(box)
    return target.get("visible") is True, box is not None, area


def _local_pose_candidates(
    *,
    event: Any,
    anchor_object_id: str,
    reachable: list[dict[str, float]],
    current_pose: AgentPose,
) -> tuple[AgentPose, ...]:
    metadata = getattr(event, "metadata", None)
    objects = metadata.get("objects") if isinstance(metadata, dict) else None
    if not isinstance(objects, list):
        return ()
    target = next(
        (
            item
            for item in objects
            if isinstance(item, dict) and str(item.get("objectId", "")) == anchor_object_id
        ),
        None,
    )
    if target is None:
        return ()
    actions = _single_target_teleport_candidates(
        target,
        reachable,
        agent_y=current_pose.y,
    )
    poses: list[AgentPose] = []
    for action, _distance in actions:
        pose = _agent_pose_from_action(action)
        if pose is None or pose.matches(current_pose):
            continue
        if pose not in poses:
            poses.append(pose)
    poses.sort(
        key=lambda pose: (
            (pose.x - current_pose.x) ** 2 + (pose.z - current_pose.z) ** 2,
            abs((pose.rotation - current_pose.rotation + 180.0) % 360.0 - 180.0),
            abs(pose.horizon - current_pose.horizon),
            pose.x,
            pose.z,
        )
    )
    return tuple(poses)


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
        rotations = _ordered_unique_ints(
            [
                base_rotation,
                base_rotation - 90,
                base_rotation + 90,
                base_rotation + 180,
                0,
                90,
                180,
                270,
            ],
            modulo=360,
        )
        horizons = _ordered_unique_ints(
            [
                base_horizon,
                base_horizon - 15,
                base_horizon + 15,
                0,
                15,
                30,
                45,
                60,
            ]
        )
        for rotation in rotations:
            for horizon in horizons:
                if horizon < -30 or horizon > 60:
                    continue
                key = (round(point["x"], 3), round(point["z"], 3), rotation, horizon)
                if key in seen:
                    continue
                seen.add(key)
                actions.append(
                    (
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
                    )
                )
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


class _AdapterOracleBackend:
    def __init__(self, adapter: AlfworldEnvAdapter) -> None:
        self._adapter = adapter

    def capture_event(self) -> ExternalEventRead:
        thor_env = self._adapter._resolve_thor_env()
        return _external_event_read(
            getattr(thor_env, "last_event", None),
            event_sequence=self._adapter._event_sequence,
        )

    def send(self, request: ExternalActionRequest) -> ExternalEventRead:
        thor_env = self._adapter._resolve_thor_env()
        event = thor_env.step(dict(request.payload))
        self._adapter._event_sequence += 1
        return _external_event_read(event, event_sequence=self._adapter._event_sequence)

    def close(self) -> CleanupResult:
        return _close_alfworld_env(self._adapter._env)


def _event_logical_scene(event: Any) -> str | None:
    metadata = getattr(event, "metadata", None)
    if not isinstance(metadata, dict):
        return None
    scene_name = metadata.get("sceneName")
    if (
        not isinstance(scene_name, str)
        or not scene_name.startswith("FloorPlan")
        or not scene_name.endswith("_physics")
    ):
        return None
    return scene_name.removesuffix("_physics")


def _external_event_read(event: Any, *, event_sequence: int) -> ExternalEventRead:
    metadata = getattr(event, "metadata", None)
    if not isinstance(metadata, dict):
        return ExternalEventRead(
            status="malformed",
            returned_action=None,
            action_success=None,
            pose=None,
            world_sha256=None,
            visibility_sha256=None,
            frame_sha256=None,
            objects=None,
            reachable_payload=None,
            strict_visible_exact_ids=(),
            bbox_areas=(),
            raw_event_ref=None,
            raw_event_sha256=None,
        )
    try:
        pose = _oracle_pose_from_event(event)
        world_sha256 = _event_world_sha256(metadata)
        frame_sha256 = _frame_sha256(getattr(event, "frame", None))
        bbox_areas = _event_bbox_areas(event)
        visible_ids = {
            str(item["objectId"])
            for item in metadata.get("objects", [])
            if isinstance(item, dict)
            and isinstance(item.get("objectId"), str)
            and item.get("visible") is True
        }
        area_ids = {object_id for object_id, area in bbox_areas if area > 0}
        strict_visible = tuple(sorted(visible_ids & area_ids))
        visibility_sha256 = _portable_state_fingerprint(
            {"strict_visible_exact_ids": strict_visible, "bbox_areas": bbox_areas}
        )
        objects = _scene_scan_inputs(metadata.get("objects"))
        action = metadata.get("lastAction")
        returned_action = action if isinstance(action, str) and action else None
        action_success = metadata.get("lastActionSuccess")
        if not isinstance(action_success, bool):
            action_success = None
        reachable_payload = None
        if returned_action == "GetReachablePositions":
            reachable = metadata.get("actionReturn")
            if not isinstance(reachable, list):
                reachable = metadata.get("reachablePositions")
            if isinstance(reachable, list):
                reachable_payload = json.dumps(
                    reachable,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                    allow_nan=False,
                ).encode("utf-8")
        return ExternalEventRead(
            status="ok",
            returned_action=returned_action,
            action_success=action_success,
            pose=pose,
            world_sha256=world_sha256,
            visibility_sha256=visibility_sha256,
            frame_sha256=frame_sha256,
            objects=objects,
            reachable_payload=reachable_payload,
            strict_visible_exact_ids=strict_visible,
            bbox_areas=bbox_areas,
            raw_event_ref=f"events/{event_sequence:04d}-{returned_action or 'capture'}.json",
            raw_event_sha256=_event_hash(event),
        )
    except (KeyError, TypeError, ValueError, OverflowError):
        return ExternalEventRead(
            status="malformed",
            returned_action=None,
            action_success=None,
            pose=None,
            world_sha256=None,
            visibility_sha256=None,
            frame_sha256=None,
            objects=None,
            reachable_payload=None,
            strict_visible_exact_ids=(),
            bbox_areas=(),
            raw_event_ref=f"events/{event_sequence:04d}-malformed.json",
            raw_event_sha256=_event_hash(event),
        )


def _oracle_pose_from_event(event: Any) -> OraclePose | None:
    pose = _agent_pose_from_event(event)
    if pose is None:
        return None
    return OraclePose(
        x=pose.x,
        y=pose.y,
        z=pose.z,
        rotation=pose.rotation,
        horizon=pose.horizon,
    )


def _scene_scan_inputs(value: Any) -> tuple[SceneObjectScanInput, ...] | None:
    if not isinstance(value, list):
        return None
    objects: dict[str, dict[str, Any]] = {}
    for item in value:
        if not isinstance(item, dict):
            return None
        object_id = item.get("objectId")
        object_type = item.get("objectType")
        position = item.get("position")
        if (
            not isinstance(object_id, str)
            or not object_id
            or not isinstance(object_type, str)
            or not object_type
            or not isinstance(position, dict)
        ):
            return None
        objects[object_id] = item

    def closed_ancestors(object_id: str) -> tuple[str, ...]:
        found: set[str] = set()
        pending = list(_string_tuple(objects[object_id].get("parentReceptacles")))
        visited: set[str] = set()
        while pending:
            parent_id = pending.pop()
            if parent_id in visited:
                continue
            visited.add(parent_id)
            parent = objects.get(parent_id)
            if parent is None:
                raise ValueError(f"unknown parent receptacle: {parent_id}")
            if parent.get("openable") is True and parent.get("isOpen") is False:
                found.add(parent_id)
            pending.extend(_string_tuple(parent.get("parentReceptacles")))
        return tuple(sorted(found))

    result: list[SceneObjectScanInput] = []
    for object_id in sorted(objects):
        item = objects[object_id]
        position = item["position"]
        parent_ids = _string_tuple(item.get("parentReceptacles"))
        child_ids = _string_tuple(item.get("receptacleObjectIds"))
        freshness = _portable_state_fingerprint(
            {
                "object_id": object_id,
                "position": position,
                "rotation": item.get("rotation"),
                "parent_receptacle_ids": parent_ids,
                "receptacle_object_ids": child_ids,
            }
        )
        result.append(
            SceneObjectScanInput(
                exact_object_id=object_id,
                object_type=str(item["objectType"]),
                position=(
                    float(position["x"]),
                    float(position["y"]),
                    float(position["z"]),
                ),
                parent_receptacle_ids=parent_ids,
                receptacle_object_ids=child_ids,
                is_picked_up=item.get("isPickedUp") is True,
                closed_ancestor_exact_ids=closed_ancestors(object_id),
                pose_freshness_sha256=freshness,
            )
        )
    return tuple(result)


def _event_world_sha256(metadata: dict[str, Any]) -> str:
    objects = metadata.get("objects")
    if not isinstance(objects, list):
        raise ValueError("event objects are unreadable")
    normalized_objects = []
    for item in objects:
        if not isinstance(item, dict) or not isinstance(item.get("objectId"), str):
            raise ValueError("event object is unreadable")
        normalized_objects.append(
            {
                key: value
                for key, value in item.items()
                if key not in {"visible", "distance"}
            }
        )
    normalized_objects.sort(key=lambda item: str(item["objectId"]))
    extras = {
        key: value
        for key, value in metadata.items()
        if key
        not in {
            "actionReturn",
            "agent",
            "colors",
            "currentTime",
            "errorCode",
            "errorMessage",
            "lastAction",
            "lastActionSuccess",
            "objects",
            "reachablePositions",
        }
    }
    return _portable_state_fingerprint({"objects": normalized_objects, "metadata": extras})


def _event_bbox_areas(event: Any) -> tuple[tuple[str, float], ...]:
    detections = getattr(event, "instance_detections2D", None)
    if not isinstance(detections, dict):
        return ()
    result = []
    for object_id, box in detections.items():
        if not isinstance(object_id, str):
            raise ValueError("detection object ID is unreadable")
        area = _bbox_area_score(box)
        if not math.isfinite(area):
            raise ValueError("detection bbox area must be finite")
        if area > 0:
            result.append((object_id, area))
    return tuple(sorted(result))


def _frame_sha256(frame: Any) -> str | None:
    if frame is None:
        return None
    tobytes = getattr(frame, "tobytes", None)
    if callable(tobytes):
        return hashlib.sha256(tobytes()).hexdigest()
    if isinstance(frame, bytes):
        return hashlib.sha256(frame).hexdigest()
    return None


def _string_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list | tuple) or any(not isinstance(item, str) for item in value):
        raise ValueError("object containment IDs are unreadable")
    return tuple(sorted(set(value)))


def _portable_state_fingerprint(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _close_alfworld_env(env: Any) -> CleanupResult:
    for name in ("close", "stop"):
        closer = getattr(env, name, None)
        if not callable(closer):
            continue
        try:
            closer()
        except Exception:
            return CleanupResult(status="failed", evidence_ref=None)
        return CleanupResult(status="succeeded", evidence_ref=None)
    return CleanupResult(status="unverified", evidence_ref=None)


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
