"""Pure canonical scan-plan and immutable oracle pose snapshot contracts."""

from __future__ import annotations

import hashlib
import json
import math
import threading
from dataclasses import asdict, dataclass
from importlib import resources
from typing import Any, Literal, Protocol

OracleReadStatus = Literal[
    "ok",
    "unobserved",
    "coverage_miss",
    "relocated",
    "absent",
    "malformed",
    "stale",
    "error",
]
ScanPoseSource = Literal["cache", "geometry"]
SnapshotPoseSource = Literal["cache", "geometry", "incidental", "none"]
AddressabilityReason = Literal[
    "public_semantic",
    "not_public_semantic",
    "inventory",
    "closed_ancestor",
]

_PUBLISHED_STATUSES = {"ok", "coverage_miss", "unobserved"}
_OVERLAY_STATUSES = {"relocated", "malformed", "stale", "error"}
_POSE_SOURCES = {"cache", "geometry", "incidental", "none"}
_ADDRESSABILITY_REASONS = {
    "public_semantic",
    "not_public_semantic",
    "inventory",
    "closed_ancestor",
}


@dataclass(frozen=True, order=True)
class OraclePose:
    x: float
    y: float
    z: float
    rotation: float
    horizon: float

    def __post_init__(self) -> None:
        for name in ("x", "y", "z", "rotation", "horizon"):
            value = float(getattr(self, name))
            if not math.isfinite(value):
                raise ValueError("oracle pose values must be finite")
            object.__setattr__(self, name, _normalize_zero(value))


def oracle_pose_matches(actual: OraclePose | None, expected: OraclePose | None) -> bool:
    """Compare poses using the precision preserved by the pinned THOR build."""

    if actual is None or expected is None:
        return False
    rotation_delta = abs((actual.rotation - expected.rotation) % 360.0)
    rotation_delta = min(rotation_delta, 360.0 - rotation_delta)
    return (
        abs(actual.x - expected.x) <= 1e-4
        and abs(actual.y - expected.y) <= 1e-4
        and abs(actual.z - expected.z) <= 1e-4
        and rotation_delta <= 1e-4
        and abs(actual.horizon - expected.horizon) <= 1.0
    )


@dataclass(frozen=True, order=True)
class ScanPoseProvenance:
    exact_object_id: str
    source_kind: ScanPoseSource
    source_record_sha256: str

    def __post_init__(self) -> None:
        _require_nonempty("exact_object_id", self.exact_object_id)
        if self.source_kind not in {"cache", "geometry"}:
            raise ValueError(f"unsupported scan pose source: {self.source_kind}")
        _validate_sha256("source_record_sha256", self.source_record_sha256)


@dataclass(frozen=True)
class ScanPoseStep:
    index: int
    pose: OraclePose
    send_teleport: bool
    provenances: tuple[ScanPoseProvenance, ...]

    def __post_init__(self) -> None:
        if self.index < 0:
            raise ValueError("scan step index cannot be negative")
        canonical = tuple(sorted(set(self.provenances)))
        if canonical != self.provenances:
            raise ValueError("scan step provenances must be sorted and unique")


@dataclass(frozen=True)
class SceneScanPlan:
    scene_generation: int
    scene_reset_fingerprint: str
    algorithm_version: str
    scan_policy_sha256: str
    reachable_payload_sha256: str
    reachable_canonical_sha256: str
    steps: tuple[ScanPoseStep, ...]
    canonical_sha256: str

    def __post_init__(self) -> None:
        _validate_generation(self.scene_generation)
        for name in (
            "scene_reset_fingerprint",
            "scan_policy_sha256",
            "reachable_payload_sha256",
            "reachable_canonical_sha256",
            "canonical_sha256",
        ):
            _validate_sha256(name, getattr(self, name))
        _require_nonempty("algorithm_version", self.algorithm_version)
        if not self.steps:
            raise ValueError("scene scan plan has no initial step")
        if [step.index for step in self.steps] != list(range(len(self.steps))):
            raise ValueError("scene scan plan step indices are not contiguous")
        if self.steps[0].send_teleport:
            raise ValueError("scene scan plan initial step must not send Teleport")
        if any(not step.send_teleport for step in self.steps[1:]):
            raise ValueError("every non-initial scan step must send Teleport")
        if len({step.pose for step in self.steps}) != len(self.steps):
            raise ValueError("scene scan plan contains duplicate poses")
        expected = _plan_sha256(
            scene_generation=self.scene_generation,
            scene_reset_fingerprint=self.scene_reset_fingerprint,
            algorithm_version=self.algorithm_version,
            scan_policy_sha256=self.scan_policy_sha256,
            reachable_canonical_sha256=self.reachable_canonical_sha256,
            steps=self.steps,
        )
        if expected != self.canonical_sha256:
            raise ValueError("scene scan plan canonical hash mismatch")


