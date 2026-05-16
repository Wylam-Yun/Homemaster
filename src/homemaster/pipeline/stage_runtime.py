"""Stage runtime helpers extracted from task_runner.py.

Contains deterministic providers, stage wrapper functions, and model boundary
logic.  Both task_runner.py and pipeline_stages.py import from here,
eliminating reverse dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from homemaster.contracts import (
    ExecutionState,
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
    "live_llm",              # Real Mimo LLM call
    "live_embedding",        # Real BGE-M3 embedding call
    "simulated_skill",       # Simulated navigation/operation (robot not integrated)
    "simulated_verification", # Simulated symbolic verification
    "programmatic",          # Pure code, no LLM or mock needed
    "not_integrated",        # Component does not exist yet (robot/VLA/VLM)
]


@dataclass(frozen=True)
class RuntimeMode:
    """Structured declaration of each pipeline component's runtime mode.

    Fields reflect the ACTUAL execution path, not aspirational labels.
    - step_decision: live_llm (LiveStepDecisionProvider)
    - skills/verification: simulated_skill/simulated_verification (robot/VLM not integrated)
    """

    task_understanding: ComponentMode
    memory_query: ComponentMode
    embedding: ComponentMode
    planning: ComponentMode
    step_decision: ComponentMode
    skills: ComponentMode
    verification: ComponentMode
    summary: ComponentMode
    memory_commit: ComponentMode
    real_robot: ComponentMode
    real_vla: ComponentMode
    real_vlm: ComponentMode

    @classmethod
    def live(cls, *, skill_mode: str = "simulated") -> RuntimeMode:
        """Construct a live-only RuntimeMode.

        skill_mode="simulated" uses simulated_skill/simulated_verification (robot not integrated).
        skill_mode="real" fails fast — real VLA/VLN/VLM executors are not integrated.
        """
        if skill_mode == "real":
            from homemaster.runtime import RuntimeConfigError

            raise RuntimeConfigError(
                "real VLA/VLN/VLM skill executors are not integrated. "
                "Use skill_mode='simulated' until real executors are available."
            )
        return cls(
            task_understanding="live_llm",
            memory_query="live_llm",
            embedding="live_embedding",
            planning="live_llm",
            step_decision="live_llm",
            skills="simulated_skill",
            verification="simulated_verification",
            summary="live_llm",
            memory_commit="programmatic",
            real_robot="not_integrated",
            real_vla="not_integrated",
            real_vlm="not_integrated",
        )

    @classmethod
    def from_flags(cls, *, live_models: bool, mock_skills: bool) -> RuntimeMode:
        """Map legacy boolean flags to structured RuntimeMode.

        Raises RuntimeConfigError unconditionally — live_models/mock_skills flags
        have been removed.  Use RuntimeMode.live() instead.
        """
        from homemaster.runtime import RuntimeConfigError

        raise RuntimeConfigError(
            "live_models/mock_skills flags have been removed. "
            "Use RuntimeMode.live() instead."
        )

    def to_boundary_dict(self) -> dict[str, str]:
        """Serialize to legacy model_boundary dict format (backward compat).

        Maps internal ComponentMode to boundary string values.
        """
        _BRAIN: dict[str, str] = {
            "live_llm": "real_mimo",
        }
        _EMB: dict[str, str] = {
            "live_embedding": "real_bge_m3",
        }
        _VERIF: dict[str, str] = {
            "simulated_verification": "simulated",
            "simulated_skill": "simulated",
        }
        _SKILL: dict[str, str] = {
            "simulated_skill": "simulated",
            "not_integrated": "not_integrated",
        }
        return {
            "stage02": _BRAIN.get(self.task_understanding, "not_configured"),
            "stage03_query": _BRAIN.get(self.memory_query, "not_configured"),
            "stage03_embedding": _EMB.get(self.embedding, "not_configured"),
            "stage04": "programmatic",
            "stage05_plan": _BRAIN.get(self.planning, "not_configured"),
            "stage05_step": _BRAIN.get(self.step_decision, "not_configured"),
            "stage05_navigation": _SKILL.get(self.skills, "not_configured"),
            "stage05_operation": _SKILL.get(self.skills, "not_configured"),
            "stage05_verification": _VERIF.get(self.verification, "not_configured"),
            "stage06_summary": _BRAIN.get(self.summary, "not_configured"),
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
# Live providers
# ---------------------------------------------------------------------------


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
    run_id: str,
    config_path: str,
    provider_name: str,
) -> TaskCard:
    """Stage 02: utterance → TaskCard (live only)."""
    from homemaster.stages.task_understanding import understand_task

    return understand_task(
        utterance,
        case_name=f"stage07_{run_id}_task_understanding",
        config_path=config_path,
        provider_name=provider_name,
        max_tokens=initial_max_tokens("stage_02_task_card"),
    ).task_card


def run_stage03(
    *,
    task_card: TaskCard,
    memory_path: Any,
    scenario: str,
    run_id: str,
    config_path: str,
    provider_name: str,
    embedding_provider_name: str,
    case_root: Any,
    results_dir: Any,
    negative_evidence: dict[str, Any] | None = None,
):
    """Stage 03: TaskCard → MemoryRagResult (live only)."""
    from homemaster.memory_rag import (
        EmbeddingClientAdapter,
        MimoMemoryQueryProvider,
        run_memory_rag,
    )
    from homemaster.embedding_client import BGEEmbeddingClient

    llm_provider = load_provider_config(config_path, provider_name=provider_name)
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
            negative_evidence=negative_evidence,
            expected={"case_name": f"stage07_{run_id}_memory_rag"},
            case_root=case_root,
            results_dir=results_dir,
            query_initial_max_tokens=initial_max_tokens("stage_03_memory_query"),
        )
    finally:
        bge_client.close()


def run_stage05_plan(
    *,
    context: PlanningContext,
    config_path: str,
    provider_name: str,
) -> OrchestrationPlan:
    """Stage 05a: PlanningContext → OrchestrationPlan (live only)."""
    from homemaster.stages.orchestrator import generate_orchestration_plan

    provider = load_provider_config(config_path, provider_name=provider_name)
    return generate_orchestration_plan(
        context,
        provider,
        max_tokens=initial_max_tokens("stage_05_orchestration"),
    ).plan


def run_stage06_summary(
    *,
    task_card: TaskCard,
    execution_state: ExecutionState,
    evidence_bundle: Any,
    config_path: str,
    provider_name: str,
    recovery_attempts: list[dict[str, Any]] | None = None,
) -> TaskSummary:
    """Stage 06a: generate TaskSummary (live only)."""
    from homemaster.stages.summary import generate_task_summary

    provider = load_provider_config(config_path, provider_name=provider_name)
    return generate_task_summary(
        task_card=task_card,
        execution_state=execution_state,
        evidence_bundle=evidence_bundle,
        provider=provider,
        max_tokens=initial_max_tokens("stage_06_summary"),
    ).summary


# ---------------------------------------------------------------------------
# Model boundary
# ---------------------------------------------------------------------------


def model_boundary(*, skill_mode: str = "simulated") -> dict[str, str]:
    """Build model boundary dict. Delegates to RuntimeMode for structured mapping."""
    rm = RuntimeMode.live(skill_mode=skill_mode)
    return rm.to_boundary_dict()
