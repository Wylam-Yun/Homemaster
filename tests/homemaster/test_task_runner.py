from __future__ import annotations

from pathlib import Path

import pytest

from homemaster.runtime import REPO_ROOT
from homemaster.task_runner import HomeMasterRunError, run_homemaster_task


def test_task_runner_non_live_runs_stage_02_to_06_and_isolates_memory(tmp_path: Path) -> None:
    result = run_homemaster_task(
        utterance="去厨房找水杯，然后拿给我",
        scenario="fetch_cup_retry",
        runtime_memory_root=tmp_path / "runs",
        debug_root=tmp_path / "debug",
        run_id="runner-fetch-cup",
        live_models=False,
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


def test_task_runner_accepts_explicit_world_and_memory_paths(tmp_path: Path) -> None:
    scenario_root = REPO_ROOT / "data" / "scenarios" / "check_medicine_success"
    result = run_homemaster_task(
        utterance="去厨房看看药盒是不是还在",
        scenario="check_medicine_success",
        world_path=scenario_root / "world.json",
        memory_path=scenario_root / "memory.json",
        runtime_memory_root=tmp_path / "runs",
        debug_root=tmp_path / "debug",
        run_id="runner-medicine",
        live_models=False,
    )

    assert result.task_card is not None
    assert result.task_card.target in {"药盒", "药"}
    assert result.paths["world_path"].endswith("world.json")
    assert result.paths["base_memory_path"].endswith("memory.json")


def test_result_has_component_modes(tmp_path: Path) -> None:
    result = run_homemaster_task(
        utterance="去厨房找水杯，然后拿给我",
        scenario="fetch_cup_retry",
        runtime_memory_root=tmp_path / "runs",
        debug_root=tmp_path / "debug",
        run_id="runner-component-modes",
        live_models=False,
    )
    # Stage02: single component
    assert result.stage_statuses["stage02"]["component_modes"]["task_understanding"] == "test_double"
    assert result.stage_statuses["stage02"]["mode"] == "deterministic"  # legacy compat

    # Stage03: two components
    assert result.stage_statuses["stage03"]["component_modes"]["memory_query"] == "test_double"
    assert result.stage_statuses["stage03"]["component_modes"]["embedding"] == "test_double"

    # Stage05: five components
    cm5 = result.stage_statuses["stage05"]["component_modes"]
    assert cm5["planning"] == "test_double"
    assert cm5["step_decision"] == "test_double"
    assert cm5["step_decision_smoke"] == "n/a"
    assert cm5["skills"] == "mock_skill"
    assert cm5["verification"] == "mock_symbolic"

    # Stage06: two components
    assert result.stage_statuses["stage06"]["component_modes"]["summary"] == "test_double"
    assert result.stage_statuses["stage06"]["component_modes"]["memory_commit"] == "programmatic"


def test_mock_skills_false_raises(tmp_path: Path) -> None:
    with pytest.raises(HomeMasterRunError, match="mock_skills=False is not supported"):
        run_homemaster_task(
            utterance="去厨房找水杯",
            scenario="fetch_cup_retry",
            runtime_memory_root=tmp_path / "runs",
            debug_root=tmp_path / "debug",
            run_id="runner-no-mock",
            live_models=False,
            mock_skills=False,
        )


def test_stage_lifecycle_logging(tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    """P3: stage enter/exit/exception are logged with run_id and component_modes."""
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
        live_models=False,
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
    started_lines = [l for l in lines if "started" in l]
    assert any("task_understanding=test_double" in l and "stage02" in l for l in started_lines)
    assert any("modes=" in l and "stage05" in l for l in started_lines)
    # Run footer
    assert any("run finished" in line for line in lines)

    # Cleanup: restore default state for other tests
    logger.handlers.clear()
    logger.propagate = True
