"""Stage 07 scenario matrix runner."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from homemaster.runtime import TEST_RESULTS_ROOT
from homemaster.task_runner import HomeMasterRunResult, run_homemaster_task
from homemaster.trace import write_json

STAGE_07_SCENARIOS: dict[str, str] = {
    "check_medicine_success": "去厨房看看药盒是不是还在。",
    "check_medicine_stale_recover": "去桌子那边看看药盒是不是还在。",
    "fetch_cup_retry": "去厨房找水杯，然后拿给我",
    "object_not_found": "去厨房找水杯，然后拿给我",
    "distractor_rejected": "去厨房找水杯，然后拿给我",
}

EXPECTED_FINAL_STATUS: dict[str, set[str]] = {
    "check_medicine_success": {"completed"},
    "check_medicine_stale_recover": {"completed"},
    "fetch_cup_retry": {"completed"},
    "object_not_found": {"failed"},
    "distractor_rejected": {"failed"},
}


@dataclass(frozen=True)
class Stage07ScenarioMatrixResult:
    passed: bool
    case_results: list[HomeMasterRunResult]
    acceptance_matrix_path: Path
    summary_path: Path


def run_stage_07_scenario_matrix(
    *,
    runtime_root: Path,
    debug_root: Path,
    live_models: bool,
    scenarios: dict[str, str] | list[str] | tuple[str, ...] = STAGE_07_SCENARIOS,
) -> Stage07ScenarioMatrixResult:
    items = _scenario_items(scenarios)
    case_results: list[HomeMasterRunResult] = []
    for scenario, utterance in items:
        run_id = f"stage07-{scenario}"
        case_results.append(
            run_homemaster_task(
                utterance=utterance,
                scenario=scenario,
                runtime_memory_root=runtime_root,
                debug_root=debug_root,
                run_id=run_id,
                live_models=live_models,
            )
        )

    matrix = _acceptance_matrix(case_results, live_models=live_models)
    results_dir = TEST_RESULTS_ROOT / "stage_07"
    acceptance_matrix_path = results_dir / "acceptance_matrix.json"
    summary_path = results_dir / "scenario_summary.md"
    write_json(acceptance_matrix_path, matrix)
    _write_summary(summary_path, matrix)
    return Stage07ScenarioMatrixResult(
        passed=bool(matrix["passed"]),
        case_results=case_results,
        acceptance_matrix_path=acceptance_matrix_path,
        summary_path=summary_path,
    )


def _scenario_items(
    scenarios: dict[str, str] | list[str] | tuple[str, ...],
) -> list[tuple[str, str]]:
    if isinstance(scenarios, dict):
        return list(scenarios.items())
    return [(name, STAGE_07_SCENARIOS[name]) for name in scenarios]


def _acceptance_matrix(
    results: list[HomeMasterRunResult],
    *,
    live_models: bool,
) -> dict[str, Any]:
    cases = []
    for result in results:
        expected = EXPECTED_FINAL_STATUS.get(result.scenario, {"completed", "failed"})
        case_passed = (
            result.final_status in expected
            and all(item.get("status") == "PASS" for item in result.stage_statuses.values())
        )
        cases.append(
            {
                "scenario": result.scenario,
                "run_id": result.run_id,
                "passed": case_passed,
                "expected_final_status": sorted(expected),
                "actual_final_status": result.final_status,
                "stage_statuses": result.stage_statuses,
                "debug_report": str(result.case_dir / "result.md"),
                "runtime_memory_root": str(result.runtime_memory_root),
                "task_record_path": str(result.runtime_memory_root / "task_records.jsonl"),
                "commit_log_path": str(result.runtime_memory_root / "commit_log.jsonl"),
            }
        )
    model_boundary = results[0].model_boundary if results else {}
    return {
        "passed": all(case["passed"] for case in cases),
        "live_models": live_models,
        "model_boundary": model_boundary,
        "cases": cases,
    }


def _write_summary(path: Path, matrix: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = f"""# Stage 07 Scenario Matrix

Status: {"PASS" if matrix["passed"] else "FAIL"}

## Model And Skill Boundary

```json
{json.dumps(matrix["model_boundary"], ensure_ascii=False, indent=2)}
```

## Cases

```json
{json.dumps(matrix["cases"], ensure_ascii=False, indent=2)}
```
"""
    path.write_text(text, encoding="utf-8")
