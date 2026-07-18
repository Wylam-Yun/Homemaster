from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

from homemaster.benchmarking.alfworld.gateway import CleanupResult, ExternalEventRead
from homemaster.benchmarking.alfworld.pose_snapshot import (
    CachePoseInput,
    FrozenOraclePoseStore,
    OraclePose,
    SceneObjectScanInput,
)
from homemaster.benchmarking.alfworld.reset_transaction import (
    AlfworldResetTransaction,
    ResetTransactionInput,
)
from homemaster.benchmarking.alfworld.types import AlfworldEnvState


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _json_sha(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode()
    ).hexdigest()


POSE = OraclePose(0, 0.9, 0, 0, 0)
SCAN_POSE = OraclePose(1, 0.9, 1, 180, 30)
NORMALIZED_SCAN_POSE = OraclePose(1, 0.9, 1, 180.000006, 30.9)
WORLD_PAYLOAD = {
    "metadata": {"sceneName": "FloorPlan1_physics"},
    "objects": [{"isOpen": False, "objectId": "Mug|1"}],
}
CONTROL_PAYLOAD = {
    "cleaned_objects": [],
    "cooled_objects": [],
    "goal_finished": False,
    "goal_idx": 0,
    "heated_objects": [],
    "step_num": 0,
    "task_type": "pick_and_place_simple",
}
WORLD = _json_sha(WORLD_PAYLOAD)
CONTROL = _json_sha(CONTROL_PAYLOAD)
VISIBILITY = _sha("visibility")
FRAME = _sha("frame")


def _object() -> SceneObjectScanInput:
    return SceneObjectScanInput(
        exact_object_id="Mug|1",
        object_type="Mug",
        position=(1.0, 0.8, 1.0),
        parent_receptacle_ids=(),
        receptacle_object_ids=(),
        is_picked_up=False,
        closed_ancestor_exact_ids=(),
        pose_freshness_sha256=_sha("mug"),
    )


def _event(
    action: str,
    *,
    success: bool = True,
    pose: OraclePose = POSE,
    world: str = WORLD,
    visible: tuple[str, ...] = (),
    control_payload: dict[str, object] | None = None,
    event_ref: str | None = None,
) -> ExternalEventRead:
    control = CONTROL_PAYLOAD if control_payload is None else control_payload
    return ExternalEventRead(
        status="ok",
        returned_action=action,
        action_success=success,
        pose=pose,
        world_sha256=world,
        visibility_sha256=VISIBILITY,
        frame_sha256=FRAME,
        objects=(_object(),) if action == "Reset" else None,
        reachable_payload=(b'[{"x":0,"y":0,"z":0}]' if action == "GetReachablePositions" else None),
        strict_visible_exact_ids=visible,
        bbox_areas=(("Mug|1", 100.0),) if visible else (),
        raw_event_ref=event_ref or f"events/{action}.json",
        raw_event_sha256=_sha(action + str(success)),
        control_sha256=_json_sha(control),
        world_payload=WORLD_PAYLOAD,
        control_payload=control,
    )


class ScriptedBackend:
    def __init__(self, events: list[ExternalEventRead], *, cleanup: str = "succeeded") -> None:
        self.events = list(events)
        self.requests = []
        self.cleanup_status = cleanup
        self.close_calls = 0

    def capture_event(self) -> ExternalEventRead:
        return _event("Reset")

    def send(self, request):
        self.requests.append(request)
        assert self.events
        return self.events.pop(0)

    def close(self) -> CleanupResult:
        self.close_calls += 1
        return CleanupResult(status=self.cleanup_status, evidence_ref="cleanup.json")


def _state() -> AlfworldEnvState:
    return AlfworldEnvState(
        episode_id="episode-1",
        task="put mug in cabinet",
        observation="room",
        inventory=None,
        last_command=None,
        last_feedback=None,
        reward=0.0,
        done=False,
        won=False,
        goal_condition_success_rate=0.0,
        frame_path="frame-0000.png",
        step_index=0,
        invalid_action_count=0,
    )


