"""Deterministic + LLM high-level planner and plan validator."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any
from uuid import uuid4

from task_brain.context import TaskContext, build_task_context
from task_brain.domain import (
    CapabilitySpec,
    HighLevelPlan,
    ParsedTask,
    Predicate,
    RuntimeState,
    Subgoal,
    SubgoalType,
    TargetObject,
    TaskIntent,
    TaskRequest,
)
from task_brain.llm import KimiPlanProvider, KimiProviderError
from task_brain.parser import parse_instruction

_ALLOWED_SUBGOAL_TYPES = {
    SubgoalType.NAVIGATE,
    SubgoalType.OBSERVE,
    SubgoalType.VERIFY_OBJECT_PRESENCE,
    SubgoalType.EMBODIED_MANIPULATION,
    SubgoalType.RETURN_TO_USER,
    SubgoalType.REPORT_FAILURE,
    SubgoalType.ASK_CLARIFICATION,
}

_EXECUTABLE_SUBGOAL_TYPES = {
    SubgoalType.NAVIGATE,
    SubgoalType.OBSERVE,
    SubgoalType.VERIFY_OBJECT_PRESENCE,
    SubgoalType.EMBODIED_MANIPULATION,
    SubgoalType.RETURN_TO_USER,
}

_ATOMIC_ACTION_KEYWORDS = {
    "move_arm_to_pregrasp",
    "close_gripper",
    "open_gripper",
    "lift",
}

_SUBGOAL_CAPABILITY_REQUIREMENTS: dict[SubgoalType, tuple[str, ...]] = {
    SubgoalType.NAVIGATE: ("mock_vln.navigate",),
    SubgoalType.OBSERVE: ("mock_perception.observe",),
    SubgoalType.VERIFY_OBJECT_PRESENCE: ("verification.evaluate",),
    SubgoalType.EMBODIED_MANIPULATION: ("robobrain.plan", "mock_atomic_executor.execute"),
    SubgoalType.RETURN_TO_USER: ("mock_atomic_executor.execute",),
    SubgoalType.REPORT_FAILURE: ("recovery.decide",),
    SubgoalType.ASK_CLARIFICATION: ("recovery.decide",),
}


class DeterministicHighLevelPlanner:
    """Generate deterministic high-level plans from TaskContext only."""

    def generate(self, context: TaskContext) -> HighLevelPlan:
        """Generate a high-level plan for Phase A intents."""
        parse_error = str(context.constraints.get("parse_error", "")).strip()
        if parse_error:
            return self._build_ask_clarification_plan(
                intent=context.parsed_task.intent,
                notes=f"parse_error={parse_error}; {self._replan_context_summary(context)}",
            )

        top_candidate = self._select_top_candidate(context.ranked_candidates)
        if top_candidate is None:
            return self._build_ask_clarification_plan(
                intent=context.parsed_task.intent,
                notes=f"candidate_unavailable; {self._replan_context_summary(context)}",
            )

        try:
            memory_id = _candidate_memory_id(top_candidate)
        except ValueError as exc:
            return self._build_ask_clarification_plan(
                intent=context.parsed_task.intent,
                notes=f"invalid_candidate={exc}; {self._replan_context_summary(context)}",
            )

        if context.parsed_task.intent == TaskIntent.CHECK_OBJECT_PRESENCE:
            return self._build_check_presence_plan(
                target_category=context.parsed_task.target_object.category,
                memory_id=memory_id,
            )
        if context.parsed_task.intent == TaskIntent.FETCH_OBJECT:
            return self._build_fetch_plan(
                target_category=context.parsed_task.target_object.category,
                memory_id=memory_id,
            )

        notes = (
            f"unsupported_intent={context.parsed_task.intent}; "
            f"{self._replan_context_summary(context)}"
        )
        return self._build_ask_clarification_plan(
            intent=context.parsed_task.intent,
            notes=notes,
        )

    @staticmethod
    def _select_top_candidate(ranked_candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
        if not ranked_candidates:
            return None
        return ranked_candidates[0]

    def _build_check_presence_plan(
        self,
        *,
        target_category: str,
        memory_id: str,
    ) -> HighLevelPlan:
        return HighLevelPlan(
            plan_id=f"plan-{uuid4().hex[:12]}",
            intent=TaskIntent.CHECK_OBJECT_PRESENCE,
            subgoals=[
                Subgoal(
                    subgoal_id="sg-1",
                    subgoal_type=SubgoalType.NAVIGATE,
                    description=f"Navigate to candidate location for {target_category}.",
                    candidate_id=memory_id,
                    target_memory_id=memory_id,
                    success_conditions=[
                        Predicate(name="arrived_at_candidate_anchor", args=[memory_id])
                    ],
                ),
                Subgoal(
                    subgoal_id="sg-2",
                    subgoal_type=SubgoalType.OBSERVE,
                    description=f"Observe candidate area for {target_category}.",
                    candidate_id=memory_id,
                    target_memory_id=memory_id,
                    success_conditions=[Predicate(name="observation_captured", args=[memory_id])],
                ),
                Subgoal(
                    subgoal_id="sg-3",
                    subgoal_type=SubgoalType.VERIFY_OBJECT_PRESENCE,
                    description=f"Verify presence of {target_category}.",
                    candidate_id=memory_id,
                    target_memory_id=memory_id,
                    success_conditions=[
                        Predicate(
                            name="object_presence_verified",
                            args=[target_category, memory_id],
                        ),
                        Predicate(name="task_goal_satisfied", args=[target_category]),
                    ],
                ),
            ],
            memory_grounding=[memory_id],
            candidate_grounding=[memory_id],
            notes="deterministic_check_presence_plan",
        )

    def _build_fetch_plan(
        self,
        *,
        target_category: str,
        memory_id: str,
    ) -> HighLevelPlan:
        return HighLevelPlan(
            plan_id=f"plan-{uuid4().hex[:12]}",
            intent=TaskIntent.FETCH_OBJECT,
            subgoals=[
                Subgoal(
                    subgoal_id="sg-1",
                    subgoal_type=SubgoalType.NAVIGATE,
                    description=f"Navigate to candidate location for {target_category}.",
                    candidate_id=memory_id,
                    target_memory_id=memory_id,
                    success_conditions=[
                        Predicate(name="arrived_at_candidate_anchor", args=[memory_id])
                    ],
                ),
                Subgoal(
                    subgoal_id="sg-2",
                    subgoal_type=SubgoalType.OBSERVE,
                    description=f"Observe candidate area for {target_category}.",
                    candidate_id=memory_id,
                    target_memory_id=memory_id,
                    success_conditions=[Predicate(name="observation_captured", args=[memory_id])],
                ),
                Subgoal(
                    subgoal_id="sg-3",
                    subgoal_type=SubgoalType.VERIFY_OBJECT_PRESENCE,
                    description=f"Verify target {target_category} is present before manipulation.",
                    candidate_id=memory_id,
                    target_memory_id=memory_id,
                    success_conditions=[
                        Predicate(name="object_presence_verified", args=[memory_id])
                    ],
                ),
                Subgoal(
                    subgoal_id="sg-4",
                    subgoal_type=SubgoalType.EMBODIED_MANIPULATION,
                    description=f"Manipulate and secure {target_category} for delivery.",
                    candidate_id=memory_id,
                    target_memory_id=memory_id,
                    success_conditions=[Predicate(name="object_secured", args=[target_category])],
                ),
                Subgoal(
                    subgoal_id="sg-5",
                    subgoal_type=SubgoalType.RETURN_TO_USER,
                    description="Return to user and complete handover.",
                    candidate_id=memory_id,
                    target_memory_id=memory_id,
                    success_conditions=[
                        Predicate(name="returned_to_user", args=["user"]),
                        Predicate(name="task_goal_satisfied", args=[target_category]),
                    ],
                ),
            ],
            memory_grounding=[memory_id],
            candidate_grounding=[memory_id],
            notes="deterministic_fetch_plan",
        )

    def _build_ask_clarification_plan(self, *, intent: TaskIntent, notes: str) -> HighLevelPlan:
        return HighLevelPlan(
            plan_id=f"plan-{uuid4().hex[:12]}",
            intent=intent,
            subgoals=[
                Subgoal(
                    subgoal_id="sg-1",
                    subgoal_type=SubgoalType.ASK_CLARIFICATION,
                    description="Ask user for clarification to recover planning context.",
                    success_conditions=[Predicate(name="clarification_received", args=[])],
                )
            ],
            memory_grounding=[],
            candidate_grounding=[],
            notes=notes,
        )

    @staticmethod
    def _replan_context_summary(context: TaskContext) -> str:
        recent_failure = (
            context.runtime_state.recent_failure_analysis.failure_type.value
            if context.runtime_state.recent_failure_analysis is not None
            else "none"
        )
        return (
            f"negative_evidence={len(context.task_negative_evidence)}; "
            f"candidate_exclusions={len(context.runtime_state.candidate_exclusion_state)}; "
            f"recent_failure={recent_failure}; "
            f"retry_budget={context.runtime_state.retry_budget}"
        )


class PlanValidator:
    """Validate generated plans before execution."""

    def validate(self, plan: HighLevelPlan, context: TaskContext) -> None:
        self._validate_subgoal_types(plan)
        self._validate_no_atomic_actions(plan)
        self._validate_manipulation_order(plan)
        self._validate_fetch_completion_contract(plan)
        # Removed: _validate_grounding_requirements - grounding fields are auto-filled and not used during execution
        self._validate_capability_requirements(plan, context.capability_registry)
        # Removed: _validate_candidate_alignment_with_context - grounding fields are metadata only

    @staticmethod
    def _validate_subgoal_types(plan: HighLevelPlan) -> None:
        invalid = [
            item.subgoal_type.value
            for item in plan.subgoals
            if item.subgoal_type not in _ALLOWED_SUBGOAL_TYPES
        ]
        if invalid:
            raise ValueError(f"unsupported subgoal types: {invalid}")

    @staticmethod
    def _validate_no_atomic_actions(plan: HighLevelPlan) -> None:
        for subgoal in plan.subgoals:
            text = (subgoal.description or "").lower()
            for keyword in _ATOMIC_ACTION_KEYWORDS:
                if keyword in text:
                    raise ValueError(
                        f"atomic action is not allowed in high-level plan: {keyword}"
                    )

    @staticmethod
    def _validate_manipulation_order(plan: HighLevelPlan) -> None:
        verify_indices = [
            idx
            for idx, subgoal in enumerate(plan.subgoals)
            if subgoal.subgoal_type == SubgoalType.VERIFY_OBJECT_PRESENCE
        ]
        manipulation_indices = [
            idx
            for idx, subgoal in enumerate(plan.subgoals)
            if subgoal.subgoal_type == SubgoalType.EMBODIED_MANIPULATION
        ]
        if not manipulation_indices:
            return
        if not verify_indices:
            raise ValueError("embodied_manipulation requires verify_object_presence beforehand.")
        if min(manipulation_indices) < min(verify_indices):
            raise ValueError("embodied_manipulation cannot happen before verify_object_presence.")

    @staticmethod
    def _validate_fetch_completion_contract(plan: HighLevelPlan) -> None:
        if plan.intent != TaskIntent.FETCH_OBJECT:
            return

        has_return = any(item.subgoal_type == SubgoalType.RETURN_TO_USER for item in plan.subgoals)
        if not has_return:
            raise ValueError("fetch plan must include return_to_user subgoal.")

        has_task_goal_predicate = any(
            condition.name == "task_goal_satisfied"
            for subgoal in plan.subgoals
            for condition in subgoal.success_conditions
        )
        if not has_task_goal_predicate:
            raise ValueError("fetch plan must include final task verification predicate.")

    @staticmethod
    def _validate_capability_requirements(
        plan: HighLevelPlan,
        registry: Mapping[str, CapabilitySpec],
    ) -> None:
        for subgoal in plan.subgoals:
            required = _SUBGOAL_CAPABILITY_REQUIREMENTS.get(subgoal.subgoal_type, ())
            for capability_name in required:
                if capability_name not in registry:
                    raise ValueError(
                        f"missing capability '{capability_name}' required by subgoal "
                        f"'{subgoal.subgoal_type.value}'."
                    )


class LLMHighLevelPlanner:
    """LLM-backed high-level planner with schema and validator guardrails."""

    def __init__(
        self,
        *,
        provider_factory: Callable[[], KimiPlanProvider] | None = None,
        validator: PlanValidator | None = None,
    ) -> None:
        self._provider_factory = provider_factory or KimiPlanProvider.from_config_file
        self._validator = validator or PlanValidator()

    def generate(self, context: TaskContext) -> HighLevelPlan:
        provider = self._provider_factory()
        try:
            prompt = self._build_prompt(context)
            plan = provider.generate_plan(prompt=prompt)
            # Auto-fill grounding fields from context before validation
            plan = self._auto_fill_grounding(plan, context)
            self._validator.validate(plan, context)
            return plan
        finally:
            provider.close()

    @staticmethod
    def _auto_fill_grounding(plan: HighLevelPlan, context: TaskContext) -> HighLevelPlan:
        """Auto-fill memory_grounding and candidate_grounding from subgoals and context."""
        # Collect all candidate/memory IDs referenced in subgoals
        referenced_ids: set[str] = set()
        for subgoal in plan.subgoals:
            if subgoal.candidate_id:
                referenced_ids.add(subgoal.candidate_id)
            if subgoal.target_memory_id:
                referenced_ids.add(subgoal.target_memory_id)

        # Separate into memory IDs and candidate IDs based on context
        memory_ids: set[str] = set()
        candidate_ids: set[str] = set()

        # Get all valid candidate IDs from ranked_candidates
        valid_candidate_ids = {
            item.get("memory_id") or item.get("candidate_id") or item.get("id")
            for item in context.ranked_candidates
            if item.get("memory_id") or item.get("candidate_id") or item.get("id")
        }

        for ref_id in referenced_ids:
            if ref_id in valid_candidate_ids:
                candidate_ids.add(ref_id)
                memory_ids.add(ref_id)  # Candidates are also memory items

        # Update plan with auto-filled grounding (only if not already set by LLM)
        if not plan.memory_grounding:
            plan.memory_grounding = sorted(memory_ids)
        if not plan.candidate_grounding:
            plan.candidate_grounding = sorted(candidate_ids)

        return plan

    @staticmethod
    def _build_prompt(context: TaskContext) -> str:
        """Build deterministic JSON prompt from TaskContext only."""
        runtime_state = context.runtime_state
        top_candidates = [
            {
                "memory_id": item.get("memory_id") or item.get("candidate_id") or item.get("id"),
                "anchor": item.get("anchor"),
                "score": item.get("score"),
                "reasons": item.get("reasons"),
            }
            for item in context.ranked_candidates[:5]
        ]

        payload = {
            "request": {
                "utterance": context.request.utterance,
                "intent": context.parsed_task.intent.value,
                "target_category": context.parsed_task.target_object.category,
                "target_aliases": context.parsed_task.target_object.aliases,
                "explicit_location_hint": context.parsed_task.explicit_location_hint,
                "delivery_target": context.parsed_task.delivery_target,
            },
            "retrieval": {
                "ranked_candidates": top_candidates,
                "category_prior_hits": context.category_prior_hits[:5],
                "recent_episodic_summaries": context.recent_episodic_summaries[:5],
                "task_negative_evidence": [
                    {
                        "location_key": item.location_key,
                        "status": item.status,
                        "object_category": item.object_category,
                    }
                    for item in context.task_negative_evidence
                ],
                "candidate_exclusion_state": dict(runtime_state.candidate_exclusion_state),
            },
            "runtime": {
                "retry_budget": runtime_state.retry_budget,
                "recent_failure_type": (
                    runtime_state.recent_failure_analysis.failure_type.value
                    if runtime_state.recent_failure_analysis is not None
                    else None
                ),
                "high_level_progress": (
                    runtime_state.high_level_progress.model_dump(mode="json")
                    if runtime_state.high_level_progress is not None
                    else None
                ),
                "embodied_action_progress": (
                    runtime_state.embodied_action_progress.model_dump(mode="json")
                    if runtime_state.embodied_action_progress is not None
                    else None
                ),
                "runtime_object_updates": [
                    item.model_dump(mode="json") for item in runtime_state.runtime_object_updates
                ],
            },
            "constraints": {
                "allowed_subgoal_types": sorted(item.value for item in _ALLOWED_SUBGOAL_TYPES),
                "forbidden_atomic_action_keywords": sorted(_ATOMIC_ACTION_KEYWORDS),
                "must_not_use_excluded_candidates": True,
                "must_include_verification": True,
                "output_format": "json_only",
            },
            "output_schema": {
                "type": "HighLevelPlan",
                "required_fields": [
                    "plan_id",
                    "intent",
                    "subgoals",
                ],
                "optional_fields": [
                    "memory_grounding",
                    "candidate_grounding",
                    "notes",
                ],
            },
        }

        instructions = (
            "Generate one high-level plan JSON object. "
            "Do not emit markdown. Do not emit atomic actions. "
            "Use only candidates from retrieval.ranked_candidates. "
            "Never use excluded candidate locations. "
            "Keep subgoal_type within allowed_subgoal_types."
        )

        return instructions + "\n\n" + json.dumps(payload, ensure_ascii=False)


class PlannerService:
    """Planner service that enforces generation + validation pipeline."""

    def __init__(
        self,
        planner: DeterministicHighLevelPlanner | None = None,
        validator: PlanValidator | None = None,
        llm_planner: LLMHighLevelPlanner | None = None,
        llm_first: bool = True,
    ) -> None:
        self._planner = planner or DeterministicHighLevelPlanner()
        self._validator = validator or PlanValidator()
        self._llm_planner = llm_planner or LLMHighLevelPlanner(validator=self._validator)
        self._llm_first = llm_first
        self.last_diagnostics: dict[str, Any] = {
            "planner_mode": "deterministic",
            "llm_attempted": False,
            "fallback_used": False,
            "planner_error_type": None,
            "planner_error_message": None,
        }

    def plan(self, context: TaskContext) -> HighLevelPlan:
        """Generate and validate a plan from task context."""
        parse_error = str(context.constraints.get("parse_error", "")).strip()
        if parse_error:
            plan = self._planner.generate(context)
            self._validator.validate(plan, context)
            self.last_diagnostics = {
                "planner_mode": "deterministic_parse_error",
                "llm_attempted": False,
                "fallback_used": False,
                "planner_error_type": None,
                "planner_error_message": None,
            }
            return plan

        if self._llm_first:
            try:
                plan = self._llm_planner.generate(context)
            except Exception as exc:  # noqa: BLE001
                error_type = _planner_error_type(exc)
                error_message = str(exc)
                fallback_plan = self._planner.generate(context)
                self._validator.validate(fallback_plan, context)
                self.last_diagnostics = {
                    "planner_mode": "deterministic_fallback",
                    "llm_attempted": True,
                    "fallback_used": True,
                    "planner_error_type": error_type,
                    "planner_error_message": error_message,
                }
                return fallback_plan

            self.last_diagnostics = {
                "planner_mode": "llm",
                "llm_attempted": True,
                "fallback_used": False,
                "planner_error_type": None,
                "planner_error_message": None,
            }
            return plan

        plan = self._planner.generate(context)
        self._validator.validate(plan, context)
        self.last_diagnostics = {
            "planner_mode": "deterministic",
            "llm_attempted": False,
            "fallback_used": False,
            "planner_error_type": None,
            "planner_error_message": None,
        }
        return plan

    def plan_from_request(
        self,
        *,
        request: TaskRequest,
        runtime_state: RuntimeState,
        ranked_candidates: list[dict[str, Any]] | None = None,
        object_memory_hits: list[dict[str, Any]] | None = None,
        category_prior_hits: list[dict[str, Any]] | None = None,
        recent_episodic_summaries: list[dict[str, Any]] | None = None,
        capability_registry: Mapping[str, CapabilitySpec | dict[str, Any]] | None = None,
        adapter_status: dict[str, Any] | None = None,
        constraints: dict[str, Any] | None = None,
    ) -> HighLevelPlan:
        """Parse request, build context, generate plan, and validate it."""
        merged_constraints = dict(constraints or {})
        ranked = list(ranked_candidates or [])

        try:
            parsed_task = parse_instruction(request)
        except ValueError as exc:
            parsed_task = ParsedTask(
                intent=TaskIntent.CHECK_OBJECT_PRESENCE,
                target_object=TargetObject(category="unknown_object"),
                requires_navigation=False,
                requires_manipulation=False,
            )
            merged_constraints["parse_error"] = str(exc)
            ranked = []

        context = build_task_context(
            request=request,
            parsed_task=parsed_task,
            runtime_state=runtime_state,
            ranked_candidates=ranked,
            object_memory_hits=object_memory_hits,
            category_prior_hits=category_prior_hits,
            recent_episodic_summaries=recent_episodic_summaries,
            capability_registry=capability_registry,
            adapter_status=adapter_status,
            constraints=merged_constraints,
        )
        return self.plan(context)


def _candidate_memory_id(candidate: Mapping[str, Any]) -> str:
    for key in ("memory_id", "candidate_id", "id"):
        value = candidate.get(key)
        if isinstance(value, str) and value:
            return value
    raise ValueError("top candidate must contain a non-empty memory_id/candidate_id/id.")


def _candidate_identifier(candidate: Mapping[str, Any]) -> str | None:
    for key in ("memory_id", "candidate_id", "id"):
        value = candidate.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _candidate_location_key(candidate: Mapping[str, Any]) -> str | None:
    anchor = candidate.get("anchor")
    if not isinstance(anchor, Mapping):
        return None
    room_id = anchor.get("room_id")
    anchor_id = anchor.get("anchor_id")
    if not isinstance(room_id, str) or not room_id:
        return None
    if not isinstance(anchor_id, str) or not anchor_id:
        return None
    return f"{room_id}:{anchor_id}"


def _planner_error_type(exc: Exception) -> str:
    if isinstance(exc, KimiProviderError):
        return exc.error_type
    return exc.__class__.__name__


__all__ = [
    "DeterministicHighLevelPlanner",
    "LLMHighLevelPlanner",
    "PlanValidator",
    "PlannerService",
]
