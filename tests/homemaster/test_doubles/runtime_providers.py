"""Deterministic/mock providers extracted from pipeline/stage_runtime.py.

Moved during Phase 1 deterministic cleanup.  These are test-only providers
and must never be imported by production src/homemaster code.
"""

from __future__ import annotations

from typing import Any, ClassVar

from homemaster.contracts import (
    MemoryRetrievalQuery,
    OrchestrationPlan,
    PlanningContext,
    StepDecision,
    Subtask,
    TaskCard,
)
from homemaster.failure_rule_provider import FailureRuleProvider
from homemaster.runtime import ProviderConfig


# ---------------------------------------------------------------------------
# Deterministic / mock providers
# ---------------------------------------------------------------------------


class StaticMemoryQueryProvider:
    runtime_mode: ClassVar[str] = "test_double"

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
    runtime_mode: ClassVar[str] = "test_double"
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
    runtime_mode: ClassVar[str] = "test_double"

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
        state: Any,
        context: PlanningContext,
    ) -> StepDecision:
        from homemaster.contracts import ExecutionState

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


def live_step_decision_smoke(
    *,
    context: PlanningContext,
    plan: OrchestrationPlan,
    initial_state: Any,
    live_models: bool,
    config_path: str,
    provider_name: str,
) -> dict[str, Any]:
    """Live-only LLM smoke test on first subtask.  Returns status dict."""
    if not live_models or not plan.subtasks:
        return {"mode": "deterministic", "status": "SKIPPED"}

    from homemaster.stages.skill_selector import generate_step_decision
    from homemaster.runtime import load_provider_config
    from homemaster.token_budget import initial_max_tokens

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
