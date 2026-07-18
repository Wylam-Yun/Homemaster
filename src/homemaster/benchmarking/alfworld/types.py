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
TasksetTerminalPhase = Literal["reset_setup", "goal_advance", "subtask_execution"]
SubtaskExecutionStatus = Literal["executed", "not_run"]
NotRunReason = Literal[
    "taskset_setup_failure",
    "goal_advance_failure",
    "prior_infrastructure_failure",
]
EpisodeClassification = Literal[
    "agent_success",
    "agent_model_failure",
    "harness_grounding_failure",
    "harness_navigation_failure",
    "harness_operation_failure",
    "execution_state_uncertain",
    "unclassified_execution_failure",
    "provider_failure",
    "runtime_failure",
    "artifact_failure",
    "cancelled",
]
SetupFailureCode = Literal[
    "external_reset_failed",
    "initial_state_unreadable",
    "reset_identity_unreadable",
    "expected_manifest_mismatch",
    "runtime_scene_mismatch",
    "addressability_unreadable",
    "cache_input_malformed",
    "scan_time_scale_enter_rejected",
    "scan_time_scale_enter_unreadable",
    "reachable_query_rejected",
    "reachable_query_unreadable",
    "scan_plan_missing",
    "scan_plan_malformed",
    "scan_pose_rejected",
    "scan_pose_mismatch",
    "scan_observation_unreadable",
    "scan_world_drift",
    "scan_restore_rejected",
    "scan_restore_mismatch",
    "scan_time_scale_restore_rejected",
    "scan_time_scale_restore_unreadable",
    "snapshot_invariant_failed",
    "scan_evidence_failed",
    "scan_cleanup_failed",
    "setup_runtime_failed",
    "setup_unexpected",
]
GoalAdvanceFailureCode = Literal[
    "expected_goal_trial_mismatch",
    "goal_scene_mismatch",
    "goal_identity_unreadable",
    "goal_advance_rejected",
    "goal_state_unreadable",
    "goal_world_drift",
    "goal_cleanup_failed",
    "goal_runtime_failed",
    "goal_advance_unexpected",
]
SetupRecoveryStatus = Literal["not_applicable", "not_needed", "restored", "unverified", "failed"]
SetupCleanupStatus = Literal["not_applicable", "not_needed", "succeeded", "unverified", "failed"]
EnvironmentDisposition = Literal["ready", "not_started", "closed", "quarantined"]
AlfworldBackendKind = Literal["thor", "textworld"]
ExecutionReadStatus = Literal["ok", "not_applicable", "absent", "malformed", "stale", "error"]
ObjectExecutionState = Literal[
    "held",
    "not_held",
    "placed",
    "heated",
    "cooled",
    "clean",
    "dirty",
    "sliced",
]
TargetExecutionState = Literal[
    "visible",
    "not_visible",
    "open",
    "closed",
    "toggled_on",
    "toggled_off",
]
ToolExecutionError = Literal[
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
]
AlfworldAction = Literal[
    "navigate",
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
]
SafeDetailCode = ToolExecutionError

AGENT_SCORE_CLASSIFICATIONS = {"agent_success", "agent_model_failure"}

