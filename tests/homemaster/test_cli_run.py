from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from homemaster.cli import app


def test_cli_run_requires_explicit_scenario_context(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "run",
            "--utterance",
            "去厨房找水杯，然后拿给我",
            "--run-id",
            "missing-scenario",
            "--runtime-memory-root",
            str(tmp_path / "memory"),
            "--debug-root",
            str(tmp_path / "debug"),
            "--no-live-models",
        ],
    )

    assert result.exit_code != 0
    assert "scenario" in result.stdout or "scenario" in result.stderr


def test_cli_run_non_live_writes_debug_and_runtime_memory(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runs"
    debug_root = tmp_path / "debug"
    result = CliRunner().invoke(
        app,
        [
            "run",
            "--utterance",
            "去厨房找水杯，然后拿给我",
            "--scenario",
            "fetch_cup_retry",
            "--run-id",
            "cli-fetch-cup",
            "--runtime-memory-root",
            str(runtime_root),
            "--debug-root",
            str(debug_root),
            "--no-live-models",
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert "final_status" in result.stdout
    assert "cli-fetch-cup" in result.stdout
    assert (runtime_root / "cli-fetch-cup" / "memory" / "object_memory.json").is_file()
    assert (runtime_root / "cli-fetch-cup" / "memory" / "task_records.jsonl").is_file()
    assert (debug_root / "stage_07" / "cli-fetch-cup" / "result.md").is_file()