@dataclass(frozen=True)
class SceneObjectScanInput:
    exact_object_id: str
    object_type: str
    position: tuple[float, float, float]
    parent_receptacle_ids: tuple[str, ...]
    receptacle_object_ids: tuple[str, ...]
    is_picked_up: bool
    closed_ancestor_exact_ids: tuple[str, ...]
    pose_freshness_sha256: str

    def __post_init__(self) -> None:
        _require_nonempty("exact_object_id", self.exact_object_id)
        _require_nonempty("object_type", self.object_type)
        if len(self.position) != 3 or not all(
            math.isfinite(float(value)) for value in self.position
        ):
            raise ValueError("object position must contain three finite values")
        object.__setattr__(
            self,
            "position",
            tuple(_normalize_zero(float(value)) for value in self.position),
        )
        for name in (
            "parent_receptacle_ids",
            "receptacle_object_ids",
            "closed_ancestor_exact_ids",
        ):
            values = tuple(sorted(set(getattr(self, name))))
            if values != getattr(self, name):
                object.__setattr__(self, name, values)
        _validate_sha256("pose_freshness_sha256", self.pose_freshness_sha256)


@dataclass(frozen=True)
class CachePoseInput:
    exact_object_id: str
    pose: OraclePose
    source_record_sha256: str

    def __post_init__(self) -> None:
        _require_nonempty("exact_object_id", self.exact_object_id)
        _validate_sha256("source_record_sha256", self.source_record_sha256)


@dataclass(frozen=True)
class ScanPolicyInput:
    scene_generation: int
    scene_reset_fingerprint: str
    algorithm_version: str
    geometry_policy_version: str
    setup_time_control_version: str
    setup_slow_time_scale: float
    setup_restore_time_scale: float
    successful_setup_action_offset: int
    initial_pose: OraclePose
    public_semantic_vocabulary: tuple[str, ...]
    objects: tuple[SceneObjectScanInput, ...]
    cache_entries: tuple[CachePoseInput, ...]
    reachable_payload: bytes

    def __post_init__(self) -> None:
        _validate_generation(self.scene_generation)
        _validate_sha256("scene_reset_fingerprint", self.scene_reset_fingerprint)
        for name in ("algorithm_version", "geometry_policy_version", "setup_time_control_version"):
            _require_nonempty(name, getattr(self, name))
        if self.setup_slow_time_scale != 0.01:
            raise ValueError("V1.8 setup slow time scale must be exactly 0.01")
        if self.setup_restore_time_scale != 1.0:
            raise ValueError("V1.8 setup restore time scale must be exactly 1.0")
        if self.successful_setup_action_offset != 4:
            raise ValueError("V1.8 successful setup action offset must be exactly 4")
        vocabulary = tuple(sorted(set(self.public_semantic_vocabulary)))
        if vocabulary != self.public_semantic_vocabulary or not vocabulary:
            raise ValueError("public semantic vocabulary must be non-empty, sorted, and unique")
        if not isinstance(self.reachable_payload, bytes):
            raise ValueError("reachable_payload must contain the exact payload bytes")


@dataclass(frozen=True)
class ObjectAddressability:
    exact_object_id: str
    addressable: bool
    reason: AddressabilityReason


