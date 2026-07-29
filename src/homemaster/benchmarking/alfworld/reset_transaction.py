"""Typed ALFWorld reset scan/restore transaction."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

from homemaster.benchmarking.alfworld.gateway import (
    ExternalEventRead,
    GatewayActionResult,
    OracleActionGateway,
    OracleExecutionBackend,
)
from homemaster.benchmarking.alfworld.pose_snapshot import (
    CachePoseInput,
    FrozenOraclePoseStore,
    OraclePose,
    OraclePoseSnapshot,
    OraclePoseSnapshotEntry,
    ScanPolicyInput,
    SceneObjectScanInput,
    SceneScanPlanBuilder,
    oracle_pose_matches,
    pose_sha256,
)
from homemaster.benchmarking.alfworld.types import (
    AlfworldBackendKind,
    AlfworldEnvState,
    AlfworldResetResult,
    EpisodeClassification,
    SetupFailureCode,
)


@dataclass(frozen=True)
class ResetTransactionInput:
    backend_kind: AlfworldBackendKind
    state: AlfworldEnvState
    initial_event: ExternalEventRead
    scene_generation: int
    goal_generation: int
    scene_reset_fingerprint: str
    goal_trial_fingerprint: str
    algorithm_version: str
    geometry_policy_version: str
    setup_time_control_version: str
    public_semantic_vocabulary: tuple[str, ...]
    cache_entries: tuple[CachePoseInput, ...]
    snapshot_ref: str
    evidence_ref: str | None
    artifact_root: Path | None = None


@dataclass(frozen=True)
class _Abort(Exception):
    code: SetupFailureCode


class AlfworldResetTransaction:
    def __init__(
        self,
        *,
        backend: OracleExecutionBackend,
        pose_store: FrozenOraclePoseStore,
    ) -> None:
        self._gateway = OracleActionGateway(backend=backend)
        self._pose_store = pose_store

    def run(self, inputs: ResetTransactionInput) -> AlfworldResetResult:
        if inputs.backend_kind != "thor":
            return AlfworldResetResult(
                backend_kind="textworld",
                ready=True,
                state=inputs.state,
                scene_generation=None,
                goal_generation=inputs.goal_generation,
                scene_reset_fingerprint=None,
                goal_trial_fingerprint=inputs.goal_trial_fingerprint,
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

        initial = inputs.initial_event
        trigger: SetupFailureCode | None = None
        slow_attempted = False
        observations: dict[str, tuple[int, float, OraclePose, str, str | None]] = {}
        try:
            self._validate_initial(initial)
            assert initial.pose is not None
            assert initial.world_sha256 is not None
            assert initial.control_sha256 is not None
            assert initial.objects is not None
            self._record_observations(
                observations,
                initial,
                pose=initial.pose,
                provenances={},
            )

            slow_attempted = True
            slow = self._gateway.execute_setup_time_control(0.01)
            self._require_unchanged_action(
                slow,
                pose=initial.pose,
                world_sha256=initial.world_sha256,
                control_sha256=initial.control_sha256,
                rejected="scan_time_scale_enter_rejected",
                unreadable="scan_time_scale_enter_unreadable",
                mismatch="scan_time_scale_enter_unreadable",
            )

            query = self._gateway.execute_setup_query()
            self._require_unchanged_action(
                query,
                pose=initial.pose,
                world_sha256=initial.world_sha256,
                control_sha256=initial.control_sha256,
                rejected="reachable_query_rejected",
                unreadable="reachable_query_unreadable",
                mismatch="scan_world_drift",
            )
            if query.event.reachable_payload is None:
                raise _Abort("reachable_query_unreadable")

            try:
                policy = ScanPolicyInput(
                    scene_generation=inputs.scene_generation,
                    scene_reset_fingerprint=inputs.scene_reset_fingerprint,
                    algorithm_version=inputs.algorithm_version,
                    geometry_policy_version=inputs.geometry_policy_version,
                    setup_time_control_version=inputs.setup_time_control_version,
                    setup_slow_time_scale=0.01,
                    setup_restore_time_scale=1.0,
                    successful_setup_action_offset=4,
                    initial_pose=initial.pose,
                    public_semantic_vocabulary=inputs.public_semantic_vocabulary,
                    objects=tuple(initial.objects),
                    cache_entries=inputs.cache_entries,
                    reachable_payload=query.event.reachable_payload,
                )
                plan = SceneScanPlanBuilder().build(policy)
            except ValueError as exc:
                raise _Abort("scan_plan_malformed") from exc

            for step in plan.steps[1:]:
                action = self._gateway.execute_setup_teleport(step)
                self._require_scan_action(
                    action,
                    pose=step.pose,
                    world_sha256=initial.world_sha256,
                    control_sha256=initial.control_sha256,
                )
                self._record_observations(
                    observations,
                    action.event,
                    pose=action.event.pose,
                    provenances={
                        provenance.exact_object_id: provenance.source_kind
                        for provenance in step.provenances
                    },
                )

            pose_restore = self._gateway.execute_restore(initial.pose)
            self._require_restore_action(
                pose_restore,
                pose=initial.pose,
                world_sha256=initial.world_sha256,
                control_sha256=initial.control_sha256,
            )
            normal = self._gateway.execute_setup_time_control(1.0)
            self._require_final_normal_event(normal, initial)

            entries = self._snapshot_entries(
                objects=tuple(initial.objects),
                public_vocabulary=set(inputs.public_semantic_vocabulary),
                observations=observations,
            )
            initial_event_ref = _event_reference(initial, "events/0000-reset.json")
            restored_event_ref = _event_reference(
                normal.event,
                f"events/{normal.request.sequence:04d}-normal-time-restore.json",
            )
            snapshot = OraclePoseSnapshot.create(
                scene_generation=inputs.scene_generation,
                scene_reset_fingerprint=inputs.scene_reset_fingerprint,
                algorithm_version=inputs.algorithm_version,
                scan_policy_sha256=plan.scan_policy_sha256,
                reachable_payload_sha256=plan.reachable_payload_sha256,
                reachable_canonical_sha256=plan.reachable_canonical_sha256,
                scan_plan_sha256=plan.canonical_sha256,
                initial_event_ref=initial_event_ref,
                restored_event_ref=restored_event_ref,
                initial_world_sha256=initial.world_sha256,
                restored_world_sha256=normal.event.world_sha256 or "",
                entries=entries,
            )
            try:
                evidence_ref = self._persist_success_artifacts(
                    inputs=inputs,
                    initial=initial,
                    snapshot=snapshot,
                    initial_event_ref=initial_event_ref,
                )
            except (OSError, TypeError, ValueError, OverflowError) as exc:
                raise _Abort("scan_evidence_failed") from exc
            self._pose_store.publish(snapshot)
            return AlfworldResetResult(
                backend_kind="thor",
                ready=True,
                state=inputs.state,
                scene_generation=inputs.scene_generation,
                goal_generation=inputs.goal_generation,
                scene_reset_fingerprint=inputs.scene_reset_fingerprint,
                goal_trial_fingerprint=inputs.goal_trial_fingerprint,
                snapshot_sha256=snapshot.snapshot_sha256,
                snapshot_ref=inputs.snapshot_ref,
                setup_trigger=None,
                setup_failure=None,
                classification=None,
                score_eligible=True,
                setup_backend_action_count=len(self._gateway.ledger),
                recovery_status="restored",
                cleanup_status="not_needed",
                quarantine_required=False,
                environment_disposition="ready",
                evidence_ref=evidence_ref,
            )
        except _Abort as exc:
            trigger = exc.code
        except Exception:
            trigger = "setup_unexpected"

        return self._terminal_result(
            inputs=inputs,
            trigger=trigger or "setup_unexpected",
            initial=initial,
            slow_attempted=slow_attempted,
        )

    def _persist_success_artifacts(
        self,
        *,
        inputs: ResetTransactionInput,
        initial: ExternalEventRead,
        snapshot: OraclePoseSnapshot,
        initial_event_ref: str,
    ) -> str | None:
        root = inputs.artifact_root
        if root is None:
            return None
        if inputs.evidence_ref is None:
            raise ValueError("reset evidence_ref is required when artifact_root is set")

        event_files, ledger_rows = _collect_transaction_evidence(
            initial_event_ref=initial_event_ref,
            initial=initial,
            ledger=self._gateway.ledger,
        )

        for ref in (*event_files, inputs.snapshot_ref, inputs.evidence_ref):
            _artifact_path(root, ref)
        for ref, payload in sorted(event_files.items()):
            _write_json_atomic(root, ref, payload)
        _write_json_atomic(root, inputs.snapshot_ref, snapshot.to_dict())
        _write_json_atomic(
            root,
            inputs.evidence_ref,
            {
                "schema_version": "alfworld-reset-transaction-v1",
                "ready": True,
                "scene_generation": inputs.scene_generation,
                "goal_generation": inputs.goal_generation,
                "scene_reset_fingerprint": inputs.scene_reset_fingerprint,
                "goal_trial_fingerprint": inputs.goal_trial_fingerprint,
                "initial_event": _event_ledger_payload(initial_event_ref, initial),
                "actions": ledger_rows,
                "setup_backend_action_count": len(ledger_rows),
                "snapshot_ref": inputs.snapshot_ref,
                "snapshot_sha256": snapshot.snapshot_sha256,
                "event_files": sorted(event_files),
            },
        )
        return inputs.evidence_ref

    def _persist_terminal_artifacts(
        self,
        *,
        inputs: ResetTransactionInput,
        initial: ExternalEventRead,
        trigger: SetupFailureCode,
        final_code: SetupFailureCode,
        recovery_status: str,
        cleanup_status: str,
        environment_disposition: str,
    ) -> str | None:
        root = inputs.artifact_root
        if root is None:
            return None
        if inputs.evidence_ref is None:
            raise ValueError("reset evidence_ref is required when artifact_root is set")
        initial_event_ref = _event_reference(initial, "events/0000-reset.json")
        event_files, ledger_rows = _collect_transaction_evidence(
            initial_event_ref=initial_event_ref,
            initial=initial,
            ledger=self._gateway.ledger,
        )
        for ref in (*event_files, inputs.evidence_ref):
            _artifact_path(root, ref)
        for ref, payload in sorted(event_files.items()):
            _write_json_atomic(root, ref, payload)
        _write_json_atomic(
            root,
            inputs.evidence_ref,
            {
                "schema_version": "alfworld-reset-transaction-v1",
                "ready": False,
                "scene_generation": inputs.scene_generation,
                "goal_generation": inputs.goal_generation,
                "scene_reset_fingerprint": inputs.scene_reset_fingerprint,
                "goal_trial_fingerprint": inputs.goal_trial_fingerprint,
                "initial_event": _event_ledger_payload(initial_event_ref, initial),
                "actions": ledger_rows,
                "setup_backend_action_count": len(ledger_rows),
                "setup_trigger": trigger,
                "setup_failure": final_code,
                "recovery_status": recovery_status,
                "cleanup_status": cleanup_status,
                "environment_disposition": environment_disposition,
                "snapshot_ref": None,
                "snapshot_sha256": None,
                "event_files": sorted(event_files),
            },
        )
        return inputs.evidence_ref

    def _terminal_result(
        self,
        *,
        inputs: ResetTransactionInput,
        trigger: SetupFailureCode,
        initial: ExternalEventRead,
        slow_attempted: bool,
    ) -> AlfworldResetResult:
        recovery_status: Literal["not_needed", "restored", "failed"] = "not_needed"
        final_code = trigger
        if (
            slow_attempted
            and initial.pose is not None
            and initial.world_sha256 is not None
            and initial.control_sha256 is not None
        ):
            pose_restore = self._gateway.execute_restore(initial.pose)
            pose_restored = self._action_matches(
                pose_restore,
                pose=initial.pose,
                world_sha256=initial.world_sha256,
                control_sha256=initial.control_sha256,
            )
            normal = self._gateway.execute_setup_time_control(1.0)
            normal_restored = self._final_event_matches(normal, initial)
            if pose_restored and normal_restored:
                recovery_status = "restored"
            else:
                recovery_status = "failed"
                if not pose_restored:
                    final_code = (
                        "scan_restore_rejected"
                        if not pose_restore.success
                        else "scan_restore_mismatch"
                    )
                elif not normal.success:
                    final_code = "scan_time_scale_restore_rejected"
                else:
                    final_code = "scan_time_scale_restore_unreadable"

        cleanup = self._gateway.close()
        quarantine_required = recovery_status == "failed"
        if cleanup.status != "succeeded":
            final_code = "scan_cleanup_failed"
            classification: EpisodeClassification = "runtime_failure"
            quarantine_required = True
            environment_disposition = "quarantined"
        else:
            classification = _classification_for(final_code, recovery_status)
            environment_disposition = "closed"
        evidence_ref = None
        try:
            evidence_ref = self._persist_terminal_artifacts(
                inputs=inputs,
                initial=initial,
                trigger=trigger,
                final_code=final_code,
                recovery_status=recovery_status,
                cleanup_status=cleanup.status,
                environment_disposition=environment_disposition,
            )
        except (OSError, TypeError, ValueError, OverflowError):
            if classification not in {"runtime_failure", "execution_state_uncertain"}:
                final_code = "scan_evidence_failed"
                classification = "artifact_failure"
        return AlfworldResetResult(
            backend_kind="thor",
            ready=False,
            state=None,
            scene_generation=inputs.scene_generation,
            goal_generation=inputs.goal_generation,
            scene_reset_fingerprint=inputs.scene_reset_fingerprint,
            goal_trial_fingerprint=inputs.goal_trial_fingerprint,
            snapshot_sha256=None,
            snapshot_ref=None,
            setup_trigger=trigger,
            setup_failure=final_code,
            classification=classification,
            score_eligible=False,
            setup_backend_action_count=len(self._gateway.ledger),
            recovery_status=recovery_status,
            cleanup_status=cleanup.status,
            quarantine_required=quarantine_required,
            environment_disposition=environment_disposition,
            evidence_ref=evidence_ref,
        )

    @staticmethod
    def _validate_initial(event: ExternalEventRead) -> None:
        if event.status != "ok":
            raise _Abort("initial_state_unreadable")
        if event.pose is None or event.world_sha256 is None or event.control_sha256 is None:
            raise _Abort("initial_state_unreadable")
        if event.visibility_sha256 is None or event.frame_sha256 is None or event.objects is None:
            raise _Abort("initial_state_unreadable")
        if event.world_payload is None or event.control_payload is None:
            raise _Abort("initial_state_unreadable")

    @staticmethod
    def _require_unchanged_action(
        result: GatewayActionResult,
        *,
        pose: OraclePose,
        world_sha256: str,
        control_sha256: str,
        rejected: SetupFailureCode,
        unreadable: SetupFailureCode,
        mismatch: SetupFailureCode,
    ) -> None:
        if result.event.status != "ok" or result.event.returned_action is None:
            raise _Abort(unreadable)
        if not result.success:
            raise _Abort(rejected)
        if (
            not oracle_pose_matches(result.event.pose, pose)
            or result.event.world_sha256 != world_sha256
            or result.event.control_sha256 != control_sha256
        ):
            raise _Abort(mismatch)

    @staticmethod
    def _require_scan_action(
        result: GatewayActionResult,
        *,
        pose: OraclePose,
        world_sha256: str,
        control_sha256: str,
    ) -> None:
        if result.event.status != "ok" or result.event.returned_action is None:
            raise _Abort("scan_observation_unreadable")
        if not result.success:
            raise _Abort("scan_pose_rejected")
        if not oracle_pose_matches(result.event.pose, pose):
            raise _Abort("scan_pose_mismatch")
        if result.event.world_sha256 != world_sha256:
            raise _Abort("scan_world_drift")
        if result.event.control_sha256 != control_sha256:
            raise _Abort("scan_world_drift")

    @staticmethod
    def _require_restore_action(
        result: GatewayActionResult,
        *,
        pose: OraclePose,
        world_sha256: str,
        control_sha256: str,
    ) -> None:
        if not result.success:
            raise _Abort("scan_restore_rejected")
        if (
            not oracle_pose_matches(result.event.pose, pose)
            or result.event.world_sha256 != world_sha256
            or result.event.control_sha256 != control_sha256
        ):
            raise _Abort("scan_restore_mismatch")

    @classmethod
    def _require_final_normal_event(
        cls,
        result: GatewayActionResult,
        initial: ExternalEventRead,
    ) -> None:
        if not result.success:
            raise _Abort("scan_time_scale_restore_rejected")
        if not cls._final_event_matches(result, initial):
            raise _Abort("scan_time_scale_restore_unreadable")

    @staticmethod
    def _action_matches(
        result: GatewayActionResult,
        *,
        pose: OraclePose,
        world_sha256: str,
        control_sha256: str,
    ) -> bool:
        return (
            result.success
            and oracle_pose_matches(result.event.pose, pose)
            and result.event.world_sha256 == world_sha256
            and result.event.control_sha256 == control_sha256
        )

    @staticmethod
    def _final_event_matches(
        result: GatewayActionResult,
        initial: ExternalEventRead,
    ) -> bool:
        event = result.event
        return (
            result.success
            and oracle_pose_matches(event.pose, initial.pose)
            and event.world_sha256 == initial.world_sha256
            and event.control_sha256 == initial.control_sha256
            and event.visibility_sha256 == initial.visibility_sha256
            and event.frame_sha256 == initial.frame_sha256
        )

    @staticmethod
    def _record_observations(
        observations: dict[str, tuple[int, float, OraclePose, str, str | None]],
        event: ExternalEventRead,
        *,
        pose: OraclePose,
        provenances: dict[str, str],
    ) -> None:
        areas = dict(event.bbox_areas)
        ranks = {"cache": 0, "geometry": 1, "incidental": 2}
        for exact_object_id in event.strict_visible_exact_ids:
            area = areas.get(exact_object_id)
            if area is None or area <= 0:
                continue
            source = provenances.get(exact_object_id, "incidental")
            candidate = (
                ranks[source],
                -area,
                pose,
                source,
                event.raw_event_ref,
            )
            previous = observations.get(exact_object_id)
            if previous is None or candidate < previous:
                observations[exact_object_id] = candidate

    @staticmethod
    def _snapshot_entries(
        *,
        objects: tuple[SceneObjectScanInput, ...],
        public_vocabulary: set[str],
        observations: dict[str, tuple[int, float, OraclePose, str, str | None]],
    ) -> tuple[OraclePoseSnapshotEntry, ...]:
        entries: list[OraclePoseSnapshotEntry] = []
        for item in sorted(objects, key=lambda value: value.exact_object_id):
            if item.is_picked_up:
                addressable, reason = False, "inventory"
            elif item.closed_ancestor_exact_ids:
                addressable, reason = False, "closed_ancestor"
            elif item.object_type not in public_vocabulary:
                addressable, reason = False, "not_public_semantic"
            else:
                addressable, reason = True, "public_semantic"
            observation = observations.get(item.exact_object_id)
            if addressable and observation is not None:
                _, _, pose, source, evidence_ref = observation
                entries.append(
                    OraclePoseSnapshotEntry(
                        exact_object_id=item.exact_object_id,
                        status="ok",
                        addressable=True,
                        addressability_reason=reason,
                        pose=pose,
                        pose_sha256=pose_sha256(pose),
                        pose_freshness_sha256=item.pose_freshness_sha256,
                        source_kind=source,
                        evidence_ref=evidence_ref,
                    )
                )
            else:
                entries.append(
                    OraclePoseSnapshotEntry(
                        exact_object_id=item.exact_object_id,
                        status="coverage_miss" if addressable else "unobserved",
                        addressable=addressable,
                        addressability_reason=reason,
                        pose=None,
                        pose_sha256=None,
                        pose_freshness_sha256=item.pose_freshness_sha256,
                        source_kind="none",
                        evidence_ref=None,
                    )
                )
        return tuple(entries)


def _collect_transaction_evidence(
    *,
    initial_event_ref: str,
    initial: ExternalEventRead,
    ledger: tuple[GatewayActionResult, ...],
) -> tuple[dict[str, dict[str, object]], list[dict[str, object]]]:
    event_files: dict[str, dict[str, object]] = {}

    def record_event(ref: str, event: ExternalEventRead) -> None:
        payload = external_event_evidence_payload(ref, event)
        previous = event_files.get(ref)
        if previous is not None and previous != payload:
            raise ValueError(f"conflicting external events share evidence ref: {ref}")
        event_files[ref] = payload

    record_event(initial_event_ref, initial)
    ledger_rows: list[dict[str, object]] = []
    for result in ledger:
        event_ref = _event_reference(
            result.event,
            f"events/{result.request.sequence:04d}-{result.request.payload['action']}.json",
        )
        record_event(event_ref, result.event)
        ledger_rows.append(
            {
                "request": {
                    "phase": result.request.phase,
                    "sequence": result.request.sequence,
                    "payload": result.request.payload,
                    "request_sha256": result.request.request_sha256,
                },
                "response": _event_ledger_payload(event_ref, result.event),
                "success": result.success,
                "duration_ms": result.duration_ms,
            }
        )
    return event_files, ledger_rows


def _event_reference(event: ExternalEventRead, fallback: str) -> str:
    return event.raw_event_ref or fallback


def _event_ledger_payload(ref: str, event: ExternalEventRead) -> dict[str, object]:
    evidence = external_event_evidence_payload(ref, event)
    return {
        "event_ref": ref,
        "event_payload_sha256": _json_sha256(evidence),
        "raw_event_sha256": event.raw_event_sha256,
        "status": event.status,
        "returned_action": event.returned_action,
        "action_success": event.action_success,
        "returned_pose": asdict(event.pose) if event.pose is not None else None,
        "world_sha256": event.world_sha256,
        "world_payload": event.world_payload,
        "control_sha256": event.control_sha256,
        "control_payload": event.control_payload,
        "visibility_sha256": event.visibility_sha256,
        "frame_sha256": event.frame_sha256,
    }


def external_event_evidence_payload(
    ref: str,
    event: ExternalEventRead,
) -> dict[str, object]:
    reachable = event.reachable_payload
    raw_frame = event.raw_frame_bytes
    return {
        "schema_version": "alfworld-external-event-v2",
        "event_ref": ref,
        "raw_event_sha256": event.raw_event_sha256,
        "raw_metadata_payload": event.raw_metadata_payload,
        "raw_frame_encoding": "base64" if raw_frame is not None else None,
        "raw_frame_base64": (
            base64.b64encode(raw_frame).decode("ascii") if raw_frame is not None else None
        ),
        "raw_frame_size": len(raw_frame) if raw_frame is not None else None,
        "status": event.status,
        "returned_action": event.returned_action,
        "action_success": event.action_success,
        "returned_pose": asdict(event.pose) if event.pose is not None else None,
        "world_sha256": event.world_sha256,
        "world_payload": event.world_payload,
        "control_sha256": event.control_sha256,
        "control_payload": event.control_payload,
        "visibility_sha256": event.visibility_sha256,
        "frame_sha256": event.frame_sha256,
        "strict_visible_exact_ids": list(event.strict_visible_exact_ids),
        "bbox_areas": [list(item) for item in event.bbox_areas],
        "reachable_payload_sha256": (
            hashlib.sha256(reachable).hexdigest() if reachable is not None else None
        ),
        "reachable_payload_size": len(reachable) if reachable is not None else None,
    }


def _json_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _artifact_path(root: Path, ref: str) -> Path:
    if not isinstance(ref, str) or not ref or "\\" in ref:
        raise ValueError("artifact reference must be a non-empty POSIX relative path")
    raw_parts = ref.split("/")
    path = PurePosixPath(ref)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in raw_parts):
        raise ValueError(f"unsafe artifact reference: {ref}")
    if path.suffix != ".json":
        raise ValueError(f"artifact reference must name a JSON file: {ref}")
    root_path = Path(root).resolve()
    target = root_path.joinpath(*path.parts).resolve()
    if not target.is_relative_to(root_path):
        raise ValueError(f"artifact reference escapes its root: {ref}")
    return target


def _write_json_atomic(root: Path, ref: str, payload: object) -> None:
    target = _artifact_path(root, ref)
    target.parent.mkdir(parents=True, exist_ok=True)
    encoded = (
        json.dumps(
            payload,
            sort_keys=True,
            indent=2,
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=target.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as writer:
            writer.write(encoded)
            writer.flush()
            os.fsync(writer.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _classification_for(
    code: SetupFailureCode,
    recovery_status: str,
) -> EpisodeClassification:
    if recovery_status != "restored" and code != "external_reset_failed":
        return "execution_state_uncertain"
    if code in {"expected_manifest_mismatch", "cache_input_malformed", "scan_evidence_failed"}:
        return "artifact_failure"
    if code in {"external_reset_failed", "setup_runtime_failed", "scan_cleanup_failed"}:
        return "runtime_failure"
    if code in {
        "scan_time_scale_enter_rejected",
        "reachable_query_rejected",
        "scan_pose_rejected",
    }:
        return "harness_navigation_failure"
    if code in {
        "scan_plan_missing",
        "scan_plan_malformed",
        "snapshot_invariant_failed",
        "setup_unexpected",
    }:
        return "unclassified_execution_failure"
    return "execution_state_uncertain"
