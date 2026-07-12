from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from homemaster.benchmarking.alfworld.env_adapter import AlfworldEnvAdapter
from homemaster.benchmarking.alfworld.execution import (
    AgentPose,
    ExecutionBackend,
    ExecutionBudget,
    ExternalActionResult,
    ExternalRead,
    ManipulationExecutor,
    ManipulationRouter,
    PoseContext,
    PutExecutionRequest,
    SceneObjectIndex,
    classify_put_transition,
    reconcile_target_resolution,
)

PENCIL_ID = "Pencil|-01.57|+00.88|+00.83"
OTHER_PENCIL_ID = "Pencil|+01.00|+00.88|+00.83"
SHELF_ID = "Shelf|-01.96|+00.87|+00.41"
DESK_ID = "Desk|-01.90|+00.00|+00.20"

CURRENT_POSE = AgentPose(
    x=0.0,
    y=0.9010564,
    z=0.0,
    rotation=0.0,
    horizon=0.0,
)
LOCAL_POSE_1 = AgentPose(
    x=0.25,
    y=0.9010564,
    z=0.0,
    rotation=90.0,
    horizon=15.0,
)
LOCAL_POSE_2 = AgentPose(
    x=0.5,
    y=0.9010564,
    z=0.0,
    rotation=90.0,
    horizon=30.0,
)


def _scene_objects() -> list[dict[str, Any]]:
    return [
        {
            "objectId": OTHER_PENCIL_ID,
            "objectType": "Pencil",
            "pickupable": True,
        },
        {
            "objectId": SHELF_ID,
            "objectType": "Shelf",
            "receptacle": True,
        },
        {
            "objectId": PENCIL_ID,
            "objectType": "Pencil",
            "pickupable": True,
        },
    ]


def _external_read(
    *,
    status: str = "ok",
    inventory_object_ids: tuple[str, ...] = (PENCIL_ID,),
    held_object_id: str | None = PENCIL_ID,
    exact_object_present: bool = True,
    object_parent_ids: tuple[str, ...] = (DESK_ID,),
    target_child_ids: tuple[str, ...] = (),
    actual_agent_pose: AgentPose = CURRENT_POSE,
    raw_event_ref: str = "raw/before.json",
    raw_event_hash: str = "before-hash",
    exact_object_is_picked_up: bool | None = True,
) -> ExternalRead:
    return ExternalRead(
        status=status,
        raw_event_ref=raw_event_ref,
        raw_event_hash=raw_event_hash,
        inventory_object_ids=inventory_object_ids,
        held_object_id=held_object_id,
        exact_object_present=exact_object_present,
        object_parent_ids=object_parent_ids,
        target_child_ids=target_child_ids,
        actual_agent_pose=actual_agent_pose,
        goal_summary={"met": 0, "total": 1},
        exact_object_is_picked_up=exact_object_is_picked_up,
    )


BEFORE = _external_read()
UNCHANGED = replace(
    BEFORE,
    raw_event_ref="raw/after-unchanged.json",
    raw_event_hash="after-unchanged-hash",
)
PLACED = _external_read(
    inventory_object_ids=(),
    held_object_id=None,
    object_parent_ids=(DESK_ID, SHELF_ID),
    target_child_ids=(PENCIL_ID,),
    raw_event_ref="raw/after-placed.json",
    raw_event_hash="after-placed-hash",
    exact_object_is_picked_up=False,
)
PARTIALLY_CHANGED = _external_read(
    object_parent_ids=(DESK_ID, SHELF_ID),
    target_child_ids=(PENCIL_ID,),
    raw_event_ref="raw/after-partial.json",
    raw_event_hash="after-partial-hash",
)


def _action(status: str) -> ExternalActionResult:
    return ExternalActionResult(
        status=status,
        raw_event_ref=f"raw/action-{status}.json",
        raw_event_hash=f"action-{status}-hash",
        detail="",
    )