class SceneScanPlanBuilder:
    def build(self, policy: ScanPolicyInput) -> SceneScanPlan:
        objects = _validated_object_map(policy.objects)
        cache = _validated_cache_map(policy.cache_entries, objects)
        reachable = _parse_reachable_positions(policy.reachable_payload)
        reachable_canonical_sha256 = _sha256_json(reachable)
        addressability = _addressability(objects, set(policy.public_semantic_vocabulary))
        scan_policy_sha256 = _scan_policy_sha256(policy, objects, cache, addressability)

        pose_provenances: dict[OraclePose, set[ScanPoseProvenance]] = {
            policy.initial_pose: set()
        }
        for exact_object_id in sorted(objects):
            if not addressability[exact_object_id].addressable:
                continue
            cached = cache.get(exact_object_id)
            if cached is not None:
                pose = cached.pose
                provenance = ScanPoseProvenance(
                    exact_object_id=exact_object_id,
                    source_kind="cache",
                    source_record_sha256=cached.source_record_sha256,
                )
            else:
                if not reachable:
                    raise ValueError("scan plan missing reachable geometry input")
                pose, source_hash = _geometry_pose(
                    objects[exact_object_id],
                    reachable,
                    agent_y=policy.initial_pose.y,
                    geometry_policy_version=policy.geometry_policy_version,
                )
                provenance = ScanPoseProvenance(
                    exact_object_id=exact_object_id,
                    source_kind="geometry",
                    source_record_sha256=source_hash,
                )
            pose_provenances.setdefault(pose, set()).add(provenance)

        ordered_poses = [policy.initial_pose]
        ordered_poses.extend(
            sorted(pose for pose in pose_provenances if pose != policy.initial_pose)
        )
        steps = tuple(
            ScanPoseStep(
                index=index,
                pose=pose,
                send_teleport=index != 0,
                provenances=tuple(sorted(pose_provenances[pose])),
            )
            for index, pose in enumerate(ordered_poses)
        )
        plan_hash = _plan_sha256(
            scene_generation=policy.scene_generation,
            scene_reset_fingerprint=policy.scene_reset_fingerprint,
            algorithm_version=policy.algorithm_version,
            scan_policy_sha256=scan_policy_sha256,
            reachable_canonical_sha256=reachable_canonical_sha256,
            steps=steps,
        )
        return SceneScanPlan(
            scene_generation=policy.scene_generation,
            scene_reset_fingerprint=policy.scene_reset_fingerprint,
            algorithm_version=policy.algorithm_version,
            scan_policy_sha256=scan_policy_sha256,
            reachable_payload_sha256=hashlib.sha256(policy.reachable_payload).hexdigest(),
            reachable_canonical_sha256=reachable_canonical_sha256,
            steps=steps,
            canonical_sha256=plan_hash,
        )


@dataclass(frozen=True)
class OraclePoseSnapshotEntry:
    exact_object_id: str
    status: OracleReadStatus
    addressable: bool
    addressability_reason: AddressabilityReason
    pose: OraclePose | None
    pose_sha256: str | None
    pose_freshness_sha256: str
    source_kind: SnapshotPoseSource
    evidence_ref: str | None

    def __post_init__(self) -> None:
        _require_nonempty("exact_object_id", self.exact_object_id)
        if self.status not in _PUBLISHED_STATUSES:
            raise ValueError(f"status is not valid in a published snapshot row: {self.status}")
        if self.addressability_reason not in _ADDRESSABILITY_REASONS:
            raise ValueError(f"unsupported addressability reason: {self.addressability_reason}")
        if self.source_kind not in _POSE_SOURCES:
            raise ValueError(f"unsupported snapshot pose source: {self.source_kind}")
        _validate_sha256("pose_freshness_sha256", self.pose_freshness_sha256)
        if self.status == "ok":
            if not self.addressable or self.pose is None or self.pose_sha256 is None:
                raise ValueError("ok snapshot row requires an addressable pose")
            if self.source_kind == "none":
                raise ValueError("ok snapshot row requires a concrete pose source")
            if pose_sha256(self.pose) != self.pose_sha256:
                raise ValueError("snapshot row pose hash mismatch")
        elif self.status == "coverage_miss":
            if not self.addressable:
                raise ValueError("coverage_miss snapshot row must be addressable")
            _require_empty_pose(self)
        else:
            if self.addressable:
                raise ValueError("unobserved snapshot row must be non-addressable")
            _require_empty_pose(self)


