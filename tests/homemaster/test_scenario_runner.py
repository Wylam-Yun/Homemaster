from __future__ import annotations

import json
from pathlib import Path

from homemaster.scenario_runner import STAGE_07_SCENARIOS, run_stage_07_scenario_matrix


def test_scenario_runner_uses_isolated_runtime_memory_roots(tmp_path: Path) -> None:
    result = run_stage_07_scenario_matrix(
        runtime_root=tmp_path / "runs",
        debug_root=tmp_path / "debug",
        live_models=False,
        scenarios=["check_medicine_success", "fetch_cup_retry"],
    )

    assert result.passed is True
    assert len(result.case_results) == 2
    memory_roots = {str(case.runtime_memory_root) for case in result.case_results}
    assert len(memory_roots) == 2
    for case in result.case_results:
        assert (case.runtime_memory_root / "object_memory.json").is_file()
        assert (case.runtime_memory_root / "commit_log.jsonl").is_file()
    matrix = json.loads(result.acceptance_matrix_path.read_text(encoding="utf-8"))
    assert matrix["model_boundary"]["stage05_navigation"] == "mock"
    assert matrix["model_boundary"]["real_robot"] == "not_integrated"


def test_stage_07_scenario_catalog_has_five_cases() -> None:
    assert set(STAGE_07_SCENARIOS) == {
        "check_medicine_success",
        "check_medicine_stale_recover",
        "fetch_cup_retry",
        "object_not_found",
        "distractor_rejected",
    }