def _transaction_input(*, artifact_root: Path | None = None) -> ResetTransactionInput:
    return ResetTransactionInput(
        backend_kind="thor",
        state=_state(),
        initial_event=_event("Reset"),
        scene_generation=1,
        goal_generation=1,
        scene_reset_fingerprint=_sha("scene"),
        goal_trial_fingerprint=_sha("goal"),
        algorithm_version="v18-bounded-scan-1",
        geometry_policy_version="v18-nearest-yaw-horizon-1",
        setup_time_control_version="change-time-scale-bracket-v1",
        public_semantic_vocabulary=("Mug",),
        cache_entries=(
            CachePoseInput(
                exact_object_id="Mug|1",
                pose=SCAN_POSE,
                source_record_sha256=_sha("cache"),
            ),
        ),
        snapshot_ref="snapshot.json",
        evidence_ref="reset.json",
        artifact_root=artifact_root,
    )


def test_reset_transaction_executes_full_sequence_and_publishes_once() -> None:
    backend = ScriptedBackend(
        [
            _event("ChangeTimeScale"),
            _event("GetReachablePositions"),
            _event("TeleportFull", pose=SCAN_POSE, visible=("Mug|1",)),
            _event("TeleportFull"),
            _event("ChangeTimeScale"),
        ]
    )
    store = FrozenOraclePoseStore()
    transaction = AlfworldResetTransaction(backend=backend, pose_store=store)

    result = transaction.run(_transaction_input())

    assert result.ready
    assert result.evidence_ref is None
    assert result.setup_backend_action_count == 5
    assert [request.payload["action"] for request in backend.requests] == [
        "ChangeTimeScale",
        "GetReachablePositions",
        "TeleportFull",
        "TeleportFull",
        "ChangeTimeScale",
    ]
    assert backend.close_calls == 0
    assert store.get_pose(
        scene_generation=1,
        scene_reset_fingerprint=_sha("scene"),
        exact_anchor_id="Mug|1",
    ).status == "ok"


def test_reset_accepts_thor_pose_normalization_and_stores_returned_pose() -> None:
    backend = ScriptedBackend(
        [
            _event("ChangeTimeScale"),
            _event("GetReachablePositions"),
            _event("TeleportFull", pose=NORMALIZED_SCAN_POSE, visible=("Mug|1",)),
            _event("TeleportFull"),
            _event("ChangeTimeScale"),
        ]
    )
    store = FrozenOraclePoseStore()

    result = AlfworldResetTransaction(backend=backend, pose_store=store).run(
        _transaction_input()
    )

    assert result.ready
    assert store.get_pose(
        scene_generation=1,
        scene_reset_fingerprint=_sha("scene"),
        exact_anchor_id="Mug|1",
    ).pose == NORMALIZED_SCAN_POSE


def test_reset_failure_recovers_time_and_never_publishes_partial_snapshot() -> None:
    backend = ScriptedBackend(
        [
            _event("ChangeTimeScale"),
            _event("GetReachablePositions"),
            _event("TeleportFull", success=False, pose=SCAN_POSE),
            _event("TeleportFull"),
            _event("ChangeTimeScale"),
        ]
    )
    store = FrozenOraclePoseStore()
    result = AlfworldResetTransaction(backend=backend, pose_store=store).run(
        _transaction_input()
    )

    assert not result.ready
    assert result.evidence_ref is None
    assert result.setup_trigger == "scan_pose_rejected"
    assert result.setup_failure == "scan_pose_rejected"
    assert result.recovery_status == "restored"
    assert result.environment_disposition == "closed"
    assert backend.requests[-1].payload == {"action": "ChangeTimeScale", "timeScale": 1.0}
    assert backend.close_calls == 1


