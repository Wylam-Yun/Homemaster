"""Stage 06 summary, evidence, and memory writeback case runners."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from homemaster.contracts import (
    EvidenceBundle,
    ExecutionState,
    FailureRecord,
    MemoryCommitPlan,
    ModuleExecutionResult,
    OrchestrationPlan,
    PlanningContext,
    Subtask,
    SubtaskRuntimeState,
    TaskSummary,
    VerificationResult,
)
from homemaster.fact_memory import append_fact_memory_writes
from homemaster.memory_commit import build_evidence_bundle, build_memory_commit_plan
from homemaster.runtime import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_PROVIDER_NAME,
    LLM_CASE_ROOT,
    REPO_ROOT,
    TEST_RESULTS_ROOT,
    ensure_stage_directories,
    load_provider_config,
)
from homemaster.runtime_memory_store import RuntimeMemoryStore
from homemaster.summary import TaskSummaryGenerationError, generate_task_summary
from homemaster.task_record import append_commit_log, append_task_record
from homemaster.trace import append_jsonl_event, sanitize_for_log, write_json

STAGE_06_CASE_ROOT = LLM_CASE_ROOT / "stage_06"
STAGE_06_RESULTS_DIR = TEST_RESULTS_ROOT / "stage_06"
DEFAULT_RUNTIME_MEMORY_ROOT = REPO_ROOT / "var" / "homemaster" / "memory"


@dataclass(frozen=True)
class Stage06CaseInputs:
    task_id: str
    case_name: str
    planning_context: PlanningContext
    orchestration_plan: OrchestrationPlan
    execution_state: ExecutionState
    skill_results: list[ModuleExecutionResult]
    verification_results: list[VerificationResult]
    failure_records: list[FailureRecord]
    trace_events: list[dict[str, Any]]
    base_memory_path: Path


@dataclass(frozen=True)
class Stage06CaseResult:
    passed: bool
    case_name: str
    task_summary: TaskSummary
    evidence_bundle: EvidenceBundle
    memory_commit_plan: MemoryCommitPlan
    checks: dict[str, bool]
    case_dir: Path
    results_dir: Path


def run_live_stage_06_summary_memory_case(
    case_name: str,
    *,
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    provider_name: str = DEFAULT_PROVIDER_NAME,
    case_root: Path = STAGE_06_CASE_ROOT,
    results_dir: Path = STAGE_06_RESULTS_DIR,
    memory_root: Path = DEFAULT_RUNTIME_MEMORY_ROOT,
) -> Stage06CaseResult:
    cases = _stage_06_cases()
    if case_name not in cases:
        raise ValueError(f"unknown Stage 06 case: {case_name}")
    expected = cases[case_name]
    inputs = build_stage_06_case_inputs(case_name)
    case_dir = case_root / case_name
    ensure_stage_directories(case_dir=case_dir, results_dir=results_dir)
    provider = load_provider_config(config_path, provider_name=provider_name)
    evidence_bundle = build_evidence_bundle(
        task_id=inputs.task_id,
        verification_results=inputs.verification_results,
        skill_results=inputs.skill_results,
        failure_records=inputs.failure_records,
        trace_events=inputs.trace_events,
        created_at="2026-04-26T00:00:00Z",
    )

    try:
        summary_result = generate_task_summary(
            task_card=inputs.planning_context.task_card,
            execution_state=inputs.execution_state,
            evidence_bundle=evidence_bundle,
            provider=provider,
        )
    except TaskSummaryGenerationError as exc:
        actual = {
            "case_name": case_name,
            "passed": False,
            "provider": provider.public_summary(),
            "input": _input_payload(inputs),
            "evidence_bundle": evidence_bundle.model_dump(mode="json"),
            "attempts": exc.attempts,
            "error_type": exc.error_type,
            "message": exc.message,
        }
        write_stage_06_debug_assets(
            case_dir=case_dir,
            results_dir=results_dir,
            expected=expected,
            actual=actual,
            status="FAIL",
        )
        raise

    commit_plan = build_memory_commit_plan(
        task_id=inputs.task_id,
        task_card=inputs.planning_context.task_card,
        planning_context=inputs.planning_context,
        orchestration_plan=inputs.orchestration_plan,
        execution_state=inputs.execution_state,
        evidence_bundle=evidence_bundle,
        task_summary=summary_result.summary,
        completed_at="2026-04-26T00:01:00Z",
        started_at="2026-04-26T00:00:00Z",
    )
    persistence = persist_stage_06_commit(
        memory_root=memory_root,
        base_memory_path=inputs.base_memory_path,
        plan=commit_plan,
        task_id=inputs.task_id,
    )
    checks = _stage_06_checks(case_name, summary_result.summary, commit_plan)
    passed = all(checks.values())
    actual = {
        "case_name": case_name,
        "passed": passed,
        "provider": summary_result.provider,
        "input": _input_payload(inputs),
        "summary_prompt": summary_result.prompt,
        "raw_response": summary_result.raw_response,
        "parsed_json": summary_result.parsed_json,
        "task_summary": summary_result.summary.model_dump(mode="json"),
        "attempts": list(summary_result.attempts),
        "evidence_bundle": evidence_bundle.model_dump(mode="json"),
        "memory_commit_plan": commit_plan.model_dump(mode="json"),
        "persistence": persistence,
        "checks": checks,
    }
    write_stage_06_debug_assets(
        case_dir=case_dir,
        results_dir=results_dir,
        expected=expected,
        actual=actual,
        status="PASS" if passed else "FAIL",
    )
    return Stage06CaseResult(
        passed=passed,
        case_name=case_name,
        task_summary=summary_result.summary,
        evidence_bundle=evidence_bundle,
        memory_commit_plan=commit_plan,
        checks=checks,
        case_dir=case_dir,
        results_dir=results_dir,
    )


def build_stage_06_case_inputs(case_name: str) -> Stage06CaseInputs:
    scenario_root = REPO_ROOT / "data" / "scenarios"
    if case_name == "check_medicine_summary_memory_live":
        context = _load_stage_04_context("ground_medicine_target")
        plan = OrchestrationPlan(
            goal="确认药盒是否还在",
            subtasks=[
                Subtask(
                    id="check_medicine",
                    intent="观察并确认药盒是否存在",
                    target_object="药盒",
                    success_criteria=["确认药盒是否在视野中"],
                )
            ],
            confidence=0.9,
        )
        return Stage06CaseInputs(
            task_id="stage06-check-medicine",
            case_name=case_name,
            planning_context=context,
            orchestration_plan=plan,
            execution_state=ExecutionState(
                task_status="completed",
                subtasks=[SubtaskRuntimeState(subtask_id="check_medicine", status="verified")],
            ),
            skill_results=[
                ModuleExecutionResult(
                    skill="navigation",
                    status="success",
                    observation={
                        "target_object_visible": True,
                        "target_object_location": context.selected_target.display_text
                        if context.selected_target
                        else "观察位置",
                    },
                )
            ],
            verification_results=[
                VerificationResult(
                    scope="subtask",
                    passed=True,
                    verified_facts=["药盒在目标观察位置被看到"],
                    confidence=0.91,
                )
            ],
            failure_records=[],
            trace_events=[{"event_id": "stage06-check-medicine", "summary": "medicine visible"}],
            base_memory_path=scenario_root / "check_medicine_success" / "memory.json",
        )

    if case_name == "fetch_cup_success_fact_memory_live":
        context = _load_stage_04_context("ground_cup_target")
        plan = OrchestrationPlan(
            goal="取水杯并交付给用户",
            subtasks=[
                Subtask(
                    id="find_cup",
                    intent="找到水杯",
                    target_object="水杯",
                    success_criteria=["看到水杯"],
                ),
                Subtask(
                    id="deliver_cup",
                    intent="交付水杯给用户",
                    target_object="水杯",
                    recipient="user",
                    depends_on=["find_cup"],
                    success_criteria=["水杯已交付用户"],
                ),
            ],
            confidence=0.9,
        )
        return Stage06CaseInputs(
            task_id="stage06-fetch-cup",
            case_name=case_name,
            planning_context=context,
            orchestration_plan=plan,
            execution_state=ExecutionState(
                task_status="completed",
                held_object=None,
                subtasks=[
                    SubtaskRuntimeState(subtask_id="find_cup", status="verified"),
                    SubtaskRuntimeState(
                        subtask_id="deliver_cup",
                        status="verified",
                        depends_on=["find_cup"],
                    ),
                ],
            ),
            skill_results=[
                ModuleExecutionResult(
                    skill="navigation",
                    status="success",
                    observation={
                        "target_object_visible": True,
                        "target_object_location": "厨房餐桌",
                    },
                ),
                ModuleExecutionResult(
                    skill="operation",
                    status="success",
                    observation={"delivered_object": "水杯", "delivery_complete": True},
                ),
            ],
            verification_results=[
                VerificationResult(
                    scope="subtask",
                    passed=True,
                    verified_facts=["水杯在厨房餐桌被观察到"],
                    confidence=0.95,
                ),
                VerificationResult(
                    scope="task",
                    passed=True,
                    verified_facts=["水杯已交付给用户"],
                    confidence=0.93,
                ),
            ],
            failure_records=[],
            trace_events=[{"event_id": "stage06-fetch-cup", "summary": "cup delivered"}],
            base_memory_path=scenario_root / "fetch_cup_retry" / "memory.json",
        )

    if case_name == "object_not_found_summary_memory_live":
        context = _load_stage_04_context("ground_cup_target")
        plan = OrchestrationPlan(
            goal="寻找水杯",
            subtasks=[
                Subtask(
                    id="find_cup",
                    intent="找到水杯",
                    target_object="水杯",
                    success_criteria=["看到水杯"],
                )
            ],
            confidence=0.8,
        )
        failure = FailureRecord(
            failure_id="failure-object-not-found",
            subtask_id="find_cup",
            subtask_intent="找到水杯",
            skill="navigation",
            failure_type="object_not_found",
            failed_reason="厨房餐桌没有观察到水杯",
            negative_evidence=[
                {
                    "memory_id": "mem-cup-1",
                    "location_key": "kitchen:anchor_kitchen_table_1",
                    "reason": "not_visible",
                }
            ],
            created_at="2026-04-26T00:00:00Z",
        )
        return Stage06CaseInputs(
            task_id="stage06-object-not-found",
            case_name=case_name,
            planning_context=context,
            orchestration_plan=plan,
            execution_state=ExecutionState(
                task_status="failed",
                subtasks=[SubtaskRuntimeState(subtask_id="find_cup", status="failed")],
                failure_record_ids=[failure.failure_id],
            ),
            skill_results=[
                ModuleExecutionResult(
                    skill="navigation",
                    status="success",
                    observation={
                        "target_object_visible": False,
                        "target_object_location": "厨房餐桌",
                    },
                )
            ],
            verification_results=[
                VerificationResult(
                    scope="subtask",
                    passed=False,
                    missing_evidence=["未看到水杯"],
                    failed_reason="厨房餐桌没有观察到水杯",
                    confidence=0.82,
                )
            ],
            failure_records=[failure],
            trace_events=[
                {"event_id": "stage06-object-not-found", "summary": "cup not visible"}
            ],
            base_memory_path=scenario_root / "fetch_cup_retry" / "memory.json",
        )

    raise ValueError(f"unknown Stage 06 case: {case_name}")


def persist_stage_06_commit(
    *,
    memory_root: Path,
    base_memory_path: Path,
    plan: MemoryCommitPlan,
    task_id: str,
) -> dict[str, Any]:
    store = RuntimeMemoryStore(memory_root)
    object_memory_path = store.apply_commit_plan(base_memory_path=base_memory_path, plan=plan)
    fact_count = append_fact_memory_writes(
        memory_root / "fact_memory.jsonl",
        plan.fact_memory_writes,
    )
    task_count = append_task_record(memory_root / "task_records.jsonl", plan.task_record)
    commit_record = append_commit_log(
        memory_root / "commit_log.jsonl",
        plan=plan,
        task_id=task_id,
        object_memory_path=str(object_memory_path),
    )
    return {
        "object_memory_path": str(object_memory_path),
        "fact_memory_write_count": fact_count,
        "task_record_write_count": task_count,
        "commit_log": commit_record,
    }


def write_stage_06_debug_assets(
    *,
    case_dir: Path,
    results_dir: Path,
    expected: dict[str, Any],
    actual: dict[str, Any],
    status: str,
) -> None:
    ensure_stage_directories(case_dir=case_dir, results_dir=results_dir)
    safe_expected = sanitize_for_log(expected)
    safe_actual = sanitize_for_log(actual)
    write_json(case_dir / "input.json", safe_actual.get("input", {}))
    write_json(case_dir / "expected.json", safe_expected)
    write_json(case_dir / "actual.json", safe_actual)
    _write_stage_06_markdown(case_dir / "result.md", safe_expected, safe_actual, status)
    append_jsonl_event(results_dir / "llm_samples.jsonl", event="stage_06", payload=actual)
    append_jsonl_event(
        results_dir / "trace" / f"{actual.get('case_name', case_dir.name)}.jsonl",
        event="stage_06",
        payload=actual,
    )


def _write_stage_06_markdown(
    path: Path,
    expected: dict[str, Any],
    actual: dict[str, Any],
    status: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = f"""# Stage 06 Summary And Memory Writeback - {actual.get("case_name", path.parent.name)}

