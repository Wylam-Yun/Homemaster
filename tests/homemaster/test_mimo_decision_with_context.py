"""Tests for ContextBuilder output as MimoDecisionClient.decide() input.

Verifies that the three-layer context produced by ContextBuilder is
compatible with the decide() interface.
"""

from __future__ import annotations

from homemaster.agent.context_builder import ContextBuilder
from homemaster.agent.state import AgentState
from homemaster.tools.builtin import build_skill_registry, build_tool_registry


def test_context_builder_output_is_decide_compatible() -> None:
    """ContextBuilder output can be passed as context= to decide()."""
    builder = ContextBuilder()
    state = AgentState(
        run_id="test-ctx-001",
        user_request="fetch the cup",
        task_card={"target": "cup", "intent": "fetch"},
        current_location="kitchen",
        turn_index=1,
        memory_context_snapshot="# Memory\n- cup in kitchen",
        user_context_snapshot="# User\n- prefers quiet",
    )

    tool_registry = build_tool_registry()
    skill_registry = build_skill_registry()

    context = builder.build(
        state,
        tool_manifests=tool_registry.tool_manifests(),
        skill_summaries=skill_registry.candidate_summaries(),
        max_turns=12,
    )

    # Verify structure is compatible with decide() expectations
    assert isinstance(context, dict)
    assert "stable_context" in context
    assert "task_state_context" in context
    assert "recent_dynamics_context" in context

    # Verify stable_context has required keys
    stable = context["stable_context"]
    assert "tool_manifests" in stable
    assert "skill_summaries" in stable
    assert isinstance(stable["tool_manifests"], list)
    assert isinstance(stable["skill_summaries"], list)

    # Verify tool_manifests are serializable (needed for API calls)
    import json

    json.dumps(context)  # must not raise


def test_context_builder_tool_manifests_match_registry() -> None:
    """Tool manifests in context must match registry's selectable tools."""
    builder = ContextBuilder()
    state = AgentState(run_id="test-ctx-002")

    tool_registry = build_tool_registry()
    skill_registry = build_skill_registry()

    context = builder.build(
        state,
        tool_manifests=tool_registry.tool_manifests(),
        skill_summaries=skill_registry.candidate_summaries(),
        max_turns=12,
    )

    manifest_names = [m["name"] for m in context["stable_context"]["tool_manifests"]]
    registry_names = [m["name"] for m in tool_registry.tool_manifests()]

    assert manifest_names == registry_names
    # finish_task must NOT be in manifests
    assert "finish_task" not in manifest_names
    # verify MUST be in manifests
    assert "verify" in manifest_names
