from __future__ import annotations

import hashlib
from types import SimpleNamespace
from typing import Any

import pytest

from homemaster.benchmarking.alfworld.execution import (
    NavigationAnchorResolver,
    OracleNavigationExecutor,
    SceneObjectIndex,
)
from homemaster.benchmarking.alfworld.gateway import (
    ExternalActionRequest,
    ExternalEventRead,
    GatewayActionResult,
)
from homemaster.benchmarking.alfworld.model_view import (
    CommittedModelView,
    VisibleObjectView,
)
from homemaster.benchmarking.alfworld.pose_snapshot import (
    OraclePose,
    OraclePoseLookup,
    SceneObjectScanInput,
)

MUG_1 = "Mug|+00.10|+00.90|+00.20"
MUG_2 = "Mug|+00.20|+00.90|+00.20"
DESK = "Desk|+00.00|+00.00|+00.00"
SCENE = "1" * 64
SNAPSHOT = "2" * 64
MUG_1_FRESH = "3" * 64
MUG_2_FRESH = "4" * 64
DESK_FRESH = "5" * 64
POSE_HASH = "6" * 64
WORLD = "7" * 64
RAW = "8" * 64
POSE = OraclePose(1.0, 0.9, 2.0, 90.0, 15.0)
START_POSE = OraclePose(0.0, 0.9, 0.0, 0.0, 0.0)


def _scan_objects(*, held: str | None = None) -> tuple[SceneObjectScanInput, ...]:
    return (
        SceneObjectScanInput(
            exact_object_id=MUG_1,
            object_type="Mug",
            position=(0.1, 0.9, 0.2),
            parent_receptacle_ids=(),
            receptacle_object_ids=(),
            is_picked_up=held == MUG_1,
            closed_ancestor_exact_ids=(),
            pose_freshness_sha256=MUG_1_FRESH,
        ),
        SceneObjectScanInput(
            exact_object_id=MUG_2,
            object_type="Mug",
            position=(0.2, 0.9, 0.2),
            parent_receptacle_ids=(DESK,),
            receptacle_object_ids=(),
            is_picked_up=held == MUG_2,
            closed_ancestor_exact_ids=(),
            pose_freshness_sha256=MUG_2_FRESH,
        ),
        SceneObjectScanInput(
            exact_object_id=DESK,
            object_type="Desk",
            position=(0.0, 0.0, 0.0),
            parent_receptacle_ids=(),
            receptacle_object_ids=(MUG_2,),
            is_picked_up=False,
            closed_ancestor_exact_ids=(),
            pose_freshness_sha256=DESK_FRESH,
        ),
    )


def _raw_event(*, visible: tuple[str, ...]) -> Any:
    np = pytest.importorskip("numpy")
    frame = np.zeros((3, 3, 3), dtype=np.uint8)
    objects = [
        {"objectId": MUG_1, "objectType": "Mug", "visible": MUG_1 in visible},
        {"objectId": MUG_2, "objectType": "Mug", "visible": MUG_2 in visible},
        {"objectId": DESK, "objectType": "Desk", "visible": DESK in visible},
    ]
    return SimpleNamespace(
        frame=frame,
        metadata={"objects": objects},
        instance_detections2D={item: [0, 0, 2, 2] for item in visible},
    )


def _current_event(*, visible: tuple[str, ...], held: str | None = None) -> ExternalEventRead:
    return ExternalEventRead(
        status="ok",
        returned_action="ChangeTimeScale",
        action_success=True,
        pose=START_POSE,
        world_sha256=WORLD,
        visibility_sha256="9" * 64,
        frame_sha256="a" * 64,
        objects=_scan_objects(held=held),
        reachable_payload=None,
        strict_visible_exact_ids=tuple(sorted(visible)),
        bbox_areas=tuple(sorted((item, 4.0) for item in visible)),
        raw_event_ref="events/current.json",
        raw_event_sha256=RAW,
    )


def _view(*, visible: tuple[str, ...], committed: bool = True) -> VisibleObjectView:
    event = _raw_event(visible=visible)
    pixel_hash = hashlib.sha256(event.frame.tobytes()).hexdigest()
    model_view = None
    if committed:
        model_view = CommittedModelView(
            model_attempt_id="attempt-1",
            request_sha256="b" * 64,
            frame_binding_id="frame-1",
            frame_content_sha256="c" * 64,
            frame_pixel_sha256=pixel_hash,
            event_sequence=12,
        )
    return VisibleObjectView(event=event, event_sequence=12, committed_view=model_view)