def test_pose_recovery_failure_still_attempts_normal_time_and_upgrades_uncertainty() -> None:
    backend = ScriptedBackend(
        [
            _event("ChangeTimeScale"),
            _event("GetReachablePositions"),
            _event("TeleportFull", success=False, pose=SCAN_POSE),
            _event("TeleportFull", success=False, pose=SCAN_POSE),
            _event("ChangeTimeScale", pose=SCAN_POSE),
        ]
    )
    result = AlfworldResetTransaction(
        backend=backend,
        pose_store=FrozenOraclePoseStore(),
    ).run(_transaction_input())

    assert not result.ready
    assert result.setup_trigger == "scan_pose_rejected"
    assert result.setup_failure == "scan_restore_rejected"
    assert result.classification == "execution_state_uncertain"
    assert result.quarantine_required is True
    assert backend.requests[-1].payload["action"] == "ChangeTimeScale"


def test_successful_but_wrong_recovery_pose_is_reported_as_restore_mismatch() -> None:
    backend = ScriptedBackend(
        [
            _event("ChangeTimeScale"),
            _event("GetReachablePositions"),
            _event("TeleportFull", success=False, pose=SCAN_POSE),
            _event("TeleportFull", pose=SCAN_POSE),
            _event("ChangeTimeScale"),
        ]
    )

    result = AlfworldResetTransaction(
        backend=backend,
        pose_store=FrozenOraclePoseStore(),
    ).run(_transaction_input())

    assert not result.ready
    assert result.setup_trigger == "scan_pose_rejected"
    assert result.setup_failure == "scan_restore_mismatch"
    assert result.recovery_status == "failed"
    assert result.classification == "execution_state_uncertain"


def test_reset_rejects_alfworld_control_state_drift() -> None:
    drifted_control = {**CONTROL_PAYLOAD, "step_num": 1}
    backend = ScriptedBackend(
        [
            _event("ChangeTimeScale"),
            _event("GetReachablePositions"),
            _event(
                "TeleportFull",
                pose=SCAN_POSE,
                visible=("Mug|1",),
                control_payload=drifted_control,
            ),
            _event("TeleportFull"),
            _event("ChangeTimeScale"),
        ]
    )

    result = AlfworldResetTransaction(
        backend=backend,
        pose_store=FrozenOraclePoseStore(),
    ).run(_transaction_input())

    assert not result.ready
    assert result.setup_trigger == "scan_world_drift"
    assert result.recovery_status == "restored"


def test_reset_persists_recomputable_snapshot_ledger_and_events(tmp_path: Path) -> None:
    backend = ScriptedBackend(
        [
            _event("ChangeTimeScale", event_ref="events/0001-slow.json"),
            _event("GetReachablePositions", event_ref="events/0002-reachable.json"),
            _event(
                "TeleportFull",
                pose=SCAN_POSE,
                visible=("Mug|1",),
                event_ref="events/0003-scan.json",
            ),
            _event("TeleportFull", event_ref="events/0004-restore.json"),
            _event("ChangeTimeScale", event_ref="events/0005-normal.json"),
        ]
    )

    result = AlfworldResetTransaction(
        backend=backend,
        pose_store=FrozenOraclePoseStore(),
    ).run(_transaction_input(artifact_root=tmp_path))

    assert result.ready
    assert result.evidence_ref == "reset.json"
    snapshot = json.loads((tmp_path / "snapshot.json").read_text())
    snapshot_sha256 = snapshot.pop("snapshot_sha256")
    assert _json_sha(snapshot) == snapshot_sha256 == result.snapshot_sha256

    ledger = json.loads((tmp_path / "reset.json").read_text())
    assert ledger["setup_backend_action_count"] == 5
    assert ledger["snapshot_sha256"] == result.snapshot_sha256
    for row in ledger["actions"]:
        request = row["request"]
        assert _json_sha(request["payload"]) == request["request_sha256"]
        response = row["response"]
        event_payload = json.loads((tmp_path / response["event_ref"]).read_text())
        assert _json_sha(event_payload) == response["event_payload_sha256"]
        assert _json_sha(event_payload["world_payload"]) == event_payload["world_sha256"]
        assert (
            _json_sha(event_payload["control_payload"])
            == event_payload["control_sha256"]
        )
    assert sorted(ledger["event_files"]) == sorted(
        path.relative_to(tmp_path).as_posix()
        for path in (tmp_path / "events").glob("*.json")
    )