def _pose_context() -> PoseContext:
    return PoseContext.lock(
        context_id="pose-context-1",
        scene_generation=3,
        goal_generation=5,
        source_event_sequence=11,
        source_frame_hash="source-frame-hash",
        anchor_object_id=SHELF_ID,
        current_actual_pose=CURRENT_POSE,
        local_candidates=(LOCAL_POSE_1, LOCAL_POSE_2),
        created_tool_call_id="call-put-1",
    )


def test_scene_object_index_is_stable_and_explicit_instance_is_exact() -> None:
    forward = SceneObjectIndex.from_objects(
        objects=_scene_objects(),
        scene_generation=3,
        snapshot_event_sequence=11,
    )
    reversed_index = SceneObjectIndex.from_objects(
        objects=list(reversed(_scene_objects())),
        scene_generation=3,
        snapshot_event_sequence=11,
    )

    # Canonical instance labels use objectId string ordering. In ASCII/Unicode
    # ordering, the '+' coordinate sorts before '-'.
    assert forward.resolve("pencil 1").object_id == OTHER_PENCIL_ID
    assert forward.resolve("pencil 2").object_id == PENCIL_ID
    assert reversed_index.resolve("pencil 1").object_id == OTHER_PENCIL_ID
    assert reversed_index.resolve("pencil 2").object_id == PENCIL_ID
    assert forward.resolve("pencil").object_id == OTHER_PENCIL_ID
    assert reversed_index.resolve("pencil").object_id == OTHER_PENCIL_ID

    # An explicit missing instance is an exact miss. It must never fall back to
    # the type-level deterministic choice (pencil 1).
    assert forward.resolve("pencil 3") is None
    assert reversed_index.resolve("pencil 3") is None


@pytest.mark.parametrize("resolver_object_id", [None, OTHER_PENCIL_ID])
def test_resolver_disagreement_with_scene_index_is_harness_grounding_failure(
    resolver_object_id: str | None,
) -> None:
    index = SceneObjectIndex.from_objects(
        objects=_scene_objects(),
        scene_generation=3,
        snapshot_event_sequence=11,
    )

    decision = reconcile_target_resolution(
        requested_label="pencil 2",
        scene_index=index,
        resolver_object_id=resolver_object_id,
    )

    assert decision.classification == "harness_grounding_failure"
    assert decision.terminal is True
    assert decision.score_eligible is False
    assert decision.resolved_object_id is None


@pytest.mark.parametrize(
    ("action_result", "after", "classification", "safe_to_retry"),
    [
        pytest.param(_action("success"), PLACED, "success", False, id="success-and-terminal"),
        pytest.param(
            _action("failure"),
            UNCHANGED,
            "candidate_failed",
            True,
            id="explicit-failure-and-full-state-unchanged",
        ),
        pytest.param(
            _action("success"),
            UNCHANGED,
            "execution_state_uncertain",
            False,
            id="success-without-terminal-state",
        ),
        pytest.param(
            _action("failure"),
            PARTIALLY_CHANGED,
            "execution_state_uncertain",
            False,
            id="failure-with-partial-change",
        ),
        pytest.param(
            _action("uncertain"),
            UNCHANGED,
            "execution_state_uncertain",
            False,
            id="exception-timeout-or-missing-return",
        ),
        pytest.param(
            _action("failure"),
            replace(UNCHANGED, status="error"),
            "execution_state_uncertain",
            False,
            id="post-read-error",
        ),
        pytest.param(
            _action("failure"),
            replace(UNCHANGED, status="missing"),
            "execution_state_uncertain",
            False,
            id="post-read-missing",
        ),
        pytest.param(
            _action("failure"),
            replace(UNCHANGED, exact_object_present=False),
            "execution_state_uncertain",
            False,
            id="exact-object-unreadable-or-missing",
        ),
        pytest.param(
            _action("success"),
            replace(PLACED, exact_object_is_picked_up=None),
            "execution_state_uncertain",
            False,
            id="put-success-missing-is-picked-up",
        ),
    ],
)
def test_put_transition_table(
    action_result: ExternalActionResult,
    after: ExternalRead,
    classification: str,
    safe_to_retry: bool,
) -> None:
    transition = classify_put_transition(
        action_result=action_result,
        before=BEFORE,
        after=after,
        held_object_id=PENCIL_ID,
        target_receptacle_id=SHELF_ID,
    )

    assert transition.classification == classification
    assert transition.safe_to_retry is safe_to_retry


