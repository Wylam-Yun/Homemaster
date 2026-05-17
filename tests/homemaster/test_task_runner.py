from __future__ import annotations

import os
from pathlib import Path

import pytest

from homemaster.config.runtime_paths import InvalidRunIdError
from homemaster.runtime import REPO_ROOT
from homemaster.task_runner import HomeMasterRunError, run_homemaster_task


def _require_live_env() -> None:
    if os.getenv("HOMEMASTER_RUN_LIVE_LLM") != "1":
        pytest.skip("set HOMEMASTER_RUN_LIVE_LLM=1 to run real Mimo Stage 07 cases")
    if os.getenv("HOMEMASTER_RUN_LIVE_EMBEDDING") != "1":
        pytest.skip("set HOMEMASTER_RUN_LIVE_EMBEDDING=1 to run real BGE-M3 Stage 07 cases")


@pytest.mark.live_api
def test_task_runner_runs_stage_02_to_06_and_isolates_memory(tmp_path: Path) -> None:
    _require_live_env()
    result = run_homemaster_task(
        utterance="去厨房找水杯，然后拿给我",
        scenario="fetch_cup_retry",
        runtime_memory_root=tmp_path / "runs",
        debug_root=tmp_path / "debug",
        run_id="runner-fetch-cup",
    )

    assert result.final_status in {"completed", "failed"}
    assert result.stage_statuses["stage02"]["status"] == "PASS"
    assert result.stage_statuses["stage03"]["status"] == "PASS"
    assert result.stage_statuses["stage04"]["status"] == "PASS"
    assert result.stage_statuses["stage05"]["status"] == "PASS"
    assert result.stage_statuses["stage06"]["status"] == "PASS"
    assert result.runtime_memory_root == tmp_path / "runs" / "runner-fetch-cup" / "memory"
    assert (result.runtime_memory_root / "object_memory.json").is_file()
    assert not (REPO_ROOT / "var" / "homemaster" / "memory" / "object_memory.json").exists()


@pytest.mark.live_api
def test_task_runner_accepts_explicit_world_and_memory_paths(tmp_path: Path) -> None:
    _require_live_env()
    scenario_root = REPO_ROOT / "data" / "scenarios" / "check_medicine_success"
    result = run_homemaster_task(
        utterance="去厨房看看药盒是不是还在",
        scenario="check_medicine_success",
        world_path=scenario_root / "world.json",
        memory_path=scenario_root / "memory.json",
        runtime_memory_root=tmp_path / "runs",
        debug_root=tmp_path / "debug",
        run_id="runner-medicine",
    )

    assert result.task_card is not None
    assert result.task_card.target in {"药盒", "药"}
    assert result.paths["world_path"].endswith("world.json")
    assert result.paths["base_memory_path"].endswith("memory.json")


@pytest.mark.live_api
def test_result_has_component_modes(tmp_path: Path) -> None:
    _require_live_env()
    result = run_homemaster_task(
        utterance="去厨房找水杯，然后拿给我",
        scenario="fetch_cup_retry",
        runtime_memory_root=tmp_path / "runs",
        debug_root=tmp_path / "debug",
        run_id="runner-component-modes",
    )
    # Stage02: single component
    assert result.stage_statuses["stage02"]["component_modes"]["task_understanding"] == "live_llm"

    # Stage03: two components
    assert result.stage_statuses["stage03"]["component_modes"]["memory_query"] == "live_llm"
    assert result.stage_statuses["stage03"]["component_modes"]["embedding"] == "live_embedding"

    # Stage05: four components
    cm5 = result.stage_statuses["stage05"]["component_modes"]
    assert cm5["planning"] == "live_llm"
    assert cm5["step_decision"] == "live_llm"
    assert cm5["skills"] == "simulated_skill"
    assert cm5["verification"] == "simulated_verification"

    # Stage06: two components
    assert result.stage_statuses["stage06"]["component_modes"]["summary"] == "live_llm"
    assert result.stage_statuses["stage06"]["component_modes"]["memory_commit"] == "programmatic"


def test_missing_services_raises(tmp_path: Path) -> None:
    with pytest.raises(HomeMasterRunError, match="required services unavailable"):
        run_homemaster_task(
            utterance="去厨房找水杯",
            scenario="fetch_cup_retry",
            runtime_memory_root=tmp_path / "runs",
            debug_root=tmp_path / "debug",
            run_id="runner-no-mock",
            config_path="/nonexistent/config.json",
        )


@pytest.mark.live_api
def test_stage_lifecycle_logging(tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    """P3: stage enter/exit/exception are logged with run_id and component_modes."""
    _require_live_env()
    # Force propagate=True so capfd can capture stderr output from the logger.
    # Other tests may have called setup_logging() which sets propagate=False.
    from homemaster.logger import get_logger, setup_logging
    logger = get_logger()
    logger.handlers.clear()
    logger.propagate = False
    setup_logging("INFO")

    run_homemaster_task(
        utterance="去厨房找水杯，然后拿给我",
        scenario="fetch_cup_retry",
        runtime_memory_root=tmp_path / "runs",
        debug_root=tmp_path / "debug",
        run_id="log-test",
    )
    captured = capfd.readouterr()
    lines = captured.err.splitlines()
    # Run header
    assert any("run started" in line and "log-test" in line for line in lines)
    # Each stage — modes appear in both started and completed logs
    for stage_name in ("stage02", "stage03", "stage04", "stage05", "stage06"):
        assert any(f"stage {stage_name} started" in line for line in lines), stage_name
        assert any(f"stage {stage_name} completed" in line for line in lines), stage_name
    # component_modes in started AND completed logs (P3: runtime_mode in all log types)
    started_lines = [ln for ln in lines if "started" in ln]
    assert any("task_understanding=live_llm" in ln and "stage02" in ln for ln in started_lines)
    assert any("modes=" in ln and "stage05" in ln for ln in started_lines)
    # Run footer
    assert any("run finished" in line for line in lines)

    # Cleanup: restore default state for other tests
    logger.handlers.clear()
    logger.propagate = True


def test_invalid_run_id_fails_before_file_write(tmp_path: Path) -> None:
    """Path-traversal run_id must fail before any file materialization."""
    with pytest.raises(InvalidRunIdError):
        run_homemaster_task(
            utterance="test",
            scenario="fetch_cup_retry",
            runtime_memory_root=tmp_path / "runs",
            debug_root=tmp_path / "debug",
            run_id="../escape",
        )


def test_run_homemaster_task_accepts_results_root() -> None:
    """Function signature must accept results_root parameter."""
    import inspect

    assert "results_root" in inspect.signature(run_homemaster_task).parameters


def test_run_homemaster_task_default_results_root_is_var() -> None:
    """Default results_root must point to var/homemaster/results, not plan/."""
    import inspect

    sig = inspect.signature(run_homemaster_task)
    default = sig.parameters["results_root"].default
    assert "var/homemaster" in str(default), f"Expected var/homemaster path, got {default}"