def _lookup(status: str = "ok", *, anchor: str = MUG_1) -> OraclePoseLookup:
    freshness = {MUG_1: MUG_1_FRESH, MUG_2: MUG_2_FRESH, DESK: DESK_FRESH}[anchor]
    return OraclePoseLookup(
        status=status,
        scene_generation=3,
        scene_reset_fingerprint=SCENE,
        snapshot_sha256=SNAPSHOT,
        pose=POSE if status == "ok" else None,
        pose_sha256=POSE_HASH if status == "ok" else None,
        pose_freshness_sha256=freshness,
        source_kind="geometry" if status == "ok" else "none",
        evidence_ref=None,
    )


class PoseStoreSpy:
    def __init__(self, lookups: dict[str, OraclePoseLookup]) -> None:
        self.lookups = lookups
        self.calls: list[str] = []

    def get_pose(self, **kwargs: Any) -> OraclePoseLookup:
        anchor = kwargs["exact_anchor_id"]
        self.calls.append(anchor)
        return self.lookups[anchor]


class ParentResolverSpy(NavigationAnchorResolver):
    def __init__(self) -> None:
        self.calls: list[str] = []

    def resolve(self, **kwargs: Any):
        self.calls.append(kwargs["target"].exact_object_id)
        return super().resolve(**kwargs)


class GatewaySpy:
    def __init__(self, *, visible_after: tuple[str, ...] = (MUG_1,)) -> None:
        self.navigation_calls: list[dict[str, Any]] = []
        self.visible_after = visible_after

    def execute_navigation(self, payload: dict[str, Any]) -> GatewayActionResult:
        self.navigation_calls.append(payload)
        request = ExternalActionRequest(phase="navigation", sequence=1, payload=payload)
        event = ExternalEventRead(
            status="ok",
            returned_action="TeleportFull",
            action_success=True,
            pose=POSE,
            world_sha256=WORLD,
            visibility_sha256="d" * 64,
            frame_sha256="e" * 64,
            objects=_scan_objects(),
            reachable_payload=None,
            strict_visible_exact_ids=tuple(sorted(self.visible_after)),
            bbox_areas=tuple(sorted((item, 4.0) for item in self.visible_after)),
            raw_event_ref="events/navigation.json",
            raw_event_sha256="f" * 64,
        )
        return GatewayActionResult(request=request, event=event, duration_ms=1.0)


def _executor(
    *,
    visible: tuple[str, ...],
    store: PoseStoreSpy,
    parent: ParentResolverSpy,
    gateway: GatewaySpy,
    committed: bool = True,
    held: str | None = None,
) -> OracleNavigationExecutor:
    return OracleNavigationExecutor(
        scene_index=SceneObjectIndex.from_objects(
            objects=_raw_event(visible=visible).metadata["objects"],
            scene_generation=3,
            snapshot_event_sequence=0,
        ),
        public_object_types=("Desk", "Mug"),
        visible_object_view=_view(visible=visible, committed=committed),
        current_event=_current_event(visible=visible, held=held),
        pose_store=store,
        parent_resolver=parent,
        gateway=gateway,
    )


def _run(executor: OracleNavigationExecutor, label: str = "mug"):
    return executor.execute(
        label,
        scene_generation=3,
        goal_generation=2,
        scene_reset_fingerprint=SCENE,
        snapshot_sha256=SNAPSHOT,
    )


@pytest.mark.parametrize("direct_status", ["ok", "unobserved"])
def test_invisible_target_stops_before_snapshot_parent_and_gateway(direct_status: str) -> None:
    store = PoseStoreSpy({MUG_1: _lookup(direct_status)})
    parent = ParentResolverSpy()
    gateway = GatewaySpy()

    result = _run(
        _executor(visible=(), store=store, parent=parent, gateway=gateway)
    )

    assert result.error == "target_not_visible"
    assert result.terminal is False
    assert store.calls == []
    assert parent.calls == []
    assert gateway.navigation_calls == []
    assert [item["event"] for item in result.trace_events] == [
        "visibility_gate_started",
        "visibility_gate_result",
    ]


def test_missing_committed_frame_is_terminal_uncertainty_with_zero_downstream_calls() -> None:
    store = PoseStoreSpy({MUG_1: _lookup()})
    parent = ParentResolverSpy()
    gateway = GatewaySpy()

    result = _run(
        _executor(
            visible=(MUG_1,),
            store=store,
            parent=parent,
            gateway=gateway,
            committed=False,
        )
    )

    assert result.error == "execution_state_uncertain"
    assert result.terminal is True
    assert store.calls == []
    assert parent.calls == []
    assert gateway.navigation_calls == []