_EPISODE_CLASSIFICATIONS = {
    "agent_success",
    "agent_model_failure",
    "harness_grounding_failure",
    "harness_navigation_failure",
    "harness_operation_failure",
    "execution_state_uncertain",
    "unclassified_execution_failure",
    "provider_failure",
    "runtime_failure",
    "artifact_failure",
    "cancelled",
}
_SETUP_FAILURE_CODES = {
    "external_reset_failed",
    "initial_state_unreadable",
    "reset_identity_unreadable",
    "expected_manifest_mismatch",
    "runtime_scene_mismatch",
    "addressability_unreadable",
    "cache_input_malformed",
    "scan_time_scale_enter_rejected",
    "scan_time_scale_enter_unreadable",
    "reachable_query_rejected",
    "reachable_query_unreadable",
    "scan_plan_missing",
    "scan_plan_malformed",
    "scan_pose_rejected",
    "scan_pose_mismatch",
    "scan_observation_unreadable",
    "scan_world_drift",
    "scan_restore_rejected",
    "scan_restore_mismatch",
    "scan_time_scale_restore_rejected",
    "scan_time_scale_restore_unreadable",
    "snapshot_invariant_failed",
    "scan_evidence_failed",
    "scan_cleanup_failed",
    "setup_runtime_failed",
    "setup_unexpected",
}
_GOAL_ADVANCE_FAILURE_CODES = {
    "expected_goal_trial_mismatch",
    "goal_scene_mismatch",
    "goal_identity_unreadable",
    "goal_advance_rejected",
    "goal_state_unreadable",
    "goal_world_drift",
    "goal_cleanup_failed",
    "goal_runtime_failed",
    "goal_advance_unexpected",
}
_RECOVERY_STATUSES = {"not_applicable", "not_needed", "restored", "unverified", "failed"}
_CLEANUP_STATUSES = {"not_applicable", "not_needed", "succeeded", "unverified", "failed"}
_ENVIRONMENT_DISPOSITIONS = {"ready", "not_started", "closed", "quarantined"}
_BACKEND_KINDS = {"thor", "textworld"}
_READ_STATUSES = {"ok", "not_applicable", "absent", "malformed", "stale", "error"}
_OBJECT_EXECUTION_STATES = {
    "held",
    "not_held",
    "placed",
    "heated",
    "cooled",
    "clean",
    "dirty",
    "sliced",
}
_TARGET_EXECUTION_STATES = {
    "visible",
    "not_visible",
    "open",
    "closed",
    "toggled_on",
    "toggled_off",
}
_ALFWORLD_ACTIONS = {
    "navigate",
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
_NON_TERMINAL_TOOL_ERRORS = {
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
}
_TERMINAL_TOOL_CLASSIFICATIONS = {
    "oracle_anchor_unresolved": "harness_navigation_failure",
    "oracle_pose_missing": "harness_navigation_failure",
    "oracle_pose_malformed": "harness_navigation_failure",
    "oracle_navigation_failed": "harness_navigation_failure",
    "oracle_target_not_visible": "harness_navigation_failure",
    "harness_operation_failure": "harness_operation_failure",
    "oracle_pose_mismatch": "execution_state_uncertain",
    "execution_state_uncertain": "execution_state_uncertain",
    "unclassified_execution_failure": "unclassified_execution_failure",
}
_TOOL_EXECUTION_ERRORS = _NON_TERMINAL_TOOL_ERRORS | set(_TERMINAL_TOOL_CLASSIFICATIONS)


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
    trial_manifest: Path | None = None

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
class AlfworldResetResult:
    backend_kind: AlfworldBackendKind
    ready: bool
    state: AlfworldEnvState | None
    scene_generation: int | None
    goal_generation: int | None
    scene_reset_fingerprint: str | None
    goal_trial_fingerprint: str | None
    snapshot_sha256: str | None
    snapshot_ref: str | None
    setup_trigger: SetupFailureCode | None
    setup_failure: SetupFailureCode | None
    classification: EpisodeClassification | None
    score_eligible: bool
    setup_backend_action_count: int
    recovery_status: SetupRecoveryStatus
    cleanup_status: SetupCleanupStatus
    quarantine_required: bool
    environment_disposition: EnvironmentDisposition
    evidence_ref: str | None

    def __post_init__(self) -> None:
        _validate_control_common(
            backend_kind=self.backend_kind,
            scene_generation=self.scene_generation,
            goal_generation=self.goal_generation,
            scene_reset_fingerprint=self.scene_reset_fingerprint,
            goal_trial_fingerprint=self.goal_trial_fingerprint,
            classification=self.classification,
            action_count=self.setup_backend_action_count,
            cleanup_status=self.cleanup_status,
            quarantine_required=self.quarantine_required,
            environment_disposition=self.environment_disposition,
        )
        _require_member("recovery_status", self.recovery_status, _RECOVERY_STATUSES)
        for name, value in (
            ("setup_trigger", self.setup_trigger),
            ("setup_failure", self.setup_failure),
        ):
            if value is not None:
                _require_member(name, value, _SETUP_FAILURE_CODES)

        if self.ready:
            _validate_ready_control_result(
                backend_kind=self.backend_kind,
                state=self.state,
                scene_generation=self.scene_generation,
                goal_generation=self.goal_generation,
                scene_reset_fingerprint=self.scene_reset_fingerprint,
                goal_trial_fingerprint=self.goal_trial_fingerprint,
                snapshot_sha256=self.snapshot_sha256,
                snapshot_ref=self.snapshot_ref,
                trigger=self.setup_trigger,
                failure=self.setup_failure,
                classification=self.classification,
                score_eligible=self.score_eligible,
                action_count=self.setup_backend_action_count,
                recovery_status=self.recovery_status,
                cleanup_status=self.cleanup_status,
                quarantine_required=self.quarantine_required,
                environment_disposition=self.environment_disposition,
            )
            return

        if self.state is not None:
            raise ValueError("terminal reset result must not contain state")
        if self.setup_trigger is None or self.setup_failure is None or self.classification is None:
            raise ValueError("terminal reset result requires trigger, failure, and classification")
        if self.score_eligible:
            raise ValueError("terminal reset result must be score-ineligible")
        if self.snapshot_sha256 is not None or self.snapshot_ref is not None:
            raise ValueError("terminal reset result must not publish a snapshot")
        if self.environment_disposition == "ready":
            raise ValueError("terminal reset result cannot leave the environment ready")
        _validate_terminal_disposition(
            allow_not_started=True,
            recovery_status=self.recovery_status,
            cleanup_status=self.cleanup_status,
            quarantine_required=self.quarantine_required,
            environment_disposition=self.environment_disposition,
        )


@dataclass(frozen=True)
class AlfworldGoalAdvanceResult:
    backend_kind: AlfworldBackendKind
    ready: bool
    state: AlfworldEnvState | None
    scene_generation: int | None
    goal_generation: int | None
    scene_reset_fingerprint: str | None
    goal_trial_fingerprint: str | None
    snapshot_sha256: str | None
    before_scene_state_sha256: str | None
    after_scene_state_sha256: str | None
    advance_trigger: GoalAdvanceFailureCode | None
    advance_failure: GoalAdvanceFailureCode | None
    classification: EpisodeClassification | None
    score_eligible: bool
    benchmark_control_action_count: int
    cleanup_status: SetupCleanupStatus
    quarantine_required: bool
    environment_disposition: EnvironmentDisposition
    evidence_ref: str | None

    def __post_init__(self) -> None:
        _validate_control_common(
            backend_kind=self.backend_kind,
            scene_generation=self.scene_generation,
            goal_generation=self.goal_generation,
            scene_reset_fingerprint=self.scene_reset_fingerprint,
            goal_trial_fingerprint=self.goal_trial_fingerprint,
            classification=self.classification,
            action_count=self.benchmark_control_action_count,
            cleanup_status=self.cleanup_status,
            quarantine_required=self.quarantine_required,
            environment_disposition=self.environment_disposition,
        )
        for name, value in (
            ("advance_trigger", self.advance_trigger),
            ("advance_failure", self.advance_failure),
        ):
            if value is not None:
                _require_member(name, value, _GOAL_ADVANCE_FAILURE_CODES)

        if self.ready:
            if self.state is None:
                raise ValueError("ready goal advance requires state")
            if any(
                value is not None
                for value in (self.advance_trigger, self.advance_failure, self.classification)
            ):
                raise ValueError("ready goal advance cannot contain terminal fields")
            if not self.score_eligible:
                raise ValueError("ready goal advance must be score-eligible")
            if self.benchmark_control_action_count != 1:
                raise ValueError("ready goal advance must send exactly one control action")
            if self.cleanup_status != "not_needed":
                raise ValueError("ready goal advance cleanup must be not_needed")
            if self.quarantine_required or self.environment_disposition != "ready":
                raise ValueError("ready goal advance must leave a reusable environment")
            if self.goal_generation is None or self.goal_trial_fingerprint is None:
                raise ValueError("ready goal advance requires goal identity")
            if self.backend_kind == "thor":
                required = (
                    self.scene_generation,
                    self.scene_reset_fingerprint,
                    self.snapshot_sha256,
                    self.before_scene_state_sha256,
                    self.after_scene_state_sha256,
                )
                if any(value is None for value in required):
                    raise ValueError("ready THOR goal advance requires scene snapshot identity")
                if self.before_scene_state_sha256 != self.after_scene_state_sha256:
                    raise ValueError("goal advance changed scene state")
            else:
                thor_only = (
                    self.scene_generation,
                    self.scene_reset_fingerprint,
                    self.snapshot_sha256,
                    self.before_scene_state_sha256,
                    self.after_scene_state_sha256,
                )
                if any(value is not None for value in thor_only):
                    raise ValueError("TextWorld goal advance cannot contain THOR scene fields")
            return

        if self.state is not None:
            raise ValueError("terminal goal advance must not contain state")
        if (
            self.advance_trigger is None
            or self.advance_failure is None
            or self.classification is None
        ):
            raise ValueError("terminal goal advance requires trigger, failure, and classification")
        if self.score_eligible:
            raise ValueError("terminal goal advance must be score-ineligible")
        _validate_terminal_disposition(
            allow_not_started=False,
            recovery_status=None,
            cleanup_status=self.cleanup_status,
            quarantine_required=self.quarantine_required,
            environment_disposition=self.environment_disposition,
        )


@dataclass(frozen=True)
class AlfworldControlTerminalRecord:
    phase: Literal["reset_setup", "goal_advance"]
    trigger_code: SetupFailureCode | GoalAdvanceFailureCode
    final_code: SetupFailureCode | GoalAdvanceFailureCode
    classification: EpisodeClassification
    worker_process_return_code: int | None
    timed_out: bool
    recovery_status: SetupRecoveryStatus
    cleanup_status: SetupCleanupStatus
    quarantine_required: bool
    environment_disposition: EnvironmentDisposition
    evidence_ref: str | None

    def __post_init__(self) -> None:
        if self.phase not in {"reset_setup", "goal_advance"}:
            raise ValueError(f"unsupported terminal phase: {self.phase}")
        codes = _SETUP_FAILURE_CODES if self.phase == "reset_setup" else _GOAL_ADVANCE_FAILURE_CODES
        _require_member("trigger_code", self.trigger_code, codes)
        _require_member("final_code", self.final_code, codes)
        _require_member("classification", self.classification, _EPISODE_CLASSIFICATIONS)
        _require_member("recovery_status", self.recovery_status, _RECOVERY_STATUSES)
        _require_member("cleanup_status", self.cleanup_status, _CLEANUP_STATUSES)
        _require_member(
            "environment_disposition", self.environment_disposition, _ENVIRONMENT_DISPOSITIONS
        )
        if self.environment_disposition == "ready":
            raise ValueError("terminal control record cannot leave the environment ready")
        if self.worker_process_return_code is not None and not isinstance(
            self.worker_process_return_code, int
        ):
            raise ValueError("worker_process_return_code must be an integer or None")

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "trigger_code": self.trigger_code,
            "final_code": self.final_code,
            "classification": self.classification,
            "worker_process_return_code": self.worker_process_return_code,
            "timed_out": self.timed_out,
            "recovery_status": self.recovery_status,
            "cleanup_status": self.cleanup_status,
            "quarantine_required": self.quarantine_required,
            "environment_disposition": self.environment_disposition,
            "evidence_ref": self.evidence_ref,
        }


