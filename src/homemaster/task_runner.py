"""Stage 07 single-task runner that wires Stage 02 through Stage 06."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from homemaster.contracts import (
    EvidenceBundle,
    ExecutionState,
    MemoryRetrievalQuery,
    OrchestrationPlan,
    PlanningContext,
    StepDecision,
    Subtask,
    TaskCard,
    TaskSummary,
)
from homemaster.embedding_client import BGEEmbeddingClient
from homemaster.executor import (
    Stage05ExecutionResult,
    execute_stage_05_plan,
)
from homemaster.frontdoor import understand_task
from homemaster.memory_commit import build_evidence_bundle, build_memory_commit_plan, utc_now_iso
from homemaster.memory_rag import (
    DEFAULT_EMBEDDING_PROVIDER_NAME,
    EmbeddingClientAdapter,
    MimoMemoryQueryProvider,
    run_memory_rag,
)
from homemaster.orchestrator import generate_orchestration_plan
from homemaster.planning_context import build_planning_context
from homemaster.runtime import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_PROVIDER_NAME,
    LLM_CASE_ROOT,
    REPO_ROOT,
    TEST_RESULTS_ROOT,
    ProviderConfig,
    load_provider_config,
)
from homemaster.stage_06 import persist_stage_06_commit
from homemaster.summary import generate_task_summary
from homemaster.token_budget import initial_max_tokens
from homemaster.trace import append_jsonl_event, sanitize_for_log, write_json

STAGE_07_CASE_ROOT = LLM_CASE_ROOT / "stage_07"
STAGE_07_RESULTS_DIR = TEST_RESULTS_ROOT / "stage_07"
DEFAULT_STAGE_07_RUNTIME_ROOT = REPO_ROOT / "var" / "homemaster" / "runs"
DEFAULT_STAGE_07_DEBUG_ROOT = STAGE_07_CASE_ROOT.parent


class HomeMasterRunError(RuntimeError):
    """Raised when Stage 07 cannot construct or run a task safely."""


@dataclass(frozen=True)
class HomeMasterRunResult:
    run_id: str
    scenario: str
    utterance: str
    final_status: str
    stage_statuses: dict[str, dict[str, Any]]
    model_boundary: dict[str, str]
    paths: dict[str, str]
    task_card: TaskCard | None
    planning_context: PlanningContext | None
    orchestration_plan: OrchestrationPlan | None
    execution_result: Stage05ExecutionResult | None
    evidence_bundle: EvidenceBundle | None
    memory_commit: dict[str, Any] | None
    case_dir: Path
    results_dir: Path
    runtime_memory_root: Path

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "scenario": self.scenario,
            "utterance": self.utterance,
            "final_status": self.final_status,
            "stage_statuses": self.stage_statuses,
            "model_boundary": self.model_boundary,
            "paths": self.paths,
            "task_card": self.task_card.model_dump(mode="json") if self.task_card else None,
            "planning_context": (
                self.planning_context.model_dump(mode="json") if self.planning_context else None
            ),
            "orchestration_plan": (
                self.orchestration_plan.model_dump(mode="json")
                if self.orchestration_plan
                else None
            ),
            "execution_result": (
                self.execution_result.as_debug_payload() if self.execution_result else None
            ),
            "evidence_bundle": (
                self.evidence_bundle.model_dump(mode="json") if self.evidence_bundle else None
            ),
            "memory_commit": self.memory_commit,
            "case_dir": str(self.case_dir),
            "results_dir": str(self.results_dir),
            "runtime_memory_root": str(self.runtime_memory_root),
        }


class StaticMemoryQueryProvider:
    def __init__(self, query: MemoryRetrievalQuery) -> None:
        self.query = query

    def generate_query(
        self,
        prompt: str,
        *,
        max_tokens: int | None = None,
    ) -> tuple[MemoryRetrievalQuery, str, dict[str, Any]]:
        raw = self.query.model_dump_json()
        return self.query, raw, {"provider_name": "deterministic", "model": "stage07-static"}


class KeywordEmbeddingProvider:
    provider_name = "deterministic-embedding"
    model = "keyword-vector-v1"

    def public_summary(self) -> dict[str, Any]:
        return {"provider_name": self.provider_name, "model": self.model}

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            vectors.append(
                [
                    1.0 if any(term in text for term in ("水杯", "杯子", "cup")) else 0.0,
                    1.0 if any(term in text for term in ("药盒", "药箱", "medicine")) else 0.0,
                    1.0 if any(term in text for term in ("厨房", "kitchen")) else 0.0,
                    1.0 if any(term in text for term in ("桌", "table")) else 0.0,
                ]
            )
        return vectors


class StaticScenarioDecisionProvider:
    def __init__(self, *, scenario: str) -> None:
        self.scenario = scenario
        self._navigation_attempts = 0

    def next_decision(
        self,
        subtask: Subtask,
        state: ExecutionState,
        context: PlanningContext,
    ) -> StepDecision:
        intent = subtask.intent
        if any(term in intent for term in ("找", "寻找", "观察", "查看", "确认")):
            self._navigation_attempts += 1
            skill_input: dict[str, Any] = {
                "goal_type": "find_object",
                "target_object": subtask.target_object or context.task_card.target,
                "room_hint": subtask.room_hint,
                "anchor_hint": subtask.anchor_hint,
                "subtask_id": subtask.id,
                "subtask_intent": subtask.intent,
            }
            if self.scenario in {"object_not_found", "distractor_rejected"}:
                skill_input["force_no_object"] = True
            return StepDecision(
                subtask_id=subtask.id,
                selected_skill="navigation",
                skill_input=skill_input,
                expected_result="找到并观察目标物",
                reason="当前子任务需要先导航或观察目标物",
            )
        if any(term in intent for term in ("回到", "到达", "去用户")):
            return StepDecision(
                subtask_id=subtask.id,
                selected_skill="navigation",
                skill_input={
                    "goal_type": "go_to_location",
                    "target_location": state.user_location or "user_start",
                    "subtask_id": subtask.id,
                    "subtask_intent": subtask.intent,
                },
                expected_result="到达用户位置",
                reason="当前子任务需要移动到已记录的用户位置",
            )
        return StepDecision(
            subtask_id=subtask.id,
            selected_skill="operation",
            skill_input={
                "subtask_id": subtask.id,
                "subtask_intent": subtask.intent,
                "target_object": subtask.target_object or context.task_card.target,
                "recipient": subtask.recipient,
                "observation": state.last_observation,
            },
            expected_result="完成操作子任务",
            reason="当前子任务需要操作 skill",
        )


class LiveStepDecisionProvider:
    def __init__(self, provider: ProviderConfig, *, scenario: str) -> None:
        self.provider = provider
        self.scenario = scenario

    def next_decision(
        self,
        subtask: Subtask,
        state: ExecutionState,
        context: PlanningContext,
    ) -> StepDecision:
        from homemaster.skill_selector import generate_step_decision

        result = generate_step_decision(
            subtask,
            state,
            context,
            self.provider,
            max_tokens=initial_max_tokens("stage_05_step_decision"),
        )
        decision = result.decision
        if (
            self.scenario in {"object_not_found", "distractor_rejected"}
            and decision.selected_skill == "navigation"
            and decision.skill_input.get("goal_type") == "find_object"
        ):
            skill_input = dict(decision.skill_input)
            skill_input["force_no_object"] = True
            decision = decision.model_copy(update={"skill_input": skill_input})
        return decision


def run_homemaster_task(
    *,
    utterance: str,
    scenario: str,
    world_path: str | Path | None = None,
    memory_path: str | Path | None = None,
    runtime_memory_root: str | Path = DEFAULT_STAGE_07_RUNTIME_ROOT,
    debug_root: str | Path = DEFAULT_STAGE_07_DEBUG_ROOT,
    run_id: str | None = None,
    live_models: bool = False,
    mock_skills: bool = True,
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    provider_name: str = DEFAULT_PROVIDER_NAME,
    embedding_provider_name: str = DEFAULT_EMBEDDING_PROVIDER_NAME,
) -> HomeMasterRunResult:
    if not scenario:
        raise HomeMasterRunError("scenario is required for Stage07 run")
    run_id = run_id or _slug_run_id(scenario)
    scenario_root = REPO_ROOT / "data" / "scenarios" / scenario
    if not scenario_root.is_dir():
        raise HomeMasterRunError(f"unknown scenario: {scenario}")
    resolved_world = _resolve_path(world_path, scenario_root / "world.json")
    resolved_memory = _resolve_path(memory_path, scenario_root / "memory.json")
    if not resolved_world.is_file():
        raise HomeMasterRunError(f"missing world file: {resolved_world}")
    if not resolved_memory.is_file():
        raise HomeMasterRunError(f"missing memory file: {resolved_memory}")

    runtime_memory_dir = Path(runtime_memory_root) / run_id / "memory"
    case_dir = Path(debug_root) / "stage_07" / run_id
    results_dir = STAGE_07_RESULTS_DIR
    paths = {
        "world_path": str(resolved_world),
        "base_memory_path": str(resolved_memory),
        "runtime_memory_root": str(runtime_memory_dir),
        "case_dir": str(case_dir),
        "results_dir": str(results_dir),
    }
    stage_statuses: dict[str, dict[str, Any]] = {}
    task_card: TaskCard | None = None
    planning_context: PlanningContext | None = None
    orchestration_plan: OrchestrationPlan | None = None
    execution_result: Stage05ExecutionResult | None = None
    evidence_bundle: EvidenceBundle | None = None
    memory_commit: dict[str, Any] | None = None
    final_status = "failed"
    model_boundary = _model_boundary(live_models=live_models, mock_skills=mock_skills)

    try:
        task_card = _stage02_task_card(
            utterance=utterance,
            live_models=live_models,
            run_id=run_id,
            config_path=config_path,
            provider_name=provider_name,
        )
        stage_statuses["stage02"] = {"status": "PASS", "mode": model_boundary["stage02"]}

        memory_result = _stage03_memory_rag(
            task_card=task_card,
            memory_path=runtime_memory_dir / "object_memory.json"
            if (runtime_memory_dir / "object_memory.json").exists()
            else resolved_memory,
            scenario=scenario,
            run_id=run_id,
            live_models=live_models,
            config_path=config_path,
            provider_name=provider_name,
            embedding_provider_name=embedding_provider_name,
            case_root=case_dir / "stage_03_cases",
            results_dir=results_dir,
        )
        stage_statuses["stage03"] = {
            "status": "PASS",
            "mode": model_boundary["stage03_query"],
            "embedding": model_boundary["stage03_embedding"],
        }

        world = json.loads(resolved_world.read_text(encoding="utf-8"))
        planning_build = build_planning_context(task_card, memory_result.memory_result, world)
        planning_context = planning_build.context
        stage_statuses["stage04"] = {
            "status": "PASS",
            "grounding_status": planning_context.runtime_state_summary.get("grounding_status"),
            "selected_target": planning_context.selected_target.memory_id
            if planning_context.selected_target
            else None,
        }

        orchestration_plan = _stage05_plan(
            context=planning_context,
            live_models=live_models,
            config_path=config_path,
            provider_name=provider_name,
        )
        initial_state = ExecutionState(
            task_status="running",
            user_location="user_start",
            current_location="robot_start",
        )
        live_step_status = _live_step_decision_smoke(
            context=planning_context,
            plan=orchestration_plan,
            initial_state=initial_state,
            live_models=live_models,
            config_path=config_path,
            provider_name=provider_name,
        )
        decision_provider = StaticScenarioDecisionProvider(scenario=scenario)
        execution_result = execute_stage_05_plan(
            planning_context,
            orchestration_plan,
            decision_provider=decision_provider,
            initial_state=initial_state,
        )
        stage_statuses["stage05"] = {
            "status": "PASS",
            "mode": model_boundary["stage05_plan"],
            "step_decision": live_step_status,
            "final_task_status": execution_result.final_state.task_status,
            "mock_skills": mock_skills,
        }

        evidence_bundle = build_evidence_bundle(
            task_id=run_id,
            verification_results=execution_result.verification_results,
            skill_results=execution_result.skill_results,
            failure_records=execution_result.failure_records,
            trace_events=[{"event_id": f"stage07:{run_id}", "summary": "stage07 task run"}],
            created_at=utc_now_iso(),
        )
        task_summary = _stage06_summary(
            task_card=task_card,
            execution_state=execution_result.final_state,
            evidence_bundle=evidence_bundle,
            live_models=live_models,
            config_path=config_path,
            provider_name=provider_name,
        )
        commit_plan = build_memory_commit_plan(
            task_id=run_id,
            task_card=task_card,
            planning_context=planning_context,
            orchestration_plan=orchestration_plan,
            execution_state=execution_result.final_state,
            evidence_bundle=evidence_bundle,
            task_summary=task_summary,
            started_at=utc_now_iso(),
            completed_at=utc_now_iso(),
        )
        memory_commit = persist_stage_06_commit(
            memory_root=runtime_memory_dir,
            base_memory_path=resolved_memory,
            plan=commit_plan,
            task_id=run_id,
        )
        stage_statuses["stage06"] = {
            "status": "PASS",
            "mode": model_boundary["stage06_summary"],
            "task_summary_result": task_summary.result,
            "object_memory_update_count": len(commit_plan.object_memory_updates),
            "fact_memory_write_count": len(commit_plan.fact_memory_writes),
        }
        final_status = execution_result.final_state.task_status
    except Exception as exc:
        if not stage_statuses:
            stage_statuses["stage07"] = {"status": "FAIL", "error": str(exc)}
        else:
            stage_statuses["stage07_error"] = {
                "status": "FAIL",
                "error_type": type(exc).__name__,
                "message": str(exc),
            }
        _write_stage_07_assets(
            case_dir=case_dir,
            results_dir=results_dir,
            expected={"scenario": scenario, "utterance": utterance},
            actual={
                "run_id": run_id,
                "scenario": scenario,
                "utterance": utterance,
                "final_status": "failed",
                "stage_statuses": stage_statuses,
                "model_boundary": model_boundary,
                "paths": paths,
                "error_type": type(exc).__name__,
                "message": str(exc),
            },
            status="FAIL",
        )
        raise

    result = HomeMasterRunResult(
        run_id=run_id,
        scenario=scenario,
        utterance=utterance,
        final_status=final_status,
        stage_statuses=stage_statuses,
        model_boundary=model_boundary,
        paths=paths,
        task_card=task_card,
        planning_context=planning_context,
        orchestration_plan=orchestration_plan,
        execution_result=execution_result,
        evidence_bundle=evidence_bundle,
        memory_commit=memory_commit,
        case_dir=case_dir,
        results_dir=results_dir,
        runtime_memory_root=runtime_memory_dir,
    )
    _write_stage_07_assets(
        case_dir=case_dir,
        results_dir=results_dir,
        expected={"scenario": scenario, "utterance": utterance},
        actual=result.to_dict(),
        status="PASS",
    )
    return result


def _stage02_task_card(
    *,
    utterance: str,
    live_models: bool,
    run_id: str,
    config_path: str | Path,
    provider_name: str,
) -> TaskCard:
    if live_models:
        return understand_task(
            utterance,
            case_name=f"stage07_{run_id}_task_understanding",
            config_path=config_path,
            provider_name=provider_name,
            max_tokens=initial_max_tokens("stage_02_task_card"),
        ).task_card
    return _deterministic_task_card(utterance)


def _stage03_memory_rag(
    *,
    task_card: TaskCard,
    memory_path: Path,
    scenario: str,
    run_id: str,
    live_models: bool,
    config_path: str | Path,
    provider_name: str,
    embedding_provider_name: str,
    case_root: Path,
    results_dir: Path,
):
    llm_provider = (
        load_provider_config(config_path, provider_name=provider_name)
        if live_models
        else _dummy_provider()
    )
    if live_models:
        embedding_config = load_provider_config(config_path, provider_name=embedding_provider_name)
        bge_client = BGEEmbeddingClient(embedding_config)
        try:
            return run_memory_rag(
                task_card,
                memory_path=memory_path,
                case_name=f"stage07_{run_id}_memory_rag",
                query_provider=MimoMemoryQueryProvider(
                    llm_provider,
                    max_tokens=initial_max_tokens("stage_03_memory_query"),
                ),
                embedding_provider=EmbeddingClientAdapter(bge_client),
                llm_provider=llm_provider,
                expected={"case_name": f"stage07_{run_id}_memory_rag"},
                case_root=case_root,
                results_dir=results_dir,
                query_initial_max_tokens=initial_max_tokens("stage_03_memory_query"),
            )
        finally:
            bge_client.close()
    return run_memory_rag(
        task_card,
        memory_path=memory_path,
        case_name=f"stage07_{run_id}_memory_rag",
        query_provider=StaticMemoryQueryProvider(_deterministic_query(task_card)),
        embedding_provider=KeywordEmbeddingProvider(),
        llm_provider=llm_provider,
        expected={"case_name": f"stage07_{run_id}_memory_rag"},
        case_root=case_root,
        results_dir=results_dir,
    )


def _stage05_plan(
    *,
    context: PlanningContext,
    live_models: bool,
    config_path: str | Path,
    provider_name: str,
) -> OrchestrationPlan:
    if live_models:
        provider = load_provider_config(config_path, provider_name=provider_name)
        return generate_orchestration_plan(
            context,
            provider,
            max_tokens=initial_max_tokens("stage_05_orchestration"),
        ).plan
    return _deterministic_plan(context)


def _live_step_decision_smoke(
    *,
    context: PlanningContext,
    plan: OrchestrationPlan,
    initial_state: ExecutionState,
    live_models: bool,
    config_path: str | Path,
    provider_name: str,
) -> dict[str, Any]:
    if not live_models or not plan.subtasks:
        return {"mode": "deterministic", "status": "SKIPPED"}
    from homemaster.skill_selector import generate_step_decision

    provider = load_provider_config(config_path, provider_name=provider_name)
    generated = generate_step_decision(
        plan.subtasks[0],
        initial_state,
        context,
        provider,
        max_tokens=initial_max_tokens("stage_05_step_decision"),
    )
    return {
        "mode": "real_mimo",
        "status": "PASS",
        "subtask_id": generated.decision.subtask_id,
        "selected_skill": generated.decision.selected_skill,
        "provider": generated.provider,
    }


def _stage06_summary(
    *,
    task_card: TaskCard,
    execution_state: ExecutionState,
    evidence_bundle: EvidenceBundle,
    live_models: bool,
    config_path: str | Path,
    provider_name: str,
) -> TaskSummary:
    if live_models:
        provider = load_provider_config(config_path, provider_name=provider_name)
        return generate_task_summary(
            task_card=task_card,
            execution_state=execution_state,
            evidence_bundle=evidence_bundle,
            provider=provider,
            max_tokens=initial_max_tokens("stage_06_summary"),
        ).summary
    result = "success" if execution_state.task_status == "completed" else "failed"
    return TaskSummary(
        result=result,  # type: ignore[arg-type]
        confirmed_facts=list(evidence_bundle.verified_facts),
        unconfirmed_facts=list(evidence_bundle.failure_facts),
        recovery_attempts=[],
        user_reply="任务完成" if result == "success" else "任务未能完成",
        failure_summary="; ".join(evidence_bundle.failure_facts) or None,
        evidence_refs=[ref.evidence_id for ref in evidence_bundle.evidence_refs],
    )


def _deterministic_task_card(utterance: str) -> TaskCard:
    target = "药盒" if "药" in utterance else "水杯" if "杯" in utterance else "unknown_object"
    task_type = (
        "fetch_object"
        if any(term in utterance for term in ("找", "拿", "取", "拿给"))
        else "check_presence"
    )
    if target == "unknown_object":
        task_type = "unknown"
    location_hint = None
    for term in ("厨房", "桌子那边", "桌子", "客厅", "储物间"):
        if term in utterance:
            location_hint = term
            break
    return TaskCard(
        task_type=task_type,  # type: ignore[arg-type]
        target=target,
        delivery_target="user" if task_type == "fetch_object" else None,
        location_hint=location_hint,
        success_criteria=[f"后续观察或验证能确认{target}相关任务是否完成"],
        needs_clarification=target == "unknown_object",
        clarification_question="请问您想让我处理哪个物品？" if target == "unknown_object" else None,
        confidence=0.85 if target != "unknown_object" else 0.3,
    )


def _deterministic_query(task_card: TaskCard) -> MemoryRetrievalQuery:
    if "药" in task_card.target:
        aliases = ["药盒", "药箱", "medicine_box", "medicine"]
        category = "medicine_box"
    else:
        aliases = ["水杯", "杯子", "cup"]
        category = "cup"
    location_terms = [task_card.location_hint] if task_card.location_hint else []
    return MemoryRetrievalQuery(
        query_text=" ".join([task_card.target, *aliases, *location_terms]),
        target_category=category,
        target_aliases=aliases,
        location_terms=location_terms,
        top_k=5,
        reason="deterministic Stage07 non-live query",
    )


def _deterministic_plan(context: PlanningContext) -> OrchestrationPlan:
    task_card = context.task_card
    room_hint = task_card.location_hint
    anchor_hint = (
        task_card.location_hint
        if task_card.location_hint and "桌" in task_card.location_hint
        else None
    )
    if context.selected_target is not None:
        room_hint = context.selected_target.room_id
        anchor_hint = context.selected_target.display_text or context.selected_target.anchor_id
    if task_card.task_type == "fetch_object":
        return OrchestrationPlan(
            goal=f"找到{task_card.target}并交付给用户",
            subtasks=[
                Subtask(
                    id="find_target",
                    intent=f"找到{task_card.target}",
                    target_object=task_card.target,
                    room_hint=room_hint,
                    anchor_hint=anchor_hint,
                    success_criteria=[f"能观察到{task_card.target}"],
                ),
                Subtask(
                    id="pick_target",
                    intent=f"拿起{task_card.target}",
                    target_object=task_card.target,
                    depends_on=["find_target"],
                    success_criteria=[f"已经拿起{task_card.target}"],
                ),
                Subtask(
                    id="return_to_user",
                    intent="回到用户位置",
                    depends_on=["pick_target"],
                    success_criteria=["已到达用户位置"],
                ),
                Subtask(
                    id="deliver_target",
                    intent=f"交付{task_card.target}给用户",
                    target_object=task_card.target,
                    recipient=task_card.delivery_target or "user",
                    depends_on=["return_to_user"],
                    success_criteria=[f"{task_card.target}已交付给用户"],
                ),
            ],
            confidence=0.82,
        )
    return OrchestrationPlan(
        goal=f"确认{task_card.target}是否存在",
        subtasks=[
            Subtask(
                id="observe_target",
                intent=f"找到并确认{task_card.target}是否存在",
                target_object=task_card.target,
                room_hint=room_hint,
                anchor_hint=anchor_hint,
                success_criteria=[f"能判断是否观察到{task_card.target}"],
            )
        ],
        confidence=0.82,
    )


def _dummy_provider() -> ProviderConfig:
    return ProviderConfig(
        name="deterministic",
        base_url="https://example.invalid/v1/messages",
        model="stage07-static",
        api_keys=("redacted",),
        protocol="anthropic",
    )


def _model_boundary(*, live_models: bool, mock_skills: bool) -> dict[str, str]:
    model = "real_mimo" if live_models else "deterministic"
    embedding = "real_bge_m3" if live_models else "deterministic"
    return {
        "stage02": model,
        "stage03_query": model,
        "stage03_embedding": embedding,
        "stage04": "programmatic",
        "stage05_plan": model,
        "stage05_step": model,
        "stage05_navigation": "mock" if mock_skills else "not_configured",
        "stage05_operation": "mock" if mock_skills else "not_configured",
        "stage05_verification": "mock" if mock_skills else "not_configured",
        "stage06_summary": model,
        "stage06_memory_commit": "programmatic",
        "real_robot": "not_integrated",
        "real_vla": "not_integrated",
        "real_vlm": "not_integrated",
    }


def _write_stage_07_assets(
    *,
    case_dir: Path,
    results_dir: Path,
    expected: dict[str, Any],
    actual: dict[str, Any],
    status: str,
) -> None:
    safe_expected = sanitize_for_log(expected)
    safe_actual = sanitize_for_log(actual)
    write_json(case_dir / "input.json", safe_expected)
    write_json(case_dir / "expected.json", safe_expected)
    write_json(case_dir / "actual.json", safe_actual)
    _write_stage_07_markdown(case_dir / "result.md", status=status, actual=safe_actual)
    append_jsonl_event(results_dir / "llm_samples.jsonl", event="stage_07", payload=safe_actual)
    append_jsonl_event(
        results_dir / "trace" / f"{actual.get('run_id', case_dir.name)}.jsonl",
        event="stage_07",
        payload=safe_actual,
    )


def _write_stage_07_markdown(path: Path, *, status: str, actual: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = f"""# Stage 07 Run - {actual.get("run_id", path.parent.name)}

Status: {status}

## Summary

- Scenario: {actual.get("scenario")}
- Utterance: {actual.get("utterance")}
- Final status: {actual.get("final_status")}

## Stage Statuses

```json
{json.dumps(actual.get("stage_statuses", {}), ensure_ascii=False, indent=2)}
```

## Model And Skill Boundary

```json
{json.dumps(actual.get("model_boundary", {}), ensure_ascii=False, indent=2)}
```

## Paths

```json
{json.dumps(actual.get("paths", {}), ensure_ascii=False, indent=2)}
```

## Full Actual

```json
{json.dumps(actual, ensure_ascii=False, indent=2)}
```
"""
    path.write_text(text, encoding="utf-8")


def _resolve_path(value: str | Path | None, default: Path) -> Path:
    path = Path(value) if value is not None else default
    return path if path.is_absolute() else REPO_ROOT / path


def _slug_run_id(scenario: str) -> str:
    return f"{scenario}-{int(time.time())}"