class FakeClock:
    def __init__(self) -> None:
        self.value_ms = 0.0

    def __call__(self) -> float:
        return self.value_ms

    def advance(self, elapsed_ms: float) -> None:
        self.value_ms += elapsed_ms


class ScriptedExecutionBackend:
    def __init__(
        self,
        *,
        reads: list[ExternalRead],
        put_results: list[ExternalActionResult],
        move_results: list[ExternalActionResult] | None = None,
        clock: FakeClock | None = None,
        action_elapsed_ms: float = 0.0,
    ) -> None:
        self.reads = list(reads)
        self.put_results = list(put_results)
        self.move_results = list(move_results or [])
        self.clock = clock
        self.action_elapsed_ms = action_elapsed_ms
        self.calls: list[tuple[Any, ...]] = []
        self.read_requests: list[tuple[str, str]] = []

    def read_external_state(
        self,
        *,
        held_object_id: str,
        target_receptacle_id: str,
    ) -> ExternalRead:
        self.read_requests.append((held_object_id, target_receptacle_id))
        if not self.reads:
            raise AssertionError("unexpected external state read")
        return self.reads.pop(0)

    def put_object(
        self,
        *,
        object_id: str,
        receptacle_object_id: str,
    ) -> ExternalActionResult:
        self.calls.append(("put", object_id, receptacle_object_id))
        if self.clock is not None:
            self.clock.advance(self.action_elapsed_ms)
        if not self.put_results:
            raise AssertionError("unexpected PutObject request")
        return self.put_results.pop(0)

    def move_to(self, *, pose: AgentPose) -> ExternalActionResult:
        self.calls.append(("move", pose))
        if self.clock is not None:
            self.clock.advance(self.action_elapsed_ms)
        if not self.move_results:
            raise AssertionError("unexpected move request")
        return self.move_results.pop(0)


def test_execution_backend_protocol_implementations_are_complete() -> None:
    protocol_methods = {
        name
        for name, value in vars(ExecutionBackend).items()
        if not name.startswith("_") and callable(value)
    }

    assert protocol_methods == {"read_external_state", "put_object", "move_to"}
    for implementation in (AlfworldEnvAdapter, ScriptedExecutionBackend):
        missing = sorted(
            method
            for method in protocol_methods
            if not callable(getattr(implementation, method, None))
        )
        assert missing == [], f"{implementation.__name__} is missing {missing}"


def test_put_locks_exact_ids_candidate_hash_and_tries_current_pose_first() -> None:
    moved_unchanged = replace(
        UNCHANGED,
        object_parent_ids=(SHELF_ID,),
        target_child_ids=(PENCIL_ID,),
        actual_agent_pose=LOCAL_POSE_1,
        raw_event_ref="raw/after-move.json",
        raw_event_hash="after-move-hash",
    )
    placed_from_local = replace(PLACED, actual_agent_pose=LOCAL_POSE_1)
    backend = ScriptedExecutionBackend(
        reads=[BEFORE, UNCHANGED, moved_unchanged, placed_from_local],
        put_results=[_action("failure"), _action("success")],
        move_results=[_action("success")],
    )
    context = _pose_context()
    request = PutExecutionRequest(
        tool_call_id="call-put-1",
        held_object_id=PENCIL_ID,
        target_receptacle_id=SHELF_ID,
        pose_context=context,
    )
    executor = ManipulationExecutor(
        backend=backend,
        budget=ExecutionBudget(
            max_pose_candidates=3,
            max_backend_actions=5,
            max_elapsed_ms=1_000,
        ),
        monotonic_ms=FakeClock(),
    )

    result = executor.execute_put(request)

    assert result.success is True
    assert result.classification == "success"
    assert result.locked_candidates_hash == context.candidates_hash
    assert result.attempted_poses == (CURRENT_POSE, LOCAL_POSE_1)
    assert result.pose_candidates_attempted == 2
    assert result.put_attempt_count == 2
    assert result.backend_action_count == 3
    assert backend.calls == [
        ("put", PENCIL_ID, SHELF_ID),
        ("move", LOCAL_POSE_1),
        ("put", PENCIL_ID, SHELF_ID),
    ]
    assert backend.read_requests == [(PENCIL_ID, SHELF_ID)] * 4
    assert LOCAL_POSE_2 not in [call[-1] for call in backend.calls]


