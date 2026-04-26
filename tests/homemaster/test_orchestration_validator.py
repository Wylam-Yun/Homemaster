from __future__ import annotations

import pytest

from homemaster.contracts import OrchestrationPlan, Subtask
from homemaster.orchestration_validator import (
    Stage05ValidationError,
    validate_orchestration_payload,
    validate_orchestration_plan,
)


def test_orchestration_validator_accepts_intent_plan() -> None:
    plan = validate_orchestration_payload(
        {
            "goal": "去厨房找水杯，然后拿给用户",
            "subtasks": [
                {
                    "id": "find_cup",
                    "intent": "找到水杯",
                    "target_object": "水杯",
                    "room_hint": "厨房",
                    "success_criteria": ["观察到水杯"],
                },
                {
                    "id": "pick_cup",
                    "intent": "拿起水杯",
                    "target_object": "水杯",
                    "success_criteria": ["确认已经拿起水杯"],
                    "depends_on": ["find_cup"],
                },
            ],
            "confidence": 0.82,
        }
    )

    assert isinstance(plan, OrchestrationPlan)
    assert [subtask.id for subtask in plan.subtasks] == ["find_cup", "pick_cup"]


def test_orchestration_validator_rejects_selected_target_and_legacy_candidate_fields() -> None:
    with pytest.raises(Stage05ValidationError) as selected_target_error:
        validate_orchestration_payload(
            {
                "goal": "取水杯",
                "selected_target": {"memory_id": "mem-cup-1"},
                "subtasks": [
                    {
                        "id": "find_cup",
                        "intent": "找到水杯",
                        "success_criteria": ["观察到水杯"],
                    }
                ],
            }
        )
    with pytest.raises(Stage05ValidationError) as candidate_error:
        validate_orchestration_payload(
            {
                "goal": "取水杯",
                "subtasks": [
                    {
                        "id": "find_cup",
                        "intent": "找到水杯",
                        "success_criteria": ["观察到水杯"],
                        "selected_candidate_id": "candidate-1",
                    }
                ],
            }
        )

    assert "selected_target" in selected_target_error.value.message
    assert "selected_candidate_id" in candidate_error.value.message


def test_orchestration_validator_rejects_missing_and_cyclic_dependencies() -> None:
    missing = OrchestrationPlan(
        goal="取水杯",
        subtasks=[
            Subtask(
                id="pick_cup",
                intent="拿起水杯",
                success_criteria=["确认水杯已拿起"],
                depends_on=["find_cup"],
            )
        ],
    )
    cyclic = OrchestrationPlan(
        goal="取水杯",
        subtasks=[
            Subtask(
                id="find_cup",
                intent="找到水杯",
                success_criteria=["观察到水杯"],
                depends_on=["pick_cup"],
            ),
            Subtask(
                id="pick_cup",
                intent="拿起水杯",
                success_criteria=["确认水杯已拿起"],
                depends_on=["find_cup"],
            ),
        ],
    )

    with pytest.raises(Stage05ValidationError) as missing_error:
        validate_orchestration_plan(missing)
    with pytest.raises(Stage05ValidationError) as cyclic_error:
        validate_orchestration_plan(cyclic)

    assert "unknown dependency" in missing_error.value.message
    assert "dependency cycle" in cyclic_error.value.message


def test_high_level_atomic_words_do_not_fail_orchestration_plan() -> None:
    plan = validate_orchestration_payload(
        {
            "goal": "取水杯",
            "subtasks": [
                {
                    "id": "pick_cup",
                    "intent": "靠近水杯并 close_gripper 拿起水杯",
                    "target_object": "水杯",
                    "success_criteria": ["确认水杯已拿起"],
                }
            ],
            "confidence": 0.7,
        }
    )

    assert plan.subtasks[0].intent == "靠近水杯并 close_gripper 拿起水杯"