Status: {status}

## Expected Conditions

```json
{json.dumps(expected, ensure_ascii=False, indent=2)}
```

## Stage05 Input Summary

```json
{json.dumps(actual.get("input"), ensure_ascii=False, indent=2)}
```

## Summary Prompt

```text
{actual.get("summary_prompt", "")}
```

## Mimo Raw Response

```text
{actual.get("raw_response", "")}
```

## Parsed TaskSummary

```json
{json.dumps(actual.get("task_summary"), ensure_ascii=False, indent=2)}
```

## EvidenceBundle

```json
{json.dumps(actual.get("evidence_bundle"), ensure_ascii=False, indent=2)}
```

## MemoryCommitPlan

```json
{json.dumps(actual.get("memory_commit_plan"), ensure_ascii=False, indent=2)}
```

## Persistence

```json
{json.dumps(actual.get("persistence"), ensure_ascii=False, indent=2)}
```

## Checks

```json
{json.dumps(actual.get("checks", {}), ensure_ascii=False, indent=2)}
```
"""
    path.write_text(text, encoding="utf-8")


def _input_payload(inputs: Stage06CaseInputs) -> dict[str, Any]:
    return {
        "task_id": inputs.task_id,
        "planning_context": inputs.planning_context.model_dump(mode="json"),
        "orchestration_plan": inputs.orchestration_plan.model_dump(mode="json"),
        "execution_state": inputs.execution_state.model_dump(mode="json"),
        "skill_results": [item.model_dump(mode="json") for item in inputs.skill_results],
        "verification_results": [
            item.model_dump(mode="json") for item in inputs.verification_results
        ],
        "failure_records": [item.model_dump(mode="json") for item in inputs.failure_records],
        "trace_events": inputs.trace_events,
        "base_memory_path": str(inputs.base_memory_path),
    }


def _stage_06_checks(
    case_name: str,
    summary: TaskSummary,
    commit: MemoryCommitPlan,
) -> dict[str, bool]:
    checks = {
        "summary_has_evidence_refs": bool(summary.evidence_refs),
        "task_record_written": commit.task_record is not None,
        "commit_has_id": bool(commit.commit_id),
        "all_fact_memory_non_searchable": all(
            write.searchable is False for write in commit.fact_memory_writes
        ),
    }
    if case_name == "check_medicine_summary_memory_live":
        checks["summary_success"] = summary.result == "success"
        checks["object_memory_updated"] = bool(commit.object_memory_updates)
        checks["has_object_seen_fact"] = any(
            write.fact_type == "object_seen" for write in commit.fact_memory_writes
        )
    if case_name == "fetch_cup_success_fact_memory_live":
        checks["summary_success"] = summary.result == "success"
        checks["has_delivery_fact"] = any(
            write.fact_type == "delivery_verified" for write in commit.fact_memory_writes
        )
        checks["only_existing_object_memory_updated"] = all(
            update.memory_id == "mem-cup-1" for update in commit.object_memory_updates
        )
    if case_name == "object_not_found_summary_memory_live":
        checks["summary_failed"] = summary.result == "failed"
        checks["has_negative_fact"] = any(
            write.fact_type == "object_not_seen" for write in commit.fact_memory_writes
        )
        checks["does_not_create_new_object_memory"] = all(
            update.update_type != "confirm" for update in commit.object_memory_updates
        )
        checks["negative_evidence_has_failure_record"] = all(
            item.get("failure_record_id") for item in commit.negative_evidence
        )
    return checks


def _stage_06_cases() -> dict[str, dict[str, Any]]:
    return {
        "check_medicine_summary_memory_live": {
            "expected_behavior": (
                "药盒查看成功生成 summary、object memory confirm update、task record"
            ),
        },
        "fetch_cup_success_fact_memory_live": {
            "expected_behavior": "取水杯成功生成 object_seen 和 delivery_verified fact memory",
        },
        "object_not_found_summary_memory_live": {
            "expected_behavior": "目标未找到生成 scoped negative fact，不新建位置",
        },
    }


def _load_stage_04_context(stage_04_case: str) -> PlanningContext:
    path = LLM_CASE_ROOT / "stage_04" / stage_04_case / "actual.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    return PlanningContext.model_validate(payload["planning_context"])
