from __future__ import annotations

import json
import re
from pathlib import Path

from homemaster.contracts import MemoryRetrievalHit, MemoryRetrievalResult, TaskCard
from homemaster.grounding import assess_hit_reliability, select_grounded_target
from homemaster.planning_context import build_planning_context

ROOT = Path(__file__).resolve().parents[2]
LLM_CASES = ROOT / "tests" / "homemaster" / "llm_cases"


def _stage_03_actual(case_name: str) -> dict[str, object]:
    path = LLM_CASES / "stage_03" / case_name / "actual.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _task_card(case_name: str = "cup_object_memory_rag") -> TaskCard:
    return TaskCard.model_validate(_stage_03_actual(case_name)["task_card"])


def _memory_result(case_name: str = "cup_object_memory_rag") -> MemoryRetrievalResult:
    return MemoryRetrievalResult.model_validate(_stage_03_actual(case_name)["memory_result"])


def _world(case_name: str = "fetch_cup_retry") -> dict[str, object]:
    path = ROOT / "data" / "scenarios" / case_name / "world.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _copy_hit(hit: MemoryRetrievalHit, **updates: object) -> MemoryRetrievalHit:
    data = hit.model_dump(mode="json")
    data.update(updates)
    metadata = dict(data.get("canonical_metadata") or {})
    for key in (
        "memory_id",
        "object_category",
        "aliases",
        "room_id",
        "anchor_id",
        "anchor_type",
        "display_text",
        "viewpoint_id",
        "confidence_level",
        "belief_state",
    ):
        if key in updates:
            metadata[key] = updates[key]
    data["canonical_metadata"] = metadata
    return MemoryRetrievalHit.model_validate(data)


def test_stage_04_rejects_top1_when_second_hit_is_reliable() -> None:
    task_card = _task_card()
    memory_result = _memory_result()
    weak_top1 = _copy_hit(
        memory_result.hits[0],
        confidence_level="low",
        belief_state="stale",
    )
    reliable_second = _copy_hit(
        memory_result.hits[1],
        room_id="kitchen",
        anchor_id="anchor_kitchen_table_1",
        viewpoint_id="kitchen_table_viewpoint",
        display_text="厨房餐桌",
        confidence_level="high",
        belief_state="confirmed",
    )
    patched_result = memory_result.model_copy(update={"hits": [weak_top1, reliable_second]})

    result = select_grounded_target(task_card, patched_result, _world())

    assert result.selected_target is not None
    assert result.selected_target.memory_id == reliable_second.memory_id
    assert result.assessments[0].status == "weak_lead"
    assert "low_confidence" in result.assessments[0].reasons
    assert "stale_belief" in result.assessments[0].reasons


def test_stage_04_ungrounded_context_has_exploration_signal() -> None:
    task_card = _task_card()
    memory_result = _memory_result().model_copy(update={"hits": []})

    result = build_planning_context(task_card, memory_result, _world())

    assert result.context.selected_target is None
    assert result.context.runtime_state_summary["grounding_status"] == "ungrounded"
    assert result.context.runtime_state_summary["needs_exploratory_search"] is True
    assert any("探索" in note or "explor" in note for note in result.context.planning_notes)


def test_stage_04_rejects_anchor_viewpoint_mismatch() -> None:
    task_card = _task_card()
    hit = _copy_hit(
        _memory_result().hits[0],
        anchor_id="anchor_kitchen_table_1",
        viewpoint_id="pantry_shelf_viewpoint",
    )

    assessment = assess_hit_reliability(task_card, hit, _world(), set())

    assert assessment.status == "unreliable"
    assert "anchor_viewpoint_mismatch" in assessment.reasons


def test_stage_04_rejects_alias_mismatch_even_with_high_score() -> None:
    task_card = _task_card()
    hit = _copy_hit(
        _memory_result().hits[0],
        object_category="remote_control",
        aliases=["遥控器", "remote"],
        ranking_reasons=["bm25_rank=1", "dense_rank=1"],
        bm25_score=999.0,
        dense_score=0.99,
        metadata_score=0.0,
        final_score=999.99,
    )

    assessment = assess_hit_reliability(task_card, hit, _world(), set())

    assert assessment.status == "unreliable"
    assert "target_metadata_mismatch" in assessment.reasons


def test_stage_01_04_debug_assets_do_not_contain_secrets() -> None:
    secret_pattern = re.compile(
        r"(Bearer\s+\S+|Authorization|x-api-key|\"api_keys\"|sk-[A-Za-z0-9_-]{8,})"
    )
    checked_files = [
        path
        for path in LLM_CASES.glob("stage_0[1-4]/**/*")
        if path.is_file() and path.suffix in {".json", ".md", ".jsonl"}
    ]

    assert checked_files
    leaked = [
        str(path.relative_to(ROOT))
        for path in checked_files
        if secret_pattern.search(path.read_text(encoding="utf-8", errors="ignore"))
    ]
    assert leaked == []
