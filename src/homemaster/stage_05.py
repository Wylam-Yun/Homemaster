"""Stage 05 live case runners and debug asset writers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from homemaster.contracts import (
    ExecutionState,
    OrchestrationPlan,
    PlanningContext,
    StepDecision,
    Subtask,
)
from homemaster.orchestrator import (
    OrchestrationGenerationError,
    generate_orchestration_plan,
)
from homemaster.runtime import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_PROVIDER_NAME,
    LLM_CASE_ROOT,
    TEST_RESULTS_ROOT,
    ensure_stage_directories,
    load_provider_config,
)
from homemaster.skill_registry import validate_skill_input
from homemaster.skill_selector import (
    StepDecisionGenerationError,
    generate_step_decision,
)
from homemaster.trace import append_jsonl_event, sanitize_for_log, write_json

STAGE_05_CASE_ROOT = LLM_CASE_ROOT / "stage_05"
STAGE_05_RESULTS_DIR = TEST_RESULTS_ROOT / "stage_05"


@dataclass(frozen=True)
class Stage05OrchestrationCaseResult:
    passed: bool
    case_name: str
    context: PlanningContext
    plan: OrchestrationPlan
    checks: dict[str, bool]
    case_dir: Path
    results_dir: Path


@dataclass(frozen=True)
class Stage05StepCaseResult:
    passed: bool
    case_name: str
    context: PlanningContext
    subtask: Subtask
    state: ExecutionState
    decision: StepDecision
    checks: dict[str, bool]
    case_dir: Path
    results_dir: Path


def run_live_stage_05_orchestration_case(
    case_name: str,
    *,
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    provider_name: str = DEFAULT_PROVIDER_NAME,
    case_root: Path = STAGE_05_CASE_ROOT,
    results_dir: Path = STAGE_05_RESULTS_DIR,
) -> Stage05OrchestrationCaseResult:
    cases = _orchestration_cases()
    if case_name not in cases:
        raise ValueError(f"unknown Stage 05 orchestration case: {case_name}")
    expected = cases[case_name]
    case_dir = case_root / case_name
    ensure_stage_directories(case_dir=case_dir, results_dir=results_dir)
    context = _load_stage_04_context(expected["stage_04_case"])
    provider = load_provider_config(config_path, provider_name=provider_name)

    try:
        generated = generate_orchestration_plan(context, provider)
    except OrchestrationGenerationError as exc:
        actual = {
            "case_name": case_name,
            "passed": False,
            "provider": provider.public_summary(),
            "planning_context": context.model_dump(mode="json"),
            "attempts": exc.attempts,
            "error_type": exc.error_type,
            "message": exc.message,
        }
        write_stage_05_debug_assets(
            case_dir=case_dir,
            results_dir=results_dir,
            expected=expected,
            actual=actual,
            status="FAIL",
        )
        raise

    checks = _orchestration_checks(case_name, generated.plan)
    passed = all(checks.values())
    actual = {
        "case_name": case_name,
        "passed": passed,
        "provider": generated.provider,
        "planning_context": context.model_dump(mode="json"),
        "orchestration_prompt": generated.prompt,
        "raw_response": generated.raw_response,
        "parsed_json": generated.parsed_json,
        "orchestration_plan": generated.plan.model_dump(mode="json"),
        "attempts": list(generated.attempts),
        "checks": checks,
    }
    write_stage_05_debug_assets(
        case_dir=case_dir,
        results_dir=results_dir,
        expected=expected,
        actual=actual,
        status="PASS" if passed else "FAIL",
    )
    return Stage05OrchestrationCaseResult(
        passed=passed,
        case_name=case_name,
        context=context,
        plan=generated.plan,
        checks=checks,
        case_dir=case_dir,
        results_dir=results_dir,
    )


def run_live_stage_05_step_case(
    case_name: str,
    *,
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    provider_name: str = DEFAULT_PROVIDER_NAME,
    case_root: Path = STAGE_05_CASE_ROOT,
    results_dir: Path = STAGE_05_RESULTS_DIR,
) -> Stage05StepCaseResult:
    cases = _step_cases()
    if case_name not in cases:
        raise ValueError(f"unknown Stage 05 step case: {case_name}")
    expected = cases[case_name]
    case_dir = case_root / case_name
    ensure_stage_directories(case_dir=case_dir, results_dir=results_dir)
    context = _load_stage_04_context(expected["stage_04_case"])
    subtask = Subtask.model_validate(expected["subtask"])
    state = ExecutionState.model_validate(expected["state"])
    provider = load_provider_config(config_path, provider_name=provider_name)

    try:
        generated = generate_step_decision(subtask, state, context, provider)
    except StepDecisionGenerationError as exc:
        actual = {
            "case_name": case_name,
            "passed": False,
            "provider": provider.public_summary(),
            "planning_context": context.model_dump(mode="json"),
            "subtask": subtask.model_dump(mode="json"),
            "execution_state": state.model_dump(mode="json"),
            "attempts": exc.attempts,
            "error_type": exc.error_type,
            "message": exc.message,
        }
        write_stage_05_debug_assets(
            case_dir=case_dir,
            results_dir=results_dir,
            expected=expected,
            actual=actual,
            status="FAIL",
        )
        raise

    checks = {
        "expected_skill_matches": generated.decision.selected_skill
        == expected["expected_skill"],
        "skill_input_valid": _skill_input_valid(generated.decision),
        "does_not_select_verification": generated.decision.selected_skill != "verification",
    }
    passed = all(checks.values())
    actual = {
        "case_name": case_name,
        "passed": passed,
        "provider": generated.provider,
        "planning_context": context.model_dump(mode="json"),
        "subtask": subtask.model_dump(mode="json"),
        "execution_state": state.model_dump(mode="json"),
        "step_decision_prompt": generated.prompt,
        "raw_response": generated.raw_response,
        "parsed_json": generated.parsed_json,
        "step_decision": generated.decision.model_dump(mode="json"),
        "attempts": list(generated.attempts),
        "checks": checks,
    }
    write_stage_05_debug_assets(
        case_dir=case_dir,
        results_dir=results_dir,
        expected=expected,
        actual=actual,
        status="PASS" if passed else "FAIL",
    )
    return Stage05StepCaseResult(
        passed=passed,
        case_name=case_name,
        context=context,
        subtask=subtask,
        state=state,
        decision=generated.decision,
        checks=checks,
        case_dir=case_dir,
        results_dir=results_dir,
    )


def write_stage_05_debug_assets(
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
    _write_stage_05_markdown(case_dir / "result.md", safe_expected, safe_actual, status)
    append_jsonl_event(
        results_dir / "llm_samples.jsonl",
        event="stage_05",
        payload=actual,
    )
    append_jsonl_event(
        results_dir / "trace" / f"{actual.get('case_name', case_dir.name)}.jsonl",
        event="stage_05",
        payload=actual,
    )


def _write_stage_05_markdown(
    path: Path,
    expected: dict[str, Any],
    actual: dict[str, Any],
    status: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    execution = actual.get("execution")
    execution_payload = execution if isinstance(execution, dict) else {}
    failure_records = execution_payload.get("failure_records", actual.get("failure_records"))
    state_and_failures = {
        "final_state": execution_payload.get("final_state"),
        "failure_records": failure_records,
    }
    text = f"""# Stage 05 Skill Execution Loop - {actual.get("case_name", path.parent.name)}

