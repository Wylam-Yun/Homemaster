"""Builtin tool executors for GenericAgentRuntime.

Each executor has signature:
    def executor(*, arguments: dict, run_context: RunContext) -> ToolResult
"""

from __future__ import annotations

from typing import Any

from homemaster.agent.normalized import RunContext
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
    run_context: RunContext,
) -> ToolResult:
    """Stub: understand_task will be migrated in Batch 3."""
    return ToolResult(
        success=False,
        tool_name="understand_task",
        executor_mode="live_llm",
        failure_reason="understand_task not yet migrated to generic runtime",
    )


def _exec_retrieve_memory(
    *,
    arguments: dict[str, Any],
    run_context: RunContext,
) -> ToolResult:
    """Stub: retrieve_memory will be migrated in Batch 3."""
    return ToolResult(
        success=False,
        tool_name="retrieve_memory",
        executor_mode="live_llm",
        failure_reason="retrieve_memory not yet migrated to generic runtime",
    )


def _exec_ground_target(
    *,
    arguments: dict[str, Any],
    run_context: RunContext,
) -> ToolResult:
    """Stub: ground_target will be migrated in Batch 3."""
    return ToolResult(
        success=False,
        tool_name="ground_target",
        executor_mode="programmatic",
        failure_reason="ground_target not yet migrated to generic runtime",
    )


def _make_get_skill_executor(skill_registry: Any):
    """Create a get_skill executor that uses the injected SkillRegistry."""

    def _exec_get_skill(
        *,
        arguments: dict[str, Any],
        run_context: RunContext,
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
                "content": spec.system_prompt_fragment,
                "tool_names": spec.tool_names,
                "constraints": spec.constraints,
                "success_criteria": spec.success_criteria,
            },
        )

    return _exec_get_skill


def _exec_update_memory(
    *,
    arguments: dict[str, Any],
    run_context: RunContext,
) -> ToolResult:
    """Stub: update_memory is now handled by domain/home/tools.py memory_writer."""
    return ToolResult(
        success=False,
        tool_name="update_memory",
        executor_mode="programmatic",
        failure_reason="update_memory migrated to domain home tools (memory_writer)",
    )


def _exec_update_user_profile(
    *,
    arguments: dict[str, Any],
    run_context: RunContext,
) -> ToolResult:
    """Validate and accept user profile proposal."""
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
    run_context: RunContext,
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
    skill_registry: Any = None,
) -> ToolRegistry:
    """Build a ToolRegistry with all 11 builtin tools."""
    if skill_registry is None:
        skill_registry = build_skill_registry()
    registry = ToolRegistry()
    for maker in _SIMPLE_TOOL_MAKERS:
        registry.register(maker())
    registry.register(_make_get_skill_spec(skill_registry))
    return registry


def build_skill_registry() -> Any:
    """Build a SkillRegistry with builtin skills."""
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
