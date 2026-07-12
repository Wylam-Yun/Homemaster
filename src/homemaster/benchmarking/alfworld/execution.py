"""Deterministic execution core for ALFWorld navigation and manipulation."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass
from typing import Any, Literal, Protocol

ReadStatus = Literal["ok", "error", "missing"]
ActionStatus = Literal["success", "failure", "uncertain"]


@dataclass(frozen=True)
class AgentPose:
    x: float
    y: float
    z: float
    rotation: float
    horizon: float

    def __post_init__(self) -> None:
        if not all(math.isfinite(value) for value in asdict(self).values()):
            raise ValueError("agent pose values must be finite")

    def matches(self, other: AgentPose, *, tolerance: float = 1e-4) -> bool:
        return (
            abs(self.x - other.x) <= tolerance
            and abs(self.y - other.y) <= tolerance
            and abs(self.z - other.z) <= tolerance
            and _angular_delta(self.rotation, other.rotation) <= tolerance
            and abs(self.horizon - other.horizon) <= tolerance
        )


@dataclass(frozen=True)
class ExternalRead:
    status: ReadStatus
    raw_event_ref: str | None
    raw_event_hash: str | None
    inventory_object_ids: tuple[str, ...]
    held_object_id: str | None
    exact_object_present: bool
    object_parent_ids: tuple[str, ...]
    target_child_ids: tuple[str, ...]
    actual_agent_pose: AgentPose | None
    goal_summary: dict[str, Any]
    exact_object_is_picked_up: bool | None = None

    @property
    def action_state(self) -> tuple[Any, ...]:
        return (
            self.held_object_id,
            self.inventory_object_ids,
            self.object_parent_ids,
            self.target_child_ids,
            self.exact_object_present,
            self.exact_object_is_picked_up,
        )


@dataclass(frozen=True)
class ExternalActionResult:
    status: ActionStatus
    raw_event_ref: str | None
    raw_event_hash: str | None
    detail: str
    actual_agent_pose: AgentPose | None = None


@dataclass(frozen=True)
class PutTransition:
    classification: Literal[
        "success",
        "candidate_failed",
        "execution_state_uncertain",
    ]
    safe_to_retry: bool


def classify_put_transition(
    *,
    action_result: ExternalActionResult,
    before: ExternalRead,
    after: ExternalRead,
    held_object_id: str,
    target_receptacle_id: str,
) -> PutTransition:
    if not _read_proves_held_state(before, held_object_id):
        return PutTransition("execution_state_uncertain", False)
    if not _read_proves_exact_state(after, held_object_id):
        return PutTransition("execution_state_uncertain", False)

    terminal_success = (
        action_result.status == "success"
        and held_object_id not in after.inventory_object_ids
        and after.held_object_id != held_object_id
        and after.exact_object_is_picked_up is False
        and target_receptacle_id in after.object_parent_ids
        and held_object_id in after.target_child_ids
    )
    if terminal_success:
        return PutTransition("success", False)

    if action_result.status != "failure":
        return PutTransition("execution_state_uncertain", False)
    if before.action_state != after.action_state:
        return PutTransition("execution_state_uncertain", False)
    return PutTransition("candidate_failed", True)


@dataclass(frozen=True)
class SceneObjectRef:
    canonical_label: str
    object_id: str
    object_type: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class SceneObjectIndex:
    scene_generation: int
    snapshot_event_sequence: int
    by_canonical_label: dict[str, SceneObjectRef]
    labels_by_type: dict[str, tuple[str, ...]]

    @classmethod
    def from_objects(
        cls,
        *,
        objects: list[dict[str, Any]],
        scene_generation: int,
        snapshot_event_sequence: int,
    ) -> SceneObjectIndex:
        unique: dict[str, dict[str, Any]] = {}
        for item in objects:
            if not isinstance(item, dict):
                continue
            object_id = item.get("objectId")
            object_type = item.get("objectType")
            if not isinstance(object_id, str) or not object_id:
                continue
            if not isinstance(object_type, str) or not object_type:
                continue
            previous = unique.get(object_id)
            if previous is not None and previous.get("objectType") != object_type:
                raise ValueError(f"objectId appears with multiple types: {object_id}")
            unique[object_id] = dict(item)

        grouped: dict[str, list[dict[str, Any]]] = {}
        for item in unique.values():
            type_key = _object_type_key(str(item["objectType"]))
            if type_key:
                grouped.setdefault(type_key, []).append(item)

        by_label: dict[str, SceneObjectRef] = {}
        labels_by_type: dict[str, tuple[str, ...]] = {}
        for type_key in sorted(grouped):
            labels: list[str] = []
            for index, item in enumerate(
                sorted(grouped[type_key], key=lambda obj: str(obj["objectId"])),
                start=1,
            ):
                label = f"{type_key} {index}"
                labels.append(label)
                by_label[label] = SceneObjectRef(
                    canonical_label=label,
                    object_id=str(item["objectId"]),
                    object_type=str(item["objectType"]),
                    metadata=item,
                )
            labels_by_type[type_key] = tuple(labels)

        return cls(
            scene_generation=scene_generation,
            snapshot_event_sequence=snapshot_event_sequence,
            by_canonical_label=by_label,
            labels_by_type=labels_by_type,
        )

    def resolve(self, label: str) -> SceneObjectRef | None:
        canonical = _canonical_query(label)
        explicit = _EXPLICIT_INSTANCE.fullmatch(canonical)
        if explicit is not None:
            return self.by_canonical_label.get(canonical)
        labels = self.labels_by_type.get(_object_type_key(canonical), ())
        if not labels:
            return None
        return self.by_canonical_label[labels[0]]


@dataclass(frozen=True)
class GroundingDecision:
    classification: Literal["resolved", "target_not_found", "harness_grounding_failure"]
    terminal: bool
    score_eligible: bool
    resolved_object_id: str | None


def reconcile_target_resolution(
    *,
    requested_label: str,
    scene_index: SceneObjectIndex,
    resolver_object_id: str | None,
) -> GroundingDecision:
    authoritative = scene_index.resolve(requested_label)
    if authoritative is None:
        return GroundingDecision(
            classification="target_not_found",
            terminal=False,
            score_eligible=True,
            resolved_object_id=None,
        )
    if resolver_object_id != authoritative.object_id:
        return GroundingDecision(
            classification="harness_grounding_failure",
            terminal=True,
            score_eligible=False,
            resolved_object_id=None,
        )
    return GroundingDecision(
        classification="resolved",
        terminal=False,
        score_eligible=True,
        resolved_object_id=authoritative.object_id,
    )


@dataclass(frozen=True)
class PoseContext:
    context_id: str
    scene_generation: int
    goal_generation: int
    source_event_sequence: int
    source_frame_hash: str
    anchor_object_id: str
    current_actual_pose: AgentPose
    locked_candidates: tuple[AgentPose, ...]
    candidates_hash: str
    created_tool_call_id: str

    @classmethod
    def lock(
        cls,
        *,
        context_id: str,
        scene_generation: int,
        goal_generation: int,
        source_event_sequence: int,
        source_frame_hash: str,
        anchor_object_id: str,
        current_actual_pose: AgentPose,
        local_candidates: tuple[AgentPose, ...],
        created_tool_call_id: str,
    ) -> PoseContext:
        candidates: list[AgentPose] = [current_actual_pose]
        for candidate in local_candidates:
            if candidate not in candidates:
                candidates.append(candidate)
        locked = tuple(candidates)
        encoded = json.dumps(
            [asdict(candidate) for candidate in locked],
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return cls(
            context_id=context_id,
            scene_generation=scene_generation,
            goal_generation=goal_generation,
            source_event_sequence=source_event_sequence,
            source_frame_hash=source_frame_hash,
            anchor_object_id=anchor_object_id,
            current_actual_pose=current_actual_pose,
            locked_candidates=locked,
            candidates_hash=hashlib.sha256(encoded).hexdigest(),
            created_tool_call_id=created_tool_call_id,
        )


@dataclass(frozen=True)
class ExecutionBudget:
    max_pose_candidates: int
    max_backend_actions: int
    max_elapsed_ms: float

    def __post_init__(self) -> None:
        if self.max_pose_candidates <= 0:
            raise ValueError("max_pose_candidates must be positive")
        if self.max_backend_actions <= 0:
            raise ValueError("max_backend_actions must be positive")
        if not math.isfinite(self.max_elapsed_ms) or self.max_elapsed_ms <= 0:
            raise ValueError("max_elapsed_ms must be finite and positive")


@dataclass(frozen=True)
class PutExecutionRequest:
    tool_call_id: str
    held_object_id: str
    target_receptacle_id: str
    pose_context: PoseContext


@dataclass(frozen=True)
class PutExecutionResult:
    success: bool
    classification: Literal[
        "success",
        "harness_operation_failure",
        "execution_state_uncertain",
    ]
    locked_candidates_hash: str
    attempted_poses: tuple[AgentPose, ...]
    pose_candidates_attempted: int
    put_attempt_count: int
    backend_action_count: int
    elapsed_ms: float
    budget_stop_reason: str | None = None
    detail: str = ""
    final_read: ExternalRead | None = None
    trace_events: tuple[dict[str, Any], ...] = ()


class ExecutionBackend(Protocol):
    def read_external_state(
        self,
        *,
        held_object_id: str,
        target_receptacle_id: str,
    ) -> ExternalRead: ...

    def put_object(
        self,
        *,
        object_id: str,
        receptacle_object_id: str,
    ) -> ExternalActionResult: ...

    def move_to(self, *, pose: AgentPose) -> ExternalActionResult: ...


class ManipulationExecutor:
    def __init__(
        self,
        *,
        backend: ExecutionBackend,
        budget: ExecutionBudget,
        monotonic_ms: Any,
    ) -> None:
        self._backend = backend
        self._budget = budget
        self._monotonic_ms = monotonic_ms

    def execute_put(self, request: PutExecutionRequest) -> PutExecutionResult:
        started_ms = float(self._monotonic_ms())
        attempted: list[AgentPose] = []
        backend_actions = 0
        put_attempts = 0
        trace_events: list[dict[str, Any]] = []
        trace_events.append(
            self._trace_event(
                event="context_created",
                request=request,
                started_ms=started_ms,
                attempted=attempted,
                backend_actions=backend_actions,
                put_attempts=put_attempts,
                context_lifecycle="execution_snapshot",
                locked_candidates=[
                    asdict(candidate) for candidate in request.pose_context.locked_candidates
                ],
                actual_pose=asdict(request.pose_context.current_actual_pose),
            )
        )

        if request.pose_context.anchor_object_id != request.target_receptacle_id:
            return self._result(
                request=request,
                started_ms=started_ms,
                attempted=attempted,
                backend_actions=backend_actions,
                put_attempts=put_attempts,
                classification="execution_state_uncertain",
                detail="PoseContext anchor does not match the locked put target.",
                trace_events=trace_events,
            )

        before = self._read(
            request,
            started_ms=started_ms,
            attempted=attempted,
            backend_actions=backend_actions,
            put_attempts=put_attempts,
            trace_events=trace_events,
            attempt_id=None,
            attempt_phase="precondition",
            requested_pose=request.pose_context.current_actual_pose,
        )
        if before is None or not _read_proves_held_state(before, request.held_object_id):
            return self._result(
                request=request,
                started_ms=started_ms,
                attempted=attempted,
                backend_actions=backend_actions,
                put_attempts=put_attempts,
                classification="execution_state_uncertain",
                final_read=before,
                detail="Could not prove the exact pre-action state.",
                trace_events=trace_events,
            )

        candidates = request.pose_context.locked_candidates
        for candidate_index, candidate in enumerate(candidates):
            stop = self._pre_candidate_stop(started_ms, len(attempted), backend_actions)
            if stop is not None:
                return self._budget_result(
                    request,
                    started_ms,
                    attempted,
                    backend_actions,
                    put_attempts,
                    stop,
                    before,
                    trace_events,
                )
            attempted.append(candidate)
            attempt_id = f"{request.pose_context.context_id}:attempt-{candidate_index + 1:04d}"
            attempt_phase = "current_pose" if candidate_index == 0 else "local_pose"
            trace_events.append(
                self._trace_event(
                    event="attempt_started",
                    request=request,
                    started_ms=started_ms,
                    attempted=attempted,
                    backend_actions=backend_actions,
                    put_attempts=put_attempts,
                    attempt_id=attempt_id,
                    attempt_phase=attempt_phase,
                    requested_pose=asdict(candidate),
                    actual_pose=(
                        asdict(before.actual_agent_pose)
                        if before.actual_agent_pose is not None
                        else None
                    ),
                )
            )

            if candidate_index > 0:
                stop = self._pre_action_stop(started_ms, backend_actions)
                if stop is not None:
                    return self._budget_result(
                        request,
                        started_ms,
                        attempted,
                        backend_actions,
                        put_attempts,
                        stop,
                        before,
                        trace_events,
                    )
                backend_actions += 1
                move_result = self._move(
                    request,
                    pose=candidate,
                    started_ms=started_ms,
                    attempted=attempted,
                    backend_actions=backend_actions,
                    put_attempts=put_attempts,
                    trace_events=trace_events,
                    attempt_id=attempt_id,
                    attempt_phase=attempt_phase,
                )
                if move_result is None or move_result.status == "uncertain":
                    return self._result(
                        request=request,
                        started_ms=started_ms,
                        attempted=attempted,
                        backend_actions=backend_actions,
                        put_attempts=put_attempts,
                        classification="execution_state_uncertain",
                        final_read=before,
                        detail="Move returned no authoritative result.",
                        trace_events=trace_events,
                    )
                after_move = self._read(
                    request,
                    started_ms=started_ms,
                    attempted=attempted,
                    backend_actions=backend_actions,
                    put_attempts=put_attempts,
                    trace_events=trace_events,
                    attempt_id=attempt_id,
                    attempt_phase="post_move",
                    requested_pose=candidate,
                )
                if after_move is None or not _move_state_is_proven(
                    before=before,
                    after=after_move,
                    requested_pose=candidate,
                    move_result=move_result,
                ):
                    return self._result(
                        request=request,
                        started_ms=started_ms,
                        attempted=attempted,
                        backend_actions=backend_actions,
                        put_attempts=put_attempts,
                        classification="execution_state_uncertain",
                        final_read=after_move,
                        detail="Move return and external state did not agree.",
                        trace_events=trace_events,
                    )
                before = after_move
                if move_result.status == "failure":
                    continue

            stop = self._pre_action_stop(started_ms, backend_actions)
            if stop is not None:
                return self._budget_result(
                    request,
                    started_ms,
                    attempted,
                    backend_actions,
                    put_attempts,
                    stop,
                    before,
                    trace_events,
                )
            backend_actions += 1
            put_attempts += 1
            action_result = self._put(
                request,
                started_ms=started_ms,
                attempted=attempted,
                backend_actions=backend_actions,
                put_attempts=put_attempts,
                trace_events=trace_events,
                attempt_id=attempt_id,
                attempt_phase=attempt_phase,
                requested_pose=candidate,
            )
            if action_result is None or action_result.status == "uncertain":
                return self._result(
                    request=request,
                    started_ms=started_ms,
                    attempted=attempted,
                    backend_actions=backend_actions,
                    put_attempts=put_attempts,
                    classification="execution_state_uncertain",
                    final_read=before,
                    detail="PutObject returned no authoritative result.",
                    trace_events=trace_events,
                )
            after = self._read(
                request,
                started_ms=started_ms,
                attempted=attempted,
                backend_actions=backend_actions,
                put_attempts=put_attempts,
                trace_events=trace_events,
                attempt_id=attempt_id,
                attempt_phase="post_put",
                requested_pose=candidate,
            )
            if after is None:
                return self._result(
                    request=request,
                    started_ms=started_ms,
                    attempted=attempted,
                    backend_actions=backend_actions,
                    put_attempts=put_attempts,
                    classification="execution_state_uncertain",
                    detail="Could not read the post-put external state.",
                    trace_events=trace_events,
                )
            transition = classify_put_transition(
                action_result=action_result,
                before=before,
                after=after,
                held_object_id=request.held_object_id,
                target_receptacle_id=request.target_receptacle_id,
            )
            if transition.classification == "success":
                return self._result(
                    request=request,
                    started_ms=started_ms,
                    attempted=attempted,
                    backend_actions=backend_actions,
                    put_attempts=put_attempts,
                    classification="success",
                    final_read=after,
                    detail=action_result.detail,
                    trace_events=trace_events,
                )
            if transition.classification == "execution_state_uncertain":
                return self._result(
                    request=request,
                    started_ms=started_ms,
                    attempted=attempted,
                    backend_actions=backend_actions,
                    put_attempts=put_attempts,
                    classification="execution_state_uncertain",
                    final_read=after,
                    detail=action_result.detail,
                    trace_events=trace_events,
                )
            before = after

            # External uncertainty and success have already won the priority
            # race. Apply the three fixed budget stops before another action.
            stop = self._post_attempt_stop(started_ms, len(attempted), backend_actions)
            if stop is not None:
                return self._budget_result(
                    request,
                    started_ms,
                    attempted,
                    backend_actions,
                    put_attempts,
                    stop,
                    before,
                    trace_events,
                )

        return self._budget_result(
            request,
            started_ms,
            attempted,
            backend_actions,
            put_attempts,
            "candidates_exhausted",
            before,
            trace_events,
        )

    def _read(
        self,
        request: PutExecutionRequest,
        *,
        started_ms: float,
        attempted: list[AgentPose],
        backend_actions: int,
        put_attempts: int,
        trace_events: list[dict[str, Any]],
        attempt_id: str | None,
        attempt_phase: str,
        requested_pose: AgentPose,
    ) -> ExternalRead | None:
        read_started_ms = float(self._monotonic_ms())
        trace_events.append(
            self._trace_event(
                event="state_read_started",
                request=request,
                started_ms=started_ms,
                attempted=attempted,
                backend_actions=backend_actions,
                put_attempts=put_attempts,
                attempt_id=attempt_id,
                attempt_phase=attempt_phase,
                requested_pose=asdict(requested_pose),
            )
        )
        try:
            result = self._backend.read_external_state(
                held_object_id=request.held_object_id,
                target_receptacle_id=request.target_receptacle_id,
            )
        except Exception:
            result = None
        trace_events.append(
            self._trace_event(
                event="state_read_result",
                request=request,
                started_ms=started_ms,
                attempted=attempted,
                backend_actions=backend_actions,
                put_attempts=put_attempts,
                attempt_id=attempt_id,
                attempt_phase=attempt_phase,
                requested_pose=asdict(requested_pose),
                actual_pose=(
                    asdict(result.actual_agent_pose)
                    if result is not None and result.actual_agent_pose is not None
                    else None
                ),
                external_status=result.status if result is not None else "error",
                raw_event_ref=result.raw_event_ref if result is not None else None,
                raw_event_hash=result.raw_event_hash if result is not None else None,
                state_read_elapsed_ms=self._phase_elapsed_ms(read_started_ms),
            )
        )
        return result

    def _put(
        self,
        request: PutExecutionRequest,
        *,
        started_ms: float,
        attempted: list[AgentPose],
        backend_actions: int,
        put_attempts: int,
        trace_events: list[dict[str, Any]],
        attempt_id: str,
        attempt_phase: str,
        requested_pose: AgentPose,
    ) -> ExternalActionResult | None:
        action_started_ms = float(self._monotonic_ms())
        trace_events.append(
            self._trace_event(
                event="put_started",
                request=request,
                started_ms=started_ms,
                attempted=attempted,
                backend_actions=backend_actions - 1,
                put_attempts=put_attempts - 1,
                attempt_id=attempt_id,
                attempt_phase=attempt_phase,
                requested_pose=asdict(requested_pose),
            )
        )
        try:
            result = self._backend.put_object(
                object_id=request.held_object_id,
                receptacle_object_id=request.target_receptacle_id,
            )
        except Exception:
            result = None
        trace_events.append(
            self._trace_event(
                event="put_result",
                request=request,
                started_ms=started_ms,
                attempted=attempted,
                backend_actions=backend_actions,
                put_attempts=put_attempts,
                attempt_id=attempt_id,
                attempt_phase=attempt_phase,
                requested_pose=asdict(requested_pose),
                actual_pose=(
                    asdict(result.actual_agent_pose)
                    if result is not None and result.actual_agent_pose is not None
                    else None
                ),
                external_status=result.status if result is not None else "uncertain",
                raw_event_ref=result.raw_event_ref if result is not None else None,
                raw_event_hash=result.raw_event_hash if result is not None else None,
                put_elapsed_ms=self._phase_elapsed_ms(action_started_ms),
            )
        )
        return result

    def _move(
        self,
        request: PutExecutionRequest,
        *,
        pose: AgentPose,
        started_ms: float,
        attempted: list[AgentPose],
        backend_actions: int,
        put_attempts: int,
        trace_events: list[dict[str, Any]],
        attempt_id: str,
        attempt_phase: str,
    ) -> ExternalActionResult | None:
        move_started_ms = float(self._monotonic_ms())
        trace_events.append(
            self._trace_event(
                event="move_started",
                request=request,
                started_ms=started_ms,
                attempted=attempted,
                backend_actions=backend_actions - 1,
                put_attempts=put_attempts,
                attempt_id=attempt_id,
                attempt_phase=attempt_phase,
                requested_pose=asdict(pose),
            )
        )
        try:
            result = self._backend.move_to(pose=pose)
        except Exception:
            result = None
        trace_events.append(
            self._trace_event(
                event="move_result",
                request=request,
                started_ms=started_ms,
                attempted=attempted,
                backend_actions=backend_actions,
                put_attempts=put_attempts,
                attempt_id=attempt_id,
                attempt_phase=attempt_phase,
                requested_pose=asdict(pose),
                actual_pose=(
                    asdict(result.actual_agent_pose)
                    if result is not None and result.actual_agent_pose is not None
                    else None
                ),
                external_status=result.status if result is not None else "uncertain",
                raw_event_ref=result.raw_event_ref if result is not None else None,
                raw_event_hash=result.raw_event_hash if result is not None else None,
                move_elapsed_ms=self._phase_elapsed_ms(move_started_ms),
            )
        )
        return result

    def _pre_candidate_stop(
        self,
        started_ms: float,
        attempted_count: int,
        backend_actions: int,
    ) -> str | None:
        if backend_actions >= self._budget.max_backend_actions:
            return "max_backend_actions"
        if self._elapsed_ms(started_ms) >= self._budget.max_elapsed_ms:
            return "max_elapsed_ms"
        if attempted_count >= self._budget.max_pose_candidates:
            return "max_pose_candidates"
        return None

    def _pre_action_stop(self, started_ms: float, backend_actions: int) -> str | None:
        if backend_actions >= self._budget.max_backend_actions:
            return "max_backend_actions"
        if self._elapsed_ms(started_ms) >= self._budget.max_elapsed_ms:
            return "max_elapsed_ms"
        return None

    def _post_attempt_stop(
        self,
        started_ms: float,
        attempted_count: int,
        backend_actions: int,
    ) -> str | None:
        if backend_actions >= self._budget.max_backend_actions:
            return "max_backend_actions"
        if self._elapsed_ms(started_ms) >= self._budget.max_elapsed_ms:
            return "max_elapsed_ms"
        if attempted_count >= self._budget.max_pose_candidates:
            return "max_pose_candidates"
        return None

    def _elapsed_ms(self, started_ms: float) -> float:
        return max(0.0, float(self._monotonic_ms()) - started_ms)

    def _phase_elapsed_ms(self, started_ms: float) -> float:
        return max(0.0, float(self._monotonic_ms()) - started_ms)

    def _budget_result(
        self,
        request: PutExecutionRequest,
        started_ms: float,
        attempted: list[AgentPose],
        backend_actions: int,
        put_attempts: int,
        reason: str,
        final_read: ExternalRead | None,
        trace_events: list[dict[str, Any]],
    ) -> PutExecutionResult:
        return self._result(
            request=request,
            started_ms=started_ms,
            attempted=attempted,
            backend_actions=backend_actions,
            put_attempts=put_attempts,
            classification="harness_operation_failure",
            budget_stop_reason=reason,
            final_read=final_read,
            trace_events=trace_events,
        )

    def _result(
        self,
        *,
        request: PutExecutionRequest,
        started_ms: float,
        attempted: list[AgentPose],
        backend_actions: int,
        put_attempts: int,
        classification: Literal[
            "success",
            "harness_operation_failure",
            "execution_state_uncertain",
        ],
        trace_events: list[dict[str, Any]],
        budget_stop_reason: str | None = None,
        final_read: ExternalRead | None = None,
        detail: str = "",
    ) -> PutExecutionResult:
        elapsed_ms = self._elapsed_ms(started_ms)
        invalidation_reason = (
            "execution_completed"
            if classification == "success"
            else budget_stop_reason or classification
        )
        trace_events.append(
            self._trace_event(
                event="context_invalidated",
                request=request,
                started_ms=started_ms,
                attempted=attempted,
                backend_actions=backend_actions,
                put_attempts=put_attempts,
                invalidation_reason=invalidation_reason,
            )
        )
        trace_events.append(
            self._trace_event(
                event="execution_terminal",
                request=request,
                started_ms=started_ms,
                attempted=attempted,
                backend_actions=backend_actions,
                put_attempts=put_attempts,
                classification=classification,
                success=classification == "success",
                budget_stop_reason=budget_stop_reason,
                elapsed_ms=elapsed_ms,
                raw_event_ref=(final_read.raw_event_ref if final_read is not None else None),
                raw_event_hash=(final_read.raw_event_hash if final_read is not None else None),
            )
        )
        return PutExecutionResult(
            success=classification == "success",
            classification=classification,
            locked_candidates_hash=request.pose_context.candidates_hash,
            attempted_poses=tuple(attempted),
            pose_candidates_attempted=len(attempted),
            put_attempt_count=put_attempts,
            backend_action_count=backend_actions,
            elapsed_ms=elapsed_ms,
            budget_stop_reason=budget_stop_reason,
            detail=detail,
            final_read=final_read,
            trace_events=tuple(trace_events),
        )

    def _trace_event(
        self,
        *,
        event: str,
        request: PutExecutionRequest,
        started_ms: float,
        attempted: list[AgentPose],
        backend_actions: int,
        put_attempts: int,
        attempt_id: str | None = None,
        attempt_phase: str | None = None,
        **details: Any,
    ) -> dict[str, Any]:
        elapsed_ms = self._elapsed_ms(started_ms)
        payload: dict[str, Any] = {
            "event": event,
            "execution_kind": "put",
            "context_kind": "pose",
            "tool_call_id": request.tool_call_id,
            "context_id": request.pose_context.context_id,
            "scene_generation": request.pose_context.scene_generation,
            "goal_generation": request.pose_context.goal_generation,
            "source_event_sequence": request.pose_context.source_event_sequence,
            "source_frame_hash": request.pose_context.source_frame_hash,
            "locked_candidates_hash": request.pose_context.candidates_hash,
            "held_object_id": request.held_object_id,
            "target_receptacle_id": request.target_receptacle_id,
            "attempt_id": attempt_id,
            "attempt_phase": attempt_phase,
            "budget_limit": {
                "candidates": self._budget.max_pose_candidates,
                "backend_actions": self._budget.max_backend_actions,
                "elapsed_ms": self._budget.max_elapsed_ms,
            },
            "budget_used": {
                "candidates": len(attempted),
                "backend_actions": backend_actions,
                "put_attempts": put_attempts,
                "elapsed_ms": elapsed_ms,
            },
            "budget_stop_reason": None,
        }
        payload.update(details)
        return payload


class LegacyManipulationExecutor(Protocol):
    def execute(self, *, action: str, arguments: dict[str, Any]) -> object: ...


class PutExecutor(Protocol):
    def execute_put(self, request: PutExecutionRequest) -> object: ...


class ManipulationRouter:
    def __init__(
        self,
        *,
        put_executor: PutExecutor,
        legacy_executor: LegacyManipulationExecutor,
    ) -> None:
        self._put_executor = put_executor
        self._legacy_executor = legacy_executor

    def execute(self, *, action: str, arguments: dict[str, Any]) -> object:
        normalized = action.strip().lower()
        if normalized != "put":
            return self._legacy_executor.execute(action=normalized, arguments=arguments)
        request = arguments.get("request")
        if not isinstance(request, PutExecutionRequest):
            raise TypeError("put arguments must contain a PutExecutionRequest under 'request'")
        return self._put_executor.execute_put(request)


def _read_proves_exact_state(read: ExternalRead, held_object_id: str) -> bool:
    return (
        read.status == "ok"
        and read.exact_object_present
        and bool(read.raw_event_ref)
        and bool(read.raw_event_hash)
        and (
            read.held_object_id == held_object_id or held_object_id not in read.inventory_object_ids
        )
    )


def _read_proves_held_state(read: ExternalRead, held_object_id: str) -> bool:
    return (
        _read_proves_exact_state(read, held_object_id)
        and read.held_object_id == held_object_id
        and held_object_id in read.inventory_object_ids
        and read.exact_object_is_picked_up is True
    )


def _move_state_is_proven(
    *,
    before: ExternalRead,
    after: ExternalRead,
    requested_pose: AgentPose,
    move_result: ExternalActionResult,
) -> bool:
    if not _read_proves_held_state(before, before.held_object_id or ""):
        return False
    held_object_id = before.held_object_id
    if held_object_id is None or not _read_proves_held_state(after, held_object_id):
        return False
    if before.actual_agent_pose is None or after.actual_agent_pose is None:
        return False
    if move_result.status == "success":
        # THOR updates containment metadata as a picked-up object moves through
        # receptacles. Inventory + isPickedUp remain the authoritative held gate.
        if before.inventory_object_ids != after.inventory_object_ids:
            return False
        return after.actual_agent_pose.matches(requested_pose)
    if move_result.status == "failure":
        return before.action_state == after.action_state and after.actual_agent_pose.matches(
            before.actual_agent_pose
        )
    return False


def _angular_delta(left: float, right: float) -> float:
    return abs((left - right + 180.0) % 360.0 - 180.0)


def _object_type_key(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


def _canonical_query(value: str) -> str:
    return " ".join(value.casefold().strip().split())


_EXPLICIT_INSTANCE = re.compile(r"[a-z0-9]+(?: [a-z0-9]+)* [0-9]+")