Status: {status}

## Expected Conditions

```json
{json.dumps(expected, ensure_ascii=False, indent=2)}
```

## PlanningContext

```json
{json.dumps(actual.get("planning_context"), ensure_ascii=False, indent=2)}
```

## Orchestration Prompt And Raw Response

```text
{actual.get("orchestration_prompt", "")}
```

```text
{actual.get("raw_response", "")}
```

## StepDecision / Skill / Verification Trace

```json
{json.dumps({
    "step_decision_prompt": actual.get("step_decision_prompt"),
    "step_decision": actual.get("step_decision"),
    "execution": actual.get("execution"),
}, ensure_ascii=False, indent=2)}
```

## ExecutionState / Failure Records

```json
{json.dumps(state_and_failures, ensure_ascii=False, indent=2)}
```

## Checks

```json
{json.dumps(actual.get("checks", {}), ensure_ascii=False, indent=2)}
```

## Full Actual

```json
{json.dumps(actual, ensure_ascii=False, indent=2)}
```
"""
    path.write_text(text, encoding="utf-8")


def _orchestration_cases() -> dict[str, dict[str, Any]]:
    return {
        "check_medicine_orchestration_live": {
            "stage_04_case": "ground_medicine_target",
            "expected_behavior": "查看药盒，不生成拿取/交付动作",
        },
        "fetch_cup_orchestration_live": {
            "stage_04_case": "ground_cup_target",
            "expected_behavior": "找到水杯、拿起、回用户位置、交付、确认",
        },
        "ungrounded_exploration_live": {
            "stage_04_case": "ungrounded_no_memory_context",
            "expected_behavior": "没有 reliable memory target 时生成探索/寻找/观察或追问计划",
        },
    }


def _step_cases() -> dict[str, dict[str, Any]]:
    return {
        "find_cup_step_decision_live": {
            "stage_04_case": "ground_cup_target",
            "expected_skill": "navigation",
            "subtask": {
                "id": "find_cup",
                "intent": "找到水杯",
                "target_object": "水杯",
                "room_hint": "厨房",
                "anchor_hint": "餐桌",
                "success_criteria": ["观察到水杯"],
            },
            "state": {
                "task_status": "running",
                "current_subtask_id": "find_cup",
                "subtasks": [{"subtask_id": "find_cup", "status": "running"}],
                "target_object_visible": False,
                "user_location": "客厅沙发旁",
            },
        },
        "pick_cup_step_decision_live": {
            "stage_04_case": "ground_cup_target",
            "expected_skill": "operation",
            "subtask": {
                "id": "pick_cup",
                "intent": "拿起水杯",
                "target_object": "水杯",
                "success_criteria": ["确认水杯已经被拿起"],
                "depends_on": ["find_cup"],
            },
            "state": {
                "task_status": "running",
                "current_subtask_id": "pick_cup",
                "subtasks": [
                    {"subtask_id": "find_cup", "status": "verified"},
                    {
                        "subtask_id": "pick_cup",
                        "status": "running",
                        "depends_on": ["find_cup"],
                    },
                ],
                "target_object_visible": True,
                "target_object_location": "厨房餐桌",
                "last_observation": {
                    "target_object_visible": True,
                    "target_object_location": "厨房餐桌",
                },
                "user_location": "客厅沙发旁",
            },
        },
    }


def _load_stage_04_context(stage_04_case: str) -> PlanningContext:
    path = LLM_CASE_ROOT / "stage_04" / stage_04_case / "actual.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    return PlanningContext.model_validate(payload["planning_context"])


def _orchestration_checks(case_name: str, plan: OrchestrationPlan) -> dict[str, bool]:
    plan_json = json.dumps(plan.model_dump(mode="json"), ensure_ascii=False)
    intents = [subtask.intent for subtask in plan.subtasks]
    checks: dict[str, bool] = {
        "has_subtasks": bool(plan.subtasks),
        "no_selected_target": "selected_target" not in plan_json,
        "no_legacy_candidate": all(
            term not in plan_json
            for term in ("selected_candidate_id", "candidate_id", "switch_candidate")
        ),
        "subtasks_have_success_criteria": all(
            bool(subtask.success_criteria) for subtask in plan.subtasks
        ),
    }
    if case_name == "check_medicine_orchestration_live":
        checks["no_fetch_or_delivery_operation"] = not _contains_any(
            intents,
            ("拿", "取", "放", "交付", "递"),
        )
        checks["mentions_medicine"] = "药" in "".join(intents) or "medicine" in plan_json
    if case_name == "fetch_cup_orchestration_live":
        checks["mentions_cup"] = "水杯" in "".join(intents) or "cup" in plan_json.lower()
        checks["has_pickup_intent"] = _contains_any(intents, ("拿", "取", "抓"))
        checks["has_delivery_or_user_intent"] = _contains_any(
            intents,
            ("用户", "交付", "递", "给", "放", "回到"),
        )
    if case_name == "ungrounded_exploration_live":
        checks["has_exploratory_intent"] = _contains_any(
            intents,
            ("找", "寻找", "搜索", "探索", "观察", "查看", "确认", "询问", "澄清"),
        )
        checks["does_not_reference_memory_id"] = "memory_id" not in plan_json
    return checks


def _contains_any(values: list[str], terms: tuple[str, ...]) -> bool:
    joined = " ".join(values).casefold()
    return any(term.casefold() in joined for term in terms)


def _skill_input_valid(decision: StepDecision) -> bool:
    try:
        validate_skill_input(decision.selected_skill, decision.skill_input)
    except Exception:
        return False
    return True
