"""Stage runtime helpers extracted from task_runner.py.

Contains deterministic providers, stage wrapper functions, and model boundary
logic.  Both task_runner.py and pipeline_stages.py import from here,
eliminating reverse dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar, Literal

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
# P2: RuntimeMode — structured per-component mode declaration
# ---------------------------------------------------------------------------


ComponentMode = Literal[
    "live_llm",          # Real Mimo LLM call
    "live_embedding",    # Real BGE-M3 embedding call
    "mock_skill",        # Mock navigation/operation/verification
    "mock_symbolic",     # Deterministic symbolic verification
    "programmatic",      # Pure code, no LLM or mock needed
    "test_double",       # Deterministic provider used as test stand-in
    "not_integrated",    # Component does not exist yet (robot/VLA/VLM)
]


@dataclass(frozen=True)
class RuntimeMode:
    """Structured declaration of each pipeline component's runtime mode.

    Fields reflect the ACTUAL execution path, not aspirational labels.
    - step_decision: always test_double (StaticScenarioDecisionProvider)
    - step_decision_smoke: live_llm when live_models=True, else "n/a"
    - skills/verification: mock_skill/mock_symbolic (robot/VLM not integrated)
    """

    task_understanding: ComponentMode
    memory_query: ComponentMode
    embedding: ComponentMode
    planning: ComponentMode
    step_decision: ComponentMode       # actual execution decision provider
    step_decision_smoke: str           # "live_llm" or "n/a" (not ComponentMode)
    skills: ComponentMode
    verification: ComponentMode
    summary: ComponentMode
    memory_commit: ComponentMode
    real_robot: ComponentMode
    real_vla: ComponentMode
    real_vlm: ComponentMode

    @classmethod
    def from_flags(cls, *, live_models: bool, mock_skills: bool) -> RuntimeMode:
        """Map legacy boolean flags to structured RuntimeMode."""
        brain: ComponentMode = "live_llm" if live_models else "test_double"
        emb: ComponentMode = "live_embedding" if live_models else "test_double"
        skill: ComponentMode = "mock_skill" if mock_skills else "test_double"
        return cls(
            task_understanding=brain,
            memory_query=brain,
            embedding=emb,
            planning=brain,
            step_decision="test_double",        # always: StaticScenarioDecisionProvider
            step_decision_smoke="live_llm" if live_models else "n/a",
            skills=skill,
            verification="mock_symbolic",
            summary=brain,
            memory_commit="programmatic",
            real_robot="not_integrated",
            real_vla="not_integrated",
            real_vlm="not_integrated",
        )

    def to_boundary_dict(self) -> dict[str, str]:
        """Serialize to legacy model_boundary dict format (backward compat).

        Maps internal ComponentMode to legacy string values.
        """
        _BRAIN: dict[str, str] = {
            "live_llm": "real_mimo",
            "test_double": "deterministic",
        }
        _EMB: dict[str, str] = {
            "live_embedding": "real_bge_m3",
            "test_double": "deterministic",
        }
        _VERIF: dict[str, str] = {
            "mock_symbolic": "mock",
            "mock_skill": "mock",
            "test_double": "not_integrated",
        }
        _SKILL: dict[str, str] = {
            "mock_skill": "mock",
            "test_double": "not_integrated",
            "not_integrated": "not_integrated",
        }
        return {
            "stage02": _BRAIN[self.task_understanding],
            "stage03_query": _BRAIN[self.memory_query],
            "stage03_embedding": _EMB[self.embedding],
            "stage04": "programmatic",
            "stage05_plan": _BRAIN[self.planning],
            "stage05_step": _BRAIN[self.step_decision],
            "stage05_navigation": _SKILL.get(self.skills, "not_configured"),
            "stage05_operation": _SKILL.get(self.skills, "not_configured"),
            "stage05_verification": _VERIF.get(self.verification, "not_configured"),
            "stage06_summary": _BRAIN[self.summary],
            "stage06_memory_commit": "programmatic",
            "real_robot": _SKILL.get(self.real_robot, "not_integrated"),
            "real_vla": _SKILL.get(self.real_vla, "not_integrated"),
            "real_vlm": _SKILL.get(self.real_vlm, "not_integrated"),
        }


@dataclass(frozen=True)
class ServiceCheckResult:
    """Result of checking whether a required service is available."""

    component: str
    mode_required: ComponentMode
    available: bool
    error: str | None = None


def validate_runtime_services(
    runtime_mode: RuntimeMode,
    *,
    config_path: str,
    provider_name: str,
    embedding_provider_name: str,
) -> list[ServiceCheckResult]:
    """Check that required services are available for the given RuntimeMode.

    Returns a list of check results.  If any required service is unavailable,
    the caller should fail-fast rather than silently falling back.
    """
    from homemaster.runtime import RuntimeConfigError

    checks: list[ServiceCheckResult] = []
    needs_llm = any(
        getattr(runtime_mode, f) == "live_llm"
        for f in ("task_understanding", "memory_query", "planning", "summary")
    )
    if needs_llm:
        try:
            load_provider_config(config_path, provider_name=provider_name)
            checks.append(ServiceCheckResult("llm_provider", "live_llm", True))
        except RuntimeConfigError as exc:
            checks.append(
                ServiceCheckResult("llm_provider", "live_llm", False, str(exc))
            )
    if runtime_mode.embedding == "live_embedding":
        try:
            load_provider_config(config_path, provider_name=embedding_provider_name)
            checks.append(
                ServiceCheckResult("embedding_provider", "live_embedding", True)
            )
        except RuntimeConfigError as exc:
            checks.append(
                ServiceCheckResult(
                    "embedding_provider", "live_embedding", False, str(exc)
                )
            )
    return checks


# ---------------------------------------------------------------------------
# Deterministic / mock providers
# ---------------------------------------------------------------------------


class StaticMemoryQueryProvider:
    runtime_mode: ClassVar[ComponentMode] = "test_double"  # P2: labeled test-double

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
    runtime_mode: ClassVar[ComponentMode] = "test_double"  # P2: labeled test-double
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
    runtime_mode: ClassVar[ComponentMode] = "test_double"  # P2: labeled test-double

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
        from homemaster.stages.skill_selector import generate_step_decision

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
        from homemaster.stages.task_understanding import understand_task

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
        from homemaster.stages.orchestrator import generate_orchestration_plan

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

    from homemaster.stages.skill_selector import generate_step_decision

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
        from homemaster.stages.summary import generate_task_summary

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
    """Create a dummy ProviderConfig for test-double mode. NOT for production use."""
    return ProviderConfig(
        name="test-double-deterministic",
        base_url="https://example.invalid/v1/messages",
        model="stage07-static",
        api_keys=("redacted",),
        protocol="anthropic",
    )


# ---------------------------------------------------------------------------
# Model boundary
# ---------------------------------------------------------------------------


def model_boundary(*, live_models: bool, mock_skills: bool) -> dict[str, str]:
    """Build model boundary dict. Delegates to RuntimeMode for structured mapping."""
    rm = RuntimeMode.from_flags(live_models=live_models, mock_skills=mock_skills)
    return rm.to_boundary_dict()
