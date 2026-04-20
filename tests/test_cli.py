from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from task_brain.cli import app

_CHECK_MEDICINE_INSTRUCTION = "去桌子那边看看药盒是不是还在。"
_FETCH_CUP_INSTRUCTION = "去厨房找水杯，然后拿给我"

_runner = CliRunner()


def test_cli_runs_check_medicine_success() -> None:
    result = _runner.invoke(
        app,
        [
            "run",
            "--scenario",
            "check_medicine_success",
            "--instruction",
            _CHECK_MEDICINE_INSTRUCTION,
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert "scenario: check_medicine_success" in result.stdout
    assert "final_status: success" in result.stdout


def test_cli_outputs_retrieve_memory_before_generate_plan() -> None:
    result = _runner.invoke(
        app,
        [
            "run",
            "--scenario",
            "check_medicine_success",
            "--instruction",
            _CHECK_MEDICINE_INSTRUCTION,
        ],
    )

    assert result.exit_code == 0, result.stdout
    retrieve_index = result.stdout.find("retrieve_memory")
    generate_index = result.stdout.find("generate_plan")
    final_verify_index = result.stdout.find("final_task_verification")

    assert retrieve_index != -1
    assert generate_index != -1
    assert final_verify_index != -1
    assert retrieve_index < generate_index < final_verify_index


def test_cli_failed_task_exits_nonzero() -> None:
    result = _runner.invoke(
        app,
        [
            "run",
            "--scenario",
            "object_not_found",
            "--instruction",
            _FETCH_CUP_INSTRUCTION,
        ],
    )

    assert result.exit_code != 0
    assert "final_status: failed" in result.stdout


def test_cli_trace_contains_failure_analysis_for_recovery_case() -> None:
    result = _runner.invoke(
        app,
        [
            "run",
            "--scenario",
            "check_medicine_stale_recover",
            "--instruction",
            _CHECK_MEDICINE_INSTRUCTION,
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert "analyze_failure" in result.stdout
    assert "decide_recovery" in result.stdout


def test_cli_trace_jsonl_writes_event_lines(tmp_path: Path) -> None:
    output_path = tmp_path / "trace" / "run.jsonl"
    result = _runner.invoke(
        app,
        [
            "run",
            "--scenario",
            "check_medicine_success",
            "--instruction",
            _CHECK_MEDICINE_INSTRUCTION,
            "--trace-jsonl",
            str(output_path),
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert output_path.exists()

    lines = output_path.read_text(encoding="utf-8").splitlines()
    assert lines

    records = [json.loads(line) for line in lines]
    assert all(record["scenario"] == "check_medicine_success" for record in records)
    assert all(record["final_status"] == "success" for record in records)
    assert all(
        "index" in record and "event" in record and "payload" in record for record in records
    )
    assert any(record["event"] == "retrieve_memory" for record in records)