@dataclass(frozen=True)
class AlfworldExecutionFeedback:
    success: bool
    action: AlfworldAction
    object: str | None
    target: str | None
    inventory: tuple[str, ...] | None
    inventory_status: ExecutionReadStatus
    object_state: ObjectExecutionState | None
    object_state_status: ExecutionReadStatus
    target_state: TargetExecutionState | None
    target_state_status: ExecutionReadStatus
    state_changed: bool | None
    state_read_status: ExecutionReadStatus
    error: ToolExecutionError | None
    terminal: bool
    classification: EpisodeClassification | None
    score_eligible: bool
    detail_code: SafeDetailCode | None

    def __post_init__(self) -> None:
        _require_member("action", self.action, _ALFWORLD_ACTIONS)
        for name, status in (
            ("inventory_status", self.inventory_status),
            ("object_state_status", self.object_state_status),
            ("target_state_status", self.target_state_status),
            ("state_read_status", self.state_read_status),
        ):
            _require_member(name, status, _READ_STATUSES)
        if self.object_state is not None:
            _require_member("object_state", self.object_state, _OBJECT_EXECUTION_STATES)
        if self.target_state is not None:
            _require_member("target_state", self.target_state, _TARGET_EXECUTION_STATES)
        if self.error is not None:
            _require_member("error", self.error, _TOOL_EXECUTION_ERRORS)
        if self.detail_code is not None:
            _require_member("detail_code", self.detail_code, _TOOL_EXECUTION_ERRORS)
        if self.classification is not None:
            _require_member("classification", self.classification, _EPISODE_CLASSIFICATIONS)

        _validate_read_value("inventory", self.inventory, self.inventory_status)
        _validate_read_value("object_state", self.object_state, self.object_state_status)
        _validate_read_value("target_state", self.target_state, self.target_state_status)
        _validate_read_value("state_changed", self.state_changed, self.state_read_status)

        uncertain_statuses = {"absent", "malformed", "stale", "error"}
        if any(
            status in uncertain_statuses
            for status in (
                self.inventory_status,
                self.object_state_status,
                self.target_state_status,
                self.state_read_status,
            )
        ):
            if (
                self.success
                or not self.terminal
                or self.classification
                not in {"execution_state_uncertain", "unclassified_execution_failure"}
            ):
                raise ValueError("unreadable execution state must be a terminal uncertainty")

        if self.success:
            if self.error is not None or self.classification is not None:
                raise ValueError("successful feedback cannot contain an error or classification")
            if self.terminal or not self.score_eligible:
                raise ValueError("successful feedback must remain score-eligible and non-terminal")
            return

        if self.error is None:
            raise ValueError("failed feedback requires an error")
        if self.terminal:
            expected = _TERMINAL_TOOL_CLASSIFICATIONS.get(self.error)
            if expected is None or self.classification != expected or self.score_eligible:
                raise ValueError("terminal tool error has an invalid classification mapping")
        elif (
            self.error not in _NON_TERMINAL_TOOL_ERRORS
            or self.classification is not None
            or not self.score_eligible
        ):
            raise ValueError(
                "non-terminal tool error must have no classification and be score-eligible"
            )

    @property
    def failure_reason(self) -> str | None:
        return self.error or self.classification

    def to_model_payload(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "action": self.action,
            "object": self.object,
            "target": self.target,
            "inventory": list(self.inventory) if self.inventory is not None else None,
            "inventory_status": self.inventory_status,
            "object_state": self.object_state,
            "object_state_status": self.object_state_status,
            "target_state": self.target_state,
            "target_state_status": self.target_state_status,
            "state_changed": self.state_changed,
            "state_read_status": self.state_read_status,
            "error": self.error,
            "terminal": self.terminal,
            "classification": self.classification,
            "score_eligible": self.score_eligible,
            "detail": _safe_feedback_detail(
                self.detail_code,
                object_label=self.object,
                target_label=self.target,
            ),
        }


