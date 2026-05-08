"""Tests verifying prompt externalization produces byte-identical output.

Snapshot fixtures in prompt_snapshots/ were exported from the original
f-string code BEFORE migration. These tests compare current builder
output against those committed fixtures, ensuring "只搬不改".
"""

from __future__ import annotations

from pathlib import Path

import pytest

from homemaster.prompt_loader import render

SNAPSHOT_DIR = Path(__file__).parent / "prompt_snapshots"


def _load_snapshot(name: str) -> str:
    return (SNAPSHOT_DIR / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# prompt_loader unit tests
# ---------------------------------------------------------------------------


def test_render_missing_template_raises_file_not_found() -> None:
    with pytest.raises(FileNotFoundError):
        render("nonexistent_template.txt")


def test_render_missing_variable_raises_key_error() -> None:
    with pytest.raises(KeyError):
        render("stage_01_task_card_prompt.txt")


def test_all_no_variable_templates_render() -> None:
    """All 6 retry templates (no variables) load and produce non-empty output."""
    for name in [
        "stage_01_retry.txt",
        "stage_02_retry.txt",
        "stage_03_retry.txt",
        "stage_05_orchestration_retry.txt",
        "stage_05_recovery_retry.txt",
        "stage_06_summary_retry.txt",
    ]:
        result = render(name)
        assert len(result) > 0, f"{name} produced empty output"


def test_all_variable_templates_render_with_placeholders() -> None:
    """All 8 prompt templates render without error when given correct variables."""
    render("stage_01_task_card_prompt.txt", utterance="test")
    render("stage_02_task_understanding_prompt.txt", context_json="{}", retry_section="")
    render("stage_03_memory_query_prompt.txt", task_card_json="{}", negative_json="{}")
    render("stage_05_orchestration_prompt.txt", context_json="{}", retry_section="")
    render(
        "stage_05_step_decision_prompt.txt",
        names_str="a",
        subtask_json="{}",
        state_json="{}",
        context_json="{}",
        skills_json="{}",
        retry_section="",
    )
    render("stage_05_step_decision_retry.txt", names_str="a")
    render("stage_05_recovery_prompt.txt", state_json="{}", failure_json="[]", retry_section="")
    render(
        "stage_06_summary_prompt.txt",
        task_json="{}",
        state_json="{}",
        evidence_json="{}",
        retry_section="",
    )


def test_prompt_loader_works_from_package() -> None:
    """Verify templates are accessible via the package path."""
    result = render("stage_01_retry.txt")
    assert "TaskCard" in result


# ---------------------------------------------------------------------------
# Snapshot tests: compare builder output against committed fixtures
# ---------------------------------------------------------------------------


def test_stage_01_prompt_snapshot() -> None:
    from homemaster.pipeline import build_stage_01_task_card_prompt

    assert build_stage_01_task_card_prompt("去桌子那边看看药盒是不是还在。") == _load_snapshot(
        "stage_01_task_card_prompt.txt"
    )


def test_stage_01_retry_snapshot() -> None:
    from homemaster.pipeline import STAGE_01_RETRY_INSTRUCTION

    assert STAGE_01_RETRY_INSTRUCTION == _load_snapshot("stage_01_retry.txt")


def test_stage_02_prompt_snapshot() -> None:
    from homemaster.frontdoor import TaskUnderstandingInput, build_task_understanding_prompt

    assert build_task_understanding_prompt(
        TaskUnderstandingInput(
            utterance="去厨房看看水杯还在不在",
            user_id="elder-001",
            source="voice",
            recent_task_summary="上一轮找过药盒",
        )
    ) == _load_snapshot("stage_02_task_understanding_prompt.txt")


def test_stage_02_retry_snapshot() -> None:
    from homemaster.frontdoor import RETRY_INSTRUCTION

    assert RETRY_INSTRUCTION == _load_snapshot("stage_02_retry.txt")


def test_stage_03_prompt_snapshot() -> None:
    from homemaster.contracts import TaskCard
    from homemaster.memory_rag import build_memory_retrieval_query_prompt

    task_card = TaskCard(
        task_type="fetch_object",
        target="水杯",
        delivery_target="user",
        location_hint="厨房",
        success_criteria=["拿到水杯"],
        needs_clarification=False,
        confidence=0.9,
    )
    assert build_memory_retrieval_query_prompt(
        task_card, negative_evidence={"excluded_memory_ids": ["mem-1"]}
    ) == _load_snapshot("stage_03_memory_query_prompt.txt")


def test_stage_03_retry_snapshot() -> None:
    from homemaster.memory_rag import MEMORY_QUERY_RETRY_INSTRUCTION

    assert MEMORY_QUERY_RETRY_INSTRUCTION == _load_snapshot("stage_03_retry.txt")


def test_stage_05_orchestration_prompt_snapshot() -> None:
    from homemaster.contracts import PlanningContext, TaskCard
    from homemaster.orchestrator import build_orchestration_prompt

    context = PlanningContext(
        task_card=TaskCard(
            task_type="fetch_object",
            target="水杯",
            delivery_target="user",
            location_hint="厨房",
            success_criteria=["拿到水杯"],
            needs_clarification=False,
            confidence=0.9,
        ),
    )
    expected = _load_snapshot("stage_05_orchestration_prompt.txt")
    assert build_orchestration_prompt(context) == expected


def test_stage_05_orchestration_retry_snapshot() -> None:
    from homemaster.orchestrator import ORCHESTRATION_RETRY_INSTRUCTION

    assert ORCHESTRATION_RETRY_INSTRUCTION == _load_snapshot("stage_05_orchestration_retry.txt")


def test_stage_05_step_decision_prompt_snapshot() -> None:
    from homemaster.contracts import (
        ExecutionState,
        PlanningContext,
        Subtask,
        SubtaskRuntimeState,
        TaskCard,
    )
    from homemaster.skill_selector import build_step_decision_prompt

    subtask = Subtask(
        id="find_cup",
        intent="找到水杯",
        target_object="水杯",
        room_hint="厨房",
        success_criteria=["观察到水杯"],
    )
    state = ExecutionState(
        current_subtask_id="find_cup",
        subtasks=[SubtaskRuntimeState(subtask_id="find_cup")],
    )
    context = PlanningContext(
        task_card=TaskCard(
            task_type="fetch_object",
            target="水杯",
            delivery_target="user",
            location_hint="厨房",
            success_criteria=["拿到水杯"],
            needs_clarification=False,
            confidence=0.9,
        ),
    )
    assert build_step_decision_prompt(subtask, state, context) == _load_snapshot(
        "stage_05_step_decision_prompt.txt"
    )


def test_stage_05_step_decision_retry_snapshot() -> None:
    from homemaster.skill_selector import build_retry_instruction

    assert build_retry_instruction() == _load_snapshot("stage_05_step_decision_retry.txt")


def test_stage_05_recovery_prompt_snapshot() -> None:
    from homemaster.contracts import ExecutionState, SubtaskRuntimeState
    from homemaster.recovery import build_recovery_prompt

    state = ExecutionState(
        current_subtask_id="find_cup",
        subtasks=[SubtaskRuntimeState(subtask_id="find_cup")],
    )
    assert build_recovery_prompt(state, []) == _load_snapshot("stage_05_recovery_prompt.txt")


def test_stage_05_recovery_retry_snapshot() -> None:
    from homemaster.recovery import RECOVERY_RETRY_INSTRUCTION

    assert RECOVERY_RETRY_INSTRUCTION == _load_snapshot("stage_05_recovery_retry.txt")


def test_stage_06_summary_prompt_snapshot() -> None:
    from homemaster.contracts import EvidenceBundle, ExecutionState, SubtaskRuntimeState, TaskCard
    from homemaster.summary import build_task_summary_prompt

    task_card = TaskCard(
        task_type="fetch_object",
        target="水杯",
        delivery_target="user",
        location_hint="厨房",
        success_criteria=["拿到水杯"],
        needs_clarification=False,
        confidence=0.9,
    )
    state = ExecutionState(
        current_subtask_id="find_cup",
        subtasks=[SubtaskRuntimeState(subtask_id="find_cup")],
    )
    assert build_task_summary_prompt(
        task_card=task_card,
        execution_state=state,
        evidence_bundle=EvidenceBundle(task_id="test-task"),
    ) == _load_snapshot("stage_06_summary_prompt.txt")


def test_stage_06_summary_retry_snapshot() -> None:
    from homemaster.summary import SUMMARY_RETRY_INSTRUCTION

    assert SUMMARY_RETRY_INSTRUCTION == _load_snapshot("stage_06_summary_retry.txt")
