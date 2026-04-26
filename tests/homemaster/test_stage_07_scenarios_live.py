from __future__ import annotations

import os
from pathlib import Path

import pytest

from homemaster.scenario_runner import STAGE_07_SCENARIOS, run_stage_07_scenario_matrix


@pytest.mark.live_api
def test_stage_07_live_scenario_matrix(tmp_path: Path) -> None:
    if os.getenv("HOMEMASTER_RUN_LIVE_LLM") != "1":
        pytest.skip("set HOMEMASTER_RUN_LIVE_LLM=1 to run real Mimo Stage 07 cases")
    if os.getenv("HOMEMASTER_RUN_LIVE_EMBEDDING") != "1":
        pytest.skip("set HOMEMASTER_RUN_LIVE_EMBEDDING=1 to run real BGE-M3 Stage 07 cases")

    result = run_stage_07_scenario_matrix(
        runtime_root=tmp_path / "runs",
        debug_root=tmp_path / "debug",
        live_models=True,
        scenarios=STAGE_07_SCENARIOS,
    )

    assert result.passed is True
    assert len(result.case_results) == 5
    assert result.acceptance_matrix_path.is_file()
    for case in result.case_results:
        assert (case.case_dir / "result.md").is_file()
