from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from homemaster.benchmarking.alfworld.types import (
    AlfworldSummary,
    SubtaskResult,
    TasksetResult,
    TasksetRunSummary,
)


def _episode(
    index: int,
    *,
    classification: str,
    score_eligible: bool,
    success: bool = False,
) -> Any:
    return SimpleNamespace(
        episode_id=f"episode-{index}",
        success=success,
        failure_reason=None if success else classification,
        steps=1,
        invalid_actions=0,
        goal_condition_success_rate=1.0 if success else 0.0,
        runtime_status="replied" if success else "failed",
        run_id=f"run-{index}",
        trace_path=Path(f"/tmp/episode-{index}/trace.jsonl"),
        classification=classification,
        score_eligible=score_eligible,
    )


def test_summary_reports_per_classification_coverage_and_formal_score_gate() -> None:
    summary = AlfworldSummary(
        run_id="mixed",
        episodes=[
            _episode(
                1,
                classification="agent_success",
                score_eligible=True,
                success=True,
            ),
            _episode(2, classification="agent_model_failure", score_eligible=True),
            _episode(3, classification="harness_grounding_failure", score_eligible=False),
            _episode(4, classification="harness_navigation_failure", score_eligible=False),
            _episode(5, classification="harness_operation_failure", score_eligible=False),
            _episode(6, classification="execution_state_uncertain", score_eligible=False),
            _episode(
                7,
                classification="unclassified_execution_failure",
                score_eligible=False,
            ),
        ],
    )

    payload = summary.to_dict()

    assert payload["total_episodes"] == 7
    assert payload["agent_scored_episodes"] == 2
    assert payload["agent_successes"] == 1
    assert payload["agent_success_rate_on_valid"] == 0.5
    assert payload["harness_invalid_episodes"] == 5
    assert payload["harness_grounding_failures"] == 1
    assert payload["harness_navigation_failures"] == 1
    assert payload["harness_operation_failures"] == 1
    assert payload["execution_state_uncertain_count"] == 1
    assert payload["unclassified_execution_failures"] == 1
    assert payload["harness_valid_coverage"] == 2 / 7
    assert payload["formal_score_available"] is False
    assert [episode["classification"] for episode in payload["episodes"]] == [
        "agent_success",
        "agent_model_failure",
        "harness_grounding_failure",
        "harness_navigation_failure",
        "harness_operation_failure",
        "execution_state_uncertain",
        "unclassified_execution_failure",
    ]
    assert [episode["score_eligible"] for episode in payload["episodes"]] == [
        True,
        True,
        False,
        False,
        False,
        False,
        False,
    ]


def test_summary_only_exposes_formal_score_at_full_classified_coverage() -> None:
    summary = AlfworldSummary(
        run_id="formal",
        episodes=[
            _episode(
                1,
                classification="agent_success",
                score_eligible=True,
                success=True,
            ),
            _episode(2, classification="agent_model_failure", score_eligible=True),
        ],
    )

    payload = summary.to_dict()

    assert payload["harness_valid_coverage"] == 1.0
    assert payload["unclassified_execution_failures"] == 0
    assert payload["formal_score_available"] is True
    assert payload["agent_success_rate_on_valid"] == 0.5


def _subtask(
    index: int,
    *,
    classification: str,
    score_eligible: bool,
    success: bool = False,
) -> SubtaskResult:
    return SubtaskResult(
        index=index,
        goal_type="pick_and_place_simple",
        object="Pencil",
        target="Shelf",
        instruction=f"subtask {index}",
        success=success,
        failure_reason=None if success else classification,
        steps=1 if classification != "not_run_due_to_infrastructure_failure" else 0,
        invalid_actions=0,
        goal_condition_success_rate=1.0 if success else 0.0,
        runtime_status=(
            "replied"
            if success
            else "not_run"
            if classification == "not_run_due_to_infrastructure_failure"
            else "failed"
        ),
        trace_path=Path(f"/tmp/subtask-{index}/trace.jsonl"),
        classification=classification,
        score_eligible=score_eligible,
        agent_tool_call_count=0 if index > 0 else 2,
        backend_action_count=0 if index > 0 else 5,
    )


def test_taskset_summary_excludes_entire_chain_after_infrastructure_failure() -> None:
    scored = TasksetResult(
        taskset_id="scored",
        floorplan=1,
        difficulty="easy",
        description="agent completed the chain",
        subtasks=[
            _subtask(
                0,
                classification="agent_success",
                score_eligible=True,
                success=True,
            )
        ],
        chain_success=True,
        trace_dir=Path("/tmp/taskset-scored"),
    )
    invalid = TasksetResult(
        taskset_id="invalid",
        floorplan=2,
        difficulty="hard",
        description="harness stopped the chain",
        subtasks=[
            _subtask(
                0,
                classification="harness_operation_failure",
                score_eligible=False,
            ),
            _subtask(
                1,
                classification="not_run_due_to_infrastructure_failure",
                score_eligible=False,
            ),
        ],
        chain_success=False,
        trace_dir=Path("/tmp/taskset-invalid"),
    )

    payload = TasksetRunSummary(
        run_id="taskset-mixed",
        taskset_results=[scored, invalid],
    ).to_dict()

    assert payload["total_tasksets"] == 2
    assert payload["agent_scored_tasksets"] == 1
    assert payload["agent_successes"] == 1
    assert payload["agent_success_rate_on_valid"] == 1.0
    assert payload["harness_invalid_tasksets"] == 1
    assert payload["harness_operation_failures"] == 1
    assert payload["harness_valid_coverage"] == 0.5
    assert payload["formal_score_available"] is False
    assert payload["not_run_subtasks"] == 1
    assert payload["tasksets"][1]["classification"] == "harness_operation_failure"
    assert payload["tasksets"][1]["score_eligible"] is False
    assert payload["tasksets"][1]["agent_tool_call_count"] == 2
    assert payload["tasksets"][1]["backend_action_count"] == 5
    assert payload["tasksets"][1]["subtasks"][1]["classification"] == (
        "not_run_due_to_infrastructure_failure"
    )
    assert payload["tasksets"][1]["subtasks"][1]["runtime_status"] == "not_run"
