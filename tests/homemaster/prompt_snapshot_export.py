"""One-shot script to export prompt snapshots from current f-string code.

Run BEFORE migrating to templates. Captures byte-exact output of every
prompt builder function and retry instruction constant.

Usage:
    PYTHONPATH=src .venv/bin/python tests/homemaster/prompt_snapshot_export.py
"""

from __future__ import annotations

from pathlib import Path

SNAPSHOT_DIR = Path(__file__).parent / "prompt_snapshots"


def export_all() -> None:
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)

    # --- Stage 01 ---
    from homemaster.pipeline import STAGE_01_RETRY_INSTRUCTION, build_stage_01_task_card_prompt

    _write(
        "stage_01_task_card_prompt.txt",
        build_stage_01_task_card_prompt("去桌子那边看看药盒是不是还在。"),
    )
    _write("stage_01_retry.txt", STAGE_01_RETRY_INSTRUCTION)

    # --- Stage 02 ---
    from homemaster.frontdoor import (
        RETRY_INSTRUCTION,
        TaskUnderstandingInput,
        build_task_understanding_prompt,
    )

    _write(
        "stage_02_task_understanding_prompt.txt",
        build_task_understanding_prompt(
            TaskUnderstandingInput(
                utterance="去厨房看看水杯还在不在",
                user_id="elder-001",
                source="voice",
                recent_task_summary="上一轮找过药盒",
            )
        ),
    )
    _write("stage_02_retry.txt", RETRY_INSTRUCTION)

    # --- Stage 03 ---
    from homemaster.contracts import TaskCard
    from homemaster.memory_rag import (
        MEMORY_QUERY_RETRY_INSTRUCTION,
        build_memory_retrieval_query_prompt,
    )

    task_card = TaskCard(
        task_type="fetch_object",
        target="水杯",
        delivery_target="user",
        location_hint="厨房",
        success_criteria=["拿到水杯"],
        needs_clarification=False,
        confidence=0.9,
    )
    _write(
        "stage_03_memory_query_prompt.txt",
        build_memory_retrieval_query_prompt(
            task_card, negative_evidence={"excluded_memory_ids": ["mem-1"]}
        ),
    )
    _write("stage_03_retry.txt", MEMORY_QUERY_RETRY_INSTRUCTION)

    # --- Stage 05 orchestration ---
    from homemaster.contracts import PlanningContext
    from homemaster.orchestrator import ORCHESTRATION_RETRY_INSTRUCTION, build_orchestration_prompt

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
    _write("stage_05_orchestration_prompt.txt", build_orchestration_prompt(context))
    _write("stage_05_orchestration_retry.txt", ORCHESTRATION_RETRY_INSTRUCTION)

    # --- Stage 05 step decision ---
    from homemaster.contracts import ExecutionState, Subtask, SubtaskRuntimeState
    from homemaster.skill_selector import build_retry_instruction, build_step_decision_prompt

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
    _write("stage_05_step_decision_prompt.txt", build_step_decision_prompt(subtask, state, context))
    _write("stage_05_step_decision_retry.txt", build_retry_instruction())

    # --- Stage 05 recovery ---
    from homemaster.recovery import RECOVERY_RETRY_INSTRUCTION, build_recovery_prompt

    _write("stage_05_recovery_prompt.txt", build_recovery_prompt(state, []))
    _write("stage_05_recovery_retry.txt", RECOVERY_RETRY_INSTRUCTION)

    # --- Stage 06 summary ---
    from homemaster.contracts import EvidenceBundle
    from homemaster.summary import SUMMARY_RETRY_INSTRUCTION, build_task_summary_prompt

    _write(
        "stage_06_summary_prompt.txt",
        build_task_summary_prompt(
            task_card=task_card,
            execution_state=state,
            evidence_bundle=EvidenceBundle(task_id="test-task"),
        ),
    )
    _write("stage_06_summary_retry.txt", SUMMARY_RETRY_INSTRUCTION)

    print(f"Exported {len(list(SNAPSHOT_DIR.glob('*.txt')))} snapshots to {SNAPSHOT_DIR}")


def _write(name: str, content: str) -> None:
    (SNAPSHOT_DIR / name).write_text(content, encoding="utf-8")
    print(f"  {name} ({len(content)} chars)")


if __name__ == "__main__":
    export_all()