def make_execution_feedback(
    *,
    action: AlfworldAction,
    success: bool,
    error: ToolExecutionError | None = None,
    object_label: str | None = None,
    target_label: str | None = None,
    inventory: tuple[str, ...] | None = None,
    inventory_status: ExecutionReadStatus = "not_applicable",
    object_state: ObjectExecutionState | None = None,
    object_state_status: ExecutionReadStatus = "not_applicable",
    target_state: TargetExecutionState | None = None,
    target_state_status: ExecutionReadStatus = "not_applicable",
    state_changed: bool | None = None,
    state_read_status: ExecutionReadStatus = "not_applicable",
) -> AlfworldExecutionFeedback:
    if success:
        classification = None
        terminal = False
        score_eligible = True
        error = None
    else:
        if error is None:
            error = "unclassified_execution_failure"
        classification = _TERMINAL_TOOL_CLASSIFICATIONS.get(error)
        terminal = classification is not None
        score_eligible = not terminal
        if error in {"execution_state_uncertain", "unclassified_execution_failure"}:
            inventory = None
            inventory_status = "error"
            object_state = None
            object_state_status = "error"
            target_state = None
            target_state_status = "error"
            state_changed = None
            state_read_status = "error"
    return AlfworldExecutionFeedback(
        success=success,
        action=action,
        object=object_label,
        target=target_label,
        inventory=inventory,
        inventory_status=inventory_status,
        object_state=object_state,
        object_state_status=object_state_status,
        target_state=target_state,
        target_state_status=target_state_status,
        state_changed=state_changed,
        state_read_status=state_read_status,
        error=error,
        terminal=terminal,
        classification=classification,
        score_eligible=score_eligible,
        detail_code=error,
    )


def _validate_control_common(
    *,
    backend_kind: str,
    scene_generation: int | None,
    goal_generation: int | None,
    scene_reset_fingerprint: str | None,
    goal_trial_fingerprint: str | None,
    classification: str | None,
    action_count: int,
    cleanup_status: str,
    quarantine_required: bool,
    environment_disposition: str,
) -> None:
    _require_member("backend_kind", backend_kind, _BACKEND_KINDS)
    _require_member("cleanup_status", cleanup_status, _CLEANUP_STATUSES)
    _require_member("environment_disposition", environment_disposition, _ENVIRONMENT_DISPOSITIONS)
    if classification is not None:
        _require_member("classification", classification, _EPISODE_CLASSIFICATIONS)
    if action_count < 0:
        raise ValueError("backend action count cannot be negative")
    for name, value in (
        ("scene_generation", scene_generation),
        ("goal_generation", goal_generation),
    ):
        if value is not None and value < 0:
            raise ValueError(f"{name} cannot be negative")
    for name, value in (
        ("scene_reset_fingerprint", scene_reset_fingerprint),
        ("goal_trial_fingerprint", goal_trial_fingerprint),
    ):
        if value is not None:
            _validate_sha256(name, value)
    if not isinstance(quarantine_required, bool):
        raise ValueError("quarantine_required must be boolean")


