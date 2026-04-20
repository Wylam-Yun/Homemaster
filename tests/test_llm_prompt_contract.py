from __future__ import annotations

import json

from task_brain.context import build_task_context
from task_brain.domain import (
    EmbodiedActionProgress,
    FailureAnalysis,
    FailureType,
    HighLevelProgress,
    ParsedTask,
    RuntimeObjectUpdate,
    RuntimeState,
    TargetObject,
    TaskIntent,
    TaskNegativeEvidence,
    TaskRequest,
)
from task_brain.planner import LLMHighLevelPlanner


def test_prompt_includes_runtime_progress_and_failure_context() -> None:
    prompt = LLMHighLevelPlanner._build_prompt(_build_context())

    assert "recent_failure_type" in prompt
    assert "high_level_progress" in prompt
    assert "embodied_action_progress" in prompt
    assert "runtime_object_updates" in prompt
    assert "candidate_exclusion_state" in prompt
    assert "task_negative_evidence" in prompt


def test_prompt_excludes_second_state_source_fields() -> None:
    prompt = LLMHighLevelPlanner._build_prompt(_build_context())

    assert "task_progress" not in prompt
    assert "current_object_changes" not in prompt
    assert "embodied_progress" not in prompt


def test_prompt_declares_allowed_subgoal_types_and_output_schema() -> None:
    prompt = LLMHighLevelPlanner._build_prompt(_build_context())

    assert "allowed_subgoal_types" in prompt
    assert "output_schema" in prompt
    assert "json_only" in prompt
    assert "Do not emit markdown" in prompt


def test_prompt_contains_structured_negative_evidence_and_exclusion_state() -> None:
    payload = _extract_payload(LLMHighLevelPlanner._build_prompt(_build_context()))
    retrieval = payload["retrieval"]

    assert retrieval["task_negative_evidence"][0]["location_key"] == "kitchen:kitchen_table_1"
    assert retrieval["task_negative_evidence"][0]["status"] == "searched_not_found"
    assert retrieval["candidate_exclusion_state"] == {
        "kitchen:kitchen_table_1": "searched_not_found"
    }


def test_prompt_caps_ranked_candidates_to_top_five() -> None:
    ranked_candidates = [
        {
            "memory_id": f"mem-{index}",
            "anchor": {
                "room_id": "kitchen",
                "anchor_id": f"anchor-{index}",
                "anchor_type": "table",
            },
            "score": float(index),
            "reasons": ["seed"],
        }
        for index in range(8)
    ]
    prompt = LLMHighLevelPlanner._build_prompt(_build_context(ranked_candidates=ranked_candidates))
    payload = _extract_payload(prompt)

    assert len(payload["retrieval"]["ranked_candidates"]) == 5
    assert payload["retrieval"]["ranked_candidates"][0]["memory_id"] == "mem-0"


def test_prompt_contains_category_prior_and_episodic_summaries() -> None:
    payload = _extract_payload(LLMHighLevelPlanner._build_prompt(_build_context()))

    assert payload["retrieval"]["category_prior_hits"] == [
        {"anchor_id": "anchor_kitchen_cabinet_1"}
    ]
    assert payload["retrieval"]["recent_episodic_summaries"] == [
        {"episode_id": "ep-1", "summary": "cup seen in kitchen"}
    ]


def test_prompt_payload_is_parseable_json_object() -> None:
    prompt = LLMHighLevelPlanner._build_prompt(_build_context())
    payload = _extract_payload(prompt)

    assert isinstance(payload, dict)
    assert payload["request"]["intent"] == "check_object_presence"
    assert payload["request"]["target_category"] == "medicine_box"


def _build_context(*, ranked_candidates: list[dict[str, object]] | None = None):
    return build_task_context(
        request=TaskRequest(
            source="cli",
            user_id="u-prompt",
            utterance="去桌子那边看看药盒是不是还在。",
            request_id="req-prompt",
        ),
        parsed_task=ParsedTask(
            intent=TaskIntent.CHECK_OBJECT_PRESENCE,
            target_object=TargetObject(category="medicine_box", aliases=["药盒"]),
            requires_navigation=True,
            requires_manipulation=False,
        ),
        runtime_state=RuntimeState(
            retry_budget=1,
            recent_failure_analysis=FailureAnalysis(
                failure_type=FailureType.OBJECT_PRESENCE_FAILURE,
                reason="searched_not_found",
                selected_candidate_id="mem-medicine-1",
            ),
            candidate_exclusion_state={"kitchen:kitchen_table_1": "searched_not_found"},
            task_negative_evidence=[
                TaskNegativeEvidence(
                    task_request_id="req-prompt",
                    location_key="kitchen:kitchen_table_1",
                    status="searched_not_found",
                    object_category="medicine_box",
                )
            ],
            high_level_progress=HighLevelProgress(
                current_subgoal_id="sg-2",
                execution_phase="executing",
                completed_subgoal_ids=["sg-1"],
                pending_subgoal_ids=["sg-3"],
                replan_count=1,
            ),
            embodied_action_progress=EmbodiedActionProgress(
                active_skill_name="mock_atomic_executor.execute",
                current_action_phase="handover",
            ),
            runtime_object_updates=[
                RuntimeObjectUpdate(
                    object_ref="mem-medicine-1",
                    source="execution_evidence",
                    reason="target_location_changed",
                )
            ],
        ),
        ranked_candidates=ranked_candidates
        or [
            {
                "memory_id": "mem-medicine-1",
                "anchor": {
                    "room_id": "kitchen",
                    "anchor_id": "anchor_kitchen_cabinet_1",
                    "anchor_type": "cabinet",
                },
                "score": 8.5,
                "reasons": ["confidence_boost"],
            }
        ],
        category_prior_hits=[{"anchor_id": "anchor_kitchen_cabinet_1"}],
        recent_episodic_summaries=[{"episode_id": "ep-1", "summary": "cup seen in kitchen"}],
    )


def _extract_payload(prompt: str) -> dict[str, object]:
    start = prompt.find("{")
    assert start != -1
    return json.loads(prompt[start:])