def test_put_missing_precondition_is_picked_up_stops_uncertain_without_action() -> None:
    backend = ScriptedExecutionBackend(
        reads=[replace(BEFORE, exact_object_is_picked_up=None)],
        put_results=[],
    )
    result = ManipulationExecutor(
        backend=backend,
        budget=ExecutionBudget(
            max_pose_candidates=3,
            max_backend_actions=5,
            max_elapsed_ms=1_000,
        ),
        monotonic_ms=FakeClock(),
    ).execute_put(
        PutExecutionRequest(
            tool_call_id="call-put-missing-picked-up",
            held_object_id=PENCIL_ID,
            target_receptacle_id=SHELF_ID,
            pose_context=_pose_context(),
        )
    )

    assert result.classification == "execution_state_uncertain"
    assert result.backend_action_count == 0
    assert backend.calls == []
    assert result.trace_events[-1]["classification"] == "execution_state_uncertain"


def test_put_execution_trace_is_replayable_and_ordered_per_attempt() -> None:
    moved_unchanged = replace(
        UNCHANGED,
        actual_agent_pose=LOCAL_POSE_1,
        raw_event_ref="raw/after-move.json",
        raw_event_hash="after-move-hash",
    )
    placed_from_local = replace(
        PLACED,
        actual_agent_pose=LOCAL_POSE_1,
        raw_event_ref="raw/after-placed-local.json",
        raw_event_hash="after-placed-local-hash",
    )
    backend = ScriptedExecutionBackend(
        reads=[BEFORE, UNCHANGED, moved_unchanged, placed_from_local],
        put_results=[_action("failure"), _action("success")],
        move_results=[_action("success")],
    )
    context = _pose_context()
    request = PutExecutionRequest(
        tool_call_id="call-put-trace",
        held_object_id=PENCIL_ID,
        target_receptacle_id=SHELF_ID,
        pose_context=context,
    )
    result = ManipulationExecutor(
        backend=backend,
        budget=ExecutionBudget(
            max_pose_candidates=3,
            max_backend_actions=5,
            max_elapsed_ms=1_000,
        ),
        monotonic_ms=FakeClock(),
    ).execute_put(request)

    events = list(result.trace_events)
    assert events[0]["event"] == "context_created"
    assert events[0]["context_id"] == context.context_id
    assert events[0]["locked_candidates_hash"] == context.candidates_hash
    assert events[0]["locked_candidates"] == [
        {
            "x": pose.x,
            "y": pose.y,
            "z": pose.z,
            "rotation": pose.rotation,
            "horizon": pose.horizon,
        }
        for pose in context.locked_candidates
    ]
    assert events[0]["held_object_id"] == PENCIL_ID
    assert events[0]["target_receptacle_id"] == SHELF_ID

    attempt_events = [event for event in events if event.get("attempt_id")]
    attempt_ids = list(dict.fromkeys(event["attempt_id"] for event in attempt_events))
    assert len(attempt_ids) == 2
    first = [event["event"] for event in attempt_events if event["attempt_id"] == attempt_ids[0]]
    second = [event["event"] for event in attempt_events if event["attempt_id"] == attempt_ids[1]]
    assert first == [
        "attempt_started",
        "put_started",
        "put_result",
        "state_read_started",
        "state_read_result",
    ]
    assert second == [
        "attempt_started",
        "move_started",
        "move_result",
        "state_read_started",
        "state_read_result",
        "put_started",
        "put_result",
        "state_read_started",
        "state_read_result",
    ]
    assert all(event["locked_candidates_hash"] == context.candidates_hash for event in events)

    put_results = [event for event in events if event["event"] == "put_result"]
    assert [(event["raw_event_ref"], event["raw_event_hash"]) for event in put_results] == [
        ("raw/action-failure.json", "action-failure-hash"),
        ("raw/action-success.json", "action-success-hash"),
    ]
    assert all(event["put_elapsed_ms"] >= 0 for event in put_results)
    assert events[-2]["event"] == "context_invalidated"
    terminal = events[-1]
    assert terminal["event"] == "execution_terminal"
    assert terminal["classification"] == "success"
    assert terminal["budget_limit"] == {
        "candidates": 3,
        "backend_actions": 5,
        "elapsed_ms": 1_000,
    }
    assert terminal["budget_used"] == {
        "candidates": 2,
        "backend_actions": 3,
        "put_attempts": 2,
        "elapsed_ms": 0.0,
    }
    assert terminal["budget_stop_reason"] is None