def _validate_ready_control_result(
    *,
    backend_kind: str,
    state: AlfworldEnvState | None,
    scene_generation: int | None,
    goal_generation: int | None,
    scene_reset_fingerprint: str | None,
    goal_trial_fingerprint: str | None,
    snapshot_sha256: str | None,
    snapshot_ref: str | None,
    trigger: str | None,
    failure: str | None,
    classification: str | None,
    score_eligible: bool,
    action_count: int,
    recovery_status: str,
    cleanup_status: str,
    quarantine_required: bool,
    environment_disposition: str,
) -> None:
    if state is None:
        raise ValueError("ready reset requires state")
    if any(value is not None for value in (trigger, failure, classification)):
        raise ValueError("ready reset cannot contain terminal fields")
    if not score_eligible:
        raise ValueError("ready reset must be score-eligible")
    if quarantine_required or environment_disposition != "ready":
        raise ValueError("ready reset must leave a reusable environment")
    if goal_generation is None or goal_trial_fingerprint is None:
        raise ValueError("ready reset requires goal identity")
    if backend_kind == "thor":
        if any(
            value is None
            for value in (
                scene_generation,
                scene_reset_fingerprint,
                snapshot_sha256,
                snapshot_ref,
            )
        ):
            raise ValueError("ready THOR reset requires complete snapshot identity")
        _validate_sha256("snapshot_sha256", snapshot_sha256)
        if recovery_status != "restored" or cleanup_status != "not_needed":
            raise ValueError("ready THOR reset requires restored recovery and no cleanup")
    else:
        if any(
            value is not None
            for value in (scene_generation, scene_reset_fingerprint, snapshot_sha256, snapshot_ref)
        ):
            raise ValueError("ready TextWorld reset cannot contain THOR scene fields")
        if action_count != 0:
            raise ValueError("ready TextWorld reset cannot send setup backend actions")
        if recovery_status != "not_applicable" or cleanup_status != "not_applicable":
            raise ValueError("ready TextWorld reset requires not-applicable THOR statuses")


def _validate_terminal_disposition(
    *,
    allow_not_started: bool,
    recovery_status: str | None,
    cleanup_status: str,
    quarantine_required: bool,
    environment_disposition: str,
) -> None:
    if environment_disposition == "not_started":
        if not allow_not_started:
            raise ValueError("goal advance terminal cannot use not_started disposition")
        if recovery_status != "not_needed" or cleanup_status != "not_needed" or quarantine_required:
            raise ValueError("not_started terminal disposition has invalid recovery/cleanup state")
    elif environment_disposition == "closed":
        if cleanup_status != "succeeded":
            raise ValueError("closed terminal disposition requires successful cleanup")
    elif environment_disposition == "quarantined":
        if not quarantine_required or cleanup_status not in {"unverified", "failed"}:
            raise ValueError("quarantined disposition requires unverified or failed cleanup")


def _validate_read_value(name: str, value: Any, status: str) -> None:
    if status == "ok" and value is None:
        raise ValueError(f"{name} is required when its read status is ok")
    if status != "ok" and value is not None:
        raise ValueError(f"{name} must be None when its read status is {status}")


def _require_member(name: str, value: str, allowed: set[str]) -> None:
    if value not in allowed:
        raise ValueError(f"unsupported {name}: {value}")


def _validate_sha256(name: str, value: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{name} must be a lowercase SHA-256 hex digest")


@dataclass(frozen=True)
class AlfworldStepResult:
    tool_name: str
    tool_args: dict[str, Any]
    translated_command: str | None
    success: bool
    state: AlfworldEnvState
    execution_feedback: AlfworldExecutionFeedback
    feedback: str | None = None
    backend_action_count: int = 0
    trace_events: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.success is not self.execution_feedback.success:
            raise ValueError("step success must match typed execution feedback")

    @property
    def failure_reason(self) -> str | None:
        return self.execution_feedback.failure_reason

    def to_model_visible_data(self) -> dict[str, Any]:
        return self.execution_feedback.to_model_payload()

    def to_trace_event(self) -> dict[str, Any]:
        data = self.to_model_visible_data()
        data.update(
            {
                "tool_name": self.tool_name,
                "tool_args": self.tool_args,
                "translated_command": self.translated_command,
                "debug_feedback": self.feedback,
                "tool_success": self.success,
                "backend_action_count": self.backend_action_count,
            }
        )
        return data


def _safe_feedback_detail(
    code: SafeDetailCode | None,
    *,
    object_label: str | None,
    target_label: str | None,
) -> str | None:
    if code is None:
        return None
    object_text = object_label or "the requested object"
    target_text = target_label or "the requested target"
    templates = {
        "invalid_tool_arguments": "The tool arguments are invalid.",
        "unknown_tool": "The requested tool is unavailable.",
        "target_not_found": f"{target_text} is not a supported target.",
        "target_not_visible": f"{target_text} is not visible in the current view.",
        "object_already_held": f"{object_text} is already held.",
        "object_not_held": f"{object_text} is not held.",
        "target_not_receptacle": f"{target_text} is not a receptacle.",
        "target_closed": f"Open {target_text} before putting the object.",
        "action_not_applicable": "The action does not apply to the requested target.",
        "navigation_required": f"Navigate to {target_text} before this action.",
        "oracle_anchor_unresolved": (
            f"No verified navigation anchor is available for {target_text}."
        ),
        "oracle_pose_missing": f"No verified navigation pose is available for {target_text}.",
        "oracle_pose_malformed": f"The verified navigation pose for {target_text} is invalid.",
        "oracle_navigation_failed": f"Navigation to {target_text} was rejected.",
        "oracle_pose_mismatch": f"Navigation to {target_text} did not reach the verified pose.",
        "oracle_target_not_visible": f"{target_text} was not visible after navigation.",
        "harness_operation_failure": "The external action was rejected without changing state.",
        "execution_state_uncertain": "The current execution state could not be verified.",
        "unclassified_execution_failure": "The execution result could not be classified.",
    }
    return templates[code]


_INTERNAL_EXECUTION_KEYS = {
    "actual_pose",
    "anchor_object_id",
    "backend_action_count",
    "backend_actions",
    "budget_limit",
    "budget_stop_reason",
    "budget_used",
    "candidate_pose",
    "candidates_hash",
    "context_id",
    "held_object_id",
    "internal",
    "locked_candidates",
    "locked_candidates_hash",
    "object_id",
    "pose_candidates_attempted",
    "put_attempt_count",
    "raw_event_hash",
    "raw_event_ref",
    "requested_pose",
    "resolved_object_id",
    "scene_objects",
    "target_object_id",
    "target_receptacle_id",
}


def _model_visible_execution_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _model_visible_execution_value(item)
            for key, item in value.items()
            if not _is_internal_execution_key(str(key))
        }
    if isinstance(value, list | tuple):
        return [_model_visible_execution_value(item) for item in value]
    return value


