from __future__ import annotations

from homemaster.stage_04 import run_stage_04_case, stage_04_case_expectations


def test_stage_04_case_runner_generates_grounded_debug_report(tmp_path) -> None:
    result = run_stage_04_case(
        "ground_cup_target",
        case_root=tmp_path / "cases",
        results_dir=tmp_path / "results",
    )

    assert result.passed is True
    assert result.context.selected_target is not None
    assert result.context.selected_target.memory_id == "mem-cup-1"
    assert (result.case_dir / "input.json").is_file()
    assert (result.case_dir / "expected.json").is_file()
    assert (result.case_dir / "actual.json").is_file()
    report = (result.case_dir / "result.md").read_text(encoding="utf-8")
    assert "## Hit Assessments" in report
    assert "## PlanningContext" in report
    assert "mem-cup-1" in report


def test_stage_04_case_runner_generates_ungrounded_debug_report(tmp_path) -> None:
    result = run_stage_04_case(
        "ungrounded_no_memory_context",
        case_root=tmp_path / "cases",
        results_dir=tmp_path / "results",
    )

    assert result.passed is True
    assert result.context.selected_target is None
    assert result.context.runtime_state_summary["grounding_status"] == "ungrounded"
    report = (result.case_dir / "result.md").read_text(encoding="utf-8")
    assert "ungrounded" in report
    assert "needs_exploratory_search" in report


def test_stage_04_expected_cases_are_declared() -> None:
    cases = stage_04_case_expectations()

    assert set(cases) == {
        "ground_cup_target",
        "ground_medicine_target",
        "ground_negative_evidence_target",
        "ungrounded_no_memory_context",
        "ungrounded_all_hits_invalid",
        "planning_context_for_orchestration",
    }