@dataclass(frozen=True)
class OraclePoseSnapshot:
    scene_generation: int
    scene_reset_fingerprint: str
    algorithm_version: str
    scan_policy_sha256: str
    reachable_payload_sha256: str
    reachable_canonical_sha256: str
    scan_plan_sha256: str
    initial_event_ref: str
    restored_event_ref: str
    initial_world_sha256: str
    restored_world_sha256: str
    entries: tuple[OraclePoseSnapshotEntry, ...]
    snapshot_sha256: str

    def __post_init__(self) -> None:
        _validate_generation(self.scene_generation)
        for name in (
            "scene_reset_fingerprint",
            "scan_policy_sha256",
            "reachable_payload_sha256",
            "reachable_canonical_sha256",
            "scan_plan_sha256",
            "initial_world_sha256",
            "restored_world_sha256",
            "snapshot_sha256",
        ):
            _validate_sha256(name, getattr(self, name))
        for name in ("algorithm_version", "initial_event_ref", "restored_event_ref"):
            _require_nonempty(name, getattr(self, name))
        exact_ids = [entry.exact_object_id for entry in self.entries]
        if exact_ids != sorted(exact_ids) or len(exact_ids) != len(set(exact_ids)):
            raise ValueError("snapshot rows must be sorted with one row per exact ID")
        if self.initial_world_sha256 != self.restored_world_sha256:
            raise ValueError("snapshot cannot publish after world-state drift")
        if _snapshot_sha256(self) != self.snapshot_sha256:
            raise ValueError("snapshot canonical hash mismatch")

    @classmethod
    def create(
        cls,
        *,
        scene_generation: int,
        scene_reset_fingerprint: str,
        algorithm_version: str,
        scan_policy_sha256: str,
        reachable_payload_sha256: str,
        reachable_canonical_sha256: str,
        scan_plan_sha256: str,
        initial_event_ref: str,
        restored_event_ref: str,
        initial_world_sha256: str,
        restored_world_sha256: str,
        entries: tuple[OraclePoseSnapshotEntry, ...],
    ) -> OraclePoseSnapshot:
        values: dict[str, Any] = {
            "scene_generation": scene_generation,
            "scene_reset_fingerprint": scene_reset_fingerprint,
            "algorithm_version": algorithm_version,
            "scan_policy_sha256": scan_policy_sha256,
            "reachable_payload_sha256": reachable_payload_sha256,
            "reachable_canonical_sha256": reachable_canonical_sha256,
            "scan_plan_sha256": scan_plan_sha256,
            "initial_event_ref": initial_event_ref,
            "restored_event_ref": restored_event_ref,
            "initial_world_sha256": initial_world_sha256,
            "restored_world_sha256": restored_world_sha256,
            "entries": tuple(sorted(entries, key=lambda entry: entry.exact_object_id)),
        }
        provisional = object.__new__(cls)
        for name, value in values.items():
            object.__setattr__(provisional, name, value)
        object.__setattr__(provisional, "snapshot_sha256", "")
        values["snapshot_sha256"] = _snapshot_sha256(provisional)
        return cls(**values)


@dataclass(frozen=True)
class OraclePoseLookup:
    status: OracleReadStatus
    scene_generation: int
    scene_reset_fingerprint: str
    snapshot_sha256: str
    pose: OraclePose | None
    pose_sha256: str | None
    pose_freshness_sha256: str | None
    source_kind: SnapshotPoseSource
    evidence_ref: str | None


class OraclePoseStore(Protocol):
    def get_pose(
        self,
        *,
        scene_generation: int,
        scene_reset_fingerprint: str,
        exact_anchor_id: str,
    ) -> OraclePoseLookup: ...