def _is_internal_execution_key(key: str) -> bool:
    normalized = key.casefold()
    return (
        normalized in _INTERNAL_EXECUTION_KEYS
        or normalized.endswith("_object_id")
        or normalized.endswith("_object_ids")
        or normalized.startswith("raw_event_")
        or "candidate_pose" in normalized
        or normalized.endswith("_pose")
    )


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
    classification: str = "agent_model_failure"
    score_eligible: bool = True
    agent_tool_call_count: int = 0
    backend_action_count: int = 0
    setup_backend_action_count: int = 0
    control_backend_action_count: int = 0
    model_backend_action_count: int = 0
    total_backend_action_count: int = 0
    total_external_request_count: int = 0


@dataclass
class EpisodeOutcome:
    terminal: bool = False
    classification: str | None = None
    terminal_tool_call_id: str | None = None
    score_eligible: bool = True
    agent_tool_call_count: int = 0
    backend_action_count: int = 0
    terminal_evidence_ref: str | None = None

    def mark_terminal(
        self,
        *,
        classification: str,
        tool_call_id: str | None,
        evidence_ref: str | None = None,
    ) -> None:
        if self.terminal:
            return
        self.terminal = True
        self.classification = classification
        self.terminal_tool_call_id = tool_call_id
        self.score_eligible = classification in AGENT_SCORE_CLASSIFICATIONS
        self.terminal_evidence_ref = evidence_ref


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

    @property
    def agent_scored_episodes(self) -> list[Any]:
        return [
            episode for episode in self.episodes if bool(getattr(episode, "score_eligible", True))
        ]

    @property
    def agent_success_rate_on_valid(self) -> float:
        eligible = self.agent_scored_episodes
        if not eligible:
            return 0.0
        return sum(1 for episode in eligible if episode.success) / len(eligible)

    def to_dict(self) -> dict[str, Any]:
        total = len(self.episodes)
        eligible = self.agent_scored_episodes
        classifications = [
            str(
                getattr(
                    episode,
                    "classification",
                    "agent_success" if episode.success else "agent_model_failure",
                )
            )
            for episode in self.episodes
        ]
        harness_invalid = total - len(eligible)
        classification_count = {
            classification: classifications.count(classification)
            for classification in set(classifications)
        }
        unclassified = classification_count.get("unclassified_execution_failure", 0)
        harness_failures = sum(
            classification_count.get(name, 0)
            for name in (
                "harness_grounding_failure",
                "harness_navigation_failure",
                "harness_operation_failure",
                "execution_state_uncertain",
            )
        )
        provider_failures = classification_count.get("provider_failure", 0)
        runtime_failures = sum(
            classification_count.get(name, 0)
            for name in ("runtime_failure", "artifact_failure")
        )
        cancelled = classification_count.get("cancelled", 0)
        evaluation_coverage = len(eligible) / total if total else 0.0
        harness_coverage = 1.0 - harness_failures / total if total else 0.0
        provider_availability = 1.0 - provider_failures / total if total else 0.0
        runtime_availability = 1.0 - runtime_failures / total if total else 0.0
        return {
            "run_id": self.run_id,
            "config": self.config,
            "episode_count": total,
            "total_episodes": total,
            "success_rate": self.success_rate,
            "raw_success_rate": self.success_rate,
            "agent_scored_episodes": len(eligible),
            "agent_successes": sum(1 for episode in eligible if episode.success),
            "agent_success_rate_on_valid": self.agent_success_rate_on_valid,
            "harness_invalid_episodes": harness_invalid,
            "harness_grounding_failures": classification_count.get("harness_grounding_failure", 0),
            "harness_navigation_failures": classification_count.get(
                "harness_navigation_failure", 0
            ),
            "harness_operation_failures": classification_count.get("harness_operation_failure", 0),
            "execution_state_uncertain_count": classification_count.get(
                "execution_state_uncertain", 0
            ),
            "unclassified_execution_failures": unclassified,
            "evaluation_valid_coverage": evaluation_coverage,
            "harness_valid_coverage": evaluation_coverage,
            "harness_coverage": harness_coverage,
            "provider_availability": provider_availability,
            "runtime_availability": runtime_availability,
            "cancelled_episodes": cancelled,
            "formal_score_available": bool(
                total
                and evaluation_coverage == 1.0
                and harness_coverage == 1.0
                and provider_availability == 1.0
                and runtime_availability == 1.0
                and unclassified == 0
                and cancelled == 0
            ),
            "average_goal_condition_success_rate": (
                sum(e.goal_condition_success_rate for e in self.episodes) / total if total else 0.0
            ),
            "average_steps": (sum(e.steps for e in self.episodes) / total if total else 0.0),
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
                    "classification": getattr(
                        e,
                        "classification",
                        "agent_success" if e.success else "agent_model_failure",
                    ),
                    "score_eligible": bool(getattr(e, "score_eligible", True)),
                    "agent_tool_call_count": int(getattr(e, "agent_tool_call_count", 0)),
                    "backend_action_count": int(getattr(e, "backend_action_count", 0)),
                    "setup_backend_action_count": int(
                        getattr(e, "setup_backend_action_count", 0)
                    ),
                    "control_backend_action_count": int(
                        getattr(e, "control_backend_action_count", 0)
                    ),
                    "model_backend_action_count": int(
                        getattr(e, "model_backend_action_count", 0)
                    ),
                    "total_backend_action_count": int(
                        getattr(e, "total_backend_action_count", 0)
                    ),
                    "total_external_request_count": int(
                        getattr(e, "total_external_request_count", 0)
                    ),
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
    classification: str | None = "agent_model_failure"
    score_eligible: bool = True
    agent_tool_call_count: int = 0
    backend_action_count: int = 0
    terminal_tool_call_id: str | None = None
    terminal_evidence_ref: str | None = None
    execution_status: SubtaskExecutionStatus = "executed"
    not_run_reason: NotRunReason | None = None
    blocked_by_classification: str | None = None
    setup_backend_action_count: int = 0
    benchmark_control_action_count: int = 0

    def __post_init__(self) -> None:
        if self.execution_status not in {"executed", "not_run"}:
            raise ValueError(f"unsupported subtask execution status: {self.execution_status}")
        if self.setup_backend_action_count != 0 or self.benchmark_control_action_count != 0:
            raise ValueError("subtask rows cannot own setup or control actions")
        if self.execution_status == "executed":
            if self.not_run_reason is not None or self.blocked_by_classification is not None:
                raise ValueError("executed subtask cannot contain not-run metadata")
            if self.classification is None:
                raise ValueError("executed subtask requires a classification")
            return
        if self.not_run_reason not in {
            "taskset_setup_failure",
            "goal_advance_failure",
            "prior_infrastructure_failure",
        }:
            raise ValueError("not-run subtask requires a closed reason")
        if self.classification is not None or self.score_eligible:
            raise ValueError(
                "not-run subtask must have classification=None and be score-ineligible"
            )
        if self.blocked_by_classification is None:
            raise ValueError("not-run subtask requires the root blocking classification")
        zero_values = (
            self.steps,
            self.invalid_actions,
            self.agent_tool_call_count,
            self.backend_action_count,
        )
        if any(value != 0 for value in zero_values):
            raise ValueError("not-run subtask counts must all be zero")
        if self.terminal_tool_call_id is not None or self.terminal_evidence_ref is not None:
            raise ValueError("not-run subtask cannot own terminal tool evidence")

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
            "classification": self.classification,
            "score_eligible": self.score_eligible,
            "execution_status": self.execution_status,
            "not_run_reason": self.not_run_reason,
            "blocked_by_classification": self.blocked_by_classification,
            "agent_tool_call_count": self.agent_tool_call_count,
            "backend_action_count": self.backend_action_count,
            "model_backend_action_count": self.backend_action_count,
            "setup_backend_action_count": self.setup_backend_action_count,
            "benchmark_control_action_count": self.benchmark_control_action_count,
            "terminal_tool_call_id": self.terminal_tool_call_id,
            "terminal_evidence_ref": self.terminal_evidence_ref,
        }


