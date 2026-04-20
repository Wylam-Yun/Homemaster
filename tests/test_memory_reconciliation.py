from __future__ import annotations

from task_brain.domain import (
    Anchor,
    BeliefState,
    Observation,
    ObservationSource,
    ObservedAnchor,
    ObservedObject,
    ParsedTask,
    RuntimeObjectUpdate,
    RuntimeState,
    TargetObject,
    TaskIntent,
    TaskNegativeEvidence,
)
from task_brain.graph import run_task_graph
from task_brain.memory import MemoryStore, reconcile_memory_after_task

_CHECK_MEDICINE_INSTRUCTION = "去桌子那边看看药盒是不是还在。"


def test_successful_observation_updates_object_memory() -> None:
    store = MemoryStore.from_dict(
        {
            "object_memory": [
                {
                    "memory_id": "mem-cup-1",
                    "object_category": "cup",
                    "aliases": ["水杯"],
                    "anchor": {
                        "room_id": "living_room",
                        "anchor_id": "anchor_living_table_1",
                        "anchor_type": "table",
                        "viewpoint_id": "living_viewpoint",
                    },
                    "confidence_level": "medium",
                    "belief_state": "confirmed",
                }
            ]
        }
    )
    runtime_state = RuntimeState(
        current_observation=Observation(
            observation_id="obs-1",
            source=ObservationSource.MOCK_WORLD,
            viewpoint_id="kitchen_viewpoint",
            room_id="kitchen",
            visible_objects=[
                ObservedObject(
                    observation_object_id="kitchen_viewpoint:obj_cup_1",
                    category="cup",
                    aliases=["水杯"],
                    detector_id="det-cup-1",
                    memory_id="mem-cup-1",
                    state_summary="on_table",
                )
            ],
            visible_anchors=[
                ObservedAnchor(
                    room_id="kitchen",
                    anchor_id="anchor_kitchen_table_1",
                    anchor_type="table",
                    viewpoint_id="kitchen_viewpoint",
                )
            ],
        )
    )

    summary = reconcile_memory_after_task(
        parsed_task=_build_task("cup"),
        runtime_state=runtime_state,
        memory_store=store,
        final_status="success",
    )

    refreshed = store.get_object_memory("mem-cup-1")
    assert refreshed is not None
    assert summary["verified"] is True
    assert summary["updated"] == 1
    assert summary["created"] == 0
    assert refreshed.anchor.room_id == "kitchen"
    assert refreshed.anchor.anchor_id == "anchor_kitchen_table_1"
    assert refreshed.belief_state == BeliefState.CONFIRMED


def test_stale_recover_scenario_downgrades_old_candidate_memory() -> None:
    result = run_task_graph(
        scenario="check_medicine_stale_recover",
        instruction=_CHECK_MEDICINE_INSTRUCTION,
    )

    store = result["memory_store"]
    stale_candidate = store.get_object_memory("mem-medicine-1")
    selected_candidate = store.get_object_memory("mem-medicine-2")
    assert stale_candidate is not None
    assert selected_candidate is not None
    assert stale_candidate.belief_state in {BeliefState.STALE, BeliefState.CONTRADICTED}
    assert selected_candidate.belief_state == BeliefState.CONFIRMED

    update_event = _trace_event(result["trace"], "update_memory")
    assert update_event is not None
    assert update_event["updated"] >= 1
    assert update_event["stale"] + update_event["contradicted"] >= 1


def test_unverified_planner_guess_does_not_update_memory() -> None:
    store = MemoryStore.from_dict(
        {
            "object_memory": [
                {
                    "memory_id": "mem-cup-1",
                    "object_category": "cup",
                    "aliases": ["水杯"],
                    "anchor": {
                        "room_id": "kitchen",
                        "anchor_id": "anchor_kitchen_table_1",
                        "anchor_type": "table",
                    },
                    "confidence_level": "high",
                    "belief_state": "confirmed",
                }
            ]
        }
    )
    runtime_state = RuntimeState(selected_candidate_id="mem-cup-1")
    original = store.get_object_memory("mem-cup-1")
    assert original is not None
    original_anchor_id = original.anchor.anchor_id

    summary = reconcile_memory_after_task(
        parsed_task=_build_task("cup"),
        runtime_state=runtime_state,
        memory_store=store,
        final_status="failed",
    )

    refreshed = store.get_object_memory("mem-cup-1")
    assert refreshed is not None
    assert summary["updated"] == 0
    assert summary["created"] == 0
    assert refreshed.anchor.anchor_id == original_anchor_id


