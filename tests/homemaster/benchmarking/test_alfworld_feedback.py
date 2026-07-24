from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from homemaster.benchmarking.alfworld.execution import (
    OracleExecutionContext,
    OracleManipulationExecutor,
    SceneObjectIndex,
)
from homemaster.benchmarking.alfworld.gateway import (
    ExternalActionRequest,
    ExternalEventRead,
    GatewayActionResult,
)
from homemaster.benchmarking.alfworld.object_view import CurrentObjectView
from homemaster.benchmarking.alfworld.pose_snapshot import OraclePose

DRAWER = "Drawer|+00.00|+00.50|+00.00"
PENCIL = "Pencil|+00.10|+00.80|+00.10"
POSE = OraclePose(1.0, 0.9, 2.0, 90.0, 15.0)
WORLD = "1" * 64


def _event(*, is_open: bool, held: bool = True) -> Any:
    np = pytest.importorskip("numpy")
    return SimpleNamespace(
        frame=np.zeros((3, 3, 3), dtype=np.uint8),
        instance_detections2D={DRAWER: [0, 0, 2, 2]},
        metadata={
            "inventoryObjects": ([{"objectId": PENCIL}] if held else []),
            "objects": [
                {
                    "objectId": DRAWER,
                    "objectType": "Drawer",
                    "visible": True,
                    "isPickedUp": False,
                    "parentReceptacles": [],
                    "receptacleObjectIds": ([] if held else [PENCIL]),
                    "pickupable": False,
                    "receptacle": True,
                    "openable": True,
                    "isOpen": is_open,
                    "toggleable": False,
                    "isToggled": False,
                    "sliceable": False,
                    "isSliced": False,
                    "isDirty": False,
                },
                {
                    "objectId": PENCIL,
                    "objectType": "Pencil",
                    "visible": False,
                    "isPickedUp": held,
                    "parentReceptacles": ([] if held else [DRAWER]),
                    "receptacleObjectIds": [],
                    "pickupable": True,
                    "receptacle": False,
                    "openable": False,
                    "isOpen": False,
                    "toggleable": False,
                    "isToggled": False,
                    "sliceable": False,
                    "isSliced": False,
                    "isDirty": False,
                },
            ],
        },
    )


def _external(*, action: str, success: bool = True) -> ExternalEventRead:
    return ExternalEventRead(
        status="ok",
        returned_action=action,
        action_success=success,
        pose=POSE,
        world_sha256=WORLD,
        visibility_sha256="2" * 64,
        frame_sha256="3" * 64,
        objects=(),
        reachable_payload=None,
        strict_visible_exact_ids=(DRAWER,),
        bbox_areas=((DRAWER, 4.0),),
        raw_event_ref=f"events/{action}.json",
        raw_event_sha256="4" * 64,
    )


def _context() -> OracleExecutionContext:
    return OracleExecutionContext(
        scene_generation=2,
        goal_generation=3,
        source_event_sequence=5,
        current_event_sequence=6,
        requested_target_id=DRAWER,
        navigation_anchor_id=DRAWER,
        oracle_snapshot_hash="5" * 64,
        oracle_pose_hash="6" * 64,
        anchor_state_hash="7" * 64,
        actual_pose=POSE,
        final_event_hash="8" * 64,
        final_event_ref="events/navigation.json",
    )


class Gateway:
    def __init__(self, holder: dict[str, Any], *, fail: bool = False) -> None:
        self.holder = holder
        self.fail = fail
        self.calls: list[dict[str, Any]] = []

    def execute_manipulation(self, payload: dict[str, Any]) -> GatewayActionResult:
        self.calls.append(payload)
        if not self.fail and payload["action"] == "OpenObject":
            self.holder["event"] = _event(is_open=True)
        request = ExternalActionRequest(phase="manipulation", sequence=1, payload=payload)
        return GatewayActionResult(
            request=request,
            event=_external(action=str(payload["action"]), success=not self.fail),
            duration_ms=1.0,
        )


def _executor(
    *,
    is_open: bool,
    context: OracleExecutionContext | None,
    fail: bool = False,
) -> tuple[OracleManipulationExecutor, Gateway]:
    raw = _event(is_open=is_open)
    holder = {"event": raw}
    gateway = Gateway(holder, fail=fail)
    index = SceneObjectIndex.from_objects(
        objects=raw.metadata["objects"],
        scene_generation=2,
        snapshot_event_sequence=0,
    )
    executor = OracleManipulationExecutor(
        scene_index=index,
        object_view=CurrentObjectView(
            event=raw,
            event_sequence=6,
        ),
        current_event=_external(action="TeleportFull"),
        raw_event=raw,
        raw_event_reader=lambda: holder["event"],
        context=context,
        gateway=gateway,  # type: ignore[arg-type]
        scene_generation=2,
        goal_generation=3,
    )
    return executor, gateway


def test_missing_context_returns_navigation_required_without_gateway_call() -> None:
    executor, gateway = _executor(is_open=False, context=None)

    result = executor.execute("open", object_label=None, target_label="drawer 1")

    assert result.feedback.error == "navigation_required"
    assert result.feedback.terminal is False
    assert result.backend_action_count == 0
    assert gateway.calls == []


def test_closed_put_returns_typed_target_closed_and_preserves_inventory() -> None:
    executor, gateway = _executor(is_open=False, context=_context())

    result = executor.execute(
        "put",
        object_label="pencil 1",
        target_label="drawer 1",
    )

    assert result.feedback.error == "target_closed"
    assert result.feedback.inventory == ("pencil 1",)
    assert result.feedback.object_state == "held"
    assert result.feedback.target_state == "closed"
    assert result.backend_action_count == 0
    assert gateway.calls == []


def test_open_sends_once_verifies_state_and_rebases_active_context() -> None:
    executor, gateway = _executor(is_open=False, context=_context())

    result = executor.execute("open", object_label=None, target_label="drawer 1")

    assert result.feedback.success is True
    assert result.feedback.target_state == "open"
    assert result.feedback.state_changed is True
    assert result.backend_action_count == 1
    assert [item["action"] for item in gateway.calls] == ["OpenObject"]
    assert result.context is not None
    assert result.context.state == "active"
    assert result.context.current_event_sequence == 7


def test_external_failure_with_unchanged_state_is_harness_operation_failure() -> None:
    executor, gateway = _executor(is_open=False, context=_context(), fail=True)

    result = executor.execute("open", object_label=None, target_label="drawer 1")

    assert result.feedback.error == "harness_operation_failure"
    assert result.feedback.classification == "harness_operation_failure"
    assert result.feedback.terminal is True
    assert result.backend_action_count == 1
    assert len(gateway.calls) == 1


def test_slice_remains_explicit_unclassified_terminal_until_gate_contract_exists() -> None:
    executor, gateway = _executor(is_open=False, context=_context())

    result = executor.execute("slice", object_label="drawer 1", target_label=None)

    assert result.feedback.error == "unclassified_execution_failure"
    assert result.feedback.terminal is True
    assert result.backend_action_count == 0
    assert gateway.calls == []