def test_generic_selects_first_current_visible_in_frozen_full_set_order() -> None:
    store = PoseStoreSpy({MUG_2: _lookup(anchor=MUG_2)})
    parent = ParentResolverSpy()
    gateway = GatewaySpy(visible_after=(MUG_2,))

    result = _run(
        _executor(visible=(MUG_2,), store=store, parent=parent, gateway=gateway)
    )

    assert result.success is True
    assert result.target_id == MUG_2
    assert store.calls == [MUG_2]
    assert len(gateway.navigation_calls) == 1


def test_explicit_invisible_ordinal_does_not_fallback_to_visible_peer() -> None:
    store = PoseStoreSpy({MUG_1: _lookup(), MUG_2: _lookup(anchor=MUG_2)})
    parent = ParentResolverSpy()
    gateway = GatewaySpy()

    result = _run(
        _executor(visible=(MUG_2,), store=store, parent=parent, gateway=gateway),
        "mug 1",
    )

    assert result.error == "target_not_visible"
    assert store.calls == []
    assert gateway.navigation_calls == []


def test_inventory_match_returns_object_already_held_without_pose_lookup() -> None:
    store = PoseStoreSpy({MUG_1: _lookup()})
    parent = ParentResolverSpy()
    gateway = GatewaySpy()

    result = _run(
        _executor(
            visible=(),
            store=store,
            parent=parent,
            gateway=gateway,
            held=MUG_1,
        ),
        "mug 1",
    )

    assert result.error == "object_already_held"
    assert result.terminal is False
    assert store.calls == []


@pytest.mark.parametrize(
    ("status", "error"),
    [
        ("coverage_miss", "oracle_pose_missing"),
        ("malformed", "oracle_pose_malformed"),
        ("stale", "execution_state_uncertain"),
        ("error", "execution_state_uncertain"),
    ],
)
def test_direct_lookup_failures_do_not_try_parent_or_move(status: str, error: str) -> None:
    store = PoseStoreSpy({MUG_1: _lookup(status)})
    parent = ParentResolverSpy()
    gateway = GatewaySpy()

    result = _run(
        _executor(visible=(MUG_1,), store=store, parent=parent, gateway=gateway)
    )

    assert result.error == error
    assert parent.calls == []
    assert gateway.navigation_calls == []


def test_unobserved_visible_target_uses_one_reciprocal_parent_pose() -> None:
    store = PoseStoreSpy(
        {
            MUG_2: _lookup("unobserved", anchor=MUG_2),
            DESK: _lookup("ok", anchor=DESK),
        }
    )
    parent = ParentResolverSpy()
    gateway = GatewaySpy(visible_after=(MUG_2,))

    result = _run(
        _executor(visible=(MUG_2,), store=store, parent=parent, gateway=gateway),
        "mug 2",
    )

    assert result.success is True
    assert result.anchor_id == DESK
    assert store.calls == [MUG_2, DESK]
    assert parent.calls == [MUG_2]
    assert len(gateway.navigation_calls) == 1


def test_success_sends_exactly_one_pose_and_creates_active_context() -> None:
    store = PoseStoreSpy({MUG_1: _lookup()})
    parent = ParentResolverSpy()
    gateway = GatewaySpy()

    result = _run(
        _executor(visible=(MUG_1,), store=store, parent=parent, gateway=gateway)
    )

    assert result.success is True
    assert result.backend_action_count == 1
    assert len(gateway.navigation_calls) == 1
    assert gateway.navigation_calls[0] == {
        "action": "TeleportFull",
        "x": POSE.x,
        "y": POSE.y,
        "z": POSE.z,
        "rotateOnTeleport": True,
        "rotation": POSE.rotation,
        "horizon": POSE.horizon,
    }
    assert result.context is not None
    assert result.context.state == "active"
    assert result.context.requested_target_id == MUG_1


def test_successful_move_with_final_target_invisible_is_terminal() -> None:
    store = PoseStoreSpy({MUG_1: _lookup()})
    parent = ParentResolverSpy()
    gateway = GatewaySpy(visible_after=())

    result = _run(
        _executor(visible=(MUG_1,), store=store, parent=parent, gateway=gateway)
    )

    assert result.error == "oracle_target_not_visible"
    assert result.terminal is True
    assert result.backend_action_count == 1
    assert len(gateway.navigation_calls) == 1