class FrozenOraclePoseStore:
    """Atomically publishes one immutable snapshot and explicit runtime overlays."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._snapshot: OraclePoseSnapshot | None = None
        self._overlays: dict[str, tuple[OracleReadStatus, str | None]] = {}

    def publish(self, snapshot: OraclePoseSnapshot) -> None:
        with self._lock:
            self._snapshot = snapshot
            self._overlays = {}

    def set_overlay(
        self,
        exact_object_id: str,
        *,
        status: OracleReadStatus,
        evidence_ref: str | None = None,
    ) -> None:
        if status not in _OVERLAY_STATUSES:
            raise ValueError(f"unsupported runtime pose overlay: {status}")
        _require_nonempty("exact_object_id", exact_object_id)
        with self._lock:
            self._overlays = {**self._overlays, exact_object_id: (status, evidence_ref)}

    def get_pose(
        self,
        *,
        scene_generation: int,
        scene_reset_fingerprint: str,
        exact_anchor_id: str,
    ) -> OraclePoseLookup:
        with self._lock:
            snapshot = self._snapshot
            overlays = self._overlays
        if snapshot is None:
            raise RuntimeError("oracle pose snapshot has not been published")
        entries = {entry.exact_object_id: entry for entry in snapshot.entries}
        entry = entries.get(exact_anchor_id)
        if (
            scene_generation != snapshot.scene_generation
            or scene_reset_fingerprint != snapshot.scene_reset_fingerprint
        ):
            return _empty_lookup(snapshot, "stale", entry, None)
        overlay = overlays.get(exact_anchor_id)
        if overlay is not None:
            return _empty_lookup(snapshot, overlay[0], entry, overlay[1])
        if entry is None:
            return _empty_lookup(snapshot, "absent", None, None)
        return OraclePoseLookup(
            status=entry.status,
            scene_generation=snapshot.scene_generation,
            scene_reset_fingerprint=snapshot.scene_reset_fingerprint,
            snapshot_sha256=snapshot.snapshot_sha256,
            pose=entry.pose,
            pose_sha256=entry.pose_sha256,
            pose_freshness_sha256=entry.pose_freshness_sha256,
            source_kind=entry.source_kind,
            evidence_ref=entry.evidence_ref,
        )


@dataclass(frozen=True)
class PublicObjectVocabulary:
    schema_version: str
    object_types: tuple[str, ...]
    canonical_sha256: str


def load_public_object_vocabulary() -> PublicObjectVocabulary:
    payload = json.loads(
        resources.files("homemaster.benchmarking.alfworld")
        .joinpath("object_vocabulary.json")
        .read_text(encoding="utf-8")
    )
    if set(payload) != {"schema_version", "object_types", "canonical_sha256"}:
        raise ValueError("public object vocabulary has an invalid schema")
    object_types = tuple(payload["object_types"])
    if len(object_types) != 25 or object_types != tuple(sorted(set(object_types))):
        raise ValueError("public object vocabulary must contain 25 sorted unique types")
    canonical_sha256 = _sha256_json(object_types)
    if canonical_sha256 != payload["canonical_sha256"]:
        raise ValueError("public object vocabulary canonical hash mismatch")
    return PublicObjectVocabulary(
        schema_version=str(payload["schema_version"]),
        object_types=object_types,
        canonical_sha256=canonical_sha256,
    )


def pose_sha256(pose: OraclePose) -> str:
    return _sha256_json(_pose_payload(pose))


def _validated_object_map(
    objects: tuple[SceneObjectScanInput, ...],
) -> dict[str, SceneObjectScanInput]:
    result: dict[str, SceneObjectScanInput] = {}
    for item in objects:
        if item.exact_object_id in result:
            raise ValueError(f"duplicate reset exact object ID: {item.exact_object_id}")
        result[item.exact_object_id] = item
    if not result:
        raise ValueError("scan policy contains no reset objects")
    for item in result.values():
        for parent_id in item.parent_receptacle_ids:
            parent = result.get(parent_id)
            if parent is None or item.exact_object_id not in parent.receptacle_object_ids:
                raise ValueError(f"reciprocal containment mismatch for {item.exact_object_id}")
        for child_id in item.receptacle_object_ids:
            child = result.get(child_id)
            if child is None or item.exact_object_id not in child.parent_receptacle_ids:
                raise ValueError(f"reciprocal containment mismatch for {item.exact_object_id}")
        if any(ancestor_id not in result for ancestor_id in item.closed_ancestor_exact_ids):
            raise ValueError(f"unknown closed ancestor for {item.exact_object_id}")
    return result


def _validated_cache_map(
    entries: tuple[CachePoseInput, ...],
    objects: dict[str, SceneObjectScanInput],
) -> dict[str, CachePoseInput]:
    result: dict[str, CachePoseInput] = {}
    for entry in entries:
        if entry.exact_object_id not in objects:
            raise ValueError(f"cache pose references unknown exact ID: {entry.exact_object_id}")
        if entry.exact_object_id in result:
            raise ValueError(f"duplicate cache pose exact ID: {entry.exact_object_id}")
        result[entry.exact_object_id] = entry
    return result


def _addressability(
    objects: dict[str, SceneObjectScanInput],
    public_vocabulary: set[str],
) -> dict[str, ObjectAddressability]:
    result: dict[str, ObjectAddressability] = {}
    for exact_object_id, item in objects.items():
        if item.is_picked_up:
            reason: AddressabilityReason = "inventory"
        elif item.closed_ancestor_exact_ids:
            reason = "closed_ancestor"
        elif item.object_type not in public_vocabulary:
            reason = "not_public_semantic"
        else:
            reason = "public_semantic"
        result[exact_object_id] = ObjectAddressability(
            exact_object_id=exact_object_id,
            addressable=reason == "public_semantic",
            reason=reason,
        )
    return result


def _parse_reachable_positions(payload: bytes) -> tuple[tuple[float, float, float], ...]:
    try:
        value = json.loads(payload)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("reachable payload is malformed JSON") from exc
    if not isinstance(value, list):
        raise ValueError("reachable payload must be an array")
    positions: set[tuple[float, float, float]] = set()
    for item in value:
        if not isinstance(item, dict) or "x" not in item or "z" not in item:
            raise ValueError("reachable position must contain x and z")
        try:
            position = tuple(
                _normalize_zero(float(item.get(axis, 0.0))) for axis in ("x", "y", "z")
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("reachable position values must be numeric") from exc
        if not all(math.isfinite(component) for component in position):
            raise ValueError("reachable position values must be finite")
        positions.add(position)
    return tuple(sorted(positions))


def _geometry_pose(
    item: SceneObjectScanInput,
    reachable: tuple[tuple[float, float, float], ...],
    *,
    agent_y: float,
    geometry_policy_version: str,
) -> tuple[OraclePose, str]:
    object_x, object_y, object_z = item.position
    selected = min(
        reachable,
        key=lambda point: (
            (point[0] - object_x) ** 2 + (point[2] - object_z) ** 2,
            point[0],
            point[2],
            point[1],
        ),
    )
    delta_x = object_x - selected[0]
    delta_z = object_z - selected[2]
    rotation = _normalize_zero(round(math.degrees(math.atan2(delta_x, delta_z)) % 360.0, 6))
    horizontal_distance = math.hypot(delta_x, delta_z)
    horizon = _normalize_zero(
        round(-math.degrees(math.atan2(object_y - agent_y, max(horizontal_distance, 1e-9))), 6)
    )
    pose = OraclePose(selected[0], agent_y, selected[2], rotation, horizon)
    source_hash = _sha256_json(
        {
            "exact_object_id": item.exact_object_id,
            "geometry_policy_version": geometry_policy_version,
            "object_position": item.position,
            "reachable_position": selected,
            "rotation": rotation,
            "horizon": horizon,
        }
    )
    return pose, source_hash


def _scan_policy_sha256(
    policy: ScanPolicyInput,
    objects: dict[str, SceneObjectScanInput],
    cache: dict[str, CachePoseInput],
    addressability: dict[str, ObjectAddressability],
) -> str:
    return _sha256_json(
        {
            "scene_generation": policy.scene_generation,
            "scene_reset_fingerprint": policy.scene_reset_fingerprint,
            "algorithm_version": policy.algorithm_version,
            "geometry_policy_version": policy.geometry_policy_version,
            "setup_time_control_version": policy.setup_time_control_version,
            "setup_slow_time_scale": policy.setup_slow_time_scale,
            "setup_restore_time_scale": policy.setup_restore_time_scale,
            "successful_setup_action_offset": policy.successful_setup_action_offset,
            "initial_pose": _pose_payload(policy.initial_pose),
            "public_semantic_vocabulary": policy.public_semantic_vocabulary,
            "objects": [
                {**asdict(objects[exact_id]), **asdict(addressability[exact_id])}
                for exact_id in sorted(objects)
            ],
            "cache_entries": [
                {
                    "exact_object_id": exact_id,
                    "pose": _pose_payload(cache[exact_id].pose),
                    "source_record_sha256": cache[exact_id].source_record_sha256,
                }
                for exact_id in sorted(cache)
            ],
        }
    )


def _plan_sha256(
    *,
    scene_generation: int,
    scene_reset_fingerprint: str,
    algorithm_version: str,
    scan_policy_sha256: str,
    reachable_canonical_sha256: str,
    steps: tuple[ScanPoseStep, ...],
) -> str:
    return _sha256_json(
        {
            "scene_generation": scene_generation,
            "scene_reset_fingerprint": scene_reset_fingerprint,
            "algorithm_version": algorithm_version,
            "scan_policy_sha256": scan_policy_sha256,
            "reachable_canonical_sha256": reachable_canonical_sha256,
            "steps": [
                {
                    "index": step.index,
                    "pose": _pose_payload(step.pose),
                    "send_teleport": step.send_teleport,
                    "provenances": [asdict(provenance) for provenance in step.provenances],
                }
                for step in steps
            ],
        }
    )


def _snapshot_sha256(snapshot: OraclePoseSnapshot) -> str:
    return _sha256_json(
        {
            "scene_generation": snapshot.scene_generation,
            "scene_reset_fingerprint": snapshot.scene_reset_fingerprint,
            "algorithm_version": snapshot.algorithm_version,
            "scan_policy_sha256": snapshot.scan_policy_sha256,
            "reachable_payload_sha256": snapshot.reachable_payload_sha256,
            "reachable_canonical_sha256": snapshot.reachable_canonical_sha256,
            "scan_plan_sha256": snapshot.scan_plan_sha256,
            "initial_event_ref": snapshot.initial_event_ref,
            "restored_event_ref": snapshot.restored_event_ref,
            "initial_world_sha256": snapshot.initial_world_sha256,
            "restored_world_sha256": snapshot.restored_world_sha256,
            "entries": [
                {
                    **asdict(entry),
                    "pose": _pose_payload(entry.pose) if entry.pose is not None else None,
                }
                for entry in snapshot.entries
            ],
        }
    )


def _empty_lookup(
    snapshot: OraclePoseSnapshot,
    status: OracleReadStatus,
    entry: OraclePoseSnapshotEntry | None,
    evidence_ref: str | None,
) -> OraclePoseLookup:
    return OraclePoseLookup(
        status=status,
        scene_generation=snapshot.scene_generation,
        scene_reset_fingerprint=snapshot.scene_reset_fingerprint,
        snapshot_sha256=snapshot.snapshot_sha256,
        pose=None,
        pose_sha256=None,
        pose_freshness_sha256=(entry.pose_freshness_sha256 if entry is not None else None),
        source_kind="none",
        evidence_ref=evidence_ref,
    )


def _require_empty_pose(entry: OraclePoseSnapshotEntry) -> None:
    if entry.pose is not None or entry.pose_sha256 is not None or entry.source_kind != "none":
        raise ValueError(f"{entry.status} snapshot row cannot contain a pose")


def _pose_payload(pose: OraclePose) -> dict[str, float]:
    return {
        "x": pose.x,
        "y": pose.y,
        "z": pose.z,
        "rotation": pose.rotation,
        "horizon": pose.horizon,
    }


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _normalize_zero(value: float) -> float:
    return 0.0 if value == 0.0 else value


def _validate_generation(value: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError("scene generation must be a non-negative integer")


def _validate_sha256(name: str, value: Any) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{name} must be a lowercase SHA-256 hex digest")


def _require_nonempty(name: str, value: Any) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
