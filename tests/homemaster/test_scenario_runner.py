from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from homemaster.scenario_runner import run_stage_07_scenario_matrix


@pytest.mark.live_api
def test_scenario_runner_uses_isolated_runtime_memory_roots(tmp_path: Path) -> None:
    if os.getenv("HOMEMASTER_RUN_LIVE_LLM") != "1":
        pytest.skip("set HOMEMASTER_RUN_LIVE_LLM=1 to run real Mimo Stage 07 cases")
    result = run_stage_07_scenario_matrix(
        runtime_root=tmp_path / "runs",
        debug_root=tmp_path / "debug",
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
    assert matrix["model_boundary"]["stage05_navigation"] == "simulated"
    assert matrix["model_boundary"]["real_robot"] == "not_integrated"


def test_stage_07_scenario_catalog_has_baseline_scenarios() -> None:
    """The llm_baseline scenarios must exist in the catalog."""
    from homemaster.scenario_catalog import load_catalog

    catalog = load_catalog()
    baseline_names = {e.name for e in catalog if "llm_baseline" in e.suites}
    assert len(baseline_names) >= 7
    assert "fetch_cup_table_success" in baseline_names
    assert "fetch_object_not_found" in baseline_names


def test_scenario_matrix_accepts_results_root() -> None:
    """run_stage_07_scenario_matrix must accept results_root."""
    import inspect

    assert "results_root" in inspect.signature(run_stage_07_scenario_matrix).parameters
