"""Deterministic execution core for ALFWorld navigation and manipulation."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from typing import Any, Literal, Protocol

from homemaster.benchmarking.alfworld.gateway import (
    ExternalEventRead,
    GatewayActionResult,
    OracleActionGateway,
)
from homemaster.benchmarking.alfworld.model_view import (
    ObjectObservationRead,
    VisibleObjectView,
)
from homemaster.benchmarking.alfworld.pose_snapshot import (
    OraclePose,
    OraclePoseLookup,
    OraclePoseStore,
    SceneObjectScanInput,
)
from homemaster.benchmarking.alfworld.types import (
    AlfworldAction,
    AlfworldExecutionFeedback,
    make_execution_feedback,
)

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

    def matching_type(self, label: str) -> tuple[SceneObjectRef, ...]:
        labels = self.labels_by_type.get(_object_type_key(label), ())
        return tuple(self.by_canonical_label[item] for item in labels)


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


NavigationError = Literal[
    "target_not_found",
    "target_not_visible",
    "object_already_held",
    "oracle_anchor_unresolved",
    "oracle_pose_missing",
    "oracle_pose_malformed",
    "oracle_navigation_failed",
    "oracle_pose_mismatch",
    "oracle_target_not_visible",
    "execution_state_uncertain",
]
ExecutionContextState = Literal["active", "consumed", "invalid"]


@dataclass(frozen=True)
class OracleExecutionContext:
    scene_generation: int
    goal_generation: int
    source_event_sequence: int
    current_event_sequence: int
    requested_target_id: str
    navigation_anchor_id: str
    oracle_snapshot_hash: str
    oracle_pose_hash: str
    anchor_state_hash: str
    actual_pose: OraclePose
    final_event_hash: str
    final_event_ref: str
    state: ExecutionContextState = "active"

    def __post_init__(self) -> None:
        if self.scene_generation < 0 or self.goal_generation < 0:
            raise ValueError("execution context generations cannot be negative")
        if self.source_event_sequence < 0 or self.current_event_sequence < 0:
            raise ValueError("execution context event sequences cannot be negative")
        if self.current_event_sequence < self.source_event_sequence:
            raise ValueError("execution context event sequence moved backwards")
        for name in (
            "oracle_snapshot_hash",
            "oracle_pose_hash",
            "anchor_state_hash",
            "final_event_hash",
        ):
            if not _is_sha256(getattr(self, name)):
                raise ValueError(f"execution context requires a valid {name}")
        if (
            not self.requested_target_id
            or not self.navigation_anchor_id
            or not self.final_event_ref
        ):
            raise ValueError("execution context requires exact target, anchor, and final event")
        if self.state not in {"active", "consumed", "invalid"}:
            raise ValueError(f"unsupported execution context state: {self.state}")


@dataclass(frozen=True)
class NavigationTargetLock:
    requested_label: str
    target: SceneObjectRef
    observation: ObjectObservationRead
    current_object: SceneObjectScanInput


@dataclass(frozen=True)
class NavigationTargetResolution:
    lock: NavigationTargetLock | None
    error: NavigationError | None


@dataclass(frozen=True)
class NavigationAnchorResolution:
    anchor_id: str | None
    lookup: OraclePoseLookup | None
    anchor_object: SceneObjectScanInput | None
    error: NavigationError | None


@dataclass(frozen=True)
class OracleNavigationResult:
    success: bool
    error: NavigationError | None
    terminal: bool
    score_eligible: bool
    target_label: str
    target_id: str | None
    anchor_id: str | None
    context: OracleExecutionContext | None
    final_event: ExternalEventRead | None
    backend_action_count: int
    trace_events: tuple[dict[str, Any], ...]

    def __post_init__(self) -> None:
        if self.success:
            if self.error is not None or self.terminal or not self.score_eligible:
                raise ValueError("successful navigation result has terminal fields")
            if self.context is None or self.backend_action_count != 1:
                raise ValueError("successful navigation requires one action and active context")
        elif self.error is None:
            raise ValueError("failed navigation result requires an error")


class NavigationAnchorResolver:
    def resolve(
        self,
        *,
        target: SceneObjectScanInput,
        objects: tuple[SceneObjectScanInput, ...],
        pose_store: OraclePoseStore,
        scene_generation: int,
        scene_reset_fingerprint: str,
        snapshot_sha256: str,
    ) -> NavigationAnchorResolution:
        by_id = {item.exact_object_id: item for item in objects}
        parents = []
        for parent_id in target.parent_receptacle_ids:
            parent = by_id.get(parent_id)
            if parent is not None and target.exact_object_id in parent.receptacle_object_ids:
                parents.append(parent)
        innermost = [
            parent
            for parent in parents
            if not any(
                parent.exact_object_id in other.parent_receptacle_ids
                for other in parents
                if other.exact_object_id != parent.exact_object_id
            )
        ]
        if len(innermost) != 1:
            return NavigationAnchorResolution(None, None, None, "oracle_anchor_unresolved")
        parent = innermost[0]
        try:
            lookup = pose_store.get_pose(
                scene_generation=scene_generation,
                scene_reset_fingerprint=scene_reset_fingerprint,
                exact_anchor_id=parent.exact_object_id,
            )
        except Exception:
            return NavigationAnchorResolution(None, None, None, "execution_state_uncertain")
        identity_error = _lookup_identity_error(
            lookup,
            scene_generation=scene_generation,
            scene_reset_fingerprint=scene_reset_fingerprint,
            snapshot_sha256=snapshot_sha256,
        )
        if identity_error is not None:
            return NavigationAnchorResolution(None, lookup, parent, identity_error)
        if lookup.status in {"stale", "error"}:
            return NavigationAnchorResolution(None, lookup, parent, "execution_state_uncertain")
        if (
            lookup.status != "ok"
            or lookup.pose is None
            or lookup.pose_sha256 is None
            or lookup.pose_freshness_sha256 != parent.pose_freshness_sha256
        ):
            return NavigationAnchorResolution(None, lookup, parent, "oracle_anchor_unresolved")
        return NavigationAnchorResolution(parent.exact_object_id, lookup, parent, None)


class OracleNavigationExecutor:
    def __init__(
        self,
        *,
        scene_index: SceneObjectIndex,
        public_object_types: tuple[str, ...],
        visible_object_view: VisibleObjectView,
        current_event: ExternalEventRead,
        pose_store: OraclePoseStore,
        parent_resolver: NavigationAnchorResolver,
        gateway: OracleActionGateway,
        context_factory: Callable[..., OracleExecutionContext] = OracleExecutionContext,
    ) -> None:
        self._scene_index = scene_index
        self._public_type_keys = {_object_type_key(item) for item in public_object_types}
        self._view = visible_object_view
        self._current_event = current_event
        self._pose_store = pose_store
        self._parent_resolver = parent_resolver
        self._gateway = gateway
        self._context_factory = context_factory

    def execute(
        self,
        requested_label: str,
        *,
        scene_generation: int,
        goal_generation: int,
        scene_reset_fingerprint: str,
        snapshot_sha256: str,
    ) -> OracleNavigationResult:
        trace: list[dict[str, Any]] = [
            {"event": "visibility_gate_started", "requested_label": requested_label}
        ]
        target = self._resolve_target(requested_label)
        trace.append(
            {
                "event": "visibility_gate_result",
                "success": target.lock is not None,
                "error": target.error,
                "target_id": target.lock.target.object_id if target.lock is not None else None,
            }
        )
        if target.lock is None:
            return _navigation_failure(
                requested_label,
                target.error or "execution_state_uncertain",
                trace,
            )
        lock = target.lock

        trace.append({"event": "pose_lookup_started", "target_id": lock.target.object_id})
        try:
            lookup = self._pose_store.get_pose(
                scene_generation=scene_generation,
                scene_reset_fingerprint=scene_reset_fingerprint,
                exact_anchor_id=lock.target.object_id,
            )
        except Exception:
            return _navigation_failure(requested_label, "execution_state_uncertain", trace, lock)
        trace.append({"event": "pose_lookup_result", "status": lookup.status})
        identity_error = _lookup_identity_error(
            lookup,
            scene_generation=scene_generation,
            scene_reset_fingerprint=scene_reset_fingerprint,
            snapshot_sha256=snapshot_sha256,
        )
        if identity_error is not None:
            return _navigation_failure(requested_label, identity_error, trace, lock)

        anchor_id = lock.target.object_id
        anchor_object = lock.current_object
        if lookup.status == "coverage_miss":
            return _navigation_failure(requested_label, "oracle_pose_missing", trace, lock)
        if lookup.status == "malformed":
            return _navigation_failure(requested_label, "oracle_pose_malformed", trace, lock)
        if lookup.status in {"stale", "error"}:
            return _navigation_failure(requested_label, "execution_state_uncertain", trace, lock)
        if lookup.status in {"unobserved", "relocated", "absent"}:
            if self._current_event.objects is None:
                return _navigation_failure(
                    requested_label, "execution_state_uncertain", trace, lock
                )
            anchor = self._parent_resolver.resolve(
                target=lock.current_object,
                objects=tuple(self._current_event.objects),
                pose_store=self._pose_store,
                scene_generation=scene_generation,
                scene_reset_fingerprint=scene_reset_fingerprint,
                snapshot_sha256=snapshot_sha256,
            )
            trace.append(
                {
                    "event": "parent_anchor_result",
                    "anchor_id": anchor.anchor_id,
                    "error": anchor.error,
                }
            )
            if anchor.error is not None or anchor.lookup is None or anchor.anchor_object is None:
                return _navigation_failure(
                    requested_label,
                    anchor.error or "oracle_anchor_unresolved",
                    trace,
                    lock,
                )
            anchor_id = anchor.anchor_id or ""
            anchor_object = anchor.anchor_object
            lookup = anchor.lookup
        elif lookup.status != "ok":
            return _navigation_failure(requested_label, "execution_state_uncertain", trace, lock)

        if lookup.pose is None or lookup.pose_sha256 is None:
            return _navigation_failure(requested_label, "oracle_pose_malformed", trace, lock)
        if lookup.pose_freshness_sha256 != anchor_object.pose_freshness_sha256:
            return _navigation_failure(requested_label, "execution_state_uncertain", trace, lock)
        before = self._current_event
        if (
            before.status != "ok"
            or before.pose is None
            or before.world_sha256 is None
            or before.raw_event_sha256 is None
        ):
            return _navigation_failure(requested_label, "execution_state_uncertain", trace, lock)

        action = self._gateway.execute_navigation(_navigation_payload(lookup.pose))
        trace.append(
            {
                "event": "navigation_gateway_result",
                "request_sha256": action.request.request_sha256,
                "success": action.success,
            }
        )
        error = _navigation_action_error(
            action,
            before=before,
            requested_pose=lookup.pose,
            requested_target_id=lock.target.object_id,
        )
        if error is not None:
            return _navigation_failure(
                requested_label,
                error,
                trace,
                lock,
                anchor_id=anchor_id,
                final_event=action.event,
                backend_action_count=1,
            )
        event = action.event
        assert event.raw_event_ref is not None
        assert event.raw_event_sha256 is not None
        context = self._context_factory(
            scene_generation=scene_generation,
            goal_generation=goal_generation,
            source_event_sequence=self._view.event_sequence,
            current_event_sequence=self._view.event_sequence + 1,
            requested_target_id=lock.target.object_id,
            navigation_anchor_id=anchor_id,
            oracle_snapshot_hash=lookup.snapshot_sha256,
            oracle_pose_hash=lookup.pose_sha256,
            anchor_state_hash=anchor_object.pose_freshness_sha256,
            actual_pose=event.pose,
            final_event_hash=event.raw_event_sha256,
            final_event_ref=event.raw_event_ref,
            state="active",
        )
        trace.append({"event": "execution_context_created", "state": context.state})
        return OracleNavigationResult(
            success=True,
            error=None,
            terminal=False,
            score_eligible=True,
            target_label=requested_label,
            target_id=lock.target.object_id,
            anchor_id=anchor_id,
            context=context,
            final_event=event,
            backend_action_count=1,
            trace_events=tuple(trace),
        )

    def _resolve_target(self, requested_label: str) -> NavigationTargetResolution:
        canonical = _canonical_query(requested_label)
        explicit = _EXPLICIT_INSTANCE.fullmatch(canonical)
        if explicit is not None:
            base, ordinal_text = canonical.rsplit(" ", 1)
            ordinal = int(ordinal_text)
        else:
            base, ordinal = canonical, None
        type_key = _object_type_key(base)
        if not canonical or type_key not in self._public_type_keys:
            return NavigationTargetResolution(None, "target_not_found")
        candidates = self._scene_index.matching_type(base)
        if ordinal is not None:
            if ordinal <= 0 or ordinal > len(candidates):
                return NavigationTargetResolution(None, "target_not_visible")
            candidates = (candidates[ordinal - 1],)
        if self._current_event.objects is None:
            return NavigationTargetResolution(None, "execution_state_uncertain")
        current = {item.exact_object_id: item for item in self._current_event.objects}
        held = False
        for candidate in candidates:
            observation = self._view.read(candidate.object_id)
            if observation.status != "ok":
                return NavigationTargetResolution(None, "execution_state_uncertain")
            item = current.get(candidate.object_id)
            if item is None:
                return NavigationTargetResolution(None, "execution_state_uncertain")
            if item.is_picked_up:
                held = True
            if observation.strict_visible:
                return NavigationTargetResolution(
                    NavigationTargetLock(requested_label, candidate, observation, item),
                    None,
                )
        if held:
            return NavigationTargetResolution(None, "object_already_held")
        return NavigationTargetResolution(None, "target_not_visible")


def _lookup_identity_error(
    lookup: OraclePoseLookup,
    *,
    scene_generation: int,
    scene_reset_fingerprint: str,
    snapshot_sha256: str,
) -> NavigationError | None:
    if (
        lookup.scene_generation != scene_generation
        or lookup.scene_reset_fingerprint != scene_reset_fingerprint
        or lookup.snapshot_sha256 != snapshot_sha256
    ):
        return "execution_state_uncertain"
    return None


def _navigation_payload(pose: OraclePose) -> dict[str, Any]:
    return {
        "action": "TeleportFull",
        "x": pose.x,
        "y": pose.y,
        "z": pose.z,
        "rotateOnTeleport": True,
        "rotation": pose.rotation,
        "horizon": pose.horizon,
    }


def _navigation_action_error(
    result: GatewayActionResult,
    *,
    before: ExternalEventRead,
    requested_pose: OraclePose,
    requested_target_id: str,
) -> NavigationError | None:
    event = result.event
    if event.status != "ok" or event.returned_action != "TeleportFull":
        return "execution_state_uncertain"
    if event.action_success is not True:
        if (
            event.action_success is False
            and event.world_sha256 == before.world_sha256
            and event.pose == before.pose
        ):
            return "oracle_navigation_failed"
        return "execution_state_uncertain"
    if event.world_sha256 is None or event.world_sha256 != before.world_sha256:
        return "execution_state_uncertain"
    if event.pose != requested_pose:
        return "oracle_pose_mismatch"
    if event.raw_event_ref is None or event.raw_event_sha256 is None:
        return "execution_state_uncertain"
    if requested_target_id not in event.strict_visible_exact_ids:
        return "oracle_target_not_visible"
    return None


def _navigation_failure(
    target_label: str,
    error: NavigationError,
    trace: list[dict[str, Any]],
    lock: NavigationTargetLock | None = None,
    *,
    anchor_id: str | None = None,
    final_event: ExternalEventRead | None = None,
    backend_action_count: int = 0,
) -> OracleNavigationResult:
    terminal = error not in {"target_not_found", "target_not_visible", "object_already_held"}
    return OracleNavigationResult(
        success=False,
        error=error,
        terminal=terminal,
        score_eligible=not terminal,
        target_label=target_label,
        target_id=lock.target.object_id if lock is not None else None,
        anchor_id=anchor_id,
        context=None,
        final_event=final_event,
        backend_action_count=backend_action_count,
        trace_events=tuple(trace),
    )


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


@dataclass(frozen=True)
class ThorObjectView:
    exact_object_id: str
    object_type: str
    visible: bool
    is_picked_up: bool
    parent_ids: tuple[str, ...]
    child_ids: tuple[str, ...]
    pickupable: bool | None
    receptacle: bool | None
    openable: bool | None
    is_open: bool | None
    toggleable: bool | None
    is_toggled: bool | None
    sliceable: bool | None
    is_sliced: bool | None
    is_dirty: bool | None
    temperature: str | None


@dataclass(frozen=True)
class ThorStateSnapshot:
    status: Literal["ok", "malformed", "error"]
    inventory_ids: tuple[str, ...]
    objects: tuple[ThorObjectView, ...]
    state_sha256: str | None

    def get(self, exact_object_id: str) -> ThorObjectView | None:
        return next(
            (item for item in self.objects if item.exact_object_id == exact_object_id),
            None,
        )


@dataclass(frozen=True)
class OracleManipulationResult:
    feedback: AlfworldExecutionFeedback
    context: OracleExecutionContext | None
    backend_action_count: int
    trace_events: tuple[dict[str, Any], ...]

    @property
    def success(self) -> bool:
        return self.feedback.success


class OracleManipulationExecutor:
    def __init__(
        self,
        *,
        scene_index: SceneObjectIndex,
        visible_object_view: VisibleObjectView,
        current_event: ExternalEventRead,
        raw_event: Any,
        raw_event_reader: Callable[[], Any],
        context: OracleExecutionContext | None,
        gateway: OracleActionGateway,
        scene_generation: int,
        goal_generation: int,
    ) -> None:
        self._scene_index = scene_index
        self._view = visible_object_view
        self._current_event = current_event
        self._raw_event = raw_event
        self._raw_event_reader = raw_event_reader
        self._context = context
        self._gateway = gateway
        self._scene_generation = scene_generation
        self._goal_generation = goal_generation

    def execute(
        self,
        action: AlfworldAction,
        *,
        object_label: str | None,
        target_label: str | None,
    ) -> OracleManipulationResult:
        trace: list[dict[str, Any]] = [{"event": "manipulation_precondition_started"}]
        context = self._context
        if action not in {"take", "open", "close", "put", "use", "slice", "heat", "cool", "clean"}:
            return self._zero_failure(
                action,
                "action_not_applicable",
                object_label,
                target_label,
                trace,
            )
        if (
            context is None
            or context.state != "active"
            or context.scene_generation != self._scene_generation
            or context.goal_generation != self._goal_generation
        ):
            return self._zero_failure(
                action,
                "navigation_required",
                object_label,
                target_label,
                trace,
            )
        requested_label = _manipulation_requested_target(
            action,
            object_label=object_label,
            target_label=target_label,
        )
        context_ref = self._scene_index.by_canonical_label.get(
            _label_for_exact_id(self._scene_index, context.requested_target_id) or ""
        )
        if (
            requested_label is None
            or context_ref is None
            or not _label_matches_ref(requested_label, context_ref, self._scene_index)
        ):
            return self._zero_failure(
                action,
                "navigation_required",
                object_label,
                target_label,
                trace,
            )
        observation = self._view.read(context.requested_target_id)
        if observation.status != "ok":
            return self._uncertain(action, object_label, target_label, trace)
        if not observation.strict_visible:
            return self._zero_failure(
                action,
                "navigation_required",
                object_label,
                target_label,
                trace,
            )
        if (
            self._view.event_sequence != context.current_event_sequence
            or self._current_event.pose != context.actual_pose
        ):
            return self._uncertain(action, object_label, target_label, trace)

        before = read_thor_state_snapshot(self._raw_event)
        if before.status != "ok":
            return self._uncertain(action, object_label, target_label, trace)
        target = before.get(context.requested_target_id)
        if target is None:
            return self._uncertain(action, object_label, target_label, trace)
        inventory_labels = _inventory_labels_from_index(before.inventory_ids, self._scene_index)
        trace.append({"event": "manipulation_precondition_passed", "action": action})

        if action == "take":
            return self._take(
                context,
                target,
                before,
                object_label,
                inventory_labels,
                trace,
            )
        if action in {"open", "close"}:
            return self._open_close(
                action,
                context,
                target,
                before,
                target_label or object_label,
                inventory_labels,
                trace,
            )
        if action == "put":
            return self._put(
                context,
                target,
                before,
                object_label,
                target_label,
                inventory_labels,
                trace,
            )
        if action == "use":
            return self._use(
                context,
                target,
                before,
                object_label,
                inventory_labels,
                trace,
            )
        if action == "slice":
            # Gate A did not verify whether this runtime preserves or replaces the exact ID.
            return self._zero_failure(
                action,
                "unclassified_execution_failure",
                object_label,
                target_label,
                trace,
            )
        return self._macro(
            action,
            context,
            target,
            before,
            object_label,
            target_label,
            inventory_labels,
            trace,
        )

    def _take(
        self,
        context: OracleExecutionContext,
        target: ThorObjectView,
        before: ThorStateSnapshot,
        object_label: str | None,
        inventory: tuple[str, ...],
        trace: list[dict[str, Any]],
    ) -> OracleManipulationResult:
        if target.is_picked_up or target.exact_object_id in before.inventory_ids:
            return self._zero_failure(
                "take",
                "object_already_held",
                object_label,
                None,
                trace,
                inventory=inventory,
                object_state="held",
            )
        if target.pickupable is not True:
            return self._zero_failure(
                "take", "action_not_applicable", object_label, None, trace, inventory=inventory
            )
        return self._send_single(
            action="take",
            payload={
                "action": "PickupObject",
                "objectId": target.exact_object_id,
                "forceAction": True,
            },
            context=context,
            before=before,
            object_label=object_label,
            target_label=None,
            terminal=lambda after: (
                target.exact_object_id in after.inventory_ids
                and (after.get(target.exact_object_id) or target).is_picked_up
            ),
            object_state="held",
            context_state="consumed",
            trace=trace,
        )

    def _open_close(
        self,
        action: Literal["open", "close"],
        context: OracleExecutionContext,
        target: ThorObjectView,
        before: ThorStateSnapshot,
        target_label: str | None,
        inventory: tuple[str, ...],
        trace: list[dict[str, Any]],
    ) -> OracleManipulationResult:
        if target.openable is not True or target.is_open is None:
            return self._zero_failure(
                action,
                "action_not_applicable",
                None,
                target_label,
                trace,
                inventory=inventory,
            )
        want_open = action == "open"
        state = "open" if want_open else "closed"
        if target.is_open is want_open:
            feedback = make_execution_feedback(
                action=action,
                success=True,
                target_label=target_label,
                inventory=inventory,
                inventory_status="ok",
                target_state=state,
                target_state_status="ok",
                state_changed=False,
                state_read_status="ok",
            )
            return OracleManipulationResult(feedback, context, 0, tuple(trace))
        backend_action = "OpenObject" if want_open else "CloseObject"
        return self._send_single(
            action=action,
            payload={
                "action": backend_action,
                "objectId": target.exact_object_id,
                "forceAction": True,
            },
            context=context,
            before=before,
            object_label=None,
            target_label=target_label,
            terminal=lambda after: (
                (after.get(target.exact_object_id) is not None)
                and (after.get(target.exact_object_id).is_open is want_open)
            ),
            target_state=state,
            context_state="active",
            trace=trace,
        )

    def _put(
        self,
        context: OracleExecutionContext,
        target: ThorObjectView,
        before: ThorStateSnapshot,
        object_label: str | None,
        target_label: str | None,
        inventory: tuple[str, ...],
        trace: list[dict[str, Any]],
    ) -> OracleManipulationResult:
        object_ref = self._scene_index.resolve(object_label or "")
        if object_ref is None:
            return self._zero_failure(
                "put", "target_not_found", object_label, target_label, trace, inventory=inventory
            )
        held = before.get(object_ref.object_id)
        if held is None:
            return self._uncertain("put", object_label, target_label, trace)
        if object_ref.object_id not in before.inventory_ids or not held.is_picked_up:
            return self._zero_failure(
                "put",
                "object_not_held",
                object_label,
                target_label,
                trace,
                inventory=inventory,
                object_state="not_held",
            )
        if target.receptacle is not True:
            return self._zero_failure(
                "put",
                "target_not_receptacle",
                object_label,
                target_label,
                trace,
                inventory=inventory,
                object_state="held",
            )
        if target.openable is True and target.is_open is False:
            return self._zero_failure(
                "put",
                "target_closed",
                object_label,
                target_label,
                trace,
                inventory=inventory,
                object_state="held",
                target_state="closed",
            )
        return self._send_single(
            action="put",
            payload={
                "action": "PutObject",
                "objectId": object_ref.object_id,
                "receptacleObjectId": target.exact_object_id,
                "forceAction": True,
                "placeStationary": True,
            },
            context=context,
            before=before,
            object_label=object_label,
            target_label=target_label,
            terminal=lambda after: _put_terminal(
                after,
                object_id=object_ref.object_id,
                target_id=target.exact_object_id,
            ),
            object_state="placed",
            context_state="consumed",
            trace=trace,
        )

    def _use(
        self,
        context: OracleExecutionContext,
        target: ThorObjectView,
        before: ThorStateSnapshot,
        object_label: str | None,
        inventory: tuple[str, ...],
        trace: list[dict[str, Any]],
    ) -> OracleManipulationResult:
        if target.toggleable is not True or target.is_toggled is None:
            return self._zero_failure(
                "use",
                "action_not_applicable",
                object_label,
                None,
                trace,
                inventory=inventory,
            )
        if target.is_toggled:
            feedback = make_execution_feedback(
                action="use",
                success=True,
                object_label=object_label,
                inventory=inventory,
                inventory_status="ok",
                target_state="toggled_on",
                target_state_status="ok",
                state_changed=False,
                state_read_status="ok",
            )
            return OracleManipulationResult(feedback, context, 0, tuple(trace))
        return self._send_single(
            action="use",
            payload={
                "action": "ToggleObjectOn",
                "objectId": target.exact_object_id,
                "forceAction": True,
            },
            context=context,
            before=before,
            object_label=object_label,
            target_label=None,
            terminal=lambda after: (
                (after.get(target.exact_object_id) is not None)
                and after.get(target.exact_object_id).is_toggled is True
            ),
            target_state="toggled_on",
            context_state="active",
            trace=trace,
        )

    def _macro(
        self,
        action: Literal["heat", "cool", "clean"],
        context: OracleExecutionContext,
        target: ThorObjectView,
        before: ThorStateSnapshot,
        object_label: str | None,
        target_label: str | None,
        inventory: tuple[str, ...],
        trace: list[dict[str, Any]],
    ) -> OracleManipulationResult:
        object_ref = self._scene_index.resolve(object_label or "")
        if object_ref is None or object_ref.object_id not in before.inventory_ids:
            return self._zero_failure(
                action,
                "object_not_held",
                object_label,
                target_label,
                trace,
                inventory=inventory,
                object_state="not_held",
            )
        plan = _macro_plan(
            action,
            object_id=object_ref.object_id,
            target_id=target.exact_object_id,
            before=before,
        )
        if plan is None:
            return self._zero_failure(
                action,
                "unclassified_execution_failure",
                object_label,
                target_label,
                trace,
            )
        current = before
        final_event: ExternalEventRead | None = None
        count = 0
        for payload, predicate in plan:
            result = self._gateway.execute_manipulation(payload)
            count += 1
            after = read_thor_state_snapshot(self._raw_event_reader())
            if after.status != "ok" or result.event.pose != context.actual_pose:
                return self._uncertain(
                    action, object_label, target_label, trace, backend_action_count=count
                )
            if not result.success:
                error = (
                    "harness_operation_failure"
                    if after.state_sha256 == current.state_sha256
                    else "execution_state_uncertain"
                )
                return self._failure(
                    action,
                    error,
                    object_label,
                    target_label,
                    after,
                    trace,
                    backend_action_count=count,
                )
            if not predicate(after):
                return self._uncertain(
                    action, object_label, target_label, trace, backend_action_count=count
                )
            current = after
            final_event = result.event
        final_object = current.get(object_ref.object_id)
        if final_event is None or final_object is None or not _macro_terminal(action, final_object):
            return self._uncertain(
                action, object_label, target_label, trace, backend_action_count=count
            )
        object_state = {"heat": "heated", "cool": "cooled", "clean": "clean"}[action]
        feedback = make_execution_feedback(
            action=action,
            success=True,
            object_label=object_label,
            target_label=target_label,
            inventory=_inventory_labels_from_index(current.inventory_ids, self._scene_index),
            inventory_status="ok",
            object_state=object_state,
            object_state_status="ok",
            state_changed=True,
            state_read_status="ok",
        )
        return OracleManipulationResult(
            feedback,
            _rebase_context(context, final_event, state="consumed", action_count=count),
            count,
            tuple(trace),
        )

    def _send_single(
        self,
        *,
        action: AlfworldAction,
        payload: dict[str, Any],
        context: OracleExecutionContext,
        before: ThorStateSnapshot,
        object_label: str | None,
        target_label: str | None,
        terminal: Callable[[ThorStateSnapshot], bool],
        context_state: ExecutionContextState,
        trace: list[dict[str, Any]],
        object_state: str | None = None,
        target_state: str | None = None,
    ) -> OracleManipulationResult:
        result = self._gateway.execute_manipulation(payload)
        after = read_thor_state_snapshot(self._raw_event_reader())
        trace.append(
            {"event": "manipulation_gateway_result", "success": result.success}
        )
        if after.status != "ok" or result.event.pose != context.actual_pose:
            return self._uncertain(
                action, object_label, target_label, trace, backend_action_count=1
            )
        if not result.success:
            error = (
                "harness_operation_failure"
                if after.state_sha256 == before.state_sha256
                else "execution_state_uncertain"
            )
            return self._failure(
                action,
                error,
                object_label,
                target_label,
                after,
                trace,
                backend_action_count=1,
            )
        if not terminal(after):
            return self._uncertain(
                action, object_label, target_label, trace, backend_action_count=1
            )
        feedback = make_execution_feedback(
            action=action,
            success=True,
            object_label=object_label,
            target_label=target_label,
            inventory=_inventory_labels_from_index(after.inventory_ids, self._scene_index),
            inventory_status="ok",
            object_state=object_state,
            object_state_status="ok" if object_state is not None else "not_applicable",
            target_state=target_state,
            target_state_status="ok" if target_state is not None else "not_applicable",
            state_changed=True,
            state_read_status="ok",
        )
        return OracleManipulationResult(
            feedback,
            _rebase_context(context, result.event, state=context_state, action_count=1),
            1,
            tuple(trace),
        )

    def _zero_failure(
        self,
        action: AlfworldAction,
        error: str,
        object_label: str | None,
        target_label: str | None,
        trace: list[dict[str, Any]],
        *,
        inventory: tuple[str, ...] | None = None,
        object_state: str | None = None,
        target_state: str | None = None,
    ) -> OracleManipulationResult:
        feedback = make_execution_feedback(
            action=action,
            success=False,
            error=error,
            object_label=object_label,
            target_label=target_label,
            inventory=inventory,
            inventory_status="ok" if inventory is not None else "not_applicable",
            object_state=object_state,
            object_state_status="ok" if object_state is not None else "not_applicable",
            target_state=target_state,
            target_state_status="ok" if target_state is not None else "not_applicable",
            state_changed=False,
            state_read_status="ok",
        )
        return OracleManipulationResult(feedback, self._context, 0, tuple(trace))

    def _failure(
        self,
        action: AlfworldAction,
        error: str,
        object_label: str | None,
        target_label: str | None,
        after: ThorStateSnapshot,
        trace: list[dict[str, Any]],
        *,
        backend_action_count: int,
    ) -> OracleManipulationResult:
        feedback = make_execution_feedback(
            action=action,
            success=False,
            error=error,
            object_label=object_label,
            target_label=target_label,
            inventory=_inventory_labels_from_index(after.inventory_ids, self._scene_index),
            inventory_status="ok",
            state_changed=False,
            state_read_status="ok",
        )
        context = self._context
        if error == "execution_state_uncertain" and context is not None:
            context = replace(context, state="invalid")
        return OracleManipulationResult(
            feedback, context, backend_action_count, tuple(trace)
        )

    def _uncertain(
        self,
        action: AlfworldAction,
        object_label: str | None,
        target_label: str | None,
        trace: list[dict[str, Any]],
        *,
        backend_action_count: int = 0,
    ) -> OracleManipulationResult:
        return self._failure(
            action,
            "execution_state_uncertain",
            object_label,
            target_label,
            ThorStateSnapshot("error", (), (), None),
            trace,
            backend_action_count=backend_action_count,
        )


def read_thor_state_snapshot(event: Any) -> ThorStateSnapshot:
    try:
        metadata = getattr(event, "metadata", None)
        if not isinstance(metadata, dict) or not isinstance(metadata.get("objects"), list):
            return ThorStateSnapshot("malformed", (), (), None)
        inventory_raw = metadata.get("inventoryObjects")
        if not isinstance(inventory_raw, list):
            return ThorStateSnapshot("malformed", (), (), None)
        inventory = tuple(
            sorted(
                str(item["objectId"])
                for item in inventory_raw
                if isinstance(item, dict) and isinstance(item.get("objectId"), str)
            )
        )
        if len(inventory) != len(inventory_raw):
            return ThorStateSnapshot("malformed", (), (), None)
        objects: list[ThorObjectView] = []
        for raw in metadata["objects"]:
            if not isinstance(raw, dict):
                return ThorStateSnapshot("malformed", (), (), None)
            object_id = raw.get("objectId")
            object_type = raw.get("objectType")
            visible = raw.get("visible")
            is_picked_up = raw.get("isPickedUp")
            if (
                not isinstance(object_id, str)
                or not isinstance(object_type, str)
                or not isinstance(visible, bool)
                or not isinstance(is_picked_up, bool)
            ):
                return ThorStateSnapshot("malformed", (), (), None)
            objects.append(
                ThorObjectView(
                    exact_object_id=object_id,
                    object_type=object_type,
                    visible=visible,
                    is_picked_up=is_picked_up,
                    parent_ids=_optional_string_tuple(raw.get("parentReceptacles")),
                    child_ids=_optional_string_tuple(raw.get("receptacleObjectIds")),
                    pickupable=_optional_bool(raw.get("pickupable")),
                    receptacle=_optional_bool(raw.get("receptacle")),
                    openable=_optional_bool(raw.get("openable")),
                    is_open=_optional_bool(raw.get("isOpen")),
                    toggleable=_optional_bool(raw.get("toggleable")),
                    is_toggled=_optional_bool(raw.get("isToggled")),
                    sliceable=_optional_bool(raw.get("sliceable")),
                    is_sliced=_optional_bool(raw.get("isSliced")),
                    is_dirty=_optional_bool(raw.get("isDirty")),
                    temperature=(
                        str(raw["ObjectTemperature"])
                        if isinstance(raw.get("ObjectTemperature"), str)
                        else None
                    ),
                )
            )
        canonical = [
            asdict(item)
            for item in sorted(objects, key=lambda item: item.exact_object_id)
        ]
        state_sha256 = hashlib.sha256(
            json.dumps(
                {"inventory": inventory, "objects": canonical},
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode()
        ).hexdigest()
        return ThorStateSnapshot("ok", inventory, tuple(objects), state_sha256)
    except (KeyError, TypeError, ValueError):
        return ThorStateSnapshot("malformed", (), (), None)


def _optional_string_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list | tuple) or any(not isinstance(item, str) for item in value):
        raise ValueError("malformed object membership")
    return tuple(sorted(set(value)))


def _optional_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _label_for_exact_id(index: SceneObjectIndex, exact_object_id: str) -> str | None:
    for label, item in index.by_canonical_label.items():
        if item.object_id == exact_object_id:
            return label
    return None


def _label_matches_ref(label: str, ref: SceneObjectRef, index: SceneObjectIndex) -> bool:
    canonical = _canonical_query(label)
    if _EXPLICIT_INSTANCE.fullmatch(canonical):
        resolved = index.resolve(canonical)
        return resolved is not None and resolved.object_id == ref.object_id
    return _object_type_key(canonical) == _object_type_key(ref.object_type)


def _manipulation_requested_target(
    action: AlfworldAction,
    *,
    object_label: str | None,
    target_label: str | None,
) -> str | None:
    if action in {"open", "close", "put", "heat", "cool", "clean"}:
        return target_label or object_label
    return object_label or target_label


def _inventory_labels_from_index(
    inventory_ids: tuple[str, ...],
    index: SceneObjectIndex,
) -> tuple[str, ...]:
    labels = []
    for object_id in inventory_ids:
        label = _label_for_exact_id(index, object_id)
        if label is not None:
            labels.append(label)
    return tuple(sorted(labels))


def _put_terminal(
    state: ThorStateSnapshot,
    *,
    object_id: str,
    target_id: str,
) -> bool:
    obj = state.get(object_id)
    target = state.get(target_id)
    return (
        obj is not None
        and target is not None
        and object_id not in state.inventory_ids
        and not obj.is_picked_up
        and target_id in obj.parent_ids
        and object_id in target.child_ids
    )


def _rebase_context(
    context: OracleExecutionContext,
    event: ExternalEventRead,
    *,
    state: ExecutionContextState,
    action_count: int,
) -> OracleExecutionContext:
    if event.pose is None or event.raw_event_sha256 is None or event.raw_event_ref is None:
        return replace(context, state="invalid")
    return replace(
        context,
        current_event_sequence=context.current_event_sequence + action_count,
        actual_pose=event.pose,
        final_event_hash=event.raw_event_sha256,
        final_event_ref=event.raw_event_ref,
        state=state,
    )


def _macro_plan(
    action: Literal["heat", "cool", "clean"],
    *,
    object_id: str,
    target_id: str,
    before: ThorStateSnapshot,
) -> tuple[tuple[dict[str, Any], Callable[[ThorStateSnapshot], bool]], ...] | None:
    def open_target(state: ThorStateSnapshot) -> bool:
        target = state.get(target_id)
        return target is not None and target.is_open is True

    def close_target(state: ThorStateSnapshot) -> bool:
        target = state.get(target_id)
        return target is not None and target.is_open is False

    def put_target(state: ThorStateSnapshot) -> bool:
        return _put_terminal(state, object_id=object_id, target_id=target_id)

    def pickup(state: ThorStateSnapshot) -> bool:
        obj = state.get(object_id)
        return object_id in state.inventory_ids and obj is not None and obj.is_picked_up
    if action == "heat":
        return (
            ({"action": "OpenObject", "objectId": target_id, "forceAction": True}, open_target),
            (
                {
                    "action": "PutObject",
                    "objectId": object_id,
                    "receptacleObjectId": target_id,
                    "forceAction": True,
                    "placeStationary": True,
                },
                put_target,
            ),
            ({"action": "CloseObject", "objectId": target_id, "forceAction": True}, close_target),
            (
                {"action": "ToggleObjectOn", "objectId": target_id, "forceAction": True},
                lambda state: state.get(target_id) is not None
                and state.get(target_id).is_toggled is True,
            ),
            (
                {"action": "ToggleObjectOff", "objectId": target_id, "forceAction": True},
                lambda state: state.get(target_id) is not None
                and state.get(target_id).is_toggled is False,
            ),
            ({"action": "OpenObject", "objectId": target_id, "forceAction": True}, open_target),
            ({"action": "PickupObject", "objectId": object_id, "forceAction": True}, pickup),
            ({"action": "CloseObject", "objectId": target_id, "forceAction": True}, close_target),
        )
    if action == "cool":
        return (
            ({"action": "OpenObject", "objectId": target_id, "forceAction": True}, open_target),
            (
                {
                    "action": "PutObject",
                    "objectId": object_id,
                    "receptacleObjectId": target_id,
                    "forceAction": True,
                    "placeStationary": True,
                },
                put_target,
            ),
            ({"action": "CloseObject", "objectId": target_id, "forceAction": True}, close_target),
            ({"action": "OpenObject", "objectId": target_id, "forceAction": True}, open_target),
            ({"action": "PickupObject", "objectId": object_id, "forceAction": True}, pickup),
            ({"action": "CloseObject", "objectId": target_id, "forceAction": True}, close_target),
        )
    faucets = [item for item in before.objects if _object_type_key(item.object_type) == "faucet"]
    if len(faucets) != 1:
        return None
    faucet_id = faucets[0].exact_object_id
    return (
        (
            {
                "action": "PutObject",
                "objectId": object_id,
                "receptacleObjectId": target_id,
                "forceAction": True,
                "placeStationary": True,
            },
            put_target,
        ),
        (
            {"action": "ToggleObjectOn", "objectId": faucet_id, "forceAction": True},
            lambda state: state.get(faucet_id) is not None
            and state.get(faucet_id).is_toggled is True,
        ),
        (
            {"action": "ToggleObjectOff", "objectId": faucet_id, "forceAction": True},
            lambda state: state.get(faucet_id) is not None
            and state.get(faucet_id).is_toggled is False,
        ),
        ({"action": "PickupObject", "objectId": object_id, "forceAction": True}, pickup),
    )


def _macro_terminal(
    action: Literal["heat", "cool", "clean"],
    obj: ThorObjectView,
) -> bool:
    if action == "heat":
        return obj.temperature == "Hot"
    if action == "cool":
        return obj.temperature == "Cold"
    return obj.is_dirty is False


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
