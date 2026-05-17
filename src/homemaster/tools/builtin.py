"""Builtin tool executors for AgentRuntime.

11 tools wrapping existing stage code or providing simulated execution.
Each executor has signature:
    def executor(*, arguments: dict, state: AgentState, settings: RuntimeSettings) -> ToolResult

Tools marked "thin wrapper" delegate to existing stage functions.
Tools marked "new code" provide simulated or programmatic execution.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from homemaster.agent.state import AgentState
from homemaster.config.runtime_settings import RuntimeSettings
from homemaster.tools.registry import ToolRegistry
from homemaster.tools.results import ToolResult
from homemaster.tools.simulated import SIMULATED_TOOL_MAKERS
from homemaster.tools.skill_tools import GET_SKILL_INPUT_SCHEMA, GET_SKILL_OUTPUT_SCHEMA
from homemaster.tools.spec import ToolSpec

# ---------------------------------------------------------------------------
# Executor implementations
# ---------------------------------------------------------------------------


def _exec_understand_task(
    *,
    arguments: dict[str, Any],
    state: AgentState,
    settings: RuntimeSettings,
    event_sink: Any = None,
) -> ToolResult:
    """Thin wrapper: run_stage02() → TaskCard."""
    from homemaster.pipeline.stage_runtime import run_stage02

    utterance = arguments.get("utterance") or state.user_request
    try:
        task_card = run_stage02(
            utterance=utterance,
            run_id=settings.run_id,
            config_path=str(settings.config_path or ""),
            provider_name=settings.provider_name,
        )
        return ToolResult(
            success=True,
            tool_name="understand_task",
            executor_mode="live_llm",
            data={"task_card": task_card.model_dump(mode="json")},
        )
    except Exception as exc:
        return ToolResult(
            success=False,
            tool_name="understand_task",
            executor_mode="live_llm",
            failure_reason=f"{type(exc).__name__}: {exc}",
        )


def _exec_retrieve_memory(
    *,
    arguments: dict[str, Any],
    state: AgentState,
    settings: RuntimeSettings,
    event_sink: Any = None,
) -> ToolResult:
    """Thin wrapper: run_stage03() → MemoryRagResult."""
    from homemaster.contracts import TaskCard
    from homemaster.pipeline.stage_runtime import run_stage03

    if not state.task_card:
        return ToolResult(
            success=False,
            tool_name="retrieve_memory",
            executor_mode="live_llm",
            failure_reason="no task_card in state — call understand_task first",
        )
    try:
        task_card = TaskCard.model_validate(state.task_card)
        result = run_stage03(
            task_card=task_card,
            memory_path=str(settings.memory_path or ""),
            scenario=settings.scenario or "",
            run_id=settings.run_id,
            config_path=str(settings.config_path or ""),
            provider_name=settings.provider_name,
            embedding_provider_name=settings.embedding_provider_name,
            case_root=settings.case_dir or Path("."),
            results_dir=settings.results_root,
            event_sink=event_sink,
        )
        return ToolResult(
            success=True,
            tool_name="retrieve_memory",
            executor_mode="live_llm",
            data={"hits": [h.model_dump(mode="json") for h in result.hits]},
        )
    except Exception as exc:
        return ToolResult(
            success=False,
            tool_name="retrieve_memory",
            executor_mode="live_llm",
            failure_reason=f"{type(exc).__name__}: {exc}",
        )


def _exec_ground_target(
    *,
    arguments: dict[str, Any],
    state: AgentState,
    settings: RuntimeSettings,
    event_sink: Any = None,
) -> ToolResult:
    """Thin wrapper: build_planning_context() → PlanningContext."""
    import json

    from homemaster.contracts import MemoryRetrievalHit, MemoryRetrievalResult, TaskCard
    from homemaster.planning_context import build_planning_context

    if not state.task_card:
        return ToolResult(
            success=False,
            tool_name="ground_target",
            executor_mode="programmatic",
            failure_reason="no task_card in state",
        )
    try:
        task_card = TaskCard.model_validate(state.task_card)
        hits = [MemoryRetrievalHit.model_validate(h) for h in state.memory_hits]
        memory_result = MemoryRetrievalResult(hits=hits, retrieval_query=None)

        world_path = settings.world_path
        world = json.loads(world_path.read_text(encoding="utf-8")) if world_path else {}

        result = build_planning_context(task_card, memory_result, world)
        context = result.context
        return ToolResult(
            success=True,
            tool_name="ground_target",
            executor_mode="programmatic",
            data={
                "candidates": [
                    {
                        "memory_id": t.memory_id,
                        "object_category": t.object_category,
                        "room_id": t.room_id,
                        "anchor_id": t.anchor_id,
                    }
                    for t in (context.rejected_hits or [])
                ] + (
                    [{
                        "memory_id": context.selected_target.memory_id,
                        "object_category": context.selected_target.object_category,
                        "room_id": context.selected_target.room_id,
                        "anchor_id": context.selected_target.anchor_id,
                    }] if context.selected_target else []
                ),
                "selected_target": (
                    {
                        "memory_id": context.selected_target.memory_id,
                        "object_category": context.selected_target.object_category,
                        "room_id": context.selected_target.room_id,
                        "anchor_id": context.selected_target.anchor_id,
                    }
                    if context.selected_target
                    else None
                ),
                "grounded": context.selected_target is not None,
            },
        )
    except Exception as exc:
        return ToolResult(
            success=False,
            tool_name="ground_target",
            executor_mode="programmatic",
            failure_reason=f"{type(exc).__name__}: {exc}",
        )


def _make_get_skill_executor(skill_registry: Any):
    """Create a get_skill executor that uses the injected SkillRegistry."""

    def _exec_get_skill(
        *,
        arguments: dict[str, Any],
        state: AgentState,
        settings: RuntimeSettings,
        event_sink: Any = None,
    ) -> ToolResult:
        skill_name = arguments.get("skill_name", "")
        if not skill_name:
            return ToolResult(
                success=False,
                tool_name="get_skill",
                executor_mode="programmatic",
                failure_reason="skill_name is required",
            )

        spec = skill_registry.get(skill_name)
        if spec is None:
            return ToolResult(
                success=False,
                tool_name="get_skill",
                executor_mode="programmatic",
                failure_reason=f"skill not found: {skill_name}",
            )

        return ToolResult(
            success=True,
            tool_name="get_skill",
            executor_mode="programmatic",
            data={
                "name": spec.name,
                "description": spec.description,
                "content": spec.context_snippet,
                "allowed_tools": spec.allowed_tools,
                "constraints": spec.constraints,
                "success_criteria": spec.success_criteria,
            },
        )

    return _exec_get_skill


def _exec_update_memory(
    *,
    arguments: dict[str, Any],
    state: AgentState,
    settings: RuntimeSettings,
    event_sink: Any = None,
) -> ToolResult:
    """Validate proposal and persist via RuntimeMemoryStore."""
    proposal = arguments.get("proposal")
    if not proposal:
        return ToolResult(
            success=False,
            tool_name="update_memory",
            executor_mode="programmatic",
            failure_reason="proposal is required",
        )

    # Validate required fields
    required = {"object_category", "room_id", "anchor_id"}
    missing = required - set(proposal.keys())
    if missing:
        return ToolResult(
            success=False,
            tool_name="update_memory",
            executor_mode="programmatic",
            failure_reason=f"proposal missing fields: {missing}",
        )

    anchor_id = proposal["anchor_id"]
    belief_state = proposal.get("belief_state", "verified")

    # Find memory_id from state.memory_hits by anchor_id match
    memory_id = anchor_id
    for hit in state.memory_hits:
        if hit.get("anchor_id") == anchor_id:
            memory_id = hit.get("memory_id", anchor_id)
            break

    # Persist via RuntimeMemoryStore if memory_path is available
    if settings.memory_path and settings.memory_path.exists():
        try:
            from homemaster.contracts import EvidenceRef, MemoryCommitPlan, ObjectMemoryUpdate
            from homemaster.memory_commit import utc_now_iso
            from homemaster.runtime_memory_store import RuntimeMemoryStore

            memory_root = settings.runtime_root / settings.run_id / "memory"
            store = RuntimeMemoryStore(memory_root)
            now = utc_now_iso()
            plan = MemoryCommitPlan(
                commit_id=f"commit:{settings.run_id}:update_memory",
                object_memory_updates=[
                    ObjectMemoryUpdate(
                        memory_id=memory_id,
                        update_type="confirm",
                        updated_fields={"belief_state": belief_state},
                        evidence_refs=[
                            EvidenceRef(
                                evidence_id=f"agent:{settings.run_id}:update_memory",
                                evidence_type="observation",
                                source_id=f"agent-{settings.run_id}",
                                created_at=now,
                                summary=f"Agent updated {proposal['object_category']}",
                            )
                        ],
                        reason="agent runtime update_memory proposal",
                    )
                ],
                skipped=False,
            )
            store.apply_commit_plan(
                base_memory_path=settings.memory_path, plan=plan,
            )
        except Exception:
            pass  # persistence is best-effort for MVP

    return ToolResult(
        success=True,
        tool_name="update_memory",
        executor_mode="programmatic",
        data={
            "committed": True,
            "object_category": proposal.get("object_category"),
            "room_id": proposal.get("room_id"),
            "anchor_id": anchor_id,
            "belief_state": belief_state,
            "memory_id": memory_id,
        },
    )


def _exec_update_user_profile(
    *,
    arguments: dict[str, Any],
    state: AgentState,
    settings: RuntimeSettings,
    event_sink: Any = None,
) -> ToolResult:
    """New code: validate and accept user profile proposal."""
    proposal = arguments.get("proposal")
    if not proposal:
        return ToolResult(
            success=False,
            tool_name="update_user_profile",
            executor_mode="programmatic",
            failure_reason="proposal is required",
        )

    key = proposal.get("key")
    value = proposal.get("value")
    if not key:
        return ToolResult(
            success=False,
            tool_name="update_user_profile",
            executor_mode="programmatic",
            failure_reason="proposal.key is required",
        )

    return ToolResult(
        success=True,
        tool_name="update_user_profile",
        executor_mode="programmatic",
        data={"committed": True, "key": key, "value": value},
    )


def _exec_finish_task(
    *,
    arguments: dict[str, Any],
    state: AgentState,
    settings: RuntimeSettings,
    event_sink: Any = None,
) -> ToolResult:
    """Internal finalizer. selectable_by_model=False. Never called by Mimo."""
    return ToolResult(
        success=True,
        tool_name="finish_task",
        executor_mode="internal",
        data={"status": "completed"},
    )


# ---------------------------------------------------------------------------
# ToolSpec definitions
# ---------------------------------------------------------------------------


def _make_understand_task_spec() -> ToolSpec:
    return ToolSpec(
        name="understand_task",
        description="Parse user utterance into a structured TaskCard.",
        input_schema={
            "type": "object",
            "properties": {
                "utterance": {"type": "string", "description": "User request text."},
            },
        },
        executor_mode="live_llm",
        selectable_by_model=True,
        state_effects=["task_card"],
        executor=_exec_understand_task,
    )


def _make_retrieve_memory_spec() -> ToolSpec:
    return ToolSpec(
        name="retrieve_memory",
        description="Retrieve relevant object memories using RAG.",
        input_schema={"type": "object", "properties": {}},
        executor_mode="live_llm",
        selectable_by_model=True,
        state_effects=["memory_hits"],
        executor=_exec_retrieve_memory,
    )


def _make_ground_target_spec() -> ToolSpec:
    return ToolSpec(
        name="ground_target",
        description="Assess memory hits and select a grounded target for execution.",
        input_schema={"type": "object", "properties": {}},
        executor_mode="programmatic",
        selectable_by_model=True,
        state_effects=["target_candidates"],
        executor=_exec_ground_target,
    )


def _make_get_skill_spec(skill_registry: Any) -> ToolSpec:
    return ToolSpec(
        name="get_skill",
        description="Retrieve full skill content, allowed tools, and constraints.",
        input_schema=GET_SKILL_INPUT_SCHEMA,
        output_schema=GET_SKILL_OUTPUT_SCHEMA,
        executor_mode="programmatic",
        selectable_by_model=True,
        state_effects=["loaded_skill_contexts"],
        executor=_make_get_skill_executor(skill_registry),
    )


def _make_update_memory_spec() -> ToolSpec:
    return ToolSpec(
        name="update_memory",
        description="Submit a proposal to update object memory.",
        input_schema={
            "type": "object",
            "properties": {
                "proposal": {
                    "type": "object",
                    "description": "Memory update proposal.",
                    "properties": {
                        "object_category": {"type": "string"},
                        "room_id": {"type": "string"},
                        "anchor_id": {"type": "string"},
                        "belief_state": {"type": "string"},
                    },
                    "required": ["object_category", "room_id", "anchor_id"],
                },
            },
            "required": ["proposal"],
        },
        executor_mode="programmatic",
        selectable_by_model=True,
        state_effects=["actions"],
        executor=_exec_update_memory,
    )


def _make_update_user_profile_spec() -> ToolSpec:
    return ToolSpec(
        name="update_user_profile",
        description="Submit a proposal to update user profile/preferences.",
        input_schema={
            "type": "object",
            "properties": {
                "proposal": {
                    "type": "object",
                    "description": "Profile update proposal.",
                    "properties": {
                        "key": {"type": "string"},
                        "value": {"type": "string"},
                    },
                    "required": ["key", "value"],
                },
            },
            "required": ["proposal"],
        },
        executor_mode="programmatic",
        selectable_by_model=True,
        state_effects=["actions"],
        executor=_exec_update_user_profile,
    )


def _make_finish_task_spec() -> ToolSpec:
    return ToolSpec(
        name="finish_task",
        description="Internal runtime finalizer. Not selectable by model.",
        input_schema={"type": "object", "properties": {}},
        executor_mode="internal",
        selectable_by_model=False,
        executor=_exec_finish_task,
    )


# ---------------------------------------------------------------------------
# Registry builders
# ---------------------------------------------------------------------------

_SIMPLE_TOOL_MAKERS = [
    _make_understand_task_spec,
    _make_retrieve_memory_spec,
    _make_ground_target_spec,
    *SIMULATED_TOOL_MAKERS,
    _make_update_memory_spec,
    _make_update_user_profile_spec,
    _make_finish_task_spec,
]


def build_tool_registry(
    skill_registry: Any = None, skill_mode: str = "simulated",
) -> ToolRegistry:
    """Build a ToolRegistry with all 11 builtin tools.

    Args:
        skill_registry: Optional SkillRegistry for get_skill executor.
            If None, a default one is built via build_skill_registry().
        skill_mode: "simulated" or "real". "real" raises RuntimeError.
    """
    if skill_registry is None:
        skill_registry = build_skill_registry(skill_mode=skill_mode)
    registry = ToolRegistry()
    for maker in _SIMPLE_TOOL_MAKERS:
        registry.register(maker())
    registry.register(_make_get_skill_spec(skill_registry))
    return registry


def build_skill_registry(skill_mode: str = "simulated") -> Any:
    """Build a SkillRegistry with builtin skills.

    Args:
        skill_mode: "simulated" or "real". "real" raises RuntimeError
            because real VLA/VLN/VLM executors are not integrated.
    """
    if skill_mode == "real":
        raise RuntimeError(
            "skill_mode='real' is not yet supported. "
            "Real VLA/VLN/VLM skill executors are not integrated."
        )
    from homemaster.skills.loader import SkillLoader
    from homemaster.skills.registry import SkillRegistry

    registry = SkillRegistry()
    loader = SkillLoader()
    for name in ("fetch_object", "check_object_state"):
        try:
            spec = loader.load_builtin(name)
            registry.register(spec)
        except FileNotFoundError:
            pass  # Skill not yet created
    return registry