@pytest.mark.parametrize(
    ("budget", "action_elapsed_ms", "expected_stop_reason"),
    [
        pytest.param(
            ExecutionBudget(
                max_pose_candidates=1,
                max_backend_actions=100,
                max_elapsed_ms=10_000,
            ),
            0.0,
            "max_pose_candidates",
            id="candidate-budget",
        ),
        pytest.param(
            ExecutionBudget(
                max_pose_candidates=100,
                max_backend_actions=1,
                max_elapsed_ms=10_000,
            ),
            0.0,
            "max_backend_actions",
            id="backend-action-budget",
        ),
        pytest.param(
            ExecutionBudget(
                max_pose_candidates=100,
                max_backend_actions=100,
                max_elapsed_ms=1,
            ),
            2.0,
            "max_elapsed_ms",
            id="wall-clock-budget",
        ),
    ],
)
def test_put_budget_stops_without_an_n_plus_one_backend_request(
    budget: ExecutionBudget,
    action_elapsed_ms: float,
    expected_stop_reason: str,
) -> None:
    clock = FakeClock()
    backend = ScriptedExecutionBackend(
        reads=[BEFORE, UNCHANGED],
        put_results=[_action("failure")],
        move_results=[],
        clock=clock,
        action_elapsed_ms=action_elapsed_ms,
    )
    executor = ManipulationExecutor(
        backend=backend,
        budget=budget,
        monotonic_ms=clock,
    )
    request = PutExecutionRequest(
        tool_call_id="call-put-budget",
        held_object_id=PENCIL_ID,
        target_receptacle_id=SHELF_ID,
        pose_context=_pose_context(),
    )

    result = executor.execute_put(request)

    assert result.success is False
    assert result.classification == "harness_operation_failure"
    assert result.budget_stop_reason == expected_stop_reason
    assert result.backend_action_count == 1
    assert result.put_attempt_count == 1
    assert result.pose_candidates_attempted == 1
    assert backend.calls == [("put", PENCIL_ID, SHELF_ID)]


class PutExecutorSpy:
    def __init__(self) -> None:
        self.requests: list[Any] = []

    def execute_put(self, request: Any) -> object:
        self.requests.append(request)
        return object()


class LegacyExecutorSpy:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.result = object()

    def execute(self, *, action: str, arguments: dict[str, Any]) -> object:
        self.calls.append((action, arguments))
        return self.result


@pytest.mark.parametrize(
    "action",
    ["take", "open", "close", "use", "heat", "cool", "clean", "slice"],
)
def test_legacy_actions_do_not_enter_the_put_executor(action: str) -> None:
    put_executor = PutExecutorSpy()
    legacy_executor = LegacyExecutorSpy()
    router = ManipulationRouter(
        put_executor=put_executor,
        legacy_executor=legacy_executor,
    )
    arguments = {"object": "mug 1"}

    result = router.execute(action=action, arguments=arguments)

    assert result is legacy_executor.result
    assert legacy_executor.calls == [(action, arguments)]
    assert put_executor.requests == []