def test_failed_reset_persists_recovery_ledger_and_events(tmp_path: Path) -> None:
    backend = ScriptedBackend(
        [
            _event("ChangeTimeScale", event_ref="events/0001-slow.json"),
            _event("GetReachablePositions", event_ref="events/0002-reachable.json"),
            _event(
                "TeleportFull",
                success=False,
                pose=SCAN_POSE,
                event_ref="events/0003-scan-failed.json",
            ),
            _event("TeleportFull", event_ref="events/0004-recovery-restore.json"),
            _event("ChangeTimeScale", event_ref="events/0005-recovery-normal.json"),
        ]
    )

    result = AlfworldResetTransaction(
        backend=backend,
        pose_store=FrozenOraclePoseStore(),
    ).run(_transaction_input(artifact_root=tmp_path))

    assert not result.ready
    assert result.evidence_ref == "reset.json"
    assert result.setup_trigger == result.setup_failure == "scan_pose_rejected"
    ledger = json.loads((tmp_path / "reset.json").read_text())
    assert ledger["ready"] is False
    assert ledger["setup_trigger"] == ledger["setup_failure"] == "scan_pose_rejected"
    assert ledger["recovery_status"] == "restored"
    assert ledger["setup_backend_action_count"] == len(ledger["actions"]) == 5
    assert not (tmp_path / "snapshot.json").exists()
    assert len(list((tmp_path / "events").glob("*.json"))) == 6


def test_reset_rejects_artifact_path_traversal_before_publish(tmp_path: Path) -> None:
    backend = ScriptedBackend(
        [
            _event("ChangeTimeScale", event_ref="events/0001-slow.json"),
            _event("GetReachablePositions", event_ref="events/0002-reachable.json"),
            _event(
                "TeleportFull",
                pose=SCAN_POSE,
                visible=("Mug|1",),
                event_ref="events/0003-scan.json",
            ),
            _event("TeleportFull", event_ref="events/0004-restore.json"),
            _event("ChangeTimeScale", event_ref="events/0005-normal.json"),
            _event("TeleportFull", event_ref="events/0006-recovery-restore.json"),
            _event("ChangeTimeScale", event_ref="events/0007-recovery-normal.json"),
        ]
    )
    store = FrozenOraclePoseStore()
    inputs = replace(
        _transaction_input(artifact_root=tmp_path),
        snapshot_ref="../snapshot.json",
    )

    result = AlfworldResetTransaction(backend=backend, pose_store=store).run(inputs)

    assert not result.ready
    assert result.setup_trigger == "scan_evidence_failed"
    assert result.classification == "artifact_failure"
    assert not (tmp_path.parent / "snapshot.json").exists()
    ledger = json.loads((tmp_path / "reset.json").read_text())
    assert ledger["ready"] is False
    assert ledger["setup_failure"] == "scan_evidence_failed"


def test_artifact_write_failure_does_not_return_dangling_evidence_ref(
    tmp_path: Path,
) -> None:
    backend = ScriptedBackend(
        [
            _event("ChangeTimeScale", event_ref="events/0001-slow.json"),
            _event("GetReachablePositions", event_ref="events/0002-reachable.json"),
            _event(
                "TeleportFull",
                pose=SCAN_POSE,
                visible=("Mug|1",),
                event_ref="events/0003-scan.json",
            ),
            _event("TeleportFull", event_ref="events/0004-restore.json"),
            _event("ChangeTimeScale", event_ref="events/0005-normal.json"),
            _event("TeleportFull", event_ref="events/0006-recovery-restore.json"),
            _event("ChangeTimeScale", event_ref="events/0007-recovery-normal.json"),
        ]
    )
    invalid_root = tmp_path / "artifact-root"
    invalid_root.write_text("not a directory", encoding="utf-8")

    result = AlfworldResetTransaction(
        backend=backend,
        pose_store=FrozenOraclePoseStore(),
    ).run(_transaction_input(artifact_root=invalid_root))

    assert not result.ready
    assert result.setup_trigger == result.setup_failure == "scan_evidence_failed"
    assert result.classification == "artifact_failure"
    assert result.evidence_ref is None