@dataclass(frozen=True)
class TasksetRootTerminal:
    phase: TasksetTerminalPhase
    classification: str
    subtask_index: int | None
    control_terminal_record: AlfworldControlTerminalRecord | None
    setup_backend_action_count: int
    benchmark_control_action_count: int
    model_backend_action_count: int
    total_backend_action_count: int
    total_external_action_count: int

    def __post_init__(self) -> None:
        if self.phase not in {"reset_setup", "goal_advance", "subtask_execution"}:
            raise ValueError(f"unsupported taskset terminal phase: {self.phase}")
        if self.phase == "reset_setup" and self.subtask_index is not None:
            raise ValueError("reset setup terminal cannot name a started subtask")
        if self.phase != "reset_setup" and self.subtask_index is None:
            raise ValueError("goal/subtask terminal requires a subtask index")
        if self.phase in {"reset_setup", "goal_advance"} and self.control_terminal_record is None:
            raise ValueError("control terminal phase requires a control terminal record")
        if self.phase == "subtask_execution" and self.control_terminal_record is not None:
            raise ValueError("subtask execution terminal cannot contain a control record")
        expected_backend = self.setup_backend_action_count + self.model_backend_action_count
        expected_external = expected_backend + self.benchmark_control_action_count
        if self.total_backend_action_count != expected_backend:
            raise ValueError("taskset terminal backend total is inconsistent")
        if self.total_external_action_count != expected_external:
            raise ValueError("taskset terminal external-action total is inconsistent")

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "classification": self.classification,
            "subtask_index": self.subtask_index,
            "control_terminal_record": (
                self.control_terminal_record.to_dict()
                if self.control_terminal_record is not None
                else None
            ),
            "setup_backend_action_count": self.setup_backend_action_count,
            "benchmark_control_action_count": self.benchmark_control_action_count,
            "model_backend_action_count": self.model_backend_action_count,
            "total_backend_action_count": self.total_backend_action_count,
            "total_external_action_count": self.total_external_action_count,
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
    setup_backend_action_count: int = 0
    benchmark_control_action_count: int = 0
    root_terminal: TasksetRootTerminal | None = None

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

    @property
    def score_eligible(self) -> bool:
        if not self.subtasks or self.root_terminal is not None:
            return False
        return all(
            subtask.score_eligible
            for subtask in self.subtasks
            if subtask.execution_status == "executed"
        )

    @property
    def classification(self) -> str:
        if self.root_terminal is not None:
            return self.root_terminal.classification
        for subtask in self.subtasks:
            if not subtask.score_eligible and subtask.classification is not None:
                return subtask.classification
        return "agent_success" if self.chain_success else "agent_model_failure"

    @property
    def agent_tool_call_count(self) -> int:
        return sum(subtask.agent_tool_call_count for subtask in self.subtasks)

    @property
    def backend_action_count(self) -> int:
        return sum(subtask.backend_action_count for subtask in self.subtasks)

    @property
    def model_backend_action_count(self) -> int:
        return self.backend_action_count

    @property
    def total_backend_action_count(self) -> int:
        return self.setup_backend_action_count + self.model_backend_action_count

    @property
    def total_external_action_count(self) -> int:
        return self.total_backend_action_count + self.benchmark_control_action_count

    @property
    def terminal_tool_call_id(self) -> str | None:
        return next(
            (
                subtask.terminal_tool_call_id
                for subtask in self.subtasks
                if subtask.terminal_tool_call_id is not None
            ),
            None,
        )

    @property
    def terminal_evidence_ref(self) -> str | None:
        return next(
            (
                subtask.terminal_evidence_ref
                for subtask in self.subtasks
                if subtask.terminal_evidence_ref is not None
            ),
            None,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "taskset_id": self.taskset_id,
            "floorplan": self.floorplan,
            "difficulty": self.difficulty,
            "description": self.description,
            "chain_success": self.chain_success,
            "classification": self.classification,
            "score_eligible": self.score_eligible,
            "chain_completed_count": self.chain_completed_count,
            "subtask_count": len(self.subtasks),
            "success_rate": self.success_rate,
            "agent_tool_call_count": self.agent_tool_call_count,
            "backend_action_count": self.backend_action_count,
            "setup_backend_action_count": self.setup_backend_action_count,
            "benchmark_control_action_count": self.benchmark_control_action_count,
            "model_backend_action_count": self.model_backend_action_count,
            "total_backend_action_count": self.total_backend_action_count,
            "total_external_action_count": self.total_external_action_count,
            "terminal_tool_call_id": self.terminal_tool_call_id,
            "terminal_evidence_ref": self.terminal_evidence_ref,
            "root_terminal": self.root_terminal.to_dict() if self.root_terminal else None,
            "trace_dir": str(self.trace_dir),
            "subtasks": [s.to_dict() for s in self.subtasks],
        }


