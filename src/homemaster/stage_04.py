"""Stage 04 case runner for grounding and PlanningContext debug assets."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from homemaster.contracts import MemoryRetrievalResult, PlanningContext, TaskCard
from homemaster.planning_context import PlanningContextBuildResult, build_planning_context
from homemaster.runtime import LLM_CASE_ROOT, REPO_ROOT, TEST_RESULTS_ROOT, ensure_stage_directories
from homemaster.trace import append_jsonl_event, write_json

STAGE_04_CASE_ROOT = LLM_CASE_ROOT / "stage_04"
STAGE_04_RESULTS_DIR = TEST_RESULTS_ROOT / "stage_04"


@dataclass(frozen=True)
class Stage04CaseResult:
    passed: bool
    case_name: str
    context: PlanningContext
    build_result: PlanningContextBuildResult
    checks: dict[str, bool]
    case_dir: Path
    results_dir: Path


def run_stage_04_case(
    case_name: str,
    *,
    case_root: Path = STAGE_04_CASE_ROOT,
    results_dir: Path = STAGE_04_RESULTS_DIR,
) -> Stage04CaseResult:
    cases = stage_04_case_expectations()
    if case_name not in cases:
        raise ValueError(f"unknown Stage 04 case: {case_name}")
    expected = cases[case_name]
    case_dir = case_root / case_name
    ensure_stage_directories(case_dir=case_dir, results_dir=results_dir)

    task_card, memory_result = _load_stage_04_inputs(expected)
    world = json.loads((REPO_ROOT / expected["world_path"]).read_text(encoding="utf-8"))
    build_result = build_planning_context(task_card, memory_result, world)
    checks = _validate_stage_04_expected(build_result.context, expected)
    passed = all(checks.values())
    actual = {
        "case_name": case_name,
        "passed": passed,
        "task_card": task_card.model_dump(mode="json"),
        "memory_result": memory_result.model_dump(mode="json"),
        "world_path": expected["world_path"],
        "grounding": build_result.grounding.as_dict(),
        "planning_context": build_result.context.model_dump(mode="json"),
        "checks": checks,
    }
    _write_stage_04_assets(
        case_dir=case_dir,
        results_dir=results_dir,
        expected=expected,
        actual=actual,
        status="PASS" if passed else "FAIL",
    )
    return Stage04CaseResult(
        passed=passed,
        case_name=case_name,
        context=build_result.context,
        build_result=build_result,
        checks=checks,
        case_dir=case_dir,
        results_dir=results_dir,
    )


def stage_04_case_expectations() -> dict[str, dict[str, Any]]:
    return {
        "ground_cup_target": {
            "stage_03_case": "cup_object_memory_rag",
            "world_path": "data/scenarios/fetch_cup_retry/world.json",
            "expected_grounding_status": "grounded",
            "expected_selected_memory_id": "mem-cup-1",
        },
        "ground_medicine_target": {
            "stage_03_case": "medicine_object_memory_rag",
            "world_path": "data/scenarios/check_medicine_success/world.json",
            "expected_grounding_status": "grounded",
            "expected_selected_memory_id": "mem-medicine-1",
        },
        "ground_negative_evidence_target": {
            "stage_03_case": "negative_evidence_excludes_location",
            "world_path": "data/scenarios/object_not_found/world.json",
            "expected_grounding_status": "ungrounded",
            "expected_selected_memory_id": None,
        },
        "ungrounded_no_memory_context": {
            "stage_03_case": "cup_object_memory_rag",
            "world_path": "data/scenarios/fetch_cup_retry/world.json",
            "force_empty_hits": True,
            "expected_grounding_status": "ungrounded",
            "expected_selected_memory_id": None,
        },
        "ungrounded_all_hits_invalid": {
            "stage_03_case": "cup_object_memory_rag",
            "world_path": "data/scenarios/fetch_cup_retry/world.json",
            "force_missing_viewpoints": True,
            "expected_grounding_status": "ungrounded",
            "expected_selected_memory_id": None,
        },
        "planning_context_for_orchestration": {
            "stage_03_case": "cup_object_memory_rag",
            "world_path": "data/scenarios/fetch_cup_retry/world.json",
            "expected_grounding_status": "grounded",
            "expected_selected_memory_id": "mem-cup-1",
        },
    }


def _load_stage_04_inputs(expected: dict[str, Any]) -> tuple[TaskCard, MemoryRetrievalResult]:
    stage_03_case = str(expected["stage_03_case"])
    actual_path = LLM_CASE_ROOT / "stage_03" / stage_03_case / "actual.json"
    actual = json.loads(actual_path.read_text(encoding="utf-8"))
    task_card = TaskCard.model_validate(actual["task_card"])
    memory_result = MemoryRetrievalResult.model_validate(actual["memory_result"])
    if expected.get("force_empty_hits"):
        memory_result = memory_result.model_copy(update={"hits": []})
    if expected.get("force_missing_viewpoints"):
        memory_result = memory_result.model_copy(
            update={
                "hits": [
                    hit.model_copy(update={"viewpoint_id": "missing_viewpoint"})
                    for hit in memory_result.hits
                ]
            }
        )
    return task_card, memory_result


def _validate_stage_04_expected(
    context: PlanningContext,
    expected: dict[str, Any],
) -> dict[str, bool]:
    selected_memory_id = context.selected_target.memory_id if context.selected_target else None
    return {
        "grounding_status_matches": (
            context.runtime_state_summary.get("grounding_status")
            == expected["expected_grounding_status"]
        ),
        "selected_memory_matches": selected_memory_id == expected["expected_selected_memory_id"],
        "planning_context_serializes": (
            PlanningContext.model_validate_json(context.model_dump_json()) == context
        ),
    }


def _write_stage_04_assets(
    *,
    case_dir: Path,
    results_dir: Path,
    expected: dict[str, Any],
    actual: dict[str, Any],
    status: str,
) -> None:
    write_json(
        case_dir / "input.json",
        {
            "case_name": actual["case_name"],
            "task_card": actual["task_card"],
            "memory_result": actual["memory_result"],
            "world_path": actual["world_path"],
        },
    )
    write_json(case_dir / "expected.json", expected)
    write_json(case_dir / "actual.json", actual)
    _write_stage_04_markdown(
        case_dir / "result.md",
        expected=expected,
        actual=actual,
        status=status,
    )
    append_jsonl_event(
        results_dir / "llm_samples.jsonl",
        event="stage_04_grounding",
        payload=actual,
    )
    append_jsonl_event(
        results_dir / "trace" / f"{actual['case_name']}.jsonl",
        event="stage_04_grounding",
        payload=actual,
    )


def _write_stage_04_markdown(
    path: Path,
    *,
    expected: dict[str, Any],
    actual: dict[str, Any],
    status: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = f"""# Stage 04 Grounding Context - {actual["case_name"]}

Status: {status}

## Expected Conditions

```json
{json.dumps(expected, ensure_ascii=False, indent=2)}
```

## TaskCard

```json
{json.dumps(actual["task_card"], ensure_ascii=False, indent=2)}
```

## Stage03 Memory Hits

```json
{json.dumps(actual["memory_result"]["hits"], ensure_ascii=False, indent=2)}
```

## Hit Assessments

```json
{json.dumps(actual["grounding"]["assessments"], ensure_ascii=False, indent=2)}
```

## Selected Target

```json
{json.dumps(actual["grounding"]["selected_target"], ensure_ascii=False, indent=2)}
```

## PlanningContext

```json
{json.dumps(actual["planning_context"], ensure_ascii=False, indent=2)}
```

## Checks

```json
{json.dumps(actual["checks"], ensure_ascii=False, indent=2)}
```
"""
    path.write_text(text, encoding="utf-8")
