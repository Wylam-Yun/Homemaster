"""Live Stage 07 scenario tests — requires Mimo LLM + BGE-M3.

These tests are the P0 LLM baseline. Without API keys they report skipped,
never baseline-pass.

Offline tests (no live_api marker) verify scenario catalog structure and
RuntimeMode boundary values without requiring API access.

Usage:
    HOMEMASTER_RUN_LIVE_LLM=1 HOMEMASTER_RUN_LIVE_EMBEDDING=1 \
        .venv/bin/pytest tests/homemaster/test_stage_07_scenarios_live.py -v
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from homemaster.scenario_catalog import baseline_scenario_names, legacy_compat_names
from homemaster.scenario_runner import run_stage_07_scenario_matrix


def _require_live_env() -> None:
    if os.getenv("HOMEMASTER_RUN_LIVE_LLM") != "1":
        pytest.skip("set HOMEMASTER_RUN_LIVE_LLM=1 to run real Mimo Stage 07 cases")
    if os.getenv("HOMEMASTER_RUN_LIVE_EMBEDDING") != "1":
        pytest.skip("set HOMEMASTER_RUN_LIVE_EMBEDDING=1 to run real BGE-M3 Stage 07 cases")


@pytest.mark.live_api
def test_stage_07_llm_baseline_matrix(tmp_path: Path) -> None:
    _require_live_env()

    names = baseline_scenario_names()
    assert len(names) >= 7, f"Expected >=7 baseline scenarios, got {len(names)}"

    result = run_stage_07_scenario_matrix(
        runtime_root=tmp_path / "runs",
        debug_root=tmp_path / "debug",
        scenarios=names,
    )

    assert result.passed is True
    assert len(result.case_results) == len(names)
    assert result.acceptance_matrix_path.is_file()

    matrix = json.loads(result.acceptance_matrix_path.read_text(encoding="utf-8"))
    boundary = matrix["model_boundary"]
    # LLM stages must be live
    assert boundary["stage02"] == "real_mimo"
    assert boundary["stage03_query"] == "real_mimo"
    assert boundary["stage03_embedding"] == "real_bge_m3"
    assert boundary["stage05_plan"] == "real_mimo"
    assert boundary["stage05_step"] == "real_mimo"
    assert boundary["stage06_summary"] == "real_mimo"
    # stage04, memory_commit are programmatic; skills/robot/VLM are mock/not_integrated
    assert boundary["stage05_navigation"] == "simulated"
    assert boundary["real_robot"] == "not_integrated"

    for case in result.case_results:
        assert (case.case_dir / "result.md").is_file()


@pytest.mark.live_api
def test_stage_07_legacy_compat_matrix(tmp_path: Path) -> None:
    """Legacy 5 scenarios run as compatibility check, not baseline."""
    _require_live_env()

    names = legacy_compat_names()
    assert len(names) == 5

    result = run_stage_07_scenario_matrix(
        runtime_root=tmp_path / "runs",
        debug_root=tmp_path / "debug",
        scenarios=names,
    )

    assert result.passed is True
    assert len(result.case_results) == 5


@pytest.mark.live_api
def test_scenario_matrix_uses_custom_results_root(tmp_path: Path) -> None:
    """When results_root is provided, matrix output goes there."""
    _require_live_env()
    custom_results = tmp_path / "custom_results"
    result = run_stage_07_scenario_matrix(
        runtime_root=tmp_path / "runs",
        debug_root=tmp_path / "debug",
        results_root=custom_results,
        scenarios=["check_medicine_success"],
    )
    assert result.acceptance_matrix_path.parent == custom_results / "stage_07"
    assert result.acceptance_matrix_path.is_file()


# ---------------------------------------------------------------------------
# Offline tests (no live_api marker) — scenario structure and boundary checks
# ---------------------------------------------------------------------------


def test_baseline_scenario_catalog_has_minimum_scenarios() -> None:
    """The catalog must expose at least 7 baseline scenarios."""
    names = baseline_scenario_names()
    assert len(names) >= 7, f"Expected >=7 baseline scenarios, got {len(names)}"


def test_legacy_compat_scenario_count() -> None:
    """Legacy compat suite must expose exactly 5 scenarios."""
    names = legacy_compat_names()
    assert len(names) == 5


def test_runtime_mode_boundary_values() -> None:
    """RuntimeMode.live() boundary dict must have expected values."""
    from homemaster.pipeline.stage_runtime import RuntimeMode

    boundary = RuntimeMode.live().to_boundary_dict()
    assert boundary["stage02"] == "real_mimo"
    assert boundary["stage03_query"] == "real_mimo"
    assert boundary["stage03_embedding"] == "real_bge_m3"
    assert boundary["stage04"] == "programmatic"
    assert boundary["stage05_plan"] == "real_mimo"
    assert boundary["stage05_step"] == "real_mimo"
    assert boundary["stage05_navigation"] == "simulated"
    assert boundary["stage05_operation"] == "simulated"
    assert boundary["stage05_verification"] == "simulated"
    assert boundary["stage06_summary"] == "real_mimo"
    assert boundary["stage06_memory_commit"] == "programmatic"
    assert boundary["real_robot"] == "not_integrated"


def test_scenario_runner_function_exists() -> None:
    """run_stage_07_scenario_matrix must be importable and callable."""
    from homemaster.scenario_runner import run_stage_07_scenario_matrix

    assert callable(run_stage_07_scenario_matrix)
