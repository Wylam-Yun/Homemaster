from __future__ import annotations

from homemaster.benchmarking.alfworld.gateway import (
    CleanupResult,
    ExternalEventRead,
    OracleActionGateway,
)
from homemaster.benchmarking.alfworld.pose_snapshot import (
    OraclePose,
    ScanPoseProvenance,
    ScanPoseStep,
)


def _event(action: str) -> ExternalEventRead:
    return ExternalEventRead(
        status="ok",
        returned_action=action,
        action_success=True,
        pose=OraclePose(0, 0.9, 0, 0, 0),
        world_sha256="1" * 64,
        visibility_sha256="2" * 64,
        frame_sha256="3" * 64,
        objects=None,
        reachable_payload=(b'[{"x":0,"y":0,"z":0}]' if action == "GetReachablePositions" else None),
        strict_visible_exact_ids=(),
        bbox_areas=(),
        raw_event_ref=f"events/{action}.json",
        raw_event_sha256="4" * 64,
    )


class ScriptedBackend:
    def __init__(self) -> None:
        self.requests = []

    def capture_event(self) -> ExternalEventRead:
        return _event("Reset")

    def send(self, request):
        self.requests.append(request)
        return _event(str(request.payload["action"]))

    def close(self) -> CleanupResult:
        return CleanupResult(status="succeeded", evidence_ref="cleanup.json")


def test_gateway_is_the_ordered_setup_sender_and_hashes_full_requests() -> None:
    backend = ScriptedBackend()
    gateway = OracleActionGateway(backend=backend, monotonic_ms=lambda: 10.0)
    pose = OraclePose(1, 0.9, 2, 90, 30)
    step = ScanPoseStep(
        index=1,
        pose=pose,
        send_teleport=True,
        provenances=(
            ScanPoseProvenance(
                exact_object_id="Mug|1",
                source_kind="geometry",
                source_record_sha256="5" * 64,
            ),
        ),
    )

    slow = gateway.execute_setup_time_control(0.01)
    gateway.execute_setup_query()
    gateway.execute_setup_teleport(step)
    gateway.execute_restore(pose)
    normal = gateway.execute_setup_time_control(1.0)

    assert [request.sequence for request in backend.requests] == [1, 2, 3, 4, 5]
    assert [request.phase for request in backend.requests] == [
        "setup_time_control",
        "setup_query",
        "setup_scan",
        "setup_pose_restore",
        "setup_time_control",
    ]
    assert [request.payload["action"] for request in backend.requests] == [
        "ChangeTimeScale",
        "GetReachablePositions",
        "TeleportFull",
        "TeleportFull",
        "ChangeTimeScale",
    ]
    assert slow.request.request_sha256 != normal.request.request_sha256
    assert tuple(gateway.ledger)[-1] == normal