@dataclass(frozen=True)
class TasksetRunSummary:
    """Summary across all tasksets in one run."""

    run_id: str
    taskset_results: list[TasksetResult]
    config: dict[str, Any] = field(default_factory=dict)

    @property
    def agent_scored_tasksets(self) -> list[TasksetResult]:
        return [taskset for taskset in self.taskset_results if taskset.score_eligible]

    @property
    def agent_success_rate_on_valid(self) -> float:
        eligible = self.agent_scored_tasksets
        if not eligible:
            return 0.0
        return sum(1 for taskset in eligible if taskset.chain_success) / len(eligible)

    def to_dict(self) -> dict[str, Any]:
        total = len(self.taskset_results)
        eligible = self.agent_scored_tasksets
        classifications = [taskset.classification for taskset in self.taskset_results]
        classification_count = {
            classification: classifications.count(classification)
            for classification in set(classifications)
        }
        harness_invalid = total - len(eligible)
        unclassified = classification_count.get("unclassified_execution_failure", 0)
        harness_failures = sum(
            classification_count.get(name, 0)
            for name in (
                "harness_grounding_failure",
                "harness_navigation_failure",
                "harness_operation_failure",
                "execution_state_uncertain",
            )
        )
        provider_failures = classification_count.get("provider_failure", 0)
        runtime_failures = sum(
            classification_count.get(name, 0)
            for name in ("runtime_failure", "artifact_failure")
        )
        cancelled = classification_count.get("cancelled", 0)
        evaluation_coverage = len(eligible) / total if total else 0.0
        harness_coverage = 1.0 - harness_failures / total if total else 0.0
        provider_availability = 1.0 - provider_failures / total if total else 0.0
        runtime_availability = 1.0 - runtime_failures / total if total else 0.0
        return {
            "run_id": self.run_id,
            "config": self.config,
            "taskset_count": total,
            "total_tasksets": total,
            "agent_scored_tasksets": len(eligible),
            "agent_successes": sum(1 for taskset in eligible if taskset.chain_success),
            "raw_success_rate": (
                sum(1 for taskset in self.taskset_results if taskset.chain_success) / total
                if total
                else 0.0
            ),
            "agent_success_rate_on_valid": self.agent_success_rate_on_valid,
            "harness_invalid_tasksets": harness_invalid,
            "harness_grounding_failures": classification_count.get("harness_grounding_failure", 0),
            "harness_navigation_failures": classification_count.get(
                "harness_navigation_failure", 0
            ),
            "harness_operation_failures": classification_count.get("harness_operation_failure", 0),
            "execution_state_uncertain_count": classification_count.get(
                "execution_state_uncertain", 0
            ),
            "unclassified_execution_failures": unclassified,
            "evaluation_valid_coverage": evaluation_coverage,
            "harness_valid_coverage": evaluation_coverage,
            "harness_coverage": harness_coverage,
            "provider_availability": provider_availability,
            "runtime_availability": runtime_availability,
            "cancelled_tasksets": cancelled,
            "formal_score_available": bool(
                total
                and evaluation_coverage == 1.0
                and harness_coverage == 1.0
                and provider_availability == 1.0
                and runtime_availability == 1.0
                and unclassified == 0
                and cancelled == 0
            ),
            "not_run_subtasks": sum(
                1
                for taskset in self.taskset_results
                for subtask in taskset.subtasks
                if subtask.execution_status == "not_run"
            ),
            "agent_tool_call_count": sum(
                taskset.agent_tool_call_count for taskset in self.taskset_results
            ),
            "backend_action_count": sum(
                taskset.backend_action_count for taskset in self.taskset_results
            ),
            "setup_backend_action_count": sum(
                taskset.setup_backend_action_count for taskset in self.taskset_results
            ),
            "benchmark_control_action_count": sum(
                taskset.benchmark_control_action_count for taskset in self.taskset_results
            ),
            "model_backend_action_count": sum(
                taskset.model_backend_action_count for taskset in self.taskset_results
            ),
            "total_backend_action_count": sum(
                taskset.total_backend_action_count for taskset in self.taskset_results
            ),
            "total_external_action_count": sum(
                taskset.total_external_action_count for taskset in self.taskset_results
            ),
            "tasksets": [t.to_dict() for t in self.taskset_results],
        }
