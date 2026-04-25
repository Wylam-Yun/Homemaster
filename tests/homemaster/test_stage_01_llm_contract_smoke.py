from __future__ import annotations

import os

import pytest

from homemaster.pipeline import run_stage_01_contract_smoke


@pytest.mark.live_api
def test_stage_01_mimo_contract_smoke() -> None:
    if os.getenv("HOMEMASTER_RUN_LIVE_LLM") != "1":
        pytest.skip("set HOMEMASTER_RUN_LIVE_LLM=1 to run the real Mimo smoke")

    result = run_stage_01_contract_smoke()

    assert result.passed is True
    assert result.task_card.task_type == "check_presence"
    assert result.checks["target_mentions_medicine_box"] is True
    assert (result.case_dir / "input.json").is_file()
    assert (result.case_dir / "expected.json").is_file()
    assert (result.case_dir / "actual.json").is_file()
    assert (result.case_dir / "result.md").is_file()
