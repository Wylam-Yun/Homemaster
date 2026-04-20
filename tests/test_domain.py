from __future__ import annotations

import pytest
from pydantic import ValidationError

from task_brain.domain import (
    Anchor,
    CapabilitySpec,
    EmbodiedActionProgress,
    FailureType,
    HighLevelProgress,
    ObjectMemory,
    Observation,
    ObservationSource,
    ObservedObject,
    Predicate,
    RuntimeObjectUpdate,
    RuntimeState,
    Subgoal,
    SubgoalType,
    TaskRequest,
)


def test_predicate_round_trip() -> None:
    raw = ["visible_category", "medicine_box"]
    predicate = Predicate.from_list(raw)
    assert predicate.name == "visible_category"
    assert predicate.args == ["medicine_box"]
    assert predicate.to_list() == raw


def test_task_request_preserves_source_user_and_utterance() -> None:
    request = TaskRequest(
        source="cli",
        user_id="user-123",
        utterance="帮我看看药盒还在不在",
    )
    assert request.source == "cli"
    assert request.user_id == "user-123"
    assert request.utterance == "帮我看看药盒还在不在"


def test_subgoal_requires_non_empty_success_conditions() -> None:
    with pytest.raises(ValidationError):
        Subgoal(
            subgoal_id="sg-1",
            subgoal_type=SubgoalType.OBSERVE,
            success_conditions=[],
        )


def test_observed_object_detector_id_and_memory_id_must_differ() -> None:
    with pytest.raises(ValidationError):
        ObservedObject(
            observation_object_id="obs-1",
            category="cup",
            detector_id="id-1",
            memory_id="id-1",
        )


def test_runtime_state_exposes_required_task_scoped_fields() -> None:
    observation = Observation(
        observation_id="obs-001",
        source=ObservationSource.MOCK_WORLD,
        viewpoint_id="kitchen_table_viewpoint",
        room_id="kitchen",
    )
    state = RuntimeState(
        current_observation=observation,
        selected_candidate_id="cand-1",
        selected_object_id="obj-1",
        retry_budget=1,
        task_negative_evidence=[],
        candidate_exclusion_state={"kitchen/table": "searched_not_found"},
    )

    assert state.current_observation is not None
    assert state.selected_candidate_id == "cand-1"
    assert state.selected_object_id == "obj-1"
    assert state.retry_budget == 1
    assert state.recent_failure_analysis is None
    assert state.task_negative_evidence == []
    assert state.candidate_exclusion_state == {"kitchen/table": "searched_not_found"}


def test_capability_spec_requires_full_contract_fields() -> None:
    with pytest.raises(ValidationError):
        CapabilitySpec(
            name="mock_perception.observe",
            input_schema={"type": "object"},
            failure_modes=["timeout"],
            timeout_s=3.0,
            returns_evidence=True,
        )

    with pytest.raises(ValidationError):
        CapabilitySpec(
            name="mock_perception.observe",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            failure_modes=["timeout"],
            timeout_s=3.0,
        )


def test_failure_type_contains_four_phase_a_failures() -> None:
    values = {item.value for item in FailureType}
    assert values == {
        "navigation_failure",
        "object_presence_failure",
        "manipulation_failure",
        "final_task_failure",
    }


def test_object_memory_uses_structured_anchor_and_long_term_memory_id() -> None:
    anchor = Anchor(
        room_id="kitchen",
        anchor_id="kitchen_table_2",
        anchor_type="table",
        viewpoint_id="kitchen_table_viewpoint",
        display_text="厨房餐桌",
    )
    memory = ObjectMemory(
        memory_id="mem-cup-001",
        object_category="cup",
        anchor=anchor,
    )

    assert memory.memory_id == "mem-cup-001"
    assert memory.anchor.anchor_id == "kitchen_table_2"


def test_runtime_state_can_hold_high_level_progress() -> None:
    state = RuntimeState(
        high_level_progress=HighLevelProgress(
            current_subgoal_id="sg-2",
            current_subgoal_type=SubgoalType.OBSERVE,
            completed_subgoal_ids=["sg-1"],
            pending_subgoal_ids=["sg-3"],
            execution_phase="executing",
            replan_count=1,
        )
    )

    assert state.high_level_progress is not None
    assert state.high_level_progress.current_subgoal_id == "sg-2"
    assert state.high_level_progress.current_subgoal_type == SubgoalType.OBSERVE
    assert state.high_level_progress.replan_count == 1


def test_runtime_state_can_hold_embodied_action_progress() -> None:
    progress = EmbodiedActionProgress(
        active_skill_name="mock_atomic_executor.execute",
        current_action_phase="grasping",
        completed_action_phases=["approach_target"],
        pending_action_phases=["lift_target"],
        local_world_state_flags={"holding_target": True},
    )
    state = RuntimeState(embodied_action_progress=progress)

    assert state.embodied_action_progress is not None
    assert state.embodied_action_progress.current_action_phase == "grasping"
    assert state.embodied_action_progress.local_world_state_flags == {
        "container_opened": False,
        "holding_target": True,
        "target_dropped": False,
        "target_location_changed": False,
    }

    with pytest.raises(ValidationError):
        EmbodiedActionProgress(local_world_state_flags={"unknown_flag": True})


def test_runtime_state_can_hold_runtime_object_updates() -> None:
    update = RuntimeObjectUpdate(
        object_ref="mem-cup-001",
        new_anchor=Anchor(
            room_id="kitchen",
            anchor_id="kitchen_floor_1",
            anchor_type="floor",
            viewpoint_id="kitchen_floor_viewpoint",
            display_text="厨房地面",
        ),
        source="execution_evidence",
        reason="target_dropped",
    )
    state = RuntimeState(runtime_object_updates=[update])

    assert len(state.runtime_object_updates) == 1
    assert state.runtime_object_updates[0].object_ref == "mem-cup-001"
    assert state.runtime_object_updates[0].reason == "target_dropped"


def test_runtime_progress_is_not_long_term_memory() -> None:
    long_term_memory_fields = set(ObjectMemory.model_fields)
    assert "high_level_progress" not in long_term_memory_fields
    assert "embodied_action_progress" not in long_term_memory_fields
    assert "runtime_object_updates" not in long_term_memory_fields
