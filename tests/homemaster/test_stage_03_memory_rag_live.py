from __future__ import annotations

import os

import pytest

from homemaster.memory_rag import run_stage_03_case, stage_03_case_expectations


@pytest.mark.live_api
@pytest.mark.parametrize("case_name", sorted(stage_03_case_expectations()))
def test_stage_03_live_memory_rag_cases(case_name: str) -> None:
    if os.getenv("HOMEMASTER_RUN_LIVE_LLM") != "1":
        pytest.skip("set HOMEMASTER_RUN_LIVE_LLM=1 to run real Mimo Stage 03 cases")
    if os.getenv("HOMEMASTER_RUN_LIVE_EMBEDDING") != "1":
        pytest.skip("set HOMEMASTER_RUN_LIVE_EMBEDDING=1 to run real BGE-M3 Stage 03 cases")

    result = run_stage_03_case(case_name)

    assert result.passed is True
    assert all(result.checks.values())
    assert (result.case_dir / "input.json").is_file()
    assert (result.case_dir / "expected.json").is_file()
    assert (result.case_dir / "actual.json").is_file()
    assert (result.case_dir / "result.md").is_file()