def test_task_negative_evidence_not_persisted_as_object_memory() -> None:
    store = MemoryStore.from_dict(
        {
            "object_memory": [
                {
                    "memory_id": "mem-medicine-1",
                    "object_category": "medicine_box",
                    "aliases": ["药盒"],
                    "anchor": {
                        "room_id": "kitchen",
                        "anchor_id": "anchor_kitchen_cabinet_1",
                        "anchor_type": "cabinet",
                    },
                    "confidence_level": "high",
                    "belief_state": "confirmed",
                }
            ]
        }
    )
    runtime_state = RuntimeState(
        task_negative_evidence=[
            TaskNegativeEvidence(
                location_key="kitchen:anchor_kitchen_cabinet_1",
                status="searched_not_found",
                object_category="medicine_box",
            )
        ]
    )
    before = len(store.dump_object_memory())

    reconcile_memory_after_task(
        parsed_task=_build_task("medicine_box"),
        runtime_state=runtime_state,
        memory_store=store,
        final_status="failed",
    )

    after = len(store.dump_object_memory())
    assert before == after


def test_detector_id_is_not_used_as_memory_id_for_new_memory() -> None:
    store = MemoryStore.from_dict({"object_memory": []})
    runtime_state = RuntimeState(
        current_observation=Observation(
            observation_id="obs-2",
            source=ObservationSource.MOCK_WORLD,
            viewpoint_id="kitchen_viewpoint",
            room_id="kitchen",
            visible_objects=[
                ObservedObject(
                    observation_object_id="kitchen_viewpoint:obj_cup_new",
                    category="cup",
                    aliases=["水杯"],
                    detector_id="det-cup-99",
                    memory_id=None,
                )
            ],
            visible_anchors=[
                ObservedAnchor(
                    room_id="kitchen",
                    anchor_id="anchor_kitchen_table_1",
                    anchor_type="table",
                    viewpoint_id="kitchen_viewpoint",
                )
            ],
        )
    )

    summary = reconcile_memory_after_task(
        parsed_task=_build_task("cup"),
        runtime_state=runtime_state,
        memory_store=store,
        final_status="success",
    )

    assert summary["created"] == 1
    created_id = summary["created_memory_ids"][0]
    assert created_id != "det-cup-99"
    assert created_id.startswith("mem-cup-")
    assert store.get_object_memory(created_id) is not None


def test_runtime_object_updates_without_verified_support_do_not_update_long_term_memory() -> None:
    store = MemoryStore.from_dict(
        {
            "object_memory": [
                {
                    "memory_id": "mem-cup-1",
                    "object_category": "cup",
                    "aliases": ["水杯"],
                    "anchor": {
                        "room_id": "kitchen",
                        "anchor_id": "anchor_kitchen_table_1",
                        "anchor_type": "table",
                    },
                    "confidence_level": "high",
                    "belief_state": "confirmed",
                }
            ]
        }
    )
    runtime_state = RuntimeState(
        current_observation=Observation(
            observation_id="obs-3",
            source=ObservationSource.MOCK_WORLD,
            viewpoint_id="living_room_viewpoint",
            room_id="living_room",
            visible_objects=[
                ObservedObject(
                    observation_object_id="living_room_viewpoint:obj_plate_1",
                    category="plate",
                    detector_id="det-plate-1",
                )
            ],
            visible_anchors=[
                ObservedAnchor(
                    room_id="living_room",
                    anchor_id="anchor_living_table_1",
                    anchor_type="table",
                )
            ],
        ),
        runtime_object_updates=[
            RuntimeObjectUpdate(
                object_ref="mem-cup-1",
                source="mock_atomic_executor",
                reason="target_location_changed",
                new_anchor=Anchor(
                    room_id="living_room",
                    anchor_id="anchor_living_table_1",
                    anchor_type="table",
                ),
            )
        ],
    )

    before = store.get_object_memory("mem-cup-1")
    assert before is not None
    before_location = (before.anchor.room_id, before.anchor.anchor_id)

    summary = reconcile_memory_after_task(
        parsed_task=_build_task("cup"),
        runtime_state=runtime_state,
        memory_store=store,
        final_status="success",
    )

    after = store.get_object_memory("mem-cup-1")
    assert after is not None
    assert summary["verified"] is False
    assert summary["updated"] == 0
    assert summary["skipped_runtime_updates"] == 1
    assert (after.anchor.room_id, after.anchor.anchor_id) == before_location


def _build_task(category: str) -> ParsedTask:
    return ParsedTask(
        intent=TaskIntent.CHECK_OBJECT_PRESENCE,
        target_object=TargetObject(category=category, aliases=[]),
    )


def _trace_event(trace: list[dict[str, object]], event_name: str) -> dict[str, object] | None:
    for item in trace:
        if isinstance(item, dict) and item.get("event") == event_name:
            return item
    return None
