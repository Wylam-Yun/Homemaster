from __future__ import annotations

import os

import pytest

from homemaster.stage_05 import (
    run_live_stage_05_orchestration_case,
    run_live_stage_05_step_case,
)


@pytest.mark.live_api
@pytest.mark.parametrize(
    "case_name",
    [
        "check_medicine_orchestration_live",
        "fetch_cup_orchestration_live",
        "ungrounded_exploration_live",
    ],
)
def test_stage_05_live_orchestration_cases(case_name: str) -> None:
    if os.getenv("HOMEMASTER_RUN_LIVE_LLM") != "1":
        pytest.skip("set HOMEMASTER_RUN_LIVE_LLM=1 to run real Mimo Stage 05 cases")

    result = run_live_stage_05_orchestration_case(case_name)

    assert result.passed is True
    assert result.plan.subtasks
    assert (result.case_dir / "result.md").is_file()
    assert all(result.checks.values())


@pytest.mark.live_api
@pytest.mark.parametrize(
    ("case_name", "expected_skill"),
    [
        ("find_cup_step_decision_live", "navigation"),
        ("pick_cup_step_decision_live", "operation"),
    ],
)
def test_stage_05_live_step_decision_cases(case_name: str, expected_skill: str) -> None:
    if os.getenv("HOMEMASTER_RUN_LIVE_LLM") != "1":
        pytest.skip("set HOMEMASTER_RUN_LIVE_LLM=1 to run real Mimo Stage 05 cases")

    result = run_live_stage_05_step_case(case_name)

    assert result.passed is True
    assert result.decision.selected_skill == expected_skill
    assert (result.case_dir / "result.md").is_file()
    assert all(result.checks.values())
