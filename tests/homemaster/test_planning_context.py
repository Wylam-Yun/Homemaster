from __future__ import annotations

import json
from pathlib import Path

from homemaster.contracts import MemoryRetrievalResult, PlanningContext, TaskCard
from homemaster.planning_context import build_planning_context


def _stage_03_actual(case_name: str) -> dict[str, object]:
    path = Path("tests/homemaster/llm_cases/stage_03") / case_name / "actual.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _task_card(case_name: str) -> TaskCard:
    return TaskCard.model_validate(_stage_03_actual(case_name)["task_card"])


def _memory_result(case_name: str) -> MemoryRetrievalResult:
    return MemoryRetrievalResult.model_validate(_stage_03_actual(case_name)["memory_result"])


def _world(case_name: str) -> dict[str, object]:
    path = Path("data/scenarios") / case_name / "world.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_planning_context_serializes_for_grounded_stage05() -> None:
    result = build_planning_context(
        _task_card("cup_object_memory_rag"),
        _memory_result("cup_object_memory_rag"),
        _world("fetch_cup_retry"),
    )

    decoded = PlanningContext.model_validate_json(result.context.model_dump_json())

    assert result.grounding.grounding_status == "grounded"
    assert decoded.selected_target is not None
    assert decoded.selected_target.memory_id == "mem-cup-1"
    assert decoded.runtime_state_summary["grounding_status"] == "grounded"


def test_ungrounded_context_when_no_hits() -> None:
    task_card = _task_card("cup_object_memory_rag")
    empty_result = _memory_result("cup_object_memory_rag").model_copy(update={"hits": []})

    result = build_planning_context(task_card, empty_result, _world("fetch_cup_retry"))

    assert result.context.selected_target is None
    assert result.context.runtime_state_summary["grounding_status"] == "ungrounded"
    assert result.context.runtime_state_summary["needs_exploratory_search"] is True
    assert any("探索" in note or "search" in note for note in result.context.planning_notes)


def test_ungrounded_context_when_all_hits_invalid() -> None:
    task_card = _task_card("cup_object_memory_rag")
    memory_result = _memory_result("cup_object_memory_rag")
    invalid_hits = [
        hit.model_copy(update={"viewpoint_id": "missing_viewpoint"}) for hit in memory_result.hits
    ]
    patched_result = memory_result.model_copy(update={"hits": invalid_hits})

    result = build_planning_context(task_card, patched_result, _world("fetch_cup_retry"))

    assert result.context.selected_target is None
    assert result.grounding.grounding_status == "ungrounded"
    assert len(result.context.rejected_hits) == len(invalid_hits)
    assert result.context.runtime_state_summary["needs_exploratory_search"] is True
