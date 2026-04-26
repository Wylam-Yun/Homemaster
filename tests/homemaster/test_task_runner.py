from __future__ import annotations

from pathlib import Path

from homemaster.runtime import REPO_ROOT
from homemaster.task_runner import run_homemaster_task


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
