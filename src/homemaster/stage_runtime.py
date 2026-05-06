"""Stage runtime helpers extracted from task_runner.py.

Contains deterministic providers, stage wrapper functions, and model boundary
logic.  Both task_runner.py and pipeline_stages.py import from here,
eliminating reverse dependencies.
"""

from __future__ import annotations

from typing import Any

from homemaster.contracts import (
    ExecutionState,
    MemoryRetrievalQuery,
    OrchestrationPlan,
    PlanningContext,
    StepDecision,
    Subtask,
    TaskCard,
    TaskSummary,
)
from homemaster.failure_rule_provider import FailureRuleProvider
from homemaster.runtime import ProviderConfig, load_provider_config
from homemaster.token_budget import initial_max_tokens


# ---------------------------------------------------------------------------
# Deterministic / mock providers
# ---------------------------------------------------------------------------


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
    def __init__(
        self,
        *,
        scenario: str,
        failure_provider: FailureRuleProvider | None = None,
    ) -> None:
        self.scenario = scenario
        self.failure_provider = failure_provider
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
            target_cat = context.retrieval_query.target_category if context.retrieval_query else None
            if self.failure_provider and self.failure_provider.should_force_no_object(target_category=target_cat):
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
    def __init__(
        self,
        provider: ProviderConfig,
        *,
        scenario: str,
        failure_provider: FailureRuleProvider | None = None,
    ) -> None:
        self.provider = provider
        self.scenario = scenario
        self.failure_provider = failure_provider

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
            self.failure_provider
            and decision.selected_skill == "navigation"
            and decision.skill_input.get("goal_type") == "find_object"
        ):
            target_cat = context.retrieval_query.target_category if context.retrieval_query else None
            if self.failure_provider.should_force_no_object(target_category=target_cat):
                skill_input = dict(decision.skill_input)
                skill_input["force_no_object"] = True
                decision = decision.model_copy(update={"skill_input": skill_input})
        return decision


# ---------------------------------------------------------------------------
# Stage wrapper functions (live/mock branching)
# ---------------------------------------------------------------------------


def run_stage02(
    *,
    utterance: str,
    live_models: bool,
    run_id: str,
    config_path: str,
    provider_name: str,
) -> TaskCard:
    """Stage 02: utterance → TaskCard."""
    if live_models:
        from homemaster.frontdoor import understand_task

        return understand_task(
            utterance,
            case_name=f"stage07_{run_id}_task_understanding",
            config_path=config_path,
            provider_name=provider_name,
            max_tokens=initial_max_tokens("stage_02_task_card"),
        ).task_card
    return deterministic_task_card(utterance)


def run_stage03(
    *,
    task_card: TaskCard,
    memory_path: Any,
    scenario: str,
    run_id: str,
    live_models: bool,
    config_path: str,
    provider_name: str,
    embedding_provider_name: str,
    case_root: Any,
    results_dir: Any,
):
    """Stage 03: TaskCard → MemoryRagResult."""
    from homemaster.memory_rag import (
        EmbeddingClientAdapter,
        MimoMemoryQueryProvider,
        run_memory_rag,
    )

    llm_provider = (
        load_provider_config(config_path, provider_name=provider_name)
        if live_models
        else dummy_provider()
    )
    if live_models:
        from homemaster.embedding_client import BGEEmbeddingClient

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
        query_provider=StaticMemoryQueryProvider(deterministic_query(task_card)),
        embedding_provider=KeywordEmbeddingProvider(),
        llm_provider=llm_provider,
        expected={"case_name": f"stage07_{run_id}_memory_rag"},
        case_root=case_root,
        results_dir=results_dir,
    )


def run_stage05_plan(
    *,
    context: PlanningContext,
    live_models: bool,
    config_path: str,
    provider_name: str,
) -> OrchestrationPlan:
    """Stage 05a: PlanningContext → OrchestrationPlan."""
    if live_models:
        from homemaster.orchestrator import generate_orchestration_plan

        provider = load_provider_config(config_path, provider_name=provider_name)
        return generate_orchestration_plan(
            context,
            provider,
            max_tokens=initial_max_tokens("stage_05_orchestration"),
        ).plan
    return deterministic_plan(context)


def live_step_decision_smoke(
    *,
    context: PlanningContext,
    plan: OrchestrationPlan,
    initial_state: ExecutionState,
    live_models: bool,
    config_path: str,
    provider_name: str,
) -> dict[str, Any]:
    """Live-only LLM smoke test on first subtask.  Returns status dict."""
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


def run_stage06_summary(
    *,
    task_card: TaskCard,
    execution_state: ExecutionState,
    evidence_bundle: Any,
    live_models: bool,
    config_path: str,
    provider_name: str,
) -> TaskSummary:
    """Stage 06a: generate TaskSummary (live or deterministic)."""
    if live_models:
        from homemaster.summary import generate_task_summary

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


# ---------------------------------------------------------------------------
# Deterministic fallback builders
# ---------------------------------------------------------------------------


def deterministic_task_card(utterance: str) -> TaskCard:
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
        clarification_question="请告诉我需要找什么物品" if target == "unknown_object" else None,
        confidence=0.95,
    )


def deterministic_query(task_card: TaskCard) -> MemoryRetrievalQuery:
    target = task_card.target
    category = "medicine_box" if "药" in target else "cup" if "杯" in target else "unknown"
    aliases = [target]
    location_terms: list[str] = []
    if task_card.location_hint:
        location_terms.append(task_card.location_hint)
    return MemoryRetrievalQuery(
        query_text=f"{target} {' '.join(location_terms)}".strip(),
        target_category=category,
        target_aliases=aliases,
        location_terms=location_terms,
        top_k=5,
        reason="deterministic Stage07 non-live query",
    )


def deterministic_plan(context: PlanningContext) -> OrchestrationPlan:
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


def dummy_provider() -> ProviderConfig:
    return ProviderConfig(
        name="deterministic",
        base_url="https://example.invalid/v1/messages",
        model="stage07-static",
        api_keys=("redacted",),
        protocol="anthropic",
    )


# ---------------------------------------------------------------------------
# Model boundary
# ---------------------------------------------------------------------------


def model_boundary(*, live_models: bool, mock_skills: bool) -> dict[str, str]:
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
